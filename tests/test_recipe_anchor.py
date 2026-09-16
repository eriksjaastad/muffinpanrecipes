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


def test_build_recipe_context_keeps_the_whole_description_not_just_sentence_one():
    """#7104 second pass: the texture is not reliably in sentence one.

    W38's sentence one was pure marketing and sentence two held the only texture
    in the field. Taking sentence one discarded exactly what the anchor exists
    to deliver.
    """
    recipe = {
        "title": "Cardamom Cinnamon Spiral Bites",
        "category": "Sweet",
        "description": (
            "These spiral bites wrap classic cinnamon-roll comfort around warm cardamom, "
            "orange, and pistachio for a Middle Eastern-inspired twist. A rich, buttery "
            "dough is coiled into each muffin cup so it bakes into a round, self-contained "
            "roll with crisp edges, tender centers, and a glossy orange-rose glaze."
        ),
    }
    summary = cron_routes._build_recipe_context(recipe)
    assert "Middle Eastern-inspired twist" in summary
    assert "crisp edges, tender centers" in summary, "the texture sentence must survive"


def test_build_recipe_context_truncates_a_runaway_description():
    recipe = {"title": "Test", "category": "Sweet", "description": "word " * 200}
    summary = cron_routes._build_recipe_context(recipe)
    assert len(summary) < 500
    assert summary.endswith("...")


def test_judge_recipe_facts_carry_the_method_the_speakers_never_saw():
    """The judge must be able to catch a technique the recipe does not use (#7104).

    W38's accepted Tuesday discussed lamination, butter in sheets and second
    folds. The recipe rolls a soft yeast dough up once - there are no folds. The
    judge scored it technical_credibility 4 because its prompt carried the same
    abbreviated blurb the speakers had, and no instructions at all.
    """
    recipe = {
        "title": "Cardamom Cinnamon Spiral Bites",
        "category": "Sweet",
        "cuisine": "Middle Eastern",
        "description": "Spiral bites with cardamom and orange.",
        "ingredients": [{"item": "yeast dough"}, {"item": "softened butter"}],
        "instructions": [
            "Roll the dough into a rectangle about 12 inches by 9 inches.",
            "Spread the filling evenly over the dough.",
            "Roll the dough up tightly into a log and cut into 12 slices.",
        ],
    }
    facts = cron_routes._build_judge_recipe_facts(recipe)
    assert "RECIPE GROUND TRUTH" in facts
    assert "Method:" in facts
    assert "roll the dough up tightly into a log".lower() in facts.lower()
    assert "yeast dough" in facts
    assert "technical_credibility" in facts
    # and it is genuinely more than the speaker-facing anchor
    assert len(facts) > len(cron_routes._build_recipe_context(recipe))


def test_judge_recipe_facts_empty_without_a_recipe():
    assert cron_routes._build_judge_recipe_facts(None) == ""
    assert cron_routes._build_judge_recipe_facts({}) == ""
    assert cron_routes._build_judge_recipe_facts({"category": "Sweet"}) == ""


def test_judge_recipe_facts_caps_a_very_long_method():
    recipe = {
        "title": "Long",
        "category": "Sweet",
        "instructions": [f"Step {i} with a good deal of explanatory text in it." for i in range(200)],
    }
    facts = cron_routes._build_judge_recipe_facts(recipe)
    assert "omitted for length" in facts, "an over-budget method must say so"
    assert len(facts) < cron_routes.JUDGE_METHOD_MAX + 900
    # both ends survive: the cut comes out of the middle, not the tail
    assert "1. Step 0 " in facts
    assert "200. Step 199 " in facts


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


def test_judge_method_budget_fits_the_longest_real_recipe():
    """Sized against stored recipes, not guessed (Codex audit).

    W35-W38 methods run 2,486-4,956 chars. A 2,000 cap truncated W38 at step 16
    of 36 and resolved its lamination claim only because "roll the dough up into
    a log" happened to sit at step 16 - every baking, unmolding and glazing step
    was cut.
    """
    assert cron_routes.JUDGE_METHOD_MAX >= 5000


def test_fit_method_drops_the_middle_not_the_tail():
    """Late steps carry baking/unmolding/finishing - the claims a judge checks."""
    steps = [f"Step {i} " + ("x" * 120) for i in range(1, 61)]
    out = cron_routes._fit_method(steps, 2000)
    assert len(out) <= 2000
    assert out.startswith("1. Step 1"), "the opening steps must survive"
    assert "60. Step 60" in out, "the closing steps must survive"
    assert "omitted for length" in out, "the cut must be stated, not silent"


def test_fit_method_returns_everything_when_it_fits():
    steps = ["Mix the batter.", "Bake for 20 minutes.", "Cool and serve."]
    out = cron_routes._fit_method(steps, 8000)
    assert out == "1. Mix the batter. 2. Bake for 20 minutes. 3. Cool and serve."
    assert "omitted" not in out


def test_judge_facts_keep_ingredient_amounts_and_notes():
    """Names alone made different recipes look identical to the judge (Codex).

    "1 tbsp melted butter" and "2 cups cold, cubed butter" produced the same
    facts whenever the method did not repeat the detail - so a ratio or
    technique claim was unverifiable for exactly the recipes where it matters.
    """
    melted = {
        "title": "A", "category": "Sweet", "instructions": ["Bake."],
        "ingredients": [{"amount": "1 tbsp", "item": "butter", "notes": "melted"}],
    }
    cubed = {
        "title": "A", "category": "Sweet", "instructions": ["Bake."],
        "ingredients": [{"amount": "2 cups", "item": "butter", "notes": "cold, cubed"}],
    }
    a = cron_routes._build_judge_recipe_facts(melted)
    b = cron_routes._build_judge_recipe_facts(cubed)
    assert "1 tbsp butter (melted)" in a
    assert "2 cups butter (cold, cubed)" in b
    assert a != b, "two different recipes must not produce identical judge facts"


def test_judge_facts_handle_plain_string_ingredients():
    recipe = {"title": "A", "category": "Sweet", "ingredients": ["2 eggs", "flour"], "instructions": ["Bake."]}
    facts = cron_routes._build_judge_recipe_facts(recipe)
    assert "2 eggs" in facts and "flour" in facts


def test_judge_prompt_actually_receives_the_recipe_facts():
    """Wiring, not construction (Codex): the facts must reach the real prompt.

    The other judge-facts tests only prove the string is BUILT correctly. They
    would all stay green if the recipe_facts argument stopped being threaded
    into _judge_dialogue's prompt, which is the regression that matters.
    """
    captured: dict[str, str] = {}

    def fake_generate(prompt, system_prompt, **_kwargs):
        captured["prompt"] = prompt
        return json.dumps({"scores": {}, "verdict": "PASS", "weakest": [], "reason": "ok"})

    facts = cron_routes._build_judge_recipe_facts({
        "title": "Spiral Bites",
        "category": "Sweet",
        "ingredients": [{"amount": "4 tbsp", "item": "butter", "notes": "very soft"}],
        "instructions": ["Roll the dough up tightly into a log and cut into 12 slices."],
    })
    dialogue = [{"character": "Margaret", "message": "The dough is too loose."}]

    with patch.object(cron_routes, "generate_judge_response", side_effect=fake_generate):
        cron_routes._judge_dialogue(
            "Spiral Bites", "tuesday", dialogue, {"episode_id": "2026-W38", "stages": {}},
            recipe_context="This week's recipe: Spiral Bites (sweet).",
            recipe_facts=facts,
        )

    assert "RECIPE GROUND TRUTH" in captured["prompt"]
    assert "roll the dough up tightly into a log" in captured["prompt"].lower()
    assert "4 tbsp butter (very soft)" in captured["prompt"]


def test_generate_and_judge_passes_facts_not_just_context(monkeypatch):
    """End to end: _generate_and_judge_dialogue must build AND forward the facts."""
    seen: dict[str, object] = {}

    def fake_judge(concept, stage, dialogue, episode, recipe_context=None, recipe_facts=None):
        seen["context"] = recipe_context
        seen["facts"] = recipe_facts
        return True, "PASS"

    monkeypatch.setattr(
        cron_routes, "_generate_dialogue",
        lambda *a, **k: [{"character": "Margaret", "message": "Fine."}],
    )
    monkeypatch.setattr(cron_routes, "_judge_dialogue", fake_judge)
    monkeypatch.setattr(cron_routes, "_score_dialogue_qa", lambda *a, **k: None)

    recipe = {
        "title": "Spiral Bites", "category": "Sweet",
        "description": "Soft dough rolled once.",
        "ingredients": [{"amount": "1 cup", "item": "flour"}],
        "instructions": ["Roll into a log."],
    }
    cron_routes._generate_and_judge_dialogue(
        "tuesday", "Spiral Bites", {"episode_id": "2026-W38", "stages": {}, "events": []},
        recipe_data=recipe,
    )

    assert seen["context"], "speaker anchor must still be passed"
    assert seen["facts"], "judge facts must be passed"
    assert "RECIPE GROUND TRUTH" in str(seen["facts"])
    assert "Roll into a log." in str(seen["facts"])


def test_fit_method_keeps_the_tail_even_when_one_step_is_enormous():
    """A single oversized step must not starve the other end (Codex)."""
    out = cron_routes._fit_method(["x" * 8000, "Cool and serve."], 2000)
    assert "Cool and serve." in out
    assert "omitted for length" in out


def test_fit_method_marker_tells_the_judge_not_to_infer_contradiction():
    out = cron_routes._fit_method([f"Step {i} " + "y" * 200 for i in range(1, 40)], 1200)
    assert "NOT" in out and "contradiction" in out
