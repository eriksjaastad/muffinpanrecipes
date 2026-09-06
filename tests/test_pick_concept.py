"""Tests for the concept picker's hard filters, scoring, and fallbacks (#6858).

No network, no LLM, no env vars beyond a test-only CONCEPT_MODEL override
(the documented escape hatch that lets a test bypass config.dialogue_model
without needing DIALOGUE_MODEL set via Doppler). Catalogs and the LLM
brainstorm call are injected everywhere pick_concept() is exercised directly;
tests that only need the filter/score pipeline call rank_candidates()
instead, which never touches the network or an LLM at all.
"""

from __future__ import annotations

import pytest

import scripts.pick_concept as pc
from scripts.pick_concept import (
    Candidate,
    CURATED_CONCEPTS,
    MUFFIN_PAN_FORM_NOUNS,
    NoConceptAvailableError,
    _build_word_freq,
    _significant_words,
    pick_concept,
    pick_target_category,
    rank_candidates,
)
from backend.utils.catalog import CatalogUnavailableError


@pytest.fixture(autouse=True)
def _concept_model_env(monkeypatch):
    """Bypass config.dialogue_model — CONCEPT_MODEL is the documented override
    and setting it here means these tests need no real Doppler-provided
    DIALOGUE_MODEL to exist."""
    monkeypatch.setenv("CONCEPT_MODEL", "test/fake-model")


def _catalog(titles=(), categories=None, cuisines=None) -> dict:
    """Build a minimal published-catalog dict from parallel lists."""
    categories = categories or [None] * len(titles)
    cuisines = cuisines or [None] * len(titles)
    recipes = []
    for title, category, cuisine in zip(titles, categories, cuisines):
        recipe: dict = {"title": title}
        if category is not None:
            recipe["category"] = category
        if cuisine is not None:
            recipe["cuisine"] = cuisine
        recipes.append(recipe)
    return {"recipes": recipes}


LIVE_TITLES = [
    "Caramelized Custard Tart Cups",
    "Lemon Ricotta Polenta Cups",
    "Tandoori Chicken Naan Cups",
    "Kimchi Cheddar Rice Cups",
    "Miso Ginger Donburi Cups",
    "Smoky Chorizo Tortilla Cups",
    "Saigon Sunrise Breakfast Cups",
    "Salsa Verde Chilaquiles Cups",
    "Spanakopita Phyllo Cups",
    "Paneer Saag Egg Bites",
    "Teriyaki Salmon Rice Cups",
    "Harissa Chickpea Feta Cups",
    "Roasted Broccoli Egg Cups",
    "Savory Bacon Biscuit Rounds",
    "Crispy Chorizo Breakfast Cups",
    "Herbed Sausage Sunrise Cups",
    "Smoky Sweet Potato Frittatas",
    "Maple Hash Brown Nests",
    "Prosciutto Potato Egg Nests",
    "Mini Caprese Bruschetta Bites",
    "Smoky Cheddar Breakfast Bites",
    "Roasted Veggie Egg Cups",
    "Roasted Veggie Frittata Cups",
    "Mini Lemon Meringue Cups",
    "Make-Ahead Veggie & Sausage Egg Cups",
    "Spinach & Feta Egg Bites",
    "Baked Oatmeal Breakfast Cups",
    "Mini Pancake Bites",
    "Muffin Tin Lasagna Cups",
    "Mini Meatloaf Bites",
    "Buffalo Chicken Mac & Cheese Bites",
    "Mini Shepherd's Pie Pots",
    "Jumbo Cornbread Honey Bites",
    "Classic Blueberry Muffin Tops",
    "Dark Chocolate Chip Decadence",
]


# ---------------------------------------------------------------------------
# pick_target_category — deterministic balancing
# ---------------------------------------------------------------------------

def test_pick_target_category_returns_thinnest_category():
    catalog = _catalog(
        titles=["a", "b", "c", "d", "e", "f", "g", "h", "i"],
        categories=["Breakfast", "Breakfast", "Breakfast",
                    "Savory", "Savory", "Savory",
                    "Party", "Party", "Party"],
    )
    assert pick_target_category(catalog) == "Sweet"


def test_pick_target_category_zero_count_wins_outright():
    catalog = _catalog(
        titles=["a", "b", "c", "d", "e", "f", "g", "h", "i"],
        categories=["Breakfast", "Breakfast", "Breakfast",
                    "Savory", "Savory", "Savory",
                    "Party", "Party", "Party"],
    )
    # Sweet never appears at all — it must win even though every other
    # category is tied at three.
    assert pick_target_category(catalog) == "Sweet"


def test_pick_target_category_tie_broken_by_oldest_most_recent_entry():
    # Newest-first. Breakfast's most recent entry is idx 0 (newest);
    # Savory's is idx 1 (older). Both end up tied at count 1, so Savory
    # — the one whose most recent entry is OLDER — must win the tie.
    catalog = _catalog(
        titles=["a", "b", "c", "d", "e", "f"],
        categories=["Breakfast", "Savory", "Party", "Sweet", "Party", "Sweet"],
    )
    assert pick_target_category(catalog) == "Savory"


def test_pick_target_category_is_deterministic_across_calls():
    catalog = _catalog(
        titles=["a", "b", "c"],
        categories=["Breakfast", "Savory", "Party"],
    )
    first = pick_target_category(catalog)
    second = pick_target_category(catalog)
    assert first == second


def test_pick_target_category_propagates_catalog_unavailable(monkeypatch):
    def _raise(**kwargs):
        raise CatalogUnavailableError("blob down")

    monkeypatch.setattr(pc, "load_published_catalog", _raise)
    with pytest.raises(CatalogUnavailableError):
        pick_target_category()


# ---------------------------------------------------------------------------
# rank_candidates — hard filters
# ---------------------------------------------------------------------------

def test_wrong_category_and_off_form_savory_mains_rejected_for_sweet_target():
    """The real W36-era candidates that shipped as a bad 'Sweet' pick (#6858
    item 1): all three are savory mains with no muffin-pan form."""
    bad = [
        Candidate("Saucy Pork Chops With Coconut Crisp", "Savory", "American", "", "brainstorm"),
        Candidate("Miso Curry Beef and Green Beans", "Savory", "Japanese", "", "brainstorm"),
        Candidate("Grilled Salmon With Dill Chimichurri", "Savory", "Argentinian", "", "brainstorm"),
    ]
    good = Candidate(
        "Lebanese Rosewater Pistachio Cheesecakes", "Sweet", "Lebanese",
        "the filling sets as it cools and releases cleanly", "brainstorm",
    )
    survivors, rejected = rank_candidates(bad + [good], "Sweet", _catalog())

    assert [c.concept for _, c in survivors] == ["Lebanese Rosewater Pistachio Cheesecakes"]
    assert len(rejected) == 3
    for cand, reason in rejected:
        assert reason.startswith("no_pan_form"), (cand.concept, reason)


def test_sweet_target_rejects_savory_marker_keyword_backstop():
    """Declared category can lie; the keyword backstop catches it anyway."""
    candidate = Candidate("Cheesy Chicken Cheesecakes", "Sweet", "American", "", "brainstorm")
    survivors, rejected = rank_candidates([candidate], "Sweet", _catalog())

    assert survivors == []
    assert len(rejected) == 1
    _, reason = rejected[0]
    assert reason.startswith("wrong_category")
    assert "chicken" in reason


def test_breakfast_target_rejects_dessert_marker_keyword_backstop():
    candidate = Candidate("Chocolate Fudge Bites", "Breakfast", "American", "", "brainstorm")
    survivors, rejected = rank_candidates([candidate], "Breakfast", _catalog())

    assert survivors == []
    _, reason = rejected[0]
    assert reason.startswith("wrong_category")


def test_dish_noun_collision_custard_tart():
    catalog = _catalog(titles=["Caramelized Custard Tart Cups"])
    candidate = Candidate(
        "Portuguese Honeyed Almond Tart Bites", "Sweet", "Portuguese",
        "the custard sets firm enough to hold its shape", "brainstorm",
    )
    survivors, rejected = rank_candidates([candidate], "Sweet", catalog)

    assert survivors == []
    _, reason = rejected[0]
    assert reason.startswith("dish_noun_collision")
    assert "tart" in reason


def test_dish_noun_collision_spanakopita():
    catalog = _catalog(titles=["Spanakopita Phyllo Cups"])
    candidate = Candidate("Greek Spanakopita Cups", "Savory", "Greek", "", "brainstorm")
    survivors, rejected = rank_candidates([candidate], "Savory", catalog)

    assert survivors == []
    _, reason = rejected[0]
    assert reason.startswith("dish_noun_collision")
    assert "spanakopita" in reason


def test_off_brand_shape_rejected():
    candidate = Candidate("Cheddar Broccoli Egg Squares", "Savory", "American", "", "brainstorm")
    survivors, rejected = rank_candidates([candidate], "Savory", _catalog())

    assert survivors == []
    _, reason = rejected[0]
    assert reason.startswith("off_brand_shape")


def test_no_pan_form_rejects_salmon_fillet():
    candidate = Candidate("Grilled Salmon With Dill Chimichurri", "Savory", "Argentinian", "", "brainstorm")
    survivors, rejected = rank_candidates([candidate], "Savory", _catalog())

    assert survivors == []
    _, reason = rejected[0]
    assert reason.startswith("no_pan_form")


def test_novelty_overlap_rejects_near_duplicate():
    catalog = _catalog(titles=["Roasted Veggie Frittata Cups"])
    candidate = Candidate("Roasted Veggie Egg Cups", "Savory", "American", "", "brainstorm")
    survivors, rejected = rank_candidates([candidate], "Savory", catalog)

    assert survivors == []
    _, reason = rejected[0]
    assert reason.startswith("novelty_overlap") or reason.startswith("dish_noun_collision")


def test_overused_word_rejects_after_three_uses():
    catalog = _catalog(titles=[
        "Roasted Broccoli Egg Cups",
        "Roasted Veggie Egg Cups",
        "Roasted Veggie Frittata Cups",
    ])
    candidate = Candidate("Roasted Zucchini Muffins", "Savory", "American", "", "brainstorm")
    survivors, rejected = rank_candidates([candidate], "Savory", catalog)

    assert survivors == []
    _, reason = rejected[0]
    assert reason.startswith("overused_word")
    assert "roasted" in reason


# ---------------------------------------------------------------------------
# Scoring — survivors are ranked, not just admitted
# ---------------------------------------------------------------------------

def test_scores_separate_fresh_from_used_cuisine():
    catalog = _catalog(titles=["x"], categories=["Sweet"], cuisines=["Italian"])
    fresh = Candidate("French Apricot Tartlets", "Sweet", "French", "", "brainstorm")
    used = Candidate("Italian Apricot Tartlets", "Sweet", "Italian", "", "brainstorm")

    survivors, rejected = rank_candidates([fresh, used], "Sweet", catalog)

    assert rejected == []
    scores = {cand.concept: score for score, cand in survivors}
    assert scores["French Apricot Tartlets"] != scores["Italian Apricot Tartlets"]
    assert scores["French Apricot Tartlets"] > scores["Italian Apricot Tartlets"]


def test_survivors_sorted_best_first():
    catalog = _catalog()
    weak = Candidate("Sugar Puffs", "Sweet", "", "", "brainstorm")
    strong = Candidate(
        "Armenian Walnut Tassies", "Sweet", "Armenian",
        "the filling binds and releases cleanly once cooled", "brainstorm",
    )
    survivors, _ = rank_candidates([weak, strong], "Sweet", catalog)
    assert [c.concept for _, c in survivors][0] == "Armenian Walnut Tassies"


# ---------------------------------------------------------------------------
# pick_concept — brainstorm + curated fallback + fail-closed
# ---------------------------------------------------------------------------

def test_brainstorm_exception_falls_back_to_curated():
    def _raiser(*_a, **_k):
        raise RuntimeError("model unavailable")

    picks = pick_concept(
        count=1, target_category="Sweet", catalog=_catalog(),
        generate=_raiser, fetch_inspiration=False,
    )
    assert len(picks) == 1
    assert picks[0] in [concept for concept, _cuisine in CURATED_CONCEPTS["Sweet"]]


def test_brainstorm_unparseable_output_falls_back_to_curated():
    def _garbage(*_a, **_k):
        return "Sorry, I can't help with that request."

    picks = pick_concept(
        count=1, target_category="Savory", catalog=_catalog(),
        generate=_garbage, fetch_inspiration=False,
    )
    assert len(picks) == 1
    assert picks[0] in [concept for concept, _cuisine in CURATED_CONCEPTS["Savory"]]


def test_brainstorm_candidates_used_when_valid():
    def _fake_generate(_prompt, _system, model=None, temperature=None):
        return (
            '[{"concept": "Georgian Walnut Tartlets", "category": "Sweet", '
            '"cuisine": "Georgian", "binds_how": "sets firm and releases cleanly"}]'
        )

    picks = pick_concept(
        count=1, target_category="Sweet", catalog=_catalog(),
        generate=_fake_generate, fetch_inspiration=False,
    )
    assert picks == ["Georgian Walnut Tartlets"]


def test_no_concept_available_when_everything_rejected(monkeypatch):
    monkeypatch.setitem(CURATED_CONCEPTS, "Sweet", [("Sweet Test Squares", "Test")])

    def _raiser(*_a, **_k):
        raise RuntimeError("model unavailable")

    with pytest.raises(NoConceptAvailableError) as exc_info:
        pick_concept(
            count=1, target_category="Sweet", catalog=_catalog(),
            generate=_raiser, fetch_inspiration=False,
        )
    assert "off_brand_shape" in str(exc_info.value)


def test_dry_run_returns_the_same_picks_it_would_otherwise_return(capsys):
    def _fake_generate(_prompt, _system, model=None, temperature=None):
        return (
            '[{"concept": "Georgian Walnut Tartlets", "category": "Sweet", '
            '"cuisine": "Georgian", "binds_how": "sets firm and releases cleanly"}]'
        )

    picks = pick_concept(
        dry_run=True, count=1, target_category="Sweet", catalog=_catalog(),
        generate=_fake_generate, fetch_inspiration=False,
    )
    assert picks == ["Georgian Walnut Tartlets"]
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out


def test_pick_concept_propagates_catalog_unavailable(monkeypatch):
    def _raise(**kwargs):
        raise CatalogUnavailableError("blob down")

    monkeypatch.setattr(pc, "load_published_catalog", _raise)
    with pytest.raises(CatalogUnavailableError):
        pick_concept(fetch_inspiration=False)


# ---------------------------------------------------------------------------
# _build_word_freq — unchanged contract from before the rewrite
# ---------------------------------------------------------------------------

def test_build_word_freq_skips_stop_words():
    recent = ["mini cups and bites with egg"]
    freq = _build_word_freq(recent)
    for sw in ["mini", "cups", "and", "bites", "with"]:
        assert sw not in freq
    assert freq.get("egg", 0) == 1


def test_build_word_freq_counts_across_titles():
    recent = [
        "make-ahead veggie & sausage egg cups",
        "roasted veggie frittata cups",
        "roasted veggie egg prep cups",
    ]
    freq = _build_word_freq(recent)
    assert freq["veggie"] == 3


# ---------------------------------------------------------------------------
# Curated pool sanity — pinned against the live catalog as of 2026-09-05
# ---------------------------------------------------------------------------

def test_curated_pool_ends_in_a_pan_form_noun():
    for category, entries in CURATED_CONCEPTS.items():
        for concept, _cuisine in entries:
            last_word = concept.lower().split()[-1].strip(".,")
            assert last_word in MUFFIN_PAN_FORM_NOUNS, f"{concept!r} ({category}) does not end in a form noun"


def test_curated_pool_survives_against_the_live_catalog():
    """Every curated concept must pass its own category's hard filters when
    checked against today's live catalog — no dish_noun_collision, no
    novelty_overlap, no overused_word, no off-brand shape, no marker
    backstop trip. This pins the pool: if the live catalog changes shape
    enough to break one of these, this test is the signal to refresh it."""
    catalog = _catalog(titles=LIVE_TITLES)
    for category, entries in CURATED_CONCEPTS.items():
        candidates = [Candidate(concept, category, cuisine, "", "curated") for concept, cuisine in entries]
        survivors, rejected = rank_candidates(candidates, category, catalog)
        assert rejected == [], f"curated pool for {category} was rejected: {rejected}"
        assert len(survivors) == len(entries)


def test_significant_words_import_still_works():
    # Sanity check that the title_validator import used for dish_noun_collision
    # is wired correctly (normalizes plurals, drops stop words).
    assert _significant_words("Caramelized Custard Tart Cups") == {"caramelized", "custard", "tart"}
