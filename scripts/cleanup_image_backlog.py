"""One-time cleanup script for accumulated image variant directories.

For each {recipe_id}/ directory under src/assets/images/:
- If it holds a protected image, skip it (#7936). Protected: any photo a
  human approved (every local photo-control version), a published or
  pinned hero (episode photo_approval), a legacy confirmed/overridden
  winner, and every image path of a published episode. Since #7936 the
  published hero lives INSIDE the round directory, so the old rule below
  would trash it.
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
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "src" / "assets" / "images"
EPISODES_DIR = ROOT / "data" / "episodes"
PHOTO_CONTROL_DIR = ROOT / "data" / "photo_control"


def _image_rel(path: str) -> str:
    return str(path).removeprefix("src/").removeprefix("assets/").removeprefix("images/")


def protected_image_paths() -> set[str]:
    """Image paths (relative to IMAGES_DIR) that must never be trashed.

    Raises on an unreadable record: guessing would risk the published hero.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.utils.episode_integrity import episode_is_published
    from backend.utils.photo_review import protected_image_paths as episode_protected

    protected: set[str] = set()
    for path in sorted(EPISODES_DIR.glob("*.json")) if EPISODES_DIR.exists() else []:
        ep = json.loads(path.read_text())
        if not isinstance(ep, dict):
            continue
        protected.update(_image_rel(p) for p in episode_protected(None, ep))
        # published_at OR a complete Sunday (the shared predicate): a legacy
        # published week keeps every image path even without a confirmed_winner.
        if episode_is_published(ep):
            wed = (ep.get("stages") or {}).get("wednesday") or {}
            for p in list(ep.get("image_paths") or []) + list(wed.get("image_paths") or []):
                if p:
                    protected.add(_image_rel(p))
    for path in sorted(PHOTO_CONTROL_DIR.rglob("v*.json")) if PHOTO_CONTROL_DIR.exists() else []:
        ctrl = json.loads(path.read_text())
        selected = ((ctrl.get("decision") or {}).get("selected") or {}) if isinstance(ctrl, dict) else {}
        if isinstance(selected, dict) and selected.get("path"):
            protected.add(_image_rel(selected["path"]))
    return protected


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
    kept: list[Path] = []  # variant dirs holding a protected image
    protected = protected_image_paths()
    protected_dirs = {p.split("/", 1)[0] for p in protected if "/" in p}

    for item in sorted(IMAGES_DIR.iterdir()):
        if not item.is_dir():
            continue
        # Skip hidden directories
        if item.name.startswith("."):
            continue

        if item.name in protected_dirs:
            kept.append(item)
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
    print(f"Found {len(kept)} directories holding an approved/published image (never trashed)")
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
