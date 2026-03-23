#!/usr/bin/env python3
"""Score live episode conversations using the same QA + Judge system from testing.

Runs the two-tier scoring system on real episode data from Vercel Blob:
  1. QA Score (0-100) — structural quality (fast, deterministic)
  2. Judge Score (1-10) — semantic quality (LLM-based, uses judge model)
  3. Combined = 0.6 * QA + 0.4 * (Judge * 10)

Usage:
    # Score all episodes
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/score_episodes.py

    # Score specific episode
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/score_episodes.py --episode 2026-W12

    # QA only (no LLM calls, fast)
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/score_episodes.py --qa-only

    # Save results to blob
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/score_episodes.py --save
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import config
from backend.storage import storage
from backend.utils.model_router import generate_judge_response
from scripts.simulate_dialogue_week import (
    Message,
    load_personas,
    score_quality,
)
from scripts.judge_conversation import (
    JUDGE_SYSTEM_PROMPT,
    DAY_ORDER,
    format_day_transcript,
    _parse_day_verdict,
)


def list_episodes() -> list[str]:
    """List all production episode IDs from blob."""
    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    resp = requests.get(
        "https://blob.vercel-storage.com",
        params={"prefix": "episodes/2026-W", "limit": 100},
        headers={"Authorization": f"Bearer {token}"},
    )
    blobs = resp.json().get("blobs", [])
    return sorted(
        b["pathname"].removeprefix("episodes/").removesuffix(".json")
        for b in blobs
        if not b["pathname"].startswith("episodes/test-")
    )


def episode_to_messages(episode: dict) -> list[Message]:
    """Convert episode stage dialogue to Message objects for QA scoring."""
    messages = []
    stages = episode.get("stages", {})
    for day in DAY_ORDER:
        stage_data = stages.get(day, {})
        dialogue = stage_data.get("dialogue", [])
        stage_name = stage_data.get("stage", day)
        for msg in dialogue:
            messages.append(Message(
                day=day,
                stage=stage_name,
                character=msg.get("character", "Unknown"),
                message=msg.get("message", ""),
                timestamp=msg.get("timestamp", ""),
                model=msg.get("model", "unknown"),
            ))
    return messages


def episode_to_day_transcripts(episode: dict) -> dict[str, list[dict]]:
    """Group episode dialogue by day for judge scoring."""
    by_day: dict[str, list[dict]] = {}
    stages = episode.get("stages", {})
    for day in DAY_ORDER:
        stage_data = stages.get(day, {})
        dialogue = stage_data.get("dialogue", [])
        if dialogue:
            by_day[day] = dialogue
    return by_day


def score_qa(episode: dict, personas: dict) -> dict[str, Any]:
    """Run QA scoring on an episode. Returns per-day and overall scores."""
    messages = episode_to_messages(episode)
    if not messages:
        return {"error": "No dialogue found", "scores": {}}

    concept = episode.get("concept", "Weekly Muffin Pan Recipe")

    # Score each day individually
    day_scores = {}
    for day in DAY_ORDER:
        day_msgs = [m for m in messages if m.day == day]
        if not day_msgs:
            continue
        result = score_quality(day_msgs, personas, concept=concept, day=None)
        day_scores[day] = {
            "score": result.get("score", 0),
            "details": {k: v for k, v in result.items() if k != "score"},
        }

    # Overall score (all messages together)
    overall = score_quality(messages, personas, concept=concept)

    scores = [d["score"] for d in day_scores.values()]
    avg_day_score = sum(scores) / len(scores) if scores else 0

    return {
        "overall_qa": overall.get("score", 0),
        "avg_day_qa": round(avg_day_score, 1),
        "day_scores": day_scores,
    }


def score_judge(episode: dict, model: str) -> dict[str, Any]:
    """Run LLM judge scoring on an episode. Returns per-day verdicts and overall."""
    by_day = episode_to_day_transcripts(episode)
    if not by_day:
        return {"error": "No dialogue found"}

    concept = episode.get("concept", "Weekly Muffin Pan Recipe")
    gen_model = "production-cron"

    accumulated_context = []
    day_verdicts = {}

    for day in DAY_ORDER:
        if day not in by_day:
            continue

        transcript = format_day_transcript(by_day[day])

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

        parsed = _parse_day_verdict(verdict_raw)
        day_verdicts[day] = parsed
        accumulated_context.append(f"=== {day.upper()} ===\n{transcript}")

    # Overall verdict
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

    # Extract overall quality score
    overall_parsed = _parse_day_verdict(overall_raw)

    # Compute average judge score across days
    day_quality_scores = [
        v["quality_score"] for v in day_verdicts.values()
        if v.get("quality_score") is not None
    ]
    avg_judge = sum(day_quality_scores) / len(day_quality_scores) if day_quality_scores else 0

    return {
        "judge_model": model,
        "avg_judge_score": round(avg_judge, 1),
        "overall_quality_score": overall_parsed.get("quality_score"),
        "overall_verdict": overall_raw,
        "day_verdicts": {
            day: {
                "verdict": v.get("verdict"),
                "quality_score": v.get("quality_score"),
                "qa_score": v.get("qa_score"),
            }
            for day, v in day_verdicts.items()
        },
    }


def score_episode(
    episode_id: str,
    personas: dict,
    qa_only: bool = False,
    judge_model: str | None = None,
) -> dict[str, Any]:
    """Score a single episode (QA + optional Judge)."""
    ep = storage.load_episode(episode_id)
    if not ep:
        return {"episode_id": episode_id, "error": "Episode not found"}

    concept = ep.get("concept", "Unknown")
    stages_complete = [
        day for day in DAY_ORDER
        if ep.get("stages", {}).get(day, {}).get("dialogue")
    ]

    result = {
        "episode_id": episode_id,
        "concept": concept,
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "days_with_dialogue": stages_complete,
    }

    # QA scoring (fast, no LLM)
    qa = score_qa(ep, personas)
    result["qa"] = qa

    # Judge scoring (LLM-based, expensive)
    if not qa_only and stages_complete:
        model = judge_model or config.judge_model
        judge = score_judge(ep, model)
        result["judge"] = judge

        # Combined score: 0.6 * QA + 0.4 * (Judge * 10)
        qa_score = qa.get("overall_qa", 0)
        judge_score = judge.get("avg_judge_score", 0)
        combined = round(0.6 * qa_score + 0.4 * (judge_score * 10), 1)
        result["combined_score"] = combined
    elif not qa_only:
        result["judge"] = {"error": "No dialogue to judge"}

    return result


def print_scorecard(result: dict) -> None:
    """Print a formatted scorecard for an episode."""
    ep_id = result.get("episode_id", "?")
    concept = result.get("concept", "?")
    days = result.get("days_with_dialogue", [])

    print(f"\n{'='*70}")
    print(f"  {ep_id} — {concept}")
    print(f"  Days with dialogue: {', '.join(days) or 'NONE'}")
    print(f"{'='*70}")

    qa = result.get("qa", {})
    if qa.get("error"):
        print(f"  QA: {qa['error']}")
    else:
        print(f"\n  QA Score (overall): {qa.get('overall_qa', '?')}/100")
        print(f"  QA Score (avg day): {qa.get('avg_day_qa', '?')}/100")
        day_scores = qa.get("day_scores", {})
        for day in DAY_ORDER:
            if day in day_scores:
                s = day_scores[day]["score"]
                bar = "█" * int(s / 5) + "░" * (20 - int(s / 5))
                print(f"    {day:10s} {bar} {s}")

    judge = result.get("judge", {})
    if judge and not judge.get("error"):
        print(f"\n  Judge Score (avg): {judge.get('avg_judge_score', '?')}/10")
        print(f"  Judge Model: {judge.get('judge_model', '?')}")
        day_verdicts = judge.get("day_verdicts", {})
        for day in DAY_ORDER:
            if day in day_verdicts:
                v = day_verdicts[day]
                verdict = v.get("verdict", "?")
                qs = v.get("quality_score", "?")
                print(f"    {day:10s} {verdict:10s} {qs}/10")

    combined = result.get("combined_score")
    if combined is not None:
        print(f"\n  COMBINED SCORE: {combined}/100")
        print(f"  (0.6 × QA:{qa.get('overall_qa', '?')} + 0.4 × Judge:{judge.get('avg_judge_score', '?')}×10)")

    print()


def main():
    parser = argparse.ArgumentParser(description="Score live episode conversations")
    parser.add_argument("--episode", help="Score a specific episode ID (e.g. 2026-W12)")
    parser.add_argument("--qa-only", action="store_true", help="QA scoring only (no LLM calls)")
    parser.add_argument("--model", default=None, help=f"Judge model (default: {config.judge_model})")
    parser.add_argument("--save", action="store_true", help="Save results to blob")
    args = parser.parse_args()

    personas = load_personas()

    if args.episode:
        episode_ids = [args.episode]
    else:
        episode_ids = list_episodes()
        print(f"Found {len(episode_ids)} episodes: {', '.join(episode_ids)}")

    all_results = []
    for ep_id in episode_ids:
        print(f"\nScoring {ep_id}...")
        result = score_episode(ep_id, personas, qa_only=args.qa_only, judge_model=args.model)
        all_results.append(result)
        print_scorecard(result)

    # Summary table
    if len(all_results) > 1:
        print(f"\n{'='*70}")
        print(f"  SUMMARY")
        print(f"{'='*70}")
        print(f"  {'Episode':12s} {'QA':>6s} {'Judge':>6s} {'Combined':>9s} {'Days':>5s}")
        print(f"  {'-'*12} {'-'*6} {'-'*6} {'-'*9} {'-'*5}")
        for r in all_results:
            ep_id = r.get("episode_id", "?")
            qa_s = r.get("qa", {}).get("overall_qa", "-")
            judge_s = r.get("judge", {}).get("avg_judge_score", "-")
            combined = r.get("combined_score", "-")
            n_days = len(r.get("days_with_dialogue", []))
            print(f"  {ep_id:12s} {str(qa_s):>6s} {str(judge_s):>6s} {str(combined):>9s} {n_days:>5d}")
        print()

    if args.save:
        content = json.dumps(all_results, indent=2)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        url = storage.save_page(f"scores/scoring-{ts}.json", content)
        print(f"Saved to blob: {url}")

        # Also save as latest
        url2 = storage.save_page("scores/latest.json", content)
        print(f"Saved latest: {url2}")


if __name__ == "__main__":
    main()
