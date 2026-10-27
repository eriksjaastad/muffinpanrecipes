"""Strict vision-evaluator validation (#7936): no defaulted scores, ever."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from backend.agents.factory import create_agent

DIMS = [
    "variety", "quality", "style_adherence", "food_appeal", "composition",
    "muffin_pan_form", "physical_realism",
]


@pytest.fixture
def agent():
    return create_agent("art_director")


def _record(image: int = 1, **overrides) -> dict:
    rec = {"image": image, "defects": [], "feedback": "ok"}
    rec.update({d: 4 for d in DIMS})
    rec.update(overrides)
    return rec


def _payload(per_image=None, **top) -> str:
    body = {
        "per_image": per_image if per_image is not None else [_record()],
        "set_diversity": 4, "passed": True, "reason": "", "recommended_winner": 1,
    }
    body.update(top)
    return json.dumps(body)  # allow_nan default: emits NaN / Infinity tokens


def _evaluate(agent, tmp_path, raw, n=1):
    paths = []
    for i in range(n):
        img = tmp_path / f"shot{i}.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        paths.append({"variant": f"v{i}", "path": "x", "local_path": str(img)})
    with patch("backend.agents.art_director.generate_vision_response", return_value=raw), \
         patch("backend.utils.discord.notify_pipeline_failure"):
        return agent._evaluate_images_vision(paths, "Test Cups")


def _assert_incomplete(result):
    assert result["review_status"] == "incomplete"
    assert result["passed"] is False
    assert result["retry_eligible"] is False
    assert not result["criteria_passed"]
    assert result["avg_score"] is None


BAD_VALUES = [
    float("nan"), float("inf"), float("-inf"), True, "4", None, 0, 6, -1,
]
BAD_IDS = ["nan", "inf", "-inf", "bool", "str", "none", "zero", "six", "neg"]


@pytest.mark.parametrize("dim", DIMS)
def test_missing_dimension_is_incomplete(agent, tmp_path, dim) -> None:
    rec = _record()
    del rec[dim]
    _assert_incomplete(_evaluate(agent, tmp_path, _payload([rec])))


def test_missing_defects_is_incomplete(agent, tmp_path) -> None:
    rec = _record()
    del rec["defects"]
    _assert_incomplete(_evaluate(agent, tmp_path, _payload([rec])))


@pytest.mark.parametrize("bad", ["none", {"a": 1}, None])
def test_defects_not_a_list_is_incomplete(agent, tmp_path, bad) -> None:
    _assert_incomplete(_evaluate(agent, tmp_path, _payload([_record(defects=bad)])))


def test_missing_set_diversity_is_incomplete(agent, tmp_path) -> None:
    raw = json.loads(_payload())
    del raw["set_diversity"]
    _assert_incomplete(_evaluate(agent, tmp_path, json.dumps(raw)))


@pytest.mark.parametrize("dim", DIMS)
@pytest.mark.parametrize("bad", BAD_VALUES, ids=BAD_IDS)
def test_invalid_dimension_value_is_incomplete(agent, tmp_path, dim, bad) -> None:
    _assert_incomplete(_evaluate(agent, tmp_path, _payload([_record(**{dim: bad})])))


@pytest.mark.parametrize("bad", BAD_VALUES, ids=BAD_IDS)
def test_invalid_set_diversity_is_incomplete(agent, tmp_path, bad) -> None:
    _assert_incomplete(_evaluate(agent, tmp_path, _payload(set_diversity=bad)))


def test_one_bad_image_among_several_is_incomplete(agent, tmp_path) -> None:
    per_image = [_record(1), _record(2, quality=float("nan")), _record(3)]
    _assert_incomplete(_evaluate(agent, tmp_path, _payload(per_image), n=3))


def test_complete_original_criteria_failure_is_retry_eligible(agent, tmp_path) -> None:
    result = _evaluate(agent, tmp_path, _payload(set_diversity=2))
    assert result["review_status"] == "failed"
    assert result["passed"] is False
    assert result["criteria_passed"] is False
    assert result["retry_eligible"] is True


def test_complete_low_form_is_retry_eligible(agent, tmp_path) -> None:
    result = _evaluate(agent, tmp_path, _payload([_record(muffin_pan_form=2)]))
    assert result["review_status"] == "failed"
    assert result["retry_eligible"] is True


def test_defects_only_fail_but_never_buy_a_reshoot(agent, tmp_path) -> None:
    result = _evaluate(agent, tmp_path, _payload([_record(defects=["fused wells"])]))
    assert result["review_status"] == "failed"
    assert result["passed"] is False
    assert result["criteria_passed"] is True
    assert result["retry_eligible"] is False


def test_low_physical_realism_only_fails_without_reshoot(agent, tmp_path) -> None:
    result = _evaluate(agent, tmp_path, _payload([_record(physical_realism=2)]))
    assert result["review_status"] == "failed"
    assert result["retry_eligible"] is False


def test_complete_clean_passes(agent, tmp_path) -> None:
    result = _evaluate(agent, tmp_path, _payload())
    assert result["review_status"] == "passed"
    assert result["passed"] is True
    assert result["criteria_passed"] is True
    assert result["retry_eligible"] is False
    assert result["avg_score"] == 4.0


def test_boundary_scores_one_and_five_are_valid(agent, tmp_path) -> None:
    result = _evaluate(agent, tmp_path, _payload([_record(physical_realism=5, variety=1)]))
    assert result["review_status"] != "incomplete"
