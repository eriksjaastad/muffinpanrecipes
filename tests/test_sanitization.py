"""Regression tests for dialogue typography sanitization (#5046)."""

import sys
from pathlib import Path

# Add scripts/ to path so we can import simulate_dialogue_week
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from simulate_dialogue_week import (
    sanitize_typographic_tells,
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
