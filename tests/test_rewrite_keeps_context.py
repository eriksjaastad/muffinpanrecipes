"""The repetition rewrite must not discard the turn's own instructions (#7189).

W38 Wednesday failed the judge three times. The decisive line in the verdict was
"Ria's closing question about locking the browning timing goes unanswered,
leaving the day's central arc unresolved" - and the scene did end on a question,
which no later turn could answer because there was no later turn.

The closer directive forbidding exactly that ("Don't introduce new topics or ask
questions") existed the whole time. The repetition rewrite built a fresh prompt
from the history and the offending draft alone, so a final turn that tripped the
repetition guard was regenerated without it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import scripts.simulate_dialogue_week as sdw


def _persona() -> dict:
    return {
        "name": "Ria Castillo",
        "role": "Social Media Manager",
        "communication_style": {"signature_phrases": ["Right."], "verbosity": "low"},
        "internal_contradictions": [],
        "relationships": {},
        "triggers": [],
    }


def _capture_prompts(responses: list[str]) -> tuple[list[str], object]:
    """Return (prompts_seen, fake_generate) feeding `responses` in order."""
    prompts: list[str] = []
    it = iter(responses)

    def fake_generate(prompt, system_prompt=None, model=None, temperature=None, **_kw):
        prompts.append(prompt)
        return next(it)

    return prompts, fake_generate


@pytest.mark.parametrize("is_last", [True, False])
def test_rewrite_prompt_carries_the_original_turn_instructions(is_last):
    repetitive = "That browning window is maybe 90 seconds though."
    prompts, fake = _capture_prompts([repetitive, "Locked. Night, all."])

    with (
        patch.object(sdw, "generate_response", side_effect=fake),
        patch.object(sdw, "_is_repetitive_candidate", return_value=True),
        patch.object(sdw, "_guard_cot_leak", side_effect=lambda m, **_kw: m),
    ):
        sdw.generate_turn(
            persona=_persona(),
            concept="Cinnamon Roll Spiral Bites",
            day="wednesday",
            stage="photography",
            deadline="5 pm",
            recent_lines=["Julian: Uploaded three approaches."],
            event=None,
            model="test-model",
            mode="openai",
            prompt_style="scene",
            day_turn=6,
            is_last_turn=is_last,
            recipe_context="This week's recipe: Cinnamon Roll Spiral Bites (sweet).",
        )

    assert len(prompts) == 2, "the repetitive draft should have triggered one rewrite"
    original, rewrite = prompts
    # The rewrite is built ON TOP of the original, so every directive survives.
    assert original in rewrite, "rewrite discarded the original turn prompt"
    assert "too repetitive" in rewrite
    assert "This week's recipe" in rewrite, "recipe anchor lost in the rewrite"


def test_rewrite_of_a_final_turn_restates_the_no_questions_rule():
    prompts, fake = _capture_prompts(["repetitive draft", "Locked. Night, all."])

    with (
        patch.object(sdw, "generate_response", side_effect=fake),
        patch.object(sdw, "_is_repetitive_candidate", return_value=True),
        patch.object(sdw, "_guard_cot_leak", side_effect=lambda m, **_kw: m),
    ):
        sdw.generate_turn(
            persona=_persona(), concept="X", day="wednesday", stage="photography",
            deadline="5 pm", recent_lines=["Julian: Uploaded three approaches."],
            event=None, model="test-model", mode="openai", prompt_style="scene",
            day_turn=6, is_last_turn=True,
        )

    rewrite = prompts[1]
    assert "LAST-MESSAGE" in rewrite
    assert "do not ask a question" in rewrite.lower()
    # and the original closer directive is still in there too
    assert "LAST message of today's conversation" in rewrite


def test_a_non_final_rewrite_does_not_claim_to_be_the_closer():
    prompts, fake = _capture_prompts(["repetitive draft", "Something new."])

    with (
        patch.object(sdw, "generate_response", side_effect=fake),
        patch.object(sdw, "_is_repetitive_candidate", return_value=True),
        patch.object(sdw, "_guard_cot_leak", side_effect=lambda m, **_kw: m),
    ):
        sdw.generate_turn(
            persona=_persona(), concept="X", day="wednesday", stage="photography",
            deadline="5 pm", recent_lines=["Julian: Uploaded three approaches."],
            event=None, model="test-model", mode="openai", prompt_style="scene",
            day_turn=3, is_last_turn=False,
        )

    assert "LAST-MESSAGE" not in prompts[1]


def test_no_rewrite_means_no_second_call():
    prompts, fake = _capture_prompts(["A perfectly fresh line."])

    with (
        patch.object(sdw, "generate_response", side_effect=fake),
        patch.object(sdw, "_is_repetitive_candidate", return_value=False),
        patch.object(sdw, "_shared_trigram_with_recent", return_value=False),
        patch.object(sdw, "_guard_cot_leak", side_effect=lambda m, **_kw: m),
    ):
        out = sdw.generate_turn(
            persona=_persona(), concept="X", day="wednesday", stage="photography",
            deadline="5 pm", recent_lines=[], event=None, model="test-model",
            mode="openai", prompt_style="scene", day_turn=1, is_last_turn=False,
        )

    assert len(prompts) == 1
    assert out == "A perfectly fresh line."
