"""api_trust_tracker instrumentation in the model router."""

from __future__ import annotations

import sys
from types import SimpleNamespace

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
