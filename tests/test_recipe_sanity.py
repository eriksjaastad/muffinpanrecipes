"""Deterministic recipe sanity checks (#7099).

Until this module nothing in the project validated the recipe. The title
gate, the overlap gate and the muffin-pan form gate all ask "have we
published this dish already"; none asks whether a reader could cook it
safely.

The severity split here was not designed up front — it was measured. Every
check ran against all 36 stored episodes and every hit was read by hand.
Checks that fired only on genuinely broken recipes block. Checks whose hits
turned out to be perfectly good published recipes warn instead. The fixtures
in tests/fixtures/recipe_sanity/ are the specific episodes that decided each
threshold, committed because `data/episodes/*.json` is gitignored and the
evidence would otherwise not survive a clean checkout.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from backend.utils.recipe_sanity import (
    OVEN_TEMP_MAX_F,
    OVEN_TEMP_MIN_F,
    PAN_CAPACITY_CUPS_MAX,
    PAN_WELLS,
    check_recipe_sanity,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "recipe_sanity"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _recipe(**overrides) -> dict:
    """A minimal recipe that passes everything, for one-defect-at-a-time tests."""
    base = {
        "title": "Test Cups",
        "servings": 12,
        "cook_time": 20,
        "ingredients": [{"item": "all-purpose flour", "amount": "1 cup", "notes": ""}],
        "instructions": [
            "Preheat the oven to 375F.",
            "Divide the flour among the wells and bake 20 minutes.",
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# The corpus — no published recipe may be rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(p.stem for p in FIXTURES.glob("*.json")))
def test_real_published_recipes_are_never_blocked(name: str) -> None:
    """Every committed fixture is a recipe that actually shipped.

    This is the guard against over-tightening. A gate that rejects recipes
    the site already published is not ready, and on Monday each false
    positive costs a paid re-bake. If a future check breaks this test, the
    check is wrong until proven otherwise — not the fixture.
    """
    verdict = check_recipe_sanity(_fixture(name))
    assert verdict.status == "clear", (
        f"{name} is a published recipe but was blocked: {verdict.reason}"
    )


def test_local_episode_corpus_is_clear_when_present() -> None:
    """Same guard over the full local corpus, when it exists.

    `data/episodes/*.json` is gitignored, so a clean checkout has only a
    handful and CI has none of the interesting ones — that is exactly why
    the fixtures above exist. This runs the wider sweep on a working machine
    and skips rather than passing vacuously elsewhere.
    """
    episodes = sorted(pathlib.Path("data/episodes").glob("2026-W*.json"))
    recipes = []
    for path in episodes:
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        recipe = (data.get("stages", {}).get("monday", {}) or {}).get("recipe_data")
        if recipe:
            recipes.append((path.stem, recipe))
    if len(recipes) < 20:
        pytest.skip(f"only {len(recipes)} local episodes present; fixtures cover CI")
    blocked = [
        (name, check_recipe_sanity(r).reason)
        for name, r in recipes
        if check_recipe_sanity(r).blocking
    ]
    assert not blocked, f"published recipes blocked: {blocked}"


# ---------------------------------------------------------------------------
# Food safety — blocking
# ---------------------------------------------------------------------------


def test_raw_poultry_without_doneness_is_unsafe() -> None:
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "boneless chicken thighs", "amount": "1 lb", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Fill the wells and bake 20 minutes."],
        )
    )
    assert verdict.status == "unsafe"
    assert "doneness" in verdict.reason


def test_ground_beef_without_doneness_is_unsafe() -> None:
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "ground beef", "amount": "1 lb", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Fill the wells and bake 20 minutes."],
        )
    )
    assert verdict.status == "unsafe"


def test_internal_temperature_satisfies_doneness() -> None:
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "boneless chicken thighs", "amount": "1 lb", "notes": ""}],
            instructions=[
                "Preheat the oven to 375F.",
                "Bake until the internal temperature reaches 165F.",
            ],
        )
    )
    assert verdict.status == "clear"


@pytest.mark.parametrize(
    "fixture,phrasing",
    [
        ("w18_sausage_browned_in_skillet", "browned in a skillet first"),
        ("w30_ground_pork_loses_pink_and_fish_sauce", "loses its pink color"),
        ("w32_salmon_just_opaque_in_center", "just opaque in the center"),
    ],
)
def test_real_doneness_phrasings_are_accepted(fixture: str, phrasing: str) -> None:
    """Recipes verify doneness in prose, not in one canonical phrase.

    The first draft of DONENESS_PATTERNS required "no longer pink" and
    "opaque throughout" and flagged all three of these published recipes as
    unsafe. They all verify doneness — just in their own words.
    """
    verdict = check_recipe_sanity(_fixture(fixture))
    assert verdict.status == "clear", f"rejected despite {phrasing}: {verdict.reason}"


def test_fish_sauce_is_not_a_raw_protein() -> None:
    """A condiment matched the bare `fish` pattern and flagged W30 unsafe."""
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[
                {"item": "fish sauce", "amount": "1 tbsp", "notes": ""},
                {"item": "all-purpose flour", "amount": "1 cup", "notes": ""},
            ],
            instructions=[
                "Preheat the oven to 375F.",
                "Whisk the fish sauce into the flour and bake 20 minutes.",
            ],
        )
    )
    assert verdict.status == "clear", verdict.reason


# ---------------------------------------------------------------------------
# Oven temperature — blocking
# ---------------------------------------------------------------------------


def test_missing_oven_temperature_is_unsafe() -> None:
    verdict = check_recipe_sanity(
        _recipe(instructions=["Mix everything.", "Bake until golden."])
    )
    assert verdict.status == "unsafe"
    assert "no oven temperature" in verdict.reason


def test_oven_temperature_above_ceiling_is_unsafe() -> None:
    verdict = check_recipe_sanity(
        _recipe(instructions=["Preheat the oven to 650F.", "Bake 20 minutes."])
    )
    assert verdict.status == "unsafe"
    assert str(OVEN_TEMP_MAX_F) in verdict.reason


def test_exactly_the_ceiling_is_allowed() -> None:
    """W36 baked at exactly 500F and published fine — the bound is inclusive."""
    verdict = check_recipe_sanity(_fixture("w36_bakes_at_exactly_500f"))
    assert verdict.status == "clear"
    assert verdict.details["oven_temp_f"] == OVEN_TEMP_MAX_F


def test_only_sub_oven_temperatures_reports_the_right_reason() -> None:
    """The reason string becomes the baker's retry constraint, so it must be
    accurate: "no temperature at all" and "all temperatures too low" need
    different fixes."""
    verdict = check_recipe_sanity(
        _recipe(instructions=["Preheat the oven to 150F.", "Bake 20 minutes."])
    )
    assert verdict.status == "unsafe"
    assert "too low" in verdict.reason
    assert str(OVEN_TEMP_MIN_F) in verdict.reason


def test_doneness_temperature_is_not_read_as_an_oven_temperature() -> None:
    """A 165F internal reading must not satisfy the oven-temperature check."""
    verdict = check_recipe_sanity(
        _recipe(
            instructions=[
                "Fill the wells.",
                "Bake until the internal temperature reaches 165F.",
            ]
        )
    )
    assert verdict.status == "unsafe"
    assert "oven temperature" in verdict.reason


# ---------------------------------------------------------------------------
# Yield and structure — blocking
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("servings", [0, 2, 40, 100])
def test_implausible_yield_is_blocked(servings: int) -> None:
    verdict = check_recipe_sanity(_recipe(servings=servings))
    assert verdict.status == "implausible"
    assert str(PAN_WELLS) in verdict.reason


def test_missing_recipe_is_reported_not_raised() -> None:
    assert check_recipe_sanity(None).status == "implausible"
    assert check_recipe_sanity({}).status == "implausible"
    # Deliberately the wrong type. The signature says dict | None, but this
    # runs on whatever the baker's parser produced, and the contract is that
    # a malformed recipe comes back as a verdict rather than an exception
    # that would take down the cron handler.
    assert check_recipe_sanity([]).status == "implausible"  # type: ignore[arg-type]


def test_no_instructions_is_blocked() -> None:
    assert check_recipe_sanity(_recipe(instructions=[])).status == "implausible"


# ---------------------------------------------------------------------------
# Advisory — must never block
# ---------------------------------------------------------------------------


def test_bulky_ingredients_warn_but_do_not_block() -> None:
    """Summed ingredient volume is not batter volume.

    W27's raw spinach measures ~7.9 cups against a 6-cup pan and wilts to
    almost nothing. Volume is not additive, produce collapses, and plenty of
    listed ingredients never enter a well. Blocking on this rejected four
    published recipes.
    """
    verdict = check_recipe_sanity(_fixture("w27_bulky_spinach_overcounts_volume"))
    assert verdict.status == "clear"
    assert verdict.details["measured_cups"] > PAN_CAPACITY_CUPS_MAX
    assert any("overflow" in w for w in verdict.warnings)


def test_ingredient_named_by_a_synonym_warns_but_does_not_block() -> None:
    """W28 lists "Parmigiano-Reggiano or Kefalotyri"; the method says
    "grated Parmesan". Same cheese, and a cook follows it fine."""
    verdict = check_recipe_sanity(_fixture("w28_parmigiano_named_parmesan_in_method"))
    assert verdict.status == "clear"
    assert any("never used" in w for w in verdict.warnings)


def test_plural_ingredients_are_not_reported_unused() -> None:
    """`normalize_ingredient` singularizes; the method's words must be
    singularized too or every plural ingredient reads as unused. That bug
    flagged 8 of 36 stored episodes."""
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "eggs", "amount": "4", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Whisk the eggs and bake 20 minutes."],
        )
    )
    assert verdict.status == "clear"
    assert not verdict.warnings


def test_cook_time_disagreement_warns_only() -> None:
    """`_parse_recipe_response` seeds cook_time=20 and overwrites it only if
    the model emitted the line, so a mismatch usually means the field was
    never set rather than that the recipe is wrong."""
    verdict = check_recipe_sanity(
        _recipe(
            cook_time=5,
            instructions=["Preheat the oven to 375F.", "Bake for 45 minutes."],
        )
    )
    assert verdict.status == "clear"
    assert any("cook_time" in w for w in verdict.warnings)


def test_warnings_never_set_blocking() -> None:
    verdict = check_recipe_sanity(_fixture("w27_bulky_spinach_overcounts_volume"))
    assert verdict.warnings
    assert not verdict.blocking


# ---------------------------------------------------------------------------
# Food safety, second pass — the holes adversarial review found
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item",
    [
        "raw bacon, diced",
        "Italian sausage, crumbled raw",
        "smoked ham, diced",
        "duck breast, diced",
        "chorizo, raw",
        "ground venison",
        "raw lamb, diced",
        "veal cutlets, diced",
        "bay scallops",
        "lump crab meat",
        "raw tuna, cubed",
    ],
)
def test_every_risk_protein_is_covered(item: str) -> None:
    """The first protein list held only chicken/turkey/pork/ground beef/
    shrimp/fish/salmon, so all of these skipped the safety check entirely.

    Bacon and sausage matter most: this is a muffin-pan breakfast site, so
    they are the likeliest raw proteins it will ever publish, and they were
    the ones going unchecked.
    """
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": item, "amount": "1 lb", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Fill the wells and bake 20 minutes."],
        )
    )
    assert verdict.status == "unsafe", f"{item} passed without a doneness check"


def test_weak_doneness_word_elsewhere_does_not_clear_the_protein() -> None:
    """"Browned" describing the cheese must not vouch for raw chicken.

    Weak doneness words are ordinary cooking vocabulary; something in almost
    any recipe satisfies them. Matched anywhere, they made the food-safety
    check decorative.
    """
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "boneless chicken thighs", "amount": "1 lb", "notes": ""}],
            instructions=[
                "Preheat the oven to 375F.",
                "Fill the wells and bake until the cheese tops are browned.",
            ],
        )
    )
    assert verdict.status == "unsafe"


def test_weak_doneness_in_the_protein_step_does_clear_it() -> None:
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "boneless chicken thighs", "amount": "1 lb", "notes": ""}],
            instructions=[
                "Preheat the oven to 375F.",
                "Brown the chicken in a skillet until cooked through.",
                "Divide among the wells and bake 20 minutes.",
            ],
        )
    )
    assert verdict.status == "clear", verdict.reason


def test_strong_doneness_counts_anywhere() -> None:
    """A thermometer reading is unambiguous and needs no proximity rule."""
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "boneless chicken thighs", "amount": "1 lb", "notes": ""}],
            instructions=[
                "Preheat the oven to 375F.",
                "Bake until an instant-read thermometer reads 165F.",
            ],
        )
    )
    assert verdict.status == "clear"


def test_stovetop_only_protein_still_needs_doneness() -> None:
    """The no-bake carve-out skips the OVEN check, not the SAFETY check."""
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "boneless chicken thighs", "amount": "1 lb", "notes": ""}],
            instructions=["Sear the chicken in a skillet.", "Divide among the wells and chill."],
        )
    )
    assert verdict.status == "unsafe"


# ---------------------------------------------------------------------------
# Raw egg without heat
# ---------------------------------------------------------------------------


def test_no_bake_raw_egg_is_unsafe() -> None:
    """Eggs are exempt from the risk-protein list because baking makes them
    safe. A mousse set in the fridge never bakes, so the exemption has to
    stop there."""
    verdict = check_recipe_sanity(
        {
            "title": "Tiramisu Cups",
            "servings": 12,
            "ingredients": [{"item": "egg yolks", "amount": "4", "notes": ""}],
            "instructions": [
                "Whisk the yolks with sugar until pale.",
                "Fold into the mascarpone and divide among the wells.",
                "Chill 4 hours until set.",
            ],
        }
    )
    assert verdict.status == "unsafe"
    assert "pasteurized" in verdict.reason


def test_pasteurized_egg_clears_the_no_bake_case() -> None:
    verdict = check_recipe_sanity(
        {
            "title": "Tiramisu Cups",
            "servings": 12,
            "ingredients": [{"item": "pasteurized egg yolks", "amount": "4", "notes": ""}],
            "instructions": [
                "Whisk the yolks with sugar until pale.",
                "Chill 4 hours until set.",
            ],
        }
    )
    assert verdict.status == "clear", verdict.reason


def test_baked_egg_recipe_is_not_flagged_raw() -> None:
    """The ordinary case — every egg recipe this site publishes."""
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "eggs", "amount": "4", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Whisk the eggs and bake 20 minutes."],
        )
    )
    assert verdict.status == "clear"


@pytest.mark.parametrize(
    "item",
    [
        "raw cod fillet", "halibut steak", "rainbow trout", "tilapia fillet",
        "raw oysters", "mussels", "littleneck clams", "lobster meat",
        "hamburger patty, raw", "raw beef steak, diced",
        "quail breast", "goat shoulder", "rabbit loin",
    ],
)
def test_species_named_proteins_are_covered(item: str) -> None:
    """A bare `fish` pattern matches only the literal word "fish".

    Every named species other than salmon went unchecked — a recipe built on
    raw cod or halibut got no doneness scrutiny at all, even though `fish`
    sat in the pattern list looking like it covered them. Same for shellfish
    beyond shrimp, and for whole-cut beef (only `ground beef` was covered).
    """
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": item, "amount": "1 lb", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Fill the wells and bake 20 minutes."],
        )
    )
    assert verdict.status == "unsafe", f"{item} passed without a doneness check"


@pytest.mark.parametrize("pantry", ["chicken broth", "beef stock", "fish sauce"])
def test_pantry_staples_are_not_raw_proteins(pantry: str) -> None:
    """Stock, broth and fish sauce are shelf-stable, not raw protein.

    Widening the protein list to bare `beef` and species names would have
    started flagging these without the lookaheads.
    """
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[
                {"item": pantry, "amount": "1 cup", "notes": ""},
                {"item": "all-purpose flour", "amount": "1 cup", "notes": ""},
            ],
            instructions=[
                "Preheat the oven to 375F.",
                f"Whisk the {pantry} into the flour and bake 20 minutes.",
            ],
        )
    )
    assert verdict.status == "clear", verdict.reason


def test_cooking_spray_does_not_count_as_cooking() -> None:
    """Greasing the liners says nothing about whether the filling is cooked.

    18 of 39 stored episodes mention cooking spray, almost always as pan
    prep. Matching it as a heat step satisfied the raw-egg check on nearly
    half this site's real output — in exactly the no-bake case the check
    exists to catch.
    """
    verdict = check_recipe_sanity(
        {
            "title": "Tiramisu Cups",
            "servings": 12,
            "ingredients": [
                {"item": "raw egg yolks", "amount": "4", "notes": ""},
                {"item": "nonstick cooking spray", "amount": "1", "notes": ""},
            ],
            "instructions": [
                "Lightly coat 12 silicone liners with nonstick cooking spray.",
                "Whisk the yolks with sugar and fold into the mascarpone.",
                "Refrigerate at least 4 hours until set.",
            ],
        }
    )
    assert verdict.status == "unsafe"
    assert "pasteurized" in verdict.reason


@pytest.mark.parametrize(
    "pantry",
    [
        "clam juice", "squid ink", "steak sauce", "oyster sauce",
        "hamburger bun crumbs", "beef-flavored bouillon cube",
        "chicken-flavored broth", "fish stock",
    ],
)
def test_products_made_from_a_protein_are_not_raw_protein(pantry: str) -> None:
    """Widening to bare species and cut names caught their pantry products too.

    Same failure class the `fish sauce` lookahead was built for. These cost a
    wasted re-bake rather than reader safety, but a Monday false positive can
    exhaust the three attempts and pause the week — which is the failure this
    whole session started with.
    """
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[
                {"item": pantry, "amount": "1 cup", "notes": ""},
                {"item": "all-purpose flour", "amount": "1 cup", "notes": ""},
            ],
            instructions=[
                "Preheat the oven to 375F.",
                f"Whisk the {pantry} into the flour and bake 20 minutes.",
            ],
        )
    )
    assert verdict.status == "clear", verdict.reason


@pytest.mark.parametrize(
    "item",
    ["littleneck clams", "raw squid, cleaned", "raw beef steak, diced",
     "hamburger patty, raw", "raw oysters"],
)
def test_the_pantry_lookaheads_do_not_excuse_the_real_protein(item: str) -> None:
    """The lookahead must suppress `clam juice`, not `clams`."""
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": item, "amount": "1 lb", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Fill the wells and bake 20 minutes."],
        )
    )
    assert verdict.status == "unsafe"


def test_hyphenated_precooked_is_accepted() -> None:
    """Grocery labelling hyphenates; `fully\\s+cooked` missed "fully-cooked"."""
    verdict = check_recipe_sanity(
        _recipe(
            ingredients=[{"item": "fully-cooked sausage links", "amount": "1 lb", "notes": ""}],
            instructions=["Preheat the oven to 375F.", "Fill the wells and bake 20 minutes."],
        )
    )
    assert verdict.status == "clear", verdict.reason
