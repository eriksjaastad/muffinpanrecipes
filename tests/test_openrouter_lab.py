"""Mocked tests for the lab-only OpenRouter route (#card-lab-openrouter).

Every test here is network-free: the OpenAI client constructor is replaced,
httpx calls are monkeypatched, and conversation-lab generation/judge calls are
monkeypatched. No real API call happens anywhere in this file.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw
from backend.utils import model_router


# ---------------------------------------------------------------------------
# model_router: openrouter provider
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _funded_account(monkeypatch):
    """Tests never hit the network; default to an account that can pay.
    Individual tests override this to exercise the balance refusal."""
    monkeypatch.setattr(cl, "_openrouter_fetch_account_balance", lambda: 100.0)


def test_parse_openrouter_model_splits_on_first_slash_only():
    routed = model_router.parse_model("openrouter/anthropic/claude-haiku-4.5")
    assert routed.provider == "openrouter"
    assert routed.model == "anthropic/claude-haiku-4.5"


def test_openrouter_allowlist_rejects_unknown(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_ALLOWLIST", raising=False)
    with pytest.raises(RuntimeError, match="not allowlisted"):
        model_router.ensure_openrouter_model_allowed("anthropic/claude-opus-4.6")


def test_openrouter_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        model_router.generate_response(
            prompt="hello",
            model="openrouter/anthropic/claude-haiku-4.5",
            temperature=0.1,
        )


def test_openrouter_request_shape_and_cost_capture(monkeypatch):
    captured: dict = {}
    model_router.reset_cost_log()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(
        model_router,
        "_central_track",
        lambda response, provider, **kwargs: response,
    )

    class FakeCompletions:
        def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            usage = SimpleNamespace(prompt_tokens=12, completion_tokens=3, cost=0.000123)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="routed response"))],
                usage=usage,
                provider="Anthropic",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    text = model_router.generate_response(
        prompt="Say hello.",
        system_prompt="Be brief.",
        model="openrouter/anthropic/claude-haiku-4.5",
        temperature=0.2,
    )

    assert text == "routed response"
    assert captured["client_kwargs"]["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["client_kwargs"]["api_key"] == "test-openrouter-key"
    create_kwargs = captured["create_kwargs"]
    assert create_kwargs["model"] == "anthropic/claude-haiku-4.5"
    assert create_kwargs["temperature"] == 0.2
    assert create_kwargs["max_tokens"] == 4096  # parity with the direct Anthropic path
    assert create_kwargs["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Say hello."},
    ]
    assert create_kwargs["extra_body"] == {
        "provider": model_router.OPENROUTER_PROVIDER_ROUTE,
        "usage": {"include": True},
    }
    assert create_kwargs["extra_body"]["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
    assert create_kwargs["extra_body"]["usage"] == {"include": True}

    [entry] = model_router.get_cost_entries()
    assert entry["provider"] == "openrouter"
    assert entry["model"] == "anthropic/claude-haiku-4.5"
    assert entry["actual_cost"] == 0.000123
    assert entry["served_provider"] == "Anthropic"


def test_openrouter_judge_uses_openrouter_judge_allowlist(monkeypatch):
    model_router.reset_cost_log()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(
        model_router,
        "_central_track",
        lambda response, provider, **kwargs: response,
    )

    class FakeCompletions:
        def create(self, **kwargs):
            usage = SimpleNamespace(prompt_tokens=5, completion_tokens=1, cost=0.0004)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="judged"))],
                usage=usage,
                provider="Anthropic",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    text = model_router.generate_judge_response(
        prompt="judge this",
        model="openrouter/anthropic/claude-opus-4.6",
        temperature=0.3,
    )
    assert text == "judged"
    [entry] = model_router.get_cost_entries()
    assert entry["provider"] == "openrouter"
    assert entry["model"] == "anthropic/claude-opus-4.6"
    assert entry["actual_cost"] == 0.0004

    with pytest.raises(RuntimeError, match="judge allowlist"):
        model_router.generate_judge_response(
            prompt="judge this",
            model="openrouter/anthropic/claude-haiku-4.5",
        )


# ---------------------------------------------------------------------------
# conversation_lab: openrouter provider flag, key check, cost totals
# ---------------------------------------------------------------------------

def _write_variant(tmp_path):
    path = tmp_path / "variant.json"
    path.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT_RULES"}))
    return path


def _messages(tag="openrouter-fixture"):
    return [
        {"character": "Margaret Chen", "message": f"{tag} line {i}"}
        for i in range(2)
    ]


def test_openrouter_default_refuses_when_limit_remaining_is_below_max_cost(tmp_path, monkeypatch):
    """The CLI default is openrouter; a key with too little remaining must
    stop the run before any generation call."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(
        cl,
        "_openrouter_fetch_key",
        lambda: {"limit": 10.0, "limit_remaining": 2.0, "usage": 0.0},
    )

    def fail_generation(**kwargs):
        raise AssertionError("generation must not start under the key cap")

    monkeypatch.setattr(sdw, "run_simulation", fail_generation)
    variant_path = _write_variant(tmp_path)
    results_dir = tmp_path / "results"

    with pytest.raises(SystemExit, match="limit_remaining"):
        cl.main([
            "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(variant_path), "--recipe-context", "anchor",
            "--max-cost", "5.0", "--results-dir", str(results_dir),
        ])


def test_openrouter_dry_run_skips_key_check_and_records_provider_route(tmp_path, monkeypatch):
    """A dry run makes zero calls, so the key check must not run; the result
    still records provider_route and zeroed openrouter cost totals."""
    model_router.reset_cost_log()
    sdw.STOP_CHECK_LOG.clear()
    monkeypatch.setattr(
        cl,
        "_openrouter_fetch_key",
        lambda: (_ for _ in ()).throw(AssertionError("key check must not run under --dry-run")),
    )
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages()})
    variant_path = _write_variant(tmp_path)
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["provider_route"] == model_router.OPENROUTER_PROVIDER_ROUTE
    assert report["cost_by_model"] == {}
    assert report["total_cost_usd"] == 0.0


def test_openrouter_cost_by_model_totals_router_and_jev_costs():
    """#7714 finding 4: Jev cost comes from the router LEDGER
    (record_external_cost), the same source --max-cost reads - not from a
    pair's stop_check_log, which only ever holds the last SUCCESSFUL check
    per tick and would miss a retried/failed attempt that was already
    charged against the cap."""
    model_router.reset_cost_log()
    model_router._record_cost(
        "openrouter", "anthropic/claude-haiku-4.5", 10, 2,
        actual_cost=0.001, served_provider="Anthropic",
    )
    model_router._record_cost(
        "openrouter", "anthropic/claude-opus-4.6", 5, 1,
        actual_cost=0.0004, served_provider="Anthropic",
    )
    # A direct-provider entry (no actual_cost) must be ignored.
    model_router._record_cost("anthropic", "claude-haiku-4-5-20251001", 10, 2)
    model_router.record_external_cost("jev", "typesafe/jev-1.13-20260917", 0.000013272)

    # pairs/partial_pairs are accepted for call-site compatibility only -
    # the function no longer reads them for jev cost.
    totals = cl._openrouter_cost_by_model_for_pairs([{"control_stop_check_log": [], "variant_stop_check_log": []}], [])
    report = cl._openrouter_cost_report(totals)

    assert report["cost_by_model"] == {
        "openrouter/anthropic/claude-haiku-4.5": 0.001,
        "openrouter/anthropic/claude-opus-4.6": 0.0004,
        "jev/typesafe/jev-1.13-20260917": 0.000013272,
    }
    assert report["total_cost_usd"] == pytest.approx(0.001413272)


def test_openrouter_ab_end_to_end_records_costs_and_key_before_after(tmp_path, monkeypatch):
    """A paid openrouter ab run records key snapshots and totals router costs."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    model_router.reset_cost_log()
    key_calls = iter([
        {"limit": 10.0, "limit_remaining": 9.5, "usage": 0.0},
        {"limit": 10.0, "limit_remaining": 9.4, "usage": 0.1},
    ])
    monkeypatch.setattr(cl, "_openrouter_fetch_key", lambda: next(key_calls))

    seen_default_models = []

    def fake_run_simulation(*, default_model, **kwargs):
        sdw.STOP_CHECK_LOG.clear()
        seen_default_models.append(default_model)
        return {"messages": _messages()}

    def fake_judge(*, model, **kwargs):
        assert model == cl.OPENROUTER_JUDGE_MODEL
        model_router._record_cost(
            "openrouter", "anthropic/claude-opus-4.6", 5, 1,
            actual_cost=0.0004, served_provider="Anthropic",
        )
        return json.dumps({
            "winner": "tie",
            "per_dimension": {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "fixture",
        })

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)
    variant_path = _write_variant(tmp_path)
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--no-log", "--results-dir", str(results_dir),
    ])

    assert seen_default_models == [cl.OPENROUTER_DIALOGUE_MODEL, cl.OPENROUTER_DIALOGUE_MODEL]

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["provider_route"] == model_router.OPENROUTER_PROVIDER_ROUTE
    assert report["cost_by_model"] == {"openrouter/anthropic/claude-opus-4.6": 0.0008}
    assert report["total_cost_usd"] == 0.0008
    assert report["openrouter_key"]["before"]["limit_remaining"] == 9.5
    assert report["openrouter_key"]["after"]["limit_remaining"] == 9.4


def test_anthropic_path_does_not_add_openrouter_cost_fields(tmp_path, monkeypatch):
    """--provider anthropic keeps the legacy report shape (budget ledger and all)."""
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("anthropic")})
    monkeypatch.setattr(
        model_router,
        "generate_judge_response",
        lambda **_kwargs: json.dumps({
            "winner": "tie",
            "per_dimension": {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "fixture",
        }),
    )
    variant_path = _write_variant(tmp_path)
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--provider", "anthropic",
        "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--no-log", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["provider_route"] is None
    assert "cost_by_model" not in report
    assert "total_cost_usd" not in report
    assert "openrouter_key" not in report


def test_openrouter_refuses_when_account_balance_is_below_max_cost(tmp_path, monkeypatch):
    """2026-09-27: the key had $49.62 of limit left but the ACCOUNT held $0.56,
    and the first paid run died at its first judge call. The balance check
    must stop the run before any generation."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(
        cl, "_openrouter_fetch_key",
        lambda: {"limit": 50.0, "limit_remaining": 49.62, "usage": 0.38},
    )
    monkeypatch.setattr(cl, "_openrouter_fetch_account_balance", lambda: 0.56)

    def fail_generation(**kwargs):
        raise AssertionError("generation must not start when the account cannot pay")

    monkeypatch.setattr(sdw, "run_simulation", fail_generation)
    variant_path = _write_variant(tmp_path)
    with pytest.raises(SystemExit, match="account balance"):
        cl.main([
            "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(variant_path), "--recipe-context", "anchor",
            "--max-cost", "5.0", "--results-dir", str(tmp_path / "results"),
        ])


def test_account_balance_is_credits_minus_usage(monkeypatch):
    monkeypatch.undo()  # drop the autouse stub for this test only
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    class _Resp:
        status_code = 200
        def json(self):
            return {"data": {"total_credits": 50.0, "total_usage": 49.44}}

    monkeypatch.setattr(cl.httpx, "get", lambda *a, **k: _Resp())
    assert cl._openrouter_fetch_account_balance() == pytest.approx(0.56)


def test_account_balance_rejects_malformed_payload(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    class _Resp:
        status_code = 200
        def json(self):
            return {"data": {"total_credits": None, "total_usage": 1.0}}

    monkeypatch.setattr(cl.httpx, "get", lambda *a, **k: _Resp())
    with pytest.raises(cl.ConversationLabError, match="total_credits"):
        cl._openrouter_fetch_account_balance()


def test_cost_by_model_warns_when_a_call_has_no_cost(monkeypatch, capsys):
    """A call with no usage.cost must not vanish silently from the total."""
    monkeypatch.setattr(model_router, "get_cost_entries", lambda: [
        {"provider": "openrouter", "model": "anthropic/claude-haiku-4.5", "actual_cost": 0.01},
        {"provider": "openrouter", "model": "anthropic/claude-haiku-4.5"},
    ])
    totals = cl._openrouter_router_cost_by_model()
    assert totals == {"openrouter/anthropic/claude-haiku-4.5": 0.01}
    assert "1 call(s) returned no usage.cost" in capsys.readouterr().err
