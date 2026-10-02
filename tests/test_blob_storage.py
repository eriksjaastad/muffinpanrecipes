"""Integration tests for Vercel Blob episode persistence (#5048).

Tests save/load/list operations on _CloudBackend, mocking the Vercel Blob
REST API to verify correct request structure, caching, and fallback behavior.
"""

import json
import os
import re
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from backend.storage import PageReadError


@pytest.fixture(autouse=True)
def _no_vercel_env(monkeypatch):
    """Ensure VERCEL_ENV is not set so _CloudBackend doesn't raise on init."""
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


@pytest.fixture
def cloud_backend():
    """Create a _CloudBackend with a fake blob token."""
    from backend.storage import _CloudBackend

    with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake-token-for-test"}):
        backend = _CloudBackend()
    return backend


@pytest.fixture
def sample_episode():
    return {
        "episode_id": "ep-test-001",
        "concept": "Test Muffins",
        "messages": [{"character": "Steph", "message": "Let's go!", "day": "monday"}],
        "created_at": "2026-03-14T00:00:00Z",
    }


class TestFilesystemBackendLoadEpisodeStrict:
    """#7630: cron_routes._apply_week_off_note calls load_episode_strict
    uniformly regardless of which backend `storage` resolved to — the
    filesystem backend needs the method to exist, even though it has no
    cloud-fallback failure mode to be "strict" about."""

    def test_returns_none_for_a_missing_episode(self, tmp_path, monkeypatch):
        from backend.storage import _FilesystemBackend

        monkeypatch.setattr("backend.storage.EPISODES_DIR", tmp_path)
        backend = _FilesystemBackend()
        assert backend.load_episode_strict("nope") is None

    def test_returns_the_episode_when_present(self, tmp_path, monkeypatch, sample_episode):
        import json as _json

        from backend.storage import _FilesystemBackend

        monkeypatch.setattr("backend.storage.EPISODES_DIR", tmp_path)
        (tmp_path / f"{sample_episode['episode_id']}.json").write_text(_json.dumps(sample_episode))
        backend = _FilesystemBackend()
        assert backend.load_episode_strict(sample_episode["episode_id"]) == sample_episode


class TestCloudBackendSaveEpisode:
    def test_save_puts_to_blob_api(self, cloud_backend, sample_episode):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"url": "https://blob.vercel-storage.com/episodes/ep-test-001.json"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.put", return_value=mock_resp) as mock_put:
            # Also mock filesystem fallback
            with patch.object(cloud_backend._fs, "save_episode"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

        mock_put.assert_called_once()
        call_args = mock_put.call_args
        assert "episodes/ep-test-001.json" in call_args[0][0]
        headers = call_args[1]["headers"]
        assert headers["Authorization"] == "Bearer fake-token-for-test"
        assert headers["x-allow-overwrite"] == "1"
        assert headers["x-add-random-suffix"] == "0"

    def test_save_updates_memory_cache(self, cloud_backend, sample_episode):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"url": "https://example.com/ep.json"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.put", return_value=mock_resp):
            with patch.object(cloud_backend._fs, "save_episode"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

        assert cloud_backend._episode_cache[("", "ep-test-001")] == sample_episode

    def test_save_raises_on_api_failure(self, cloud_backend, sample_episode):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 Server Error")

        with patch("requests.put", return_value=mock_resp):
            with pytest.raises(Exception, match="500 Server Error"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

    def test_save_with_prefix(self, cloud_backend, sample_episode):
        cloud_backend.set_prefix("test/")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"url": "https://example.com/ep.json"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.put", return_value=mock_resp) as mock_put:
            with patch.object(cloud_backend._fs, "save_episode"):
                cloud_backend.save_episode("ep-test-001", sample_episode)

        url = mock_put.call_args[0][0]
        assert "test/episodes/ep-test-001.json" in url


class TestCloudBackendLoadEpisode:
    def test_load_returns_cached_data(self, cloud_backend, sample_episode):
        cloud_backend._episode_cache[("", "ep-cached")] = sample_episode
        result = cloud_backend.load_episode("ep-cached")
        assert result == sample_episode

    def test_load_fetches_from_blob_api(self, cloud_backend, sample_episode):
        # Mock list API response
        mock_list = MagicMock()
        mock_list.json.return_value = {
            "blobs": [{"url": "https://cdn.example.com/ep.json", "pathname": "episodes/ep-test-001.json"}]
        }
        mock_list.raise_for_status = MagicMock()

        # Mock content fetch
        mock_content = MagicMock()
        mock_content.json.return_value = sample_episode
        mock_content.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[mock_list, mock_content]):
            result = cloud_backend.load_episode("ep-test-001")

        assert result == sample_episode
        # Should also cache the result
        assert cloud_backend._episode_cache[("", "ep-test-001")] == sample_episode

    def test_load_does_not_use_cache_from_another_prefix(self, cloud_backend, sample_episode):
        production_episode = {**sample_episode, "concept": "Production Muffins"}
        test_episode = {**sample_episode, "concept": "Test Muffins"}
        cloud_backend._episode_cache[("", "ep-shared")] = production_episode

        mock_list = MagicMock()
        mock_list.json.return_value = {
            "blobs": [{"url": "https://cdn.example.com/test-ep.json", "pathname": "test/episodes/ep-shared.json"}]
        }
        mock_list.raise_for_status = MagicMock()
        mock_content = MagicMock()
        mock_content.json.return_value = test_episode
        mock_content.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[mock_list, mock_content]) as mock_get:
            with cloud_backend.prefix_scope("test/"):
                result = cloud_backend.load_episode("ep-shared")

        assert result == test_episode
        assert mock_get.call_args_list[0].kwargs["params"]["prefix"] == "test/episodes/ep-shared.json"
        assert cloud_backend._episode_cache[("", "ep-shared")] == production_episode
        assert cloud_backend._episode_cache[("test/", "ep-shared")] == test_episode

    def test_load_falls_back_to_filesystem_on_empty_blobs(self, cloud_backend, sample_episode):
        mock_list = MagicMock()
        mock_list.json.return_value = {"blobs": []}
        mock_list.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_list):
            with patch.object(cloud_backend._fs, "load_episode", return_value=sample_episode) as mock_fs:
                result = cloud_backend.load_episode("ep-missing")

        mock_fs.assert_called_once_with("ep-missing")
        assert result == sample_episode

    def test_load_falls_back_to_filesystem_on_api_error(self, cloud_backend, sample_episode):
        with patch("requests.get", side_effect=Exception("Network error")):
            with patch.object(cloud_backend._fs, "load_episode", return_value=sample_episode) as mock_fs:
                result = cloud_backend.load_episode("ep-broken")

        mock_fs.assert_called_once_with("ep-broken")
        assert result == sample_episode

    def test_strict_load_does_not_fall_back_on_api_error(self, cloud_backend):
        with patch("requests.get", side_effect=Exception("Network error")), \
             patch.object(cloud_backend._fs, "load_episode") as mock_fs:
            with pytest.raises(Exception, match="Network error"):
                cloud_backend.load_episode_strict("ep-broken")

        mock_fs.assert_not_called()

    def test_strict_load_returns_none_on_genuine_not_found(self, cloud_backend):
        """#7630: the not-found vs error distinction load_episode_strict
        exists for. A SUCCESSFUL list call that legitimately finds nothing
        (empty blobs) is not an error — it must return None cleanly, not
        raise, and must not fall back to the filesystem either (unlike the
        non-strict load_episode)."""
        mock_list = MagicMock()
        mock_list.json.return_value = {"blobs": []}
        mock_list.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_list), \
             patch.object(cloud_backend._fs, "load_episode") as mock_fs:
            result = cloud_backend.load_episode_strict("ep-missing")

        assert result is None
        mock_fs.assert_not_called()

    @pytest.mark.parametrize(
        "malformed_payload",
        [{}, {"blobs": "x"}, {"blobs": [1]}],
        ids=["missing-blobs-key", "blobs-not-a-list", "blobs-item-not-a-dict"],
    )
    def test_strict_load_raises_on_a_malformed_200_body(self, cloud_backend, malformed_payload):
        """Round-4 review (#7630): a malformed 200 body — {}, blobs not a
        list, or a non-dict item in it — is not a genuine not-found (the old
        `.get("blobs", [])` treated it as one, which stamps a false
        week-off note). It must raise, and must not fall back to the
        filesystem (unlike the non-strict load_episode)."""
        mock_list = MagicMock()
        mock_list.json.return_value = malformed_payload
        mock_list.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_list), \
             patch.object(cloud_backend._fs, "load_episode") as mock_fs:
            with pytest.raises(PageReadError):
                cloud_backend.load_episode_strict("ep-malformed")

        mock_fs.assert_not_called()


    def test_strict_load_never_answers_from_a_warm_cache(self, cloud_backend):
        """Codex (#7630): a warm Lambda cached the previous week as
        unpublished; another instance then published it. The strict read
        must fetch, not return the stale cached copy, and the fresh result
        replaces the cache entry."""
        cloud_backend._episode_cache[(cloud_backend.prefix, "ep-1")] = {"episode_id": "ep-1"}

        fresh = {"episode_id": "ep-1", "published_at": "2026-09-28T01:00:00Z"}
        mock_list = MagicMock()
        mock_list.ok = True
        mock_list.json.return_value = {
            "blobs": [
                {
                    "url": "https://blob/episodes/ep-1.json",
                    "pathname": f"{cloud_backend.prefix}episodes/ep-1.json",
                }
            ]
        }
        mock_list.raise_for_status = MagicMock()
        mock_content = MagicMock()
        mock_content.content = json.dumps(fresh).encode("utf-8")
        mock_content.text = json.dumps(fresh)
        mock_content.json.return_value = fresh
        mock_content.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[mock_list, mock_content]) as get:
            result = cloud_backend.load_episode_strict("ep-1")

        assert get.call_count == 2
        assert result == fresh
        assert cloud_backend._episode_cache[(cloud_backend.prefix, "ep-1")] == fresh

class TestCloudBackendListEpisodes:
    def test_list_returns_sorted_episodes(self, cloud_backend):
        ep1 = {"episode_id": "ep-1", "created_at": "2026-03-01T00:00:00Z"}
        ep2 = {"episode_id": "ep-2", "created_at": "2026-03-10T00:00:00Z"}

        mock_list = MagicMock()
        mock_list.json.return_value = {
            "blobs": [
                {"pathname": "episodes/ep-1.json", "url": "https://cdn.example.com/ep1.json"},
                {"pathname": "episodes/ep-2.json", "url": "https://cdn.example.com/ep2.json"},
            ],
            "hasMore": False,
        }
        mock_list.raise_for_status = MagicMock()

        mock_content1 = MagicMock()
        mock_content1.json.return_value = ep1
        mock_content1.raise_for_status = MagicMock()

        mock_content2 = MagicMock()
        mock_content2.json.return_value = ep2
        mock_content2.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[mock_list, mock_content1, mock_content2]):
            results = cloud_backend.list_episodes()

        assert len(results) == 2
        # Newest first
        assert results[0]["episode_id"] == "ep-2"
        assert results[1]["episode_id"] == "ep-1"

    def test_list_falls_back_on_error(self, cloud_backend):
        with patch("requests.get", side_effect=Exception("timeout")):
            with patch.object(cloud_backend._fs, "list_episodes", return_value=[]) as mock_fs:
                results = cloud_backend.list_episodes()

        mock_fs.assert_called_once()
        assert results == []

    def test_strict_list_does_not_fall_back_on_error(self, cloud_backend):
        with patch("requests.get", side_effect=Exception("timeout")), \
             patch.object(cloud_backend._fs, "list_episodes") as mock_fs:
            with pytest.raises(Exception, match="timeout"):
                cloud_backend.list_episodes_strict()

        mock_fs.assert_not_called()

    def test_list_strips_storage_prefix_from_episode_ids(self, cloud_backend):
        cloud_backend.set_prefix("preview/")
        episode = {"episode_id": "2026-W34", "created_at": "2026-08-24T00:00:00Z"}
        mock_list = MagicMock()
        mock_list.json.return_value = {
            "blobs": [{
                "pathname": "preview/episodes/2026-W34.json",
                "url": "https://cdn.example.com/w34.json",
            }],
            "hasMore": False,
        }
        mock_list.raise_for_status = MagicMock()
        mock_content = MagicMock()
        mock_content.json.return_value = episode
        mock_content.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[mock_list, mock_content]):
            results = cloud_backend.list_episodes()

        assert [item["episode_id"] for item in results] == ["2026-W34"]

    def test_list_handles_pagination(self, cloud_backend):
        # First page
        page1 = MagicMock()
        page1.json.return_value = {
            "blobs": [{"pathname": "episodes/ep-1.json", "url": "https://cdn.example.com/ep1.json"}],
            "hasMore": True,
            "cursor": "abc123",
        }
        page1.raise_for_status = MagicMock()

        # Content for ep-1
        content1 = MagicMock()
        content1.json.return_value = {"episode_id": "ep-1", "created_at": "2026-03-01T00:00:00Z"}
        content1.raise_for_status = MagicMock()

        # Second page
        page2 = MagicMock()
        page2.json.return_value = {
            "blobs": [{"pathname": "episodes/ep-2.json", "url": "https://cdn.example.com/ep2.json"}],
            "hasMore": False,
        }
        page2.raise_for_status = MagicMock()

        # Content for ep-2
        content2 = MagicMock()
        content2.json.return_value = {"episode_id": "ep-2", "created_at": "2026-03-05T00:00:00Z"}
        content2.raise_for_status = MagicMock()

        with patch("requests.get", side_effect=[page1, content1, page2, content2]):
            results = cloud_backend.list_episodes()

        assert len(results) == 2


class TestCloudBackendNoToken:
    def test_falls_back_to_filesystem_without_token(self):
        """Without BLOB_READ_WRITE_TOKEN, all operations should use filesystem."""
        from backend.storage import _CloudBackend

        with patch.dict(os.environ, {}, clear=True):
            # Remove VERCEL_ENV so it doesn't raise
            os.environ.pop("VERCEL_ENV", None)
            os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
            backend = _CloudBackend()

        assert backend._has_cloud() is False

        with patch.object(backend._fs, "save_episode") as mock_save:
            backend.save_episode("ep-local", {"test": True})
        mock_save.assert_called_once()

        with patch.object(backend._fs, "load_episode", return_value=None) as mock_load:
            backend.load_episode("ep-local")
        mock_load.assert_called_once()

    def test_raises_on_vercel_without_token(self, monkeypatch):
        """On Vercel (VERCEL_ENV set) without token, should raise RuntimeError."""
        from backend.storage import _CloudBackend

        monkeypatch.setenv("VERCEL_ENV", "production")
        monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)

        with pytest.raises(RuntimeError, match="BLOB_READ_WRITE_TOKEN"):
            _CloudBackend()


class TestCloudBackendImageSiblings:
    @staticmethod
    def _png_bytes() -> bytes:
        image = Image.new("RGBA", (16, 8), (220, 80, 40, 255))
        image.putpixel((0, 0), (0, 0, 0, 0))
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    @staticmethod
    def _response(url: str) -> MagicMock:
        response = MagicMock()
        response.json.return_value = {"url": url}
        response.raise_for_status = MagicMock()
        return response

    def test_social_encoder_outputs_jpeg_at_social_dimensions(self):
        from backend.storage import SOCIAL_IMAGE_SIZE, _encode_social_jpeg

        result = _encode_social_jpeg(self._png_bytes())

        with Image.open(BytesIO(result)) as image:
            assert image.format == "JPEG"
            assert image.size == SOCIAL_IMAGE_SIZE == (1200, 630)
            assert image.mode == "RGB"

    def test_png_upload_keeps_canonical_and_uploads_deterministic_siblings(
        self, cloud_backend
    ):
        canonical = self._response("https://cdn.example.com/images/recipe/hero.png")
        webp = self._response("https://cdn.example.com/images/recipe/hero.webp")
        webp_400w = self._response("https://cdn.example.com/images/recipe/hero-400w.webp")
        webp_800w = self._response("https://cdn.example.com/images/recipe/hero-800w.webp")
        social = self._response("https://cdn.example.com/images/recipe/hero.social.jpg")
        jpeg_fallback = self._response("https://cdn.example.com/images/recipe/hero-1200w.jpg")

        with patch(
            "requests.put",
            side_effect=[canonical, webp, webp_400w, webp_800w, social, jpeg_fallback],
        ) as mock_put:
            result = cloud_backend.save_image(
                "src/assets/images/recipe/hero.png", self._png_bytes()
            )

        assert result == "https://cdn.example.com/images/recipe/hero.png"
        assert mock_put.call_count == 6

        (
            canonical_call,
            webp_call,
            webp_400_call,
            webp_800_call,
            social_call,
            jpeg_fallback_call,
        ) = mock_put.call_args_list
        assert [call.args[0] for call in mock_put.call_args_list] == [
            "https://blob.vercel-storage.com/images/recipe/hero.png",
            "https://blob.vercel-storage.com/images/recipe/hero.webp",
            "https://blob.vercel-storage.com/images/recipe/hero-400w.webp",
            "https://blob.vercel-storage.com/images/recipe/hero-800w.webp",
            "https://blob.vercel-storage.com/images/recipe/hero.social.jpg",
            "https://blob.vercel-storage.com/images/recipe/hero-1200w.jpg",
        ]

        assert canonical_call.kwargs["headers"]["Content-Type"] == "image/png"
        assert webp_call.kwargs["headers"]["Content-Type"] == "image/webp"
        assert webp_400_call.kwargs["headers"]["Content-Type"] == "image/webp"
        assert webp_800_call.kwargs["headers"]["Content-Type"] == "image/webp"
        assert jpeg_fallback_call.kwargs["headers"]["Content-Type"] == "image/jpeg"
        # Variant siblings use the same deterministic-pathname contract as
        # every other sibling upload (#5251) — otherwise the renderer's
        # srcset/fallback string rewrite can't resolve them.
        for call in (webp_400_call, webp_800_call, jpeg_fallback_call):
            assert call.kwargs["headers"]["x-add-random-suffix"] == "0"
            assert call.kwargs["headers"]["x-allow-overwrite"] == "1"
        # 400w variant is actually downscaled, not just re-encoded at full size.
        with Image.open(BytesIO(webp_400_call.kwargs["data"])) as image:
            assert image.width == 400
        social_headers = social_call.kwargs["headers"]
        assert social_headers == {
            "Authorization": "Bearer fake-token-for-test",
            "Content-Type": "image/jpeg",
            "x-vercel-access": "public",
            "x-add-random-suffix": "0",
            "x-allow-overwrite": "1",
        }
        with Image.open(BytesIO(social_call.kwargs["data"])) as image:
            assert image.format == "JPEG"
            assert image.size == (1200, 630)
        # JPEG fallback preserves aspect ratio (no crop) unlike the social sibling.
        with Image.open(BytesIO(jpeg_fallback_call.kwargs["data"])) as image:
            assert image.format == "JPEG"
            assert image.width == 1200

    def test_sibling_conversion_or_upload_failure_does_not_fail_png_publish(
        self, cloud_backend
    ):
        canonical = self._response("https://cdn.example.com/images/recipe/hero.png")

        with patch(
            "requests.put",
            side_effect=[
                canonical,
                RuntimeError("webp unavailable"),
                RuntimeError("400w variant unavailable"),
                RuntimeError("800w variant unavailable"),
                RuntimeError("social jpeg unavailable"),
                RuntimeError("jpeg fallback unavailable"),
            ],
        ) as mock_put:
            result = cloud_backend.save_image(
                "src/assets/images/recipe/hero.png", self._png_bytes()
            )

        assert result == "https://cdn.example.com/images/recipe/hero.png"
        assert mock_put.call_count == 6

        with (
            patch("backend.storage._encode_social_jpeg", side_effect=OSError("bad PNG")),
            patch("requests.put", return_value=canonical),
        ):
            result = cloud_backend.save_image(
                "src/assets/images/recipe/hero.png", self._png_bytes()
            )

        assert result == "https://cdn.example.com/images/recipe/hero.png"


class TestCanonicalPngKey:
    """_canonical_png_key strips Vercel's legacy random-hash suffix (#7185 review).

    The single shared function _webp_variant_key, _jpeg_fallback_key, and
    episode_renderer's URL rewriters all route through, so a historical
    suffixed PNG can never make the probe and the rendered URL disagree.
    """

    def test_strips_suffix(self):
        from backend.storage import _canonical_png_key

        assert _canonical_png_key("images/abc-9VSOT4SGhaUDoAUDM3kZPqxd3.png") == (
            "images/abc.png"
        )

    def test_clean_key_is_a_no_op(self):
        from backend.storage import _canonical_png_key

        assert _canonical_png_key("images/recipe/hero.png") == "images/recipe/hero.png"

    def test_short_hyphenated_segment_is_not_mistaken_for_a_suffix(self):
        """A real deterministic key can contain short hyphenated segments
        (recipe slugs, round-N variants) — only a 20+ char hash must strip."""
        from backend.storage import _canonical_png_key

        assert _canonical_png_key("images/2068c0cc/round_1/hero-closeup.png") == (
            "images/2068c0cc/round_1/hero-closeup.png"
        )


class TestWebpVariantKey:
    """Deterministic width-variant key naming (#6755)."""

    def test_appends_width_descriptor_before_webp_extension(self):
        from backend.storage import _webp_variant_key

        assert _webp_variant_key("images/recipe/hero.png", 400) == (
            "images/recipe/hero-400w.webp"
        )
        assert _webp_variant_key("images/recipe/hero.png", 800) == (
            "images/recipe/hero-800w.webp"
        )

    def test_rejects_non_png_key(self):
        from backend.storage import _webp_variant_key

        with pytest.raises(ValueError):
            _webp_variant_key("images/recipe/hero.webp", 400)

    def test_strips_vercel_random_suffix_before_building_the_variant_key(self):
        """#7185 review, HIGH: a historical suffixed PNG key must resolve to
        the SAME variant key the renderer's stripped URL rewrite expects —
        otherwise a backfilled/probed variant lives at a key the page never
        points at."""
        from backend.storage import _webp_variant_key

        assert _webp_variant_key(
            "images/abc-9VSOT4SGhaUDoAUDM3kZPqxd3.png", 400
        ) == "images/abc-400w.webp"


class TestEncodeWebpVariant:
    """_encode_webp resizing (#6755)."""

    @staticmethod
    def _png_bytes(size=(160, 80)) -> bytes:
        image = Image.new("RGB", size, (10, 20, 30))
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def test_downscales_by_width_preserving_aspect_ratio(self):
        from backend.storage import _encode_webp

        result = _encode_webp(self._png_bytes(size=(1536, 1536)), width=400)
        with Image.open(BytesIO(result)) as image:
            assert image.format == "WEBP"
            assert image.size == (400, 400)

    def test_no_width_keeps_original_size(self):
        from backend.storage import _encode_webp

        result = _encode_webp(self._png_bytes(size=(160, 80)))
        with Image.open(BytesIO(result)) as image:
            assert image.size == (160, 80)

    def test_non_square_aspect_ratio_preserved(self):
        from backend.storage import _encode_webp

        result = _encode_webp(self._png_bytes(size=(1600, 800)), width=400)
        with Image.open(BytesIO(result)) as image:
            assert image.size == (400, 200)


class TestCloudBackendImageVariantsAvailable:
    """storage.image_variants_available (#6755) — the ordering-hazard guard.

    The renderer must never emit a srcset candidate that 404s, so this is
    the single source of truth the renderer's fallback logic depends on.
    """

    def test_true_when_400w_variant_blob_exists(self, cloud_backend):
        """Probe the PUBLIC url, unauthenticated. The API host answers 404 for
        blobs that exist (verified live 2026-09-05), which silently disabled
        every srcset even after the variants were uploaded."""
        response = MagicMock()
        response.status_code = 200
        with patch("requests.head", return_value=response) as mock_head:
            assert cloud_backend.image_variants_available("recipe/hero.png") is True

        called = [c.args[0] for c in mock_head.call_args_list]
        assert called == [
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/recipe/hero-400w.webp",
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/recipe/hero-800w.webp",
        ], "every configured width is probed, in order"
        assert all("headers" not in c.kwargs for c in mock_head.call_args_list), "public probe must not send the token"

    def test_false_when_only_the_smallest_variant_exists(self, cloud_backend):
        """Uploads are per-width and best-effort; a 400w without an 800w must
        not advertise an 800w candidate that would 404 (review, 2026-09-05)."""
        ok, missing = MagicMock(), MagicMock()
        ok.status_code, missing.status_code = 200, 404
        with patch("requests.head", side_effect=[ok, missing]):
            assert cloud_backend.image_variants_available("recipe/hero.png") is False

    def test_false_when_400w_variant_blob_missing(self, cloud_backend):
        response = MagicMock()
        response.status_code = 404
        with patch("requests.head", return_value=response):
            assert cloud_backend.image_variants_available("recipe/hero.png") is False

    def test_a_redirect_or_error_status_is_not_presence(self, cloud_backend):
        for code in (301, 403, 500):
            response = MagicMock()
            response.status_code = code
            with patch("requests.head", return_value=response):
                assert cloud_backend.image_variants_available("recipe/hero.png") is False, code

    def test_false_on_network_error(self, cloud_backend):
        with patch("requests.head", side_effect=Exception("timeout")):
            assert cloud_backend.image_variants_available("recipe/hero.png") is False

    def test_false_for_non_png_key_without_a_network_call(self, cloud_backend):
        with patch("requests.head") as mock_head:
            assert cloud_backend.image_variants_available("recipe/hero.webp") is False
        mock_head.assert_not_called()

    def test_respects_prefix_for_test_mode_isolation(self, cloud_backend):
        cloud_backend.set_prefix("test/")
        response = MagicMock()
        response.status_code = 200
        with patch("requests.head", return_value=response) as mock_head:
            cloud_backend.image_variants_available("recipe/hero.png")

        called_url = mock_head.call_args_list[0].args[0]
        assert called_url == (
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/test/images/recipe/hero-400w.webp"
        )

    def test_falls_back_to_filesystem_without_cloud_token(self):
        from backend.storage import _CloudBackend

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VERCEL_ENV", None)
            os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
            backend = _CloudBackend()

        with patch.object(
            backend._fs, "image_variants_available", return_value=True
        ) as mock_fs:
            assert backend.image_variants_available("recipe/hero.png") is True
        mock_fs.assert_called_once_with("recipe/hero.png")


class TestJpegFallbackKey:
    """Deterministic sized-JPEG fallback key naming (#7185)."""

    def test_appends_width_descriptor_before_jpg_extension(self):
        from backend.storage import JPEG_FALLBACK_WIDTH, _jpeg_fallback_key

        assert _jpeg_fallback_key("images/recipe/hero.png") == (
            f"images/recipe/hero-{JPEG_FALLBACK_WIDTH}w.jpg"
        )

    def test_rejects_non_png_key(self):
        from backend.storage import _jpeg_fallback_key

        with pytest.raises(ValueError):
            _jpeg_fallback_key("images/recipe/hero.webp")

    def test_distinct_from_social_jpeg_key(self):
        """The fallback sibling and the social crop must never collide."""
        from backend.storage import _jpeg_fallback_key, _social_jpeg_key

        assert _jpeg_fallback_key("images/recipe/hero.png") != _social_jpeg_key(
            "images/recipe/hero.png"
        )

    def test_strips_vercel_random_suffix_before_building_the_fallback_key(self):
        """#7185 review, HIGH: for a historical suffixed PNG, the key this
        function returns is what the uploader, the backfill script, AND the
        renderer's existence probe all use — it must be the canonical
        (stripped) key, matching episode_renderer._to_jpeg_fallback_url's
        rendered URL, or a backfilled sibling lives at a key nothing ever
        requests."""
        from backend.storage import JPEG_FALLBACK_WIDTH, _jpeg_fallback_key

        assert _jpeg_fallback_key("images/abc-9VSOT4SGhaUDoAUDM3kZPqxd3.png") == (
            f"images/abc-{JPEG_FALLBACK_WIDTH}w.jpg"
        )


class TestSourcePngKey:
    """_source_png_key (#7185 review round 2, HIGH) — the inverse of every
    sibling-key builder above. Anything reading a hero identity back out of
    rendered HTML (scripts/pin_published_heroes.py) must route through this
    before storing it, or a JPEG/WebP URL can get permanently pinned as the
    hero's "source PNG"."""

    def test_jpeg_fallback_inverts_to_source_png(self):
        from backend.storage import JPEG_FALLBACK_WIDTH, _source_png_key

        assert _source_png_key(f"images/recipe/hero-{JPEG_FALLBACK_WIDTH}w.jpg") == (
            "images/recipe/hero.png"
        )

    def test_webp_width_variant_inverts_to_source_png(self):
        from backend.storage import _source_png_key

        assert _source_png_key("images/recipe/hero-400w.webp") == "images/recipe/hero.png"
        assert _source_png_key("images/recipe/hero-800w.webp") == "images/recipe/hero.png"

    def test_bare_full_size_webp_is_left_ambiguous_not_inverted(self):
        """#7185 review round 3, MEDIUM: a width-less '.webp' is what
        _upload_webp_sibling names the derived full-size sibling, but a
        filename alone cannot prove THIS one is that sibling rather than an
        original, hand-authored webp with no PNG behind it. Resolving that
        ambiguity needs real I/O (an existence check), which does not belong
        in this pure storage-layer function — see
        scripts/pin_published_heroes.py's _webp_source_png_if_it_exists."""
        from backend.storage import _source_png_key

        assert _source_png_key("images/recipe/hero.webp") == "images/recipe/hero.webp"

    def test_social_jpeg_inverts_to_source_png(self):
        from backend.storage import _source_png_key

        assert _source_png_key("images/recipe/hero.social.jpg") == "images/recipe/hero.png"

    def test_png_key_passes_through_unchanged(self):
        """Deliberately does NOT also canonicalize a Vercel suffix here —
        this is the inverse of the sibling-key builders, not a second copy
        of _canonical_png_key's job, and scripts/pin_published_heroes.py
        already round-trips a suffixed PNG unchanged."""
        from backend.storage import _source_png_key

        assert _source_png_key("images/recipe/hero.png") == "images/recipe/hero.png"
        assert _source_png_key("images/abc-9VSOT4SGhaUDoAUDM3kZPqxd3.png") == (
            "images/abc-9VSOT4SGhaUDoAUDM3kZPqxd3.png"
        )

    def test_unrecognized_format_passes_through_unchanged(self):
        """A seed .webp with no PNG sibling at all must not be rewritten to
        a PNG key that was never uploaded."""
        from backend.storage import _source_png_key

        assert _source_png_key("classic-blueberry-muffins.jpeg") == (
            "classic-blueberry-muffins.jpeg"
        )


class TestEncodeJpegFallback:
    """_encode_jpeg_fallback (#7185 review round 4) — a fixed
    JPEG_FALLBACK_WIDTH x JPEG_FALLBACK_HEIGHT (16:9, HERO_ASPECT) center
    crop, TRUE BY CONSTRUCTION regardless of the source's own aspect ratio.
    Round 3 used a fixed SQUARE crop instead — also true by construction,
    but visually wrong: cropping a wide source to a square first, then
    having the hero box's own `object-fit: cover` crop that square again to
    16:9, throws away real content (see the function's docstring)."""

    @staticmethod
    def _png_bytes(size=(1536, 1536)) -> bytes:
        image = Image.new("RGBA", size, (10, 20, 30, 255))
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def test_square_source_encodes_to_the_fallback_16_9(self):
        from backend.storage import (
            JPEG_FALLBACK_HEIGHT,
            JPEG_FALLBACK_WIDTH,
            _encode_jpeg_fallback,
        )

        result = _encode_jpeg_fallback(self._png_bytes(size=(1536, 1536)))
        with Image.open(BytesIO(result)) as image:
            assert image.format == "JPEG"
            assert image.size == (JPEG_FALLBACK_WIDTH, JPEG_FALLBACK_HEIGHT)
            assert image.mode == "RGB"

    def test_wide_2_to_1_source_is_center_cropped_to_the_same_fixed_16_9(self):
        """The core round-4 regression case: a real, wide 2:1 source must
        STILL come out exactly 1200x675 — matching the hero box's own 16:9
        exactly, not a square that the browser would have to crop AGAIN."""
        from backend.storage import (
            JPEG_FALLBACK_HEIGHT,
            JPEG_FALLBACK_WIDTH,
            _encode_jpeg_fallback,
        )

        result = _encode_jpeg_fallback(self._png_bytes(size=(1600, 800)))
        with Image.open(BytesIO(result)) as image:
            assert image.size == (JPEG_FALLBACK_WIDTH, JPEG_FALLBACK_HEIGHT)

    def test_tall_3_to_4_source_is_center_cropped_to_the_same_fixed_16_9(self):
        from backend.storage import (
            JPEG_FALLBACK_HEIGHT,
            JPEG_FALLBACK_WIDTH,
            _encode_jpeg_fallback,
        )

        result = _encode_jpeg_fallback(self._png_bytes(size=(900, 1200)))
        with Image.open(BytesIO(result)) as image:
            assert image.size == (JPEG_FALLBACK_WIDTH, JPEG_FALLBACK_HEIGHT)

    def test_transparency_composited_onto_white(self):
        from backend.storage import _encode_jpeg_fallback

        image = Image.new("RGBA", (1536, 1536), (0, 0, 0, 0))
        buf = BytesIO()
        image.save(buf, format="PNG")

        result = _encode_jpeg_fallback(buf.getvalue())
        with Image.open(BytesIO(result)) as decoded:
            assert decoded.convert("RGB").getpixel((0, 0)) == (255, 255, 255)


# --- CSS-aware hero aspect-ratio checker (#7185 review round 5) ---
#
# A plain regex search for the FIRST `.recipe-hero__image { ... }` block
# (the round-4 version of this guard) only ever sees the top-level rule —
# a later `@media` override changing the ratio for phones would leave that
# check green while phones actually lose content. This walks the real CSS
# structure instead: strip comments, recurse into every brace-nested block
# (so an @media wrapper is descended into, not skipped), and read the
# aspect-ratio declaration off every LEAF rule whose selector targets
# `.recipe-hero__image` specifically (never `-placeholder` or similar).

_HERO_SELECTOR_RE = re.compile(r"\.recipe-hero__image(?![\w-])")
_ASPECT_RATIO_RE = re.compile(r"aspect-ratio\s*:\s*(\d+)\s*/\s*(\d+)\s*;?")


def _iter_css_leaf_rules(css: str):
    """Yield (selector, declarations) for every CSS rule whose body holds
    declarations, not further nested rules — at ANY nesting depth. A block
    whose body itself contains `{` (an @-rule wrapper like @media/@supports)
    is recursed into instead of yielded, so a selector nested inside it is
    still found."""

    def scan(text: str):
        i = 0
        n = len(text)
        sel_start = 0
        while i < n:
            if text[i] == "{":
                selector = text[sel_start:i]
                depth = 1
                j = i + 1
                while j < n and depth:
                    if text[j] == "{":
                        depth += 1
                    elif text[j] == "}":
                        depth -= 1
                    j += 1
                body = text[i + 1 : j - 1]
                if "{" in body:
                    yield from scan(body)
                else:
                    yield selector.strip(), body
                i = j
                sel_start = j
            else:
                i += 1

    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    yield from scan(stripped)


def _hero_aspect_ratios_in_css(css: str) -> list[tuple[int, int]]:
    """Every aspect-ratio declared on a rule targeting .recipe-hero__image,
    in document order, at any nesting depth (including inside @media)."""
    ratios = []
    for selector, body in _iter_css_leaf_rules(css):
        if not _HERO_SELECTOR_RE.search(selector):
            continue
        match = _ASPECT_RATIO_RE.search(body)
        if match:
            ratios.append((int(match.group(1)), int(match.group(2))))
    return ratios


class TestHeroAspectMatchesCss:
    """storage.HERO_ASPECT must track src/assets/site.css's actual rule(s)
    (#7185 review round 4, tightened round 5) — the whole point of
    _encode_jpeg_fallback's fixed-crop fix is that the fallback's shape
    matches the hero box's real CSS shape exactly, on EVERY viewport the
    CSS targets, not just the first rule in the file. If any rule (base or
    an @media override) ever sets a different ratio without HERO_ASPECT
    changing too, the fallback silently goes back to being a second, lossy
    crop inside `object-fit: cover` for whichever viewport that rule
    targets — this test is the tripwire."""

    def test_recipe_hero_image_aspect_ratio_matches_hero_aspect_everywhere(self):
        from backend.storage import HERO_ASPECT

        css_path = Path(__file__).resolve().parents[1] / "src" / "assets" / "site.css"
        css = css_path.read_text()

        ratios = _hero_aspect_ratios_in_css(css)
        assert ratios, "no .recipe-hero__image rule declares an aspect-ratio in site.css"
        assert all(ratio == HERO_ASPECT for ratio in ratios), (
            f"site.css declares aspect-ratio(s) {ratios} for .recipe-hero__image "
            f"across its rules (including any @media overrides), but "
            f"storage.HERO_ASPECT is {HERO_ASPECT} — some viewport would show "
            "a hero box shaped differently than the JPEG fallback's fixed crop; "
            "update HERO_ASPECT (and re-run scripts/backfill_image_variants.py)."
        )

        assert (
            "object-fit: cover" in css or "object-fit:cover" in css
        ), "the img object-fit:cover rule moved or was removed — re-check this test's assumptions"

    def test_checker_descends_into_media_queries_and_flags_a_disagreeing_override(self):
        """Proves the guard actually works rather than just looking
        plausible: a synthetic stylesheet with a base 16:9 rule plus a
        mobile @media override at a DIFFERENT ratio must yield BOTH values
        — exactly the drift the real test above exists to catch, and which
        a first-rule-only regex would have missed entirely."""
        synthetic_css = """
        .recipe-hero__image {
            aspect-ratio: 16 / 9;
            overflow: hidden;
        }
        .recipe-hero__image img { object-fit: cover; }
        @media (max-width: 480px) {
            .recipe-hero__image {
                aspect-ratio: 1 / 1;
            }
        }
        """
        ratios = _hero_aspect_ratios_in_css(synthetic_css)
        assert ratios == [(16, 9), (1, 1)]
        assert len(set(ratios)) > 1, "the checker must be able to detect a disagreement"

    def test_checker_strips_comments_before_matching(self):
        synthetic_css = """
        .recipe-hero__image { aspect-ratio: 16 / 9; }
        /* .recipe-hero__image { aspect-ratio: 1 / 1; } */
        """
        assert _hero_aspect_ratios_in_css(synthetic_css) == [(16, 9)]

    def test_checker_ignores_the_placeholder_class(self):
        """.recipe-hero__image-placeholder must never be mistaken for the
        hero image container itself."""
        synthetic_css = """
        .recipe-hero__image { aspect-ratio: 16 / 9; }
        .recipe-hero__image-placeholder { aspect-ratio: 4 / 3; }
        """
        assert _hero_aspect_ratios_in_css(synthetic_css) == [(16, 9)]

    def test_checker_finds_a_deeply_nested_at_rule_override(self):
        """@supports wrapping @media (or any other nesting depth) must
        still be descended into, not just one level of @media."""
        synthetic_css = """
        .recipe-hero__image { aspect-ratio: 16 / 9; }
        @supports (aspect-ratio: 1 / 1) {
            @media (max-width: 480px) {
                .recipe-hero__image { aspect-ratio: 1 / 1; }
            }
        }
        """
        assert _hero_aspect_ratios_in_css(synthetic_css) == [(16, 9), (1, 1)]


class TestCloudBackendJpegFallbackAvailable:
    """storage.jpeg_fallback_available (#7185) — same HEAD-probe contract as
    image_variants_available, for the single JPEG <img>-fallback sibling.
    """

    def test_true_when_fallback_blob_exists(self, cloud_backend):
        from backend.storage import JPEG_FALLBACK_WIDTH

        response = MagicMock()
        response.status_code = 200
        with patch("requests.head", return_value=response) as mock_head:
            assert cloud_backend.jpeg_fallback_available("recipe/hero.png") is True

        assert mock_head.call_args_list[0].args[0] == (
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/"
            f"images/recipe/hero-{JPEG_FALLBACK_WIDTH}w.jpg"
        )
        assert "headers" not in mock_head.call_args_list[0].kwargs, "public probe must not send the token"

    def test_suffixed_historical_key_probes_the_canonical_sibling(self, cloud_backend):
        """#7185 review, HIGH: a pre-#5251 PNG's lookup key still carries
        Vercel's random-hash suffix (episode_renderer._variant_lookup_key
        never strips it) — the probe must still check the canonical
        (stripped) sibling key, the same one _to_jpeg_fallback_url renders,
        not a suffixed key nothing ever uploads to."""
        from backend.storage import JPEG_FALLBACK_WIDTH

        response = MagicMock()
        response.status_code = 200
        with patch("requests.head", return_value=response) as mock_head:
            assert cloud_backend.jpeg_fallback_available(
                "recipe/hero-9VSOT4SGhaUDoAUDM3kZPqxd3.png"
            ) is True

        assert mock_head.call_args_list[0].args[0] == (
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/"
            f"images/recipe/hero-{JPEG_FALLBACK_WIDTH}w.jpg"
        )

    def test_false_when_fallback_blob_missing(self, cloud_backend):
        response = MagicMock()
        response.status_code = 404
        with patch("requests.head", return_value=response):
            assert cloud_backend.jpeg_fallback_available("recipe/hero.png") is False

    def test_false_on_network_error(self, cloud_backend):
        with patch("requests.head", side_effect=Exception("timeout")):
            assert cloud_backend.jpeg_fallback_available("recipe/hero.png") is False

    def test_false_for_non_png_key_without_a_network_call(self, cloud_backend):
        with patch("requests.head") as mock_head:
            assert cloud_backend.jpeg_fallback_available("recipe/hero.webp") is False
        mock_head.assert_not_called()

    def test_respects_prefix_for_test_mode_isolation(self, cloud_backend):
        from backend.storage import JPEG_FALLBACK_WIDTH

        cloud_backend.set_prefix("test/")
        response = MagicMock()
        response.status_code = 200
        with patch("requests.head", return_value=response) as mock_head:
            cloud_backend.jpeg_fallback_available("recipe/hero.png")

        called_url = mock_head.call_args_list[0].args[0]
        assert called_url == (
            "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/"
            f"test/images/recipe/hero-{JPEG_FALLBACK_WIDTH}w.jpg"
        )

    def test_falls_back_to_filesystem_without_cloud_token(self):
        from backend.storage import _CloudBackend

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VERCEL_ENV", None)
            os.environ.pop("BLOB_READ_WRITE_TOKEN", None)
            backend = _CloudBackend()

        with patch.object(
            backend._fs, "jpeg_fallback_available", return_value=True
        ) as mock_fs:
            assert backend.jpeg_fallback_available("recipe/hero.png") is True
        mock_fs.assert_called_once_with("recipe/hero.png")


class TestCloudBackendCleanupImageVariants:
    """#6712 — cloud cleanup must be an honest no-op, never touch the fs backend.

    Round-1 image variants are LIVE BTS-gallery content on every published
    page, not discarded photography candidates (see card #6712 scope
    correction). Delegating to the filesystem backend's send2trash (the
    original bug) silently did nothing on Vercel; the fix must not silently
    do something destructive instead.
    """

    def test_returns_empty_list(self, cloud_backend):
        assert cloud_backend.cleanup_image_variants("some-recipe-id") == []

    def test_never_delegates_to_filesystem_backend(self, cloud_backend):
        with patch.object(cloud_backend._fs, "cleanup_image_variants") as mock_fs:
            cloud_backend.cleanup_image_variants("some-recipe-id")
        mock_fs.assert_not_called()

    def test_never_calls_delete_by_prefix(self, cloud_backend):
        with patch.object(cloud_backend, "delete_by_prefix") as mock_delete:
            cloud_backend.cleanup_image_variants("some-recipe-id")
        mock_delete.assert_not_called()


@pytest.mark.parametrize("body", [[], "x", 5, None], ids=["list", "string", "number", "null"])
def test_strict_load_rejects_a_non_object_episode_body(cloud_backend, body):
    """Codex (#7630): valid JSON that is not an episode object must take the
    read-error path, never read as an unpublished week."""
    mock_list = MagicMock()
    mock_list.ok = True
    mock_list.json.return_value = {
        "blobs": [{"url": "https://blob/e.json", "pathname": f"{cloud_backend.prefix}episodes/ep-1.json"}]
    }
    mock_content = MagicMock()
    mock_content.json.return_value = body
    mock_content.raise_for_status = MagicMock()

    with patch("requests.get", side_effect=[mock_list, mock_content]):
        with pytest.raises(PageReadError):
            cloud_backend.load_episode_strict("ep-1")
    assert (cloud_backend.prefix, "ep-1") not in cloud_backend._episode_cache


@pytest.mark.parametrize("body", ["[]", '"x"', "5"], ids=["list", "string", "number"])
def test_filesystem_strict_load_rejects_a_non_object_episode_body(tmp_path, monkeypatch, body):
    """Codex (#7630): the filesystem strict read matches the cloud one."""
    from backend.storage import _FilesystemBackend

    monkeypatch.setattr("backend.storage.EPISODES_DIR", tmp_path)
    (tmp_path / "2026-W39.json").write_text(body)
    with pytest.raises(PageReadError):
        _FilesystemBackend().load_episode_strict("2026-W39")



def test_cloud_backend_without_a_token_uses_the_strict_filesystem_read(tmp_path, monkeypatch):
    """Codex (#7630): the no-token filesystem fallback of load_episode_strict
    must reject a non-object body like the cloud path does."""
    from backend.storage import _CloudBackend

    monkeypatch.setattr("backend.storage.EPISODES_DIR", tmp_path)
    (tmp_path / "2026-W39.json").write_text("[]")
    backend = _CloudBackend()
    assert not backend._has_cloud()
    with pytest.raises(PageReadError):
        backend.load_episode_strict("2026-W39")
