#!/usr/bin/env python3
"""One-time fix: clean stale parenthetical recipe-page titles (W10, W11).

The W10 and W11 standalone recipe pages froze in Vercel Blob with old titles
carrying parenthetical qualifiers — pre-title-cleanup artifacts. The live
catalog (`pages/recipes.json`) already holds the clean titles, but the page
`<title>`/`og:title`/`twitter:title` come from the episode JSON's raw
`stages.monday.recipe_data.title` (episode_renderer reads it un-cleaned), so
the pages diverged from the catalog.

This reconciles the authored source: set the clean title on the episode JSON,
re-render the page through the same renderer, and write it back to the two
blob paths the renderer/fix_encoding use. Canonical is pinned to the page's
existing serve-slug; description and every other field are left untouched.

W12 (roasted-chicken-potato-cups) is deliberately EXCLUDED: its Sunday
editorial QA failed (published_at=None) — it is a skipped recipe, not a
title bug, and is tracked for a separate remove/rehabilitate decision.

Usage:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_stale_titles.py --dry-run
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_stale_titles.py
"""

from __future__ import annotations

import argparse
import html
import sys

sys.path.insert(0, ".")

from backend.storage import storage
from backend.publishing.episode_renderer import render_episode_page

# episode_id -> (serve_slug, approved clean title, stale substring that must vanish)
FIXES = {
    "2026-W10": ("mini-lemon-meringue-cups", "Mini Lemon Meringue Cups", "Muffin-Tin Tartlets"),
    "2026-W11": ("make-ahead-veggie-sausage-egg-cups", "Make-Ahead Veggie & Sausage Egg Cups", "Weekly Muffin Pan Breakfast"),
}


def fix(episode_id: str, serve_slug: str, clean_title: str, stale_marker: str, dry_run: bool) -> bool:
    ep = storage.load_episode(episode_id)
    if not ep:
        print(f"  SKIP {episode_id}: episode not found")
        return False

    recipe = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})
    old_title = recipe.get("title", "")
    print(f"  {episode_id}: {old_title!r}")
    print(f"          -> {clean_title!r}   (page /recipes/{serve_slug})")

    if dry_run:
        return True

    # 1) Update the authored title at its source.
    recipe["title"] = clean_title
    storage.save_episode(episode_id, ep)

    # 2) Round-trip the episode write so a stale read can't silently no-op it.
    back = storage.load_episode(episode_id) or {}
    got = back.get("stages", {}).get("monday", {}).get("recipe_data", {}).get("title")
    assert got == clean_title, f"episode title round-trip failed: got {got!r}"

    # 3) Re-render from the corrected episode; pin canonical to the serve-slug.
    image_urls = back.get("image_urls", [])
    image_url = image_urls[0] if image_urls else None
    page_html = render_episode_page(back, image_url=image_url, canonical_slug=serve_slug)

    # 4) Verify the rendered output before writing it live.
    esc = html.escape(clean_title)
    assert f"<title>{esc} | Muffin Pan Recipes</title>" in page_html, "clean <title> missing"
    assert f'<meta property="og:title" content="{esc} | Muffin Pan Recipes">' in page_html, "og:title missing"
    assert f"https://muffinpanrecipes.com/recipes/{serve_slug}" in page_html, "canonical/serve-slug missing"
    assert stale_marker not in page_html, f"stale marker {stale_marker!r} still present"

    # 5) Write to the two blob paths the renderer/fix_encoding maintain.
    storage.save_page(f"pages/recipes/{serve_slug}/index.html", page_html)
    storage.save_page(f"pages/{episode_id}/index.html", page_html)
    print(f"          wrote pages/recipes/{serve_slug}/index.html ({len(page_html)} bytes) + pages/{episode_id}/index.html")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean stale parenthetical recipe titles (W10, W11)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    print(f"{'DRY RUN: ' if args.dry_run else ''}Cleaning stale recipe-page titles...\n")
    count = 0
    for episode_id, (serve_slug, clean_title, stale_marker) in FIXES.items():
        if fix(episode_id, serve_slug, clean_title, stale_marker, args.dry_run):
            count += 1
        print()
    print(f"Done. {'would fix' if args.dry_run else 'fixed'} {count} page(s).")


if __name__ == "__main__":
    main()
