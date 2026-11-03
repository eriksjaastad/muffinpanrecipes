"""Admin per-stage Re-run (#8604): eligibility, the one synthetic cron dispatch, the buttons.

Every cron_{day} handler is replaced by a recorder, so no stage logic, paid call
or network runs. The synthetic request the handler receives is what is asserted.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from backend.admin import cron_routes
from backend.utils.episode_integrity import DAY_ORDER
from tests.test_photo_request_decision_lifecycle import _SAME_ORIGIN, _Store, _client

WEEK = "2026-W41"  # long past: every stage's cron window has closed
FUTURE_WEEK = "2099-W01"  # nothing is due yet
SECRET = "test-cron-secret"


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
    ("held Sunday", dict(episode={"stages": _stages(wednesday="complete")}, stage="sunday", hold="awaiting_photo_approval"), 409),
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
