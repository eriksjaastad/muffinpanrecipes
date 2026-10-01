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

import asyncio
import json
import logging
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from fastapi import HTTPException

from backend.admin import cron_routes, episode_routes
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

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


_LISTED = _Resp(
    200,
    {"blobs": [{"url": "https://cdn.example/pages/recipes.json", "pathname": "pages/recipes.json"}]},
)


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
        listing = _Resp(200, {"blobs": [{"pathname": "pages/recipes.json"}]})
        with patch("requests.get", _get_sequence(listing)):
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


# ---------------------------------------------------------------------------
# Exact pathname match: a same-prefix sibling is never the file (#7833 review)
# ---------------------------------------------------------------------------


def _blob(pathname: str) -> dict:
    return {"url": f"https://cdn.example/{pathname}", "pathname": pathname}


class _Recorder:
    """requests.get stub: answers the list call(s) in order, then serves
    content by URL, recording every URL fetched."""

    def __init__(self, listings, contents):
        self.listings = list(listings)
        self.contents = contents
        self.calls = []

    def __call__(self, url, *args, **kwargs):
        self.calls.append((url, kwargs.get("params")))
        if url == "https://blob.vercel-storage.com":
            return self.listings.pop(0)
        return self.contents[url]


class TestExactPathnameMatch:
    def test_load_page_sibling_only_listing_is_absent(self, cloud_backend):
        stub = _Recorder([_Resp(200, {"blobs": [_blob("pages/recipes.json.bak")]})], {})
        with patch("requests.get", stub):
            assert cloud_backend.load_page("pages/recipes.json") is None
        # The sibling's content was never fetched.
        assert all(url == "https://blob.vercel-storage.com" for url, _ in stub.calls)

    def test_load_page_sibling_listed_first_does_not_hide_the_real_file(self, cloud_backend):
        listing = _Resp(200, {"blobs": [_blob("pages/recipes.json.bak"), _blob("pages/recipes.json")]})
        stub = _Recorder(
            [listing],
            {
                "https://cdn.example/pages/recipes.json": _Resp(200, content=b'{"recipes": ["real"]}'),
                "https://cdn.example/pages/recipes.json.bak": _Resp(200, content=b'{"recipes": ["bak"]}'),
            },
        )
        with patch("requests.get", stub):
            assert cloud_backend.load_page("pages/recipes.json") == '{"recipes": ["real"]}'

    def test_load_page_real_file_on_a_later_list_page_is_found(self, cloud_backend):
        page1 = _Resp(200, {"blobs": [_blob("pages/recipes.json.bak")], "hasMore": True, "cursor": "c1"})
        page2 = _Resp(200, {"blobs": [_blob("pages/recipes.json")], "hasMore": False})
        stub = _Recorder(
            [page1, page2],
            {"https://cdn.example/pages/recipes.json": _Resp(200, content=b"real")},
        )
        with patch("requests.get", stub):
            assert cloud_backend.load_page("pages/recipes.json") == "real"
        assert stub.calls[1][1]["cursor"] == "c1"

    def test_load_page_listing_does_not_rely_on_limit_one(self, cloud_backend):
        stub = _Recorder([_Resp(200, {"blobs": []})], {})
        with patch("requests.get", stub):
            cloud_backend.load_page("pages/recipes.json")
        assert stub.calls[0][1]["limit"] != "1"

    def test_load_page_has_more_without_cursor_is_a_read_error(self, cloud_backend):
        page1 = _Resp(200, {"blobs": [_blob("pages/recipes.json.bak")], "hasMore": True})
        with patch("requests.get", _Recorder([page1], {})):
            with pytest.raises(PageReadError, match="cursor"):
                cloud_backend.load_page("pages/recipes.json")

    def test_load_episode_strict_sibling_only_is_absent(self, cloud_backend):
        stub = _Recorder([_Resp(200, {"blobs": [_blob("episodes/2026-W40.json.bak")]})], {})
        with patch("requests.get", stub):
            assert cloud_backend.load_episode_strict("2026-W40") is None

    def test_load_episode_strict_picks_the_exact_episode(self, cloud_backend):
        listing = _Resp(200, {"blobs": [_blob("episodes/2026-W40.json.bak"), _blob("episodes/2026-W40.json")]})
        stub = _Recorder(
            [listing],
            {
                "https://cdn.example/episodes/2026-W40.json": _Resp(200, {"episode_id": "real"}),
                "https://cdn.example/episodes/2026-W40.json.bak": _Resp(200, {"episode_id": "bak"}),
            },
        )
        with patch("requests.get", stub):
            assert cloud_backend.load_episode_strict("2026-W40") == {"episode_id": "real"}

    def test_load_episode_sibling_only_is_absent_not_the_sibling(self, cloud_backend):
        stub = _Recorder([_Resp(200, {"blobs": [_blob("episodes/2026-W40.json.bak")]})], {})
        with patch("requests.get", stub), \
             patch.object(cloud_backend._fs, "load_episode", return_value=None) as fs_load:
            assert cloud_backend.load_episode("2026-W40") is None
        fs_load.assert_called_once_with("2026-W40")

    def test_load_episode_picks_the_exact_episode(self, cloud_backend):
        listing = _Resp(200, {"blobs": [_blob("episodes/2026-W40.json.bak"), _blob("episodes/2026-W40.json")]})
        stub = _Recorder(
            [listing],
            {
                "https://cdn.example/episodes/2026-W40.json": _Resp(200, {"episode_id": "real"}),
                "https://cdn.example/episodes/2026-W40.json.bak": _Resp(200, {"episode_id": "bak"}),
            },
        )
        with patch("requests.get", stub):
            assert cloud_backend.load_episode("2026-W40") == {"episode_id": "real"}

    def test_character_memory_week_sibling_only_is_not_found(self, cloud_backend):
        from backend.storage import CharacterMemoryUnavailable

        sibling = "character_memory/margaret-chen/2026-W40.json.bak"
        with patch("requests.get", _Recorder([_Resp(200, {"blobs": [_blob(sibling)]})], {})):
            with pytest.raises(CharacterMemoryUnavailable, match="not found"):
                cloud_backend.load_character_memory_week("margaret-chen", "2026-W40")

    def test_character_memory_week_picks_the_exact_blob(self, cloud_backend):
        real = "character_memory/margaret-chen/2026-W40.json"
        entry = {"week": "2026-W40", "recipe": "Real Cups", "memory": "the real one"}
        stub = _Recorder(
            [_Resp(200, {"blobs": [_blob(real + ".bak"), _blob(real)]})],
            {
                f"https://cdn.example/{real}": _Resp(200, entry),
                f"https://cdn.example/{real}.bak": _Resp(200, {**entry, "memory": "bak"}),
            },
        )
        with patch("requests.get", stub), \
             patch("backend.storage.validate_character_memory_entry", side_effect=lambda e: e):
            assert cloud_backend.load_character_memory_week("margaret-chen", "2026-W40") == entry


# ---------------------------------------------------------------------------
# Reader routes degrade loudly on a Blob read failure (#7833 review)
# ---------------------------------------------------------------------------


_BLOB_DOWN = PageReadError("Blob list for 'pages/x' returned HTTP 503")
_STATIC_CATALOG = (
    Path(episode_routes.__file__).resolve().parents[2] / "src" / "recipes.json"
).read_text()


def _errors(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def _read_fails():
    return patch.object(episode_routes.storage, "load_page", side_effect=_BLOB_DOWN)


class TestReaderRoutesDegradeLoudly:
    def test_this_week_serves_the_placeholder_and_logs(self, caplog):
        with _read_fails(), \
             patch.object(episode_routes.storage, "load_episode", return_value=None), \
             caplog.at_level(logging.ERROR):
            resp = asyncio.run(episode_routes.this_week_page())
        assert resp.status_code == 200
        assert "<html" in bytes(resp.body).decode().lower()
        assert any("/this-week" in m and "HTTP 503" in m for m in _errors(caplog))

    def test_this_week_still_503s_when_a_page_is_due(self, caplog):
        due = {"episode_id": "x", "stages": {"monday": {"status": "complete"}}}
        with _read_fails(), \
             patch.object(episode_routes.storage, "load_episode", return_value=due), \
             caplog.at_level(logging.ERROR):
            resp = asyncio.run(episode_routes.this_week_page())
        assert resp.status_code == 503
        assert any("/this-week" in m for m in _errors(caplog))

    def test_teaser_answers_no_episode_and_logs(self, caplog):
        with _read_fails(), caplog.at_level(logging.ERROR):
            resp = asyncio.run(episode_routes.get_episode_teaser())
        assert resp.status_code == 200
        assert json.loads(bytes(resp.body)) == {"status": "no_episode"}
        assert any("pages/latest.json" in m and "HTTP 503" in m for m in _errors(caplog))

    def test_recipes_json_serves_the_static_catalog_and_logs(self, caplog):
        with _read_fails(), caplog.at_level(logging.ERROR):
            resp = asyncio.run(episode_routes.recipes_json())
        assert resp.status_code == 200
        assert bytes(resp.body).decode() == _STATIC_CATALOG
        assert any("/recipes.json" in m and "HTTP 503" in m for m in _errors(caplog))

    def test_recipes_hub_renders_from_the_static_catalog_and_logs(self, caplog):
        with _read_fails(), caplog.at_level(logging.ERROR):
            resp = asyncio.run(episode_routes.recipes_index())
        assert resp.status_code == 200
        first_slug = json.loads(_STATIC_CATALOG)["recipes"][0]["slug"]
        assert f"/recipes/{first_slug}" in bytes(resp.body).decode()
        assert any("/recipes" in m and "HTTP 503" in m for m in _errors(caplog))

    def test_recipe_page_still_serves_a_seed_recipe_and_logs(self, caplog):
        slug = sorted(episode_routes._load_seed_recipes().keys())[0]
        with _read_fails(), caplog.at_level(logging.ERROR):
            resp = asyncio.run(episode_routes.recipe_page(slug))
        assert resp.status_code == 200
        assert any(slug in m and "HTTP 503" in m for m in _errors(caplog))

    def test_recipe_page_for_a_cron_recipe_errors_instead_of_a_false_404(self, caplog):
        with _read_fails(), caplog.at_level(logging.ERROR):
            with pytest.raises(PageReadError):
                asyncio.run(episode_routes.recipe_page("some-cron-recipe-cups"))
        assert any("some-cron-recipe-cups" in m for m in _errors(caplog))

    def test_absent_pages_keep_their_old_behaviour_without_error_logs(self, caplog):
        with patch.object(episode_routes.storage, "load_page", return_value=None), \
             patch.object(episode_routes.storage, "load_episode", return_value=None), \
             caplog.at_level(logging.ERROR):
            assert asyncio.run(episode_routes.this_week_page()).status_code == 200
            teaser = asyncio.run(episode_routes.get_episode_teaser())
            assert json.loads(bytes(teaser.body)) == {"status": "no_episode"}
            assert bytes(asyncio.run(episode_routes.recipes_json()).body).decode() == _STATIC_CATALOG
            assert asyncio.run(episode_routes.recipe_page("no-such-recipe")).status_code == 404
        assert _errors(caplog) == []

    def test_sitemap_stays_fail_closed(self):
        with _read_fails():
            with pytest.raises(PageReadError):
                asyncio.run(episode_routes.sitemap_xml())
