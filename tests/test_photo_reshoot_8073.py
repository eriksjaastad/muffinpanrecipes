"""Dashboard Reshoot (#8073): a whole new Wednesday image set from the photo review.

The real photo-control log (filesystem code) backs every test; the shoot, the
upload and the email are the only things replaced. No network, no paid call.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.admin import cron_routes
from backend.agents.factory import create_agent
from backend.core.task import Task
from backend.storage import PhotoControlConflict
from backend.utils import photo_review
from backend.utils.episode_integrity import recorded_photo_hold
from tests.photo_review_helpers import BLOB, decide, register, wednesday_stage
from tests.test_art_director_image_pipeline import _png_bytes
from tests.test_photo_approval_7936 import (
    EP_ID,
    _SAME_ORIGIN,
    _client,
    _control,
    _episode,
    _photography_result,
    _Store,
)

DIALOGUE = [{"character": "Julian Torres", "message": "Kept."}]


def _store_with_set(**stage_overrides):
    """An episode with a finished Wednesday (dialogue included) and a registered set."""
    wed = wednesday_stage(dialogue=DIALOGUE, judge_verdict="PASS", **stage_overrides)
    store = _Store()
    store.put(EP_ID, _episode(wed))
    register(EP_ID, wed, store)
    return store, wed


def _start(store, wed, calls=3):
    ep = store.get(EP_ID)
    return photo_review.start_reshoot(store, EP_ID, ep, wed["photo_review"]["image_set_id"], calls)


def _finish(store, started):
    photo_review.clear_reshoot_in_flight(store, EP_ID, started.reshoots["in_flight"]["id"])


# --- start_reshoot: the cap and the double click live on the control ---------------

def test_start_refuses_past_the_call_cap():
    store, wed = _store_with_set()
    for _ in range(3):
        _finish(store, _start(store, wed))
    with pytest.raises(photo_review.ReshootRefused, match="9 of 9"):
        _start(store, wed)
    assert _control(store)["reshoots"]["calls_used"] == 9


def test_second_concurrent_start_loses_the_compare_and_swap_and_is_refused():
    store, wed = _store_with_set()
    real_create = store.create_photo_control_version
    raced = []

    def rival_wins_first(episode_id, version, body):
        if not raced:
            raced.append(True)
            _start(store, wed)  # the other click creates this version first
        return real_create(episode_id, version, body)

    with patch.object(store, "create_photo_control_version", side_effect=rival_wins_first):
        with pytest.raises(photo_review.ReshootRefused, match="already running"):
            _start(store, wed)
    assert _control(store)["reshoots"]["calls_used"] == 3  # counted once, not twice


def test_a_reshoot_in_flight_for_over_six_minutes_no_longer_blocks():
    store, wed = _store_with_set()
    long_ago = (datetime.now(timezone.utc) - timedelta(seconds=photo_review.RESHOOT_IN_FLIGHT_S + 5)).isoformat()
    with patch.object(photo_review, "_now", return_value=long_ago):
        _start(store, wed)
    second = _start(store, wed)
    assert second.reshoots["calls_used"] == 6


def test_start_refuses_a_stale_page():
    store, wed = _store_with_set()
    with pytest.raises(photo_review.ReshootRefused, match="changed since this page loaded"):
        photo_review.start_reshoot(store, EP_ID, store.get(EP_ID), "0" * 16, 3)


def test_start_refuses_once_sunday_has_claimed():
    store, wed = _store_with_set()
    ep = store.get(EP_ID)
    decide(EP_ID, ep, store=store)
    photo_review.claim_for_publication(store, EP_ID, ep)
    with pytest.raises(photo_review.ReshootRefused, match="frozen"):
        _start(store, wed)


def test_the_count_survives_a_decision_after_the_reshoot():
    store, wed = _store_with_set()
    _finish(store, _start(store, wed))
    decide(EP_ID, store.get(EP_ID), store=store)
    ctrl = _control(store)
    assert ctrl["state"] == "approved" and ctrl["reshoots"]["calls_used"] == 3


# --- The route ---------------------------------------------------------------------

def _reshoot(client, wed, headers=_SAME_ORIGIN):
    return client.post(
        f"/admin/episodes/{EP_ID}/photos/reshoot",
        json={"image_set_id": wed["photo_review"]["image_set_id"]}, headers=headers,
    )


@contextmanager
def _shoot_env(store, tmp_path, authed=True):
    """The route over ``store``, with the shoot, upload and email replaced."""
    orch = MagicMock()
    orch.return_value._execute_stage_photography.return_value = _photography_result(
        tmp_path, "g20261009T000000Z-bbbbbb",
    )
    store.save_image = lambda p, _b: f"{BLOB}/{p.split('images/')[1]}"
    dialogue = MagicMock()
    client, patcher = _client(store, authed=authed)
    with patch.object(cron_routes, "storage", store), \
         patch.object(cron_routes, "_get_orchestrator", return_value=orch), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", dialogue), \
         patch.object(cron_routes, "notify_photos_ready", return_value=True) as notify:
        try:
            yield client, orch.return_value._execute_stage_photography, notify, dialogue
        finally:
            patcher.stop()


def test_reshoot_route_refuses_a_cross_origin_post(tmp_path):
    store, wed = _store_with_set()
    with _shoot_env(store, tmp_path) as (client, shoot, _notify, _dialogue):
        resp = _reshoot(client, wed, {"Origin": "https://evil.example"})
    assert resp.status_code == 403
    shoot.assert_not_called()
    assert _control(store)["version"] == 1


def test_reshoot_route_runs_on_the_event_loop_not_a_thread():
    # A threadpool route would hold storage.prefix_scope (a shared singleton
    # attribute) while other requests run: RUNBOOK Incident 1.
    import inspect

    from fastapi import FastAPI

    from backend.admin.routes import create_routes

    app = FastAPI()
    create_routes(app)
    route = next(r for r in app.routes if getattr(r, "path", "").endswith("/photos/reshoot"))
    assert inspect.iscoroutinefunction(route.endpoint)


def test_reshoot_route_refuses_an_unauthenticated_post(tmp_path, monkeypatch):
    from backend.auth import middleware

    store, wed = _store_with_set()
    monkeypatch.setattr(middleware, "_session_manager", MagicMock(verify_token=lambda _t: None))
    with patch("backend.config._Config.auth_bypass", new=property(lambda self: False)):
        with _shoot_env(store, tmp_path, authed=False) as (client, shoot, _notify, _dialogue):
            resp = client.post(
                f"/admin/episodes/{EP_ID}/photos/reshoot",
                json={"image_set_id": wed["photo_review"]["image_set_id"]},
                headers=_SAME_ORIGIN, follow_redirects=False,
            )
    assert resp.status_code in (401, 403, 307)
    shoot.assert_not_called()
    assert _control(store)["version"] == 1


def test_reshoot_route_refuses_a_claimed_week_without_an_image_call(tmp_path):
    store, wed = _store_with_set()
    ep = store.get(EP_ID)
    decide(EP_ID, ep, store=store)
    photo_review.claim_for_publication(store, EP_ID, ep)
    with _shoot_env(store, tmp_path) as (client, shoot, notify, _dialogue):
        resp = _reshoot(client, wed)
    assert resp.status_code == 409
    shoot.assert_not_called()
    notify.assert_not_called()


def test_reshoot_registers_a_new_unapproved_set_and_keeps_wednesdays_conversation(tmp_path):
    store, wed = _store_with_set()
    decide(EP_ID, store.get(EP_ID), store=store)
    before = store.get(EP_ID)["stages"]["wednesday"]
    with _shoot_env(store, tmp_path) as (client, shoot, notify, dialogue):
        resp = _reshoot(client, wed)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["calls_left"] == 6 and body["image_set_id"] != wed["photo_review"]["image_set_id"]
    assert shoot.call_args.kwargs["max_rounds"] == 1
    ctrl = _control(store)
    assert ctrl["state"] == "awaiting" and ctrl["decision"] is None
    assert ctrl["request"]["image_set_id"] == body["image_set_id"]
    assert "in_flight" not in ctrl["reshoots"] and ctrl["reshoots"]["calls_used"] == 3
    notify.assert_called_once()
    dialogue.assert_not_called()
    after = store.get(EP_ID)["stages"]["wednesday"]
    for kept in ("status", "dialogue", "judge_verdict", "completed_at"):
        assert after[kept] == before[kept]
    assert after["photo_review"]["image_set_id"] == body["image_set_id"]


def test_a_failed_shoot_frees_the_next_click_but_keeps_the_calls_counted(tmp_path):
    store, wed = _store_with_set()
    with _shoot_env(store, tmp_path) as (client, shoot, _notify, _dialogue):
        shoot.side_effect = RuntimeError("image model down")
        failed = _reshoot(client, wed)
        shoot.side_effect = None
        retried = _reshoot(client, wed)
    assert failed.status_code == 502 and retried.status_code == 200
    assert _control(store)["reshoots"]["calls_used"] == 6


# --- One reshoot = one round = 3 image calls ----------------------------------------

def test_max_rounds_one_makes_exactly_three_image_calls(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "ROOT", tmp_path)
    agent = create_agent("art_director")
    calls = []
    monkeypatch.setattr(agent, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(agent, "_call_image_model", lambda *_a, **_k: calls.append(1) or _png_bytes())
    # Identical 1x1 PNGs fail the diversity check, which buys the automatic reshoot.
    monkeypatch.setattr(
        agent, "_evaluate_images_vision",
        lambda _variants, _title, _facts="", _pan_size="standard": {"passed": True, "recommended_winner": 1},
    )
    task = Task(type="photograph_recipe", content="Photograph",
                context={"recipe_id": "test-recipe", "recipe_data": {"title": "Test Muffins"}, "max_rounds": 1})
    assert agent.process_task(task).success
    assert len(calls) == 3


# --- Failure paths of the route (preflight, #8073) ----------------------------------

def test_clearing_another_reshoots_marker_is_a_no_op():
    store, wed = _store_with_set()
    started = _start(store, wed)
    photo_review.clear_reshoot_in_flight(store, EP_ID, "not-this-reshoot")
    assert _control(store)["version"] == started.version
    assert "in_flight" in _control(store)["reshoots"]


def test_sunday_claiming_mid_shoot_is_a_409_with_the_calls_counted_and_the_set_kept(tmp_path):
    store, wed = _store_with_set()
    ep = store.get(EP_ID)
    decide(EP_ID, ep, store=store)

    def sunday_claims(*_a, **_k):
        photo_review.claim_for_publication(store, EP_ID, ep)
        return _photography_result(tmp_path, "g20261009T000000Z-bbbbbb")

    with _shoot_env(store, tmp_path) as (client, shoot, notify, _dialogue):
        shoot.side_effect = sunday_claims
        resp = _reshoot(client, wed)
    assert resp.status_code == 409
    ctrl = _control(store)
    assert ctrl["state"] == "claimed" and ctrl["request"]["image_set_id"] == wed["photo_review"]["image_set_id"]
    assert ctrl["reshoots"]["calls_used"] == 3
    notify.assert_not_called()


def test_an_unreadable_episode_after_the_shoot_registers_nothing_and_says_so(tmp_path):
    store, wed = _store_with_set()
    with _shoot_env(store, tmp_path) as (client, shoot, notify, _dialogue):
        shoot.side_effect = lambda *_a, **_k: (
            setattr(store, "fail_verified", RuntimeError("blob stale")) or _photography_result(tmp_path, "g20261009T000000Z-bbbbbb")
        )
        resp = _reshoot(client, wed)
    assert resp.status_code == 502 and "not registered" in resp.json()["detail"]
    ctrl = _control(store)
    assert ctrl["request"]["image_set_id"] == wed["photo_review"]["image_set_id"]
    assert ctrl["reshoots"]["calls_used"] == 3 and "in_flight" not in ctrl["reshoots"]
    notify.assert_not_called()


# --- Codex round (#8073) --------------------------------------------------------------

def test_a_reshoot_after_sundays_hold_re_points_the_hold_at_the_new_set(tmp_path):
    store, wed = _store_with_set()
    ep = store.get(EP_ID)
    ep["publish_hold"] = {
        "reason": "awaiting_photo_approval", "image_set_id": wed["photo_review"]["image_set_id"],
        "since": "2026-10-11T00:00:00+00:00", "notified": True,
        "last_attempt": {"at": "2026-10-11T00:00:00+00:00", "outcome": "held"},
    }
    store.put(EP_ID, ep)
    assert recorded_photo_hold(store.get(EP_ID)) == "awaiting_photo_approval"
    with _shoot_env(store, tmp_path) as (client, _shoot, _notify, _dialogue):
        resp = _reshoot(client, wed)
    assert resp.status_code == 200, resp.text
    after = store.get(EP_ID)
    assert recorded_photo_hold(after) == "awaiting_photo_approval"
    assert after["publish_hold"]["image_set_id"] == resp.json()["image_set_id"]
    assert "last_attempt" not in after["publish_hold"] and after["publish_hold"]["notified"] is False


def test_any_episode_read_failure_is_a_503_before_anything_is_counted(tmp_path):
    store, wed = _store_with_set()
    store.fail_verified = RuntimeError("blob down")
    with _shoot_env(store, tmp_path) as (client, shoot, _notify, _dialogue):
        resp = _reshoot(client, wed)
    assert resp.status_code == 503 and "nothing was counted" in resp.json()["detail"]
    shoot.assert_not_called()
    assert _control(store)["version"] == 1


def test_clearing_the_marker_retries_a_lost_compare_and_swap():
    store, wed = _store_with_set()
    started = _start(store, wed)
    real_create = store.create_photo_control_version
    lost = []

    def lose_once(episode_id, version, body):
        if not lost:
            lost.append(True)
            raise PhotoControlConflict("lost")
        return real_create(episode_id, version, body)

    with patch.object(store, "create_photo_control_version", side_effect=lose_once):
        _finish(store, started)
    assert "in_flight" not in _control(store)["reshoots"]


def test_an_uncertain_start_points_at_the_page_instead_of_promising_nothing_was_counted(tmp_path):
    # A control write can land and then fail its verification read, so the
    # handler cannot know whether the calls were counted (#8073).
    store, wed = _store_with_set()
    with _shoot_env(store, tmp_path) as (client, shoot, _notify, _dialogue), \
         patch("backend.utils.photo_review.start_reshoot", side_effect=OSError("verify read failed")):
        resp = _reshoot(client, wed)
    detail = resp.json()["detail"]
    assert resp.status_code == 503 and "Reload the page" in detail and "counted" not in detail
    shoot.assert_not_called()


def test_a_shoot_that_lost_its_marker_cannot_register_over_the_newer_reshoot():
    store, wed = _store_with_set()
    started = _start(store, wed)
    version = _control(store)["version"]
    newer = dict(wed["photo_review"], image_set_id="feedfacefeedface")
    with pytest.raises(photo_review.ReshootRefused, match="newer reshoot"):
        photo_review.register_request(store, EP_ID, {**_control(store)["request"], **newer}, reshoot_id="expired-shoot")
    assert _control(store)["version"] == version
    assert started.reshoots["in_flight"]["id"] != "expired-shoot"
