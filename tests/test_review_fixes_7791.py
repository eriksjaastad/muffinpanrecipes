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

def _with_old_prompt_sha(pair: dict) -> dict:
    """A pair whose saved instrument records an earlier judge prompt."""
    for orientation in pair["judge_orientations"]:
        orientation["evidence"]["judge_instrument"] = {
            **orientation["evidence"]["judge_instrument"], "evaluator_prompt_sha256": "0" * 64,
        }
    return pair


def test_rejudge_refuses_a_source_judged_under_an_earlier_prompt(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path, pairs=[_with_old_prompt_sha(_fake_pair())])
    with pytest.raises(SystemExit, match=r"differ from the source's: evaluator_prompt_sha256\."):
        cl.main(["rejudge", str(path), "--models", "claude-o55"])


def test_allow_instrument_change_runs_a_deliberate_prompt_revision_comparison(tmp_path, monkeypatch, capsys):
    """Codex round 4 P2: the opt-in must reach a changed prompt, not be
    pre-empted by the loader."""
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    path = _fake_ab_result(tmp_path, pairs=[_with_old_prompt_sha(_fake_pair())])
    out = tmp_path / "out"
    cl.main(["rejudge", str(path), "--models", "claude-o55", "--allow-instrument-change", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "instrument_changed"
    assert report["instrument_changed_fields"] == ["evaluator_prompt_sha256"]
    assert "V3 test-retest agreement" not in capsys.readouterr().out


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
    cl.main(["rejudge", str(path), "--models", "claude-o55", "--results-dir", str(tmp_path / "out")])
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


# ---------------------------------------------------------------------------
# Round 2 (Codex FAIL 4e4217e): the whole instrument, sweep input, balances
# ---------------------------------------------------------------------------

def _verdict_json(winner: str = "A") -> str:
    return json.dumps({
        "winner": winner, "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS}, "reason": "x",
    })


def test_rejudge_refuses_a_default_judge_that_differs_from_the_source_judge(tmp_path, monkeypatch):
    # The fixture was scored by Opus 5.5; with no --models the CLI default set
    # judges with Opus 4.6. That is a different instrument, refused before any call.
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match=r"differ from the source's: .*\bmodel\b"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path))])


def test_rejudge_same_instrument_is_reported_as_v3_retest(tmp_path, monkeypatch, capsys):
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    out = tmp_path / "out"
    cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--models", "claude-o55", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "retest"
    assert report["instrument_changed_fields"] == []
    assert report["judge_instrument"] == report["source_judge_instrument"]
    assert "V3 test-retest agreement" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("field", "attr", "value"),
    [
        # Each is a judge setting that is not the model, prompt or temperature.
        ("max_tokens", "_OPENROUTER_ANTHROPIC_MAX_TOKENS", 8192),
        ("extra_body", "openrouter_provider_route", lambda _m: {"order": ["anthropic"], "allow_fallbacks": True}),
        ("empty_stop_max_attempts", "_OPENROUTER_EMPTY_STOP_MAX_ATTEMPTS", 5),
    ],
)
def test_rejudge_refuses_when_any_judge_request_setting_changed(tmp_path, monkeypatch, field, attr, value):
    """Codex round 3: the route, token ceiling and retry bound are part of
    the instrument too. The source fixture recorded today's values; change
    one and the same-model rejudge must refuse, naming that field."""
    path = _fake_ab_result(tmp_path)  # records the instrument BEFORE the change
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    monkeypatch.setattr(model_router, attr, value)
    with pytest.raises(SystemExit, match=rf"differ from the source's: {field}\."):
        cl.main(["rejudge", str(path), "--models", "claude-o55"])


def test_rejudge_refuses_when_the_json_retry_bound_changed(tmp_path, monkeypatch):
    path = _fake_ab_result(tmp_path)
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    monkeypatch.setattr(cl, "_JUDGE_JSON_MAX_RETRIES", 5)
    with pytest.raises(SystemExit, match=r"differ from the source's: json_max_retries\."):
        cl.main(["rejudge", str(path), "--models", "claude-o55"])


def test_allow_instrument_change_runs_and_is_never_labelled_v3(tmp_path, monkeypatch, capsys):
    _mock_openrouter_preflight(monkeypatch)
    models_seen: list[str] = []

    def judge(*, model, **_k):
        models_seen.append(model)
        return _verdict_json()

    monkeypatch.setattr(model_router, "generate_judge_response", judge)
    out = tmp_path / "out"
    cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--allow-instrument-change", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "instrument_changed"
    assert "model" in report["instrument_changed_fields"]
    assert set(models_seen) == {report["judge_model"]} != {report["source_judge_model"]}
    printed = capsys.readouterr().out
    assert "CHANGED-INSTRUMENT" in printed and "V3 test-retest agreement" not in printed


def test_rejudge_of_a_source_without_a_recorded_instrument_needs_the_opt_in(tmp_path, monkeypatch, capsys):
    pair = _fake_pair()
    for orientation in pair["judge_orientations"]:
        del orientation["evidence"]["judge_instrument"]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match="does not record its judge instrument"):
        cl.main(["rejudge", str(path), "--models", "claude-o55"])

    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    out = tmp_path / "out"
    cl.main(["rejudge", str(path), "--models", "claude-o55", "--allow-instrument-change", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "instrument_unverified"
    assert "V3 test-retest agreement" not in capsys.readouterr().out


def test_rejudge_refuses_a_source_with_inconsistent_instruments(tmp_path, monkeypatch):
    pair = _fake_pair()
    pair["judge_orientations"][1]["evidence"]["judge_instrument"] = {
        **pair["judge_orientations"][1]["evidence"]["judge_instrument"], "max_tokens": 1,
    }
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match="different judge instruments"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path, pairs=[pair])), "--models", "claude-o55"])


def test_rejudge_refuses_a_source_scored_by_two_judge_models(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    second = _fake_pair(run_index=2)
    for orientation in second["judge_orientations"]:
        orientation["evidence"]["model"] = "openrouter/anthropic/claude-opus-4.6"
    path = _fake_ab_result(tmp_path, pairs=[_fake_pair(), second])
    with pytest.raises(SystemExit, match="more than one judge model"):
        cl.main(["rejudge", str(path), "--models", "claude-o55"])


def test_rejudge_refuses_a_source_that_does_not_record_its_judge_model(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    del pair["judge_orientations"][1]["evidence"]["model"]
    with pytest.raises(SystemExit, match="which judge model"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path, pairs=[pair])), "--models", "claude-o55"])


def test_new_judge_calls_save_the_full_instrument_in_evidence():
    evidence: dict = {}
    import unittest.mock as um
    with um.patch.object(model_router, "generate_judge_response", return_value=_verdict_json()):
        cl._judge_orientation(
            judge_model="openrouter/anthropic/claude-opus-5.5", concept="c", stage="monday",
            recipe_context=None, expected_cast=[], first_arm="control", first_messages=[],
            second_arm="variant", second_messages=[], evidence=evidence,
        )
    inst = evidence["judge_instrument"]
    assert inst == cl._judge_instrument("openrouter/anthropic/claude-opus-5.5")
    assert inst["model"] == "anthropic/claude-opus-5.5"
    assert inst["extra_body"]["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
    assert inst["max_tokens"] == 4096 and inst["temperature"] == 0.2
    assert inst["request_shape"] == "fixed"
    assert inst["evaluator_prompt_sha256"] and inst["json_max_retries"] == cl._JUDGE_JSON_MAX_RETRIES


def test_preflight_prices_only_the_judge_for_rejudge(tmp_path, monkeypatch):
    """Codex round 3 P2: rejudge never calls the dialogue model, so a missing
    dialogue-model price must not block it; a missing judge price still does."""
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(cl, "_fetch_openrouter_model_prices",
                        lambda: {"anthropic/claude-opus-5.5": (0.000004, 0.00002)})
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    out = tmp_path / "out"
    cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--models", "claude-o55", "--results-dir", str(out)])
    assert list(out.glob("*-rejudge-*.json"))

    monkeypatch.setattr(cl, "_fetch_openrouter_model_prices",
                        lambda: {"anthropic/claude-haiku-4.5": (0.000001, 0.000005)})
    with pytest.raises(SystemExit, match="no published price for anthropic/claude-opus-5.5"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path, filename="b.json")), "--models", "claude-o55"])


def test_preflight_still_prices_both_models_for_ab(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(cl, "_fetch_openrouter_model_prices",
                        lambda: {"anthropic/claude-opus-5.5": (0.000004, 0.00002)})
    args = SimpleNamespace(command="ab", max_cost=5.0, models="claude-o55", provider="openrouter", dry_run=False)
    with pytest.raises(SystemExit, match="anthropic/claude-haiku-4.5"):
        cl._openrouter_preflight(args)


def test_offline_metrics_refuses_a_sweep_result(tmp_path):
    import scripts.lab_offline_metrics as lom
    path = _fake_ab_result(tmp_path, mode="sweep", pairs=[])
    data = json.loads(path.read_text())
    data["variants"] = {"v1": {"pairs": [_fake_pair()]}}
    path.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="sweep"):
        lom.compute([path])


def test_offline_metrics_refuses_a_result_with_no_pairs(tmp_path):
    import scripts.lab_offline_metrics as lom
    with pytest.raises(SystemExit, match="no pairs"):
        lom.compute([_fake_ab_result(tmp_path, pairs=[])])


def _fake_http(monkeypatch, payload):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload))


@pytest.mark.parametrize("field", ["total_credits", "total_usage"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_account_balance_rejects_non_finite_amounts(monkeypatch, field, bad):
    data = {"total_credits": 150.0, "total_usage": 116.0}
    data[field] = bad
    _fake_http(monkeypatch, {"data": data})
    with pytest.raises(cl.ConversationLabError, match=field):
        cl._openrouter_fetch_account_balance()


@pytest.mark.parametrize("field", ["limit", "limit_remaining"])
def test_key_check_rejects_non_finite_amounts(monkeypatch, field):
    data = {"limit": 100.0, "limit_remaining": 32.7, "usage": 67.3}
    data[field] = float("nan")
    _fake_http(monkeypatch, {"data": data})
    with pytest.raises(cl.ConversationLabError, match=field):
        cl._openrouter_fetch_key()


def test_balance_fetch_still_returns_a_normal_balance(monkeypatch):
    _fake_http(monkeypatch, {"data": {"total_credits": 150, "total_usage": 116.36}})
    assert cl._openrouter_fetch_account_balance() == pytest.approx(33.64)


@pytest.mark.parametrize("bad", ["nan", "inf", "0", "-1"])
def test_cli_refuses_an_unenforceable_max_cost(tmp_path, monkeypatch, bad):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match="--max-cost must be a positive finite amount"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--models", "claude-o55", "--max-cost", bad])


# ---------------------------------------------------------------------------
# Round 4 (Codex FAIL 58580c7): the description IS the request
# ---------------------------------------------------------------------------

def test_openrouter_instrument_equals_the_kwargs_actually_sent(monkeypatch):
    """The recorded settings are built by the same function the send path
    uses; prove it against what the SDK actually receives."""
    captured = _fake_openrouter(monkeypatch, content="ok")
    model = "anthropic/claude-opus-5.5"
    model_router.allow_openrouter_models(dialogue="anthropic/claude-haiku-4.5", judge=model)
    model_router.generate_judge_response(prompt="p", system_prompt="s", model=f"openrouter/{model}", temperature=0.2)
    sent = {k: v for k, v in captured["create_kwargs"].items() if k != "messages"}
    settings = model_router.judge_request_settings(f"openrouter/{model}", 0.2)
    assert {k: settings[k] for k in sent} == sent


def test_anthropic_instrument_equals_the_kwargs_actually_sent(monkeypatch):
    captured: dict = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(model_router, "_central_track", lambda response, provider, **kwargs: response)

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text="ok")], usage=None)

    monkeypatch.setattr("anthropic.Anthropic", lambda **_k: SimpleNamespace(messages=FakeMessages()))
    model = next(iter(model_router.JUDGE_ALLOWLIST))
    model_router.generate_judge_response(prompt="p", system_prompt="s", model=f"anthropic/{model}", temperature=0.2)
    sent = {k: v for k, v in captured.items() if k not in {"messages", "system"}}
    settings = model_router.judge_request_settings(f"anthropic/{model}", 0.2)
    assert {k: settings[k] for k in sent} == sent


def test_a_variable_shape_judge_is_never_a_verified_retest(tmp_path, monkeypatch):
    """OpenAI's request can change at run time (endpoint fallback, dropped
    temperature), so an openai judge instrument is unverifiable by design."""
    assert model_router.judge_request_settings("openai/gpt-5.1", 0.2)["request_shape"] == "variable"
    variable = {**cl._judge_instrument("openai/gpt-5.1")}
    mode, _ = cl._rejudge_mode(False, "openai/gpt-5.1", variable)
    assert mode == "instrument_unverified"
    fixed = cl._judge_instrument("openrouter/anthropic/claude-opus-5.5")
    assert cl._rejudge_mode(False, "openrouter/anthropic/claude-opus-5.5", fixed) == ("retest", [])
