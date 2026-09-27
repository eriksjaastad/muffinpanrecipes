"""Director knob tests (#7679, absorbs #7677).

Every test is network-free: model calls are monkeypatched. The roll/subset
properties, prompt contents, repeat rejection, fallback and DirectorError
behaviour are exercised directly against backend.utils.director; the
simulate_dialogue_week and conversation_lab integration tests monkeypatch
the model boundary too.
"""

from __future__ import annotations

import json

import pytest

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw
from backend.utils.director import (
    CATEGORIES,
    INTENSITY_SCALE,
    DirectorError,
    Direction,
    Roll,
    direct_day,
    roll_day,
)

CHARACTERS = [
    "Margaret Chen",
    "Stephanie 'Steph' Whitmore",
    "Julian Torres",
    "Marcus Reid",
    "Devon Park",
    "Ria Castillo",
]


def _roll(character: str, category: str, has_something: bool, u: float = 0.1) -> Roll:
    return Roll(character=character, category=category, u=u, has_something=has_something)


# ---------------------------------------------------------------------------
# roll_day: determinism and the common-random-number subset property
# ---------------------------------------------------------------------------


def test_roll_day_is_deterministic() -> None:
    a = roll_day("Jalapeno Corn Dog Bites", CHARACTERS, 0.5, 42, day="monday")
    b = roll_day("Jalapeno Corn Dog Bites", CHARACTERS, 0.5, 42, day="monday")
    assert [(r.character, r.category, r.u, r.has_something) for r in a] == [
        (r.character, r.category, r.u, r.has_something) for r in b
    ]


def test_roll_day_varies_with_seed_key_day_and_rng_seed() -> None:
    base = roll_day("Jalapeno Corn Dog Bites", CHARACTERS, 0.5, 42, day="monday")
    other_day = roll_day("Jalapeno Corn Dog Bites", CHARACTERS, 0.5, 42, day="tuesday")
    other_concept = roll_day("Brown Butter Pecan Tassies", CHARACTERS, 0.5, 42, day="monday")
    other_seed = roll_day("Jalapeno Corn Dog Bites", CHARACTERS, 0.5, 43, day="monday")
    assert [(r.category, r.u) for r in base] != [(r.category, r.u) for r in other_day]
    assert [(r.category, r.u) for r in base] != [(r.category, r.u) for r in other_concept]
    assert [(r.category, r.u) for r in base] != [(r.category, r.u) for r in other_seed]


def test_roll_day_subset_property_across_probabilities() -> None:
    """For a fixed seed the 25% rolls must be a subset of the 50% rolls.

    The draws happen in a fixed order before the threshold is applied, so the
    categories and u values are identical across probabilities.
    """
    low = roll_day("Jalapeno Corn Dog Bites", CHARACTERS, 0.25, 7, day="monday")
    high = roll_day("Jalapeno Corn Dog Bites", CHARACTERS, 0.5, 7, day="monday")
    for low_roll, high_roll in zip(low, high):
        assert low_roll.character == high_roll.character
        assert low_roll.category == high_roll.category
        assert low_roll.u == high_roll.u
        if low_roll.has_something:
            assert high_roll.has_something
    assert sum(r.has_something for r in low) <= sum(r.has_something for r in high)


def test_roll_day_probability_bounds() -> None:
    none = roll_day("X", CHARACTERS, 0.0, 7, day="monday")
    all_ = roll_day("X", CHARACTERS, 1.0, 7, day="monday")
    mid = roll_day("X", CHARACTERS, 0.5, 7, day="monday")
    assert all(not r.has_something for r in none)
    assert all(r.has_something for r in all_)
    for n, m in zip(none, mid):
        assert n.category == m.category and n.u == m.u


def test_roll_day_categories_all_valid() -> None:
    rolls = roll_day("X", CHARACTERS, 0.5, 7, day="monday")
    assert all(r.category in CATEGORIES for r in rolls)


# ---------------------------------------------------------------------------
# direct_day: prompt contents, parsing, repeat rejection, fallback
# ---------------------------------------------------------------------------


def test_direct_day_prompt_carries_intensity_category_and_history(monkeypatch) -> None:
    captured: dict = {}

    def fake_generate_response(*, prompt, system_prompt, model, temperature):
        captured.update(prompt=prompt, system_prompt=system_prompt, model=model,
                        temperature=temperature)
        return json.dumps({
            "scene": "The test kitchen smells like sugar.",
            "characters": {"Margaret Chen": "She slept badly."},
        })

    monkeypatch.setattr("backend.utils.director.generate_response", fake_generate_response)
    history = [{
        "day": "monday",
        "character": "Margaret Chen",
        "category": "weather",
        "summary": "a storm kept her awake",
        "scene": "rain hammered the skylight",
    }]
    rolls = [
        _roll("Margaret Chen", "personal", True),
        _roll("Marcus Reid", "logistics", False),
    ]

    direction = direct_day(
        day="tuesday",
        concept="Jalapeno Corn Dog Bites",
        objective="Nail down the recipe - ratios, technique, substitutions",
        rolls=rolls,
        intensity=3,
        history=history,
        model="anthropic/claude-haiku-4-5-20251001",
    )

    assert direction.scene == "The test kitchen smells like sugar."
    assert direction.characters == {"Margaret Chen": "She slept badly."}
    assert captured["model"] == "anthropic/claude-haiku-4-5-20251001"
    assert captured["temperature"] == 0.7
    assert INTENSITY_SCALE[3] in captured["prompt"]
    assert "Nail down the recipe" in captured["prompt"]
    assert "Jalapeno Corn Dog Bites" in captured["prompt"]
    # Chosen characters are listed with their categories...
    assert "Margaret Chen: personal" in captured["prompt"]
    # ...and characters whose roll has_something=False are not listed.
    assert "Marcus Reid: logistics" not in captured["prompt"]
    assert "a storm kept her awake" in captured["prompt"]
    assert "never repeat" in captured["prompt"].lower()


def test_direct_day_ignores_lines_for_unchosen_characters(monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.utils.director.generate_response",
        lambda **kwargs: json.dumps({
            "scene": "The room is quiet.",
            "characters": {"Margaret Chen": "Her oven is being serviced.", "Marcus Reid": "He missed his train."},
        }),
    )
    direction = direct_day(
        day="monday",
        concept="X",
        objective="Pick it",
        rolls=[_roll("Margaret Chen", "personal", True)],
        intensity=2,
        history=[],
        model="m",
    )
    assert direction.characters == {"Margaret Chen": "Her oven is being serviced."}


@pytest.mark.parametrize("raw", [
    "not json at all",
    "[1, 2, 3]",
    '{"scene": "only a scene"}',
    '{"scene": "", "characters": {}}',
    '{"scene": "ok", "characters": []}',
    '{"scene": "ok", "characters": {"Margaret Chen": 3}}',
])
def test_direct_day_malformed_json_raises(monkeypatch, raw) -> None:
    monkeypatch.setattr("backend.utils.director.generate_response", lambda **kwargs: raw)
    with pytest.raises(DirectorError):
        direct_day(
            day="monday",
            concept="X",
            objective="Pick it",
            rolls=[_roll("Margaret Chen", "personal", True)],
            intensity=3,
            history=[],
            model="m",
        )


def test_direct_day_model_failure_raises(monkeypatch) -> None:
    def boom(**kwargs):
        raise RuntimeError("API down")

    monkeypatch.setattr("backend.utils.director.generate_response", boom)
    with pytest.raises(DirectorError, match="API down"):
        direct_day(
            day="monday",
            concept="X",
            objective="Pick it",
            rolls=[],
            intensity=3,
            history=[],
            model="m",
        )


def test_direct_day_invalid_intensity_raises() -> None:
    with pytest.raises(DirectorError, match="intensity"):
        direct_day(
            day="monday",
            concept="X",
            objective="Pick it",
            rolls=[],
            intensity=9,
            history=[],
            model="m",
        )


def test_repeat_category_pair_regenerates_once_then_falls_back(monkeypatch) -> None:
    """A (character, category) pair repeat cannot be fixed by rewriting, so the
    one allowed regeneration still names it and the second rejection falls back."""
    calls: list[str] = []

    def fake_generate_response(*, prompt, system_prompt, model, temperature):
        calls.append(prompt)
        return json.dumps({
            "scene": "The team gathers as usual.",
            "characters": {"Margaret Chen": "She is tired."},
        })

    monkeypatch.setattr("backend.utils.director.generate_response", fake_generate_response)
    history = [{
        "day": "monday",
        "character": "Margaret Chen",
        "category": "weather",
        "summary": "a storm kept her awake",
        "scene": "rain outside",
    }]

    direction = direct_day(
        day="tuesday",
        concept="X",
        objective="Pick it",
        rolls=[_roll("Margaret Chen", "weather", True)],
        intensity=3,
        history=history,
        model="m",
    )

    assert len(calls) == 2
    assert direction.fallback is True
    assert direction.scene == ""
    assert direction.characters == {}
    # The retry prompt names the offending item.
    assert "Margaret Chen" in calls[1]
    assert "weather" in calls[1]
    assert "previous draft repeated the history" in calls[1].lower()


def test_repeat_phrase_regenerates_once_then_succeeds(monkeypatch) -> None:
    """A 4-word phrasing repeat can be fixed by rewriting; the retry is used."""
    calls: list[str] = []

    def fake_generate_response(*, prompt, system_prompt, model, temperature):
        calls.append(prompt)
        if len(calls) == 1:
            return json.dumps({
                "scene": "Rain hammered the skylight again.",
                "characters": {"Margaret Chen": "A storm kept her awake."},
            })
        return json.dumps({
            "scene": "A delivery van blocks the loading dock.",
            "characters": {"Margaret Chen": "She left her lunch on the counter."},
        })

    monkeypatch.setattr("backend.utils.director.generate_response", fake_generate_response)
    history = [{
        "day": "monday",
        "character": "Margaret Chen",
        "category": "weather",
        "summary": "a storm kept her awake",
        "scene": "rain hammered the skylight",
    }]

    direction = direct_day(
        day="tuesday",
        concept="X",
        objective="Pick it",
        # A different category so the pair check passes; only phrasing repeats.
        rolls=[_roll("Margaret Chen", "personal", True)],
        intensity=3,
        history=history,
        model="m",
    )

    assert len(calls) == 2
    assert direction.fallback is False
    assert direction.scene == "A delivery van blocks the loading dock."
    assert direction.characters == {"Margaret Chen": "She left her lunch on the counter."}
    assert "previous draft repeated the history" in calls[1].lower()


# ---------------------------------------------------------------------------
# simulate_dialogue_week: default off, prompt identity, direction injection
# ---------------------------------------------------------------------------


def _capture_turn_prompt(monkeypatch, *, persona_name, day="monday", stage="brainstorm",
                         day_turn=1, prompt_style="scene", recent_lines=None,
                         direction=None) -> str:
    captured: dict = {}
    monkeypatch.setattr(sdw, "build_system_prompt", lambda persona: "SYS")

    def fake_generate_response(*, prompt, system_prompt, model, temperature):
        captured["prompt"] = prompt
        return "Fine."

    monkeypatch.setattr(sdw, "generate_response", fake_generate_response)
    sdw.generate_turn(
        persona={"name": persona_name},
        concept="Jalapeno Corn Dog Bites",
        day=day,
        stage=stage,
        deadline="5:00 PM local",
        recent_lines=recent_lines or [],
        event=None,
        model="stub",
        mode="llm",
        prompt_style=prompt_style,
        day_turn=day_turn,
        direction=direction,
    )
    return captured["prompt"]


def _expected_scene_opener_prompt() -> str:
    day = "monday"
    stage = "brainstorm"
    concept = "Jalapeno Corn Dog Bites"
    name = "Margaret Chen"
    goal_line = f"The team needs to: {sdw.DAY_MEETING_GOAL[day]['objective']}\n"
    char_goal = sdw.CHARACTER_DAY_GOALS.get(day, {}).get(name, "")
    char_goal_line = f"Your goal today: {char_goal}\n"
    return (
        f"Episode concept: {concept}\n"
        f"Day: {day.title()} ({stage})\n"
        f"Scene context: {sdw.DAY_STAGE_DIRECTIONS[day]}\n"
        f"Story arc: {sdw._build_dynamic_arc(day, concept)}\n"
        f"{goal_line}"
        f"{char_goal_line}"
        "Do NOT mention specific clock times.\n"
        "Injected event: none\n"
        "Recent chat:\n(no prior messages)\n\n"
        "IMPORTANT: You are opening this conversation. Nobody has spoken yet today.\n"
        "You just arrived - walked in, logged on, opened the chat. "
        "Your first words should reflect that arrival moment in YOUR voice. "
        "Then introduce the day's topic.\n"
        f"Arrival context: {sdw._DAY_OPENER_CONTEXT[day]}\n"
        "What do you say next?"
    )


def test_director_default_is_off() -> None:
    assert sdw.DIRECTOR == {
        "enabled": False,
        "probability": 0.5,
        "intensity": 3,
        "rng_seed": 0,
        "no_repeat_window": 14,
    }


def test_disabled_director_leaves_generate_turn_prompt_byte_identical(monkeypatch) -> None:
    captured = _capture_turn_prompt(monkeypatch, persona_name="Margaret Chen")
    assert captured == _expected_scene_opener_prompt()
    assert "Scene today:" not in captured
    assert "Before this meeting:" not in captured


def test_fallback_direction_leaves_prompt_identical_to_plain_scene(monkeypatch) -> None:
    captured = _capture_turn_prompt(
        monkeypatch,
        persona_name="Margaret Chen",
        direction=Direction(scene="", characters={}, fallback=True),
    )
    assert captured == _expected_scene_opener_prompt()


def test_opener_scene_is_replaced_by_direction_scene(monkeypatch) -> None:
    direction = Direction(scene="The ovens are cold and the lights are off.", characters={})
    captured = _capture_turn_prompt(monkeypatch, persona_name="Margaret Chen", direction=direction)
    assert "Scene context: The ovens are cold and the lights are off.\n" in captured
    assert sdw.DAY_STAGE_DIRECTIONS["monday"] not in captured
    assert "Scene today:" not in captured


@pytest.mark.parametrize("prompt_style", ["scene", "full"])
def test_later_speakers_see_the_direction_scene(monkeypatch, prompt_style) -> None:
    direction = Direction(scene="The ovens are cold and the lights are off.", characters={})
    captured = _capture_turn_prompt(
        monkeypatch,
        persona_name="Margaret Chen",
        day_turn=2,
        prompt_style=prompt_style,
        recent_lines=["Steph: Morning everyone."],
        direction=direction,
    )
    assert "Scene today: The ovens are cold and the lights are off.\n" in captured


@pytest.mark.parametrize("prompt_style", ["scene", "full"])
def test_private_line_appears_only_in_its_owners_prompt(monkeypatch, prompt_style) -> None:
    direction = Direction(
        scene="The ovens are cold and the lights are off.",
        characters={
            "Margaret Chen": "Her oven is being serviced.",
            "Marcus Reid": "His train was cancelled.",
        },
    )
    margaret_prompt = _capture_turn_prompt(
        monkeypatch,
        persona_name="Margaret Chen",
        day_turn=2,
        prompt_style=prompt_style,
        recent_lines=["Steph: Morning everyone."],
        direction=direction,
    )
    marcus_prompt = _capture_turn_prompt(
        monkeypatch,
        persona_name="Marcus Reid",
        day_turn=2,
        prompt_style=prompt_style,
        recent_lines=["Steph: Morning everyone."],
        direction=direction,
    )
    steph_prompt = _capture_turn_prompt(
        monkeypatch,
        persona_name="Stephanie 'Steph' Whitmore",
        day_turn=2,
        prompt_style=prompt_style,
        recent_lines=["Steph: Morning everyone."],
        direction=direction,
    )

    assert "Before this meeting: Her oven is being serviced.\n" in margaret_prompt
    assert "Before this meeting: His train was cancelled.\n" not in margaret_prompt
    assert "Before this meeting: His train was cancelled.\n" in marcus_prompt
    assert "Before this meeting: Her oven is being serviced.\n" not in marcus_prompt
    assert "Before this meeting:" not in steph_prompt


def test_run_simulation_calls_director_once_per_day_and_passes_direction(monkeypatch) -> None:
    calls: dict = {"rolls": [], "direct": [], "turns": []}
    sdw.DIRECTOR_LOG[:] = [{"junk": True}]

    def fake_roll_day(seed_key, characters, probability, rng_seed, day=""):
        calls["rolls"].append({
            "seed_key": seed_key,
            "characters": list(characters),
            "probability": probability,
            "rng_seed": rng_seed,
            "day": day,
        })
        return [Roll(character=characters[0], category="personal", u=0.1, has_something=True)]

    def fake_direct_day(day, concept, objective, rolls, intensity, history, model,
                        no_repeat_window=14):
        calls["direct"].append({
            "day": day,
            "concept": concept,
            "objective": objective,
            "rolls": rolls,
            "intensity": intensity,
            "history": list(history),
            "model": model,
            "no_repeat_window": no_repeat_window,
        })
        return Direction(scene=f"Scene for {day}.", characters={rolls[0].character: f"Line for {day}."})

    def fake_generate_turn(**kwargs):
        calls["turns"].append({"direction": kwargs.get("direction"), "persona": kwargs["persona"]["name"]})
        return "Line."

    monkeypatch.setattr(sdw, "DIRECTOR", {
        "enabled": True,
        "probability": 0.5,
        "intensity": 3,
        "rng_seed": 0,
        "no_repeat_window": 14,
    })
    monkeypatch.setattr(sdw, "roll_day", fake_roll_day)
    monkeypatch.setattr(sdw, "direct_day", fake_direct_day)
    monkeypatch.setattr(sdw, "generate_turn", fake_generate_turn)

    sdw.run_simulation(
        concept="Jalapeno Corn Dog Bites",
        default_model="stub",
        run_index=0,
        stage_only="monday",
        injected_event=None,
        ticks_per_day=5,
        mode="llm",
        prompt_style="scene",
        character_models=None,
    )

    assert len(calls["rolls"]) == 1
    assert calls["rolls"][0]["seed_key"] == "Jalapeno Corn Dog Bites"
    assert calls["rolls"][0]["day"] == "monday"
    assert len(calls["direct"]) == 1
    assert calls["direct"][0]["model"] == "stub"
    assert calls["direct"][0]["history"] == []
    assert calls["direct"][0]["no_repeat_window"] == 14
    assert len(calls["turns"]) == 5
    assert all(turn["direction"] is not None for turn in calls["turns"])
    assert all(turn["direction"].scene == "Scene for monday." for turn in calls["turns"])
    assert sdw.DIRECTOR_LOG == [{
        "day": "monday",
        "rolls": [{"character": "Margaret Chen", "category": "personal", "u": 0.1,
                   "has_something": True}],
        "direction": {"scene": "Scene for monday.",
                      "characters": {"Margaret Chen": "Line for monday."}},
        "fallback": False,
    }]


def test_run_simulation_director_history_accumulates_across_days(monkeypatch) -> None:
    history_lengths: list[int] = []

    def fake_roll_day(seed_key, characters, probability, rng_seed, day=""):
        return [Roll(character=characters[0], category="personal", u=0.1, has_something=True)]

    def fake_direct_day(day, concept, objective, rolls, intensity, history, model,
                        no_repeat_window=14):
        history_lengths.append(len(history))
        return Direction(scene=f"Scene for {day}.", characters={rolls[0].character: f"Line for {day}."})

    def fake_generate_turn(**kwargs):
        return "Line."

    monkeypatch.setattr(sdw, "DIRECTOR", {
        "enabled": True,
        "probability": 0.5,
        "intensity": 3,
        "rng_seed": 0,
        "no_repeat_window": 14,
    })
    monkeypatch.setattr(sdw, "roll_day", fake_roll_day)
    monkeypatch.setattr(sdw, "direct_day", fake_direct_day)
    monkeypatch.setattr(sdw, "generate_turn", fake_generate_turn)
    monkeypatch.setattr(sdw, "_generate_day_highlights", lambda **kwargs: "Summary")
    monkeypatch.setattr(sdw, "_generate_episode_memories", lambda *a, **k: None)

    sdw.run_simulation(
        concept="Jalapeno Corn Dog Bites",
        default_model="stub",
        run_index=0,
        stage_only=None,
        injected_event=None,
        ticks_per_day=5,
        mode="llm",
        prompt_style="scene",
        character_models=None,
    )

    assert history_lengths == [0, 1, 2, 3, 4, 5, 6]
    assert len(sdw.DIRECTOR_LOG) == 7


def test_run_simulation_skips_director_in_template_mode(monkeypatch) -> None:
    def fail_roll_day(*args, **kwargs):
        raise AssertionError("roll_day must not be called in template mode")

    def fail_direct_day(*args, **kwargs):
        raise AssertionError("direct_day must not be called in template mode")

    monkeypatch.setattr(sdw, "DIRECTOR", {
        "enabled": True,
        "probability": 0.5,
        "intensity": 3,
        "rng_seed": 0,
        "no_repeat_window": 14,
    })
    monkeypatch.setattr(sdw, "roll_day", fail_roll_day)
    monkeypatch.setattr(sdw, "direct_day", fail_direct_day)

    sdw.run_simulation(
        concept="Jalapeno Corn Dog Bites",
        default_model="template",
        run_index=0,
        stage_only="monday",
        injected_event=None,
        ticks_per_day=5,
        mode="template",
        prompt_style="scene",
        character_models=None,
    )
    assert sdw.DIRECTOR_LOG == []


# ---------------------------------------------------------------------------
# conversation_lab: DIRECTOR shape validation and DIRECTOR_LOG reporting
# ---------------------------------------------------------------------------


def test_director_is_an_allowed_variant_attr() -> None:
    assert "DIRECTOR" in cl.ALLOWED_VARIANT_ATTRS


def test_lab_accepts_valid_director_shape() -> None:
    cl.validate_variant(sdw, {
        "DIRECTOR": {
            "enabled": True,
            "probability": 0.5,
            "intensity": 3,
            "rng_seed": 0,
            "no_repeat_window": 14,
        }
    })


@pytest.mark.parametrize("bad", [
    {"enabled": "yes", "probability": 0.5, "intensity": 3, "rng_seed": 0, "no_repeat_window": 14},
    {"enabled": True, "probability": 1.5, "intensity": 3, "rng_seed": 0, "no_repeat_window": 14},
    {"enabled": True, "probability": True, "intensity": 3, "rng_seed": 0, "no_repeat_window": 14},
    {"enabled": True, "probability": 0.5, "intensity": 0, "rng_seed": 0, "no_repeat_window": 14},
    {"enabled": True, "probability": 0.5, "intensity": 6, "rng_seed": 0, "no_repeat_window": 14},
    {"enabled": True, "probability": 0.5, "intensity": 3.0, "rng_seed": 0, "no_repeat_window": 14},
    {"enabled": True, "probability": 0.5, "intensity": 3, "rng_seed": 1.5, "no_repeat_window": 14},
    {"enabled": True, "probability": 0.5, "intensity": 3, "rng_seed": 0, "no_repeat_window": -1},
    {"enabled": True, "probability": 0.5, "intensity": 3, "rng_seed": 0, "no_repeat_window": True},
    {"enabled": True, "probability": 0.5, "intensity": 3, "rng_seed": 0, "no_repeat_window": 14, "extra": 1},
    {"enabled": True, "probability": 0.5, "intensity": 3, "rng_seed": 0},
])
def test_lab_rejects_malformed_director_shapes(bad) -> None:
    with pytest.raises(cl.ConversationLabError):
        cl.validate_variant(sdw, {"DIRECTOR": bad})


def _fake_messages(tag: str) -> list[dict]:
    return [
        {"day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
         "message": f"{tag} line {i}", "timestamp": "", "model": "test", "attachments": []}
        for i in range(2)
    ]


def test_ab_result_carries_director_logs_next_to_stop_check_logs(tmp_path, monkeypatch) -> None:
    def fake_run_simulation(*, default_model, **kwargs):
        arm = "variant" if sdw._SHARED_CHARACTER_RULES == "VARIANT_RULES" else "control"
        sdw.STOP_CHECK_LOG[:] = [{"day": "monday", "tick": 2, "provider": "haiku"}]
        sdw.DIRECTOR_LOG[:] = [{
            "day": "monday",
            "rolls": [{"character": "Margaret Chen", "category": "personal",
                       "u": 0.1, "has_something": True}],
            "direction": {"scene": f"Scene for {arm}.", "characters": {"Margaret Chen": "Line."}},
            "fallback": False,
        }]
        return {"messages": _fake_messages(arm)}

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)

    variant_path = tmp_path / "variant.json"
    variant_path.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT_RULES"}))
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    pair = report["pairs"][0]
    assert pair["control_stop_check_log"] == [{"day": "monday", "tick": 2, "provider": "haiku"}]
    assert pair["variant_stop_check_log"] == [{"day": "monday", "tick": 2, "provider": "haiku"}]
    assert pair["control_director_log"][0]["direction"]["scene"] == "Scene for control."
    assert pair["variant_director_log"][0]["direction"]["scene"] == "Scene for variant."
    assert pair["control_director_log"][0]["rolls"][0]["character"] == "Margaret Chen"
    assert pair["variant_director_log"][0]["rolls"][0]["category"] == "personal"


def test_every_category_has_a_meaning():
    """The dice pick a bare label; the director must be told what it means."""
    from backend.utils.director import CATEGORIES, CATEGORY_MEANINGS
    assert set(CATEGORIES) == set(CATEGORY_MEANINGS)
    assert all(CATEGORY_MEANINGS[c].strip() for c in CATEGORIES)
