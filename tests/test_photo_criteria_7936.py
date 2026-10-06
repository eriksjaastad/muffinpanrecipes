"""Photo criteria and automated-review handling (#7936). Offline only.

Moved unchanged from tests/test_photo_approval_7936.py when that file was
rewritten around the photo-control log.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest


# --- Evaluator criteria and winner handling --------------------------------

@pytest.fixture
def art_director():
    from backend.agents.factory import create_agent

    return create_agent("art_director")


def _vision(art_director, tmp_path, response):
    img = tmp_path / "a.png"
    img.write_bytes(b"png")
    paths = [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}]
    with patch("backend.agents.art_director.generate_vision_response", return_value=json.dumps(response)) as call, \
         patch("backend.utils.discord.notify_pipeline_failure"):
        result = art_director._evaluate_images_vision(paths, "Egg Cups", "Ingredients: eggs, spinach")
    return result, call.call_args.kwargs["prompt"]


_GOOD = {"defects": [], "variety": 4, "quality": 4, "style_adherence": 4, "food_appeal": 4,
         "composition": 4, "muffin_pan_form": 5, "physical_realism": 5}


def test_prompt_names_pan_and_food_defects_and_recipe_facts(art_director, tmp_path):
    _, prompt = _vision(art_director, tmp_path, {"per_image": [{"image": 1, **_GOOD}], "set_diversity": 4})
    for phrase in ("non-overlapping", "fused", "underfilled", "liquid", "invented pastry",
                   "Empty wells alone are fine", "Ingredients: eggs, spinach", "physical_realism"):
        assert phrase in prompt


def test_physical_defect_fails_the_set(art_director, tmp_path):
    bad = {**_GOOD, "defects": ["wells fused together"], "physical_realism": 2}
    result, _ = _vision(art_director, tmp_path, {"per_image": [{"image": 1, **bad}], "set_diversity": 4})
    assert result["passed"] is False and result["review_status"] == "failed"


def test_missing_defect_list_is_not_an_automated_pass(art_director, tmp_path):
    partial = {k: v for k, v in _GOOD.items() if k != "defects"}
    result, _ = _vision(art_director, tmp_path, {"per_image": [{"image": 1, **partial}], "set_diversity": 4})
    assert result["review_status"] == "incomplete"


def _photograph(art_director, tmp_path, monkeypatch, evaluation):
    from backend.core.task import Task

    monkeypatch.setattr(art_director, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("STABILITY_API_KEY", "test-key")
    monkeypatch.setattr(art_director, "_call_stability", lambda *_a, **_k: b"png")
    monkeypatch.setattr("backend.agents.art_director._check_visual_diversity", lambda _p: True)
    calls = []
    monkeypatch.setattr(art_director, "_evaluate_images_vision",
                        lambda *_a: calls.append(1) or dict(evaluation))
    with patch("backend.storage.storage.save_image", return_value="u"):
        out = art_director.process_task(Task(
            type="photograph_recipe", content="shoot",
            context={"recipe_id": "rid-7", "recipe_data": {"title": "Egg Cups"}},
        )).output
    return out, calls


def test_recommended_winner_one_is_honoured(art_director, tmp_path, monkeypatch):
    out, _ = _photograph(art_director, tmp_path, monkeypatch,
                         {"passed": True, "review_status": "passed", "recommended_winner": 1})
    assert out["winner"]["variant"] == out["rounds"][0]["variants"][0]["variant"]
    assert out["automated_review_status"] == "passed"


def test_unavailable_review_stops_without_reshoot_and_is_not_approved(art_director, tmp_path, monkeypatch):
    out, calls = _photograph(art_director, tmp_path, monkeypatch,
                             {"passed": False, "review_status": "unavailable", "fallback": True})
    assert len(calls) == 1 and len(out["rounds"]) == 1
    assert out["automated_review_status"] == "unavailable"


def test_failed_rounds_never_report_approved(art_director, tmp_path, monkeypatch):
    out, calls = _photograph(art_director, tmp_path, monkeypatch,
                             {"passed": False, "review_status": "failed", "reason": "fused wells"})
    assert len(calls) == art_director._MAX_ROUNDS
    assert out["automated_review_status"] == "failed"



def _vision_n(art_director, tmp_path, response, n=2, missing=()):
    paths = []
    for i in range(n):
        img = tmp_path / f"img{i}.png"
        if i not in missing:
            img.write_bytes(b"png")
        paths.append({"variant": f"v{i}", "path": f"nowhere/v{i}.png", "local_path": str(img)})
    with patch("backend.agents.art_director.generate_vision_response",
               return_value=json.dumps(response)) as call, \
         patch("backend.utils.discord.notify_pipeline_failure"):
        result = art_director._evaluate_images_vision(paths, "Egg Cups")
    return result, call


def test_listed_defect_blocks_a_pass_even_with_a_perfect_realism_score(art_director, tmp_path):
    lying = {**_GOOD, "defects": ["custard shells underfilled"], "physical_realism": 5}
    result, _ = _vision_n(art_director, tmp_path, {
        "per_image": [{"image": 1, **_GOOD}, {"image": 2, **lying}], "set_diversity": 4})
    assert result["passed"] is False and result["review_status"] == "failed"
    assert result["quality_defects"] == {"2": ["custard shells underfilled"]}
    # Original criteria passed, so no paid reshoot over the new standard.
    assert result["criteria_passed"] is True and result["retry_eligible"] is False


@pytest.mark.parametrize("per_image", [
    [{"image": 1, **_GOOD}],                                   # missing record
    [{"image": 1, **_GOOD}, {"image": 1, **_GOOD}],            # duplicate
    [{"image": 1, **_GOOD}, {"image": 3, **_GOOD}],            # out of range
    [{"image": 1, **_GOOD}, {"image": "2", **_GOOD}],          # not an int
    [{"image": 1, **_GOOD}, {"image": 2, **{k: v for k, v in _GOOD.items() if k != "physical_realism"}}],
    [{"image": 1, **_GOOD}, {"image": 2, **_GOOD, "quality": "high"}],
    "not a list",
])
def test_malformed_per_image_is_incomplete_never_passed_never_reshot(art_director, tmp_path, per_image):
    result, _ = _vision_n(art_director, tmp_path, {"per_image": per_image, "set_diversity": 4})
    assert result["review_status"] == "incomplete"
    assert result["passed"] is False and result["retry_eligible"] is False


def test_original_criteria_failure_still_buys_the_reshoot(art_director, tmp_path):
    weak = {**_GOOD, "quality": 2, "food_appeal": 2}
    result, _ = _vision_n(art_director, tmp_path, {
        "per_image": [{"image": 1, **weak}, {"image": 2, **weak}], "set_diversity": 4})
    assert result["review_status"] == "failed" and result["retry_eligible"] is True


def test_partial_image_set_is_not_ranked_or_reshot(art_director, tmp_path):
    result, call = _vision_n(art_director, tmp_path, {"per_image": []}, n=3, missing=(1,))
    call.assert_not_called()
    assert result["review_status"] == "incomplete"
    assert result["passed"] is False and result["retry_eligible"] is False
    assert "v1" in result["reason"]


@pytest.mark.parametrize("evaluation", [
    {"passed": False, "review_status": "failed", "retry_eligible": False, "reason": "fused wells"},
    {"passed": False, "review_status": "incomplete", "retry_eligible": False},
])
def test_quality_only_or_incomplete_review_stops_after_one_round(art_director, tmp_path, monkeypatch, evaluation):
    out, calls = _photograph(art_director, tmp_path, monkeypatch, evaluation)
    assert len(calls) == 1 and len(out["rounds"]) == 1
    assert out["automated_review_status"] == evaluation["review_status"]
