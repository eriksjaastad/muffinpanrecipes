"""api_cost_tracker instrumentation in the model router."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from backend.utils import model_router


class _FakeUsage:
    input_tokens = 11
    output_tokens = 7


class _FakeBlock:
    text = "tracked response"


class _FakeResponse:
    usage = _FakeUsage()
    content = [_FakeBlock()]


class _FakeMessages:
    def create(self, **_kwargs):
        return _FakeResponse()


class _FakeAnthropic:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.messages = _FakeMessages()


def _install_fake_anthropic(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=_FakeAnthropic))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_anthropic_text_generation_tracks_with_model_router_caller(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    calls = []
    monkeypatch.setattr(
        model_router,
        "_central_track",
        lambda response, provider, **kwargs: calls.append((response, provider, kwargs)) or response,
    )
    model_router.reset_cost_log()

    text = model_router._generate_anthropic(
        prompt="Say hello.",
        system_prompt=None,
        model="claude-haiku-4-5-20251001",
        temperature=0.2,
    )

    assert text == "tracked response"
    assert calls
    assert calls[0][1] == "anthropic"
    assert calls[0][2]["project"] == "muffinpanrecipes"
    assert calls[0][2]["caller"] == "model_router"


def test_anthropic_vision_generation_tracks_with_model_router_caller(monkeypatch):
    _install_fake_anthropic(monkeypatch)
    calls = []
    monkeypatch.setattr(
        model_router,
        "_central_track",
        lambda response, provider, **kwargs: calls.append((response, provider, kwargs)) or response,
    )
    model_router.reset_cost_log()

    text = model_router._generate_vision_anthropic(
        prompt="Describe it.",
        images=[b"fake-png"],
        system_prompt=None,
        model="claude-haiku-4-5-20251001",
        temperature=0.2,
    )

    assert text == "tracked response"
    assert calls
    assert calls[0][1] == "anthropic"
    assert calls[0][2]["project"] == "muffinpanrecipes"
    assert calls[0][2]["caller"] == "model_router"


def test_openrouter_response_is_recorded_centrally_with_its_billed_cost(monkeypatch):
    """Intended with api-cost-tracker 0.2.0: OpenRouter calls reach the central
    tracker with tokens and the billed usage.cost (0.1.x dropped the provider
    silently) (#8038). Runs the real client's extractor; only the POST is captured."""
    client = pytest.importorskip("api_cost_tracker.tracker")  # in the dev extra

    posted = []
    monkeypatch.setattr(client, "_post_to_server", lambda endpoint, payload: posted.append((endpoint, payload)) or True)
    monkeypatch.setattr(model_router, "_central_track", client.track)
    monkeypatch.setattr(model_router, "_record_cost", lambda *args, **kwargs: None)

    response = SimpleNamespace(
        model="openai/gpt-4o-mini",
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5, total_tokens=17, cost=0.0031),
        choices=[SimpleNamespace(message=SimpleNamespace(content=" hi "), finish_reason="stop")],
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )

    text, finish, completion_tokens = model_router._openrouter_attempt(
        fake_client, "openai/gpt-4o-mini", [{"role": "user", "content": "x"}], 0.2
    )

    assert (text, finish, completion_tokens) == ("hi", "stop", 5)
    ((endpoint, payload),) = posted
    assert endpoint == "/track"
    assert payload["provider"] == "openrouter"
    assert payload["caller"] == "model_router.openrouter"
    assert (payload["prompt_tokens"], payload["completion_tokens"]) == (12, 5)
    assert payload["estimated_cost_usd"] == 0.0031


def test_haiku_4_5_is_priced_at_its_list_rate():
    """#8158: $1 input / $5 output per M tokens (Anthropic list price). The router
    had Haiku 3.5's 0.80/4.00, so cost estimates ran 20% low and the lab's
    --max-cost under-counted Haiku spend. The lab's ledger guard prices the same
    model in microdollars per token; the two tables must agree."""
    from scripts.conversation_budget import MODEL_RATES

    assert model_router._COST_PER_M_TOKENS["claude-haiku-4-5-20251001"] == (1.00, 5.00)
    assert model_router._COST_PER_M_TOKENS["claude-haiku-4-5-20251001:vision"] == (1.00, 5.00)
    assert MODEL_RATES["claude-haiku-4-5-20251001"] == (1, 5)
