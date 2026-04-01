#!/usr/bin/env python3
"""Auto-select a weekly recipe concept by scraping trending food sites.

Scores candidates on muffin pan adaptability, novelty vs recent episodes,
seasonal fit, and sweet/savory balance, then does a weighted random pick.

Usage:
  PYTHONPATH=. uv run scripts/pick_concept.py
  PYTHONPATH=. uv run scripts/pick_concept.py --dry-run
  PYTHONPATH=. uv run scripts/pick_concept.py --count 3
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"

# ---------------------------------------------------------------------------
# Source sites — (name, url, CSS/text hint for recipe title extraction)
# ---------------------------------------------------------------------------
SOURCES = [
    ("Bon Appétit",   "https://www.bonappetit.com/recipes"),
    ("Food52",        "https://food52.com/recipes"),
    ("Serious Eats",  "https://www.seriouseats.com/recipes"),
    ("NYT Cooking",   "https://cooking.nytimes.com/topics/new-recipes"),
    ("Smitten Kitchen", "https://smittenkitchen.com/"),
]

# Months considered "current season" for mild boosting
SEASON_MAP = {
    (12, 1, 2): "winter",
    (3, 4, 5):  "spring",
    (6, 7, 8):  "summer",
    (9, 10, 11): "fall",
}

MUFFIN_PAN_KEYWORDS = [
    "mini", "cup", "bite", "individual", "tartlet", "muffin",
    "cake", "meatball", "frittata", "egg", "quiche", "cheesecake",
    "brownie", "custard", "flan", "biscuit", "popover", "pudding",
    "panna cotta", "tassie", "slider", "mushroom", "stuffed",
]

SAVORY_KEYWORDS = ["chicken", "beef", "pork", "shrimp", "salmon", "pasta",
                   "cheese", "egg", "potato", "mushroom", "sausage", "taco",
                   "shepherd", "meatball", "pizza", "quiche", "frittata"]

SWEET_KEYWORDS = ["cake", "cookie", "brownie", "chocolate", "caramel",
                  "vanilla", "cheesecake", "tart", "pudding", "custard",
                  "panna cotta", "tiramisu", "lemon", "berry", "pumpkin"]

PARTY_KEYWORDS = ["slider", "bite", "dip", "wing", "nacho", "bruschetta",
                  "crostini", "skewer", "appetizer", "finger", "pinwheel",
                  "phyllo", "puff pastry", "spring roll", "wonton",
                  "crab", "brie", "prosciutto", "caprese", "stuffed"]

BREAKFAST_KEYWORDS = ["egg", "oatmeal", "pancake", "waffle", "bacon",
                      "sausage", "hash", "toast", "granola", "frittata",
                      "quiche", "brunch", "morning"]

VALID_CATEGORIES = ["Breakfast", "Savory", "Sweet", "Party"]

CATEGORY_KEYWORDS = {
    "Breakfast": BREAKFAST_KEYWORDS,
    "Savory": SAVORY_KEYWORDS,
    "Sweet": SWEET_KEYWORDS,
    "Party": PARTY_KEYWORDS,
}


def _fetch(url: str, timeout: int = 5) -> str:
    """Fetch URL text, returning empty string on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MuffinPanBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [warn] fetch failed for {url}: {e}", file=sys.stderr)
        return ""


def _extract_recipe_names(html: str, max_results: int = 8) -> list[str]:
    """Rough heuristic extraction of recipe title strings from raw HTML."""
    # Strip tags, decode entities, collapse whitespace
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)

    candidates: list[str] = []

    # Pattern 1: Title-case Name With/and Title-case continuation
    candidates += re.findall(
        r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)+ (?:With|and|&|on|in) [A-Z][a-z]+(?: [A-Za-z]+){1,5})\b",
        text,
    )
    # Pattern 2:  Adjective + Food noun (e.g. "Crispy Polenta", "Spiced Meatballs")
    candidates += re.findall(
        r"\b([A-Z][a-z]+(?:-[A-Za-z]+)? (?:[A-Z][a-z]+ ){1,3}(?:Cake|Biscuit|Muffin|Tart|Cups?|Pies?|Balls?|Pasta|Soup|Salad|Bread|Pudding|Custard|Tassies?|Frittata|Quiche|Brownies?|Cookies?))\b",
        text,
    )

    # Deduplicate and filter noise
    seen: set[str] = set()
    clean: list[str] = []
    # Reject anything containing obvious nav/ad noise words
    noise_words = {"view", "recipe", "tips", "guides", "format", "slot",
                   "method", "technique", "ordinal", "scroll", "DocOrdinal",
                   "unit", "unless", "would", "getting", "key", "here",
                   "how", "modify", "serve"}
    for c in candidates:
        c = c.strip().rstrip(".,;:")
        words = c.split()
        if len(words) < 3 or len(c) > 70:
            continue
        if any(w.lower() in noise_words for w in words):
            continue
        key = c.lower()
        if key not in seen:
            seen.add(key)
            clean.append(c)
    return clean[:max_results]


def _load_recent_concepts(n: int = 4) -> list[str]:
    """Return concept strings from the n most recent episode JSONs."""
    episodes = sorted(EPISODES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    concepts: list[str] = []
    for ep_path in episodes:
        try:
            data = json.loads(ep_path.read_text())
            c = data.get("concept") or data.get("stages", {}).get("monday", {}).get("concept", "")
            if c:
                concepts.append(c.lower())
        except Exception:
            pass
    return concepts


def score_candidate(
    name: str,
    recent_concepts: list[str],
    current_month: int,
    target_category: str | None = None,
) -> float:
    """Score a recipe name 0–10. Higher = better pick."""
    low = name.lower()
    score = 0.0

    # 1. Muffin pan adaptability (0-3)
    pan_hits = sum(1 for kw in MUFFIN_PAN_KEYWORDS if kw in low)
    score += min(3.0, pan_hits * 1.0)

    # 2. Novelty (0-2) — penalise if too similar to recent episodes
    for recent in recent_concepts:
        words_overlap = len(set(low.split()) & set(recent.split())) / max(len(low.split()), 1)
        if words_overlap > 0.5:
            score -= 2.0
            break
    else:
        score += 2.0

    # 3. Seasonal fit (0-2)
    season = next(
        (s for months, s in SEASON_MAP.items() if current_month in months), "unknown"
    )
    season_map: dict[str, list[str]] = {
        "winter": ["pumpkin", "cranberry", "ginger", "spice", "chocolate", "caramel", "biscuit"],
        "spring": ["lemon", "strawberry", "rhubarb", "asparagus", "pea", "herb"],
        "summer": ["zucchini", "corn", "berry", "peach", "tomato", "basil"],
        "fall":   ["apple", "pumpkin", "squash", "caramel", "maple", "cinnamon", "pecan"],
    }
    for kw in season_map.get(season, []):
        if kw in low:
            score += 0.5

    # 4. Category identification (0-1)
    for cat_keywords in CATEGORY_KEYWORDS.values():
        if any(kw in low for kw in cat_keywords):
            score += 0.5
            break

    # 5. Target category bonus (+2) — boost candidates matching the desired category
    if target_category and target_category in CATEGORY_KEYWORDS:
        cat_kws = CATEGORY_KEYWORDS[target_category]
        if any(kw in low for kw in cat_kws):
            score += 2.0

    score = min(score, 10.0)
    return round(max(0.0, score), 2)


def pick_concept(
    dry_run: bool = False,
    count: int = 1,
    target_category: str | None = None,
) -> list[str]:
    """Main entry point. Returns list of selected concept strings.

    Args:
        target_category: If set, boost candidates matching this category
                         (one of "Breakfast", "Savory", "Sweet", "Party").
    """
    recent = _load_recent_concepts()
    month = date.today().month

    all_candidates: list[tuple[str, str, float]] = []  # (source, name, score)

    for source_name, url in SOURCES:
        print(f"Fetching {source_name}...", file=sys.stderr)
        html = _fetch(url)
        if not html:
            continue
        names = _extract_recipe_names(html)
        for name in names:
            s = score_candidate(name, recent, month, target_category=target_category)
            all_candidates.append((source_name, name, s))

    if not all_candidates:
        # Hard fallback list — curated muffin pan concepts
        fallbacks = [
            "Mini Lemon Ricotta Cheesecakes",
            "Cheddar Old Bay Biscuit Cups",
            "Tiramisu Panna Cotta Cups",
            "Mini Shepherd's Pie Cups",
            "Brown Butter Pecan Tassies",
            "Spinach and Feta Egg Cups",
            "Mini Chocolate Lava Cakes",
            "Buffalo Chicken Muffin Cups",
            "Mini Puff Pastry Brie Bites",
            "Buffalo Chicken Wonton Cups",
            "Spinach Artichoke Dip Cups",
            "Mini Caprese Bruschetta Bites",
        ]
        # Filter out recently used
        fallbacks = [f for f in fallbacks if f.lower() not in recent]
        # If targeting a category, prefer matching fallbacks
        if target_category and target_category in CATEGORY_KEYWORDS:
            cat_kws = CATEGORY_KEYWORDS[target_category]
            matching = [f for f in fallbacks if any(kw in f.lower() for kw in cat_kws)]
            if matching:
                fallbacks = matching
        selected = random.sample(fallbacks, min(count, len(fallbacks)))
        print("\n[fallback] No candidates scraped — using curated list.")
        for c in selected:
            print(f"  ✅ {c}")
        return selected

    # Sort by score desc
    all_candidates.sort(key=lambda x: x[2], reverse=True)

    if dry_run:
        print(f"\n{'='*60}")
        print(f"  CONCEPT PICKER — DRY RUN ({len(all_candidates)} candidates)")
        print(f"  Recent episodes: {recent or ['none']}")
        print(f"{'='*60}")
        for src, name, s in all_candidates[:15]:
            print(f"  {s:4.1f}  [{src}]  {name}")
        print(f"{'='*60}\n")
        return []

    # Weighted random from top 10
    pool = all_candidates[:10]
    weights = [max(0.1, c[2]) for c in pool]
    chosen_raw = random.choices(pool, weights=weights, k=min(count, len(pool)))
    # Deduplicate by concept text
    seen: set[str] = set()
    chosen: list[str] = []
    for src, name, s in chosen_raw:
        if name not in seen:
            seen.add(name)
            chosen.append(name)
            print(f"  ✅ Selected: {name}  (score={s}, source={src})", file=sys.stderr)

    return chosen


def pick_target_category() -> str:
    """Weighted random category selection favoring underrepresented categories.

    Reads the recipe catalog to count recipes per category, then uses
    inverse-frequency weighting so underrepresented categories are more
    likely to be picked.
    """
    counts: dict[str, int] = {cat: 0 for cat in VALID_CATEGORIES}

    # Try blob catalog first (production), fall back to static file
    catalog_data = None
    try:
        from backend.storage import storage
        blob_content = storage.load_page("pages/recipes.json")
        if blob_content:
            catalog_data = json.loads(blob_content)
    except Exception:
        pass

    if not catalog_data:
        catalog_path = ROOT / "src" / "recipes.json"
        try:
            catalog_data = json.loads(catalog_path.read_text())
        except Exception:
            pass

    if catalog_data:
        for recipe in catalog_data.get("recipes", []):
            cat = recipe.get("category", "").title()
            if cat in counts:
                counts[cat] += 1

    # Inverse-frequency weighting: fewer recipes = higher weight
    total = sum(counts.values()) or 1
    weights = [(total - counts[cat] + 1) for cat in VALID_CATEGORIES]
    chosen = random.choices(VALID_CATEGORIES, weights=weights, k=1)[0]
    print(f"  [category] Counts: {counts} → selected: {chosen}", file=sys.stderr)
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description="Pick this week's recipe concept from trending food sites")
    parser.add_argument("--dry-run", action="store_true", help="Print scored candidates without selecting")
    parser.add_argument("--count", type=int, default=1, help="Number of concepts to select (default: 1)")
    args = parser.parse_args()

    concepts = pick_concept(dry_run=args.dry_run, count=args.count)
    if concepts and not args.dry_run:
        # Print final selection to stdout (one per line) for piping into other scripts
        for c in concepts:
            print(c)


if __name__ == "__main__":
    main()
