"""WORD_CAPS knob (#7705 follow-on): caps-off prompts, guard suppression,
cache isolation, and lab validation.

Every test here is network-free: build_system_prompt reads only committed
local data, and the conversation-lab run is --dry-run with run_simulation
monkeypatched (no judge, no paid calls).
"""

from __future__ import annotations

import hashlib
import json
import re

import pytest

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw

# SHA256 of build_system_prompt output with WORD_CAPS=True (the production
# default) for every character, captured 2026-09-27 on the REWRITE_GUARDS /
# REWRITE_LOG tree this work builds on. The default arm must stay byte-identical.
#
# Ria Castillo's hash was recaptured 2026-09-30 (#6968): her memory.json is
# genuinely empty ({"episodes": []}), and before #6968 that made
# build_system_prompt render the "THIS IS YOUR FIRST WEEK ON THE JOB" block
# for her on every run — the exact bug #6968 fixes (Ria must never be told
# she is new after her real first episode). The production default now
# renders the truthful "known coworker, recent details unspecified"
# fallback for empty/unavailable memory instead.
#
# The other 5 characters' hashes were recaptured 2026-09-30 (#6968 review
# round 2, finding 4): each legacy backend/data/characters/*/memory.json
# seed file carries THREE entries that all share "week": "2026-W11" (the
# old writer appended and truncated without deduping by week). Before this
# fix, _load_memories took the raw last 2 list entries and showed them as
# if they were 2 different weeks; storage.order_valid_episodes_by_week now
# dedupes by week first, so only ONE "2026-W11" entry survives per
# character and the memory block shows 1 bullet instead of 2. Ria's file
# has no entries at all, so she was never affected by this dedup.
_GOLDEN_PROMPT_SHA256 = {
    "Margaret Chen": "1c4fdd823760c7edd038d9cd10d40f6d0f43c23c26eb9c3cad21549433f81739",
    "Stephanie 'Steph' Whitmore": "89b0078f2c79a2dfd4aa865456de07dbd1a18427892069484dda07a06489d201",
    "Julian Torres": "bfa267bee03cdec65b32923400f75d475257f6fe403195c0171d9dfa0c79e744",
    "Marcus Reid": "fb4a1998107c77f41c0e8ce46148f2b89964bbe2a7301e59017a4de6a8739c64",
    "Devon Park": "b5ab19241467875b1402e85d4dd64d8bb4ab3ebc0f3d848015d40d7bfb2391cd",
    "Ria Castillo": "e2f66f564f7ac94921c1b264024a5f7f438035cb0277092ead289c3f10104c0b",
}

# Distinctive personality text that must survive the caps-off transform, per
# character - proves the transform removes only the numeric constraints.
_PERSONALITY_MARKERS = {
    "Margaret Chen": ["short, clipped sentences", "Dry humor slips out sideways"],
    "Stephanie 'Steph' Whitmore": ["hedges DIFFERENTLY every time", "terrified of Margaret"],
    "Julian Torres": ["art-school vocabulary", "aesthetic terms"],
    "Marcus Reid": ["'whom' in Slack", "food-history tangent"],
    "Devon Park": ["efficient and understated", "no-small-talk policy"],
    "Ria Castillo": ["platform-speak", "thinks visually and temporally"],
}


def _persona(name: str = "Margaret Chen") -> dict:
    return {
        "name": name,
        "role": "Head Recipe Developer",
        "communication_style": {"signature_phrases": ["Right."], "verbosity": "low"},
        "internal_contradictions": [],
        "relationships": {},
        "triggers": [],
    }


def _generate_once(monkeypatch, word_caps: bool, first_draft: str, recent_lines: list[str]):
    monkeypatch.setattr(sdw, "WORD_CAPS", word_caps)
    monkeypatch.setattr(sdw, "REWRITE_LOG", [])
    prompts: list[str] = []
    replies = iter([first_draft, "Fixed short line."])

    def fake_generate(prompt, system_prompt=None, model=None, temperature=None, **_kw):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(sdw, "generate_response", fake_generate)
    monkeypatch.setattr(sdw, "_guard_cot_leak", lambda m, **_kw: m)
    monkeypatch.setattr(sdw, "build_system_prompt", lambda persona: "SYS")
    sdw.generate_turn(
        persona=_persona("Margaret Chen"),
        concept="Spiral Bites",
        day="wednesday",
        stage="photography",
        deadline="5 pm",
        recent_lines=recent_lines,
        event=None,
        model="test-model",
        mode="openai",
        prompt_style="scene",
        day_turn=4,
        is_last_turn=False,
    )
    return prompts, list(sdw.REWRITE_LOG)


def test_word_caps_defaults_to_true():
    assert sdw.WORD_CAPS is True


def test_default_prompt_unchanged_for_every_character(monkeypatch):
    """WORD_CAPS=True renders every prompt byte-identical to the captured
    production default."""
    monkeypatch.setattr(sdw, "WORD_CAPS", True)
    sdw._system_prompt_cache.clear()
    for name, persona in sdw.load_personas().items():
        prompt = sdw.build_system_prompt(persona)
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        assert digest == _GOLDEN_PROMPT_SHA256[name], name


def test_caps_off_keeps_personality_and_removes_numeric_caps(monkeypatch):
    """WORD_CAPS=False strips every numeric length constraint while keeping
    the personality, style, and relationship text."""
    monkeypatch.setattr(sdw, "WORD_CAPS", False)
    sdw._system_prompt_cache.clear()
    for name, persona in sdw.load_personas().items():
        prompt = sdw.build_system_prompt(persona)
        for marker in _PERSONALITY_MARKERS[name]:
            assert marker in prompt, (name, marker)
        # No remaining numeric cap phrase in the rendered prompt.
        for pattern in sdw.WORD_CAP_PATTERNS:
            assert re.search(pattern, prompt, re.IGNORECASE) is None, (name, pattern)
        # The transformed guide has no digit + "words"/"sentences" left at all.
        stripped_guide = sdw._strip_word_caps(sdw._CHARACTER_VOICE_GUIDES[name])
        assert re.search(
            r"\d+(?:\s*-\s*\d+)?\s+(?:words?|sentences?)",
            stripped_guide,
            re.IGNORECASE,
        ) is None, name
    stripped_rules = sdw._strip_word_caps(sdw._SHARED_CHARACTER_RULES)
    assert re.search(
        r"\d+(?:\s*-\s*\d+)?\s+(?:words?|sentences?)",
        stripped_rules,
        re.IGNORECASE,
    ) is None


def test_caps_on_over_budget_draft_still_rewrites(monkeypatch):
    """Sanity anchor: with WORD_CAPS=True the word_budget fault fires as before."""
    draft = " ".join(["word"] * 33)
    prompts, log = _generate_once(monkeypatch, True, draft, ["Julian: Plain opening line here."])
    assert len(prompts) == 2
    assert "MAXIMUM is 15" in prompts[1]
    assert log[-1]["faults"] == ["word_budget"]


def test_word_budget_fault_suppressed_when_caps_off(monkeypatch):
    """WORD_CAPS=False treats the word_budget guard as off: no rewrite, no
    fault, and the model is never told a number."""
    draft = " ".join(["word"] * 33)
    prompts, log = _generate_once(monkeypatch, False, draft, ["Julian: Plain opening line here."])
    assert len(prompts) == 1
    assert log == [{
        "day": "wednesday",
        "speaker": "Margaret Chen",
        "faults": [],
        "rewritten": False,
        "draft_words": 33,
        "final_words": 33,
        "draft": draft[:300],
        "cot_retry": False,
    }]


def test_prompt_cache_isolates_capped_and_uncapped_arms(monkeypatch):
    """A capped prompt can never be served to an uncapped arm, or vice versa."""
    persona = sdw.load_personas()["Margaret Chen"]
    sdw._system_prompt_cache.clear()

    monkeypatch.setattr(sdw, "WORD_CAPS", True)
    capped = sdw.build_system_prompt(persona)
    monkeypatch.setattr(sdw, "WORD_CAPS", False)
    uncapped = sdw.build_system_prompt(persona)

    assert capped != uncapped
    assert "MAXIMUM 15 words" in capped
    assert "MAXIMUM 15 words" not in uncapped

    # Switching back returns each arm's own cached prompt.
    monkeypatch.setattr(sdw, "WORD_CAPS", True)
    assert sdw.build_system_prompt(persona) == capped
    monkeypatch.setattr(sdw, "WORD_CAPS", False)
    assert sdw.build_system_prompt(persona) == uncapped


def test_word_caps_is_an_allowed_variant_attr_and_validates():
    assert "WORD_CAPS" in cl.ALLOWED_VARIANT_ATTRS
    cl.validate_variant(sdw, {"WORD_CAPS": False})
    with pytest.raises(cl.ConversationLabError):
        cl.validate_variant(sdw, {"WORD_CAPS": "off"})
    original = sdw.WORD_CAPS
    saved = cl._apply_variant(sdw, {"WORD_CAPS": False})
    try:
        assert sdw.WORD_CAPS is False
    finally:
        cl._restore_variant(sdw, saved)
    assert sdw.WORD_CAPS is original


def test_ab_word_caps_variant_applies_and_report_carries_length_stats(tmp_path, monkeypatch):
    """A {"WORD_CAPS": False} variant arm runs with caps off, restores the
    module afterward, and the ab report carries per-arm mean/median/max
    words-per-line per character."""
    def fake_run_simulation(*, default_model, **kwargs):
        if sdw.WORD_CAPS is False:
            return {"messages": [
                {"day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
                 "message": "one two three", "timestamp": "", "model": "test", "attachments": []},
                {"day": "monday", "stage": "brainstorm", "character": "Devon Park",
                 "message": "one two three four five", "timestamp": "", "model": "test", "attachments": []},
            ]}
        return {"messages": [
            {"day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
             "message": "one two three four", "timestamp": "", "model": "test", "attachments": []},
        ]}

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)

    variant_path = tmp_path / "word_caps_off.json"
    variant_path.write_text(json.dumps({"WORD_CAPS": False}))
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    assert sdw.WORD_CAPS is True
    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())

    assert report["length_stats"]["control"]["Margaret Chen"] == {
        "lines": 1,
        "mean": 4.0,
        "median": 4.0,
        "max": 4,
    }
    assert report["length_stats"]["variant"]["Margaret Chen"] == {
        "lines": 1,
        "mean": 3.0,
        "median": 3.0,
        "max": 3,
    }
    assert report["length_stats"]["variant"]["Devon Park"] == {
        "lines": 1,
        "mean": 5.0,
        "median": 5.0,
        "max": 5,
    }


def test_length_stats_mean_median_max_per_character():
    messages = [
        {"character": "Margaret Chen", "message": "one two three"},
        {"character": "Margaret Chen", "message": "one two three four five"},
        {"character": "Devon Park", "message": "one"},
        {"character": "Devon Park", "message": "one two"},
        {"character": "Devon Park", "message": "one two three four"},
    ]
    stats = cl._length_stats(messages)
    assert stats["Margaret Chen"] == {"lines": 2, "mean": 4.0, "median": 4.0, "max": 5}
    assert stats["Devon Park"] == {
        "lines": 3,
        "mean": round(7 / 3, 3),
        "median": 2.0,
        "max": 4,
    }
