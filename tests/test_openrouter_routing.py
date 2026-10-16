"""Production model ids reach OpenRouter, mapped by OPENROUTER_MODEL_IDS (#8343).

Network-free: the OpenAI client constructor is replaced, so every request is
captured instead of sent.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.utils import model_router


@pytest.fixture
def sent(monkeypatch):
    """Every chat.completions.create kwargs dict, in order."""
    requests: list[dict] = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    class FakeCompletions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2, cost=0.0001),
                provider="Vendor",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    return requests


def test_anthropic_dialogue_id_reaches_openrouter_mapped_and_guarded(sent, monkeypatch):
    hooked = []
    monkeypatch.setattr(model_router, "_PRE_CALL_HOOK", lambda *args: hooked.append(args))

    model_router.generate_response("hi", model="anthropic/claude-haiku-4-5-20251001", temperature=0.8)

    (request,) = sent
    assert request["model"] == "anthropic/claude-haiku-4.5"
    assert request["extra_body"]["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
    assert hooked == [("openrouter", "anthropic/claude-haiku-4.5", 2)]


def test_openai_recipe_id_reaches_openrouter_mapped(sent):
    model_router.generate_response("hi", model="openai/gpt-5.1", temperature=0.7)

    (request,) = sent
    assert request["model"] == "openai/gpt-5.1"
    assert request["temperature"] == 0.7
    assert request["extra_body"]["provider"] == {"order": ["openai"], "allow_fallbacks": False}


def test_reasoning_model_sends_effort_and_no_temperature(sent):
    model_router.generate_response("hi", model="openai/gpt-6-astra", temperature=0.7)

    (request,) = sent
    assert "temperature" not in request
    assert request["extra_body"]["reasoning"] == {"effort": model_router.OPENAI_REASONING_EFFORT}


def test_anthropic_judge_id_reaches_openrouter_mapped(sent):
    model_router.generate_judge_response("judge this", model="anthropic/claude-opus-4-6")

    (request,) = sent
    assert request["model"] == "anthropic/claude-opus-4.6"


def test_vision_call_sends_images_to_openrouter_as_data_urls(sent):
    model_router.generate_vision_response("rate it", images=[b"png"], model="openai/gpt-5-mini")

    (request,) = sent
    assert request["model"] == "openai/gpt-5-mini"
    text, image = request["messages"][-1]["content"]
    assert text == {"type": "text", "text": "rate it"}
    assert image["image_url"]["url"] == "data:image/png;base64,cG5n"


def test_an_allowlisted_id_with_no_mapping_fails_closed(sent, monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL_ALLOWLIST", "gpt-4o")

    with pytest.raises(RuntimeError, match="No OpenRouter id mapped for 'openai/gpt-4o'"):
        model_router.generate_response("hi", model="openai/gpt-4o")
    assert sent == []
