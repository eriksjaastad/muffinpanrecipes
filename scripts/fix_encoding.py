#!/usr/bin/env python3
"""Re-render all published recipe pages from episode JSON to fix encoding.

Fixes double-encoded UTF-8 (mojibake) in blob-stored recipe pages by
re-rendering them fresh from the episode data.

Usage:
    # Dry run (show what would be fixed):
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_encoding.py --dry-run

    # Fix all published recipes:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_encoding.py

    # Fix a specific episode:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_encoding.py --episode 2026-W12
"""

from __future__ import annotations

import argparse
import sys

# Ensure project root is on path
sys.path.insert(0, ".")

from backend.storage import storage
from backend.publishing.episode_renderer import (
    render_episode_page,
    _slugify,
    _clean_title,
)


def fix_episode(episode_id: str, dry_run: bool = False) -> bool:
    """Re-render and re-upload a published episode's recipe page.

    Returns True if the page was fixed (or would be in dry-run mode).
    """
    ep = storage.load_episode(episode_id)
    if not ep:
        print(f"  SKIP {episode_id}: episode not found")
        return False

    # Check if published
    sunday = ep.get("stages", {}).get("sunday", {})
    if sunday.get("status") != "complete":
        print(f"  SKIP {episode_id}: not published (sunday status={sunday.get('status')!r})")
        return False

    # Get recipe title and slug
    monday = ep.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    title = _clean_title(recipe.get("title", ""))
    if not title:
        print(f"  SKIP {episode_id}: no recipe title")
        return False

    slug = _slugify(title)
    image_urls = ep.get("image_urls", [])
    image_url = image_urls[0] if image_urls else None

    if dry_run:
        print(f"  WOULD FIX {episode_id}: /recipes/{slug} ({title})")
        return True

    # Re-render fresh from episode JSON
    page_html = render_episode_page(ep, image_url=image_url)

    # Upload episode page
    storage.save_page(f"pages/{episode_id}/index.html", page_html)
    print(f"  FIXED pages/{episode_id}/index.html ({len(page_html)} bytes)")

    # Upload recipe page
    storage.save_page(f"pages/recipes/{slug}/index.html", page_html)
    print(f"  FIXED pages/recipes/{slug}/index.html ({len(page_html)} bytes)")

    return True


def main():
    parser = argparse.ArgumentParser(description="Fix encoding in published recipe pages")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be fixed without making changes")
    parser.add_argument("--episode", type=str, help="Fix a specific episode ID (e.g. 2026-W12)")
    args = parser.parse_args()

    print(f"{'DRY RUN: ' if args.dry_run else ''}Re-rendering published recipe pages...\n")

    if args.episode:
        fixed = fix_episode(args.episode, dry_run=args.dry_run)
        total = 1 if fixed else 0
    else:
        episodes = storage.list_episodes()
        print(f"Found {len(episodes)} episodes\n")
        total = 0
        for ep_summary in episodes:
            episode_id = ep_summary.get("episode_id", "")
            if fix_episode(episode_id, dry_run=args.dry_run):
                total += 1

    action = "would fix" if args.dry_run else "fixed"
    print(f"\nDone. {action} {total} recipe page(s).")


if __name__ == "__main__":
    main()
