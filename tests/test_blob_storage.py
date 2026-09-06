"""Integration tests for Vercel Blob episode persistence (#5048).

Tests save/load/list operations on _CloudBackend, mocking the Vercel Blob
REST API to verify correct request structure, caching, and fallback behavior.
"""

import os
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


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
            "blobs": [{"url": "https://cdn.example.com/ep.json"}]
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
            "blobs": [{"url": "https://cdn.example.com/test-ep.json"}]
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

        with patch(
            "requests.put",
            side_effect=[canonical, webp, webp_400w, webp_800w, social],
        ) as mock_put:
            result = cloud_backend.save_image(
                "src/assets/images/recipe/hero.png", self._png_bytes()
            )

        assert result == "https://cdn.example.com/images/recipe/hero.png"
        assert mock_put.call_count == 5

        canonical_call, webp_call, webp_400_call, webp_800_call, social_call = (
            mock_put.call_args_list
        )
        assert [call.args[0] for call in mock_put.call_args_list] == [
            "https://blob.vercel-storage.com/images/recipe/hero.png",
            "https://blob.vercel-storage.com/images/recipe/hero.webp",
            "https://blob.vercel-storage.com/images/recipe/hero-400w.webp",
            "https://blob.vercel-storage.com/images/recipe/hero-800w.webp",
            "https://blob.vercel-storage.com/images/recipe/hero.social.jpg",
        ]

        assert canonical_call.kwargs["headers"]["Content-Type"] == "image/png"
        assert webp_call.kwargs["headers"]["Content-Type"] == "image/webp"
        assert webp_400_call.kwargs["headers"]["Content-Type"] == "image/webp"
        assert webp_800_call.kwargs["headers"]["Content-Type"] == "image/webp"
        # Variant siblings use the same deterministic-pathname contract as
        # every other sibling upload (#5251) — otherwise the renderer's
        # srcset string rewrite can't resolve them.
        for call in (webp_400_call, webp_800_call):
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
            ],
        ) as mock_put:
            result = cloud_backend.save_image(
                "src/assets/images/recipe/hero.png", self._png_bytes()
            )

        assert result == "https://cdn.example.com/images/recipe/hero.png"
        assert mock_put.call_count == 5

        with (
            patch("backend.storage._encode_social_jpeg", side_effect=OSError("bad PNG")),
            patch("requests.put", return_value=canonical),
        ):
            result = cloud_backend.save_image(
                "src/assets/images/recipe/hero.png", self._png_bytes()
            )

        assert result == "https://cdn.example.com/images/recipe/hero.png"


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
