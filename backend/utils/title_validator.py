"""Title validation: catalog uniqueness + Mini overuse guard.

Runs at the Monday baker stage to catch LLM-generated titles that collide
with already-published recipes. The concept picker has its own catalog-aware
novelty check (scripts/pick_concept.py), but the baker LLM generates its
own title downstream and can still produce duplicates despite a distinct
input concept. See #5911 — W16 "Roasted Veggie Egg Cups" incident.
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from typing import Optional

from backend.utils.logging import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[2]

# Read the catalog directly from the public blob CDN. This bypasses the
# storage layer's prefix system (which rewrites pages/ → test/pages/ in
# test mode) so duplicate detection always runs against the real
# production catalog, even during test-mode cron invocations.
CATALOG_PUBLIC_URL = (
    "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/pages/recipes.json"
)

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

def load_catalog_titles() -> list[str]:
    """Return all published recipe titles (lowercased) from the public catalog.

    Reads directly from the blob CDN public URL (no auth, no prefix) so it
    always sees the real production catalog regardless of test-mode prefix.
    Falls back to static src/recipes.json on CDN failure. Returns [] on
    total failure so callers can no-op cleanly on first-ever run.
    """
    catalog_data = None
    try:
        req = urllib.request.Request(
            CATALOG_PUBLIC_URL,
            headers={"User-Agent": "muffinpanrecipes-title-validator/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            catalog_data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning(f"title_validator: public catalog fetch failed: {exc}")

    if not catalog_data:
        try:
            catalog_data = json.loads((ROOT / "src" / "recipes.json").read_text())
        except Exception as exc:
            logger.warning(f"title_validator: static catalog load failed: {exc}")
            return []

    titles: list[str] = []
    for recipe in catalog_data.get("recipes", []):
        t = recipe.get("title", "").strip().lower()
        if t:
            titles.append(t)
    return titles


def _title_word_sequence(title: str) -> list[str]:
    """Ordered lowercase tokens, stop words included (for phrase matching)."""
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", title.lower())


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

    new_seq = _title_word_sequence(title)
    for cat_title in catalog_titles:
        cat_seq = _title_word_sequence(cat_title)
        if _contains_phrase(new_seq, cat_seq) or _contains_phrase(cat_seq, new_seq):
            return f"phrase containment with '{cat_title}'"
        segment = _shared_phrase(new_seq, cat_seq)
        if segment and {_normalize_title_word(w) for w in segment} - STOP_WORDS:
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
