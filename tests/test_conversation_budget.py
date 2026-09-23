"""Offline tests for the tool-only conversation-lab budget guard."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest
from anthropic.resources.messages import Messages

from backend.utils import model_router
from scripts.conversation_budget import (
    AnthropicBudgetGuard,
    BudgetExceeded,
    BudgetGuardError,
    BudgetLedgerError,
)


@pytest.fixture
def fake_sdk(monkeypatch):
    calls = {
        "count": [],
        "create": [],
        "retries": [],
        "usage": {
            "input_tokens": 80,
            "output_tokens": 4,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
            "service_tier": "standard",
            "inference_geo": None,
        },
        "create_error": None,
        "count_error": None,
    }

    def count_tokens(self, **kwargs):
        calls["count"].append(kwargs)
        if calls["count_error"] is not None:
            raise calls["count_error"]
        return SimpleNamespace(input_tokens=100)

    def create(self, **kwargs):
        calls["create"].append(kwargs)
        if calls["create_error"] is not None:
            raise calls["create_error"]
        usage = calls["usage"]
        return SimpleNamespace(
            usage=SimpleNamespace(model_dump=lambda exclude_none=True: usage),
            content=[SimpleNamespace(text="fixture response")],
        )

    def make_client(*, api_key=None, max_retries=2, **kwargs):
        calls["retries"].append(max_retries)
        return SimpleNamespace(messages=Messages.__new__(Messages))

    monkeypatch.setattr(Messages, "count_tokens", count_tokens)
    monkeypatch.setattr(Messages, "create", create)
    monkeypatch.setattr(anthropic, "Anthropic", make_client)
    monkeypatch.setattr(model_router, "_central_track", lambda *args, **kwargs: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    return calls


def _generate(phase="calibration"):
    return model_router.generate_response(
        "A short test prompt.",
        system_prompt="A test system message.",
        model="anthropic/claude-haiku-4-5-20251001",
        temperature=0.3,
    )


def _ledger(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_guard_reserves_then_settles_and_disables_sdk_retries(tmp_path, fake_sdk):
    ledger_path = tmp_path / "combined-ledger.json"
    with AnthropicBudgetGuard(ledger_path, create=True) as guard:
        with guard.phase("bench"):
            assert _generate() == "fixture response"

    ledger = _ledger(ledger_path)
    assert fake_sdk["retries"] == [0]
    assert len(fake_sdk["count"]) == 1
    assert len(fake_sdk["create"]) == 1
    assert ledger["totals"]["count_token_requests"] == 1
    assert ledger["totals"]["generation_attempts"] == 1
    assert ledger["totals"]["reserved_microusd"] == 0
    assert ledger["totals"]["actual_microusd"] == 100
    assert ledger["phases"]["bench"]["actual_microusd"] == 100
    assert "A short test prompt." not in ledger_path.read_text(encoding="utf-8")
    assert fake_sdk["count"][0] == {
        "model": "claude-haiku-4-5-20251001",
        "messages": [{"role": "user", "content": "A short test prompt."}],
        "system": "A test system message.",
    }
    assert fake_sdk["create"][0]["service_tier"] == "standard_only"
    assert fake_sdk["create"][0]["max_tokens"] == 4096


def test_opus_global_usage_uses_the_correct_rates(tmp_path, fake_sdk):
    fake_sdk["usage"] = {
        "input_tokens": 8,
        "output_tokens": 2,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
        "service_tier": "standard",
        "inference_geo": "global",
    }
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        client = anthropic.Anthropic(api_key="fixture")
        client.messages.create(
            model="claude-opus-4-6",
            max_tokens=32,
            temperature=0.3,
            messages=[{"role": "user", "content": "prompt"}],
        )
    assert _ledger(path)["totals"]["actual_microusd"] == 90
    assert fake_sdk["create"][0]["inference_geo"] == "global"


def test_one_ledger_carries_spend_between_all_three_phases(tmp_path, fake_sdk):
    ledger_path = tmp_path / "ledger.json"
    for index, phase in enumerate(("calibration", "bench", "ab")):
        with AnthropicBudgetGuard(ledger_path, create=index == 0) as guard:
            with guard.phase(phase):
                _generate()

    final = _ledger(ledger_path)
    assert final["totals"]["generation_attempts"] == 3
    assert final["totals"]["actual_microusd"] == 300
    assert [final["phases"][name]["generation_attempts"] for name in ("calibration", "bench", "ab")] == [1, 1, 1]


def test_denial_happens_before_messages_create_and_latches(tmp_path, fake_sdk):
    path = tmp_path / "tiny-ledger.json"
    with AnthropicBudgetGuard(path, budget_usd="0.000001", create=True) as guard:
        with pytest.raises(BudgetExceeded):
            _generate()
        with pytest.raises(BudgetGuardError):
            guard.raise_if_stopped()

    ledger = _ledger(path)
    assert fake_sdk["count"]
    assert fake_sdk["create"] == []
    assert ledger["status"] == "stopped"
    assert ledger["stop_reason"] == "budget_exhausted"
    assert ledger["totals"]["generation_attempts"] == 0
    with pytest.raises(BudgetGuardError, match="stopped"):
        AnthropicBudgetGuard(path, budget_usd="0.000001").__enter__()


def test_create_refuses_existing_file_and_resume_never_resets_missing_or_corrupt_ledger(tmp_path):
    path = tmp_path / "ledger.json"
    with pytest.raises(BudgetLedgerError, match="missing"):
        AnthropicBudgetGuard(path).__enter__()
    with AnthropicBudgetGuard(path, create=True):
        pass
    original = path.read_bytes()
    with pytest.raises(BudgetLedgerError, match="existing"):
        AnthropicBudgetGuard(path, create=True).__enter__()
    assert path.read_bytes() == original
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(BudgetLedgerError, match="cannot be read"):
        AnthropicBudgetGuard(path).__enter__()


def test_resume_requires_same_budget(tmp_path):
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, budget_usd=4, create=True):
        pass
    with pytest.raises(BudgetLedgerError, match="does not match"):
        AnthropicBudgetGuard(path, budget_usd=5).__enter__()


def test_parseable_but_inconsistent_spend_ledger_is_rejected(tmp_path, fake_sdk):
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        _generate()
    ledger = _ledger(path)
    ledger["totals"]["actual_microusd"] = 0
    ledger["phases"]["calibration"]["actual_microusd"] = 0
    path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(BudgetLedgerError, match="actual charges do not match"):
        AnthropicBudgetGuard(path).__enter__()


def test_non_anthropic_provider_is_rejected_before_provider_call(tmp_path, fake_sdk):
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        with pytest.raises(BudgetGuardError, match="unsupported_provider"):
            model_router.generate_response("prompt", model="openai/gpt-5-mini")
    ledger = _ledger(path)
    assert ledger["status"] == "stopped"
    assert ledger["stop_reason"] == "unsupported_provider"
    assert fake_sdk["create"] == []


def test_unknown_model_and_unpriced_kwargs_stop_before_count_or_generation(tmp_path, fake_sdk):
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        client = anthropic.Anthropic(api_key="fixture")
        with pytest.raises(BudgetGuardError, match="unsupported_model"):
            client.messages.create(
                model="claude-sonnet-4-6", max_tokens=4096, temperature=0.3,
                messages=[{"role": "user", "content": "prompt"}],
            )
    assert fake_sdk["count"] == []
    assert fake_sdk["create"] == []
    assert _ledger(path)["stop_reason"] == "unsupported_model"


def test_unsupported_generation_shape_stops_before_count_or_generation(tmp_path, fake_sdk):
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        client = anthropic.Anthropic(api_key="fixture")
        with pytest.raises(BudgetGuardError, match="unsupported_generation_request_shape"):
            client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=4096,
                temperature=0.3, tools=[],
                messages=[{"role": "user", "content": "prompt"}],
            )
    assert fake_sdk["count"] == []
    assert fake_sdk["create"] == []


@pytest.mark.parametrize("modifier", [{"service_tier": "auto"}, {"inference_geo": "us"}])
def test_unpriced_billing_modifiers_are_rejected_before_count(tmp_path, fake_sdk, modifier):
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        client = anthropic.Anthropic(api_key="fixture")
        with pytest.raises(BudgetGuardError, match="unsupported_generation_billing_modifier"):
            client.messages.create(
                model="claude-opus-4-6", max_tokens=4096, temperature=0.3,
                messages=[{"role": "user", "content": "prompt"}], **modifier,
            )
    assert fake_sdk["count"] == []
    assert fake_sdk["create"] == []


@pytest.mark.parametrize(
    "usage",
    [None, {"input_tokens": -1, "output_tokens": 1},
     {"input_tokens": 10, "output_tokens": 1, "service_tier": "standard", "cache_read_input_tokens": 2},
     {"input_tokens": 10, "output_tokens": 1, "service_tier": "priority"},
     {"input_tokens": 10, "output_tokens": 1, "service_tier": "standard", "inference_geo": "us"}],
)
def test_invalid_or_unpriced_usage_retains_reservation_and_halts(tmp_path, fake_sdk, usage):
    fake_sdk["usage"] = usage
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        with pytest.raises(BudgetGuardError):
            _generate()
    ledger = _ledger(path)
    assert ledger["status"] == "stopped"
    assert ledger["stop_reason"] == "generation_usage_uncertain"
    assert ledger["totals"]["reserved_microusd"] == 0
    assert ledger["totals"]["uncertain_microusd"] > 0
    assert ledger["totals"]["actual_microusd"] == 0


def test_ambiguous_generation_failure_keeps_full_reservation_and_halts(tmp_path, fake_sdk):
    fake_sdk["create_error"] = TimeoutError("fixture timeout")
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        with pytest.raises(TimeoutError):
            _generate()
    ledger = _ledger(path)
    call = ledger["calls"][0]
    assert call["status"] == "uncertain"
    assert call["uncertain_microusd"] == call["reservation_microusd"]
    assert ledger["totals"]["uncertain_microusd"] == call["reservation_microusd"]
    assert ledger["status"] == "stopped"


def test_known_usage_above_reservation_is_charged_and_stops(tmp_path, fake_sdk):
    fake_sdk["usage"] = {
        "input_tokens": 30_000,
        "output_tokens": 0,
        "service_tier": "standard",
        "inference_geo": None,
    }
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        with pytest.raises(BudgetGuardError):
            _generate()
    ledger = _ledger(path)
    assert ledger["stop_reason"] == "usage_exceeded_reservation"
    assert ledger["totals"]["actual_microusd"] == 30_000
    assert ledger["calls"][0]["status"] == "over_reservation"


def test_count_tokens_failure_is_counted_and_prevents_generation(tmp_path, fake_sdk):
    fake_sdk["count_error"] = RuntimeError("count failed")
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        with pytest.raises(RuntimeError, match="count failed"):
            _generate()
    ledger = _ledger(path)
    assert ledger["totals"]["count_token_requests"] == 1
    assert ledger["totals"]["count_token_failures"] == 1
    assert ledger["totals"]["generation_attempts"] == 0
    assert fake_sdk["create"] == []


def test_interrupted_request_is_uncertain_on_resume(tmp_path, fake_sdk):
    fake_sdk["create_error"] = KeyboardInterrupt()
    path = tmp_path / "ledger.json"
    with AnthropicBudgetGuard(path, create=True):
        with pytest.raises(KeyboardInterrupt):
            _generate()
    assert _ledger(path)["totals"]["reserved_microusd"] > 0
    with pytest.raises(BudgetGuardError, match="reservation is uncertain"):
        AnthropicBudgetGuard(path).__enter__()
    ledger = _ledger(path)
    assert ledger["status"] == "stopped"
    assert ledger["totals"]["reserved_microusd"] == 0
    assert ledger["totals"]["uncertain_microusd"] > 0


def test_concurrent_reservations_serialize_and_second_is_denied(tmp_path):
    path = tmp_path / "ledger.json"
    guard = AnthropicBudgetGuard(path, create=True)
    with guard:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(guard._reserve, "ab", "claude-haiku-4-5-20251001", 100) for _ in range(2)]
            results = []
            for future in futures:
                try:
                    results.append(future.result())
                except BudgetGuardError:
                    results.append(None)
    ledger = _ledger(path)
    assert sum(result is not None for result in results) == 1
    assert ledger["totals"]["generation_attempts"] == 1
    assert ledger["status"] == "stopped"


def test_context_restores_sdk_and_router_patches_after_exception(tmp_path, fake_sdk):
    original_client = anthropic.Anthropic
    original_create = Messages.create
    original_count = Messages.count_tokens
    original_openai = model_router._generate_openai
    path = tmp_path / "ledger.json"
    with pytest.raises(RuntimeError, match="after patch"):
        with AnthropicBudgetGuard(path, create=True):
            assert anthropic.Anthropic is not original_client
            assert Messages.create is not original_create
            assert Messages.count_tokens is not original_count
            assert model_router._generate_openai is not original_openai
            raise RuntimeError("after patch")
    assert anthropic.Anthropic is original_client
    assert Messages.create is original_create
    assert Messages.count_tokens is original_count
    assert model_router._generate_openai is original_openai
