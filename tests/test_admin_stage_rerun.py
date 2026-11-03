"""Admin per-stage Re-run (#8604): eligibility, the one synthetic cron dispatch, the buttons.

Every cron_{day} handler is replaced by a recorder, so no stage logic, paid call
or network runs. The synthetic request the handler receives is what is asserted.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import PropertyMock, patch

import pytest

import backend.storage as storage_module
from backend.admin import cron_routes
from backend.storage import PhotoControlUnavailable
from backend.utils.episode_integrity import DAY_ORDER
from backend.utils import admin_rerun_claim, photo_review
from tests.photo_review_helpers import decide, register, wednesday_stage
from tests.test_photo_request_decision_lifecycle import _SAME_ORIGIN, _Store, _client, _episode

WEEK = "2026-W41"  # long past: every stage's cron window has closed
FUTURE_WEEK = "2099-W01"  # nothing is due yet
SECRET = "test-cron-secret"


@pytest.fixture(autouse=True)
def _isolated_control_logs(tmp_path, monkeypatch):
    """Photo-control and admin re-run logs live under tmp_path, never in repo data/."""
    monkeypatch.setattr(storage_module, "PHOTO_CONTROL_DIR", tmp_path / "photo_control")


def _stages(**by_day):
    return {day: {"status": status} for day, status in by_day.items()}


@pytest.fixture
def calls(monkeypatch):
    """Record every cron handler call; none does real work."""
    monkeypatch.setenv("CRON_SECRET", SECRET)
    recorded = []

    def make(day):
        async def handler(request):
            recorded.append({
                "day": day,
                "method": request.method,
                "path": request.url.path,
                "json": await request.json(),
                "authorization": request.headers["authorization"],
            })
            return {"handled": day}
        return handler

    for day in DAY_ORDER:
        monkeypatch.setattr(cron_routes, f"cron_{day}", make(day))
    return recorded


def _rerun(episode, stage, week=WEEK, query="", hold=None):
    store = _Store()
    if episode is not None:
        store.put(week, {"episode_id": week, **episode})
    client, patcher = _client(store)
    try:
        with patch("backend.utils.episode_integrity.recorded_photo_hold", return_value=hold):
            return client.post(f"/admin/episodes/{week}/stages/{stage}/rerun{query}", headers=_SAME_ORIGIN)
    finally:
        patcher.stop()


@pytest.mark.parametrize("stage, stages", [
    ("thursday", _stages(monday="complete", thursday="failed")),   # failed
    ("friday", _stages(monday="complete", thursday="complete")),   # absent and due
])
def test_eligible_stage_dispatches_the_real_handler_once_with_force_and_secret(calls, stage, stages):
    resp = _rerun({"stages": stages}, stage)

    assert resp.status_code == 200
    assert resp.json() == {"handled": stage}
    assert calls == [{
        "day": stage,
        "method": "POST",
        "path": f"/api/cron/{stage}",
        "json": {"episode_id": WEEK, "force": True},
        "authorization": f"Bearer {SECRET}",
    }]


@pytest.mark.parametrize("name, kwargs, status", [
    ("complete stage", dict(episode={"stages": _stages(thursday="complete")}, stage="thursday"), 409),
    ("absent but not yet due", dict(episode={"stages": {}}, stage="monday", week=FUTURE_WEEK), 409),
    ("published week", dict(episode={"published_at": "2026-10-12T00:00:00+00:00", "stages": _stages(thursday="failed")}, stage="thursday"), 409),
    ("missing episode", dict(episode=None, stage="thursday"), 404),
    ("unknown stage", dict(episode={"stages": {}}, stage="funday"), 400),
    ("test namespace", dict(episode={"stages": _stages(thursday="failed")}, stage="thursday", query="?ns=test"), 400),
])
def test_ineligible_requests_are_refused_without_dispatch(calls, name, kwargs, status):
    assert _rerun(**kwargs).status_code == status, name
    assert calls == []


def test_non_iso_week_is_refused_without_dispatch(calls):
    assert _rerun({"stages": {}}, "thursday", week="not-a-week").status_code == 400
    assert calls == []


def test_page_shows_a_button_only_for_failed_or_due_absent_stages():
    store = _Store()
    store.put(WEEK, {
        "episode_id": WEEK,
        "stages": _stages(monday="complete", tuesday="failed", wednesday="complete", thursday="complete"),
    })
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{WEEK}").text
        published = {"episode_id": WEEK, "published_at": "2026-10-12T00:00:00+00:00",
                     "stages": _stages(tuesday="failed")}
        store.put(WEEK, published)
        published_page = client.get(f"/admin/episodes/{WEEK}").text
    finally:
        patcher.stop()

    buttons = re.findall(r'data-action="stage-rerun"[^>]*data-stage="(\w+)"', page)
    assert buttons == ["tuesday", "friday", "saturday", "sunday"]
    assert 'data-action="stage-rerun"' not in published_page


# --- Per-episode claim (#8604): paid dispatches never overlap ------------------
#
# The control log is the REAL filesystem compare-and-swap; only the failures are
# injected. A held run blocks inside its handler, so the order is deterministic.

class _ClaimStore(_Store):
    def __init__(self):
        super().__init__()
        self.fail_read: Exception | None = None
        self.fail_write = None  # predicate(body) -> bool

    def read_admin_rerun_control(self, episode_id):
        if self.fail_read:
            raise self.fail_read
        return super().read_admin_rerun_control(episode_id)

    def create_admin_rerun_control_version(self, episode_id, version, body):
        if self.fail_write and self.fail_write(body):
            raise PhotoControlUnavailable("storage down")
        super().create_admin_rerun_control_version(episode_id, version, body)


@pytest.fixture
def rerun_client():
    store = _ClaimStore()
    store.put(WEEK, {"episode_id": WEEK, "stages": _stages(monday="complete", thursday="failed")})
    client, patcher = _client(store)
    try:
        with patch("backend.utils.episode_integrity.recorded_photo_hold", return_value=None):
            yield store, client
    finally:
        patcher.stop()


def _post_rerun(client, stage):
    return client.post(f"/admin/episodes/{WEEK}/stages/{stage}/rerun", headers=_SAME_ORIGIN)


def _days(calls):
    return [c["day"] for c in calls]


def _claim_log():
    directory = storage_module.PHOTO_CONTROL_DIR.parent / storage_module.ADMIN_RERUN_LOG / "production" / WEEK
    return [json.loads(p.read_text()) for p in sorted(directory.glob("v*.json"))]


class _HeldRun:
    """A thursday re-run whose handler stays inside the claim until finish()."""

    def __init__(self, client, calls, monkeypatch):
        entered, self._go = threading.Event(), threading.Event()
        self._entered = entered

        async def handler(request):
            calls.append({"day": "thursday"})
            entered.set()
            for _ in range(1000):  # bounded, so a failing test cannot hang the run
                if self._go.is_set():
                    break
                await asyncio.sleep(0.01)
            return {"handled": "thursday"}

        monkeypatch.setattr(cron_routes, "cron_thursday", handler)
        self.response = None
        self._thread = threading.Thread(
            target=lambda: setattr(self, "response", _post_rerun(client, "thursday")), daemon=True,
        )

    def start(self):
        self._thread.start()
        assert self._entered.wait(10), "the held handler never started"

    def finish(self):
        self._go.set()
        self._thread.join(10)
        return self.response


def test_concurrent_rerun_dispatches_once_and_a_later_request_runs_after_release(rerun_client, calls, monkeypatch):
    store, client = rerun_client
    held = _HeldRun(client, calls, monkeypatch)
    held.start()

    second = _post_rerun(client, "thursday")

    assert second.status_code == 409 and "already in progress" in second.json()["detail"]
    assert _days(calls) == ["thursday"]
    assert held.finish().status_code == 200
    assert [(v["state"], v["stage"]) for v in _claim_log()] == [("claimed", "thursday"), ("released", "thursday")]
    assert SECRET not in json.dumps(_claim_log())

    assert _post_rerun(client, "friday").status_code == 200
    assert _days(calls) == ["thursday", "friday"]


def test_a_different_stage_of_the_same_episode_is_serialized(rerun_client, calls, monkeypatch):
    store, client = rerun_client
    held = _HeldRun(client, calls, monkeypatch)
    held.start()

    other_day = _post_rerun(client, "friday")  # eligible on its own: absent and due

    assert other_day.status_code == 409 and "already in progress" in other_day.json()["detail"]
    assert _days(calls) == ["thursday"]
    held.finish()


def test_a_handler_that_raises_still_releases_the_claim(rerun_client, calls, monkeypatch):
    store, client = rerun_client

    async def boom(request):
        raise RuntimeError("stage blew up")

    monkeypatch.setattr(cron_routes, "cron_thursday", boom)
    with pytest.raises(RuntimeError):
        _post_rerun(client, "thursday")

    assert [v["state"] for v in _claim_log()] == ["claimed", "released"]
    assert _post_rerun(client, "friday").status_code == 200


@pytest.mark.parametrize(
    "fault", ["read_fails", "claim_write_fails", "unknown_state", "malformed_release"],
)
def test_unreadable_or_unwritable_claim_never_dispatches(rerun_client, calls, fault):
    store, client = rerun_client
    if fault == "read_fails":
        store.fail_read = PhotoControlUnavailable("storage down")
    elif fault == "claim_write_fails":
        store.fail_write = lambda body: True
    elif fault == "malformed_release":
        store.create_admin_rerun_control_version(
            WEEK, 1, {"episode_id": WEEK, "version": 1, "write_id": "x", "state": "released"},
        )
    else:
        store.create_admin_rerun_control_version(
            WEEK, 1, {"episode_id": WEEK, "version": 1, "write_id": "x", "state": "weird"},
        )

    resp = _post_rerun(client, "thursday")

    assert resp.status_code == 503 and "nothing was dispatched" in resp.json()["detail"]
    assert calls == []


def test_unconfirmed_release_keeps_the_episode_blocked(rerun_client, calls):
    store, client = rerun_client
    store.fail_write = lambda body: body["state"] == "released"

    assert _post_rerun(client, "thursday").status_code == 200  # the handler's result still reaches the caller
    store.fail_write = None
    blocked = _post_rerun(client, "friday")

    assert blocked.status_code == 409
    assert _days(calls) == ["thursday"]


# --- Sunday vs the authoritative photo view (#8604) ----------------------------
#
# A recorded hold is patched in (stale or not); the photo control is the real
# log. Only AWAITING or REJECTED keeps a missing Sunday quiet.

def _photo_store(state):
    wed = wednesday_stage()
    ep = _episode(wed)
    store = _ClaimStore()
    store.put(WEEK, ep)
    register(WEEK, wed, store)
    if state == photo_review.REJECTED:
        decide(WEEK, ep, action="reject", store=store)
    elif state == photo_review.APPROVED:
        decide(WEEK, ep, pick=1, store=store)
    return store


def _with_hold(store, fn):
    client, patcher = _client(store)
    try:
        with patch("backend.utils.episode_integrity.recorded_photo_hold", return_value="awaiting_photo_approval"):
            return fn(client)
    finally:
        patcher.stop()


def _sunday_button(client):
    page = client.get(f"/admin/episodes/{WEEK}").text
    return "sunday" in re.findall(r'data-action="stage-rerun"[^>]*data-stage="(\w+)"', page)


@pytest.mark.parametrize("state, offered", [
    (photo_review.AWAITING, False),
    (photo_review.REJECTED, False),
    (photo_review.APPROVED, True),  # a stale publish_hold must not hide Sunday
])
def test_page_offers_a_held_missing_sunday_only_when_the_photo_view_is_not_pending(state, offered):
    assert _with_hold(_photo_store(state), _sunday_button) is offered


@pytest.mark.parametrize("state, status", [
    (photo_review.AWAITING, 409),
    (photo_review.REJECTED, 409),
    (photo_review.APPROVED, 200),
])
def test_post_loads_a_fresh_photo_view_before_dispatching_sunday(calls, state, status):
    resp = _with_hold(_photo_store(state), lambda c: _post_rerun(c, "sunday"))

    assert resp.status_code == status
    assert _days(calls) == (["sunday"] if status == 200 else [])


def test_unreadable_photo_control_returns_503_without_dispatch_and_releases_the_claim(calls):
    store = _photo_store(photo_review.APPROVED)
    store.fail_control_read = PhotoControlUnavailable("control down")

    resp = _with_hold(store, lambda c: _post_rerun(c, "sunday"))

    assert resp.status_code == 503
    assert calls == []
    assert [v["state"] for v in _claim_log()] == ["claimed", "released"]


# --- Route security: nothing dispatches without auth and a same-origin request --

def _eligible_store():
    store = _ClaimStore()
    store.put(WEEK, {"episode_id": WEEK, "stages": _stages(monday="complete", thursday="failed")})
    return store


def test_unauthenticated_request_cannot_dispatch(calls):
    from backend.auth.middleware import get_session_manager
    from backend.config import config

    client, patcher = _client(_eligible_store(), authed=False)
    client.app.dependency_overrides[get_session_manager] = lambda: object()
    try:
        with patch.object(type(config), "auth_bypass", new_callable=PropertyMock, return_value=False):
            resp = client.post(
                f"/admin/episodes/{WEEK}/stages/thursday/rerun", headers=_SAME_ORIGIN,
                follow_redirects=False,
            )
    finally:
        patcher.stop()

    assert resp.status_code == 307
    assert calls == []
    assert _claim_log() == []


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.test"}, {"Referer": "https://evil.test/x"}],
                         ids=["missing-origin", "cross-origin", "cross-referer"])
def test_authenticated_request_without_same_origin_cannot_dispatch(calls, headers):
    client, patcher = _client(_eligible_store())
    try:
        resp = client.post(f"/admin/episodes/{WEEK}/stages/thursday/rerun", headers=headers)
    finally:
        patcher.stop()

    assert resp.status_code == 403
    assert calls == []
    assert _claim_log() == []


# --- Crash reconciliation (#8604): an operator releases a claim no live run can hold --
#
# A hard process crash skips the release, so the log keeps a ``claimed`` version.
# Acquisition never expires it; only the explicit reconcile POST appends a release.

_RECONCILE = f"/admin/episodes/{WEEK}/rerun-claim/reconcile"


def _crash_after_claim(monkeypatch):
    """Simulate a killed process: the handler runs, the release never happens."""
    monkeypatch.setattr(admin_rerun_claim, "_release", lambda *args, **kwargs: None)


def _clock_after_bound(monkeypatch, extra=timedelta(minutes=1)):
    later = datetime.now(timezone.utc) + admin_rerun_claim.RECONCILE_MIN_AGE + extra
    monkeypatch.setattr(admin_rerun_claim, "_utcnow", lambda: later)


def _reconcile_post(client, headers=_SAME_ORIGIN):
    return client.post(_RECONCILE, headers=headers)


def _seed_claim(store, claimed_at, **overrides):
    body = admin_rerun_claim._body(WEEK, 1, "claimed", "thursday", at=claimed_at)
    store.create_admin_rerun_control_version(WEEK, 1, {**body, **overrides})


def test_crashed_claim_blocks_until_old_reconcile_releases_it_then_a_rerun_runs(rerun_client, calls, monkeypatch):
    store, client = rerun_client
    with monkeypatch.context() as crash:
        _crash_after_claim(crash)
        assert _post_rerun(client, "thursday").status_code == 200
    assert [v["state"] for v in _claim_log()] == ["claimed"]

    busy = _post_rerun(client, "friday")
    assert busy.status_code == 409
    assert f"/admin/episodes/{WEEK}/rerun-claim/reconcile" in busy.json()["detail"]
    assert "try again later" not in busy.json()["detail"]

    _clock_after_bound(monkeypatch)
    resp = _reconcile_post(client)

    assert resp.status_code == 200
    assert resp.json() == {"reconciled": True, "episode_id": WEEK, "stage": "thursday", "released_version": 2}
    log = _claim_log()
    assert [(v["state"], v["stage"], v["version"]) for v in log] == [("claimed", "thursday", 1), ("released", "thursday", 2)]
    assert log[1]["outcome"] == admin_rerun_claim.RECONCILED_OUTCOME and log[1]["reconciled_version"] == 1
    assert not {"email", "sub", "user"} & set(log[1])
    assert SECRET not in json.dumps(log) and "erik@example.com" not in json.dumps(log)

    assert _post_rerun(client, "friday").status_code == 200
    assert _days(calls) == ["thursday", "friday"]


def test_recent_claim_is_not_reconciled_and_still_blocks(rerun_client, calls, monkeypatch):
    store, client = rerun_client
    _crash_after_claim(monkeypatch)
    assert _post_rerun(client, "thursday").status_code == 200

    refused = _reconcile_post(client)

    assert refused.status_code == 409 and "Nothing was changed" in refused.json()["detail"]
    assert [v["state"] for v in _claim_log()] == ["claimed"]
    assert _post_rerun(client, "friday").status_code == 409
    assert _days(calls) == ["thursday"]


def test_reconcile_age_bound_uses_the_injected_clock_and_exceeds_vercel_max_duration():
    assert admin_rerun_claim.RECONCILE_MIN_AGE > timedelta(seconds=300) * 10  # vercel.json maxDuration is 300
    store = _ClaimStore()
    claimed_at = datetime(2026, 10, 1, tzinfo=timezone.utc)
    _seed_claim(store, claimed_at)
    just_before = claimed_at + admin_rerun_claim.RECONCILE_MIN_AGE - timedelta(seconds=1)

    with pytest.raises(admin_rerun_claim.RerunNotReconcilable):
        admin_rerun_claim.reconcile(store, WEEK, now=lambda: just_before)
    assert admin_rerun_claim.reconcile(store, WEEK, now=lambda: claimed_at + admin_rerun_claim.RECONCILE_MIN_AGE)["version"] == 2


def test_concurrent_reconcile_lost_cas_writes_nothing_and_reports_conflict(rerun_client, monkeypatch):
    store, client = rerun_client
    _seed_claim(store, datetime(2026, 10, 1, tzinfo=timezone.utc))
    original = store.create_admin_rerun_control_version

    def other_operator_wins_first(episode_id, version, body):
        monkeypatch.setattr(store, "create_admin_rerun_control_version", original)
        original(episode_id, version, {**body, "write_id": "other-operator"})
        original(episode_id, version, body)  # the real CAS refuses the loser

    monkeypatch.setattr(store, "create_admin_rerun_control_version", other_operator_wins_first)

    resp = _reconcile_post(client)

    assert resp.status_code == 409 and "Nothing was changed" in resp.json()["detail"]
    log = _claim_log()
    assert [v["state"] for v in log] == ["claimed", "released"]
    assert log[1]["write_id"] == "other-operator"  # the winner's record is the only release


@pytest.mark.parametrize("name, setup, status", [
    ("no claim ever written", lambda s: None, 409),
    ("already released", lambda s: (_seed_claim(s, datetime(2026, 10, 1, tzinfo=timezone.utc)),
                                    s.create_admin_rerun_control_version(WEEK, 2, admin_rerun_claim._body(WEEK, 2, "released", "thursday"))), 409),
    ("unknown state", lambda s: _seed_claim(s, datetime(2026, 10, 1, tzinfo=timezone.utc), state="weird"), 503),
    ("unusable timestamp", lambda s: _seed_claim(s, datetime(2026, 10, 1, tzinfo=timezone.utc), at="not-a-time"), 503),
    ("unreadable log", lambda s: setattr(s, "fail_read", PhotoControlUnavailable("storage down")), 503),
    ("release write fails", lambda s: (_seed_claim(s, datetime(2026, 10, 1, tzinfo=timezone.utc)),
                                       setattr(s, "fail_write", lambda body: True)), 503),
])
def test_reconcile_fails_truthfully_and_leaves_the_log_unchanged(rerun_client, name, setup, status):
    store, client = rerun_client
    setup(store)
    before = _claim_log()

    resp = _reconcile_post(client)

    assert resp.status_code == status, name
    assert resp.json().get("reconciled") is None
    assert _claim_log() == before


def test_reconcile_requires_auth_and_same_origin():
    from backend.auth.middleware import get_session_manager
    from backend.config import config

    store = _eligible_store()
    _seed_claim(store, datetime(2026, 10, 1, tzinfo=timezone.utc))
    before = _claim_log()

    anon, patcher = _client(store, authed=False)
    anon.app.dependency_overrides[get_session_manager] = lambda: object()
    try:
        with patch.object(type(config), "auth_bypass", new_callable=PropertyMock, return_value=False):
            unauthenticated = anon.post(_RECONCILE, headers=_SAME_ORIGIN, follow_redirects=False)
    finally:
        patcher.stop()
    assert unauthenticated.status_code == 307

    client, patcher = _client(store)
    try:
        refusals = [
            _reconcile_post(client, headers).status_code
            for headers in ({}, {"Origin": "https://evil.test"}, {"Referer": "https://evil.test/x"})
        ]
    finally:
        patcher.stop()
    assert refusals == [403, 403, 403]
    assert _claim_log() == before
