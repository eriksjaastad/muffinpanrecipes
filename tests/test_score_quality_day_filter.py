from scripts.simulate_dialogue_week import Message, load_personas, score_quality


def test_score_quality_day_filter_matches_subset():
    personas = load_personas()
    messages = [
        Message(day="monday", stage="brainstorm", character="Margaret Chen", message="No.", timestamp="t1", model="openai/gpt-5-mini"),
        Message(day="monday", stage="brainstorm", character="Marcus Reid", message="Maybe.", timestamp="t2", model="openai/gpt-5-mini"),
        Message(day="tuesday", stage="recipe", character="Margaret Chen", message="Fine.", timestamp="t3", model="openai/gpt-5-mini"),
    ]

    subset = [m for m in messages if m.day == "monday"]
    full_day = score_quality(messages, personas, day="monday")
    subset_score = score_quality(subset, personas)
    assert full_day["score"] == subset_score["score"]


# ---------------------------------------------------------------------------
# #6832 - cast coverage: Julian/Devon were excluded from days where
# CHARACTER_DAY_GOALS already wrote them a goal.
# ---------------------------------------------------------------------------

def test_monday_roster_includes_julian_the_photographer():
    """Monday's roster excluded Julian while CHARACTER_DAY_GOALS wrote him a
    goal for that day ("You're already thinking about how this will
    photograph"), so a Monday about photographing the dish ran with no
    photographer present (W36)."""
    from scripts.simulate_dialogue_week import participants_for_day

    assert "Julian Torres" in participants_for_day("monday")


def test_tuesday_roster_includes_devon():
    """Same mismatch as Julian above, one day later."""
    from scripts.simulate_dialogue_week import participants_for_day

    assert "Devon Park" in participants_for_day("tuesday")


def test_pan_case_rules_fold_generic_menu_into_specific_property_rule():
    """The generic menu bullet used to sit as its own standalone permission
    directly above the 'property specific to THIS dish' rule, reading as
    license to fall back on exactly the generic framing the later rule
    rules out. It must be folded in, not stated as its own list."""
    from scripts.simulate_dialogue_week import _SHARED_CHARACTER_RULES

    assert "specific to THIS dish" in _SHARED_CHARACTER_RULES
    # The old standalone bullet opened with this exact clause - it must be gone.
    assert (
        "When it fits, surface a genuine practical advantage of the format"
        not in _SHARED_CHARACTER_RULES
    )


# ---------------------------------------------------------------------------
# #6840 - is_prompt_echo must catch verbatim recitation of the rules'
# CONTENT, not just the hardcoded scaffolding labels.
# ---------------------------------------------------------------------------

def test_is_prompt_echo_flags_six_word_run_from_shared_rules():
    from scripts.simulate_dialogue_week import is_prompt_echo

    # Six consecutive words lifted straight from _SHARED_CHARACTER_RULES.
    assert is_prompt_echo("This is a group chat, not an email.")


def test_is_prompt_echo_does_not_flag_ordinary_dialogue():
    from scripts.simulate_dialogue_week import is_prompt_echo

    assert not is_prompt_echo("The crust ratio is off. Too much butter.")


def test_is_prompt_echo_near_miss_not_flagged():
    """One word different from a real 6-gram in the rules must NOT trip the
    detector - shingle overlap requires an exact run, not a fuzzy match."""
    from scripts.simulate_dialogue_week import is_prompt_echo

    # Real rule text is "...group chat, not an email"; swap the last word.
    assert not is_prompt_echo("This is a group chat, seriously.")


def test_reaction_directive_allows_short_turns_and_direct_answers():
    """One turn-taking nudge (#6840): explicit permission for a one-sentence
    turn, and priority on answering a direct question over adding something new."""
    from scripts.simulate_dialogue_week import _REACTION_DIRECTIVE

    assert "one sentence" in _REACTION_DIRECTIVE
    assert "answer it before you add anything new" in _REACTION_DIRECTIVE
