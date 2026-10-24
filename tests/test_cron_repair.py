"""cron_repair re-runs a failed or missing day in the 3 hours after its window (#8605).

W39 and W42 each died on one failed day that nothing retried. The real
Tuesday handler runs against an in-memory store; only the paid dialogue call
is faked. W43 Tuesday's window opens 2026-10-20 15:15 UTC (14:30 + 45 min).
"""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from backend.admin import cron_routes
from backend.storage import EpisodeReadStale

WEEK = "2026-W43"


def _at(hour: int, minute: int = 20, day: int = 20) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


def _episode(**stages) -> dict:
    days = {"monday": {"status": "complete", "recipe_data": {"title": "Gougeres"}},
            "tuesday": {"status": "failed", "error": "outage"}}
    return {"episode_id": WEEK, "concept": "Gougeres", "recipe_id": "r1", "events": [],
            "stages": {**days, **stages}}


def _generator(*failing_calls):
    calls = iter(range(1, 10))

    def generate(*_a, **_kw):
        if next(calls) in failing_calls:
            raise RuntimeError("model outage")
        return [{"character": "Margaret", "message": "Rest them in the oven."}], "PASS"
    return generate


def _repair(store: dict, now: datetime, generate, read_back_fails=False):
    """Run cron_repair once at ``now``; returns (result, generate calls, give-up alert)."""
    reads = []

    def load(_id):
        reads.append(_id)
        if read_back_fails and len(reads) == 2:
            raise EpisodeReadStale("stale read")
        return copy.deepcopy(store["ep"])

    def save(_id, data):
        store["ep"] = copy.deepcopy(data)

    request = cast(Request, SimpleNamespace(method="GET", url=SimpleNamespace(path="/api/cron/repair")))
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_current_episode_id", return_value=WEEK), \
         patch.object(cron_routes, "_utc_now", return_value=now), \
         patch.object(cron_routes.storage, "load_episode_verified", side_effect=load), \
         patch.object(cron_routes.storage, "load_episode_strict",
                      side_effect=lambda _id: copy.deepcopy(store["ep"])), \
         patch.object(cron_routes.storage, "save_episode", side_effect=save), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", side_effect=generate) as gen, \
         patch.object(cron_routes, "_judge_meta_fields", return_value={}), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "_alert_below_bar"), \
         patch.object(cron_routes, "notify_pipeline_failure"), \
         patch.object(cron_routes, "notify_repair_exhausted", return_value=True) as gave_up:
        result = asyncio.run(cron_routes.cron_repair(request))
    return result, gen.call_count, gave_up


def test_a_day_that_fails_twice_then_passes_ends_complete_without_giving_up():
    store, generate = {"ep": _episode()}, _generator(1, 2)
    for hour in (15, 16, 17):
        result, ran, gave_up = _repair(store, _at(hour), generate)
        assert ran == 1
    assert result == {"stage": "tuesday", "episode_id": WEEK, "retry": 3, "status": "complete"}
    assert store["ep"]["stages"]["tuesday"]["status"] == "complete"

    result, ran, gave_up = _repair(store, _at(18), generate)
    assert result["status"] == "nothing_to_repair" and ran == 0
    gave_up.assert_not_called()


def test_three_failed_retries_give_up_once_in_the_fourth_hour_without_a_fourth_run():
    store, generate = {"ep": _episode()}, _generator(1, 2, 3, 4)
    assert [_repair(store, _at(hour), generate)[1] for hour in (15, 16, 17)] == [1, 1, 1]

    result, ran, gave_up = _repair(store, _at(18), generate)
    assert ran == 0 and result.status_code == 500
    gave_up.assert_called_once_with(WEEK, "tuesday")

    result, ran, gave_up = _repair(store, _at(19), generate)
    assert result["status"] == "nothing_to_repair" and ran == 0
    gave_up.assert_not_called()


def test_outside_the_three_hours_nothing_runs():
    for now in (_at(15, 0), _at(19), _at(15, 20, day=25)):
        store = {"ep": _episode(sunday={"status": "failed"})}
        result, ran, _ = _repair(store, now, _generator())
        assert result["status"] == "nothing_to_repair" and ran == 0


def test_a_later_day_is_not_retried_while_monday_has_no_recipe():
    store = {"ep": _episode(monday={"status": "failed"})}
    result, ran, _ = _repair(store, _at(15), _generator())
    assert result["status"] == "nothing_to_repair" and ran == 0


def test_an_unreadable_outcome_counts_as_unfinished():
    store = {"ep": _episode()}
    result, _, _ = _repair(store, _at(15), _generator(), read_back_fails=True)
    assert result.status_code == 500
    assert b"outcome unreadable: EpisodeReadStale" in result.body



def _outage(store: dict, request_path: str, now: datetime, generator_down: bool):
    """Run a route through the real judge loop during an outage; return the alerts sent."""
    def generate(*_a, **_kw):
        if generator_down:
            raise RuntimeError("model outage")
        return [{"character": "Margaret", "message": "Rest them."}]

    def judge(_concept, stage, _dialogue, episode, **_kw):
        episode.setdefault("judge_scores", {})[stage] = {}
        return False, "JUDGE ERROR: RuntimeError: provider down"

    def save(_id, data):
        store["ep"] = copy.deepcopy(data)

    handler = cron_routes.cron_repair if request_path.endswith("repair") else cron_routes.cron_tuesday
    request = cast(Request, SimpleNamespace(method="GET", url=SimpleNamespace(path=request_path)))
    sent = []
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_current_episode_id", return_value=WEEK), \
         patch.object(cron_routes, "_utc_now", return_value=now), \
         patch.object(cron_routes.storage, "load_episode_verified",
                      side_effect=lambda _id: copy.deepcopy(store["ep"])), \
         patch.object(cron_routes.storage, "load_episode_strict",
                      side_effect=lambda _id: copy.deepcopy(store["ep"])), \
         patch.object(cron_routes.storage, "save_episode", side_effect=save), \
         patch.object(cron_routes, "_generate_dialogue", side_effect=generate), \
         patch.object(cron_routes, "_judge_dialogue", judge), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}), \
         patch("backend.utils.discord.send_alert", side_effect=lambda **kw: sent.append(kw["subject"]) or True):
        if handler is cron_routes.cron_repair:
            assert asyncio.run(handler(request)).status_code == 500
        else:
            with pytest.raises(HTTPException):
                asyncio.run(handler(request))
    return sent


@pytest.mark.parametrize("generator_down, alert", [(False, "Episode paused"), (True, "Pipeline failed")],
                         ids=["judge-outage", "generator-outage"])
def test_a_failed_retry_holds_its_alerts_but_the_scheduled_run_still_sends_them(generator_down, alert):
    store = {"ep": _episode()}
    assert _outage(store, "/api/cron/repair", _at(15), generator_down) == []
    assert store["ep"]["stages"]["tuesday"]["status"] == "failed"
    assert _outage(store, "/api/cron/tuesday", _at(14, 31), generator_down) == [alert]


def test_the_hold_ends_with_its_block_even_on_an_exception():
    from backend.utils import discord

    with pytest.raises(RuntimeError), discord.failure_alerts_held():
        raise RuntimeError("stage blew up")
    assert discord._held("Pipeline failed", "tuesday") is False
