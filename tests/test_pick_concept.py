"""Tests for pick_concept scoring helpers."""

from scripts.pick_concept import (
    _build_word_freq,
    score_candidate,
)


def test_score_candidate_caps_at_ten() -> None:
    """Max-keyword name should score high but stay within 0-10 range."""
    name = (
        "Mini Pumpkin Cranberry Ginger Spice Chocolate Caramel Biscuit Cups"
    )
    score = score_candidate(name, recent_concepts=[], current_month=12)
    assert 0.0 <= score <= 10.0
    # Should score high: 3 pan keywords + 2 novelty + 3.5 seasonal + 0.5 category = 9.0
    assert score == 9.0


def test_novelty_penalises_overlapping_concept() -> None:
    """A candidate with >50% word overlap with a recent recipe scores much lower."""
    recent = ["roasted veggie frittata cups"]
    score_novel = score_candidate("Lemon Ricotta Cheesecake Cups", recent, current_month=4)
    score_overlap = score_candidate("Roasted Veggie Egg Cups", recent, current_month=4)
    assert score_novel > score_overlap


def test_word_freq_penalises_overused_words() -> None:
    """Words appearing in 2+ published recipes get penalised."""
    recent = [
        "make-ahead veggie & sausage egg cups",
        "roasted veggie frittata cups",
        "roasted veggie egg prep cups",
    ]
    word_freq = _build_word_freq(recent)
    # "veggie" appears 3 times — should get hard penalty
    assert word_freq["veggie"] == 3

    score_veggie = score_candidate(
        "Veggie Quiche Bites", recent, current_month=4, word_freq=word_freq
    )
    score_fresh = score_candidate(
        "Lemon Ricotta Cheesecake Cups", recent, current_month=4, word_freq=word_freq
    )
    assert score_fresh > score_veggie


def test_stop_words_not_counted_as_repetition() -> None:
    """Generic words (cups, mini, bites) should not trigger frequency penalties."""
    recent = ["mini lemon cups", "mini chocolate cups", "mini berry cups"]
    word_freq = _build_word_freq(recent)
    # "mini" and "cups" are stop words — shouldn't be in freq
    assert "mini" not in word_freq
    assert "cups" not in word_freq
    # "lemon", "chocolate", "berry" each appear once — no penalty
    assert word_freq.get("lemon", 0) == 1


def test_build_word_freq_skips_stop_words() -> None:
    """Verify stop words are excluded from frequency counts."""
    recent = ["mini cups and bites with egg"]
    freq = _build_word_freq(recent)
    for sw in ["mini", "cups", "and", "bites", "with"]:
        assert sw not in freq
    assert freq.get("egg", 0) == 1
