"""Sunday publishes over a wording-only copy FAIL once auto-fix is spent (#8607).

W42 had a recipe, approved photos and six complete days, and never published:
the reviewer failed "easy" in the description, which auto-fix never rewrites.
The real reviewer and fixer code run here; only their model calls are faked.
A safety FAIL, a malformed verdict, or any deterministic failure still stops.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from hypothesis import given, strategies as st

from backend.admin import cron_routes
from backend.admin.cron import editorial_qa
from tests.test_advisory_judge_publishes import _sunday_episode, _sunday_request
from tests.test_cron_fail_open_audit import _reviewable_episode

COPY = ("STATUS: FAIL\nISSUES:\n- TITLE RULE VIOLATION (10b): 'easy' in the description\n"
        "SEVERITY: COPY\nRECOMMENDATION: Drop 'easy'.")
SAFETY = ("STATUS: FAIL\nISSUES:\n- Chicken is only taken to 120F internal temperature\n"
          "SEVERITY: BLOCKING\nRECOMMENDATION: Cook chicken to 165F.")


def _sunday(verdict: str):
    episode = _sunday_episode()
    episode["stages"]["monday"] = _reviewable_episode()["stages"]["monday"]
    recipe = episode["stages"]["monday"]["recipe_data"]
    body = cron_routes.StageRequest(episode_id="2026-W99", force=True)
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue",
                      return_value=([{"character": "Devon Park", "message": "live"}], "PASS")), \
         patch.object(editorial_qa, "_recent_catalog_titles", return_value=["Something Else"]), \
         patch.object(editorial_qa, "generate_judge_response", return_value=verdict), \
         patch.object(editorial_qa, "generate_response", return_value=json.dumps(recipe)), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "_set_static_deploy_state"), \
         patch.object(cron_routes, "_complete_static_source_handoff"), \
         patch.object(cron_routes, "regenerate_and_upload", create=True), \
         patch.object(cron_routes, "_announce_advisory_publication"), \
         patch.object(cron_routes, "_submit_sunday_indexnow"), \
         patch.object(cron_routes, "notify_pipeline_failure"), \
         patch.object(cron_routes, "notify_judge_failure") as hard_alert, \
         patch.object(cron_routes, "notify_copy_advisory", return_value=True) as advisory:
        try:
            result = asyncio.run(cron_routes.cron_sunday(_sunday_request()))
        except HTTPException as exc:
            result = exc
    return result, episode, advisory, hard_alert


def test_a_wording_only_fail_after_auto_fix_publishes_and_emails_once():
    result, episode, advisory, hard_alert = _sunday(COPY)
    assert result["published"] is True
    qa = episode["editorial_qa"]
    assert (qa["passed"], qa["advisory"], qa["alert_sent"]) == (False, True, True)
    assert qa["fix_attempts"] >= 1
    advisory.assert_called_once_with("2026-W99")
    hard_alert.assert_not_called()


@pytest.mark.parametrize("verdict", [
    SAFETY, "STATUS:", "STATUS: FAIL\nISSUES:\n- 'easy'\n",
    "STATUS: FAIL\nISSUES:\n- 'easy'\nSEVERITY: COPY\nSEVERITY: BLOCKING",
    "ISSUES:\n- 'easy'\nSEVERITY: COPY",
], ids=["safety", "truncated", "no-severity", "copy-and-blocking", "no-status"])
def test_a_safety_or_malformed_fail_still_stops_sunday(verdict):
    result, episode, advisory, hard_alert = _sunday(verdict)
    assert isinstance(result, HTTPException) and result.status_code == 400
    assert episode["stages"]["sunday"]["status"] == "failed"
    advisory.assert_not_called()
    hard_alert.assert_called_once()


def test_deterministic_and_unavailable_reports_are_never_copy_only():
    broken = _reviewable_episode()
    broken["stages"]["monday"]["recipe_data"]["description"] = "Golden Ã¢â‚¬â„¢ bites"
    _, deterministic = editorial_qa._editorial_qa_review(broken)
    with patch.object(editorial_qa, "_recent_catalog_titles", return_value=["x"]), \
         patch.object(editorial_qa, "generate_judge_response", side_effect=TimeoutError):
        _, unavailable = editorial_qa._editorial_qa_review(_reviewable_episode())
    assert not editorial_qa.is_copy_only_failure(deterministic)
    assert not editorial_qa.is_copy_only_failure(unavailable)


@given(st.text())
def test_only_a_fail_with_a_copy_severity_line_and_no_blocking_is_copy_only(noise):
    report = f"STATUS: FAIL\nISSUES:\n- {noise}\nSEVERITY: COPY\nRECOMMENDATION: x"
    expected = "BLOCKING" not in report.upper()
    assert editorial_qa.is_copy_only_failure(report) is expected
    assert not editorial_qa.is_copy_only_failure(noise.replace("SEVERITY", "")
                                                 .replace("severity", ""))
