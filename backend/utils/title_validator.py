"""Title validation: catalog uniqueness + Mini overuse guard.

Runs at the Monday baker stage to catch LLM-generated titles that collide
with already-published recipes. The concept picker has its own catalog-aware
novelty check (scripts/pick_concept.py), but the baker LLM generates its
own title downstream and can still produce duplicates despite a distinct
input concept. See #5911 — W16 "Roasted Veggie Egg Cups" incident.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from backend.utils.catalog import (
    CATALOG_PUBLIC_URL,
    CatalogUnavailableError,
    load_published_catalog,
)
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Re-exported so existing callers/imports (RUNBOOK snippets included) keep
# working — backend.utils.catalog is now the one source of truth for the URL
# (#6878).
CATALOG_PUBLIC_URL = CATALOG_PUBLIC_URL

# Words too common/generic to count as overlap signals.
# Single source of truth — scripts/pick_concept.py imports this constant.
STOP_WORDS = frozenset({
    "a", "an", "the", "and", "&", "of", "in", "on", "with", "for", "to",
    "mini", "cups", "cup", "bites", "bite", "muffin", "tin", "pan", "tops",
    "pots", "nest", "nests", "baked",
    "breakfast", "savory", "sweet", "party", "recipe", "recipes",
})

# Small-word companions that make a leading "Mini" redundant.
# "Mini X Bites" reads as "small X small things" — strip the "Mini".
SMALL_COMPANIONS = frozenset({
    "bite", "bites", "cup", "cups", "tassie", "tassies",
    "pop", "pops", "ball", "balls", "bit", "bits",
})

def _load_catalog() -> dict:
    """Strict-read the published catalog via backend.utils.catalog (#6878).

    Raises CatalogUnavailableError on failure — no fallback to the static
    src/recipes.json ten launch seeds. That fallback used to make a CDN
    hiccup silently shrink every caller's view of the catalog to ten
    recipes while still reporting success (#6878). Kept as a thin,
    separately-patchable function so tests/test_title_validator.py and the
    RUNBOOK diagnostic snippets can keep patching/importing it by name.
    """
    return load_published_catalog()


def load_catalog_titles() -> list[str]:
    """Return all published recipe titles (lowercased) from the public catalog.

    Steering/QA use only (not a publishing gate — Monday's duplicate gate
    reads backend.utils.catalog.load_published_catalog directly and raises).
    A catalog read failure here degrades to [] with a logged error rather
    than raising, since the live callers (cron_routes._recent_catalog_titles
    for QA context, and the RUNBOOK diagnostic snippets) already alert on an
    empty result or are a human reading output by hand (#6878).
    """
    try:
        catalog = _load_catalog()
    except CatalogUnavailableError as exc:
        logger.error(f"title_validator: catalog unavailable, titles list empty: {exc}")
        return []

    titles: list[str] = []
    for recipe in catalog.get("recipes", []):
        t = recipe.get("title", "").strip().lower()
        if t:
            titles.append(t)
    return titles


def load_recent_cuisines(n: int = 4) -> list[str]:
    """Return the cuisines of the n most recently published recipes.

    The catalog is prepend-ordered (newest first), so the first entries are
    the most recent. Used to steer the baker away from repeating cuisines
    and keep the catalog globally varied — steering only, not a gate, so a
    catalog read failure degrades to [] with a logged error instead of
    raising: a Monday must not fail because cuisine steering could not read
    the catalog (#6878). Entries without a cuisine are skipped. Also
    returns [] when no catalog/cuisines exist yet.
    """
    try:
        catalog = _load_catalog()
    except CatalogUnavailableError as exc:
        logger.error(f"title_validator: catalog unavailable, cuisine steering degraded to []: {exc}")
        return []

    cuisines: list[str] = []
    for recipe in catalog.get("recipes", []):
        c = str(recipe.get("cuisine", "")).strip()
        if c:
            cuisines.append(c)
        if len(cuisines) >= n:
            break
    return cuisines


def _title_word_sequence(title: str) -> list[str]:
    """Ordered lowercase tokens, stop words included (for phrase matching).

    Curly apostrophes are straightened and accents folded first, so
    "Crème Brûlée Cups" tokenizes as [creme, brulee, cups] instead of
    fragmenting into ASCII shards that match nothing — which would let an
    accented duplicate through and let an accented candidate look "fresh".
    The concept picker tokenizes titles through this same function so the
    two stay aligned (#6858 review, 2026-09-05).
    """
    text = title.replace("\u2019", "'").replace("\u2018", "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower())


def _contains_phrase(haystack: list[str], needle: list[str]) -> bool:
    if len(needle) < 2 or len(needle) > len(haystack):
        return False
    span = len(needle)
    return any(
        haystack[i : i + span] == needle
        for i in range(len(haystack) - span + 1)
    )


def _shared_phrase(a: list[str], b: list[str], min_len: int = 3) -> Optional[list[str]]:
    """Longest contiguous word run appearing in both titles, if >= min_len."""
    for span in range(min(len(a), len(b)), min_len - 1, -1):
        for i in range(len(a) - span + 1):
            segment = a[i : i + span]
            if any(b[j : j + span] == segment for j in range(len(b) - span + 1)):
                return segment
    return None


def _normalize_title_word(word: str) -> str:
    if word in {"cups", "bites", "nests"}:
        return word[:-1]
    if len(word) > 4 and word.endswith("s"):
        return word[:-1]
    return word


def _significant_words(title: str) -> set[str]:
    words = _title_word_sequence(title)
    return {_normalize_title_word(word) for word in words} - STOP_WORDS


def distinctive_title_words(title: str) -> set[str]:
    """Return title words that count as repetition signals."""
    return _significant_words(title)


def check_title_conflict(title: str, catalog_titles: list[str]) -> Optional[str]:
    """Return a conflict-reason string if `title` collides with catalog, else None.

    Rules (checked in order):
    1. Exact case-insensitive match
    2. Phrase containment in either direction
       (catches 'Vegan Blueberry Muffin Tops' vs 'Blueberry Muffin Tops')
    3. Shared contiguous phrase of 3+ words with at least one distinctive word
       (catches 'Vegan Blueberry Muffin Tops' vs 'Classic Blueberry Muffin Tops')
    4. Two or more shared distinctive words with a single catalog title
       (catches 'Roasted Veggie Egg Cups' vs 'Roasted Veggie Frittata Cups')
    5. Zero fresh distinctive words against the whole catalog
       (catches distributed repeats like 'Paprika Cheddar Frittata Cups'
       where every word is recycled from somewhere in the catalog)

    A single shared word is deliberately NOT a conflict on its own. With a
    growing catalog, any-single-word matching empties the title namespace —
    W24 (2026-06-08) failed Monday twice because 'egg' alone collided.
    A new title only needs one fresh distinctive word to be publishable.
    """
    lo = title.strip().lower()
    if not lo:
        return "title is empty"

    for cat_title in catalog_titles:
        if lo == cat_title:
            return f"exact match with '{cat_title}'"

    def _has_distinctive(seq: list[str]) -> bool:
        return bool({_normalize_title_word(w) for w in seq} - STOP_WORDS)

    new_seq = _title_word_sequence(title)
    for cat_title in catalog_titles:
        cat_seq = _title_word_sequence(cat_title)
        if (_contains_phrase(new_seq, cat_seq) and _has_distinctive(cat_seq)) or (
            _contains_phrase(cat_seq, new_seq) and _has_distinctive(new_seq)
        ):
            return f"phrase containment with '{cat_title}'"
        segment = _shared_phrase(new_seq, cat_seq)
        if segment and _has_distinctive(segment):
            return (
                f"shared phrase '{' '.join(segment)}' with '{cat_title}'"
            )

    new_words = _significant_words(title)
    if not new_words:
        return None

    catalog_word_pool: set[str] = set()
    for cat_title in catalog_titles:
        cat_words = _significant_words(cat_title)
        catalog_word_pool |= cat_words
        if not cat_words:
            continue
        shared = new_words & cat_words
        if len(shared) >= 2:
            return (
                f"distinctive word overlap with '{cat_title}' "
                f"(shared: {sorted(shared)})"
            )

    if catalog_word_pool and new_words <= catalog_word_pool:
        return (
            "distinctive word overlap with published catalog: every title "
            f"word is already in use ({sorted(new_words)}). "
            "Needs at least one fresh distinctive word."
        )

    return None


def strip_redundant_mini(title: str) -> str:
    """Drop a leading 'Mini' when the title already implies smallness.

    'Mini Caprese Bruschetta Bites' → 'Caprese Bruschetta Bites'
    'Mini Shepherd's Pie Pots' → unchanged ('Pots' not in SMALL_COMPANIONS)
    'Mini Chocolate Lava Cakes' → unchanged ('Cakes' not a small-word)
    """
    words = title.split()
    if len(words) < 2 or words[0].lower() != "mini":
        return title
    remaining_lower = {w.lower().rstrip(".,'\"") for w in words[1:]}
    if remaining_lower & SMALL_COMPANIONS:
        stripped = " ".join(words[1:])
        logger.info(f"Stripped redundant 'Mini': '{title}' → '{stripped}'")
        return stripped
    return title
