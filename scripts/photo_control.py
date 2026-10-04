"""Inspect and reconcile a week's photo-approval control (#7936).

Operator recovery for RUNBOOK "Photo approval". Read-only unless --execute.

    doppler run -- uv run python scripts/photo_control.py show 2026-W41
    doppler run -- uv run python scripts/photo_control.py reconcile 2026-W41            # dry run
    doppler run -- uv run python scripts/photo_control.py reconcile 2026-W41 --execute
    ... add --test for the test namespace.

reconcile does exactly one of:
  * the episode (read freshness-verified) is published by the control's
    claim -> record the control as published;
  * the control is "claimed" and the episode is NOT published -> release
    the claim back to "approved" (compare-and-swap: a Sunday run that still
    holds the claim then loses it before publishing and publishes nothing),
    so a normal Sunday run can claim it again;
  * the control is "publishing"/"published" and the episode does not show
    it -> refuse: these are never released. Run Sunday again; it completes
    the same publication from the control's checkpoint with no paid work;
  * anything else -> refuse and explain. It never deletes, never edits
    the episode and never publishes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    from backend.storage import storage
    from backend.utils import photo_review

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["show", "reconcile"])
    parser.add_argument("episode_id")
    parser.add_argument("--test", action="store_true", help="use the test/ namespace")
    parser.add_argument("--execute", action="store_true", help="write the reconciliation")
    args = parser.parse_args(argv)

    with storage.prefix_scope("test/" if args.test else ""):
        ep = storage.load_episode_verified(args.episode_id)
        if ep is None:
            print(f"episode {args.episode_id} not found")
            return 1
        view = photo_review.read_view(storage, args.episode_id, ep)
        summary = {
            "episode_id": args.episode_id,
            "namespace": "test" if args.test else "production",
            "control_version": view.version,
            "state": view.state,
            "image_set_id": view.image_set_id,
            "selected": (view.selected or {}).get("path"),
            "claim_id": (view.claim or {}).get("claim_id"),
            "version_age_seconds": photo_review.claim_age_seconds(view) if view.raw else None,
            "has_publication_checkpoint": view.publication is not None,
            "episode_published_at": ep.get("published_at"),
            "episode_claim_id": (ep.get("photo_approval") or {}).get("claim_id")
            if isinstance(ep.get("photo_approval"), dict) else None,
        }
        print(json.dumps(summary, indent=2))
        if args.command == "show":
            return 0

        if view.state not in (photo_review.CLAIMED, photo_review.PUBLISHING, photo_review.PUBLISHED):
            print(f"nothing to reconcile: control is {view.state}")
            return 0
        if view.state == photo_review.PUBLISHED and summary["episode_claim_id"] == summary["claim_id"] \
                and ep.get("published_at"):
            print("nothing to reconcile: control and episode agree the week is published")
            return 0
        if ep.get("published_at"):
            if summary["episode_claim_id"] != summary["claim_id"]:
                print("REFUSED: the episode is published but not by this claim. Investigate by hand.")
                return 2
            if not args.execute:
                print("dry run: would record the control as published")
                return 0
            new = photo_review.reconcile_published(storage, args.episode_id, ep)
            print(f"recorded published (control v{new.version})" if new else "nothing changed")
            return 0
        if view.state != photo_review.CLAIMED:
            print(
                f"REFUSED: control is {view.state} and is never released. Run Sunday again "
                "(RUNBOOK photo approval): it completes this same publication from the "
                "checkpoint with no paid work."
            )
            return 2
        try:
            if not args.execute:
                print(
                    "dry run: would release the claim back to approved. A Sunday run that still "
                    "holds it would lose it before publishing and publish nothing."
                )
                return 0
            new = photo_review.operator_release(storage, args.episode_id, ep)
        except photo_review.PhotoReviewError as exc:
            print(f"REFUSED: {exc}")
            return 2
        print(f"released to approved (control v{new.version}); run Sunday again to publish")
        return 0


if __name__ == "__main__":
    sys.exit(main())
