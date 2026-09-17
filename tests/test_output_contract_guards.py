"""Shape and word-budget guards on generated dialogue.

The pre-existing repetition guards police VOCABULARY - _is_repetitive_candidate
is a Jaccard over tokens, _shared_trigram_with_recent is word trigrams. Neither
can see SHAPE, and W38 shipped 21 of 21 lines across monday/tuesday/wednesday
built as "<claim> - <elaboration>": five characters, three days, 100%, every
vocabulary guard green because the words differed every time.

Separately, every character states a MAXIMUM word count in their voice guide and
nothing enforced it, so all five converged on 27-33 words regardless of a
designed 12-to-35 spread.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

import scripts.simulate_dialogue_week as sdw


# --- shape detection ---------------------------------------------------------

@pytest.mark.parametrize(
    "message,expected",
    [
        ("The steam's the problem though - it reads as blur on mobile.", "dash_clause"),
        ("Can we lock the timing before Julian shoots again?", "question"),
        ("Locked. Night, all.", "terse"),
        ("The cardamom should brown before the filling even sets properly here.", "declarative"),
        ("", "empty"),
    ],
)
def test_sentence_shape(message, expected):
    assert sdw._sentence_shape(message) == expected


def test_shape_saturation_is_a_rate_limit_not_a_ban():
    """One dash clause is fine. Three in a row is the W38 failure."""
    one_prior = ["Julian: Uploaded three approaches - macro, overhead, three-quarter."]
    assert not sdw._shape_is_saturated("The break works - you see the glaze.", one_prior)

    two_prior = one_prior + ["Steph: The break feels right - you see the glaze hit."]
    assert sdw._shape_is_saturated("The steam is the issue - it reads as blur.", two_prior)


def test_a_different_shape_escapes_saturation():
    saturated = [
        "Julian: Uploaded three - macro, overhead, three-quarter.",
        "Steph: The break feels right - you see the glaze.",
    ]
    assert not sdw._shape_is_saturated("Cardamom isn't visible anywhere.", saturated)


def test_terse_messages_are_never_shape_limited():
    """Short sign-offs shouldn't be forced to vary."""
    prior = ["A: Locked.", "B: Night."]
    assert not sdw._shape_is_saturated("Out.", prior)


# --- word budgets ------------------------------------------------------------

def test_word_budgets_match_the_voice_guides():
    """Parsed, not duplicated - the number cannot drift from what the model sees."""
    expected = {
        "Margaret Chen": 15,
        "Stephanie 'Steph' Whitmore": 25,
        "Julian Torres": 20,
        "Marcus Reid": 35,
        "Devon Park": 12,
        "Ria Castillo": 20,
    }
    for name, budget in expected.items():
        assert sdw._word_budget_for(name) == budget, name


def test_every_character_has_a_budget():
    for name in sdw._CHARACTER_VOICE_GUIDES:
        assert sdw._word_budget_for(name), f"{name} has no MAXIMUM in its voice guide"


def test_over_budget_uses_a_tolerance_not_a_hard_line():
    """Margaret's max is 15. 18 is natural variance; 33 is the observed failure."""
    assert not sdw._over_word_budget(" ".join(["w"] * 18), "Margaret Chen")
    assert sdw._over_word_budget(" ".join(["w"] * 33), "Margaret Chen")


def test_budget_tolerance_preserves_the_designed_spread():
    """A message legal for Marcus must still be illegal for Devon."""
    thirty = " ".join(["w"] * 30)
    assert not sdw._over_word_budget(thirty, "Marcus Reid")
    assert sdw._over_word_budget(thirty, "Devon Park")


def test_unknown_character_has_no_budget_and_never_trips():
    assert sdw._word_budget_for("Nobody At All") is None
    assert not sdw._over_word_budget(" ".join(["w"] * 200), "Nobody At All")


# --- wiring into generate_turn ----------------------------------------------

def _persona(name: str = "Margaret Chen") -> dict:
    return {
        "name": name,
        "role": "Head Recipe Developer",
        "communication_style": {"signature_phrases": ["Right."], "verbosity": "low"},
        "internal_contradictions": [],
        "relationships": {},
        "triggers": [],
    }


def _run(persona, first_draft, recent_lines):
    prompts: list[str] = []
    replies = iter([first_draft, "Cardamom isn't visible. Fix that."])

    def fake_generate(prompt, system_prompt=None, model=None, temperature=None, **_kw):
        prompts.append(prompt)
        return next(replies)

    with (
        patch.object(sdw, "generate_response", side_effect=fake_generate),
        patch.object(sdw, "_guard_cot_leak", side_effect=lambda m, **_kw: m),
    ):
        sdw.generate_turn(
            persona=persona, concept="Spiral Bites", day="wednesday",
            stage="photography", deadline="5 pm", recent_lines=recent_lines,
            event=None, model="test-model", mode="openai", prompt_style="scene",
            day_turn=4, is_last_turn=False,
        )
    return prompts


def test_a_saturated_shape_triggers_a_rewrite_naming_the_construction():
    prior = [
        "Julian: Uploaded three - macro, overhead, three-quarter.",
        "Steph: The break feels right - you see the glaze hit.",
    ]
    prompts = _run(_persona(), "The steam is the issue - it reads as blur.", prior)
    assert len(prompts) == 2, "saturated shape should have forced one rewrite"
    assert "claim> - <elaboration" in prompts[1]
    assert "Problems with it:" in prompts[1]


def test_an_over_budget_draft_triggers_a_rewrite_naming_the_number():
    draft = " ".join(["word"] * 33)
    prompts = _run(_persona("Margaret Chen"), draft, ["Julian: Plain opening line here."])
    assert len(prompts) == 2
    assert "33 words" in prompts[1]
    assert "MAXIMUM is 15" in prompts[1]


def test_a_clean_draft_costs_exactly_one_call():
    prompts = _run(_persona(), "Cardamom isn't visible in any of those shots.", ["Julian: Plain line."])
    assert len(prompts) == 1


def test_multiple_faults_are_reported_together_in_one_rewrite():
    """Same API cost as before: one rewrite, every fault named."""
    prior = [
        "Julian: Uploaded three - macro, overhead, three-quarter.",
        "Steph: The break feels right - you see the glaze hit.",
    ]
    draft = "The steam is the issue - " + " ".join(["word"] * 30)
    prompts = _run(_persona("Margaret Chen"), draft, prior)
    assert len(prompts) == 2
    body = prompts[1]
    assert "claim> - <elaboration" in body
    assert "MAXIMUM is 15" in body
    assert body.count("- ") >= 2


# --- Codex review of 7a0bfba: two P2 bugs -------------------------------------

@pytest.mark.parametrize("dash", [" - ", "—", "–"])
def test_em_and_en_dashes_are_classified_as_dash_clauses(dash):
    """An em-dash clause used to escape the guard entirely.

    _sentence_shape matched " - " only, so an em-dash line was classified
    `declarative`, passed the saturation check, and was THEN rewritten to " - "
    by sanitize_typographic_tells on the way out. The guard was blind to the
    exact mechanism the tic uses - house style converts em dashes to hyphens.
    """
    assert sdw._sentence_shape(f"The steam is the issue{dash}it reads as blur.") == "dash_clause"


def test_shape_guard_matches_the_metric_definition():
    """The guard's regex is duplicated from conversation_metrics, not imported.

    simulate_dialogue_week ships in the Vercel bundle and conversation_metrics
    does not (RUNBOOK Incident 4), so importing it would fail only in
    production. Duplication is deliberate - this test is what keeps the two
    definitions from drifting.
    """
    from scripts.conversation_metrics import DASH_CLAUSE_RE

    assert sdw._DASH_CLAUSE_RE.pattern == DASH_CLAUSE_RE.pattern


@pytest.mark.parametrize("dash", [" - ", "—", "–"])
def test_an_em_dash_draft_triggers_the_same_rewrite_as_a_hyphen_one(dash):
    """Behavioural, not just classification: the rewrite must actually fire."""
    prior = [
        "Julian: Uploaded three — macro, overhead, three-quarter.",
        "Steph: The break feels right – you see the glaze hit.",
    ]
    prompts = _run(_persona(), f"The steam is the issue{dash}it reads as blur.", prior)
    assert len(prompts) == 2, "an em/en-dash saturated draft should have been rewritten"
    assert "claim> - <elaboration" in prompts[1]


def test_shape_window_is_read_at_call_time(monkeypatch):
    """SHAPE_WINDOW was a default argument, so it bound once at import.

    A lab variant overriding the module constant changed nothing - the same
    no-op that #7158 fixed for HISTORY_DEPTH, reintroduced here.
    """
    prior = ["a: x - y", "b: p - q", "c: m - n"]
    for window in (1, 2, 3):
        monkeypatch.setattr(sdw, "SHAPE_WINDOW", window)
        assert len(sdw._recent_shapes(prior)) == window


def test_shape_window_override_changes_saturation_behaviour(monkeypatch):
    """The lever has to bite, not just be readable."""
    # One dash clause immediately prior, two plain lines before it.
    prior = ["a: plain sentence with no join at all", "b: another plain one here", "c: p - q"]
    candidate = "The steam is the issue - it reads as blur."

    monkeypatch.setattr(sdw, "SHAPE_MAX_IN_WINDOW", 1)
    monkeypatch.setattr(sdw, "SHAPE_WINDOW", 1)
    assert sdw._shape_is_saturated(candidate, prior) is True, "window=1 sees the one dash clause"

    monkeypatch.setattr(sdw, "SHAPE_MAX_IN_WINDOW", 2)
    assert sdw._shape_is_saturated(candidate, prior) is False, "max=2 needs two in the window"

    monkeypatch.setattr(sdw, "SHAPE_WINDOW", 3)
    assert sdw._shape_is_saturated(candidate, prior) is False, "only one dash clause in three"


def test_shape_max_in_window_is_read_at_call_time(monkeypatch):
    prior = ["a: x - y", "b: p - q"]
    candidate = "A claim - an elaboration."
    monkeypatch.setattr(sdw, "SHAPE_MAX_IN_WINDOW", 2)
    assert sdw._shape_is_saturated(candidate, prior) is True
    monkeypatch.setattr(sdw, "SHAPE_MAX_IN_WINDOW", 3)
    assert sdw._shape_is_saturated(candidate, prior) is False
