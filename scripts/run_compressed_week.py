#!/usr/bin/env python3
"""Run all 7 pipeline stages (Mon–Sun) in sequence with configurable delay.

For testing the full week before trusting the daily cron. Always runs in
dry-run mode (no publishing, no git push).

Examples:
  PYTHONPATH=. uv run scripts/run_compressed_week.py \
    --concept "Lemon Ricotta Breakfast Muffins" --episode-id "2026-W10-test"

  PYTHONPATH=. uv run scripts/run_compressed_week.py \
    --concept "mini shepherds pies" --episode-id "2026-W10-test" --delay 30
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"

# Note: secrets (STABILITY_API_KEY etc.) are injected by 'doppler run --' in cron commands.
# When running manually, prefix with: doppler run -- python scripts/run_compressed_week.py ...


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

DEFAULT_STAGE_TIMEOUT_SECONDS = 300
STAGE_TIMEOUT_OVERRIDES_SECONDS = {
    # Monday does the heaviest orchestration work and can legitimately exceed 5m.
    "monday": 900,
}


def resolve_stage_timeout(stage: str) -> int:
    return STAGE_TIMEOUT_OVERRIDES_SECONDS.get(stage, DEFAULT_STAGE_TIMEOUT_SECONDS)


def run_stage(
    stage: str,
    episode_id: str,
    concept: str,
    dry_run: bool = False,
    dialogue_model: str | None = None,
    recipe_model: str | None = None,
) -> tuple[bool, str]:
    """Run a single stage via run_pipeline_stage.py and return (ok, output)."""
    timeout_seconds = resolve_stage_timeout(stage)
    local_env = {**os.environ, "PYTHONPATH": str(ROOT)}
    # Compressed-week runs are local test workflows; default to filesystem mode.
    local_env.setdefault("LOCAL_DEV", "true")
    # Pass model overrides via env vars so run_pipeline_stage.py picks them up
    if dialogue_model:
        local_env["DIALOGUE_MODEL"] = dialogue_model
    if recipe_model:
        local_env["RECIPE_MODEL"] = recipe_model
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "run_pipeline_stage.py"),
        "--stage", stage,
        "--episode", episode_id,
        "--concept", concept,
    ]
    if dry_run:
        cmd.append("--dry-run")
    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            env=local_env,
        )
        output = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        return result.returncode == 0, output.strip()
    except subprocess.TimeoutExpired as e:
        return False, f"timeout after {timeout_seconds}s: {(e.stdout or '')} {(e.stderr or '')}"
    except Exception as e:
        return False, str(e)


def _extract_cost_data(output: str) -> dict | None:
    """Extract COST_SUMMARY JSON line from stage output."""
    for line in output.splitlines():
        if line.startswith("COST_SUMMARY:"):
            try:
                return json.loads(line[len("COST_SUMMARY:"):])
            except json.JSONDecodeError:
                pass
    return None


def print_cost_summary(results: list[dict]) -> None:
    """Aggregate and print cost data from all stage outputs."""
    totals: dict[str, dict] = {}
    for r in results:
        # Cost data is in the stage output (stored in run loop, not in error)
        # We need to check the full output — pass it through
        cost = _extract_cost_data(r.get("_output", ""))
        if not cost:
            continue
        for model_key, data in cost.get("by_model", {}).items():
            if model_key not in totals:
                totals[model_key] = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "estimated_cost": 0.0}
            totals[model_key]["calls"] += data["calls"]
            totals[model_key]["tokens_in"] += data["tokens_in"]
            totals[model_key]["tokens_out"] += data["tokens_out"]
            totals[model_key]["estimated_cost"] += data["estimated_cost"]

    if not totals:
        print("\n  (No cost data collected — costs are only tracked for OpenAI/Anthropic/Google calls)\n")
        return

    grand_total = sum(m["estimated_cost"] for m in totals.values())
    grand_calls = sum(m["calls"] for m in totals.values())
    grand_in = sum(m["tokens_in"] for m in totals.values())
    grand_out = sum(m["tokens_out"] for m in totals.values())

    print("\n" + "=" * 60)
    print("  COST SUMMARY")
    print("=" * 60)
    for model_key, data in sorted(totals.items()):
        print(f"  {model_key:40s}  {data['calls']:3d} calls  "
              f"{data['tokens_in']:7,} in  {data['tokens_out']:7,} out  "
              f"${data['estimated_cost']:.4f}")
    print(f"  {'TOTAL':40s}  {grand_calls:3d} calls  "
          f"{grand_in:7,} in  {grand_out:7,} out  "
          f"${grand_total:.4f}")
    print("=" * 60 + "\n")


def load_episode(episode_id: str) -> dict:
    path = EPISODES_DIR / f"{episode_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {"stages": {}}


def print_summary(episode_id: str, concept: str, results: list[dict], elapsed: float) -> None:
    ep = load_episode(episode_id)
    stages_data = ep.get("stages", {})

    print("\n" + "=" * 60)
    print("  COMPRESSED WEEK SUMMARY")
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
        cost = _extract_cost_data(r.get("_output", ""))
        token_info = ""
        if cost and cost["total_calls"] > 0:
            total_tokens = cost["total_tokens_in"] + cost["total_tokens_out"]
            token_info = f"  [{total_tokens:,} tokens, ${cost['total_cost']:.4f}]"
        print(f"  {icon} {stage:10s} ({label}){token_info}")

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
    parser = argparse.ArgumentParser(description="Compressed full-week pipeline runner")
    parser.add_argument("--concept", required=True, help="Recipe concept, e.g. 'Lemon Ricotta Breakfast Muffins'")
    parser.add_argument("--episode-id", required=True, help="Episode ID, e.g. '2026-W10-test'")
    parser.add_argument("--delay", type=int, default=60, help="Seconds to wait between stages (default: 60)")
    parser.add_argument("--stages", default=",".join(STAGES), help="Comma-separated stages to run (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Pass --dry-run to each stage (no publishing, no Stability AI)")
    parser.add_argument("--dialogue-model", default=None, help="Override dialogue model (e.g. 'anthropic/claude-haiku-4-5-20251001')")
    parser.add_argument("--recipe-model", default=None, help="Override recipe generation model (e.g. 'openai/gpt-5-mini')")
    args = parser.parse_args()

    stages_to_run = [s.strip() for s in args.stages.split(",") if s.strip()]
    invalid = [s for s in stages_to_run if s not in STAGES]
    if invalid:
        print(f"Unknown stages: {invalid}. Valid: {STAGES}")
        sys.exit(1)

    EPISODES_DIR.mkdir(parents=True, exist_ok=True)

    mode_label = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{'='*60}")
    print(f"  {mode_label}Compressed Week — {args.episode_id}")
    print(f"  Concept:  {args.concept}")
    print(f"  Stages:   {' → '.join(stages_to_run)}")
    print(f"  Delay:    {args.delay}s between stages")
    if args.dialogue_model:
        print(f"  Dialogue: {args.dialogue_model}")
    if args.recipe_model:
        print(f"  Recipe:   {args.recipe_model}")
    print(f"  Started:  {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}\n")

    results: list[dict] = []
    start_time = time.monotonic()

    for i, stage in enumerate(stages_to_run):
        label = STAGE_LABELS.get(stage, stage)
        timeout_seconds = resolve_stage_timeout(stage)
        print(f"[{i+1}/{len(stages_to_run)}] Running {stage} ({label}) [timeout={timeout_seconds}s]...")

        ok, output = run_stage(
            stage, args.episode_id, args.concept,
            dry_run=args.dry_run,
            dialogue_model=args.dialogue_model,
            recipe_model=args.recipe_model,
        )
        results.append({"stage": stage, "ok": ok, "error": output if not ok else "", "_output": output})

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

    # Aggregate and print cost summary from all stages
    print_cost_summary(results)

    # Exit non-zero if any stage failed
    if any(not r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
