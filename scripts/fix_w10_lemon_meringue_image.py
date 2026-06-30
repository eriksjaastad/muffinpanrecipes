#!/usr/bin/env python3
"""One-time fix: repair the W10 "Mini Lemon Meringue Cups" hero image.

The W10 episode JSON points its images at a phantom hierarchical path
(`images/8a79d045/round_1/macro_closeup.png`) that was never written to blob —
the real photo is the flat `images/8a79d045.webp` (the known W10 flat-vs-
hierarchical naming mismatch). The rendered recipe page therefore 404s on its
hero image while the catalog (already repaired) serves the flat path fine.

This repoints the hero reference in the episode JSON to the working flat path
so any re-render produces the real image. It is a SOURCE-DATA fix only — it
does NOT re-render the live page. The page re-render happens at the single prod
deploy (the new template needs the deployed `/assets/site.css`), at which point
the corrected image goes live. Idempotent: a second run finds nothing to do.

Usage:
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/fix_w10_lemon_meringue_image.py --dry-run
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/fix_w10_lemon_meringue_image.py
"""

from __future__ import annotations

import argparse
import sys

from backend.storage import storage

EPISODE_ID = "2026-W10"
BROKEN = "images/8a79d045/round_1/macro_closeup.png"
WORKING = "images/8a79d045.webp"


def _repoint(urls):
    """Replace the phantom hero path with the flat working path. Returns
    (new_list, count). Leaves the list untouched if it isn't a list."""
    if not isinstance(urls, list):
        return urls, 0
    out, n = [], 0
    for u in urls:
        if isinstance(u, str) and BROKEN in u:
            out.append(u.replace(BROKEN, WORKING))
            n += 1
        else:
            out.append(u)
    return out, n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Show changes without writing.")
    args = ap.parse_args()

    ep = storage.load_episode(EPISODE_ID)
    if not ep:
        print(f"ERROR: episode {EPISODE_ID} not found in blob")
        return 1

    title = ep.get("stages", {}).get("monday", {}).get("recipe_data", {}).get("title", "?")
    print(f"Episode {EPISODE_ID}: {title}")

    total = 0
    ep["image_urls"], n = _repoint(ep.get("image_urls"))
    if n:
        print(f"  top-level image_urls: repointed hero ({n})")
        total += n
    for day, stage in ep.get("stages", {}).items():
        if isinstance(stage, dict) and "image_urls" in stage:
            stage["image_urls"], n = _repoint(stage["image_urls"])
            if n:
                print(f"  stages.{day}.image_urls: repointed hero ({n})")
                total += n

    if total == 0:
        print("  nothing to fix — already repaired (idempotent, no write).")
        return 0

    if args.dry_run:
        print(f"  DRY RUN: would repoint {total} reference(s) -> {WORKING} and save.")
        return 0

    storage.save_episode(EPISODE_ID, ep)
    print(f"  SAVED: repointed {total} reference(s) -> {WORKING}")

    # Confirm the hero now resolves to the flat path the page will render.
    hero = (ep.get("image_urls") or [None])[0]
    if hero:
        from backend.publishing.episode_renderer import _to_local_image_url
        print(f"  hero image_urls[0] now renders -> {_to_local_image_url(hero)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
