"""A failed catalog read must never become a re-seeded catalog (#7833).

``_CloudBackend.load_page`` used to return None both for "no such blob" and
for a non-OK Blob response. ``publish_recipe_to_catalog`` read None as "first
run", seeded from ``src/recipes.json``, prepended the new recipe and saved
that over ``pages/recipes.json``. Corrupt catalog JSON became
``{"recipes": []}`` and saved a one-recipe catalog. So one Blob 5xx or a
timeout during the Sunday publish could erase every cron-published recipe.

Every test here is stubbed: no Blob, no Vercel, no alerts.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import HTTPException

from backend.admin import cron_routes
from backend.publishing import episode_renderer
from backend.storage import PageReadError
from backend.utils.catalog import CatalogUnavailableError


# ---------------------------------------------------------------------------
# load_page: absent vs read failure
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_vercel_env(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


@pytest.fixture
def cloud_backend():
    from backend.storage import _CloudBackend

    with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake-token-for-test"}):
        return _CloudBackend()


class _Resp:
    """Minimal stand-in for requests.Response."""

    def __init__(self, status_code=200, payload=None, content=b"", json_error=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload
        self._json_error = json_error
        self.content = content

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


_LISTED = _Resp(200, {"blobs": [{"url": "https://cdn.example/pages/recipes.json"}]})


def _get_sequence(*responses):
    """A requests.get stub returning (or raising) each item in turn."""
    queue = list(responses)

    def _get(*args, **kwargs):
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return _get


class TestLoadPageDistinguishesAbsentFromFailed:
    def test_absent_blob_returns_none(self, cloud_backend):
        with patch("requests.get", _get_sequence(_Resp(200, {"blobs": []}))):
            assert cloud_backend.load_page("pages/recipes.json") is None

    def test_present_blob_returns_utf8_content(self, cloud_backend):
        body = '{"recipes": [{"title": "Café Cups — ‘smart’"}]}'
        with patch("requests.get", _get_sequence(_LISTED, _Resp(200, content=body.encode()))):
            assert cloud_backend.load_page("pages/recipes.json") == body

    @pytest.mark.parametrize("code", [500, 502, 503, 429, 401, 403, 404])
    def test_non_ok_list_response_raises(self, cloud_backend, code):
        # A 404 from the LIST endpoint is an API fault, not "no such blob":
        # absence is an OK list with no blobs.
        with patch("requests.get", _get_sequence(_Resp(code, {}))):
            with pytest.raises(PageReadError, match=f"HTTP {code}"):
                cloud_backend.load_page("pages/recipes.json")

    @pytest.mark.parametrize(
        "error",
        [requests.Timeout("read timed out"), requests.ConnectionError("reset")],
    )
    def test_list_network_error_raises(self, cloud_backend, error):
        with patch("requests.get", _get_sequence(error)):
            with pytest.raises(PageReadError):
                cloud_backend.load_page("pages/recipes.json")

    def test_list_bad_json_raises(self, cloud_backend):
        bad = _Resp(200, json_error=ValueError("Expecting value"))
        with patch("requests.get", _get_sequence(bad)):
            with pytest.raises(PageReadError, match="unusable payload"):
                cloud_backend.load_page("pages/recipes.json")

    @pytest.mark.parametrize("payload", [{}, {"blobs": None}, ["not", "a", "dict"]])
    def test_list_payload_without_blobs_list_raises(self, cloud_backend, payload):
        with patch("requests.get", _get_sequence(_Resp(200, payload))):
            with pytest.raises(PageReadError):
                cloud_backend.load_page("pages/recipes.json")

    @pytest.mark.parametrize("code", [500, 503, 404])
    def test_non_ok_content_response_raises(self, cloud_backend, code):
        # The list just said the blob exists, so even a content 404 is a
        # failed read rather than absence.
        with patch("requests.get", _get_sequence(_LISTED, _Resp(code))):
            with pytest.raises(PageReadError, match=f"HTTP {code}"):
                cloud_backend.load_page("pages/recipes.json")

    def test_content_timeout_raises(self, cloud_backend):
        with patch("requests.get", _get_sequence(_LISTED, requests.Timeout("slow"))):
            with pytest.raises(PageReadError, match="Timeout"):
                cloud_backend.load_page("pages/recipes.json")

    def test_listed_blob_without_url_raises(self, cloud_backend):
        with patch("requests.get", _get_sequence(_Resp(200, {"blobs": [{}]}))):
            with pytest.raises(PageReadError, match="no url"):
                cloud_backend.load_page("pages/recipes.json")

    def test_undecodable_content_raises(self, cloud_backend):
        with patch("requests.get", _get_sequence(_LISTED, _Resp(200, content=b"\xff\xfe\x00"))):
            with pytest.raises(PageReadError, match="UTF-8"):
                cloud_backend.load_page("pages/recipes.json")

    def test_page_cache_hit_needs_no_network(self, cloud_backend):
        cloud_backend._page_cache["pages/recipes.json"] = "cached"
        with patch("requests.get", side_effect=AssertionError("network used")):
            assert cloud_backend.load_page("pages/recipes.json") == "cached"

    def test_filesystem_backend_absent_page_is_none(self, tmp_path, monkeypatch):
        from backend import storage as storage_module

        monkeypatch.setattr(storage_module, "ROOT", tmp_path)
        assert storage_module._FilesystemBackend().load_page("pages/recipes.json") is None


# ---------------------------------------------------------------------------
# publish_recipe_to_catalog: never re-seed over the live catalog
# ---------------------------------------------------------------------------


def _episode() -> dict:
    return {
        "episode_id": "2026-W40",
        "recipe_id": "abc12345",
        "image_urls": [],
        "stages": {
            "monday": {
                "recipe_data": {
                    "title": "Portuguese Custard Cups",
                    "category": "sweet",
                    "description": "Custard tarts baked in a muffin pan.",
                    "ingredients": [{"amount": "4", "item": "egg yolks"}],
                    "instructions": ["Bake."],
                }
            }
        },
    }


_LIVE_CATALOG = {
    "recipes": [
        {"slug": f"cron-recipe-{i}", "title": f"Cron Recipe {i}", "episode_id": f"2026-W{i:02d}"}
        for i in range(10, 40)
    ]
}


def _publish(load_page, *, prefix=""):
    """Run publish_recipe_to_catalog against stubbed storage; return save_page mock."""
    save_page = MagicMock(return_value="https://blob.example/pages/recipes.json")
    kwargs = {"side_effect": load_page} if callable(load_page) or isinstance(
        load_page, BaseException
    ) else {"return_value": load_page}
    with patch.object(episode_renderer.storage, "load_page", **kwargs), \
         patch.object(episode_renderer.storage, "save_page", save_page), \
         patch.object(episode_renderer.storage, "prefix", prefix):
        result = episode_renderer.publish_recipe_to_catalog(_episode())
    return result, save_page


class TestPublishRecipeToCatalogNeverReseeds:
    def test_blob_read_failure_aborts_without_writing(self):
        save_page = MagicMock()
        with patch.object(episode_renderer.storage, "load_page",
                          side_effect=PageReadError("HTTP 503")), \
             patch.object(episode_renderer.storage, "save_page", save_page), \
             patch.object(episode_renderer.storage, "prefix", ""):
            with pytest.raises(PageReadError):
                episode_renderer.publish_recipe_to_catalog(_episode())
        save_page.assert_not_called()

    @pytest.mark.parametrize("raw", ["{not json", "", '{"recipes": "nope"}', "{}", "[]"])
    def test_corrupt_or_malformed_catalog_aborts_without_writing(self, raw):
        save_page = MagicMock()
        with patch.object(episode_renderer.storage, "load_page", return_value=raw), \
             patch.object(episode_renderer.storage, "save_page", save_page), \
             patch.object(episode_renderer.storage, "prefix", ""):
            with pytest.raises(CatalogUnavailableError):
                episode_renderer.publish_recipe_to_catalog(_episode())
        save_page.assert_not_called()

    def test_absent_production_catalog_aborts_and_never_reads_the_seed_file(self):
        save_page = MagicMock()
        real_open = open

        def _guarded_open(path, *args, **kwargs):
            assert not str(path).endswith(os.path.join("src", "recipes.json")), (
                "production publish must never read the static seed catalog"
            )
            return real_open(path, *args, **kwargs)

        with patch.object(episode_renderer.storage, "load_page", return_value=None), \
             patch.object(episode_renderer.storage, "save_page", save_page), \
             patch.object(episode_renderer.storage, "prefix", ""), \
             patch("builtins.open", _guarded_open):
            with pytest.raises(CatalogUnavailableError, match="absent"):
                episode_renderer.publish_recipe_to_catalog(_episode())
        save_page.assert_not_called()

    def test_absent_test_namespace_catalog_still_seeds(self):
        """run_full_week.py --test starts from an empty test/ prefix every run."""
        result, save_page = _publish(None, prefix="test/")

        assert result == "https://blob.example/pages/recipes.json"
        pathname, content = save_page.call_args.args
        assert pathname == "pages/recipes.json"
        saved = json.loads(content)
        seed_path = os.path.join(
            os.path.dirname(episode_renderer.__file__), "..", "..", "src", "recipes.json"
        )
        with open(seed_path, encoding="utf-8") as f:
            seed = json.load(f)
        assert saved["recipes"][0]["slug"] == "portuguese-custard-cups"
        assert len(saved["recipes"]) == len(seed["recipes"]) + 1

    def test_healthy_catalog_keeps_every_recipe_and_prepends(self):
        result, save_page = _publish(json.dumps(_LIVE_CATALOG))

        assert result is not None
        saved = json.loads(save_page.call_args.args[1])
        assert saved["recipes"][0]["slug"] == "portuguese-custard-cups"
        assert saved["recipes"][1:] == _LIVE_CATALOG["recipes"]

    def test_empty_but_well_formed_catalog_is_not_an_error(self):
        result, save_page = _publish(json.dumps({"recipes": []}))

        assert result is not None
        assert len(json.loads(save_page.call_args.args[1])["recipes"]) == 1


# ---------------------------------------------------------------------------
# The Sunday handoff routes the abort through the existing failure + alert path
# ---------------------------------------------------------------------------


def _sunday_episode() -> dict:
    ep = _episode()
    ep["concept"] = "Portuguese Custard Cups"
    ep["stages"]["monday"]["status"] = "complete"
    ep["static_deploy"] = {"status": "pending"}
    return ep


@pytest.mark.parametrize(
    "load_page_kwargs",
    [
        {"side_effect": PageReadError("Blob list for 'pages/recipes.json' returned HTTP 503")},
        {"return_value": "{not json"},
        {"return_value": None},
    ],
    ids=["blob-5xx", "corrupt-json", "absent-in-production"],
)
def test_sunday_handoff_alerts_and_leaves_catalog_untouched(load_page_kwargs):
    ep = _sunday_episode()
    save_page = MagicMock(return_value="https://blob.example/x")

    with patch.object(cron_routes, "regenerate_and_upload", return_value="https://blob.example/page"), \
         patch.object(cron_routes.storage, "load_page", **load_page_kwargs), \
         patch.object(cron_routes.storage, "save_page", save_page), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes.storage, "prefix", ""), \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        with pytest.raises(HTTPException) as exc_info:
            cron_routes._complete_static_source_handoff(ep["episode_id"], ep)

    assert exc_info.value.status_code == 500
    # The catalog and the reader page were never written.
    written = [c.args[0] for c in save_page.call_args_list]
    assert "pages/recipes.json" not in written
    assert not any(p.startswith("pages/recipes/") for p in written)
    # Alerted through the same channel as every other source-write failure.
    notify.assert_called_once()
    assert notify.call_args.kwargs["stage"] == "sunday (source write)"
    # And a retry marker was persisted so a re-fire resumes the handoff.
    assert ep["static_deploy"]["status"] == "failed"
    assert ep["static_deploy"]["phase"] == "sources"
    save_episode.assert_called()
