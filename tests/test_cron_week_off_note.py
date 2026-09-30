"""Cron-time "kitchen took the week off" note (#7630).

Codex review of an earlier version found that deciding "did the last closed
week publish?" per homepage request required an extra Blob read (correctness
demanded it essentially every day of the week, not just a 9-hour window),
that a per-request `episode_id` override broke health_check's teaser check,
and that an unbounded storage cache could keep serving a stale answer after a
late publish. The fix moves the decision to cron time: `_apply_week_off_note`
runs once at the start of Monday's cron (and cron_sunday's own
refuse-to-publish path handles the card's own motivating case on Sunday
evening), stamping a `week_off_note` field onto the episode that
`regenerate_and_upload` (tested in test_teaser_suppression.py) then carries
into whatever it writes to `pages/latest.json`. The teaser endpoint
(test_teaser_endpoint_suppression.py) just passes the field through.

This file covers the decision itself: when it's set, when it's cleared, that
it survives inside whatever prefix scope (test/prod) the caller established,
and the Sunday-side insertion.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.admin import cron_routes
from backend.storage import storage

# Monday of W37 (2026-09-07); W36 (2026-08-31 through 2026-09-06) is the
# week immediately before it — matches the fixtures in test_episode_integrity.py.
MONDAY_W37 = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _apply_week_off_note — the Monday-cron decision
# ---------------------------------------------------------------------------

def test_apply_week_off_note_sets_field_when_previous_week_has_no_episode():
    ep = {"episode_id": "2026-W37", "stages": {}}
    with patch.object(storage, "load_episode", return_value=None) as load_episode:
        cron_routes._apply_week_off_note(ep, now=MONDAY_W37)
    load_episode.assert_called_once_with("2026-W36")
    assert ep["week_off_note"] == {
        "message": cron_routes.WEEK_OFF_MESSAGE,
        "missed_week": "2026-W36",
    }


def test_apply_week_off_note_sets_field_when_previous_week_unpublished():
    previous = {"episode_id": "2026-W36", "stages": {"wednesday": {"status": "failed"}}}
    ep = {"episode_id": "2026-W37", "stages": {}}
    with patch.object(storage, "load_episode", return_value=previous):
        cron_routes._apply_week_off_note(ep, now=MONDAY_W37)
    assert ep["week_off_note"]["missed_week"] == "2026-W36"


def test_apply_week_off_note_clears_field_when_previous_week_published():
    previous = {"episode_id": "2026-W36", "published_at": "2026-09-06T00:10:00+00:00"}
    ep = {"episode_id": "2026-W37", "stages": {}, "week_off_note": {"stale": "leftover"}}
    with patch.object(storage, "load_episode", return_value=previous):
        cron_routes._apply_week_off_note(ep, now=MONDAY_W37)
    assert "week_off_note" not in ep


def test_apply_week_off_note_respects_the_active_prefix():
    """A test-mode Monday must read the previous week's episode under
    whatever prefix its caller's _test_mode_scope already established — no
    hardcoded/prefixless path — so it can never see or touch the prod
    previous-week episode (RUNBOOK Incident 1 / #5911 lineage, #7630)."""
    seen_prefixes: list[str] = []

    def fake_load_episode(_episode_id):
        seen_prefixes.append(storage.prefix)
        return {"published_at": "2026-09-06T00:10:00+00:00"}

    with patch.object(storage, "load_episode", side_effect=fake_load_episode):
        with storage.prefix_scope("test/"):
            cron_routes._apply_week_off_note({"episode_id": "2026-W37", "stages": {}}, now=MONDAY_W37)
        with storage.prefix_scope(""):
            cron_routes._apply_week_off_note({"episode_id": "2026-W37", "stages": {}}, now=MONDAY_W37)

    assert seen_prefixes == ["test/", ""]


# ---------------------------------------------------------------------------
# cron_sunday's own refuse-to-publish path (#7630's own motivating case)
# ---------------------------------------------------------------------------

def _request() -> SimpleNamespace:
    return SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/cron/sunday"))


def _body(episode_id: str = "2026-W40") -> cron_routes.StageRequest:
    return cron_routes.StageRequest(episode_id=episode_id, force=True)


def test_cron_sunday_sets_week_off_note_when_a_required_stage_is_incomplete():
    """The card's own motivating case: Wednesday never completed, so Sunday
    refuses to publish. This must set week_off_note on the episode, forward
    it into pages/latest.json via regenerate_and_upload, and STILL raise the
    same 400 — publish gating itself is unchanged."""
    episode = {
        "episode_id": "2026-W40",
        "concept": "Some Concept",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "X"}},
            "tuesday": {"status": "complete"},
        },
        "events": [],
    }

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "regenerate_and_upload") as regenerate:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(cron_routes.cron_sunday(_request()))

    assert exc_info.value.status_code == 400
    assert "wednesday" in exc_info.value.detail
    assert episode["week_off_note"] == {
        "message": cron_routes.WEEK_OFF_MESSAGE,
        "missed_week": "2026-W40",
    }
    save_episode.assert_called_once_with("2026-W40", episode)
    regenerate.assert_called_once_with(episode)


def test_cron_sunday_does_not_set_week_off_note_when_publish_succeeds():
    """Sanity check: the note-setting code path in the required-stages loop
    must not fire when every required stage IS complete."""
    episode = {
        "episode_id": "2026-W40",
        "concept": "Some Concept",
        "recipe_id": "abc123",
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {
                    "title": "Some Recipe",
                    "description": "A savory bite.",
                    "ingredients": [{"amount": "2", "item": "eggs"}],
                    "instructions": ["Whisk and bake."],
                },
            },
            "wednesday": {"status": "complete", "confirmed_winner": {}, "image_status": "auto_selected"},
        },
        "events": [],
        "image_urls": [],
    }

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes.storage, "save_page"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Devon Park", "message": "Ready."}], "PASS - ready",
         )), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    assert result["published"] is True
    assert "week_off_note" not in episode
