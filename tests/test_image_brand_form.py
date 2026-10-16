"""Brand-form guarantees for generated recipe images.

June 2026 off-brand drift: hero photos showed sheet-pan squares, flat biscuit
discs, and sliced rolls because (a) image prompts carried only the recipe
title, (b) the vision eval never scored muffin-pan form, and (c) the eval had
been silently falling back to PASS for weeks after gpt-5-mini started
rejecting temperature=0.3 with a 400.
"""

from __future__ import annotations

import json
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
            "image": 1, "defects": [], "variety": 4, "quality": 4, "style_adherence": 4,
            "food_appeal": 4, "composition": 4,
            "muffin_pan_form": muffin_pan_form, "physical_realism": 5, "feedback": "ok",
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


def test_vision_eval_checks_wells_against_the_stored_pan_size(agent, tmp_path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    with patch("backend.agents.art_director.generate_vision_response",
               return_value=_vision_result(muffin_pan_form=4)) as vision:
        agent._evaluate_images_vision(
            [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}], "Test Cups", pan_size="jumbo")
    assert "coherent jumbo pan" in vision.call_args.kwargs["prompt"]


def test_vision_eval_fails_when_form_below_threshold(agent, tmp_path) -> None:
    result = _eval_with_mocked_vision(agent, tmp_path, _vision_result(muffin_pan_form=2))
    assert result["passed"] is False
    assert "muffin" in result["reason"].lower() or result["reason"]
    # Off-brand form is an ORIGINAL criterion: it may still buy the reshoot.
    assert result["retry_eligible"] is True


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


def test_vision_eval_missing_form_key_is_incomplete_and_alerts(agent, tmp_path) -> None:
    """A response that drops muffin_pan_form must not silently disable the brand check.

    #7936: it is "incomplete" - not a pass, and not worth a paid reshoot.
    """
    raw = json.dumps({
        "per_image": [{
            "image": 1, "defects": [], "variety": 4, "quality": 4, "style_adherence": 4,
            "food_appeal": 4, "composition": 4, "physical_realism": 5, "feedback": "ok",
        }],
        "set_diversity": 4, "passed": True, "reason": "", "recommended_winner": 1,
    })
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    image_paths = [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}]
    with patch("backend.agents.art_director.generate_vision_response", return_value=raw), \
         patch("backend.utils.discord.notify_pipeline_failure") as notify:
        result = agent._evaluate_images_vision(image_paths, "Test Cups")

    assert result["passed"] is False
    assert result["review_status"] == "incomplete"
    assert result["retry_eligible"] is False
    notify.assert_called_once()
    assert "muffin_pan_form" in notify.call_args.kwargs["error_message"]


# ---------------------------------------------------------------------------
# Vision eval errors need human review and are NEVER silent
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

    # #7936: an unavailable review is never a pass; the caller stops on it
    # without a reshoot and the human review decides.
    assert result["passed"] is False
    assert result["review_status"] == "unavailable"
    assert result["fallback"] is True
    notify.assert_called_once()
    assert "human review" in notify.call_args.kwargs["error_message"]
