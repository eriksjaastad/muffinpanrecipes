#!/usr/bin/env python3
"""Offline dialogue experiment runner for the conversation lab (card #6492).

See docs/conversation-lab/PROTOCOL.md for the full method this implements;
this docstring covers the mechanics.

Three subcommands:

  baseline <episode_id> [--local]
      Score an already-generated episode's dialogue with
      scripts.conversation_metrics.summarize() per day, alongside whatever
      judge_scores/judge_weakest the production judge (#6861) already
      persisted. Reads a public CDN JSON blob (falling back to the local
      mirror in data/episodes/ with a warning on any fetch failure) or, with
      --local, reads the local mirror directly. Makes ZERO LLM/API calls.

  ab --concept TEXT --stage DAY --runs N --variant PATH
     (--from-episode ID | --recipe-context TEXT) [--target DIM]
     [--max-calls N] [--dry-run] [--no-log]
      The offline controlled experiment: generate N control/variant pairs
      through the *production* call shape
      (scripts.simulate_dialogue_week.run_simulation with
      mode="openai", prompt_style="scene", ticks_per_day=0, plus a
      recipe_context anchor - see .scratch/regen_w36.py and
      backend/admin/cron_routes.py's `_generate_dialogue` for the exact
      shape this mirrors), then judge every pair TWICE with positions
      swapped so the judge's own position bias cancels out. A dimension
      counts as a win only when both orderings agree; otherwise it is a
      tie. --dry-run runs entirely on mode="template" (zero API calls) and
      skips the judge (every verdict is "tie", dry_run=true) - use it to
      exercise the plumbing for free.

      The --variant file is a JSON object mapping
      scripts.simulate_dialogue_week module attribute names to replacement
      values (a string or a dict). Only existing, non-callable module
      attributes may be overridden - see ALLOWED_VARIANT_ATTRS below for the
      documented, useful levers (docs/conversation-lab/PROTOCOL.md's "Where
      the levers live"). The patch applies to the variant arm's generation
      call only and is always restored afterward, even on error.

  calibrate --from-episode ID --stage DAY [--runs 3] [--local]
            [--max-calls 40] [--dry-run]
      Grader sanity check (PROTOCOL.md's "Open hypothesis"): take a real
      transcript and build two degraded copies - shuffled turn order
      (seeded) and speakers rotated by one - then pairwise-judge real vs.
      degraded, positions swapped, same agreement rule as `ab`. Reports the
      judge's preference rate for the real transcript per degradation:
      >= 0.8 is "GRADER OK", otherwise "GRADER SUSPECT".

Cost control: every subcommand accepts --max-calls (ab default 120,
calibrate default 40, per docs/conversation-lab/PROTOCOL.md's cost budget)
and aborts BEFORE the next unit of paid work (a control/variant generation,
or a judge call) once the running total would exceed it, writing whatever
was completed so far as a partial result with "aborted": true. Generation
cost is counted via backend.utils.model_router.get_cost_summary()'s
"total_calls" delta across the call when it moved (a real LLM call
happened); otherwise the transcript's message count is used as the lower
bound (mode="template"/--dry-run, or a test double that never touches the
cost log).

Zero paid API calls happen anywhere in this module's own test suite -
scripts.simulate_dialogue_week.run_simulation and
backend.utils.model_router.generate_judge_response are always monkeypatched
in tests/test_conversation_lab.py.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import scripts.simulate_dialogue_week as simulate_module
from backend.admin.cron_routes import _build_recipe_context
from backend.config import config
from backend.utils import model_router
from scripts.conversation_metrics import summarize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAB_DIR = ROOT / "docs" / "conversation-lab"
DEFAULT_RESULTS_DIR = DEFAULT_LAB_DIR / "results"

# Public, read-only CDN mirror of published/in-progress episode JSON.
_CDN_BASE = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/episodes"
_CDN_TIMEOUT_SECONDS = 8

# The module attribute names worth varying in an `ab` experiment - the
# character-prompt levers documented in docs/conversation-lab/PROTOCOL.md's
# "Where the levers live" (build_system_prompt itself is a function, not a
# string/dict, so it is not a valid variant *value* even though it is where
# these constants get assembled; _JUDGE_SYSTEM_PROMPT lives in
# backend/admin/cron_routes.py, a file this tool never edits or patches -
# PROTOCOL.md deliberately treats a judge-prompt change as a separate,
# rarer kind of experiment, never bundled with a character-prompt change).
ALLOWED_VARIANT_ATTRS: tuple[str, ...] = (
    "_SHARED_CHARACTER_RULES",
    "_REACTION_DIRECTIVE",
    "DAY_STAGE_DIRECTIONS",
    "CHARACTER_DAY_GOALS",
)

# The 8 dimensions the production judge scores (backend/admin/cron_routes.py
# _JUDGE_SYSTEM_PROMPT, ~line 267: 6 rubric dimensions + turn_taking +
# cast_coverage, added in #6861).
JUDGE_DIMENSIONS: tuple[str, ...] = (
    "title_fidelity",
    "arc_resolution",
    "voice_distinctiveness",
    "technical_credibility",
    "natural_progression",
    "promise_delivery",
    "turn_taking",
    "cast_coverage",
)

# A new prompt (this module's own, not backend/admin/cron_routes.py's
# _JUDGE_SYSTEM_PROMPT) asking for a pairwise A/B verdict instead of a
# single-transcript score. Reuses the same 8 dimension *definitions* as
# cron_routes.py's judge (lines ~267-317) so the pairwise judge is scoring
# the same things the production judge scores, just comparatively.
PAIRWISE_JUDGE_SYSTEM_PROMPT = (
    "You are a senior editorial judge for a food content site comparing TWO "
    "candidate dialogue transcripts for the SAME day, recipe concept, and "
    "cast - 6-7 AI characters (Margaret, Steph, Julian, Marcus, Devon, Ria) "
    "collaborating on a muffin-tin recipe. Transcript A and Transcript B are "
    "two independent generations of the identical stage. Decide which one "
    "is better on each dimension below, from a reader's perspective - which "
    "one would you publish?\n\n"
    "DIMENSIONS:\n"
    "- title_fidelity: does the talk stay anchored to the named dish/hero "
    "ingredient, or does it wander into an unrelated tangent?\n"
    "- arc_resolution: does a problem a character raises actually get "
    "resolved, not reframed away or dropped?\n"
    "- voice_distinctiveness: are the characters who spoke separable blind, "
    "each sounding like themselves and nobody else?\n"
    "- technical_credibility: would a real cook believe the food science?\n"
    "- natural_progression: does the conversation build, or do characters "
    "repeat themselves and agree in circles?\n"
    "- promise_delivery: does the dialogue promise something (a technique, "
    "a visual, a result) that the recipe or images plausibly can't "
    "deliver?\n"
    "- turn_taking: do lines respond to the one before them - addressing, "
    "answering, questioning, pushing back - or does each message read as a "
    "polished monologue stacked next to the last, sound bites rather than a "
    "conversation?\n"
    "- cast_coverage: EXPECTED CAST is given in the prompt below. The "
    "transcript where everyone on that list spoke and contributed "
    "something substantive wins; a no-show (especially in a scene about "
    "their job) loses.\n\n"
    "Respond with ONLY a JSON object - no markdown fences, no prose before "
    "or after it:\n"
    '{"winner": "A" or "B" or "tie", "per_dimension": {'
    '"title_fidelity": "A"|"B"|"tie", "arc_resolution": "A"|"B"|"tie", '
    '"voice_distinctiveness": "A"|"B"|"tie", '
    '"technical_credibility": "A"|"B"|"tie", '
    '"natural_progression": "A"|"B"|"tie", "promise_delivery": "A"|"B"|"tie", '
    '"turn_taking": "A"|"B"|"tie", "cast_coverage": "A"|"B"|"tie"}, '
    '"reason": "<one sentence>"}'
)

DECISION_RULE_TEXT = (
    "ship if variant wins >= 65% of pairs on --target and loses no other "
    "dimension by > 50%; N is small, treat as signal"
)


class ConversationLabError(RuntimeError):
    """Raised for lab-specific failures that main() turns into a clean exit."""


@dataclass
class CallBudget:
    """Tracks calls spent against --max-calls; abort BEFORE exceeding it."""

    max_calls: int
    used: int = 0

    def would_exceed(self, upcoming: int = 1) -> bool:
        return self.used + upcoming > self.max_calls

    def record(self, amount: int) -> None:
        self.used += amount


# ---------------------------------------------------------------------------
# Episode loading (baseline, calibrate, ab --from-episode)
#
# Deliberately small and private per the card's instructions - this does NOT
# import scripts/review_episode.py (owned by another implementer working the
# same card).
# ---------------------------------------------------------------------------


def _load_episode(episode_id: str, local: bool) -> dict[str, Any]:
    """Load an episode by id: CDN by default, local mirror on request or fallback."""
    if local:
        return _load_episode_local(episode_id)
    try:
        return _load_episode_cdn(episode_id)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            f"WARNING: STALE MIRROR - CDN fetch for {episode_id!r} failed "
            f"({type(exc).__name__}: {exc}); falling back to "
            f"data/episodes/{episode_id}.json",
            file=sys.stderr,
        )
        return _load_episode_local(episode_id)


def _load_episode_cdn(episode_id: str) -> dict[str, Any]:
    cache_buster = int(time.time() * 1000)
    url = f"{_CDN_BASE}/{episode_id}.json?cb={cache_buster}"
    request = urllib.request.Request(url, headers={"User-Agent": "conversation-lab/1.0"})
    with urllib.request.urlopen(request, timeout=_CDN_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"CDN episode payload for {episode_id!r} was not a JSON object")
    data["_lab_source"] = "cdn"
    return data


def _load_episode_local(episode_id: str) -> dict[str, Any]:
    path = ROOT / "data" / "episodes" / f"{episode_id}.json"
    if not path.exists():
        raise SystemExit(
            f"conversation_lab: no local mirror at {path} - cannot load episode {episode_id!r}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_lab_source"] = "local"
    return data


def _judge_info_for_stage(episode: dict[str, Any], stage_data: dict[str, Any], stage: str) -> dict[str, Any]:
    """Read persisted judge output for one stage.

    #6861 persists judge_scores/judge_weakest/judge_reason at the episode
    top level, keyed by stage (backend/admin/cron_routes.py's
    `_record_judge_meta`). Also checks the per-stage dict first in case a
    future shape nests it there instead - "stage dict or
    episode['judge_scores'][stage]" per this module's spec.
    """
    scores = stage_data.get("judge_scores")
    weakest = stage_data.get("judge_weakest")
    reason = stage_data.get("judge_reason")
    if scores is None:
        scores = (episode.get("judge_scores") or {}).get(stage)
    if weakest is None:
        weakest = (episode.get("judge_weakest") or {}).get(stage)
    if reason is None:
        reason = (episode.get("judge_reason") or {}).get(stage)
    return {
        "scores": scores,
        "weakest": weakest,
        "reason": reason,
        "legacy_verdict": stage_data.get("judge_verdict"),
    }


# ---------------------------------------------------------------------------
# Results / experiment-log paths
# ---------------------------------------------------------------------------


def _results_dir(args: argparse.Namespace) -> Path:
    raw = getattr(args, "results_dir", None)
    return Path(raw) if raw else DEFAULT_RESULTS_DIR


def _experiments_log_path(args: argparse.Namespace) -> Path:
    """EXPERIMENTS.md lives next to the results dir (docs/conversation-lab/
    by default) so a --results-dir override redirects both, keeping tests
    out of docs/ entirely."""
    return _results_dir(args).parent / "EXPERIMENTS.md"


def _write_json_result(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


# ---------------------------------------------------------------------------
# Variant mechanism
# ---------------------------------------------------------------------------


def _load_variant_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"conversation_lab: variant file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"conversation_lab: variant file is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict) or not data:
        raise SystemExit(f"conversation_lab: variant file must be a non-empty JSON object: {path}")
    return data


def _clear_prompt_cache(module: Any) -> None:
    """build_system_prompt caches per-character prompts by name only
    (scripts/simulate_dialogue_week.py's `_system_prompt_cache`, keyed
    purely on persona name), so a lever change to e.g.
    _SHARED_CHARACTER_RULES is silently ignored for any character whose
    prompt was already built this process, unless the cache is cleared
    both when applying a variant and when restoring the control."""
    cache = getattr(module, "_system_prompt_cache", None)
    if isinstance(cache, dict):
        cache.clear()


def _apply_variant(module: Any, variant: dict[str, Any]) -> dict[str, Any]:
    """setattr the variant values onto `module`; return originals for restore.

    Only overrides attributes that already exist on the module (never
    creates new ones) and refuses anything callable - variant values are
    always strings or dicts (see ALLOWED_VARIANT_ATTRS), never a function
    replacement, which the simple save/restore contract here cannot safely
    reason about.
    """
    original: dict[str, Any] = {}
    for name, value in variant.items():
        if not hasattr(module, name):
            raise ConversationLabError(
                f"variant file names unknown attribute {name!r} on {module.__name__} - "
                f"allowed levers: {', '.join(ALLOWED_VARIANT_ATTRS)}"
            )
        current = getattr(module, name)
        if callable(current):
            raise ConversationLabError(
                f"refusing to patch {name!r} - it is a function, not a string/dict "
                "constant this variant mechanism can safely restore"
            )
        original[name] = current
        setattr(module, name, value)
    _clear_prompt_cache(module)
    return original


def _restore_variant(module: Any, original: dict[str, Any]) -> None:
    for name, value in original.items():
        setattr(module, name, value)
    _clear_prompt_cache(module)


# ---------------------------------------------------------------------------
# Generation (production call shape)
# ---------------------------------------------------------------------------


def _run_arm(
    concept: str,
    stage: str,
    run_index: int,
    recipe_context: str | None,
    mode: str,
    default_model: str,
) -> dict[str, Any]:
    """Call run_simulation with the exact production call shape.

    Mirrors backend/admin/cron_routes.py's `_generate_dialogue` (~line
    228) and .scratch/regen_w36.py: mode="openai", prompt_style="scene",
    ticks_per_day=0, plus a recipe_context anchor. --dry-run substitutes
    mode="template" for a zero-API-call plumbing check.
    """
    return simulate_module.run_simulation(
        concept=concept,
        default_model=default_model,
        run_index=run_index,
        stage_only=stage,
        injected_event=None,
        ticks_per_day=0,
        mode=mode,
        prompt_style="scene",
        character_models=None,
        image_paths=[],
        photography_context=None,
        recipe_context=recipe_context,
        initial_recent_lines=None,
    )


def _run_arm_and_count(
    concept: str,
    stage: str,
    run_index: int,
    recipe_context: str | None,
    mode: str,
    default_model: str,
) -> tuple[dict[str, Any], int]:
    """Run one arm and estimate its call cost.

    Prefers the actual delta in
    backend.utils.model_router.get_cost_summary()['total_calls'] (a real
    LLM call happened); falls back to the transcript's message count as a
    lower bound when the cost log did not move (mode="template",
    --dry-run, or a monkeypatched run_simulation in tests).
    """
    try:
        before = model_router.get_cost_summary().get("total_calls", 0)
    except Exception:
        before = 0
    result = _run_arm(concept, stage, run_index, recipe_context, mode, default_model)
    try:
        after = model_router.get_cost_summary().get("total_calls", 0)
    except Exception:
        after = 0
    delta = after - before
    message_count = len(result.get("messages", []))
    calls = delta if delta > 0 else message_count
    return result, calls


# ---------------------------------------------------------------------------
# Pairwise judge
# ---------------------------------------------------------------------------


def _format_transcript(messages: list[dict[str, Any]]) -> str:
    lines = []
    for m in messages:
        name = (m.get("character") or "?").split()[0]
        lines.append(f"{name}: {' '.join((m.get('message') or '').split())}")
    return "\n".join(lines)


def _build_pairwise_prompt(
    concept: str,
    stage: str,
    recipe_context: str | None,
    expected_cast: list[str],
    first_messages: list[dict[str, Any]],
    second_messages: list[dict[str, Any]],
) -> str:
    recipe_section = f"{recipe_context}\n" if recipe_context else ""
    roster_line = f"Expected cast for {stage}: {', '.join(expected_cast)}\n"
    return (
        f"Recipe concept: {concept}\n"
        f"{recipe_section}"
        f"{roster_line}"
        f"TRANSCRIPT A:\n{_format_transcript(first_messages)}\n\n"
        f"TRANSCRIPT B:\n{_format_transcript(second_messages)}\n\n"
        "Score this pair and return the JSON verdict described in your instructions."
    )


def _parse_judge_json(raw: str) -> dict[str, Any] | None:
    """Tolerant slice-and-parse: first '{' to last '}'.

    Same approach as backend/admin/cron_routes.py's `_parse_judge_json`
    (~line 313) - judge models occasionally wrap JSON in a markdown fence
    or add a sentence of preamble despite being told not to. Reimplemented
    locally (not imported) so this module does not depend on a private
    helper in a file it is not allowed to edit.
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _judge_orientation(
    judge_model: str,
    concept: str,
    stage: str,
    recipe_context: str | None,
    expected_cast: list[str],
    first_arm: str,
    first_messages: list[dict[str, Any]],
    second_arm: str,
    second_messages: list[dict[str, Any]],
) -> dict[str, str]:
    """Judge once with A=first_arm, B=second_arm; map the A/B verdict back to arm labels."""
    prompt = _build_pairwise_prompt(concept, stage, recipe_context, expected_cast, first_messages, second_messages)
    raw = model_router.generate_judge_response(
        prompt=prompt,
        system_prompt=PAIRWISE_JUDGE_SYSTEM_PROMPT,
        model=judge_model,
        temperature=0.2,
    )
    parsed = _parse_judge_json(raw)
    if parsed is None:
        raise ConversationLabError(f"pairwise judge returned unparseable output: {raw[:200]!r}")

    mapping = {"A": first_arm, "B": second_arm, "tie": "tie"}
    result: dict[str, str] = {"overall": mapping.get(parsed.get("winner", "tie"), "tie")}
    per_dimension = parsed.get("per_dimension") or {}
    for dim in JUDGE_DIMENSIONS:
        result[dim] = mapping.get(per_dimension.get(dim, "tie"), "tie")
    result["reason"] = str(parsed.get("reason", ""))
    return result


def _combine_orientations(first: dict[str, str], second: dict[str, str]) -> dict[str, str]:
    """A dimension (or overall) wins only when both position-swapped
    orderings pick the same arm; otherwise it is a tie."""
    combined: dict[str, str] = {}
    for key in ("overall", *JUDGE_DIMENSIONS):
        v1, v2 = first.get(key, "tie"), second.get(key, "tie")
        combined[key] = v1 if v1 == v2 else "tie"
    return combined


def _dry_run_combined() -> dict[str, str]:
    return {"overall": "tie", **{dim: "tie" for dim in JUDGE_DIMENSIONS}}


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def cmd_baseline(args: argparse.Namespace) -> None:
    episode = _load_episode(args.episode_id, local=args.local)
    stages = episode.get("stages") or {}
    concept = episode.get("concept", "")

    day_reports: dict[str, Any] = {}
    for day in simulate_module.DAY_ORDER:
        stage_data = stages.get(day)
        if not stage_data:
            continue
        dialogue = stage_data.get("dialogue") or []
        if not dialogue:
            continue
        expected_cast = simulate_module.participants_for_day(day)
        day_reports[day] = {
            "expected_cast": expected_cast,
            "message_count": len(dialogue),
            "summary": summarize(dialogue, expected_cast, concept=concept, day=day),
            "judge": _judge_info_for_stage(episode, stage_data, day),
        }

    result_path = _results_dir(args) / f"{args.episode_id}-baseline.json"
    report = {
        "command": "baseline",
        "episode_id": args.episode_id,
        "source": episode.get("_lab_source", "unknown"),
        "concept": concept,
        "days": day_reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_file": str(result_path),
    }
    _write_json_result(result_path, report)
    _print_baseline_table(report)


def _print_baseline_table(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab baseline: {report['episode_id']} ({report['source']}) ===")
    print(f"concept: {report['concept']}")
    if not report["days"]:
        print("(no days with dialogue found)")
    else:
        print(f"{'day':<10}{'msgs':>6}{'cast':>7}{'adj':>7}{'qa':>7}{'legacy':>8}  weakest")
        for day, info in report["days"].items():
            s = info["summary"]
            judge = info["judge"]
            weakest = judge.get("weakest") or []
            weakest_str = ", ".join(weakest) if weakest else ("n/a" if judge.get("scores") is None else "-")
            print(
                f"{day:<10}{info['message_count']:>6}{s['cast_ratio']:>7.2f}"
                f"{s['adjacency_mean_overlap']:>7.2f}{s['qa_rate']:>7.2f}"
                f"{s['legacy_score']:>8}  {weakest_str}"
            )
    print(f"\nresults written to: {report['results_file']}")


# ---------------------------------------------------------------------------
# ab
# ---------------------------------------------------------------------------


def _numeric_keys(d: dict[str, Any]) -> list[str]:
    return [k for k, v in d.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _resolve_recipe_context(args: argparse.Namespace) -> str | None:
    if bool(args.from_episode) == bool(args.recipe_context):
        raise SystemExit("conversation_lab ab: pass exactly one of --from-episode or --recipe-context")
    if args.recipe_context:
        return args.recipe_context

    episode = _load_episode(args.from_episode, local=args.local)
    stage_data = (episode.get("stages") or {}).get(args.stage) or {}
    recipe_data = stage_data.get("recipe_data") or (episode.get("stages") or {}).get("monday", {}).get("recipe_data")
    recipe_context = _build_recipe_context(recipe_data)
    if not recipe_context:
        raise SystemExit(
            f"conversation_lab ab: episode {args.from_episode!r} has no usable recipe_data "
            f"for stage {args.stage!r} - pass --recipe-context instead"
        )
    return recipe_context


def _resolve_models(dry_run: bool) -> tuple[str, str, str]:
    """Return (mode, default_model, judge_model).

    Fails loud (never a silent fallback) when the real models don't
    resolve and --dry-run was not passed - config.dialogue_model raises
    RuntimeError itself when DIALOGUE_MODEL is unset (backend/config.py).
    """
    if dry_run:
        return "template", "template", "template"
    try:
        return "openai", config.dialogue_model, config.judge_model
    except RuntimeError as exc:
        raise SystemExit(
            f"conversation_lab ab: {exc}\n"
            "Run under `doppler run -- uv run ...` so DIALOGUE_MODEL/JUDGE_MODEL "
            "resolve, or pass --dry-run for a zero-cost plumbing check."
        ) from exc


def cmd_ab(args: argparse.Namespace) -> None:
    variant_path = Path(args.variant)
    variant = _load_variant_file(variant_path)
    recipe_context = _resolve_recipe_context(args)
    mode, default_model, judge_model = _resolve_models(args.dry_run)
    expected_cast = simulate_module.participants_for_day(args.stage)

    budget = CallBudget(max_calls=args.max_calls)
    pairs: list[dict[str, Any]] = []
    aborted = False
    restore_pending: dict[str, Any] | None = None

    try:
        for run_index in range(1, args.runs + 1):
            if budget.would_exceed(1):
                aborted = True
                break

            control_result, control_calls = _run_arm_and_count(
                args.concept, args.stage, run_index, recipe_context, mode, default_model
            )
            budget.record(control_calls)

            if budget.would_exceed(1):
                aborted = True
                break

            restore_pending = _apply_variant(simulate_module, variant)
            try:
                variant_result, variant_calls = _run_arm_and_count(
                    args.concept, args.stage, run_index, recipe_context, mode, default_model
                )
            finally:
                _restore_variant(simulate_module, restore_pending)
                restore_pending = None
            budget.record(variant_calls)

            control_messages = control_result.get("messages", [])
            variant_messages = variant_result.get("messages", [])

            if args.dry_run:
                combined = _dry_run_combined()
            else:
                if budget.would_exceed(1):
                    aborted = True
                    break
                first = _judge_orientation(
                    judge_model, args.concept, args.stage, recipe_context, expected_cast,
                    "control", control_messages, "variant", variant_messages,
                )
                budget.record(1)

                if budget.would_exceed(1):
                    aborted = True
                    break
                second = _judge_orientation(
                    judge_model, args.concept, args.stage, recipe_context, expected_cast,
                    "variant", variant_messages, "control", control_messages,
                )
                budget.record(1)
                combined = _combine_orientations(first, second)

            pairs.append({
                "run_index": run_index,
                "control_summary": summarize(control_messages, expected_cast, concept=args.concept, day=args.stage),
                "variant_summary": summarize(variant_messages, expected_cast, concept=args.concept, day=args.stage),
                "judge": combined,
                "dry_run": bool(args.dry_run),
            })
    finally:
        if restore_pending is not None:
            _restore_variant(simulate_module, restore_pending)

    result_path = _results_dir(args) / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-ab-"
        f"{_slugify(args.concept)}-{variant_path.stem}.json"
    )
    report = _build_ab_report(args, variant_path, variant, pairs, aborted, budget, result_path)
    _write_json_result(result_path, report)
    if not args.no_log:
        _append_experiments_row(args, report, variant, result_path)
    _print_ab_report(report)


def _build_ab_report(
    args: argparse.Namespace,
    variant_path: Path,
    variant: dict[str, Any],
    pairs: list[dict[str, Any]],
    aborted: bool,
    budget: CallBudget,
    result_path: Path,
) -> dict[str, Any]:
    n = len(pairs)
    overall_counts = Counter(p["judge"]["overall"] for p in pairs)
    per_dimension_counts = {dim: Counter(p["judge"][dim] for p in pairs) for dim in JUDGE_DIMENSIONS}

    metric_deltas: dict[str, float] = {}
    if pairs:
        for key in _numeric_keys(pairs[0]["control_summary"]):
            deltas = [p["variant_summary"].get(key, 0) - p["control_summary"].get(key, 0) for p in pairs]
            metric_deltas[key] = round(mean(deltas), 4)

    target = args.target
    target_wins = (
        overall_counts.get("variant", 0)
        if target == "overall"
        else per_dimension_counts.get(target, Counter()).get("variant", 0)
    )
    target_win_rate = round(target_wins / n, 4) if n else 0.0

    other_dims = [d for d in JUDGE_DIMENSIONS if d != target]
    worst_other_loss_rate = 0.0
    losing_dims: list[str] = []
    for dim in other_dims:
        counts = per_dimension_counts.get(dim, Counter())
        control_wins = counts.get("control", 0)
        if control_wins > 0:
            losing_dims.append(dim)
        worst_other_loss_rate = max(worst_other_loss_rate, (control_wins / n) if n else 0.0)

    try:
        cost_summary = model_router.get_cost_summary()
    except Exception:
        cost_summary = {}

    meets_decision_rule = (
        not args.dry_run and n > 0 and target_win_rate >= 0.65 and worst_other_loss_rate <= 0.50
    )

    return {
        "command": "ab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "concept": args.concept,
        "stage": args.stage,
        "variant_file": str(variant_path),
        "variant_name": variant_path.stem,
        "variant_keys": sorted(variant.keys()),
        "requested_runs": args.runs,
        "completed_pairs": n,
        "aborted": aborted,
        "max_calls": args.max_calls,
        "calls_used": budget.used,
        "dry_run": bool(args.dry_run),
        "target_dimension": target,
        "overall_counts": dict(overall_counts),
        "per_dimension_counts": {d: dict(c) for d, c in per_dimension_counts.items()},
        "target_win_rate": target_win_rate,
        "worst_other_dimension_loss_rate": round(worst_other_loss_rate, 4),
        "dimensions_losing_to_control": losing_dims,
        "meets_decision_rule": meets_decision_rule,
        "decision_rule": DECISION_RULE_TEXT,
        "metric_deltas": metric_deltas,
        "cost_summary": cost_summary,
        "pairs": pairs,
        "results_file": str(result_path),
    }


def _print_ab_report(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab ab: {report['concept']} / {report['stage']} ===")
    print(f"variant: {report['variant_name']} ({report['variant_file']}) keys={report['variant_keys']}")
    abort_note = "  [ABORTED: max-calls hit]" if report["aborted"] else ""
    print(f"pairs completed: {report['completed_pairs']} / requested {report['requested_runs']}{abort_note}")
    print(f"calls used: {report['calls_used']} / max {report['max_calls']}  dry_run={report['dry_run']}")
    print(f"\noverall: {dict(report['overall_counts'])}")
    print(f"{'dimension':<24}{'variant':>8}{'tie':>8}{'control':>8}")
    for dim in JUDGE_DIMENSIONS:
        counts = report["per_dimension_counts"].get(dim, {})
        print(f"{dim:<24}{counts.get('variant', 0):>8}{counts.get('tie', 0):>8}{counts.get('control', 0):>8}")
    print(f"\ntarget ({report['target_dimension']}) win rate: {report['target_win_rate']:.2%}")
    print(f"worst other-dimension loss rate: {report['worst_other_dimension_loss_rate']:.2%}")
    print(f"dimensions losing to control: {report['dimensions_losing_to_control'] or 'none'}")
    print("\nmean metric deltas (variant - control):")
    for key, value in sorted(report["metric_deltas"].items()):
        print(f"  {key:<32}{value:+.4f}")
    print(f"\nDECISION RULE (informational, not enforced): {report['decision_rule']}")
    print(f"cost summary: {report['cost_summary']}")
    print(f"\nresults written to: {report['results_file']}")


_EXPERIMENTS_HEADER = (
    "# Conversation Lab Experiments\n\n"
    "| Date | Experiment ID | Lever (one) | Target dimension(s) | N | "
    "Wins/Ties/Losses on target | Other dimensions lost | Decision | Shipped PR | "
    "Live confirmation week + scores |\n"
    "|------|----------------|--------------|----------------------|---|"
    "------------------------------|------------------------|-----------|-------------|"
    "-----------------------------------|\n"
)


def _target_win_tie_loss(report: dict[str, Any]) -> tuple[int, int, int]:
    target = report["target_dimension"]
    counts = report["overall_counts"] if target == "overall" else report["per_dimension_counts"].get(target, {})
    return counts.get("variant", 0), counts.get("tie", 0), counts.get("control", 0)


def _append_experiments_row(
    args: argparse.Namespace,
    report: dict[str, Any],
    variant: dict[str, Any],
    result_path: Path,
) -> None:
    """Append one row to EXPERIMENTS.md. Append-only: an existing file (its
    initial content authored by another implementer working this same
    card) is never rewritten or validated - only ever appended to, per this
    module's spec. A header is written only when the file does not exist
    at all."""
    log_path = _experiments_log_path(args)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()

    wins, ties, losses = _target_win_tie_loss(report)
    lever = "+".join(sorted(variant.keys())) or Path(report["variant_file"]).stem
    losing = ", ".join(report["dimensions_losing_to_control"]) or "none"

    if report["dry_run"]:
        decision = "DRY RUN - no signal"
    elif report["completed_pairs"] == 0:
        decision = "no pairs completed"
    elif report["meets_decision_rule"]:
        decision = f"signal: SHIP ({report['target_win_rate']:.0%} on {report['target_dimension']})"
    else:
        decision = f"signal: HOLD ({report['target_win_rate']:.0%} on {report['target_dimension']})"

    row = (
        f"| {report['generated_at']} | {result_path.stem} | {lever} | "
        f"{report['target_dimension']} | {report['completed_pairs']} | "
        f"{wins}/{ties}/{losses} | {losing} | {decision} | TBD | TBD |\n"
    )

    with log_path.open("a", encoding="utf-8") as handle:
        if is_new:
            handle.write(_EXPERIMENTS_HEADER)
        handle.write(row)


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


def _shuffle_turns(dialogue: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Degrade by shuffling turn order with a seeded RNG (seed = run index)."""
    rng = random.Random(seed)
    shuffled = list(dialogue)
    rng.shuffle(shuffled)
    return shuffled


def _rotate_speakers(dialogue: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Degrade by rotating which character label is attached to each turn
    by one position ("same words, wrong mouth").

    `seed` is accepted for signature symmetry with `_shuffle_turns` (and
    calibrate's uniform per-run degradation call), even though the
    rotation itself has no randomness - it is always by exactly one
    position, so the result is identical for every seed.
    """
    del seed
    if len(dialogue) < 2:
        return [dict(m) for m in dialogue]
    characters = [m.get("character") for m in dialogue]
    rotated = characters[1:] + characters[:1]
    return [dict(m, character=c) for m, c in zip(dialogue, rotated)]


_DEGRADATIONS: tuple[tuple[str, Any], ...] = (
    ("shuffled_order", _shuffle_turns),
    ("rotated_speakers", _rotate_speakers),
)


def cmd_calibrate(args: argparse.Namespace) -> None:
    episode = _load_episode(args.from_episode, local=args.local)
    stage_data = (episode.get("stages") or {}).get(args.stage) or {}
    dialogue = stage_data.get("dialogue") or []
    if not dialogue:
        raise SystemExit(
            f"conversation_lab calibrate: no dialogue for stage={args.stage!r} in "
            f"episode {args.from_episode!r}"
        )

    concept = episode.get("concept", "")
    expected_cast = simulate_module.participants_for_day(args.stage)
    recipe_context = _build_recipe_context(stage_data.get("recipe_data"))

    if args.dry_run:
        judge_model = "template"
    else:
        try:
            judge_model = config.judge_model
        except RuntimeError as exc:
            raise SystemExit(f"conversation_lab calibrate: {exc} Pass --dry-run instead.") from exc

    budget = CallBudget(max_calls=args.max_calls)
    aborted = False
    degradation_reports: dict[str, Any] = {}

    for name, degrade in _DEGRADATIONS:
        pair_records: list[dict[str, Any]] = []
        wins = 0
        attempted = 0
        for run_index in range(1, args.runs + 1):
            if budget.would_exceed(1):
                aborted = True
                break
            degraded = degrade(dialogue, run_index)
            attempted += 1

            if args.dry_run:
                combined = _dry_run_combined()
            else:
                if budget.would_exceed(1):
                    aborted = True
                    break
                first = _judge_orientation(
                    judge_model, concept, args.stage, recipe_context, expected_cast,
                    "real", dialogue, "degraded", degraded,
                )
                budget.record(1)

                if budget.would_exceed(1):
                    aborted = True
                    break
                second = _judge_orientation(
                    judge_model, concept, args.stage, recipe_context, expected_cast,
                    "degraded", degraded, "real", dialogue,
                )
                budget.record(1)
                combined = _combine_orientations(first, second)

            if combined["overall"] == "real":
                wins += 1
            pair_records.append({"run_index": run_index, "judge": combined})

        preference_rate = round(wins / attempted, 4) if attempted else 0.0
        degradation_reports[name] = {
            "attempted": attempted,
            "real_preference_rate": preference_rate,
            "verdict": "GRADER OK" if preference_rate >= 0.8 else "GRADER SUSPECT",
            "pairs": pair_records,
        }
        if aborted:
            break

    result_path = _results_dir(args) / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-calibrate-"
        f"{args.from_episode}-{args.stage}.json"
    )
    report = {
        "command": "calibrate",
        "episode_id": args.from_episode,
        "stage": args.stage,
        "concept": concept,
        "dry_run": bool(args.dry_run),
        "aborted": aborted,
        "max_calls": args.max_calls,
        "calls_used": budget.used,
        "degradations": degradation_reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_file": str(result_path),
    }
    _write_json_result(result_path, report)
    _print_calibrate_report(report)


def _print_calibrate_report(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab calibrate: {report['episode_id']} / {report['stage']} ===")
    abort_note = "  [ABORTED: max-calls hit]" if report["aborted"] else ""
    print(f"calls used: {report['calls_used']} / max {report['max_calls']}{abort_note}  dry_run={report['dry_run']}")
    for name, info in report["degradations"].items():
        print(
            f"  {name:<18} attempted={info['attempted']:<4} "
            f"real_preference_rate={info['real_preference_rate']:.2%}  {info['verdict']}"
        )
    print(f"\nresults written to: {report['results_file']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conversation_lab",
        description=(
            "Offline dialogue experiment runner for card #6492 (the "
            "conversation lab). See docs/conversation-lab/PROTOCOL.md for "
            "the full method."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    baseline = sub.add_parser(
        "baseline",
        help="Score a week's already-generated dialogue. Zero paid calls.",
        description=(
            "Load an episode (CDN by default, local mirror on --local or on "
            "CDN failure), run scripts.conversation_metrics.summarize() per "
            "day, and report it alongside the persisted judge_scores/"
            "judge_weakest. Makes zero LLM/API calls."
        ),
    )
    baseline.add_argument("episode_id", help="Episode id, e.g. 2026-W36")
    baseline.add_argument("--local", action="store_true", help="Skip the CDN; read data/episodes/<id>.json directly")
    baseline.add_argument(
        "--results-dir", default=None,
        help="Override the results directory (default: docs/conversation-lab/results)",
    )

    ab = sub.add_parser(
        "ab",
        help="Offline blind, position-swapped A/B between a control and a variant prompt lever.",
        description=(
            "Generate N control/variant pairs through the production call "
            "shape (mode='openai', prompt_style='scene', ticks_per_day=0) "
            "and judge each pair twice with positions swapped. --variant is "
            "a JSON object mapping scripts.simulate_dialogue_week module "
            "attribute names to replacement values (a string or a dict) - "
            "allowed levers: " + ", ".join(ALLOWED_VARIANT_ATTRS) + ". "
            "--dry-run runs mode='template' (zero API calls) and skips the "
            "judge (every verdict is 'tie', dry_run=true)."
        ),
    )
    ab.add_argument("--concept", required=True)
    ab.add_argument("--stage", required=True, choices=simulate_module.DAY_ORDER)
    ab.add_argument("--runs", type=int, required=True)
    ab.add_argument("--variant", required=True, type=Path, help="JSON file: {attribute_name: replacement_value}")
    ab.add_argument("--from-episode", default=None, help="Build --recipe-context from this episode's recipe_data")
    ab.add_argument(
        "--recipe-context", default=None,
        help="One-line recipe anchor, verbatim (mutually exclusive with --from-episode)",
    )
    ab.add_argument("--local", action="store_true", help="With --from-episode, skip the CDN; read the local mirror")
    ab.add_argument("--target", default="turn_taking", choices=(*JUDGE_DIMENSIONS, "overall"))
    ab.add_argument("--max-calls", type=int, default=120)
    ab.add_argument("--dry-run", action="store_true")
    ab.add_argument("--no-log", action="store_true", help="Do not append a row to docs/conversation-lab/EXPERIMENTS.md")
    ab.add_argument("--results-dir", default=None)

    calibrate = sub.add_parser(
        "calibrate",
        help="Sanity-check the judge itself against known-degraded transcripts.",
        description=(
            "Take a real transcript for --stage and build two degraded "
            "copies (shuffled turn order, speakers rotated by one), "
            "pairwise-judge real vs. degraded with positions swapped, and "
            "report the judge's preference rate for the real transcript "
            "per degradation - >= 0.8 is 'GRADER OK'."
        ),
    )
    calibrate.add_argument("--from-episode", required=True)
    calibrate.add_argument("--stage", required=True, choices=simulate_module.DAY_ORDER)
    calibrate.add_argument("--runs", type=int, default=3)
    calibrate.add_argument("--local", action="store_true")
    calibrate.add_argument("--max-calls", type=int, default=40)
    calibrate.add_argument("--dry-run", action="store_true")
    calibrate.add_argument("--results-dir", default=None)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "baseline":
            cmd_baseline(args)
        elif args.command == "ab":
            cmd_ab(args)
        elif args.command == "calibrate":
            cmd_calibrate(args)
        else:  # pragma: no cover - argparse enforces valid choices
            raise SystemExit(f"conversation_lab: unknown command {args.command!r}")
    except ConversationLabError as exc:
        raise SystemExit(f"conversation_lab {args.command}: {exc}") from exc


if __name__ == "__main__":
    main()
