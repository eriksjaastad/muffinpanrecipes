"""Regression tests for the Codex review of fbf70a3 (#7791 / #7793).

1. rejudge must not change the instrument: a different judge system prompt,
   or an incomplete / unswapped A/B record, is refused before any call.
2. A non-finite or negative number anywhere in the cost cap (published
   prices, a response's usage.cost, the cap itself) must block, not admit.
3. The atomic pick writer trashes its temp file instead of deleting it.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import scripts.conversation_lab as cl
from backend.utils import model_router
from tests.test_openrouter_lab import _fake_openrouter
from tests.test_research_prereqs_7791 import (
    _fake_ab_result,
    _fake_pair,
    _mock_openrouter_preflight,
    _no_judge_calls,
)


# ---------------------------------------------------------------------------
# 1. rejudge instrument integrity
# ---------------------------------------------------------------------------

def test_rejudge_refuses_source_judged_under_a_different_prompt_sha(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path)
    data = json.loads(path.read_text())
    data["evaluator_prompt_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="change the instrument"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_orientation_saved_with_a_different_system_prompt(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    pair["judge_orientations"][1]["evidence"]["system_prompt"] = "an older judge prompt"
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="different system prompt"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_when_neither_system_prompt_nor_sha_is_recorded(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    for orientation in pair["judge_orientations"]:
        del orientation["evidence"]["system_prompt"]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    data = json.loads(path.read_text())
    del data["evaluator_prompt_sha256"]
    path.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="neither its judge system prompt"):
        cl.main(["rejudge", str(path)])


@pytest.mark.parametrize("missing", ["first_arm", "second_arm"])
def test_rejudge_refuses_a_missing_arm_record_instead_of_defaulting(tmp_path, monkeypatch, missing):
    # Before the fix a missing first/second_arm defaulted to control/variant,
    # which scores the variant_first orientation with the wrong A/B order.
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    del pair["judge_orientations"][1]["evidence"][missing]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="refusing to guess the A/B order"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_arm_record_that_contradicts_the_mapping(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    pair["judge_orientations"][1]["evidence"]["mapping"] = {"A": "control", "B": "variant", "tie": "tie"}
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="refusing to guess the A/B order"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_two_orientations_with_the_same_order(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    first = pair["judge_orientations"][0]["evidence"]
    second = pair["judge_orientations"][1]["evidence"]
    second.update(first_arm=first["first_arm"], second_arm=first["second_arm"], mapping=dict(first["mapping"]))
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="not a position-swapped pair"):
        cl.main(["rejudge", str(path)])


def test_rejudge_passes_each_saved_arm_order_to_the_judge(tmp_path, monkeypatch):
    """The legitimate path still works and hands the judge the saved order."""
    _mock_openrouter_preflight(monkeypatch)
    seen: list[tuple[str, str]] = []
    real = cl._run_judge_orientation

    def spy(**kwargs):
        seen.append((kwargs["first_arm"], kwargs["second_arm"]))
        return real(**kwargs)

    monkeypatch.setattr(cl, "_run_judge_orientation", spy)
    monkeypatch.setattr(
        model_router, "generate_judge_response",
        lambda **_k: json.dumps({
            "winner": "A", "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS}, "reason": "x",
        }),
    )
    path = _fake_ab_result(tmp_path)
    cl.main(["rejudge", str(path), "--results-dir", str(tmp_path)])
    assert seen == [("control", "variant"), ("variant", "control")]


# ---------------------------------------------------------------------------
# 2. non-finite money values fail closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["NaN", "nan", "inf", "-Infinity", "-0.000001"])
def test_price_fetch_leaves_non_finite_or_negative_prices_unpriced(monkeypatch, bad):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    payload = {"data": [
        {"id": "anthropic/claude-opus-5.5", "pricing": {"prompt": bad, "completion": "0.00002"}},
        {"id": "anthropic/claude-haiku-4.5", "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
    ]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload))
    prices = cl._fetch_openrouter_model_prices()
    assert "anthropic/claude-opus-5.5" not in prices
    assert prices["anthropic/claude-haiku-4.5"] == (0.000001, 0.000005)
    # Unpriced means the per-call reservation is an unconditional block.
    monkeypatch.setattr(cl, "_OPENROUTER_MODEL_PRICES", prices)
    assert cl._openrouter_worst_case_call_cost("anthropic/claude-opus-5.5", 10) == float("inf")


@pytest.mark.parametrize("bad_cost", [float("nan"), float("inf"), -0.01])
def test_non_finite_usage_cost_is_recorded_as_absent_and_blocks_the_next_call(monkeypatch, bad_cost):
    model_router.reset_cost_log()
    _fake_openrouter(monkeypatch, content="a line", cost=bad_cost)
    model = "deepseek/deepseek-v4.1-flash"
    monkeypatch.setattr(cl, "_OPENROUTER_MODEL_PRICES", {model: (0.0000003, 0.0000003)})

    model_router.generate_response(prompt="x", model=f"openrouter/{model}", temperature=0.2)
    [entry] = model_router.get_cost_entries()
    assert entry.get("actual_cost") is None

    budget = cl.CallBudget(max_calls=1000)
    with cl._installed_budget_guard(budget, 100.0):
        with pytest.raises(cl.LabBudgetAbort):
            model_router.generate_response(prompt="x", model=f"openrouter/{model}", temperature=0.2)


def test_guard_blocks_when_the_cap_itself_is_nan(monkeypatch):
    model_router.reset_cost_log()
    _fake_openrouter(monkeypatch, content="a line")
    model = "deepseek/deepseek-v4.1-flash"
    monkeypatch.setattr(cl, "_OPENROUTER_MODEL_PRICES", {model: (0.0000003, 0.0000003)})
    budget = cl.CallBudget(max_calls=1000)
    with cl._installed_budget_guard(budget, float("nan")):
        with pytest.raises(cl.LabBudgetAbort):
            model_router.generate_response(prompt="x", model=f"openrouter/{model}", temperature=0.2)


def test_would_exceed_cost_treats_a_nan_total_or_cap_as_exceeded(monkeypatch):
    monkeypatch.setattr(cl, "_lab_cost_total", lambda: float("nan"))
    assert cl._would_exceed_cost(5.0) is True
    monkeypatch.setattr(cl, "_lab_cost_total", lambda: 1.0)
    assert cl._would_exceed_cost(float("nan")) is True
    assert cl._would_exceed_cost(5.0) is False
    assert cl._would_exceed_cost(1.0) is True  # reached counts as exceeded, unchanged


# ---------------------------------------------------------------------------
# 3. atomic writer trashes, never deletes
# ---------------------------------------------------------------------------

def test_atomic_pick_writer_trashes_temp_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "result.json"
    target.write_text('{"keep": true}')
    trashed: list[str] = []
    monkeypatch.setattr(cl, "send2trash", lambda p: trashed.append(p))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(cl.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        cl._write_pairs_report_atomic(target, {"new": True})

    assert len(trashed) == 1
    assert trashed[0].startswith(str(tmp_path / ".result.json."))
    assert json.loads(target.read_text()) == {"keep": True}


def test_atomic_pick_writer_success_leaves_no_temp_file(tmp_path):
    target = tmp_path / "result.json"
    cl._write_pairs_report_atomic(target, {"picked": 1})
    assert json.loads(target.read_text()) == {"picked": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["result.json"]
