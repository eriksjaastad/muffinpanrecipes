"""Hermetic tests for the authored dialogue path."""

import re

import pytest

from scripts import simulate_dialogue_week as sim


def run(**kwargs):
    return sim.run_simulation(
        concept="Custard Cups",
        default_model="stub",
        run_index=1,
        stage_only=kwargs.pop("stage_only", "monday"),
        injected_event="late delivery",
        mode=kwargs.pop("mode", "openai"),
        recipe_context="Lemon custard in pastry",
        recipe_facts="Method: Bake at 500 F.",
        **kwargs,
    )


def test_run_simulation_uses_authored_scene_and_preserves_prompt_context(monkeypatch):
    prompts = []
    scene = '\n'.join([
        'Margaret: "Too wet."',
        'Steph: "More flour?"',
        'Margaret: "No, hotter oven."',
    ] * 2)

    def generate(**kwargs):
        prompts.append(kwargs["prompt"])
        return scene

    monkeypatch.setattr(sim, "generate_response", generate)
    monkeypatch.setattr(
        sim,
        "_load_memories",
        lambda name: [{"concept": "Savory cups", "summary": f"Saved memory for {name}."}],
    )

    result = run(
        initial_highlights=["Earlier: flour delivery still unresolved."],
        retry_note="Nobody answered the skeptic.",
    )

    assert len(prompts) == 1
    assert "Method: Bake at 500 F." in prompts[0]
    assert "late delivery" in prompts[0]
    assert "flour delivery still unresolved" in prompts[0]
    assert "Nobody answered the skeptic." in prompts[0]
    assert "Saved memory for Margaret Chen." in prompts[0]
    assert result["messages"][1]["character"] == "Stephanie 'Steph' Whitmore"
    assert [m["message"] for m in result["messages"]] == result["name_strip_transcript"]
    assert result["metrics"]["message_count"] == 6
    assert result["qa"]["score"] >= 0


def test_authored_output_retries_malformed_scenes_and_fails_after_three(monkeypatch):
    names = sim.participants_for_day("monday")
    valid = '\n'.join(['Margaret: "Too wet."'] * 6)
    replies = iter(["Narrator: The kitchen is quiet.", valid])
    prompts = []
    monkeypatch.setattr(
        sim,
        "generate_response",
        lambda **kwargs: prompts.append(kwargs["prompt"]) or next(replies),
    )

    result = run()

    assert result["messages"]
    assert len(prompts) == 2
    assert "Your previous draft was rejected" in prompts[1]
    with pytest.raises(ValueError, match="off-roster") as failed:
        sim._parse_author_scene("Devon: \"Off roster.\"", names)
    assert failed.value.args

    prompts.clear()
    monkeypatch.setattr(
        sim,
        "generate_response",
        lambda **kwargs: prompts.append(kwargs["prompt"]) or "Narrator: x",
    )
    with pytest.raises(ValueError, match="off-roster") as exhausted:
        run()
    assert len(prompts) == sim._AUTHOR_PARSE_TRIES
    assert exhausted.value.author_invalid_scene == "Narrator: x"


def test_full_authored_week_keeps_saved_memories_and_wednesday_images(monkeypatch):
    prompts = []
    memory_writes = []
    photos = ["one.png", "two.png", "three.png"]

    def generate(**kwargs):
        prompts.append(kwargs["prompt"])
        day = sim.DAY_ORDER[len(prompts) - 1]
        name = sim.participants_for_day(day)[0]
        speaker = "Steph" if name.startswith("Stephanie") else name.split()[0]
        return f'{speaker}: "{day} stays unresolved."'

    monkeypatch.setattr(sim, "generate_response", generate)
    monkeypatch.setattr(
        sim,
        "_load_memories",
        lambda name: [{"concept": "Earlier recipe", "summary": "Remembered saved work."}],
    )
    monkeypatch.setattr(
        sim,
        "_generate_episode_memories",
        lambda messages, concept, *args: memory_writes.append((messages, concept)),
    )

    result = run(
        stage_only=None,
        mode="llm",
        image_paths=photos,
        photography_context={"winner_variant": "crispy edge"},
        initial_highlights=["The custard ratio was settled."],
    )

    assert len(prompts) == len(sim.DAY_ORDER)
    assert "The custard ratio was settled." in prompts[0]
    assert "Remembered saved work." in prompts[0]
    assert "monday stays unresolved" in prompts[1].lower()
    assert "tuesday stays unresolved" in prompts[2].lower()
    assert "crispy edge" in prompts[2]
    assert all(any(photo in message["attachments"] for message in result["messages"])
               for photo in photos)
    assert len(memory_writes) == 1
    saved_messages, saved_concept = memory_writes[0]
    assert saved_concept == "Custard Cups"
    assert {message.day for message in saved_messages} == set(sim.DAY_ORDER)


def test_author_prompt_keeps_persona_markers_and_drops_numeric_caps(monkeypatch):
    names = sim.participants_for_day("monday")
    monkeypatch.setattr(sim, "_load_bio", lambda name: f"Bio of {name}.")
    monkeypatch.setattr(sim, "_load_memories", lambda name: [])
    personas = {name: {"role": "role"} for name in names}

    prompt = sim._author_prompt(
        personas, names, "Custard Cups", "monday", None, None, None, [], [],
    )

    # Distinctive persona lines from the voice guides survive the cap stripping.
    assert "Fragments. Verdicts, not speeches." in prompt
    assert "Her internal war:" in prompt
    # Every numeric word/sentence cap is gone from the authored prompt.
    assert not re.search(r"\d+(?:\s*-\s*\d+)?\s+sentences?", prompt, re.IGNORECASE)
    assert not re.search(r"Average message", prompt, re.IGNORECASE)
    assert not re.search(r"MAXIMUM\s+\d+\s+words", prompt, re.IGNORECASE)
    assert not re.search(r"Be punchy", prompt, re.IGNORECASE)


def test_author_scene_parser_rejects_narration_and_accepts_literal_parentheses():
    names = sim.participants_for_day("monday")
    for invalid in ("Narrator: Everyone sits.", "They sit down.", 'Devon: "Off roster."'):
        with pytest.raises(ValueError):
            sim._parse_author_scene(invalid, names)
    assert sim._parse_author_scene(
        'Margaret: "Bake until set (looks firm at the edge)."', names
    ) == [("Margaret Chen", "Bake until set (looks firm at the edge).")]
