"""24-hour clock stripping must not strand a meridiem (#7198).

Reproduced live on /this-week W38 Wednesday: stripping a 24-hour time left
an orphaned "pm" ("...makes someone stop scrolling pm."). The two regexes
in generate_turn removed the digits but not an adjacent am/pm, and
_normalize_time_notation (which runs first) can itself turn something like
"12 pm" into the two-digit-hour string "12:00 pm" that the strip regexes
then mangle the same way.
"""

from __future__ import annotations

from scripts.simulate_dialogue_week import _strip_24h_clock_references


class TestStrip24hClockReferences:
    def test_reproduction_at_1700_pm(self):
        # Exact reported bug: W38 Wednesday, Ria's line.
        msg = (
            "that glossy glaze catching light while it's still hot is what "
            "makes someone stop scrolling at 17:00 pm."
        )
        out = _strip_24h_clock_references(msg)
        assert "pm" not in out
        assert "am" not in out.lower().split()  # no stray "am" either
        assert out == (
            "that glossy glaze catching light while it's still hot is what "
            "makes someone stop scrolling."
        )

    def test_reproduction_at_0500_pm(self):
        assert _strip_24h_clock_references("stop scrolling at 05:00 pm.") == "stop scrolling."

    def test_reproduction_bare_1700_pm_no_at(self):
        assert _strip_24h_clock_references("stop scrolling 17:00 pm.") == "stop scrolling."

    def test_must_not_change_single_digit_hour(self):
        # A one-digit hour never matched the 24-hour patterns and must stay
        # exactly as written.
        msg = "let's lock this in at 5:00 pm."
        assert _strip_24h_clock_references(msg) == msg

    def test_must_not_change_single_digit_hour_no_leading_zero_pm(self):
        msg = "ready by 3:30 p.m. today"
        assert _strip_24h_clock_references(msg) == msg

    def test_normalize_time_notation_can_manufacture_two_digit_hour(self):
        # _normalize_time_notation turns "12 pm" into "12:00 pm" before this
        # function ever sees the text - "12" starts with a digit in [012],
        # so it looks exactly like a stranding 24-hour reference and must be
        # removed whole, not partially.
        assert (
            _strip_24h_clock_references("let's meet at 12:00 pm today") == "let's meet today"
        )

    def test_compact_form_no_space_before_meridiem(self):
        assert (
            _strip_24h_clock_references("catch you at 09:15am tomorrow")
            == "catch you tomorrow"
        )

    def test_uppercase_meridiem(self):
        assert (
            _strip_24h_clock_references("stranded 17:00 PM right there")
            == "stranded right there"
        )

    def test_bare_24h_time_no_meridiem_still_stripped(self):
        assert _strip_24h_clock_references("call me 15:00.") == "call me."

    def test_no_stray_space_before_punctuation_after_strip(self):
        # Removing "at 17:00 pm" from directly before a period must not
        # leave a dangling " ." artifact.
        out = _strip_24h_clock_references("wrap it up at 20:00 pm.")
        assert " ." not in out
        assert out == "wrap it up."

    def test_pm_as_unrelated_word_is_untouched(self):
        msg = "the pm team loves this recipe"
        assert _strip_24h_clock_references(msg) == msg

    def test_am_as_unrelated_acronym_is_untouched(self):
        msg = "AM I right about this ratio"
        assert _strip_24h_clock_references(msg) == msg

    def test_by_due_prefixes_are_not_special_cased_like_at(self):
        # Only "at" gets the word-prefix treatment; "by"/"due" two-digit
        # clock references still get their digits stripped (pre-existing
        # behavior), just without stranding the meridiem now.
        assert _strip_24h_clock_references("wrap by 17:00pm sharp") == "wrap by sharp"
