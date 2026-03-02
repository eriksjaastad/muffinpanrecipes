#!/usr/bin/env python3
"""
Cleanup script for test episodes and their associated images.
Identifies episodes with dry_run=true or "-test" in the ID and handles deletion.

Usage:
    uv run scripts/cleanup_test_episodes.py             # Dry run (shows what would be deleted)
    uv run scripts/cleanup_test_episodes.py --confirm   # Actually delete
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from send2trash import send2trash


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cleanup test episodes and images.",
        epilog="Without --confirm this is a dry run — nothing is deleted.",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually perform deletion (default is dry run)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    episodes_dir = repo_root / "data" / "episodes"
    images_base = repo_root / "src" / "assets" / "images"

    if not episodes_dir.exists():
        print(f"Error: Episodes directory not found at {episodes_dir}")
        return

    test_episodes: list[tuple[Path, dict]] = []
    for path in sorted(episodes_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())

            # NEVER touch published episodes
            if data.get("status") == "published":
                continue

            is_test = data.get("dry_run") is True or "-test" in path.stem
            if is_test:
                test_episodes.append((path, data))
        except Exception as e:
            print(f"Error reading {path.name}: {e}")

    if not test_episodes:
        print("No test episodes found.")
        return

    mode_label = "DELETING" if args.confirm else "DRY RUN"
    print(f"Found {len(test_episodes)} test episodes. [{mode_label}]")
    print("-" * 60)

    for path, data in test_episodes:
        recipe_id = data.get("recipe_id")
        to_delete: list[Path] = [path]

        if recipe_id:
            image_dir = images_base / recipe_id
            featured_image = images_base / f"{recipe_id}.png"
            if image_dir.exists():
                to_delete.append(image_dir)
            if featured_image.exists():
                to_delete.append(featured_image)

        print(f"Episode: {path.name}  (recipe_id: {recipe_id or 'none'})")
        for item in to_delete:
            rel = item.relative_to(repo_root)
            if args.confirm:
                try:
                    send2trash(str(item))
                    print(f"  [TRASHED] {rel}")
                except Exception as e:
                    print(f"  [ERROR]   {rel}: {e}")
            else:
                print(f"  [PLAN]    would delete: {rel}")
        print()

    if not args.confirm:
        print("-" * 60)
        print("DRY RUN COMPLETE. Run with --confirm to actually delete files.")
    else:
        print("CLEANUP COMPLETE.")


if __name__ == "__main__":
    main()
