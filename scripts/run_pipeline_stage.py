#!/usr/bin/env python3
"""Trigger a single pipeline stage for an episode (compressed or production)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from backend.utils.discord import notify_pipeline_failure, notify_recipe_ready

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"


STAGE_TO_ROLE = {
    "monday": "brainstorm",
    "tuesday": "recipe_development",
    "wednesday": "photography",
    "thursday": "copywriting",
    "friday": "final_review",
    "saturday": "deployment",
    "sunday": "publish",
}


def load_episode(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {
        "episode_id": None,
        "created_at": datetime.now().isoformat(),
        "stages": {},
        "events": [],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=list(STAGE_TO_ROLE.keys()))
    parser.add_argument("--episode", required=True, help="e.g., 2026-W09")
    parser.add_argument("--concept", default="Weekly Muffin Pan Recipe")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--fail", action="store_true", help="Test failure path")
    args = parser.parse_args()

    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    ep_path = EPISODES_DIR / f"{args.episode}.json"
    ep = load_episode(ep_path)
    ep["episode_id"] = args.episode

    try:
        if args.fail:
            raise RuntimeError("forced failure for alert path testing")

        ep["stages"][args.stage] = {
            "stage": STAGE_TO_ROLE[args.stage],
            "completed_at": datetime.now().isoformat(),
            "concept": args.concept,
            "status": "complete",
        }
        ep["events"].append(f"{args.stage}: complete")
        ep_path.write_text(json.dumps(ep, indent=2))

        if args.notify:
            notify_recipe_ready(
                recipe_title=f"{args.concept} [{args.episode}]",
                recipe_id=f"{args.episode}-{args.stage}",
                description_preview=f"Stage complete: {STAGE_TO_ROLE[args.stage]}",
                ingredient_count=0,
            )

        print(f"stage complete: {args.stage} -> {STAGE_TO_ROLE[args.stage]}")
        print(f"episode file: {ep_path}")
    except Exception as e:
        notify_pipeline_failure(
            recipe_id=f"{args.episode}-{args.stage}",
            concept=args.concept,
            stage=args.stage,
            error_message=str(e),
        )
        raise


if __name__ == "__main__":
    main()
