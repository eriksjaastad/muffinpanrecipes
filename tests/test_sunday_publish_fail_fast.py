"""The Sunday publish must fail loudly rather than report false success.

Before this, a Sunday publish could log two errors, write nothing a reader
could load, and still return ``{"published": true}``. Once the reader routes
point back at the lambda (#6793), the Blob pages these writes produce ARE the
reader pages, so a swallowed failure means a 404 recipe behind a publish that
claimed to succeed.

Mid-week stays deliberately fail-open: a failed re-render there is non-fatal
because the next stage rewrites the page from the same episode JSON.
"""

from __future__ import annotations

import pytest

from backend.admin import cron_routes
from backend.publishing import episode_renderer


def _episode() -> dict:
    return {
        "episode_id": "2026-W35",
        "concept": "Greek Spanakopita Cups",
        "image_urls": [],
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {"title": "Greek Spanakopita Cups", "ingredients": []},
            }
        },
        "events": [],
    }


class TestRegenerateAndUploadStrictMode:
    def test_defaults_to_fail_open_for_midweek_stages(self, monkeypatch):
        """Mid-week a failed render must not fail the cron."""
        monkeypatch.setattr(
            episode_renderer,
            "render_episode_page",
            lambda ep: (_ for _ in ()).throw(RuntimeError("render exploded")),
        )

        assert episode_renderer.regenerate_and_upload(_episode()) is None

    def test_strict_mode_raises_instead_of_returning_none(self, monkeypatch):
        """Sunday passes strict=True, so the same failure must propagate."""
        monkeypatch.setattr(
            episode_renderer,
            "render_episode_page",
            lambda ep: (_ for _ in ()).throw(RuntimeError("render exploded")),
        )

        with pytest.raises(RuntimeError, match="render exploded"):
            episode_renderer.regenerate_and_upload(_episode(), strict=True)


class TestPublishSundaySourcesFailsFast:
    def test_raises_when_the_recipe_page_write_fails(self, monkeypatch):
        """The recipe page is what /recipes/<slug> serves. It may not fail open."""
        monkeypatch.setattr(
            cron_routes, "regenerate_and_upload", lambda ep, **kw: "https://example/page"
        )
        monkeypatch.setattr(
            episode_renderer, "publish_recipe_to_catalog", lambda ep: "https://example/cat"
        )

        def _explode(pathname, html):
            raise RuntimeError("blob write failed")

        monkeypatch.setattr(cron_routes.storage, "save_page", _explode)

        with pytest.raises(RuntimeError, match="blob write failed"):
            cron_routes._publish_sunday_sources(_episode())

    def test_raises_when_the_episode_page_write_returns_none(self, monkeypatch):
        """A None return means nothing was written, even without an exception."""
        monkeypatch.setattr(cron_routes, "regenerate_and_upload", lambda ep, **kw: None)
        monkeypatch.setattr(
            episode_renderer, "publish_recipe_to_catalog", lambda ep: "https://example/cat"
        )

        with pytest.raises(RuntimeError, match="Episode page write did not complete"):
            cron_routes._publish_sunday_sources(_episode())

    def test_raises_when_the_recipe_title_is_missing(self, monkeypatch):
        """No title means no reader page can be written. That is a failure."""
        monkeypatch.setattr(
            cron_routes, "regenerate_and_upload", lambda ep, **kw: "https://example/page"
        )
        monkeypatch.setattr(
            episode_renderer, "publish_recipe_to_catalog", lambda ep: "https://example/cat"
        )

        episode = _episode()
        episode["stages"]["monday"]["recipe_data"]["title"] = ""

        with pytest.raises(RuntimeError, match="no recipe title"):
            cron_routes._publish_sunday_sources(episode)


class TestThisWeekDistinguishesPlaceholderFromFailure:
    """A 200 placeholder over a missing page is what hid broken publishes.

    HTTP status was never able to reveal the failure, which forced every
    downstream check onto a body-size heuristic.
    """

    def _call(self):
        import asyncio

        from backend.admin import episode_routes

        return asyncio.run(episode_routes.this_week_page())

    def test_returns_200_placeholder_before_the_week_has_an_episode(self, monkeypatch):
        """Legitimate pre-cron window: no episode yet, so a placeholder is correct."""
        from backend.admin import episode_routes

        monkeypatch.setattr(episode_routes.storage, "load_page", lambda p: None)
        monkeypatch.setattr(episode_routes.storage, "load_episode", lambda e: None)

        assert self._call().status_code == 200

    def test_returns_503_when_the_episode_exists_but_its_page_does_not(self, monkeypatch):
        """A publish wrote episode JSON and then failed to write the page."""
        from backend.admin import episode_routes

        monkeypatch.setattr(episode_routes.storage, "load_page", lambda p: None)
        monkeypatch.setattr(
            episode_routes.storage, "load_episode", lambda e: {"episode_id": e}
        )

        response = self._call()
        assert response.status_code == 503
        assert response.headers.get("Retry-After") == "300"

    def test_serves_the_stored_page_unmodified_when_present(self, monkeypatch):
        from backend.admin import episode_routes

        monkeypatch.setattr(episode_routes.storage, "load_page", lambda p: "<html>hi</html>")

        response = self._call()
        assert response.status_code == 200
        assert response.body.decode() == "<html>hi</html>"
