"""Preflight-5 neighbours of the #7936 cycle-3 corrections (F1-F6). Offline only.

F1 shared published predicate in the cleanup sweep and operator script;
F2 a week with no reviewable photo set is a truthful failure, not a hold;
F3 claim CAS exhaustion is a conflict (alerted, attempt recorded), not a rival publisher;
F4 the "hold cleared" event exists only when the clear persisted;
F5 display surfaces label legacy complete-Sunday weeks as published, no invented time;
F6 the verified-read failure alert says the monitor shows the last persisted snapshot.
"""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from backend.storage import EpisodeReadStale
from backend.utils import photo_review
from backend.utils.episode_integrity import episode_summary, recorded_photo_hold
from tests.photo_review_helpers import decide, register, wednesday_stage
from tests.test_photo_approval_7936 import (
    EP_ID,
    _Store,
    _approved_store,
    _client,
    _control,
    _episode,
    _never_published,
    _status,
    _sunday,
)
from tests.test_photo_approval_cycle2_7936 import _monitor, _page
from tests.test_photo_approval_cycle3_7936 import _held_store, _legacy_published_episode


# --- F1 ---------------------------------------------------------------------------------

def test_cleanup_sweep_protects_a_legacy_published_week_without_a_confirmed_winner(tmp_path, monkeypatch):
    from scripts import cleanup_image_backlog as sweep

    ep = _legacy_published_episode()
    ep["stages"]["wednesday"].pop("confirmed_winner")
    ep.pop("hero_image_url", None)
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    (episodes / f"{EP_ID}.json").write_text(json.dumps(ep))
    monkeypatch.setattr(sweep, "EPISODES_DIR", episodes)
    monkeypatch.setattr(sweep, "PHOTO_CONTROL_DIR", tmp_path / "none")
    protected = sweep.protected_image_paths()
    for p in ep["stages"]["wednesday"]["image_paths"]:
        assert p.removeprefix("src/assets/images/") in protected
    # And an unpublished week with the same shape protects nothing beyond the control.
    ep["stages"]["sunday"] = {"status": "failed"}
    (episodes / f"{EP_ID}.json").write_text(json.dumps(ep))
    assert sweep.protected_image_paths() == set()


def test_operator_script_reports_legacy_publication_and_refuses_to_release(capsys):
    from scripts import photo_control

    store, ep, wed = _approved_store()
    photo_review.claim_for_publication(store, EP_ID, ep)
    legacy = store.get(EP_ID)
    legacy["stages"]["sunday"] = {"status": "complete"}
    store.put(EP_ID, legacy)
    with patch("backend.storage.storage", store):
        assert photo_control.main(["show", EP_ID]) == 0
        out = capsys.readouterr().out
        assert '"episode_published": true' in out and '"episode_published_at": null' in out
        assert photo_control.main(["reconcile", EP_ID, "--execute"]) == 2
        assert "legacy week" in capsys.readouterr().out
    assert _control(store)["state"] == "claimed"


# --- F2 ---------------------------------------------------------------------------------

@pytest.mark.parametrize("uploads", ["empty_urls", "none_urls", "no_rounds", "missing_urls_key"])
def test_no_reviewable_photo_set_is_a_failure_not_a_hold(uploads):
    wed = wednesday_stage()
    wed.pop("photo_review")
    if uploads == "empty_urls":
        wed["image_urls"] = ["", "", ""]
    elif uploads == "none_urls":
        wed["image_urls"] = [None, None, None]
    elif uploads == "no_rounds":
        wed["photography_data"] = {"rounds": []}
    else:
        wed.pop("image_urls")
    store = _Store()
    store.put(EP_ID, _episode(wed))
    env = _sunday(store)
    assert _status(env) == 400
    assert "No reviewable photos" in env.error.detail and "nothing to approve" in env.error.detail
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    assert env.held.call_count == 0, "no 'Publish held' email for a week nobody can approve"
    assert env.failure.call_count == 1
    assert "was recorded on the episode" in env.failure.call_args.kwargs["error_message"]
    _never_published(store)
    saved = store.get(EP_ID)
    assert saved["stages"]["sunday"]["status"] == "failed" and "No reviewable photos" in saved["stages"]["sunday"]["error"]
    assert "publish_hold" not in saved
    assert store.read_photo_control(EP_ID) is None
    failures, summary = _monitor(saved)
    assert any(f.startswith("sunday stage is 'failed'") and "No reviewable photos" in f for f in failures)
    assert "awaiting" not in summary


def test_no_photos_after_an_earlier_hold_replaces_the_hold_with_the_failure():
    store, wed = _held_store()
    stale = store.get(EP_ID)
    stale["stages"]["wednesday"] = {**wednesday_stage(), "image_urls": ["", "", ""]}
    stale["stages"]["wednesday"].pop("photo_review")
    store.put(EP_ID, stale)
    # The control still has the registered set, so the derived view is not used... unless the
    # control is gone. Simulate the authority having no request: remove the control log.
    import shutil

    import backend.storage as storage_module
    shutil.move(str(storage_module.PHOTO_CONTROL_DIR), str(storage_module.PHOTO_CONTROL_DIR.with_name("moved")))
    env = _sunday(store)
    assert _status(env) == 400
    saved = store.get(EP_ID)
    assert "publish_hold" not in saved and saved["stages"]["sunday"]["status"] == "failed"
    assert recorded_photo_hold(saved) is None
    assert env.held.call_count == 0


def test_no_photos_failure_that_cannot_be_saved_says_so():
    wed = wednesday_stage()
    wed.pop("photo_review")
    wed["image_urls"] = ["", "", ""]
    store = _Store()
    store.put(EP_ID, _episode(wed))
    before = copy.deepcopy(store.get(EP_ID))
    store.fail_save = lambda data: True
    env = _sunday(store)
    assert _status(env) == 400
    message = env.failure.call_args.kwargs["error_message"]
    assert "could NOT be recorded" in message and "last persisted snapshot" in message
    assert "was recorded on the episode" not in message
    assert store.get(EP_ID) == before


def test_genuine_awaiting_and_rejected_holds_are_unchanged():
    wed = wednesday_stage()
    store = _Store()
    store.put(EP_ID, _episode(wed))
    register(EP_ID, wed, store)
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval" and env.held.call_count == 1
    assert env.failure.call_count == 0
    decide(EP_ID, store.get(EP_ID), action="reject", store=store)
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "photos_rejected" and env.failure.call_count == 0
    assert "sunday" not in store.get(EP_ID)["stages"]


# --- F3 ---------------------------------------------------------------------------------

def test_claim_conflict_is_reported_as_a_conflict_and_recorded_on_the_hold():
    store, wed = _held_store()
    decide(EP_ID, store.get(EP_ID), store=store)
    with patch.object(photo_review, "claim_for_publication",
                      side_effect=photo_review.ReviewConflict("photo control kept changing while Sunday tried to claim it")):
        env = _sunday(store)
    assert _status(env) == 409
    assert "kept changing" in env.error.detail and "another Sunday" not in env.error.detail
    env.dialogue.assert_not_called()
    assert env.failure.call_count == 1
    assert "was recorded on the episode" in env.failure.call_args.kwargs["error_message"]
    saved = store.get(EP_ID)
    assert saved["publish_hold"]["last_attempt"]["outcome"] == "failed"
    assert recorded_photo_hold(saved) is None
    failures, summary = _monitor(saved)
    assert any("attempt after the photo hold failed" in f for f in failures) and "awaiting" not in summary
    assert _control(store)["state"] == "approved"


def test_real_publication_underway_is_still_a_quiet_409():
    store, ep, wed = _approved_store()
    photo_review.claim_for_publication(store, EP_ID, ep)
    env = _sunday(store)
    body = json.loads(env.result.body)
    assert env.result.status_code == 409 and body["status"] == "publication_underway"
    assert "another Sunday run holds" in body["message"]
    assert env.failure.call_count == 0 and env.dialogue.call_count == 0


# --- F4 ---------------------------------------------------------------------------------

def test_failed_hold_clear_never_records_a_successful_clear_event():
    store, wed = _held_store()
    decide(EP_ID, store.get(EP_ID), store=store)
    attempted = []

    def fail_clear(data):
        if "sunday: hold cleared (approved photo claimed)" in data.get("events", []):
            attempted.append(copy.deepcopy(data))
            return True
        return False

    store.fail_save = fail_clear
    env = _sunday(store)
    assert _status(env) == 503 and attempted, "the clear was attempted and refused"
    saved = store.get(EP_ID)
    assert "sunday: hold cleared (approved photo claimed)" not in saved["events"]
    assert "publish_hold" in saved
    # The attempt record itself persisted here (the predicate only refused the clear),
    # and it carries the honest events.
    assert "sunday: hold clear NOT persisted (save failed)" in saved["events"]
    assert "sunday: attempt after hold failed" in saved["events"]
    assert saved["publish_hold"]["last_attempt"]["outcome"] == "failed"
    store.fail_save = None
    env = _sunday(store)
    assert env.result["published"] is True
    assert "sunday: hold cleared (approved photo claimed)" in store.get(EP_ID)["events"]


def test_failed_hold_clear_keeps_an_earlier_genuine_clear_event_in_history():
    """Duplicate history: a prior hold WAS cleared (same event text). A later failed clear must
    withdraw only its own appended event, never the historical one, and never leave a false success."""
    store, wed = _held_store()
    saved = store.get(EP_ID)
    # History from an earlier week-cycle on this episode: a hold that really was cleared, then new
    # photos/hold again (the same hold record is still present for this run).
    historic = ["monday: complete", "sunday: hold cleared (approved photo claimed)", "wednesday: photos ready for review"]
    saved["events"] = historic + saved["events"]
    store.put(EP_ID, saved)
    decide(EP_ID, store.get(EP_ID), store=store)
    before_events = list(store.get(EP_ID)["events"])
    attempted = []

    def fail_clear(data):
        events = data.get("events", [])
        # Fail only the clear save of THIS run: it carries two equal clear events (history + new).
        if events.count("sunday: hold cleared (approved photo claimed)") == 2:
            attempted.append(copy.deepcopy(data))
            return True
        return False

    store.fail_save = fail_clear
    env = _sunday(store)
    assert _status(env) == 503 and attempted
    saved = store.get(EP_ID)
    events = saved["events"]
    assert events.count("sunday: hold cleared (approved photo claimed)") == 1, "only the historical success remains"
    assert events.index("sunday: hold cleared (approved photo claimed)") == 1, "the historical event kept its slot"
    assert events[: len(before_events)] == before_events, "no earlier history was removed or reordered"
    assert events[-2:] == ["sunday: hold clear NOT persisted (save failed)", "sunday: attempt after hold failed"]
    assert "publish_hold" in saved and saved["publish_hold"]["last_attempt"]["outcome"] == "failed"
    store.fail_save = None
    assert _sunday(store).result["published"] is True
    assert store.get(EP_ID)["events"].count("sunday: hold cleared (approved photo claimed)") == 2


# --- F5 ---------------------------------------------------------------------------------

def test_display_surfaces_label_a_legacy_week_published_without_inventing_a_time():
    store = _Store()
    store.put(EP_ID, _legacy_published_episode())
    client, patcher = _client(store)
    try:
        page = _page(client).text
        listing = client.get("/admin/episodes")
    finally:
        patcher.stop()
    assert "Published (legacy week, no timestamp recorded)" in page
    assert "Not published" not in page.split("<!-- Stage cards -->", 1)[0]
    assert listing.status_code == 200 and EP_ID in listing.text
    summary = episode_summary(store.get(EP_ID))
    assert summary.endswith("published") and "awaiting" not in summary
    # A real published_at still shows the timestamp; an unpublished week says so.
    store.put(EP_ID, _episode(wednesday_stage(), published_at="2026-10-11T00:05:00+00:00"))
    client, patcher = _client(store)
    try:
        assert "2026-10-11T00:05:00+00:00" in _page(client).text
    finally:
        patcher.stop()
    store.put(EP_ID, _episode(wednesday_stage()))
    client, patcher = _client(store)
    try:
        assert "Not published" in _page(client).text
    finally:
        patcher.stop()


def test_episode_list_entry_carries_the_shared_published_flag():
    """The list summary dict exposes the legacy-aware flag beside the raw timestamp (no invented time)."""
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates

    app = FastAPI()
    app.state.templates = Jinja2Templates(directory="backend/admin/templates")
    captured = {}
    original = Jinja2Templates.TemplateResponse

    def capture(self, *args, **kwargs):
        ctx = kwargs.get("context") or next((a for a in args if isinstance(a, dict)), {})
        captured.update(ctx)
        return original(self, *args, **kwargs)

    store = _Store()
    store.put(EP_ID, _legacy_published_episode())
    other = _episode(wednesday_stage())
    other["episode_id"] = "2026-W40"
    store.put("2026-W40", other, "")
    client, patcher = _client(store)
    try:
        with patch.object(Jinja2Templates, "TemplateResponse", capture):
            assert client.get("/admin/episodes").status_code == 200
    finally:
        patcher.stop()
    by_id = {e["episode_id"]: e for e in captured["episodes"]}
    assert by_id[EP_ID]["published"] is True and by_id[EP_ID]["published_at"] is None
    assert by_id["2026-W40"]["published"] is False


# --- F6 ---------------------------------------------------------------------------------

@pytest.mark.parametrize("error", [EpisodeReadStale("served ETag differs"), RuntimeError("blob down")])
def test_verified_read_failure_alert_is_honest_about_the_monitor(error):
    store, wed = _held_store()
    store.fail_verified = error
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    message = env.failure.call_args.kwargs["error_message"]
    assert "last persisted snapshot" in message and "Treat this alert as the record" in message
    assert "was recorded on the episode" not in message
    # Nothing was saved: the earlier hold is exactly as it was (the limit the alert states).
    assert store.saves == [] or all("publish_hold" in s for s in store.saves)
    assert recorded_photo_hold(store.get(EP_ID)) == "awaiting_photo_approval"
