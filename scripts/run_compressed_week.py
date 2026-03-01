#!/usr/bin/env python3
"""Run all 7 pipeline stages (Mon–Sun) in sequence with configurable delay.

For testing the full week before trusting the daily cron. Always runs in
dry-run mode (no publishing, no git push).

Examples:
  PYTHONPATH=. .venv/bin/python scripts/run_compressed_week.py \
    --concept "Lemon Ricotta Breakfast Muffins" --episode-id "2026-W10-test"

  PYTHONPATH=. .venv/bin/python scripts/run_compressed_week.py \
    --concept "mini shepherds pies" --episode-id "2026-W10-test" --delay 30
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from backend.utils.discord import notify_pipeline_failure

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"

STAGES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

STAGE_LABELS = {
    "monday": "Brainstorm",
    "tuesday": "Recipe Development",
    "wednesday": "Photography",
    "thursday": "Copywriting",
    "friday": "Final Review",
    "saturday": "Deployment",
    "sunday": "Publish",
}


def run_stage(stage: str, episode_id: str, concept: str) -> tuple[bool, str]:
    """Run a single stage via run_pipeline_stage.py and return (ok, output)."""
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_pipeline_stage.py"),
        "--stage", stage,
        "--episode", episode_id,
        "--concept", concept,
        "--dry-run",  # always dry-run in compressed mode
    ]
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=300,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired as e:
        return False, f"timeout after 300s: {(e.stdout or '')} {(e.stderr or '')}"
    except Exception as e:
        return False, str(e)


def load_episode(episode_id: str) -> dict:
    path = EPISODES_DIR / f"{episode_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"stages": {}}


def print_summary(episode_id: str, concept: str, results: list[dict], elapsed: float) -> None:
    ep = load_episode(episode_id)
    stages_data = ep.get("stages", {})

    print("\n" + "=" * 60)
    print(f"  COMPRESSED WEEK SUMMARY")
    print(f"  Episode:   {episode_id}")
    print(f"  Concept:   {concept}")
    print(f"  Elapsed:   {elapsed:.1f}s")
    print(f"  Episode:   {EPISODES_DIR / episode_id}.json")
    print("=" * 60)

    for r in results:
        stage = r["stage"]
        ok = r["ok"]
        icon = "✅" if ok else "❌"
        label = STAGE_LABELS.get(stage, stage)
        print(f"  {icon} {stage:10s} ({label})")

        stage_data = stages_data.get(stage, {})
        if ok:
            if "recipe_data" in stage_data:
                title = stage_data["recipe_data"].get("title", "—")
                ing_count = len(stage_data["recipe_data"].get("ingredients", []))
                print(f"           recipe: {title} ({ing_count} ingredients)")
            if "photography_data" in stage_data or "image_paths" in stage_data:
                image_paths = stage_data.get("image_paths") or []
                shots = stage_data.get("photography_data")
                if image_paths:
                    print(f"           photos: {len(image_paths)} image(s)")
                    for p in image_paths:
                        print(f"             - {p}")
                elif isinstance(shots, list):
                    print(f"           photos: {len(shots)} shot(s) (paths not recorded)")
            if "copy_text" in stage_data:
                body = (stage_data["copy_text"].get("body") or "")[:80]
                print(f"           copy:   {body}...")
            if "review_data" in stage_data:
                approved = stage_data.get("approved", "?")
                print(f"           review: approved={approved}")
            if "dialogue" in stage_data:
                print(f"           dialogue: {len(stage_data['dialogue'])} line(s)")
        else:
            err = r.get("error", "")[:120]
            print(f"           error: {err}")

    errors = [r for r in results if not r["ok"]]
    total = len(results)
    passed = total - len(errors)
    print(f"\n  {passed}/{total} stages complete", end="")
    if errors:
        print(f"  ({len(errors)} failed: {', '.join(r['stage'] for r in errors)})")
    else:
        print("  — all stages passed")
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compressed full-week dry run")
    parser.add_argument("--concept", required=True, help="Recipe concept, e.g. 'Lemon Ricotta Breakfast Muffins'")
    parser.add_argument("--episode-id", required=True, help="Episode ID, e.g. '2026-W10-test'")
    parser.add_argument("--delay", type=int, default=60, help="Seconds to wait between stages (default: 60)")
    parser.add_argument("--stages", default=",".join(STAGES), help="Comma-separated stages to run (default: all)")
    args = parser.parse_args()

    stages_to_run = [s.strip() for s in args.stages.split(",") if s.strip()]
    invalid = [s for s in stages_to_run if s not in STAGES]
    if invalid:
        print(f"Unknown stages: {invalid}. Valid: {STAGES}")
        sys.exit(1)

    EPISODES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  [DRY RUN] Compressed Week — {args.episode_id}")
    print(f"  Concept:  {args.concept}")
    print(f"  Stages:   {' → '.join(stages_to_run)}")
    print(f"  Delay:    {args.delay}s between stages")
    print(f"  Started:  {datetime.now().isoformat()}")
    print(f"{'='*60}\n")

    results: list[dict] = []
    start_time = time.monotonic()

    for i, stage in enumerate(stages_to_run):
        label = STAGE_LABELS.get(stage, stage)
        print(f"[{i+1}/{len(stages_to_run)}] Running {stage} ({label})...")

        ok, output = run_stage(stage, args.episode_id, args.concept)
        results.append({"stage": stage, "ok": ok, "error": output if not ok else ""})

        for line in output.splitlines():
            print(f"    {line}")

        if not ok:
            print(f"  ⚠️  Stage '{stage}' FAILED — continuing to next stage")
        else:
            print(f"  ✅ Stage '{stage}' complete")

        # Wait between stages (skip after last stage)
        if i < len(stages_to_run) - 1:
            print(f"  ⏱  Waiting {args.delay}s before next stage (Stability AI cooldown)...")
            time.sleep(args.delay)

    elapsed = time.monotonic() - start_time
    print_summary(args.episode_id, args.concept, results, elapsed)

    # Exit non-zero if any stage failed
    if any(not r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
