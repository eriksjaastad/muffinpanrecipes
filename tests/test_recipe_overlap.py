"""Tests for backend.utils.recipe_overlap — the ingredient-overlap gate (#6854).

No network: fixtures below are hardcoded from the live catalog dump used to
calibrate DUPLICATE_THRESHOLD (see the module docstring), not fetched here.
"""

from backend.utils.recipe_overlap import (
    DUPLICATE_THRESHOLD,
    MIN_ITEMS,
    check_ingredient_overlap,
    find_ingredient_overlaps,
    ingredient_items,
    items_match,
    normalize_ingredient,
    overlap_coefficient,
)

# ---------------------------------------------------------------------------
# Real ingredient strings, copied verbatim from the live catalog
# (pages/recipes.json via load_published_catalog) — the pair that
# calibrated DUPLICATE_THRESHOLD.
# ---------------------------------------------------------------------------

ROASTED_VEGGIE_EGG_CUPS = {
    "slug": "roasted-veggie-egg-cups",
    "title": "Roasted Veggie Egg Cups",
    "episode_id": "2026-W14",
    "ingredients": [
        "1 tbsp olive oil (plus extra for greasing)",
        "1 cup small broccoli florets (fresh, not frozen)",
        "1 cup red bell pepper (diced small)",
        "1/2 cup yellow onion (finely diced)",
        "1/2 cup zucchini, diced small (seeds trimmed)",
        "1/2 tsp kosher salt (for vegetables)",
        "1/4 tsp black pepper (for vegetables)",
        "8 large eggs",
        "4 large egg whites (about 1/2 cup, from carton or separated)",
        "1/2 cup 2% milk (or whole milk for richer cups)",
        "1/2 tsp kosher salt (for egg mixture)",
        "1/4 tsp black pepper (for egg mixture)",
        "1/2 tsp garlic powder",
        "1/4 tsp smoked paprika",
        "1/4 cup finely grated Parmesan cheese",
        "1/2 cup shredded sharp cheddar cheese (divided)",
        "2 tbsp chopped fresh parsley (or chives)",
        "Nonstick cooking spray (canola or avocado oil)",
    ],
}

ROASTED_VEGGIE_FRITTATA_CUPS = {
    "slug": "roasted-veggie-frittata-cups",
    "title": "Roasted Veggie Frittata Cups",
    "episode_id": "2026-W16",
    "ingredients": [
        "1 tbsp unsalted butter (for greasing muffin tin)",
        "1 tbsp olive oil",
        "1 cup small broccoli florets (cut into 1/2-inch pieces)",
        "3/4 cup red bell pepper, diced (1/4-inch pieces)",
        "1/2 cup yellow onion (finely diced)",
        "1/2 cup cremini mushrooms (finely chopped)",
        "1/2 tsp kosher salt (divided)",
        "1/4 tsp black pepper (divided)",
        "10 large eggs",
        "1/3 cup whole milk (or half-and-half for richer texture)",
        "1/4 tsp garlic powder",
        "1/4 tsp smoked paprika",
        "1/8 tsp crushed red pepper flakes (optional, for mild heat)",
        "1/2 cup shredded sharp cheddar cheese (Tillamook or similar)",
        "1/2 cup crumbled feta cheese",
        "2 tbsp chopped fresh parsley",
        "1 tbsp chopped fresh chives (or green onion tops)",
        "1/4 cup finely grated Parmesan cheese (for topping)",
    ],
}

# A genuinely different dish, also from the live catalog, with a similar
# ingredient count — the "clear" control.
SPANAKOPITA_PHYLLO_CUPS = {
    "slug": "spanakopita-phyllo-cups",
    "title": "Spanakopita Phyllo Cups",
    "episode_id": "2026-W28",
    "ingredients": [
        "10 sheets phyllo dough, thawed (from a 16 oz package, 9x14 inch sheets)",
        "6 tbsp unsalted butter, melted (plus more for greasing)",
        "1 tbsp extra-virgin olive oil",
        "1 small leek, white and light green parts only, finely chopped (about 3/4 cup, rinsed well)",
        "3 green onions (thinly sliced)",
        "2 cloves garlic (minced)",
        "10 oz frozen chopped spinach, thawed and very well squeezed dry",
        "1/4 cup fresh dill, finely chopped (or 2 tbsp dried dill)",
        "2 tbsp fresh flat-leaf parsley (finely chopped)",
        "1/4 tsp ground nutmeg",
        "1/2 tsp kosher salt (plus more to taste)",
        "1/4 tsp black pepper",
        "2 large eggs",
        "6 oz feta cheese, crumbled (preferably sheep's milk)",
        "1/4 cup finely grated Parmigiano-Reggiano or Kefalotyri",
        "2 tbsp plain Greek yogurt (whole milk)",
    ],
}

CARAMELIZED_CUSTARD_TART_CUPS = {
    "slug": "caramelized-custard-tart-cups",
    "title": "Caramelized Custard Tart Cups",
    "episode_id": "2026-W36",
    "ingredients": [
        "1 sheet frozen puff pastry, thawed (from a 17.3 oz package)",
        "1 tbsp unsalted butter, melted (for greasing)",
        "3/4 cup granulated sugar (divided)",
        "3 tbsp water",
        "1 cup heavy cream",
        "1/2 cup whole milk",
        "4 large egg yolks",
        "1 large egg",
        "1/4 cup granulated sugar",
        "1 tbsp cornstarch",
        "1 tsp vanilla extract",
        "1/2 tsp ground cinnamon",
        "1/4 tsp ground nutmeg",
        "1 tsp lemon zest",
        "1/4 tsp kosher salt",
        "1 tbsp all-purpose flour (for dusting)",
    ],
}


# ---------------------------------------------------------------------------
# normalize_ingredient
# ---------------------------------------------------------------------------


def test_normalize_drops_parenthetical_and_quantity() -> None:
    assert normalize_ingredient(
        "1 tbsp olive oil (plus extra for greasing)"
    ) == frozenset({"olive", "oil"})


def test_normalize_drops_descriptors() -> None:
    assert normalize_ingredient(
        "1/2 cup shredded sharp cheddar cheese (divided)"
    ) == frozenset({"cheddar", "cheese"})


def test_normalize_singularizes() -> None:
    assert normalize_ingredient("8 large eggs") == frozenset({"egg"})


def test_normalize_handles_leading_parenthetical_aside() -> None:
    assert normalize_ingredient(
        "1 stick (about 3 inches) Cinnamon stick"
    ) == frozenset({"cinnamon", "stick"})


def test_normalize_drops_trailing_comma_notes() -> None:
    assert normalize_ingredient(
        "10 sheets phyllo dough, thawed (from a 16 oz package)"
    ) == frozenset({"phyllo", "dough"})


def test_normalize_dict_uses_item_field_only() -> None:
    raw = {"item": "Frozen puff pastry sheet, thawed", "amount": "1 sheet"}
    assert normalize_ingredient(raw) == frozenset({"puff", "pastry", "sheet"})


def test_normalize_empty_string_is_none() -> None:
    assert normalize_ingredient("") is None


def test_normalize_amount_only_is_none() -> None:
    assert normalize_ingredient("2 tbsp") is None


def test_normalize_dict_empty_item_is_none() -> None:
    assert normalize_ingredient({"item": "", "amount": "1 tsp"}) is None


# ---------------------------------------------------------------------------
# items_match / overlap_coefficient
# ---------------------------------------------------------------------------


def test_items_match_exact() -> None:
    assert items_match(frozenset({"egg"}), frozenset({"egg"})) is True


def test_items_match_subset_either_direction() -> None:
    cheddar = frozenset({"cheddar"})
    sharp_cheddar_cheese = frozenset({"sharp", "cheddar", "cheese"})
    assert items_match(cheddar, sharp_cheddar_cheese) is True
    assert items_match(sharp_cheddar_cheese, cheddar) is True


def test_items_match_unrelated_is_false() -> None:
    assert items_match(frozenset({"egg"}), frozenset({"flour"})) is False


def test_overlap_coefficient_empty_sides_is_zero() -> None:
    assert overlap_coefficient([], [frozenset({"egg"})]) == 0.0
    assert overlap_coefficient([frozenset({"egg"})], []) == 0.0
    assert overlap_coefficient([], []) == 0.0


def test_overlap_coefficient_uses_min_as_denominator() -> None:
    a = [frozenset({"egg"}), frozenset({"flour"})]
    b = [frozenset({"egg"}), frozenset({"flour"}), frozenset({"milk"})]
    # both a-items match; denominator is min(2, 3) = 2
    assert overlap_coefficient(a, b) == 1.0


def test_greedy_matching_does_not_double_count_a_repeated_item() -> None:
    """A recipe listing 'salt' twice must not match a single 'salt' in the
    other recipe twice over — each catalog-side item is consumed once."""
    a = [frozenset({"salt"}), frozenset({"salt"}), frozenset({"pepper"})]
    b = [frozenset({"salt"}), frozenset({"oil"})]
    # only one of the two 'salt' entries in `a` can match the single 'salt'
    # in `b`; 'pepper' has nothing to match. matched = 1, denom = min(3,2)=2
    assert overlap_coefficient(a, b) == 0.5


# ---------------------------------------------------------------------------
# ingredient_items
# ---------------------------------------------------------------------------


def test_ingredient_items_dedupes_and_preserves_order() -> None:
    recipe = {
        "ingredients": [
            "1 tsp kosher salt",
            "1 cup olive oil",
            "1/2 tsp kosher salt",  # normalizes to the same set as the first
        ]
    }
    items = ingredient_items(recipe)
    assert items == [frozenset({"salt"}), frozenset({"olive", "oil"})]


def test_ingredient_items_skips_unnormalizable_entries() -> None:
    recipe = {"ingredients": ["2 tbsp", "1 cup flour"]}
    assert ingredient_items(recipe) == [frozenset({"flour"})]


# ---------------------------------------------------------------------------
# find_ingredient_overlaps / check_ingredient_overlap — real duplicate pair
# ---------------------------------------------------------------------------


def test_known_duplicate_pair_scores_above_threshold() -> None:
    a = ingredient_items(ROASTED_VEGGIE_EGG_CUPS)
    b = ingredient_items(ROASTED_VEGGIE_FRITTATA_CUPS)
    assert len(a) >= MIN_ITEMS and len(b) >= MIN_ITEMS
    assert overlap_coefficient(a, b) >= DUPLICATE_THRESHOLD


def test_check_ingredient_overlap_flags_known_duplicate() -> None:
    catalog = [ROASTED_VEGGIE_FRITTATA_CUPS, SPANAKOPITA_PHYLLO_CUPS]
    verdict = check_ingredient_overlap(ROASTED_VEGGIE_EGG_CUPS, catalog)
    assert verdict.status == "duplicate"
    assert verdict.best is not None
    assert verdict.best.title == "Roasted Veggie Frittata Cups"
    assert "Roasted Veggie Frittata Cups" in verdict.reason
    assert "2026-W16" in verdict.reason
    assert "ingredients shared" in verdict.reason


def test_unrelated_recipes_score_well_under_threshold() -> None:
    """Custard tart vs spanakopita — both well over MIN_ITEMS, no real
    overlap beyond a couple of pantry staples."""
    catalog = [SPANAKOPITA_PHYLLO_CUPS]
    verdict = check_ingredient_overlap(CARAMELIZED_CUSTARD_TART_CUPS, catalog)
    assert verdict.status == "clear"
    assert verdict.best is not None
    assert verdict.best.score < DUPLICATE_THRESHOLD


def test_thin_recipe_is_skipped_even_if_everything_matches() -> None:
    """A 7-ingredient recipe that is a word-for-word subset of a published
    recipe is still 'skipped_thin', never silently 'clear' or 'duplicate' —
    a thin recipe must be visible to the caller, not silently passed."""
    thin = {
        "title": "Tiny Egg Cups",
        "ingredients": [
            "8 large eggs",
            "1/2 cup milk",
            "1/2 tsp kosher salt",
            "1/4 tsp black pepper",
            "1/4 cup grated parmesan cheese",
            "1/2 cup shredded cheddar cheese",
            "2 tbsp chopped parsley",
        ],
    }
    assert len(ingredient_items(thin)) == 7
    verdict = check_ingredient_overlap(thin, [ROASTED_VEGGIE_EGG_CUPS])
    assert verdict.status == "skipped_thin"
    assert verdict.new_items == 7
    assert verdict.reason is not None and "7" in verdict.reason
    assert verdict.best is None


def test_seed_recipes_below_min_items_are_excluded_from_catalog_side() -> None:
    """Launch seed recipes (4 ingredients) must never count as a comparison
    target — MIN_ITEMS applies to both sides."""
    seed = {
        "slug": "seed-recipe",
        "title": "Seed Recipe",
        "episode_id": "seed",
        "ingredients": ["1 cup flour", "1 egg", "1 cup milk", "1 tsp salt"],
    }
    matches = find_ingredient_overlaps(ROASTED_VEGGIE_EGG_CUPS, [seed])
    assert matches == []


def test_exclude_episode_id_skips_own_catalog_entry() -> None:
    """Re-running the check on an already-published week (Monday retry)
    must not compare a recipe against itself in the catalog."""
    catalog = [ROASTED_VEGGIE_FRITTATA_CUPS]
    matches = find_ingredient_overlaps(
        ROASTED_VEGGIE_FRITTATA_CUPS,
        catalog,
        exclude_episode_id="2026-W16",
    )
    assert matches == []


def test_exclude_slug_skips_own_catalog_entry() -> None:
    catalog = [ROASTED_VEGGIE_FRITTATA_CUPS]
    matches = find_ingredient_overlaps(
        ROASTED_VEGGIE_FRITTATA_CUPS,
        catalog,
        exclude_slug="roasted-veggie-frittata-cups",
    )
    assert matches == []


def test_find_ingredient_overlaps_returns_full_sorted_distribution() -> None:
    catalog = [
        SPANAKOPITA_PHYLLO_CUPS,
        ROASTED_VEGGIE_FRITTATA_CUPS,
        CARAMELIZED_CUSTARD_TART_CUPS,
    ]
    matches = find_ingredient_overlaps(ROASTED_VEGGIE_EGG_CUPS, catalog)
    # every eligible catalog entry appears, not just ones above threshold
    assert len(matches) == 3
    scores = [m.score for m in matches]
    assert scores == sorted(scores, reverse=True)
    assert matches[0].title == "Roasted Veggie Frittata Cups"


def test_threshold_override_is_honored() -> None:
    catalog = [ROASTED_VEGGIE_FRITTATA_CUPS]
    # Real score is ~0.81 — with a threshold above that, it must read clear.
    verdict = check_ingredient_overlap(
        ROASTED_VEGGIE_EGG_CUPS, catalog, threshold=0.99
    )
    assert verdict.status == "clear"


def test_min_items_override_is_honored() -> None:
    thin = {
        "title": "Tiny Egg Cups",
        "ingredients": [
            "8 large eggs",
            "1/2 cup milk",
            "1/2 tsp kosher salt",
            "1/4 tsp black pepper",
            "1/4 cup grated parmesan cheese",
            "1/2 cup shredded cheddar cheese",
            "2 tbsp chopped parsley",
        ],
    }
    # 7 items would normally be skipped_thin at MIN_ITEMS=10; lowering the
    # bar to 5 makes it eligible for comparison.
    verdict = check_ingredient_overlap(
        thin, [ROASTED_VEGGIE_EGG_CUPS], min_items=5
    )
    assert verdict.status != "skipped_thin"
