#!/usr/bin/env python3
"""Offline dialogue experiment runner for the conversation lab (card #6492).

See docs/conversation-lab/PROTOCOL.md for the full method this implements;
this docstring covers the mechanics.

Four subcommands:

  baseline <episode_id> [--local]
      Score an already-generated episode's dialogue with
      scripts.conversation_metrics.summarize() per day, alongside whatever
      judge_scores/judge_weakest the production judge (#6861) already
      persisted. Reads a public CDN JSON blob (falling back to the local
      mirror in data/episodes/ with a warning on any fetch failure) or, with
      --local, reads the local mirror directly. The concept reported is the
      real recipe title (backend.utils.episode_integrity._recipe_title),
      not the episode's top-level `concept` field, which stays the
      "Weekly Muffin Pan Recipe" placeholder on any week whose concept
      picker never ran even after the baker has picked a real dish. Makes
      ZERO LLM/API calls.

  ab --stage DAY --variant PATH [--concept TEXT --runs N
     (--from-episode ID | --recipe-context TEXT)] [--target DIM]
     [--max-calls N] [--max-cost USD] [--dry-run] [--no-log]
     [--experiments-log PATH]
  ab --stage DAY --variant PATH --testbed [PATH] [--runs N] [--target DIM]
     [--max-calls N] [--max-cost USD] [--dry-run] [--no-log]
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

      --testbed replaces a single --concept/--recipe-context run with the
      frozen five-scenario panel in docs/conversation-lab/testbed.json (or
      a path you pass), running --runs pairs (default 3 in testbed mode)
      per scenario and reporting both a per-scenario breakdown and an
      aggregate across every scenario's pairs - the fixed panel keeps
      experiments comparable month to month instead of drifting with
      whatever episode happens to be in progress. --concept,
      --recipe-context, and --from-episode are forbidden with --testbed;
      each scenario already carries its own.

      Every judged dimension - the production 8 plus two lab-only ones,
      emotional_range and register_naturalness (see
      LAB_ONLY_JUDGE_DIMENSIONS) - is a valid --target and appears in the
      per-dimension table, the aggregation, and the JSON report.

      The --variant file is a JSON object mapping
      scripts.simulate_dialogue_week module attribute names to replacement
      values (a string or a dict). Only existing, non-callable module
      attributes may be overridden - see ALLOWED_VARIANT_ATTRS below for the
      documented, useful levers (docs/conversation-lab/PROTOCOL.md's "Where
      the levers live"). The patch applies to the variant arm's generation
      call only and is always restored afterward, even on error.

      --experiments-log overrides where the one-row-per-run log is
      inserted into the "## Experiments" table (default
      docs/conversation-lab/EXPERIMENTS.md, created with a fresh heading
      and table when absent, otherwise inserted as the table's last row
      regardless of what sections follow it in the file) - independent of
      --results-dir, never derived from it.

  ab --sweep DIR [--testbed PATH] --stage DAY [--runs N] [--target DIM]
     [--max-cost USD] [--dry-run] [--no-log] [--experiments-log PATH]
     [--results-dir PATH]
      Rank MANY variants against ONE shared control in a single run.
      Every *.json file directly under DIR is one variant, same format as
      --variant, keyed by filename stem. Generates the control transcript
      for every (scenario, run) pair in the testbed panel (--testbed, or
      the default frozen panel when omitted) EXACTLY ONCE, then reuses it
      against every variant - this is what makes running ten experiments
      in a week affordable, instead of paying for a fresh control per
      variant. Each variant is pairwise-judged against the shared control
      with the same position-swap rule as a single --variant run.

      --max-cost applies PER VARIANT, not to the sweep as a whole - Erik's
      $5 cap (DEFAULT_MAX_COST_USD) is per experiment, and each variant in
      a sweep is one experiment; a variant's own spend is measured
      relative to the running total right before that variant started, so
      an earlier variant's spend never eats into a later variant's
      allowance. The shared control's own cost is reported separately
      (`control_cost` in the result JSON) and charged to the sweep as a
      whole, never to any one variant.

      Reports a ranking table across variants (sorted by wins on
      --target): wins/ties/losses on --target, the overall win count,
      per-area metric deltas (variant mean - control mean) via
      scripts.conversation_metrics.AREA_METRICS when present (falling back
      to the flat metric deltas otherwise), the top 10 phrases recurring
      across each arm's own transcripts via
      scripts.conversation_heatmap.phrase_heat() when present - so a
      variant that kills a boilerplate phrase shows it directly - and paid
      calls/cost per variant. Both are getattr-guarded: this sweep works
      whether or not those two names exist in a given revision of
      scripts/conversation_metrics.py / scripts/conversation_heatmap.py
      (both owned by another implementer on this same card).

      Writes ONE result JSON covering every variant, every pair, and both
      transcripts per pair (so `pairs --from` can review any variant's
      arm with a `--variant-name` selector), and ONE EXPERIMENTS.md row
      per variant (id = the result file's own stamp plus the variant name)
      unless --no-log. Forbids --concept, --recipe-context, and
      --from-episode - scenarios always come from the testbed panel.
      Partial-result-on-exception, exactly like a single --variant `ab`
      run (see below) - any exception mid-sweep writes whatever variants/
      pairs completed so far before re-raising.

  calibrate --from-episode ID --stage DAY [--runs 3] [--local]
            [--max-calls 40] [--max-cost USD] [--dry-run]
      Grader sanity check (PROTOCOL.md's "Open hypothesis"): take a real
      transcript and build two degraded copies - shuffled turn order
      (seeded) and speakers rotated by one - then pairwise-judge real vs.
      degraded, positions swapped, same agreement rule as `ab`. Reports the
      judge's preference rate for the real transcript per degradation:
      >= 0.8 is "GRADER OK", otherwise "GRADER SUSPECT". --dry-run reports
      "DRY RUN - no signal" instead of either verdict - a template-mode,
      no-judge run has no preference rate worth calling OK or SUSPECT.

  pairs --from RESULT_JSON [--show] [--pick "1:A,2:B,..."]
        [--variant-name NAME]
      Blind human read of an `ab` result. --show prints each pair's two
      transcripts unlabeled as A/B (order scrambled per pair, seeded from
      the pair's 1-based position in the file - not run_index, which
      restarts at 1 for every scenario in a --testbed result - so a
      re-run reproduces the same labeling) without revealing which is
      control and which is variant. --pick "POSITION:A|B|tie,..." (using
      the same numbering --show printed) records Erik's picks into the
      result JSON as `human_picks` and reports agreed/disagreed/judge-tie
      counts separately - the agreement RATE is computed over pairs where
      the judge reached a non-tie overall verdict only, since a judge tie
      carries no direction to agree or disagree with. --variant-name picks
      which variant's own pairs to review in an `ab --sweep` result (which
      nests pairs per variant instead of one flat top-level list).

Both models fail loud, never a silent default: `ab`/`calibrate` read
DIALOGUE_MODEL via backend.config.config.dialogue_model (which itself
raises when unset) and JUDGE_MODEL directly from the environment - NEVER
via backend.config.config.judge_model, which silently falls back to
anthropic/claude-sonnet-4-6 in production. Either missing var exits
non-zero with a `doppler run --` hint unless --dry-run is passed.

Cost control, two independent caps checked before every paid unit (a
control/variant generation, or a judge call), whichever hits first:

  --max-calls: DERIVED when omitted (None) rather than one flat number -
  the printed report always shows the value used and whether it was
  derived or explicitly passed:
    - a single --concept/--recipe-context `ab` run: a flat 120
      (_SINGLE_CONCEPT_MAX_CALLS, per docs/conversation-lab/PROTOCOL.md's
      cost budget); `calibrate` keeps its own flat default of 40.
    - `ab --testbed`/`ab --sweep`: `panel_size * runs * (2 * max_turns +
      2)`, where max_turns is the upper bound of
      scripts.simulate_dialogue_week.TICKS_RANGE for --stage, floored at
      _MIN_MAX_TURNS_FLOOR (10) - some stages can run more turns than
      their static range says (Wednesday with photography context bumps
      to a 7-10 turn minimum in simulate_dialogue_week.py) - a deliberately
      generous ceiling, not a tight budget, especially for --sweep, whose
      shared control makes the true call count lower than this formula in
      practice.
  Once resolved (derived or explicit), the SAME per-call check applies:
  aborts once the next unit's estimated call count - taken from the LAST
  OBSERVED control/variant call count of the same kind, or the last judge
  call's flat 1 - would push the running total over it. Generation cost is
  counted via backend.utils.model_router.get_cost_summary()'s
  "total_calls" delta across the call when it moved (a real LLM call
  happened); otherwise the transcript's message count is used as the
  lower bound (mode="template"/--dry-run, or a test double that never
  touches the cost log).

  --max-cost (default $5.00 - Erik's standing cap, 2026-09-06) aborts once
  backend.utils.model_router.get_cost_summary()['total_cost'] has already
  reached it - the next paid unit, whatever it costs, is refused. This is
  an ABSOLUTE cap for `ab` (single/--testbed) and `calibrate`. For `ab
  --sweep` it applies PER VARIANT instead: each variant's own spend is
  measured relative to the total observed right before that variant
  started (see `_would_exceed_cost`'s `baseline` parameter), so Erik's $5
  cap governs each variant-as-experiment independently rather than the
  sweep as a whole - and the shared control's own cost, spent before any
  variant's baseline is captured, is never charged against one.

  A get_cost_summary() read failure never disables either cap by raising -
  it fails open (the check reports "not yet exceeded") and prints ONE
  stderr warning the first time this happens per process, since
  --max-calls remains the primary, always-available spending guard either
  way.

Either cap hitting writes whatever was completed so far as a partial
result with "aborted": true, and both caps are echoed in the printed
report. Any exception raised mid-run (a judge parse failure, a generation
error) writes the same kind of partial result - with "error" set to the
exception - before re-raising, so paid work already completed is never
silently lost.

Zero paid API calls happen anywhere in this module's own test suite -
scripts.simulate_dialogue_week.run_simulation and
backend.utils.model_router.generate_judge_response are always monkeypatched
in tests/test_conversation_lab.py.
"""

from __future__ import annotations

import argparse
import json
import os
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

import scripts.conversation_heatmap as conversation_heatmap
import scripts.conversation_metrics as conversation_metrics
import scripts.simulate_dialogue_week as simulate_module
from backend.admin.cron_routes import _build_recipe_context
from backend.config import config
from backend.utils import model_router
from backend.utils.episode_integrity import PLACEHOLDER_CONCEPT, _recipe_title
from scripts.conversation_metrics import summarize

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAB_DIR = ROOT / "docs" / "conversation-lab"
DEFAULT_RESULTS_DIR = DEFAULT_LAB_DIR / "results"
DEFAULT_TESTBED_PATH = DEFAULT_LAB_DIR / "testbed.json"
DEFAULT_TESTBED_RUNS = 3

# Erik's standing cost cap for a single conversation-lab invocation, 2026-09-06.
DEFAULT_MAX_COST_USD = 5.00

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
# cast_coverage, added in #6861). Keep this tuple exactly matched to the
# production judge - never add a lab-only dimension here.
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

# Two dimensions the lab's OWN pairwise judge scores in addition to the
# production 8 - not part of the live publish gate, just this tool's
# offline instrumentation for qualities the structured judge doesn't
# capture: whether characters ever sound like anything other than a
# confident expert, and whether a line reads as a person talking or a
# panel presenting.
LAB_ONLY_JUDGE_DIMENSIONS: tuple[str, ...] = (
    "emotional_range",
    "register_naturalness",
)

ALL_JUDGE_DIMENSIONS: tuple[str, ...] = JUDGE_DIMENSIONS + LAB_ONLY_JUDGE_DIMENSIONS

# A new prompt (this module's own, not backend/admin/cron_routes.py's
# _JUDGE_SYSTEM_PROMPT) asking for a pairwise A/B verdict instead of a
# single-transcript score. Reuses the same 8 dimension *definitions* as
# cron_routes.py's judge (lines ~267-317) so the pairwise judge is scoring
# the same things the production judge scores, just comparatively, plus
# LAB_ONLY_JUDGE_DIMENSIONS above (lab-only - never fed back into the
# production judge prompt).
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
    "their job) loses.\n"
    "- emotional_range: is there any attitude - tired, teasing, unsure, "
    "irritated, delighted - or is everyone a confident expert all the "
    "time?\n"
    "- register_naturalness: does this read like people talking, or like a "
    "panel presenting: every line a 25-word declarative claim with a dash "
    "and a mechanism?\n\n"
    "Respond with ONLY a JSON object - no markdown fences, no prose before "
    "or after it:\n"
    '{"winner": "A" or "B" or "tie", "per_dimension": {'
    '"title_fidelity": "A"|"B"|"tie", "arc_resolution": "A"|"B"|"tie", '
    '"voice_distinctiveness": "A"|"B"|"tie", '
    '"technical_credibility": "A"|"B"|"tie", '
    '"natural_progression": "A"|"B"|"tie", "promise_delivery": "A"|"B"|"tie", '
    '"turn_taking": "A"|"B"|"tie", "cast_coverage": "A"|"B"|"tie", '
    '"emotional_range": "A"|"B"|"tie", "register_naturalness": "A"|"B"|"tie"}, '
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


# Set once, the first time backend.utils.model_router.get_cost_summary()
# raises - see _warn_cost_summary_failure_once - so a broken cost log warns
# exactly once per process instead of once per checked call (ab/calibrate
# check it before every single generation and judge call).
_cost_summary_failure_warned = False


def _warn_cost_summary_failure_once(exc: Exception) -> None:
    global _cost_summary_failure_warned
    if _cost_summary_failure_warned:
        return
    print(
        "WARNING: cost cap check failed open - "
        f"backend.utils.model_router.get_cost_summary() raised {type(exc).__name__}: {exc} - "
        "--max-cost cannot be enforced until this is fixed; --max-calls remains the "
        "primary, always-available spending guard",
        file=sys.stderr,
    )
    _cost_summary_failure_warned = True


def _total_cost_or_none() -> float | None:
    """Current backend.utils.model_router.get_cost_summary()['total_cost'],
    or None on a read failure - after firing the one-time stderr warning
    above. Shared by `_would_exceed_cost` and `ab --sweep`'s per-variant
    cost baseline (see `_would_exceed_cost`'s `baseline` parameter)."""
    try:
        return model_router.get_cost_summary().get("total_cost", 0.0)
    except Exception as exc:
        _warn_cost_summary_failure_once(exc)
        return None


def _would_exceed_cost(max_cost: float, baseline: float = 0.0) -> bool:
    """True once (running total - `baseline`) has already reached
    --max-cost - refuse the next paid unit outright once that happens,
    since any positive-cost call from there pushes further over the cap.
    Erik's standing cap is $5.00 (2026-09-06); see DEFAULT_MAX_COST_USD.

    `baseline` defaults to 0.0 (an absolute cap on the running total) -
    unchanged behavior for `ab`'s single/testbed modes and `calibrate`.
    `ab --sweep` passes the total cost observed right before a variant's
    own generation/judge calls begin, so each variant gets its own
    max_cost allowance relative to that starting point - Erik's cap is
    PER VARIANT there, not shared across every variant (and the control)
    in the sweep - rather than one absolute cap racing against everything
    the sweep has already spent.

    A cost-summary read failure does not disable the cap by raising - it
    fails open (returns False, i.e. "not yet exceeded") the same way
    _run_arm_and_count already tolerates a failed read, because
    --max-calls remains the primary, always-available spending guard
    regardless of whether cost tracking itself is working.
    """
    total_cost = _total_cost_or_none()
    if total_cost is None:
        return False
    return (total_cost - baseline) >= max_cost


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
    episode['judge_scores'][stage]" per this module's spec. An empty dict
    at the stage level counts as missing, not present-but-empty - matches
    scripts/review_episode.py's `judge_meta` (`isinstance(..., dict) and
    stage_scores`), which falls back the same way rather than reporting a
    stage as judged when the field is just `{}`.
    """
    scores = stage_data.get("judge_scores")
    weakest = stage_data.get("judge_weakest")
    reason = stage_data.get("judge_reason")
    if not scores:
        scores = (episode.get("judge_scores") or {}).get(stage)
    if not weakest:
        weakest = (episode.get("judge_weakest") or {}).get(stage)
    if not reason:
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


DEFAULT_EXPERIMENTS_LOG = DEFAULT_LAB_DIR / "EXPERIMENTS.md"


def _experiments_log_path(args: argparse.Namespace) -> Path:
    """Where `ab` appends its experiment row.

    Defaults to docs/conversation-lab/EXPERIMENTS.md - an explicit,
    independent path, never derived from --results-dir's parent. Deriving
    a write location from another flag's parent directory means
    `--results-dir /tmp/x` would write to `/tmp/EXPERIMENTS.md`, silently
    escaping docs/ the moment anyone points results somewhere unrelated.
    """
    raw = getattr(args, "experiments_log", None)
    return Path(raw) if raw else DEFAULT_EXPERIMENTS_LOG


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
    winner_value = _normalize_verdict_value(parsed.get("winner", "tie"), field="winner")
    result: dict[str, str] = {"overall": mapping[winner_value]}
    per_dimension = parsed.get("per_dimension") or {}
    for dim in ALL_JUDGE_DIMENSIONS:
        value = _normalize_verdict_value(per_dimension.get(dim, "tie"), field=f"per_dimension.{dim}")
        result[dim] = mapping[value]
    result["reason"] = str(parsed.get("reason", ""))
    return result


def _normalize_verdict_value(raw: Any, *, field: str) -> str:
    """Normalise a judge verdict value's case and whitespace, and validate
    it is really one of A/B/tie.

    A missing field already defaulted to the string "tie" by the caller
    before this runs, which is always valid and never raises. What must
    never happen is silently mapping a garbage value - a hallucinated
    "C", a typo, an empty string the model returned instead of following
    instructions - into "tie", because that hides a judge that isn't
    doing its job behind an innocuous-looking result.
    """
    text = str(raw).strip().upper()
    if text == "TIE":
        return "tie"
    if text in ("A", "B"):
        return text
    raise ConversationLabError(
        f"pairwise judge returned an invalid {field} verdict: {raw!r} (expected A, B, or tie)"
    )


def _combine_orientations(first: dict[str, str], second: dict[str, str]) -> dict[str, str]:
    """A dimension (or overall) wins only when both position-swapped
    orderings pick the same arm; otherwise it is a tie."""
    combined: dict[str, str] = {}
    for key in ("overall", *ALL_JUDGE_DIMENSIONS):
        v1, v2 = first.get(key, "tie"), second.get(key, "tie")
        combined[key] = v1 if v1 == v2 else "tie"
    return combined


def _dry_run_combined() -> dict[str, str]:
    return {"overall": "tie", **{dim: "tie" for dim in ALL_JUDGE_DIMENSIONS}}


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def _episode_concept(episode: dict[str, Any]) -> str:
    """Prefer the real recipe title over the top-level `concept` field.

    `concept` stays PLACEHOLDER_CONCEPT ("Weekly Muffin Pan Recipe") on any
    week where scripts/pick_concept.py never ran - 23 of the 39 local
    mirrors under data/episodes/ as of 2026-09-06 - even though the baker
    has already picked a real dish name in
    stages.monday.recipe_data.title
    (backend.utils.episode_integrity._recipe_title). Reporting or judging
    against the placeholder instead of the real title is exactly the kind
    of silent-degradation-that-looks-healthy #6857 exists to catch.
    Falling back to the raw `concept` field only when there is no recipe
    title yet keeps an early stage (Monday before the baker runs)
    reporting something rather than an empty string.
    """
    title = _recipe_title(episode)
    if title:
        return title
    concept = str(episode.get("concept") or "")
    if concept == PLACEHOLDER_CONCEPT:
        print(
            f"WARNING: episode {episode.get('episode_id', '?')!r} has no recipe title yet "
            f"and its concept is still the placeholder {PLACEHOLDER_CONCEPT!r} - "
            "scripts/pick_concept.py has not run",
            file=sys.stderr,
        )
    return concept


def cmd_baseline(args: argparse.Namespace) -> None:
    episode = _load_episode(args.episode_id, local=args.local)
    stages = episode.get("stages") or {}
    concept = _episode_concept(episode)

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


def _stage_recipe_data(episode: dict[str, Any], stage_data: dict[str, Any]) -> dict[str, Any] | None:
    """recipe_data for one stage, falling back to Monday's.

    The baker writes recipe_data once, on Monday
    (backend/admin/cron_routes.py); a later stage's own dict rarely
    carries a copy. Shared by `_resolve_recipe_context` (ab) and
    cmd_calibrate so both build the same recipe_context for the same
    episode/stage instead of drifting.
    """
    return stage_data.get("recipe_data") or (episode.get("stages") or {}).get("monday", {}).get("recipe_data")


def _resolve_recipe_context(args: argparse.Namespace) -> str | None:
    if bool(args.from_episode) == bool(args.recipe_context):
        raise SystemExit("conversation_lab ab: pass exactly one of --from-episode or --recipe-context")
    if args.recipe_context:
        return args.recipe_context

    episode = _load_episode(args.from_episode, local=args.local)
    stage_data = (episode.get("stages") or {}).get(args.stage) or {}
    recipe_data = _stage_recipe_data(episode, stage_data)
    recipe_context = _build_recipe_context(recipe_data)
    if not recipe_context:
        raise SystemExit(
            f"conversation_lab ab: episode {args.from_episode!r} has no usable recipe_data "
            f"for stage {args.stage!r} - pass --recipe-context instead"
        )
    return recipe_context


def _resolve_judge_model() -> str:
    """Read JUDGE_MODEL straight from the environment - never through
    backend.config.config.judge_model, which silently falls back to
    "anthropic/claude-sonnet-4-6" when the env var is unset
    (backend/config.py's `judge_model` property, by design for
    production - a lab experiment must never silently judge on a model
    nobody explicitly chose, so it does not get to inherit that default).
    """
    judge_model = os.environ.get("JUDGE_MODEL", "").strip()
    if not judge_model:
        raise SystemExit(
            "conversation_lab: JUDGE_MODEL is not set. Run under "
            "`doppler run -- uv run ...` so JUDGE_MODEL resolves, or pass "
            "--dry-run for a zero-cost plumbing check."
        )
    return judge_model


def _resolve_models(dry_run: bool) -> tuple[str, str, str]:
    """Return (mode, default_model, judge_model) for `ab`.

    Fails loud (never a silent fallback) when the real models don't
    resolve and --dry-run was not passed. config.dialogue_model raises
    RuntimeError itself when DIALOGUE_MODEL is unset (backend/config.py);
    judge_model is always read via _resolve_judge_model(), never through
    config.judge_model (see that function's docstring for why).
    """
    if dry_run:
        return "template", "template", "template"
    try:
        default_model = config.dialogue_model
    except RuntimeError as exc:
        raise SystemExit(
            f"conversation_lab ab: {exc}\n"
            "Run under `doppler run -- uv run ...` so DIALOGUE_MODEL/JUDGE_MODEL "
            "resolve, or pass --dry-run for a zero-cost plumbing check."
        ) from exc
    return "openai", default_model, _resolve_judge_model()


def _load_testbed(path: Path) -> list[dict[str, Any]]:
    """Load the frozen scenario panel (docs/conversation-lab/testbed.json).

    Committed data, not regenerated at run time - see the module docstring
    and _build_parser's `--testbed` help for how it was produced.
    """
    if not path.exists():
        raise SystemExit(f"conversation_lab ab: testbed file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"conversation_lab ab: testbed file is not valid JSON: {path} ({exc})") from exc
    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        raise SystemExit(f"conversation_lab ab: testbed file must contain a non-empty 'scenarios' list: {path}")
    for scenario in scenarios:
        for field in ("id", "concept", "recipe_context"):
            if not isinstance(scenario, dict) or not scenario.get(field):
                raise SystemExit(
                    f"conversation_lab ab: testbed scenario missing required field {field!r}: {scenario}"
                )
    return scenarios


def _generate_and_judge_pairs(
    *,
    concept: str,
    stage: str,
    recipe_context: str | None,
    runs: int,
    variant: dict[str, Any],
    mode: str,
    default_model: str,
    judge_model: str,
    expected_cast: list[str],
    budget: CallBudget,
    max_cost: float,
    dry_run: bool,
    pairs: list[dict[str, Any]],
) -> bool:
    """Append up to `runs` control/variant pairs to `pairs` in place; return
    whether the run aborted (--max-calls or --max-cost hit).

    `pairs` is mutated in place rather than returned, so that a pair that
    finished before run N+1 raised (a judge parse failure, a generation
    error) is never lost - the caller's list already holds it even though
    this function itself never returns normally in that case. Every
    control/variant/judge call here is paid work; losing an already-
    completed pair because the run after it errored is exactly the kind
    of waste `~/projects/CLAUDE.md`'s cost doctrine forbids, which is why
    cmd_ab additionally writes a partial result (with "error"/"aborted")
    on any exception from this function - see its call sites.

    The pre-generation budget checks estimate the upcoming arm's call
    cost from the LAST OBSERVED control/variant call count of the SAME
    kind (1 before anything has been observed) rather than checking
    against a flat 1 - a flat 1 only guards the very next call, so a
    single arm's full turn count (an 8-turn day, say) could still blow
    straight through --max-calls in one step right at the boundary.
    """
    aborted = False
    restore_pending: dict[str, Any] | None = None
    last_control_calls = 1
    last_variant_calls = 1

    try:
        for run_index in range(1, runs + 1):
            if budget.would_exceed(last_control_calls) or _would_exceed_cost(max_cost):
                aborted = True
                break

            control_result, control_calls = _run_arm_and_count(
                concept, stage, run_index, recipe_context, mode, default_model
            )
            budget.record(control_calls)
            last_control_calls = control_calls

            if budget.would_exceed(last_variant_calls) or _would_exceed_cost(max_cost):
                aborted = True
                break

            restore_pending = _apply_variant(simulate_module, variant)
            try:
                variant_result, variant_calls = _run_arm_and_count(
                    concept, stage, run_index, recipe_context, mode, default_model
                )
            finally:
                _restore_variant(simulate_module, restore_pending)
                restore_pending = None
            budget.record(variant_calls)
            last_variant_calls = variant_calls

            control_messages = control_result.get("messages", [])
            variant_messages = variant_result.get("messages", [])

            if dry_run:
                combined = _dry_run_combined()
            else:
                if budget.would_exceed(1) or _would_exceed_cost(max_cost):
                    aborted = True
                    break
                first = _judge_orientation(
                    judge_model, concept, stage, recipe_context, expected_cast,
                    "control", control_messages, "variant", variant_messages,
                )
                budget.record(1)

                if budget.would_exceed(1) or _would_exceed_cost(max_cost):
                    aborted = True
                    break
                second = _judge_orientation(
                    judge_model, concept, stage, recipe_context, expected_cast,
                    "variant", variant_messages, "control", control_messages,
                )
                budget.record(1)
                combined = _combine_orientations(first, second)

            pairs.append({
                "run_index": run_index,
                "control_messages": control_messages,
                "variant_messages": variant_messages,
                "control_summary": summarize(control_messages, expected_cast, concept=concept, day=stage),
                "variant_summary": summarize(variant_messages, expected_cast, concept=concept, day=stage),
                "judge": combined,
                "dry_run": bool(dry_run),
            })
    finally:
        if restore_pending is not None:
            _restore_variant(simulate_module, restore_pending)
    return aborted


def _ab_result_path(results_dir: Path, slug: str, variant_path: Path) -> Path:
    return results_dir / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-ab-{slug}-{variant_path.stem}.json"
    )


# Flat --max-calls default for a single --concept run, unchanged from the
# original slice's hardcoded argparse default - a single concept/recipe
# doesn't have a "panel size" to derive a formula from.
_SINGLE_CONCEPT_MAX_CALLS = 120

# Floor under a stage's TICKS_RANGE upper bound when deriving --max-calls
# for --testbed/--sweep (below). Some stages can run MORE turns than their
# static TICKS_RANGE says: scripts/simulate_dialogue_week.py bumps
# Wednesday's day_ticks to max(day_ticks, 7) (or 10 on a reshoot) whenever
# photography_context is present - a case this module's own --testbed/
# --sweep calls never hit (they always pass photography_context=None via
# _run_arm), but the derived cap is a safety margin, not a tight budget,
# so it is worth padding for every stage rather than special-casing just
# Wednesday.
_MIN_MAX_TURNS_FLOOR = 10


def _max_turns_for_stage(stage: str) -> int:
    upper = simulate_module.TICKS_RANGE.get(stage, (4, 6))[1]
    return max(upper, _MIN_MAX_TURNS_FLOOR)


def _derive_max_calls(mode: str, *, scenario_count: int, runs: int, stage: str) -> int:
    """The --max-calls default when the flag itself is omitted (None).

    "single": a flat 120 (docs/conversation-lab/PROTOCOL.md's cost
    budget) - a lone --concept/--recipe-context run has no panel size to
    derive a formula from.

    "testbed"/"sweep": `scenario_count * runs * (3 * max_turns + 2)` -
    `3 * max_turns` estimates one pair's control + variant generation
    calls with retry headroom (each arm can run up to `max_turns` turns -
    see `_max_turns_for_stage` - and a live turn can cost a second call
    for the CoT-leak retry or the repetition rewrite, so 2x would abort a
    normal live variant mid-run), `+ 2` its two position-swapped judge
    calls.
    Both modes use this same formula; a --sweep's shared control makes
    the true call count lower than this in practice (the control is
    generated once, not once per variant), so the derived cap is a
    deliberately generous ceiling, not a tight budget.
    """
    if mode == "single":
        return _SINGLE_CONCEPT_MAX_CALLS
    max_turns = _max_turns_for_stage(stage)
    return scenario_count * runs * (3 * max_turns + 2)


def cmd_ab(args: argparse.Namespace) -> None:
    if args.sweep and args.variant:
        raise SystemExit("conversation_lab ab: pass --variant or --sweep, not both")
    if not args.sweep and not args.variant:
        raise SystemExit("conversation_lab ab: --variant is required unless --sweep is passed")

    mode, default_model, judge_model = _resolve_models(args.dry_run)

    if args.sweep:
        _cmd_ab_sweep(args, mode, default_model, judge_model)
        return

    variant_path = Path(args.variant)
    variant = _load_variant_file(variant_path)

    if args.testbed is not None:
        _cmd_ab_testbed(args, variant_path, variant, mode, default_model, judge_model)
        return

    if not args.concept:
        raise SystemExit("conversation_lab ab: --concept is required unless --testbed or --sweep is passed")
    if args.runs is None:
        raise SystemExit("conversation_lab ab: --runs is required unless --testbed or --sweep is passed")

    if args.max_calls is None:
        args.max_calls = _derive_max_calls("single", scenario_count=1, runs=args.runs, stage=args.stage)
    budget = CallBudget(max_calls=args.max_calls)

    recipe_context = _resolve_recipe_context(args)
    expected_cast = simulate_module.participants_for_day(args.stage)
    result_path = _ab_result_path(_results_dir(args), _slugify(args.concept), variant_path)

    pairs: list[dict[str, Any]] = []
    try:
        aborted = _generate_and_judge_pairs(
            concept=args.concept, stage=args.stage, recipe_context=recipe_context, runs=args.runs,
            variant=variant, mode=mode, default_model=default_model, judge_model=judge_model,
            expected_cast=expected_cast, budget=budget, max_cost=args.max_cost, dry_run=args.dry_run,
            pairs=pairs,
        )
    except BaseException as exc:
        report = _build_ab_report(
            args, variant_path, variant, pairs, True, budget, result_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        _write_json_result(result_path, report)
        raise

    report = _build_ab_report(args, variant_path, variant, pairs, aborted, budget, result_path)
    _write_json_result(result_path, report)
    if not args.no_log:
        _append_experiments_row(args, report, variant, result_path)
    _print_ab_report(report)


def _cmd_ab_testbed(
    args: argparse.Namespace,
    variant_path: Path,
    variant: dict[str, Any],
    mode: str,
    default_model: str,
    judge_model: str,
) -> None:
    if args.concept or args.recipe_context or args.from_episode:
        raise SystemExit(
            "conversation_lab ab: --testbed cannot be combined with --concept, "
            "--recipe-context, or --from-episode - each testbed scenario supplies its own"
        )
    scenarios = _load_testbed(Path(args.testbed))
    runs = args.runs if args.runs is not None else DEFAULT_TESTBED_RUNS
    max_calls_derived = args.max_calls is None
    if max_calls_derived:
        args.max_calls = _derive_max_calls("testbed", scenario_count=len(scenarios), runs=runs, stage=args.stage)
    budget = CallBudget(max_calls=args.max_calls)
    expected_cast = simulate_module.participants_for_day(args.stage)
    result_path = _ab_result_path(_results_dir(args), "testbed", variant_path)

    scenario_reports: list[dict[str, Any]] = []
    all_pairs: list[dict[str, Any]] = []
    aborted = False

    try:
        for scenario in scenarios:
            scenario_pairs: list[dict[str, Any]] = []
            try:
                scenario_aborted = _generate_and_judge_pairs(
                    concept=scenario["concept"], stage=args.stage, recipe_context=scenario["recipe_context"],
                    runs=runs, variant=variant, mode=mode, default_model=default_model, judge_model=judge_model,
                    expected_cast=expected_cast, budget=budget, max_cost=args.max_cost, dry_run=args.dry_run,
                    pairs=scenario_pairs,
                )
            finally:
                for pair in scenario_pairs:
                    pair["scenario_id"] = scenario["id"]
                all_pairs.extend(scenario_pairs)
                scenario_reports.append({
                    "id": scenario["id"],
                    "concept": scenario["concept"],
                    "category": scenario.get("category"),
                    "cuisine": scenario.get("cuisine"),
                    "source_episode": scenario.get("source_episode"),
                    **_aggregate_pairs(scenario_pairs, args.target, args.dry_run),
                })
            if scenario_aborted:
                aborted = True
                break
    except BaseException as exc:
        report = _build_testbed_ab_report(
            args, variant_path, variant, scenario_reports, all_pairs, True, budget, result_path,
            max_calls_derived, error=f"{type(exc).__name__}: {exc}",
        )
        _write_json_result(result_path, report)
        raise

    report = _build_testbed_ab_report(
        args, variant_path, variant, scenario_reports, all_pairs, aborted, budget, result_path, max_calls_derived,
    )
    _write_json_result(result_path, report)
    if not args.no_log:
        _append_experiments_row(args, report, variant, result_path)
    _print_testbed_ab_report(report)


def _aggregate_pairs(pairs: list[dict[str, Any]], target: str, dry_run: bool) -> dict[str, Any]:
    """Wins/ties/losses per dimension, target win rate, worst-other-loss
    rate, and mean metric deltas for one set of judged pairs.

    Shared by the single-scenario report and both the per-scenario and
    cross-scenario testbed aggregates, so the three never compute this
    differently by accident. Metric deltas are generic over every numeric
    key `scripts.conversation_metrics.summarize()` returns (read off the
    first pair's own summary, since every pair's summary has the same key
    set) - a new metric someone adds there shows up here with no code
    change.
    """
    n = len(pairs)
    overall_counts = Counter(p["judge"]["overall"] for p in pairs)
    per_dimension_counts = {dim: Counter(p["judge"][dim] for p in pairs) for dim in ALL_JUDGE_DIMENSIONS}

    metric_deltas: dict[str, float] = {}
    if pairs:
        for key in _numeric_keys(pairs[0]["control_summary"]):
            deltas = [p["variant_summary"].get(key, 0) - p["control_summary"].get(key, 0) for p in pairs]
            metric_deltas[key] = round(mean(deltas), 4)

    target_wins = (
        overall_counts.get("variant", 0)
        if target == "overall"
        else per_dimension_counts.get(target, Counter()).get("variant", 0)
    )
    target_win_rate = round(target_wins / n, 4) if n else 0.0

    other_dims = [d for d in ALL_JUDGE_DIMENSIONS if d != target]
    worst_other_loss_rate = 0.0
    losing_dims: list[str] = []
    for dim in other_dims:
        counts = per_dimension_counts.get(dim, Counter())
        control_wins = counts.get("control", 0)
        if control_wins > 0:
            losing_dims.append(dim)
        worst_other_loss_rate = max(worst_other_loss_rate, (control_wins / n) if n else 0.0)

    meets_decision_rule = not dry_run and n > 0 and target_win_rate >= 0.65 and worst_other_loss_rate <= 0.50

    return {
        "completed_pairs": n,
        "overall_counts": dict(overall_counts),
        "per_dimension_counts": {d: dict(c) for d, c in per_dimension_counts.items()},
        "target_win_rate": target_win_rate,
        "worst_other_dimension_loss_rate": round(worst_other_loss_rate, 4),
        "dimensions_losing_to_control": losing_dims,
        "meets_decision_rule": meets_decision_rule,
        "metric_deltas": metric_deltas,
    }


def _build_ab_report(
    args: argparse.Namespace,
    variant_path: Path,
    variant: dict[str, Any],
    pairs: list[dict[str, Any]],
    aborted: bool,
    budget: CallBudget,
    result_path: Path,
    error: str | None = None,
) -> dict[str, Any]:
    try:
        cost_summary = model_router.get_cost_summary()
    except Exception:
        cost_summary = {}

    report = {
        "command": "ab",
        "mode": "single",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "concept": args.concept,
        "stage": args.stage,
        "variant_file": str(variant_path),
        "variant_name": variant_path.stem,
        "variant_keys": sorted(variant.keys()),
        "requested_runs": args.runs,
        "aborted": aborted,
        "max_calls": args.max_calls,
        "max_cost": args.max_cost,
        "calls_used": budget.used,
        "dry_run": bool(args.dry_run),
        "target_dimension": args.target,
        "decision_rule": DECISION_RULE_TEXT,
        "cost_summary": cost_summary,
        "pairs": pairs,
        "results_file": str(result_path),
        **_aggregate_pairs(pairs, args.target, args.dry_run),
    }
    if error is not None:
        report["error"] = error
    return report


def _build_testbed_ab_report(
    args: argparse.Namespace,
    variant_path: Path,
    variant: dict[str, Any],
    scenario_reports: list[dict[str, Any]],
    all_pairs: list[dict[str, Any]],
    aborted: bool,
    budget: CallBudget,
    result_path: Path,
    max_calls_derived: bool,
    error: str | None = None,
) -> dict[str, Any]:
    try:
        cost_summary = model_router.get_cost_summary()
    except Exception:
        cost_summary = {}

    report = {
        "command": "ab",
        "mode": "testbed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "concept": None,
        "stage": args.stage,
        "testbed_path": str(args.testbed),
        "scenario_count": len(scenario_reports),
        "panel_size": len(scenario_reports),
        "runs_per_scenario": args.runs if args.runs is not None else DEFAULT_TESTBED_RUNS,
        "variant_file": str(variant_path),
        "variant_name": variant_path.stem,
        "variant_keys": sorted(variant.keys()),
        "requested_runs": args.runs if args.runs is not None else DEFAULT_TESTBED_RUNS,
        "aborted": aborted,
        "max_calls": args.max_calls,
        "max_calls_derived": max_calls_derived,
        "max_cost": args.max_cost,
        "calls_used": budget.used,
        "dry_run": bool(args.dry_run),
        "target_dimension": args.target,
        "decision_rule": DECISION_RULE_TEXT,
        "cost_summary": cost_summary,
        "scenarios": scenario_reports,
        "pairs": all_pairs,
        "results_file": str(result_path),
        **_aggregate_pairs(all_pairs, args.target, args.dry_run),
    }
    if error is not None:
        report["error"] = error
    return report


def _print_dimension_table(per_dimension_counts: dict[str, dict[str, int]]) -> None:
    print(f"{'dimension':<24}{'variant':>8}{'tie':>8}{'control':>8}")
    for dim in ALL_JUDGE_DIMENSIONS:
        counts = per_dimension_counts.get(dim, {})
        print(f"{dim:<24}{counts.get('variant', 0):>8}{counts.get('tie', 0):>8}{counts.get('control', 0):>8}")


def _print_metric_deltas(metric_deltas: dict[str, float]) -> None:
    print("\nmean metric deltas (variant - control):")
    for key, value in sorted(metric_deltas.items()):
        print(f"  {key:<32}{value:+.4f}")


def _print_ab_report(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab ab: {report['concept']} / {report['stage']} ===")
    print(f"variant: {report['variant_name']} ({report['variant_file']}) keys={report['variant_keys']}")
    abort_note = "  [ABORTED: max-calls/max-cost hit]" if report["aborted"] else ""
    print(f"pairs completed: {report['completed_pairs']} / requested {report['requested_runs']}{abort_note}")
    print(
        f"calls used: {report['calls_used']} / max {report['max_calls']}  "
        f"cost cap: ${report['max_cost']:.2f}  dry_run={report['dry_run']}"
    )
    print(f"\noverall: {dict(report['overall_counts'])}")
    _print_dimension_table(report["per_dimension_counts"])
    print(f"\ntarget ({report['target_dimension']}) win rate: {report['target_win_rate']:.2%}")
    print(f"worst other-dimension loss rate: {report['worst_other_dimension_loss_rate']:.2%}")
    print(f"dimensions losing to control: {report['dimensions_losing_to_control'] or 'none'}")
    _print_metric_deltas(report["metric_deltas"])
    print(f"\nDECISION RULE (informational, not enforced): {report['decision_rule']}")
    print(f"cost summary: {report['cost_summary']}")
    print(f"\nresults written to: {report['results_file']}")


def _print_testbed_ab_report(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab ab --testbed: {report['scenario_count']} scenarios / {report['stage']} ===")
    print(f"testbed: {report['testbed_path']}  runs per scenario: {report['runs_per_scenario']}")
    print(f"variant: {report['variant_name']} ({report['variant_file']}) keys={report['variant_keys']}")
    abort_note = "  [ABORTED: max-calls/max-cost hit]" if report["aborted"] else ""
    print(f"pairs completed: {report['completed_pairs']}{abort_note}")
    print(f"panel size: {report['panel_size']} scenarios")
    print(
        f"calls used: {report['calls_used']} / max {report['max_calls']} "
        f"({'derived' if report['max_calls_derived'] else 'explicit'})  "
        f"cost cap: ${report['max_cost']:.2f}  dry_run={report['dry_run']}"
    )

    print("\n--- per-scenario breakdown ---")
    for scenario in report["scenarios"]:
        print(
            f"  {scenario['id']:<12} {scenario['concept']:<40} "
            f"pairs={scenario['completed_pairs']:<3} "
            f"target_win_rate={scenario['target_win_rate']:.2%}"
        )

    print("\n--- aggregate across all scenarios ---")
    print(f"overall: {dict(report['overall_counts'])}")
    _print_dimension_table(report["per_dimension_counts"])
    print(f"\ntarget ({report['target_dimension']}) win rate: {report['target_win_rate']:.2%}")
    print(f"worst other-dimension loss rate: {report['worst_other_dimension_loss_rate']:.2%}")
    print(f"dimensions losing to control: {report['dimensions_losing_to_control'] or 'none'}")
    _print_metric_deltas(report["metric_deltas"])
    print(f"\nDECISION RULE (informational, not enforced): {report['decision_rule']}")
    print(f"cost summary: {report['cost_summary']}")
    print(f"\nresults written to: {report['results_file']}")


_EXPERIMENTS_SECTION_HEADING = "## Experiments"
_EXPERIMENTS_TABLE_HEADER_LINE = (
    "| Date | Experiment ID | Lever (one) | Target dimension(s) | N | "
    "Wins/Ties/Losses on target | Other dimensions lost | Decision | Shipped PR | "
    "Live confirmation week + scores |\n"
)
_EXPERIMENTS_TABLE_SEPARATOR_LINE = (
    "|------|----------------|--------------|----------------------|---|"
    "------------------------------|------------------------|-----------|-------------|"
    "-----------------------------------|\n"
)
_EXPERIMENTS_HEADER = "# Conversation Lab Experiments\n\n" + _EXPERIMENTS_TABLE_HEADER_LINE + _EXPERIMENTS_TABLE_SEPARATOR_LINE


def _target_win_tie_loss(report: dict[str, Any]) -> tuple[int, int, int]:
    target = report["target_dimension"]
    counts = report["overall_counts"] if target == "overall" else report["per_dimension_counts"].get(target, {})
    return counts.get("variant", 0), counts.get("tie", 0), counts.get("control", 0)


def _insert_experiments_row(text: str, row: str) -> str:
    """Insert `row` (one newline-terminated markdown table line) as the
    LAST row of the Experiments table under the "## Experiments" heading
    in `text` - creating the heading and its table header/separator when
    either is missing.

    The pre-slice-3 implementation appended unconditionally at end of
    file, which lands every row AFTER whatever section happens to follow
    the Experiments table in a real file (e.g. "## Heat map baseline") -
    outside the table it is supposed to be a row of - instead of inside
    it. This walks the heading, table header, and separator explicitly
    and inserts right after the last existing data row (or right after
    the separator line itself when the table has no rows yet), so the row
    always lands inside the table regardless of what comes after it.
    """
    lines = text.splitlines(keepends=True)
    heading_idx = next(
        (i for i, line in enumerate(lines) if line.strip() == _EXPERIMENTS_SECTION_HEADING),
        None,
    )

    if heading_idx is None:
        # No "## Experiments" section anywhere in the file - create it
        # (heading + table header/separator) at the end.
        prefix = "" if (not lines or lines[-1].endswith("\n")) else "\n"
        addition = (
            f"{prefix}\n{_EXPERIMENTS_SECTION_HEADING}\n\n"
            f"{_EXPERIMENTS_TABLE_HEADER_LINE}{_EXPERIMENTS_TABLE_SEPARATOR_LINE}{row}"
        )
        return text + addition

    # Find the table header anywhere in the section (up to the next "## "
    # heading or EOF). The real EXPERIMENTS.md keeps a prose paragraph
    # between the heading and the table, so the header is not necessarily
    # the first non-blank line under the heading - looking only there
    # synthesized a second header and split the table (slice-3 review).
    section_end = heading_idx + 1
    while section_end < len(lines) and not lines[section_end].startswith("## "):
        section_end += 1
    header_idx = next(
        (i for i in range(heading_idx + 1, section_end) if lines[i].lstrip().startswith("| Date")),
        None,
    )
    if header_idx is None:
        # Heading exists but the section has no table yet: create the
        # header/separator right under the heading's trailing blank lines.
        i = heading_idx + 1
        while i < len(lines) and lines[i].strip() == "":
            i += 1
        insertion = _EXPERIMENTS_TABLE_HEADER_LINE + _EXPERIMENTS_TABLE_SEPARATOR_LINE + row
        return "".join(lines[:i]) + insertion + "".join(lines[i:])

    # Consume the separator and every existing data row (consecutive
    # "|"-prefixed lines) to find the true end of the table body.
    table_end = header_idx + 1
    while table_end < len(lines) and lines[table_end].lstrip().startswith("|"):
        table_end += 1
    return "".join(lines[:table_end]) + row + "".join(lines[table_end:])


def _experiment_decision_text(
    *, dry_run: bool, completed_pairs: int, meets_decision_rule: bool, target_win_rate: float, target_dimension: str
) -> str:
    if dry_run:
        return "DRY RUN - no signal"
    if completed_pairs == 0:
        return "no pairs completed"
    verb = "SHIP" if meets_decision_rule else "HOLD"
    return f"signal: {verb} ({target_win_rate:.0%} on {target_dimension})"


def _write_experiments_row(log_path: Path, row: str) -> None:
    """Insert `row` into EXPERIMENTS.md's Experiments table (see
    `_insert_experiments_row`) - or create the whole file, header
    included, when it does not exist yet. An existing file's content
    outside the Experiments table (its initial prose, other sections -
    possibly authored by another implementer working this same card) is
    never rewritten, only ever inserted into at the one right spot."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text(_EXPERIMENTS_HEADER + row, encoding="utf-8")
        return
    existing_text = log_path.read_text(encoding="utf-8")
    log_path.write_text(_insert_experiments_row(existing_text, row), encoding="utf-8")


def _append_experiments_row(
    args: argparse.Namespace,
    report: dict[str, Any],
    variant: dict[str, Any],
    result_path: Path,
) -> None:
    """Write one row into EXPERIMENTS.md's Experiments table for a single-
    concept or --testbed `ab` run (see `_write_experiments_row`)."""
    wins, ties, losses = _target_win_tie_loss(report)
    lever = "+".join(sorted(variant.keys())) or Path(report["variant_file"]).stem
    losing = ", ".join(report["dimensions_losing_to_control"]) or "none"
    decision = _experiment_decision_text(
        dry_run=report["dry_run"], completed_pairs=report["completed_pairs"],
        meets_decision_rule=report["meets_decision_rule"], target_win_rate=report["target_win_rate"],
        target_dimension=report["target_dimension"],
    )

    row = (
        f"| {report['generated_at']} | {result_path.stem} | {lever} | "
        f"{report['target_dimension']} | {report['completed_pairs']} | "
        f"{wins}/{ties}/{losses} | {losing} | {decision} | TBD | TBD |\n"
    )

    _write_experiments_row(_experiments_log_path(args), row)


# ---------------------------------------------------------------------------
# ab --sweep: one shared control, many variants ranked against it
# ---------------------------------------------------------------------------


def _load_sweep_variants(sweep_dir: Path) -> dict[str, dict[str, Any]]:
    """Every *.json file directly under `sweep_dir` is one variant - same
    file format as --variant (module-attribute-name -> replacement value).
    Keyed by filename stem so a variant is addressable by a short name in
    the report, EXPERIMENTS.md rows, and `pairs --from ... --variant-name`.
    """
    if not sweep_dir.is_dir():
        raise SystemExit(f"conversation_lab ab: --sweep directory not found: {sweep_dir}")
    variant_files = sorted(sweep_dir.glob("*.json"))
    if not variant_files:
        raise SystemExit(f"conversation_lab ab: --sweep directory has no *.json variant files: {sweep_dir}")
    return {vf.stem: _load_variant_file(vf) for vf in variant_files}


def _generate_sweep_control(
    *,
    scenarios: list[dict[str, Any]],
    stage: str,
    runs: int,
    mode: str,
    default_model: str,
    budget: CallBudget,
    transcripts: dict[tuple[str, int], dict[str, Any]],
) -> bool:
    """Generate the control transcript for every (scenario, run) pair
    EXACTLY ONCE, into `transcripts` (mutated in place) - every variant in
    the sweep judges against these same transcripts instead of
    regenerating its own control, which is what makes running many
    variants against the same panel affordable (this module's docstring
    and `docs/conversation-lab/PROTOCOL.md`). Only --max-calls guards this
    loop - --max-cost is a PER-VARIANT cap (see `_run_sweep_variant`) and
    the control's own cost is reported separately, charged to the sweep as
    a whole, never to a variant. Returns whether the run aborted before
    every (scenario, run) pair got a control transcript.
    """
    last_calls = 1
    for scenario in scenarios:
        for run_index in range(1, runs + 1):
            if budget.would_exceed(last_calls):
                return True
            result, calls = _run_arm_and_count(
                scenario["concept"], stage, run_index, scenario["recipe_context"], mode, default_model,
            )
            budget.record(calls)
            last_calls = calls
            transcripts[(scenario["id"], run_index)] = result
    return False


def _run_sweep_variant(
    *,
    variant: dict[str, Any],
    scenarios: list[dict[str, Any]],
    stage: str,
    runs: int,
    mode: str,
    default_model: str,
    judge_model: str,
    expected_cast: list[str],
    control_transcripts: dict[tuple[str, int], dict[str, Any]],
    max_calls: int,
    max_cost: float,
    dry_run: bool,
    pairs: list[dict[str, Any]],
) -> tuple[bool, int, float | None]:
    """Generate this ONE variant's own transcripts (one per scenario/run)
    and pairwise-judge each against the ALREADY-GENERATED shared control
    at the same (scenario, run) key - the control is never regenerated
    here. Mutates `pairs` in place (mirrors `_generate_and_judge_pairs`)
    so a pair that finished before a later one raised is never lost.

    Spends against a budget and cost cap PRIVATE to this one variant:
    `_would_exceed_cost`'s `baseline` is the total cost observed right
    before this variant started, so Erik's --max-cost applies PER VARIANT
    (each variant is one experiment under his $5 cap) rather than as one
    cap shared across every variant - and the shared control's own cost,
    spent before `baseline` was captured, is never charged against it.

    Returns (aborted, calls_used, cost_spent_by_this_variant) - cost is
    None when the cost log itself could not be read (see
    `_total_cost_or_none`), so the caller can tell "spent nothing" apart
    from "unknown."
    """
    budget = CallBudget(max_calls=max_calls)
    baseline_cost = _total_cost_or_none() or 0.0
    aborted = False
    restore_pending: dict[str, Any] | None = None
    last_variant_calls = 1

    try:
        for scenario in scenarios:
            for run_index in range(1, runs + 1):
                key = (scenario["id"], run_index)
                control_result = control_transcripts.get(key)
                if control_result is None:
                    # The shared control loop itself aborted before reaching
                    # this key - nothing to compare this variant's arm
                    # against, so this variant stops here too.
                    aborted = True
                    break

                if budget.would_exceed(last_variant_calls) or _would_exceed_cost(max_cost, baseline=baseline_cost):
                    aborted = True
                    break

                restore_pending = _apply_variant(simulate_module, variant)
                try:
                    variant_result, variant_calls = _run_arm_and_count(
                        scenario["concept"], stage, run_index, scenario["recipe_context"], mode, default_model,
                    )
                finally:
                    _restore_variant(simulate_module, restore_pending)
                    restore_pending = None
                budget.record(variant_calls)
                last_variant_calls = variant_calls

                control_messages = control_result.get("messages", [])
                variant_messages = variant_result.get("messages", [])

                if dry_run:
                    combined = _dry_run_combined()
                else:
                    if budget.would_exceed(1) or _would_exceed_cost(max_cost, baseline=baseline_cost):
                        aborted = True
                        break
                    first = _judge_orientation(
                        judge_model, scenario["concept"], stage, scenario["recipe_context"], expected_cast,
                        "control", control_messages, "variant", variant_messages,
                    )
                    budget.record(1)

                    if budget.would_exceed(1) or _would_exceed_cost(max_cost, baseline=baseline_cost):
                        aborted = True
                        break
                    second = _judge_orientation(
                        judge_model, scenario["concept"], stage, scenario["recipe_context"], expected_cast,
                        "variant", variant_messages, "control", control_messages,
                    )
                    budget.record(1)
                    combined = _combine_orientations(first, second)

                pairs.append({
                    "scenario_id": scenario["id"],
                    "run_index": run_index,
                    "control_messages": control_messages,
                    "variant_messages": variant_messages,
                    "control_summary": summarize(control_messages, expected_cast, concept=scenario["concept"], day=stage),
                    "variant_summary": summarize(variant_messages, expected_cast, concept=scenario["concept"], day=stage),
                    "judge": combined,
                    "dry_run": bool(dry_run),
                })
            if aborted:
                break
    finally:
        if restore_pending is not None:
            _restore_variant(simulate_module, restore_pending)

    cost_after = _total_cost_or_none()
    cost_spent = round(cost_after - baseline_cost, 6) if cost_after is not None else None
    return aborted, budget.used, cost_spent


def _build_sweep_variant_report(
    variant_name: str,
    variant: dict[str, Any],
    pairs: list[dict[str, Any]],
    aborted: bool,
    calls_used: int | None,
    cost: float | None,
    target: str,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "variant_name": variant_name,
        "variant_keys": sorted(variant.keys()),
        "aborted": aborted,
        "calls_used": calls_used,
        "cost": cost,
        "pairs": pairs,
        **_aggregate_pairs(pairs, target, dry_run),
    }


def _arm_top_phrases(transcripts_by_key: dict[str, list[dict[str, Any]]]) -> list[str] | None:
    """Top 10 phrases recurring across an arm's OWN transcripts (present in
    2+ distinct (scenario, run) transcripts), via
    scripts.conversation_heatmap.phrase_heat() - so a variant that kills a
    boilerplate phrase shows it directly in the sweep ranking.

    getattr-guarded (returns None when absent) rather than imported by
    name: scripts/conversation_heatmap.py is owned by another implementer
    working this same card (#6492) and may not carry this name in every
    revision - this sweep must work whether or not it does.
    """
    fn = getattr(conversation_heatmap, "phrase_heat", None)
    if fn is None or not transcripts_by_key:
        return None
    heat = fn(transcripts_by_key, min_groups=2, top=10)
    return [p["phrase"] for p in heat.get("phrases", [])]


def _rank_sweep_variants(
    variant_reports: dict[str, Any],
    target: str,
) -> list[dict[str, Any]]:
    """One ranking row per variant: wins/ties/losses on --target against
    the shared control, the overall win count, per-area metric deltas
    (variant mean - control mean) when
    scripts.conversation_metrics.AREA_METRICS exists (falling back to the
    flat metric deltas when it does not - both getattr-guarded, same
    reasoning as `_arm_top_phrases` above), paid calls, and cost. Sorted
    by target wins, then target win rate, so the strongest variant leads.
    """
    area_metrics = getattr(conversation_metrics, "AREA_METRICS", None)
    ranking: list[dict[str, Any]] = []

    for name, report in variant_reports.items():
        overall_counts = report.get("overall_counts", {})
        per_dimension_counts = report.get("per_dimension_counts", {})
        target_counts = overall_counts if target == "overall" else per_dimension_counts.get(target, {})

        entry: dict[str, Any] = {
            "variant_name": name,
            "target_dimension": target,
            "target_wins": target_counts.get("variant", 0),
            "target_ties": target_counts.get("tie", 0),
            "target_losses": target_counts.get("control", 0),
            "target_win_rate": report.get("target_win_rate", 0.0),
            "overall_wins": overall_counts.get("variant", 0),
            "meets_decision_rule": report.get("meets_decision_rule", False),
            "completed_pairs": report.get("completed_pairs", 0),
            "calls_used": report.get("calls_used"),
            "cost": report.get("cost"),
            "aborted": report.get("aborted", False),
        }

        metric_deltas = report.get("metric_deltas", {})
        if area_metrics:
            entry["area_metric_deltas"] = {
                area: {k: metric_deltas[k] for k in keys if k in metric_deltas}
                for area, keys in area_metrics.items()
            }
        else:
            entry["metric_deltas"] = metric_deltas

        transcripts_by_key = {
            f"{p['scenario_id']}-run{p['run_index']}": p.get("variant_messages", [])
            for p in report.get("pairs", [])
        }
        top_phrases = _arm_top_phrases(transcripts_by_key)
        if top_phrases is not None:
            entry["top_phrases"] = top_phrases

        ranking.append(entry)

    ranking.sort(key=lambda e: (-e["target_wins"], -e["target_win_rate"], e["variant_name"]))
    return ranking


def _build_sweep_report(
    args: argparse.Namespace,
    sweep_dir: Path,
    variants: dict[str, dict[str, Any]],
    scenarios: list[dict[str, Any]],
    runs: int,
    control_transcripts: dict[tuple[str, int], dict[str, Any]],
    control_aborted: bool,
    control_cost: float | None,
    control_budget: CallBudget,
    variant_reports: dict[str, Any],
    aborted: bool,
    result_path: Path,
    max_calls_derived: bool,
    error: str | None = None,
) -> dict[str, Any]:
    try:
        cost_summary = model_router.get_cost_summary()
    except Exception:
        cost_summary = {}

    control_transcripts_by_key = {
        f"{sid}-run{run_index}": result.get("messages", [])
        for (sid, run_index), result in control_transcripts.items()
    }

    report = {
        "command": "ab",
        "mode": "sweep",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage": args.stage,
        "sweep_dir": str(sweep_dir),
        "variant_names": sorted(variants.keys()),
        "testbed_path": str(args.testbed) if args.testbed else str(DEFAULT_TESTBED_PATH),
        "scenario_count": len(scenarios),
        "panel_size": len(scenarios),
        "runs_per_scenario": runs,
        "requested_runs": runs,
        "aborted": aborted,
        "max_calls": args.max_calls,
        "max_calls_derived": max_calls_derived,
        "max_cost_per_variant": args.max_cost,
        "control_aborted": control_aborted,
        "control_calls_used": control_budget.used,
        "control_cost": control_cost,
        "control_transcripts_generated": len(control_transcripts),
        "control_top_phrases": _arm_top_phrases(control_transcripts_by_key),
        "dry_run": bool(args.dry_run),
        "target_dimension": args.target,
        "decision_rule": DECISION_RULE_TEXT,
        "cost_summary": cost_summary,
        "variants": variant_reports,
        "ranking": _rank_sweep_variants(variant_reports, args.target),
        "results_file": str(result_path),
    }
    if error is not None:
        report["error"] = error
    return report


def _print_sweep_report(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab ab --sweep: {len(report['variant_names'])} variants / {report['stage']} ===")
    print(f"sweep dir: {report['sweep_dir']}  testbed: {report['testbed_path']}")
    print(
        f"panel size: {report['panel_size']} scenarios x {report['runs_per_scenario']} runs  "
        f"max_calls: {report['max_calls']} ({'derived' if report['max_calls_derived'] else 'explicit'})"
    )
    control_cost = "unknown" if report["control_cost"] is None else f"${report['control_cost']:.4f}"
    abort_note = "  [CONTROL ABORTED]" if report["control_aborted"] else ""
    print(
        f"control: {report['control_transcripts_generated']} transcripts generated once, "
        f"calls_used={report['control_calls_used']}, cost={control_cost} - "
        f"charged to the sweep, not to any one variant{abort_note}"
    )
    print(f"max_cost per variant: ${report['max_cost_per_variant']:.2f}  dry_run={report['dry_run']}")

    print(f"\n--- variant ranking (target: {report['target_dimension']}) ---")
    for entry in report["ranking"]:
        abort_note = "  [ABORTED]" if entry["aborted"] else ""
        cost = "unknown" if entry["cost"] is None else f"${entry['cost']:.4f}"
        print(
            f"  {entry['variant_name']:<24} target_wins={entry['target_wins']:<3} "
            f"ties={entry['target_ties']:<3} losses={entry['target_losses']:<3} "
            f"win_rate={entry['target_win_rate']:.2%} overall_wins={entry['overall_wins']:<3} "
            f"calls={entry['calls_used']} cost={cost}{abort_note}"
        )
        if entry.get("top_phrases"):
            note = "  (dry-run: template boilerplate, no signal)" if report.get("dry_run") else ""
            print(f"      top phrases: {', '.join(entry['top_phrases'][:5])}{note}")
        deltas = entry.get("area_metric_deltas") or {}
        if deltas:
            parts: list[str] = []
            if all(isinstance(v, dict) for v in deltas.values()):
                for area, metrics in deltas.items():
                    inner = ", ".join(
                        f"{k} {v:+.3f}" for k, v in metrics.items() if isinstance(v, (int, float))
                    )
                    if inner:
                        parts.append(f"{area}: {inner}")
            else:
                parts = [f"{k} {v:+.3f}" for k, v in deltas.items() if isinstance(v, (int, float))]
            if parts:
                print("      area deltas (variant - control): " + " | ".join(parts))

    if report.get("error"):
        print(f"\nERROR: {report['error']}")
    print(f"\nresults written to: {report['results_file']}")


def _append_sweep_experiments_row(
    args: argparse.Namespace,
    report: dict[str, Any],
    variant_name: str,
    variant_report: dict[str, Any],
    variant: dict[str, Any],
    result_path: Path,
) -> None:
    """One EXPERIMENTS.md row per sweep variant - experiment id is the
    sweep's own result-file stamp plus the variant name, so each variant's
    row is independently addressable even though every variant in the
    sweep shares one result JSON."""
    target = args.target
    overall_counts = variant_report.get("overall_counts", {})
    per_dimension_counts = variant_report.get("per_dimension_counts", {})
    counts = overall_counts if target == "overall" else per_dimension_counts.get(target, {})
    wins, ties, losses = counts.get("variant", 0), counts.get("tie", 0), counts.get("control", 0)

    lever = "+".join(sorted(variant.keys())) or variant_name
    losing = ", ".join(variant_report.get("dimensions_losing_to_control", [])) or "none"
    decision = _experiment_decision_text(
        dry_run=bool(args.dry_run), completed_pairs=variant_report.get("completed_pairs", 0),
        meets_decision_rule=variant_report.get("meets_decision_rule", False),
        target_win_rate=variant_report.get("target_win_rate", 0.0), target_dimension=target,
    )

    experiment_id = f"{result_path.stem}-{variant_name}"
    row = (
        f"| {report['generated_at']} | {experiment_id} | {lever} | "
        f"{target} | {variant_report.get('completed_pairs', 0)} | "
        f"{wins}/{ties}/{losses} | {losing} | {decision} | TBD | TBD |\n"
    )

    _write_experiments_row(_experiments_log_path(args), row)


def _cmd_ab_sweep(
    args: argparse.Namespace,
    mode: str,
    default_model: str,
    judge_model: str,
) -> None:
    if args.concept or args.recipe_context or args.from_episode:
        raise SystemExit(
            "conversation_lab ab: --sweep cannot be combined with --concept, "
            "--recipe-context, or --from-episode - scenarios come from the testbed panel"
        )
    sweep_dir = Path(args.sweep)
    variants = _load_sweep_variants(sweep_dir)

    testbed_path = Path(args.testbed) if args.testbed else DEFAULT_TESTBED_PATH
    scenarios = _load_testbed(testbed_path)
    runs = args.runs if args.runs is not None else DEFAULT_TESTBED_RUNS
    expected_cast = simulate_module.participants_for_day(args.stage)

    max_calls_derived = args.max_calls is None
    if max_calls_derived:
        args.max_calls = _derive_max_calls("sweep", scenario_count=len(scenarios), runs=runs, stage=args.stage)

    result_path = _ab_result_path(_results_dir(args), "sweep", sweep_dir)

    control_budget = CallBudget(max_calls=args.max_calls)
    control_baseline = _total_cost_or_none() or 0.0
    control_transcripts: dict[tuple[str, int], dict[str, Any]] = {}
    variant_reports: dict[str, Any] = {}
    control_aborted = False
    aborted = False

    def _control_cost() -> float | None:
        total = _total_cost_or_none()
        return round(total - control_baseline, 6) if total is not None else None

    try:
        control_aborted = _generate_sweep_control(
            scenarios=scenarios, stage=args.stage, runs=runs, mode=mode, default_model=default_model,
            budget=control_budget, transcripts=control_transcripts,
        )
        aborted = control_aborted

        if not control_aborted:
            for variant_name, variant in variants.items():
                variant_pairs: list[dict[str, Any]] = []
                try:
                    v_aborted, v_calls, v_cost = _run_sweep_variant(
                        variant=variant, scenarios=scenarios, stage=args.stage, runs=runs, mode=mode,
                        default_model=default_model, judge_model=judge_model, expected_cast=expected_cast,
                        control_transcripts=control_transcripts, max_calls=args.max_calls, max_cost=args.max_cost,
                        dry_run=args.dry_run, pairs=variant_pairs,
                    )
                except BaseException:
                    variant_reports[variant_name] = _build_sweep_variant_report(
                        variant_name, variant, variant_pairs, True, None, None, args.target, args.dry_run,
                    )
                    raise
                variant_reports[variant_name] = _build_sweep_variant_report(
                    variant_name, variant, variant_pairs, v_aborted, v_calls, v_cost, args.target, args.dry_run,
                )
                if v_aborted:
                    aborted = True
                    break
    except BaseException as exc:
        report = _build_sweep_report(
            args, sweep_dir, variants, scenarios, runs, control_transcripts, control_aborted, _control_cost(),
            control_budget, variant_reports, True, result_path, max_calls_derived,
            error=f"{type(exc).__name__}: {exc}",
        )
        _write_json_result(result_path, report)
        raise

    report = _build_sweep_report(
        args, sweep_dir, variants, scenarios, runs, control_transcripts, control_aborted, _control_cost(),
        control_budget, variant_reports, aborted, result_path, max_calls_derived,
    )
    _write_json_result(result_path, report)
    if not args.no_log:
        for variant_name, variant_report in variant_reports.items():
            _append_sweep_experiments_row(args, report, variant_name, variant_report, variants[variant_name], result_path)
    _print_sweep_report(report)


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


def _build_calibrate_report(
    args: argparse.Namespace,
    concept: str,
    degradation_reports: dict[str, Any],
    aborted: bool,
    budget: CallBudget,
    result_path: Path,
    error: str | None = None,
) -> dict[str, Any]:
    report = {
        "command": "calibrate",
        "episode_id": args.from_episode,
        "stage": args.stage,
        "concept": concept,
        "dry_run": bool(args.dry_run),
        "aborted": aborted,
        "max_calls": args.max_calls,
        "max_cost": args.max_cost,
        "calls_used": budget.used,
        "degradations": degradation_reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_file": str(result_path),
    }
    if error is not None:
        report["error"] = error
    return report


def cmd_calibrate(args: argparse.Namespace) -> None:
    episode = _load_episode(args.from_episode, local=args.local)
    stage_data = (episode.get("stages") or {}).get(args.stage) or {}
    dialogue = stage_data.get("dialogue") or []
    if not dialogue:
        raise SystemExit(
            f"conversation_lab calibrate: no dialogue for stage={args.stage!r} in "
            f"episode {args.from_episode!r}"
        )

    concept = _episode_concept(episode)
    expected_cast = simulate_module.participants_for_day(args.stage)
    recipe_context = _build_recipe_context(_stage_recipe_data(episode, stage_data))

    if args.dry_run:
        judge_model = "template"
    else:
        judge_model = _resolve_judge_model()

    budget = CallBudget(max_calls=args.max_calls)
    aborted = False
    degradation_reports: dict[str, Any] = {}
    result_path = _results_dir(args) / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-calibrate-"
        f"{args.from_episode}-{args.stage}.json"
    )

    # Any exception below (a judge parse failure mid-degradation, say)
    # still writes whatever degradations/pairs completed so far as a
    # partial, error-flagged result before re-raising - paid judge calls
    # must never be lost just because a later one failed. The inner
    # try/finally does the same for the CURRENT degradation's own
    # completed pairs, which would otherwise be dropped because they are
    # only assigned into degradation_reports after the inner loop returns
    # normally.
    try:
        for name, degrade in _DEGRADATIONS:
            pair_records: list[dict[str, Any]] = []
            wins = 0
            attempted = 0
            try:
                for run_index in range(1, args.runs + 1):
                    if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                        aborted = True
                        break
                    degraded = degrade(dialogue, run_index)
                    attempted += 1

                    if args.dry_run:
                        combined = _dry_run_combined()
                    else:
                        if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                            aborted = True
                            break
                        first = _judge_orientation(
                            judge_model, concept, args.stage, recipe_context, expected_cast,
                            "real", dialogue, "degraded", degraded,
                        )
                        budget.record(1)

                        if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
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
            finally:
                preference_rate = round(wins / attempted, 4) if attempted else 0.0
                if args.dry_run:
                    verdict = "DRY RUN - no signal"
                else:
                    verdict = "GRADER OK" if preference_rate >= 0.8 else "GRADER SUSPECT"
                degradation_reports[name] = {
                    "attempted": attempted,
                    "real_preference_rate": preference_rate,
                    "verdict": verdict,
                    "pairs": pair_records,
                }
            if aborted:
                break
    except BaseException as exc:
        report = _build_calibrate_report(
            args, concept, degradation_reports, True, budget, result_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        _write_json_result(result_path, report)
        raise

    report = _build_calibrate_report(args, concept, degradation_reports, aborted, budget, result_path)
    _write_json_result(result_path, report)
    _print_calibrate_report(report)


def _print_calibrate_report(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab calibrate: {report['episode_id']} / {report['stage']} ===")
    abort_note = "  [ABORTED: max-calls/max-cost hit]" if report["aborted"] else ""
    print(
        f"calls used: {report['calls_used']} / max {report['max_calls']}  "
        f"cost cap: ${report['max_cost']:.2f}{abort_note}  dry_run={report['dry_run']}"
    )
    for name, info in report["degradations"].items():
        print(
            f"  {name:<18} attempted={info['attempted']:<4} "
            f"real_preference_rate={info['real_preference_rate']:.2%}  {info['verdict']}"
        )
    print(f"\nresults written to: {report['results_file']}")


# ---------------------------------------------------------------------------
# pairs (blind human read)
# ---------------------------------------------------------------------------


def _blind_order(pair_position: int) -> tuple[str, str]:
    """Deterministic-but-scrambled A/B order for one pair.

    Seeded from the pair's 1-based POSITION in the result file's `pairs`
    list - never `run_index`, which is only unique within a single
    scenario: an `ab --testbed` result concatenates every scenario's own
    1..N run_index sequence, so pair 1 of scenario 2 shares run_index=1
    with pair 1 of scenario 1. Position in the file is unique regardless
    of mode, and it is exactly what --show numbers its "Pair N" output
    with, so --pick "N:..." always addresses what Erik just read.

    `pairs --show` and a later `pairs --pick` against the SAME result
    file always reproduce the identical labeling Erik saw - the mapping
    never needs to be persisted for correctness, only for the audit trail
    `cmd_pairs` writes into human_review_order.
    """
    rng = random.Random(pair_position)
    return ("control", "variant") if rng.random() < 0.5 else ("variant", "control")


def _parse_pick_spec(spec: str) -> dict[int, str]:
    """Parse "1:A,2:B,3:tie" into {1: "A", 2: "B", 3: "tie"}."""
    picks: dict[int, str] = {}
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise SystemExit(f"conversation_lab pairs: bad --pick entry {chunk!r} - expected POSITION:A|B|tie")
        idx_str, value = chunk.split(":", 1)
        try:
            idx = int(idx_str.strip())
        except ValueError as exc:
            raise SystemExit(f"conversation_lab pairs: bad --pick position {idx_str!r}") from exc
        normalized = value.strip().upper()
        if normalized not in ("A", "B", "TIE"):
            raise SystemExit(
                f"conversation_lab pairs: bad --pick value {value!r} for pair {idx} - expected A, B, or tie"
            )
        picks[idx] = "tie" if normalized == "TIE" else normalized
    return picks


def cmd_pairs(args: argparse.Namespace) -> None:
    if not args.show and not args.pick:
        raise SystemExit("conversation_lab pairs: pass --show, --pick, or both")

    result_path = Path(args.from_result)
    if not result_path.exists():
        raise SystemExit(f"conversation_lab pairs: result file not found: {result_path}")
    report = json.loads(result_path.read_text(encoding="utf-8"))

    # An `ab --sweep` result nests every variant's own pairs under
    # report["variants"][name]["pairs"] instead of a flat top-level
    # "pairs" list (one sweep run judges N variants against the SAME
    # shared control, so there is no single "the pairs" to default to) -
    # --variant-name picks which variant's arm to review; human_picks and
    # the agreement stats below are recorded into that variant's own
    # sub-dict, not the sweep report's top level.
    container = report
    if report.get("mode") == "sweep":
        variants = report.get("variants") or {}
        if not args.variant_name:
            raise SystemExit(
                "conversation_lab pairs: this is an `ab --sweep` result - it nests pairs "
                f"per variant, so pass --variant-name (available: {', '.join(sorted(variants)) or 'none'})"
            )
        if args.variant_name not in variants:
            raise SystemExit(
                f"conversation_lab pairs: unknown --variant-name {args.variant_name!r} "
                f"(available: {', '.join(sorted(variants))})"
            )
        container = variants[args.variant_name]

    pairs = container.get("pairs") or []
    if not pairs:
        raise SystemExit(f"conversation_lab pairs: {result_path} has no pairs to review")

    order_by_position: dict[int, tuple[str, str]] = {i: _blind_order(i) for i in range(1, len(pairs) + 1)}

    if args.show:
        for position, pair in enumerate(pairs, start=1):
            first_arm, second_arm = order_by_position[position]
            first_messages = pair.get(f"{first_arm}_messages") or []
            second_messages = pair.get(f"{second_arm}_messages") or []
            scenario_note = f" (scenario {pair['scenario_id']!r})" if "scenario_id" in pair else ""
            print(f"\n=== Pair {position}{scenario_note} ===")
            print(f"--- A ---\n{_format_transcript(first_messages)}")
            print(f"--- B ---\n{_format_transcript(second_messages)}")

    if args.pick:
        picks = _parse_pick_spec(args.pick)
        unknown = sorted(set(picks) - set(order_by_position))
        if unknown:
            raise SystemExit(
                f"conversation_lab pairs: --pick references unknown pair position(s): {unknown} "
                f"(valid: 1-{len(pairs)})"
            )

        human_picks: dict[str, str] = dict(container.get("human_picks") or {})
        for position, label in picks.items():
            first_arm, second_arm = order_by_position[position]
            mapped = "tie" if label == "tie" else (first_arm if label == "A" else second_arm)
            human_picks[str(position)] = mapped
        container["human_picks"] = human_picks
        container["human_review_order"] = {
            str(position): {"A": order[0], "B": order[1]} for position, order in order_by_position.items()
        }

        # Agreement is computed over pairs where the judge reached a
        # non-tie verdict only - a judge "tie" carries no directional
        # signal for a human pick to agree or disagree with, so folding it
        # into either bucket (or into the denominator at all) would water
        # down what the rate actually measures. judge-tie pairs are still
        # reported, just kept separate.
        judge_by_position = {i: pair["judge"]["overall"] for i, pair in enumerate(pairs, start=1)}
        agreed = 0
        disagreed = 0
        judge_tie = 0
        for position_str, mapped in human_picks.items():
            judge_overall = judge_by_position.get(int(position_str))
            if judge_overall is None:
                continue
            if judge_overall == "tie":
                judge_tie += 1
                continue
            if judge_overall == mapped:
                agreed += 1
            else:
                disagreed += 1
        compared = agreed + disagreed
        agreement_rate = round(agreed / compared, 4) if compared else 0.0
        container["human_judge_agreement_rate"] = agreement_rate
        container["human_judge_agreed_count"] = agreed
        container["human_judge_disagreed_count"] = disagreed
        container["human_judge_tie_count"] = judge_tie

        result_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"\nrecorded {len(picks)} human pick(s) into {result_path}")
        print(
            f"human/judge agreement rate: {agreement_rate:.2%} ({agreed} agreed / {disagreed} disagreed"
            f" / {judge_tie} judge-tie - judge ties are excluded from the agreement rate)"
        )


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
        help="Offline blind, position-swapped A/B between a control and one or many variant prompt levers.",
        description=(
            "Generate N control/variant pairs through the production call "
            "shape (mode='openai', prompt_style='scene', ticks_per_day=0) "
            "and judge each pair twice with positions swapped. --variant is "
            "a JSON object mapping scripts.simulate_dialogue_week module "
            "attribute names to replacement values (a string or a dict) - "
            "allowed levers: " + ", ".join(ALLOWED_VARIANT_ATTRS) + ". "
            "--dry-run runs mode='template' (zero API calls) and skips the "
            "judge (every verdict is 'tie', dry_run=true). Pass exactly one "
            "of --variant (a single variant, optionally --testbed for the "
            "scenario panel) or --sweep (many variants at once, always "
            "against the testbed panel)."
        ),
    )
    ab.add_argument("--concept", default=None, help="Required unless --testbed or --sweep is passed")
    ab.add_argument("--stage", required=True, choices=simulate_module.DAY_ORDER)
    ab.add_argument("--runs", type=int, default=None, help="Required unless --testbed or --sweep is passed")
    ab.add_argument(
        "--variant", default=None, type=Path,
        help="JSON file: {attribute_name: replacement_value}. Required unless --sweep is passed; mutually exclusive with --sweep",
    )
    ab.add_argument(
        "--sweep", default=None,
        help=(
            "Directory of variant JSON files - every *.json file in DIR is one variant (same "
            "format as --variant), keyed by filename stem. Generates the CONTROL transcripts "
            "ONCE per (scenario, run) from the testbed panel (--testbed, or the default panel if "
            "omitted) and reuses them against EVERY variant - this is what makes running many "
            "variants against the same panel affordable - then pairwise-judges each variant "
            "against the shared control with the same position-swap rule as a single --variant "
            "run, and reports a ranking table (wins/ties/losses on --target, overall wins, "
            "per-area metric deltas, top recurring phrases per arm when available, calls and "
            "cost). --max-cost applies PER VARIANT (Erik's $5 cap is per experiment, and each "
            "variant is one experiment) - the shared control's own cost is reported separately, "
            "charged to the sweep as a whole, never to any one variant. Forbids --concept, "
            "--recipe-context, and --from-episode - scenarios come from the testbed panel. "
            "Mutually exclusive with --variant. Writes one result JSON covering every variant "
            "(both transcripts per pair) and one EXPERIMENTS.md row per variant unless --no-log - "
            "review one variant's pairs with `pairs --from ... --variant-name NAME`."
        ),
    )
    ab.add_argument("--from-episode", default=None, help="Build --recipe-context from this episode's recipe_data")
    ab.add_argument(
        "--recipe-context", default=None,
        help="One-line recipe anchor, verbatim (mutually exclusive with --from-episode, --testbed, and --sweep)",
    )
    ab.add_argument("--local", action="store_true", help="With --from-episode, skip the CDN; read the local mirror")
    ab.add_argument(
        "--testbed", nargs="?", const=str(DEFAULT_TESTBED_PATH), default=None,
        help=(
            f"Run every scenario in the frozen testbed panel instead of a single --concept "
            f"(bare flag defaults to {DEFAULT_TESTBED_PATH}; pass a path to use another file). "
            f"Runs --runs pairs per scenario (default {DEFAULT_TESTBED_RUNS} in testbed mode) and "
            "reports a per-scenario breakdown plus an aggregate across all scenarios. Forbids "
            "--concept, --recipe-context, and --from-episode - each scenario supplies its own. "
            "With --sweep, --testbed is optional and selects the panel file (defaulting to the "
            "same frozen panel) rather than switching modes - --sweep always uses a panel."
        ),
    )
    ab.add_argument("--target", default="turn_taking", choices=(*ALL_JUDGE_DIMENSIONS, "overall"))
    ab.add_argument(
        "--max-calls", type=int, default=None,
        help=(
            "Defaults to a value DERIVED from the mode when omitted (printed in the report): "
            f"a single --concept/--recipe-context run gets a flat {_SINGLE_CONCEPT_MAX_CALLS}; "
            "--testbed and --sweep get `panel_size * runs * (2 * max_turns + 2)`, where "
            "max_turns is the upper bound of scripts.simulate_dialogue_week.TICKS_RANGE for "
            f"--stage (floored at {_MIN_MAX_TURNS_FLOOR} - Wednesday with photography context "
            "can run more turns than its static range says). Pass an explicit value to override."
        ),
    )
    ab.add_argument(
        "--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
        help=(
            f"USD cap on backend.utils.model_router.get_cost_summary()['total_cost'] for this "
            f"invocation (default ${DEFAULT_MAX_COST_USD:.2f}, Erik's standing cap, 2026-09-06). "
            "Checked before every paid unit; aborts with a partial result once the running total "
            "has reached it. With --sweep, this cap applies PER VARIANT, not to the sweep as a "
            "whole - the shared control's cost is reported separately."
        ),
    )
    ab.add_argument("--dry-run", action="store_true")
    ab.add_argument("--no-log", action="store_true", help="Do not append a row to the experiments log")
    ab.add_argument("--experiments-log", default=None, help=f"Override the EXPERIMENTS.md path (default: {DEFAULT_EXPERIMENTS_LOG})")
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
    calibrate.add_argument(
        "--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
        help=f"USD cap on total_cost for this invocation (default ${DEFAULT_MAX_COST_USD:.2f}); see `ab --help`.",
    )
    calibrate.add_argument("--dry-run", action="store_true")
    calibrate.add_argument("--results-dir", default=None)

    pairs_cmd = sub.add_parser(
        "pairs",
        help="Blind human read of an ab result's transcripts, scored for agreement with the judge.",
        description=(
            "Read an ab result JSON and, per pair, present its two "
            "transcripts unlabeled as A/B - order randomised per pair, "
            "seeded from the pair's 1-based position in the file (not "
            "run_index, which restarts at 1 per scenario in a --testbed "
            "result), so the same result file always reproduces the same "
            "labeling on a re-run. --show prints them for a blind read, "
            "numbered by that same position. --pick 'POSITION:A|B|tie,...' "
            "records Erik's picks back into the result file as "
            "human_picks and reports agreed/disagreed/judge-tie counts "
            "separately, mapped back through the stored A/B order - the "
            "agreement RATE is computed over pairs where the judge reached "
            "a non-tie overall verdict only (a judge tie carries no "
            "direction to agree or disagree with), so a run with several "
            "judge ties does not silently dilute the rate. An `ab --sweep` "
            "result nests pairs per variant - pass --variant-name to pick "
            "which one."
        ),
    )
    pairs_cmd.add_argument("--from", dest="from_result", required=True, help="Path to an ab result JSON file")
    pairs_cmd.add_argument("--show", action="store_true", help="Print each pair's two transcripts, unlabeled as A/B")
    pairs_cmd.add_argument(
        "--pick", default=None,
        help='Comma-separated POSITION:A|B|tie entries (1-based, matching --show), e.g. "1:A,2:B,3:tie"',
    )
    pairs_cmd.add_argument(
        "--variant-name", default=None,
        help=(
            "For an `ab --sweep` result only: which variant's pairs to review - a sweep result "
            "nests pairs per variant under result['variants'][NAME]['pairs'] instead of a flat "
            "top-level 'pairs' list, since one sweep judges several variants against one shared "
            "control. Ignored for a single-/--testbed-mode result."
        ),
    )

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
        elif args.command == "pairs":
            cmd_pairs(args)
        else:  # pragma: no cover - argparse enforces valid choices
            raise SystemExit(f"conversation_lab: unknown command {args.command!r}")
    except ConversationLabError as exc:
        raise SystemExit(f"conversation_lab {args.command}: {exc}") from exc


if __name__ == "__main__":
    main()
