#!/usr/bin/env python3
"""Pick this week's recipe concept: brainstorm, filter hard, score soft.

#6858 (absorbs #6391): the old picker treated the muffin-pan-form check and
the target-category match as soft bonuses on top of a scrape of five
third-party recipe sites. That let "Saucy Pork Chops With Coconut Crisp"
outrank nothing (score 2.5, same as two other savory mains) and get returned
for a *Sweet* target category — because +2 for matching the category and -0
for being an unmoldable pork chop was never enough to reject it. It also let
pick_target_category() roll dice: two runs minutes apart could return
different categories, and the fullest category still had ~17% odds of being
"corrected" into an even heavier lead.

The fix has three parts:
  1. pick_target_category() is now deterministic — thinnest category wins,
     no randomness (see its docstring).
  2. Category, muffin-pan form, and catalog duplication are HARD FILTERS
     (reject or survive), not bonuses. A candidate that fails any of them
     never reaches scoring, so it can never outrank a correct one.
  3. Candidates come from one LLM brainstorm call against the full published
     catalog (title list, cuisines, overused words), not a scrape of
     third-party sites. The scrape still runs, but only as optional
     "trending" inspiration fed into the prompt — a candidate is never
     accepted straight off a scraped page, so five unreachable sites (the
     W36 setup) can no longer collapse the pool to whichever one answered.

Usage:
  PYTHONPATH=. uv run scripts/pick_concept.py
  PYTHONPATH=. uv run scripts/pick_concept.py --dry-run
  PYTHONPATH=. uv run scripts/pick_concept.py --count 3
  PYTHONPATH=. uv run scripts/pick_concept.py --category Sweet
  PYTHONPATH=. uv run scripts/pick_concept.py --no-inspiration
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
import urllib.request

# Single source of truth for overlap-signal stop words and the strict
# catalog reader — importing keeps this script aligned with the runtime
# title validator so title drift (#5911) and catalog blind spots
# (#6854/#6858) can't recur from two copies going out of sync.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.config import config  # noqa: E402
from backend.utils.catalog import (  # noqa: E402
    VALID_CATEGORIES,
    CatalogUnavailableError,
    catalog_recipes,
    catalog_titles,
    category_counts,
    load_published_catalog,
)
from backend.utils.model_router import generate_response as _generate_response  # noqa: E402
from backend.utils.muffin_pan_form import (  # noqa: E402
    MUFFIN_PAN_FORM_NOUNS,
    OFF_BRAND_TITLE_SHAPES,
)
from backend.utils.title_validator import (  # noqa: E402
    STOP_WORDS,
    _normalize_title_word,
    _significant_words,
)

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"

CATEGORY_DEFINITIONS = {
    "Breakfast": "morning dishes",
    "Savory": "lunch/dinner mains and sides",
    "Sweet": "desserts and sweet bakes",
    "Party": "appetizers and finger food for entertaining, sweet or savory",
}

# Keyword backstop applied regardless of the LLM's declared category — a
# brainstorm call can mislabel a dish, and a mislabeled savory main is
# exactly what shipped as a "Sweet" pick before (#6858 item 1).
SAVORY_MARKERS = frozenset({
    "chicken", "beef", "pork", "lamb", "turkey", "shrimp", "salmon", "tuna",
    "cod", "fish", "sausage", "bacon", "ham", "prosciutto", "chorizo", "crab",
    "cheddar", "parmesan", "feta", "mozzarella", "gouda", "garlic", "onion",
    "potato", "spinach", "kale", "mushroom", "broccoli", "pepper", "chili",
    "curry", "miso", "kimchi",
})
DESSERT_MARKERS = frozenset({
    "chocolate", "brownie", "cheesecake", "caramel", "meringue", "custard",
    "cookie", "fudge", "tiramisu", "mousse", "pudding", "tart", "truffle",
})

# Source sites — used ONLY for optional brainstorm inspiration now (#6858
# item 4). Never a candidate pool on their own: a scrape throw used to
# silently shrink the pool to whichever site answered, which is what set up
# the W36 duplicate.
SOURCES = [
    ("Bon Appétit",   "https://www.bonappetit.com/recipes"),
    ("Food52",        "https://food52.com/recipes"),
    ("Serious Eats",  "https://www.seriouseats.com/recipes"),
    ("NYT Cooking",   "https://cooking.nytimes.com/topics/new-recipes"),
    ("Smitten Kitchen", "https://smittenkitchen.com/"),
]

_INSPIRATION_PER_REQUEST_TIMEOUT = 5
_INSPIRATION_OVERALL_TIMEOUT = 6

# Months considered "current season" for the seasonal score bonus.
SEASON_MAP = {
    (12, 1, 2): "winter",
    (3, 4, 5):  "spring",
    (6, 7, 8):  "summer",
    (9, 10, 11): "fall",
}
SEASON_KEYWORDS: dict[str, list[str]] = {
    "winter": ["pumpkin", "cranberry", "ginger", "spice", "chocolate", "caramel", "biscuit"],
    "spring": ["lemon", "strawberry", "rhubarb", "asparagus", "pea", "herb"],
    "summer": ["zucchini", "corn", "berry", "peach", "tomato", "basil"],
    "fall":   ["apple", "pumpkin", "squash", "caramel", "maple", "cinnamon", "pecan"],
}

# Fixed roster the brainstorm prompt draws "not yet used" suggestions from.
# Deliberately world-cuisine heavy, not American-comfort-food heavy — the
# catalog already leans that way without help.
WORLD_CUISINES = [
    "French", "Vietnamese", "Thai", "Moroccan", "Ethiopian", "Peruvian",
    "Turkish", "Lebanese", "Filipino", "Brazilian", "Jamaican", "Chinese",
    "Polish", "German", "Hungarian", "Portuguese", "Swedish", "Russian",
    "Argentinian", "Nigerian", "Georgian", "Uzbek", "Malaysian",
    "Indonesian", "Egyptian",
]

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


class NoConceptAvailableError(RuntimeError):
    """Raised when zero candidates survive filtering from BOTH pools.

    #6858: never return [] and never return a wrong-category or off-form
    pick just because nothing scored well — pork chops and salmon fillets
    shipped as "sweet" picks before precisely because a bad fit only cost a
    candidate a few points, not its eligibility. `_pick_weekly_concept()` in
    cron_routes.py already treats any exception from this module as a
    retryable, then fail-closed, condition.
    """


@dataclass(frozen=True)
class Candidate:
    """One proposed concept, before filtering."""

    concept: str
    category: str
    cuisine: str
    binds_how: str
    source: str  # "brainstorm" or "curated"


@dataclass(frozen=True)
class _CatalogSignals:
    """Precomputed catalog-derived data shared by every filter/score call."""

    all_titles: list[str]  # catalog titles + local in-flight concepts, lowercased
    word_freq: dict[str, int]
    catalog_significant_words: set[str]
    used_cuisines: set[str]


# ---------------------------------------------------------------------------
# Curated fallback pool — used only when the brainstorm produced nothing
# usable (raised, returned unparseable text, or every candidate was
# rejected). Each entry is checked against the exact same hard filters as a
# brainstorm candidate; none of these collide with the catalog as of
# 2026-09-05 (pinned by a test fixture in tests/test_pick_concept.py).
# ---------------------------------------------------------------------------
CURATED_CONCEPTS: dict[str, list[tuple[str, str]]] = {
    "Breakfast": [
        ("French Toast Popovers", "French"),
        ("Swedish Cardamom Bun Cups", "Swedish"),
        ("Filipino Pandesal Cups", "Filipino"),
        ("Ethiopian Injera Cups", "Ethiopian"),
        ("Malaysian Kaya Jam Cups", "Malaysian"),
        ("Georgian Khachapuri Cups", "Georgian"),
        ("Polish Twarog Buns", "Polish"),
        ("Turkish Menemen Popovers", "Turkish"),
    ],
    "Savory": [
        ("Peruvian Aji Beef Cups", "Peruvian"),
        ("Moroccan Lamb Tagine Cups", "Moroccan"),
        ("Brazilian Feijoada Pots", "Brazilian"),
        ("Hungarian Goulash Bites", "Hungarian"),
        ("Vietnamese Lemongrass Pork Cups", "Vietnamese"),
        ("Argentinian Chimichurri Steak Cups", "Argentinian"),
        ("Nigerian Suya Skewer Bites", "Nigerian"),
        ("Uzbek Plov Bites", "Uzbek"),
    ],
    "Sweet": [
        ("Portuguese Cinnamon Sugar Puffs", "Portuguese"),
        ("Colombian Tres Leches Cakes", "Colombian"),
        ("Lebanese Rosewater Pistachio Cheesecakes", "Lebanese"),
        ("Egyptian Basbousa Semolina Cakes", "Egyptian"),
        ("Indonesian Klepon Coconut Cakes", "Indonesian"),
        ("Russian Poppy Seed Cakes", "Russian"),
        ("Armenian Walnut Tassies", "Armenian"),
        ("Swedish Kladdkaka Fudge Cakes", "Swedish"),
    ],
    "Party": [
        ("Korean Bulgogi Beef Bites", "Korean"),
        ("Jamaican Jerk Shrimp Cups", "Jamaican"),
        ("Thai Satay Peanut Cups", "Thai"),
        ("Lebanese Kibbeh Bites", "Lebanese"),
        ("Chinese Char Siu Bao Buns", "Chinese"),
        ("Basque Manchego Croquette Bites", "Basque"),
        ("Indian Samosa Cups", "Indian"),
        ("French Gougères Puffs", "French"),
    ],
}


def _curated_pool(target_category: str) -> list[Candidate]:
    return [
        Candidate(concept=concept, category=target_category, cuisine=cuisine, binds_how="", source="curated")
        for concept, cuisine in CURATED_CONCEPTS.get(target_category, [])
    ]


# ---------------------------------------------------------------------------
# Optional inspiration scrape — never a candidate source, see module docstring.
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = _INSPIRATION_PER_REQUEST_TIMEOUT) -> str:
    """Fetch URL text, returning empty string on failure."""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MuffinPanBot/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [warn] inspiration fetch failed for {url}: {e}", file=sys.stderr)
        return ""


def _extract_recipe_names(html: str, max_results: int = 8) -> list[str]:
    """Rough heuristic extraction of recipe title strings from raw HTML."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)

    candidates: list[str] = []
    candidates += re.findall(
        r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)+ (?:With|and|&|on|in) [A-Z][a-z]+(?: [A-Za-z]+){1,5})\b",
        text,
    )
    candidates += re.findall(
        r"\b([A-Z][a-z]+(?:-[A-Za-z]+)? (?:[A-Z][a-z]+ ){1,3}(?:Cake|Biscuit|Muffin|Tart|Cups?|Pies?|Balls?|Pasta|Soup|Salad|Bread|Pudding|Custard|Tassies?|Frittata|Quiche|Brownies?|Cookies?))\b",
        text,
    )

    seen: set[str] = set()
    clean: list[str] = []
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


def _fetch_inspiration() -> list[str]:
    """Fetch trending recipe names from third-party sites, concurrently.

    Best-effort only: this feeds the brainstorm prompt as "trending right
    now" context, never a candidate itself. Every source unreachable simply
    means an empty inspiration list — the brainstorm still runs (#6858
    item 4; the old design let a scrape failure silently shrink the entire
    candidate pool).
    """
    names: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        futures = {pool.submit(_fetch, url): name for name, url in SOURCES}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=_INSPIRATION_OVERALL_TIMEOUT):
                name = futures[future]
                try:
                    html = future.result()
                except Exception as exc:
                    print(f"  [warn] inspiration fetch failed for {name}: {exc}", file=sys.stderr)
                    continue
                if html:
                    names.extend(_extract_recipe_names(html))
        except concurrent.futures.TimeoutError:
            print("  [warn] inspiration fetch: overall timeout, using partial results", file=sys.stderr)
    return names


# ---------------------------------------------------------------------------
# Local in-flight concepts (unpublished weeks) — separate from the published
# catalog, kept exactly as before.
# ---------------------------------------------------------------------------

def _load_recent_concepts(n: int = 4) -> list[str]:
    """Concept fields from the newest local episode JSON files.

    These are in-flight, not-yet-published weeks, so they are a signal the
    published catalog can't provide on its own — a week that is mid-pipeline
    right now shouldn't get picked again next Monday.
    """
    concepts: list[str] = []
    seen: set[str] = set()
    episodes = sorted(EPISODES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    for ep_path in episodes:
        try:
            data = json.loads(ep_path.read_text())
            c = data.get("concept") or data.get("stages", {}).get("monday", {}).get("concept", "")
            if c and c.lower() not in seen:
                seen.add(c.lower())
                concepts.append(c.lower())
        except Exception:
            pass
    return concepts


def _build_word_freq(recent_concepts: list[str]) -> dict[str, int]:
    """Count how often each significant word appears across recent recipes."""
    freq: dict[str, int] = {}
    for concept in recent_concepts:
        words = set(concept.lower().split()) - STOP_WORDS
        for w in words:
            freq[w] = freq.get(w, 0) + 1
    return freq


# ---------------------------------------------------------------------------
# Deterministic category balancing (#6858 item 6)
# ---------------------------------------------------------------------------

def pick_target_category(catalog: Optional[dict[str, Any]] = None) -> str:
    """Return the VALID_CATEGORIES member with the fewest published recipes.

    Deliberately deterministic, not weighted-random. Weighted-random still
    gave the fullest category real odds (17 of 35 recipes kept ~17% odds of
    being picked *again*), and two runs minutes apart could disagree —
    useless for retrying a failed pick, and useless for reasoning about why
    a given Monday chose what it chose. Balancing the catalog is an input to
    the picker, not a suggestion it gets to roll dice on.

    Tie-break: the tied category whose most recent catalog entry is OLDEST
    (the catalog is newest-first, so a larger index is older); a category
    with zero entries has no index and sorts as older than everything, so it
    wins outright. Any further tie falls back to VALID_CATEGORIES order, so
    the result is always exactly reproducible from the same catalog. Once
    all four categories are within one of each other, this simply rotates
    through them in that order as each new recipe nudges the counts, rather
    than converging on one fixed favorite.
    """
    if catalog is None:
        catalog = load_published_catalog()

    counts = category_counts(catalog)
    full_counts = {cat: counts.get(cat, 0) for cat in VALID_CATEGORIES}
    recipes = catalog_recipes(catalog)

    most_recent_index: dict[str, int] = {}
    for idx, recipe in enumerate(recipes):
        cat = str(recipe.get("category") or "").strip().title()
        if cat in VALID_CATEGORIES and cat not in most_recent_index:
            most_recent_index[cat] = idx

    min_count = min(full_counts.values())
    tied = [cat for cat in VALID_CATEGORIES if full_counts[cat] == min_count]
    tied.sort(key=lambda cat: (-most_recent_index.get(cat, len(recipes)), VALID_CATEGORIES.index(cat)))
    chosen = tied[0]

    print(f"  [category] Counts: {full_counts} -> selected: {chosen}", file=sys.stderr)
    return chosen


# ---------------------------------------------------------------------------
# Title parsing helpers
# ---------------------------------------------------------------------------

def _title_words(title: str) -> list[str]:
    return _WORD_RE.findall(title.lower())


def _dish_noun(title: str) -> str:
    """The candidate's core dish word: the last significant word before the
    trailing muffin-pan form noun, normalized to match catalog phrasing.

    This is deliberately AGGRESSIVE — at this decision point there are 12+
    candidates to choose from, so rejecting one for reusing a dish word the
    catalog already has costs nothing. The exact same rule would be wrong
    applied at the title validator gate (RUNBOOK INCIDENT 3): there it runs
    against a single already-generated title with no fallback pool, so a
    false rejection there fails the whole week instead of just trimming a
    candidate list.
    """
    words = _title_words(title)
    if words and words[-1] in MUFFIN_PAN_FORM_NOUNS:
        words = words[:-1]
    significant = [w for w in words if w not in STOP_WORDS]
    if not significant:
        return ""
    return _normalize_title_word(significant[-1])


def _season_for_month(month: int) -> str:
    return next((s for months, s in SEASON_MAP.items() if month in months), "unknown")


def _build_signals(catalog: dict[str, Any], extra_recent_titles: Sequence[str]) -> _CatalogSignals:
    all_titles = catalog_titles(catalog) + [t.lower().strip() for t in extra_recent_titles if t]
    word_freq = _build_word_freq(all_titles)
    sig_words: set[str] = set()
    for t in all_titles:
        sig_words |= _significant_words(t)
    used_cuisines = {
        str(r.get("cuisine") or "").strip().title()
        for r in catalog_recipes(catalog)
        if str(r.get("cuisine") or "").strip()
    }
    return _CatalogSignals(
        all_titles=all_titles,
        word_freq=word_freq,
        catalog_significant_words=sig_words,
        used_cuisines=used_cuisines,
    )


# ---------------------------------------------------------------------------
# Hard filters — a candidate failing ANY is rejected outright (#6858 item 4).
# ---------------------------------------------------------------------------

def _reject_reason(
    candidate: Candidate,
    target_category: str,
    signals: _CatalogSignals,
) -> Optional[str]:
    low = candidate.concept.lower()

    if any(re.search(pattern, low) for pattern in OFF_BRAND_TITLE_SHAPES):
        return "off_brand_shape: title names a shape a muffin pan cannot make"

    words = _title_words(candidate.concept)
    last = words[-1] if words else ""
    if last not in MUFFIN_PAN_FORM_NOUNS:
        return f"no_pan_form: title does not end in a muffin-pan form noun (last word {last!r})"

    wset = set(words)
    if target_category == "Sweet":
        hit = wset & SAVORY_MARKERS
        if hit:
            return f"wrong_category: contains savory marker(s) {sorted(hit)} despite Sweet target"
    if target_category in ("Breakfast", "Savory"):
        hit = wset & DESSERT_MARKERS
        if hit:
            return f"wrong_category: contains dessert marker(s) {sorted(hit)} despite {target_category} target"

    declared = candidate.category.strip().title()
    if declared != target_category:
        return f"wrong_category: declared '{declared}' != target '{target_category}'"

    # Checked before novelty_overlap: a shared dish noun is the more precise
    # signal (a title can share one distinctive word with a catalog entry
    # without crossing the 50% overlap threshold below), and reporting it as
    # the reason is more useful than a generic overlap percentage.
    dn = _dish_noun(candidate.concept)
    if dn and dn in signals.catalog_significant_words:
        return f"dish_noun_collision: '{dn}' is already a significant word in the published catalog"

    for recent in signals.all_titles:
        overlap = len(set(low.split()) & set(recent.split())) / max(len(low.split()), 1)
        if overlap > 0.5:
            return f"novelty_overlap: {overlap:.2f} word overlap with catalog title '{recent}'"

    cand_words = set(low.split()) - STOP_WORDS
    overused = sorted(w for w in cand_words if signals.word_freq.get(w, 0) >= 3)
    if overused:
        return f"overused_word: {overused} already appear in 3+ published titles"

    return None


# ---------------------------------------------------------------------------
# Soft score — ranks survivors only; never decides eligibility (#6858 item 5).
# ---------------------------------------------------------------------------

_BINDS_HOW_KEYWORDS = ("bind", "set", "hold", "firm", "release", "unmold")


def _score_candidate(candidate: Candidate, signals: _CatalogSignals, month: int) -> float:
    low = candidate.concept.lower()
    score = 0.0

    cand_words = set(low.split()) - STOP_WORDS
    if cand_words:
        fresh_fraction = sum(1 for w in cand_words if signals.word_freq.get(w, 0) == 0) / len(cand_words)
    else:
        fresh_fraction = 0.0
    score += 3.0 * fresh_fraction

    cuisine = candidate.cuisine.strip()
    if cuisine and cuisine.title() not in signals.used_cuisines:
        score += 2.0

    season = _season_for_month(month)
    for kw in SEASON_KEYWORDS.get(season, []):
        if kw in low:
            score += 0.5

    penalty = sum(1.0 for w in cand_words if signals.word_freq.get(w, 0) == 2)
    score -= min(penalty, 2.0)

    if candidate.source == "brainstorm":
        binds_low = candidate.binds_how.lower()
        if any(kw in binds_low for kw in _BINDS_HOW_KEYWORDS):
            score += 0.5

    return round(score, 2)


def rank_candidates(
    candidates: list[Candidate],
    target_category: str,
    catalog: dict[str, Any],
    *,
    extra_recent_titles: Sequence[str] = (),
    month: Optional[int] = None,
) -> tuple[list[tuple[float, Candidate]], list[tuple[Candidate, str]]]:
    """Filter `candidates` against the catalog, then score+sort survivors.

    Returns (survivors sorted best-first as (score, candidate) pairs,
    rejected as (candidate, reason) pairs) so callers — including tests —
    can inspect exactly why a candidate was dropped without parsing
    dry-run stdout.
    """
    signals = _build_signals(catalog, extra_recent_titles)
    if month is None:
        month = date.today().month

    survivors: list[tuple[float, Candidate]] = []
    rejected: list[tuple[Candidate, str]] = []
    for cand in candidates:
        reason = _reject_reason(cand, target_category, signals)
        if reason:
            rejected.append((cand, reason))
            continue
        survivors.append((_score_candidate(cand, signals, month), cand))

    survivors.sort(key=lambda pair: (-pair[0], pair[1].concept))
    return survivors, rejected


# ---------------------------------------------------------------------------
# Brainstorm — one LLM call, catalog-aware, inspiration-optional (#6858 item 2)
# ---------------------------------------------------------------------------

def _build_brainstorm_prompt(
    target_category: str,
    month: int,
    signals: _CatalogSignals,
    inspiration: list[str],
) -> str:
    season = _season_for_month(month)
    overused_words = sorted(w for w, c in signals.word_freq.items() if c >= 2)
    fresh_cuisines = [c for c in WORLD_CUISINES if c.title() not in signals.used_cuisines]
    category_lines = "\n".join(f"- {cat}: {definition}" for cat, definition in CATEGORY_DEFINITIONS.items())
    catalog_titles_block = "\n".join(f"- {t.title()}" for t in signals.all_titles) or "(none published yet)"
    inspiration_block = (
        "\n".join(f"- {name}" for name in inspiration)
        if inspiration else "(no external inspiration reachable this run)"
    )

    return f"""Propose exactly 12 NEW muffin-pan recipe concepts for this week's target category: {target_category}.

Category definitions (every candidate you propose must be a {target_category} dish):
{category_lines}

Season: it is currently {season} (month {month} of the year).

Cuisines already published — avoid repeating these: {", ".join(sorted(signals.used_cuisines)) or "(none yet)"}.
Reach for a cuisine NOT yet used. World cuisines to draw from: {", ".join(fresh_cuisines) or ", ".join(WORLD_CUISINES)}.

Overused words across the published catalog — avoid these in your titles: {", ".join(overused_words) or "(none)"}.

Every existing published or in-progress title — do NOT propose these dishes or close variants under other names:
{catalog_titles_block}

Trending right now, optional inspiration only — do not copy these as-is and do not treat them as candidates themselves:
{inspiration_block}

Brand rule: a muffin pan's wells are ROUND. Never use these words in a title: squares, bars, slabs, slices, wedges, sheet, traybake, casserole.

Reply with ONLY a JSON array of exactly 12 objects, no prose, no markdown fences, each shaped exactly like:
{{"concept": "<Title Case, 3-6 words, MUST end in a muffin-pan form noun such as Cups, Bites, Tartlets, Loaf, Cakes, or Popovers>", "category": "{target_category}", "cuisine": "<one or two words>", "binds_how": "<one sentence: how it sets, binds, or holds shape when lifted from the pan>"}}
"""


def _parse_brainstorm_json(raw: str) -> list[Candidate]:
    """Tolerant parse: slice from the first '[' to the last ']' and decode.

    An LLM response wrapped in prose or markdown fences is still usable this
    way. Any parse failure — or a payload that isn't a JSON array — means
    zero brainstorm candidates, which routes to the curated fallback below
    rather than raising; a malformed brainstorm is not fatal on its own.
    """
    try:
        start = raw.index("[")
        end = raw.rindex("]")
        data = json.loads(raw[start : end + 1])
    except Exception as exc:
        print(f"  [warn] brainstorm output unparseable ({type(exc).__name__}: {exc}); "
              f"treating as zero candidates", file=sys.stderr)
        return []

    if not isinstance(data, list):
        print("  [warn] brainstorm output was not a JSON array; treating as zero candidates", file=sys.stderr)
        return []

    candidates: list[Candidate] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        concept = str(item.get("concept", "")).strip()
        category = str(item.get("category", "")).strip()
        cuisine = str(item.get("cuisine", "")).strip()
        binds_how = str(item.get("binds_how", "")).strip()
        if concept and category:
            candidates.append(Candidate(concept, category, cuisine, binds_how, source="brainstorm"))
    return candidates


def _brainstorm(
    generate: Optional[Callable[..., str]],
    target_category: str,
    month: int,
    signals: _CatalogSignals,
    inspiration: list[str],
) -> list[Candidate]:
    prompt = _build_brainstorm_prompt(target_category, month, signals, inspiration)
    system_prompt = (
        "You are the concept brainstormer for an editorial muffin-pan recipe "
        "site. Respond with ONLY a JSON array of 12 objects — no prose, no "
        "markdown fences."
    )
    generate_fn = generate or _generate_response
    model = os.environ.get("CONCEPT_MODEL", "").strip() or config.dialogue_model

    try:
        raw = generate_fn(prompt, system_prompt, model=model, temperature=0.9)
    except Exception as exc:
        print(f"  [warn] brainstorm call failed ({type(exc).__name__}: {exc}); "
              f"treating as zero candidates", file=sys.stderr)
        return []

    return _parse_brainstorm_json(raw)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _no_concept_message(target_category: str, rejected: list[tuple[Candidate, str]]) -> str:
    reason_counts: Counter[str] = Counter()
    for _, reason in rejected:
        reason_counts[reason.split(":", 1)[0]] += 1
    return (
        f"no concept candidate survived filtering for target_category="
        f"{target_category!r}; rejected {len(rejected)} candidates by reason: "
        f"{dict(reason_counts)}"
    )


def _print_dry_run(
    target_category: str,
    survivors: list[tuple[float, Candidate]],
    rejected: list[tuple[Candidate, str]],
) -> None:
    print(f"\n{'=' * 70}")
    print(f"  CONCEPT PICKER — DRY RUN  (target category: {target_category})")
    print(f"  {len(survivors)} survivors, {len(rejected)} rejected")
    print(f"{'=' * 70}")
    for score, cand in survivors:
        print(f"  {score:5.2f}  [{cand.source:10s}]  {cand.category:9s}  {cand.cuisine:15s}  {cand.concept}")
    if rejected:
        print("\n  Rejected:")
        for cand, reason in rejected:
            print(f"    {cand.concept!r} — {reason}")
    print(f"{'=' * 70}\n")


def pick_concept(
    dry_run: bool = False,
    count: int = 1,
    target_category: Optional[str] = None,
    *,
    catalog: Optional[dict[str, Any]] = None,
    generate: Optional[Callable[..., str]] = None,
    fetch_inspiration: bool = True,
) -> list[str]:
    """Pick this week's concept(s). Returns the top `count` concept strings.

    Never returns []: with zero survivors from both the brainstorm and
    curated pools this raises NoConceptAvailableError instead (#6858) —
    the cron caller already treats any exception here as a retryable, then
    fail-closed, condition (see cron_routes._pick_weekly_concept).

    `catalog`, `generate`, and `fetch_inspiration` exist for injection in
    tests; production calls (cron_routes.py, scripts/run_pipeline_stage.py)
    leave them at their defaults, which load the live catalog and call the
    real LLM.

    CatalogUnavailableError from load_published_catalog() is intentionally
    left to propagate — a picker that cannot see the catalog has no basis
    for duplicate avoidance or category balancing and must not guess.
    """
    if catalog is None:
        catalog = load_published_catalog()

    if target_category is None:
        target_category = pick_target_category(catalog)

    local_recent = _load_recent_concepts()
    month = date.today().month
    signals = _build_signals(catalog, local_recent)

    inspiration = _fetch_inspiration() if fetch_inspiration else []

    brainstorm_candidates = _brainstorm(generate, target_category, month, signals, inspiration)
    survivors, rejected = rank_candidates(
        brainstorm_candidates, target_category, catalog,
        extra_recent_titles=local_recent, month=month,
    )

    if not survivors:
        curated_candidates = _curated_pool(target_category)
        c_survivors, c_rejected = rank_candidates(
            curated_candidates, target_category, catalog,
            extra_recent_titles=local_recent, month=month,
        )
        survivors = c_survivors
        rejected = rejected + c_rejected

    if dry_run:
        _print_dry_run(target_category, survivors, rejected)

    if not survivors:
        raise NoConceptAvailableError(_no_concept_message(target_category, rejected))

    chosen = [cand.concept for _, cand in survivors[:count]]
    if not dry_run:
        for score, cand in survivors[:count]:
            print(f"  ✅ Selected: {cand.concept}  (score={score}, source={cand.source})", file=sys.stderr)
    return chosen


def main() -> None:
    parser = argparse.ArgumentParser(description="Pick this week's muffin-pan recipe concept")
    parser.add_argument("--dry-run", action="store_true", help="Print survivors and rejects without selecting")
    parser.add_argument("--count", type=int, default=1, help="Number of concepts to select (default: 1)")
    parser.add_argument(
        "--category", choices=VALID_CATEGORIES, default=None,
        help="Force a target category instead of the deterministic thinnest-category pick",
    )
    parser.add_argument(
        "--no-inspiration", action="store_true",
        help="Skip the optional third-party scrape for trending inspiration",
    )
    args = parser.parse_args()

    try:
        concepts = pick_concept(
            dry_run=args.dry_run,
            count=args.count,
            target_category=args.category,
            fetch_inspiration=not args.no_inspiration,
        )
    except (CatalogUnavailableError, NoConceptAvailableError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)

    if concepts and not args.dry_run:
        for c in concepts:
            print(c)


if __name__ == "__main__":
    main()
