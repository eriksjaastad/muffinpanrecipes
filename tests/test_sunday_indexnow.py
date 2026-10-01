"""IndexNow submission from the Sunday publish hook (#7806).

Two things matter here beyond the happy path already covered in
test_sunday_publish_idempotency.py: a test-mode Sunday must make ZERO
IndexNow calls (RUNBOOK Incident 1 was exactly this shape of leak — test data
reaching a real destination), and a submission failure must never raise into
the publish path. No network: `_indexnow_submit_urls` is mocked throughout.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.admin import cron_routes
from backend.utils.indexnow import IndexNowResult


def _request() -> SimpleNamespace:
    return SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/cron/sunday"))


def _episode() -> dict:
    return {
        "episode_id": "2026-W20",
        "concept": "Herbed Sausage Sunrise Cups",
        "recipe_id": "e9f30301",
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {
                    "title": "Herbed Sausage Sunrise Cups",
                    "description": "A savory breakfast bite.",
                    "ingredients": [{"amount": "2", "item": "eggs"}],
                    "instructions": ["Whisk and bake."],
                },
            },
            "wednesday": {
                "status": "complete",
                "confirmed_winner": {},
                "image_status": "auto_selected",
            },
        },
        "events": [],
        "image_urls": [],
    }


def _run_sunday(body: cron_routes.StageRequest, indexnow_result: IndexNowResult):
    """Drive cron_sunday through the full first-time-publish path."""
    episode = _episode()

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes.storage, "save_page"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Devon Park", "message": "Ready."}],
             "PASS - ready",
         )), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "_indexnow_submit_urls", return_value=indexnow_result) as indexnow_submit, \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    return result, episode, indexnow_submit


def test_test_mode_sunday_makes_zero_indexnow_calls(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=True)

    result, episode, indexnow_submit = _run_sunday(
        body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )

    assert result["published"] is True
    indexnow_submit.assert_not_called()
    assert not any("indexnow" in event for event in episode["events"])


def test_production_sunday_submits_indexnow(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=False)

    result, episode, indexnow_submit = _run_sunday(
        body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )

    assert result["published"] is True
    indexnow_submit.assert_called_once_with([
        "https://muffinpanrecipes.com/recipes/herbed-sausage-sunrise-cups",
        "https://muffinpanrecipes.com/",
        "https://muffinpanrecipes.com/recipes",
    ])
    assert any("indexnow submitted" in event for event in episode["events"])


def test_indexnow_failure_is_recorded_and_never_raises(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=False)

    result, episode, indexnow_submit = _run_sunday(
        body, IndexNowResult(ok=False, status_code=429, detail="IndexNow returned HTTP 429: rate limited")
    )

    # The publish itself must succeed regardless of the IndexNow outcome.
    assert result["published"] is True
    indexnow_submit.assert_called_once()
    assert any("indexnow submission failed" in event for event in episode["events"])


def test_submitted_recipe_url_uses_the_catalog_slug_for_a_qualified_title():
    """The catalog strips a trailing parenthetical before slugifying; the
    IndexNow URL must be that same page, not a slug of the raw title that
    would 404."""
    from backend.publishing import episode_renderer

    episode = {
        "stages": {"monday": {"recipe_data": {
            "title": "Make-Ahead Veggie & Sausage Egg Cups (Weekly Muffin Pan Breakfast)",
        }}},
        "events": [],
    }
    captured = {}

    def fake_submit(urls):
        captured["urls"] = urls
        return IndexNowResult(ok=True, status_code=200, detail="submitted")

    with patch.object(cron_routes, "_indexnow_submit_urls", side_effect=fake_submit), \
         patch.object(cron_routes.storage, "save_episode"):
        cron_routes._submit_sunday_indexnow(episode, "2026-W20", "c")

    slug = episode_renderer.catalog_slug(episode)
    assert slug == episode_renderer._slugify("Make-Ahead Veggie & Sausage Egg Cups")
    assert captured["urls"][0] == f"https://muffinpanrecipes.com/recipes/{slug}"
