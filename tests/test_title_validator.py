"""Tests for backend.utils.title_validator — #5911."""

from backend.utils.title_validator import (
    check_title_conflict,
    strip_redundant_mini,
)


# ---------------------------------------------------------------------------
# check_title_conflict
# ---------------------------------------------------------------------------

def test_no_conflict_with_empty_catalog() -> None:
    assert check_title_conflict("Caprese Bruschetta Bites", []) is None


def test_no_conflict_with_distinct_title() -> None:
    catalog = ["mini shepherd's pie pots", "baked oatmeal breakfast cups"]
    assert check_title_conflict("Caprese Bruschetta Bites", catalog) is None


def test_exact_match_conflict() -> None:
    catalog = ["roasted veggie egg cups"]
    reason = check_title_conflict("Roasted Veggie Egg Cups", catalog)
    assert reason is not None
    assert "exact match" in reason


def test_exact_match_is_case_insensitive() -> None:
    catalog = ["Roasted Veggie Egg Cups".lower()]
    assert check_title_conflict("ROASTED VEGGIE EGG CUPS", catalog) is not None


def test_real_w13_w14_overlap_is_caught() -> None:
    """The W13/W14 duplicate pair that triggered #5911 should register as a conflict."""
    catalog = ["roasted veggie frittata cups"]
    reason = check_title_conflict("Roasted Veggie Egg Cups", catalog)
    assert reason is not None
    assert "roasted veggie frittata cups" in reason


def test_distributed_distinctive_word_overlap_is_caught() -> None:
    """W19-style single distinctive overlaps should fail the Monday gate."""
    catalog = [
        "smoky cheddar breakfast bites",
        "roasted veggie frittata cups",
        "paprika sausage cups",
    ]
    reason = check_title_conflict("Paprika Cheddar Frittata Cups", catalog)
    assert reason is not None
    assert "distinctive word overlap" in reason


def test_w22_garden_frittata_egg_bites_hits_frittata_collision() -> None:
    catalog = [
        "Smoky Sweet Potato Frittatas",
        "Roasted Veggie Egg Cups",
        "Prosciutto Potato Egg Nests",
        "Mini Caprese Bruschetta Bites",
        "Smoky Cheddar Breakfast Bites",
    ]

    reason = check_title_conflict("Garden Frittata Egg Bites", catalog)

    assert reason is not None
    assert "smoky sweet potato frittatas" in reason.lower()
    assert "frittata" in reason


def test_no_shared_distinctive_words_is_allowed() -> None:
    """Distinct titles should pass when they do not share signal words."""
    catalog = ["chocolate chip decadence"]
    assert check_title_conflict("Caprese Bruschetta Bites", catalog) is None


def test_generic_category_words_do_not_cause_false_positives() -> None:
    """Breakfast/cup words describe format/category, not distinctive title repetition."""
    catalog = ["baked oatmeal breakfast cups"]
    assert check_title_conflict("Maple Bacon Breakfast Cups", catalog) is None


def test_stop_words_do_not_cause_false_positives() -> None:
    """Only overlap in 'Cups' (a stop word) should not flag a conflict."""
    catalog = ["baked oatmeal breakfast cups"]
    assert check_title_conflict("Lemon Meringue Cups", catalog) is None


def test_empty_title_flagged() -> None:
    assert check_title_conflict("", ["anything"]) == "title is empty"


def test_subset_title_is_caught() -> None:
    """A title that is a superset of an existing short title should conflict."""
    catalog = ["blueberry muffin tops"]
    reason = check_title_conflict("Vegan Blueberry Muffin Tops", catalog)
    assert reason is not None
    assert "blueberry" in reason.lower()


# ---------------------------------------------------------------------------
# strip_redundant_mini
# ---------------------------------------------------------------------------

def test_strip_mini_with_bites() -> None:
    assert strip_redundant_mini("Mini Caprese Bruschetta Bites") == "Caprese Bruschetta Bites"


def test_strip_mini_with_cups() -> None:
    assert strip_redundant_mini("Mini Lemon Meringue Cups") == "Lemon Meringue Cups"


def test_keep_mini_with_cakes() -> None:
    """Cakes aren't inherently small — Mini adds real meaning."""
    assert strip_redundant_mini("Mini Chocolate Lava Cakes") == "Mini Chocolate Lava Cakes"


def test_keep_mini_with_pots() -> None:
    """Pots are a serving vessel, not inherently small."""
    assert strip_redundant_mini("Mini Shepherd's Pie Pots") == "Mini Shepherd's Pie Pots"


def test_no_change_when_mini_not_leading() -> None:
    assert strip_redundant_mini("Chocolate Chip Decadence") == "Chocolate Chip Decadence"


def test_no_change_on_single_word() -> None:
    assert strip_redundant_mini("Mini") == "Mini"


def test_case_insensitive_mini_detection() -> None:
    assert strip_redundant_mini("mini caprese bruschetta bites") == "caprese bruschetta bites"
