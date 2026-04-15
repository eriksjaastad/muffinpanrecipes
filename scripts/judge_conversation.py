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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

For each day, respond in EXACTLY this format (every field on its own line, no markdown bold, no extra commentary above the block):
VERDICT: <PASS | SOFT FAIL | HARD FAIL>
QUALITY_SCORE: <integer 1-10, where 1 = unpublishable, 5 = publishable but rough, 10 = best in class>
QA_SCORE: <integer 0-100, structural quality, matches the QA rubric (0 = hard fail, 100 = perfect)>
RATIONALE: <1-2 sentences justifying the numeric scores. This is the field consumers will read — be specific and concrete.>
PROBLEM_LINES: <quote specific lines, or "None">

SCALE NOTES (be strict, do not cluster everything at 7/80):
- QUALITY_SCORE is an INTEGER 1-10. Use the full range.
- QA_SCORE is an INTEGER 0-100. A SOFT FAIL should land 50-75, a HARD FAIL below 50, a PASS 75+.
- A 1-point move in QUALITY_SCORE should mean something. Don't drift by +/- 1 for cosmetic reasons.

At the end, give an OVERALL verdict and whether this week should be published or regenerated."""

def _parse_day_verdict(verdict_raw: str) -> dict[str, Any]:
    """Parse a judge day-verdict block into structured fields.

    Returns a dict with verdict, quality_score (1-10 int), qa_score (0-100 int),
    rationale (short 1-2 sentence string), and the original raw text. Any field
    the model omits comes back as None — callers decide how to handle that.
    """
    verdict = None
    quality = None
    qa = None
    rationale: str | None = None

    # Strip markdown bold formatting before parsing
    import re
    cleaned = re.sub(r"\*\*", "", verdict_raw)

    upper = cleaned.upper()
    if "HARD FAIL" in upper:
        verdict = "HARD FAIL"
    elif "SOFT FAIL" in upper:
        verdict = "SOFT FAIL"
    elif "PASS" in upper:
        verdict = "PASS"

    def _extract_int(label: str, max_val: int) -> int | None:
        pattern = re.compile(rf"{label}\s*[:\-]?\s*(\d+)", re.IGNORECASE)
        match = pattern.search(cleaned)
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

    # Extract RATIONALE (preferred) or fall back to REASON for backward compat.
    # Captures the label and reads until the next ALL-CAPS label or end of block.
    rationale_pattern = re.compile(
        r"(?:RATIONALE|REASON)\s*[:\-]\s*(.+?)(?=\n[A-Z_]{3,}\s*[:\-]|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    rationale_match = rationale_pattern.search(cleaned)
    if rationale_match:
        rationale_text = rationale_match.group(1).strip()
        # Collapse whitespace/newlines inside rationale for compact storage
        rationale_text = re.sub(r"\s+", " ", rationale_text)
        rationale = rationale_text or None

    return {
        "verdict": verdict,
        "quality_score": quality,
        "qa_score": qa,
        "rationale": rationale,
        "raw": verdict_raw,
    }


# ---------------------------------------------------------------------------
# Schema versioning for saved judgment files
# ---------------------------------------------------------------------------

JUDGMENT_SCHEMA_VERSION = 2


def _weekly_rollup_from_days(day_verdicts: dict[str, dict]) -> dict[str, float | None]:
    """Compute weekly average quality (1-10) and QA (0-100) from per-day numerics.

    Missing scores are skipped. Returns None for a field if every day is missing.
    """
    q_scores = [
        v["quality_score"] for v in day_verdicts.values()
        if isinstance(v, dict) and v.get("quality_score") is not None
    ]
    qa_scores = [
        v["qa_score"] for v in day_verdicts.values()
        if isinstance(v, dict) and v.get("qa_score") is not None
    ]
    return {
        "avg_quality_score": round(sum(q_scores) / len(q_scores), 1) if q_scores else None,
        "avg_qa_score": round(sum(qa_scores) / len(qa_scores), 1) if qa_scores else None,
    }


def load_judgment(path: str | Path) -> dict | list[dict]:
    """Load a judgment file and normalize to the v2 schema shape in memory.

    v1 files (implicit — no ``schema_version`` key, or ``schema_version`` < 2):
      - ``day_verdicts`` may be a dict of raw-string verdicts.
      - No per-day numeric fields are guaranteed.
    v2 files:
      - Top-level ``schema_version == 2``.
      - ``day_verdicts[day]`` is a dict with ``quality_score``/``qa_score``/``rationale``.

    This loader never crashes on v1 — callers get a consistent shape back, with
    missing numerics set to None. The file on disk is not modified.
    """
    p = Path(path)
    with p.open() as f:
        data = json.load(f)

    # score_episodes / judge_conversation save a LIST of results (one per judge model).
    # Normalize to list-of-dicts for uniform handling, then return the same shape.
    is_list = isinstance(data, list)
    records = data if is_list else [data]

    normalized: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            normalized.append(rec)
            continue
        version = rec.get("schema_version", 1)
        day_verdicts = rec.get("day_verdicts", {}) or {}

        # Coerce raw-string day verdicts (v1 files) into the structured shape.
        fixed: dict[str, dict] = {}
        for day, v in day_verdicts.items():
            if isinstance(v, str):
                fixed[day] = {
                    "verdict": None,
                    "quality_score": None,
                    "qa_score": None,
                    "rationale": None,
                    "raw": v,
                }
            elif isinstance(v, dict):
                # Ensure all expected keys exist (old v1 dicts may lack rationale)
                fixed[day] = {
                    "verdict": v.get("verdict"),
                    "quality_score": v.get("quality_score"),
                    "qa_score": v.get("qa_score"),
                    "rationale": v.get("rationale"),
                    "raw": v.get("raw"),
                }
            else:
                fixed[day] = v  # leave non-dict/non-str alone

        out = dict(rec)
        out["schema_version"] = version if version >= 2 else 1
        out["day_verdicts"] = fixed
        normalized.append(out)

    return normalized if is_list else normalized[0]


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

    rollup = _weekly_rollup_from_days(day_verdicts)

    return {
        "schema_version": JUDGMENT_SCHEMA_VERSION,
        "source_file": filepath,
        "concept": concept,
        "generation_model": gen_model,
        "judge_model": model,
        "judged_at": datetime.now(timezone.utc).isoformat(),
        "day_verdicts": day_verdicts,
        "weekly_rollup": rollup,
        "overall_verdict": overall_raw,
        # Preserve the previously stored weekly QA number exactly as before.
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
