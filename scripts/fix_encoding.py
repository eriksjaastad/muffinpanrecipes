#!/usr/bin/env python3
"""Re-render all published recipe pages from episode JSON to fix encoding.

Fixes double-encoded UTF-8 (mojibake) in blob-stored recipe pages by
re-rendering them fresh from the episode data.

Usage:
    # Dry run one episode (show what would be fixed):
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_encoding.py --episode 2026-W34 --dry-run

    # Dry run all published recipes:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_encoding.py --all --dry-run

    # Fix a specific episode:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_encoding.py --episode 2026-W12

    # Explicitly authorize a full bulk rewrite:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/fix_encoding.py --all --full-rebuild
"""

from __future__ import annotations

import argparse
import sys

# Ensure project root is on path
sys.path.insert(0, ".")

from backend.utils.catalog import (
    CatalogUnavailableError,
    load_published_catalog,
)
from backend.publishing.episode_renderer import (
    _clean_title,
    _slugify,
    render_episode_page,
)
from backend.storage import storage
from backend.utils.recipe_prompts import normalize_recipe_instructions

W34_EPISODE_ID = "2026-W34"



def catalog_slug_for_title(
    title: str,
    catalog: dict | None,
    *,
    episode_id: str | None = None,
    recipe_id: str | None = None,
) -> str | None:
    """Return the slug the live catalog serves this episode under, or None.

    Identity first, title second. Catalog rows written since 2026-09 carry
    `episode_id`/`recipe_id`, and matching on those is exact; the older comment
    here claimed rows had no episode id, which is no longer true (live W35-W37
    all have one). Only legacy rows need the title fallback.

    A title match must be UNIQUE. The previous version returned the first row
    whose title matched, so two rows sharing a cleaned title would have silently
    picked whichever came first and written a published page to the wrong slug.

    Returning None is deliberate in every ambiguous case: a published page whose
    slug cannot be confirmed must be left alone, never written to a guessed path.
    """
    if not catalog:
        return None
    rows = catalog.get("recipes", []) or []

    def _slug_of(row: dict) -> str | None:
        """A slug is only usable if it is a genuinely non-empty string.

        `str(row.get("slug", ""))` turns a JSON null into the literal "None",
        which is truthy - so a null slug used to be accepted and the caller
        happily wrote pages/recipes/None/index.html and reported success.
        """
        raw = row.get("slug")
        if not isinstance(raw, str):
            return None
        return raw.strip() or None

    def _field(row: dict, key: str) -> str:
        raw = row.get(key)
        return raw.strip() if isinstance(raw, str) else ""

    for key, value in (("episode_id", episode_id), ("recipe_id", recipe_id)):
        if not value:
            continue
        wanted = str(value).strip()
        hits = [r for r in rows if _field(r, key) == wanted]
        if len(hits) == 1:
            slug = _slug_of(hits[0])
            if slug:
                return slug
            print(f"  UNUSABLE: catalog row for {key}={value!r} has no valid slug - refusing")
            return None
        if len(hits) > 1:
            print(f"  AMBIGUOUS: {len(hits)} catalog rows share {key}={value!r} - refusing")
            return None

    # Title fallback, for legacy rows only. A row that CARRIES an identity and
    # whose identity differs from ours belongs to another episode - matching it
    # on title would write this episode's content over that episode's page.
    # Reproduced: a W37 repair wrote W37 content to W36's catalog path.
    matches: set[str] = set()
    for row in rows:
        if _clean_title(str(row.get("title", ""))).casefold() != title.casefold():
            continue
        conflict = False
        for key, value in (("episode_id", episode_id), ("recipe_id", recipe_id)):
            row_value = _field(row, key)
            if row_value and str(value or "").strip() and row_value != str(value).strip():
                conflict = True
                break
            # A row owned by SOME episode, when we do not know ours, is equally
            # unsafe to claim by title alone.
            if row_value and not str(value or "").strip():
                conflict = True
                break
        if conflict:
            print(
                f"  OWNED: catalog row {row.get('slug')!r} matches {title!r} by title but "
                f"belongs to another episode - refusing the title fallback"
            )
            continue
        slug = _slug_of(row)
        if slug:
            matches.add(slug)

    if len(matches) == 1:
        return matches.pop()
    if len(matches) > 1:
        print(f"  AMBIGUOUS: {title!r} matches {sorted(matches)} - refusing")
    return None


def fix_episode(
    episode_id: str, dry_run: bool = False, catalog: dict | None = None
) -> bool:
    """Re-render and re-upload a published episode's recipe page.

    Returns True if the page was fixed (or would be in dry-run mode).
    """
    # Never render test episodes (test-e2e-*, test-local-*, *-test) into live
    # recipe pages — they leak orphan, crawlable junk under /recipes/.
    if "test" in episode_id.lower():
        print(f"  SKIP {episode_id}: test episode (not a real recipe)")
        return False

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

    # Resolve the serving identity BEFORE anything writes. The W34 instruction
    # repair below calls storage.save_episode(), and it used to run first - so an
    # episode whose slug could not be confirmed got its recipe data mutated in
    # production and THEN reported "SKIP ... refusing", leaving the episode and
    # its published page out of sync. Refusing after a write is not refusing.
    #
    # The slug itself must come from the live catalog, never be re-derived:
    #   1. Seed recipes are served under a hand-chosen slug that differs from
    #      the title ("Dark Chocolate Chip Decadence" -> dark-chocolate-chip-muffins).
    #   2. #7106 changed _slugify, so W37's "Pao" (tilde) now renders
    #      brazilian-pao-de-queijo-bites while the live URL is the old
    #      brazilian-p-o-de-queijo-bites. Re-deriving would write a fresh orphan
    #      page nothing links to and leave the real URL serving stale HTML.
    # Published slugs are frozen; a re-render must never move one.
    slug = catalog_slug_for_title(title, catalog, episode_id=episode_id, recipe_id=ep.get("recipe_id"))
    if slug is None:
        print(f"  SKIP {episode_id}: no catalog row for {title!r} - refusing to guess a slug")
        return False

    # W34 is the only known stored episode with markdown-shaped instruction
    # entries.  Keep this repair explicitly scoped so a bulk page rebuild can
    # never silently rewrite another week's approved recipe data.
    if episode_id == W34_EPISODE_ID:
        original_instructions = recipe.get("instructions", [])
        repaired_instructions = normalize_recipe_instructions(original_instructions)
        if repaired_instructions != original_instructions:
            if dry_run:
                print(
                    f"  WOULD REPAIR {episode_id}: "
                    f"{len(original_instructions)} instructions -> "
                    f"{len(repaired_instructions)}"
                )
            else:
                recipe["instructions"] = repaired_instructions
                storage.save_episode(episode_id, ep)
                print(
                    f"  REPAIRED {episode_id}: "
                    f"{len(original_instructions)} instructions -> "
                    f"{len(repaired_instructions)}"
                )

    if dry_run:
        print(f"  WOULD FIX {episode_id}: /recipes/{slug} ({title})")
        return True

    # Re-render fresh from episode JSON (hero derived from the confirmed winner).
    # canonical_slug pins <link rel=canonical>/og:url to the URL actually serving
    # this page, instead of letting the renderer re-derive one from the title.
    page_html = render_episode_page(ep, canonical_slug=slug)

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
    parser.add_argument(
        "--all", action="store_true",
        help="Process every published episode (requires --full-rebuild unless dry-running)",
    )
    parser.add_argument(
        "--full-rebuild", action="store_true",
        help="Explicitly authorize a bulk rewrite of existing published pages",
    )
    args = parser.parse_args()

    if not args.episode and not args.all:
        parser.error("choose --episode or explicitly opt in with --all")
    if args.episode and args.all:
        parser.error("choose either --episode or --all, not both")
    if args.all and not args.dry_run and not args.full_rebuild:
        parser.error("--all requires --full-rebuild when it will write pages")

    print(f"{'DRY RUN: ' if args.dry_run else ''}Re-rendering published recipe pages...\n")

    # Published slugs come from the live catalog and are frozen (see
    # catalog_slug_for_title). If the catalog is unreachable we stop rather than
    # re-derive slugs from titles, which would write orphan pages.
    try:
        catalog = load_published_catalog()
    except CatalogUnavailableError as exc:
        print(f"ABORT: cannot read the live catalog, so serving slugs are unknown: {exc}")
        return 1
    print(f"Catalog: {len(catalog.get('recipes', []))} published rows\n")

    if args.episode:
        fixed = fix_episode(args.episode, dry_run=args.dry_run, catalog=catalog)
        total = 1 if fixed else 0
        if not fixed:
            # An EXPLICITLY requested episode that could not be repaired is a
            # failure, not a skip. Bulk mode legitimately skips unpublished and
            # test episodes; asking for one by name and getting nothing is the
            # caller being wrong about the world, and must not exit 0.
            print(f"\nFAILED: {args.episode} was requested explicitly and was not fixed.")
            return 1
    else:
        strict_lister = getattr(storage, "list_episodes_strict", None)
        lister = strict_lister if callable(strict_lister) else storage.list_episodes
        episodes = lister()
        print(f"Found {len(episodes)} episodes\n")
        total = 0
        for ep_summary in episodes:
            episode_id = ep_summary.get("episode_id", "")
            if fix_episode(episode_id, dry_run=args.dry_run, catalog=catalog):
                total += 1

    action = "would fix" if args.dry_run else "fixed"
    print(f"\nDone. {action} {total} recipe page(s).")


# Propagate the exit status. main() returns 1 when the catalog is unreachable,
# and that used to be discarded here - the CLI printed ABORT and exited 0, so a
# caller or CI step saw success while nothing had been re-rendered.
if __name__ == "__main__":
    sys.exit(main())
