#!/usr/bin/env python3
"""One-time data fix: correct the stray "Dessert" category to "Sweet".

The site's taxonomy is Breakfast / Savory / Sweet / Party. One cron recipe
(W10, mini-lemon-meringue-cups) was mislabeled "Dessert", which split it off
into a category of one. Fix it at both sources of truth:

  1. The live catalog (pages/recipes.json) -> homepage filter + JSON-LD-via-page.
  2. The W10 episode JSON (recipe_data.category) -> what the page re-renders from.

The catalog is read straight from the public CDN with a unique cache-buster to
dodge Vercel-Blob read-after-write staleness.

Usage:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_category_dessert_to_sweet.py --dry-run
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_category_dessert_to_sweet.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

sys.path.insert(0, ".")

from backend.storage import storage

PUBLIC_BASE = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
KNOWN_EPISODE = "2026-W10"  # mini-lemon-meringue-cups


def _fresh_catalog():
    r = requests.get(f"{PUBLIC_BASE}/pages/recipes.json?cb={time.time_ns()}", timeout=15)
    r.raise_for_status()
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    catalog = _fresh_catalog()
    recipes = catalog if isinstance(catalog, list) else catalog.get("recipes", [])
    strays = [r for r in recipes if r.get("category", "").strip().lower() == "dessert"]
    print(f"catalog: {len(recipes)} recipes; category=='Dessert': "
          f"{[r.get('slug') for r in strays]}")

    ep = storage.load_episode(KNOWN_EPISODE) or {}
    ep_cat = ep.get("stages", {}).get("monday", {}).get("recipe_data", {}).get("category")
    print(f"episode {KNOWN_EPISODE} recipe_data.category: {ep_cat!r}")

    if args.dry_run:
        print(f"\n[DRY RUN] would set {len(strays)} catalog entr(y/ies) -> 'Sweet' "
              f"and episode category -> 'sweet'.")
        return

    # 1) Catalog: Dessert -> Sweet (title-case, matching sibling categories).
    for r in strays:
        r["category"] = "Sweet"
    if strays:
        storage.save_page("pages/recipes.json", json.dumps(catalog, indent=2))
        print(f"catalog: set {len(strays)} entr(y/ies) to 'Sweet'")

    # 2) Episode: dessert -> sweet (lowercase, matching sibling episodes; the
    #    renderer .title()s it). Keep published_at and everything else intact.
    rd = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})
    if rd.get("category", "").strip().lower() == "dessert":
        rd["category"] = "sweet"
        storage.save_episode(KNOWN_EPISODE, ep)
        back = storage.load_episode(KNOWN_EPISODE) or {}
        got = back.get("stages", {}).get("monday", {}).get("recipe_data", {}).get("category")
        assert got == "sweet", f"episode category round-trip failed: {got!r}"
        print(f"episode {KNOWN_EPISODE}: category -> 'sweet' (verified)")

    print("Done.")


if __name__ == "__main__":
    main()
