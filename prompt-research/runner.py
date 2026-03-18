#!/usr/bin/env python3
"""Autoresearch-style prompt optimization loop for dialogue quality.

Generates dialogue for a single target day, scores it, keeps or discards
prompt changes based on whether the score improved.

Usage:
  # Run from project root with Doppler for env vars:
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run prompt-research/runner.py \
    --concept "Brown Butter Pecan Tassies" \
    --target-day monday \
    --iterations 20

  # Target a later day (frozen history generated automatically):
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run prompt-research/runner.py \
    --concept "Jalapeño Corn Dog Bites" \
    --target-day friday \
    --iterations 50

  # QA-only mode (no judge, faster, cheaper):
  PYTHONPATH=. doppler run --project muffinpanrecipes --config dev -- \
    uv run prompt-research/runner.py \
    --concept "Mini Shepherd's Pies" \
    --target-day monday \
    --iterations 100 \
    --qa-only
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
RUNS_DIR = RESEARCH_DIR / "runs"
BASELINES_DIR = RESEARCH_DIR / "baselines"
RESULTS_TSV = RESEARCH_DIR / "results.tsv"
PROGRAM_MD = RESEARCH_DIR / "program.md"

DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def concept_slug(concept: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", concept.lower()).strip("-")


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_text().encode()).hexdigest()[:8]


def load_prompts_module():
    """Load (or reload) the mutable prompts.py as a module."""
    spec = importlib.util.spec_from_file_location("prompts", RESEARCH_DIR / "prompts.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load prompts.py from {RESEARCH_DIR}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def patch_generation_engine(prompts_mod):
    """Monkey-patch simulate_dialogue_week with experimental prompts."""
    import scripts.simulate_dialogue_week as sdw

    sdw._CHARACTER_VOICE_GUIDES = prompts_mod.CHARACTER_VOICE_GUIDES
    sdw._SHARED_CHARACTER_RULES = prompts_mod.SHARED_CHARACTER_RULES
    sdw._CHARACTER_EXAMPLE_MESSAGES = prompts_mod.CHARACTER_EXAMPLE_MESSAGES

    # Wrap build_system_prompt to use the experimental template
    original_load_bio = sdw._load_bio
    original_load_memories = sdw._load_memories

    def patched_build_system_prompt(persona: dict[str, Any]) -> str:
        name = persona["name"]
        comm = persona["communication_style"]
        bio = original_load_bio(name)
        who_you_are = bio if bio else persona["backstory"][:600]

        relationships = persona.get("relationships", {})
        rel_lines = []
        for role, desc in relationships.items():
            sentences = desc.split(". ")
            rel_lines.append(f"- {role}: {'. '.join(sentences[:2])}.")

        examples = prompts_mod.CHARACTER_EXAMPLE_MESSAGES.get(name, [])
        examples_block = ""
        if examples:
            formatted = chr(10).join(f'- "{ex}"' for ex in examples)
            examples_block = (
                f"EXAMPLE MESSAGES (this is how you sound — match this energy and length):\n"
                f"{formatted}\n\n"
            )

        memories = original_load_memories(name)
        if memories:
            mem_lines = []
            for ep in memories:
                ep_concept = ep.get("concept", "unknown")
                summary = ep.get("summary", "")
                mem_lines.append(f"- {ep_concept}: {summary}")
            memory_block = "WHAT YOU REMEMBER FROM RECENTLY:\n" + chr(10).join(mem_lines) + "\n\n"
        else:
            memory_block = (
                "THIS IS YOUR FIRST WEEK ON THE JOB.\n"
                "You've never worked with these people before. You were hired separately. "
                "You know everyone's name and role from email introductions, but you haven't "
                "seen how they actually work. First impressions are forming RIGHT NOW. "
                "Be slightly guarded, curious, or nervous depending on your personality.\n\n"
            )

        voice_guide = prompts_mod.CHARACTER_VOICE_GUIDES.get(name, "")
        return prompts_mod.build_system_prompt(
            name=name,
            role=persona["role"],
            who_you_are=who_you_are,
            voice_guide=voice_guide,
            examples_block=examples_block,
            memory_block=memory_block,
            signature_phrases=comm.get("signature_phrases", []),
            internal_contradictions=persona.get("internal_contradictions", []),
            rel_lines=rel_lines,
            triggers=persona.get("triggers", []),
        )

    sdw.build_system_prompt = patched_build_system_prompt
    sdw._system_prompt_cache.clear()


def generate_frozen_history(
    concept: str,
    target_day: str,
    model: str,
) -> list[dict]:
    """Generate days before target_day once and cache to disk."""
    slug = concept_slug(concept)
    cache_path = BASELINES_DIR / slug / f"frozen-before-{target_day}.json"

    if cache_path.exists():
        print(f"  Loading frozen history from cache: {cache_path.name}")
        return json.loads(cache_path.read_text())

    target_idx = DAY_ORDER.index(target_day)
    if target_idx == 0:
        # Monday — no history needed
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("[]")
        return []

    print(f"  Generating frozen history (Mon-{DAY_ORDER[target_idx - 1].title()})...")
    import scripts.simulate_dialogue_week as sdw

    # Run simulation for days before target using ORIGINAL (unpatched) prompts
    # Reload to ensure we're using baseline
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

    # Extract only the days before target
    history_messages = [
        m for m in result["messages"]
        if DAY_ORDER.index(m["day"]) < target_idx
    ]

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(history_messages, indent=2))
    print(f"  Cached {len(history_messages)} messages of frozen history")
    return history_messages


def generate_target_day(
    concept: str,
    target_day: str,
    model: str,
    frozen_history: list[dict],  # noqa: ARG001 — reserved for future history injection
) -> list[dict]:
    """Generate dialogue for just the target day, using frozen history as context.

    NOTE: Currently run_simulation with stage_only starts fresh (no prior context).
    The frozen_history is passed to the evaluator so the judge has context.
    Future improvement: inject frozen_history as initial recent_lines so the
    target day's generation can reference prior days' conversation.
    """
    import scripts.simulate_dialogue_week as sdw

    # Clear system prompt cache so patched prompts take effect
    sdw._system_prompt_cache.clear()

    result = sdw.run_simulation(
        concept=concept,
        default_model=model,
        run_index=0,
        stage_only=target_day,
        injected_event=None,
        ticks_per_day=6,
        mode="llm",
        prompt_style="scene",
        character_models=None,
    )

    return result["messages"]


def ask_agent_for_modification(
    current_prompts_path: Path,
    results_tail: str,
    agent_model: str,
    iteration: int,
) -> str:
    """Ask an LLM to suggest a modification to prompts.py."""
    current_prompts = current_prompts_path.read_text()
    program = PROGRAM_MD.read_text() if PROGRAM_MD.exists() else "Improve dialogue quality."

    prompt = (
        f"You are an autonomous prompt researcher. Iteration {iteration}.\n\n"
        f"RESEARCH INSTRUCTIONS:\n{program}\n\n"
        f"CURRENT PROMPTS (the file you're modifying):\n```python\n{current_prompts}\n```\n\n"
        f"RECENT RESULTS:\n{results_tail}\n\n"
        "Based on the research instructions and recent results, suggest ONE modification "
        "to improve dialogue quality. Return the COMPLETE modified prompts.py file.\n\n"
        "Rules:\n"
        "- Make ONE focused change per iteration\n"
        "- Keep the same function signatures and variable names\n"
        "- The file must be valid Python\n"
        "- Return ONLY the Python code, no markdown fences, no explanation"
    )

    response = generate_response(
        prompt=prompt,
        system_prompt="You are a prompt engineering researcher. Return only valid Python code.",
        model=agent_model,
        temperature=0.7,
    ).strip()

    # Strip markdown code fences if present
    if response.startswith("```"):
        lines = response.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        response = "\n".join(lines)

    return response


def init_results_tsv():
    """Create results.tsv with header if it doesn't exist."""
    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(
            "exp_id\ttimestamp\ttarget_day\tconcept\tqa_score\tjudge_score\t"
            "combined_score\tverdict\tchange_summary\tprompts_hash\n"
        )


def append_result(
    exp_id: int,
    target_day: str,
    concept: str,
    qa_score: int,
    judge_score: int | None,
    combined_score: float,
    verdict: str,
    change_summary: str,
    prompts_hash: str,
):
    """Append a row to results.tsv."""
    ts = datetime.now(timezone.utc).isoformat()
    judge_str = str(judge_score) if judge_score is not None else "n/a"
    # Sanitize description for TSV (no tabs or newlines)
    clean_summary = change_summary.replace("\t", " ").replace("\n", " ")[:200]
    row = (
        f"{exp_id:04d}\t{ts}\t{target_day}\t{concept}\t{qa_score}\t{judge_str}\t"
        f"{combined_score}\t{verdict}\t{clean_summary}\t{prompts_hash}\n"
    )
    with open(RESULTS_TSV, "a") as f:
        f.write(row)


def analyze_trend(scores: list[float], window: int = 5) -> dict[str, Any]:
    """Analyze score trend to detect plateau, decline, or healthy climb.

    Returns a dict with:
      - trend: "climbing", "plateau", "declining", "oscillating", "insufficient"
      - slope: average score change per iteration (last `window` scores)
      - best_at: iteration index where best score was achieved
      - since_best: how many iterations since last improvement
      - recommendation: "continue", "stop", or "review"
    """
    if len(scores) < 5:
        return {"trend": "insufficient", "slope": 0.0, "best_at": 0,
                "since_best": 0, "recommendation": "continue"}

    best_val = max(scores)
    best_at = scores.index(best_val)
    since_best = len(scores) - 1 - best_at

    # Calculate slope over the last `window` scores
    recent = scores[-window:]
    if len(recent) >= 2:
        deltas = [recent[j] - recent[j - 1] for j in range(1, len(recent))]
        slope = sum(deltas) / len(deltas)
    else:
        slope = 0.0

    # Detect oscillation: sign changes in deltas
    if len(scores) >= window:
        recent_deltas = [scores[j] - scores[j - 1] for j in range(max(1, len(scores) - window), len(scores))]
        sign_changes = sum(
            1 for k in range(1, len(recent_deltas))
            if (recent_deltas[k] > 0) != (recent_deltas[k - 1] > 0)
        )
        oscillating = sign_changes >= (window - 2)  # Nearly every step reverses
    else:
        oscillating = False

    # Classify
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

    # Recommendation
    if trend == "climbing":
        recommendation = "continue"
    elif trend == "plateau" and since_best < window * 3:
        recommendation = "continue"  # Give it a bit more room
    elif trend == "oscillating" and since_best > window * 2:
        recommendation = "stop"
    elif trend == "declining":
        recommendation = "stop"
    elif since_best >= window * 3:
        recommendation = "stop"  # Haven't improved in 3 windows
    else:
        recommendation = "continue"

    return {
        "trend": trend,
        "slope": round(slope, 3),
        "best_at": best_at,
        "since_best": since_best,
        "recommendation": recommendation,
    }


CHECKPOINT_INTERVALS = [5, 10, 20, 50, 100]


def print_checkpoint(iteration: int, scores: list[float], best_score: float, keeps: int):
    """Print a trend report at checkpoint intervals."""
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


def get_results_tail(n: int = 10) -> str:
    """Get last n rows of results.tsv for the agent to read."""
    if not RESULTS_TSV.exists():
        return "(no results yet)"
    lines = RESULTS_TSV.read_text().strip().split("\n")
    return "\n".join(lines[:1] + lines[-n:])  # header + last n


def run_loop(
    concept: str,
    target_day: str,
    iterations: int,
    dialogue_model: str,
    judge_model: str,
    agent_model: str,
    qa_only: bool = False,
    judge_every: int = 5,
    auto_stop: bool = False,
):
    """Main experiment loop."""
    sys.path.insert(0, str(RESEARCH_DIR))
    from evaluator import evaluate
    import scripts.simulate_dialogue_week as sdw

    init_results_tsv()
    prompts_path = RESEARCH_DIR / "prompts.py"
    best_prompts_path = RESEARCH_DIR / "best_prompts.py"

    print(f"{'=' * 60}")
    print(f"PROMPT RESEARCH: {concept}")
    print(f"Target day: {target_day}")
    print(f"Iterations: {iterations}")
    print(f"Dialogue model: {dialogue_model}")
    print(f"Judge model: {judge_model}")
    print(f"Agent model: {agent_model}")
    print(f"QA-only: {qa_only}")
    print(f"{'=' * 60}")

    # Step 1: Generate frozen history
    print("\n[1/3] Generating frozen history...")
    frozen_history = generate_frozen_history(concept, target_day, dialogue_model)

    # Step 2: Run baseline with original prompts
    print("\n[2/3] Running baseline...")
    importlib.reload(sdw)  # Reset to original prompts
    baseline_messages = generate_target_day(concept, target_day, dialogue_model, frozen_history)
    baseline_messages_dicts = [m if isinstance(m, dict) else m.__dict__ for m in baseline_messages]

    personas = sdw.load_personas()
    baseline_eval = evaluate(
        messages=baseline_messages_dicts,
        personas=personas,
        concept=concept,
        target_day=target_day,
        frozen_history=frozen_history,
        judge_model=judge_model,
        run_judge=not qa_only,
    )

    best_score = baseline_eval["combined_score"]
    best_qa = baseline_eval["qa_score"]
    best_judge = baseline_eval.get("judge_score")

    # Save baseline
    exp_dir = RUNS_DIR / "exp-0000"
    exp_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prompts_path, exp_dir / "prompts.py")
    (exp_dir / "dialogue.json").write_text(json.dumps(baseline_messages_dicts, indent=2))
    (exp_dir / "scores.json").write_text(json.dumps(baseline_eval, indent=2, default=str))
    shutil.copy2(prompts_path, best_prompts_path)

    append_result(
        exp_id=0,
        target_day=target_day,
        concept=concept,
        qa_score=best_qa,
        judge_score=best_judge,
        combined_score=best_score,
        verdict="BASELINE",
        change_summary="original prompts",
        prompts_hash=file_hash(prompts_path),
    )

    print(f"  Baseline: QA={best_qa}, Judge={best_judge}, Combined={best_score}")
    keeps = 0
    all_scores: list[float] = [best_score]  # Track every score for trend analysis
    stopped_early = False

    # Step 3: Experiment loop
    print(f"\n[3/3] Starting experiment loop ({iterations} iterations)...")
    for i in range(1, iterations + 1):
        print(f"\n--- Experiment {i:04d}/{iterations} ---")

        # Ask agent for a modification (with retry on API errors)
        print("  Asking agent for modification...")
        try:
            new_code = retry_on_api_error(
                ask_agent_for_modification,
                current_prompts_path=best_prompts_path,
                results_tail=get_results_tail(),
                agent_model=agent_model,
                iteration=i,
            )
        except Exception as e:
            print(f"  Agent error: {e}. Skipping.")
            append_result(i, target_day, concept, 0, None, 0, "ERROR", f"agent error: {e}", "n/a")
            continue

        # Write modified prompts
        exp_dir = RUNS_DIR / f"exp-{i:04d}"
        exp_dir.mkdir(parents=True, exist_ok=True)
        modified_path = exp_dir / "prompts.py"
        modified_path.write_text(new_code)

        # Also write to the main prompts.py for monkey-patching
        prompts_path.write_text(new_code)

        # Try to load and patch
        try:
            prompts_mod = load_prompts_module()
            patch_generation_engine(prompts_mod)
        except Exception as e:
            print(f"  Invalid prompts.py: {e}. Discarding.")
            # Restore best
            shutil.copy2(best_prompts_path, prompts_path)
            append_result(i, target_day, concept, 0, None, 0, "CRASH", f"invalid prompts: {e}", "n/a")
            continue

        # Generate target day with modified prompts
        try:
            messages = generate_target_day(concept, target_day, dialogue_model, frozen_history)
            messages_dicts = [m if isinstance(m, dict) else m.__dict__ for m in messages]
        except Exception as e:
            print(f"  Generation error: {e}. Discarding.")
            shutil.copy2(best_prompts_path, prompts_path)
            importlib.reload(sdw)
            append_result(i, target_day, concept, 0, None, 0, "CRASH", f"generation error: {e}", "n/a")
            continue

        # Evaluate
        # Run judge on every Nth keep, or if qa_only is False
        should_judge = not qa_only and (keeps % judge_every == 0 or i <= 3)
        try:
            eval_result = evaluate(
                messages=messages_dicts,
                personas=personas,
                concept=concept,
                target_day=target_day,
                frozen_history=frozen_history,
                judge_model=judge_model,
                run_judge=should_judge,
            )
        except Exception as e:
            print(f"  Evaluation error: {e}. Discarding.")
            shutil.copy2(best_prompts_path, prompts_path)
            append_result(i, target_day, concept, 0, None, 0, "CRASH", f"eval error: {e}", "n/a")
            continue

        score = eval_result["combined_score"]
        qa = eval_result["qa_score"]
        judge = eval_result.get("judge_score")

        # Save artifacts
        (exp_dir / "dialogue.json").write_text(json.dumps(messages_dicts, indent=2, default=str))
        (exp_dir / "scores.json").write_text(json.dumps(eval_result, indent=2, default=str))

        # Extract change summary from agent (first line of diff or generic)
        old_lines = set(best_prompts_path.read_text().split("\n"))
        new_lines = set(new_code.split("\n"))
        added = new_lines - old_lines
        summary = next(iter(added), "unknown change")[:200] if added else "no visible change"

        # Keep or discard
        if score > best_score:
            verdict = "KEEP"
            keeps += 1
            best_score = score
            best_qa = qa
            best_judge = judge
            shutil.copy2(prompts_path, best_prompts_path)
            print(f"  KEEP: QA={qa}, Judge={judge}, Combined={score} (was {best_score - (score - best_score):.1f})")
        else:
            verdict = "DISCARD"
            shutil.copy2(best_prompts_path, prompts_path)
            print(f"  DISCARD: QA={qa}, Judge={judge}, Combined={score} (best={best_score})")

        append_result(i, target_day, concept, qa, judge, score, verdict, summary, file_hash(modified_path))
        all_scores.append(score)

        # Trend checkpoint (always prints) and auto-stop (if enabled)
        checkpoint = print_checkpoint(i, all_scores, best_score, keeps)
        if auto_stop and checkpoint and checkpoint["recommendation"] == "stop":
            print(f"\n  AUTO-STOP: {checkpoint['trend']} detected after {i} iterations.")
            print(f"  No improvement in {checkpoint['since_best']} iterations. Stopping early.")
            stopped_early = True
            break

    # Final trend analysis
    final_trend = analyze_trend(all_scores)

    # Final report
    total_run = len(all_scores) - 1  # exclude baseline
    print(f"\n{'=' * 60}")
    if stopped_early:
        print(f"STOPPED EARLY at iteration {total_run} (trend: {final_trend['trend']})")
    print(f"COMPLETE: {total_run} experiments, {keeps} improvements kept")
    print(f"Final trend: {final_trend['trend']} (slope: {final_trend['slope']:+.2f})")
    print(f"Best score: QA={best_qa}, Judge={best_judge}, Combined={best_score}")
    print(f"Best prompts saved to: {best_prompts_path}")
    print(f"Results log: {RESULTS_TSV}")
    print(f"{'=' * 60}")


def main():
    parser = argparse.ArgumentParser(description="Autoresearch prompt optimization loop")
    parser.add_argument("--concept", required=True, help="Recipe concept to test with")
    parser.add_argument("--target-day", required=True, choices=DAY_ORDER, help="Day to optimize")
    parser.add_argument("--iterations", type=int, default=20, help="Number of experiments to run")
    parser.add_argument("--dialogue-model", default=None, help="Model for dialogue generation (default: from Doppler)")
    parser.add_argument("--judge-model", default="anthropic/claude-sonnet-4-6", help="Model for judging")
    parser.add_argument("--agent-model", default="anthropic/claude-sonnet-4-6", help="Model for suggesting modifications")
    parser.add_argument("--qa-only", action="store_true", help="Skip judge, use QA score only (faster/cheaper)")
    parser.add_argument("--judge-every", type=int, default=5, help="Run judge every N keeps (default: 5)")
    parser.add_argument("--auto-stop", action="store_true", help="Stop early if trend plateaus or declines")
    args = parser.parse_args()

    # Use Doppler env var if no model specified
    dialogue_model = args.dialogue_model
    if not dialogue_model:
        from backend.config import config
        dialogue_model = config.dialogue_model

    run_loop(
        concept=args.concept,
        target_day=args.target_day,
        iterations=args.iterations,
        dialogue_model=dialogue_model,
        auto_stop=args.auto_stop,
        judge_model=args.judge_model,
        agent_model=args.agent_model,
        qa_only=args.qa_only,
        judge_every=args.judge_every,
    )


if __name__ == "__main__":
    main()
