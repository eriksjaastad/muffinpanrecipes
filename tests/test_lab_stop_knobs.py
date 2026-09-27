"""Lab knobs: wind-down trigger, stop check, conversation length (#7680/#7681/#7629).

Every test here is network-free: HTTP calls are monkeypatched, model calls
are monkeypatched, and the conversation-lab commands under test run with
--dry-run so no judge is invoked.
"""

from __future__ import annotations

import json

import httpx
import pytest

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw
from backend.utils.stop_check import (
    HAIKU_MODEL,
    StopCheckError,
    StopCheckResult,
    check_scene_done,
)

# ---------------------------------------------------------------------------
# simulate_dialogue_week: phase behaviour for each WINDDOWN_TRIGGER
# ---------------------------------------------------------------------------


def _scripted_run(monkeypatch, *, trigger=None, stop_check=None, stop_checks=None,
                  open_ended=None, ticks=5, line_fn=None):
    """Drive the real run_simulation day loop with only generate_turn replaced.

    Returns (recorded_turn_info, run_simulation_result).
    """
    recorded: list[dict] = []

    def fake_turn(**kwargs):
        recorded.append({
            "tick": kwargs["day_turn"] - 1,
            "phase": kwargs["phase"],
            "is_last_turn": kwargs["is_last_turn"],
        })
        if line_fn is not None:
            return line_fn(kwargs["day_turn"])
        return "Just another line."

    monkeypatch.setattr(sdw, "generate_turn", fake_turn)
    if trigger is not None:
        monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", trigger)
    if stop_check is not None:
        monkeypatch.setattr(sdw, "STOP_CHECK", stop_check)
    if stop_checks is not None:
        monkeypatch.setattr(sdw, "check_scene_done", stop_checks)
    if open_ended is not None:
        monkeypatch.setattr(sdw, "OPEN_ENDED_MAX_TICKS", open_ended)

    result = sdw.run_simulation(
        concept="Jalapeno Corn Dog Bites",
        default_model="stub",
        run_index=0,
        stage_only="monday",
        injected_event=None,
        ticks_per_day=ticks,
        mode="template",
        prompt_style="plain",
        character_models=None,
    )
    return recorded, result


def test_default_attributes_reproduce_regex_phase_sequence(monkeypatch):
    """Defaults must leave today's production behaviour byte-for-byte intact."""
    assert sdw.WINDDOWN_TRIGGER == "regex"
    assert sdw.STOP_CHECK == {
        "provider": "haiku",
        "decided_threshold": 0.7,
        "require_pushback": True,
        "pushback_threshold": 0.5,
    }
    assert sdw.OPEN_ENDED_MAX_TICKS == {}

    # "let's do" lands on tick 2 (day_turn 3) and matches monday's regex.
    recorded, _result = _scripted_run(
        monkeypatch,
        line_fn=lambda day_turn: "Let's do it" if day_turn == 3 else "Hmm",
    )
    assert [r["phase"] for r in recorded] == [
        "active", "active", "active", "winding_down", "closing",
    ]
    assert [r["is_last_turn"] for r in recorded] == [False, False, False, False, True]
    assert sdw.STOP_CHECK_LOG == []


def test_off_trigger_never_sets_goal_met(monkeypatch):
    """With WINDDOWN_TRIGGER='off' the regex never fires, even on 'let's do'."""
    recorded, _result = _scripted_run(
        monkeypatch,
        trigger="off",
        line_fn=lambda day_turn: "Let's do it",
    )
    phases = [r["phase"] for r in recorded]
    # No winding_down before day_ticks - 2 (tick 3 of 5)...
    assert phases[:3] == ["active", "active", "active"]
    # ...and the purely tick-based wind-down still closes the scene.
    assert phases[3:] == ["winding_down", "closing"]
    assert sdw.STOP_CHECK_LOG == []


def test_check_trigger_sets_goal_met_from_mocked_stop_check(monkeypatch):
    """WINDDOWN_TRIGGER='check' uses the stop check verdict, logged per call."""
    calls: list[dict] = []
    verdict = StopCheckResult(
        decided=0.95,
        pushback=0.8,
        provider="jev",
        model="typesafe/jev-1.13-20260917",
        cost_usd=0.000013272,
    )

    def fake_check(lines, provider, *, day, objective):
        calls.append({"day": day, "lines": list(lines), "objective": objective, "provider": provider})
        return verdict

    recorded, _result = _scripted_run(
        monkeypatch,
        trigger="check",
        stop_checks=fake_check,
        line_fn=lambda day_turn: "Line.",
    )
    assert [r["phase"] for r in recorded] == [
        "active", "active", "active", "winding_down", "closing",
    ]
    # First evaluation happens after the tick-2 line, over the 3 lines so far.
    assert len(calls) == 1
    assert calls[0]["day"] == "monday"
    assert calls[0]["provider"] == "haiku"  # the default STOP_CHECK provider
    assert len(calls[0]["lines"]) == 3
    assert "Decide which concept" in calls[0]["objective"]
    assert sdw.STOP_CHECK_LOG == [{
        "day": "monday",
        "tick": 2,
        "decided": 0.95,
        "pushback": 0.8,
        "provider": "jev",
        "model": "typesafe/jev-1.13-20260917",
        "cost_usd": 0.000013272,
    }]


def test_check_thresholds_are_honored(monkeypatch):
    """decided below decided_threshold keeps the scene open."""
    verdict = StopCheckResult(0.8, 0.95, "haiku", HAIKU_MODEL, None)
    monkeypatch.setattr(sdw, "STOP_CHECK", {
        "provider": "haiku",
        "decided_threshold": 0.9,
        "require_pushback": False,
        "pushback_threshold": 0.5,
    })
    recorded, _result = _scripted_run(
        monkeypatch,
        trigger="check",
        stop_checks=lambda **kwargs: verdict,
    )
    assert [r["phase"] for r in recorded] == [
        "active", "active", "active", "winding_down", "closing",
    ]
    # Never done, so the check ran on every tick from 2 onward (2, 3, 4).
    assert [entry["tick"] for entry in sdw.STOP_CHECK_LOG] == [2, 3, 4]


def test_check_require_pushback_is_honored(monkeypatch):
    """Strong decided but weak pushback fails while require_pushback is set."""
    verdict = StopCheckResult(0.95, 0.3, "haiku", HAIKU_MODEL, None)
    monkeypatch.setattr(sdw, "STOP_CHECK", {
        "provider": "haiku",
        "decided_threshold": 0.7,
        "require_pushback": True,
        "pushback_threshold": 0.9,
    })
    recorded, _result = _scripted_run(
        monkeypatch,
        trigger="check",
        stop_checks=lambda **kwargs: verdict,
    )
    assert [entry["tick"] for entry in sdw.STOP_CHECK_LOG] == [2, 3, 4]


def test_check_require_pushback_false_accepts_zero_pushback(monkeypatch):
    """require_pushback=False means decided alone can close the scene."""
    verdict = StopCheckResult(0.95, 0.0, "haiku", HAIKU_MODEL, None)
    monkeypatch.setattr(sdw, "STOP_CHECK", {
        "provider": "haiku",
        "decided_threshold": 0.7,
        "require_pushback": False,
        "pushback_threshold": 0.5,
    })
    _recorded, _result = _scripted_run(
        monkeypatch,
        trigger="check",
        stop_checks=lambda **kwargs: verdict,
    )
    assert [entry["tick"] for entry in sdw.STOP_CHECK_LOG] == [2]


def test_open_ended_ends_one_tick_after_goal_met_with_closing_turn(monkeypatch):
    """The tick after goal_met is the closing turn and the day ends after it."""
    verdict = StopCheckResult(0.95, 0.8, "jev", "typesafe/jev-1.13-20260917", 0.0)
    recorded, result = _scripted_run(
        monkeypatch,
        trigger="check",
        open_ended={"monday": 6},
        stop_checks=lambda **kwargs: verdict,
    )
    assert [r["phase"] for r in recorded] == ["active", "active", "active", "closing"]
    assert [r["is_last_turn"] for r in recorded] == [False, False, False, True]
    assert len(result["messages"]) == 4  # cap was 6, but goal_met ended it early
    assert [entry["tick"] for entry in sdw.STOP_CHECK_LOG] == [2]


def test_open_ended_cap_respected_when_check_never_done(monkeypatch):
    """If the cap is reached first, the last tick closes exactly as today."""
    verdict = StopCheckResult(0.0, 0.0, "haiku", HAIKU_MODEL, None)
    recorded, result = _scripted_run(
        monkeypatch,
        trigger="check",
        open_ended={"monday": 6},
        stop_checks=lambda **kwargs: verdict,
    )
    assert [r["phase"] for r in recorded] == ["active"] * 4 + ["winding_down", "closing"]
    assert len(result["messages"]) == 6


def test_open_ended_requires_check_trigger(monkeypatch):
    monkeypatch.setattr(sdw, "OPEN_ENDED_MAX_TICKS", {"monday": 5})
    monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", "regex")
    with pytest.raises(ValueError, match="OPEN_ENDED_MAX_TICKS requires WINDDOWN_TRIGGER"):
        sdw.run_simulation(
            concept="Jalapeno Corn Dog Bites",
            default_model="stub",
            run_index=0,
            stage_only="monday",
            injected_event=None,
            ticks_per_day=0,
            mode="template",
            prompt_style="plain",
            character_models=None,
        )


# ---------------------------------------------------------------------------
# backend.utils.stop_check
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


_JEV_VERIFIED_RESPONSE = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {
        "decided": {"type": "noul", "noul": 0.95},
        "pushback": {"type": "noul", "noul": 0.17},
    },
    "usage": {"input_tokens": 316, "output_tokens": 38, "cost": 0.000013272},
}


def test_jev_request_shape_and_parse(monkeypatch):
    captured: dict = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(payload=_JEV_VERIFIED_RESPONSE)

    monkeypatch.setattr("backend.utils.stop_check.httpx.post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    result = check_scene_done(
        lines=["Margaret: I like this one.", "Steph: Let's do it."],
        provider="jev",
        day="monday",
        objective="Decide which concept the team is making this week",
    )

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["headers"] == {"Authorization": "Bearer test-openrouter-key"}
    assert captured["json"]["model"] == "typesafe/jev-1.13"
    assert captured["json"]["state"] == "Margaret: I like this one.\nSteph: Let's do it."
    questions = captured["json"]["questions"]
    assert questions["decided"]["type"] == "noul"
    assert "Decide which concept the team is making this week" in questions["decided"]["instructions"]
    assert "disagree or push back" in questions["pushback"]["instructions"]
    assert captured["timeout"] == 30.0

    assert result.decided == 0.95
    assert result.pushback == 0.17
    assert result.provider == "jev"
    assert result.model == "typesafe/jev-1.13-20260917"
    assert result.cost_usd == 0.000013272


@pytest.mark.parametrize(
    "payload",
    [
        {"answers": _JEV_VERIFIED_RESPONSE["answers"]},  # missing model
        {"model": "typesafe/jev-1.13-20260917"},  # missing answers
        {"model": "typesafe/jev-1.13-20260917", "answers": {"decided": {"type": "noul", "noul": 0.9}}},  # missing pushback
        {"model": "typesafe/jev-1.13-20260917", "answers": {"decided": {"type": "noul", "noul": 0.9}, "pushback": {"type": "noul"}}},  # missing noul
    ],
)
def test_jev_missing_keys_raise(monkeypatch, payload):
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: _FakeResponse(payload=payload))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


def test_jev_non_200_raises(monkeypatch):
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: _FakeResponse(payload={}, status_code=500))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="500"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


def test_jev_malformed_json_raises(monkeypatch):
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: _FakeResponse(payload=ValueError("not json")))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="malformed JSON"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


def test_jev_timeout_raises(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("backend.utils.stop_check.httpx.post", timeout)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="timed out"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


def test_haiku_parse(monkeypatch):
    captured: dict = {}

    def fake_generate_response(*, prompt, system_prompt, model, temperature):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        captured["model"] = model
        captured["temperature"] = temperature
        return '{"decided": 0.9, "pushback": 0.4}'

    monkeypatch.setattr("backend.utils.stop_check.generate_response", fake_generate_response)

    result = check_scene_done(
        lines=["Margaret: yes", "Steph: agreed"],
        provider="haiku",
        day="tuesday",
        objective="Nail down the recipe",
    )

    assert result.decided == 0.9
    assert result.pushback == 0.4
    assert result.provider == "haiku"
    assert result.model == HAIKU_MODEL
    assert result.cost_usd is None
    assert captured["model"] == HAIKU_MODEL
    assert "Nail down the recipe" in captured["prompt"]
    assert "STRICT JSON" in captured["prompt"]
    assert "Margaret: yes" in captured["prompt"]


@pytest.mark.parametrize("raw", ["not json at all", '{"decided": 0.9}', '{"decided": 0.9, "pushback": "yes"}'])
def test_haiku_malformed_raises(monkeypatch, raw):
    monkeypatch.setattr("backend.utils.stop_check.generate_response", lambda **kwargs: raw)
    with pytest.raises(StopCheckError):
        check_scene_done(lines=["a"], provider="haiku", day="monday", objective="Pick it")


def test_unknown_provider_raises():
    with pytest.raises(StopCheckError, match="unknown stop check provider"):
        check_scene_done(lines=["a"], provider="oracle", day="monday", objective="Pick it")


# ---------------------------------------------------------------------------
# conversation_lab: validation and result reporting
# ---------------------------------------------------------------------------


def test_validate_ticks_range_accepts_json_lists_and_apply_converts_to_tuples():
    before = sdw.TICKS_RANGE
    cl.validate_variant(sdw, {"TICKS_RANGE": {"monday": [6, 10]}})
    original = cl._apply_variant(sdw, {"TICKS_RANGE": {"monday": [6, 10]}})
    try:
        assert sdw.TICKS_RANGE == {"monday": (6, 10)}
        assert isinstance(sdw.TICKS_RANGE["monday"], tuple)
    finally:
        cl._restore_variant(sdw, original)
    assert sdw.TICKS_RANGE == before


@pytest.mark.parametrize(
    "variant",
    [
        {"WINDDOWN_TRIGGER": "sometimes"},
        {"WINDDOWN_TRIGGER": 1},
        {"STOP_CHECK": {"provider": "openai", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5}},
        {"STOP_CHECK": {"provider": "haiku", "decided_threshold": 1.2, "require_pushback": True, "pushback_threshold": 0.5}},
        {"STOP_CHECK": {"provider": "haiku", "decided_threshold": 0.7, "require_pushback": "yes", "pushback_threshold": 0.5}},
        {"STOP_CHECK": {"provider": "haiku", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5, "extra": 1}},
        {"STOP_CHECK": {"provider": "haiku", "decided_threshold": 0.7, "require_pushback": True}},
        {"TICKS_RANGE": {"monday": [2, 1]}},
        {"TICKS_RANGE": {"monday": [0, 3]}},
        {"TICKS_RANGE": {"monday": [True, 3]}},
        {"TICKS_RANGE": {"monday": 5}},
        {"TICKS_RANGE": {"notaday": [5, 6]}},
        {"OPEN_ENDED_MAX_TICKS": {"monday": 2}},
        {"OPEN_ENDED_MAX_TICKS": {"monday": True}},
        {"OPEN_ENDED_MAX_TICKS": {"monday": "six"}},
        {"OPEN_ENDED_MAX_TICKS": {"notaday": 5}},
    ],
)
def test_lab_rejects_malformed_stop_knob_shapes(variant):
    with pytest.raises(cl.ConversationLabError):
        cl.validate_variant(sdw, variant)


def test_history_depth_invariant_refuses_ticks_range_that_swallows_the_window():
    with pytest.raises(cl.ConversationLabError, match="HISTORY_DEPTH") as exc:
        cl.validate_variant(sdw, {"TICKS_RANGE": {"friday": (6, 20)}})
    assert "friday" in str(exc.value)


def test_history_depth_invariant_refuses_open_ended_cap_that_swallows_the_window():
    with pytest.raises(cl.ConversationLabError, match="HISTORY_DEPTH") as exc:
        cl.validate_variant(sdw, {"OPEN_ENDED_MAX_TICKS": {"monday": 12}})
    assert "monday" in str(exc.value)


def test_history_depth_invariant_passes_when_same_variant_raises_history_depth():
    cl.validate_variant(sdw, {
        "TICKS_RANGE": {"friday": (6, 20)},
        "HISTORY_DEPTH": {"early": (16, 12), "late": (20, 21)},
    })
    cl.validate_variant(sdw, {
        "OPEN_ENDED_MAX_TICKS": {"monday": 12},
        "HISTORY_DEPTH": {"early": (16, 13), "late": (20, 16)},
    })


def test_history_depth_invariant_passes_for_tick_counts_under_the_floor():
    cl.validate_variant(sdw, {"TICKS_RANGE": {"monday": [6, 11]}})
    cl.validate_variant(sdw, {"OPEN_ENDED_MAX_TICKS": {"monday": 11}})


def _fake_messages(tag: str) -> list[dict]:
    return [
        {"day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
         "message": f"{tag} line {i}", "timestamp": "", "model": "test", "attachments": []}
        for i in range(2)
    ]


def test_ab_result_carries_stop_check_logs_and_jev_cost(tmp_path, monkeypatch):
    """After each arm the lab copies STOP_CHECK_LOG into the pair JSON, and the
    report summary totals Jev spend (which never touches the Anthropic ledger)."""
    def fake_run_simulation(*, default_model, **kwargs):
        arm = "variant" if sdw._SHARED_CHARACTER_RULES == "VARIANT_RULES" else "control"
        sdw.STOP_CHECK_LOG[:] = [{
            "day": "monday",
            "tick": 2,
            "decided": 0.9,
            "pushback": 0.8,
            "provider": "jev" if arm == "variant" else "haiku",
            "model": "typesafe/jev-1.13-20260917" if arm == "variant" else HAIKU_MODEL,
            "cost_usd": 0.000013272 if arm == "variant" else None,
        }]
        return {"messages": _fake_messages(arm)}

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)

    variant_path = tmp_path / "variant.json"
    variant_path.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT_RULES"}))
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())

    pair = report["pairs"][0]
    assert pair["control_stop_check_log"][0]["provider"] == "haiku"
    assert pair["variant_stop_check_log"][0]["provider"] == "jev"
    assert pair["variant_stop_check_log"][0]["cost_usd"] == 0.000013272
    assert report["jev_cost_usd"] == 0.000013272
