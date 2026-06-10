"""Downstream stages must refuse to run when Monday produced no recipe.

W24 (2026-06-08): Monday hard-failed on the title validator, but Tuesday and
Wednesday crons ran anyway against the placeholder concept — Wednesday shot
55 images for a recipe that didn't exist and the live site rendered
"Weekly Muffin Pan Recipe" as the episode title. `_require_monday_recipe`
is the gate that stops that spend.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request

from backend.admin import cron_routes


def _request(day: str) -> Request:
    return cast(Request, SimpleNamespace(url=SimpleNamespace(path=f"/api/cron/{day}")))


def _body() -> cron_routes.StageRequest:
    return cron_routes.StageRequest(episode_id="2026-W24", force=True)


def _failed_monday_episode() -> dict:
    return {
        "episode_id": "2026-W24",
        "concept": "Weekly Muffin Pan Recipe",
        "stages": {
            "monday": {
                "status": "failed",
                "error": "Baker produced duplicate title twice.",
            }
        },
        "events": [],
    }


def _complete_monday_episode() -> dict:
    return {
        "episode_id": "2026-W24",
        "concept": "Cheddar Broccoli Egg Squares",
        "recipe_id": "ffd2aff5",
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {"title": "Cheddar Broccoli Egg Squares"},
            }
        },
        "events": [],
    }


@pytest.mark.parametrize("day,handler", [
    ("tuesday", cron_routes.cron_tuesday),
    ("wednesday", cron_routes.cron_wednesday),
    ("thursday", cron_routes.cron_thursday),
    ("friday", cron_routes.cron_friday),
    ("saturday", cron_routes.cron_saturday),
    ("sunday", cron_routes.cron_sunday),
])
def test_stage_blocked_when_monday_failed(day: str, handler) -> None:
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=_failed_monday_episode()), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "_generate_and_judge_dialogue") as generate_dialogue, \
         patch.object(cron_routes, "_get_orchestrator") as get_orchestrator, \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(handler(_request(day)))

    assert exc_info.value.status_code == 409
    assert "monday" in exc_info.value.detail
    generate_dialogue.assert_not_called()
    get_orchestrator.assert_not_called()
    save_episode.assert_not_called()
    notify.assert_called_once()


def test_stage_blocked_when_monday_missing_entirely() -> None:
    """A fresh episode with no Monday stage at all is also blocked."""
    episode = {"episode_id": "2026-W24", "concept": "x", "stages": {}, "events": []}
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "notify_pipeline_failure"):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(cron_routes.cron_tuesday(_request("tuesday")))

    assert exc_info.value.status_code == 409


def test_tuesday_runs_when_monday_complete() -> None:
    episode = _complete_monday_episode()
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Margaret Chen", "message": "Back to the bench."}],
             "PASS",
         )), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        result = asyncio.run(cron_routes.cron_tuesday(_request("tuesday")))

    assert result["status"] == "complete"
    save_episode.assert_called_once()
    notify.assert_not_called()
