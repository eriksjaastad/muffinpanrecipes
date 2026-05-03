"""Recipe-anchor injection for dialogue + judge prompts.

Without recipe_data, characters drift off-recipe (e.g., debating
butter-to-sugar ratios for a savory hash-brown nest). _build_recipe_context
emits a one-line summary that gets injected into both the simulator and
the judge prompts so they stay anchored to the actual dish.
"""

from unittest.mock import patch

from backend.admin import cron_routes


def test_build_recipe_context_full_payload():
    recipe = {
        "title": "Maple Hash Brown Nests",
        "category": "Savory",
        "ingredients": [
            {"item": "russet potatoes", "amount": "1 lb"},
            {"item": "maple breakfast sausage", "amount": "8 oz"},
            {"item": "yellow onion", "amount": "1/2 cup"},
            {"item": "red bell pepper", "amount": "1/2 cup"},
            {"item": "large eggs", "amount": "6"},
            {"item": "whole milk", "amount": "1/3 cup"},  # 6th — must be excluded (cap=5)
        ],
    }
    summary = cron_routes._build_recipe_context(recipe)
    assert "Maple Hash Brown Nests" in summary
    assert "(savory)" in summary
    assert "russet potatoes" in summary
    assert "large eggs" in summary
    assert "whole milk" not in summary  # capped at 5 hero items


def test_build_recipe_context_strips_parentheticals():
    recipe = {
        "title": "Test Recipe",
        "category": "Savory",
        "ingredients": [
            {"item": "kosher salt (divided)"},
            {"item": "black pepper (freshly ground)"},
        ],
    }
    summary = cron_routes._build_recipe_context(recipe)
    assert "(divided)" not in summary
    assert "(freshly ground)" not in summary
    assert "kosher salt" in summary


def test_build_recipe_context_handles_string_ingredients():
    recipe = {
        "title": "Test",
        "category": "Sweet",
        "ingredients": ["all-purpose flour", "granulated sugar"],
    }
    summary = cron_routes._build_recipe_context(recipe)
    assert "all-purpose flour" in summary
    assert "granulated sugar" in summary


def test_build_recipe_context_empty_inputs():
    """No title or no recipe_data → empty string (not anchored)."""
    assert cron_routes._build_recipe_context(None) == ""
    assert cron_routes._build_recipe_context({}) == ""
    assert cron_routes._build_recipe_context({"category": "savory"}) == ""
    assert cron_routes._build_recipe_context({"title": ""}) == ""


def test_build_recipe_context_title_only():
    """Recipe with only a title still produces a usable anchor."""
    summary = cron_routes._build_recipe_context({"title": "Some Recipe"})
    assert summary == "This week's recipe: Some Recipe."


def test_judge_prompt_includes_recipe_anchor():
    """When recipe_context is supplied, the judge prompt must contain it."""
    captured: dict[str, str] = {}

    def fake_generate(prompt, system_prompt, **_kwargs):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        return "PASS - anchored to the recipe"

    dialogue = [{"character": "Margaret", "message": "Trays cooled overnight."}]
    episode = {"episode_id": "2026-W18", "stages": {}}
    recipe_anchor = "This week's recipe: Maple Hash Brown Nests (savory). Key ingredients: russet potatoes, eggs."

    with patch.object(cron_routes, "generate_judge_response", side_effect=fake_generate):
        passed, _ = cron_routes._judge_dialogue(
            "Weekly Muffin Pan Recipe", "tuesday", dialogue, episode,
            recipe_context=recipe_anchor,
        )

    assert passed is True
    assert recipe_anchor in captured["prompt"]
    # System prompt teaches the judge to enforce recipe fidelity
    assert "RECIPE FIDELITY" in captured["system_prompt"]


def test_judge_prompt_omits_anchor_when_absent():
    """No recipe_context → judge falls back to its old behavior (no anchor line)."""
    captured: dict[str, str] = {}

    def fake_generate(prompt, **_kwargs):
        captured["prompt"] = prompt
        return "PASS"

    dialogue = [{"character": "Margaret", "message": "Hi."}]
    with patch.object(cron_routes, "generate_judge_response", side_effect=fake_generate):
        cron_routes._judge_dialogue("Concept", "tuesday", dialogue, {"stages": {}})

    assert "This week's recipe:" not in captured["prompt"]


def test_generate_and_judge_passes_recipe_through():
    """Verify the wrapper threads recipe_data into both _generate_dialogue and _judge_dialogue."""
    recipe = {"title": "Maple Hash Brown Nests", "category": "savory", "ingredients": [{"item": "potato"}]}

    sim_calls: list[dict] = []
    judge_calls: list[dict] = []

    def fake_generate_dialogue(stage, concept, **kwargs):
        sim_calls.append({"stage": stage, **kwargs})
        return [{"character": "Margaret", "message": "Hi."}]

    def fake_judge_dialogue(concept, stage, dialogue, episode, **kwargs):
        judge_calls.append({"stage": stage, **kwargs})
        return True, "PASS"

    def fake_qa(*_args, **_kwargs):
        return None

    with patch.object(cron_routes, "_generate_dialogue", side_effect=fake_generate_dialogue), \
         patch.object(cron_routes, "_judge_dialogue", side_effect=fake_judge_dialogue), \
         patch.object(cron_routes, "_score_dialogue_qa", side_effect=fake_qa):
        cron_routes._generate_and_judge_dialogue(
            "tuesday", "Concept", {"stages": {}, "episode_id": "T"},
            recipe_data=recipe,
        )

    assert sim_calls and "Maple Hash Brown Nests" in sim_calls[0]["recipe_context"]
    assert judge_calls and "Maple Hash Brown Nests" in judge_calls[0]["recipe_context"]
