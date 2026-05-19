"""Sunday publish idempotency guards."""

from __future__ import annotations

import asyncio
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
         patch.object(cron_routes, "regenerate_and_upload") as regenerate:
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    assert result["published"] is True
    assert result["already_published"] is True
    assert result["published_at"] == "2026-05-17T12:00:00+00:00"
    assert result["dialogue_messages"] == 1
    save_episode.assert_not_called()
    generate_dialogue.assert_not_called()
    qa_review.assert_not_called()
    regenerate.assert_not_called()


def test_cron_sunday_still_publishes_unpublished_episode():
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

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes.storage, "save_page") as save_page, \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Devon Park", "message": "Ready."}],
             "PASS - ready",
         )) as generate_dialogue, \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog") as publish_catalog, \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    assert result["published"] is True
    assert "already_published" not in result
    assert episode["published_at"]
    generate_dialogue.assert_called_once()
    save_episode.assert_called_once()
    publish_catalog.assert_called_once()
    save_page.assert_called_once()
