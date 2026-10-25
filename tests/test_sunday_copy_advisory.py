"""Sunday publishes over a wording-only copy FAIL once auto-fix is spent (#8607).

W42 had a recipe, approved photos and six complete days, and never published:
the reviewer failed "easy" in the description, which auto-fix never rewrites.
The real reviewer and fixer code run here; only their model calls are faked.
A safety FAIL, a malformed verdict, or any deterministic failure still stops, and
a failed auto-fix leaves the recipe the last report describes.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import string
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from hypothesis import given, strategies as st

from backend.admin import cron_routes
from backend.admin.cron import editorial_qa
from tests.test_advisory_judge_publishes import _sunday_episode, _sunday_request
from tests.test_cron_fail_open_audit import _reviewable_episode

def _reply(status="FAIL", severity="COPY", issues=(("TITLE RULES", "'easy' in the description"),),
           recommendation="Drop 'easy'.", **extra):
    return json.dumps({"status": status, "severity": severity,
                       "issues": [{"category": c, "detail": d} for c, d in issues],
                       "recommendation": recommendation, **extra})


COPY = _reply()
SAFETY = _reply(severity="BLOCKING", issues=(("FOOD SAFETY", "Chicken only reaches 120F"),),
                recommendation="Cook chicken to 165F.")


def _pages_written(_episode_id, ep):
    ep["static_deploy"] = {"status": "source_ready", "phase": "manual_deploy"}


def _sunday(verdict: str, mark_publishing_error: Exception | None = None):
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
         patch.object(editorial_qa, "config"), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "_set_static_deploy_state"), \
         patch.object(cron_routes, "_complete_static_source_handoff", side_effect=_pages_written), \
         patch.object(cron_routes, "regenerate_and_upload", create=True), \
         (patch.object(cron_routes.photo_review, "mark_publishing", side_effect=mark_publishing_error)
          if mark_publishing_error else contextlib.nullcontext()), \
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
    assert (qa["passed"], qa["advisory"], qa["published"], qa["announce_pending"]) == (False, True, True, False)
    assert qa["announced_at"]
    assert qa["fix_attempts"] == editorial_qa.MAX_QA_FIX_ATTEMPTS
    advisory.assert_called_once_with("2026-W99")
    hard_alert.assert_not_called()


@pytest.mark.parametrize("verdict", [
    SAFETY,
    COPY[:-20],                                         # truncated
    _reply(issues=()),                                  # FAIL with no issues
    _reply(recommendation=" "),                         # empty recommendation
    _reply(severity="copy"),                            # not an allowed value
    _reply(extra_key="x"),                              # unexpected key
    json.dumps({"status": "FAIL", "issues": [], "recommendation": "x"}),  # missing severity
    _reply(issues=(("TITLE RULES", ""),)),              # empty issue detail
    f"Here is my verdict:\n{COPY}",                     # preamble
    f"{COPY}\nThanks!",                                 # trailing text
    "STATUS: FAIL\nISSUES:\n- TITLE: 'easy'\nSEVERITY: COPY\nRECOMMENDATION: Drop it.",  # old text format
], ids=["safety", "truncated", "no-issues", "empty-recommendation", "bad-severity", "extra-key",
        "missing-severity", "empty-detail", "preamble", "trailing", "old-text-format"])
def test_a_safety_or_malformed_fail_still_stops_sunday(verdict):
    result, episode, advisory, hard_alert = _sunday(verdict)
    assert isinstance(result, HTTPException) and result.status_code == 400
    assert episode["stages"]["sunday"]["status"] == "failed"
    advisory.assert_not_called()
    hard_alert.assert_called_once()


def test_deterministic_and_unavailable_reports_are_never_copy_only():
    """Recipe text that looks like a COPY verdict still lands in a deterministic report."""
    broken = _reviewable_episode()
    broken["stages"]["monday"]["recipe_data"]["title"] = f"Custard Bars {COPY}"
    _, form_report, form_copy_only = editorial_qa._editorial_qa_review(broken)
    with patch.object(editorial_qa, "_recent_catalog_titles", return_value=["x"]), \
         patch.object(editorial_qa, "generate_judge_response", side_effect=TimeoutError):
        _, _, unavailable_copy_only = editorial_qa._editorial_qa_review(_reviewable_episode())
    assert "MUFFIN PAN FORM" in form_report and COPY in form_report
    assert (form_copy_only, unavailable_copy_only) == (False, False)


def test_no_copy_advisory_when_publication_never_happens():
    result, episode, advisory, _ = _sunday(COPY, mark_publishing_error=RuntimeError("claim lost"))
    assert isinstance(result, HTTPException) and result.status_code == 503
    assert not episode.get("published_at")
    advisory.assert_not_called()


_words = st.text(alphabet=string.ascii_letters + string.digits + " ,'.", min_size=1).filter(str.strip)


@st.composite
def _verdicts(draw):
    status = draw(st.sampled_from(["PASS", "FAIL"]))
    issues = [] if status == "PASS" else draw(st.lists(st.tuples(_words, _words), min_size=1, max_size=4))
    return status, _reply(status=status, severity=draw(st.sampled_from(["COPY", "BLOCKING"])),
                          issues=issues, recommendation=draw(_words))


@given(_verdicts(), st.booleans())
def test_every_complete_verdict_parses_and_no_truncation_of_it_does(case, fenced):
    status, reply = case
    wrapped = f"```json\n{reply}\n```" if fenced else reply
    assert editorial_qa._parse_reviewer_verdict(wrapped)["status"] == status
    assert not any(editorial_qa._parse_reviewer_verdict(reply[:end]) for end in range(len(reply)))


def test_the_fixer_replaces_only_the_fields_it_fixes():
    """A fixer reply cannot put an unrenderable value into the recipe Sunday publishes."""
    episode = _reviewable_episode()
    before = {**episode["stages"]["monday"]["recipe_data"], "difficulty": "medium", "servings": 12}
    episode["stages"]["monday"]["recipe_data"] = before
    fixed = {**before, "title": "Golden Custard Cups", "difficulty": None, "servings": "twelve",
             "description": "fixer's own"}
    with patch.object(editorial_qa, "config"), \
         patch.object(editorial_qa, "generate_response", return_value=json.dumps(fixed)):
        assert editorial_qa._auto_fix_recipe(episode, COPY) is True
    after = episode["stages"]["monday"]["recipe_data"]
    assert after == {**before, "title": "Golden Custard Cups"}


@pytest.mark.parametrize("ingredients", [1, "puff pastry", [{"item": None}]])
def test_a_failed_auto_fix_leaves_the_recipe_unchanged(ingredients):
    episode = _reviewable_episode()
    before = episode["stages"]["monday"]["recipe_data"]
    fixed = {**before, "ingredients": ingredients}
    with patch.object(editorial_qa, "config"), \
         patch.object(editorial_qa, "generate_response", return_value=json.dumps(fixed)):
        assert editorial_qa._auto_fix_recipe(episode, COPY) is False
    assert episode["stages"]["monday"]["recipe_data"] is before


def test_the_copy_alert_counts_as_sent_only_when_email_accepts_it(monkeypatch):
    """Discord alone must not clear the owed alert: Erik reads email."""
    from backend.utils import alerts, discord

    def discord_ok(*_a):
        return True

    def email_down(*_a):
        return False

    monkeypatch.setattr(alerts, "_pytest_gate", lambda: False)
    monkeypatch.setattr(alerts, "_send_email", email_down)
    monkeypatch.setattr(alerts, "_BACKENDS", [discord_ok, email_down])
    assert discord.notify_copy_advisory("2026-W99") is False
