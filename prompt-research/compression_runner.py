#!/usr/bin/env python3
"""Autoresearch-style compression optimization loop for Sunday dialogue.

Freezes Mon-Sat conversations once, then iterates on compression strategies.
Each iteration: compress frozen days → inject into Sunday → score → keep/discard.

Usage:
  # Run from project root with Doppler for env vars:
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run prompt-research/compression_runner.py \
    --concept "Jalapeño Corn Dog Bites" \
    --iterations 100

  # QA-only mode (no judge, faster, cheaper):
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run prompt-research/compression_runner.py \
    --concept "Mini Shepherd's Pies" \
    --iterations 50 \
    --qa-only

  # Auto-stop when improvements plateau:
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run prompt-research/compression_runner.py \
    --concept "Jalapeño Corn Dog Bites" \
    --iterations 100 \
    --auto-stop
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.utils.model_router import generate_response

def retry_on_api_error(fn, *args, max_retries: int = 3, base_delay: float = 5.0, **kwargs):
    """Retry a function call on 500/529 API errors with exponential backoff."""
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            error_str = str(e)
            is_retryable = "529" in error_str or "500" in error_str or "overloaded" in error_str.lower()
            if not is_retryable or attempt == max_retries:
                raise
            delay = base_delay * (2 ** attempt)
            print(f"    API error (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay:.0f}s...")
            time.sleep(delay)


RESEARCH_DIR = Path(__file__).resolve().parent
RUNS_DIR = RESEARCH_DIR / "compression_runs"
BASELINES_DIR = RESEARCH_DIR / "baselines"
RESULTS_TSV = RESEARCH_DIR / "compression_results.tsv"
PROGRAM_MD = RESEARCH_DIR / "compression_program.md"
COMPRESSION_PROMPTS = RESEARCH_DIR / "compression_prompts.py"
BEST_COMPRESSION = RESEARCH_DIR / "best_compression_prompts.py"

DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAYS_BEFORE_SUNDAY = DAY_ORDER[:6]  # mon-sat


def concept_slug(concept: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", concept.lower()).strip("-")


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_text().encode()).hexdigest()[:8]


def load_compression_module():
    """Load (or reload) the mutable compression_prompts.py as a module."""
    spec = importlib.util.spec_from_file_location(
        "compression_prompts", COMPRESSION_PROMPTS
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load compression_prompts.py from {RESEARCH_DIR}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Frozen week generation
# ---------------------------------------------------------------------------

def generate_frozen_week(concept: str, model: str) -> list[dict]:
    """Generate a full Mon-Sat week of conversations and cache to disk.

    Returns all messages for Mon through Saturday (Sunday excluded).
    """
    slug = concept_slug(concept)
    cache_path = BASELINES_DIR / slug / "frozen-week-mon-sat.json"

    if cache_path.exists():
        print(f"  Loading frozen week from cache: {cache_path.name}")
        return json.loads(cache_path.read_text())

    print("  Generating full Mon-Sat conversations (this takes a few minutes)...")
    import scripts.simulate_dialogue_week as sdw
    importlib.reload(sdw)

    result = sdw.run_simulation(
        concept=concept,
        default_model=model,
        run_index=0,
        stage_only=None,
        injected_event=None,
        ticks_per_day=6,
        mode="llm",
        prompt_style="scene",
        character_models=None,
    )

    # Extract Mon-Sat only (exclude Sunday)
    week_messages = [
        m for m in result["messages"]
        if m["day"] != "sunday"
    ]

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(week_messages, indent=2))
    print(f"  Cached {len(week_messages)} messages ({len(set(m['day'] for m in week_messages))} days)")
    return week_messages


def group_by_day(messages: list[dict]) -> dict[str, list[dict]]:
    """Group messages by day."""
    by_day: dict[str, list[dict]] = {}
    for m in messages:
        by_day.setdefault(m["day"], []).append(m)
    return by_day


# ---------------------------------------------------------------------------
# Compression step
# ---------------------------------------------------------------------------

def compress_week(
    frozen_week: list[dict],
    concept: str,
    model: str,
    compression_mod: Any,
) -> list[str]:
    """Apply compression to each day's conversation using the mutable template.

    Returns a list of "DayName: summary" strings ready for injection.
    """
    by_day = group_by_day(frozen_week)
    highlights: list[str] = []

    for day in DAYS_BEFORE_SUNDAY:
        day_msgs = by_day.get(day, [])
        if not day_msgs:
            continue

        summary = compression_mod.compress_day(
            day=day,
            messages=day_msgs,
            concept=concept,
            model=model,
            generate_fn=generate_response,
        )

        if summary:
            highlights.append(f"{day.title()}: {summary}")

    return highlights


def compress_week_progressive(
    frozen_week: list[dict],
    concept: str,
    model: str,
    compression_mod: Any,
) -> list[str]:
    """Apply progressive (rolling) compression across the week.

    Each day's summary incorporates the prior days' rolling summary.
    Returns a single-item list with the final rolling summary, formatted
    as a highlight for injection.
    """
    by_day = group_by_day(frozen_week)
    rolling_summary = ""

    for day_index, day in enumerate(DAYS_BEFORE_SUNDAY):
        day_msgs = by_day.get(day, [])
        if not day_msgs:
            continue

        rolling_summary = compression_mod.compress_day_progressive(
            day=day,
            messages=day_msgs,
            concept=concept,
            model=model,
            prior_summary=rolling_summary,
            day_index=day_index,
            generate_fn=generate_response,
        )

    if rolling_summary:
        return [f"Week summary (Monday through Saturday): {rolling_summary}"]
    return []


# ---------------------------------------------------------------------------
# Sunday generation with injected highlights
# ---------------------------------------------------------------------------

def generate_sunday(
    concept: str,
    model: str,
    highlights: list[str],
    frozen_week: list[dict] | None = None,
    highlight_format: str = "plain",
) -> list[dict]:
    """Generate Sunday dialogue with compressed week context injected.

    If frozen_week is provided, Saturday's messages are seeded as recent_lines
    so Sunday characters can see immediate prior context (not just highlights).
    """
    import scripts.simulate_dialogue_week as sdw
    sdw._system_prompt_cache.clear()

    # Seed recent_lines from Saturday so Sunday sees immediate prior context
    initial_recent: list[str] | None = None
    if frozen_week:
        by_day = group_by_day(frozen_week)
        sat_messages = by_day.get("saturday", [])
        if sat_messages:
            initial_recent = [
                f"{m['character'].split()[0]}: {m['message']}"
                for m in sat_messages
            ]

    result = sdw.run_simulation(
        concept=concept,
        default_model=model,
        run_index=0,
        stage_only="sunday",
        injected_event=None,
        ticks_per_day=6,
        mode="llm",
        prompt_style="scene",
        character_models=None,
        initial_highlights=highlights,
        initial_recent_lines=initial_recent,
        highlight_format=highlight_format,
    )

    return result["messages"]


# ---------------------------------------------------------------------------
# Agent modification request
# ---------------------------------------------------------------------------

def ask_agent_for_modification(
    current_path: Path,
    results_tail: str,
    agent_model: str,
    iteration: int,
) -> str:
    """Ask an LLM to suggest a modification to compression_prompts.py."""
    current_code = current_path.read_text()
    program = PROGRAM_MD.read_text() if PROGRAM_MD.exists() else "Improve compression quality."

    prompt = (
        f"You are an autonomous compression researcher. Iteration {iteration}.\n\n"
        f"RESEARCH INSTRUCTIONS:\n{program}\n\n"
        f"CURRENT COMPRESSION PROMPTS (the file you're modifying):\n```python\n{current_code}\n```\n\n"
        f"RECENT RESULTS:\n{results_tail}\n\n"
        "Based on the research instructions and recent results, suggest ONE modification "
        "to improve Sunday dialogue quality through better compression. "
        "Return the COMPLETE modified compression_prompts.py file.\n\n"
        "Rules:\n"
        "- Make ONE focused change per iteration\n"
        "- Keep the same function signatures and variable names\n"
        "- The file must be valid Python\n"
        "- Return ONLY the Python code, no markdown fences, no explanation"
    )

    response = generate_response(
        prompt=prompt,
        system_prompt="You are a compression strategy researcher. Return only valid Python code.",
        model=agent_model,
        temperature=0.7,
    ).strip()

    # Strip markdown code fences if present
    if response.startswith("```"):
        lines = response.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        response = "\n".join(lines)

    return response


# ---------------------------------------------------------------------------
# Results tracking
# ---------------------------------------------------------------------------

def init_results_tsv():
    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(
            "exp_id\ttimestamp\tconcept\tqa_score\tjudge_score\t"
            "combined_score\tverdict\tchange_summary\tprompts_hash\t"
            "highlights_preview\n"
        )


def append_result(
    exp_id: int,
    concept: str,
    qa_score: int,
    judge_score: int | None,
    combined_score: float,
    verdict: str,
    change_summary: str,
    prompts_hash: str,
    highlights_preview: str = "",
):
    ts = datetime.now(timezone.utc).isoformat()
    judge_str = str(judge_score) if judge_score is not None else "n/a"
    clean_summary = change_summary.replace("\t", " ").replace("\n", " ")[:200]
    clean_preview = highlights_preview.replace("\t", " ").replace("\n", " | ")[:300]
    row = (
        f"{exp_id:04d}\t{ts}\t{concept}\t{qa_score}\t{judge_str}\t"
        f"{combined_score}\t{verdict}\t{clean_summary}\t{prompts_hash}\t"
        f"{clean_preview}\n"
    )
    with open(RESULTS_TSV, "a") as f:
        f.write(row)


def get_results_tail(n: int = 10) -> str:
    if not RESULTS_TSV.exists():
        return "(no results yet)"
    lines = RESULTS_TSV.read_text().strip().split("\n")
    return "\n".join(lines[:1] + lines[-n:])


# ---------------------------------------------------------------------------
# Trend analysis (reused from runner.py)
# ---------------------------------------------------------------------------

def analyze_trend(scores: list[float], window: int = 5) -> dict[str, Any]:
    if len(scores) < 5:
        return {"trend": "insufficient", "slope": 0.0, "best_at": 0,
                "since_best": 0, "recommendation": "continue"}

    best_val = max(scores)
    best_at = scores.index(best_val)
    since_best = len(scores) - 1 - best_at

    recent = scores[-window:]
    if len(recent) >= 2:
        deltas = [recent[j] - recent[j - 1] for j in range(1, len(recent))]
        slope = sum(deltas) / len(deltas)
    else:
        slope = 0.0

    if len(scores) >= window:
        recent_deltas = [scores[j] - scores[j - 1]
                         for j in range(max(1, len(scores) - window), len(scores))]
        sign_changes = sum(
            1 for k in range(1, len(recent_deltas))
            if (recent_deltas[k] > 0) != (recent_deltas[k - 1] > 0)
        )
        oscillating = sign_changes >= (window - 2)
    else:
        oscillating = False

    if oscillating:
        trend = "oscillating"
    elif since_best >= window * 2:
        trend = "declining"
    elif abs(slope) < 0.5 and since_best >= window:
        trend = "plateau"
    elif slope > 0:
        trend = "climbing"
    else:
        trend = "plateau"

    if trend == "climbing":
        recommendation = "continue"
    elif trend == "plateau" and since_best < window * 3:
        recommendation = "continue"
    elif trend == "oscillating" and since_best > window * 2:
        recommendation = "stop"
    elif trend == "declining":
        recommendation = "stop"
    elif since_best >= window * 3:
        recommendation = "stop"
    else:
        recommendation = "continue"

    return {
        "trend": trend, "slope": round(slope, 3),
        "best_at": best_at, "since_best": since_best,
        "recommendation": recommendation,
    }


CHECKPOINT_INTERVALS = [5, 10, 20, 50, 100]


def print_checkpoint(iteration: int, scores: list[float], best_score: float, keeps: int):
    if iteration not in CHECKPOINT_INTERVALS:
        return None

    trend = analyze_trend(scores)
    print(f"\n{'─' * 50}")
    print(f"  CHECKPOINT @ iteration {iteration}")
    print(f"  Best score: {best_score:.1f} (achieved at exp {trend['best_at']})")
    print(f"  Keeps: {keeps}/{iteration} ({100 * keeps / iteration:.0f}%)")
    print(f"  Trend: {trend['trend']} (slope: {trend['slope']:+.2f})")
    print(f"  Since best: {trend['since_best']} iterations")
    print(f"  Recommendation: {trend['recommendation'].upper()}")
    print(f"{'─' * 50}")
    return trend


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_loop(
    concept: str,
    iterations: int,
    dialogue_model: str,
    judge_model: str,
    agent_model: str,
    compression_model: str,
    qa_only: bool = False,
    judge_every: int = 3,
    auto_stop: bool = False,
    mode: str = "independent",
    highlight_format: str = "plain",
):
    """Main compression experiment loop."""
    sys.path.insert(0, str(RESEARCH_DIR))
    from evaluator import evaluate
    import scripts.simulate_dialogue_week as sdw

    init_results_tsv()

    print(f"{'=' * 60}")
    print(f"COMPRESSION RESEARCH: {concept}")
    print(f"Target: Sunday (with Mon-Sat compressed context)")
    print(f"Iterations: {iterations}")
    print(f"Dialogue model: {dialogue_model}")
    print(f"Compression model: {compression_model}")
    print(f"Judge model: {judge_model}")
    print(f"Agent model: {agent_model}")
    print(f"QA-only: {qa_only}")
    print(f"Compression mode: {mode}")
    print(f"Highlight format: {highlight_format}")
    print(f"{'=' * 60}")

    # Step 1: Generate and freeze Mon-Sat
    print("\n[1/4] Freezing Mon-Sat conversations...")
    frozen_week = generate_frozen_week(concept, dialogue_model)
    by_day = group_by_day(frozen_week)
    print(f"  Days frozen: {', '.join(d.title() for d in by_day.keys())}")
    print(f"  Total messages: {len(frozen_week)}")

    # Step 2: Baseline — no compression (Sunday without any highlights)
    print("\n[2/4] Running baseline (Sunday with NO context injection)...")
    importlib.reload(sdw)
    baseline_messages = generate_sunday(concept, dialogue_model, [], frozen_week)
    baseline_dicts = [m if isinstance(m, dict) else m.__dict__ for m in baseline_messages]

    personas = sdw.load_personas()

    # Build frozen history for judge context (all Mon-Sat messages)
    frozen_for_judge = frozen_week

    baseline_eval = retry_on_api_error(
        evaluate,
        messages=baseline_dicts,
        personas=personas,
        concept=concept,
        target_day="sunday",
        frozen_history=frozen_for_judge,
        judge_model=judge_model,
        run_judge=not qa_only,
    )

    best_score = baseline_eval["combined_score"]
    best_qa = baseline_eval["qa_score"]
    best_judge = baseline_eval.get("judge_score")

    # Save baseline
    exp_dir = RUNS_DIR / "exp-0000"
    exp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(COMPRESSION_PROMPTS, exp_dir / "compression_prompts.py")
    (exp_dir / "dialogue.json").write_text(json.dumps(baseline_dicts, indent=2))
    (exp_dir / "scores.json").write_text(json.dumps(baseline_eval, indent=2, default=str))
    (exp_dir / "highlights.json").write_text("[]")
    shutil.copy2(COMPRESSION_PROMPTS, BEST_COMPRESSION)

    append_result(
        exp_id=0, concept=concept, qa_score=best_qa,
        judge_score=best_judge, combined_score=best_score,
        verdict="BASELINE", change_summary="no compression (baseline)",
        prompts_hash=file_hash(COMPRESSION_PROMPTS),
        highlights_preview="(none)",
    )

    print(f"  Baseline: QA={best_qa}, Judge={best_judge}, Combined={best_score}")

    # Step 3: Run initial compression with default template
    print("\n[3/4] Running initial compression baseline...")
    compression_mod = load_compression_module()
    compress_fn = compress_week_progressive if mode == "progressive" else compress_week
    initial_highlights = compress_fn(frozen_week, concept, compression_model, compression_mod)
    initial_messages = generate_sunday(concept, dialogue_model, initial_highlights, frozen_week, highlight_format)
    initial_dicts = [m if isinstance(m, dict) else m.__dict__ for m in initial_messages]

    initial_eval = retry_on_api_error(
        evaluate,
        messages=initial_dicts,
        personas=personas,
        concept=concept,
        target_day="sunday",
        frozen_history=frozen_for_judge,
        judge_model=judge_model,
        run_judge=not qa_only,
    )

    init_score = initial_eval["combined_score"]
    init_qa = initial_eval["qa_score"]
    init_judge = initial_eval.get("judge_score")

    # Save initial compression result
    exp_dir = RUNS_DIR / "exp-0001"
    exp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(COMPRESSION_PROMPTS, exp_dir / "compression_prompts.py")
    (exp_dir / "dialogue.json").write_text(json.dumps(initial_dicts, indent=2))
    (exp_dir / "scores.json").write_text(json.dumps(initial_eval, indent=2, default=str))
    (exp_dir / "highlights.json").write_text(json.dumps(initial_highlights, indent=2))

    preview = " | ".join(h[:60] for h in initial_highlights[:3])
    append_result(
        exp_id=1, concept=concept, qa_score=init_qa,
        judge_score=init_judge, combined_score=init_score,
        verdict="INITIAL", change_summary="default compression template",
        prompts_hash=file_hash(COMPRESSION_PROMPTS),
        highlights_preview=preview,
    )

    print(f"  Initial compression: QA={init_qa}, Judge={init_judge}, Combined={init_score}")
    print(f"  Highlights preview: {preview[:120]}...")

    if init_score > best_score:
        best_score = init_score
        best_qa = init_qa
        best_judge = init_judge
        shutil.copy2(COMPRESSION_PROMPTS, BEST_COMPRESSION)
        print("  Compression IMPROVED over no-context baseline!")
    else:
        print("  Compression did NOT improve over baseline (starting from here anyway)")
        # Still use compression as the starting point — the whole point is to optimize it
        best_score = init_score
        best_qa = init_qa
        best_judge = init_judge
        shutil.copy2(COMPRESSION_PROMPTS, BEST_COMPRESSION)

    keeps = 0
    all_scores: list[float] = [baseline_eval["combined_score"], init_score]
    stopped_early = False

    # Step 4: Experiment loop
    print(f"\n[4/4] Starting compression experiment loop ({iterations} iterations)...")
    for i in range(2, iterations + 2):  # Start at 2 since 0=baseline, 1=initial
        print(f"\n--- Experiment {i:04d}/{iterations + 1} ---")

        # Ask agent for a modification (with retry on API errors)
        print("  Asking agent for compression modification...")
        try:
            new_code = retry_on_api_error(
                ask_agent_for_modification,
                current_path=BEST_COMPRESSION,
                results_tail=get_results_tail(),
                agent_model=agent_model,
                iteration=i,
            )
        except Exception as e:
            print(f"  Agent error: {e}. Skipping.")
            append_result(i, concept, 0, None, 0, "ERROR", f"agent error: {e}", "n/a")
            continue

        # Write modified compression prompts
        exp_dir = RUNS_DIR / f"exp-{i:04d}"
        exp_dir.mkdir(parents=True, exist_ok=True)
        modified_path = exp_dir / "compression_prompts.py"
        modified_path.write_text(new_code)
        COMPRESSION_PROMPTS.write_text(new_code)

        # Try to load
        try:
            compression_mod = load_compression_module()
        except Exception as e:
            print(f"  Invalid compression_prompts.py: {e}. Discarding.")
            shutil.copy2(BEST_COMPRESSION, COMPRESSION_PROMPTS)
            append_result(i, concept, 0, None, 0, "CRASH", f"invalid code: {e}", "n/a")
            continue

        # Compress frozen week with new template (with retry)
        try:
            highlights = retry_on_api_error(
                compress_fn, frozen_week, concept, compression_model, compression_mod,
            )
        except Exception as e:
            print(f"  Compression error: {e}. Discarding.")
            shutil.copy2(BEST_COMPRESSION, COMPRESSION_PROMPTS)
            append_result(i, concept, 0, None, 0, "CRASH", f"compression error: {e}", "n/a")
            continue

        # Generate Sunday with new compression (with retry)
        try:
            messages = retry_on_api_error(
                generate_sunday, concept, dialogue_model, highlights, frozen_week, highlight_format,
            )
            messages_dicts = [m if isinstance(m, dict) else m.__dict__ for m in messages]
        except Exception as e:
            print(f"  Generation error: {e}. Discarding.")
            shutil.copy2(BEST_COMPRESSION, COMPRESSION_PROMPTS)
            append_result(i, concept, 0, None, 0, "CRASH", f"generation error: {e}", "n/a")
            continue

        # Evaluate (with retry)
        should_judge = not qa_only and (keeps % judge_every == 0 or i <= 4)
        try:
            eval_result = retry_on_api_error(
                evaluate,
                messages=messages_dicts,
                personas=personas,
                concept=concept,
                target_day="sunday",
                frozen_history=frozen_for_judge,
                judge_model=judge_model,
                run_judge=should_judge,
            )
        except Exception as e:
            print(f"  Evaluation error: {e}. Discarding.")
            shutil.copy2(BEST_COMPRESSION, COMPRESSION_PROMPTS)
            append_result(i, concept, 0, None, 0, "CRASH", f"eval error: {e}", "n/a")
            continue

        score = eval_result["combined_score"]
        qa = eval_result["qa_score"]
        judge = eval_result.get("judge_score")

        # Save artifacts
        (exp_dir / "dialogue.json").write_text(json.dumps(messages_dicts, indent=2, default=str))
        (exp_dir / "scores.json").write_text(json.dumps(eval_result, indent=2, default=str))
        (exp_dir / "highlights.json").write_text(json.dumps(highlights, indent=2))

        # Change summary
        old_lines = set(BEST_COMPRESSION.read_text().split("\n"))
        new_lines = set(new_code.split("\n"))
        added = new_lines - old_lines
        summary = next(iter(added), "unknown change")[:200] if added else "no visible change"
        preview = " | ".join(h[:60] for h in highlights[:3])

        # Keep or discard
        if score > best_score:
            verdict = "KEEP"
            keeps += 1
            best_score = score
            best_qa = qa
            best_judge = judge
            shutil.copy2(COMPRESSION_PROMPTS, BEST_COMPRESSION)
            print(f"  KEEP: QA={qa}, Judge={judge}, Combined={score} (was {best_score - (score - best_score):.1f})")
        else:
            verdict = "DISCARD"
            shutil.copy2(BEST_COMPRESSION, COMPRESSION_PROMPTS)
            print(f"  DISCARD: QA={qa}, Judge={judge}, Combined={score} (best={best_score})")

        append_result(i, concept, qa, judge, score, verdict, summary,
                      file_hash(modified_path), preview)
        all_scores.append(score)

        # Trend checkpoint and auto-stop
        checkpoint = print_checkpoint(i - 1, all_scores, best_score, keeps)
        if auto_stop and checkpoint and checkpoint["recommendation"] == "stop":
            print(f"\n  AUTO-STOP: {checkpoint['trend']} detected after {i - 1} iterations.")
            print(f"  No improvement in {checkpoint['since_best']} iterations. Stopping early.")
            stopped_early = True
            break

    # Final report
    final_trend = analyze_trend(all_scores)
    total_run = len(all_scores) - 1
    print(f"\n{'=' * 60}")
    if stopped_early:
        print(f"STOPPED EARLY at iteration {total_run} (trend: {final_trend['trend']})")
    print(f"COMPLETE: {total_run} experiments, {keeps} improvements kept")
    print(f"Final trend: {final_trend['trend']} (slope: {final_trend['slope']:+.2f})")
    print(f"Best score: QA={best_qa}, Judge={best_judge}, Combined={best_score}")
    print(f"Best compression saved to: {BEST_COMPRESSION}")
    print(f"Results log: {RESULTS_TSV}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Compression optimization loop for Sunday dialogue")
    parser.add_argument("--concept", required=True, help="Recipe concept to test with")
    parser.add_argument("--iterations", type=int, default=20, help="Number of experiments to run")
    parser.add_argument("--dialogue-model", default=None, help="Model for dialogue generation")
    parser.add_argument("--compression-model", default=None, help="Model for compression (default: same as dialogue)")
    parser.add_argument("--judge-model", default="anthropic/claude-sonnet-4-6", help="Model for judging")
    parser.add_argument("--agent-model", default="anthropic/claude-sonnet-4-6", help="Model for suggesting modifications")
    parser.add_argument("--qa-only", action="store_true", help="Skip judge, use QA score only")
    parser.add_argument("--judge-every", type=int, default=3, help="Run judge every N keeps")
    parser.add_argument("--auto-stop", action="store_true", help="Stop early if improvements plateau")
    parser.add_argument("--mode", choices=["independent", "progressive"], default="independent",
                        help="Compression mode: independent (per-day) or progressive (rolling)")
    parser.add_argument("--highlight-format", choices=["plain", "xml"], default="plain",
                        help="Injection format: plain (bullet points) or xml (structured tags)")
    args = parser.parse_args()

    from backend.config import config

    dialogue_model = args.dialogue_model or config.dialogue_model
    compression_model = args.compression_model or dialogue_model

    run_loop(
        concept=args.concept,
        iterations=args.iterations,
        dialogue_model=dialogue_model,
        compression_model=compression_model,
        judge_model=args.judge_model,
        agent_model=args.agent_model,
        qa_only=args.qa_only,
        judge_every=args.judge_every,
        auto_stop=args.auto_stop,
        mode=args.mode,
        highlight_format=args.highlight_format,
    )


if __name__ == "__main__":
    main()
