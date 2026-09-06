"""Pan-first generation: prompts must make the LLM envision the muffin pan.

Gates catch off-brand output after the fact; these prompts are the positive
side — the baker, concept picker, and Sunday auto-fixer all reason from the
physical pan (twelve ROUND flared wells) before inventing or repairing a
recipe. Brand drift incident: W24 "Cheddar Broccoli Egg Squares".
"""

from __future__ import annotations

import json


from backend.admin.cron_routes import _RECIPE_FIX_SYSTEM_PROMPT
from backend.utils.recipe_prompts import (
    _build_recipe_system_prompt,
    _build_recipe_user_prompt,
)
from scripts.pick_concept import CURATED_CONCEPTS, Candidate, pick_concept, rank_candidates


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

def _sweet(name: str) -> Candidate:
    return Candidate(name, "Sweet", "French", "sets as it cools", "brainstorm")


def test_off_brand_shape_candidates_are_hard_rejected() -> None:
    """Squares/bars/slices/wedges/traybake name a shape a muffin pan cannot
    make. They used to score 0.0 and could still slip through a thin pool's
    weighted floor; now they are rejected before scoring (#6858)."""
    for name in (
        "Lemon Crumble Bars",
        "Sheet Pan Frittata Squares",
        "Pumpkin Pie Slices",
        "Brownie Wedges",
        "Chicken Traybake",
    ):
        survivors, rejected = rank_candidates([_sweet(name)], "Sweet", {"recipes": []})
        assert survivors == [], name
        assert rejected[0][1].startswith("off_brand_shape"), (name, rejected[0][1])


def test_on_brand_candidates_survive_and_score() -> None:
    survivors, rejected = rank_candidates(
        [Candidate("Spinach Feta Egg Cups", "Breakfast", "Greek", "", "curated")],
        "Breakfast",
        {"recipes": []},
    )
    assert rejected == []
    assert survivors[0][0] > 0.0


def test_all_off_brand_brainstorm_falls_back_to_curated_list(monkeypatch) -> None:
    """A brainstorm that only proposes off-brand shapes must route to the
    curated pool, never return one of the shapes (#6858)."""
    monkeypatch.setenv("CONCEPT_MODEL", "test/fake-model")

    def fake_generate(prompt, system_prompt=None, *, model, temperature):
        return json.dumps([
            {"concept": "Lemon Crumble Bars", "category": "Sweet", "cuisine": "American", "binds_how": ""},
            {"concept": "Brownie Wedges", "category": "Sweet", "cuisine": "American", "binds_how": ""},
        ])

    picks = pick_concept(
        count=1, target_category="Sweet", catalog={"recipes": []},
        generate=fake_generate, fetch_inspiration=False,
    )

    assert len(picks) == 1
    assert "bars" not in picks[0].lower()
    assert "wedges" not in picks[0].lower()
    assert picks[0] in {concept for concept, _ in CURATED_CONCEPTS["Sweet"]}


# ---------------------------------------------------------------------------
# Cuisine: baker declares it + prompts steer toward global variety
# ---------------------------------------------------------------------------

from backend.utils.recipe_prompts import _parse_recipe_response  # noqa: E402


def test_output_format_asks_for_cuisine() -> None:
    prompt = _build_recipe_system_prompt({"name": "Margaret Chen"})
    assert "CUISINE:" in prompt
    assert "Do NOT default to American" in prompt


def test_parser_captures_cuisine() -> None:
    response = (
        "TITLE: Gochujang Pork Bites\n"
        "DESCRIPTION: Spicy Korean-inspired bites.\n"
        "CATEGORY: savory\n"
        "CUISINE: Korean\n"
        "INGREDIENTS:\n- 1 lb pork\n"
        "INSTRUCTIONS:\n1. Mix.\n2. Bake.\n"
    )
    parsed = _parse_recipe_response(response, "concept")
    assert parsed["cuisine"] == "Korean"


def test_parser_defaults_cuisine_empty_when_absent() -> None:
    response = "TITLE: Plain Cups\nINGREDIENTS:\n- 1 egg\nINSTRUCTIONS:\n1. Bake.\n"
    assert _parse_recipe_response(response, "c")["cuisine"] == ""


# ---------------------------------------------------------------------------
# Calories: asked for, parsed, never invented
# ---------------------------------------------------------------------------

def test_output_format_asks_for_calories() -> None:
    prompt = _build_recipe_system_prompt({"name": "Margaret Chen"})
    assert "CALORIES:" in prompt
    assert "per serving" in prompt


def test_parser_captures_calories_as_int() -> None:
    response = (
        "TITLE: Plain Cups\n"
        "CALORIES: 185\n"
        "INGREDIENTS:\n- 1 egg\n"
        "INSTRUCTIONS:\n1. Bake.\n"
    )
    assert _parse_recipe_response(response, "c")["calories"] == 185


def test_parser_leaves_calories_none_when_absent() -> None:
    """No fabricated default — an absent figure must stay absent so the page
    and its JSON-LD both omit nutrition."""
    response = "TITLE: Plain Cups\nINGREDIENTS:\n- 1 egg\nINSTRUCTIONS:\n1. Bake.\n"
    assert _parse_recipe_response(response, "c")["calories"] is None


def test_user_prompt_always_encourages_global_cuisines() -> None:
    prompt = " ".join(_build_recipe_user_prompt("Egg Cups").split())
    assert "Reach beyond American comfort food" in prompt


def test_user_prompt_names_recent_cuisines_to_avoid() -> None:
    prompt = " ".join(
        _build_recipe_user_prompt("Egg Cups", recent_cuisines=["American", "Italian"]).split()
    )
    assert "Recent recipes have been: American, Italian" in prompt
    assert "NOT in that list" in prompt
