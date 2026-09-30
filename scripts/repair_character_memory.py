#!/usr/bin/env python3
"""Repair/backfill durable per-character memory for already-published episodes (#6968).

Rebuilds one or more episodes' per-character memory entries from their
stored, ACCEPTED dialogue (episode["stages"][<day>]["dialogue"]) using the
exact same summarization and per-week-blob write logic as the production
Sunday writer (backend.admin.cron_routes._generate_episode_memories) — this
script calls that function directly rather than re-implementing it, so the
two can never drift apart. Each write is a single, complete blob for the
target week (character_memory/<slug>/<week>.json) with no read or merge, so
replaying the same week is an idempotent overwrite of only that week's own
blob and can never touch or lose any other week's history.

It never re-fires any cron stage (no /api/cron/sunday call, no recipe/QA/
publish logic runs) and never touches published pages or the catalog: it
only reads the episode JSON through the storage layer (the same layer the
app uses) and writes durable character memory through that same layer.

Refuses to touch an episode that has no `published_at` — this command
repairs memory for weeks that already published, it does not create new
ones or backfill an in-progress week.

Defaults to a DRY RUN: prints what each character's outcome would be and
makes no storage writes. Pass --apply to write for real.

Examples:
    # Dry run against two already-published weeks
    uv run python scripts/repair_character_memory.py 2026-W37 2026-W38

    # Actually write, one week
    uv run python scripts/repair_character_memory.py 2026-W37 --apply

CAUTION: this writes through whichever storage backend the environment is
configured for (BLOB_READ_WRITE_TOKEN / VERCEL_ENV -> Vercel Blob, else the
local filesystem). --apply against a production-configured environment
performs a real production write. Never run --apply without deliberately
confirming which storage backend is active.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.admin.cron_routes import _generate_episode_memories  # noqa: E402
from backend.storage import storage  # noqa: E402
from backend.utils.logging import get_logger  # noqa: E402

logger = get_logger(__name__)


def repair_episode(episode_id: str, *, dry_run: bool) -> dict[str, list[str]] | None:
    """Rebuild one already-published episode's per-character memory.

    Returns the {"saved", "absent", "failed"} outcome dict from
    _generate_episode_memories, or None if the episode does not exist or
    has not been published — published_at is the same completeness signal
    Sunday's own idempotency guard reads (RUNBOOK Incident 2).
    """
    ep = storage.load_episode(episode_id)
    if ep is None:
        logger.error(f"{episode_id}: episode not found")
        return None
    if not ep.get("published_at"):
        logger.error(
            f"{episode_id}: not published (published_at unset) — refusing to "
            "backfill memory for an incomplete/unpublished week"
        )
        return None

    concept = (
        ep.get("concept")
        or ep.get("stages", {}).get("monday", {}).get("recipe_data", {}).get("title")
        or "unknown"
    )
    return _generate_episode_memories(ep, concept, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "episode_ids", nargs="+", help="Already-published episode IDs to repair, e.g. 2026-W37"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually write to durable character-memory storage. Default is a "
            "dry run that prints what would be written and makes no storage writes."
        ),
    )
    args = parser.parse_args(argv)

    dry_run = not args.apply
    if dry_run:
        print("DRY RUN - no storage writes will happen. Pass --apply to write for real.\n")

    exit_code = 0
    for episode_id in args.episode_ids:
        outcome = repair_episode(episode_id, dry_run=dry_run)
        print(f"{episode_id}:")
        if outcome is None:
            print("  SKIPPED (see error above)")
            exit_code = 1
            continue

        verb = "would save" if dry_run else "saved"
        if outcome["saved"]:
            print(f"  {verb}: {', '.join(sorted(outcome['saved']))}")
        if outcome["absent"]:
            print(f"  absent (no dialogue this week): {', '.join(sorted(outcome['absent']))}")
        if outcome["failed"]:
            print(f"  FAILED: {', '.join(sorted(outcome['failed']))}")
            exit_code = 1
        if not any(outcome.values()):
            print("  nothing to do")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
