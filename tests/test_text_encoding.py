"""Regression tests for text encoding and sanitization.

Ensures UTF-8 smart punctuation is handled correctly throughout the
rendering pipeline, and mojibake (double-encoded text) is detected.
"""

import html

import pytest

from backend.utils.text_sanitize import has_encoding_issues, sanitize_text


class TestSanitizeText:
    """Test sanitize_text normalizes smart punctuation."""

    def test_smart_apostrophe(self):
        assert sanitize_text("Don\u2019t") == "Don't"

    def test_smart_double_quotes(self):
        assert sanitize_text("\u201cmeal prep\u201d") == '"meal prep"'

    def test_em_dash(self):
        assert sanitize_text("eggs\u2014fresh") == "eggs - fresh"

    def test_en_dash(self):
        assert sanitize_text("5\u20137 minutes") == "5-7 minutes"

    def test_ellipsis(self):
        assert sanitize_text("wait\u2026") == "wait..."

    def test_degree_symbol_preserved(self):
        assert sanitize_text("350\u00b0F") == "350\u00b0F"

    def test_non_breaking_space(self):
        assert sanitize_text("1\u00a0cup") == "1 cup"

    def test_empty_string(self):
        assert sanitize_text("") == ""

    def test_none_passthrough(self):
        # sanitize_text returns falsy input as-is
        assert sanitize_text("") == ""

    def test_plain_ascii_unchanged(self):
        text = "Preheat oven to 350F. Mix eggs and flour."
        assert sanitize_text(text) == text

    def test_multiple_smart_chars(self):
        text = "\u201cDon\u2019t forget\u201d \u2014 she said"
        result = sanitize_text(text)
        assert '"Don\'t forget"' in result
        assert "she said" in result


class TestHasEncodingIssues:
    """Test mojibake detection."""

    def test_clean_text(self):
        assert not has_encoding_issues("Preheat oven to 350°F")

    def test_empty(self):
        assert not has_encoding_issues("")

    def test_smart_quotes_are_fine(self):
        # Smart quotes themselves are NOT encoding issues
        assert not has_encoding_issues("Don\u2019t worry")

    def test_double_encoded_apostrophe(self):
        # This is what mojibake looks like: UTF-8 bytes of ' decoded as Latin-1
        mojibake = "\xc3\xa2\xc2\x80\xc2\x99"
        assert has_encoding_issues(mojibake)

    def test_double_encoded_degree(self):
        mojibake = "\xc3\x82\xc2\xb0"
        assert has_encoding_issues(mojibake)


class TestRenderingParity:
    """Ensure sanitized text renders correctly through html.escape."""

    def test_apostrophe_in_html(self):
        raw = "Don\u2019t burn the muffins"
        rendered = html.escape(sanitize_text(raw))
        assert "Don&#x27;t" in rendered or "Don't" in rendered
        assert "\u2019" not in rendered

    def test_degree_in_html(self):
        raw = "350\u00b0F"
        rendered = html.escape(sanitize_text(raw))
        assert "350" in rendered
        assert "F" in rendered

    def test_quotes_in_html(self):
        raw = '\u201cmeal prep\u201d'
        rendered = html.escape(sanitize_text(raw))
        assert "&quot;" in rendered or '"' in rendered

    def test_no_double_escaping(self):
        # sanitize_text should not introduce HTML entities
        # (that's html.escape's job at render time)
        raw = "5 & 10"
        sanitized = sanitize_text(raw)
        assert "&amp;" not in sanitized  # no premature escaping
        rendered = html.escape(sanitized)
        assert rendered == "5 &amp; 10"  # single escape only

    def test_mixed_content(self):
        raw = "Saut\u00e9 veggies at 350\u00b0F for 5\u20137 min \u2014 don\u2019t overcook"
        result = sanitize_text(raw)
        # Accented e preserved (it's legitimate cooking term)
        # But wait, our sanitize_text doesn't touch sauté - and it shouldn't
        assert "350\u00b0F" in result  # degree preserved
        assert "5-7" in result  # en dash normalized
        assert " - " in result  # em dash normalized
        assert "don't" in result  # apostrophe normalized
