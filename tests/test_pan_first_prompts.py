"""Pan-first generation: prompts must make the LLM envision the muffin pan.

Gates catch off-brand output after the fact; these prompts are the positive
side — the baker, concept picker, and Sunday auto-fixer all reason from the
physical pan (twelve ROUND flared wells) before inventing or repairing a
recipe. Brand drift incident: W24 "Cheddar Broccoli Egg Squares".
"""

from __future__ import annotations

from backend.admin.cron_routes import _RECIPE_FIX_SYSTEM_PROMPT
from backend.utils.recipe_prompts import (
    _build_recipe_system_prompt,
    _build_recipe_user_prompt,
)
from scripts.pick_concept import score_candidate


# ---------------------------------------------------------------------------
# Baker prompts
# ---------------------------------------------------------------------------

def test_system_prompt_opens_with_pan_visualization() -> None:
    prompt = _build_recipe_system_prompt({"name": "Margaret Chen"})
    assert "ENVISION THE PAN FIRST" in prompt
    assert "ROUND" in prompt
    # Geometry grounding, not just an adjective
    assert "2 3/4 inches" in prompt
    # Visualization must come before the rules
    assert prompt.index("ENVISION THE PAN FIRST") < prompt.index("CRITICAL RULES")


def test_system_prompt_bans_impossible_shapes() -> None:
    prompt = _build_recipe_system_prompt({"name": "Margaret Chen"})
    assert "cannot make squares" in prompt
    assert "NEVER Squares, Bars, Slabs, Slices, or Wedges" in prompt
    assert '"Small" is not the brand' in prompt


def test_title_format_cites_w24_bad_example() -> None:
    prompt = _build_recipe_system_prompt({"name": "Margaret Chen"})
    assert "Cheddar Broccoli Egg Squares" in prompt


def test_user_prompt_reinforces_pan_first_design() -> None:
    prompt = " ".join(_build_recipe_user_prompt("Lemon Ricotta Cheesecake Cups").split())
    assert "Envision the pan first" in prompt
    assert "round molded shape" in prompt


# ---------------------------------------------------------------------------
# Sunday auto-fix prompt
# ---------------------------------------------------------------------------

def test_fix_prompt_carries_pan_geometry_and_shape_ban() -> None:
    assert "twelve ROUND, flared wells" in _RECIPE_FIX_SYSTEM_PROMPT
    assert "NEVER Squares" in _RECIPE_FIX_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Concept picker
# ---------------------------------------------------------------------------

def test_off_brand_shape_candidates_score_zero() -> None:
    for name in (
        "Lemon Crumble Bars",
        "Sheet Pan Frittata Squares",
        "Pumpkin Pie Slices",
        "Brownie Wedges",
        "Chicken Traybake",
    ):
        assert score_candidate(name, recent_concepts=[], current_month=6) == 0.0, name


def test_on_brand_candidates_still_score() -> None:
    score = score_candidate(
        "Spinach Feta Egg Cups", recent_concepts=[], current_month=6
    )
    assert score > 0.0
