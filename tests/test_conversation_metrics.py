"""Unit tests for scripts/conversation_metrics.py.

All metrics here are pure/deterministic (no network, no LLM) so these tests
run with zero paid API calls. `legacy_quality`/`summarize` import
`scripts.simulate_dialogue_week` lazily and call `load_personas()`, which
only reads a local JSON file (`backend/data/agent_personalities.json`) - no
network, no model_router call.
"""

from __future__ import annotations

from scripts import conversation_metrics as cm


def _msg(character: str, message: str, **extra) -> dict:
    base = {"character": character, "message": message}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# cast_coverage
# ---------------------------------------------------------------------------


def test_cast_coverage_reports_present_missing_unexpected():
    expected = ["Margaret Chen", "Julian Torres"]
    messages = [
        _msg("Margaret Chen", "The ratio is off."),
        _msg("Devon Park", "Deployed."),
    ]
    result = cm.cast_coverage(expected, messages)
    assert result["present"] == ["Margaret"]
    assert result["missing"] == ["Julian"]
    assert result["unexpected"] == ["Devon"]
    assert result["ratio"] == 0.5


def test_cast_coverage_normalises_full_names_to_first_name():
    expected = ["Stephanie 'Steph' Whitmore"]
    messages = [_msg("Stephanie 'Steph' Whitmore", "Let's lock it.")]
    result = cm.cast_coverage(expected, messages)
    assert result["expected"] == ["Stephanie"]
    assert result["present"] == ["Stephanie"]
    assert result["ratio"] == 1.0


def test_cast_coverage_empty_expected_is_full_ratio():
    result = cm.cast_coverage([], [_msg("Margaret Chen", "Hi")])
    assert result["ratio"] == 1.0


# ---------------------------------------------------------------------------
# adjacency_continuity - known Jaccard values
# ---------------------------------------------------------------------------


def test_adjacency_continuity_known_jaccard_value():
    # content words (stopwords "we"/"should"/"the"/"needs" is NOT a stopword):
    #   msg0: {muffin, batter, needs, butter}
    #   msg1: {butter, ratio, needs, work}
    # intersection = {needs, butter} = 2; union = 6 distinct words -> 2/6
    messages = [
        _msg("Margaret Chen", "Muffin batter needs butter."),
        _msg("Marcus Reid", "Butter ratio needs work."),
    ]
    result = cm.adjacency_continuity(messages)
    assert result["turn_count"] == 1
    assert result["mean_overlap"] == round(2 / 6, 4)
    assert result["share_with_link"] == 1.0


def test_adjacency_continuity_no_shared_content_words():
    messages = [
        _msg("Margaret Chen", "Muffin batter needs butter."),
        _msg("Devon Park", "Staging looks fine today."),
    ]
    result = cm.adjacency_continuity(messages)
    assert result["mean_overlap"] == 0.0
    assert result["share_with_link"] == 0.0


def test_adjacency_continuity_single_message_is_zero():
    result = cm.adjacency_continuity([_msg("Margaret Chen", "Hello.")])
    assert result == {"mean_overlap": 0.0, "share_with_link": 0.0, "turn_count": 0}


# ---------------------------------------------------------------------------
# question_answer_rate - answered vs unanswered
# ---------------------------------------------------------------------------


def test_question_answer_rate_answered_vs_unanswered():
    messages = [
        _msg("Margaret Chen", "Should we use butter or oil?"),
        _msg("Stephanie 'Steph' Whitmore", "Butter, obviously."),
        _msg("Marcus Reid", "Should we ship on Friday?"),  # last turn, unanswered
    ]
    result = cm.question_answer_rate(messages)
    assert result["question_count"] == 2
    assert result["answered_count"] == 1
    assert result["rate"] == 0.5


def test_question_answer_rate_same_character_reply_does_not_count():
    messages = [
        _msg("Margaret Chen", "Should we use butter or oil?"),
        _msg("Margaret Chen", "Butter. Obviously butter."),
    ]
    result = cm.question_answer_rate(messages)
    assert result["question_count"] == 1
    assert result["answered_count"] == 0
    assert result["rate"] == 0.0


def test_question_answer_rate_no_questions():
    result = cm.question_answer_rate([_msg("Margaret Chen", "Ship it.")])
    assert result == {"rate": 0.0, "question_count": 0, "answered_count": 0}


# ---------------------------------------------------------------------------
# repeated_phrases - cross-character 3-grams and per-character 4-gram tics
# ---------------------------------------------------------------------------


def test_repeated_phrases_detects_per_character_4gram_tic():
    messages = [
        _msg("Marcus Reid", "That is the story here."),
        _msg("Julian Torres", "The light is great today."),
        _msg("Marcus Reid", "Honestly that is the story."),
    ]
    result = cm.repeated_phrases(messages)
    tics = result["per_character_4gram_tics"].get("Marcus", [])
    assert ("that is the story", 2) in tics


def test_repeated_phrases_detects_cross_character_3gram():
    messages = [
        _msg("Margaret Chen", "It holds together well."),
        _msg("Marcus Reid", "It holds together beautifully."),
    ]
    result = cm.repeated_phrases(messages)
    phrases = dict(result["cross_character_3grams"])
    assert phrases.get("it holds together") == 2


def test_repeated_phrases_single_character_3gram_is_not_cross_character():
    messages = [
        _msg("Marcus Reid", "It holds together well."),
        _msg("Marcus Reid", "It holds together nicely."),
    ]
    result = cm.repeated_phrases(messages)
    phrases = dict(result["cross_character_3grams"])
    assert "it holds together" not in phrases


# ---------------------------------------------------------------------------
# length_stats
# ---------------------------------------------------------------------------


def test_length_stats_mean_min_max_and_per_character():
    messages = [
        _msg("Margaret Chen", "Short one."),  # 2 words
        _msg("Marcus Reid", "This one runs a fair bit longer than that."),  # 9 words
    ]
    result = cm.length_stats(messages)
    assert result["min"] == 2
    assert result["max"] == 9
    assert result["mean"] == 5.5
    assert result["per_character_mean"] == {"Margaret": 2.0, "Marcus": 9.0}


def test_length_stats_empty_messages():
    assert cm.length_stats([]) == {"mean": 0.0, "min": 0, "max": 0, "per_character_mean": {}}


# ---------------------------------------------------------------------------
# legacy_quality - wraps the production heuristic grader
# ---------------------------------------------------------------------------


def test_legacy_quality_returns_score_quality_shape():
    messages = [
        _msg("Margaret Chen", "The ratio is off. Fix it before we bake again."),
        _msg("Marcus Reid", "There is something almost cinematic about a good ratio."),
    ]
    result = cm.legacy_quality(messages, concept="Test Muffins", day="monday")
    assert "score" in result
    assert isinstance(result["score"], int)
    assert "prompt_echo_hits" in result


# ---------------------------------------------------------------------------
# summarize - flat dict combining everything
# ---------------------------------------------------------------------------


def test_summarize_flat_dict_has_expected_top_level_numeric_keys():
    expected_cast = ["Margaret Chen", "Marcus Reid"]
    messages = [
        _msg("Margaret Chen", "Should we lock the ratio now?"),
        _msg("Marcus Reid", "Yes, the ratio finally works."),
    ]
    result = cm.summarize(messages, expected_cast, concept="Test Muffins", day="monday")
    for key in (
        "message_count", "unique_characters", "cast_ratio", "cast_missing_count",
        "cast_unexpected_count", "adjacency_mean_overlap", "adjacency_share_with_link",
        "qa_rate", "qa_question_count", "length_mean_words", "length_min_words",
        "length_max_words", "cross_character_3gram_repeat_count",
        "per_character_4gram_tic_count", "legacy_score",
    ):
        assert key in result, f"missing summarize() key: {key}"
    assert result["message_count"] == 2
    assert result["cast_ratio"] == 1.0
    assert isinstance(result["legacy_quality_detail"], dict)
