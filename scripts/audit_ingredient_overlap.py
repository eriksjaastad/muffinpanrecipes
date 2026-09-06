#!/usr/bin/env python3
"""Operator tool for the ingredient-overlap duplicate gate (#6854).

Two jobs:

1. Default mode — recalibration. Loads the live catalog and prints the
   pairwise overlap-coefficient distribution: the top 15 closest pairs, a
   publish-order retrospective (each recipe's max score against everything
   published before it, flagging anything that would have tripped the
   gate), and which catalog entries were excluded for being too thin
   (< --min-items) to compare. Read-only, always exits 0. Re-run this after
   a batch of new weeks to see whether DUPLICATE_THRESHOLD is still holding
   the margin documented in backend/utils/recipe_overlap.py's docstring.

2. ``--episode 2026-W37`` — the Saturday/pre-publish check. Fetches that
   week's episode JSON, pulls ``stages.monday.recipe_data``, and runs
   ``check_ingredient_overlap`` against the live catalog (excluding the
   week's own episode_id, since a re-run after Monday already published
   would otherwise compare the recipe against itself). Prints the top 5
   matches and the verdict; exits 1 if the verdict is "duplicate" so this
   can gate a script or CI step, not just be eyeballed.

Usage:
    uv run python scripts/audit_ingredient_overlap.py
    uv run python scripts/audit_ingredient_overlap.py --episode 2026-W37
    uv run python scripts/audit_ingredient_overlap.py --threshold 0.75 --min-items 8
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.catalog import (  # noqa: E402
    CatalogUnavailableError,
    catalog_recipes,
    load_published_catalog,
)
from backend.utils.recipe_overlap import (  # noqa: E402
    DUPLICATE_THRESHOLD,
    MIN_ITEMS,
    check_ingredient_overlap,
    ingredient_items,
    overlap_coefficient,
)

BLOB_CDN = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
TIMEOUT_SECONDS = 10


def _fetch_json(url: str) -> object:
    req = urllib.request.Request(
        url, headers={"User-Agent": "muffinpanrecipes-overlap-audit/1.0"}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _print_top_pairs(
    recipes: list[dict], sets_by_title: dict[str, list], min_items: int, limit: int = 15
) -> None:
    pairs: list[tuple[float, str, str, int, int]] = []
    for a, b in itertools.combinations(recipes, 2):
        set_a, set_b = sets_by_title[a["title"]], sets_by_title[b["title"]]
        if len(set_a) < min_items or len(set_b) < min_items:
            continue
        pairs.append(
            (overlap_coefficient(set_a, set_b), a["title"], b["title"], len(set_a), len(set_b))
        )
    pairs.sort(reverse=True)
    print(f"\n== Top {limit} pairs by overlap coefficient ({len(pairs)} eligible pairs) ==")
    for score, title_a, title_b, count_a, count_b in pairs[:limit]:
        print(f"  {score:5.2f}  {title_a} [{count_a}]  vs  {title_b} [{count_b}]")


def _print_retrospective(
    recipes_newest_first: list[dict], sets_by_title: dict[str, list], min_items: int, threshold: float
) -> None:
    # catalog_recipes() returns newest-first; publish order is oldest-first.
    oldest_first = list(reversed(recipes_newest_first))
    print("\n== Publish-order retrospective (max score vs everything published before) ==")
    seen: list[dict] = []
    for recipe in oldest_first:
        items = sets_by_title[recipe["title"]]
        # Not every early recipe carries an episode_id — the field was added
        # after the first several weeks. "no-id" here is honest about that;
        # it does NOT mean "one of the ten launch seeds" (those are the
        # entries excluded below for being under --min-items).
        label = recipe.get("episode_id") or "no-id"
        if len(items) < min_items:
            seen.append(recipe)
            continue
        eligible_prior = [r for r in seen if len(sets_by_title[r["title"]]) >= min_items]
        if not eligible_prior:
            print(f"  {label:9} {recipe['title']:42} (first eligible entry)")
            seen.append(recipe)
            continue
        best_score, best_title = max(
            (
                (overlap_coefficient(items, sets_by_title[prior["title"]]), prior["title"])
                for prior in eligible_prior
            ),
            key=lambda pair: pair[0],
        )
        flag = f"  <-- would flag @ {threshold:.2f}" if best_score >= threshold else ""
        print(f"  {label:9} {recipe['title']:42} max={best_score:.2f} vs {best_title}{flag}")
        seen.append(recipe)


def _print_excluded(recipes: list[dict], sets_by_title: dict[str, list], min_items: int) -> None:
    excluded = [r["title"] for r in recipes if len(sets_by_title[r["title"]]) < min_items]
    print(f"\n== Excluded for < {min_items} normalized items: {len(excluded)} ==")
    for title in excluded:
        print(f"  {title}")


def _run_default(min_items: int, threshold: float) -> int:
    try:
        catalog = load_published_catalog()
    except CatalogUnavailableError as exc:
        print(f"could not load the published catalog: {exc}")
        return 0  # informational tool — never fails a session over a flaky fetch

    recipes = catalog_recipes(catalog)
    sets_by_title = {r["title"]: ingredient_items(r) for r in recipes}

    _print_top_pairs(recipes, sets_by_title, min_items)
    _print_retrospective(recipes, sets_by_title, min_items, threshold)
    _print_excluded(recipes, sets_by_title, min_items)
    return 0


def _run_episode(episode_id: str, min_items: int, threshold: float) -> int:
    try:
        catalog = load_published_catalog()
    except CatalogUnavailableError as exc:
        print(f"could not load the published catalog: {exc}")
        return 1

    try:
        episode = _fetch_json(f"{BLOB_CDN}/episodes/{episode_id}.json")
    except Exception as exc:
        print(f"could not fetch episode {episode_id}: {type(exc).__name__}: {exc}")
        return 1

    if not isinstance(episode, dict):
        print(f"episode {episode_id} did not return a JSON object")
        return 1

    recipe_data = episode.get("stages", {}).get("monday", {}).get("recipe_data")
    if not isinstance(recipe_data, dict):
        print(f"episode {episode_id} has no stages.monday.recipe_data yet")
        return 1

    recipes = catalog_recipes(catalog)
    verdict = check_ingredient_overlap(
        recipe_data,
        recipes,
        threshold=threshold,
        min_items=min_items,
        exclude_episode_id=episode_id,
    )

    from backend.utils.recipe_overlap import find_ingredient_overlaps

    top_matches = find_ingredient_overlaps(
        recipe_data, recipes, min_items=min_items, exclude_episode_id=episode_id
    )[:5]

    title = recipe_data.get("title", "(untitled)")
    print(f"episode {episode_id}: '{title}' — {verdict.new_items} normalized ingredient items")
    print("\n== Top 5 matches ==")
    if not top_matches:
        print("  (no eligible catalog entries to compare against)")
    for match in top_matches:
        print(
            f"  {match.score:5.2f}  {match.title} ({match.episode_id}) "
            f"[{match.new_items} vs {match.existing_items}]"
        )

    print(f"\nverdict: {verdict.status}")
    if verdict.reason:
        print(f"reason: {verdict.reason}")

    return 1 if verdict.status == "duplicate" else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--episode",
        help="ISO week to check, e.g. 2026-W37 (pre-publish check; exits 1 on duplicate)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DUPLICATE_THRESHOLD,
        help=f"overlap-coefficient duplicate cutoff (default: {DUPLICATE_THRESHOLD})",
    )
    parser.add_argument(
        "--min-items",
        type=int,
        default=MIN_ITEMS,
        help=f"minimum normalized ingredient items required on both sides (default: {MIN_ITEMS})",
    )
    args = parser.parse_args()

    if args.episode:
        return _run_episode(args.episode, args.min_items, args.threshold)
    return _run_default(args.min_items, args.threshold)


if __name__ == "__main__":
    sys.exit(main())
