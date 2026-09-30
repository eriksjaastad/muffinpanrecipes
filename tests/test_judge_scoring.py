"""The scored dialogue judge (#6861).

The judge used to be a boolean: "Respond with EXACTLY one line: PASS or
FAIL". These tests lock in the structured-JSON replacement — parsing
(happy path, tolerant slice, unparseable-fails-closed), score persistence
on the episode, the expected-cast roster reaching the prompt so
cast_coverage is judgeable, and the weakest dimensions reaching the final
FAIL alert.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backend.admin import cron_routes


def _dialogue() -> list[dict]:
    return [
        {"character": "Margaret Chen", "message": "These hold together fine."},
        {"character": "Marcus Reid", "message": "There's a story in that."},
    ]


def _episode() -> dict:
    return {"episode_id": "2026-W99", "stages": {}}


# ---------------------------------------------------------------------------
# _parse_judge_json
# ---------------------------------------------------------------------------

def test_parse_judge_json_happy_path():
    raw = json.dumps({
        "scores": {"title_fidelity": 4, "turn_taking": 3},
        "verdict": "PASS",
        "weakest": ["turn_taking"],
        "reason": "Solid but a bit monologue-heavy.",
    })
    parsed = cron_routes._parse_judge_json(raw)
    assert parsed["verdict"] == "PASS"
    assert parsed["scores"]["title_fidelity"] == 4


def test_parse_judge_json_tolerant_slice():
    """A markdown fence or a sentence of preamble around the JSON must not
    break parsing — slice from the first '{' to the last '}'."""
    raw = (
        "Sure, here is my verdict:\n```json\n"
        '{"scores": {"cast_coverage": 2}, "verdict": "FAIL", '
        '"weakest": ["cast_coverage"], "reason": "Julian never showed up."}'
        "\n```\nLet me know if you need anything else."
    )
    parsed = cron_routes._parse_judge_json(raw)
    assert parsed is not None
    assert parsed["verdict"] == "FAIL"
    assert parsed["scores"]["cast_coverage"] == 2


def test_parse_judge_json_unparseable_returns_none():
    assert cron_routes._parse_judge_json("PASS - looks good, no notes.") is None
    assert cron_routes._parse_judge_json("") is None
    assert cron_routes._parse_judge_json("{not: valid json,}") is None


# ---------------------------------------------------------------------------
# _judge_dialogue — parsing + persistence
# ---------------------------------------------------------------------------

def test_judge_dialogue_parses_scores_and_records_on_episode():
    valid = json.dumps({
        "scores": {
            "title_fidelity": 5, "arc_resolution": 4, "voice_distinctiveness": 4,
            "technical_credibility": 5, "natural_progression": 3, "promise_delivery": 4,
            "turn_taking": 3, "cast_coverage": 5,
        },
        "verdict": "PASS",
        "weakest": ["natural_progression", "turn_taking"],
        "reason": "Anchored and coherent, a little repetitive.",
    })
    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", return_value=valid):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "monday", _dialogue(), episode,
        )

    assert passed is True
    assert "Anchored and coherent" in verdict
    assert episode["judge_scores"]["monday"]["turn_taking"] == 3
    assert episode["judge_weakest"]["monday"] == ["natural_progression", "turn_taking"]
    assert episode["judge_reason"]["monday"] == "Anchored and coherent, a little repetitive."


def test_judge_dialogue_unparseable_fails_closed():
    """Fail closed (#6861): never default to PASS when the judge's own
    output can't be parsed. This is the publish gate."""
    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", return_value="not json at all"):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "tuesday", _dialogue(), episode,
        )

    assert passed is False
    assert "judge output unparseable" in verdict
    assert episode["judge_reason"]["tuesday"] == "judge output unparseable"
    assert episode["judge_scores"]["tuesday"] == {}


def test_judge_dialogue_retries_once_on_unparseable_then_succeeds():
    valid = json.dumps({
        "scores": {"cast_coverage": 5},
        "verdict": "PASS",
        "weakest": [],
        "reason": "Fine.",
    })
    calls = {"n": 0}

    def fake_generate(prompt, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return "sorry, I can't format that as JSON"
        assert "CRITICAL" in prompt  # the stricter retry reminder
        return valid

    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", side_effect=fake_generate):
        passed, _verdict = cron_routes._judge_dialogue(
            "Concept", "wednesday", _dialogue(), episode,
        )

    assert calls["n"] == 2
    assert passed is True


# ---------------------------------------------------------------------------
# Expected cast reaches the prompt (#6832 cast_coverage is judgeable)
# ---------------------------------------------------------------------------

def test_expected_roster_included_in_judge_prompt():
    from scripts.simulate_dialogue_week import participants_for_day

    captured: dict[str, str] = {}

    def fake_generate(prompt, **_kwargs):
        captured["prompt"] = prompt
        return json.dumps({"scores": {}, "verdict": "PASS", "weakest": [], "reason": "ok"})

    with patch.object(cron_routes, "generate_judge_response", side_effect=fake_generate):
        cron_routes._judge_dialogue("Concept", "monday", _dialogue(), _episode())

    assert "Expected cast for today (monday)" in captured["prompt"]
    for name in participants_for_day("monday"):
        assert name in captured["prompt"]
    assert "Julian Torres" in captured["prompt"]  # #6832 — must be present now


# ---------------------------------------------------------------------------
# Weakest dimensions reach the final-FAIL alert (#6861)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Malformed verdict (#7405) — JSON parses fine but never actually rendered a
# verdict. A missing/empty/unrecognized `verdict` must be treated exactly
# like unparseable output, never silently read as a scored FAIL via the
# `== "PASS"` comparison.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("parsed", [
    {"scores": {"title_fidelity": 5}},               # missing verdict key
    {"scores": {"title_fidelity": 5}, "verdict": ""},  # empty string
    {"scores": {"title_fidelity": 5}, "verdict": "MAYBE"},  # unrecognized value
    {"scores": {"title_fidelity": 5}, "verdict": None},  # explicit null
])
def test_judge_verdict_is_malformed_true_cases(parsed):
    assert cron_routes._judge_verdict_is_malformed(parsed) is True


@pytest.mark.parametrize("verdict_value", ["PASS", "pass", "FAIL", " Fail "])
def test_judge_verdict_is_malformed_false_for_recognized_verdict(verdict_value):
    assert cron_routes._judge_verdict_is_malformed({"verdict": verdict_value}) is False


def test_judge_dialogue_missing_verdict_key_is_malformed_not_scored_fail():
    """#7405 — the exact defect: a parseable response with scores but no
    `verdict` key must not become a silent scored FAIL."""
    malformed = json.dumps({"scores": {"title_fidelity": 5}})
    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", return_value=malformed):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "tuesday", _dialogue(), episode,
        )

    assert passed is False
    assert verdict == "FAIL - judge verdict malformed"
    assert episode["judge_reason"]["tuesday"] == "judge verdict malformed"
    # Never carries the raw (misleading) scores through — empty, exactly
    # like the unparseable path, so nothing downstream can rank or publish
    # this attempt as though the judge actually scored it.
    assert episode["judge_scores"]["tuesday"] == {}
    assert episode["judge_weakest"]["tuesday"] == []


def test_judge_dialogue_empty_verdict_string_is_malformed():
    raw = json.dumps({"scores": {"turn_taking": 3}, "verdict": ""})
    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", return_value=raw):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "wednesday", _dialogue(), episode,
        )
    assert passed is False
    assert verdict == "FAIL - judge verdict malformed"


def test_judge_dialogue_unexpected_verdict_value_is_malformed():
    raw = json.dumps({"scores": {"turn_taking": 3}, "verdict": "MAYBE"})
    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", return_value=raw):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "thursday", _dialogue(), episode,
        )
    assert passed is False
    assert verdict == "FAIL - judge verdict malformed"


def test_judge_dialogue_non_dict_json_stays_on_the_unparseable_path():
    """A JSON array (or other non-dict) is not a malformed verdict object —
    `_parse_judge_json` already returns None for it, so it takes the
    pre-existing unparseable path, not the new malformed one."""
    raw = json.dumps(["PASS"])
    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", return_value=raw):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "friday", _dialogue(), episode,
        )
    assert passed is False
    assert "judge output unparseable" in verdict
    assert episode["judge_reason"]["friday"] == "judge output unparseable"


def test_judge_dialogue_retries_once_on_malformed_verdict_then_succeeds():
    valid = json.dumps({
        "scores": {"cast_coverage": 5}, "verdict": "PASS", "weakest": [], "reason": "Fine.",
    })
    calls = {"n": 0}

    def fake_generate(prompt, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"scores": {"cast_coverage": 1}})  # no verdict key
        assert "CRITICAL" in prompt  # the stricter retry reminder
        return valid

    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", side_effect=fake_generate):
        passed, _verdict = cron_routes._judge_dialogue(
            "Concept", "saturday", _dialogue(), episode,
        )

    assert calls["n"] == 2
    assert passed is True


def test_judge_dialogue_malformed_verdict_after_retry_fails_closed_with_empty_meta():
    """Still malformed after the retry: fail closed exactly like unparseable
    output — empty meta, never a scored FAIL (#7405)."""
    still_malformed = json.dumps({"scores": {"cast_coverage": 1}})  # no verdict, both calls

    episode = _episode()
    with patch.object(cron_routes, "generate_judge_response", return_value=still_malformed):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "sunday", _dialogue(), episode,
        )

    assert passed is False
    assert verdict == "FAIL - judge verdict malformed"
    assert episode["judge_scores"]["sunday"] == {}
    assert episode["judge_weakest"]["sunday"] == []
    assert episode["judge_reason"]["sunday"] == "judge verdict malformed"


# ---------------------------------------------------------------------------
# Route-level (#7405): the shared judge path used by every cron day, with the
# real _judge_dialogue exercised (not mocked out) — a malformed verdict must
# be retried and then handled exactly like an unparseable/outage judge, and
# must never be recorded as a scored FAIL that can gate a day closed or be
# selected for advisory publication.
# ---------------------------------------------------------------------------

def test_generate_and_judge_raises_when_every_attempt_is_malformed_not_scored_fail():
    dialogue = _dialogue()
    malformed = json.dumps({"scores": {"cast_coverage": 1}})  # no verdict key, every call

    episode = {"episode_id": "2026-W99", "stages": {}}
    with patch.object(cron_routes, "_generate_dialogue", return_value=dialogue), \
         patch.object(cron_routes, "generate_judge_response", return_value=malformed), \
         patch.object(cron_routes, "notify_judge_failure") as notify:
        with pytest.raises(cron_routes.JudgeFailedError):
            cron_routes._generate_and_judge_dialogue(
                "tuesday", "Concept", episode, max_retries=1,
            )

    notify.assert_called_once()
    # Never recorded as a real scored FAIL — the episode's judge_scores for
    # this stage stays empty, exactly like the unparseable-output path.
    assert episode["judge_scores"]["tuesday"] == {}
    assert episode["judge_reason"]["tuesday"] == "judge verdict malformed"


def test_advisory_gate_fails_closed_when_every_attempt_is_malformed():
    """The advisory gate (Sunday, #7394) ranks attempts by `scores` and only
    publishes one the judge actually scored. A malformed attempt must carry
    empty scores exactly like unparseable output, so it can never be
    selected and published below the bar — it must fail closed instead."""
    dialogue = _dialogue()
    malformed = json.dumps({"scores": {"cast_coverage": 1}})  # no verdict key, every call

    episode = {"episode_id": "2026-W99", "stages": {}, "events": []}
    with patch.object(cron_routes, "_generate_dialogue", return_value=dialogue), \
         patch.object(cron_routes, "generate_judge_response", return_value=malformed), \
         patch.object(cron_routes, "notify_judge_failure") as notify:
        with pytest.raises(cron_routes.JudgeFailedError):
            cron_routes._generate_and_judge_dialogue(
                "sunday", "Concept", episode, max_retries=1, advisory=True,
            )

    notify.assert_called_once()
    assert "judge_advisory" not in episode  # nothing was eligible to publish


def test_final_fail_verdict_carries_weakest_dims_into_alert():
    """notify_judge_failure's signature is fixed (owned elsewhere); the
    weakest dims and scores must reach it through the existing `verdict`
    argument, not a new parameter."""
    fail_json = json.dumps({
        "scores": {"cast_coverage": 1, "turn_taking": 2, "title_fidelity": 5},
        "verdict": "FAIL",
        "weakest": ["cast_coverage", "turn_taking"],
        "reason": "Julian and Devon never appeared in a shoot-planning day.",
    })
    episode = {"episode_id": "2026-W99", "stages": {}}

    with patch.object(cron_routes, "_generate_dialogue", return_value=_dialogue()), \
         patch.object(cron_routes, "generate_judge_response", return_value=fail_json), \
         patch.object(cron_routes, "notify_judge_failure") as notify:
        try:
            cron_routes._generate_and_judge_dialogue(
                "monday", "Concept", episode, max_retries=0,
            )
        except cron_routes.JudgeFailedError:
            pass

    notify.assert_called_once()
    alert_verdict = notify.call_args.kwargs["verdict"]
    assert "cast_coverage" in alert_verdict
    assert "turn_taking" in alert_verdict
