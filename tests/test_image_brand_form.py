"""Brand-form guarantees for generated recipe images.

June 2026 off-brand drift: hero photos showed sheet-pan squares, flat biscuit
discs, and sliced rolls because (a) image prompts carried only the recipe
title, (b) the vision eval never scored muffin-pan form, and (c) the eval had
been silently falling back to PASS for weeks after gpt-5-mini started
rejecting temperature=0.3 with a 400.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.agents.factory import create_agent


@pytest.fixture
def agent():
    return create_agent("art_director")


# ---------------------------------------------------------------------------
# Image prompts must demand the muffin-cup form in every variant
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("variant", ["macro_closeup", "overhead_flatlay", "hero_threequarter"])
def test_build_prompt_includes_muffin_form_clause(agent, variant) -> None:
    prompt = agent._build_prompt("Smoky Sweet Potato Frittatas", variant)
    assert "muffin-cup shape" in prompt
    assert "Regardless of what the recipe name suggests" in prompt
    assert "Never sheet-pan squares" in prompt


def test_macro_prompt_keeps_tin_out_but_demands_cup_form(agent) -> None:
    prompt = agent._build_prompt("Test Cups", "macro_closeup")
    assert "No full tin visible" in prompt
    assert "muffin-cup form" in prompt


# ---------------------------------------------------------------------------
# Vision eval scores muffin_pan_form and fails off-brand sets
# ---------------------------------------------------------------------------

def _vision_result(muffin_pan_form: float) -> str:
    return json.dumps({
        "per_image": [{
            "image": 1, "variety": 4, "quality": 4, "style_adherence": 4,
            "food_appeal": 4, "composition": 4,
            "muffin_pan_form": muffin_pan_form, "feedback": "ok",
        }],
        "set_diversity": 4,
        "passed": True,
        "reason": "",
        "recommended_winner": 1,
    })


def _eval_with_mocked_vision(agent, tmp_path, raw_response):
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    image_paths = [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}]
    with patch("backend.agents.art_director.generate_vision_response", return_value=raw_response):
        return agent._evaluate_images_vision(image_paths, "Test Cups")


def test_vision_eval_fails_when_form_below_threshold(agent, tmp_path) -> None:
    result = _eval_with_mocked_vision(agent, tmp_path, _vision_result(muffin_pan_form=2))
    assert result["passed"] is False
    assert "muffin" in result["reason"].lower() or result["reason"]


def test_vision_eval_passes_on_brand_form(agent, tmp_path) -> None:
    result = _eval_with_mocked_vision(agent, tmp_path, _vision_result(muffin_pan_form=5))
    assert result["passed"] is True


def test_eval_prompt_asks_for_muffin_pan_form(agent, tmp_path) -> None:
    captured = {}

    def _capture(prompt, images, model, temperature):
        captured["prompt"] = prompt
        return _vision_result(5)

    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    image_paths = [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}]
    with patch("backend.agents.art_director.generate_vision_response", side_effect=_capture):
        agent._evaluate_images_vision(image_paths, "Test Cups")
    assert "muffin_pan_form" in captured["prompt"]
    assert "muffinpanrecipes.com" in captured["prompt"]


def test_vision_eval_missing_form_key_passes_but_alerts(agent, tmp_path) -> None:
    """A response that drops muffin_pan_form must not silently disable the brand check."""
    raw = json.dumps({
        "per_image": [{
            "image": 1, "variety": 4, "quality": 4, "style_adherence": 4,
            "food_appeal": 4, "composition": 4, "feedback": "ok",
        }],
        "set_diversity": 4, "passed": True, "reason": "", "recommended_winner": 1,
    })
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    image_paths = [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}]
    with patch("backend.agents.art_director.generate_vision_response", return_value=raw), \
         patch("backend.utils.discord.notify_pipeline_failure") as notify:
        result = agent._evaluate_images_vision(image_paths, "Test Cups")

    assert result["passed"] is True
    notify.assert_called_once()
    assert "muffin_pan_form" in notify.call_args.kwargs["error_message"]


# ---------------------------------------------------------------------------
# Vision eval errors fall back to pass but NEVER silently
# ---------------------------------------------------------------------------

def test_vision_eval_error_notifies_discord(agent, tmp_path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    image_paths = [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}]
    with patch(
        "backend.agents.art_director.generate_vision_response",
        side_effect=RuntimeError("Error code: 400 - temperature unsupported"),
    ), patch("backend.utils.discord.notify_pipeline_failure") as notify:
        result = agent._evaluate_images_vision(image_paths, "Test Cups")

    assert result["passed"] is True
    assert result["fallback"] is True
    notify.assert_called_once()
    assert "unreviewed" in notify.call_args.kwargs["error_message"]


# ---------------------------------------------------------------------------
# OpenAI vision call retries without temperature on the gpt-5 400
# ---------------------------------------------------------------------------

def test_vision_openai_retries_without_temperature(monkeypatch) -> None:
    calls = []

    class _FakeBadRequestError(Exception):
        pass

    class _FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if "temperature" in kwargs:
                raise _FakeBadRequestError(
                    "Error code: 400 - Unsupported value: 'temperature' does not "
                    "support 0.3 with this model. Only the default (1) value is supported."
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                model="gpt-5-mini",
            )

    class _FakeClient:
        def __init__(self, api_key):
            self.chat = SimpleNamespace(completions=_FakeCompletions())

    monkeypatch.setitem(
        sys.modules, "openai",
        SimpleNamespace(OpenAI=_FakeClient, BadRequestError=_FakeBadRequestError),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from backend.utils.model_router import _generate_vision_openai

    out = _generate_vision_openai(
        prompt="evaluate", images=[b"\x89PNG fake"], system_prompt=None,
        model="gpt-5-mini", temperature=0.3,
    )

    assert out == "ok"
    assert len(calls) == 2
    assert "temperature" in calls[0]
    assert "temperature" not in calls[1]
