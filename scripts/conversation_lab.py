#!/usr/bin/env python3
"""Offline dialogue experiment runner for the conversation lab (card #6492).

See docs/conversation-lab/PROTOCOL.md for the full method this implements;
this docstring covers the mechanics.

Five subcommands:

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
      frozen seven-scenario panel in docs/conversation-lab/testbed-v3.json
      (or a path you pass), running --runs pairs (default 3 in testbed mode)
      per scenario and reporting both a per-scenario breakdown and an
      aggregate across every scenario's pairs - the fixed panel keeps
      experiments comparable month to month instead of drifting with
      whatever episode happens to be in progress. v3 is the current anchor
      panel (#7441); v2 and v1 (legacy) are available for historical
      comparison. --concept, --recipe-context, and --from-episode are
      forbidden with --testbed; each scenario already carries its own.

      --prior-days FROZEN_JSON (--testbed/--sweep only) seeds BOTH arms of
      every pair with that scenario's frozen earlier days from `freeze`,
      passed as run_simulation's initial_recent_lines (the exact
      "FirstName: line" entries a full-week run would have accumulated by
      that day). The frozen file must cover every panel scenario and must
      not contain --stage or a later day; provenance (path, sha256, days)
      is recorded in the result JSON as `prior_days`.

      Every judged dimension - the production 8 plus two lab-only ones,
      emotional_range and register_naturalness (see
      LAB_ONLY_JUDGE_DIMENSIONS) - is a valid --target and appears in the
      per-dimension table, the aggregation, and the JSON report.

      The --variant file is a JSON object mapping
      scripts.simulate_dialogue_week module attribute names to replacement
      values (a string or a dict). Only existing, non-callable module
      attributes may be overridden - see ALLOWED_VARIANT_ATTRS below for the
      documented, useful levers (docs/conversation-lab/PROTOCOL.md's "Where
      the levers live"). The lever set spans the character-prompt levers
      (_SHARED_CHARACTER_RULES, _REACTION_DIRECTIVE, DAY_STAGE_DIRECTIONS,
      CHARACTER_DAY_GOALS), the history window HISTORY_DEPTH, the
      output-contract guards (SHAPE_WINDOW, SHAPE_MAX_IN_WINDOW,
      WORD_BUDGET_TOLERANCE, REWRITE_GUARDS, WORD_CAPS), the wind-down/stop knobs (TICKS_RANGE,
      WINDDOWN_TRIGGER, STOP_CHECK, OPEN_ENDED_MAX_TICKS), and the director
      knob (DIRECTOR). The patch applies
      to the variant arm's generation call only and is always restored
      afterward, even on error.

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

  calibrate (--from-episode ID --stage DAY | --reference-panel PATH) [--runs 3] [--local]
            [--max-calls 40] [--max-cost USD] [--dry-run]
      Grader sanity check (PROTOCOL.md's "Open hypothesis"): take a real
      transcript and build two degraded copies - shuffled turn order
      (seeded) and speakers rotated by one - then pairwise-judge real vs.
      degraded, positions swapped, same agreement rule as `ab`. Reports the
      judge's preference rate for the real transcript per degradation:
      >= 0.8 is "GRADER OK", otherwise "GRADER SUSPECT". --dry-run reports
      "DRY RUN - no signal" instead of either verdict - a template-mode,
      no-judge run has no preference rate worth calling OK or SUSPECT.
      --reference-panel validates and compares the versioned human-reference
      fixture. Its paid comparisons use the versioned pairwise evaluator and
      remain separate from the independent-generation grader verdict.

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

  freeze --from RESULT_JSON --arm {control,variant} --pick {judge,run0}
         --out FROZEN_JSON [--variant NAME] | --append-to FROZEN_JSON
      Distill one arm's transcript per scenario_id from an `ab --testbed`
      or `ab --sweep` result into a frozen prior-days file. --pick judge
      selects the pair where that arm won the most judge dimensions (ties
      broken by lowest run_index); --pick run0 selects the lowest run_index
      (the lab's runs are 1-based, so in practice run 1). --append-to adds
      a later day onto an existing file so Mon+Tue live in one file, keyed
      by scenario then day, in week order; it refuses a day that is not
      after the last frozen day.

  Paid experiment commands accept --budget-ledger PATH to share an
  authoritative Anthropic token-usage ledger across ab, bench and calibrate;
  pass --create-budget-ledger once to create it, then resume without that flag.

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
    - `ab --testbed`/`ab --sweep`: `panel_size * runs * (2 * 4 * max_turns +
      2)`, reserving four generation requests per turn for each arm, where max_turns is the upper bound of
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
import contextvars
import copy
import fcntl
import hashlib
import http.server
import json
import math
import os
import tempfile
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import httpx
from send2trash import send2trash
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from math import isfinite, sqrt
from statistics import mean, median, stdev
from typing import Any

import scripts.conversation_heatmap as conversation_heatmap
import scripts.conversation_metrics as conversation_metrics
import scripts.simulate_dialogue_week as simulate_module
from backend.admin.cron_routes import (
    _build_judge_recipe_facts,
    _build_recipe_context,
    _judge_dialogue as judge_dialogue,
)
from backend.config import config
from backend.utils import model_router
from backend.utils import stop_check
from backend.utils.episode_integrity import PLACEHOLDER_CONCEPT, _recipe_title
from scripts.conversation_metrics import summarize
from scripts.conversation_budget import AnthropicBudgetGuard, BudgetGuardError

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LAB_DIR = ROOT / "docs" / "conversation-lab"
DEFAULT_RESULTS_DIR = DEFAULT_LAB_DIR / "results"
# v3 is the CURRENT-anchor panel (#7441). v2 (#7201) and v1 (testbed.json) are kept
# for historical baseline comparison. v2 carried recipe_context without ingredient
# boundaries; v3 builds from production's #7441-aware _build_recipe_context, so
# experiments now measure what speakers actually see in production. v1 carries the
# pre-#7104 "Key ingredients:" format (production stopped emitting, 2026-09-15).
DEFAULT_TESTBED_PATH = DEFAULT_LAB_DIR / "testbed-v3.json"
LEGACY_TESTBED_V2_PATH = DEFAULT_LAB_DIR / "testbed-v2.json"
LEGACY_TESTBED_PATH = DEFAULT_LAB_DIR / "testbed.json"
DEFAULT_TESTBED_RUNS = 3

# Erik's standing cost cap for a single conversation-lab invocation, 2026-09-06.
DEFAULT_MAX_COST_USD = 5.00

# ---------------------------------------------------------------------------
# Swappable lab model sets (#7714). scripts/lab_models.json maps a set name to
# a {"dialogue": ..., "judge": ...} pair of bare OpenRouter dotted ids
# (vendor/model, e.g. "anthropic/claude-haiku-4.5"); `ab --models NAME` picks
# one, defaulting to the file's "default" key. Changing which models the lab
# uses is then a one-line edit to that file, never a code change here.
#
# model_router itself must never read this file (backend/ ships in the Vercel
# Lambda bundle, scripts/ does not - see .vercelignore) - this module reads it
# and calls model_router.allow_openrouter_models() to register every set's ids.
# ---------------------------------------------------------------------------
LAB_MODELS_PATH = ROOT / "scripts" / "lab_models.json"

# vendor/model - a single slash, no leading/trailing slash, no whitespace.
_LAB_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+$")


@dataclass(frozen=True)
class LabModelSet:
    name: str
    dialogue: str  # bare OpenRouter id, e.g. "anthropic/claude-haiku-4.5"
    judge: str


@dataclass(frozen=True)
class LabModelsFile:
    default: str
    sets: dict[str, "LabModelSet"]

    def get(self, name: str) -> "LabModelSet":
        try:
            return self.sets[name]
        except KeyError:
            raise SystemExit(
                f"conversation_lab: --models {name!r} is not defined in {LAB_MODELS_PATH} - "
                f"defined sets: {', '.join(sorted(self.sets)) or '(none)'}"
            )


def _validate_lab_model_id(path: Path, set_name: str, key: str, value: Any) -> str:
    if not isinstance(value, str) or not _LAB_MODEL_ID_RE.match(value):
        raise SystemExit(
            f"conversation_lab: {path} set {set_name!r} key {key!r} must look like "
            f"'vendor/model' (e.g. 'anthropic/claude-haiku-4.5'), got {value!r}"
        )
    return value


def _load_lab_models_file(path: Path = LAB_MODELS_PATH) -> LabModelsFile:
    """Load and validate a lab_models.json file. Raises SystemExit (never a
    silent fallback) on anything malformed - a bad model-set file must fail
    loud before any generation call, the same as every other lab config."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"conversation_lab: model set file not found: {path}") from exc
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"conversation_lab: model set file is not valid JSON: {path} ({exc})") from exc
    if not isinstance(raw, dict):
        raise SystemExit(f"conversation_lab: model set file must be a JSON object: {path}")
    default = raw.get("default")
    sets_raw = raw.get("sets")
    if not isinstance(default, str) or not default:
        raise SystemExit(f"conversation_lab: {path} 'default' must be a non-empty string")
    if not isinstance(sets_raw, dict) or not sets_raw:
        raise SystemExit(f"conversation_lab: {path} 'sets' must be a non-empty object")
    sets: dict[str, LabModelSet] = {}
    for set_name, entry in sets_raw.items():
        if not isinstance(entry, dict) or set(entry) != {"dialogue", "judge"}:
            raise SystemExit(
                f"conversation_lab: {path} set {set_name!r} must be an object with exactly "
                f"'dialogue' and 'judge' keys, got {entry!r}"
            )
        dialogue = _validate_lab_model_id(path, set_name, "dialogue", entry["dialogue"])
        judge = _validate_lab_model_id(path, set_name, "judge", entry["judge"])
        sets[set_name] = LabModelSet(name=set_name, dialogue=dialogue, judge=judge)
    if default not in sets:
        raise SystemExit(f"conversation_lab: {path} 'default' {default!r} is not one of 'sets': {sorted(sets)}")
    return LabModelsFile(default=default, sets=sets)


# Loaded once at import - scripts/ is not part of the Vercel Lambda bundle, so
# reading this file here (unlike in backend/utils/model_router.py) is safe.
_LAB_MODELS = _load_lab_models_file()
_DEFAULT_LAB_MODEL_SET = _LAB_MODELS.get(_LAB_MODELS.default)

# Register every set's ids with model_router's OpenRouter allowlists (both
# dialogue and judge) up front - regardless of which set a given invocation
# picks at runtime via --models.
for _lab_model_set in _LAB_MODELS.sets.values():
    model_router.allow_openrouter_models(dialogue=_lab_model_set.dialogue, judge=_lab_model_set.judge)
del _lab_model_set

# Kept as module attributes (names other tests/code import) - always the
# DEFAULT set's ids, "openrouter/"-scheme-prefixed the way model_router.
# parse_model expects (it splits on the FIRST "/" only).
OPENROUTER_DIALOGUE_MODEL = f"openrouter/{_DEFAULT_LAB_MODEL_SET.dialogue}"
OPENROUTER_JUDGE_MODEL = f"openrouter/{_DEFAULT_LAB_MODEL_SET.judge}"

_OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
_OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_OPENROUTER_KEY_TIMEOUT_SECONDS = 30.0

# Key snapshots for one paid OpenRouter run. `before` is captured in main()
# before dispatch; `after` is fetched lazily by the first report builder.
_OPENROUTER_KEY_BEFORE: dict[str, Any] | None = None
_OPENROUTER_KEY_AFTER: dict[str, Any] | None = None

# {model_id: (prompt_price_per_token_usd, completion_price_per_token_usd)},
# fetched ONCE by _openrouter_preflight (#7714 finding 2) - never under
# --dry-run, since _openrouter_preflight itself is only called when not
# args.dry_run. None until then; the mid-arm guard's worst-case reservation
# (_openrouter_worst_case_call_cost) fails CLOSED (treats the model as
# infinitely expensive) when a price is missing, rather than silently
# reading this as "$0 remaining prices," but the real fail-closed moment is
# _openrouter_preflight refusing to start the run at all.
_OPENROUTER_MODEL_PRICES: dict[str, tuple[float, float]] | None = None

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
    # #7158: the history window was a local in generate_turn, so #7160 could not be
    # measured here at all. Shape is {"early"|"late": (opening_turn, later_turns)}.
    "HISTORY_DEPTH",
    # Output-contract guards. Levers so the sweep can answer "does enforcing the
    # budget hurt the writing?" rather than us guessing.
    "SHAPE_WINDOW",
    "SHAPE_MAX_IN_WINDOW",
    "WORD_BUDGET_TOLERANCE",
    # Per-line rewrite guards (#7705). A variant may carry a SUBSET of the
    # {"repetition", "shape", "word_budget"} bool keys; _apply_variant merges the
    # subset onto the module default, so the patched module always has all three.
    # A guard set False is not evaluated as a fault, so it cannot trigger a rewrite.
    "REWRITE_GUARDS",
    # Word-caps knob (#7705 follow-on). False strips every numeric length
    # constraint from the voice guides/shared rules and suppresses the
    # word_budget rewrite fault for that arm.
    "WORD_CAPS",
    # Wind-down/stop knobs (#7680, #7681, #7629). TICKS_RANGE and
    # OPEN_ENDED_MAX_TICKS raise a scene's turn count; WINDDOWN_TRIGGER and
    # STOP_CHECK choose how the scene decides it is done.
    "TICKS_RANGE",
    "WINDDOWN_TRIGGER",
    "STOP_CHECK",
    "OPEN_ENDED_MAX_TICKS",
    # Director knob (#7679, absorbs #7677). When enabled, run_simulation rolls
    # and directs each scene before the first turn (one Haiku call per day).
    "DIRECTOR",
    # #7791 P4 / RESEARCH_PLAN.md S3 R3. False (default) is production-identical:
    # speakers see only the recipe_context anchor. True appends the scenario's
    # judge_recipe_facts to that anchor so speakers argue from the same facts
    # the judge scores against.
    "SPEAKERS_SEE_JUDGE_RECIPE_FACTS",
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

# This lab-only prompt asks for a pairwise A/B verdict instead of a
# single-transcript score. Its candidate-version framing covers saved,
# transformed, and independently generated scenes. The character rules are
# copied verbatim from backend/admin/cron_routes.py's _JUDGE_SYSTEM_PROMPT;
# production code and its prompt are not changed here. Reuses the same 8 dimension *definitions* as
# cron_routes.py's judge (lines ~267-317) so the pairwise judge is scoring
# the same things the production judge scores, just comparatively, plus
# LAB_ONLY_JUDGE_DIMENSIONS above (lab-only - never fed back into the
# production judge prompt).
PAIRWISE_JUDGE_PROMPT_VERSION = "pairwise-v2-candidate-versions-character-rules"
# Part of the judge instrument alongside the model and the system prompt.
PAIRWISE_JUDGE_TEMPERATURE = 0.2
PAIRWISE_JUDGE_SYSTEM_PROMPT = (
    "You are a senior editorial judge for a food content site comparing TWO "
    "candidate dialogue transcripts for the SAME day, recipe concept, and "
    "cast of a six-person creative team (Margaret, Steph, Julian, Marcus, Devon, Ria) "
    "collaborating on a muffin-tin recipe. EXPECTED CAST below defines who appears "
    "in this scene. Transcript A and Transcript B are "
    "two candidate versions of the same scene. Decide which one "
    "is better on each dimension below, from a reader's perspective - which "
    "one would you publish?\n\n"
    "CHARACTER RULES:\n"
    "- Margaret: Blunt, short sentences, zero fluff, standards enforcer\n"
    "- Steph: Warm, diplomatic, NOT a nervous intern\n"
    "- Julian: Visual thinker, theatrical, cares about light/composition\n"
    "- Marcus: Literary, verbose, metaphor-heavy\n"
    "- Devon: Efficient, understated, speaks only when needed\n"
    "- Ria: Direct, platform-savvy, thinks in hooks and engagement, impatient with process\n\n"
    "DIMENSIONS:\n"
    "- title_fidelity: does the talk stay anchored to the named dish/hero "
    "ingredient, or does it wander into an unrelated tangent?\n"
    "- arc_resolution: does a problem a character raises actually get "
    "resolved, not reframed away or dropped?\n"
    "- voice_distinctiveness: are the characters who spoke separable blind, "
    "each sounding like the named character and nobody else?\n"
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


def _judge_instrument(judge_model: str) -> dict[str, Any]:
    """The complete judge instrument for `judge_model` (#7791): the full
    request `model_router.generate_judge_response` makes, minus the prompt
    text (from `model_router.judge_request_settings`), plus the lab-side
    parts that decide a verdict - the system prompt's sha and version and
    this module's JSON-retry bound. Saved with every judge orientation;
    `rejudge` calls a re-run V3 test-retest only when this whole dict is
    unchanged, so a setting added to the request later is covered without
    a new hand-written check."""
    return {
        **model_router.judge_request_settings(judge_model, PAIRWISE_JUDGE_TEMPERATURE),
        **_pairwise_evaluator_metadata(),
        "json_max_retries": _JUDGE_JSON_MAX_RETRIES,
    }


def _pairwise_evaluator_metadata() -> dict[str, str]:
    return {
        "evaluator_prompt_version": PAIRWISE_JUDGE_PROMPT_VERSION,
        "evaluator_prompt_sha256": hashlib.sha256(PAIRWISE_JUDGE_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
    }

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

# Set once, the first time the lab's own cost total cannot be read - see
# _warn_cost_summary_failure_once - so a broken cost log warns exactly once
# per process instead of once per checked call (ab/calibrate check it
# before every single generation and judge call).
_cost_summary_failure_warned = False

def _warn_cost_summary_failure_once(exc: Exception) -> None:
    global _cost_summary_failure_warned
    if _cost_summary_failure_warned:
        return
    print(
        "WARNING: cost cap check failed open - "
        f"backend.utils.model_router.get_cost_entries() raised {type(exc).__name__}: {exc} - "
        "--max-cost cannot be enforced until this is fixed; --max-calls remains the "
        "primary, always-available spending guard",
        file=sys.stderr,
    )
    _cost_summary_failure_warned = True

# Set once, the first time _lab_cost_total() hits an untrusted OpenRouter
# entry (see its docstring) - a separate warning from the read-failure one
# above, since this path fails CLOSED rather than open and Erik should be
# able to tell the two apart from stderr alone.
_untrusted_cost_entry_warned = False

def _warn_untrusted_cost_entry_once(exc: Exception) -> None:
    global _untrusted_cost_entry_warned
    if _untrusted_cost_entry_warned:
        return
    print(
        f"WARNING: cost cap check failed CLOSED - {exc} - "
        "aborting the run rather than treating unmetered OpenRouter spend as $0",
        file=sys.stderr,
    )
    _untrusted_cost_entry_warned = True

def _cost_summary_or_none() -> dict[str, Any] | None:
    """model_router's running totals, or None if the log cannot be read."""
    try:
        summary = model_router.get_cost_summary()
    except Exception as exc:
        _warn_cost_summary_failure_once(exc)
        return None
    return summary if isinstance(summary, dict) else None

class _UntrustedCostEntry(RuntimeError):
    """Raised by `_lab_cost_total` for an OpenRouter cost-log entry that
    reports neither a real `actual_cost` nor a nonzero `estimated_cost`.

    #7714 finding 1: `backend.utils.model_router.get_cost_summary()`'s
    `total_cost` sums ONLY `estimated_cost`, computed from
    `_COST_PER_M_TOKENS` - a table with no OpenRouter ids in it - so every
    OpenRouter call estimates to $0 there even though `_generate_openrouter`
    stores the real `usage.cost` as `actual_cost` right next to it. Treating
    that shape as $0 spend would let real, unmetered OpenRouter cost run
    straight past --max-cost, so `_would_exceed_cost` treats this as an
    immediate, fail-CLOSED cap hit instead of a read failure (which fails
    open)."""

def _lab_cost_total() -> float:
    """The lab's own running-cost total: sum, over every entry in
    `backend.utils.model_router.get_cost_entries()`, of that entry's
    `actual_cost` when present, else its `estimated_cost` - NOT
    `get_cost_summary()['total_cost']` (see `_UntrustedCostEntry`'s
    docstring for why that undercounts OpenRouter spend to $0).

    Raises `_UntrustedCostEntry` for an OpenRouter entry with neither a
    real `actual_cost` nor a nonzero `estimated_cost` - callers decide
    whether that fails open or closed (see `_would_exceed_cost` and
    `_total_cost_or_none`).
    """
    total = 0.0
    for entry in model_router.get_cost_entries():
        if not isinstance(entry, dict):
            continue
        actual = entry.get("actual_cost")
        if isinstance(actual, (int, float)) and not isinstance(actual, bool):
            total += float(actual)
            continue
        estimated = entry.get("estimated_cost")
        if isinstance(estimated, (int, float)) and not isinstance(estimated, bool):
            estimated = float(estimated)
        else:
            estimated = 0.0
        if entry.get("provider") == "openrouter" and estimated == 0.0:
            raise _UntrustedCostEntry(
                "openrouter cost entry for model "
                f"{entry.get('model')!r} reports no actual_cost and a zero "
                "estimate - refusing to count it as $0 spend"
            )
        total += estimated
    return total

def _total_cost_or_none() -> float | None:
    """Current lab-wide running cost total (see `_lab_cost_total`), or
    None on ANY failure to compute it - including an untrusted OpenRouter
    entry - after firing the one-time stderr warning above. This is the
    REPORTING-facing total (`ab --sweep`'s per-variant baseline and
    `cost_spent`), so it fails open to None uniformly rather than raising;
    `_would_exceed_cost` is the one caller that must tell "unknown" apart
    from "untrusted", and it calls `_lab_cost_total()` directly instead of
    going through this function."""
    try:
        return _lab_cost_total()
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

    A cost-log read failure does not disable the cap by raising - it
    fails open (returns False, i.e. "not yet exceeded") the same way
    _run_arm_and_count already tolerates a failed read, because
    --max-calls remains the primary, always-available spending guard
    regardless of whether cost tracking itself is working. An untrusted
    OpenRouter entry (see `_UntrustedCostEntry`) is NOT a read failure -
    it fails CLOSED (returns True) instead, since the alternative is
    silently letting unmetered OpenRouter spend run past --max-cost.
    """
    try:
        total_cost = _lab_cost_total()
    except _UntrustedCostEntry as exc:
        _warn_untrusted_cost_entry_once(exc)
        return True
    except Exception as exc:
        _warn_cost_summary_failure_once(exc)
        return False
    # `not (x < cap)`: a NaN total or cap counts as exceeded (fail closed).
    return not ((total_cost - baseline) < max_cost)


# ---------------------------------------------------------------------------
# OpenRouter (lab-only) key and cost accounting
# ---------------------------------------------------------------------------
def _provider_for(args: argparse.Namespace) -> str:
    """The provider a command invocation is running under.

    argparse always sets `provider` for ab/bench/calibrate; direct calls from
    tests (or internal callers) that predate the flag default to the
    production-direct Anthropic path.
    """
    return getattr(args, "provider", None) or "anthropic"


def _models_arg_for(args: argparse.Namespace) -> str | None:
    """The --models value, or None when this subcommand has no such flag
    (only `ab` gets --models) or it was not passed."""
    return getattr(args, "models", None)


def _resolve_lab_model_set(args: argparse.Namespace) -> "LabModelSet":
    """The LabModelSet an `ab` invocation uses - always the REAL ids, never
    collapsed to "template" by --dry-run, so provider_route reporting and the
    STOP_CHECK/haiku compatibility check both reason about the actual set even
    under a zero-cost dry run.

    --models is only meaningful with --provider openrouter: --provider
    anthropic is the production-direct path and always runs the default set.
    """
    name = _models_arg_for(args) or _LAB_MODELS.default
    if name != _LAB_MODELS.default and _provider_for(args) != "openrouter":
        raise SystemExit(
            f"conversation_lab: --models {name!r} is only meaningful with --provider "
            f"openrouter (--provider anthropic always uses the {_LAB_MODELS.default!r} set)"
        )
    return _LAB_MODELS.get(name)


def _refuse_if_stop_check_conflicts_with_models(
    args: argparse.Namespace, variant: dict[str, Any] | None,
) -> None:
    """STOP_CHECK['provider'] == "haiku" always calls real Anthropic Claude
    Haiku directly (backend/utils/stop_check.py's HAIKU_MODEL, through
    model_router with --provider anthropic) - it never routes through
    --models. Running a non-default model set (e.g. --models deepseek) with a
    haiku stop check would silently spend on Claude while everything else in
    the run is billed to a different vendor, so refuse instead of doing that
    quietly. The lab's own convention is STOP_CHECK provider="jev" (see
    scripts/simulate_dialogue_week.py); stop_check.py's haiku path stays out
    of scope for #7714 (it is not swappable via lab_models.json).

    STOP_CHECK is only ever consulted when WINDDOWN_TRIGGER == "check" -
    scripts/simulate_dialogue_week.py's run_simulation calls check_scene_done
    (which reads STOP_CHECK['provider']) inside the `elif WINDDOWN_TRIGGER ==
    "check":` branch of its per-tick loop, and nowhere else in the codebase.
    The "regex" (production default) and "off" triggers never call it, so an
    arm whose effective WINDDOWN_TRIGGER isn't "check" cannot spend on Claude
    no matter what STOP_CHECK says - refusing that arm was over-strict and
    blocked every WINDDOWN_TRIGGER="check" + STOP_CHECK provider="jev" variant
    from running with a non-default model set, since the unpatched control
    module default (WINDDOWN_TRIGGER="regex", STOP_CHECK provider="haiku")
    always looked like a conflict.

    Checks BOTH the control arm (the module's current, unpatched
    WINDDOWN_TRIGGER/STOP_CHECK - control is never variant-patched) and the
    variant arm (the variant's own WINDDOWN_TRIGGER/STOP_CHECK overrides, if
    any, else the same module defaults) - each arm's *effective* pairing.

    Runs under --dry-run too (unlike the OpenRouter key/balance preflight,
    which needs the network): this check only reads the lab-models registry
    and module attributes already loaded in memory, no network call and no
    credential read, so there is no cost reason to skip it. #7680's bug was
    found in a real run, not a dry run, specifically because this used to
    return early here - a --dry-run of the exact same variant/--models combo
    could not have caught it.
    """
    if _provider_for(args) != "openrouter":
        return
    model_set = _resolve_lab_model_set(args)
    if model_set.name == _LAB_MODELS.default:
        return

    control_trigger = simulate_module.WINDDOWN_TRIGGER
    control_provider = simulate_module.STOP_CHECK.get("provider")

    is_dict = isinstance(variant, dict)
    variant_trigger = (
        variant.get("WINDDOWN_TRIGGER")
        if is_dict and "WINDDOWN_TRIGGER" in variant
        else control_trigger
    )
    variant_stop_check = variant.get("STOP_CHECK") if is_dict else None
    variant_provider = (
        variant_stop_check.get("provider")
        if isinstance(variant_stop_check, dict) and "provider" in variant_stop_check
        else control_provider
    )

    control_conflict = control_trigger == "check" and control_provider == "haiku"
    variant_conflict = variant_trigger == "check" and variant_provider == "haiku"

    if control_conflict or variant_conflict:
        raise SystemExit(
            f"conversation_lab: --models {model_set.name!r} cannot run with STOP_CHECK "
            "provider='haiku' - stop_check.py's haiku provider always calls Anthropic "
            "Claude Haiku directly, never through --models, so this would silently spend "
            "on Claude while everything else runs on a different model set. Set STOP_CHECK "
            f"provider to 'jev' in the variant, or drop --models to use the "
            f"{_LAB_MODELS.default!r} default."
        )


def _provider_route_for(provider: str, model_set: "LabModelSet | None" = None) -> dict[str, Any] | None:
    if provider != "openrouter":
        return None
    if model_set is None:
        model_set = _DEFAULT_LAB_MODEL_SET
    return model_router.openrouter_provider_route(model_set.dialogue)


def _resolve_provider(args: argparse.Namespace, ledger_path: Any) -> str | None:
    """Which provider a CLI invocation uses.

    The CLI default is openrouter for ab/bench/calibrate/rejudge. A
    --budget-ledger run always uses the production-direct Anthropic path,
    because the guard only meters the Anthropic SDK - rejudge does not
    expose --budget-ledger (see `_add_budget_guard_options` call sites), so
    ledger_path is always None for it, but it still needs a default
    provider so `_openrouter_preflight` runs the same OpenRouter key/price
    checks every other paid command gets.
    """
    if args.command not in {"ab", "bench", "calibrate", "rejudge"}:
        return None
    explicit = getattr(args, "provider", None)
    if explicit:
        return explicit
    return "anthropic" if ledger_path is not None else "openrouter"


def _openrouter_preflight(args: argparse.Namespace) -> None:
    """Check the OpenRouter key limit before any generation and refuse if it
    cannot cover --max-cost. Prints limit and limit_remaining.

    Also fetches OpenRouter's per-token model prices ONCE (#7714 finding
    2) and refuses to start if a model this run will actually use has no
    published price - the mid-arm guard's --max-cost check reserves the
    NEXT call's worst-case cost for an OpenRouter model (see
    `_openrouter_worst_case_call_cost`), and a model with no price has no
    worst case to reserve. Never reached under --dry-run: `main()` only
    calls this when `not args.dry_run`.
    """
    global _OPENROUTER_KEY_BEFORE, _OPENROUTER_MODEL_PRICES
    _require_openrouter_key()
    key_info = _openrouter_fetch_key()
    _OPENROUTER_KEY_BEFORE = key_info
    print(
        "[openrouter] key limit: "
        f"${key_info['limit']:.2f}  limit_remaining: ${key_info['limit_remaining']:.2f}"
    )
    if not (key_info["limit_remaining"] >= args.max_cost):
        raise SystemExit(
            "conversation_lab: OpenRouter limit_remaining "
            f"${key_info['limit_remaining']:.2f} is below --max-cost ${args.max_cost:.2f}; "
            "refusing to start"
        )
    # The key limit is only a ceiling; spend comes out of the ACCOUNT balance.
    # 2026-09-27 the first paid run passed the key check with $49.62 of limit
    # remaining and died at its first judge call because the account held $0.56.
    balance = _openrouter_fetch_account_balance()
    print(f"[openrouter] account balance: ${balance:.2f}")
    if not (balance >= args.max_cost):
        raise SystemExit(
            f"conversation_lab: OpenRouter account balance ${balance:.2f} is below "
            f"--max-cost ${args.max_cost:.2f}; add credits before starting"
        )

    _OPENROUTER_MODEL_PRICES = _fetch_openrouter_model_prices()
    model_set = _resolve_lab_model_set(args)
    # Price only the models this command will call: rejudge and calibrate
    # judge saved transcripts and never generate dialogue.
    models_called = (
        (model_set.judge,) if args.command in {"rejudge", "calibrate"} else (model_set.dialogue, model_set.judge)
    )
    missing = sorted({m for m in models_called if m not in _OPENROUTER_MODEL_PRICES})
    if missing:
        raise SystemExit(
            "conversation_lab: OpenRouter has no published price for "
            f"{', '.join(missing)} - --max-cost cannot reserve this model's "
            "worst-case call cost; refusing to start"
        )


def _fetch_openrouter_model_prices() -> dict[str, tuple[float, float]]:
    """GET OpenRouter's /models endpoint; return {model_id: (prompt_price,
    completion_price)} in USD per token, parsed from each entry's
    `pricing.prompt`/`pricing.completion` (OpenRouter publishes these as
    strings). An entry missing either field, or with a non-numeric price,
    is left out - `_openrouter_preflight` treats an absent id as "no
    price" and fails closed."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ConversationLabError("OPENROUTER_API_KEY is not set")
    try:
        response = httpx.get(
            _OPENROUTER_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_OPENROUTER_KEY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ConversationLabError(f"openrouter models check failed: {exc}") from exc
    if response.status_code != 200:
        raise ConversationLabError(f"openrouter models check returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConversationLabError("openrouter models check returned malformed JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ConversationLabError("openrouter models check response is missing the data list")

    prices: dict[str, tuple[float, float]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        pricing = entry.get("pricing")
        if not isinstance(model_id, str) or not model_id or not isinstance(pricing, dict):
            continue
        try:
            prompt_price = float(pricing.get("prompt"))
            completion_price = float(pricing.get("completion"))
        except (TypeError, ValueError):
            continue
        # float() accepts "NaN", "inf" and negatives; any of those would make
        # the worst-case reservation NaN/inf/negative and the cap comparison
        # meaningless. Leave such a model unpriced so the preflight and the
        # pre-call guard refuse it (fail closed).
        if not (isfinite(prompt_price) and isfinite(completion_price)) or prompt_price < 0 or completion_price < 0:
            continue
        prices[model_id] = (prompt_price, completion_price)
    return prices


# Role markers and special tokens the chat template adds around the
# system and user messages - a generous allowance, not a measured value.
_OPENROUTER_PROMPT_OVERHEAD_TOKENS = 256


def _openrouter_worst_case_call_cost(model: str, prompt_bytes: int) -> float:
    """Conservative worst-case USD cost of ONE OpenRouter call to `model`
    whose prompt is `prompt_bytes` bytes of UTF-8 (#7714 finding 2).

    Input bound: one token per UTF-8 byte plus _OPENROUTER_PROMPT_OVERHEAD_TOKENS
    for the chat template. Byte-level BPE tokens cover at least one byte
    each, so a byte count is a true upper bound for any text - a
    characters/2 estimate undercounted non-ASCII prompts. That, at the
    model's per-token prompt price,
    plus `backend.utils.model_router.openrouter_max_tokens(model)` tokens
    (the full output ceiling, not an expected length - OpenRouter bills
    tokens actually generated, but a reasoning model can spend the WHOLE
    ceiling reasoning; see that function's docstring) at its per-token
    completion price.

    Returns float('inf') - an unconditional block - when `model` has no
    cached price: `_openrouter_preflight` already fails closed by
    refusing to START a run with an unpriced model in use, but this is
    the defense-in-depth fallback if the guard is ever asked about a
    model that check didn't cover.
    """
    prices = _OPENROUTER_MODEL_PRICES or {}
    price = prices.get(model)
    if price is None:
        return float("inf")
    prompt_price, completion_price = price
    prompt_tokens_bound = prompt_bytes + _OPENROUTER_PROMPT_OVERHEAD_TOKENS
    max_tokens = model_router.openrouter_max_tokens(model)
    return prompt_tokens_bound * prompt_price + max_tokens * completion_price


def _openrouter_fetch_account_balance() -> float:
    """GET OpenRouter's /credits endpoint; return total_credits - total_usage."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ConversationLabError("OPENROUTER_API_KEY is not set")
    try:
        response = httpx.get(
            _OPENROUTER_CREDITS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_OPENROUTER_KEY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ConversationLabError(f"openrouter credits check failed: {exc}") from exc
    if response.status_code != 200:
        raise ConversationLabError(f"openrouter credits check returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConversationLabError("openrouter credits check returned malformed JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ConversationLabError("openrouter credits check response is missing the data object")
    total_credits = data.get("total_credits")
    total_usage = data.get("total_usage")
    for field, value in (("total_credits", total_credits), ("total_usage", total_usage)):
        # json.loads accepts NaN/Infinity; a non-finite amount must not reach
        # the preflight comparison, where it would compare False and pass.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ConversationLabError(f"openrouter credits check {field} is invalid")
    return float(total_credits) - float(total_usage)


def _openrouter_fetch_key() -> dict[str, Any]:
    """GET OpenRouter's /key endpoint and return limit, remaining and usage."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise ConversationLabError("OPENROUTER_API_KEY is not set")
    try:
        response = httpx.get(
            _OPENROUTER_KEY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_OPENROUTER_KEY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        raise ConversationLabError(f"openrouter key check failed: {exc}") from exc
    if response.status_code != 200:
        raise ConversationLabError(f"openrouter key check returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ConversationLabError("openrouter key check returned malformed JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ConversationLabError("openrouter key check response is missing the data object")
    limit = data.get("limit")
    limit_remaining = data.get("limit_remaining")
    usage = data.get("usage")
    for field, value in (("limit", limit), ("limit_remaining", limit_remaining)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ConversationLabError(f"openrouter key check {field} is invalid")
    return {
        "limit": float(limit),
        "limit_remaining": float(limit_remaining),
        "usage": usage if isinstance(usage, (dict, int, float)) else None,
    }


def _openrouter_key_usage_report() -> dict[str, Any] | None:
    """Before/after OpenRouter key snapshots for the result JSON."""
    global _OPENROUTER_KEY_AFTER
    if _OPENROUTER_KEY_BEFORE is None:
        return None
    if _OPENROUTER_KEY_AFTER is None:
        try:
            _OPENROUTER_KEY_AFTER = _openrouter_fetch_key()
        except Exception as exc:
            return {
                "before": _OPENROUTER_KEY_BEFORE,
                "after": None,
                "after_error": f"{type(exc).__name__}: {exc}",
            }
    return {"before": _OPENROUTER_KEY_BEFORE, "after": _OPENROUTER_KEY_AFTER}


def _openrouter_router_cost_by_model() -> dict[str, float]:
    """OpenRouter usage.cost totals recorded by the model router."""
    totals: dict[str, float] = {}
    # No try/except: get_cost_entries() is a list copy, and a failure here must
    # not print as "$0 spent" (review of ffbae2c).
    entries = model_router.get_cost_entries()
    missing = 0
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("provider") != "openrouter":
            continue
        actual = entry.get("actual_cost")
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not isfinite(actual):
            missing += 1
            continue
        model = str(entry.get("model") or "unknown")
        key = f"openrouter/{model}"
        totals[key] = round(totals.get(key, 0.0) + float(actual), 9)
    if missing:
        print(
            f"[openrouter] WARNING: {missing} call(s) returned no usage.cost; "
            "cost_by_model undercounts them - compare against the key usage before/after",
            file=sys.stderr,
        )
    return totals


def _jev_router_cost_by_model() -> dict[str, float]:
    """Jev cost totals from the model router's ledger - EVERY HTTP attempt
    backend.utils.stop_check._record_jev_attempt recorded via
    record_external_cost (success, retry, and terminal failure), not just
    the successful ones scripts.simulate_dialogue_week.STOP_CHECK_LOG
    holds.

    #7714 round 2, finding 4: the OLD per-pair helper
    (`_jev_cost_by_model_from_log`, removed) derived cost from
    STOP_CHECK_LOG snapshots attached to each pair, which only ever
    contain the LAST successful check per tick - a retried/failed attempt
    was already charged against --max-cost (see `_lab_cost_total`), but
    never showed up in the REPORTED total, so the report could read lower
    than what the cap actually saw. This reads the SAME ledger the cap
    reads, so the two can never disagree.
    """
    totals: dict[str, float] = {}
    entries = model_router.get_cost_entries()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("provider") != "jev":
            continue
        actual = entry.get("actual_cost")
        if isinstance(actual, bool) or not isinstance(actual, (int, float)) or not isfinite(actual):
            continue
        model = str(entry.get("model") or "typesafe/jev-1.13")
        key = f"jev/{model}"
        totals[key] = round(totals.get(key, 0.0) + float(actual), 9)
    return totals


def _jev_cost_usd_total() -> float:
    """Total Jev spend, from the same router ledger --max-cost reads (see
    `_lab_cost_total`) - so `jev_cost_usd` in a report can never read
    lower than what the cap actually saw (#7714 finding 4)."""
    return round(sum(_jev_router_cost_by_model().values()), 9)


def _openrouter_cost_by_model_for_pairs(
    pairs: list[dict[str, Any]],
    partial_pairs: list[dict[str, Any]] | None = None,
) -> dict[str, float]:
    """OpenRouter dialogue/judge cost + Jev stop-check cost, both read from
    the model router's ledger (#7714 round 2, finding 4). `pairs`/
    `partial_pairs` are accepted for call-site compatibility but no longer
    read - Jev cost used to be derived from THEIR per-pair stop-check
    logs, which undercounted (see `_jev_router_cost_by_model`)."""
    del pairs, partial_pairs
    totals = _openrouter_router_cost_by_model()
    totals.update(_jev_router_cost_by_model())
    return totals


def _openrouter_cost_by_model_for_sweep(
    control_transcripts: dict[tuple[str, int], dict[str, Any]],
    variant_reports: dict[str, Any],
) -> dict[str, float]:
    """See `_openrouter_cost_by_model_for_pairs` - same fix, same reason;
    `control_transcripts`/`variant_reports` are no longer read."""
    del control_transcripts, variant_reports
    totals = _openrouter_router_cost_by_model()
    totals.update(_jev_router_cost_by_model())
    return totals


def _openrouter_cost_report(cost_by_model: dict[str, float]) -> dict[str, Any]:
    return {
        "cost_by_model": cost_by_model,
        "total_cost_usd": round(sum(cost_by_model.values()), 9),
    }


def _openrouter_fields_for(
    args: argparse.Namespace,
    cost_by_model: dict[str, float],
) -> dict[str, Any]:
    """The provider_route/cost/key fields added to every OpenRouter report."""
    provider = _provider_for(args)
    model_set = _resolve_lab_model_set(args) if provider == "openrouter" else None
    fields: dict[str, Any] = {"provider_route": _provider_route_for(provider, model_set)}
    if provider != "openrouter":
        return fields
    fields["models"] = {"set": model_set.name, "dialogue": model_set.dialogue, "judge": model_set.judge}
    fields.update(_openrouter_cost_report(cost_by_model))
    key_report = _openrouter_key_usage_report()
    if key_report is not None:
        fields["openrouter_key"] = key_report
    return fields


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
_ACTIVE_BUDGET_GUARD: contextvars.ContextVar[AnthropicBudgetGuard | None] = (
    contextvars.ContextVar("conversation_lab_budget_guard", default=None)
)


def _budget_checkpoint() -> None:
    guard = _ACTIVE_BUDGET_GUARD.get()
    if guard is not None:
        guard.raise_if_stopped()

def _budget_generation_attempts() -> int | None:
    """Read the shared ledger's provider-generation counter when guarded."""
    guard = _ACTIVE_BUDGET_GUARD.get()
    if guard is None:
        return None
    total = guard.summary().get("totals", {}).get("generation_attempts")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise BudgetGuardError("budget ledger has invalid generation_attempts")
    return total


def _budget_guard_stopped() -> bool:
    guard = _ACTIVE_BUDGET_GUARD.get()
    if guard is None:
        return False
    try:
        guard.raise_if_stopped()
    except BudgetGuardError:
        return True
    return False


def _annotate_budget_report(path: Path, payload: dict[str, Any]) -> None:
    guard = _ACTIVE_BUDGET_GUARD.get()
    if guard is None:
        return
    ledger_ref = os.path.relpath(guard.path.resolve(), ROOT)
    try:
        summary = guard.summary()
        metadata = {"status": summary["status"], "summary": summary}
    except Exception as exc:
        # Preserve already-collected run evidence even when the ledger itself
        # becomes unreadable. The command remains failed/aborted and main's
        # final checkpoint will fail closed; unknown spend is never zeroed.
        summary = None
        metadata = {
            "status": "unavailable",
            "summary": None,
            "summary_error": f"{type(exc).__name__}: {exc}",
        }
        payload["aborted"] = True
        if payload.get("error") is None:
            payload["error"] = "budget ledger summary unavailable; accounting is unknown"
    payload["budget_guard_ledger"] = {
        "ledger_ref": ledger_ref,
        "authoritative": True,
        **metadata,
        "cost_summary_note": (
            "The lab router cost_summary is an estimate; this separate ledger "
            "is authoritative for the guarded Anthropic request shapes."
        ),
    }

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
    _annotate_budget_report(path, payload)
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
    """build_system_prompt caches per-character prompts by (persona name,
    WORD_CAPS) — see scripts/simulate_dialogue_week.py's
    `_system_prompt_cache` — so a lever change to e.g.
    _SHARED_CHARACTER_RULES or WORD_CAPS is silently ignored for any
    character whose prompt was already built this process, unless the
    cache is cleared both when applying a variant and when restoring the
    control."""
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
    # Validate EVERY key before mutating ANY of them. Interleaving the two meant
    # a variant whose second key was malformed raised after the first had already
    # been installed, and _apply_variant raises before returning its restore map -
    # so the caller had nothing to restore from and the module stayed patched.
    validate_variant(module, variant)

    original: dict[str, Any] = {}
    for name, value in variant.items():
        original[name] = getattr(module, name)
        if name == "TICKS_RANGE":
            # Variant files are JSON, so pairs arrive as lists. run_simulation
            # unpacks them by position, which works either way, but the module's
            # own type is tuples - normalise on the way in so the patched module
            # looks exactly like the default shape.
            value = {day: tuple(pair) for day, pair in value.items()}
        elif name == "REWRITE_GUARDS":
            # validate_variant allows a SUBSET of the three bool guards; merge
            # the subset onto the module default so the patched module always
            # carries exactly {"repetition", "shape", "word_budget"}.
            value = {**getattr(module, name), **value}
        setattr(module, name, value)
    _clear_prompt_cache(module)
    return original

def validate_variant(module: Any, variant: dict[str, Any]) -> None:
    """Reject a structurally wrong variant. Safe to call before any generation.

    Public because the caller must run it BEFORE spending: the single-experiment
    path generates its control arm first and a sweep generates shared controls
    first, so validation that only happened inside _apply_variant ran after real
    paid calls. The old docstring claimed "before any API call" and that was
    false.
    """
    for name, value in variant.items():
        if not hasattr(module, name):
            raise ConversationLabError(
                f"variant file names unknown attribute {name!r} on {module.__name__} - "
                f"allowed levers: {', '.join(ALLOWED_VARIANT_ATTRS)}"
            )
        if callable(getattr(module, name)):
            raise ConversationLabError(
                f"refusing to patch {name!r} - it is a function, not a string/dict "
                "constant this variant mechanism can safely restore"
            )
        _validate_lever_shape(name, value)
    _validate_history_depth_invariant(module, variant)

def _validate_lever_shape(name: str, value: Any) -> None:
    """Reject a structurally wrong lever BEFORE any API call is made.

    _apply_variant used to setattr any dict straight onto the module. A
    HISTORY_DEPTH override missing the "late" key raised KeyError inside
    generate_turn the moment the run reached Friday - after the earlier days had
    already been generated and paid for, against the $5/experiment cap. Fail at
    patch time, naming the key, instead of mid-run.
    """
    if name in ("SHAPE_WINDOW", "SHAPE_MAX_IN_WINDOW"):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConversationLabError(
                f"{name} must be a positive integer, got {value!r}"
            )
        return
    if name == "WORD_BUDGET_TOLERANCE":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 1:
            raise ConversationLabError(
                f"WORD_BUDGET_TOLERANCE must be a number >= 1 (1.0 enforces the stated "
                f"maximum exactly; higher allows slack), got {value!r}"
            )
        return
    if name == "WINDDOWN_TRIGGER":
        if value not in ("regex", "off", "check"):
            raise ConversationLabError(
                f"WINDDOWN_TRIGGER must be one of 'regex', 'off', 'check', got {value!r}"
            )
        return
    if name == "STOP_CHECK":
        _validate_stop_check_shape(value)
        return
    if name == "TICKS_RANGE":
        _validate_ticks_range_shape(value)
        return
    if name == "OPEN_ENDED_MAX_TICKS":
        _validate_open_ended_max_ticks_shape(value)
        return
    if name == "DIRECTOR":
        _validate_director_shape(value)
        return
    if name == "REWRITE_GUARDS":
        _validate_rewrite_guards_shape(value)
        return
    if name == "WORD_CAPS":
        if not isinstance(value, bool):
            raise ConversationLabError(
                f"WORD_CAPS must be a bool, got {value!r}"
            )
        return
    if name == "SPEAKERS_SEE_JUDGE_RECIPE_FACTS":
        if not isinstance(value, bool):
            raise ConversationLabError(
                f"SPEAKERS_SEE_JUDGE_RECIPE_FACTS must be a bool, got {value!r}"
            )
        return
    if name != "HISTORY_DEPTH":
        return
    if not isinstance(value, dict):
        raise ConversationLabError(
            f"HISTORY_DEPTH must be a dict of {{'early'|'late': [opening_turn, later_turns]}}, "
            f"got {type(value).__name__}"
        )
    missing = sorted({"early", "late"} - set(value))
    if missing:
        raise ConversationLabError(
            f"HISTORY_DEPTH override is missing required key(s) {missing}. "
            "Both 'early' (mon-thu) and 'late' (fri-sun) must be present, or the "
            "run crashes partway through the week with the earlier days already paid for."
        )
    for key in ("early", "late"):
        pair = value[key]
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ConversationLabError(
                f"HISTORY_DEPTH['{key}'] must be a 2-item [opening_turn, later_turns], got {pair!r}"
            )
        if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in pair):
            raise ConversationLabError(
                f"HISTORY_DEPTH['{key}'] entries must be positive integers, got {pair!r}"
            )

def _validate_stop_check_shape(value: Any) -> None:
    expected_keys = {"provider", "decided_threshold", "require_pushback", "pushback_threshold"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        missing = sorted(expected_keys - (set(value) if isinstance(value, dict) else set()))
        extra = sorted((set(value) if isinstance(value, dict) else set()) - expected_keys)
        raise ConversationLabError(
            f"STOP_CHECK must be a dict with exactly the keys {sorted(expected_keys)}; "
            f"missing {missing}, unexpected {extra}, got {type(value).__name__}"
        )
    provider = value["provider"]
    if provider not in ("jev", "haiku"):
        raise ConversationLabError(
            f"STOP_CHECK['provider'] must be 'jev' or 'haiku', got {provider!r}"
        )
    decided_threshold = value["decided_threshold"]
    if (
        isinstance(decided_threshold, bool)
        or not isinstance(decided_threshold, (int, float))
        or not 0 <= decided_threshold <= 1
    ):
        raise ConversationLabError(
            f"STOP_CHECK['decided_threshold'] must be a number 0-1, got {decided_threshold!r}"
        )
    require_pushback = value["require_pushback"]
    if not isinstance(require_pushback, bool):
        raise ConversationLabError(
            f"STOP_CHECK['require_pushback'] must be a bool, got {require_pushback!r}"
        )
    pushback_threshold = value["pushback_threshold"]
    if (
        isinstance(pushback_threshold, bool)
        or not isinstance(pushback_threshold, (int, float))
        or not 0 <= pushback_threshold <= 1
    ):
        raise ConversationLabError(
            f"STOP_CHECK['pushback_threshold'] must be a number 0-1, got {pushback_threshold!r}"
        )

def _validate_ticks_range_shape(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise ConversationLabError(
            f"TICKS_RANGE must be a non-empty dict of day -> [lo, hi] ints, "
            f"got {type(value).__name__}"
        )
    for day, pair in value.items():
        if day not in simulate_module.DAY_ORDER:
            raise ConversationLabError(
                f"TICKS_RANGE has unknown day {day!r} (expected one of {simulate_module.DAY_ORDER})"
            )
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ConversationLabError(
                f"TICKS_RANGE[{day!r}] must be a 2-item [lo, hi], got {pair!r}"
            )
        lo, hi = pair
        if (
            isinstance(lo, bool) or not isinstance(lo, int)
            or isinstance(hi, bool) or not isinstance(hi, int)
        ):
            raise ConversationLabError(
                f"TICKS_RANGE[{day!r}] entries must be integers, got {pair!r}"
            )
        if not 1 <= lo <= hi:
            raise ConversationLabError(
                f"TICKS_RANGE[{day!r}] must satisfy 1 <= lo <= hi, got {pair!r}"
            )

def _validate_open_ended_max_ticks_shape(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise ConversationLabError(
            f"OPEN_ENDED_MAX_TICKS must be a non-empty dict of day -> cap >= 3, "
            f"got {type(value).__name__}"
        )
    for day, cap in value.items():
        if day not in simulate_module.DAY_ORDER:
            raise ConversationLabError(
                f"OPEN_ENDED_MAX_TICKS has unknown day {day!r} "
                f"(expected one of {simulate_module.DAY_ORDER})"
            )
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 3:
            raise ConversationLabError(
                f"OPEN_ENDED_MAX_TICKS[{day!r}] must be an integer >= 3, got {cap!r}"
            )

def _validate_director_shape(value: Any) -> None:
    expected_keys = {"enabled", "probability", "intensity", "rng_seed", "no_repeat_window"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        missing = sorted(expected_keys - (set(value) if isinstance(value, dict) else set()))
        extra = sorted((set(value) if isinstance(value, dict) else set()) - expected_keys)
        raise ConversationLabError(
            f"DIRECTOR must be a dict with exactly the keys {sorted(expected_keys)}; "
            f"missing {missing}, unexpected {extra}, got {type(value).__name__}"
        )
    enabled = value["enabled"]
    if not isinstance(enabled, bool):
        raise ConversationLabError(
            f"DIRECTOR['enabled'] must be a bool, got {enabled!r}"
        )
    probability = value["probability"]
    if (
        isinstance(probability, bool)
        or not isinstance(probability, (int, float))
        or not 0 <= probability <= 1
    ):
        raise ConversationLabError(
            f"DIRECTOR['probability'] must be a number 0-1, got {probability!r}"
        )
    intensity = value["intensity"]
    if isinstance(intensity, bool) or not isinstance(intensity, int) or not 1 <= intensity <= 5:
        raise ConversationLabError(
            f"DIRECTOR['intensity'] must be an integer 1-5, got {intensity!r}"
        )
    rng_seed = value["rng_seed"]
    if isinstance(rng_seed, bool) or not isinstance(rng_seed, int):
        raise ConversationLabError(
            f"DIRECTOR['rng_seed'] must be an int, got {rng_seed!r}"
        )
    no_repeat_window = value["no_repeat_window"]
    if (
        isinstance(no_repeat_window, bool)
        or not isinstance(no_repeat_window, int)
        or no_repeat_window < 0
    ):
        raise ConversationLabError(
            f"DIRECTOR['no_repeat_window'] must be an integer >= 0, got {no_repeat_window!r}"
        )

def _validate_rewrite_guards_shape(value: Any) -> None:
    """REWRITE_GUARDS accepts a non-empty SUBSET of the three bool guards.

    A variant file may override only the guards it cares about - the merge in
    _apply_variant fills in the missing keys from the module default, so the
    patched module always carries exactly {'repetition', 'shape',
    'word_budget'} with bool values.
    """
    expected_keys = {"repetition", "shape", "word_budget"}
    if not isinstance(value, dict) or not value or not set(value) <= expected_keys:
        extra = sorted((set(value) if isinstance(value, dict) else set()) - expected_keys)
        raise ConversationLabError(
            f"REWRITE_GUARDS must be a non-empty dict with a subset of keys "
            f"{sorted(expected_keys)}; unexpected {extra}, got {type(value).__name__}"
        )
    for key, flag in value.items():
        if not isinstance(flag, bool):
            raise ConversationLabError(
                f"REWRITE_GUARDS[{key!r}] must be a bool, got {flag!r}"
            )

def _validate_history_depth_invariant(module: Any, variant: dict[str, Any]) -> None:
    """Refuse a variant whose max tick count would swallow the history floor.

    HISTORY_DEPTH's later_turns floor must stay ABOVE the largest tick count a
    scene can run (scripts/simulate_dialogue_week.py's comment above
    HISTORY_DEPTH): once a scene's own premise scrolls out of the window nobody
    can answer or close it. A variant that raises TICKS_RANGE upper bounds or
    OPEN_ENDED_MAX_TICKS toward that floor must also raise HISTORY_DEPTH in the
    SAME variant, or it would only fail mid-run, after paid calls.
    """
    history = variant.get("HISTORY_DEPTH")
    if history is None:
        history = getattr(module, "HISTORY_DEPTH")

    def later_turns(day: str) -> int:
        key = "late" if day in ("friday", "saturday", "sunday") else "early"
        return history[key][1]

    problems: list[str] = []
    ticks_range = variant.get("TICKS_RANGE")
    if isinstance(ticks_range, dict):
        for day, pair in ticks_range.items():
            if pair[1] >= later_turns(day):
                problems.append(
                    f"TICKS_RANGE[{day!r}] upper bound {pair[1]} reaches "
                    f"HISTORY_DEPTH later_turns {later_turns(day)}"
                )
    open_ended = variant.get("OPEN_ENDED_MAX_TICKS")
    if isinstance(open_ended, dict):
        for day, cap in open_ended.items():
            if cap >= later_turns(day):
                problems.append(
                    f"OPEN_ENDED_MAX_TICKS[{day!r}] cap {cap} reaches "
                    f"HISTORY_DEPTH later_turns {later_turns(day)}"
                )
    if problems:
        raise ConversationLabError(
            "; ".join(problems) + ". Set HISTORY_DEPTH higher in the same variant."
        )

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
    photography_context: dict[str, Any] | None = None,
    image_paths: list[Any] | None = None,
    prior_lines: list[str] | None = None,
    message_sink: list | None = None,
    recipe_facts: str | None = None,
) -> dict[str, Any]:
    """Call run_simulation with the exact production call shape.

    Mirrors backend/admin/cron_routes.py's `_generate_dialogue` (~line
    228) and .scratch/regen_w36.py: mode="openai", prompt_style="scene",
    ticks_per_day=0, plus a recipe_context anchor. --dry-run substitutes
    mode="template" for a zero-API-call plumbing check.

    `prior_lines` (frozen earlier days from `conversation_lab freeze`)
    seed run_simulation's `initial_recent_lines` so a stage-only run sees
    exactly the lines a full-week run would have accumulated by that day.
    `initial_highlights` stays None on purpose: run_simulation only builds
    week highlights when mode=="llm" (see its `_generate_day_highlights`
    call site), and production/this lab call with mode="openai" - so a real
    full-week openai run would carry an empty week-highlights list too.

    `message_sink`, when given, is forwarded UNCHANGED to
    `run_simulation`'s own `message_sink` (#7714) - see that function's
    docstring. `_run_arm_and_count` passes one so it can recover whatever
    turns were already generated (and paid for) if this call raises
    partway through, most commonly a LabBudgetAbort from the ambient
    mid-arm guard.

    `recipe_facts` (#7791 P4), when given, is forwarded UNCHANGED to
    `run_simulation`'s own `recipe_facts` - it only reaches a speaker prompt
    when the SPEAKERS_SEE_JUDGE_RECIPE_FACTS variant lever is True (default
    False, production-identical); passing it here costs nothing when the
    lever is off.
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
        image_paths=copy.deepcopy(image_paths) if image_paths is not None else [],
        photography_context=copy.deepcopy(photography_context),
        recipe_context=recipe_context,
        recipe_facts=recipe_facts,
        initial_highlights=None,
        initial_recent_lines=list(prior_lines) if prior_lines is not None else None,
        message_sink=message_sink,
    )

class LabBudgetAbort(BaseException):
    """Raised by the mid-arm budget guard (see `_installed_budget_guard`)
    the moment --max-calls or --max-cost would be exceeded by the very
    next paid call/attempt - checked INSIDE a running arm, not only at the
    coarser arm/judge boundaries the existing `budget.would_exceed(
    arm_call_reservation)`/`_would_exceed_cost(max_cost)` checks already
    guard. #7714 finding 3: an open-ended arm (OPEN_ENDED_MAX_TICKS) can
    run well past either cap between one boundary check and the next, and
    every Jev stop-check HTTP attempt (#7714 finding 2) never reached
    either cap at all before this guard existed.

    Deliberately derives from BaseException, not Exception (#7714 round 2,
    finding 3) - the SAME family as KeyboardInterrupt/SystemExit. This
    abort has to travel out through arbitrary library code on the arm's
    call path - backend.utils.director's `except Exception as exc: raise
    DirectorError(...)`, backend.utils.stop_check's `_check_haiku`
    `except Exception as exc: raise StopCheckError(...)`,
    scripts.simulate_dialogue_week's best-effort highlight/memory
    generation `except Exception: continue` - none of which this module
    is allowed to special-case for a lab-only signal, and any of which
    silently converted or swallowed the OLD RuntimeError-based abort,
    which is exactly what happened in the round-1 review. Only a caller
    that names LabBudgetAbort explicitly can catch it; every ordinary
    `except Exception`/`except (Exception, ...)` in between now lets it
    straight through, by construction.

    Every command-level catch that is SUPPOSED to catch this (cmd_ab,
    cmd_bench, cmd_calibrate, and their sweep/testbed helpers) does so by
    name - `except LabBudgetAbort` or an explicit tuple entry - and turns
    it into a clean aborted/partial result, exactly like the existing
    boundary checks."""

@contextmanager
def _installed_budget_guard(budget: CallBudget, max_cost: float | None, baseline_cost: float = 0.0):
    """Install a pre-call hook on backend.utils.model_router and a
    pre-attempt hook on backend.utils.stop_check for the duration of the
    `with` block. Both hooks are the SAME closure: the moment the next
    paid unit (a model_router call, or a single Jev HTTP attempt/retry)
    would push the running call count past --max-calls or the running
    cost past --max-cost (relative to `baseline_cost` - see
    `_would_exceed_cost`), it raises LabBudgetAbort instead of letting the
    call happen. `max_cost=None` disables the cost side entirely (used by
    `_generate_sweep_control`'s shared control loop, which --max-cost
    never governs - it is a per-variant cap, see `_run_sweep_variant`).

    #7714 round 2: this is now installed ONCE per command-level scope -
    an entire `_generate_and_judge_pairs`/`_run_sweep_variant` call, an
    entire sweep control loop, an entire bench/calibrate run - covering
    EVERY arm inside that scope by construction, rather than needing to
    be threaded through every individual `_run_arm_and_count`/generation
    call site (round 1's approach, which is exactly how `_generate_sweep_
    control` and `cmd_bench` were missed).

    The call-count side tracks a LIVE delta from a baseline (`_calls_now()`)
    captured ONCE here, at install time, and compares it DIRECTLY against
    `budget.max_calls` - it never adds `budget.used`. `budget.used` only
    advances when `budget.record()` runs, between arms; since one guard
    installation can span MANY arms, adding `budget.used` to a live delta
    measured from a baseline that predates those same already-recorded
    arms would double-count every arm that finished before the one
    currently in flight. The live delta alone, measured from install time,
    is the exact total spend since this guard was installed, whether or
    not any of it has been `record()`ed yet.

    Fails OPEN on the calls side when the counter cannot be read at all
    (`_calls_now()` returns None) - the coarser `budget.would_exceed(...)`
    boundary checks remain the fallback guard, same philosophy as
    `_would_exceed_cost`'s own read-failure handling.

    Restores whatever hook was installed before (normally None) on exit,
    even on exception, so any code path that never opens this context -
    every production call, and every existing test that predates #7714's
    mid-arm guard - stays byte-identical.
    """
    calls_before = _calls_now()
    # Calls this budget already recorded before the guard went in - e.g.
    # earlier scenarios of an `ab --testbed` run, which shares one budget
    # across every scenario but installs a fresh guard per scenario. Snapshot
    # once: anything recorded later inside this scope is already in the live
    # delta, so reading `budget.used` live would double-count it.
    used_before = budget.used

    def _guard(provider: str | None = None, model: str | None = None, prompt_bytes: int | None = None) -> None:
        """`provider`/`model`/`prompt_bytes` describe the NEXT call about
        to be made (#7714 finding 2): `model_router.set_pre_call_hook`
        passes the real triple for every generation/judge call;
        `stop_check.set_pre_attempt_hook` calls with no arguments (a Jev
        HTTP attempt has no model_router provider/model of its own), which
        is exactly how the branches below tell a Jev attempt apart from an
        OpenRouter/direct-provider call without needing a separate flag.
        """
        calls_blocked = False
        if calls_before is not None:
            calls_now = _calls_now()
            live_total = calls_now - calls_before if (calls_now is not None and calls_now > calls_before) else 0
            calls_blocked = used_before + live_total + 1 > budget.max_calls
        cost_blocked = False
        if max_cost is not None:
            if provider == "openrouter":
                # #7714 finding 2: --max-cost used to admit a call whenever
                # ANY money remained, regardless of that call's possible
                # cost - with a 32768-token completion ceiling
                # (openrouter_max_tokens), one call can overshoot the cap
                # materially. Reserve its worst case instead of just
                # checking the running total.
                worst_case = _openrouter_worst_case_call_cost(model or "", prompt_bytes or 0)
                # An unreadable total (e.g. an earlier OpenRouter entry with
                # no usage.cost) blocks: admitting the call would spend
                # against a cap nobody can check.
                total = _total_cost_or_none()
                # `not (x <= cap)` rather than `x > cap`: a NaN anywhere
                # (total, reservation or the cap itself) blocks instead of
                # comparing False and admitting the call.
                cost_blocked = total is None or not ((total - baseline_cost) + worst_case <= max_cost)
            elif provider is None:
                # A Jev HTTP attempt (stop_check's hook call) - no
                # per-model price to look up, so reserve the same
                # documented conservative per-attempt estimate the cost
                # log itself charges a failed/unmetered attempt (see
                # backend.utils.stop_check._JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD).
                total = _total_cost_or_none()
                cost_blocked = (
                    total is None
                    or not ((total - baseline_cost) + stop_check._JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD <= max_cost)
                )
            else:
                # Non-OpenRouter direct providers (anthropic/openai/google):
                # unchanged - the estimated-cost table's absolute running
                # total, no worst-case reservation (#7714 finding 2 scopes
                # the reservation fix to OpenRouter, whose reasoning-model
                # output ceiling is what let a single call overshoot).
                cost_blocked = _would_exceed_cost(max_cost, baseline=baseline_cost)
        if calls_blocked or cost_blocked:
            raise LabBudgetAbort(
                "--max-calls or --max-cost would be exceeded by the next paid call/attempt"
            )

    previous_router_hook = model_router.set_pre_call_hook(_guard)
    previous_stop_check_hook = stop_check.set_pre_attempt_hook(_guard)
    try:
        yield
    finally:
        model_router.set_pre_call_hook(previous_router_hook)
        stop_check.set_pre_attempt_hook(previous_stop_check_hook)

def _run_arm_and_count(
    concept: str,
    stage: str,
    run_index: int,
    recipe_context: str | None,
    mode: str,
    default_model: str,
    prior_lines: list[str] | None = None,
    recipe_facts: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Run one arm and estimate its call cost.

    Prefers the actual delta in
    backend.utils.model_router.get_cost_summary()['total_calls'] (a real
    LLM call happened); falls back to the transcript's message count as a
    lower bound when the cost log did not move (mode="template",
    --dry-run, or a monkeypatched run_simulation in tests).

    Installs no guard itself (#7714 round 2) - a caller wraps the SCOPE
    this arm runs inside with `_installed_budget_guard` (see that
    function's docstring for why one guard per scope, not per arm, is the
    right granularity). If `_run_arm` raises for ANY reason - most
    commonly `LabBudgetAbort` from that ambient guard, but any exception
    after real paid calls happened - the exception is annotated with:

    - `conversation_lab_calls_made`: the call delta actually observed
      before it happened. Callers read this to record real spend instead
      of silently reporting zero (#7714 finding 2) - `budget.record()`
      otherwise only happens on normal return, which an abort or any
      other mid-arm failure never reaches.
    - `conversation_lab_partial_messages` (#7714 round 3): whatever turns
      `run_simulation` had already generated - real, paid work - before
      it raised, in the SAME `[m.__dict__ for m in messages]` shape its
      own "messages" field uses on a normal return. `run_simulation`
      keeps its `messages` list local and only returns it at the very
      end, so recovering it on a mid-run exception requires handing it a
      pre-created list (`message_sink`) it appends into as it goes -
      that list is still readable here even though `_run_arm` itself
      never got a return value.

    Neither attribute is overwritten if a deeper frame already set it
    (e.g. a nested arm invocation this module doesn't currently have, but
    kept defensive to match the existing `conversation_lab_calls_used`/
    `conversation_lab_calls_made` convention elsewhere in this file).
    """
    try:
        before = model_router.get_cost_summary().get("total_calls", 0)
    except Exception:
        before = 0
    sink: list = []
    try:
        result = _run_arm(
            concept, stage, run_index, recipe_context, mode, default_model,
            prior_lines=prior_lines, message_sink=sink, recipe_facts=recipe_facts,
        )
    except BaseException as exc:
        if not hasattr(exc, "conversation_lab_calls_made"):
            try:
                after = model_router.get_cost_summary().get("total_calls", 0)
            except Exception:
                after = before
            setattr(exc, "conversation_lab_calls_made", max(after - before, 0))
        if not hasattr(exc, "conversation_lab_partial_messages"):
            try:
                setattr(exc, "conversation_lab_partial_messages", [m.__dict__ for m in sink])
            except Exception:
                setattr(exc, "conversation_lab_partial_messages", [])
        raise
    try:
        after = model_router.get_cost_summary().get("total_calls", 0)
    except Exception:
        after = 0
    delta = after - before
    message_count = len(result.get("messages", []))
    calls = delta if delta > 0 else message_count
    return result, calls

def _snapshot_stop_check_log() -> list[dict[str, Any]]:
    """Copy the simulator's STOP_CHECK_LOG (a list of dicts) after one arm."""
    log = getattr(simulate_module, "STOP_CHECK_LOG", [])
    return list(log) if isinstance(log, list) else []

def _snapshot_director_log() -> list[dict[str, Any]]:
    """Copy the simulator's DIRECTOR_LOG (a list of dicts) after one arm."""
    log = getattr(simulate_module, "DIRECTOR_LOG", [])
    return list(log) if isinstance(log, list) else []

def _snapshot_rewrite_log() -> list[dict[str, Any]]:
    """Copy the simulator's REWRITE_LOG (a list of dicts) after one arm."""
    log = getattr(simulate_module, "REWRITE_LOG", [])
    return list(log) if isinstance(log, list) else []

def _rewrite_summary(entries: Any) -> dict[str, Any]:
    """Per-arm rewrite accounting: lines, rewritten count/rate, fault counts,
    and CoT-guard retry count (#7705)."""
    entries = [e for e in (entries or []) if isinstance(e, dict)]
    rewritten = sum(1 for e in entries if e.get("rewritten") is True)
    fault_counts: Counter[str] = Counter()
    cot_retry = 0
    for entry in entries:
        faults = entry.get("faults")
        if isinstance(faults, list):
            for fault in faults:
                if isinstance(fault, str):
                    fault_counts[fault] += 1
        if entry.get("cot_retry") is True:
            cot_retry += 1
    n = len(entries)
    return {
        "lines": n,
        "rewritten": rewritten,
        "rewrite_rate": round(rewritten / n, 4) if n else 0.0,
        "fault_counts": dict(fault_counts),
        "cot_retry": cot_retry,
    }

def _length_stats(messages: Any) -> dict[str, dict[str, float | int | None]]:
    """mean/median/max words per line, per character, for one arm's transcript.

    Words are counted with `len(line.split())` on each message's `message`
    field — the same counting the word_budget rewrite guard uses — so the
    length stats in a lab report speak the same language as the guard.
    """
    by_char: dict[str, list[int]] = {}
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        character = str(message.get("character") or "?")
        text = str(message.get("message") or "")
        by_char.setdefault(character, []).append(len(text.split()))

    stats: dict[str, dict[str, float | int | None]] = {}
    for character, counts in sorted(by_char.items()):
        stats[character] = {
            "lines": len(counts),
            "mean": round(mean(counts), 3) if counts else None,
            "median": round(median(counts), 3) if counts else None,
            "max": max(counts) if counts else None,
        }
    return stats


def _pair_arm_length_stats(pairs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-arm length stats across every completed pair in an `ab` report."""
    return {
        "control": _length_stats(
            [message for pair in pairs for message in (pair.get("control_messages") or [])]
        ),
        "variant": _length_stats(
            [message for pair in pairs for message in (pair.get("variant_messages") or [])]
        ),
    }


def _pair_arm_rewrite_summaries(pairs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The control/variant rewrite_summary block for a set of completed pairs."""
    return {
        "control": _rewrite_summary(
            [entry for pair in pairs for entry in (pair.get("control_rewrite_log") or [])]
        ),
        "variant": _rewrite_summary(
            [entry for pair in pairs for entry in (pair.get("variant_rewrite_log") or [])]
        ),
    }

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
    recipe_facts: str | None = None,
) -> str:
    recipe_section = f"{recipe_context}\n" if recipe_context else ""
    # Ground truth the speakers never saw. Without it the lab judge scores
    # technical_credibility against the same abbreviated anchor that produced the
    # claim - the blind spot #7104 fixed in production and left here, which made
    # the W38 lamination scenario decorative.
    facts_section = f"{recipe_facts}\n" if recipe_facts else ""
    roster_line = f"Expected cast for {stage}: {', '.join(expected_cast)}\n"
    return (
        f"Recipe concept: {concept}\n"
        f"{recipe_section}"
        f"{facts_section}"
        f"{roster_line}"
        f"TRANSCRIPT A:\n{_format_transcript(first_messages)}\n\n"
        f"TRANSCRIPT B:\n{_format_transcript(second_messages)}\n\n"
        "Score this pair and return the JSON verdict described in your instructions."
    )

def _extract_first_json_object(raw: str) -> str | None:
    """Slice out the first BALANCED {...} object in `raw`, honoring quoted
    string literals (so a brace inside "reason": "uses {braces} in prose"
    never miscounts) and escapes inside those strings.

    This is "first complete JSON object", not "first '{' to last '}'" - a
    judge that keeps talking after the JSON (or wraps it in a markdown fence
    with commentary after the closing fence) must not corrupt the slice by
    dragging in trailing braces that aren't part of the verdict. Returns None
    when there's no '{' at all, or the object never closes (truncated output -
    #7714, 09-27: one truncated Opus verdict aborted a whole paid run).
    """
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _parse_judge_json(raw: str) -> dict[str, Any] | None:
    """Tolerant parse: extract the first complete JSON object (see
    _extract_first_json_object) and parse it. Same intent as
    backend/admin/cron_routes.py's `_parse_judge_json` (~line 313) - judge
    models occasionally wrap JSON in a markdown fence or add a sentence of
    preamble/trailing chatter despite being told not to. Reimplemented
    locally (not imported) so this module does not depend on a private
    helper in a file it is not allowed to edit.
    """
    candidate = _extract_first_json_object(raw)
    if candidate is None:
        return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


# A truncated/unparseable judge response gets this many retries of the SAME
# orientation call before _judge_orientation gives up (#7714, 09-27: one
# truncated Opus verdict aborted a whole paid run). A structurally invalid
# but PARSEABLE response (missing "winner", wrong-typed per_dimension, ...) is
# never retried - that would loosen validation instead of working around a
# transport/truncation glitch.
_JUDGE_JSON_MAX_RETRIES = 2

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
    recipe_facts: str | None = None,
    evidence: dict[str, Any] | None = None,
    budget: "CallBudget | None" = None,
    max_cost: float | None = None,
    baseline: float = 0.0,
    prebuilt_prompt: str | None = None,
) -> dict[str, str]:
    """Judge once with A=first_arm, B=second_arm; map the A/B verdict back to arm labels.

    `budget`/`max_cost`/`baseline` are OPTIONAL and gate only the RETRY
    attempts (see `_JUDGE_JSON_MAX_RETRIES`) - the first attempt is always
    made; it was already pre-checked by the caller (every `_run_judge_
    orientation` call site checks `budget.would_exceed(1)` and
    `_would_exceed_cost` before invoking this orientation at all). Passing
    `None` for either disables that dimension's retry cap (used by direct
    unit-test calls that don't exercise budget/cost enforcement).

    `prebuilt_prompt` (#7791 P2, `rejudge`) skips `_build_pairwise_prompt`
    entirely and sends this exact text instead - used to re-judge a saved
    `ab`/`calibrate` result's ALREADY-BUILT prompt (stored verbatim in that
    orientation's `evidence["prompt"]`) under a different judge model,
    without needing to reconstruct concept/stage/recipe_context/
    expected_cast/recipe_facts, which single-concept `ab` results do not
    even persist at the top level. `concept`/`stage`/`recipe_context`/
    `expected_cast`/`recipe_facts`/`first_messages`/`second_messages` are
    then unused for prompt-building (still accepted so `_run_judge_
    orientation`'s call shape stays uniform across every caller)."""
    prompt = prebuilt_prompt if prebuilt_prompt is not None else _build_pairwise_prompt(
        concept, stage, recipe_context, expected_cast, first_messages, second_messages,
        recipe_facts=recipe_facts,
    )
    mapping = {"A": first_arm, "B": second_arm, "tie": "tie"}
    if evidence is not None:
        evidence.update({
            "prompt": prompt,
            "system_prompt": PAIRWISE_JUDGE_SYSTEM_PROMPT,
            "model": judge_model,
            "temperature": PAIRWISE_JUDGE_TEMPERATURE,
            "mapping": mapping,
            "first_arm": first_arm,
            "second_arm": second_arm,
        })
        # Describing the instrument must never cost the evidence above: a
        # model id the router cannot parse is recorded, not raised here (the
        # judge call itself raises on it next). rejudge treats a missing
        # instrument as unverifiable, never as V3.
        try:
            evidence["judge_instrument"] = _judge_instrument(judge_model)
        except RuntimeError as exc:
            evidence["judge_instrument"] = None
            evidence["judge_instrument_error"] = str(exc)
    before_attempts = _budget_generation_attempts()
    if evidence is not None:
        evidence["guard_generation_attempts_before"] = before_attempts
        evidence["model_router_invoked"] = True

    raw = ""
    parsed: dict[str, Any] | None = None
    retries_used = 0
    retry_blocked_by: str | None = None
    attempt = 0
    while True:
        try:
            raw = model_router.generate_judge_response(
                prompt=prompt,
                system_prompt=PAIRWISE_JUDGE_SYSTEM_PROMPT,
                model=judge_model,
                temperature=PAIRWISE_JUDGE_TEMPERATURE,
            )
        except BaseException as original_error:
            if evidence is not None:
                evidence["judge_retries"] = retries_used
                try:
                    evidence["guard_generation_attempts_after"] = _budget_generation_attempts()
                except BaseException as snapshot_error:
                    evidence["generation_attempt_snapshot_error"] = (
                        f"{type(snapshot_error).__name__}: {snapshot_error}"
                    )
            raise
        if evidence is not None:
            evidence["raw_response"] = raw
        try:
            after_attempts = _budget_generation_attempts()
        except BaseException as snapshot_error:
            if evidence is not None:
                evidence["generation_attempt_snapshot_error"] = (
                    f"{type(snapshot_error).__name__}: {snapshot_error}"
                )
            raise
        if evidence is not None:
            evidence["guard_generation_attempts_after"] = after_attempts
        parsed = _parse_judge_json(raw)
        if parsed is not None:
            break
        if attempt >= _JUDGE_JSON_MAX_RETRIES:
            break
        # About to spend ONE MORE real call on a retry of this SAME
        # orientation. `budget.record()` only happens after the whole
        # orientation finishes (see `_run_judge_orientation`), so
        # `budget.used` does not yet reflect the `attempt + 1` real calls
        # already made here - add them back in before checking the cap.
        # Without this, an orientation that starts right at the edge of
        # --max-calls/--max-cost can overshoot both by up to
        # `_JUDGE_JSON_MAX_RETRIES` extra billed judge calls (#7714 follow-up).
        calls_blocked = budget is not None and budget.would_exceed(attempt + 2)
        cost_blocked = max_cost is not None and _would_exceed_cost(max_cost, baseline=baseline)
        if calls_blocked or cost_blocked:
            retry_blocked_by = "calls" if calls_blocked else "cost"
            break
        retries_used += 1
        attempt += 1
    if evidence is not None:
        evidence["judge_retries"] = retries_used
        if retry_blocked_by is not None:
            evidence["judge_retry_blocked_by_cap"] = retry_blocked_by

    if parsed is None:
        raise ConversationLabError(
            f"pairwise judge returned unparseable output after {retries_used} "
            f"retr{'y' if retries_used == 1 else 'ies'}: {raw[:200]!r}"
        )

    if "winner" not in parsed:
        raise ConversationLabError("pairwise judge response is missing winner")
    winner_value = _normalize_verdict_value(parsed["winner"], field="winner")
    result: dict[str, str] = {"overall": mapping[winner_value]}
    per_dimension = parsed.get("per_dimension")
    if not isinstance(per_dimension, dict):
        raise ConversationLabError("pairwise judge response per_dimension must be an object")
    for dim in ALL_JUDGE_DIMENSIONS:
        if dim not in per_dimension:
            raise ConversationLabError(f"pairwise judge response is missing per_dimension.{dim}")
        value = _normalize_verdict_value(per_dimension[dim], field=f"per_dimension.{dim}")
        result[dim] = mapping[value]
    result["reason"] = str(parsed.get("reason", ""))
    return result

def _normalize_verdict_value(raw: Any, *, field: str) -> str:
    """Normalise a judge verdict value's case and whitespace, and validate
    it is really one of A/B/tie.

    Only string verdicts are accepted. Missing, null, and mistyped values
    are malformed scorecards, not ties.
    """
    if not isinstance(raw, str):
        raise ConversationLabError(
            f"pairwise judge returned an invalid {field} verdict: {raw!r} (expected a string)"
        )
    text = raw.strip().upper()
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

def _orientation_diagnostics(first: dict[str, str], second: dict[str, str]) -> dict[str, dict[str, str]]:
    """Explain conservative ties without changing the combined decision."""
    diagnostics = {}
    for key in ("overall", *ALL_JUDGE_DIMENSIONS):
        a, b = first[key], second[key]
        if a == b == "tie":
            status = "unanimous_tie"
        elif a != b:
            status = "orientation_disagreement"
        else:
            status = "agreement"
        diagnostics[key] = {"status": status, "first": a, "second": b}
    return diagnostics

def _run_judge_orientation(
    *, orientation: str, pending_pair: dict[str, Any], budget: "CallBudget", **kwargs: Any,
) -> dict[str, str]:
    """Record evidence even when generation/parsing fails; count each invocation.

    `budget` is always forwarded into `_judge_orientation` (in addition to
    being used here for `.record()`) so a truncated-response retry can be
    capped mid-orientation; pass `max_cost`/`baseline` through `**kwargs`
    from the call site for the same reason - see `_judge_orientation`."""
    evidence: dict[str, Any] = {}
    result = None
    original_error: BaseException | None = None
    original_traceback = None
    try:
        result = _judge_orientation(**kwargs, evidence=evidence, budget=budget)
    except BaseException as exc:
        original_error = exc
        original_traceback = exc.__traceback__

    # A retried judge call (see _JUDGE_JSON_MAX_RETRIES) makes MORE than one
    # real generation attempt for this single orientation - the guard delta
    # below must expect exactly (1 + retries), not a hardcoded 1.
    judge_retries = evidence.get("judge_retries") or 0
    expected_attempts = 1 + judge_retries

    guard_active = _ACTIVE_BUDGET_GUARD.get() is not None
    if not guard_active:
        # Without a guard, the orientation function directly invoked the
        # router; there is no provider-side counter to disambiguate preflight.
        attempted: bool | None = True
    elif not evidence.get("model_router_invoked"):
        attempted = False
    else:
        before = evidence.get("guard_generation_attempts_before")
        after = evidence.get("guard_generation_attempts_after")
        if evidence.get("generation_attempt_snapshot_error") or not isinstance(before, int) or not isinstance(after, int):
            attempted = None
        else:
            delta = after - before
            attempted = delta == expected_attempts
            if delta < 0 or delta > expected_attempts:
                evidence["generation_attempt_accounting_error"] = (
                    f"unexpected guarded attempt delta: {delta} (expected {expected_attempts})"
                )
                attempted = None

    # Guard preflight failures (token count, route, reservation, or cap) have
    # no generation attempt. Preserve the failure in the outer partial report
    # and keep the established contract that they consume no judge call.
    entry = None
    if attempted is not False:
        if attempted is True:
            budget.record(expected_attempts)
        entry = {
            "orientation": orientation,
            "status": "invoked" if attempted else "accounting_unknown",
            "evidence": evidence,
        }
        if result is not None:
            entry["result"] = result
        if original_error is not None:
            entry["error"] = f"{type(original_error).__name__}: {original_error}"
        elif attempted is None:
            entry["error"] = "guarded generation attempt count could not be determined"
        pending_pair["judge_orientations"].append(entry)

    checkpoint_error = None
    try:
        _budget_checkpoint()
    except BaseException as exc:
        checkpoint_error = exc
        if entry is not None:
            entry["checkpoint_error"] = f"{type(exc).__name__}: {exc}"

    if original_error is not None:
        if checkpoint_error is not None and entry is None:
            # The checkpoint must not hide the original preflight/provider error.
            raise original_error.with_traceback(original_traceback) from checkpoint_error
        raise original_error.with_traceback(original_traceback)
    if checkpoint_error is not None:
        raise checkpoint_error
    if attempted is False:
        raise ConversationLabError("guard reported no generation attempt for a returned judge response")
    if attempted is None:
        raise ConversationLabError("could not determine whether the guarded judge request reached generation")
    return result

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
    carries a copy. Shared by the episode-backed `ab` and `calibrate` paths so
    both build the same recipe context and judge facts from one snapshot.
    """
    return stage_data.get("recipe_data") or (episode.get("stages") or {}).get("monday", {}).get("recipe_data")


def _bench_photography_inputs(episode: dict[str, Any], stage: str) -> dict[str, Any]:
    """Freeze the photo inputs production passes for a Wednesday or Friday run."""
    stages = episode.get("stages") or {}
    if stage == "wednesday":
        source_stage = stages.get("wednesday")
        photo_stage = source_stage if isinstance(source_stage, dict) else {}
        path_stage = photo_stage
        paths_applicable = True
    elif stage == "friday":
        source_stage = stages.get("wednesday")
        photo_stage = source_stage if isinstance(source_stage, dict) else {}
        path_stage = {}
        paths_applicable = False
    else:
        return {
            "photography_context": None,
            "image_paths": [],
            "sources": {"photography_context": "not_applicable", "image_paths": "not_applicable"},
        }

    missing = object()
    raw_context = photo_stage.get("photography_data", missing)
    if isinstance(raw_context, dict):
        photography_context = copy.deepcopy(raw_context)
        context_source = "present"
    elif raw_context is missing or raw_context is None:
        photography_context = None
        context_source = "missing"
    else:
        # Match cron's isinstance(dict) guard while retaining malformed-vs-
        # absent provenance in the comparison scenario.
        photography_context = None
        context_source = f"invalid:{type(raw_context).__name__}"

    if not paths_applicable:
        image_paths: list[Any] = []
        paths_source = "not_applicable"
    else:
        raw_paths = path_stage.get("image_paths", missing)
        if isinstance(raw_paths, list):
            image_paths = copy.deepcopy(raw_paths)
            paths_source = "present"
        elif raw_paths is missing or raw_paths is None:
            image_paths = []
            paths_source = "missing"
        else:
            image_paths = []
            paths_source = f"invalid:{type(raw_paths).__name__}"

    return {
        "photography_context": photography_context,
        "image_paths": image_paths,
        "sources": {"photography_context": context_source, "image_paths": paths_source},
    }


def _effective_bench_photography_inputs(
    stage: str, concept: str, photography_context: Any
) -> dict[str, Any]:
    """Render the photo inputs that can affect bench prompts or Wednesday turns.

    Call the simulator's pure renderers directly so comparison semantics stay
    tied to the code that creates the prompts, rather than a copied field list.
    Image attachments are audit metadata and are added after turn generation.
    """
    try:
        dynamic_arc = simulate_module._build_dynamic_arc(
            stage, concept, photography_context=photography_context
        )
        scene_direction = simulate_module._build_photography_scene_direction(
            photography_context, stage
        )
        tick_floor = 0
        if stage == "wednesday" and photography_context:
            tick_floor = 10 if photography_context.get("reshoot_happened") else 7
    except Exception as exc:
        raise ConversationLabError(
            f"photography inputs cannot be rendered for stage={stage!r}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return {
        "dynamic_arc": dynamic_arc,
        "scene_direction": scene_direction,
        "wednesday_tick_floor": tick_floor,
    }

def _resolve_recipe_context_and_facts(
    args: argparse.Namespace,
) -> tuple[str | None, str | None]:
    """Return the speaker anchor and judge facts from one recipe snapshot.

    An explicit context has no recipe object behind it, so it deliberately
    returns no judge facts and performs no episode load.  Episode-backed runs
    load once and derive both strings from the selected stage (falling back to
    Monday's recipe data when later stages do not carry their own copy).
    """
    if bool(args.from_episode) == bool(args.recipe_context):
        raise SystemExit("conversation_lab ab: pass exactly one of --from-episode or --recipe-context")
    if args.recipe_context:
        return args.recipe_context, None

    episode = _load_episode(args.from_episode, local=args.local)
    stage_data = (episode.get("stages") or {}).get(args.stage) or {}
    recipe_data = _stage_recipe_data(episode, stage_data)
    recipe_context = _build_recipe_context(recipe_data)
    if not recipe_context:
        raise SystemExit(
            f"conversation_lab ab: episode {args.from_episode!r} has no usable recipe_data "
            f"for stage {args.stage!r} - pass --recipe-context instead"
        )
    recipe_facts = _build_judge_recipe_facts(recipe_data) or None
    return recipe_context, recipe_facts

def _resolve_recipe_context(args: argparse.Namespace) -> str | None:
    """Compatibility wrapper returning only the light speaker anchor."""
    recipe_context, _ = _resolve_recipe_context_and_facts(args)
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


def _require_openrouter_key() -> None:
    if not os.environ.get("OPENROUTER_API_KEY", "").strip():
        raise SystemExit(
            "conversation_lab: OPENROUTER_API_KEY is not set. Run under "
            "`doppler run -- uv run ...` so OPENROUTER_API_KEY resolves, or pass "
            "--provider anthropic / --dry-run for a zero-cost plumbing check."
        )


def _resolve_openrouter_models(dry_run: bool, model_set: "LabModelSet") -> tuple[str, str, str]:
    """Return (mode, dialogue_model, judge_model) for --provider openrouter.

    The model strings are the OpenRouter dotted names named by `model_set`
    (scripts/lab_models.json, "claude" by default - the same models
    production uses); prompts/temperatures/max_tokens are unchanged. Fails
    loud on a missing OPENROUTER_API_KEY unless --dry-run is passed.
    """
    if dry_run:
        return "template", "template", "template"
    _require_openrouter_key()
    return "openai", f"openrouter/{model_set.dialogue}", f"openrouter/{model_set.judge}"


def _resolve_openrouter_judge_model(dry_run: bool, model_set: "LabModelSet") -> str:
    if dry_run:
        return "template"
    _require_openrouter_key()
    return f"openrouter/{model_set.judge}"


def _resolve_models_for_args(args: argparse.Namespace) -> tuple[str, str, str]:
    provider = _provider_for(args)
    if provider == "openrouter":
        return _resolve_openrouter_models(args.dry_run, _resolve_lab_model_set(args))
    return _resolve_models(args.dry_run)


def _resolve_judge_model_for_args(args: argparse.Namespace) -> str:
    if _provider_for(args) == "openrouter":
        return _resolve_openrouter_judge_model(args.dry_run, _resolve_lab_model_set(args))
    return "template" if args.dry_run else _resolve_judge_model()

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

# ---------------------------------------------------------------------------
# Frozen prior days (freeze / --prior-days)
#
# Erik's one-day-at-a-time method: freeze the best Monday per scenario, run
# Tuesday with that Monday as context, freeze Tuesday, and so on. `freeze`
# distills one arm's transcript per scenario from an `ab` result into a
# frozen file; `ab --prior-days` seeds both arms with that file's earlier
# days so no tokens are spent regenerating Monday.
# ---------------------------------------------------------------------------

def _day_index(day: str) -> int:
    try:
        return simulate_module.DAY_ORDER.index(day)
    except ValueError as exc:
        raise SystemExit(
            f"conversation_lab: unknown day {day!r} (expected one of {', '.join(simulate_module.DAY_ORDER)})"
        ) from exc


def _frozen_scenario_days(data: dict[str, Any], scenario_id: str) -> list[tuple[str, list[dict[str, Any]]]]:
    """Return [(day, messages), ...] for one scenario, sorted in week order.

    A fresh `freeze` writes one day directly under the scenario id:
        {"day": "monday", "messages": [...]}
    An appended file is keyed by scenario then day (Mon+Tue live in one file):
        {"monday": {"day": "monday", "messages": [...]}, "tuesday": {...}}
    This reader accepts both shapes. Returns None when the scenario is absent.
    """
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict) or scenario_id not in scenarios:
        return None
    entry = scenarios[scenario_id]
    if not isinstance(entry, dict):
        raise SystemExit(
            f"conversation_lab: frozen scenario {scenario_id!r} entry is not a JSON object"
        )
    if "messages" in entry:
        day = entry.get("day")
        if day not in simulate_module.DAY_ORDER:
            raise SystemExit(
                f"conversation_lab: frozen scenario {scenario_id!r} has unknown day {day!r}"
            )
        return [(day, entry.get("messages") or [])]
    entries: list[tuple[str, list[dict[str, Any]]]] = []
    for day, day_entry in entry.items():
        if not isinstance(day_entry, dict) or "messages" not in day_entry:
            raise SystemExit(
                f"conversation_lab: frozen scenario {scenario_id!r} day {day!r} "
                "is not a {\"day\": ..., \"messages\": [...]} object"
            )
        if day not in simulate_module.DAY_ORDER:
            raise SystemExit(
                f"conversation_lab: frozen scenario {scenario_id!r} has unknown day {day!r}"
            )
        entries.append((day, day_entry.get("messages") or []))
    entries.sort(key=lambda item: _day_index(item[0]))
    return entries


def _frozen_days_present(data: dict[str, Any]) -> list[str]:
    """Every day present anywhere in a frozen file, in week order."""
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict) or not scenarios:
        raise SystemExit("conversation_lab: frozen file has no 'scenarios' object")
    days: set[str] = set()
    for scenario_id in scenarios:
        for day, _messages in _frozen_scenario_days(data, scenario_id):
            days.add(day)
    return sorted(days, key=_day_index)


def _prior_recent_lines(data: dict[str, Any], scenario_id: str, stage: str) -> list[str]:
    """Frozen earlier days as the exact `recent_lines` entries a full-week run
    would have accumulated before `stage`.

    run_simulation appends f"{speaker.split()[0]}: {line}" for every message
    of every day (see its run loop), so the frozen messages are flattened the
    same way, in week order, and passed as `initial_recent_lines`.
    """
    stage_index = _day_index(stage)
    lines: list[str] = []
    for day, messages in _frozen_scenario_days(data, scenario_id):
        if _day_index(day) >= stage_index:
            continue
        for message in messages:
            if not isinstance(message, dict):
                continue
            text = message.get("message")
            if not text:
                continue
            character = str(message.get("character") or "Unknown")
            lines.append(f"{character.split()[0]}: {text}")
    return lines


def _load_frozen_prior_days(path: Path) -> tuple[dict[str, Any], str]:
    """Load a frozen prior-days file; return (data, sha256-of-file-bytes)."""
    if not path.exists():
        raise SystemExit(f"conversation_lab ab: --prior-days file not found: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"conversation_lab ab: cannot read --prior-days file {path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"conversation_lab ab: --prior-days file is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"conversation_lab ab: --prior-days file must be a JSON object: {path}")
    if not isinstance(data.get("scenarios"), dict) or not data["scenarios"]:
        raise SystemExit(f"conversation_lab ab: --prior-days file has no 'scenarios' object: {path}")
    # Touch every scenario once so a malformed entry fails before any paid work.
    for scenario_id in data["scenarios"]:
        _frozen_scenario_days(data, scenario_id)
    return data, digest


def _prepare_prior_days(
    args: argparse.Namespace, scenario_ids: list[str],
) -> dict[str, list[str]]:
    """Validate `--prior-days` against the panel and build per-scenario lines.

    Refuses (before any paid work) when the frozen file lacks a panel
    scenario, or contains --stage or a later day. Both arms of every pair get
    the SAME lines: the returned mapping is computed once and reused verbatim.
    """
    if not getattr(args, "prior_days", None):
        return {}
    path = Path(args.prior_days)
    data, digest = _load_frozen_prior_days(path)
    days_present = _frozen_days_present(data)
    stage_index = _day_index(args.stage)
    for day in days_present:
        if _day_index(day) >= stage_index:
            raise SystemExit(
                f"conversation_lab ab: --prior-days file {path} contains day {day!r}, "
                f"which is --stage ({args.stage}) or later"
            )
    prior_by_scenario: dict[str, list[str]] = {}
    for scenario_id in scenario_ids:
        if scenario_id not in data["scenarios"]:
            raise SystemExit(
                f"conversation_lab ab: --prior-days file {path} lacks scenario {scenario_id!r} "
                "from the panel"
            )
        prior_by_scenario[scenario_id] = _prior_recent_lines(data, scenario_id, args.stage)
    args.prior_days_provenance = {
        "path": str(path),
        "sha256": digest,
        "days": days_present,
    }
    return prior_by_scenario


def _freeze_pick_pair(
    pairs: list[dict[str, Any]], arm: str, pick: str,
) -> dict[str, Any]:
    """Pick ONE transcript per scenario from its judged pairs.

    pick="run0": the pair with the lowest run_index (the lab's runs are
    1-based, so in practice run 1; a result that carried run_index 0 would
    pick that).
    pick="judge": the pair where `arm` won the most judge dimensions; ties
    break by lowest run_index (a secondary tie keeps the first pair in the
    file, which mirrors lowest run_index for well-formed results).
    """
    if not pairs:
        raise SystemExit("conversation_lab freeze: scenario has no completed pairs to pick from")

    def run_key(pair: dict[str, Any]) -> int:
        run_index = pair.get("run_index")
        return run_index if isinstance(run_index, int) else 1 << 30

    if pick == "run0":
        return min(pairs, key=run_key)

    ranked = []
    for pair in pairs:
        judge = pair.get("judge") if isinstance(pair.get("judge"), dict) else {}
        wins = sum(1 for dimension in ALL_JUDGE_DIMENSIONS if judge.get(dimension) == arm)
        ranked.append((wins, run_key(pair), pair))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][2]


def _load_testbed(path: Path) -> list[dict[str, Any]]:
    """Load the frozen scenario panel (docs/conversation-lab/testbed-v3.json by default).

    Committed data, not regenerated at run time - see the module docstring
    and _build_parser's `--testbed` help for how it was produced. v3 includes
    the ingredient boundaries from #7441 so experiments measure what speakers
    actually see in production. Legacy v2 and v1 panels are available for
    historical baseline comparison.
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

    # Say which panel ran, every time. A result that does not name its panel
    # cannot be compared against another result months later, and the whole
    # point of a frozen panel is month-to-month comparability.
    version = data.get("panel_version", "v1-legacy") if isinstance(data, dict) else "unknown"
    stale = sum(1 for sc in scenarios if "Key ingredients:" in (sc.get("recipe_context") or ""))
    print(
        f"[testbed] panel={version} file={path.name} scenarios={len(scenarios)}"
        + (f"  STALE-FORMAT SCENARIOS: {stale}" if stale else "")
    )
    if stale:
        print(
            "[testbed] WARNING: those scenarios use the pre-#7104 'Key ingredients:' "
            "anchor. Production now emits 'What it is: <description>' plus ingredient "
            "boundaries (added #7441). Results from this panel do not transfer to production "
            "behaviour."
        )
    if version == "v2":
        print(
            "[testbed] WARNING: v2 predates the #7441 ingredient boundary. Speakers in these "
            "scenarios see only the description, without ingredient names. Current production "
            "includes ingredient boundaries. v2 measurements do not transfer to production "
            "behaviour."
        )
    return scenarios

def _generate_and_judge_pairs(
    *,
    concept: str,
    stage: str,
    recipe_context: str | None,
    recipe_facts: str | None = None,
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
    partial_pairs: list[dict[str, Any]] | None = None,
    prior_lines: list[str] | None = None,
) -> bool:
    """Append up to `runs` control/variant pairs to `pairs` in place; return
    whether the run aborted (--max-calls or --max-cost hit).

    `prior_lines` (frozen earlier days) is passed UNCHANGED to both the
    control and the variant arm, so the two arms of every pair see identical
    prior context.

    `pairs` is mutated in place rather than returned, so that a pair that
    finished before run N+1 raised (a judge parse failure, a generation
    error) is never lost - the caller's list already holds it even though
    this function itself never returns normally in that case. Every
    control/variant/judge call here is paid work; losing an already-
    completed pair because the run after it errored is exactly the kind
    of waste `~/projects/CLAUDE.md`'s cost doctrine forbids, which is why
    cmd_ab additionally writes a partial result (with "error"/"aborted")
    on any exception from this function - see its call sites.

    Before each generation arm, reserve the structural worst case of four
    paid requests per turn: the initial request, its CoT retry, a fault
    rewrite, and the rewrite's CoT retry. Recording still uses actual calls.
    """
    aborted = False
    restore_pending: dict[str, Any] | None = None
    partial_pairs = partial_pairs if partial_pairs is not None else []
    arm_call_reservation = _arm_call_reservation(stage, variant, dry_run=dry_run)

    # Before the control arm costs anything (Codex audit): a malformed variant
    # used to surface only when _apply_variant ran, which is after the control
    # generation for this run index.
    validate_variant(simulate_module, variant)

    # #7714 round 2: ONE guard install for this whole function's run loop -
    # every control/variant arm and every judge call inside it is covered
    # by construction, not by threading budget/max_cost through each
    # individual _run_arm_and_count/generate_judge_response call.
    guard_ctx = _installed_budget_guard(budget, max_cost)
    guard_ctx.__enter__()
    try:
        for run_index in range(1, runs + 1):
            if budget.would_exceed(arm_call_reservation) or _would_exceed_cost(max_cost):
                aborted = True
                break

            try:
                control_result, control_calls = _run_arm_and_count(
                    concept, stage, run_index, recipe_context, mode, default_model,
                    prior_lines=prior_lines, recipe_facts=recipe_facts,
                )
            except BaseException as exc:
                # #7714 round 3 / round 4 finding 1: the control arm's
                # turns generated before ANY exception - a LabBudgetAbort
                # from the guard, or any other mid-arm error (dialogue,
                # rewrite, director, the Haiku stop-check path, an empty-
                # content RuntimeError from an OpenRouter reasoning model)
                # - are real, paid work, saved as a partial pair (never
                # judged, see the docstring above) instead of discarded.
                # A budget abort still reads as a clean aborted result; any
                # other exception still propagates as a truthful error -
                # only the accounting changes, not the control flow.
                made = getattr(exc, "conversation_lab_calls_made", 0)
                budget.record(0 if dry_run else made)
                partial_pairs.append({
                    "run_index": run_index,
                    "status": "aborted_mid_arm" if isinstance(exc, LabBudgetAbort) else "error_mid_arm",
                    "control_messages": getattr(exc, "conversation_lab_partial_messages", []),
                    "control_stop_check_log": _snapshot_stop_check_log(),
                    "control_director_log": _snapshot_director_log(),
                    "control_rewrite_log": _snapshot_rewrite_log(),
                    "control_calls_before_abort": made,
                    "variant_messages": None,
                    "judge_orientations": [],
                })
                if not isinstance(exc, LabBudgetAbort):
                    raise
                aborted = True
                break
            control_stop_check_log = _snapshot_stop_check_log()
            control_director_log = _snapshot_director_log()
            control_rewrite_log = _snapshot_rewrite_log()
            budget.record(0 if dry_run else control_calls)
            pending_pair: dict[str, Any] = {
                "run_index": run_index,
                "status": "control_generated",
                "control_messages": control_result.get("messages", []),
                "control_stop_check_log": control_stop_check_log,
                "control_director_log": control_director_log,
                "control_rewrite_log": control_rewrite_log,
                "variant_messages": None,
                "judge_orientations": [],
            }
            partial_pairs.append(pending_pair)
            _budget_checkpoint()

            if budget.would_exceed(arm_call_reservation) or _would_exceed_cost(max_cost):
                aborted = True
                break

            restore_pending = _apply_variant(simulate_module, variant)
            try:
                try:
                    variant_result, variant_calls = _run_arm_and_count(
                        concept, stage, run_index, recipe_context, mode, default_model,
                        prior_lines=prior_lines, recipe_facts=recipe_facts,
                    )
                except BaseException as exc:
                    # #7714 finding 2 / round 3 / round 4 finding 1: the
                    # pending pair already reflects real, paid control-arm
                    # work - mark it explicitly instead of leaving it in an
                    # ambiguous "control_generated" status, record what
                    # this variant arm actually spent instead of silently
                    # reporting zero, and save whatever variant turns it
                    # generated before ANY exception - not just a budget
                    # abort - instead of discarding them.
                    made = getattr(exc, "conversation_lab_calls_made", 0)
                    budget.record(0 if dry_run else made)
                    pending_pair["status"] = "aborted_mid_arm" if isinstance(exc, LabBudgetAbort) else "error_mid_arm"
                    pending_pair["variant_calls_before_abort"] = made
                    pending_pair["variant_messages"] = getattr(exc, "conversation_lab_partial_messages", [])
                    if not isinstance(exc, LabBudgetAbort):
                        raise
                    aborted = True
                    break
                variant_stop_check_log = _snapshot_stop_check_log()
                variant_director_log = _snapshot_director_log()
                variant_rewrite_log = _snapshot_rewrite_log()
            finally:
                _restore_variant(simulate_module, restore_pending)
                restore_pending = None
            budget.record(0 if dry_run else variant_calls)

            control_messages = control_result.get("messages", [])
            variant_messages = variant_result.get("messages", [])
            pending_pair["variant_messages"] = variant_messages
            pending_pair["variant_stop_check_log"] = variant_stop_check_log
            pending_pair["variant_director_log"] = variant_director_log
            pending_pair["variant_rewrite_log"] = variant_rewrite_log
            pending_pair["status"] = "awaiting_judges"
            _budget_checkpoint()

            if dry_run:
                combined = _dry_run_combined()
            else:
                if budget.would_exceed(1) or _would_exceed_cost(max_cost):
                    aborted = True
                    break
                first = _run_judge_orientation(
                    orientation="control_first", pending_pair=pending_pair, budget=budget,
                    judge_model=judge_model, concept=concept, stage=stage,
                    recipe_context=recipe_context, expected_cast=expected_cast,
                    first_arm="control", first_messages=control_messages,
                    second_arm="variant", second_messages=variant_messages,
                    recipe_facts=recipe_facts, max_cost=max_cost,
                )

                if budget.would_exceed(1) or _would_exceed_cost(max_cost):
                    aborted = True
                    break
                second = _run_judge_orientation(
                    orientation="variant_first", pending_pair=pending_pair, budget=budget,
                    judge_model=judge_model, concept=concept, stage=stage,
                    recipe_context=recipe_context, expected_cast=expected_cast,
                    first_arm="variant", first_messages=variant_messages,
                    second_arm="control", second_messages=control_messages,
                    recipe_facts=recipe_facts, max_cost=max_cost,
                )
                combined = _combine_orientations(first, second)

            completed_pair = {
                "run_index": run_index,
                "control_messages": control_messages,
                "variant_messages": variant_messages,
                "control_stop_check_log": control_stop_check_log,
                "variant_stop_check_log": variant_stop_check_log,
                "control_director_log": control_director_log,
                "variant_director_log": variant_director_log,
                "control_rewrite_log": control_rewrite_log,
                "variant_rewrite_log": variant_rewrite_log,
                "control_summary": summarize(control_messages, expected_cast, concept=concept, day=stage),
                "variant_summary": summarize(variant_messages, expected_cast, concept=concept, day=stage),
                "judge": combined,
                "judge_diagnostics": _orientation_diagnostics(first, second) if not dry_run else None,
                "judge_orientations": pending_pair["judge_orientations"],
                "dry_run": bool(dry_run),
            }
            pairs.append(completed_pair)
            partial_pairs.remove(pending_pair)
    finally:
        guard_ctx.__exit__(None, None, None)
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

def _effective_max_turns_for_stage(stage: str, variant: dict[str, Any] | None) -> int:
    """Like `_max_turns_for_stage`, but also honors whatever a VARIANT
    raises TICKS_RANGE/OPEN_ENDED_MAX_TICKS to for `stage` (#7714 finding
    3) - `arm_call_reservation` used to be computed once, from
    `_max_turns_for_stage` alone, BEFORE `_apply_variant` ever ran for
    that run, so a variant whose OPEN_ENDED_MAX_TICKS[stage] (or raised
    TICKS_RANGE[stage] upper bound) exceeded the unpatched module default
    silently under-reserved: the pre-arm budget check passed using a
    ceiling the variant arm could - and, per `validate_variant`'s own
    HISTORY_DEPTH cross-check, was explicitly allowed to - exceed.

    Also honors the CONTROL's own current module attributes (not just
    their checked-in defaults) - a test or future CLI knob that sets
    OPEN_ENDED_MAX_TICKS directly on the module without going through a
    variant must not be under-reserved either.
    """
    upper = simulate_module.TICKS_RANGE.get(stage, (4, 6))[1]

    control_open_ended = getattr(simulate_module, "OPEN_ENDED_MAX_TICKS", None)
    if isinstance(control_open_ended, dict):
        cap = control_open_ended.get(stage)
        if isinstance(cap, int) and not isinstance(cap, bool):
            upper = max(upper, cap)

    if isinstance(variant, dict):
        variant_ticks_range = variant.get("TICKS_RANGE")
        if isinstance(variant_ticks_range, dict):
            pair = variant_ticks_range.get(stage)
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                try:
                    upper = max(upper, int(pair[1]))
                except (TypeError, ValueError):
                    pass
        variant_open_ended = variant.get("OPEN_ENDED_MAX_TICKS")
        if isinstance(variant_open_ended, dict):
            cap = variant_open_ended.get(stage)
            if isinstance(cap, int) and not isinstance(cap, bool):
                upper = max(upper, cap)

    return max(upper, _MIN_MAX_TURNS_FLOOR)

def _effective_stop_check_active(variant: dict[str, Any] | None) -> bool:
    """True when EITHER arm's effective WINDDOWN_TRIGGER is "check" - the
    only trigger under which `check_scene_done` (and therefore Jev HTTP
    attempts) runs per tick (see
    `_refuse_if_stop_check_conflicts_with_models`, which reasons about the
    same two effective triggers)."""
    control_trigger = simulate_module.WINDDOWN_TRIGGER
    is_dict = isinstance(variant, dict)
    variant_trigger = (
        variant.get("WINDDOWN_TRIGGER")
        if is_dict and "WINDDOWN_TRIGGER" in variant
        else control_trigger
    )
    return control_trigger == "check" or variant_trigger == "check"

# backend.utils.stop_check._JEV_MAX_ATTEMPTS: the worst case number of Jev
# HTTP attempts (first try + retries) ONE stop check can make in a single
# tick - reserved per tick, on top of _MAX_CALLS_PER_TURN's four generation
# requests, whenever the effective WINDDOWN_TRIGGER is "check" (#7714
# finding 2/3: those attempts are real paid calls that used to run entirely
# outside this reservation).
_MAX_STOP_CHECK_ATTEMPTS_PER_TICK = stop_check._JEV_MAX_ATTEMPTS

def _arm_call_reservation(stage: str, variant: dict[str, Any] | None, *, dry_run: bool) -> int:
    """Structural worst-case call reservation for ONE control+variant pair
    at `stage`: four paid generation requests per turn (the initial
    request, its CoT retry, a fault rewrite, and the rewrite's CoT retry)
    across `_effective_max_turns_for_stage`'s ceiling (#7714 finding 3),
    PLUS - when a stop check can actually run - up to
    `_MAX_STOP_CHECK_ATTEMPTS_PER_TICK` Jev HTTP attempts per tick (#7714
    finding 2)."""
    if dry_run:
        return 0
    max_turns = _effective_max_turns_for_stage(stage, variant)
    reservation = _MAX_CALLS_PER_TURN * max_turns
    if _effective_stop_check_active(variant):
        reservation += _MAX_STOP_CHECK_ATTEMPTS_PER_TICK * max_turns
    return reservation

def _derive_max_calls(mode: str, *, scenario_count: int, runs: int, stage: str) -> int:
    """The --max-calls default when the flag itself is omitted (None).

    "single": a flat 120 (docs/conversation-lab/PROTOCOL.md's cost
    budget) - a lone --concept/--recipe-context run has no panel size to
    derive a formula from.

    "testbed"/"sweep": `scenario_count * runs * (2 * _MAX_CALLS_PER_TURN * max_turns + 2)`.
    This reserves four generation requests per turn for each arm, plus the
    two position-swapped judge calls.
    Both modes use this same formula; a --sweep's shared control makes
    the true call count lower than this in practice (the control is
    generated once, not once per variant), so the derived cap is a
    deliberately generous ceiling, not a tight budget.
    """
    if mode == "single":
        return _SINGLE_CONCEPT_MAX_CALLS
    max_turns = _max_turns_for_stage(stage)
    return scenario_count * runs * (2 * _MAX_CALLS_PER_TURN * max_turns + 2)

def cmd_ab(args: argparse.Namespace) -> None:
    if args.sweep and args.variant:
        raise SystemExit("conversation_lab ab: pass --variant or --sweep, not both")
    if not args.sweep and not args.variant:
        raise SystemExit("conversation_lab ab: --variant is required unless --sweep is passed")
    if args.prior_days and not args.sweep and args.testbed is None:
        raise SystemExit(
            "conversation_lab ab: --prior-days requires --testbed or --sweep - "
            "a single --concept run has no scenario to attach frozen days to"
        )

    if _models_arg_for(args) is not None:
        # Validates the name and refuses --provider anthropic + a non-default
        # set REGARDLESS of which branch below actually resolves models -
        # _resolve_models_for_args only calls _resolve_lab_model_set on the
        # openrouter path, so the anthropic path needs this checked explicitly.
        _resolve_lab_model_set(args)

    mode, default_model, judge_model = _resolve_models_for_args(args)

    if args.sweep:
        _cmd_ab_sweep(args, mode, default_model, judge_model)
        return

    variant_path = Path(args.variant)
    variant = _load_variant_file(variant_path)
    _refuse_if_stop_check_conflicts_with_models(args, variant)

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

    recipe_context, recipe_facts = _resolve_recipe_context_and_facts(args)
    expected_cast = simulate_module.participants_for_day(args.stage)
    result_path = _ab_result_path(_results_dir(args), _slugify(args.concept), variant_path)

    pairs: list[dict[str, Any]] = []
    partial_pairs: list[dict[str, Any]] = []
    try:
        aborted = _generate_and_judge_pairs(
            concept=args.concept, stage=args.stage, recipe_context=recipe_context,
            recipe_facts=recipe_facts, runs=args.runs,
            variant=variant, mode=mode, default_model=default_model, judge_model=judge_model,
            expected_cast=expected_cast, budget=budget, max_cost=args.max_cost, dry_run=args.dry_run,
            pairs=pairs, partial_pairs=partial_pairs,
        )
    except BaseException as exc:
        report = _build_ab_report(
            args, variant_path, variant, pairs, True, budget, result_path,
            partial_pairs=partial_pairs,
            error=f"{type(exc).__name__}: {exc}",
        )
        _write_json_result(result_path, report)
        raise

    report = _build_ab_report(
        args, variant_path, variant, pairs, aborted, budget, result_path,
        partial_pairs=partial_pairs,
    )
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
    prior_by_scenario = _prepare_prior_days(args, [scenario["id"] for scenario in scenarios])
    runs = args.runs if args.runs is not None else DEFAULT_TESTBED_RUNS
    max_calls_derived = args.max_calls is None
    if max_calls_derived:
        args.max_calls = _derive_max_calls("testbed", scenario_count=len(scenarios), runs=runs, stage=args.stage)
    budget = CallBudget(max_calls=args.max_calls)
    expected_cast = simulate_module.participants_for_day(args.stage)
    result_path = _ab_result_path(_results_dir(args), "testbed", variant_path)

    scenario_reports: list[dict[str, Any]] = []
    all_pairs: list[dict[str, Any]] = []
    all_partial_pairs: list[dict[str, Any]] = []
    aborted = False

    try:
        for scenario in scenarios:
            scenario_pairs: list[dict[str, Any]] = []
            scenario_partial_pairs: list[dict[str, Any]] = []
            try:
                scenario_aborted = _generate_and_judge_pairs(
                    concept=scenario["concept"], stage=args.stage, recipe_context=scenario["recipe_context"],
                        recipe_facts=scenario.get("judge_recipe_facts"),
                    runs=runs, variant=variant, mode=mode, default_model=default_model, judge_model=judge_model,
                    expected_cast=expected_cast, budget=budget, max_cost=args.max_cost, dry_run=args.dry_run,
                    pairs=scenario_pairs, partial_pairs=scenario_partial_pairs,
                    prior_lines=prior_by_scenario.get(scenario["id"]),
                )
            finally:
                for pair in scenario_pairs:
                    pair["scenario_id"] = scenario["id"]
                for partial in scenario_partial_pairs:
                    partial["scenario_id"] = scenario["id"]
                all_pairs.extend(scenario_pairs)
                all_partial_pairs.extend(scenario_partial_pairs)
                scenario_reports.append({
                    "id": scenario["id"],
                    "concept": scenario["concept"],
                    "category": scenario.get("category"),
                    "cuisine": scenario.get("cuisine"),
                    "source_episode": scenario.get("source_episode"),
                    "partial_pairs": scenario_partial_pairs,
                    **_aggregate_pairs(scenario_pairs, args.target, args.dry_run),
                })
            if scenario_aborted:
                aborted = True
                break
    except BaseException as exc:
        report = _build_testbed_ab_report(
            args, variant_path, variant, scenario_reports, all_pairs, True, budget, result_path,
            max_calls_derived, partial_pairs=all_partial_pairs,
            error=f"{type(exc).__name__}: {exc}",
        )
        _write_json_result(result_path, report)
        raise

    report = _build_testbed_ab_report(
        args, variant_path, variant, scenario_reports, all_pairs, aborted, budget, result_path, max_calls_derived,
        partial_pairs=all_partial_pairs,
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
    differently by accident. Metric deltas are generic over summary keys,
    but include only pairs with finite numeric observations on both sides.
    Coverage records how many pairs supported each metric; unavailable values
    are never imputed as zero.
    """
    n = len(pairs)
    overall_counts = Counter(p["judge"]["overall"] for p in pairs)
    per_dimension_counts = {dim: Counter(p["judge"][dim] for p in pairs) for dim in ALL_JUDGE_DIMENSIONS}

    metric_deltas: dict[str, float] = {}
    metric_coverage: dict[str, dict[str, float | int]] = {}
    summary_keys = {
        key
        for pair in pairs
        for summary_name in ("control_summary", "variant_summary")
        for key, value in pair[summary_name].items()
        if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool))
    }
    for key in sorted(summary_keys):
        deltas = []
        for pair in pairs:
            control_value = pair["control_summary"].get(key)
            variant_value = pair["variant_summary"].get(key)
            if (
                isinstance(control_value, (int, float))
                and not isinstance(control_value, bool)
                and math.isfinite(control_value)
                and isinstance(variant_value, (int, float))
                and not isinstance(variant_value, bool)
                and math.isfinite(variant_value)
            ):
                deltas.append(variant_value - control_value)
        metric_coverage[key] = {
            "paired_samples": len(deltas),
            "eligible_pairs": n,
            "coverage": round(len(deltas) / n, 4) if n else 0.0,
        }
        if deltas:
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
        "metric_coverage": metric_coverage,
    }

def _build_ab_report(
    args: argparse.Namespace,
    variant_path: Path,
    variant: dict[str, Any],
    pairs: list[dict[str, Any]],
    aborted: bool,
    budget: CallBudget,
    result_path: Path,
    partial_pairs: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    try:
        cost_summary = model_router.get_cost_summary()
    except Exception:
        cost_summary = {}

    report = {
        "command": "ab",
        "mode": "single",
        **_pairwise_evaluator_metadata(),
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
        "jev_cost_usd": _jev_cost_usd_total(),
        **_openrouter_fields_for(args, _openrouter_cost_by_model_for_pairs(pairs, partial_pairs or [])),
        "pairs": pairs,
        "partial_pairs": partial_pairs or [],
        "rewrite_summary": _pair_arm_rewrite_summaries(pairs),
        "length_stats": _pair_arm_length_stats(pairs),
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
    partial_pairs: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    try:
        cost_summary = model_router.get_cost_summary()
    except Exception:
        cost_summary = {}

    report = {
        "command": "ab",
        "mode": "testbed",
        **_pairwise_evaluator_metadata(),
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
        "jev_cost_usd": _jev_cost_usd_total(),
        **_openrouter_fields_for(args, _openrouter_cost_by_model_for_pairs(all_pairs, partial_pairs or [])),
        "scenarios": scenario_reports,
        "pairs": all_pairs,
        "partial_pairs": partial_pairs or [],
        "rewrite_summary": _pair_arm_rewrite_summaries(all_pairs),
        "length_stats": _pair_arm_length_stats(all_pairs),
        "results_file": str(result_path),
        **_aggregate_pairs(all_pairs, args.target, args.dry_run),
    }
    if error is not None:
        report["error"] = error
    prior_days = getattr(args, "prior_days_provenance", None)
    if prior_days is not None:
        report["prior_days"] = prior_days
    return report

def _print_dimension_table(per_dimension_counts: dict[str, dict[str, int]]) -> None:
    print(f"{'dimension':<24}{'variant':>8}{'tie':>8}{'control':>8}")
    for dim in ALL_JUDGE_DIMENSIONS:
        counts = per_dimension_counts.get(dim, {})
        print(f"{dim:<24}{counts.get('variant', 0):>8}{counts.get('tie', 0):>8}{counts.get('control', 0):>8}")

def _print_metric_deltas(
    metric_deltas: dict[str, float], metric_coverage: dict[str, dict[str, float | int]] | None = None,
) -> None:
    print("\nmean metric deltas (variant - control):")
    coverage = metric_coverage or {}
    for key in sorted(set(metric_deltas) | set(coverage)):
        sample = coverage.get(key)
        sample_text = f" n={sample['paired_samples']}/{sample['eligible_pairs']}" if sample else ""
        if key in metric_deltas:
            print(f"  {key:<32}{metric_deltas[key]:+.4f}{sample_text}")
        else:
            print(f"  {key:<32}unavailable{sample_text}")

def _print_openrouter_costs(report: dict[str, Any]) -> None:
    if "cost_by_model" not in report:
        return
    print("openrouter costs by model:")
    for model in sorted(report["cost_by_model"]):
        print(f"  {model}: ${report['cost_by_model'][model]:.9f}")
    print(f"total cost: ${report['total_cost_usd']:.9f}")
    key_report = report.get("openrouter_key")
    if key_report:
        before = key_report.get("before") or {}
        after = key_report.get("after")
        print(
            f"openrouter key before: limit=${before.get('limit', 0.0):.2f} "
            f"limit_remaining=${before.get('limit_remaining', 0.0):.2f}"
        )
        if after:
            print(
                f"openrouter key after: limit=${after.get('limit', 0.0):.2f} "
                f"limit_remaining=${after.get('limit_remaining', 0.0):.2f}"
            )
        elif key_report.get("after_error"):
            print(f"openrouter key after: unavailable ({key_report['after_error']})")


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
    _print_metric_deltas(report["metric_deltas"], report.get("metric_coverage"))
    print(f"\nDECISION RULE (informational, not enforced): {report['decision_rule']}")
    print(f"cost summary: {report['cost_summary']}")
    print(f"jev cost: ${report['jev_cost_usd']:.9f}")
    _print_openrouter_costs(report)
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
    _print_metric_deltas(report["metric_deltas"], report.get("metric_coverage"))
    print(f"\nDECISION RULE (informational, not enforced): {report['decision_rule']}")
    print(f"cost summary: {report['cost_summary']}")
    print(f"jev cost: ${report['jev_cost_usd']:.9f}")
    _print_openrouter_costs(report)
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
    prior_by_scenario: dict[str, list[str]] | None = None,
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
    dry_run = mode == "template"
    arm_call_reservation = _arm_call_reservation(stage, None, dry_run=dry_run)
    # #7714 round 2, finding 1: this loop had NO guard at all - max_cost=None
    # disables the cost side (this loop is calls-only by design, see the
    # docstring above), but every arm here is still covered for --max-calls
    # by construction, same as every other arm-running loop in this module.
    with _installed_budget_guard(budget, None):
        for scenario in scenarios:
            for run_index in range(1, runs + 1):
                if budget.would_exceed(arm_call_reservation):
                    return True
                try:
                    result, calls = _run_arm_and_count(
                        scenario["concept"], stage, run_index, scenario["recipe_context"], mode, default_model,
                        prior_lines=(prior_by_scenario or {}).get(scenario["id"]),
                        recipe_facts=scenario.get("judge_recipe_facts"),
                    )
                except BaseException as exc:
                    # #7714 round 3 / round 4 finding 1: the aborted
                    # (scenario, run)'s turns generated before ANY
                    # exception - not just a budget abort - are real, paid
                    # work, saved into `transcripts` at this key (the same
                    # "messages" shape a completed entry has, so
                    # _build_sweep_report's existing control_transcripts_
                    # by_key/unpaired_control_transcripts machinery picks it
                    # up with no further changes) instead of discarded.
                    # Never judged: control aborting/erroring here means no
                    # variant ever runs this key (see _cmd_ab_sweep's `if
                    # not control_aborted:` guard, and this re-raises for a
                    # non-budget error so the sweep's own outer handler
                    # writes a truthful error report instead of a clean
                    # "aborted" one).
                    made = getattr(exc, "conversation_lab_calls_made", 0)
                    budget.record(0 if dry_run else made)
                    transcripts[(scenario["id"], run_index)] = {
                        "status": "aborted_mid_arm" if isinstance(exc, LabBudgetAbort) else "error_mid_arm",
                        "messages": getattr(exc, "conversation_lab_partial_messages", []),
                        "stop_check_log": _snapshot_stop_check_log(),
                        "director_log": _snapshot_director_log(),
                        "rewrite_log": _snapshot_rewrite_log(),
                        "calls_before_abort": made,
                    }
                    if not isinstance(exc, LabBudgetAbort):
                        raise
                    return True
                budget.record(0 if dry_run else calls)
                # The shared control is generated once, up front; its stop-check,
                # director, and rewrite logs would be long gone from the module by
                # the time its pairs are judged, so copy them into the stored
                # transcript now.
                result["stop_check_log"] = _snapshot_stop_check_log()
                result["director_log"] = _snapshot_director_log()
                result["rewrite_log"] = _snapshot_rewrite_log()
                transcripts[(scenario["id"], run_index)] = result
                _budget_checkpoint()
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
    partial_pairs: list[dict[str, Any]] | None = None,
    prior_by_scenario: dict[str, list[str]] | None = None,
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
    partial_pairs = partial_pairs if partial_pairs is not None else []
    baseline_cost = _total_cost_or_none() or 0.0
    aborted = False
    restore_pending: dict[str, Any] | None = None
    arm_call_reservation = _arm_call_reservation(stage, variant, dry_run=dry_run)

    # #7714 round 2: ONE guard install for this whole variant's run - every
    # arm and judge call across every (scenario, run) pair it processes is
    # covered by construction.
    guard_ctx = _installed_budget_guard(budget, max_cost, baseline_cost=baseline_cost)
    guard_ctx.__enter__()
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

                if budget.would_exceed(arm_call_reservation) or _would_exceed_cost(max_cost, baseline=baseline_cost):
                    aborted = True
                    break

                control_messages = control_result.get("messages", [])
                control_stop_check_log = control_result.get("stop_check_log", [])
                control_director_log = control_result.get("director_log", [])
                control_rewrite_log = control_result.get("rewrite_log", [])
                pending_pair: dict[str, Any] = {
                    "scenario_id": scenario["id"],
                    "run_index": run_index,
                    "status": "control_generated",
                    "control_messages": control_messages,
                    "control_stop_check_log": control_stop_check_log,
                    "control_director_log": control_director_log,
                    "control_rewrite_log": control_rewrite_log,
                    "variant_messages": None,
                    "judge_orientations": [],
                }
                partial_pairs.append(pending_pair)

                restore_pending = _apply_variant(simulate_module, variant)
                try:
                    try:
                        variant_result, variant_calls = _run_arm_and_count(
                            scenario["concept"], stage, run_index, scenario["recipe_context"], mode, default_model,
                            prior_lines=(prior_by_scenario or {}).get(scenario["id"]),
                            recipe_facts=scenario.get("judge_recipe_facts"),
                        )
                    except BaseException as exc:
                        # #7714 finding 2 / round 3 / round 4 finding 1:
                        # save whatever variant turns were generated before
                        # ANY exception - not just a budget abort - instead
                        # of discarding them, mirroring _generate_and_
                        # judge_pairs' same fix. A non-budget error still
                        # re-raises so the sweep's outer handler writes a
                        # truthful error report.
                        made = getattr(exc, "conversation_lab_calls_made", 0)
                        budget.record(0 if dry_run else made)
                        pending_pair["status"] = "aborted_mid_arm" if isinstance(exc, LabBudgetAbort) else "error_mid_arm"
                        pending_pair["variant_calls_before_abort"] = made
                        pending_pair["variant_messages"] = getattr(exc, "conversation_lab_partial_messages", [])
                        if not isinstance(exc, LabBudgetAbort):
                            raise
                        aborted = True
                        break
                    variant_stop_check_log = _snapshot_stop_check_log()
                    variant_director_log = _snapshot_director_log()
                    variant_rewrite_log = _snapshot_rewrite_log()
                finally:
                    _restore_variant(simulate_module, restore_pending)
                    restore_pending = None
                budget.record(0 if dry_run else variant_calls)

                variant_messages = variant_result.get("messages", [])
                pending_pair["variant_messages"] = variant_messages
                pending_pair["variant_stop_check_log"] = variant_stop_check_log
                pending_pair["variant_director_log"] = variant_director_log
                pending_pair["variant_rewrite_log"] = variant_rewrite_log
                pending_pair["status"] = "awaiting_judges"
                _budget_checkpoint()

                if dry_run:
                    combined = _dry_run_combined()
                else:
                    if budget.would_exceed(1) or _would_exceed_cost(max_cost, baseline=baseline_cost):
                        aborted = True
                        break
                    first = _run_judge_orientation(
                        orientation="control_first", pending_pair=pending_pair, budget=budget,
                        judge_model=judge_model, concept=scenario["concept"], stage=stage,
                        recipe_context=scenario["recipe_context"], expected_cast=expected_cast,
                        first_arm="control", first_messages=control_messages,
                        second_arm="variant", second_messages=variant_messages,
                        recipe_facts=scenario.get("judge_recipe_facts"),
                        max_cost=max_cost, baseline=baseline_cost,
                    )

                    if budget.would_exceed(1) or _would_exceed_cost(max_cost, baseline=baseline_cost):
                        aborted = True
                        break
                    second = _run_judge_orientation(
                        orientation="variant_first", pending_pair=pending_pair, budget=budget,
                        judge_model=judge_model, concept=scenario["concept"], stage=stage,
                        recipe_context=scenario["recipe_context"], expected_cast=expected_cast,
                        first_arm="variant", first_messages=variant_messages,
                        second_arm="control", second_messages=control_messages,
                        recipe_facts=scenario.get("judge_recipe_facts"),
                        max_cost=max_cost, baseline=baseline_cost,
                    )
                    combined = _combine_orientations(first, second)

                completed_pair = {
                    "scenario_id": scenario["id"],
                    "run_index": run_index,
                    "control_messages": control_messages,
                    "variant_messages": variant_messages,
                    "control_stop_check_log": control_stop_check_log,
                    "variant_stop_check_log": variant_stop_check_log,
                    "control_director_log": control_director_log,
                    "variant_director_log": variant_director_log,
                    "control_rewrite_log": control_rewrite_log,
                    "variant_rewrite_log": variant_rewrite_log,
                    "control_summary": summarize(control_messages, expected_cast, concept=scenario["concept"], day=stage),
                    "variant_summary": summarize(variant_messages, expected_cast, concept=scenario["concept"], day=stage),
                    "judge": combined,
                    "judge_diagnostics": _orientation_diagnostics(first, second) if not dry_run else None,
                    "judge_orientations": pending_pair["judge_orientations"],
                    "dry_run": bool(dry_run),
                }
                pairs.append(completed_pair)
                partial_pairs.remove(pending_pair)
            if aborted:
                break
    except BaseException as exc:
        # The report writer catches this outside the function; expose the
        # truthful invocation count so a failed pair does not erase it.
        setattr(exc, "conversation_lab_calls_used", budget.used)
        raise
    finally:
        guard_ctx.__exit__(None, None, None)
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
    partial_pairs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "variant_name": variant_name,
        "variant_keys": sorted(variant.keys()),
        "aborted": aborted,
        "calls_used": calls_used,
        "cost": cost,
        "pairs": pairs,
        "partial_pairs": partial_pairs or [],
        "rewrite_summary": _pair_arm_rewrite_summaries(pairs),
        "length_stats": _pair_arm_length_stats(pairs),
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
        metric_coverage = report.get("metric_coverage", {})
        entry["metric_coverage"] = metric_coverage
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
    represented_control_keys = {
        (pair.get("scenario_id"), pair.get("run_index"))
        for variant_report in variant_reports.values()
        for pair in [*variant_report.get("pairs", []), *variant_report.get("partial_pairs", [])]
    }
    unpaired_control_transcripts = [
        {
            "scenario_id": scenario_id,
            "run_index": run_index,
            "messages": result.get("messages", []),
        }
        for (scenario_id, run_index), result in control_transcripts.items()
        if (scenario_id, run_index) not in represented_control_keys
    ]

    report = {
        "command": "ab",
        "mode": "sweep",
        **_pairwise_evaluator_metadata(),
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
        "unpaired_control_transcripts": unpaired_control_transcripts,
        "control_top_phrases": _arm_top_phrases(control_transcripts_by_key),
        "control_length_stats": _length_stats(
            [
                message
                for result in control_transcripts.values()
                for message in (result.get("messages") or [])
            ]
        ),
        "rewrite_summary": {
            "control": _rewrite_summary(
                [
                    entry
                    for result in control_transcripts.values()
                    for entry in (result.get("rewrite_log") or [])
                ]
            ),
            "variant": _rewrite_summary(
                [
                    entry
                    for variant_report in variant_reports.values()
                    for pair in variant_report.get("pairs", [])
                    for entry in (pair.get("variant_rewrite_log") or [])
                ]
            ),
        },
        "dry_run": bool(args.dry_run),
        "target_dimension": args.target,
        "decision_rule": DECISION_RULE_TEXT,
        "cost_summary": cost_summary,
        "jev_cost_usd": _jev_cost_usd_total(),
        **_openrouter_fields_for(args, _openrouter_cost_by_model_for_sweep(control_transcripts, variant_reports)),
        "variants": variant_reports,
        "ranking": _rank_sweep_variants(variant_reports, args.target),
        "results_file": str(result_path),
    }
    if error is not None:
        report["error"] = error
    prior_days = getattr(args, "prior_days_provenance", None)
    if prior_days is not None:
        report["prior_days"] = prior_days
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
    print(f"jev cost: ${report['jev_cost_usd']:.9f}")

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
            coverage = entry.get("metric_coverage", {})
            if all(isinstance(v, dict) for v in deltas.values()):
                for area, metrics in deltas.items():
                    inner = ", ".join(
                        f"{k} {v:+.3f}"
                        + (f" (n={coverage[k]['paired_samples']}/{coverage[k]['eligible_pairs']})" if k in coverage else "")
                        for k, v in metrics.items() if isinstance(v, (int, float))
                    )
                    if inner:
                        parts.append(f"{area}: {inner}")
            else:
                parts = [
                    f"{k} {v:+.3f}"
                    + (f" (n={coverage[k]['paired_samples']}/{coverage[k]['eligible_pairs']})" if k in coverage else "")
                    for k, v in deltas.items() if isinstance(v, (int, float))
                ]
            if parts:
                print("      area deltas (variant - control): " + " | ".join(parts))
        for key, sample in entry.get("metric_coverage", {}).items():
            if sample["paired_samples"] == 0:
                print(f"      {key}: unavailable (n=0/{sample['eligible_pairs']})")

    if report.get("error"):
        print(f"\nERROR: {report['error']}")
    _print_openrouter_costs(report)
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
    prior_by_scenario = _prepare_prior_days(args, [scenario["id"] for scenario in scenarios])
    runs = args.runs if args.runs is not None else DEFAULT_TESTBED_RUNS
    expected_cast = simulate_module.participants_for_day(args.stage)

    max_calls_derived = args.max_calls is None
    if max_calls_derived:
        args.max_calls = _derive_max_calls("sweep", scenario_count=len(scenarios), runs=runs, stage=args.stage)

    result_path = _ab_result_path(_results_dir(args), "sweep", sweep_dir)

    # Validate EVERY variant before the shared control is generated (Codex audit).
    # The shared control is generated once, up front, and paid for; a malformed
    # variant file used to surface only when its arm ran, after that spend.
    # Naming the offending file here costs nothing and saves the control.
    for _variant_name, _variant_body in variants.items():
        try:
            validate_variant(simulate_module, _variant_body)
        except ConversationLabError as exc:
            raise ConversationLabError(f"variant {_variant_name!r}: {exc}") from exc
        _refuse_if_stop_check_conflicts_with_models(args, _variant_body)

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
            prior_by_scenario=prior_by_scenario,
        )
        aborted = control_aborted

        if not control_aborted:
            for variant_name, variant in variants.items():
                variant_pairs: list[dict[str, Any]] = []
                variant_partial_pairs: list[dict[str, Any]] = []
                try:
                    v_aborted, v_calls, v_cost = _run_sweep_variant(
                        variant=variant, scenarios=scenarios, stage=args.stage, runs=runs, mode=mode,
                        default_model=default_model, judge_model=judge_model, expected_cast=expected_cast,
                        control_transcripts=control_transcripts, max_calls=args.max_calls, max_cost=args.max_cost,
                        dry_run=args.dry_run, pairs=variant_pairs,
                        partial_pairs=variant_partial_pairs,
                        prior_by_scenario=prior_by_scenario,
                    )
                except BaseException as exc:
                    variant_reports[variant_name] = _build_sweep_variant_report(
                        variant_name, variant, variant_pairs, True,
                        getattr(exc, "conversation_lab_calls_used", None), None, args.target, args.dry_run,
                        partial_pairs=variant_partial_pairs,
                    )
                    raise
                variant_reports[variant_name] = _build_sweep_variant_report(
                    variant_name, variant, variant_pairs, v_aborted, v_calls, v_cost, args.target, args.dry_run,
                    partial_pairs=variant_partial_pairs,
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

REFERENCE_PANEL_PROMPT_NOTE = (
    "Evaluator v1 framed A/B as independent generations and omitted named-character "
    "rules. Versioned v2 uses neutral candidate-version wording and production "
    "character rules verbatim. The consistent-name permutation remains diagnostic; "
    "its direction is an evidence-based hypothesis, not a human label."
)


def _load_reference_panel(path: Path) -> dict[str, Any]:
    """Load the frozen v0 panel and verify its declared transformations.

    This is deliberately an input-integrity check, not a quality rubric. It
    validates exact content-preserving controls and confines authored edits
    to the turns declared in the fixture.
    """
    try:
        panel_bytes = path.read_bytes()
        panel = json.loads(panel_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"conversation_lab calibrate: invalid reference panel {path}: {exc}") from exc
    if not isinstance(panel, dict) or panel.get("packet_id") != "voice-reference-panel-v0":
        raise SystemExit("conversation_lab calibrate: unsupported reference panel (expected voice-reference-panel-v0)")
    cases = panel.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SystemExit("conversation_lab calibrate: reference panel must contain cases")
    by_id = {c.get("id"): c for c in cases if isinstance(c, dict)}
    if len(by_id) != len(cases):
        raise SystemExit("conversation_lab calibrate: reference panel case ids must be unique")

    def turns(value: Any, where: str) -> list[dict[str, str]]:
        if not isinstance(value, list) or not value:
            raise SystemExit(f"conversation_lab calibrate: {where} must be a non-empty turn list")
        for index, turn in enumerate(value, start=1):
            if not isinstance(turn, dict) or not isinstance(turn.get("speaker"), str) or not isinstance(turn.get("text"), str):
                raise SystemExit(f"conversation_lab calibrate: malformed {where} turn {index}")
        return value

    try:
        identical = by_id["identical-pair"]["inputs"]
        a = turns(identical["A"], "identical-pair A")
        b = turns(identical["B"], "identical-pair B")
        if a != b:
            raise ValueError("identical-pair A and B differ")

        swap = by_id["consistent-name-permutation"]["inputs"]
        original = turns(swap["original"], "consistent-name-permutation original")
        permuted = turns(swap["permuted"], "consistent-name-permutation permuted")
        name_swap = {"Margaret Chen": "Ria Castillo", "Ria Castillo": "Margaret Chen"}
        expected = [{"speaker": name_swap.get(t["speaker"], t["speaker"]), "text": t["text"]} for t in original]
        if permuted != expected:
            raise ValueError("consistent-name-permutation is not the exact Margaret/Ria label swap")

        mixed_case = by_id["one-turn-each-label-exchange"]["inputs"]
        base = turns(mixed_case["original"], "one-turn-each-label-exchange original")
        mixed = turns(mixed_case["mixed_labels"], "one-turn-each-label-exchange mixed")
        expected_mixed = [dict(t) for t in base]
        if len(base) != 5:
            raise ValueError("one-turn-each-label-exchange must contain five turns")
        expected_mixed[1]["speaker"], expected_mixed[2]["speaker"] = (
            expected_mixed[2]["speaker"], expected_mixed[1]["speaker"]
        )
        if mixed != expected_mixed:
            raise ValueError("one-turn-each-label-exchange must exchange labels on turns 2 and 3 only")

        w38_case = by_id["w38-agreement-heavy-manual-variant"]["inputs"]
        w38 = turns(w38_case["original"], "w38 original")
        edited = turns(w38_case["manual_variant"], "w38 manual_variant")
        if len(w38) != 7 or len(edited) != 7:
            raise ValueError("W38 comparison must contain seven turns per arm")
        changed = [i for i, (left, right) in enumerate(zip(w38, edited), start=1) if left != right]
        if changed != [5, 6] or any(w38[i - 1]["speaker"] != edited[i - 1]["speaker"] for i in changed):
            raise ValueError("W38 manual variant may change text on turns 5 and 6 only, preserving speakers")

        topic = by_id["optional-template-topic-confound"]["inputs"].get("synthetic_messages")
        turns(topic, "optional-template-topic-confound")
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"conversation_lab calibrate: invalid reference panel structure: {exc}") from exc

    panel["_loaded_fixture_sha256"] = hashlib.sha256(panel_bytes).hexdigest()
    return panel


def _reference_pairs(panel: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only declared A/B comparisons; the optional topic probe is not a pair."""
    input_fields = {
        "identical-pair": ("A", "B"),
        "consistent-name-permutation": ("original", "permuted"),
        "one-turn-each-label-exchange": ("original", "mixed_labels"),
        "w38-agreement-heavy-manual-variant": ("original", "manual_variant"),
    }
    result = []
    for case in panel["cases"]:
        if case["id"] not in input_fields:
            continue
        left_key, right_key = input_fields[case["id"]]
        scene = (panel.get("scene_contexts") or {}).get(case.get("scene_source"), {})
        if not scene.get("concept") or not scene.get("recipe_context"):
            raise SystemExit(f"conversation_lab calibrate: missing scene context for {case['id']}")
        result.append({
            "case_id": case["id"],
            "target_property": case["target_property"],
            "structural_hypothesis": case["structural_hypothesis"],
            "concept": scene["concept"],
            "recipe_context": scene["recipe_context"],
            "left_messages": [
                {"character": turn["speaker"], "message": turn["text"]}
                for turn in case["inputs"][left_key]
            ],
            "right_messages": [
                {"character": turn["speaker"], "message": turn["text"]}
                for turn in case["inputs"][right_key]
            ],
        })
    return result


def _reference_acceptance_plan() -> dict[str, Any]:
    """Proposed, reviewable gates; these are not human labels or defaults."""
    return {
        "identical-pair": {
            "target_dimension": "overall logical-tie control",
            "proposed_acceptance": "unanimous ties in both orientations for every repeat",
        },
        "one-turn-each-label-exchange": {
            "target_dimension": "voice_distinctiveness",
            "proposed_acceptance": "original preferred in at least 80% of completed pairs",
        },
        "w38-agreement-heavy-manual-variant": {
            "target_dimension": ["natural_progression (diagnostic only)", "raw judge reason (pushback diagnostic)"],
            "proposed_acceptance": "no binary gate; the current rubric has no dedicated pushback dimension, and turn_taking does not isolate it",
        },
        "consistent-name-permutation": {
            "target_dimension": "voice_distinctiveness; named-character fit and anonymous separability remain separate interpretations",
            "proposed_acceptance": "diagnostic only; the original named-character fit is a source-based hypothesis, not a human-labeled direction",
        },
        "reporting": [
            "valid judge response rate per case",
            "position-order agreement per case and dimension",
            "repetition stability per case and dimension",
        ],
    }

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
        **_pairwise_evaluator_metadata(),
        "episode_id": args.from_episode,
        "stage": args.stage,
        "concept": concept,
        "dry_run": bool(args.dry_run),
        "aborted": aborted,
        "max_calls": args.max_calls,
        "max_cost": args.max_cost,
        "calls_used": budget.used,
        **_openrouter_fields_for(args, _openrouter_router_cost_by_model()),
        "degradations": degradation_reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_file": str(result_path),
    }
    if error is not None:
        report["error"] = error
    return report


def _cmd_calibrate_reference_panel(args: argparse.Namespace) -> None:
    if args.runs < 1:
        raise SystemExit("conversation_lab calibrate: --runs must be at least 1 for --reference-panel")
    panel_path = Path(args.reference_panel)
    panel = _load_reference_panel(panel_path)
    pairs = _reference_pairs(panel)
    judge_model = _resolve_judge_model_for_args(args)
    budget = CallBudget(max_calls=args.max_calls)
    result_path = _results_dir(args) / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}-calibrate-reference-panel-v0.json"
    )
    reports: dict[str, Any] = {}
    aborted = False
    error = None

    def evidence_summary() -> dict[str, Any]:
        if args.dry_run:
            return {
                "status": "not collected under dry-run",
                "valid_responses": 0,
                "attempted_orientations": 0,
                "valid_response_rate": None,
                "attempted_valid_response_rate": None,
                "planned_orientation_count": 0,
                "planned_orientation_coverage": None,
                "valid_response_rate_by_case": {},
                "order_agreement_by_case_and_dimension": {},
                "repetition_stability_by_case_and_dimension": {},
            }
        all_orientations = [
            orientation
            for case_report in reports.values()
            for pair in [*case_report.get("completed_pairs", []), *case_report.get("partial_pairs", [])]
            for orientation in pair.get("judge_orientations", [])
        ]
        valid = sum(1 for item in all_orientations if isinstance(item.get("result"), dict))
        planned_orientation_count = len(pairs) * args.runs * 2
        summary: dict[str, Any] = {
            "valid_responses": valid,
            "attempted_orientations": len(all_orientations),
            "valid_response_rate": round(valid / len(all_orientations), 4) if all_orientations else None,
            "attempted_valid_response_rate": round(valid / len(all_orientations), 4) if all_orientations else None,
            "planned_orientation_count": planned_orientation_count,
            "planned_orientation_coverage": (
                round(len(all_orientations) / planned_orientation_count, 4)
                if planned_orientation_count else None
            ),
            "valid_response_rate_by_case": {},
            "order_agreement_by_case_and_dimension": {},
            "repetition_stability_by_case_and_dimension": {},
        }
        for case_id, case_report in reports.items():
            pairs_for_case = case_report.get("completed_pairs", [])
            case_orientations = [
                orientation
                for pair in [*case_report.get("completed_pairs", []), *case_report.get("partial_pairs", [])]
                for orientation in pair.get("judge_orientations", [])
            ]
            case_valid = sum(1 for item in case_orientations if isinstance(item.get("result"), dict))
            summary["valid_response_rate_by_case"][case_id] = {
                "valid_responses": case_valid,
                "attempted_orientations": len(case_orientations),
                "valid_response_rate": round(case_valid / len(case_orientations), 4) if case_orientations else None,
            }
            dimensions = ("overall", *ALL_JUDGE_DIMENSIONS)
            order: dict[str, Any] = {}
            repeat: dict[str, Any] = {}
            for dimension in dimensions:
                diag = [p.get("judge_diagnostics", {}).get(dimension, {}).get("status") for p in pairs_for_case]
                comparable = [status for status in diag if status]
                agreements = sum(status in {"agreement", "unanimous_tie"} for status in comparable)
                order[dimension] = {
                    "agreements": agreements,
                    "comparable_pairs": len(comparable),
                    "rate": round(agreements / len(comparable), 4) if comparable else None,
                }
                values = [p.get("judge", {}).get(dimension) for p in pairs_for_case]
                values = [value for value in values if value is not None]
                counts = dict(Counter(values))
                repeat[dimension] = {
                    "stable": (len(set(values)) <= 1) if len(values) >= 2 else None,
                    "runs_with_result": len(values),
                    "result_counts": counts,
                }
            summary["order_agreement_by_case_and_dimension"][case_id] = order
            summary["repetition_stability_by_case_and_dimension"][case_id] = repeat
        return summary

    def make_report() -> dict[str, Any]:
        cost_summary_error = None
        try:
            cost_summary = model_router.get_cost_summary()
        except Exception as exc:
            cost_summary = {}
            cost_summary_error = f"{type(exc).__name__}: {exc}"
        return {
            "command": "calibrate",
            "mode": "reference_panel",
            "panel_id": panel["packet_id"],
            "panel_path": str(panel_path),
            "panel_sha256": panel["_loaded_fixture_sha256"],
            "source_sha256": {
                name: source.get("sha256") for name, source in (panel.get("sources") or {}).items()
            },
            "validation_checks": [
                "identical-pair content equality",
                "consistent Margaret/Ria label permutation with exact text preservation",
                "one-turn-each label exchange on W25 turns 2 and 3 with exact text preservation",
                "W38 manual edits limited to text on turns 5 and 6; speaker labels preserved",
                "optional synthetic topic-confound shape",
            ],
            "dry_run": bool(args.dry_run),
            "aborted": aborted,
            "max_calls": args.max_calls,
            "max_cost": args.max_cost,
            "calls_used": budget.used,
            "cost_summary": cost_summary,
            **({"cost_summary_error": cost_summary_error} if cost_summary_error else {}),
            **_openrouter_fields_for(args, _openrouter_router_cost_by_model()),
            "evaluator": {
                "prompt_sha256": hashlib.sha256(PAIRWISE_JUDGE_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
                "prompt_version": PAIRWISE_JUDGE_PROMPT_VERSION,
                "prompt_note": REFERENCE_PANEL_PROMPT_NOTE,
                "model": judge_model,
            },
            "proposed_acceptance_plan": _reference_acceptance_plan(),
            "evidence_summary": evidence_summary(),
            "requested_runs_per_case": args.runs,
            "planned_pairs": len(pairs) * args.runs,
            "planned_judge_orientations": len(pairs) * args.runs * (0 if args.dry_run else 2),
            "cases": reports,
            "results_file": str(result_path),
            **({"error": error} if error else {}),
        }

    guard_ctx = _installed_budget_guard(budget, args.max_cost)
    guard_ctx.__enter__()
    try:
        for case in pairs:
            case_id = case["case_id"]
            pair_records: list[dict[str, Any]] = []
            partial_records: list[dict[str, Any]] = []
            case_report = {
                "target_property": case["target_property"],
                "structural_hypothesis": case["structural_hypothesis"],
                "target_dimension": _reference_acceptance_plan().get(case_id, {}).get("target_dimension"),
                "proposed_acceptance": _reference_acceptance_plan().get(case_id, {}).get("proposed_acceptance"),
                "concept": case["concept"],
                "recipe_context": case["recipe_context"],
                "left_cast": sorted({m["character"] for m in case["left_messages"]}),
                "right_cast": sorted({m["character"] for m in case["right_messages"]}),
                "completed_pairs": pair_records,
                "partial_pairs": partial_records,
                "interpretation": (
                    "DRY RUN - validation only; no judge signal"
                    if args.dry_run else "EXPLORATORY - versioned pairwise evaluator; no human-label verdict"
                ),
            }
            reports[case_id] = case_report
            for run_index in range(1, args.runs + 1):
                pending: dict[str, Any] = {
                    "run_index": run_index,
                    "status": "awaiting_judges",
                    "left_messages": case["left_messages"],
                    "right_messages": case["right_messages"],
                    "judge_orientations": [],
                }
                partial_records.append(pending)
                if args.dry_run:
                    combined = _dry_run_combined()
                    diagnostics = None
                else:
                    speakers = sorted({m["character"] for m in [*case["left_messages"], *case["right_messages"]]})
                    judge_kwargs = {
                        "judge_model": judge_model,
                        "concept": case["concept"],
                        "stage": "reference scene",
                        "recipe_context": case["recipe_context"],
                        "expected_cast": speakers,
                        "recipe_facts": None,
                        "max_cost": args.max_cost,
                    }
                    if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                        aborted = True
                        break
                    first = _run_judge_orientation(
                        orientation="left_first", pending_pair=pending, budget=budget,
                        first_arm="left", first_messages=case["left_messages"],
                        second_arm="right", second_messages=case["right_messages"],
                        **judge_kwargs,
                    )
                    if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                        aborted = True
                        break
                    second = _run_judge_orientation(
                        orientation="right_first", pending_pair=pending, budget=budget,
                        first_arm="right", first_messages=case["right_messages"],
                        second_arm="left", second_messages=case["left_messages"],
                        **judge_kwargs,
                    )
                    combined = _combine_orientations(first, second)
                    diagnostics = _orientation_diagnostics(first, second)
                pair_records.append({
                    "run_index": run_index,
                    "judge": combined,
                    "judge_diagnostics": diagnostics,
                    "judge_orientations": pending["judge_orientations"],
                })
                partial_records.remove(pending)
            if aborted:
                break
    except LabBudgetAbort:
        # #7714 round 2: same reasoning as cmd_calibrate's --from-episode
        # path - a mid-arm budget abort is the SAME event the coarse
        # would_exceed()/_would_exceed_cost() checks above already handle
        # inline, and must read the same way: aborted, no `error`, no
        # re-raise past main() as an uncaught exception.
        aborted = True
        report = make_report()
        _write_json_result(result_path, report)
        print(f"\n=== conversation_lab calibrate: reference panel {panel['packet_id']} ===")
        print(f"status: ABORTED  calls used: {report['calls_used']} / max {report['max_calls']}  dry_run={report['dry_run']}")
        print(f"\nresults written to: {report['results_file']}")
        return
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        aborted = True
        _write_json_result(result_path, make_report())
        raise
    finally:
        guard_ctx.__exit__(None, None, None)

    report = make_report()
    _write_json_result(result_path, report)
    print(f"\n=== conversation_lab calibrate: reference panel {panel['packet_id']} ===")
    run_status = "ABORTED" if report["aborted"] else "COMPLETE"
    print(f"status: {run_status}  calls used: {report['calls_used']} / max {report['max_calls']}  dry_run={report['dry_run']}")
    if not args.dry_run:
        print(f"NOTICE: {REFERENCE_PANEL_PROMPT_NOTE}")
    print("preregistered operational checks (not human labels):")
    for case_id, gate in report["proposed_acceptance_plan"].items():
        if isinstance(gate, dict):
            print(f"  {case_id}: {gate['target_dimension']} — {gate['proposed_acceptance']}")
    print("consistent-name-permutation: source-based named-fit hypothesis only; no pass/fail gate")
    for name, info in reports.items():
        print(f"  {name}: completed={len(info['completed_pairs'])} partial={len(info['partial_pairs'])} {info['interpretation']}")
    _print_openrouter_costs(report)
    print(f"\nresults written to: {report['results_file']}")

# ---------------------------------------------------------------------------
# bench (#7314) - characterize ONE setting over N runs
#
# `ab` answers "is variant B better than control A". It cannot answer "what
# does setting A actually produce", because it only ever emits pairwise
# win/tie/loss verdicts - there is no absolute number for a later run to be
# compared against. Erik, 2026-09-19: "We need to know what a setting
# creates, so running it 30 times gives us enough numbers to find some sort
# of average. We make one change and then see where that average moves."
#
# So bench runs ONE arm N times, scores every run with the free deterministic
# metrics AND with the production publish gate, and reports mean/spread per
# metric plus a judge pass rate. `--compare` diffs two such runs.
# ---------------------------------------------------------------------------

# |z| at or above this is reported as a moved average rather than noise.
# Two standard errors of the difference is the usual two-sigma convention;
# it is a screening threshold for deciding what to look at next, NOT a
# significance test - the runs are not independent samples of a stable
# population and no multiple-comparison correction is applied across the
# ~25 metrics compared at once.
_BENCH_MOVED_Z = 2.0

def _frozen_prior_stages(episode: dict[str, Any], stage: str) -> dict[str, Any]:
    """The days BEFORE `stage`, as the production judge would see them.

    backend/admin/cron_routes.py's `_judge_dialogue` walks DAY_ORDER and
    feeds every earlier day's dialogue to the judge as PREVIOUS DAYS
    context, so judging a Saturday transcript with no week behind it is
    not the gate that actually runs in production. Freezing one real
    episode's earlier days keeps every run in a bench judged against
    identical context, which is what makes the runs comparable.
    """
    prior: dict[str, Any] = {}
    for day in simulate_module.DAY_ORDER:
        if day == stage:
            break
        dialogue = ((episode.get("stages") or {}).get(day) or {}).get("dialogue") or []
        if dialogue:
            prior[day] = {"dialogue": dialogue}
    return prior

def _calls_now() -> int | None:
    """Current total_calls, or None when the counter cannot be read.

    None is distinct from 0 on purpose (Codex): a zero delta means "no
    paid call happened" (template mode, a monkeypatched model), while an
    unreadable counter means "spending is invisible". Collapsing both to 0
    made the accounting-failure path charge the small fallback for a unit
    that can really cost forty calls, so `--max-calls` stopped being an
    upper bound exactly when the tracking broke.
    """
    try:
        return model_router.get_cost_summary().get("total_calls", 0)
    except Exception:
        return None

def _spend(budget: CallBudget, fn=None, *, fallback: int = 1, reservation: int | None = None):
    """Run `fn` and record what it spent - even if it raises.

    The budget CHECK reserves a worst case before a unit starts; this is
    what gets RECORDED after it, so `calls_used` reports real spending
    rather than reservations.

    The `finally` is the point (Codex): `run_simulation` can raise after
    making paid requests - a turn whose second CoT-leak retry still leaks
    raises RuntimeError - and recording only on the success path left
    those calls out of the report entirely, so a first-arm failure
    reported `calls_used: 0` while real money had been spent. An audit
    trail that under-reports on exactly the paths worth auditing is worse
    than none.

    `fallback` is used only when the cost log did not move, which means no
    real call happened: a template run, or a monkeypatched model in tests.
    """
    before = _calls_now()
    try:
        return fn()
    finally:
        after = _calls_now()
        if before is None or after is None:
            # Spending is invisible, so assume the worst rather than the
            # best: charge what was reserved for this unit. Under-recording
            # here is what let a 60-call cap permit 82 (Codex).
            budget.record(reservation if reservation is not None else fallback)
        else:
            delta = after - before
            budget.record(delta if delta > 0 else fallback)

def _judge_one_transcript(
    *,
    concept: str,
    stage: str,
    messages: list[dict[str, Any]],
    prior_stages: dict[str, Any],
    recipe_context: str | None,
    recipe_facts: str | None,
    judge_model: str,
) -> dict[str, Any]:
    """Score one transcript with the PRODUCTION publish gate, not the lab's
    pairwise judge.

    That is deliberate: a bench number is only useful if it predicts what
    the Sunday cron will do, so this calls the same `_judge_dialogue` the
    cron calls. It writes its structured scores onto the episode dict it
    is handed (see that function's docstring), so each run gets a FRESH
    outer dict - `prior_stages` is shared read-only, the judge_* keys are
    not, and reusing one dict would let run N read run N-1's scores.

    ``judge_model`` is the lab-resolved judge route (direct Anthropic under
    --provider anthropic, the same model through OpenRouter under
    --provider openrouter); production callers that invoke `_judge_dialogue`
    directly still default to ``config.judge_model``.
    """
    episode: dict[str, Any] = {"stages": prior_stages}
    passed, verdict = judge_dialogue(
        concept,
        stage,
        messages,
        episode,
        recipe_context=recipe_context,
        recipe_facts=recipe_facts,
        judge_model=judge_model,
    )
    _budget_checkpoint()
    return {  # noqa: DOC201 - the caller measures calls separately
        "passed": bool(passed),
        "verdict": verdict,
        "scores": (episode.get("judge_scores") or {}).get(stage) or {},
        "weakest": (episode.get("judge_weakest") or {}).get(stage) or [],
        "reason": (episode.get("judge_reason") or {}).get(stage) or "",
    }

def _distribution(values: list[Any]) -> dict[str, Any] | None:
    """mean / spread / range for one metric across a bench's runs.

    `stdev` is the SAMPLE standard deviation (n-1): these runs are a
    sample used to estimate where the next run would land, not the whole
    population. `stderr` is what `--compare` actually uses - the spread of
    the MEAN is what decides whether a moved average moved.

    Non-finite samples are dropped rather than aggregated (Codex). The
    production judge's parser accepts JSON `NaN`/`Infinity` and stores the
    scores unchecked, so one bad verdict would otherwise persist a
    non-finite aggregate on a single-run bench, or raise ValueError out of
    `stdev` on a multi-run one - after every call was paid for and outside
    the partial-result recovery. `n` reflects the samples actually used,
    so a dropped score shows up as a smaller n rather than silently
    skewing the mean.
    """
    vals = [
        v
        for v in values
        if isinstance(v, (int, float)) and not isinstance(v, bool) and isfinite(v)
    ]
    if not vals:
        return None
    spread = stdev(vals) if len(vals) > 1 else 0.0
    # NOT rounded (Codex): _bench_delta divides by stderr, and two genuinely
    # nonzero errors can both round to 0.0 at 4dp - which the zero-variance
    # branch then reads as "perfectly repeatable" for a delta whose real |z|
    # is well under 2. Full precision here; rounding happens where numbers
    # are printed.
    return {
        "n": len(vals),
        "mean": mean(vals),
        "stdev": spread,
        "stderr": (spread / sqrt(len(vals))) if len(vals) > 1 else 0.0,
        "min": min(vals),
        "max": max(vals),
    }

# The judge's own contract: _JUDGE_SYSTEM_PROMPT asks for every dimension
# on a 1-5 scale. Scores outside it are malformed verdicts, not data.
_JUDGE_SCORE_RANGE = (1, 5)

def _is_valid_judge_score(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and _JUDGE_SCORE_RANGE[0] <= value <= _JUDGE_SCORE_RANGE[1]
    )


def _usable_bench_verdict(run: Any) -> bool:
    """Whether a production judge produced a complete scored verdict."""
    if not isinstance(run, dict) or not isinstance(run.get("judge"), dict):
        return False
    judge = run["judge"]
    scores = judge.get("scores")
    verdict = judge.get("verdict")
    passed = judge.get("passed")
    verdict_passed = verdict == "PASS" or (
        isinstance(verdict, str) and verdict.startswith("PASS -")
    )
    verdict_failed = verdict == "FAIL" or (
        isinstance(verdict, str)
        and (verdict.startswith("FAIL -") or verdict.startswith("FAIL |"))
    )
    return (
        isinstance(passed, bool)
        and ((passed and verdict_passed) or (not passed and verdict_failed))
        and isinstance(scores, dict)
        and all(_is_valid_judge_score(scores.get(dim)) for dim in JUDGE_DIMENSIONS)
    )


def _bench_judge_coverage(runs: list[Any]) -> dict[str, Any]:
    scored = sum(1 for run in runs if _usable_bench_verdict(run))
    attempted = len(runs)
    return {
        "attempted_runs": attempted,
        "scored_verdicts": scored,
        "unjudged_runs": attempted - scored,
        "rate": round(scored / attempted, 4) if attempted else None,
        "applicable": True,
    }


def _baseline_judge_summary(baseline: dict[str, Any]) -> dict[str, Any]:
    """Re-derive legacy pass rates from per-run evidence, never trust old aggregates."""
    if baseline.get("dry_run") is True:
        return {
            "pass_count": None,
            "pass_rate": None,
            "judge_coverage": {
                "attempted_runs": 0, "scored_verdicts": 0, "unjudged_runs": 0,
                "rate": None, "applicable": False,
            },
            "unavailable_reason": "baseline is a dry run; judge scoring is not applicable",
        }
    runs = baseline.get("runs")
    if not isinstance(runs, list):
        return {
            "pass_count": None,
            "pass_rate": None,
            "judge_coverage": None,
            "unavailable_reason": (
                "baseline has no per-run judge evidence; stored aggregate pass rate "
                "may include unjudged failures"
            ),
        }
    coverage = _bench_judge_coverage(runs)
    scored = [run for run in runs if _usable_bench_verdict(run)]
    pass_count = sum(1 for run in scored if run["judge"]["passed"])
    rate = round(pass_count / len(scored), 4) if scored else None
    reason = "baseline has no complete usable scored verdicts" if not scored else None
    return {
        "pass_count": pass_count if scored else None,
        "pass_rate": rate,
        "judge_coverage": coverage,
        "unavailable_reason": reason,
    }

def _bench_aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Collapse per-run summaries and verdicts into one distribution each."""
    metric_keys = sorted({k for run in runs for k in _numeric_keys(run.get("summary") or {})})
    metrics: dict[str, Any] = {}
    for key in metric_keys:
        dist = _distribution([(run.get("summary") or {}).get(key) for run in runs])
        if dist:
            metrics[key] = dist

    judged = [run for run in runs if isinstance(run, dict) and isinstance(run.get("judge"), dict)]
    dimensions: dict[str, Any] = {}
    for dim in JUDGE_DIMENSIONS:
        # Clamped to the judge's own contract (Codex). _JUDGE_SYSTEM_PROMPT
        # defines every dimension as 1-5, but the verdict parser stores
        # whatever JSON it is handed, so a malformed `50` would sail
        # through _distribution's numeric-and-finite check and drag a
        # dimension mean far enough to invent or hide movement. An
        # out-of-range score is a broken verdict, not a datum.
        samples = [
            score
            for run in judged
            for score in [((run["judge"].get("scores")) or {}).get(dim)
                          if isinstance(run["judge"].get("scores"), dict) else None]
            if _is_valid_judge_score(score)
        ]
        # A dimension with no usable samples is recorded as an explicit
        # n=0 placeholder rather than omitted (Codex). _bench_delta walks
        # the current aggregate's keys, so dropping the key produced no
        # row, no indeterminate count, and a summary that said nothing
        # moved - a TOTAL measurement failure reading as "no change",
        # which is the exact inversion this tool must never print.
        dimensions[dim] = _distribution(samples) or {
            "n": 0,
            "mean": 0.0,
            "stdev": 0.0,
            "stderr": 0.0,
            "min": 0.0,
            "max": 0.0,
            "no_valid_samples": True,
        }

    weakest_counts: Counter[str] = Counter()
    usable = [run for run in judged if _usable_bench_verdict(run)]
    for run in usable:
        weakest_counts.update(str(w) for w in (run["judge"].get("weakest") or []))

    pass_count = sum(1 for run in usable if run["judge"]["passed"])
    coverage = _bench_judge_coverage(runs)
    return {
        "metrics": metrics,
        "dimensions": dimensions,
        "judged_runs": len(usable),
        "scored_verdicts": len(usable),
        "unjudged_runs": coverage["unjudged_runs"],
        "judge_coverage": coverage,
        "pass_count": pass_count,
        "pass_rate": round(pass_count / len(usable), 4) if usable else None,
        "weakest_counts": dict(weakest_counts.most_common()),
    }

def _bench_delta(
    current: dict[str, Any], baseline: dict[str, Any], section: str = "metrics"
) -> dict[str, Any]:
    """Compare every key seen in either aggregate without inventing samples.

    `z` is the difference in means over the standard error of that
    difference. See `_BENCH_MOVED_Z` for what it is and is not.

    `section` selects which distributions to walk. It exists because this
    only ever read `aggregate.metrics` (Codex): a prompt change that moved
    a judge dimension - `natural_progression`, or the `voice_
    distinctiveness` that has sat at 3 for weeks - while the deterministic
    metrics stayed flat was reported as "nothing moved", which is the tool
    failing at the exact job it was built for.
    """
    current_distributions = current.get(section)
    baseline_distributions = baseline.get(section)
    current_distributions = current_distributions if isinstance(current_distributions, dict) else {}
    baseline_distributions = baseline_distributions if isinstance(baseline_distributions, dict) else {}
    rows: dict[str, Any] = {}
    for key in sorted(set(current_distributions) | set(baseline_distributions)):
        cur = current_distributions.get(key)
        base = baseline_distributions.get(key)
        cur = cur if isinstance(cur, dict) else None
        base = base if isinstance(base, dict) else None
        cur_n = cur.get("n", 0) if cur else 0
        base_n = base.get("n", 0) if base else 0
        current_available = bool(cur and cur_n > 0 and not cur.get("no_valid_samples"))
        baseline_available = bool(base and base_n > 0 and not base.get("no_valid_samples"))
        common = {
            "baseline_available": baseline_available,
            "current_available": current_available,
            "baseline_n": base_n,
            "current_n": cur_n,
        }
        if not current_available or not baseline_available:
            rows[key] = {
                **common,
                "status": "unavailable",
                "unavailable": True,
                "no_valid_samples": True,
                "baseline_mean": base.get("mean") if baseline_available else None,
                "mean": cur.get("mean") if current_available else None,
                "delta": None,
                "stderr_diff": None,
                "z": None,
                "moved": None,
                "estimable": False,
            }
            continue

        delta = cur["mean"] - base["mean"]
        se = sqrt(cur["stderr"] ** 2 + base["stderr"] ** 2)
        estimable = cur.get("n", 0) >= 2 and base.get("n", 0) >= 2
        if not estimable:
            # Checked FIRST (Codex). One arm at n=1 contributes a zero
            # standard error, so if the OTHER arm varies, `se` is nonzero
            # and a large delta was still being called `moved` even though
            # half the comparison had no variance estimate at all.
            z: float | None = None
            moved: bool | None = None
        elif se:
            z = delta / se
            moved = abs(z) >= _BENCH_MOVED_Z
        else:
            # Both arms had >= 2 samples and neither varied: a genuinely
            # repeatable shift. No z exists (the denominator is zero), but
            # reporting z=0.0 would have filed it under "did not move".
            z = None
            moved = delta != 0
        rows[key] = {
            **common,
            "status": "indeterminate" if moved is None else ("moved" if moved else "unchanged"),
            "unavailable": False,
            "no_valid_samples": False,
            "baseline_mean": base["mean"],
            "mean": cur["mean"],
            "delta": round(delta, 4),
            "stderr_diff": round(se, 4),
            "z": None if z is None else round(z, 4),
            "moved": moved,
            "estimable": estimable,
        }
    return rows


def _comparison_row_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, float, str]:
    """Order moved, indeterminate, unavailable, then unchanged rows safely."""
    key, row = item
    order = {"moved": 0, "indeterminate": 1, "unavailable": 2, "unchanged": 3}
    delta = row.get("delta")
    z = row.get("z")
    magnitude = abs(z) if isinstance(z, (int, float)) else (
        abs(delta) if isinstance(delta, (int, float)) else 0.0
    )
    return order.get(row.get("status"), 4), -magnitude, key


def _comparison_summary(label: str, rows: dict[str, dict[str, Any]]) -> str:
    """Summarize movement while naming unavailable and indeterminate rows."""
    if not rows:
        return f"{label} comparison unavailable: neither bench recorded any measurements."
    counts = Counter(row.get("status", "unavailable") for row in rows.values())
    parts = [f"{counts[status]} {status}" for status in ("moved", "unchanged", "indeterminate", "unavailable") if counts[status]]
    if counts["unavailable"] == len(rows):
        return f"{label} comparison unavailable for all rows ({'; '.join(parts)})."
    if counts["moved"]:
        lead = f"{counts['moved']} {label} moved"
    elif counts["indeterminate"] or counts["unavailable"]:
        lead = f"No {label} movement established"
    else:
        lead = f"No {label} moved"
    return lead + "; " + "; ".join(parts) + "."


def _pass_rate_label(
    rate: float | None, coverage: dict[str, Any] | None, unavailable_reason: str | None = None
) -> str:
    if isinstance(coverage, dict) and coverage.get("applicable") is False:
        return "n/a (dry run)"
    if rate is None:
        if unavailable_reason:
            return f"unavailable ({unavailable_reason})"
        scored = coverage.get("scored_verdicts", 0) if isinstance(coverage, dict) else 0
        attempted = coverage.get("attempted_runs", 0) if isinstance(coverage, dict) else 0
        return f"unavailable ({scored}/{attempted} complete scored verdicts)"
    if isinstance(coverage, dict):
        return (
            f"{rate:.0%} ({coverage.get('scored_verdicts', 0)}/"
            f"{coverage.get('attempted_runs', 0)} scored; "
            f"{coverage.get('unjudged_runs', 0)} unjudged)"
        )
    return f"{rate:.0%} (scoring coverage unavailable)"

def _resolve_bench_scenario(
    args: argparse.Namespace,
) -> tuple[str, str | None, str | None, dict[str, Any], dict[str, Any]]:
    """Return concept, recipe anchor/facts, prior stages, and frozen photo inputs.

    Deliberately does NOT reuse `_resolve_recipe_context_and_facts`: that
    helper loads the episode and throws it away, and bench needs the same
    episode for both the judge's PREVIOUS DAYS context and the real recipe
    title, so loading it once here avoids a second CDN fetch.
    """
    if args.recipe_context:
        return args.concept, args.recipe_context, None, {}, _bench_photography_inputs({}, args.stage)

    episode = _load_episode(args.from_episode, local=args.local)
    stage_data = (episode.get("stages") or {}).get(args.stage) or {}
    recipe_data = _stage_recipe_data(episode, stage_data)
    recipe_context = _build_recipe_context(recipe_data)
    if not recipe_context:
        raise ConversationLabError(
            f"episode {args.from_episode!r} has no usable recipe_data for stage "
            f"{args.stage!r} - pass --recipe-context and --concept instead"
        )
    concept = args.concept or _episode_concept(episode)
    recipe_facts = _build_judge_recipe_facts(recipe_data) or None
    photo_inputs = _bench_photography_inputs(episode, args.stage)
    return concept, recipe_context, recipe_facts, _frozen_prior_stages(episode, args.stage), photo_inputs

# Longest slugified --label allowed. The result filename is
# "bench-<label>-<20-char stamp>[-N].json", and most filesystems cap a single
# path component at 255 bytes, so this leaves comfortable room for the stamp,
# the collision suffix and the extension.
_MAX_LABEL_SLUG_LEN = 180

def _validate_bench_args(args: argparse.Namespace) -> None:
    """Pure flag checks, run BEFORE anything that costs or can fail.

    Ordering is the point: `_resolve_models` raises when DIALOGUE_MODEL is
    unset and `_resolve_bench_scenario` fetches an episode, so doing either
    first meant a simple flag typo surfaced as "DIALOGUE_MODEL is not set"
    instead of naming the flag the caller actually got wrong.
    """
    if args.runs < 1:
        raise ConversationLabError("--runs must be at least 1")
    if bool(args.from_episode) == bool(args.recipe_context):
        raise ConversationLabError("pass exactly one of --from-episode or --recipe-context")
    if args.recipe_context and not args.concept:
        raise ConversationLabError("--recipe-context also needs --concept (no episode to read a title from)")
    # Checked here, not at write time (Codex): a long --label only failed
    # when _unique_result_path built the filename, which is after the whole
    # bench has been paid for and after the partial-result recovery has
    # ended - so ENAMETOOLONG published nothing at all.
    if args.label and len(_slugify(args.label)) > _MAX_LABEL_SLUG_LEN:
        raise ConversationLabError(
            f"--label is too long: {len(_slugify(args.label))} characters after "
            f"slugification, maximum {_MAX_LABEL_SLUG_LEN}. It becomes part of the "
            f"result filename."
        )

# The most paid calls ONE dialogue turn can cost, traced through
# scripts/simulate_dialogue_week.py's generate_turn:
#   1. the initial generate_response
#   2. _guard_cot_leak's retry on that response
#   3. the fault rewrite (repetition / saturated shape / word budget)
#   4. _guard_cot_leak's retry on the rewrite
# Codex caught this on PR #119: the first fix reserved 2x turns and called
# it a worst case, so an arm admitted under `--max-calls 20` could still
# spend 40 when both guards fired. A reservation that is not actually the
# maximum is not a guard at all.
_MAX_CALLS_PER_TURN = 4

def _publish_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """Serialize to a sibling `.partial`, then rename onto `path`.

    `_unique_result_path` claims the name by creating a zero-byte file, so
    writing straight into it means a serialization error, a full disk or a
    kill leaves an empty or truncated .json where a later glob or
    `--compare` will find it and read it as a real result (Codex).
    `os.replace` is atomic within a directory, so the claimed name only
    ever holds a complete document.

    A failed write deliberately leaves the `.partial` file behind rather
    than deleting it: it does not match the `*.json` glob that finds
    results, `--compare` against it fails loudly with a JSON error, and it
    is evidence that a write failed. Deleting agent-created files is also
    hook-blocked in this workspace, and reaching for trash tooling to tidy
    a temp file would be the wrong trade.
    """
    _annotate_budget_report(path, payload)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)

def _unique_result_path(directory: Path, stem: str) -> Path:
    """A path that does not already exist, so no paid result is overwritten.

    A UTC timestamp alone is not enough: it has second granularity, and two
    benches can finish inside the same second (a dry run, a short N, a
    test). Codex's finding allowed either a unique identifier or an outright
    refusal to overwrite; this does both, falling back to a counter suffix
    when the stamped name is taken.
    """
    for n in range(1, 1000):
        candidate = directory / (f"{stem}.json" if n == 1 else f"{stem}-{n}.json")
        if candidate.exists():
            continue
        try:
            # The claim is staked on the SIDECAR, never on the .json itself
            # (Codex): claiming the result name directly left a visible
            # zero-byte .json if publishing then failed, which a later glob
            # or --compare would read as a result. Creating the sidecar with
            # exist_ok=False is still atomic, so two benches finishing in
            # the same UTC second get different names, and the .json only
            # ever appears via the os.replace in _publish_json_atomically -
            # complete, or not at all.
            candidate.with_name(candidate.name + ".partial").touch(exist_ok=False)
        except FileExistsError:
            continue
        return candidate
    raise ConversationLabError(
        f"cannot find an unused result filename for {stem!r} in {directory}"
    )

def _load_bench_baseline(args: argparse.Namespace) -> dict[str, Any]:
    """Read and validate a --compare baseline before any paid work starts.

    Two Codex findings live here. Validating late meant a typo spent the
    whole budget and then raised before the results were written; and
    checking only `command == "bench"` let a Monday baseline be compared
    against a Saturday run, producing movement rows that look valid while
    the cast, turn range, rubric and prior-day context all differ - which
    destroys the one thing a delta is for, attributing movement to the
    setting that changed.
    """
    path = Path(args.compare)
    try:
        baseline = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConversationLabError(f"--compare file not found: {args.compare}") from None
    except json.JSONDecodeError as exc:
        raise ConversationLabError(f"--compare file is not valid JSON: {args.compare} ({exc})") from None
    if not isinstance(baseline, dict) or baseline.get("command") != "bench":
        kind = baseline.get("command", "unknown") if isinstance(baseline, dict) else type(baseline).__name__
        raise ConversationLabError(
            f"--compare expects a bench result JSON; {args.compare!r} is a {kind!r} result"
        )

    _validate_bench_aggregate(baseline, args.compare)
    return baseline

def _validate_bench_aggregate(baseline: dict[str, Any], source: str) -> None:
    """Check the parts `_bench_delta` will actually read.

    Codex: `command == "bench"` alone still let a structurally broken file
    through - an `aggregate` that is a list, or a metric distribution
    missing `mean`/`stderr` - and the resulting TypeError landed in
    `_bench_delta` AFTER every generation and judge call had been paid for
    and BEFORE `_write_json_result` ran, destroying the new result. Every
    field read downstream is checked here, while checking is still free.
    """
    aggregate = baseline.get("aggregate")
    if not isinstance(aggregate, dict):
        raise ConversationLabError(
            f"--compare baseline {source!r} has no usable 'aggregate' object "
            f"(found {type(aggregate).__name__})"
        )
    pass_rate = aggregate.get("pass_rate")
    if pass_rate is not None and (
        not isinstance(pass_rate, (int, float))
        or isinstance(pass_rate, bool)
        or not 0.0 <= pass_rate <= 1.0
    ):
        # Codex: _print_bench_report formats this with :.0%, so a string
        # here crashed AFTER every call was paid for and the report written.
        raise ConversationLabError(
            f"--compare baseline {source!r} has a non-numeric or out-of-range "
            f"'aggregate.pass_rate' ({pass_rate!r})"
        )

    # BOTH sections, because _bench_delta now reads both (Codex). A
    # dimensions block that is a list, or a distribution missing mean /
    # stderr / a numeric n, used to survive preflight and then raise in the
    # delta - after every call was paid for, before the result was written.
    for section in ("metrics", "dimensions"):
        _validate_distribution_section(
            aggregate, section, source, dry_run=bool(baseline.get("dry_run"))
        )

def _validate_distribution_section(
    aggregate: dict[str, Any], section: str, source: str, dry_run: bool = False
) -> None:
    """Check every distribution in one section of a baseline aggregate."""
    distributions = aggregate.get(section)
    if distributions is None and section == "dimensions":
        # Only a genuine dry run may omit them (Codex). Waving through any
        # baseline without dimensions meant _bench_delta produced no
        # dimension rows at all, and with stable deterministic metrics the
        # report could say nothing moved while every judge comparison was
        # simply missing.
        if dry_run:
            return
        raise ConversationLabError(
            f"--compare baseline {source!r} has no 'aggregate.dimensions' block and is "
            f"not a dry run - every judge-dimension comparison would be silently absent"
        )
    if not isinstance(distributions, dict):
        raise ConversationLabError(
            f"--compare baseline {source!r} has no usable 'aggregate.{section}' object "
            f"(found {type(distributions).__name__})"
        )
    if section == "dimensions" and not dry_run:
        # An EMPTY or PARTIAL dimensions block used to pass, because only
        # entries that are present get validated (Codex). _bench_delta
        # walks current dimensions and skips those missing from the baseline,
        # so with stable metrics the report can say
        # nothing moved while every judge comparison is simply absent.
        # Same inversion as the missing-block case, one level down.
        missing = [dim for dim in JUDGE_DIMENSIONS if dim not in distributions]
        if missing:
            raise ConversationLabError(
                f"--compare baseline {source!r} is missing judge dimension(s) "
                f"{', '.join(missing)} and is not a dry run - those comparisons "
                f"would be silently absent"
            )

    for key, dist in distributions.items():
        label = f"{section[:-1]} {key!r}"
        if not isinstance(dist, dict):
            raise ConversationLabError(
                f"--compare baseline {source!r}: {label} is not a distribution object"
            )
        n = dist.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            # _bench_delta evaluates `n >= 2`, which raises TypeError on a
            # string - after the whole bench has been paid for (Codex).
            raise ConversationLabError(
                f"--compare baseline {source!r}: {label} has a non-integer "
                f"sample count 'n' ({n!r})"
            )
        for field in ("mean", "stderr"):
            value = dist.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConversationLabError(
                    f"--compare baseline {source!r}: {label} has a non-numeric "
                    f"{field!r} ({value!r})"
                )
            # json.loads accepts NaN and Infinity, and both pass the check
            # above (Codex). A NaN stderr yields z = NaN, and abs(NaN) >= 2
            # is False - silently turning an uncomputable measurement into
            # "did not move", which is the worst possible way to be wrong.
            if not isfinite(value):
                raise ConversationLabError(
                    f"--compare baseline {source!r}: {label} has a non-finite "
                    f"{field!r} ({value!r})"
                )
        if dist["stderr"] < 0:
            raise ConversationLabError(
                f"--compare baseline {source!r}: {label} has a negative "
                f"'stderr' ({dist['stderr']!r})"
            )
        if section == "dimensions":
            no_samples = dist.get("no_valid_samples")
            if "no_valid_samples" in dist and not isinstance(no_samples, bool):
                raise ConversationLabError(
                    f"--compare baseline {source!r}: {label} has a non-boolean "
                    "'no_valid_samples' flag"
                )
            if n == 0:
                if (
                    no_samples is not True
                    or dist["mean"] != 0
                    or dist["stderr"] != 0
                ):
                    raise ConversationLabError(
                        f"--compare baseline {source!r}: {label} with n=0 must use "
                        "the no_valid_samples=true, zero mean/stderr placeholder"
                    )
            else:
                if no_samples is True:
                    raise ConversationLabError(
                        f"--compare baseline {source!r}: {label} has n={n} but "
                        "contradictory no_valid_samples=true"
                    )
                low, high = _JUDGE_SCORE_RANGE
                if not low <= dist["mean"] <= high:
                    raise ConversationLabError(
                        f"--compare baseline {source!r}: {label} mean must be "
                        f"within the judge score range {low}..{high} "
                        f"(found {dist['mean']!r})"
                    )

# Recorded scenario fields checked before a comparison. They cover the frozen
# judge inputs and stage-specific photo inputs, but do not fingerprint source
# code or other generator inputs; operators must establish those are unchanged
# (see PROTOCOL.md).
_BENCH_SCENARIO_FIELDS = (
    "stage",
    "dry_run",
    "concept",
    "recipe_context",
    "judged_against_prior_days",
    "judge_input_digest",
    "effective_photography_inputs",
    "models",
)

def _judge_input_digest(
    prior_stages: dict[str, Any],
    recipe_facts: str | None,
    expected_cast: list[str],
) -> str:
    """Hash of everything the judge sees that the day names do not capture.

    Codex: `judged_against_prior_days` lists only which days had dialogue,
    so re-running a week's Monday leaves that list identical while
    `_judge_dialogue` receives different text - and `recipe_facts`, which
    drives the technical-credibility scoring, was not represented at all.
    A pass-rate difference could then be attributed to the tested lever
    when the judge's own context had changed underneath it.
    """
    payload = {
        # Rendered exactly as _judge_dialogue renders it (Codex): first
        # token of the name, whitespace collapsed. Hashing the raw values
        # meant collapsing a double space refused a comparison whose judge
        # context was byte-for-byte identical.
        "prior": {
            day: [
                f"{str(m.get('character') or '?').split()[0] if str(m.get('character') or '?').split() else '?'}: "
                + " ".join(str(m.get("message") or "").split())
                for m in (stage.get("dialogue") or [])
            ]
            for day, stage in sorted(prior_stages.items())
        },
        "recipe_facts": recipe_facts or "",
        # NOT sorted (Codex): run_simulation passes participants_for_day()'s
        # ORDERED list into _select_next_speaker, whose weighted selection
        # iterates it, and _judge_dialogue renders the roster in that same
        # order. Sorting here hid a difference that really does change both
        # the generated conversation and the judge's input.
        "expected_cast": list(expected_cast),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

def _assert_writable(directory: Path) -> None:
    """Prove a file can actually be created here, before spending anything.

    Codex: `mkdir(exist_ok=True)` succeeds on a directory that already
    exists but is not writable, so every generation and judge call would
    run and only the sidecar claim would fail - with no report written and
    the whole paid bench lost.

    `tempfile.TemporaryFile` is deliberate: it proves write capability and
    the OS reclaims the file on close, so nothing is left behind and
    nothing has to be deleted.
    """
    try:
        with tempfile.TemporaryFile(dir=directory):
            pass
    except OSError as exc:
        raise ConversationLabError(
            f"results directory {directory} is not writable: {exc}"
        ) from exc

def _assert_comparable_scenario(baseline: dict[str, Any], report: dict[str, Any]) -> None:
    """Refuse a baseline whose scenario differs from this bench's."""
    mismatches = []
    for field in _BENCH_SCENARIO_FIELDS:
        if field == "effective_photography_inputs":
            theirs = _effective_bench_photography_inputs(
                baseline.get("stage"), baseline.get("concept"),
                baseline.get("photography_context"),
            )
            ours = _effective_bench_photography_inputs(
                report.get("stage"), report.get("concept"),
                report.get("photography_context"),
            )
        else:
            theirs, ours = baseline.get(field), report.get(field)
        if theirs != ours:
            label = (
                "photography_context effective prompts/tick floor"
                if field == "effective_photography_inputs" else field
            )
            mismatches.append(f"{label}: {theirs!r} vs {ours!r}")
    if mismatches:
        raise ConversationLabError(
            "--compare baseline is not comparable to this bench:\n  "
            + "\n  ".join(mismatches)
            + "\nA delta is only meaningful between runs that differ in the one "
            "setting under test. Pass --allow-mismatched-baseline only when "
            "the mismatch is deliberate and documented."
        )

def cmd_bench(args: argparse.Namespace) -> None:
    _validate_bench_args(args)
    mode, default_model, judge_model = _resolve_models_for_args(args)
    concept, recipe_context, recipe_facts, prior_stages, photo_inputs = _resolve_bench_scenario(args)
    expected_cast = simulate_module.participants_for_day(args.stage)

    # Load and validate --compare FIRST (Codex P2). A typo'd path, missing
    # file, malformed JSON or wrong result type used to surface only after
    # every generation and judge call had already been paid for, and then
    # raised before _write_json_result - losing the whole run. Validation is
    # free; do it before anything costs money.
    baseline_report = _load_bench_baseline(args) if args.compare else None

    # Everything that makes two benches comparable is known before the first
    # call, so the scenario check belongs here too - not after the money is
    # spent (Codex).
    scenario: dict[str, Any] = {
        "stage": args.stage,
        "dry_run": bool(args.dry_run),
        "concept": concept,
        "recipe_context": recipe_context,
        "judged_against_prior_days": sorted(prior_stages.keys()),
        "judge_input_digest": _judge_input_digest(prior_stages, recipe_facts, expected_cast),
        "photography_context": photo_inputs["photography_context"],
        "image_paths": photo_inputs["image_paths"],
        "photography_input_sources": photo_inputs["sources"],
        "effective_photography_inputs": _effective_bench_photography_inputs(
            args.stage, concept, photo_inputs["photography_context"]
        ),
        "models": {"mode": mode, "dialogue": default_model, "judge": judge_model},
    }
    if baseline_report is not None and not args.allow_mismatched_baseline:
        _assert_comparable_scenario(baseline_report, scenario)

    # Worst-case reservation, not last-observed (Codex P1). run_simulation
    # makes one paid call PER TURN plus possible rewrite retries, so
    # reserving the previous arm's count let `--max-calls 1` sail through
    # the check and then spend a whole day's turns. The cap is a runaway
    # guard and must be a real upper bound, so reserve what an arm can cost
    # at worst and refuse to start one that would not fit.
    # A --dry-run makes zero API calls (mode="template", no judge), so it
    # must neither reserve nor record any (Codex): a one-run plumbing check
    # was reporting `calls_used: 10`, and an explicit low --max-calls could
    # refuse to start a run that cannot spend anything.
    # Before any paid call (Codex): a permission or path error here after a
    # 30-run bench would lose every transcript, since the report can only be
    # written into a directory that exists.
    results_dir = _results_dir(args)
    results_dir.mkdir(parents=True, exist_ok=True)
    _assert_writable(results_dir)
    if not args.no_log and not args.dry_run:
        # Preflighted alongside results_dir (Codex): a custom
        # --experiments-log with a non-creatable parent, or a read-only
        # destination, previously raised inside _append_bench_log - after
        # every call was paid for and the result published - so the
        # required audit row was simply never written.
        log_path = _experiments_log_path(args)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConversationLabError(
                f"experiments log directory {log_path.parent} is not usable: {exc}"
            ) from exc
        _assert_writable(log_path.parent)
        if log_path.exists() and not log_path.is_file():
            # An existing DIRECTORY at that path has a perfectly writable
            # parent, so the check above passes, every call gets paid for,
            # and _append_bench_row_locked then dies on read_text() with
            # IsADirectoryError - published result, no audit row (Codex).
            raise ConversationLabError(
                f"--experiments-log {log_path} exists and is not a regular file"
            )

    gen_reserve = 0 if args.dry_run else _MAX_CALLS_PER_TURN * _max_turns_for_stage(args.stage)
    judge_reserve = 0 if args.dry_run else 2  # the judge retries once on an unparseable verdict

    max_calls = args.max_calls
    if max_calls is None:
        max_calls = args.runs * (gen_reserve + judge_reserve)
    budget = CallBudget(max_calls=max_calls)

    runs: list[dict[str, Any]] = []
    # Turns from a run the mid-arm guard cut short: paid for, so saved, but
    # never summarized, judged, aggregated or counted as completed (#7714).
    aborted_runs: list[dict[str, Any]] = []
    aborted = False
    error: str | None = None
    interrupted = False

    # #7714 round 2, finding 1: this loop had NO mid-arm guard at all -
    # every arm/judge call here is now covered by construction, same as
    # every other arm-running loop in this module.
    guard_ctx = _installed_budget_guard(budget, args.max_cost)
    guard_ctx.__enter__()
    # #7714 round 3: a fresh list per run_index, passed to _run_arm as
    # message_sink - run_simulation appends into it as turns are
    # generated, so whatever it holds is recoverable even if _run_arm
    # raises before returning (most commonly a mid-arm LabBudgetAbort).
    # `generation_recorded` distinguishes "the exception happened before
    # this run's transcript ever reached `runs`" (sink holds a real
    # partial transcript worth saving) from "generation finished and the
    # exception came from summarize()/judging instead" (the run is
    # already in `runs`; sink is stale and must not be saved again).
    sink: list = []
    generation_recorded = True
    try:
        for run_index in range(1, args.runs + 1):
            if budget.would_exceed(gen_reserve) or _would_exceed_cost(args.max_cost):
                aborted = True
                break
            sink = []
            generation_recorded = False
            result = _spend(
                budget,
                lambda: _run_arm(
                    concept,
                    args.stage,
                    run_index,
                    recipe_context,
                    mode,
                    default_model,
                    photography_context=photo_inputs["photography_context"],
                    image_paths=photo_inputs["image_paths"],
                    message_sink=sink,
                    recipe_facts=recipe_facts,
                ),
                fallback=0 if args.dry_run else _max_turns_for_stage(args.stage),
                reservation=gen_reserve,
            )
            messages = result.get("messages", [])

            # Appended before the summary is computed and before judging
            # (Codex): this transcript is already paid for, so neither a
            # summarize() failure nor a judge failure may discard it. Both
            # `summary` and `judge` stay absent on such a record, which
            # _bench_aggregate already tolerates as unsummarized/unjudged.
            record: dict[str, Any] = {
                "run_index": run_index,
                "message_count": len(messages),
                "transcript": messages,
            }
            runs.append(record)
            generation_recorded = True
            # The transcript is durable partial evidence before we consult
            # the ledger again. A denied or unreadable ledger must stop here,
            # before summary/judge work or another paid generation request.
            _budget_checkpoint()
            record["summary"] = summarize(messages, expected_cast, concept=concept, day=args.stage)

            # --dry-run renders the call plan with mode="template" and never
            # judges: there is nothing to judge that a model wrote.
            if not args.dry_run:
                if budget.would_exceed(judge_reserve) or _would_exceed_cost(args.max_cost):
                    aborted = True
                    break
                record["judge"] = _spend(
                    budget,
                    fallback=0 if args.dry_run else 1,
                    reservation=judge_reserve,
                    fn=lambda: _judge_one_transcript(
                        concept=concept,
                        stage=args.stage,
                        messages=messages,
                        prior_stages=prior_stages,
                        recipe_context=recipe_context,
                        recipe_facts=recipe_facts,
                        judge_model=judge_model,
                    ),
                )
    except (Exception, KeyboardInterrupt, LabBudgetAbort) as exc:  # noqa: BLE001
        # Same contract as _generate_and_judge_pairs: runs that already
        # finished are paid for and must reach disk, so record the failure
        # and fall through to writing the report instead of propagating.
        #
        # KeyboardInterrupt is included deliberately (Codex): it inherits
        # from BaseException, so Ctrl-C on a long bench skipped this handler
        # entirely and threw away every completed paid run along with the
        # spend accounting _spend's finally had just recorded. Interrupting
        # a run you are watching go wrong is a NORMAL thing to do, and it
        # must not be the one path that loses the evidence.
        #
        # LabBudgetAbort is included deliberately too (#7714 round 2): it
        # derives from BaseException, same family as KeyboardInterrupt, for
        # the same reason - a plain `except Exception` must never be able
        # to swallow it on its way up from a deeply nested paid call.
        #
        # It is NOT treated as an `error` (Codex round 2): hitting the cap
        # via the coarse would_exceed()/_would_exceed_cost() pre-check
        # never sets `error` or raises SystemExit below - it is the guard
        # doing its job, and the results up to that point are valid. The
        # mid-arm guard hitting mid-flight is the SAME event, just caught
        # later; it must read the same way, not like a bench that crashed.
        if isinstance(exc, LabBudgetAbort):
            aborted = True
        else:
            error = f"{type(exc).__name__}: {exc}"
        interrupted = isinstance(exc, KeyboardInterrupt)
        if _budget_guard_stopped():
            aborted = True
        # #7714 round 3 / round 4 finding 1: `sink` holds whatever turns
        # run_simulation had already generated - real, paid work - before
        # ANY exception (a LabBudgetAbort, or any other mid-arm error),
        # unless the CURRENT run's transcript already made it into `runs`
        # (generation_recorded=True means the failure was in summarize()/
        # judging instead, and `sink` is stale from a completed run - do
        # not save it again).
        if not generation_recorded and sink:
            partial_messages = [m.__dict__ for m in sink]
            aborted_runs.append({
                "run_index": len(runs) + 1,
                "message_count": len(partial_messages),
                "transcript": partial_messages,
                "status": "aborted_mid_arm" if isinstance(exc, LabBudgetAbort) else "error_mid_arm",
            })
    finally:
        guard_ctx.__exit__(None, None, None)

    aggregate = _bench_aggregate(runs)
    if args.dry_run:
        aggregate["judge_coverage"] = {
            "attempted_runs": 0,
            "scored_verdicts": 0,
            "unjudged_runs": 0,
            "rate": None,
            "applicable": False,
        }
        aggregate["unjudged_runs"] = 0
    corpus = conversation_metrics.recurring_phrases_across(
        [run["transcript"] for run in runs], n=4, min_transcripts=2
    )

    label = args.label or f"{args.stage}-n{args.runs}"
    # Timestamped (Codex P1). The filename used to be deterministic from the
    # label, so running the documented baseline -> change -> bench-again
    # cycle twice at the same stage and N silently overwrote the first paid
    # result; both Benchmarks rows then pointed at the same file, and
    # --compare against that path read the current report as its own
    # baseline. Every bench now writes its own file.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report: dict[str, Any] = {
        "command": "bench",
        "label": label,
        **scenario,
        "requested_runs": args.runs,
        "completed_runs": len(runs),
        "expected_cast": expected_cast,
        "max_calls": max_calls,
        "calls_used": budget.used,
        "max_cost": args.max_cost,
        "aborted": aborted,
        "error": error,
        "aggregate": aggregate,
        "recurring_phrases_across_runs": corpus,
        "runs": runs,
        "aborted_runs": aborted_runs,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # PROTOCOL.md says every experiment logs calls AND cost, and `ab`
        # already snapshots this. The cost log is process-local, so without
        # it the result retains no dollar figure once the command exits and
        # nobody can audit how close a run came to --max-cost (Codex).
        "cost_summary": _cost_summary_or_none(),
        **_openrouter_fields_for(args, _openrouter_router_cost_by_model()),
    }

    comparison = None
    if baseline_report is not None:
        baseline_judge = _baseline_judge_summary(baseline_report)
        comparison = {
            "baseline_file": str(args.compare),
            "baseline_label": baseline_report.get("label"),
            "baseline_pass_rate": baseline_judge["pass_rate"],
            "baseline_pass_count": baseline_judge["pass_count"],
            "baseline_judge_coverage": baseline_judge["judge_coverage"],
            "baseline_pass_rate_unavailable_reason": baseline_judge["unavailable_reason"],
            "current_judge_coverage": aggregate["judge_coverage"],
            "metrics": _bench_delta(aggregate, baseline_report.get("aggregate") or {}),
            "dimensions": _bench_delta(
                aggregate, baseline_report.get("aggregate") or {}, section="dimensions"
            ),
        }
        report["comparison"] = comparison

    # Claim the filename LAST. _unique_result_path creates the file to win
    # the race, so anything that could raise between the claim and the write
    # would strand an empty .json in the results dir for a later glob or
    # --compare to trip over. Nothing sits between these two lines.
    result_path = _unique_result_path(results_dir, f"bench-{_slugify(label)}-{stamp}")
    report["results_file"] = str(result_path)
    _publish_json_atomically(result_path, report)
    if not args.no_log and not args.dry_run:
        _append_bench_log(_experiments_log_path(args), report)
    _print_bench_report(report)

    # Truthful exit code. The partial report above is deliberately written and
    # printed first - those runs are paid work - but a bench that died partway
    # through must not look like a clean one to a caller or a shell script.
    # `aborted` is NOT an error: hitting --max-calls/--max-cost is the guard
    # doing its job, and the results up to that point are valid.
    if interrupted:
        # Re-raised, not converted: Ctrl-C should still read as Ctrl-C to
        # whatever is running this. The partial report is already on disk.
        print(
            f"\ninterrupted after {len(runs)} of {args.runs} run(s) - "
            f"partial result: {result_path}",
            file=sys.stderr,
        )
        raise KeyboardInterrupt
    if error:
        raise SystemExit(
            f"conversation_lab bench: stopped after {len(runs)} of "
            f"{args.runs} run(s): {error} (partial result: {result_path})"
        )

_BENCH_SECTION_HEADING = "## Benchmarks"
_BENCH_TABLE_HEADER_LINE = (
    "| Date | Label | Stage | N | Pass rate (scored/ran) | Most frequent weakest | Result file |\n"
)
_BENCH_TABLE_SEPARATOR_LINE = "|------|-------|-------|---|----------------------|-----------------------|-------------|\n"

def _insert_in_section(text: str, heading: str, row: str) -> str:
    """Append `row` to the last table row under `heading`.

    Falls back to an end-of-file append when the heading is absent, which
    is the caller's own just-created-the-section path.
    """
    lines = text.rstrip("\n").split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading.strip())
    except StopIteration:
        return text.rstrip("\n") + "\n" + row

    # The section ends at the next heading of the same or higher level.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        stripped = lines[i].lstrip()
        if stripped.startswith("#") and not stripped.startswith("###"):
            end = i
            break

    # Insert after the last table row in the section, so the row joins the
    # table rather than trailing any prose beneath it.
    insert_at = end
    for i in range(end - 1, start, -1):
        if lines[i].lstrip().startswith("|"):
            insert_at = i + 1
            break

    lines.insert(insert_at, row.rstrip("\n"))
    return "\n".join(lines) + "\n"

def _md_cell(value: Any) -> str:
    """One Markdown table cell, safe against delimiters in the value.

    A pipe or newline in --label, or in the judge's unvalidated `weakest`
    text, split the audit row into extra columns or rows - leaving a paid
    run's required log entry malformed even though its result published
    fine (Codex).
    """
    text = " ".join(str(value).split())
    return text.replace("\\", "\\\\").replace("|", "\\|") or "-"

def _append_bench_log(path: Path, report: dict[str, Any]) -> None:
    """Append one row to EXPERIMENTS.md's Benchmarks table.

    A bench is a paid run, and PROTOCOL.md's cost budget requires every
    paid run to leave a logged, reviewable trace. It gets its OWN table
    rather than a row in the Experiments table above it: that table's
    columns are Wins/Ties/Losses on a target dimension, which a
    single-arm run has none of, and forcing one in would make the A/B
    audit trail unreadable.
    """
    aggregate = report.get("aggregate") or {}
    weakest = aggregate.get("weakest_counts") or {}
    top_weakest = next(iter(weakest), "-")
    pass_rate = aggregate.get("pass_rate")
    scored = aggregate.get("scored_verdicts", aggregate.get("judged_runs", 0))
    ran = report.get("completed_runs", 0)
    pass_cell = (
        f"n/a ({scored}/{ran} scored)"
        if pass_rate is None
        else f"{pass_rate:.0%} ({scored}/{ran})"
    )
    row = (
        f"| {datetime.now(timezone.utc).date().isoformat()} "
        f"| {_md_cell(report['label'])} "
        f"| {_md_cell(report['stage'])} "
        f"| {report['completed_runs']} "
        f"| {_md_cell(pass_cell)} "
        f"| {_md_cell(top_weakest)} "
        f"| {_md_cell(Path(report['results_file']).name)} |\n"
    )

    # Locked (Codex): the append is a read-modify-write, so two benches
    # finishing together could each read the same snapshot and the second
    # writer would silently drop the first's row - leaving a paid result
    # file with no required log trace.
    with _file_lock(path):
        _append_bench_row_locked(path, report, row)

@contextmanager
def _file_lock(path: Path):
    """Exclusive lock for `path`, held on a file OUTSIDE the worktree.

    Codex: a sibling `EXPERIMENTS.md.lock` is untracked, un-ignored
    repository noise that every operator run would leave behind - and the
    locked hygiene contract's session-end gate refuses to close on a dirty
    tree, so the lab would have blocked the end of every session that used
    it. The lock lives in the system temp dir instead, keyed by a hash of
    the absolute log path so two processes locking the same log still
    collide and two different logs do not.
    """
    key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"conversation-lab-{key}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

def _append_bench_row_locked(path: Path, report: dict[str, Any], row: str) -> None:
    existing = path.read_text() if path.exists() else _EXPERIMENTS_HEADER
    if _BENCH_SECTION_HEADING not in existing:
        existing = existing.rstrip("\n") + (
            f"\n\n{_BENCH_SECTION_HEADING}\n\n"
            "Single-arm characterization runs (`conversation_lab.py bench`). "
            "A row here is a baseline another run gets compared against, not a decision.\n\n"
            + _BENCH_TABLE_HEADER_LINE
            + _BENCH_TABLE_SEPARATOR_LINE
        )
    # Inserted at the END OF THE BENCHMARKS TABLE, not the end of the file
    # (Codex). An unconditional append put the row under whatever section
    # happened to come last - so once any narrative section follows the
    # table, a paid run's audit row lands outside the table it belongs to
    # and stops being a valid entry. The Experiments table already does
    # section-aware insertion for exactly this reason.
    updated = _insert_in_section(existing, _BENCH_SECTION_HEADING, row)

    # Atomic (Codex): a full-file write_text interrupted partway - a disk
    # filling up while a paid bench appends its row - would truncate
    # EXPERIMENTS.md and destroy every prior audit row. The lock stops
    # concurrent writers; it does not make a partial write safe.
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(updated)
    os.replace(tmp, path)

def _print_bench_report(report: dict[str, Any]) -> None:
    agg = report["aggregate"]
    print(f"\n=== conversation_lab bench: {report['label']} ===")
    print(f"stage: {report['stage']}   concept: {report['concept']}")
    print(f"runs: {report['completed_runs']}/{report['requested_runs']}   calls: {report['calls_used']}/{report['max_calls']}")
    print(f"models: dialogue={report['models']['dialogue']}  judge={report['models']['judge']}")
    prior = report["judged_against_prior_days"]
    print(f"judged against prior days: {', '.join(prior) if prior else '(none - isolated stage)'}")
    if report["dry_run"]:
        print("DRY RUN - template dialogue, no judge, numbers are plumbing only")
    if report["aborted"]:
        print("ABORTED - hit --max-calls or --max-cost; results below are partial")
    if report["error"]:
        print(f"ERROR after {report['completed_runs']} run(s): {report['error']}")

    coverage = agg.get("judge_coverage") or {}
    if coverage.get("applicable") is not False and coverage.get("attempted_runs", 0):
        print(
            f"\njudge coverage: {coverage.get('scored_verdicts', 0)}/"
            f"{coverage['attempted_runs']} scored; "
            f"{coverage.get('unjudged_runs', 0)} unjudged"
        )
    if coverage.get("attempted_runs", 0) and not report["dry_run"]:
        if agg["pass_rate"] is None:
            print("judge pass rate: unavailable (no complete usable scorecards)")
        else:
            print(
                f"judge: {agg['pass_count']}/{agg['judged_runs']} PASS "
                f"({agg['pass_rate']:.0%})"
            )
        print(f"{'dimension':<24}{'mean':>8}{'sd':>7}{'min':>6}{'max':>6}")
        for dim, dist in agg["dimensions"].items():
            if dist.get("no_valid_samples"):
                # The comparison table already honoured this; the primary
                # table did not, so a standalone bench printed mean 0.00 for
                # a dimension scored 1-5 - an out-of-range number the judge
                # never produced (Codex).
                print(f"{dim:<24}{'NOT SCORED':>27}")
                continue
            print(f"{dim:<24}{dist['mean']:>8.2f}{dist['stdev']:>7.2f}{dist['min']:>6.0f}{dist['max']:>6.0f}")
        if agg["weakest_counts"]:
            counts = ", ".join(f"{k} x{v}" for k, v in agg["weakest_counts"].items())
            print(f"weakest most often: {counts}")

    print(f"\n{'metric':<34}{'mean':>9}{'sd':>8}{'stderr':>9}{'min':>8}{'max':>8}")
    for key, dist in agg["metrics"].items():
        print(
            f"{key:<34}{dist['mean']:>9.3f}{dist['stdev']:>8.3f}"
            f"{dist['stderr']:>9.3f}{dist['min']:>8.3f}{dist['max']:>8.3f}"
        )

    phrases = (report.get("recurring_phrases_across_runs") or {}).get("per_character_catchphrases") or {}
    if phrases:
        print("\nphrases a character reused across runs (4-grams, >= 2 runs):")
        for char, hits in phrases.items():
            top = ", ".join(f"{p!r} x{c}" for p, c in hits[:3])
            print(f"  {char:<12}{top}")

    comparison = report.get("comparison")
    if comparison:
        print(f"\n=== vs {comparison['baseline_label']} ({comparison['baseline_file']}) ===")
        base_rate = comparison["baseline_pass_rate"]
        baseline_rate_text = _pass_rate_label(
            base_rate,
            comparison.get("baseline_judge_coverage"),
            comparison.get("baseline_pass_rate_unavailable_reason"),
        )
        current_rate_text = _pass_rate_label(
            agg.get("pass_rate"), comparison.get("current_judge_coverage")
        )
        print(f"pass rate: {baseline_rate_text} -> {current_rate_text}")
        dim_rows = comparison.get("dimensions") or {}
        if dim_rows:
            print(f"\n{'judge dimension':<34}{'baseline':>10}{'now':>10}{'delta':>10}{'z':>8}")
            for key, row in sorted(dim_rows.items(), key=_comparison_row_sort_key):
                if row.get("unavailable"):
                    missing = []
                    if not row.get("baseline_available"):
                        missing.append("baseline")
                    if not row.get("current_available"):
                        missing.append("now")
                    reason = "NOT SCORED" if row.get("no_valid_samples") else "UNAVAILABLE"
                    print(f"{key:<34}{reason + ' (' + ', '.join(missing) + ')':>44}")
                    continue
                z_text = "   n/a" if row["z"] is None else f"{row['z']:>+6.2f}"
                if row["status"] == "indeterminate":
                    flag = "  <- indeterminate (n < 2)"
                elif row["moved"]:
                    flag = "  <- moved"
                else:
                    flag = ""
                print(
                    f"{key:<34}{row['baseline_mean']:>10.2f}{row['mean']:>10.2f}"
                    f"{row['delta']:>+10.2f}  {z_text}{flag}"
                )

        metric_rows = comparison.get("metrics") or {}
        if metric_rows:
            print(f"{'metric':<34}{'baseline':>10}{'now':>10}{'delta':>10}{'z':>8}")
            for key, row in sorted(metric_rows.items(), key=_comparison_row_sort_key):
                if row.get("unavailable"):
                    missing = []
                    if not row.get("baseline_available"):
                        missing.append("baseline")
                    if not row.get("current_available"):
                        missing.append("now")
                    print(f"{key:<34}{('UNAVAILABLE (' + ', '.join(missing) + ')'):>44}")
                    continue
                z_text = "   n/a" if row["z"] is None else f"{row['z']:>+6.2f}"
                flag = "  <- indeterminate (n < 2)" if row["status"] == "indeterminate" else (
                    "  <- moved" if row["moved"] else ""
                )
                print(
                    f"{key:<34}{row['baseline_mean']:>10.3f}{row['mean']:>10.3f}"
                    f"{row['delta']:>+10.3f}  {z_text}{flag}"
                )
        else:
            print("metric comparison unavailable: neither bench recorded any metric keys.")

        print(_comparison_summary("metric", metric_rows))
        if dim_rows:
            print(_comparison_summary("judge dimension", dim_rows))
            indeterminate_dims = sum(
                row["status"] == "indeterminate" for row in dim_rows.values()
            )
            if indeterminate_dims:
                print(
                    f"{indeterminate_dims} judge dimension(s) were "
                    "INDETERMINATE, not unchanged."
                )
        metric_statuses = {row["status"] for row in metric_rows.values()}
        if metric_rows and metric_statuses == {"unchanged"}:
            moved_dims = sum(row["status"] == "moved" for row in dim_rows.values())
            uncertain_dims = sum(
                row["status"] in {"indeterminate", "unavailable"}
                for row in dim_rows.values()
            )
            if moved_dims or uncertain_dims:
                note = "no DETERMINISTIC metric moved"
                if moved_dims:
                    note += f"; {moved_dims} judge dimension(s) did"
                elif uncertain_dims:
                    note += (
                        f"; {uncertain_dims} judge dimension(s) were "
                        "INDETERMINATE, not unchanged"
                    )
                print(f"({note}; unavailable rows remain listed above)")

    _print_openrouter_costs(report)
    print(f"\nresults written to: {report['results_file']}")

def cmd_calibrate(args: argparse.Namespace) -> None:
    if args.reference_panel:
        if args.from_episode or args.stage:
            raise SystemExit("conversation_lab calibrate: --reference-panel cannot be combined with --from-episode/--stage")
        if args.local:
            raise SystemExit("conversation_lab calibrate: --local is only valid with --from-episode")
        _cmd_calibrate_reference_panel(args)
        return
    if not args.from_episode or not args.stage:
        raise SystemExit("conversation_lab calibrate: pass --reference-panel PATH or both --from-episode ID and --stage DAY")
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
    recipe_data = _stage_recipe_data(episode, stage_data)
    recipe_context = _build_recipe_context(recipe_data)
    recipe_facts = _build_judge_recipe_facts(recipe_data) or None

    judge_model = _resolve_judge_model_for_args(args)

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
    #
    # #7714 round 2: covered by the mid-arm guard too, for the same
    # by-construction reason as every other command - every judge call
    # here is already individually pre-checked, so this is defense in
    # depth rather than closing a real gap, but it keeps calibrate
    # consistent with ab/bench instead of being the one command a future
    # reviewer has to reason about separately.
    guard_ctx = _installed_budget_guard(budget, args.max_cost)
    guard_ctx.__enter__()
    try:
        for name, degrade in _DEGRADATIONS:
            pair_records: list[dict[str, Any]] = []
            partial_pair_records: list[dict[str, Any]] = []
            wins = 0
            attempted = 0
            try:
                for run_index in range(1, args.runs + 1):
                    if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                        aborted = True
                        break
                    degraded = degrade(dialogue, run_index)
                    attempted += 1
                    pending_pair: dict[str, Any] = {
                        "run_index": run_index,
                        "status": "awaiting_judges",
                        "real_dialogue": dialogue,
                        "degraded_dialogue": degraded,
                        "judge_orientations": [],
                    }
                    partial_pair_records.append(pending_pair)

                    if args.dry_run:
                        combined = _dry_run_combined()
                    else:
                        if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                            aborted = True
                            break
                        first = _run_judge_orientation(
                            orientation="real_first", pending_pair=pending_pair, budget=budget,
                            judge_model=judge_model, concept=concept, stage=args.stage,
                            recipe_context=recipe_context, expected_cast=expected_cast,
                            first_arm="real", first_messages=dialogue,
                            second_arm="degraded", second_messages=degraded,
                            recipe_facts=recipe_facts, max_cost=args.max_cost,
                        )

                        if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                            aborted = True
                            break
                        second = _run_judge_orientation(
                            orientation="degraded_first", pending_pair=pending_pair, budget=budget,
                            judge_model=judge_model, concept=concept, stage=args.stage,
                            recipe_context=recipe_context, expected_cast=expected_cast,
                            first_arm="degraded", first_messages=degraded,
                            second_arm="real", second_messages=dialogue,
                            recipe_facts=recipe_facts, max_cost=args.max_cost,
                        )
                        combined = _combine_orientations(first, second)

                    if combined["overall"] == "real":
                        wins += 1
                    pair_records.append({
                        "run_index": run_index,
                        "judge": combined,
                        "judge_diagnostics": _orientation_diagnostics(first, second) if not args.dry_run else None,
                        "judge_orientations": pending_pair["judge_orientations"],
                    })
                    partial_pair_records.remove(pending_pair)
            finally:
                scored_pairs = 0 if args.dry_run else len(pair_records)
                preference_rate = (
                    round(wins / scored_pairs, 4) if scored_pairs else None
                )
                if args.dry_run:
                    verdict = "DRY RUN - no signal"
                elif not scored_pairs or len(pair_records) < args.runs:
                    verdict = "INCOMPLETE - grader readiness unavailable"
                else:
                    verdict = "GRADER OK" if preference_rate >= 0.8 else "GRADER SUSPECT"
                degradation_reports[name] = {
                    "attempted": attempted,
                    "requested_runs": args.runs,
                    "completed_pairs": len(pair_records),
                    "scored_pairs": scored_pairs,
                    "real_preference_rate": preference_rate,
                    "verdict": verdict,
                    "pairs": pair_records,
                    "partial_pairs": partial_pair_records,
                }
            if aborted:
                break
    except LabBudgetAbort:
        # #7714 round 2: hitting the cap via the mid-arm guard is the SAME
        # event the coarse would_exceed()/_would_exceed_cost() checks above
        # already handle inline (aborted=True; break, no exception, no
        # `error`) - it must read the same way, not like calibrate crashed.
        # A bare `except BaseException: ...; raise` here would re-raise it
        # all the way past `main()` (which only catches ConversationLabError/
        # BudgetGuardError), printing a raw traceback instead of a clean
        # aborted/partial result.
        aborted = True
        report = _build_calibrate_report(args, concept, degradation_reports, True, budget, result_path)
        _write_json_result(result_path, report)
        _print_calibrate_report(report)
        return
    except BaseException as exc:
        report = _build_calibrate_report(
            args, concept, degradation_reports, True, budget, result_path,
            error=f"{type(exc).__name__}: {exc}",
        )
        _write_json_result(result_path, report)
        raise
    finally:
        guard_ctx.__exit__(None, None, None)

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
        rate = info["real_preference_rate"]
        rate_text = "unavailable" if rate is None else f"{rate:.2%}"
        print(
            f"  {name:<18} attempted={info['attempted']:<4} "
            f"completed={info.get('completed_pairs', 0)}/{info.get('requested_runs', info['attempted'])} "
            f"scored={info.get('scored_pairs', info.get('completed_pairs', 0))} "
            f"real_preference_rate={rate_text}  {info['verdict']}"
        )
    _print_openrouter_costs(report)
    print(f"\nresults written to: {report['results_file']}")

# ---------------------------------------------------------------------------
# rejudge (#7791 P2: RESEARCH_PLAN.md S0a V3 test-retest)
# ---------------------------------------------------------------------------

_REJUDGE_ORIENTATION_KEYS = ("orientation", "status", "evidence", "result")


def _load_rejudge_source(result_path: Path) -> dict[str, Any]:
    """Load and validate an `ab` result file for `rejudge`. Raises SystemExit
    (never a silent skip) on anything that means the file cannot be safely
    re-judged - an aborted run, a partial arm, a missing transcript, or a
    missing saved prompt/mapping (dropping into a code path that would have
    to reconstruct one instead)."""
    try:
        raw_text = result_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"conversation_lab rejudge: result file not found: {result_path}") from exc
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"conversation_lab rejudge: {result_path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"conversation_lab rejudge: {result_path} is not a JSON object")
    if data.get("command") != "ab":
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} is a {data.get('command')!r} result, not an "
            "`ab` result - rejudge only re-scores an ab result's saved transcripts"
        )
    if data.get("mode") == "sweep":
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} is an `ab --sweep` result (pairs nested per "
            "variant) - rejudge does not support --sweep results; pass a single-variant or --testbed result"
        )
    if data.get("dry_run"):
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} is a --dry-run result - it has no real judge "
            "calls to redo"
        )
    if data.get("aborted"):
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} is an ABORTED ab result (--max-calls/--max-cost "
            "hit mid-run) - refusing to re-judge a partial run"
        )
    partial_pairs = data.get("partial_pairs") or []
    if partial_pairs:
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} has {len(partial_pairs)} partial pair(s) (a "
            "control/variant arm that never finished) - refusing to re-judge a partial run"
        )
    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise SystemExit(f"conversation_lab rejudge: {result_path} has no pairs to re-judge")

    # The judge prompt is the instrument (RESEARCH_PLAN.md section 3): a
    # re-judge under a different system prompt is a new instrument, not a
    # test-retest, so refuse rather than report it as V3 agreement.
    current_sha = _pairwise_evaluator_metadata()["evaluator_prompt_sha256"]
    source_sha = data.get("evaluator_prompt_sha256")
    if source_sha is not None and source_sha != current_sha:
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} was judged under evaluator_prompt_sha256 "
            f"{source_sha}, but the current judge prompt is {current_sha} - a re-judge would "
            "change the instrument, not retest it"
        )

    for i, pair in enumerate(pairs, start=1):
        if not pair.get("control_messages") or not pair.get("variant_messages"):
            raise SystemExit(
                f"conversation_lab rejudge: {result_path} pair {i} is missing a transcript "
                "(control_messages/variant_messages) - refusing to re-judge a partial run"
            )
        orientations = pair.get("judge_orientations")
        if not isinstance(orientations, list) or len(orientations) != 2:
            found = len(orientations) if isinstance(orientations, list) else 0
            raise SystemExit(
                f"conversation_lab rejudge: {result_path} pair {i} does not have exactly 2 recorded "
                f"judge orientations (found {found}) - refusing to re-judge a partial run"
            )
        for orientation in orientations:
            if not isinstance(orientation, dict) or orientation.get("status") != "invoked" or "result" not in orientation:
                raise SystemExit(
                    f"conversation_lab rejudge: {result_path} pair {i} has an orientation that never "
                    "completed - refusing to re-judge a partial run"
                )
            evidence = orientation.get("evidence") or {}
            if not evidence.get("prompt") or not isinstance(evidence.get("mapping"), dict):
                raise SystemExit(
                    f"conversation_lab rejudge: {result_path} pair {i} orientation is missing its "
                    "saved prompt/mapping - cannot re-judge without regenerating"
                )
            saved_system = evidence.get("system_prompt")
            if saved_system is None and source_sha is None:
                raise SystemExit(
                    f"conversation_lab rejudge: {result_path} pair {i} records neither its judge "
                    "system prompt nor an evaluator_prompt_sha256 - cannot show the instrument is unchanged"
                )
            if saved_system is not None and saved_system != PAIRWISE_JUDGE_SYSTEM_PROMPT:
                raise SystemExit(
                    f"conversation_lab rejudge: {result_path} pair {i} was judged under a different "
                    "system prompt than the current one - a re-judge would change the instrument"
                )
            first_arm = evidence.get("first_arm")
            second_arm = evidence.get("second_arm")
            mapping = evidence["mapping"]
            if (
                {first_arm, second_arm} != {"control", "variant"}
                or mapping.get("A") != first_arm
                or mapping.get("B") != second_arm
            ):
                raise SystemExit(
                    f"conversation_lab rejudge: {result_path} pair {i} orientation has an incomplete "
                    "or inconsistent first_arm/second_arm/mapping record - refusing to guess the A/B order"
                )
        first_arms = [o["evidence"]["first_arm"] for o in orientations]
        if first_arms[0] == first_arms[1]:
            raise SystemExit(
                f"conversation_lab rejudge: {result_path} pair {i} has two orientations with the same "
                "A/B order - not a position-swapped pair"
            )
    return data


def _rejudge_source_instrument(
    result_path: Path, pairs: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    """(judge model, saved judge instrument) for a source result. Every
    orientation must name the same judge model; the saved instrument (see
    `_judge_instrument`) must be identical across orientations, or absent
    from all of them (a result written before instruments were recorded,
    returned as None: the instrument cannot be verified)."""
    models: set[str] = set()
    instruments: list[dict[str, Any] | None] = []
    for i, pair in enumerate(pairs, start=1):
        for orientation in pair["judge_orientations"]:
            evidence = orientation["evidence"]
            model = evidence.get("model")
            if not isinstance(model, str) or not model:
                raise SystemExit(
                    f"conversation_lab rejudge: {result_path} pair {i} does not record which judge model "
                    "scored it - cannot show the instrument is unchanged"
                )
            models.add(model)
            instrument = evidence.get("judge_instrument")
            if instrument is not None and not isinstance(instrument, dict):
                raise SystemExit(f"conversation_lab rejudge: {result_path} pair {i} has a malformed judge_instrument")
            instruments.append(instrument)
    if len(models) != 1:
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} was scored by more than one judge model "
            f"({', '.join(sorted(models))}) - there is no single instrument to retest"
        )
    if all(inst is None for inst in instruments):
        return models.pop(), None
    first = instruments[0]
    if any(inst != first for inst in instruments):
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} records different judge instruments across its "
            "orientations - there is no single instrument to retest"
        )
    return models.pop(), first


def _rejudge_mode(
    dry_run: bool, judge_model: str, source_instrument: dict[str, Any] | None,
) -> tuple[str, list[str]]:
    """("dry_run" | "retest" | "instrument_changed" | "instrument_unverified",
    names of the instrument fields that differ). Only "retest" is V3."""
    if dry_run:
        return "dry_run", []
    if source_instrument is None:
        return "instrument_unverified", []
    current = _judge_instrument(judge_model)
    changed = sorted(k for k in set(current) | set(source_instrument) if current.get(k) != source_instrument.get(k))
    return ("retest", []) if not changed else ("instrument_changed", changed)


def cmd_rejudge(args: argparse.Namespace) -> None:
    result_path = Path(args.result_file)
    data = _load_rejudge_source(result_path)
    pairs: list[dict[str, Any]] = data["pairs"]

    judge_model = _resolve_judge_model_for_args(args)
    source_judge_model, source_instrument = _rejudge_source_instrument(result_path, pairs)
    # V3 test-retest means the SAME instrument scores the pairs again: the
    # whole judge request (_judge_instrument), not a subset of it. Anything
    # else is refused unless asked for, and then never reported as V3.
    mode, changed = _rejudge_mode(bool(args.dry_run), judge_model, source_instrument)
    if mode in {"instrument_changed", "instrument_unverified"} and not args.allow_instrument_change:
        detail = (
            f"these judge settings differ from the source's: {', '.join(changed)}"
            if mode == "instrument_changed"
            else "the source does not record its judge instrument, so it cannot be shown unchanged"
        )
        raise SystemExit(
            f"conversation_lab rejudge: {result_path} was scored by {source_judge_model} and this run "
            f"would judge with {judge_model}; {detail}. V3 test-retest needs the identical instrument. "
            "To compare on purpose, pass --allow-instrument-change (reported as such, never as V3)"
        )
    target = args.target or data.get("target_dimension") or "turn_taking"

    total_orientations = len(pairs) * 2
    max_calls_derived = args.max_calls is None
    max_calls = args.max_calls if args.max_calls is not None else total_orientations * (1 + _JUDGE_JSON_MAX_RETRIES)
    budget = CallBudget(max_calls=max_calls)
    aborted = False
    error: str | None = None
    new_pairs: list[dict[str, Any]] = []
    result_path_out = _results_dir(args) / (
        f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-rejudge-{result_path.stem}.json"
    )

    guard_ctx = _installed_budget_guard(budget, args.max_cost)
    guard_ctx.__enter__()
    try:
        for pair in pairs:
            if budget.would_exceed(2) or _would_exceed_cost(args.max_cost):
                aborted = True
                break
            if args.dry_run:
                combined = _dry_run_combined()
                new_orientations: list[dict[str, Any]] = []
            else:
                new_results: list[dict[str, str]] = []
                new_orientations = []
                for orientation in pair["judge_orientations"]:
                    if budget.would_exceed(1) or _would_exceed_cost(args.max_cost):
                        aborted = True
                        break
                    evidence = orientation["evidence"]
                    pending_pair: dict[str, Any] = {"judge_orientations": []}
                    result = _run_judge_orientation(
                        orientation=orientation["orientation"], pending_pair=pending_pair, budget=budget,
                        judge_model=judge_model, concept=data.get("concept") or "", stage=data.get("stage") or "",
                        recipe_context=None, expected_cast=[],
                        first_arm=evidence["first_arm"], first_messages=[],
                        second_arm=evidence["second_arm"], second_messages=[],
                        recipe_facts=None, max_cost=args.max_cost,
                        prebuilt_prompt=evidence["prompt"],
                    )
                    new_results.append(result)
                    new_orientations.extend(pending_pair["judge_orientations"])
                if aborted or len(new_results) < 2:
                    aborted = True
                    break
                combined = _combine_orientations(new_results[0], new_results[1])
            new_pair = dict(pair)
            new_pair["judge"] = combined
            new_pair["judge_orientations"] = new_orientations
            new_pair["previous_judge"] = pair.get("judge")
            new_pair["previous_judge_orientations"] = pair.get("judge_orientations")
            new_pairs.append(new_pair)
            _budget_checkpoint()
    except LabBudgetAbort:
        aborted = True
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        aborted = True
        raise
    finally:
        guard_ctx.__exit__(None, None, None)
        if error is not None:
            report = _build_rejudge_report(
                args, data, result_path, judge_model, target, new_pairs, True, budget,
                result_path_out, max_calls, max_calls_derived, error=error,
            )
            _write_json_result(result_path_out, report)

    report = _build_rejudge_report(
        args, data, result_path, judge_model, target, new_pairs, aborted, budget,
        result_path_out, max_calls, max_calls_derived,
    )
    _write_json_result(result_path_out, report)
    _print_rejudge_report(report)


def _rejudge_agreement(old_pairs: list[dict[str, Any]], new_pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """V3 test-retest: per-key agreement between the ORIGINAL saved verdict
    and the just-recomputed one, for every pair that was actually re-judged
    (a pair dropped by a mid-run abort has no new verdict to compare)."""
    keys = ("overall", *ALL_JUDGE_DIMENSIONS)
    matches = {key: 0 for key in keys}
    n = min(len(old_pairs), len(new_pairs))
    for old_pair, new_pair in zip(old_pairs[:n], new_pairs[:n]):
        for key in keys:
            if old_pair["judge"][key] == new_pair["judge"][key]:
                matches[key] += 1
    return {
        "pairs_compared": n,
        "rates": {key: (round(matches[key] / n, 4) if n else None) for key in keys},
    }


def _build_rejudge_report(
    args: argparse.Namespace,
    source: dict[str, Any],
    source_path: Path,
    judge_model: str,
    target: str,
    new_pairs: list[dict[str, Any]],
    aborted: bool,
    budget: CallBudget,
    result_path: Path,
    max_calls: int,
    max_calls_derived: bool,
    error: str | None = None,
) -> dict[str, Any]:
    source_judge_model, source_instrument = _rejudge_source_instrument(source_path, source.get("pairs") or [])
    rejudge_mode, instrument_changed_fields = _rejudge_mode(bool(args.dry_run), judge_model, source_instrument)
    report: dict[str, Any] = {
        "command": "rejudge",
        **_pairwise_evaluator_metadata(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_result_file": str(source_path),
        "source_command": source.get("command"),
        "source_evaluator_prompt_sha256": source.get("evaluator_prompt_sha256"),
        "source_models": source.get("models"),
        "source_judge_model": source_judge_model,
        "judge_model": judge_model,
        "rejudge_mode": rejudge_mode,
        "instrument_changed_fields": instrument_changed_fields,
        "source_judge_instrument": source_instrument,
        "judge_instrument": None if args.dry_run else _judge_instrument(judge_model),
        "concept": source.get("concept"),
        "stage": source.get("stage"),
        "requested_pairs": len(source.get("pairs") or []),
        "rejudged_pairs_count": len(new_pairs),
        "aborted": aborted,
        "max_calls": max_calls,
        "max_calls_derived": max_calls_derived,
        "max_cost": args.max_cost,
        "calls_used": budget.used,
        "dry_run": bool(args.dry_run),
        "target_dimension": target,
        **_openrouter_fields_for(args, _openrouter_router_cost_by_model()),
        "pairs": new_pairs,
        "agreement": _rejudge_agreement(source.get("pairs") or [], new_pairs),
        "results_file": str(result_path),
    }
    if new_pairs:
        report.update(_aggregate_pairs(new_pairs, target, bool(args.dry_run)))
    if error is not None:
        report["error"] = error
    return report


def _print_rejudge_report(report: dict[str, Any]) -> None:
    print(f"\n=== conversation_lab rejudge: {report['source_result_file']} ===")
    print(f"judge_model: {report['judge_model']}")
    abort_note = "  [ABORTED: max-calls/max-cost hit]" if report["aborted"] else ""
    print(
        f"pairs re-judged: {report['rejudged_pairs_count']} / requested {report['requested_pairs']}{abort_note}"
    )
    print(
        f"calls used: {report['calls_used']} / max {report['max_calls']}  "
        f"cost cap: ${report['max_cost']:.2f}  dry_run={report['dry_run']}"
    )
    if "overall_counts" in report:
        print(f"\noverall (new verdicts): {dict(report['overall_counts'])}")
    agreement = report["agreement"]
    overall_rate = agreement["rates"].get("overall")
    rate_text = "n/a" if overall_rate is None else f"{overall_rate:.2%}"
    label = {
        "retest": "V3 test-retest agreement",
        "instrument_changed": (
            f"CHANGED-INSTRUMENT agreement ({', '.join(report['instrument_changed_fields'])} differ; "
            f"{report['source_judge_model']} -> {report['judge_model']}; NOT V3 test-retest)"
        ),
        "instrument_unverified": (
            "UNVERIFIED-INSTRUMENT agreement (source did not record its judge instrument; NOT V3 test-retest)"
        ),
        "dry_run": "dry-run agreement (template verdicts; not a measurement)",
    }[report["rejudge_mode"]]
    print(f"\n{label} (overall, vs original verdicts): {rate_text} over {agreement['pairs_compared']} pairs")
    _print_openrouter_costs(report)
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

def _resolve_pairs_container(report: dict[str, Any], variant_name: str | None) -> dict[str, Any]:
    """Resolve the pairs container: `report` itself for a normal ab/testbed
    result, or one variant's own sub-dict for an `ab --sweep` result (it
    nests every variant's own pairs under report["variants"][name]["pairs"]
    instead of a flat top-level "pairs" list, since one sweep judges N
    variants against the SAME shared control - there is no single "the
    pairs" to default to). Shared by `pairs --pick`/`--show` and `pairs-ui`
    (#7793) so both pick apart a --sweep result identically."""
    container = report
    if report.get("mode") == "sweep":
        variants = report.get("variants") or {}
        if not variant_name:
            raise SystemExit(
                "conversation_lab pairs: this is an `ab --sweep` result - it nests pairs "
                f"per variant, so pass --variant-name (available: {', '.join(sorted(variants)) or 'none'})"
            )
        if variant_name not in variants:
            raise SystemExit(
                f"conversation_lab pairs: unknown --variant-name {variant_name!r} "
                f"(available: {', '.join(sorted(variants))})"
            )
        container = variants[variant_name]
    return container


def _total_word_count(messages: list[dict[str, Any]] | None) -> int:
    return sum(len(str(m.get("message") or "").split()) for m in (messages or []) if isinstance(m, dict))


def _longer_arm(pair: dict[str, Any]) -> str:
    """"control" | "variant" | "tie" by total word count across the whole
    arm (#7793) - the S0b V5 length-bias split ("human agreement when the
    longer transcript won") needs to know which arm was actually longer,
    independent of which one the human or the judge picked."""
    control_words = _total_word_count(pair.get("control_messages"))
    variant_words = _total_word_count(pair.get("variant_messages"))
    if control_words == variant_words:
        return "tie"
    return "control" if control_words > variant_words else "variant"


def _apply_human_picks(
    container: dict[str, Any],
    pairs: list[dict[str, Any]],
    order_by_position: dict[int, tuple[str, str]],
    picks: dict[int, str],
) -> dict[str, Any]:
    """Merge `picks` ({position: "A"|"B"|"tie"}) into `container` IN PLACE:
    `human_picks` (arm labels, mapped through `order_by_position` exactly
    like the old inline `pairs --pick` code did), `human_review_order`,
    `human_pick_meta` (#7793 - per-pick longer-arm bookkeeping for the S0b
    V5 split), and the `human_judge_*` agreement stats.

    The agreement stats are recomputed from the FULL current `human_picks`
    on every call, not just the picks just added - so `pairs --pick` (which
    can record several positions in one call) and the review UI (which
    records exactly one position per HTTP request) always leave `container`
    in the identical state, and picking an already-picked position simply
    overwrites it.

    Returns the stats dict (already written into `container`) so a caller
    - the CLI's own print, or the UI's finish screen - can report it
    directly without re-deriving it.
    """
    human_picks: dict[str, str] = dict(container.get("human_picks") or {})
    human_pick_meta: dict[str, Any] = dict(container.get("human_pick_meta") or {})
    for position, label in picks.items():
        first_arm, second_arm = order_by_position[position]
        mapped = "tie" if label == "tie" else (first_arm if label == "A" else second_arm)
        human_picks[str(position)] = mapped
        pair = pairs[position - 1]
        longer_arm = _longer_arm(pair)
        human_pick_meta[str(position)] = {
            "longer_arm": longer_arm,
            "picked_longer": bool(mapped != "tie" and longer_arm != "tie" and mapped == longer_arm),
        }
    container["human_picks"] = human_picks
    container["human_pick_meta"] = human_pick_meta
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
    return {
        "agreement_rate": agreement_rate,
        "agreed": agreed,
        "disagreed": disagreed,
        "judge_tie": judge_tie,
        "picked_count": len(human_picks),
    }


def _write_pairs_report_atomic(path: Path, report: dict[str, Any]) -> None:
    """Write a pairs-review result file atomically (temp file + os.replace,
    #7793) so a crash or a concurrent read mid-write never sees a truncated
    file. Both `pairs --pick` and the review UI call this - the UI writes on
    every single pick, so this is on the hot path for it."""
    payload = json.dumps(report, indent=2, default=str)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        # The repo forbids permanent deletion (AGENTS.md): trash the orphaned
        # temp file. A trash failure propagates chained to the original error.
        if os.path.exists(tmp_name):
            send2trash(tmp_name)
        raise


def cmd_pairs(args: argparse.Namespace) -> None:
    if not args.show and not args.pick:
        raise SystemExit("conversation_lab pairs: pass --show, --pick, or both")

    result_path = Path(args.from_result)
    if not result_path.exists():
        raise SystemExit(f"conversation_lab pairs: result file not found: {result_path}")
    report = json.loads(result_path.read_text(encoding="utf-8"))
    container = _resolve_pairs_container(report, args.variant_name)

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

        stats = _apply_human_picks(container, pairs, order_by_position, picks)
        _write_pairs_report_atomic(result_path, report)
        print(f"\nrecorded {len(picks)} human pick(s) into {result_path}")
        print(
            f"human/judge agreement rate: {stats['agreement_rate']:.2%} "
            f"({stats['agreed']} agreed / {stats['disagreed']} disagreed"
            f" / {stats['judge_tie']} judge-tie - judge ties are excluded from the agreement rate)"
        )

# ---------------------------------------------------------------------------
# pairs-ui (#7793) - a local, stdlib-only web UI replacing the terminal
# `pairs --show` / `pairs --pick` flow for a blind human read (V6).
#
# Blindness contract: the HTTP JSON responses below NEVER include an arm
# name ("control"/"variant"), the A/B mapping itself, or any judge field
# (`pair["judge"]`, `judge_orientations`, ...) - only "transcript_a"/
# "transcript_b" (whichever the seeded `_blind_order` assigned), a "picked"
# label already re-mapped back to "A"/"B"/"tie", and the aggregate agreement
# stats, which are revealed only once every pair is picked (the finish
# screen). The browser never sees which side is real; only this process,
# writing straight to the result file, does.
# ---------------------------------------------------------------------------


class PairsReviewState:
    """One `ab` result's pairs, loaded once, re-read from disk on `refresh`.

    Every mutation (a pick) is written straight back to `result_path` via
    `_apply_human_picks`/`_write_pairs_report_atomic` - the SAME functions
    `pairs --pick` uses - so a pick made in the UI and a pick made on the
    command line for the same file are indistinguishable in the result JSON.
    """

    def __init__(self, result_path: Path, variant_name: str | None):
        self.result_path = result_path
        self.variant_name = variant_name
        self.refresh()

    def refresh(self) -> None:
        self.report = json.loads(self.result_path.read_text(encoding="utf-8"))
        self.container = _resolve_pairs_container(self.report, self.variant_name)
        self.pairs: list[dict[str, Any]] = self.container.get("pairs") or []
        if not self.pairs:
            raise SystemExit(f"conversation_lab pairs-ui: {self.result_path} has no pairs to review")
        self.order_by_position: dict[int, tuple[str, str]] = {
            i: _blind_order(i) for i in range(1, len(self.pairs) + 1)
        }

    @property
    def total(self) -> int:
        return len(self.pairs)

    def _human_picks(self) -> dict[str, str]:
        return self.container.get("human_picks") or {}

    def _picked_label(self, position: int) -> str | None:
        """The stored arm label for `position`, translated back to what the
        browser is allowed to see: "A", "B", "tie", or None (never picked)."""
        stored = self._human_picks().get(str(position))
        if stored is None:
            return None
        if stored == "tie":
            return "tie"
        first_arm, _second_arm = self.order_by_position[position]
        return "A" if stored == first_arm else "B"

    def first_unpicked(self) -> int | None:
        picked = self._human_picks()
        for position in range(1, self.total + 1):
            if str(position) not in picked:
                return position
        return None

    def state(self) -> dict[str, Any]:
        picked_count = len(self._human_picks())
        finished = picked_count >= self.total
        payload: dict[str, Any] = {
            "total": self.total,
            "picked_count": picked_count,
            "finished": finished,
            "first_unpicked": self.first_unpicked(),
        }
        if finished:
            payload["stats"] = {
                "agreement_rate": self.container.get("human_judge_agreement_rate"),
                "agreed": self.container.get("human_judge_agreed_count"),
                "disagreed": self.container.get("human_judge_disagreed_count"),
                "judge_tie": self.container.get("human_judge_tie_count"),
            }
        return payload

    def pair_payload(self, position: int) -> dict[str, Any]:
        if position < 1 or position > self.total:
            raise ValueError(f"position {position} out of range 1-{self.total}")
        pair = self.pairs[position - 1]
        first_arm, second_arm = self.order_by_position[position]
        first_messages = pair.get(f"{first_arm}_messages") or []
        second_messages = pair.get(f"{second_arm}_messages") or []

        def _as_turns(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
            return [
                {
                    "speaker": (m.get("character") or "?").split()[0],
                    "message": " ".join((m.get("message") or "").split()),
                }
                for m in messages
            ]

        return {
            "position": position,
            "total": self.total,
            "scenario": pair.get("scenario_id"),
            "concept": self.container.get("concept") or pair.get("concept"),
            "picked": self._picked_label(position),
            "transcript_a": _as_turns(first_messages),
            "transcript_b": _as_turns(second_messages),
        }

    def apply_pick(self, position: int, label: str) -> dict[str, Any]:
        if position < 1 or position > self.total:
            raise ValueError(f"position {position} out of range 1-{self.total}")
        if label not in ("A", "B", "tie"):
            raise ValueError(f"label {label!r} must be A, B, or tie")
        _apply_human_picks(self.container, self.pairs, self.order_by_position, {position: label})
        _write_pairs_report_atomic(self.result_path, self.report)
        return self.state()


# One self-contained page: no external requests, no build step - stdlib
# `http.server` is the entire dependency. Hotkeys A/B/T record and
# auto-advance to the next unpicked pair; Left/Right and J/K navigate
# without recording. Picking an already-picked pair overwrites it.
_PAIRS_UI_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind pair review</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 1.25rem;
         max-width: 1100px; margin-inline: auto; line-height: 1.5; }
  header { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 0.5rem; margin-bottom: 1rem; }
  h1 { font-size: 1.1rem; margin: 0; }
  #meta { color: #777; font-size: 0.9rem; }
  .columns { display: flex; gap: 1rem; flex-wrap: wrap; }
  .column { flex: 1 1 320px; min-width: 280px; border: 1px solid #999; border-radius: 8px; padding: 0.75rem 1rem;
            max-height: 65vh; overflow-y: auto; }
  .column h2 { font-size: 0.95rem; margin: 0 0 0.5rem 0; }
  .turn { margin: 0 0 0.6rem 0; }
  .speaker { font-weight: 600; }
  .picked { outline: 3px solid #2a7; }
  .controls { display: flex; gap: 0.6rem; margin: 1rem 0; flex-wrap: wrap; }
  button { font-size: 1rem; padding: 0.5rem 1.1rem; border-radius: 6px; border: 1px solid #888; cursor: pointer; }
  button.pick-a, button.pick-b, button.pick-tie { font-weight: 600; }
  .nav { display: flex; gap: 0.4rem; }
  #status { font-size: 0.85rem; color: #777; min-height: 1.2em; }
  #finish { display: none; text-align: center; margin-top: 3rem; }
  #finish h2 { font-size: 1.3rem; }
  .hint { font-size: 0.8rem; color: #888; }
</style>
</head>
<body>
<header>
  <h1 id="pair-title">Pair - of -</h1>
  <span id="meta"></span>
</header>
<div id="review">
  <div class="columns">
    <div class="column" id="col-a"><h2>A</h2><div id="turns-a"></div></div>
    <div class="column" id="col-b"><h2>B</h2><div id="turns-b"></div></div>
  </div>
  <div class="controls">
    <button class="pick-a" data-label="A">A - pick left (A)</button>
    <button class="pick-tie" data-label="tie">Tie (T)</button>
    <button class="pick-b" data-label="B">B - pick right (B)</button>
    <span class="nav">
      <button id="prev">&larr; prev (J)</button>
      <button id="next">next (K) &rarr;</button>
    </span>
  </div>
  <div id="status"></div>
  <p class="hint">Hotkeys: A / B / T to pick and advance; Left/Right or J/K to navigate without picking.</p>
</div>
<div id="finish">
  <h2>Done</h2>
  <p id="finish-count"></p>
  <p id="finish-stats"></p>
</div>
<script>
let position = null;
let total = null;

async function getJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || ("HTTP " + res.status));
  }
  return res.json();
}

function renderTurns(el, turns) {
  el.innerHTML = "";
  for (const t of turns) {
    const div = document.createElement("div");
    div.className = "turn";
    const speaker = document.createElement("span");
    speaker.className = "speaker";
    speaker.textContent = t.speaker + ": ";
    div.appendChild(speaker);
    div.appendChild(document.createTextNode(t.message));
    el.appendChild(div);
  }
}

function setPickedHighlight(label) {
  document.getElementById("col-a").classList.toggle("picked", label === "A");
  document.getElementById("col-b").classList.toggle("picked", label === "B");
}

async function loadPair(pos) {
  const pair = await getJSON("/api/pair/" + pos);
  position = pair.position;
  total = pair.total;
  const scenario = pair.scenario ? (" - " + pair.scenario) : "";
  document.getElementById("pair-title").textContent = "Pair " + pair.position + " of " + pair.total + scenario;
  document.getElementById("meta").textContent = pair.concept ? pair.concept : "";
  renderTurns(document.getElementById("turns-a"), pair.transcript_a);
  renderTurns(document.getElementById("turns-b"), pair.transcript_b);
  setPickedHighlight(pair.picked);
  document.getElementById("status").textContent = pair.picked ? ("Picked: " + pair.picked) : "";
}

async function refreshState() {
  const state = await getJSON("/api/state");
  if (state.finished) {
    document.getElementById("review").style.display = "none";
    document.getElementById("finish").style.display = "block";
    document.getElementById("finish-count").textContent = state.picked_count + " of " + state.total + " picked.";
    const s = state.stats || {};
    document.getElementById("finish-stats").textContent =
      "Human/judge agreement: " + (s.agreement_rate != null ? (Math.round(s.agreement_rate * 10000) / 100) + "%" : "n/a") +
      " (" + s.agreed + " agreed / " + s.disagreed + " disagreed / " + s.judge_tie + " judge-tie)";
    return true;
  }
  document.getElementById("review").style.display = "block";
  document.getElementById("finish").style.display = "none";
  return false;
}

async function start() {
  const state = await getJSON("/api/state");
  if (await refreshState()) return;
  await loadPair(state.first_unpicked || 1);
}

async function pick(label) {
  if (position === null) return;
  try {
    await getJSON("/api/pick", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({position: position, label: label}),
    });
  } catch (e) {
    document.getElementById("status").textContent = "Error: " + e.message;
    return;
  }
  const state = await getJSON("/api/state");
  if (await refreshState()) return;
  const next = state.first_unpicked || Math.min(position + 1, total);
  await loadPair(next);
}

async function navigate(delta) {
  if (position === null) return;
  const next = Math.max(1, Math.min(total, position + delta));
  if (next !== position) await loadPair(next);
}

document.querySelectorAll("button[data-label]").forEach(btn => {
  btn.addEventListener("click", () => pick(btn.dataset.label));
});
document.getElementById("prev").addEventListener("click", () => navigate(-1));
document.getElementById("next").addEventListener("click", () => navigate(1));

window.addEventListener("keydown", (ev) => {
  const key = ev.key.toLowerCase();
  if (key === "a") pick("A");
  else if (key === "b") pick("B");
  else if (key === "t") pick("tie");
  else if (key === "arrowleft" || key === "j") navigate(-1);
  else if (key === "arrowright" || key === "k") navigate(1);
});

start();
</script>
</body>
</html>
"""


class _PairsUIHandler(http.server.BaseHTTPRequestHandler):
    """Routes:
    GET  /                -> the page (_PAIRS_UI_HTML)
    GET  /api/state        -> {total, picked_count, finished, first_unpicked, stats?}
    GET  /api/pair/<n>      -> blind pair payload (see PairsReviewState.pair_payload)
    POST /api/pick          -> {"position": int, "label": "A"|"B"|"tie"} -> new state
    """

    server_version = "ConversationLabPairsUI/1"

    def log_message(self, fmt: str, *fmt_args: Any) -> None:  # noqa: A003 - stdlib signature
        pass  # keep stdout to the one startup URL line; nothing here reveals review content anyway

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        state: PairsReviewState = self.server.review_state  # type: ignore[attr-defined]
        if path in ("/", "/index.html"):
            self._send_html(_PAIRS_UI_HTML)
            return
        if path == "/api/state":
            self._send_json(state.state())
            return
        if path.startswith("/api/pair/"):
            raw = path[len("/api/pair/"):]
            try:
                position = int(raw)
                payload = state.pair_payload(position)
            except (ValueError, IndexError) as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            self._send_json(payload)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib method name
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/api/pick":
            self._send_json({"error": "not found"}, status=404)
            return
        state: PairsReviewState = self.server.review_state  # type: ignore[attr-defined]
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length else b""
            body = json.loads(raw_body or b"{}")
            position = body["position"]
            label = body["label"]
            if not isinstance(position, int) or not isinstance(label, str):
                raise ValueError("position must be an int and label must be a string")
            new_state = state.apply_pick(position, label)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        self._send_json(new_state)


class _PairsUIServer(http.server.HTTPServer):
    """Binds to 127.0.0.1 only (never 0.0.0.0) - this is a local review tool
    over unauthenticated HTTP; it must never be reachable off the machine."""

    allow_reuse_address = True

    def __init__(self, port: int, review_state: PairsReviewState):
        super().__init__(("127.0.0.1", port), _PairsUIHandler)
        self.review_state = review_state


def cmd_pairs_ui(args: argparse.Namespace) -> None:
    result_path = Path(args.from_result)
    if not result_path.exists():
        raise SystemExit(f"conversation_lab pairs-ui: result file not found: {result_path}")
    state = PairsReviewState(result_path, args.variant_name)
    server = _PairsUIServer(args.port, state)
    host, port = server.server_address[:2]
    print(f"conversation_lab pairs-ui: serving {result_path} at http://{host}:{port}/  (Ctrl-C to stop)")
    print(f"{state.total} pairs, {len(state._human_picks())} already picked")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

# ---------------------------------------------------------------------------
# freeze - distill one arm's transcript per scenario from an ab result
# ---------------------------------------------------------------------------

def cmd_freeze(args: argparse.Namespace) -> None:
    """Write (or append to) a frozen prior-days file from an `ab` result.

    See the "Frozen prior days" section above for the file shapes and the
    one-day-at-a-time method this supports.
    """
    from_path = Path(args.from_result)
    if not from_path.exists():
        raise SystemExit(f"conversation_lab freeze: result file not found: {from_path}")
    try:
        raw = from_path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"conversation_lab freeze: cannot read result file {from_path}: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        report = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"conversation_lab freeze: result file is not valid JSON: {from_path} ({exc})") from exc
    if not isinstance(report, dict) or report.get("command") != "ab":
        raise SystemExit(f"conversation_lab freeze: --from must be an `ab` result JSON: {from_path}")

    mode = report.get("mode")
    if mode not in ("testbed", "sweep"):
        raise SystemExit(
            "conversation_lab freeze: --from must be an `ab --testbed` or `ab --sweep` result - "
            "a single-concept result has no scenario_id to key frozen days by"
        )
    stage = report.get("stage")
    if stage not in simulate_module.DAY_ORDER:
        raise SystemExit(f"conversation_lab freeze: result has unknown stage {stage!r}")

    variant_name = args.variant
    if mode == "sweep":
        if not variant_name:
            raise SystemExit("conversation_lab freeze: --variant NAME is required for an `ab --sweep` result")
        variants = report.get("variants") or {}
        if variant_name not in variants:
            raise SystemExit(
                f"conversation_lab freeze: unknown --variant {variant_name!r} "
                f"(available: {', '.join(sorted(variants)) or 'none'})"
            )
        pairs = variants[variant_name].get("pairs") or []
    else:
        if variant_name:
            raise SystemExit("conversation_lab freeze: --variant is only valid for an `ab --sweep` result")
        pairs = report.get("pairs") or []

    by_scenario: dict[str, list[dict[str, Any]]] = {}
    for pair in pairs:
        scenario_id = pair.get("scenario_id")
        if not scenario_id:
            raise SystemExit(
                "conversation_lab freeze: result pair has no scenario_id - "
                "freeze supports --testbed/--sweep results only"
            )
        by_scenario.setdefault(scenario_id, []).append(pair)
    if not by_scenario:
        raise SystemExit("conversation_lab freeze: result has no completed pairs to freeze")

    frozen_scenarios: dict[str, dict[str, Any]] = {}
    for scenario_id in sorted(by_scenario):
        chosen = _freeze_pick_pair(by_scenario[scenario_id], args.arm, args.pick)
        messages = chosen.get(f"{args.arm}_messages")
        if messages is None:
            raise SystemExit(
                f"conversation_lab freeze: chosen pair for scenario {scenario_id!r} "
                f"has no {args.arm}_messages"
            )
        frozen_scenarios[scenario_id] = {"day": stage, "messages": copy.deepcopy(messages)}

    if args.append_to:
        out_path = Path(args.append_to)
        if args.out and Path(args.out).resolve() != out_path.resolve():
            raise SystemExit(
                "conversation_lab freeze: --out and --append-to disagree - "
                "pass only --append-to (it is both input and output)"
            )
        if not out_path.exists():
            raise SystemExit(f"conversation_lab freeze: --append-to file not found: {out_path}")
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"conversation_lab freeze: --append-to file is not valid JSON: {out_path} ({exc})") from exc
        if not isinstance(existing, dict) or not isinstance(existing.get("scenarios"), dict):
            raise SystemExit(f"conversation_lab freeze: --append-to file is not a frozen prior-days file: {out_path}")

        existing_days = _frozen_days_present(existing)
        if existing_days:
            last_day = max(existing_days, key=_day_index)
            if _day_index(stage) <= _day_index(last_day):
                raise SystemExit(
                    f"conversation_lab freeze: cannot append {stage!r} - it is not after "
                    f"the last frozen day {last_day!r} in {out_path}"
                )
        existing_ids = set(existing["scenarios"])
        new_ids = set(frozen_scenarios)
        if existing_ids != new_ids:
            raise SystemExit(
                f"conversation_lab freeze: cannot append - scenario sets differ "
                f"(existing: {sorted(existing_ids)}, new: {sorted(new_ids)})"
            )

        merged_scenarios: dict[str, dict[str, Any]] = {}
        for scenario_id in sorted(existing_ids):
            merged: dict[str, Any] = {}
            for day, messages in _frozen_scenario_days(existing, scenario_id):
                merged[day] = {"day": day, "messages": copy.deepcopy(messages)}
            merged[stage] = frozen_scenarios[scenario_id]
            merged_scenarios[scenario_id] = merged
        output: dict[str, Any] = {
            "frozen_from": {"path": str(from_path), "sha256": digest},
            "stage": stage,
            "arm": args.arm,
            "pick": args.pick,
            "scenarios": merged_scenarios,
        }
    else:
        if not args.out:
            raise SystemExit("conversation_lab freeze: --out is required unless --append-to is passed")
        out_path = Path(args.out)
        output = {
            "frozen_from": {"path": str(from_path), "sha256": digest},
            "stage": stage,
            "arm": args.arm,
            "pick": args.pick,
            "scenarios": frozen_scenarios,
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    verb = "appended" if args.append_to else "froze"
    print(
        f"{verb} {len(frozen_scenarios)} scenario(s) for {stage} "
        f"({args.arm}, {args.pick}) from {from_path} -> {out_path}"
    )

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_budget_guard_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--budget-ledger", type=Path, default=None,
        help="Persist/resume the shared Anthropic spending ledger across ab, bench, and calibrate.",
    )
    parser.add_argument(
        "--create-budget-ledger", action="store_true",
        help="Create a new ledger at --budget-ledger; refuses to replace an existing file.",
    )


def _add_provider_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider", choices=("anthropic", "openrouter"), default=None,
        help=(
            "Which provider routes paid lab calls. Default: openrouter (the lab's "
            "OpenRouter bill, pinned to Anthropic's servers). Pass --provider anthropic "
            "for the production-direct path; --budget-ledger always uses anthropic."
        ),
    )


def _add_models_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--models", default=None, choices=tuple(sorted(_LAB_MODELS.sets)),
        help=(
            f"Which model set from {LAB_MODELS_PATH} to use for dialogue + judge "
            f"calls (default: {_LAB_MODELS.default!r}). Only meaningful with "
            "--provider openrouter - --provider anthropic always uses the default set."
        ),
    )


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
            "--testbed and --sweep reserve four calls/turn for each generation arm, plus two judges: "
            "`panel_size * runs * (2 * 4 * max_turns + 2)`, where "
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
    ab.add_argument(
        "--prior-days", default=None,
        help=(
            "Frozen prior-day transcript file from `conversation_lab freeze`. "
            "In --testbed/--sweep mode, both arms of every pair get that scenario's "
            "frozen earlier days as identical initial context (initial_recent_lines). "
            "Refuses when the file lacks a panel scenario, or contains --stage or a later day."
        ),
    )
    _add_provider_option(ab)
    _add_models_option(ab)
    _add_budget_guard_options(ab)

    bench = sub.add_parser(
        "bench",
        help="Characterize ONE setting over N runs: mean and spread per metric, plus a judge pass rate.",
        description=(
            "Run a single arm --runs times through the production call shape "
            "(mode='openai', prompt_style='scene', ticks_per_day=0, plus a recipe "
            "anchor), score every run with the deterministic metrics in "
            "scripts/conversation_metrics.py AND with the production publish gate "
            "(backend/admin/cron_routes.py's _judge_dialogue, the same judge the "
            "Sunday cron runs), and report mean/stdev/stderr per metric, the judge "
            "pass rate, per-dimension score distributions, which dimension came back "
            "weakest most often, and any 4-gram a character reused across runs. "
            "This is the baseline `ab` cannot produce: `ab` only emits pairwise "
            "win/tie/loss, so it has no absolute number for a later run to move away "
            "from. Change one thing, bench again, and pass --compare with the first "
            "result file to see which averages moved."
        ),
    )
    bench.add_argument("--stage", required=True, choices=simulate_module.DAY_ORDER)
    bench.add_argument("--runs", type=int, required=True, help="How many times to run the setting (no default - choose N per bench)")
    bench.add_argument("--concept", default=None, help="Dish name; required with --recipe-context, optional with --from-episode (defaults to that episode's recipe title)")
    bench.add_argument("--from-episode", default=None, help="Take the recipe anchor, concept and the judge's PREVIOUS DAYS context from this episode")
    bench.add_argument("--recipe-context", default=None, help="One-line recipe anchor, verbatim; the judge then sees no previous days (mutually exclusive with --from-episode)")
    bench.add_argument("--local", action="store_true", help="With --from-episode, skip the CDN; read the local mirror")
    bench.add_argument("--label", default=None, help="Name for this bench, used for the result filename and the log row (default: <stage>-n<runs>)")
    bench.add_argument("--compare", default=None, help="Earlier bench result JSON. Before paid work, validates aggregate schema and recorded stage, run mode, recipe context, frozen judge inputs, and models; source comparability for metrics, judge rules, generator code/configuration, and other prompt inputs must be checked manually.")
    bench.add_argument(
        "--allow-mismatched-baseline", action="store_true",
        help=(
            "Bypass all recorded scenario checks (stage, run mode, recipe and frozen judge inputs, "
            "and models). Aggregate schema validation still applies. Use only when the mismatch "
            "is deliberate and documented."
        ),
    )
    bench.add_argument(
        "--max-calls", type=int, default=None,
        help=(
            f"Defaults to `runs * ({_MAX_CALLS_PER_TURN} * max_turns + 2)` when omitted, "
            "where max_turns is the upper bound of "
            "scripts.simulate_dialogue_week.TICKS_RANGE for --stage (floored at "
            f"{_MIN_MAX_TURNS_FLOOR}) and {_MAX_CALLS_PER_TURN} is the most paid calls one "
            "turn can cost (initial response, CoT-leak retry, fault rewrite, CoT-leak retry "
            "on the rewrite). Pass an explicit value to override. Ignored under --dry-run, "
            "which cannot spend anything."
        ),
    )
    bench.add_argument(
        "--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
        help=f"USD cap on total_cost for this invocation (default ${DEFAULT_MAX_COST_USD:.2f}); see `ab --help`.",
    )
    bench.add_argument("--dry-run", action="store_true", help="mode='template', zero API calls, no judge - a plumbing check")
    bench.add_argument("--no-log", action="store_true", help="Do not append a row to the experiments log")
    bench.add_argument("--experiments-log", default=None, help=f"Override the EXPERIMENTS.md path (default: {DEFAULT_EXPERIMENTS_LOG})")
    bench.add_argument("--results-dir", default=None)
    _add_provider_option(bench)
    _add_budget_guard_options(bench)

    calibrate = sub.add_parser(
        "calibrate",
        help="Sanity-check the judge itself against known-degraded transcripts.",
        description=(
            "Take a real transcript for --stage and build two degraded "
            "copies (shuffled turn order, speakers rotated by one), "
            "pairwise-judge real vs. degraded with positions swapped, and "
            "report the judge's preference rate for the real transcript "
            "per degradation - >= 0.8 is 'GRADER OK'. Or pass "
            "--reference-panel to validate and evaluate the frozen human "
            "versioned reference fixture with a separate exploratory report."
        ),
    )
    calibrate.add_argument("--from-episode")
    calibrate.add_argument("--reference-panel", help="Validate / compare a versioned reference panel JSON")
    calibrate.add_argument("--stage", choices=simulate_module.DAY_ORDER)
    calibrate.add_argument("--runs", type=int, default=3)
    calibrate.add_argument("--local", action="store_true")
    calibrate.add_argument("--max-calls", type=int, default=40)
    calibrate.add_argument(
        "--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
        help=f"USD cap on total_cost for this invocation (default ${DEFAULT_MAX_COST_USD:.2f}); see `ab --help`.",
    )
    calibrate.add_argument("--dry-run", action="store_true")
    calibrate.add_argument("--results-dir", default=None)
    _add_provider_option(calibrate)
    _add_models_option(calibrate)
    _add_budget_guard_options(calibrate)

    rejudge = sub.add_parser(
        "rejudge",
        help="Re-run the judge on an ab result's SAVED transcripts, without regenerating dialogue.",
        description=(
            "Load an `ab` result JSON (single-concept or --testbed mode; --sweep results are not "
            "supported) and re-judge every pair's two already-completed orientations from their "
            "saved prompts (evidence.prompt, byte-identical to what the original judge call sent - "
            "the judge prompt itself is never changed by this command), using --models' judge (or "
            "--provider anthropic). No dialogue is regenerated - this is a judge-only re-score, for "
            "RESEARCH_PLAN.md's V3 test-retest (re-judge a pilot's saved pairs with the SAME judge a "
            "second time; any difference in the saved judge instrument - model, route, token ceiling, "
            "temperature, prompt, retry bounds - is refused unless --allow-instrument-change is passed, "
            "and is then reported as such, never as V3). Refuses (nonzero exit, "
            "no result written) on a source file that is aborted, has any partial pair, or is missing "
            "a transcript or a saved prompt - there is nothing safe to re-judge in a partial run. "
            "Writes a new result file recording the source file, the new verdicts, the new "
            "judge_orientations, the judge model, evaluator_prompt_sha256, and a per-dimension "
            "agreement summary against the ORIGINAL verdicts."
        ),
    )
    rejudge.add_argument("result_file", help="Path to an ab result JSON file (single-concept or --testbed mode)")
    rejudge.add_argument(
        "--target", default=None, choices=(*ALL_JUDGE_DIMENSIONS, "overall"),
        help="Target dimension for the re-judged aggregate report (default: the source result's own target_dimension)",
    )
    rejudge.add_argument(
        "--max-calls", type=int, default=None,
        help=(
            "Defaults to 2 * pair_count * (1 + retry cap) when omitted (each pair replays exactly 2 "
            "orientations, each retried up to _JUDGE_JSON_MAX_RETRIES times on an unparseable verdict)."
        ),
    )
    rejudge.add_argument(
        "--max-cost", type=float, default=DEFAULT_MAX_COST_USD,
        help=f"USD cap on total_cost for this invocation (default ${DEFAULT_MAX_COST_USD:.2f}); see `ab --help`.",
    )
    rejudge.add_argument("--dry-run", action="store_true", help="Zero paid calls - every re-judged verdict is 'tie'")
    rejudge.add_argument(
        "--allow-instrument-change", action="store_true",
        help=(
            "Allow a judge instrument (model, route, token ceiling, temperature, prompt, retry bounds) "
            "different from - or not recorded by - the source. Without it rejudge refuses, because V3 "
            "test-retest needs the identical instrument; with it the report says instrument_changed "
            "or instrument_unverified, never V3."
        ),
    )
    rejudge.add_argument("--results-dir", default=None)
    _add_provider_option(rejudge)
    _add_models_option(rejudge)

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

    pairs_ui_cmd = sub.add_parser(
        "pairs-ui",
        help="Local web UI for the same blind human read `pairs --show`/`--pick` does (#7793).",
        description=(
            "Serve a one-pair-per-screen web UI (stdlib http.server, 127.0.0.1 only) over an ab "
            "result's transcripts - replaces the terminal pairs --show/--pick flow for a V6 blind "
            "read. Hotkeys A/B/T record a pick and auto-advance to the next unpicked pair; "
            "Left/Right or J/K navigate without recording; picking an already-picked pair "
            "overwrites it. Every pick is written straight to the result file (via the SAME "
            "_apply_human_picks/_write_pairs_report_atomic functions `pairs --pick` uses, so the "
            "two are interchangeable on the same file) - reloading the page resumes at the first "
            "unpicked pair. The arm names, the A/B mapping, and every judge field are never sent "
            "to the browser; a finish screen appears once every pair is picked, showing the count "
            "and the human/judge agreement stats."
        ),
    )
    pairs_ui_cmd.add_argument("--from", dest="from_result", required=True, help="Path to an ab result JSON file")
    pairs_ui_cmd.add_argument(
        "--variant-name", default=None,
        help="For an `ab --sweep` result only - see `pairs --help`.",
    )
    pairs_ui_cmd.add_argument("--port", type=int, default=8765, help="Port to bind on 127.0.0.1 (default 8765; 0 picks a free port)")

    freeze_cmd = sub.add_parser(
        "freeze",
        help="Freeze one arm's best transcript per scenario from an ab result into a prior-days file.",
        description=(
            "Distill an `ab --testbed` or `ab --sweep` result into a frozen "
            "prior-days file for `ab --prior-days`. Per scenario_id, pick one "
            "transcript from the --arm arm: --pick judge selects the pair where "
            "that arm won the most judge dimensions (ties broken by lowest "
            "run_index); --pick run0 selects the lowest run_index. Writes "
            '{"frozen_from": {"path", "sha256"}, "stage", "arm", "pick", '
            '"scenarios": {id: {"day", "messages"}}}. With --append-to, a later '
            "day is merged into the existing file (scenario -> day -> entry, in "
            "week order); refuses when the new day is not after the last frozen day."
        ),
    )
    freeze_cmd.add_argument("--from", dest="from_result", required=True, help="Path to an ab result JSON file")
    freeze_cmd.add_argument("--arm", required=True, choices=("control", "variant"))
    freeze_cmd.add_argument("--pick", required=True, choices=("judge", "run0"))
    freeze_cmd.add_argument("--out", default=None, help="Write the frozen file here (unless --append-to)")
    freeze_cmd.add_argument(
        "--append-to", default=None,
        help="Append this result's day to an existing frozen file (input and output in one path)",
    )
    freeze_cmd.add_argument(
        "--variant", dest="variant", default=None,
        help="For an `ab --sweep` result only: which variant's pairs to freeze",
    )

    return parser

def _dispatch_command(args: argparse.Namespace) -> None:
    if args.command == "baseline":
        cmd_baseline(args)
    elif args.command == "ab":
        cmd_ab(args)
    elif args.command == "bench":
        cmd_bench(args)
    elif args.command == "calibrate":
        cmd_calibrate(args)
    elif args.command == "rejudge":
        cmd_rejudge(args)
    elif args.command == "pairs":
        cmd_pairs(args)
    elif args.command == "pairs-ui":
        cmd_pairs_ui(args)
    elif args.command == "freeze":
        cmd_freeze(args)
    else:  # pragma: no cover - argparse enforces valid choices
        raise SystemExit(f"conversation_lab: unknown command {args.command!r}")


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    ledger_path = getattr(args, "budget_ledger", None)
    create_ledger = bool(getattr(args, "create_budget_ledger", False))
    if create_ledger and ledger_path is None:
        raise SystemExit("conversation_lab: --create-budget-ledger requires --budget-ledger PATH")
    if ledger_path is not None and args.command not in {"ab", "bench", "calibrate"}:
        raise SystemExit("conversation_lab: --budget-ledger is supported only for ab, bench, and calibrate")

    # argparse's float accepts "nan"/"inf"; a non-finite or non-positive cap
    # cannot be enforced, so refuse it before anything is spent.
    max_cost_arg = getattr(args, "max_cost", None)
    if max_cost_arg is not None and not (isfinite(max_cost_arg) and max_cost_arg > 0):
        raise SystemExit(f"conversation_lab: --max-cost must be a positive finite amount, got {max_cost_arg!r}")

    provider = _resolve_provider(args, ledger_path)
    if provider is not None:
        args.provider = provider

    global _OPENROUTER_KEY_BEFORE, _OPENROUTER_KEY_AFTER
    _OPENROUTER_KEY_BEFORE = None
    _OPENROUTER_KEY_AFTER = None

    try:
        if provider == "openrouter":
            if ledger_path is not None:
                raise SystemExit("conversation_lab: --budget-ledger requires --provider anthropic")
            if not args.dry_run:
                _openrouter_preflight(args)

        if ledger_path is None:
            _dispatch_command(args)
            return

        guard = AnthropicBudgetGuard(
            ledger_path,
            budget_usd=getattr(args, "max_cost", DEFAULT_MAX_COST_USD),
            create=create_ledger,
        )
        with guard:
            token = _ACTIVE_BUDGET_GUARD.set(guard)
            try:
                phase = "calibration" if args.command == "calibrate" else args.command
                with guard.phase(phase):
                    if not args.dry_run:
                        guard.validate_configured_models(
                            dialogue_model=(
                                None if args.command == "calibrate"
                                else os.environ.get("DIALOGUE_MODEL", "").strip()
                            ),
                            judge_model=os.environ.get("JUDGE_MODEL", "").strip(),
                        )
                    _dispatch_command(args)
                _budget_checkpoint()
            finally:
                _ACTIVE_BUDGET_GUARD.reset(token)
    except (ConversationLabError, BudgetGuardError) as exc:
        raise SystemExit(f"conversation_lab {args.command}: {exc}") from exc

if __name__ == "__main__":
    main()
