"""Recipe-anchor injection for dialogue + judge prompts.

Without recipe_data, characters drift off-recipe (e.g., debating
butter-to-sugar ratios for a savory hash-brown nest). _build_recipe_context
emits a one-line summary that gets injected into both the simulator and
the judge prompts so they stay anchored to the actual dish.
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from backend.admin import cron_routes


def test_build_recipe_context_full_payload():
    """#7104: the anchor states what the dish IS, not what is in it."""
    recipe = {
        "title": "Maple Hash Brown Nests",
        "category": "Savory",
        "description": "Shredded potato is pressed into each well and baked until the edges go crisp while the centre stays soft. Serve warm.",
        "ingredients": [
            {"item": "russet potatoes", "amount": "1 lb"},
            {"item": "maple breakfast sausage", "amount": "8 oz"},
        ],
    }
    summary = cron_routes._build_recipe_context(recipe)
    assert "Maple Hash Brown Nests" in summary
    assert "(savory)" in summary
    assert "edges go crisp" in summary
    # The ingredient list is deliberately gone — it invited invented texture.
    assert "russet potatoes" not in summary
    assert "maple breakfast sausage" not in summary


def test_build_recipe_context_uses_only_the_first_description_sentence():
    """"Light by design": a multi-sentence description must not become a recitation."""
    recipe = {
        "title": "Test Recipe",
        "category": "Savory",
        "description": "These are chewy and stretchy. Then a second sentence. And a third.",
    }
    summary = cron_routes._build_recipe_context(recipe)
    assert "chewy and stretchy" in summary
    assert "second sentence" not in summary
    assert "third" not in summary


def test_build_recipe_context_truncates_a_runaway_first_sentence():
    recipe = {
        "title": "Test",
        "category": "Sweet",
        "description": "word " * 200,
    }
    summary = cron_routes._build_recipe_context(recipe)
    assert len(summary) < 300
    assert summary.endswith("...")


def test_build_recipe_context_survives_a_missing_description():
    """Older episodes and pre-baker runs carry no description; anchor still works."""
    summary = cron_routes._build_recipe_context(
        {"title": "No Description Cups", "category": "Sweet", "ingredients": [{"item": "sugar"}]}
    )
    assert summary == "This week's recipe: No Description Cups (sweet)."


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
        return json.dumps({"scores": {}, "verdict": "PASS", "weakest": [], "reason": "anchored to the recipe"})

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


def test_execute_cron_stage_stub_passes_recipe_context_into_dialogue():
    """Admin simulation stubs should anchor dialogue to the Monday recipe."""
    recipe = {
        "title": "Maple Hash Brown Nests",
        "category": "savory",
        "description": "Shredded potato bakes crisp at the edges and stays soft in the middle.",
        "ingredients": [{"item": "potato"}, {"item": "maple sausage"}],
    }
    episode = {
        "episode_id": "2026-W18",
        "concept": "Hash brown breakfast cups",
        "stages": {"monday": {"recipe_data": recipe}},
        "events": [],
    }

    dialogue_calls: list[dict] = []

    def fake_generate_dialogue(stage, concept, **kwargs):
        dialogue_calls.append({"stage": stage, "concept": concept, **kwargs})
        return [{"character": "Margaret", "message": "These nests hold together."}]

    with patch.object(cron_routes, "_load_or_create_episode", return_value=episode), \
         patch.object(cron_routes, "_generate_dialogue", side_effect=fake_generate_dialogue), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "_get_orchestrator") as get_orchestrator:
        result = asyncio.run(
            cron_routes.execute_cron_stage_stub(
                "tuesday",
                "2026-W18",
                "Hash brown breakfast cups",
                model="test-model",
            )
        )

    assert result["mode"] == "simulation"
    assert dialogue_calls
    assert dialogue_calls[0]["stage"] == "tuesday"
    assert dialogue_calls[0]["model"] == "test-model"
    assert "Maple Hash Brown Nests" in dialogue_calls[0]["recipe_context"]
    assert "crisp at the edges" in dialogue_calls[0]["recipe_context"]
    assert episode["stages"]["tuesday"]["status"] == "complete"
    save_episode.assert_called_once_with("2026-W18", episode)
    get_orchestrator.assert_not_called()


def test_judge_dialogue_fails_closed_on_exception():
    """Judge provider errors must not silently approve recipe-fidelity failures."""
    dialogue = [{"character": "Margaret", "message": "This should be judged."}]

    with patch.object(cron_routes, "generate_judge_response", side_effect=RuntimeError("provider down")):
        passed, verdict = cron_routes._judge_dialogue(
            "Concept", "tuesday", dialogue, {"stages": {}}
        )

    assert passed is False
    assert "JUDGE ERROR: RuntimeError: provider down" == verdict
    assert "defaulting" not in verdict.lower()


def test_generate_and_judge_raises_after_judge_exceptions():
    dialogue = [{"character": "Margaret", "message": "This should be judged."}]

    with patch.object(cron_routes, "_generate_dialogue", return_value=dialogue), \
         patch.object(cron_routes, "generate_judge_response", side_effect=RuntimeError("provider down")), \
         patch.object(cron_routes, "notify_judge_failure") as notify:
        with pytest.raises(cron_routes.JudgeFailedError) as exc:
            cron_routes._generate_and_judge_dialogue(
                "tuesday",
                "Concept",
                {"episode_id": "2026-W20", "stages": {}},
                max_retries=1,
            )

    assert "JUDGE ERROR: RuntimeError: provider down" in str(exc.value)
    notify.assert_called_once()
