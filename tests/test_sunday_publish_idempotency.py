"""Sunday publish idempotency guards."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.admin import cron_routes


def _request() -> SimpleNamespace:
    return SimpleNamespace(url=SimpleNamespace(path="/api/cron/sunday"))


def _body(episode_id: str = "2026-W20") -> cron_routes.StageRequest:
    return cron_routes.StageRequest(episode_id=episode_id, force=True)


def test_cron_sunday_returns_without_side_effects_when_already_published():
    episode = {
        "episode_id": "2026-W20",
        "concept": "Herbed Sausage Sunrise Cups",
        "published_at": "2026-05-17T12:00:00+00:00",
        "stages": {
            "sunday": {
                "status": "complete",
                "dialogue": [{"character": "Devon Park", "message": "Published."}],
            }
        },
        "events": [],
    }

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "_generate_and_judge_dialogue") as generate_dialogue, \
         patch.object(cron_routes, "_editorial_qa_review") as qa_review, \
         patch.object(cron_routes, "regenerate_and_upload") as regenerate, \
         patch.object(cron_routes, "trigger_vercel_deploy_hook") as trigger_hook:
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    assert result["published"] is True
    assert result["already_published"] is True
    assert result["published_at"] == "2026-05-17T12:00:00+00:00"
    assert result["dialogue_messages"] == 1
    save_episode.assert_not_called()
    generate_dialogue.assert_not_called()
    qa_review.assert_not_called()
    regenerate.assert_not_called()
    trigger_hook.assert_not_called()


def test_cron_sunday_still_publishes_unpublished_episode(monkeypatch, caplog):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("VERCEL_DEPLOY_HOOK_URL", raising=False)

    episode = {
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

    saved_states = []

    def capture_save(_episode_id, data):
        saved_states.append(data["static_deploy"]["status"])

    with caplog.at_level(logging.WARNING), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode", side_effect=capture_save) as save_episode, \
         patch.object(cron_routes.storage, "save_page") as save_page, \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Devon Park", "message": "Ready."}],
             "PASS - ready",
         )) as generate_dialogue, \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "trigger_vercel_deploy_hook", wraps=cron_routes.trigger_vercel_deploy_hook) as trigger_hook, \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog") as publish_catalog, \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    assert result["published"] is True
    assert "already_published" not in result
    assert episode["published_at"]
    generate_dialogue.assert_called_once()
    assert save_episode.call_count == 2
    assert saved_states == ["pending", "source_ready"]
    assert episode["static_deploy"]["status"] == "source_ready"
    publish_catalog.assert_called_once()
    save_page.assert_called_once()
    trigger_hook.assert_called_once_with(test_mode=False)
    assert any("VERCEL_DEPLOY_HOOK_URL is not configured" in record.getMessage() for record in caplog.records)
