"""Tests for pick_concept scoring helpers."""

from scripts.pick_concept import score_candidate


def test_score_candidate_caps_at_eight() -> None:
    name = (
        "Mini Pumpkin Cranberry Ginger Spice Chocolate Caramel Biscuit Cups"
    )
    score = score_candidate(name, recent_concepts=[], current_month=12)
    assert score == 8.0
