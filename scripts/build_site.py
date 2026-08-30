#!/usr/bin/env python3
"""Build Muffin Pan Recipes into static deployment artifacts.

Examples:

    # Safe preview artifact; does not touch src/ or Blob pages.
    uv run python -m scripts.build_site --preview

    # Routine weekly build. Existing page changes fail before any write.
    uv run python -m scripts.build_site

    # Explicit design/content migration. Replaces existing generated pages.
    uv run python -m scripts.build_site --full-rebuild
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.publishing.site_builder import (
    ExistingPageMutationError,
    SiteBuildError,
    StaticSiteBuilder,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--full-rebuild",
        "--rebuild",
        action="store_true",
        help="Allow replacement of existing pages and rebuild every seed/page.",
    )
    mode.add_argument(
        "--incremental",
        action="store_true",
        help="Render missing pages only; this is the default.",
    )
    parser.add_argument(
        "--episode",
        action="append",
        dest="episode_ids",
        help="Build one published episode (repeat for more than one).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Static artifact directory relative to the repository root.",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="Write to .scratch/site-preview instead of the deployable src/ tree.",
    )
    parser.add_argument(
        "--storage-prefix",
        default="",
        help="Isolated storage prefix for preview/test source data (for example test/).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the plan and report writes without changing files.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.preview and args.output_dir is not None:
        print("ERROR: choose either --preview or --output-dir, not both", file=sys.stderr)
        return 2

    output_dir = args.output_dir or (Path(".scratch") / "site-preview" if args.preview else None)
    builder = StaticSiteBuilder(
        output_dir=output_dir,
        full_rebuild=args.full_rebuild,
        storage_prefix=args.storage_prefix,
    )
    try:
        result = builder.build(args.episode_ids, dry_run=args.dry_run)
    except ExistingPageMutationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except SiteBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    action = "would write" if args.dry_run else "wrote"
    print(
        f"Static build complete ({'full' if result.full_rebuild else 'incremental'}): "
        f"{action} {len(result.written)} file(s), "
        f"{len(result.unchanged)} unchanged in {result.output_dir}"
    )
    for path in result.written:
        print(f"  {action}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
