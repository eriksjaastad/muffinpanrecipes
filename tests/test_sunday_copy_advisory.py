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
from unittest.mock import DEFAULT, AsyncMock, MagicMock, patch

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


# The live catalog holds this week's own entry (a re-fire), which never counts as a collision.
OWN_ENTRY = {"recipes": [{"title": "Portuguese Custard Tart Cups", "episode_id": "2026-W99"}]}


def _raise_or(value):
    if isinstance(value, Exception):
        raise value
    return value


def _sunday(verdict: str, mark_publishing_error: Exception | None = None, catalog=OWN_ENTRY):
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
         patch.multiple(editorial_qa, config=DEFAULT,
                        _recent_catalog_titles=MagicMock(return_value=["Something Else"]),
                        load_published_catalog=MagicMock(side_effect=lambda: _raise_or(catalog)),
                        generate_judge_response=MagicMock(return_value=verdict),
                        generate_response=MagicMock(return_value=json.dumps(recipe))), \
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
    COPY.replace('"severity": "COPY"', '"severity": "BLOCKING", "severity": "COPY"'),  # repeated key
    COPY.replace('"category": "TITLE RULES"', '"category": "x", "category": "TITLE RULES"'),
    _reply(issues=(("FOOD SAFETY (BLOCKING)", "Chicken only reaches 120F"),)),  # COPY naming BLOCKING
    _reply(recommendation="Blocking: cook chicken to 165F."),
], ids=["safety", "truncated", "no-issues", "empty-recommendation", "bad-severity", "extra-key",
        "missing-severity", "empty-detail", "preamble", "trailing", "old-text-format",
        "repeated-severity", "repeated-issue-key", "copy-names-blocking-issue",
        "copy-names-blocking-recommendation"])
def test_a_safety_or_malformed_fail_still_stops_sunday(verdict):
    result, episode, advisory, hard_alert = _sunday(verdict)
    assert isinstance(result, HTTPException) and result.status_code == 400
    assert episode["stages"]["sunday"]["status"] == "failed"
    advisory.assert_not_called()
    hard_alert.assert_called_once()


@pytest.mark.parametrize("catalog", [
    {"recipes": [{"title": "Portuguese Custard Tart Cups", "episode_id": "2026-W40"}]},
    {"recipes": [{"title": "Portuguese Custard Tart Cups"}]},  # a legacy entry with no episode_id
    editorial_qa.CatalogUnavailableError("blob down"),
], ids=["published-elsewhere", "legacy-entry", "catalog-unreadable"])
def test_a_copy_fail_never_publishes_a_title_the_catalog_already_has(catalog):
    result, episode, advisory, hard_alert = _sunday(COPY, catalog=catalog)
    assert isinstance(result, HTTPException) and result.status_code == 400
    assert "TITLE REPETITION (blocks publishing over the copy FAIL)" in episode["editorial_qa"]["report"]
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


@given(_verdicts(), st.sampled_from(sorted(editorial_qa._VERDICT_KEYS)), st.sampled_from(["COPY", "PASS", []]))
def test_no_verdict_with_a_repeated_key_parses(case, key, value):
    """json.loads keeps the last of two equal keys; the parser must refuse instead."""
    _, reply = case
    assert editorial_qa._parse_reviewer_verdict(f'{{"{key}": {json.dumps(value)}, {reply[1:]}') is None


def test_the_fixer_replaces_only_the_fields_it_fixes():
    """A fixer reply cannot put an unrenderable value into the recipe Sunday publishes,
    and its corrected times and servings are kept."""
    episode = _reviewable_episode()
    before = {**episode["stages"]["monday"]["recipe_data"], "difficulty": "medium",
              "servings": 12, "prep_time": 15, "cook_time": 20}
    episode["stages"]["monday"]["recipe_data"] = before
    fixed = {**before, "title": "Golden Custard Cups", "difficulty": None, "servings": 10,
             "cook_time": 0, "description": "fixer's own"}  # a no-bake correction
    del fixed["prep_time"]
    with patch.object(editorial_qa, "config"), \
         patch.object(editorial_qa, "generate_response", return_value=json.dumps(fixed)):
        assert editorial_qa._auto_fix_recipe(episode, COPY) is True
    after = episode["stages"]["monday"]["recipe_data"]
    assert after == {**before, "title": "Golden Custard Cups", "servings": 10, "cook_time": 0}


@pytest.mark.parametrize("change, keeps_calories", [
    ({"title": "Golden Custard Cups"}, True),
    ({"servings": 6}, False),
    ({"ingredients": [{"item": "eggs", "amount": "4", "notes": ""}]}, False),
])
def test_a_fix_to_ingredients_or_servings_drops_the_stale_calorie_figure(change, keeps_calories):
    episode = _reviewable_episode()
    before = {**episode["stages"]["monday"]["recipe_data"], "servings": 12, "calories": 240}
    episode["stages"]["monday"]["recipe_data"] = before
    with patch.object(editorial_qa, "config"), \
         patch.object(editorial_qa, "generate_response", return_value=json.dumps({**before, **change})):
        assert editorial_qa._auto_fix_recipe(episode, COPY) is True
    assert ("calories" in episode["stages"]["monday"]["recipe_data"]) is keeps_calories


@pytest.mark.parametrize("change", [
    {"ingredients": 1}, {"ingredients": "puff pastry"}, {"ingredients": [{"item": None}]},
    {"servings": "twelve"}, {"servings": 0}, {"cook_time": 35.5}, {"cook_time": True},
    {"prep_time": None}, {"prep_time": 10**400}, {"cook_time": -5}, {"cook_time": float("inf")}, {"prep_time": 1441},
])
def test_a_failed_auto_fix_leaves_the_recipe_unchanged(change):
    episode = _reviewable_episode()
    before = episode["stages"]["monday"]["recipe_data"]
    fixed = {**before, **change}
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
