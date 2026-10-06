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
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.admin import cron_routes
from tests.photo_review_helpers import approved_wednesday
from backend.storage import storage
from backend.utils import episode_integrity
from backend.utils.indexnow import IndexNowResult

# Monday of W37 (2026-09-07); W36 (2026-08-31 through 2026-09-06) is the
# week immediately before it — matches the fixtures in test_episode_integrity.py.
MONDAY_W37 = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)
# W40's Sunday cron time has arrived (Sunday 2026-10-04, evening UTC).
AFTER_SUNDAY_W40 = datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc)
# W37's own Monday cron time: an ordinary on-time Monday run.
MONDAY_W37 = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)
# Thursday of W40, before W40's own Sunday window.
THURSDAY_W40 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def before_sunday_w40():
    """These tests were written on 2026-10-02 against the real clock and
    silently assumed W40's Sunday had not arrived; pin that instead."""
    with patch.object(cron_routes, "_utc_now", return_value=THURSDAY_W40):
        yield


@pytest.fixture
def on_time_monday():
    with patch.object(cron_routes, "_utc_now", return_value=MONDAY_W37):
        yield


# ---------------------------------------------------------------------------
# _apply_week_off_note — the Monday-cron decision
# ---------------------------------------------------------------------------

def test_apply_week_off_note_sets_field_when_previous_week_has_no_episode(on_time_monday):
    """Genuine not-found (load_episode_verified returns None cleanly, no
    exception) -> the note IS set (#7630 finding 2)."""
    ep = {"episode_id": "2026-W37", "stages": {}}
    with patch.object(storage, "load_episode_verified", return_value=None) as load_episode:
        cron_routes._apply_week_off_note("2026-W37", ep)
    load_episode.assert_called_once_with("2026-W36")
    assert ep["week_off_note"] == {
        "message": cron_routes.WEEK_OFF_MESSAGE,
        "missed_week": "2026-W36",
    }


def test_apply_week_off_note_sets_field_when_previous_week_unpublished(on_time_monday):
    previous = {"episode_id": "2026-W36", "stages": {"wednesday": {"status": "failed"}}}
    ep = {"episode_id": "2026-W37", "stages": {}}
    with patch.object(storage, "load_episode_verified", return_value=previous):
        cron_routes._apply_week_off_note("2026-W37", ep)
    assert ep["week_off_note"]["missed_week"] == "2026-W36"


def test_apply_week_off_note_clears_field_when_previous_week_published(on_time_monday):
    previous = {"episode_id": "2026-W36", "published_at": "2026-09-06T00:10:00+00:00"}
    ep = {"episode_id": "2026-W37", "stages": {}, "week_off_note": {"stale": "leftover"}}
    with patch.object(storage, "load_episode_verified", return_value=previous):
        cron_routes._apply_week_off_note("2026-W37", ep)
    assert "week_off_note" not in ep


def test_apply_week_off_note_respects_the_active_prefix(on_time_monday):
    """A test-mode Monday must read the previous week's episode under
    whatever prefix its caller's _test_mode_scope already established — no
    hardcoded/prefixless path — so it can never see or touch the prod
    previous-week episode (RUNBOOK Incident 1 / #5911 lineage, #7630)."""
    seen_prefixes: list[str] = []

    def fake_load_episode(_episode_id):
        seen_prefixes.append(storage.prefix)
        return {"published_at": "2026-09-06T00:10:00+00:00"}

    with patch.object(storage, "load_episode_verified", side_effect=fake_load_episode):
        with storage.prefix_scope("test/"):
            cron_routes._apply_week_off_note("2026-W37", {"episode_id": "2026-W37", "stages": {}})
        with storage.prefix_scope(""):
            cron_routes._apply_week_off_note("2026-W37", {"episode_id": "2026-W37", "stages": {}})

    assert seen_prefixes == ["test/", ""]


def test_apply_week_off_note_on_an_older_week_refire_asks_about_the_week_before_it(before_sunday_w40):
    """Codex review of 7668ff4: a manual Monday re-fire of a PUBLISHED W38
    while W40 is current must check W37 (the week before W38), not the
    clock's previous week, so it never stamps a false note that Monday's
    writer would then put on the live homepage. (An unpublished week
    re-fired after its own Sunday keeps its own note instead; see
    test_a_published_week_refired_after_sunday_takes_the_ordinary_decision.)"""
    ep = {"episode_id": "2026-W38", "stages": {}, "published_at": "2026-09-20T23:00:00+00:00"}
    with patch.object(storage, "load_episode_verified", return_value={"published_at": "x"}) as load_episode:
        cron_routes._apply_week_off_note("2026-W38", ep)
    load_episode.assert_called_once_with("2026-W37")
    assert "week_off_note" not in ep


def test_apply_week_off_note_unchanged_on_a_read_error_with_a_warning(caplog, before_sunday_w40):
    """#7630 finding 2: a cloud read ERROR (not a genuine not-found) must
    take the logged-skip path, never be mistaken for "no previous episode".
    load_episode_verified raises on a read/network failure (unlike plain
    load_episode, which would swallow it and return None) — caught here,
    the field is left exactly as it was, and a warning is logged."""
    ep = {"episode_id": "2026-W40", "stages": {}, "week_off_note": {"kept": True}}
    with patch.object(storage, "load_episode_verified", side_effect=RuntimeError("blob down")):
        with caplog.at_level("WARNING"):
            cron_routes._apply_week_off_note("2026-W40", ep)
    assert ep["week_off_note"] == {"kept": True}
    assert any("blob down" in record.message for record in caplog.records)


@pytest.mark.parametrize("failure", ["bad-id", "read-error"])
def test_apply_week_off_note_never_fails_monday(failure, before_sunday_w40):
    ep = {"episode_id": "2026-W40", "stages": {}, "week_off_note": {"kept": True}}
    if failure == "bad-id":
        cron_routes._apply_week_off_note("2026-W99", ep)
    else:
        with patch.object(storage, "load_episode_verified", side_effect=RuntimeError("blob down")):
            cron_routes._apply_week_off_note("2026-W40", ep)
    assert ep["week_off_note"] == {"kept": True}



@pytest.mark.parametrize("previous", [
    {"episode_id": "2026-W39", "published_at": "2026-09-27T23:00:00+00:00"},
    None,
], ids=["previous-published", "previous-missing"])
def test_monday_refire_keeps_this_weeks_own_refusal_note(previous):
    """Codex (#7630): Sunday refused W40 and stamped 'missed W40'; a forced
    Monday re-fire that repairs W40 must not clear (or replace) that note
    because W39 published. W40 still has not published."""
    own = {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"}
    ep = {"episode_id": "2026-W40", "stages": {}, "week_off_note": dict(own)}
    with patch.object(storage, "load_episode_verified", return_value=previous) as load:
        cron_routes._apply_week_off_note("2026-W40", ep)
    assert ep["week_off_note"] == own
    load.assert_not_called()


def test_own_week_note_is_not_kept_once_the_episode_published():
    ep = {
        "episode_id": "2026-W40",
        "stages": {},
        "published_at": "2026-10-04T23:00:00+00:00",
        "week_off_note": {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"},
    }
    with patch.object(storage, "load_episode_verified", return_value={"published_at": "x"}):
        cron_routes._apply_week_off_note("2026-W40", ep)
    assert "week_off_note" not in ep

def test_week_before_crosses_the_iso_year_boundary():
    from backend.utils.episode_integrity import week_before
    assert week_before("2026-W01") == "2025-W52"
    assert week_before("2021-W01") == "2020-W53"
    assert week_before("2026-W40") == "2026-W39"


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

    with patch.object(cron_routes, "_utc_now", return_value=AFTER_SUNDAY_W40), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "load_episode_verified", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "_current_episode_id", return_value="2026-W40"), \
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


def test_refusing_an_older_week_never_rewrites_the_live_latest_json():
    """Codex review of 2d0567b/6b86ede: a manual force=true retry of an
    OLDER incomplete week must not replace the current week's live teaser
    in the global pages/latest.json. It still refuses with the same 400 and
    records the note on that older episode's own record.

    regenerate_and_upload is NOT mocked here — the invariant now lives
    inside it (#7630), so this is an end-to-end check that cron_sunday's
    refusal path really is protected by the writer's own current-week gate,
    not just trusted to be."""
    episode = {
        "episode_id": "2026-W40",
        "concept": "Some Concept",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "X"}},
            "tuesday": {"status": "complete"},
        },
        "events": [],
        "image_urls": [],
    }

    writes: dict[str, str] = {}

    def fake_save_page(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(cron_routes, "_utc_now", return_value=AFTER_SUNDAY_W40), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body("2026-W40"))), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "load_episode_verified", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes.storage, "save_page", side_effect=fake_save_page), \
         patch.object(cron_routes.episode_integrity, "current_episode_id", return_value="2026-W41"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(cron_routes.cron_sunday(_request()))

    assert exc_info.value.status_code == 400
    assert "wednesday" in exc_info.value.detail
    save_episode.assert_called_once_with("2026-W40", episode)
    assert "pages/2026-W40/index.html" in writes  # its own page still rendered
    assert "pages/latest.json" not in writes       # the live homepage teaser, untouched



def test_an_early_forced_sunday_refusal_does_not_announce_the_week_off():
    """Codex (#7630): a manual force=true Sunday POST before the week's
    Sunday cron time still has time to recover; it refuses with the same
    400 but must not stamp or render the note."""
    episode = {
        "episode_id": "2026-W40",
        "concept": "Some Concept",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "X"}},
            "tuesday": {"status": "complete"},
        },
        "events": [],
    }
    early = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)  # Thursday of W40

    with patch.object(cron_routes, "_utc_now", return_value=early), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "load_episode_verified", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "regenerate_and_upload") as regenerate:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(cron_routes.cron_sunday(_request()))

    assert exc_info.value.status_code == 400
    assert "wednesday" in exc_info.value.detail
    assert "week_off_note" not in episode
    regenerate.assert_not_called()


def test_monday_puts_its_week_off_decision_on_the_homepage_before_the_stage_runs():
    """Codex (#7630): a Monday whose stage then failed never wrote the note
    to pages/latest.json. The decision is written immediately."""
    order: list[str] = []
    ep = {"episode_id": "2026-W41", "stages": {}, "events": []}

    def _apply(eid, episode):
        episode["week_off_note"] = {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"}
        order.append("decide")

    def _upload(episode):
        order.append(f"upload:{bool(episode.get('week_off_note'))}")
        return True

    def _stage_fails(*_args, **_kwargs):
        order.append("stage")
        raise RuntimeError("monday stage blew up")

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body("2026-W41"))), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_load_or_create_episode", return_value=ep), \
         patch.object(cron_routes, "_apply_week_off_note", side_effect=_apply), \
         patch.object(cron_routes, "upload_latest_json", side_effect=_upload), \
         patch.object(cron_routes, "_run_stage", side_effect=_stage_fails), \
         patch.object(cron_routes.storage, "save_episode"):
        with pytest.raises(Exception):
            asyncio.run(cron_routes.cron_monday(_request()))

    assert order[:2] == ["decide", "upload:True"]


def test_monday_homepage_update_failure_never_stops_monday(caplog):
    ep = {"episode_id": "2026-W41", "stages": {}, "events": []}
    reached = []

    def _stage(*_args, **_kwargs):
        reached.append("stage")
        raise RuntimeError("stop here")

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body("2026-W41"))), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_load_or_create_episode", return_value=ep), \
         patch.object(cron_routes, "_apply_week_off_note"), \
         patch.object(cron_routes, "upload_latest_json", side_effect=OSError("blob down")), \
         patch.object(cron_routes, "_run_stage", side_effect=_stage), \
         patch.object(cron_routes.storage, "save_episode"):
        with caplog.at_level("WARNING"), pytest.raises(RuntimeError, match="stop here"):
            asyncio.run(cron_routes.cron_monday(_request()))

    assert reached == ["stage"]
    assert any("homepage week-off update skipped" in r.message for r in caplog.records)

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
            "wednesday": approved_wednesday(episode_id="2026-W40"),
        },
        "events": [],
        "image_urls": [],
    }

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "load_episode_verified", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes.storage, "save_page"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Devon Park", "message": "Ready."}], "PASS - ready",
         )), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "_indexnow_submit_urls",
                      return_value=IndexNowResult(ok=True, status_code=200, detail="submitted")), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    assert result["published"] is True
    assert "week_off_note" not in episode


def test_cron_sunday_late_publish_leaves_the_successors_note_alone():
    """Full wiring check through the real success path: Monday already
    stamped W41 with 'missed W40' because W40 hadn't published at that
    point. A (recovered/late) W40 publish must NOT touch W41: no read-modify
    of its episode, no teaser or page write for it (option c, Erik
    2026-10-05). W41's next stage write drops the note via
    episode_renderer._week_off_note_still_true."""
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
            "wednesday": approved_wednesday(episode_id="2026-W40"),
        },
        "events": [],
        "image_urls": [],
    }
    next_episode = {
        "episode_id": "2026-W41",
        "stages": {},
        "week_off_note": {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"},
    }
    episodes_by_id = {"2026-W40": episode, "2026-W41": next_episode}
    save_calls: list[tuple[str, dict]] = []

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", side_effect=lambda eid: episodes_by_id.get(eid)), \
         patch.object(cron_routes.storage, "load_episode_verified", side_effect=lambda eid: episodes_by_id.get(eid)), \
         patch.object(cron_routes.storage, "save_episode", side_effect=lambda eid, data: save_calls.append((eid, data))), \
         patch.object(cron_routes.storage, "save_page"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Devon Park", "message": "Ready."}], "PASS - ready",
         )), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "_indexnow_submit_urls",
                      return_value=IndexNowResult(ok=True, status_code=200, detail="submitted")), \
         patch.object(cron_routes, "regenerate_and_upload") as regenerate, \
         patch.object(cron_routes, "upload_latest_json") as upload_latest, \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    # Option c (Erik, 2026-10-05): a late publish never touches the successor.
    # Its note is dropped by the successor's own next stage write instead
    # (episode_renderer._week_off_note_still_true), so a recipe-less
    # successor's valid note can never be erased by this publish.
    assert result["published"] is True
    assert next_episode["week_off_note"]["missed_week"] == "2026-W40"
    assert [eid for eid, _ in save_calls if eid == "2026-W41"] == []
    assert all(c.args[0].get("episode_id") != "2026-W41" for c in upload_latest.call_args_list)
    assert all(c.args[0].get("episode_id") != "2026-W41" for c in regenerate.call_args_list)


def test_recipe_less_week_gets_its_note_only_once_sundays_window_arrives():
    """Codex (#7630): a week whose Monday never produced a recipe cannot
    publish. Sunday's own run announces it (homepage teaser only, never the
    episode page); an early forced re-fire does not."""
    from datetime import timedelta

    from backend.utils import episode_integrity

    def _episode():
        return {"episode_id": "2026-W40", "concept": "x", "stages": {"monday": {"status": "failed"}}, "events": []}

    due = episode_integrity.stage_deadline("2026-W40", "sunday")
    for now, expect_note in ((due - timedelta(hours=2), False), (due, True)):
        ep = _episode()
        with patch.object(cron_routes, "_utc_now", return_value=now), \
             patch.object(cron_routes.storage, "save_episode") as save_episode, \
             patch.object(cron_routes, "upload_latest_json") as upload_latest, \
             patch.object(cron_routes, "regenerate_and_upload") as regenerate:
            cron_routes._note_week_off_without_a_recipe("2026-W40", ep)
        regenerate.assert_not_called()
        save_episode.assert_not_called()
        assert "week_off_note" not in ep  # the caller's episode is untouched
        if expect_note:
            upload_latest.assert_called_once_with(
                {**_episode(), "week_off_note": {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"}}
            )
        else:
            upload_latest.assert_not_called()


def test_non_iso_episode_id_keeps_the_409_and_alert_on_a_recipe_less_sunday():
    """Review (#7630): run_full_week.py posts test-<ts> ids. stage_deadline
    raises for them; that must not turn the 409 + notify into a 500."""
    from fastapi import HTTPException

    ep = {"episode_id": "test-20261002-1200", "concept": "x", "stages": {}, "events": []}
    with patch.object(cron_routes, "upload_latest_json") as upload_latest, \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        cron_routes._note_week_off_without_a_recipe("test-20261002-1200", ep)
        with pytest.raises(HTTPException) as exc_info:
            cron_routes._require_monday_recipe(ep, "sunday")
    upload_latest.assert_not_called()
    assert exc_info.value.status_code == 409
    notify.assert_called_once()
    assert cron_routes._sunday_window_reached("test-20261002-1200") is False


@pytest.mark.parametrize("failing", ["save_episode", "regenerate_and_upload"])
def test_a_failed_note_write_keeps_sundays_400_refusal(failing):
    """Codex (#7630): the note is cosmetic; a failed save or render must not
    turn the refusal into a 500."""
    episode = {
        "episode_id": "2026-W40",
        "concept": "Some Concept",
        "stages": {"monday": {"status": "complete", "recipe_data": {"title": "X"}}},
        "events": [],
    }
    boom = RuntimeError("blob write failed")
    with patch.object(cron_routes, "_utc_now", return_value=AFTER_SUNDAY_W40), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "load_episode_verified", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode",
                      side_effect=boom if failing == "save_episode" else None), \
         patch.object(cron_routes, "_current_episode_id", return_value="2026-W40"), \
         patch.object(cron_routes, "regenerate_and_upload",
                      side_effect=boom if failing == "regenerate_and_upload" else None):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(cron_routes.cron_sunday(_request()))

    assert exc_info.value.status_code == 400
    assert "wednesday" in exc_info.value.detail



def test_a_monday_refired_after_its_own_sunday_keeps_the_weeks_note():
    """Codex (#7630): a recipe-less week's note lives only on the homepage.
    A Monday re-fired after that week's own Sunday window, with the week
    still unpublished, rebuilds the own-week note instead of clearing it."""
    ep = {"episode_id": "2026-W40", "stages": {}}
    with patch.object(cron_routes, "_utc_now", return_value=AFTER_SUNDAY_W40), \
         patch.object(storage, "load_episode_verified") as load_strict:
        cron_routes._apply_week_off_note("2026-W40", ep)
    assert ep["week_off_note"] == {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"}
    load_strict.assert_not_called()


def test_a_monday_refired_after_sunday_during_a_photo_hold_builds_no_own_week_note():
    """Codex on e6b1fc3 / Erik decision 1: a week WITH a recipe that missed
    Sunday only because it is held for photo approval is not off. A Monday
    re-fired after its Sunday window must not invent an own-week note from
    the clock; it takes the ordinary previous-week decision instead."""
    previous = {"episode_id": "2026-W39", "published_at": "2026-09-28T00:10:00+00:00"}
    ep = {
        "episode_id": "2026-W40",
        "stages": {"monday": {"status": "complete", "recipe_data": {"title": "Held Cups"}}},
    }
    with patch.object(cron_routes, "_utc_now", return_value=AFTER_SUNDAY_W40), \
         patch.object(storage, "load_episode_verified", return_value=previous) as load:
        cron_routes._apply_week_off_note("2026-W40", ep)
    assert "week_off_note" not in ep
    load.assert_called_once_with("2026-W39")


def test_a_published_week_refired_after_sunday_takes_the_ordinary_decision():
    previous = {"episode_id": "2026-W39", "published_at": "2026-09-28T00:10:00+00:00"}
    ep = {"episode_id": "2026-W40", "stages": {}, "published_at": "2026-10-05T00:10:00+00:00"}
    with patch.object(cron_routes, "_utc_now", return_value=AFTER_SUNDAY_W40 + timedelta(days=1)), \
         patch.object(storage, "load_episode_verified", return_value=previous):
        cron_routes._apply_week_off_note("2026-W40", ep)
    assert "week_off_note" not in ep


# ---------------------------------------------------------------------------
# Merge with #7936 (2026-10-05 preflight): legacy-aware publication check and
# the checkpoint-completion recovery publish.
# ---------------------------------------------------------------------------

LEGACY_PUBLISHED = {"episode_id": "2026-W20", "stages": {"sunday": {"status": "complete"}}}


def test_a_legacy_published_week_owes_no_note():
    """Weeks published before published_at existed have only a complete
    Sunday; episode_is_published counts them, so no false note."""
    assert episode_integrity.week_off_note_due(LEGACY_PUBLISHED) is False
    assert episode_integrity.week_off_note_due({"stages": {"sunday": {"status": "simulated"}}}) is True
    assert episode_integrity.week_off_note_due(None) is True


def test_monday_after_a_legacy_published_week_sets_no_note(on_time_monday):
    ep = {"episode_id": "2026-W37", "stages": {}, "week_off_note": {"message": "x", "missed_week": "2026-W36"}}
    with patch.object(storage, "load_episode_verified", return_value=LEGACY_PUBLISHED):
        cron_routes._apply_week_off_note("2026-W37", ep)
    assert "week_off_note" not in ep


def test_recheck_drops_an_own_week_note_once_the_week_is_legacy_published():
    from backend.publishing import episode_renderer

    own = {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W20"}
    episode = dict(LEGACY_PUBLISHED, week_off_note=own)
    assert episode_renderer._week_off_note_still_true(episode) is None


def test_a_sunday_held_for_photo_approval_shows_no_week_off_note():
    """Erik, 2026-10-05 (option c): a week waiting on his photo approval is
    not "off". Sunday holds it with no note on the episode or the homepage;
    if it never publishes, next Monday's _apply_week_off_note notes it."""
    from tests.photo_review_helpers import register, wednesday_stage
    from tests.test_photo_approval_7936 import EP_ID, _episode, _status, _Store, _sunday

    wed = wednesday_stage()
    store = _Store()
    store.put(EP_ID, _episode(wed), "")
    register(EP_ID, wed, store)
    with patch.object(cron_routes, "_utc_now", return_value=datetime(2026, 10, 12, 0, 30, tzinfo=timezone.utc)):
        env = _sunday(store)
    assert _status(env) == 202
    assert "week_off_note" not in store.get(EP_ID)
    assert not any("week_off_note" in saved for saved in store.saves)
