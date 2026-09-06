"""Ingredient-overlap duplicate detection — the second Monday gate (#6854).

W36 (2026-08-31) "Greek Spanakopita Cups" cleared ``check_title_conflict()``
against the existing "Spanakopita Phyllo Cups" (same phyllo, spinach, feta,
parmesan, dill, nutmeg, eggs) and shipped. It was caught by hand five days
later. The title gate cannot see this class of duplicate by design: a
completely different title is exactly what an LLM producing the same dish
under a new name will generate.

**Decision already made — do not tighten title_validator.py instead.** Its
">= 2 shared distinctive words" rule is a deliberate relaxation (PR #51)
after an over-strict validator failed Monday and a week ran headless
(RUNBOOK.md INCIDENT 3). Tightening the title namespace re-triggers that
incident. Ingredients are a different, safer axis: the title namespace is
small and gets exhausted as the catalog grows, but a genuinely different dish
always uses different ingredients no matter how big the catalog gets — so an
ingredient gate does not need to be relaxed as the catalog grows the way the
title gate did.

This module runs on the baker's ``recipe_data`` (dict-shaped ingredients) on
Monday and compares it against the published catalog (string-shaped
ingredients, "<amount> <item> (<notes>)"). See ``normalize_ingredient`` for
how the two shapes are reduced to the same comparable form.

## Calibration summary (do not re-derive this — see card #6854)

Metric: **overlap coefficient** = matched items / min(|A|, |B|), computed at
the ingredient level with staples (salt, egg, butter, oil, ...) KEPT, not
dropped. Jaccard was tried and thrown away — the known W36 duplicate scored
only 0.34 Jaccard, under any sane threshold. IDF weighting and staple-dropping
were also tried against the live catalog and gave thinner margins between
real duplicates and real near-neighbors than plain overlap coefficient did.

``MIN_ITEMS = 10`` (normalized items, required on **both** sides of a
comparison). This excludes exactly the ten launch seed recipes (4 ingredients
each) and prevents a 6-item recipe sharing 5 items with a 20-item recipe from
reading as a 0.83 near-duplicate.

``DUPLICATE_THRESHOLD = 0.80``. Known duplicates score: Roasted Veggie Egg
Cups vs Roasted Veggie Frittata Cups 0.81 (the W14/W16 pair); Roasted Veggie
Egg Cups vs Make-Ahead Veggie & Sausage Egg Cups 0.88; a W36-class
spanakopita reconstruction 0.81-0.88. The highest scores among the recent,
internationally-varied weeks (W23-W36) sit at 0.70, 0.69, 0.62 — genuinely
different dishes that happen to share a base of egg/oil/salt-class staples.

A publish-order retrospective at this threshold would have flagged 3 of the
25 cron weeks to date (Roasted Veggie Egg Cups; Smoky Cheddar Breakfast
Bites; Herbed Sausage Sunrise Cups) — all three inspected by hand and judged
same-dish variants (veg/potato egg cups with sausage, cheddar, parmesan,
peppers). **Be plain about this with whoever reads it next: the early
egg-cup cluster is a continuum, not two clean clusters, so there is no gap in
the score distribution to point at.** 0.80 is chosen to catch every known
duplicate while flagging only pairs an editor would also have sent back —
not because a clean separation exists above and below it.

Re-run ``scripts/audit_ingredient_overlap.py`` (no args) after each
significant batch of new weeks to see whether that margin is holding up as
the catalog grows into more cuisines.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

MIN_ITEMS = 10
DUPLICATE_THRESHOLD = 0.80

# ---------------------------------------------------------------------------
# Normalization constants
# ---------------------------------------------------------------------------
# Units that precede an ingredient name in the catalog's string form
# ("2 tbsp olive oil"). Stripped from the front of the string, repeatedly,
# along with quantities and size words, until nothing more strips off.
UNITS = (
    r"(?:cups?|c\.|tbsps?|tablespoons?|tsps?|teaspoons?|oz|ounces?|lbs?|"
    r"pounds?|g|grams?|kg|ml|l|liters?|sheets?|sticks?|strips?|cloves?|"
    r"cans?|packages?|pkgs?|pinch(?:es)?|dash(?:es)?|stalks?|slices?|"
    r"pieces?|heads?|bunch(?:es)?|sprigs?|handfuls?|jars?|bags?|boxes?|"
    r"containers?|links?|ears?|inch(?:es)?|quarts?|pints?|fl)"
)
# A leading quantity: "1", "1/2", "1.5", "1 1/2", or a spelled range "2-3".
_QTY = r"(?:\d+(?:[./]\d+)?|\d*\s*[½¼¾⅓⅔⅛]|\d+\s*(?:-|–|to)\s*\d+)"
# Size/imprecision words that sit between the quantity and the ingredient
# name ("1 large egg", "about 2 cups flour").
_SIZE = (
    r"(?:large|medium|small|jumbo|extra-large|whole|half|heaping|scant|"
    r"about|approx\.?|approximately)"
)

# Descriptor words dropped from anywhere in the remaining text — prep
# instructions and modifiers that don't change what the ingredient IS
# ("shredded sharp cheddar cheese" -> "cheddar cheese").
DESCRIPTORS = frozenset(
    """
    fresh frozen chopped diced minced sliced grated shredded crumbled
    finely thinly roughly coarsely melted softened thawed divided plus
    more for greasing optional to taste extra about large small medium
    jumbo unsalted salted kosher ground whole pure granulated all-purpose
    plain dry dried cooked uncooked raw ripe peeled seeded trimmed rinsed
    drained squeezed well very light dark sharp mild hot cold warm room
    temperature at cut into pieces piece thin thick halved quartered
    crushed toasted packed lightly firmly preferably or and of the a an
    if possible needed as store-bought homemade good quality
    """.split()
)

_WORD_RE = re.compile(r"[a-z][a-z\-']+")


def _strip_leading_quantity_unit_size(text: str) -> str:
    """Peel quantity/unit/size tokens off the front, repeatedly.

    "1/2 cup shredded sharp cheddar cheese" needs two passes: the quantity
    comes off, exposing the unit; the unit comes off, exposing the ingredient
    name. Capped at 4 passes — real ingredient strings never nest that deep,
    and the cap keeps a pathological input from looping.
    """
    for _ in range(4):
        next_text = re.sub(rf"^\s*(?:{_QTY})\s*", " ", text)
        next_text = re.sub(rf"^\s*(?:{UNITS})\b\.?\s*(?:of\s+)?", " ", next_text)
        next_text = re.sub(rf"^\s*(?:{_SIZE})\b\s*", " ", next_text)
        if next_text == text:
            break
        text = next_text
    return text


def _singularize(word: str) -> str:
    """Cheap singularizer: drop a trailing 's' unless it would leave < 3 chars
    or the word ends in a double-s (e.g. "boneless" stays as-is)."""
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_ingredient(raw: object) -> frozenset[str] | None:
    """Reduce one ingredient (either shape) to a comparable set of words.

    Dict-shaped ingredients (the baker's ``recipe_data``,
    ``{"item": ..., "amount": ..., "notes": ...}``) use the ``item`` field
    only — amount and notes carry no dish-identity signal. String-shaped
    ingredients (the published catalog, "<amount> <item> (<notes>)") have
    their parenthetical dropped, everything after the first comma dropped,
    then leading quantity/unit/size tokens stripped repeatedly.

    Either way the remaining text is tokenized, descriptor words are
    dropped, each word is singularized, and the result is returned as a
    frozenset — order-independent so "sharp cheddar cheese" and "cheddar,
    sharp" would compare equal (a real duplicate should not survive on
    word order alone). Returns None for an ingredient with nothing left
    after normalization (empty, or amount/descriptor words only).
    """
    if isinstance(raw, dict):
        text = str(raw.get("item") or "")
    elif isinstance(raw, str):
        text = raw
    else:
        # A stray null/number in an ingredients array is garbage, not an
        # ingredient — str(None) would otherwise mint the word "none".
        return None

    text = text.lower()
    text = re.sub(r"\([^)]*\)", " ", text)  # drop parentheticals
    text = text.split(",")[0]  # drop ", thawed"-style trailing notes
    text = _strip_leading_quantity_unit_size(text)

    words = [
        _singularize(w)
        for w in _WORD_RE.findall(text)
        if w not in DESCRIPTORS
    ]
    if not words:
        return None
    return frozenset(words)


def ingredient_items(recipe: dict[str, Any]) -> list[frozenset[str]]:
    """Normalized ingredient sets for a recipe, de-duplicated, order preserved.

    Accepts either a baker ``recipe_data`` dict (dict-shaped ingredients) or
    a catalog entry (string-shaped ingredients) — ``normalize_ingredient``
    dispatches on each element's own type, so a recipe can even mix shapes.
    """
    seen: set[frozenset[str]] = set()
    items: list[frozenset[str]] = []
    for raw in recipe.get("ingredients") or []:
        normalized = normalize_ingredient(raw)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


def items_match(a: frozenset[str], b: frozenset[str]) -> bool:
    """Two normalized items match when equal or one is a subset of the other.

    "cheddar" matches "sharp cheddar cheese" — the shorter name is what's
    left after a shopping-list ingredient gets its brand/descriptor words
    stripped, not a different ingredient.
    """
    return a == b or a <= b or b <= a


def _max_match(
    a: list[frozenset[str]], b: list[frozenset[str]]
) -> list[frozenset[str]]:
    """The subset of `a` in a MAXIMUM one-to-one matching with `b`.

    One-to-one, so a recipe listing "salt" and "kosher salt" as two entries in
    `a` cannot both match a single "salt" in `b` and inflate the score. And
    maximum — Kuhn's augmenting-path algorithm — not greedy first-fit: with
    subset matching, "cheddar" can pair with either "cheddar cheese" or
    "sharp cheddar cheese", and a greedy pass that grabbed the wrong one
    first left the other unmatched. That undercounted overlap depending on
    incidental list order (review finding, 2026-09-05) — the one direction a
    duplicate gate must never err in. Lists are a few dozen items at most,
    so O(|a|·|b|) augmenting paths is instantaneous.
    """
    match_b: dict[int, int] = {}  # b index -> a index it is matched to

    def _augment(i: int, seen: set[int]) -> bool:
        for j, other in enumerate(b):
            if j in seen or not items_match(a[i], other):
                continue
            seen.add(j)
            if j not in match_b or _augment(match_b[j], seen):
                match_b[j] = i
                return True
        return False

    for i in range(len(a)):
        _augment(i, set())
    return [a[i] for i in sorted(set(match_b.values()))]


def overlap_coefficient(a: list[frozenset[str]], b: list[frozenset[str]]) -> float:
    """matched items / min(|A|, |B|). 0.0 if either side is empty."""
    if not a or not b:
        return 0.0
    matched = _max_match(a, b)
    return len(matched) / min(len(a), len(b))


def _shared_labels(matched: list[frozenset[str]]) -> tuple[str, ...]:
    """Readable, deterministic labels for a list of matched items.

    Words within one item are alphabetized before joining — a frozenset's
    own iteration order is not stable across processes (string hash
    randomization), and the reason string this feeds needs to be the same
    every time the same recipes are compared.
    """
    return tuple(" ".join(sorted(item)) for item in matched)


@dataclass(frozen=True)
class OverlapMatch:
    title: str
    slug: str
    episode_id: str
    score: float
    shared: tuple[str, ...]
    new_items: int
    existing_items: int


@dataclass(frozen=True)
class OverlapVerdict:
    status: Literal["clear", "duplicate", "skipped_thin"]
    reason: str | None
    best: OverlapMatch | None
    new_items: int


def find_ingredient_overlaps(
    recipe_data: dict[str, Any],
    catalog_recipes: list[dict[str, Any]],
    *,
    min_items: int = MIN_ITEMS,
    exclude_episode_id: str | None = None,
    exclude_slug: str | None = None,
) -> list[OverlapMatch]:
    """Every eligible comparison between `recipe_data` and the catalog, sorted
    by score descending — not just the ones above threshold.

    Eligibility (mirrors the calibration methodology, MIN_ITEMS on BOTH
    sides): the new recipe and the catalog entry must each normalize to at
    least `min_items` items. A catalog entry matching `exclude_episode_id`
    or `exclude_slug` is skipped outright — needed when re-running the check
    on a week that is already in the published catalog (Monday retries,
    or the audit script's --episode mode).

    Returning the whole distribution (not just matches above threshold) is
    deliberate: `scripts/audit_ingredient_overlap.py` needs it to draw the
    top-N tables used for recalibrating the threshold as the catalog grows.
    """
    new_items = ingredient_items(recipe_data)
    if len(new_items) < min_items:
        return []

    matches: list[OverlapMatch] = []
    for entry in catalog_recipes:
        if exclude_episode_id and entry.get("episode_id") == exclude_episode_id:
            continue
        if exclude_slug and entry.get("slug") == exclude_slug:
            continue

        existing_items = ingredient_items(entry)
        if len(existing_items) < min_items:
            continue

        matched = _max_match(new_items, existing_items)
        score = len(matched) / min(len(new_items), len(existing_items))
        matches.append(
            OverlapMatch(
                title=str(entry.get("title") or ""),
                slug=str(entry.get("slug") or ""),
                episode_id=str(entry.get("episode_id") or ""),
                score=score,
                shared=_shared_labels(matched),
                new_items=len(new_items),
                existing_items=len(existing_items),
            )
        )

    matches.sort(key=lambda m: m.score, reverse=True)
    return matches


def check_ingredient_overlap(
    recipe_data: dict[str, Any],
    catalog_recipes: list[dict[str, Any]],
    *,
    threshold: float = DUPLICATE_THRESHOLD,
    min_items: int = MIN_ITEMS,
    exclude_episode_id: str | None = None,
    exclude_slug: str | None = None,
) -> OverlapVerdict:
    """Ingredient-overlap gate for the Monday baker output (#6854).

    Mirrors `title_validator.check_title_conflict`'s shape (a reason string
    the caller can log and feed back to the baker as a retry constraint) but
    scores dish identity through ingredients instead of title words, so a
    duplicate wearing a fresh name cannot slip through both gates at once.
    """
    new_items = ingredient_items(recipe_data)
    if len(new_items) < min_items:
        return OverlapVerdict(
            status="skipped_thin",
            reason=(
                f"recipe has only {len(new_items)} normalized ingredient "
                f"items (need >= {min_items} on both sides to compare "
                "reliably); ingredient-overlap check skipped, not passed"
            ),
            best=None,
            new_items=len(new_items),
        )

    matches = find_ingredient_overlaps(
        recipe_data,
        catalog_recipes,
        min_items=min_items,
        exclude_episode_id=exclude_episode_id,
        exclude_slug=exclude_slug,
    )
    best = matches[0] if matches else None

    if best is not None and best.score >= threshold:
        denom = min(best.new_items, best.existing_items)
        preview = ", ".join(best.shared[:8])
        if len(best.shared) > 8:
            preview += ", ..."
        reason = (
            f"ingredient overlap {best.score:.2f} with '{best.title}' "
            f"({best.episode_id}): {len(best.shared)} of {denom} "
            f"ingredients shared — {preview}"
        )
        return OverlapVerdict(
            status="duplicate", reason=reason, best=best, new_items=len(new_items)
        )

    return OverlapVerdict(
        status="clear", reason=None, best=best, new_items=len(new_items)
    )
