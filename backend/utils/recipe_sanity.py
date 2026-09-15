"""Deterministic sanity checks on the recipe itself (#7099).

Every gate this project had before this one asks "have we published this
dish already" — `title_validator` on the name, `recipe_overlap` on the
ingredients, `muffin_pan_form` on the shape. None of them asks whether the
recipe is *cookable*, and none asks whether it is *safe*. A reader who
follows a published recipe is the only person this project can actually
harm, and until this module nothing stood between them and whatever the
model emitted on Monday.

Deterministic on purpose. The editorial QA reviewer's prompt already claims
to check temperatures, plausibility and ingredient-instruction match (rules
6-8), but it is an LLM reading prose with no ground truth, and W36 passed it
with a 500F bake. These checks cannot hallucinate and cost nothing, which is
what makes tightening those prompt rules worth doing later.

House contract, same as `check_muffin_pan_form` and `check_ingredient_overlap`:
a pure function that returns a verdict, never raises. The caller decides
whether to retry, block, or log.

Two facts make this possible, both verified across all 36 stored episodes:
ingredients are uniformly `{item, amount, notes}` (619/619), and while oven
temperature exists only as prose inside instruction strings, it is present
in 36/36 episodes and regex-extractable.

KNOWN LIMIT, stated rather than papered over. Weak doneness evidence is
accepted when it appears in the same instruction step as the protein, which
is a proxy for "describes the protein" and not the same thing. A single
compound sentence naming both defeats it:

    "Stir the raw chicken into the glaze until the glaze looks glossy and
     browned, then divide among the wells and bake 20 minutes."

reads as clear, because "browned" and "chicken" share a step even though the
word describes the glaze. Separating those needs to parse what the adjective
attaches to, which regex cannot do. This is deliberately left to the LLM
editorial reviewer, whose rules 6-8 cover exactly this kind of judgement —
the point of this module is to give that reviewer a floor it cannot fall
below, not to replace it. Do not widen WEAK_DONENESS_PATTERNS to compensate;
that trades a narrow hole for a wide one.

SECOND KNOWN LIMIT, same category. The pantry lookaheads on the risk-protein
patterns ("chicken" but not "chicken stock") suppress the match only when the
excluded word follows immediately, so an ingredient named backwards past the
qualifier defeats them: "chicken stock-braised breast, raw" reads as clear.
No ingredient in the stored corpus is phrased that way — they are uniformly
cut-or-form first ("boneless chicken thighs", "raw salmon fillet") — and this
is the irreducible residual of any exclusion list rather than a defect in a
particular pattern. Adding another guard per adversarial phrasing moves the
target instead of closing the category, so it is recorded here rather than
chased.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from backend.utils.recipe_overlap import _singularize, normalize_ingredient

# ---------------------------------------------------------------------------
# Pan geometry
# ---------------------------------------------------------------------------
# These numbers existed only as prose inside the baker's system prompt
# (recipe_prompts.py, "ENVISION THE PAN FIRST": twelve wells, about 2 3/4
# inches across the top tapering to 2 inches, each holding roughly 1/3 to
# 1/2 cup). Nothing could check them while they lived in a string. They are
# constants now so a capacity check can exist at all.
PAN_WELLS = 12
WELL_CAPACITY_CUPS_MIN = 1.0 / 3.0
WELL_CAPACITY_CUPS_MAX = 0.5
PAN_CAPACITY_CUPS_MAX = PAN_WELLS * WELL_CAPACITY_CUPS_MAX  # 6.0

# A yield outside this band is not a 12-cup muffin pan recipe. Mini and
# jumbo pans exist, hence the width; zero or 40 does not.
SERVINGS_MIN = 6
SERVINGS_MAX = 24

# Oven temperature bounds. 250F is below anything that bakes; above 500F is
# past a domestic oven's useful range and past what these recipes have ever
# used. W36 baked at exactly 500F, so the ceiling is live on real data.
OVEN_TEMP_MIN_F = 250
OVEN_TEMP_MAX_F = 500
# Below this a three-digit F reading is a doneness temperature (145/160/165),
# not an oven setting, so it must not be read as one.
_OVEN_TEMP_FLOOR_F = 200

# ---------------------------------------------------------------------------
# Food safety
# ---------------------------------------------------------------------------
# Proteins that must reach a verified internal temperature.
#
# Widened after review: the first list held only chicken/turkey/pork/ground
# beef/shrimp/fish/salmon, so raw bacon, generic sausage, ham, duck, chorizo,
# lamb, veal and game skipped the safety check entirely. Bacon and sausage are
# the most plausible ingredients on a muffin-pan breakfast site, which made
# that the worst possible gap to leave.
#
# Eggs and dairy are deliberately absent: they appear in nearly every recipe
# here and are safe once baked. The no-heat case they leave open is handled
# separately by _check_raw_egg below.
RISK_PROTEIN_PATTERNS = (
    # Poultry and game birds. The lookaheads keep pantry products made FROM a
    # protein - stock, bouillon, clam juice, squid ink, steak sauce, hamburger
    # buns - from demanding a doneness check they have no use for. They accept
    # a hyphen as well as a space because grocery labelling hyphenates freely
    # ("beef-flavored bouillon"), and a whitespace-only lookahead let that
    # through as a raw protein.
    r"\bchicken\b(?![\s-]+(?:broth|stock|bouillon|base|powder|flavou?red))",
    r"\bturkey\b",
    r"\bduck\b",
    r"\bquail\b",
    # Pork and cured-or-not pork products. Bacon and sausage matter most here:
    # this is a muffin-pan breakfast site, so they are the likeliest raw
    # proteins it will ever publish, and both went unchecked in the first pass.
    r"\bpork\b",
    r"\bbacon\b",
    r"\bham\b",
    r"\bchorizo\b",
    r"\bsausages?\b",
    r"\bpancetta\b",
    # Red meat and game. Bare "beef" rather than only "ground beef" - a raw
    # patty or diced steak in a sliders concept skipped the check entirely.
    r"\bbeef\b(?![\s-]+(?:broth|stock|bouillon|base|consomm|flavou?red))",
    r"\bsteak\b(?![\s-]+sauce)",
    r"\bhamburger\b(?![\s-]+(?:bun|roll))",
    r"\blamb\b",
    r"\bveal\b",
    r"\bvenison\b",
    r"\bbison\b",
    r"\bgoat\b",
    r"\brabbit\b",
    r"\belk\b",
    r"\bboar\b",
    r"\bground\s+(?:meat|poultry)\b",
    # Seafood. The bare "fish" pattern only ever matched the literal word, so
    # every named species other than salmon went unchecked - a recipe built on
    # raw cod or halibut got no scrutiny at all.
    r"\bfish\b(?![\s-]+(?:sauce|stock|broth|flavou?red))",
    r"\bsalmon\b",
    r"\btuna\b",
    r"\bcod\b",
    r"\bhalibut\b",
    r"\btrout\b",
    r"\btilapia\b",
    r"\bhaddock\b",
    r"\bsnapper\b",
    r"\bmahi\b",
    r"\bsole\b",
    r"\bcatfish\b",
    r"\bshrimp\b",
    r"\bprawns?\b",
    r"\bscallops?\b",
    r"\bcrab\b",
    r"\blobster\b",
    r"\boysters?\b(?![\s-]+sauce)",
    r"\bmussels?\b",
    r"\bclams?\b(?![\s-]+juice)",
    r"\bsquid\b(?![\s-]+ink)",
    r"\bcalamari\b",
    r"\boctopus\b",
)

# Doneness evidence, split by how much it actually proves.
#
# STRONG signals establish doneness wherever they appear. A thermometer
# reading or an explicit "pre-cooked" is unambiguous and does not need to sit
# next to the protein to mean what it says.
STRONG_DONENESS_PATTERNS = (
    r"\binternal\s+temp(?:erature)?\b",
    r"\b1[4-7]\d\s*°?\s*F\b",          # 140-179F covers 145/160/165
    r"\binstant-read\b",
    r"\bthermometer\b",
    r"\bfully[\s-]+cooked\b",
    r"\bpre-?cooked\b",
    r"\balready[\s-]+cooked\b",
)

# WEAK signals are ordinary cooking words that mean doneness only when they
# describe the protein. Matched anywhere in the recipe they are trivially
# satisfied by something else - "bake until the cheese tops are browned"
# cleared raw chicken before this split - so they count only in a step that
# also names the protein.
WEAK_DONENESS_PATTERNS = (
    r"\bcooked\s+through\b",
    r"\bno\s+longer\s+pink\b",
    r"\bloses?\s+(?:its\s+)?pink\b",
    r"\bopaque\b",
    r"\bflakes?\s+easily\b",
    r"\bbrowned?\b",
    r"\bcrisp(?:s|ed|y)?\b",
)

# Raw egg is safe once baked, which is why eggs are not risk proteins - but
# that reasoning only holds if heat is actually applied. A no-bake custard or
# mousse built on raw yolk needs pasteurized egg.
EGG_PATTERNS = (r"\begg\b", r"\beggs\b", r"\byolks?\b", r"\bwhites?\b")
PASTEURIZED_PATTERN = r"\bpasteuri[sz]ed\b"

# Any heat step at all, oven or not: a stovetop sear cooks a protein as well
# as an oven does, so the raw-egg check must not fire on a recipe that cooks
# on the hob.
HEAT_METHOD_PATTERNS = (
    r"\bbake[sd]?\b", r"\bbaking\b", r"\boven\b", r"\broast(?:s|ed|ing)?\b",
    r"\bbroil(?:s|ed|ing)?\b", r"\bpreheat\b", r"\bsaut[e\u00e9]\b",
    r"\bsear(?:s|ed|ing)?\b", r"\bfry(?:ing)?\b", r"\bfried\b",
    r"\bsimmer(?:s|ed|ing)?\b", r"\bboil(?:s|ed|ing)?\b", r"\bskillet\b",
    r"\bsaucepan\b", r"\bpoach(?:es|ed|ing)?\b", r"\bsteam(?:s|ed|ing)?\b",
        # NOT "cooking spray" - that is liner prep and says nothing about
    # whether the filling is cooked. 18 of 39 stored episodes mention it,
    # so without this lookahead the raw-egg check is satisfied by greasing
    # boilerplate in nearly half the site's real output.
    r"\bcook(?:s|ed|ing)?\b(?![\s-]+spray)",
)

# Evidence the recipe actually uses an oven. Absent these, it is a no-bake
# recipe and has no business being asked for a baking temperature.
OVEN_METHOD_PATTERNS = (
    r"\bbake[sd]?\b",
    r"\bbaking\b",
    r"\boven\b",
    r"\broast(?:s|ed|ing)?\b",
    r"\bbroil(?:s|ed|ing)?\b",
    r"\bpreheat\b",
)

# Volumetric units we can convert with confidence. Weight and count units
# are deliberately absent — see _volumetric_cups.
_CUPS_PER_UNIT = {
    "cup": 1.0,
    "cups": 1.0,
    "tbsp": 1.0 / 16.0,
    "tbsps": 1.0 / 16.0,
    "tablespoon": 1.0 / 16.0,
    "tablespoons": 1.0 / 16.0,
    "tsp": 1.0 / 48.0,
    "tsps": 1.0 / 48.0,
    "teaspoon": 1.0 / 48.0,
    "teaspoons": 1.0 / 48.0,
    "quart": 4.0,
    "quarts": 4.0,
    "pint": 2.0,
    "pints": 2.0,
    "ml": 1.0 / 236.6,
    "l": 4.227,
    "liter": 4.227,
    "liters": 4.227,
}

_UNICODE_FRACTIONS = {
    "½": 0.5, "¼": 0.25, "¾": 0.75,
    "⅓": 1.0 / 3.0, "⅔": 2.0 / 3.0, "⅛": 0.125,
}

_TEMP_RE = re.compile(r"(\d{3})\s*°?\s*F\b", re.IGNORECASE)
_MINUTES_RE = re.compile(r"(\d+)(?:\s*(?:-|–|to)\s*(\d+))?\s*minutes?\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(
    r"^\s*(?P<qty>\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|[½¼¾⅓⅔⅛])?\s*"
    r"(?P<frac>[½¼¾⅓⅔⅛])?\s*"
    r"(?P<unit>[a-zA-Z]+)?",
)


@dataclass(frozen=True)
class RecipeVerdict:
    """Outcome of the sanity checks.

    Mirrors `recipe_overlap.OverlapVerdict`, which mirrors
    `title_validator.check_title_conflict` — a reason string the caller can
    log and feed back to the baker as a retry constraint.

    `status` is "clear" when nothing blocking was found. `unsafe` means a
    food-safety failure and must never be published. `implausible` means the
    recipe does not describe something a cook could follow. `warnings` is
    always advisory and never blocks, so a caller can surface it without
    branching on it.

    The split between blocking and advisory was set by running these checks
    over all 36 stored episodes and reading every hit. Only checks with zero
    false positives on that corpus block. Everything whose failures turned
    out to be good recipes - summed ingredient volume, unused ingredients,
    cook_time disagreement - warns instead. A gate that cries wolf on real
    recipes gets ignored, and on Monday it also costs a paid re-bake.
    """

    status: Literal["clear", "unsafe", "implausible"]
    reason: str = ""
    issues: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.status != "clear"


def _instructions_text(recipe: dict[str, Any]) -> str:
    return " ".join(str(s) for s in (recipe.get("instructions") or []))


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def _parse_quantity(qty: str | None, frac: str | None) -> float | None:
    """Parse '1', '1/2', '1 1/2', '1.5', or a unicode fraction into a float."""
    total = 0.0
    seen = False
    if qty:
        qty = qty.strip()
        if qty in _UNICODE_FRACTIONS:
            total += _UNICODE_FRACTIONS[qty]
            seen = True
        elif " " in qty:  # "1 1/2"
            whole, _, rest = qty.partition(" ")
            try:
                total += float(whole)
                num, _, den = rest.partition("/")
                total += float(num) / float(den)
                seen = True
            except (ValueError, ZeroDivisionError):
                return None
        elif "/" in qty:
            num, _, den = qty.partition("/")
            try:
                total += float(num) / float(den)
                seen = True
            except (ValueError, ZeroDivisionError):
                return None
        else:
            try:
                total += float(qty)
                seen = True
            except ValueError:
                return None
    if frac and frac in _UNICODE_FRACTIONS:
        total += _UNICODE_FRACTIONS[frac]
        seen = True
    return total if seen else None


def _volumetric_cups(amount: str) -> float | None:
    """Cups for an amount string, or None when it isn't clearly volumetric.

    Returns None for weights ("8 oz"), counts ("4"), and anything it cannot
    parse. That under-counts total volume on purpose: the capacity check
    only flags when the portion it is *certain* about already overflows the
    pan, so an unparseable ingredient can never cause a false rejection.
    """
    m = _AMOUNT_RE.match(amount or "")
    if not m:
        return None
    unit = (m.group("unit") or "").lower()
    if unit not in _CUPS_PER_UNIT:
        return None
    qty = _parse_quantity(m.group("qty"), m.group("frac"))
    if qty is None:
        return None
    return qty * _CUPS_PER_UNIT[unit]


def _check_food_safety(recipe: dict[str, Any], instructions: str) -> str | None:
    """A risk protein must have its doneness verified, near the protein itself.

    Strong evidence (a thermometer reading, "pre-cooked") counts anywhere.
    Weak evidence — "browned", "opaque", "crisp" — counts only in a step that
    also names the protein, because those are ordinary cooking words that
    something else in the recipe will almost always satisfy. Before that
    proximity rule, "bake until the cheese tops are browned" cleared a raw
    chicken filling.
    """
    ingredient_text = " ".join(
        str(i.get("item", "")) if isinstance(i, dict) else str(i)
        for i in (recipe.get("ingredients") or [])
    )
    if not _matches_any(ingredient_text, RISK_PROTEIN_PATTERNS):
        return None

    chef_notes = str(recipe.get("chef_notes") or "")
    if _matches_any(f"{ingredient_text} {instructions} {chef_notes}",
                    STRONG_DONENESS_PATTERNS):
        return None

    # Weak evidence, but only where it is actually talking about the protein.
    steps = [str(s) for s in (recipe.get("instructions") or [])]
    if chef_notes:
        steps.append(chef_notes)
    for step in steps:
        if _matches_any(step, RISK_PROTEIN_PATTERNS) and _matches_any(
            step, WEAK_DONENESS_PATTERNS
        ):
            return None

    return (
        "recipe contains a raw protein (poultry, pork, bacon, sausage, ground "
        "meat, game, or seafood) but never verifies its doneness; state an "
        "internal temperature (165F poultry, 160F ground meat, 145F pork/fish) "
        "in the same step that cooks it, or say explicitly that it goes in "
        "pre-cooked"
    )


def _check_raw_egg(recipe: dict[str, Any], instructions: str) -> str | None:
    """Raw egg is fine baked. It is not fine chilled and served.

    Eggs are excluded from the risk-protein list because they are in nearly
    every recipe here and baking makes them safe. That reasoning collapses in
    a no-bake recipe — a mousse or custard set in the fridge never heats the
    yolk at all — so this covers the case the exclusion leaves open.
    """
    ingredient_text = " ".join(
        str(i.get("item", "")) if isinstance(i, dict) else str(i)
        for i in (recipe.get("ingredients") or [])
    )
    if not _matches_any(ingredient_text, EGG_PATTERNS):
        return None
    if _matches_any(instructions, HEAT_METHOD_PATTERNS):
        return None
    haystack = f"{ingredient_text} {instructions} {recipe.get('chef_notes') or ''}"
    if re.search(PASTEURIZED_PATTERN, haystack, re.IGNORECASE):
        return None
    return (
        "recipe uses egg but never heats anything, so the egg is served raw; "
        "specify pasteurized eggs or add a cooking step"
    )


def _check_oven_temp(instructions: str) -> tuple[str | None, int | None]:
    """Oven temperature must be present and sane — for recipes that bake.

    A no-bake recipe (icebox cups, panna cotta, anything that sets in the
    fridge) legitimately has no oven temperature, and demanding one would
    hard-block it and burn three re-bakes before pausing the week. So the
    check only applies when the method actually heats something. If the
    recipe says "bake" and then never says how hot, that IS a defect.
    """
    if not _matches_any(instructions, OVEN_METHOD_PATTERNS):
        return None, None

    temps = [int(t) for t in _TEMP_RE.findall(instructions)]
    # Doneness temperatures are not oven temperatures; a 165F internal
    # reading must not be mistaken for a 165F oven.
    oven_temps = [t for t in temps if t >= _OVEN_TEMP_FLOOR_F]
    if not oven_temps:
        # The reason string is fed back to the baker as a retry constraint,
        # so it has to say the right thing. "No temperature at all" and
        # "every temperature found is too low to be an oven" call for
        # different fixes.
        if temps:
            return (
                f"no usable oven temperature; the only temperatures in the "
                f"instructions are {sorted(set(temps))}F, all too low to bake "
                f"at (minimum {OVEN_TEMP_MIN_F}F)",
                max(temps),
            )
        return (
            "no oven temperature appears anywhere in the instructions; a "
            "reader cannot bake this",
            None,
        )
    hottest = max(oven_temps)
    if hottest > OVEN_TEMP_MAX_F:
        return (
            f"oven temperature {hottest}F exceeds {OVEN_TEMP_MAX_F}F, past a "
            f"domestic oven's useful range",
            hottest,
        )
    if hottest < OVEN_TEMP_MIN_F:
        return (
            f"oven temperature {hottest}F is below {OVEN_TEMP_MIN_F}F and will "
            f"not bake",
            hottest,
        )
    return None, hottest


def _check_ingredient_use(recipe: dict[str, Any], instructions: str) -> list[str]:
    """Every ingredient should be used, and no step should invent one.

    Uses `recipe_overlap.normalize_ingredient` and `items_match` rather than
    a second parser — they already handle quantities, units, descriptors and
    singularization, and `items_match`'s subset rule is exactly right here
    ("cheddar" in a step matches "sharp cheddar cheese" in the list).
    """
    issues: list[str] = []
    # Singularize the method's words the same way normalize_ingredient
    # singularizes the ingredient's. Without this every plural ingredient
    # reads as unused - "eggs" normalizes to {egg} and would never match the
    # step that says "eggs". That bug flagged 8 of 36 stored episodes before
    # calibration caught it.
    instruction_words = frozenset(
        _singularize(w) for w in re.findall(r"[a-z][a-z\-']+", instructions.lower())
    )
    for raw in recipe.get("ingredients") or []:
        normalized = normalize_ingredient(raw)
        if normalized is None:
            continue
        item = raw.get("item") if isinstance(raw, dict) else str(raw)
        # Used when ANY identifying word appears in the method: a step
        # saying "cheddar" uses "sharp cheddar cheese", and a step saying
        # "fold in the herbs" is allowed to stand for chives.
        if not (normalized & instruction_words):
            issues.append(f"ingredient '{item}' is never used in the instructions")
    return issues


def _check_capacity(recipe: dict[str, Any]) -> tuple[str | None, float]:
    """Advisory only. Summed ingredient volume is NOT batter volume.

    The first draft blocked on this and flagged four published recipes:
    W10-bookend-a at 8.5 cups, W16 at 6.4, W27 at 7.9, W10-bookend-b at 6.2.
    All four were fine. Volume is not additive (dry absorbs wet), bulky
    produce collapses (W27's spinach wilts to almost nothing), and plenty of
    listed ingredients never enter a well at all - toppings, sauces, things
    served alongside. So this over-counts, in a way no threshold fixes
    without also catching nothing.

    Kept as a warning because the number is still worth seeing in the
    episode record, and a genuinely absurd total would show up here first.
    Never block on it.
    """
    total = 0.0
    for raw in recipe.get("ingredients") or []:
        if not isinstance(raw, dict):
            continue
        cups = _volumetric_cups(str(raw.get("amount") or ""))
        if cups:
            total += cups
    if total > PAN_CAPACITY_CUPS_MAX:
        return (
            f"measurable ingredient volume is already {total:.1f} cups against "
            f"a {PAN_CAPACITY_CUPS_MAX:.0f}-cup pan ({PAN_WELLS} wells x up to "
            f"1/2 cup); the batter will overflow",
            total,
        )
    return None, total


def _check_servings(recipe: dict[str, Any]) -> str | None:
    servings = recipe.get("servings")
    if not isinstance(servings, int):
        return None
    if servings < SERVINGS_MIN or servings > SERVINGS_MAX:
        return (
            f"yield of {servings} is not a {PAN_WELLS}-cup muffin pan recipe "
            f"(expected {SERVINGS_MIN}-{SERVINGS_MAX})"
        )
    return None


def _check_time_agreement(recipe: dict[str, Any], instructions: str) -> str | None:
    """Advisory only. `cook_time` is frequently a parser default.

    `_parse_recipe_response` seeds `cook_time: 20` and overwrites it only if
    the model emitted the line, so a mismatch usually means the field was
    never set rather than that the recipe is wrong. W36 stored cook_time 20
    against a "15-20 minutes" bake plus a broil plus a 10-15 minute cool.
    Blocking on this would reject good recipes, so it warns.
    """
    cook_time = recipe.get("cook_time")
    if not isinstance(cook_time, int) or cook_time <= 0:
        return None
    spans = [
        int(hi or lo)
        for lo, hi in _MINUTES_RE.findall(instructions)
    ]
    if not spans:
        return None
    longest = max(spans)
    if longest > cook_time * 2:
        return (
            f"cook_time is {cook_time} min but a single step runs up to "
            f"{longest} min; cook_time may be a stale default"
        )
    return None



# Ingredients that no dessert contains. Deliberately tiny and pungent-only:
# #7154 was "Beet Horseradish Cream Cups" built with granulated sugar, brown
# sugar and vanilla, and it cleared four rounds of adversarial review because
# nothing here looks at the category and the ingredients TOGETHER.
#
# The bar for adding a word is that it cannot appear in a real dessert. Savory
# staples that DO cross over are deliberately absent - carrot, zucchini, sweet
# potato, beet, olive oil, black pepper, chili, bacon and rosemary all appear in
# published desserts, and a gate that blocks a chocolate beet cake is worse than
# the hole it closes. Calibrated to zero hits across the stored corpus.
# NOTE on two words that are NOT here: "chocolate mayonnaise cake" and
# "chocolate sauerkraut cake" are real, published desserts. They were on this
# list until code review caught that they violate the rule stated above. A word
# surviving the 37-episode corpus is not evidence it is safe - the corpus only
# shows what HAS been generated, not what could be.
# The chili and pickle families are deliberately absent for the same reason
# `chili` always was: chili-chocolate and Mexican chocolate are ordinary, and
# dill pickle ice cream is a real published novelty dessert. That rules out
# sriracha, pickled jalapeno, pickle and pickles. Kimchi went the same way -
# Salt & Straw have published a kimchi ice cream, and it is fermented cabbage
# like the sauerkraut already pulled. What is left is savory-only.
#
# THE PATTERN, for whoever adds to this next: every word checked against real
# published desserts so far has failed. A denylist of "never sweet" ingredients
# is inherently leaky, because novelty desserts are a genre. This list earns its
# place by catching ONE observed failure (a sugar-and-vanilla dessert built on
# horseradish); do not grow it speculatively. If it ever needs to be broad, it
# needs a different mechanism than a word list.
INCOHERENT_IN_SWEET = frozenset({
    "horseradish", "wasabi", "anchovy", "anchovies", "fish sauce",
    "worcestershire", "dijon",
    "capers", "caper",
    "bouillon", "gravy", "ketchup",
})

# Word-boundary matched, not substring: a bare `m in haystack` would let a future
# addition collide with an unrelated ingredient that merely contains it.
_INCOHERENT_IN_SWEET_RE = re.compile(
    r"\b(?:" + "|".join(sorted((re.escape(w) for w in INCOHERENT_IN_SWEET), key=len, reverse=True)) + r")\b"
)

_SWEET_CATEGORIES = frozenset({"sweet", "dessert", "desserts"})


def _check_category_coherence(recipe: dict[str, Any]) -> str | None:
    """A Sweet-labelled recipe must not be built on a savory-signature ingredient.

    #7154: _enforce_target_category in the picker only rewrites the LABEL, so a
    savory concept relabelled "Sweet" reaches the baker, and the baker dutifully
    builds it with sugar and vanilla alongside horseradish. The judge then fails
    the DIALOGUE on technical_credibility, because characters cannot talk
    credibly about a dish that does not cohere - which costs a whole week rather
    than one re-bake. Catch it at the recipe.
    """
    category = str(recipe.get("category") or "").strip().lower()
    if category not in _SWEET_CATEGORIES:
        return None
    haystack = " ".join(
        (ing.get("item") or "") if isinstance(ing, dict) else str(ing)
        for ing in (recipe.get("ingredients") or [])
    ).lower()
    if not haystack.strip():
        return None
    hits = sorted(set(_INCOHERENT_IN_SWEET_RE.findall(haystack)))
    if hits:
        return (
            f"category_incoherent: recipe is labelled '{category}' but is built on "
            f"{', '.join(hits)} - relabel the category or change the dish, "
            f"do not ship a dessert nobody would eat"
        )
    return None


def check_recipe_sanity(recipe: dict[str, Any] | None) -> RecipeVerdict:
    """Is this recipe safe and cookable?

    Returns a `RecipeVerdict`. Never raises — a malformed recipe is reported
    as a verdict, not an exception, so the caller keeps control of whether to
    retry or block.
    """
    if not isinstance(recipe, dict) or not recipe:
        return RecipeVerdict(status="implausible", reason="recipe data is missing")

    instructions = _instructions_text(recipe)
    if not instructions.strip():
        return RecipeVerdict(
            status="implausible", reason="recipe has no instructions"
        )

    details: dict[str, Any] = {}
    unsafe: list[str] = []
    implausible: list[str] = []
    warnings: list[str] = []

    safety = _check_food_safety(recipe, instructions)
    if safety:
        unsafe.append(safety)

    raw_egg = _check_raw_egg(recipe, instructions)
    if raw_egg:
        unsafe.append(raw_egg)

    temp_issue, hottest = _check_oven_temp(instructions)
    details["oven_temp_f"] = hottest
    if temp_issue:
        unsafe.append(temp_issue)

    # Advisory, not blocking. A listed-but-unused ingredient is a real
    # editorial defect, but the check cannot tell it apart from a synonym:
    # W28 lists "Parmigiano-Reggiano or Kefalotyri" and the method says
    # "grated Parmesan", which is the same cheese under its English name. A
    # cook follows that recipe fine. Blocking on it would cost a re-bake to
    # fix a naming inconsistency. The dangerous direction is the opposite one
    # - a step calling for an ingredient absent from the list, which leaves a
    # reader mid-recipe without it - and that needs a reliable noun extractor
    # from instruction prose. Not in this pass.
    warnings.extend(_check_ingredient_use(recipe, instructions))

    coherence = _check_category_coherence(recipe)
    if coherence:
        implausible.append(coherence)

    capacity_issue, total_cups = _check_capacity(recipe)
    details["measured_cups"] = round(total_cups, 2)
    if capacity_issue:
        warnings.append(capacity_issue)

    servings_issue = _check_servings(recipe)
    if servings_issue:
        implausible.append(servings_issue)

    time_warning = _check_time_agreement(recipe, instructions)
    if time_warning:
        warnings.append(time_warning)

    if unsafe:
        return RecipeVerdict(
            status="unsafe",
            reason=unsafe[0],
            issues=tuple(unsafe + implausible),
            warnings=tuple(warnings),
            details=details,
        )
    if implausible:
        return RecipeVerdict(
            status="implausible",
            reason=implausible[0],
            issues=tuple(implausible),
            warnings=tuple(warnings),
            details=details,
        )
    return RecipeVerdict(
        status="clear", warnings=tuple(warnings), details=details
    )
