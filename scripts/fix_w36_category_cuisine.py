#!/usr/bin/env python3
"""One-time data fix: W36 "Caramelized Custard Tart Cups" is a pastel de nata
— a Sweet, Portuguese dish — but shipped mislabeled Savory/blank (#6877).

WHY: the 2026-09-05 manual repair re-fired Monday with an explicit
body.concept and no stored target_category, so the baker ran with
target_category=None and _parse_recipe_response defaulted category to
'savory'. The catalog entry copies recipe_data.category verbatim.

Modelled on scripts/fix_category_dessert_to_sweet.py. Fix at every source of
truth so the picker's category counts (#6858) and the frozen page/JSON-LD
agree with each other:

  1. pages/recipes.json — the 2026-W36 catalog entry: category, cuisine.
  2. episodes/2026-W36.json — stages.monday.recipe_data.category/cuisine,
     top-level target_category, and stages.monday.target_category (the
     episode_integrity fields #6858 added).
  3. Re-render + re-upload the page via
     backend.publishing.episode_renderer.regenerate_and_upload so the frozen
     HTML/JSON-LD stop saying "savory".

Refuses to run against anything but the one title this was written for —
this is a scalpel for one known-bad week, not a general category editor.

Usage:
    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/fix_w36_category_cuisine.py            # dry-run
    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/fix_w36_category_cuisine.py --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time

import requests

sys.path.insert(0, ".")

from backend.storage import storage

PUBLIC_BASE = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
KNOWN_EPISODE = "2026-W36"
EXPECTED_TITLE = "Caramelized Custard Tart Cups"
FIXED_CATEGORY_TITLE_CASE = "Sweet"  # catalog entries + episode target_category
FIXED_CATEGORY_LOWER = "sweet"  # stages.monday.recipe_data.category
FIXED_CUISINE = "Portuguese"


def _fresh_catalog() -> dict:
    """Read the catalog straight from the public CDN with a cache-buster.

    Dodges Vercel-Blob read-after-write staleness — same reason
    fix_category_dessert_to_sweet.py does this instead of going through
    storage.load_page.
    """
    r = requests.get(f"{PUBLIC_BASE}/pages/recipes.json?cb={time.time_ns()}", timeout=15)
    r.raise_for_status()
    return r.json()


def fix_catalog_entry(entry: dict) -> dict:
    """Pure transform: return a copy of the W36 catalog entry, corrected.

    Changes exactly category -> 'Sweet' and cuisine -> 'Portuguese'; every
    other field passes through untouched.
    """
    fixed = dict(entry)
    fixed["category"] = FIXED_CATEGORY_TITLE_CASE
    fixed["cuisine"] = FIXED_CUISINE
    return fixed


def fix_episode(ep: dict) -> dict:
    """Pure transform: return a deep copy of the W36 episode, corrected.

    Changes exactly:
      - stages.monday.recipe_data.category -> 'sweet'
      - stages.monday.recipe_data.cuisine  -> 'Portuguese'
      - stages.monday.target_category      -> 'Sweet'
      - target_category (top-level)        -> 'Sweet'
    Everything else — including every other field in recipe_data and every
    other stage — passes through untouched.
    """
    fixed = copy.deepcopy(ep)
    monday = fixed.setdefault("stages", {}).setdefault("monday", {})
    recipe_data = monday.setdefault("recipe_data", {})
    recipe_data["category"] = FIXED_CATEGORY_LOWER
    recipe_data["cuisine"] = FIXED_CUISINE
    monday["target_category"] = FIXED_CATEGORY_TITLE_CASE
    fixed["target_category"] = FIXED_CATEGORY_TITLE_CASE
    return fixed


def _find_w36_entry(catalog: dict) -> dict | None:
    recipes = catalog if isinstance(catalog, list) else catalog.get("recipes", [])
    for entry in recipes:
        if isinstance(entry, dict) and entry.get("episode_id") == KNOWN_EPISODE:
            return entry
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write the fix. Default is dry-run.")
    args = ap.parse_args()

    catalog = _fresh_catalog()
    entry = _find_w36_entry(catalog)
    if entry is None:
        print(f"No catalog entry with episode_id={KNOWN_EPISODE!r} found. Nothing to do.")
        return

    title = entry.get("title", "")
    if title != EXPECTED_TITLE:
        print(
            f"REFUSING: {KNOWN_EPISODE} catalog title is {title!r}, "
            f"expected {EXPECTED_TITLE!r}. This script is a scalpel for one "
            "known-bad week — not touching anything else.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"catalog entry: category={entry.get('category')!r} cuisine={entry.get('cuisine')!r}")
    fixed_entry = fix_catalog_entry(entry)
    print(f"       ->      category={fixed_entry['category']!r} cuisine={fixed_entry['cuisine']!r}")

    with storage.prefix_scope(""):
        ep = storage.load_episode(KNOWN_EPISODE) or {}
    if not ep:
        print(f"REFUSING: could not load episode {KNOWN_EPISODE}.", file=sys.stderr)
        sys.exit(1)

    ep_title = ep.get("stages", {}).get("monday", {}).get("recipe_data", {}).get("title", "")
    if ep_title != EXPECTED_TITLE:
        print(
            f"REFUSING: {KNOWN_EPISODE} episode recipe_data.title is {ep_title!r}, "
            f"expected {EXPECTED_TITLE!r}.",
            file=sys.stderr,
        )
        sys.exit(1)

    before_rd = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})
    before_target = ep.get("target_category")
    before_monday_target = ep.get("stages", {}).get("monday", {}).get("target_category")
    print(
        f"episode recipe_data: category={before_rd.get('category')!r} "
        f"cuisine={before_rd.get('cuisine')!r}; target_category={before_target!r}; "
        f"stages.monday.target_category={before_monday_target!r}"
    )
    fixed_ep = fix_episode(ep)
    after_rd = fixed_ep["stages"]["monday"]["recipe_data"]
    print(
        f"       ->      category={after_rd['category']!r} "
        f"cuisine={after_rd['cuisine']!r}; "
        f"target_category={fixed_ep['target_category']!r}; "
        f"stages.monday.target_category={fixed_ep['stages']['monday']['target_category']!r}"
    )

    if not args.apply:
        print("\n[DRY RUN] would write the catalog entry, the episode, and re-render the page.")
        print("Re-run with --apply to write.")
        return

    with storage.prefix_scope(""):
        recipes = catalog if isinstance(catalog, list) else catalog.get("recipes", [])
        new_recipes = [
            fixed_entry if (isinstance(r, dict) and r.get("episode_id") == KNOWN_EPISODE) else r
            for r in recipes
        ]
        if isinstance(catalog, list):
            payload = new_recipes
        else:
            payload = dict(catalog)
            payload["recipes"] = new_recipes
        storage.save_page("pages/recipes.json", json.dumps(payload, indent=2))
        print("catalog: wrote corrected entry")

        storage.save_episode(KNOWN_EPISODE, fixed_ep)
        back = storage.load_episode(KNOWN_EPISODE) or {}
        got_rd = back.get("stages", {}).get("monday", {}).get("recipe_data", {})
        assert got_rd.get("category") == FIXED_CATEGORY_LOWER, (
            f"episode round-trip failed: category={got_rd.get('category')!r}"
        )
        assert got_rd.get("cuisine") == FIXED_CUISINE, (
            f"episode round-trip failed: cuisine={got_rd.get('cuisine')!r}"
        )
        print(f"episode {KNOWN_EPISODE}: category/cuisine/target_category fixed (verified)")

        from backend.publishing.episode_renderer import regenerate_and_upload

        url = regenerate_and_upload(back, strict=True)
        if url is None:
            print("REFUSING: page re-render failed; catalog/episode were already written.", file=sys.stderr)
            sys.exit(1)
        print(f"page re-rendered and uploaded: {url}")

    print("\nDone. Verify with:")
    print("  curl -s https://muffinpanrecipes.com/recipes.json | python3 -m json.tool | grep -A2 W36")
    print(
        "  doppler run --project muffinpanrecipes --config prd -- "
        "uv run python scripts/health_check.py --no-alert"
    )


if __name__ == "__main__":
    main()
