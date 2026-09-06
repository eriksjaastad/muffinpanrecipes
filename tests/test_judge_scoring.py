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
