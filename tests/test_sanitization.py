"""Regression tests for newline sanitization and anti-repetition (#5046).

Tests sanitize_typographic_tells() and the anti-repetition detection functions
(_is_repetitive_candidate, _shared_trigram_with_recent) from simulate_dialogue_week.py.
"""

import sys
from pathlib import Path

# Add scripts/ to path so we can import simulate_dialogue_week
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from simulate_dialogue_week import (
    sanitize_typographic_tells,
    _is_repetitive_candidate,
    _shared_trigram_with_recent,
    _TYPOGRAPHIC_REPLACEMENTS,
)


# --- sanitize_typographic_tells ---


class TestSanitizeTypographicTells:
    def test_em_dash_replaced(self):
        assert sanitize_typographic_tells("Hello\u2014world") == "Hello - world"

    def test_en_dash_replaced(self):
        assert sanitize_typographic_tells("pages 1\u20135") == "pages 1 - 5"

    def test_curly_apostrophe_replaced(self):
        assert sanitize_typographic_tells("it\u2019s fine") == "it's fine"

    def test_curly_double_quotes_replaced(self):
        assert sanitize_typographic_tells("\u201cHello,\u201d she said") == '"Hello," she said'

    def test_plain_ascii_unchanged(self):
        text = "Just plain text with 'quotes' and - dashes."
        assert sanitize_typographic_tells(text) == text

    def test_empty_string(self):
        assert sanitize_typographic_tells("") == ""

    def test_multiple_replacements_in_one_string(self):
        text = "\u201cIt\u2019s a long story,\u201d she said\u2014quite firmly."
        result = sanitize_typographic_tells(text)
        assert "\u201c" not in result
        assert "\u201d" not in result
        assert "\u2019" not in result
        assert "\u2014" not in result

    def test_all_replacement_pairs_covered(self):
        """Every entry in _TYPOGRAPHIC_REPLACEMENTS should actually replace."""
        for bad, replacement in _TYPOGRAPHIC_REPLACEMENTS:
            result = sanitize_typographic_tells(f"before{bad}after")
            assert bad not in result
            assert replacement in result

    def test_idempotent(self):
        """Running sanitize twice should produce the same result."""
        text = "\u201cHello\u2014world\u201d"
        once = sanitize_typographic_tells(text)
        twice = sanitize_typographic_tells(once)
        assert once == twice


# --- _is_repetitive_candidate ---


class TestIsRepetitiveCandidate:
    def test_exact_duplicate_detected(self):
        candidate = "I think we should try the corn dogs."
        recent = ["Steph: I think we should try the corn dogs."]
        assert _is_repetitive_candidate(candidate, recent) is True

    def test_near_duplicate_detected(self):
        candidate = "I think we should definitely try the corn dogs today."
        recent = ["Steph: I think we should try the corn dogs."]
        assert _is_repetitive_candidate(candidate, recent) is True

    def test_different_content_passes(self):
        candidate = "The photography setup needs better lighting."
        recent = ["Steph: I think we should try the corn dogs."]
        assert _is_repetitive_candidate(candidate, recent) is False

    def test_empty_recent_passes(self):
        assert _is_repetitive_candidate("Anything goes", []) is False

    def test_only_checks_last_six(self):
        """Should only check the last 6 recent lines."""
        old_dup = "Marcus: I love this recipe concept."
        filler = [f"Steph: Filler message number {i} about different topics." for i in range(7)]
        recent = [old_dup] + filler
        # The duplicate is at index 0, pushed out of the last-6 window
        assert _is_repetitive_candidate("I love this recipe concept.", recent) is False

    def test_speaker_prefix_stripped(self):
        """The 'Speaker: ' prefix should be stripped before comparison."""
        candidate = "Let's get started on the recipe."
        recent = ["Margaret: Let's get started on the recipe."]
        assert _is_repetitive_candidate(candidate, recent) is True


# --- _shared_trigram_with_recent ---


class TestSharedTrigramWithRecent:
    def test_shared_trigram_detected(self):
        candidate = "The brown butter pecan flavor is incredible."
        recent = ["Marcus: The brown butter pecan tassies are ready."]
        assert _shared_trigram_with_recent(candidate, recent) is True

    def test_no_shared_trigrams(self):
        candidate = "Photography setup needs adjustment."
        recent = ["Steph: The recipe turned out great today."]
        assert _shared_trigram_with_recent(candidate, recent) is False

    def test_short_candidate_skipped(self):
        """Candidates with fewer than 3 tokens should return False."""
        assert _shared_trigram_with_recent("Too short", []) is False

    def test_stopword_only_trigrams_ignored(self):
        """Trigrams made entirely of stop words shouldn't count."""
        candidate = "It is the end of the line."
        recent = ["Devon: It is the start of the day."]
        # "it is the" is all stop words — should be filtered
        # "is the end" vs "is the start" — no match
        assert _shared_trigram_with_recent(candidate, recent) is False

    def test_empty_recent_returns_false(self):
        assert _shared_trigram_with_recent("Some meaningful content here.", []) is False

    def test_speaker_prefix_stripped(self):
        candidate = "jalapeño corn dog bites recipe"
        recent = ["Julian: jalapeño corn dog bites are photogenic"]
        assert _shared_trigram_with_recent(candidate, recent) is True
