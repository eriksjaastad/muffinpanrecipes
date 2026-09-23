"""HTTP-level checks for the temporary W39 scheduled-cron pause."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.admin import cron_routes


_CRON_SECRET = "synthetic-w39-cron-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(cron_routes.config, "_local_dev", False)
    monkeypatch.setenv("CRON_SECRET", _CRON_SECRET)
    app = FastAPI()
    app.include_router(cron_routes.router)
    with TestClient(app) as test_client:
        yield test_client


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_CRON_SECRET}"}


@pytest.mark.parametrize("stage", ["thursday", "friday", "saturday", "sunday"])
def test_w39_scheduled_get_returns_explicit_pause_before_any_stage_work(
    client, monkeypatch, stage,
):
    monkeypatch.setattr(cron_routes, "_current_episode_id", lambda: "2026-W39")
    parse_body = AsyncMock(side_effect=AssertionError("paused GET parsed a body"))
    day_check = Mock(side_effect=AssertionError("paused GET validated weekday"))
    monkeypatch.setattr(cron_routes, "_parse_body", parse_body)
    monkeypatch.setattr(cron_routes, "_verify_day_of_week", day_check)

    forbidden = AssertionError("paused GET reached stage side effects")
    for owner, name in (
        (cron_routes, "_load_or_create_episode"),
        (cron_routes.storage, "load_episode"),
        (cron_routes.storage, "save_episode"),
        (cron_routes, "_get_orchestrator"),
        (cron_routes, "_generate_and_judge_dialogue"),
        (cron_routes, "_run_simulation"),
        (cron_routes, "regenerate_and_upload"),
        (cron_routes, "notify_pipeline_failure"),
        (cron_routes, "notify_judge_failure"),
        (cron_routes, "notify_judge_advisory"),
        (cron_routes, "generate_response"),
        (cron_routes, "generate_judge_response"),
    ):
        monkeypatch.setattr(owner, name, Mock(side_effect=forbidden))
    monkeypatch.setattr(
        cron_routes, "_test_mode_scope",
        Mock(side_effect=forbidden),
    )

    response = client.get(f"/api/cron/{stage}", headers=_auth_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "status": "paused",
        "stage": stage,
        "episode_id": "2026-W39",
        "reason": (
            "Temporary W39 containment: this scheduled stage was intentionally skipped "
            "while the existing Tuesday/Wednesday failures remain unresolved. "
            "No stage was completed or published."
        ),
    }
    assert "complete" != payload["status"]
    parse_body.assert_not_awaited()
    day_check.assert_not_called()


def test_unauthorized_w39_get_is_rejected_before_week_lookup_or_pause(client, monkeypatch):
    current_week = Mock(side_effect=AssertionError("unauthorized request checked week"))
    monkeypatch.setattr(cron_routes, "_current_episode_id", current_week)
    parse_body = AsyncMock(side_effect=AssertionError("unauthorized request parsed body"))
    monkeypatch.setattr(cron_routes, "_parse_body", parse_body)

    response = client.get("/api/cron/thursday")

    assert response.status_code == 401
    current_week.assert_not_called()
    parse_body.assert_not_awaited()


@pytest.mark.parametrize(
    "stage,week",
    [
        ("tuesday", "2026-W39"),
        ("wednesday", "2026-W39"),
        ("thursday", "2026-W40"),
    ],
)
def test_other_days_and_next_week_continue_into_normal_dispatch(
    client, monkeypatch, stage, week,
):
    monkeypatch.setattr(cron_routes, "_current_episode_id", lambda: week)
    monkeypatch.setattr(cron_routes, "_verify_day_of_week", lambda *_args: None)
    monkeypatch.setattr(cron_routes, "_test_mode_scope", lambda _body: nullcontext())
    dispatch = Mock(side_effect=HTTPException(status_code=418, detail="normal dispatch reached"))
    monkeypatch.setattr(cron_routes, "_load_or_create_episode", dispatch)

    response = client.get(f"/api/cron/{stage}", headers=_auth_headers())

    assert response.status_code == 418
    assert response.json()["detail"] == "normal dispatch reached"
    dispatch.assert_called_once()


def test_scheduled_pause_expires_at_monday_utc_week_boundary(client, monkeypatch):
    class FrozenDateTime:
        now_value = datetime(2026, 9, 27, 23, 59, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.now_value.astimezone(tz) if tz else cls.now_value

    monkeypatch.setattr(cron_routes, "datetime", FrozenDateTime)
    monkeypatch.setattr(cron_routes, "_verify_day_of_week", lambda *_args: None)
    monkeypatch.setattr(cron_routes, "_test_mode_scope", lambda _body: nullcontext())
    dispatch = Mock(side_effect=HTTPException(status_code=418, detail="normal dispatch reached"))
    monkeypatch.setattr(cron_routes, "_load_or_create_episode", dispatch)

    before_expiry = client.get("/api/cron/sunday", headers=_auth_headers())
    assert before_expiry.status_code == 200
    assert before_expiry.json()["episode_id"] == "2026-W39"
    assert before_expiry.json()["status"] == "paused"
    dispatch.assert_not_called()

    FrozenDateTime.now_value = datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)
    at_expiry = client.get("/api/cron/sunday", headers=_auth_headers())

    assert at_expiry.status_code == 418
    assert at_expiry.json()["detail"] == "normal dispatch reached"
    dispatch.assert_called_once()


def test_w39_malformed_post_keeps_existing_body_validation(client, monkeypatch):
    current_week = Mock(side_effect=AssertionError("POST should not check pause week"))
    monkeypatch.setattr(cron_routes, "_current_episode_id", current_week)
    response = client.post(
        "/api/cron/thursday",
        content=b'{"episode_id": }',
        headers={**_auth_headers(), "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert "Malformed cron request body" in response.json()["detail"]
    current_week.assert_not_called()


def test_w39_valid_manual_post_keeps_existing_dispatch_available(client, monkeypatch):
    monkeypatch.setattr(cron_routes, "_test_mode_scope", lambda _body: nullcontext())
    dispatch = Mock(side_effect=HTTPException(status_code=418, detail="manual dispatch reached"))
    monkeypatch.setattr(cron_routes, "_load_or_create_episode", dispatch)
    current_week = Mock(side_effect=AssertionError("POST should not check pause week"))
    monkeypatch.setattr(cron_routes, "_current_episode_id", current_week)

    response = client.post(
        "/api/cron/thursday",
        json={"episode_id": "2026-W39", "force": True},
        headers=_auth_headers(),
    )

    assert response.status_code == 418
    assert response.json()["detail"] == "manual dispatch reached"
    dispatch.assert_called_once()
    current_week.assert_not_called()
