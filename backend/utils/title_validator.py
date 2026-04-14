"""Title validation: catalog uniqueness + Mini overuse guard.

Runs at the Monday baker stage to catch LLM-generated titles that collide
with already-published recipes. The concept picker has its own catalog-aware
novelty check (scripts/pick_concept.py), but the baker LLM generates its
own title downstream and can still produce duplicates despite a distinct
input concept. See #5911 — W16 "Roasted Veggie Egg Cups" incident.
"""
from __future__ import annotations

import json
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
    "pots", "baked",
})

# Small-word companions that make a leading "Mini" redundant.
# "Mini X Bites" reads as "small X small things" — strip the "Mini".
SMALL_COMPANIONS = frozenset({
    "bite", "bites", "cup", "cups", "tassie", "tassies",
    "pop", "pops", "ball", "balls", "bit", "bits",
})

# Fraction of significant-word overlap that counts as a conflict.
OVERLAP_THRESHOLD = 0.5


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


def _significant_words(title: str) -> set[str]:
    return set(title.lower().split()) - STOP_WORDS


def check_title_conflict(title: str, catalog_titles: list[str]) -> Optional[str]:
    """Return a conflict-reason string if `title` collides with catalog, else None.

    Rules (checked in order):
    1. Exact case-insensitive match
    2. Significant-word overlap > OVERLAP_THRESHOLD with any catalog title
       (catches 'Roasted Veggie Egg Cups' vs 'Roasted Veggie Frittata Cups')
    """
    lo = title.strip().lower()
    if not lo:
        return "title is empty"

    for cat_title in catalog_titles:
        if lo == cat_title:
            return f"exact match with '{cat_title}'"

    new_words = _significant_words(title)
    if not new_words:
        return None

    for cat_title in catalog_titles:
        cat_words = _significant_words(cat_title)
        if not cat_words:
            continue
        shared = new_words & cat_words
        ratio = len(shared) / max(len(new_words), len(cat_words))
        if ratio > OVERLAP_THRESHOLD:
            return (
                f"{int(ratio * 100)}% word overlap with '{cat_title}' "
                f"(shared: {sorted(shared)})"
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
