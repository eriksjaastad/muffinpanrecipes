"""One-time cleanup script for accumulated image variant directories.

For each {recipe_id}/ directory under src/assets/images/:
- If {recipe_id}.png (the winner) exists alongside it, trash the directory
- If no winner exists, skip (manual review needed)

Usage:
    # Dry run (default) — shows what would be trashed
    uv run scripts/cleanup_image_backlog.py

    # Actually trash the directories
    uv run scripts/cleanup_image_backlog.py --execute
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

IMAGES_DIR = Path(__file__).resolve().parents[1] / "src" / "assets" / "images"


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean up accumulated image variant directories")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually trash directories. Without this flag, runs in dry-run mode.",
    )
    args = parser.parse_args()

    if not IMAGES_DIR.exists():
        print(f"Images directory not found: {IMAGES_DIR}")
        sys.exit(1)

    candidates: list[tuple[Path, Path]] = []  # (variant_dir, winner_png)
    orphans: list[Path] = []  # variant dirs without a winner

    for item in sorted(IMAGES_DIR.iterdir()):
        if not item.is_dir():
            continue
        # Skip hidden directories
        if item.name.startswith("."):
            continue

        winner = IMAGES_DIR / f"{item.name}.png"
        if winner.exists():
            candidates.append((item, winner))
        else:
            orphans.append(item)

    # Calculate sizes
    total_size = 0
    for variant_dir, _ in candidates:
        for f in variant_dir.rglob("*"):
            if f.is_file():
                total_size += f.stat().st_size

    print(f"Images directory: {IMAGES_DIR}")
    print(f"Found {len(candidates)} directories with winners (safe to clean)")
    print(f"Found {len(orphans)} directories WITHOUT winners (skipping)")
    print(f"Estimated space to reclaim: {total_size / 1024 / 1024:.1f} MB")
    print()

    if orphans:
        print("Orphan directories (no winner .png — need manual review):")
        for d in orphans:
            print(f"  {d.name}/")
        print()

    if not candidates:
        print("Nothing to clean up.")
        return

    if not args.execute:
        print("DRY RUN — directories that would be trashed:")
        for variant_dir, winner in candidates:
            file_count = sum(1 for f in variant_dir.rglob("*") if f.is_file())
            print(f"  {variant_dir.name}/ ({file_count} files) — winner: {winner.name}")
        print()
        print("Run with --execute to actually trash these directories.")
        return

    # Execute cleanup
    from send2trash import send2trash

    trashed = 0
    errors = 0
    for variant_dir, _ in candidates:
        try:
            send2trash(str(variant_dir))
            print(f"  Trashed: {variant_dir.name}/")
            trashed += 1
        except Exception as e:
            print(f"  ERROR trashing {variant_dir.name}/: {e}")
            errors += 1

    print()
    print(f"Done: {trashed} directories trashed, {errors} errors.")


if __name__ == "__main__":
    main()
