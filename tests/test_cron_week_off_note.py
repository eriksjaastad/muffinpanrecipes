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
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.admin import cron_routes
from backend.storage import storage
from backend.utils.indexnow import IndexNowResult

# Monday of W37 (2026-09-07); W36 (2026-08-31 through 2026-09-06) is the
# week immediately before it — matches the fixtures in test_episode_integrity.py.
MONDAY_W37 = datetime(2026, 9, 7, 14, 30, tzinfo=timezone.utc)
# W40's Sunday cron time has arrived (Sunday 2026-10-04, evening UTC).
AFTER_SUNDAY_W40 = datetime(2026, 10, 4, 23, 59, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# _apply_week_off_note — the Monday-cron decision
# ---------------------------------------------------------------------------

def test_apply_week_off_note_sets_field_when_previous_week_has_no_episode():
    """Genuine not-found (load_episode_strict returns None cleanly, no
    exception) -> the note IS set (#7630 finding 2)."""
    ep = {"episode_id": "2026-W37", "stages": {}}
    with patch.object(storage, "load_episode_strict", return_value=None) as load_episode:
        cron_routes._apply_week_off_note("2026-W37", ep)
    load_episode.assert_called_once_with("2026-W36")
    assert ep["week_off_note"] == {
        "message": cron_routes.WEEK_OFF_MESSAGE,
        "missed_week": "2026-W36",
    }


def test_apply_week_off_note_sets_field_when_previous_week_unpublished():
    previous = {"episode_id": "2026-W36", "stages": {"wednesday": {"status": "failed"}}}
    ep = {"episode_id": "2026-W37", "stages": {}}
    with patch.object(storage, "load_episode_strict", return_value=previous):
        cron_routes._apply_week_off_note("2026-W37", ep)
    assert ep["week_off_note"]["missed_week"] == "2026-W36"


def test_apply_week_off_note_clears_field_when_previous_week_published():
    previous = {"episode_id": "2026-W36", "published_at": "2026-09-06T00:10:00+00:00"}
    ep = {"episode_id": "2026-W37", "stages": {}, "week_off_note": {"stale": "leftover"}}
    with patch.object(storage, "load_episode_strict", return_value=previous):
        cron_routes._apply_week_off_note("2026-W37", ep)
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

    with patch.object(storage, "load_episode_strict", side_effect=fake_load_episode):
        with storage.prefix_scope("test/"):
            cron_routes._apply_week_off_note("2026-W37", {"episode_id": "2026-W37", "stages": {}})
        with storage.prefix_scope(""):
            cron_routes._apply_week_off_note("2026-W37", {"episode_id": "2026-W37", "stages": {}})

    assert seen_prefixes == ["test/", ""]


def test_apply_week_off_note_on_an_older_week_refire_asks_about_the_week_before_it():
    """Codex review of 7668ff4: a manual Monday re-fire of W40 while W41 is
    current must check W39 (the week before W40), not W40 itself, so it
    never stamps a false 'missed W40' note that Monday's writer would then
    put on the live homepage."""
    ep = {"episode_id": "2026-W40", "stages": {}}
    with patch.object(storage, "load_episode_strict", return_value={"published_at": "x"}) as load_episode:
        cron_routes._apply_week_off_note("2026-W40", ep)
    load_episode.assert_called_once_with("2026-W39")
    assert "week_off_note" not in ep


def test_apply_week_off_note_unchanged_on_a_read_error_with_a_warning(caplog):
    """#7630 finding 2: a cloud read ERROR (not a genuine not-found) must
    take the logged-skip path, never be mistaken for "no previous episode".
    load_episode_strict raises on a read/network failure (unlike plain
    load_episode, which would swallow it and return None) — caught here,
    the field is left exactly as it was, and a warning is logged."""
    ep = {"episode_id": "2026-W40", "stages": {}, "week_off_note": {"kept": True}}
    with patch.object(storage, "load_episode_strict", side_effect=RuntimeError("blob down")):
        with caplog.at_level("WARNING"):
            cron_routes._apply_week_off_note("2026-W40", ep)
    assert ep["week_off_note"] == {"kept": True}
    assert any("blob down" in record.message for record in caplog.records)


@pytest.mark.parametrize("failure", ["bad-id", "read-error"])
def test_apply_week_off_note_never_fails_monday(failure):
    ep = {"episode_id": "2026-W40", "stages": {}, "week_off_note": {"kept": True}}
    if failure == "bad-id":
        cron_routes._apply_week_off_note("2026-W99", ep)
    else:
        with patch.object(storage, "load_episode_strict", side_effect=RuntimeError("blob down")):
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
    with patch.object(storage, "load_episode_strict", return_value=previous) as load:
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
    with patch.object(storage, "load_episode_strict", return_value={"published_at": "x"}):
        cron_routes._apply_week_off_note("2026-W40", ep)
    assert "week_off_note" not in ep

def test_week_before_crosses_the_iso_year_boundary():
    from backend.utils.episode_integrity import week_before
    assert week_before("2026-W01") == "2025-W52"
    assert week_before("2021-W01") == "2020-W53"
    assert week_before("2026-W40") == "2026-W39"


def test_week_after_crosses_the_iso_year_boundary():
    from backend.utils.episode_integrity import week_after
    assert week_after("2025-W52") == "2026-W01"
    assert week_after("2020-W53") == "2021-W01"
    assert week_after("2026-W39") == "2026-W40"


def test_week_after_is_the_exact_inverse_of_week_before():
    from backend.utils.episode_integrity import week_after, week_before
    for episode_id in ["2026-W01", "2020-W53", "2026-W40", "2025-W52"]:
        assert week_before(week_after(episode_id)) == episode_id
        assert week_after(week_before(episode_id)) == episode_id


# ---------------------------------------------------------------------------
# _clear_stale_week_off_note_after_late_publish — a late/recovered Sunday
# publish must clear a false note it left on the SUCCESSOR week (#7630)
# ---------------------------------------------------------------------------

def test_clear_stale_note_removes_it_when_the_successor_blames_this_week():
    """Monday stamped W41 with 'missed W40' because W40 hadn't published
    yet at that point. A late W40 publish must clear it and re-render W41's
    page (the writer's own current-week gate decides whether that reaches
    pages/latest.json)."""
    next_episode = {
        "episode_id": "2026-W41",
        "stages": {},
        "week_off_note": {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"},
    }

    with patch.object(cron_routes.storage, "load_episode_strict", return_value=next_episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "regenerate_and_upload") as regenerate, \
         patch.object(cron_routes, "upload_latest_json") as upload_latest:
        cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")

    cleared = {k: v for k, v in next_episode.items() if k != "week_off_note"}
    upload_latest.assert_called_once_with(cleared)
    # Teaser only: re-rendering the successor's episode page from this
    # snapshot could roll back a newer stage (Codex, #7630).
    regenerate.assert_not_called()
    # Never saved back: the body came through the CDN and may be up to ~60s
    # stale, so saving could overwrite a newer stage write (review, #7630).
    save_episode.assert_not_called()
    # The reader's object itself is left untouched (round 7: it may be cached).
    assert next_episode["week_off_note"]["missed_week"] == "2026-W40"


@pytest.mark.parametrize(
    "next_episode",
    [
        None,  # successor doesn't exist yet — the ordinary on-time-publish case
        {"episode_id": "2026-W41", "stages": {}},  # exists, no note at all
        {  # exists, but the note blames a DIFFERENT week
            "episode_id": "2026-W41",
            "stages": {},
            "week_off_note": {"message": "x", "missed_week": "2026-W39"},
        },
    ],
    ids=["no-successor-episode", "no-note", "note-for-a-different-week"],
)
def test_clear_stale_note_changes_nothing_when_not_applicable(next_episode):
    original = copy.deepcopy(next_episode) if next_episode is not None else None

    with patch.object(cron_routes.storage, "load_episode_strict", return_value=next_episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "regenerate_and_upload") as regenerate:
        cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")

    assert next_episode == original
    save_episode.assert_not_called()
    regenerate.assert_not_called()



def test_clear_stale_note_render_failure_keeps_the_note_stored_so_a_retry_redoes_it():
    """Codex round 6: the stored note is the signal the already_published
    retry needs. The clear never saves the successor, so a failed render
    leaves the note stored and the retry renders again."""
    stored = {
        "episode_id": "2026-W41",
        "stages": {},
        "week_off_note": {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"},
    }

    def load(_eid):
        return copy.deepcopy(stored)

    def save(eid, data):
        stored.clear()
        stored.update(copy.deepcopy(data))

    with patch.object(cron_routes.storage, "load_episode_strict", side_effect=load), \
         patch.object(cron_routes.storage, "save_episode", side_effect=save) as save_episode, \
         patch.object(cron_routes, "upload_latest_json", side_effect=RuntimeError("blob write failed")):
        cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")  # must not raise

    save_episode.assert_not_called()
    assert stored["week_off_note"]["missed_week"] == "2026-W40"

    # The retry (already_published catch-up) now succeeds and clears it.
    with patch.object(cron_routes.storage, "load_episode_strict", side_effect=load), \
         patch.object(cron_routes.storage, "save_episode", side_effect=save) as save_retry, \
         patch.object(cron_routes, "upload_latest_json") as upload_latest:
        cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")

    upload_latest.assert_called_once_with({"episode_id": "2026-W41", "stages": {}})
    save_retry.assert_not_called()




_real_current_episode_id = cron_routes.episode_integrity.current_episode_id


def _now_is(week: str):
    """Stub "what week is it now" without breaking week_after/week_before,
    which call current_episode_id with an explicit date."""
    def _current(when=None):
        return _real_current_episode_id(when) if when is not None else week
    return _current

def test_late_publish_after_rollover_with_no_successor_marks_the_homepage_published():
    """Codex (#7630): W40's refusal put a note on the homepage; W40 then
    published after Monday 00:00 UTC but before Monday's cron created W41.
    The writer's gate skipped W40's own latest.json write and there was no
    W41 episode to clear, so the note stayed up. With W41 current and no W41
    episode, the published marker is written."""
    with patch.object(cron_routes.storage, "load_episode_strict", return_value=None), \
         patch.object(cron_routes.episode_integrity, "current_episode_id", side_effect=_now_is("2026-W41")), \
         patch.object(cron_routes, "mark_latest_published") as mark:
        cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")
    mark.assert_called_once_with()


def test_on_time_publish_with_no_successor_writes_nothing_extra():
    with patch.object(cron_routes.storage, "load_episode_strict", return_value=None), \
         patch.object(cron_routes.episode_integrity, "current_episode_id", side_effect=_now_is("2026-W40")), \
         patch.object(cron_routes, "mark_latest_published") as mark:
        cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")
    mark.assert_not_called()

def test_clear_stale_note_error_is_logged_and_swallowed(caplog):
    """Best-effort: nothing in this path may turn a successful publish into
    a failed request."""
    with patch.object(cron_routes.storage, "load_episode_strict", side_effect=RuntimeError("blob down")):
        with caplog.at_level("WARNING"):
            cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")  # must not raise

    assert any("blob down" in record.message for record in caplog.records)


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
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes.storage, "save_page", side_effect=fake_save_page), \
         patch.object(cron_routes.episode_integrity, "current_episode_id", return_value="2026-W41"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(cron_routes.cron_sunday(_request()))

    assert exc_info.value.status_code == 400
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
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "regenerate_and_upload") as regenerate:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(cron_routes.cron_sunday(_request()))

    assert exc_info.value.status_code == 400
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
         patch.object(cron_routes, "_indexnow_submit_urls",
                      return_value=IndexNowResult(ok=True, status_code=200, detail="submitted")), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    assert result["published"] is True
    assert "week_off_note" not in episode


def test_cron_sunday_late_publish_clears_the_successors_note_end_to_end():
    """Full wiring check through the real success path: Monday already
    stamped W41 with 'missed W40' because W40 hadn't published at that
    point. A (recovered/late) W40 publish must clear it and re-render W41 —
    the end-to-end companion to the unit tests above, which exercise
    _clear_stale_week_off_note_after_late_publish directly."""
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
         patch.object(cron_routes.storage, "load_episode_strict", side_effect=lambda eid: episodes_by_id.get(eid)), \
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

    assert result["published"] is True
    cleared = {k: v for k, v in next_episode.items() if k != "week_off_note"}
    assert [eid for eid, _ in save_calls if eid == "2026-W41"] == []
    upload_latest.assert_any_call(cleared)
    assert all(c.args[0].get("episode_id") != "2026-W41" for c in regenerate.call_args_list)


def test_clear_stale_note_never_mutates_the_readers_cached_object():
    """Codex round 7: load_episode_strict can return its cache's own object.
    Popping the note from it hid the note from a retry in the same warm
    Lambda after a failed render. The reader here returns ONE shared object,
    like the cache does."""
    cached = {
        "episode_id": "2026-W41",
        "stages": {},
        "week_off_note": {"message": cron_routes.WEEK_OFF_MESSAGE, "missed_week": "2026-W40"},
    }

    with patch.object(cron_routes.storage, "load_episode_strict", return_value=cached), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "regenerate_and_upload", side_effect=RuntimeError("blob write failed")):
        cron_routes._clear_stale_week_off_note_after_late_publish("2026-W40")

    save_episode.assert_not_called()
    assert cached["week_off_note"]["missed_week"] == "2026-W40"


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
