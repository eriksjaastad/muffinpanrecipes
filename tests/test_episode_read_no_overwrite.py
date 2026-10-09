"""A failed episode read must never become a fresh episode that overwrites the real one (#8145).

The stage handlers load the week through `_load_or_create_episode`. It used the
lenient `load_episode`, which turns ANY Blob error into the bundled filesystem
copy — on the Lambda, no copy at all — so a read failure looked exactly like
"this week has no episode yet". Monday then built a fresh skeleton and saved
it, replacing the real episode. A re-fired Monday (the RUNBOOK recovery path,
used on W41 on 2026-10-05) during a Blob blip would have wiped Tuesday's
dialogue, Wednesday's paid photos and the photo approval.

The read is now strict: None only for a genuinely missing episode, a raise for
a failed read. The handler alerts and returns 503 without writing anything.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from backend.admin import cron_routes
from backend.storage import _CloudBackend


@pytest.fixture
def failing_cloud():
    """A real cloud backend whose every Blob request fails, with no local copy."""
    with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake-token-for-test"}):
        backend = _CloudBackend()
    with patch("requests.get", side_effect=ConnectionError("blob list 503")), \
         patch.object(backend._fs, "load_episode", return_value=None), \
         patch.object(backend, "save_episode") as save, \
         patch.object(cron_routes, "storage", backend):
        yield SimpleNamespace(backend=backend, save=save)


def _request(day: str) -> Request:
    return cast(Request, SimpleNamespace(url=SimpleNamespace(path=f"/api/cron/{day}")))


def _run(handler, day: str, body: cron_routes.StageRequest):
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "upload_latest_json"), \
         patch.object(cron_routes, "_get_orchestrator"), \
         patch.object(cron_routes, "load_published_catalog", return_value={"recipes": []}), \
         patch.object(cron_routes, "_bake_through_gates", return_value=({"title": "x"}, [])), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=([], {})), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(handler(_request(day)))
    return exc.value, notify


def test_monday_refire_during_a_blob_failure_writes_nothing(failing_cloud):
    body = cron_routes.StageRequest(episode_id="2026-W41", concept="Black Sesame Popover Cups")

    error, notify = _run(cron_routes.cron_monday, "monday", body)

    failing_cloud.save.assert_not_called()
    assert error.status_code == 503
    assert "2026-W41" in error.detail
    notify.assert_called_once()
    assert "could not be read" in notify.call_args.kwargs["error_message"]


def test_tuesday_reports_the_read_failure_not_a_missing_monday(failing_cloud):
    body = cron_routes.StageRequest(episode_id="2026-W41")

    error, notify = _run(cron_routes.cron_tuesday, "tuesday", body)

    failing_cloud.save.assert_not_called()
    assert error.status_code == 503
    assert "monday stage is" not in error.detail
    notify.assert_called_once()


def test_a_genuinely_missing_episode_still_starts_a_new_week():
    """Not-found is the normal first-Monday case and must keep working."""
    store = MagicMock()
    store.load_episode_strict.return_value = None
    with patch.object(cron_routes, "storage", store):
        ep = cron_routes._load_or_create_episode("2026-W42", "Some Concept", "monday")

    store.load_episode_strict.assert_called_once_with("2026-W42")
    assert ep["episode_id"] == "2026-W42"
    assert ep["stages"] == {}
    assert ep["concept"] == "Some Concept"


def test_an_existing_episode_is_returned_unchanged():
    stored = {"episode_id": "2026-W41", "stages": {"monday": {"status": "complete"}}}
    store = MagicMock()
    store.load_episode_strict.return_value = stored
    with patch.object(cron_routes, "storage", store):
        assert cron_routes._load_or_create_episode("2026-W41", "ignored", "tuesday") is stored


def test_the_stage_runner_script_does_not_mistake_a_failed_read_for_a_new_episode(failing_cloud):
    """scripts/run_pipeline_stage.py saves whatever it loaded, so it reads strictly too."""
    import scripts.run_pipeline_stage as rps

    with patch.object(rps, "storage", failing_cloud.backend):
        with pytest.raises(ConnectionError):
            rps.load_episode("2026-W41")
