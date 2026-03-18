#!/usr/bin/env python3
"""Judge a week's dialogue with growing context across days.

The judge reads each day's conversation sequentially, accumulating context.
By Friday, it remembers Monday's discussion. This catches:
- Cross-day inconsistencies (wrong ingredients, forgotten decisions)
- Character breaks (someone acting wildly out of character)
- Pacing issues (resolving too quickly, no tension)
- Hallucinations (mentioning things that don't exist in the concept)

Usage:
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run scripts/judge_conversation.py \
    --file data/simulations/sim-XXXXX-full-week.json \
    --model anthropic/claude-opus-4-6

  # Judge all recent runs:
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run scripts/judge_conversation.py \
    --file data/simulations/sim-XXXXX-full-week.json \
    --model anthropic/claude-sonnet-4-6

  # Compare judge models on the same conversation:
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run scripts/judge_conversation.py \
    --file data/simulations/sim-XXXXX-full-week.json \
    --models "anthropic/claude-opus-4-6,anthropic/claude-sonnet-4-6"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.config import config
from backend.utils.model_router import generate_judge_response

DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

JUDGE_SYSTEM_PROMPT = """You are a senior editorial judge reviewing AI-generated dialogue for a food content website called Muffin Pan Recipes.

The site publishes weekly episodes where 5 characters (Margaret, Steph, Julian, Marcus, Devon) collaborate on a muffin-tin recipe concept. Each day of the week is a different production stage.

Your job is to evaluate each day's conversation for quality issues that would embarrass the site if published. You are accumulating context across the week - you remember everything from previous days.

CHARACTER QUICK REFERENCE:
- Margaret Chen: Executive producer. Blunt, short sentences, zero patience for fluff. Standards enforcer.
- Stephanie "Steph" Whitmore: Host/producer. Warm, diplomatic, holds the team together. NOT a nervous intern.
- Julian Voss: Photographer/videographer. Visual thinker, theatrical, cares about light and composition.
- Marcus Reid: Copywriter. Literary, verbose, metaphor-heavy. Sometimes too clever for his own good.
- Devon Park: Web developer. Efficient, understated, technical. Speaks only when needed.

PRODUCTION STAGES:
- Monday: Brainstorm (pick the concept)
- Tuesday: Recipe development
- Wednesday: Photography/video
- Thursday: Copywriting
- Friday: Final review
- Saturday: Deployment/staging
- Sunday: Publish

EVALUATE EACH DAY FOR:
1. HALLUCINATIONS: Wrong ingredients, techniques, or details that don't match the concept
2. CHARACTER BREAKS: Someone acting wildly out of character (Margaret being flowery, Devon giving speeches)
3. CONTINUITY: References to previous days should be accurate
4. PACING: Did the conversation have real tension/disagreement, or did everyone agree instantly?
5. BOOKENDS: Did the first message include a greeting/arrival? Did the last message include a sign-off?
6. NATURALNESS: Does this sound like real coworkers or like AI characters?

For each day, respond in EXACTLY this format:
VERDICT: <PASS | SOFT FAIL | HARD FAIL>
QUALITY_SCORE: <1-10>
QA_SCORE: <0-100>
REASON: <1-2 sentences>
PROBLEM_LINES: <quote specific lines, or "None">

At the end, give an OVERALL verdict and whether this week should be published or regenerated."""

def _parse_day_verdict(verdict_raw: str) -> dict[str, str | int | None]:
    verdict = None
    quality = None
    qa = None

    upper = verdict_raw.upper()
    if "HARD FAIL" in upper:
        verdict = "HARD FAIL"
    elif "SOFT FAIL" in upper:
        verdict = "SOFT FAIL"
    elif "PASS" in upper:
        verdict = "PASS"

    def _extract_int(label: str, max_val: int) -> int | None:
        import re

        pattern = re.compile(rf"{label}\\s*[:\\-]?\\s*(\\d+)", re.IGNORECASE)
        match = pattern.search(verdict_raw)
        if not match:
            return None
        value = int(match.group(1))
        if value < 0:
            return None
        if value > max_val:
            return max_val
        return value

    quality = _extract_int("QUALITY_SCORE", 10)
    qa = _extract_int("QA_SCORE", 100)

    return {
        "verdict": verdict,
        "quality_score": quality,
        "qa_score": qa,
        "raw": verdict_raw,
    }


def load_conversation(filepath: str) -> dict:
    with open(filepath) as f:
        return json.load(f)


def group_by_day(messages: list[dict]) -> dict[str, list[dict]]:
    by_day: dict[str, list[dict]] = {}
    for m in messages:
        day = m["day"]
        by_day.setdefault(day, []).append(m)
    return by_day


def format_day_transcript(day_messages: list[dict]) -> str:
    lines = []
    for m in day_messages:
        name = m["character"].split()[0]
        lines.append(f"{name}: {m['message']}")
    return "\n".join(lines)


def judge_week(filepath: str, model: str) -> dict:
    """Judge a full week with growing context."""
    data = load_conversation(filepath)
    concept = data.get("concept", "Unknown")
    gen_model = data.get("default_model", "Unknown")
    by_day = group_by_day(data["messages"])

    accumulated_context = []
    day_verdicts = {}

    for day in DAY_ORDER:
        if day not in by_day:
            continue

        transcript = format_day_transcript(by_day[day])

        # Build the growing-context prompt
        context_section = ""
        if accumulated_context:
            context_section = (
                "PREVIOUS DAYS (you remember all of this):\n"
                + "\n\n".join(accumulated_context)
                + "\n\n---\n\n"
            )

        prompt = (
            f"Recipe concept this week: {concept}\n"
            f"Generated by: {gen_model}\n\n"
            f"{context_section}"
            f"TODAY IS {day.upper()}:\n"
            f"{transcript}\n\n"
            f"Judge this day's conversation. Remember everything from previous days."
        )

        verdict_raw = generate_judge_response(
            prompt=prompt,
            system_prompt=JUDGE_SYSTEM_PROMPT,
            model=model,
            temperature=0.2,
        )

        day_verdicts[day] = _parse_day_verdict(verdict_raw)
        accumulated_context.append(f"=== {day.upper()} ===\n{transcript}")

        # Print as we go
        print(f"\n{'='*60}")
        print(f"  {day.upper()} — judged by {model}")
        print(f"{'='*60}")
        print(verdict_raw)

    # Final overall verdict with full week in context
    full_transcript = "\n\n".join(accumulated_context)
    overall_prompt = (
        f"Recipe concept: {concept}\n"
        f"Generated by: {gen_model}\n\n"
        f"FULL WEEK TRANSCRIPT:\n{full_transcript}\n\n"
        "Give your FINAL OVERALL VERDICT for this week:\n"
        "1. PUBLISH / REGENERATE / PUBLISH WITH NOTES\n"
        "2. Overall quality score (1-10)\n"
        "3. Top 3 issues (if any)\n"
        "4. Top 3 strengths\n"
        "5. Which day was weakest and why?"
    )

    overall_raw = generate_judge_response(
        prompt=overall_prompt,
        system_prompt=JUDGE_SYSTEM_PROMPT,
        model=model,
        temperature=0.2,
    )

    print(f"\n{'='*60}")
    print(f"  OVERALL VERDICT — {model}")
    print(f"{'='*60}")
    print(overall_raw)

    return {
        "source_file": filepath,
        "concept": concept,
        "generation_model": gen_model,
        "judge_model": model,
        "judged_at": datetime.now(timezone.utc).isoformat(),
        "day_verdicts": day_verdicts,
        "overall_verdict": overall_raw,
        "qa_score": data.get("qa", {}).get("score"),
    }


def main():
    parser = argparse.ArgumentParser(description="Judge a week's dialogue with growing context")
    parser.add_argument("--file", required=True, help="Path to simulation JSON file")
    parser.add_argument("--model", default=config.judge_model, help=f"Judge model (default: {config.judge_model})")
    parser.add_argument("--models", default=None, help="Comma-separated models for comparison judging")
    parser.add_argument("--save", action="store_true", help="Save verdicts to JSON")
    args = parser.parse_args()

    models = args.models.split(",") if args.models else [args.model]

    results = []
    for model in models:
        model = model.strip()
        print(f"\n{'#'*60}")
        print(f"  JUDGING WITH: {model}")
        print(f"{'#'*60}")

        result = judge_week(args.file, model)
        results.append(result)

    if args.save:
        out_dir = Path("data/judgments")
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(args.file).stem
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out_path = out_dir / f"judge-{ts}-{stem}.json"
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nSaved: {out_path}")

    return results


if __name__ == "__main__":
    main()
