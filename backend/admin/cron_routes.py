"""Vercel Cron API routes for the Muffin Pan Recipes pipeline.

Each route corresponds to one pipeline stage. Vercel hits these endpoints
on a schedule defined in vercel.json. Local dev can also POST to them directly.

IMPORTANT: Vercel crons send GET requests (no body). Manual/test invocations
use POST with a JSON body. All routes must accept both methods via api_route().
The _parse_body() helper returns StageRequest defaults for GET requests.

Authentication: Vercel sends the CRON_SECRET as Authorization: Bearer <secret>.
Unauthorized requests are rejected with 401.

Timeout budget:
  - monday/tuesday/thursday/friday/saturday/sunday: ~30-60s (OpenAI dialogue)
  - wednesday: ~3-4 min (3x Gemini images + dialogue)
  Vercel Pro plan allows up to 300s function timeout — set in vercel.json.

Usage:
    # Trigger manually (dev) with default model:
    curl -X POST http://localhost:8000/api/cron/monday \
      -H "Authorization: Bearer $CRON_SECRET" \
      -H "Content-Type: application/json"

    # Override dialogue model per request:
    curl -X POST http://localhost:8000/api/cron/monday \
      -H "Authorization: Bearer $CRON_SECRET" \
      -H "Content-Type: application/json" \
      -d '{"concept": "Mini Shepherd Pies", "model": "openai/gpt-5.1"}'

    # Available models: see backend/config.py docstring for full list.
"""

from __future__ import annotations
import copy
import hmac
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend.config import config
from backend.admin.cron.baker_gates import _bake_through_gates, _category_from_recipe
from backend.admin.cron.concept import ConceptSelectionError, _resolve_monday_concept
from backend.admin.cron.memories import _generate_episode_memories
from backend.admin.cron.editorial_qa import (
    MAX_QA_FIX_ATTEMPTS,
    QA_UNAVAILABLE_MARKER,
    _auto_fix_recipe,
    _editorial_qa_review,
)
from backend.judge_rubric import CHARACTER_RULES
from backend.recipe_model import ingredient_names
from backend.publishing.episode_renderer import (
    regenerate_and_upload,
    upload_latest_json,
)
from backend.storage import storage
from backend.utils import episode_integrity
from backend.utils.indexnow import submit_urls as _indexnow_submit_urls
from backend.utils.catalog import (
    VALID_CATEGORIES,
    load_published_catalog,
    normalize_category,
)
from backend.utils.logging import get_logger
from backend.utils.recipe_copy import generate_intro, recent_openers, recipe_pitch
from backend.utils import photo_review
from backend.utils.discord import (
    notify_judge_advisory,
    notify_judge_failure,
    notify_photos_ready,
    notify_pipeline_failure,
    notify_publish_held,
)
from backend.utils.model_router import generate_judge_response

logger = get_logger(__name__)

# Lazy imports of heavy pipeline modules so the router can load even
# if orchestrator dependencies are missing in non-pipeline environments.
_orchestrator_cls = None
_run_simulation = None


def _get_orchestrator():
    global _orchestrator_cls
    if _orchestrator_cls is None:
        from backend.orchestrator import RecipeOrchestrator
        _orchestrator_cls = RecipeOrchestrator
    return _orchestrator_cls


def _get_run_simulation():
    global _run_simulation
    if _run_simulation is None:
        from scripts.simulate_dialogue_week import run_simulation
        _run_simulation = run_simulation
    return _run_simulation


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _verify_cron_secret(request: Request) -> None:
    """Reject requests that don't carry the CRON_SECRET.

    Vercel automatically attaches Authorization: Bearer <CRON_SECRET> to
    all cron-triggered requests. Manual triggers must do the same.
    In LOCAL_DEV mode the check is skipped entirely.
    """
    if config.is_local_dev:
        if os.environ.get("VERCEL_ENV"):
            # Safety guard: LOCAL_DEV must never bypass auth in a Vercel environment
            logger.warning("CRON_SECRET bypass blocked: VERCEL_ENV is set but LOCAL_DEV=true")
        else:
            return  # bypass only in genuine local dev

    cron_secret = os.environ["CRON_SECRET"]

    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {cron_secret}"
    if not hmac.compare_digest(auth_header, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing CRON_SECRET",
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# The concept a week carries before anything has picked one. It must NEVER
# survive into a stored episode: a stored placeholder means the catalog-aware
# novelty scorer in scripts/pick_concept.py never ran, and with it every
# duplicate defense that reads the published catalog. Every production week
# from 2026-W30 to 2026-W35 stored exactly this string — see RUNBOOK
# "INCIDENT 4" and card #6855. Defined in backend/utils/episode_integrity so
# the health check and the session-start surface assert against the same
# literal this module writes.
PLACEHOLDER_CONCEPT = episode_integrity.PLACEHOLDER_CONCEPT
# THE publication predicate (#7936): published_at OR a complete Sunday, as the
# static builder has always decided it. Every published-week guard uses it.
episode_is_published = episode_integrity.episode_is_published

# In-character homepage note for a week that never published (#7630).
# Defined in episode_integrity so episode_routes' read path and this
# module's write path share exactly one copy of the text.
WEEK_OFF_MESSAGE = episode_integrity.WEEK_OFF_MESSAGE

# Temporary containment for the remainder of W39. This applies only to
# authenticated scheduled GETs; manual POST recovery remains available.
_SCHEDULED_PAUSE_EPISODE_ID = "2026-W39"
_SCHEDULED_PAUSE_STAGES = frozenset({"thursday", "friday", "saturday", "sunday"})


STAGE_TO_ROLE = {
    "monday": "brainstorm",
    "tuesday": "recipe_development",
    "wednesday": "photography",
    "thursday": "copywriting",
    "friday": "final_review",
    "saturday": "deployment",
    "sunday": "publish",
}

def _current_episode_id() -> str:
    """Return the ISO week episode ID, e.g. '2026-W09' (UTC; tests pin this name)."""
    return episode_integrity.current_episode_id(datetime.now(timezone.utc))


def _scheduled_w39_pause(request: Request, stage: str) -> dict | None:
    """Return an explicit pause response for W39's remaining scheduled GETs."""
    if request.method != "GET" or stage not in _SCHEDULED_PAUSE_STAGES:
        return None
    episode_id = _current_episode_id()
    if episode_id != _SCHEDULED_PAUSE_EPISODE_ID:
        return None
    return {
        "status": "paused",
        "stage": stage,
        "episode_id": episode_id,
        "reason": (
            "Temporary W39 containment: this scheduled stage was intentionally skipped "
            "while the existing Tuesday/Wednesday failures remain unresolved. "
            "No stage was completed or published."
        ),
    }


def _load_or_create_episode(episode_id: str, concept: str, stage: str) -> dict:
    """Load the week's episode, or start a new one only if none exists.

    The read is strict (#8145): None means the episode is genuinely missing,
    and a failed read raises. The lenient `load_episode` turned any Blob error
    into "no episode", so a re-fired Monday during a Blob failure saved a fresh
    skeleton over the real week. A failed read now alerts and returns 503
    before anything is written.
    """
    try:
        ep = storage.load_episode_strict(episode_id)
    except Exception as exc:
        detail = (
            f"Episode {episode_id} could not be read: {type(exc).__name__}: {exc}. "
            f"The {stage} stage did not run and nothing was written."
        )
        logger.error(detail)
        notify_pipeline_failure(
            recipe_id="unknown", concept=concept or "unknown",
            stage=stage, error_message=detail,
        )
        raise HTTPException(status_code=503, detail=detail) from exc
    if ep:
        return ep
    return _new_episode(episode_id, concept)


def _new_episode(episode_id: str, concept: str) -> dict:
    return {
        "episode_id": episode_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "concept": concept,
        "stages": {},
        "events": [],
        "recipe_id": None,
    }


# How early the Sunday cron may fire and still count as the week's own
# Sunday run for the refusal note (#7630).
_SUNDAY_NOTE_TOLERANCE = timedelta(minutes=5)


def _utc_now() -> datetime:
    """The clock, behind one name so tests can pin it."""
    return datetime.now(timezone.utc)

def _apply_week_off_note(episode_id: str, ep: dict) -> None:
    """Stamp `ep` with a "kitchen took the week off" note when the week
    that just ended never published (#7630).

    Decided ONCE, at the start of Monday's cron — by then the previous
    week's Sunday window has necessarily already closed (see
    episode_integrity.week_before), so this is a plain
    published check (episode_is_published), no time-window arithmetic. Stored on the CURRENT
    week's episode itself, not written straight to pages/latest.json:
    every later stage this week that calls regenerate_and_upload reads it
    off this same `ep` (reloaded from storage each time), so it carries
    forward through Tuesday-Saturday for free, and through a Monday retry
    on a warm Lambda for the same reason. A successful Sunday publish never
    looks at this field at all (its write is a bare
    ``{"status": "published"}``), which is what clears the note.

    Only ever called from inside a cron handler's `_test_mode_scope(body)`
    block, so it reads and writes through whatever prefix (`""` or
    `"test/"`) that handler already established — a test-mode Monday can
    only ever see test-prefixed data here, never the prod previous week.

    The previous week is derived from `episode_id` itself, never the clock:
    a manual force=true re-fire of an older week asks about the week before
    THAT episode, so it cannot stamp a false note for the current week.

    The note is cosmetic: nothing here may fail Monday. An unparseable
    episode id or a failed read of the previous week is logged and leaves
    the field exactly as it was (ops still see the failure through their own
    alerts and health checks).

    Uses `load_episode_verified`, not the ordinary `load_episode` (#7630): a
    Blob outage must not be mistaken for "the previous week never
    published". `load_episode` intentionally falls back to (empty) local
    filesystem data on ANY cloud error and returns None either way, which
    would stamp a false note during an outage. `load_episode_verified`
    returns None only for an ACTUAL missing episode, and raises on a failed
    read or on a body the Blob CDN served stale (its ETag differs from the
    stored version's, #7936) — caught below, same as an unparseable id.
    """
    # A note naming THIS week came from Sunday's own refuse-to-publish path,
    # and stays true until this week publishes. A re-fired Monday must not
    # replace or clear it on the strength of the previous week (Codex, #7630).
    existing = ep.get("week_off_note")
    if (
        isinstance(existing, dict)
        and existing.get("missed_week") == episode_id
        and not episode_integrity.episode_is_published(ep)
    ):
        return
    # A Monday re-fired after this week's own Sunday window, on a week that
    # has not published AND still has no Monday recipe, is late for its own
    # week: Sunday's recipe-less refusal put the note on the homepage only
    # (_note_week_off_without_a_recipe saves no episode), so it is rebuilt
    # here rather than cleared (Codex, #7630). The missing recipe is the
    # evidence of that refusal, never the clock alone: a week WITH a recipe
    # that missed Sunday (held for photo approval, or failed after
    # Wednesday) shows no own-week note (Erik, 2026-10-05, decision 1), and
    # a Wednesday-incomplete refusal is already kept by the check above.
    if (
        not episode_integrity.episode_is_published(ep)
        and not _monday_recipe_ready(ep)
        and _sunday_window_reached(episode_id)
    ):
        ep["week_off_note"] = {"message": WEEK_OFF_MESSAGE, "missed_week": episode_id}
        return
    try:
        previous_id = episode_integrity.week_before(episode_id)
        previous_episode = storage.load_episode_verified(previous_id)
    except Exception as exc:  # noqa: BLE001 - best-effort cosmetic note
        logger.warning(
            f"week-off note check skipped for {episode_id}: {type(exc).__name__}: {exc}"
        )
        return
    if episode_integrity.week_off_note_due(previous_episode):
        ep["week_off_note"] = {"message": WEEK_OFF_MESSAGE, "missed_week": previous_id}
    else:
        ep.pop("week_off_note", None)


# Cap for the texture/identity anchor in _build_recipe_context (#7104). Long
# enough for the recipe's whole description, short enough that it cannot become
# the recitation the summary exists to prevent.
RECIPE_CONTEXT_ANCHOR_MAX = 400
# Names-only ingredient list handed to the SPEAKERS (#7441). Generous enough
# that a normal recipe fits whole — W39's 22 ingredients render to ~250 chars —
# because a truncated list cannot honestly be called complete, and the whole
# point of this line is that it IS complete.
RECIPE_CONTEXT_INGREDIENTS_MAX = 600

# Cap for the method block the JUDGE receives (#7104 second pass). Sized against
# real stored recipes, not guessed: W35-W38 methods run 2,486-4,956 chars, the
# 4,956 being W38's 36 steps. An earlier 2,000 truncated W38 at step 16 of 36 -
# it resolved the lamination claim only because "roll the dough up into a log"
# happened to sit at step 16, and every baking, unmolding and glazing step was
# cut. A technique claim about a later step would have been unverifiable, which
# is the exact defect this block exists to close.
#
# 8,000 clears the longest real recipe with headroom. The judge runs on a
# frontier model a handful of times a week, so ~2k tokens there is cheap next to
# shipping an episode that discusses a technique the recipe does not use.
JUDGE_METHOD_MAX = 8000


def _build_recipe_context(recipe_data: dict | None) -> str:
    """One-line recipe summary for dialogue + judge prompts.

    Light by design — title + category + the recipe's own one-line description.
    Heavier injection causes characters to recite recipe details instead of
    holding a real conversation. Empty string if recipe_data is missing
    or shapeless (e.g., Monday before the baker has run, or test runs
    that skip the baker).

    #7104: this used to carry the first 5 ingredients, which say what is IN the
    dish but nothing about what the finished thing is LIKE. Characters filled the
    gap by inventing texture, and sometimes inverted it — W37's Sunday judge
    caught "the tapioca's shatter" for pao de queijo, a dish whose whole identity
    is chewy and stretchy. The description is written by the baker from the actual
    method, so it states the texture instead of leaving it to be guessed.
    """
    if not isinstance(recipe_data, dict):
        return ""
    title = (recipe_data.get("title") or "").strip()
    if not title:
        return ""
    category = (recipe_data.get("category") or "").strip().lower()
    parts = [f"This week's recipe: {title}"]
    if category:
        parts.append(f"({category})")
    summary = " ".join(parts) + "."
    # The WHOLE description, not its first sentence. Taking sentence one was a
    # bet that the texture lives at the front, and W38 disproved it: sentence
    # one was "wrap classic cinnamon-roll comfort around warm cardamom, orange,
    # and pistachio for a Middle Eastern-inspired twist" (pure marketing) while
    # the discarded sentence two carried "a rich, buttery dough is coiled into
    # each muffin cup... crisp edges, tender centers" - the only texture in the
    # field. Descriptions run two or three sentences; the cap, not a sentence
    # count, is what keeps this from becoming a recitation.
    # #7853: the pitch, not the published description, so the speakers see
    # the same Monday text whether or not Marcus's intro exists yet.
    description = " ".join(recipe_pitch(recipe_data).split())
    if description:
        anchor = description
        if len(anchor) > RECIPE_CONTEXT_ANCHOR_MAX:
            anchor = anchor[:RECIPE_CONTEXT_ANCHOR_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "..."
        summary += f" What it is: {anchor}"

    # The speakers' ingredient boundary (#7441). W39 Tuesday failed the judge
    # three times on technical_credibility: attempt 2 asserted butter for a
    # recipe that uses only olive oil, attempt 3 asserted egg white for a shell
    # of bulgur, beef, onion, herbs and spices. Neither speaker had ever seen
    # the ingredient list — the judge had, with amounts and notes. Tuesday is
    # the recipe-development day, so that asymmetry made technical_credibility
    # unsatisfiable by construction on the one day it matters most (#7079 is
    # the same defect class).
    #
    # This is not a revert of #7104. That change dropped a truncated
    # first-five-ingredients list because it said what was IN the dish and
    # nothing about what the finished thing was LIKE; the description above
    # still carries the texture. Names are back for factual grounding only.
    names = ingredient_names(recipe_data)
    if names:
        joined = ", ".join(names)
        complete = True
        if len(joined) > RECIPE_CONTEXT_INGREDIENTS_MAX:
            kept: list[str] = []
            used = 0
            for name in names:
                cost = len(name) + (2 if kept else 0)
                if used + cost > RECIPE_CONTEXT_INGREDIENTS_MAX:
                    break
                kept.append(name)
                used += cost
            joined = ", ".join(kept)
            complete = False
        # A first name larger than the cap leaves no truthful list to show.
        # Keep the title/description anchor intact instead of appending an
        # empty "Some listed" boundary.
        if not joined:
            return summary
        if complete:
            summary += (
                f" Listed ingredient names (amounts, optionality and substitution notes omitted): {joined}. "
                f"Ground factual ingredient claims in these names without assuming every item is required. "
                f"Other ingredients may be discussed as proposals, but do not assert they are in this recipe."
            )
        else:
            summary += (
                f" Some listed ingredient names (amounts, optionality and substitution notes omitted): {joined}. "
                f"This list is incomplete; do not infer that an unlisted ingredient is absent. "
                f"Ground factual ingredient claims in these names without assuming every item is required. "
                f"Other ingredients may be discussed as proposals, but do not assert they are in this recipe."
            )
    return summary


def _fit_method(steps: list[str], budget: int) -> str:
    """Render numbered steps within `budget`, dropping from the MIDDLE if needed.

    A raw character cut removes every late step, and late steps are where baking,
    unmolding and finishing live - exactly the claims a judge needs to check. So
    an over-budget method keeps both ends and says plainly which steps are gone,
    rather than silently ending mid-sentence at step 16.
    """
    numbered = [f"{i}. {t}" for i, t in enumerate(steps, 1)]
    whole = " ".join(numbered)
    if len(whole) <= budget:
        return whole

    def _marker(first: int, last: int) -> str:
        return (
            f"[... steps {first}-{last} omitted for length. Their absence is NOT "
            f"evidence a technique is missing from the recipe - do not treat it as a "
            f"contradiction ...]"
        )

    # Reserve the marker's ACTUAL length, not a guessed 80. The marker grew and
    # the reserve did not, so an 8,000-char budget produced 8,010.
    reserve = len(_marker(len(numbered), len(numbered))) + 2

    head: list[str] = []
    tail: list[str] = []
    head_len = tail_len = 0
    lo, hi = 0, len(numbered) - 1
    took_head = took_tail = True
    # Grow from both ends. A single step too large to fit must not stop the
    # other end from being tried - one 8,000-char opening step used to consume
    # the whole budget check and return nothing but the marker, dropping the
    # "Cool and serve" tail this function promises to keep.
    while lo <= hi and (took_head or took_tail):
        took_head = took_tail = False
        if head_len <= tail_len and lo <= hi:
            nxt = numbered[lo]
            if head_len + tail_len + len(nxt) + reserve <= budget:
                head.append(nxt); head_len += len(nxt) + 1; lo += 1
                took_head = True
        if lo <= hi and (not took_head or tail_len < head_len):
            nxt = numbered[hi]
            if head_len + tail_len + len(nxt) + reserve <= budget:
                tail.insert(0, nxt); tail_len += len(nxt) + 1; hi -= 1
                took_tail = True

    if lo > hi:
        return " ".join(head + tail)
    return " ".join(head + [_marker(lo + 1, hi + 1)] + tail)

def _build_judge_recipe_facts(recipe_data: dict | None) -> str:
    """Ground truth about the dish for the JUDGE, independent of what speakers saw.

    #7104 second pass. The judge used to receive exactly the same abbreviated
    anchor the characters did, and no instructions at all - so it had no way to
    tell that W38's accepted Tuesday discussed lamination, butter staying in
    sheets, second folds and refrigerated rests for a recipe that kneads soft
    butter into yeast dough and rolls it up once. There are no folds in it. A
    rejected attempt had flagged that same mismatch; the accepted one scored
    technical_credibility 4 because the evidence needed to catch it was absent
    from the judge's prompt.

    The speaker-facing context stays deliberately light. This does not.
    """
    if not isinstance(recipe_data, dict) or not recipe_data:
        return ""
    title = (recipe_data.get("title") or "").strip()
    if not title:
        return ""

    lines = [f"RECIPE GROUND TRUTH - {title}"]
    category = (recipe_data.get("category") or "").strip()
    cuisine = (recipe_data.get("cuisine") or "").strip()
    if category:
        lines.append(f"Category: {category}" + (f" | Cuisine: {cuisine}" if cuisine else ""))
    # #7853: the pitch, held constant for the judge (see the speakers' anchor).
    description = " ".join(recipe_pitch(recipe_data).split())
    if description:
        lines.append(f"Description: {description}")

    # Amount and notes, not just the name. Dropping them made "1 tbsp melted
    # butter" and "2 cups cold, cubed butter" produce identical judge facts
    # whenever the method did not repeat the detail - so a ratio or technique
    # claim ("the butter's breaking through instead of staying in sheets") was
    # unverifiable for exactly the recipes where it matters most.
    items: list[str] = []
    for ing in (recipe_data.get("ingredients") or []):
        if isinstance(ing, dict):
            part = " ".join(
                str(ing.get(k) or "").strip()
                for k in ("amount", "item")
                if str(ing.get(k) or "").strip()
            )
            notes = str(ing.get("notes") or "").strip()
            if notes:
                part = f"{part} ({notes})" if part else notes
        else:
            part = str(ing).strip()
        if part:
            items.append(part)
    if items:
        lines.append("Ingredients: " + "; ".join(items))

    steps = [
        " ".join(str(step).split())
        for step in (recipe_data.get("instructions") or [])
        if str(step).strip()
    ]
    if steps:
        lines.append("Method: " + _fit_method(steps, JUDGE_METHOD_MAX))

    lines.append(
        "Judge technique claims against the Method above. If a speaker ASSERTS as "
        "fact that this dish uses a technique, texture or step the recipe does not "
        "actually use, that is a technical_credibility failure - say which claim and "
        "which step contradicts it. Do NOT penalise a technique that is raised as a "
        "proposal, a rejected alternative, a comparison to another dish, or a "
        "hypothetical ('we could deep-fry these, but the pan gets there cleaner') - "
        "debating technique and substitutions is the point of the midweek stages. "
        "(The example is deliberately a technique no stored recipe uses: an example "
        "naming a real regression case would prime you to accept that very claim.)"
    )
    return "\n".join(lines)


def _generate_dialogue(
    stage: str,
    concept: str,
    image_paths: list[str] | None = None,
    photography_context: dict | None = None,
    model: str | None = None,
    injected_event: str | None = None,
    recipe_context: str | None = None,
) -> list[dict]:
    """Run dialogue simulator for a single stage. Non-fatal on failure.

    Args:
        model: Override dialogue model. Pass from API request body.
               Defaults to config.dialogue_model (DIALOGUE_MODEL env / Doppler).
        injected_event: Narrative event injected into every character's prompt.
        recipe_context: One-line recipe summary built by _build_recipe_context.
                        Anchors dialogue to the actual dish so characters don't
                        drift into pastry/glaze/sugar talk for a savory recipe.
    """
    use_model = model or config.dialogue_model
    try:
        run_simulation = _get_run_simulation()
        result = run_simulation(
            concept=concept,
            default_model=use_model,
            run_index=1,
            stage_only=stage,
            injected_event=injected_event,
            ticks_per_day=0,
            mode="openai",
            prompt_style="scene",
            character_models=None,
            image_paths=image_paths or [],
            photography_context=photography_context,
            recipe_context=recipe_context,
        )
        return result.get("messages", [])
    # governance: allow-silent SF002: both callers treat [] as a failure; _generate_and_judge_dialogue and execute_cron_stage_stub raise on an empty dialogue
    except Exception as e:
        # TRIAGE (#6856): non-fatal HERE, fail-closed in the callers.
        # Both callers treat an empty list as a hard failure:
        # _generate_and_judge_dialogue (the cron path) raises a stage failure,
        # and execute_cron_stage_stub (admin simulation) raises before it can
        # save a "complete" stage with no dialogue. Do NOT "fix" either path
        # by swallowing it further down.
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Dialogue generation FAILED for stage={stage}: {type(e).__name__}: {e}\n{tb}")
        return []


DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# #6861 - the judge used to be a boolean ("Respond with EXACTLY one line: PASS
# or FAIL"). Nothing accumulated, so nothing could trend, and its checklist
# never asked whether the expected characters showed up or whether anyone
# was actually talking TO anyone else - the two failures Erik reported
# (Julian/Devon absent from a photography day; dialogue reading as "sound
# bites... put next to each other"). This version scores 8 dimensions,
# still resolves to a PASS/FAIL gate, and fails closed on its own output
# instead of ever defaulting to PASS (this is the publish gate).
_JUDGE_SYSTEM_PROMPT = (
    "You are a senior editorial judge for a food content site. "
    "6 characters (Margaret, Steph, Julian, Marcus, Devon, Ria) collaborate on a muffin-tin "
    "recipe each week. Not everyone appears every day - the EXPECTED CAST FOR TODAY line in "
    "the prompt below tells you who is supposed to be in today's scene.\n\n"
) + CHARACTER_RULES + (
    "AUTOMATIC FAIL, regardless of the scores below:\n"
    "1. RECIPE FIDELITY: When a 'This week's recipe:' line is provided, the dialogue\n"
    "   must stay anchored to that dish. FAIL if characters discuss techniques or\n"
    "   ingredients that contradict it (e.g., 'brown butter' or 'glaze' for a savory\n"
    "   sausage-and-egg recipe; 'pastry ratios' for a hash-brown nest).\n"
    "2. HALLUCINATIONS: Wrong ingredients/details not matching the recipe or concept.\n"
    "3. CHARACTER BREAKS: Someone wildly out of character.\n"
    "4. CONTINUITY: A reference to a previous day that is not accurate.\n\n"
    "SCORE EACH DIMENSION 1-5 (1 = fails badly, 5 = excellent):\n"
    "- title_fidelity: does the talk stay anchored to the named dish/hero ingredient,\n"
    "  or does it wander into an unrelated tangent (e.g. a salmon dish disappearing\n"
    "  into a rice essay)?\n"
    "- arc_resolution: does a problem a character raises actually get resolved, not\n"
    "  reframed away or dropped?\n"
    "- voice_distinctiveness: are the characters who spoke today separable blind, each\n"
    "  sounding like themselves and nobody else?\n"
    "- technical_credibility: would a real cook believe the food science here?\n"
    "- natural_progression: does the conversation build, or do characters repeat\n"
    "  themselves and agree in circles?\n"
    "- promise_delivery: does the dialogue promise something (a technique, a visual, a\n"
    "  result) that the recipe or images plausibly can't deliver?\n"
    "- turn_taking: do lines actually respond to the one before them - addressing,\n"
    "  answering, questioning, pushing back - or does each message read as a polished\n"
    "  monologue stacked next to the last one, sound bites rather than a conversation?\n"
    "- cast_coverage: EXPECTED CAST FOR TODAY is given in the prompt below. Score 5\n"
    "  only if everyone on that list actually spoke and contributed something\n"
    "  substantive; score low if someone on the list is silent, or a no-show in a\n"
    "  scene that is supposed to be about their job (e.g. a photography discussion\n"
    "  with no photographer).\n\n"
    "Respond with ONLY a JSON object - no markdown fences, no prose before or after it:\n"
    '{"scores": {"title_fidelity": 1-5, "arc_resolution": 1-5, "voice_distinctiveness": 1-5, '
    '"technical_credibility": 1-5, "natural_progression": 1-5, "promise_delivery": 1-5, '
    '"turn_taking": 1-5, "cast_coverage": 1-5}, "verdict": "PASS" or "FAIL", '
    '"weakest": ["<1-3 lowest-scoring dimension names>"], "reason": "<one sentence>"}\n'
    "FAIL if any automatic-fail condition above applies, or if the scores overall don't "
    "support shipping this to readers. PASS only when the conversation is genuinely "
    "ready to publish."
)


def _parse_judge_json(raw: str) -> dict | None:
    """Extract and parse the judge's structured verdict.

    Slices from the first '{' to the last '}' so a stray markdown fence or a
    sentence of preamble the model adds despite instructions doesn't break
    parsing. Returns None on any failure - callers retry once, then fail
    closed (#6861: this is the publish gate, never default to PASS).
    """
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:  # governance: allow-silent SF002: None means unparseable verdict; _judge_dialogue retries once then fails closed, never defaulting to PASS
        return None
    return parsed if isinstance(parsed, dict) else None


def _judge_verdict_is_malformed(parsed: dict) -> bool:
    """True when JSON parsed cleanly but never actually rendered a verdict.

    #7405: a response like ``{"scores": {"title_fidelity": 5}}`` with no
    ``verdict`` key parses fine, so `passed = ... == "PASS"` silently reads
    it as a scored FAIL - the judge never said FAIL, it said nothing. Same
    failure shape for an empty string or an unrecognized value ("MAYBE") - a
    plain parse failure already returns None from `_parse_judge_json` and
    never reaches here. Any of these must be treated exactly like
    unparseable output by the caller, never as a judged PASS or FAIL.

    Deliberately does NOT also require `scores` to be non-empty or to carry
    specific dimensions: several already-shipped, already-tested call sites
    (e.g. judge-prompt wiring tests) mock a valid PASS/FAIL verdict with an
    empty or partial `scores` dict, and that is a real, judged verdict, not
    a malformed one. Widening this check to scores content would silently
    fail those closed too.
    """
    verdict_raw = str(parsed.get("verdict") or "").strip().upper()
    return verdict_raw not in ("PASS", "FAIL")


def _judge_dialogue(
    concept: str,
    stage: str,
    dialogue: list[dict],
    episode: dict,
    recipe_context: str | None = None,
    recipe_facts: str | None = None,
    judge_model: str | None = None,
) -> tuple[bool, str]:
    """Judge today's dialogue with growing context from previous days.

    Returns (passed: bool, verdict: str) — kept as a 2-tuple for backward
    compatibility; several tests and every call site unpack exactly this.
    The full structured score (#6861) is written onto `episode` instead —
    judge_scores / judge_weakest / judge_reason, each keyed by `stage` —
    mirroring how `_score_dialogue_qa` already stashes qa_scores on the
    episode rather than widening this function's return signature.

    When recipe_context is supplied, the judge enforces recipe fidelity —
    flagging dialogue that drifts off the actual dish (e.g., characters
    debating "butter-to-sugar ratios" for a savory hash-brown recipe).

    ``judge_model`` is an optional override for lab callers that resolve
    their own judge route (for example OpenRouter). Production callers omit
    it and keep the direct-Anthropic default from ``config.judge_model``.
    """
    judge_model = judge_model or config.judge_model

    # Build context from previous days
    previous_context = []
    for day in DAY_ORDER:
        if day == stage:
            break
        day_dialogue = episode.get("stages", {}).get(day, {}).get("dialogue", [])
        if day_dialogue:
            lines = []
            for m in day_dialogue:
                name = m.get("character", "?").split()[0]
                lines.append(f"{name}: {' '.join((m.get('message') or '').split())}")
            previous_context.append(f"=== {day.upper()} ===\n" + "\n".join(lines))

    # Format today's dialogue
    today_lines = []
    for m in dialogue:
        name = m.get("character", "?").split()[0]
        today_lines.append(f"{name}: {' '.join((m.get('message') or '').split())}")

    context_section = ""
    if previous_context:
        context_section = "PREVIOUS DAYS:\n" + "\n\n".join(previous_context) + "\n\n---\n\n"

    recipe_section = f"{recipe_context}\n\n" if recipe_context else ""
    # #7104: ground truth the speakers never saw, so the judge can check a
    # technique claim against the actual method instead of against the same
    # abbreviated blurb that produced the claim.
    facts_section = f"{recipe_facts}\n\n" if recipe_facts else ""

    # Expected cast for today, so cast_coverage is judgeable (#6861/#6832).
    # Lazy import mirrors _get_run_simulation()'s pattern above — the judge
    # path only needs one function out of the simulator module.
    from scripts.simulate_dialogue_week import participants_for_day
    roster_line = f"Expected cast for today ({stage}): {', '.join(participants_for_day(stage))}\n"

    prompt = (
        f"Recipe concept: {concept}\n"
        f"{recipe_section}"
        f"{facts_section}"
        f"{roster_line}"
        f"{context_section}"
        f"TODAY IS {stage.upper()}:\n"
        + "\n".join(today_lines)
        + "\n\nScore this day and return the JSON verdict described in your instructions."
    )

    def _record_judge_meta(scores: dict, weakest: list, reason: str) -> None:
        episode.setdefault("judge_scores", {})[stage] = scores
        episode.setdefault("judge_weakest", {})[stage] = weakest
        episode.setdefault("judge_reason", {})[stage] = reason

    try:
        raw = generate_judge_response(
            prompt=prompt,
            system_prompt=_JUDGE_SYSTEM_PROMPT,
            model=judge_model,
            temperature=0.2,
        ).strip()
    except Exception as e:
        # TRIAGE (#6856): FAILS CLOSED, deliberately. Returning False routes
        # into the retry loop in _generate_and_judge_dialogue, which raises
        # JudgeFailedError + Discord-notifies once retries are exhausted. A
        # provider outage therefore pauses the episode instead of waving
        # unjudged dialogue through (the PR #45 contract).
        logger.error(f"Judge errored for {stage} (treated as FAIL): {type(e).__name__}: {e}")
        # Record EMPTY meta, do not leave the previous attempt's (or the
        # previous run's) numbers standing (#7394). Whatever is on the
        # episode describes a dialogue that is not this one, and callers
        # read these keys to rank attempts and to fill the published stage
        # record. Empty is the honest answer for an attempt nobody judged.
        _record_judge_meta({}, [], f"judge error: {type(e).__name__}")
        return False, f"JUDGE ERROR: {type(e).__name__}: {e}"

    parsed = _parse_judge_json(raw)
    malformed = parsed is not None and _judge_verdict_is_malformed(parsed)
    if parsed is None or malformed:
        # One retry with a stricter reminder — models occasionally wrap the
        # JSON in a markdown fence, add a sentence of preamble, or (#7405)
        # return parseable JSON that omits `verdict` entirely or uses an
        # unrecognized value, despite being told not to.
        retry_prompt = (
            f"{prompt}\n\nCRITICAL: your previous response could not be used as a "
            "verdict (it was either not valid JSON, or was missing a `verdict` "
            "field, or used a `verdict` value other than \"PASS\"/\"FAIL\"). "
            "Return ONLY the JSON object described above, with a `verdict` of "
            "exactly \"PASS\" or \"FAIL\". No markdown fences, no prose before or "
            "after it."
        )
        try:
            raw = generate_judge_response(
                prompt=retry_prompt,
                system_prompt=_JUDGE_SYSTEM_PROMPT,
                model=judge_model,
                temperature=0.1,
            ).strip()
        except Exception as e:
            logger.error(f"Judge errored on retry for {stage} (treated as FAIL): {type(e).__name__}: {e}")
            _record_judge_meta({}, [], f"judge error: {type(e).__name__}")
            return False, f"JUDGE ERROR: {type(e).__name__}: {e}"
        parsed = _parse_judge_json(raw)
        malformed = parsed is not None and _judge_verdict_is_malformed(parsed)

    if parsed is None:
        # FAIL CLOSED (#6861). This is the publish gate — an unparseable
        # verdict must never be waved through as a PASS by default.
        reason = "judge output unparseable"
        _record_judge_meta({}, [], reason)
        logger.error(f"Judge output unparseable for {stage} after retry: {raw[:200]!r}")
        return False, f"FAIL - {reason}"

    if malformed:
        # FAIL CLOSED (#7405). The response parsed as JSON but never actually
        # rendered a verdict (`verdict` missing, empty, or an unrecognized
        # value) — semantically the same as unparseable output, so it gets
        # the exact same contract: empty meta, fail closed, and it is NEVER
        # recorded as a judged FAIL. This is what keeps a malformed response
        # out of the advisory-gate "attempt is eligible to publish" path and
        # out of a FAIL alert the judge never actually issued.
        reason = "judge verdict malformed"
        _record_judge_meta({}, [], reason)
        logger.error(f"Judge verdict malformed for {stage} after retry: {parsed!r}")
        return False, f"FAIL - {reason}"

    scores = parsed.get("scores")
    scores = scores if isinstance(scores, dict) else {}
    weakest = parsed.get("weakest")
    weakest = weakest if isinstance(weakest, list) else ([str(weakest)] if weakest else [])
    reason = str(parsed.get("reason") or "")
    passed = str(parsed.get("verdict") or "").strip().upper() == "PASS"

    _record_judge_meta(scores, weakest, reason)

    verdict = f"{'PASS' if passed else 'FAIL'}" + (f" - {reason}" if reason else "")
    if not passed and weakest:
        verdict += f" | weakest: {', '.join(str(w) for w in weakest[:3])}"
    if not passed and scores:
        verdict += f" | scores: {scores}"
    logger.info(f"Judge verdict for {stage}: {verdict[:300]}")
    return passed, verdict


def _judge_meta_fields(episode: dict, stage: str) -> dict:
    """Structured judge output for `stage`, for spreading into a stage record
    next to judge_verdict (#6861). `_judge_dialogue` writes these onto the
    episode as a side effect (mirroring qa_scores) since its return stays a
    2-tuple for compatibility; empty defaults when the judge was mocked out
    or never ran (test call sites that patch `_generate_and_judge_dialogue`
    wholesale never populate them, which is fine).
    """
    return {
        "judge_scores": episode.get("judge_scores", {}).get(stage, {}),
        "judge_weakest": episode.get("judge_weakest", {}).get(stage, []),
        "judge_reason": episode.get("judge_reason", {}).get(stage, ""),
    }


def _announce_advisory_publication(episode_id: str, episode: dict, stage: str, concept: str) -> None:
    """Send the advisory alert once the page it describes actually exists,
    and keep retrying on later Sunday invocations until it is delivered
    (#7403).

    _generate_and_judge_dialogue records that it SELECTED a below-bar
    dialogue; only the publish path knows whether the recipe went live. The
    publish path sets ``published`` and ``announce_pending`` in the same
    save, before the static source handoff. This function sends only while
    ``announce_pending`` is set AND the handoff has reached ``source_ready``
    (the reader pages were written), so it never announces a page that was
    not written. It is called from the publish path and again from
    cron_sunday's already-published fast path, so a crash between the
    publish save and the alert is retried on the next invocation.

    Guarantees, stated exactly:
    - A delivery that ``notify_judge_advisory`` reports as failed (it returns
      False without raising when every channel is down) leaves the record
      pending, so the next invocation tries again. Only a confirmed delivery
      clears it.
    - Records published before ``announce_pending`` existed never carry it,
      so this never re-sends an advisory that older code already announced.
    - Sequential invocations send at most once per delivery confirmed and
      saved. If the send succeeds but saving the cleared flag fails, the
      error is logged and the stage is not failed (the recipe did publish);
      the next invocation may send one duplicate. Two invocations running
      at the same moment can both send: there is no storage-level lock, so
      this is at-least-once under concurrency, not exactly-once.
    """
    record = episode.get("judge_advisory", {}).get(stage)
    if not record or not record.get("published") or not record.get("announce_pending"):
        return
    if _static_deploy_state(episode).get("status") != "source_ready":
        # Pages not confirmed written; the handoff raises its own alert on
        # failure. Stay pending so a later invocation announces after it.
        return
    delivered = notify_judge_advisory(
        concept=concept,
        stage=stage,
        verdict=record.get("verdict", ""),
        episode_id=episode_id,
        attempts=record.get("attempts", 0),
        scores=record.get("scores") or {},
        weakest=record.get("weakest") or [],
    )
    if not delivered:
        logger.error(
            f"Advisory alert for {episode_id}/{stage} was not delivered on any channel; "
            "left pending for the next Sunday invocation"
        )
        return
    # Mark a copy and save it; apply the marker to the live episode only once
    # the save succeeds. Cloud storage caches the loaded episode object by
    # reference, so marking it first would let a warm-process retry read
    # "announced" from memory while storage still says the alert is owed.
    announced_at = datetime.now(timezone.utc).isoformat()
    updated = copy.deepcopy(episode)
    updated["judge_advisory"][stage]["announce_pending"] = False
    updated["judge_advisory"][stage]["announced_at"] = announced_at
    try:
        storage.save_episode(episode_id, updated)
    # governance: allow-silent SF002: returns None like every path here; the alert was delivered and announce_pending stays True in storage so the next Sunday invocation re-sends
    except Exception as exc:  # noqa: BLE001 - the publish already succeeded
        logger.error(
            f"Advisory alert for {episode_id}/{stage} was delivered but its announced marker "
            f"could not be saved ({type(exc).__name__}: {exc}); it stays owed, so a later "
            "invocation may send one duplicate"
        )
        return
    record["announce_pending"] = False
    record["announced_at"] = announced_at


class JudgeFailedError(Exception):
    """Raised when dialogue fails judge review after all retries."""

    # notify_judge_failure has already fired by the time this is raised, with
    # far better detail than a generic stage alert. Without this flag the same
    # event pings Discord twice — once as a judge failure, once as a stage
    # failure — on what is the most common real failure mode there is.
    already_notified = True

    def __init__(self, stage: str, verdict: str, attempts: int):
        self.stage = stage
        self.verdict = verdict
        self.attempts = attempts
        super().__init__(f"Judge failed {stage} after {attempts} attempts: {verdict}")


def _score_dialogue_qa(
    dialogue: list[dict],
    stage: str,
    concept: str,
) -> dict:
    """Run structural QA scoring on dialogue. Returns score dict.

    Uses the same scoring system from testing (simulate_dialogue_week.py).
    Non-fatal — returns empty dict on failure.
    """
    try:
        from backend.utils.qa_scoring import Message, load_personas, score_quality

        personas = load_personas()
        messages = [
            Message(
                day=stage,
                stage=stage,
                character=msg.get("character", "Unknown"),
                message=msg.get("message", ""),
                timestamp=msg.get("timestamp", ""),
                model=msg.get("model", "unknown"),
            )
            for msg in dialogue
        ]
        result = score_quality(messages, personas, concept=concept)
        return {"score": result.get("score", 0), "details": result}
    # governance: allow-silent SF002: descriptive metric, not a gate; both callers skip writing qa_scores when the result is empty and the judge gates publication
    except Exception as e:
        # TRIAGE (#6856): GENUINELY NON-FATAL, logged. QA scoring is a
        # descriptive metric written alongside the dialogue, not a gate — the
        # judge above is the gate. Losing a score costs a data point in the
        # tuning log; it cannot ship anything wrong to readers.
        logger.warning(f"QA scoring failed (non-fatal, metric only): {type(e).__name__}: {e}")
        return {}


def _generate_and_judge_dialogue(
    stage: str,
    concept: str,
    episode: dict,
    model: str | None = None,
    image_paths: list[str] | None = None,
    photography_context: dict | None = None,
    max_retries: int = 2,
    injected_event: str | None = None,
    recipe_data: dict | None = None,
    advisory: bool = False,
) -> tuple[list[dict], str]:
    """Generate dialogue and run judge. Retry on FAIL up to max_retries.

    Returns (dialogue, verdict_str).
    Raises JudgeFailedError if all retries exhausted — caller should
    save episode as judge_failed and NOT publish.

    Set advisory=True to score without gating: an exhausted retry loop then
    returns the best-scoring rejected attempt instead of raising. Sunday uses
    it (#7394). The dialogue is an accompaniment to the recipe, and until now
    a weak conversation could withhold a finished, QA-passed recipe from
    readers — which is what lost W38 entirely. The recipe's own gate,
    _editorial_qa_review, still blocks and is unaffected.

    Pass recipe_data (typically `episode['stages']['monday']['recipe_data']`)
    to anchor both the simulator and the judge to the actual dish. Without
    it, characters drift off-recipe and the judge can only enforce internal
    consistency.
    """
    verdict = ""
    dialogue: list[dict] = []
    total_attempts = 1 + max_retries
    recipe_context = _build_recipe_context(recipe_data)
    recipe_facts = _build_judge_recipe_facts(recipe_data)
    # Every attempt the judge rejects, kept so a failure leaves evidence
    # behind (#7100). See the write below for why this is not on the stage.
    rejected: list[dict] = []

    for attempt in range(total_attempts):
        dialogue = _generate_dialogue(
            stage, concept,
            image_paths=image_paths,
            photography_context=photography_context,
            model=model,
            injected_event=injected_event,
            recipe_context=recipe_context or None,
        )
        if not dialogue:
            # FAIL CLOSED (#6856). This used to return the sentinel verdict
            # "NO DIALOGUE GENERATED", which the handlers stored verbatim and
            # then marked the stage `complete` — a day with zero turns, green
            # status and no alert. The dialogue IS the product; an empty one
            # is a stage failure, so raise and let _run_stage record it and
            # notify.
            raise RuntimeError(
                f"Dialogue generation produced no messages for {stage}. "
                f"See the DIALOGUE GENERATION FAILED log line for the cause."
            )

        passed, verdict = _judge_dialogue(
            concept, stage, dialogue, episode,
            recipe_context=recipe_context or None,
            recipe_facts=recipe_facts or None,
        )
        if passed:
            # This stage cleared the judge, so any advisory record from an
            # earlier abandoned run is now a lie. A Sunday that failed the
            # judge and then failed editorial QA persists
            # judge_advisory["sunday"] with published=False; the re-fire
            # after the recipe is fixed would otherwise inherit it, flip it
            # to published=True and email the discarded run's verdict and
            # scores for a week that actually passed. Unlike
            # rejected_dialogues, which is inert evidence, this record is
            # read to make a claim — so it has to be cleared, not kept.
            episode.get("judge_advisory", {}).pop(stage, None)
            # Run QA scoring on the accepted dialogue
            qa_scores = _score_dialogue_qa(dialogue, stage, concept)
            if qa_scores:
                episode.setdefault("qa_scores", {})[stage] = qa_scores
                logger.info(f"QA score for {stage}: {qa_scores.get('score', '?')}/100")
            return dialogue, verdict

        # _judge_dialogue writes this attempt's structured score onto the
        # episode, keyed by stage, and the NEXT attempt overwrites it. Read it
        # here, while it still belongs to the dialogue we are about to discard.
        rejected.append(
            {
                "attempt": attempt + 1,
                "dialogue": dialogue,
                "verdict": verdict,
                "scores": episode.get("judge_scores", {}).get(stage, {}),
                "weakest": episode.get("judge_weakest", {}).get(stage, []),
                "reason": episode.get("judge_reason", {}).get(stage, ""),
            }
        )
        logger.warning(f"Judge FAILED {stage} attempt {attempt + 1}/{total_attempts}: {verdict[:200]}")

    # Exhausted retries — keep the evidence, notify Erik, pause the episode.
    #
    # This goes on the EPISODE, not the stage, for two independent reasons
    # (#7100):
    #
    #   1. _save_stage_failure does `ep["stages"][stage] = {...}` — a blind
    #      overwrite. Anything written to the stage here is destroyed moments
    #      later. Episode-level keys survive, which is how judge_scores made it
    #      out of W38's failure on 2026-09-14 while three dialogues did not.
    #   2. The key must not be called `dialogue`. episode_renderer reads
    #      `stage["dialogue"]` to decide reader-facing output, so a rejected
    #      attempt under that name would publish failed dialogue to the site.
    #
    # Without this, the only surviving evidence of a judge failure is one
    # sentence of verdict, which is not enough to tune a prompt against.
    best = None
    if rejected:
        # Only attempts the judge actually scored can win. An attempt whose
        # judge call errored or came back unparseable carries {} — it was
        # never assessed, so it must not out-rank a judged one on a sum of
        # nothing, and under the advisory gate it must not be publishable
        # at all (see below).
        scored = [i for i, r in enumerate(rejected) if r.get("scores")]
        best = max(
            scored or range(len(rejected)),
            key=lambda i: sum(
                v for v in (rejected[i].get("scores") or {}).values()
                if isinstance(v, (int, float))
            ),
        )
        rejected[best]["best_of_run"] = True
        episode.setdefault("rejected_dialogues", {})[stage] = rejected

    episode_id = episode.get("episode_id", "unknown")

    if advisory:
        # Ship the best attempt rather than the week. `best` is None only if
        # the loop never ran, which max_retries >= 0 makes impossible — but a
        # publish path must not depend on that, so fall back to the dialogue
        # in hand and say so.
        # "The judge said it is weak" and "the judge never spoke" are
        # different states, and only the first one may publish. The comment
        # on the judge's own error path states the contract from PR #45: a
        # provider outage pauses the episode rather than waving unjudged
        # dialogue through. Advisory relaxes the verdict, never the
        # requirement that there BE one — otherwise an Anthropic outage
        # publishes a conversation nobody ever looked at.
        chosen = rejected[best] if best is not None else {}
        if not chosen.get("scores"):
            chosen = {}
        dialogue = chosen.get("dialogue") or []
        if not dialogue:
            # Nothing to publish is a different failure from something weak,
            # so this one still fails closed. Alert BEFORE raising:
            # JudgeFailedError.already_notified is True, which makes
            # _save_stage_failure skip its own ping on the promise that the
            # raiser already sent a better one. Raising here without alerting
            # would make Sunday fail in total silence — the exact shape of
            # the incident this advisory gate exists to prevent.
            notify_judge_failure(
                concept=concept,
                stage=stage,
                verdict=verdict or "no dialogue survived the judge loop",
                episode_id=episode_id,
                attempts=total_attempts,
            )
            logger.error(
                f"Advisory gate cannot publish {stage}: no attempt was "
                f"actually judged (all {total_attempts} errored or were "
                f"unparseable). Failing closed."
            )
            raise JudgeFailedError(stage=stage, verdict=verdict, attempts=total_attempts)
        scores = chosen.get("scores") or {}
        weakest = chosen.get("weakest") or []
        # Restore the chosen attempt's own scores. The loop leaves the LAST
        # attempt's scores on the episode, and the attempt we publish is
        # usually not the last one, so without this the site's stage record
        # would carry another dialogue's numbers. Written unconditionally:
        # if the published attempt has no parsed scores, empty is the honest
        # value — keeping a different attempt's numbers is the bug.
        episode.setdefault("judge_scores", {})[stage] = scores
        episode.setdefault("judge_weakest", {})[stage] = weakest
        episode.setdefault("judge_reason", {})[stage] = chosen.get("reason", "")
        # SELECTED, not published. Sunday still has to clear editorial QA and
        # the publish itself, and this helper cannot know whether either
        # succeeds. Claiming publication here would write
        # published_below_bar=True onto an episode whose QA then rejected the
        # recipe, and email Erik that a page is live when it is not. The
        # handler flips `published` and sends the alert once the page exists.
        episode.setdefault("judge_advisory", {})[stage] = {
            "selected_below_bar": True,
            "published": False,
            "attempts": total_attempts,
            "attempt_selected": chosen.get("attempt"),
            "verdict": chosen.get("verdict") or verdict,
            "scores": scores,
            "weakest": weakest,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        episode.setdefault("events", []).append(
            f"{stage}: judge FAILED all {total_attempts} attempts — "
            f"selected the best attempt anyway (advisory gate)"
        )
        qa_scores = _score_dialogue_qa(dialogue, stage, concept)
        if qa_scores:
            episode.setdefault("qa_scores", {})[stage] = qa_scores
        logger.error(
            f"Judge failed all {total_attempts} attempts for {stage}; advisory "
            f"gate — selected attempt {chosen.get('attempt')} for publication."
        )
        return dialogue, chosen.get("verdict") or verdict

    notify_judge_failure(
        concept=concept,
        stage=stage,
        verdict=verdict,
        episode_id=episode_id,
        attempts=total_attempts,
    )
    logger.error(f"Judge failed all {total_attempts} attempts for {stage}. Episode paused.")
    raise JudgeFailedError(stage=stage, verdict=verdict, attempts=total_attempts)


class StageRequest(BaseModel):
    episode_id: Optional[str] = None   # defaults to current ISO week
    concept: Optional[str] = None      # defaults to stored or generic
    # Breakfast | Savory | Sweet | Party. Only meaningful alongside an explicit
    # concept: it names the shelf the operator chose the dish for (#6858).
    target_category: Optional[str] = None
    model: Optional[str] = None        # override dialogue model (e.g. "openai/gpt-5.1")
    test: bool = False                 # test mode: saves to test/ prefix in blob
    force: bool = False                # skip day-of-week check (manual catch-ups only)
    # Narrative problem handed to every character for this stage (#7352). The
    # parameter has been plumbed end to end since W15 but nothing could supply
    # one, so every production prompt has carried "Injected event: none" and
    # the back half of the week had nothing left to decide. Operator-supplied
    # only: the cron sends no body, so unattended runs still inject nothing.
    # Automatic per-day events are a separate, gated question (#6967).
    injected_event: Optional[str] = None


async def _parse_body(request: Request) -> StageRequest:
    """Parse JSON body from POST, return defaults for GET (Vercel cron sends GET)."""
    if request.method != "POST":
        return StageRequest()

    # A body-less POST is a legitimate "run this stage with defaults" call and
    # keeps working. A body that IS present but malformed used to be swallowed
    # into the same defaults (#6856) — so a typo'd episode_id or a misspelled
    # "concept" key silently ran auto-pick against the current week instead.
    # That is now a 400.
    raw = await request.body()
    if not raw.strip():
        return StageRequest()
    try:
        body = StageRequest(**json.loads(raw))
        if body.target_category is not None and normalize_category(body.target_category) is None:
            raise ValueError(
                f"target_category must be one of {', '.join(VALID_CATEGORIES)}, "
                f"got {body.target_category!r}"
            )
        return body
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed cron request body: {type(e).__name__}: {e}",
        ) from e


_DAY_TO_WEEKDAY = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _verify_day_of_week(stage: str, body: StageRequest) -> None:
    """Reject requests that fire on the wrong day of the week.

    Vercel crons are scheduled per-day, but the handlers don't inherently
    know what day it is. This guard prevents accidental firings (e.g. curl
    testing that triggers real API calls on the wrong day).

    Bypass with force=True in POST body for manual catch-ups.
    Test mode also bypasses (compressed week simulations).
    """
    if body.force or body.test:
        return
    expected = _DAY_TO_WEEKDAY.get(stage)
    if expected is None:
        return
    actual = datetime.now(timezone.utc).weekday()
    if actual != expected:
        actual_name = list(_DAY_TO_WEEKDAY.keys())[actual]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stage '{stage}' can only run on {stage.title()} (today is {actual_name.title()}). Use force=true to override.",
        )


def _test_mode_scope(body: StageRequest):
    """Context manager that scopes the storage prefix to the handler body.

    Wraps storage.prefix_scope with the test-mode decision so handlers say:

        with _test_mode_scope(body):
            ... handler body ...

    Structural guarantee: prefix is restored on exit, even if the handler
    raises. Prevents test-mode prefix contamination across warm Lambda
    invocations (RUNBOOK Incident 1, #5911).
    """
    return storage.prefix_scope("test/" if body.test else "")


_STATIC_DEPLOY_STATE_KEY = "static_deploy"


def _static_deploy_state(ep: dict) -> dict:
    state = ep.get(_STATIC_DEPLOY_STATE_KEY, {})
    return state if isinstance(state, dict) else {}


def _set_static_deploy_state(
    ep: dict,
    status_value: str,
    *,
    phase: str | None = None,
    error: str | None = None,
) -> None:
    state = {
        "status": status_value,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if phase:
        state["phase"] = phase
    if error:
        state["error"] = error
    ep[_STATIC_DEPLOY_STATE_KEY] = state


def _persist_static_deploy_failure(
    episode_id: str,
    ep: dict,
    *,
    phase: str,
    error: str,
) -> None:
    """Record a retryable handoff failure without masking the root error."""
    _set_static_deploy_state(ep, "failed", phase=phase, error=error)
    try:
        storage.save_episode(episode_id, ep)
    except Exception as persist_error:
        # TRIAGE (#6856): NON-FATAL last resort. We are already inside a
        # failure path; the caller re-raises the original error, so swallowing
        # this one preserves the root cause instead of masking it with a blob
        # write error. Logged at ERROR because it means the retry marker did
        # not land and the handoff cannot self-heal on the next invocation.
        logger.error(
            "Could not persist static deployment failure for %s "
            "(error_type=%s)",
            episode_id,
            type(persist_error).__name__,
        )


def _catalog_contains_episode(ep: dict) -> bool:
    """Return whether the authoritative catalog already contains this recipe."""
    catalog_json = storage.load_page("pages/recipes.json")
    if not catalog_json or not isinstance(catalog_json, str):
        return False
    try:
        catalog = json.loads(catalog_json)
    except json.JSONDecodeError:  # governance: allow-silent SF002: False means "not proven present"; _publish_sunday_sources then raises "Recipe catalog write did not complete" (fails closed)
        return False

    recipe = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})
    title = str(recipe.get("title") or "").strip()
    episode_id = str(ep.get("episode_id") or "")
    if not title or not episode_id:
        return False

    from backend.recipe_model import slugify

    slug = slugify(title)
    for entry in catalog.get("recipes", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("episode_id") == episode_id:
            return True
        # Older catalog entries do not always carry episode_id. A matching
        # slug is still an idempotent success when the catalog writer skipped
        # an already-present recipe.
        if not entry.get("episode_id") and entry.get("slug") == slug:
            return True
    return False


def _publish_sunday_sources(ep: dict) -> None:
    """Write the Sunday Blob sources. Every write here is reader-facing."""
    # /this-week serves this page directly from Blob, so a failed write must
    # fail the publish rather than report success over a page nobody wrote.
    if regenerate_and_upload(ep, strict=True) is None:
        raise RuntimeError("Episode page write did not complete")

    from backend.publishing.episode_renderer import publish_recipe_to_catalog

    catalog_url = publish_recipe_to_catalog(ep)
    if catalog_url is None and not _catalog_contains_episode(ep):
        raise RuntimeError("Recipe catalog write did not complete")

    # This is THE reader page: vercel.json routes /recipes/<slug> at the lambda,
    # which serves this stored file. A swallowed failure here would return
    # {"published": true} while the recipe 404s for every reader, so it raises.
    from backend.publishing.episode_renderer import render_episode_page
    from backend.recipe_model import slugify

    monday = ep.get("stages", {}).get("monday", {})
    recipe_title = monday.get("recipe_data", {}).get("title", "")
    if not recipe_title:
        raise RuntimeError("Sunday publish has no recipe title; cannot write reader page")

    slug = slugify(recipe_title)
    recipe_html = render_episode_page(ep)
    storage.save_page(f"pages/recipes/{slug}/index.html", recipe_html)
    logger.info("Published recipe page at /recipes/%s", slug)


def _complete_static_source_handoff(episode_id: str, ep: dict) -> None:
    """Finish retryable source writes before the manual static deployment."""
    state = _static_deploy_state(ep)
    state_status = state.get("status")
    source_needs_retry = state_status == "pending" or (
        state_status == "failed" and state.get("phase") == "sources"
    )

    if source_needs_retry:
        try:
            _publish_sunday_sources(ep)
        except Exception as exc:
            # TRIAGE (#6856): FAILS CLOSED, and now alerts. It already
            # persisted a retry marker and raised a 500, but _run_stage
            # re-raises HTTPException untouched — so this reader-facing
            # publish failure reached nothing but the Lambda log.
            _persist_static_deploy_failure(
                episode_id,
                ep,
                phase="sources",
                error="Sunday authoritative source write failed",
            )
            notify_pipeline_failure(
                recipe_id=ep.get("recipe_id") or "unknown",
                concept=ep.get("concept") or "unknown",
                stage="sunday (source write)",
                error_message=(
                    f"Sunday publish source write failed for {episode_id}: "
                    f"{type(exc).__name__}: {exc}. The episode is marked "
                    f"published but the reader pages/catalog were NOT written. "
                    f"Re-fire /api/cron/sunday to retry the handoff."
                ),
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Sunday publish source write failed; "
                    "manual static deployment is still pending"
                ),
            ) from exc

        _set_static_deploy_state(ep, "source_ready", phase="manual_deploy")
        storage.save_episode(episode_id, ep)


_PRESERVED_ON_FAILURE = {
    "wednesday": (
        "photography_data", "reshoot_happened", "image_paths", "image_urls",
        "image_status", "confirmed_winner", "photo_review",
    ),
}


def _save_stage_failure(ep: dict, stage: str, error: Exception) -> None:
    """Write a failed-stage marker, persist the episode, and alert.

    The Discord ping is the point (#6856). Before it, a stage that blew up
    wrote {"status": "failed"} into a blob file nobody opens and returned a
    500 to a Vercel cron runner that discards the response. Monday could fail
    on a Monday and the first human signal was Tuesday's 409 — or, if nothing
    downstream tripped, nothing at all.
    """
    prior = ep.setdefault("stages", {}).get(stage)
    failed = {"status": "failed", "error": str(error)}
    if isinstance(prior, dict):
        # Paid photos and their review request survive a later failure in
        # the same stage (#7936): rerunning Wednesday to recover would
        # regenerate every image.
        failed.update({k: prior[k] for k in _PRESERVED_ON_FAILURE.get(stage, ()) if k in prior})
    ep["stages"][stage] = failed
    storage.save_episode(ep["episode_id"], ep)
    if getattr(error, "already_notified", False):
        # The raiser sent a better-targeted alert before raising (see
        # JudgeFailedError). Loud once, not twice.
        return
    notify_pipeline_failure(
        recipe_id=ep.get("recipe_id") or "unknown",
        concept=ep.get("concept") or "unknown",
        stage=stage,
        error_message=(
            f"Stage '{stage}' failed for episode {ep.get('episode_id')}: "
            f"{type(error).__name__}: {error}"
        ),
    )



def _sunday_window_reached(episode_id: str) -> bool:
    """Whether the week's own Sunday cron time has arrived (within the
    tolerance for a cron that fires a little early). A non-ISO id (the
    run_full_week.py harness uses test-<ts>) has no Sunday window, so it never
    announces a week off and its refusal path stays exactly as before."""
    try:
        sunday_due = episode_integrity.stage_deadline(episode_id, "sunday")
    # governance: allow-silent SF002: a non-ISO id is an expected input with no Sunday window; the caller's own refusal (409 + alert, or 400) still runs
    except ValueError:
        return False
    return _utc_now() >= sunday_due - _SUNDAY_NOTE_TOLERANCE


def _monday_recipe_ready(ep: dict) -> bool:
    monday = ep.get("stages", {}).get("monday", {})
    return bool(monday.get("status") == "complete" and monday.get("recipe_data"))


def _note_week_off_without_a_recipe(episode_id: str, ep: dict) -> None:
    """Sunday's window closed on a week whose Monday never produced a recipe:
    the week cannot publish, so say so on the homepage now (Codex, #7630).

    Writes pages/latest.json only, through the writer's own current-week
    gate, and never saves the episode. Never renders the episode page: with no recipe it would publish
    placeholder content (the W24 incident _require_monday_recipe guards).
    Best-effort; the 409 that follows is unchanged. An early forced re-fire
    (before the week's own Sunday cron time) still has time to recover and
    announces nothing.
    """
    if not _sunday_window_reached(episode_id):
        return
    try:
        # Teaser only, on a copy: the episode is not saved. No stage can
        # write this recipe-less week again, and when Monday never ran at
        # all `ep` is an unsaved placeholder that must not become a stored
        # episode (it would change what /this-week serves).
        noted = {**ep, "week_off_note": {"message": WEEK_OFF_MESSAGE, "missed_week": episode_id}}
        upload_latest_json(noted)
    except Exception as exc:  # noqa: BLE001 - best-effort cosmetic note, the 409 still fires
        logger.warning(
            f"week-off note for recipe-less {episode_id} skipped: {type(exc).__name__}: {exc}"
        )


def _require_monday_recipe(ep: dict, stage: str) -> None:
    """Block downstream stages when Monday never produced a recipe.

    Without this gate, Tue-Sun run against the placeholder concept, spending
    dialogue/image API budget on a recipe that doesn't exist and pushing
    placeholder content to the live site (W24, 2026-06-10: Wednesday shot
    55 images for the literal concept "Weekly Muffin Pan Recipe" after
    Monday had hard-failed).
    """
    monday = ep.get("stages", {}).get("monday", {})
    if monday.get("status") == "complete" and monday.get("recipe_data"):
        return
    detail = (
        f"Stage '{stage}' blocked for episode {ep.get('episode_id')!r}: "
        f"monday stage is {monday.get('status', 'missing')!r}, so the week "
        f"has no recipe. Re-fire /api/cron/monday first (pass an explicit "
        f"'concept' in the body if the title validator rejected auto-pick)."
        + (f" Monday error: {monday['error']}" if monday.get("error") else "")
    )
    notify_pipeline_failure(
        recipe_id=ep.get("recipe_id") or "unknown",
        concept=ep.get("concept") or "unknown",
        stage=stage,
        error_message=detail,
    )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@contextmanager
def _run_stage(ep: dict, stage: str):
    """Context manager: saves a failure record and re-raises on any exception.

    Replaces the 7x copy-paste try/except boilerplate in cron handlers:

        with _run_stage(ep, "monday"):
            ... stage logic ...

    On success the caller is responsible for saving the episode.
    On failure, writes {status: failed, error: ...} and re-raises as HTTP 500.
    """
    try:
        yield
    except HTTPException:
        # Explicit HTTP errors are raised by code that has already decided how
        # loud to be (the _require_monday_recipe 409 and the Sunday source-write
        # 500 both notify before raising). Re-raise unchanged.
        raise
    except Exception as e:
        # TRIAGE (#6856): FAILS CLOSED, and _save_stage_failure now alerts.
        _save_stage_failure(ep, stage, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/cron", tags=["cron"])


def _stage_response(stage: str, episode_id: str, concept: str, result: dict) -> dict:
    return {
        "stage": stage,
        "episode_id": episode_id,
        "concept": concept,
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }


# ---------------------------------------------------------------------------
# Monday — Brainstorm / Baker
# ---------------------------------------------------------------------------

@router.api_route("/monday", methods=["GET", "POST"])
async def cron_monday(request: Request):
    _verify_cron_secret(request)
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT, "monday")
      _apply_week_off_note(episode_id, ep)
      # Put the decision on the homepage now, not only at the end of a
      # successful Monday: a stage failure below would otherwise keep the
      # note (or its absence) off the homepage until a later stage ran
      # (#7630). Current week only (the writer's gate); best-effort.
      try:
          upload_latest_json(ep)
      except Exception as exc:  # noqa: BLE001 - cosmetic; Monday must run
          logger.warning(
              f"homepage week-off update skipped for {episode_id}: {type(exc).__name__}: {exc}"
          )

      # W15 narrative injection: characters discover the "Party" category
      injected_event: str | None = None
      if episode_id == "2026-W15":
          injected_event = (
              "The team has been making mostly savory and breakfast recipes for weeks. "
              "Someone suggests they should add a new category to their repertoire: "
              "'Party' — appetizers, finger foods, entertaining bites. Summer is coming "
              "and people will want recipes for gatherings. The team debates and gets "
              "excited about the creative possibilities of party food in muffin tins."
          )

      with _run_stage(ep, "monday"):
        # Concept selection runs INSIDE the stage scope so a failure writes a
        # failed-stage record and Discord-alerts, instead of quietly seeding
        # the week with the placeholder (#6855).
        concept, target_category, concept_source = _resolve_monday_concept(body, ep)
        if not concept or concept == PLACEHOLDER_CONCEPT:
            raise ConceptSelectionError(
                "Refusing to run the week on the placeholder concept "
                f"{PLACEHOLDER_CONCEPT!r}: it means the catalog-aware novelty "
                "scorer never ran, so nothing is preventing a duplicate."
            )
        ep["concept"] = concept
        if target_category:
            ep["target_category"] = target_category

        import uuid
        if not ep.get("recipe_id"):
            ep["recipe_id"] = str(uuid.uuid4())[:8]

        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        # Note: active_recipes check removed — a new orchestrator instance is created
        # per request so active_recipes is always empty. start_recipe is idempotent.
        orchestrator.pipeline.start_recipe(ep["recipe_id"], concept)

        # Steer the baker away from recently-used cuisines so the catalog
        # keeps reaching across world cuisines instead of drifting American.
        from backend.utils.title_validator import load_recent_cuisines
        recent_cuisines = load_recent_cuisines()

        # Every Monday gate reads the catalog through the ONE strict reader.
        # It retries, then raises — and a raise here fails Monday closed via
        # _run_stage before the baker spends anything. The old readers fell
        # back to src/recipes.json, i.e. the ten launch seeds, so a CDN hiccup
        # silently shrank every duplicate check to a ten-recipe view that
        # still reported success (#6854, #6858).
        catalog = load_published_catalog()

        recipe_data, gate_trace = _bake_through_gates(
            orchestrator, ep, concept, target_category, recent_cuisines, catalog,
        )

        if not target_category:
            # Explicit-concept path with no category supplied or stored: the
            # operator chose the dish, so the honest label is the dish's own —
            # the baker's CATEGORY: line — not a guess at the thinnest shelf.
            target_category = _category_from_recipe(recipe_data)
            if target_category:
                ep["target_category"] = target_category

        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "monday", concept, ep, model=body.model,
            injected_event=body.injected_event or injected_event,
            recipe_data=recipe_data,
        )

        ep["stages"]["monday"] = {
            "stage": "brainstorm",
            "status": "complete",
            "concept": concept,
            "target_category": target_category,
            "concept_source": concept_source,
            "recipe_data": recipe_data,
            "gate_trace": gate_trace,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "monday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("monday: complete")
        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

    return _stage_response("monday", episode_id, concept, {
        "recipe_title": recipe_data.get("title", concept) if recipe_data else concept,
        "target_category": target_category,
        "dialogue_messages": len(dialogue),
    })


# ---------------------------------------------------------------------------
# Tuesday — Recipe Development
# ---------------------------------------------------------------------------

@router.api_route("/tuesday", methods=["GET", "POST"])
async def cron_tuesday(request: Request):
    _verify_cron_secret(request)
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT, "tuesday")
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "tuesday")

      with _run_stage(ep, "tuesday"):
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "tuesday", concept, ep, model=body.model,
            injected_event=body.injected_event,
            recipe_data=ep.get("stages", {}).get("monday", {}).get("recipe_data"),
        )
        ep["stages"]["tuesday"] = {
            "stage": "recipe_development",
            "status": "complete",
            "concept": concept,
            "recipe_data_ref": "from monday stage",
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "tuesday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("tuesday: complete")
        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

    return _stage_response("tuesday", episode_id, concept, {"dialogue_messages": len(dialogue)})


# ---------------------------------------------------------------------------
# Wednesday — Photography (longest stage ~3 min)
# ---------------------------------------------------------------------------

@router.api_route("/wednesday", methods=["GET", "POST"])
async def cron_wednesday(request: Request):
    _verify_cron_secret(request)
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT, "wednesday")
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "wednesday")
      recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})

      # #7936: once Sunday has claimed or published this week's photo, a new
      # image set would race publication. Refuse BEFORE paying for images.
      _refuse_wednesday_on_frozen_photos(episode_id, ep, concept)

      with _run_stage(ep, "wednesday"):
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        photography_result = orchestrator._execute_stage_photography(
            recipe_id, recipe_data, episode_id=episode_id,
        )
        # photography_result is now a full dict with rounds, vision eval, winner, selected_shots
        image_paths: list[str] = photography_result.get("selected_shots", []) if isinstance(photography_result, dict) else []

        # Upload ALL round images to blob (art director only uploads the winner).
        # Build local_path → canonical_path map from photography rounds.
        local_to_canonical: dict[str, str] = {}
        if isinstance(photography_result, dict):
            for rnd in photography_result.get("rounds", []):
                for v in rnd.get("variants", []):
                    lp = v.get("local_path", "")
                    cp = v.get("path", "")
                    if lp and cp:
                        local_to_canonical[cp] = lp

        # TRIAGE (#6856): the HERO fails closed, the rest alert.
        # image_paths[0] is the shot the recipe page and every social card
        # use; publishing without it ships a recipe with no photograph, so a
        # hero failure raises and _run_stage records + alerts. A non-hero
        # failure only costs a gallery slot, and raising there would force a
        # full reshoot of every image in the week — real Stability/Nano
        # Banana spend to recover a cosmetic gap. Those alert instead.
        image_urls = []
        failed_uploads: list[str] = []
        for canonical_path in image_paths:
            local_path = local_to_canonical.get(canonical_path, "")
            if not local_path:
                logger.error(f"No local_path for {canonical_path}")
                image_urls.append("")
                failed_uploads.append(f"{canonical_path}: no local path")
                continue
            try:
                lp = Path(local_path)
                if lp.exists():
                    blob_url = storage.save_image(canonical_path, lp.read_bytes())
                    image_urls.append(blob_url)
                else:
                    logger.error(f"Image file missing: {lp}")
                    image_urls.append("")
                    failed_uploads.append(f"{canonical_path}: file missing at {lp}")
            except Exception as e:
                logger.error(f"Failed to upload image {canonical_path}: {type(e).__name__}: {e}")
                image_urls.append("")
                failed_uploads.append(f"{canonical_path}: {type(e).__name__}: {e}")

        if image_urls and not image_urls[0]:
            raise RuntimeError(
                "Hero image upload failed, so the week has no publishable "
                f"photograph: {failed_uploads[0]}"
            )
        if failed_uploads:
            notify_pipeline_failure(
                recipe_id=ep.get("recipe_id") or "unknown",
                concept=concept,
                stage="wednesday (image upload)",
                error_message=(
                    f"{len(failed_uploads)} non-hero image(s) failed to upload "
                    f"for {episode_id}; the hero is fine and the week "
                    f"continues:\n" + "\n".join(failed_uploads[:5])
                ),
            )

        # #7936: the photos are saved and put up for Erik's review BEFORE the
        # paid dialogue runs, so a dialogue failure cannot lose them and the
        # review request exists before anything announces it. A new image set
        # always starts a new review, which invalidates any earlier decision.
        wed = {
            "stage": "photography",
            "status": "photos_ready",
            "concept": concept,
            "photography_data": photography_result,
            "reshoot_happened": photography_result.get("reshoot_happened", False) if isinstance(photography_result, dict) else False,
            "image_paths": image_paths,
            "image_urls": image_urls,
            "image_status": "auto_selected",
            "confirmed_winner": photography_result.get("winner", {}) if isinstance(photography_result, dict) else {},
        }
        request = photo_review.new_review(wed)
        # The control log is the authority (#7936); the episode keeps a
        # display mirror. Registered first, so nothing announces a set the
        # control does not know. Images live under a generation-specific
        # path, so a refused registration leaves nothing overwritten.
        try:
            photo_review.register_request(storage, episode_id, request)
        except photo_review.PublicationUnderway as exc:
            detail = (
                f"Wednesday {episode_id}: {exc}. The new photos were saved under their own "
                "generation path and are not up for review."
            )
            notify_pipeline_failure(
                recipe_id=ep.get("recipe_id") or "unknown", concept=concept,
                stage="wednesday (photo review)", error_message=detail,
            )
            raise HTTPException(status_code=409, detail=detail)
        wed["photo_review"] = {k: request[k] for k in ("image_set_id", "generated_at", "requested_at")}
        ep["stages"]["wednesday"] = wed
        ep["image_paths"] = image_paths
        ep["image_urls"] = image_urls
        ep.pop("publish_hold", None)
        ep["events"].append("wednesday: photos ready for review")
        storage.save_episode(episode_id, ep)
        sent = notify_photos_ready(
            episode_id, len(request["candidates"]), namespace=photo_review_namespace(storage.prefix),
        )
        wed["photo_review"]["notification"] = {
            "status": "sent" if sent else "failed",
            "at": datetime.now(timezone.utc).isoformat(),
        }
        storage.save_episode(episode_id, ep)

        wed_photo_ctx = photography_result if isinstance(photography_result, dict) else None
        review_ctx = _photo_review_dialogue_context(episode_id, ep)
        if wed_photo_ctx is not None and review_ctx:
            # The team's pick is a recommendation until Erik chooses (#7936).
            wed_photo_ctx = {**wed_photo_ctx, "human_review": review_ctx}
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "wednesday", concept, ep,
            image_paths=image_paths,
            photography_context=wed_photo_ctx,
            model=body.model,
            injected_event=body.injected_event,
            recipe_data=ep.get("stages", {}).get("monday", {}).get("recipe_data"),
        )

        wed.update({
            "status": "complete",
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "wednesday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        ep["stages"]["wednesday"] = wed
        ep["events"].append("wednesday: complete")
        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

    return _stage_response("wednesday", episode_id, concept, {
        "images_generated": len(image_paths),
        "image_urls": image_urls,
        "dialogue_messages": len(dialogue),
    })


# ---------------------------------------------------------------------------
# Thursday — Copywriting
# ---------------------------------------------------------------------------

@router.api_route("/thursday", methods=["GET", "POST"])
async def cron_thursday(request: Request):
    _verify_cron_secret(request)
    paused = _scheduled_w39_pause(request, "thursday")
    if paused is not None:
        return paused
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT, "thursday")
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "thursday")
      recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})

      with _run_stage(ep, "thursday"):
        # #7853: Marcus's intro, written as if he just tasted the dish, becomes
        # the published description. It replaces the 600-800-word essay this
        # stage used to pay for and never publish. No fallback text: a failed
        # call or a rule-breaking intro fails the stage (IntroError) and
        # Sunday will not publish without an intro. Openers of the last 10
        # published descriptions are banned (live catalog; unavailable ->
        # the stage fails rather than guessing).
        if episode_is_published(ep):
            # A published week's description is frozen reader-facing text: a
            # forced re-fire keeps it and the copy that produced it, and pays
            # for no new intro (Codex, #7853).
            intro = None
            copy_text = ep.get("stages", {}).get("thursday", {}).get("copy_text")
        else:
            banned_openers = recent_openers(load_published_catalog()["recipes"])
            intro = generate_intro(recipe_data, banned_openers)
            copy_text = {"body": intro, "kind": "intro", "banned_openers": banned_openers}
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "thursday", concept, ep, model=body.model,
            photography_context=_photo_review_only_context(episode_id, ep),
            injected_event=body.injected_event,
            recipe_data=recipe_data,
        )

        # Applied only once the stage has succeeded, so a failed Thursday leaves
        # no intro behind (Sunday's guard then refuses the week). A week whose
        # Monday ran before #7853 has its Monday one-liner in `description`;
        # keep it as the pitch before the intro replaces it.
        if intro is not None:
            recipe_data.setdefault("pitch", recipe_data.get("description", ""))
            recipe_data["description"] = intro
        ep["stages"]["thursday"] = {
            "stage": "copywriting",
            "status": "complete",
            "concept": concept,
            "copy_text": copy_text,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "thursday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("thursday: complete")
        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

    return _stage_response("thursday", episode_id, concept, {
        "copy_preview": (copy_text.get("body", "") if isinstance(copy_text, dict) else str(copy_text or ""))[:80],
        "dialogue_messages": len(dialogue),
    })


# ---------------------------------------------------------------------------
# Friday — Final Review
# ---------------------------------------------------------------------------

@router.api_route("/friday", methods=["GET", "POST"])
async def cron_friday(request: Request):
    _verify_cron_secret(request)
    paused = _scheduled_w39_pause(request, "friday")
    if paused is not None:
        return paused
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT, "friday")
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "friday")

      with _run_stage(ep, "friday"):
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        approved, review_output = orchestrator._execute_stage_review(recipe_id)

        # Pass photography context from Wednesday to Friday dialogue for hero shot awareness
        wed_photo_data = ep.get("stages", {}).get("wednesday", {}).get("photography_data")
        friday_photo_ctx = wed_photo_data if isinstance(wed_photo_data, dict) else None
        # The actual human review state, so nobody claims an approval that
        # has not happened (#7936).
        review_ctx = _photo_review_dialogue_context(episode_id, ep)
        if review_ctx:
            friday_photo_ctx = {**(friday_photo_ctx or {}), "human_review": review_ctx}
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "friday", concept, ep,
            photography_context=friday_photo_ctx,
            model=body.model,
            injected_event=body.injected_event,
            recipe_data=ep.get("stages", {}).get("monday", {}).get("recipe_data"),
        )

        ep["stages"]["friday"] = {
            "stage": "final_review",
            "status": "complete",
            "concept": concept,
            "approved": approved,
            "review_data": review_output,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "friday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("friday: complete")
        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

    return _stage_response("friday", episode_id, concept, {
        "approved": approved,
        "dialogue_messages": len(dialogue),
    })


# ---------------------------------------------------------------------------
# Saturday — Deployment / Staging
# ---------------------------------------------------------------------------

@router.api_route("/saturday", methods=["GET", "POST"])
async def cron_saturday(request: Request):
    _verify_cron_secret(request)
    paused = _scheduled_w39_pause(request, "saturday")
    if paused is not None:
        return paused
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT, "saturday")
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "saturday")

      with _run_stage(ep, "saturday"):
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        orchestrator._execute_stage_deployment(recipe_id)
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "saturday", concept, ep, model=body.model,
            photography_context=_photo_review_only_context(episode_id, ep),
            injected_event=body.injected_event,
            recipe_data=ep.get("stages", {}).get("monday", {}).get("recipe_data"),
        )

        ep["stages"]["saturday"] = {
            "stage": "deployment",
            "status": "complete",
            "concept": concept,
            "deployment_status": "staged",
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "saturday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("saturday: complete")
        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

    return _stage_response("saturday", episode_id, concept, {"dialogue_messages": len(dialogue)})


def _submit_sunday_indexnow(ep: dict, episode_id: str, concept: str) -> None:
    """Tell IndexNow the published recipe page (and the pages that list it)
    changed, so participating search engines don't wait for their next crawl.

    TRIAGE (#7806): GENUINELY NON-FATAL, logged and recorded. This runs after
    ``published_at`` is set and the reader pages are confirmed written — the
    recipe is already live and correct without this. IndexNow submission is a
    courtesy to the crawler, never a publish requirement, so a failure here
    must never raise into the publish path (see backend/utils/indexnow.py:
    ``submit_urls`` already reports rather than raises; this wrapper just
    decides what to do with that report).
    """
    # Everything, from deriving the slug to reading the result, is inside one
    # guard (Codex round 1): an unexpected error anywhere here would
    # otherwise reach _run_stage and mark an already-live publish failed.
    event: str | None = None
    try:
        from backend.recipe_model import catalog_slug

        # The same slug the catalog (and so the sitemap) uses. URLs match the
        # sitemap exactly: "/recipes" with no trailing slash, since
        # "/recipes/" is a 307 to it and IndexNow should get the final URL.
        slug = catalog_slug(ep)
        if not slug:
            event = "sunday: indexnow skipped (no recipe slug)"
        else:
            urls = [
                f"https://muffinpanrecipes.com/recipes/{slug}",
                "https://muffinpanrecipes.com/",
                "https://muffinpanrecipes.com/recipes",
            ]
            result = _indexnow_submit_urls(urls)
            if result.ok:
                event = f"sunday: indexnow submitted ({len(urls)} urls)"
            else:
                logger.warning(f"IndexNow submission for {episode_id} failed: {result.detail}")
                event = f"sunday: indexnow submission failed ({result.detail})"
    except Exception as exc:  # noqa: BLE001 - the publish already succeeded
        logger.error(
            f"IndexNow submission raised unexpectedly (non-fatal): {type(exc).__name__}: {exc}"
        )
        event = f"sunday: indexnow submission failed ({type(exc).__name__})"

    # Every outcome, including an unexpected error, is persisted, so the
    # runbook's "check the episode events" verification always has an answer.
    # The outcome and `indexnow_pending = False` go into a COPY that is saved
    # first and applied to `ep` only once the save succeeded: a failed save
    # leaves the submission owed, in memory and in Blob, for the
    # already-published catch-up (Codex round 2).
    try:
        updated = copy.deepcopy(ep)
        updated.setdefault("events", []).append(event)
        updated["indexnow_pending"] = False
        storage.save_episode(episode_id, updated)
    # governance: allow-silent SF002: publish already succeeded; indexnow_pending stays True in storage so the already-published catch-up retries the submission
    except Exception as exc:  # noqa: BLE001 - recording the outcome, not the publish
        logger.error(
            f"Could not persist indexnow event for {episode_id} "
            f"(error_type={type(exc).__name__}); left pending for a retry"
        )
        return
    ep.clear()
    ep.update(updated)


# ---------------------------------------------------------------------------
# Sunday — Publish
# ---------------------------------------------------------------------------

_HOLD_MESSAGES = {
    "awaiting_photo_approval": "Not published: waiting for photo approval.",
    "photos_rejected": "Not published: all photos were rejected.",
}


def photo_review_namespace(prefix: str) -> str:
    """The admin ``ns`` value for a storage prefix: "" (production) or "test"."""
    return "test" if prefix == "test/" else ""


def _refuse_wednesday_on_frozen_photos(episode_id: str, ep: dict, concept: str) -> None:
    """Wednesday must not replace photos Sunday has claimed or published (#7936).

    Checked before the paid generation. A control read failure is a 503:
    without it Wednesday cannot know whether publication has begun.
    """
    if episode_is_published(ep):
        # published_at OR a complete Sunday: the legacy-aware predicate, so a
        # historical week's photos are never replaced either.
        raise HTTPException(
            status_code=409,
            detail=f"Episode {episode_id} is already published; Wednesday will not replace its photos.",
        )
    try:
        photo_review.ensure_not_frozen(storage, episode_id)
    except photo_review.PublicationUnderway as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Episode {episode_id}: {exc}. Wednesday will not replace the photos.",
        )
    except Exception as exc:
        detail = (
            f"Wednesday could not read the photo control for {episode_id}: "
            f"{type(exc).__name__}: {exc}. No photos were generated."
        )
        logger.error(detail)
        notify_pipeline_failure(
            recipe_id=ep.get("recipe_id") or "unknown", concept=concept or "unknown",
            stage="wednesday (photo review)", error_message=detail,
        )
        raise HTTPException(status_code=503, detail=detail)


def _photo_review_dialogue_context(episode_id: str, ep: dict) -> dict | None:
    """The human review status for a day's dialogue, from the control (#7936).

    A read failure is stated as "not confirmed", never as an approval, and
    does not fail the day: the dialogue only mentions it.
    """
    try:
        view = photo_review.read_view(storage, episode_id, ep)
    except Exception as exc:
        logger.error(f"Photo control read failed for dialogue context: {type(exc).__name__}: {exc}")
        return photo_review.dialogue_context(None, unavailable=True)
    return photo_review.dialogue_context(view)


def _photo_review_only_context(episode_id: str, ep: dict) -> dict | None:
    """Thu/Sat/Sun: just the review status, or None for weeks without one."""
    review_ctx = _photo_review_dialogue_context(episode_id, ep)
    return {"human_review": review_ctx} if review_ctx else None


def _sunday_storage_failure(episode_id: str, concept: str, detail: str, status_code: int = 503):
    """Alert and refuse. Raised as HTTPException so _run_stage saves nothing."""
    logger.error(detail)
    notify_pipeline_failure(
        recipe_id="unknown", concept=concept or "unknown", stage="sunday", error_message=detail,
    )
    return HTTPException(status_code=status_code, detail=detail)


def _read_episode_verified(episode_id: str, concept: str, purpose: str) -> dict | None:
    """The stored episode, proven current (#7936), or an alerted 503.

    Uses the metadata-ETag check in ``storage.load_episode_verified``: a CDN
    copy older than the stored version is refused rather than acted on.
    Nothing is saved on failure: the caller's copy may be the stale one.
    None means storage answered and the episode does not exist.
    """
    try:
        return storage.load_episode_verified(episode_id)
    except Exception as exc:
        raise _sunday_storage_failure(
            episode_id, concept,
            f"Sunday could not read a current copy of episode {episode_id} {purpose}: "
            f"{type(exc).__name__}: {exc}. Nothing was published, spent or saved; "
            "run Sunday again in a minute. No verified episode is in hand, so this failure "
            "could not be recorded on it: the monitor only sees the last persisted snapshot "
            "(an earlier photo hold or status recorded there keeps showing until a Sunday run "
            "succeeds). Treat this alert as the record.",
        )


def _record_failed_hold_attempt(episode_id: str, ep: dict, detail: str) -> str:
    """A Sunday that found an earlier hold and then failed before deciding (#7936).

    Marks ``publish_hold.last_attempt`` failed and saves, so the integrity
    monitor stops reading the week as quietly "awaiting photo approval" and
    reports this failure instead. Returns a sentence for the alert: either
    that the attempt was recorded, or that it could NOT be (the monitor will
    then still show the hold, which the alert says plainly).
    """
    hold = ep.get("publish_hold")
    if not isinstance(hold, dict):
        return ""
    hold["last_attempt"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "outcome": "failed",
        "detail": detail[:500],
    }
    ep.setdefault("events", []).append("sunday: attempt after hold failed")
    try:
        storage.save_episode(episode_id, ep)
    except Exception as exc:
        return (
            " The failed attempt could NOT be recorded on the episode either "
            f"({type(exc).__name__}: {exc}); until a Sunday run succeeds, the monitor will keep "
            "showing the earlier photo hold. Treat this alert as the record."
        )
    return " The failed attempt was recorded on the episode; the monitor reports it."


def _no_reviewable_photos_failure(episode_id: str, ep: dict, concept: str, detail: str):
    """Wednesday is complete but nothing can be approved (#7936, preflight 5).

    Not a hold: no email asks Erik to approve, no "awaiting" status is saved.
    The Sunday stage is recorded as failed (an earlier hold, now meaningless,
    is removed) so the monitor reports it, and the alert says what to do:
    re-fire Wednesday. Nothing is spent. If the record cannot be saved the
    alert says so rather than claiming it was.
    """
    ep.pop("publish_hold", None)
    ep.setdefault("stages", {})["sunday"] = {
        "stage": "publish", "status": "failed", "published": False,
        "error": f"No reviewable photos: {detail}",
    }
    ep.setdefault("events", []).append("sunday: failed (no reviewable photos)")
    try:
        storage.save_episode(episode_id, ep)
        recorded = " The failed Sunday was recorded on the episode; the monitor reports it."
    except Exception as exc:
        recorded = (
            f" The failed Sunday could NOT be recorded on the episode ({type(exc).__name__}: {exc}); "
            "the monitor only sees the last persisted snapshot. Treat this alert as the record."
        )
    message = (
        f"Sunday {episode_id} cannot publish. No reviewable photos: {detail} No paid work ran and "
        "nothing was published; no approval was requested because there is nothing to approve." + recorded
    )
    logger.error(message)
    notify_pipeline_failure(
        recipe_id=ep.get("recipe_id") or "unknown", concept=concept or "unknown",
        stage="sunday (photo review)", error_message=message,
    )
    return HTTPException(status_code=400, detail=message)


def _hold_for_photo_approval(episode_id: str, ep: dict, concept: str, reason: str, view):
    """Record and report a photo-approval hold. Expected waiting, not a failure.

    The hold is persisted before the one alert, and re-sent only if an earlier
    send did not go out or the reason/image set changed. The saved episode
    carries the AUTHORITATIVE request's mirror and evaluation snapshot
    (#7936), so the integrity monitor, which checks the hold against
    ``episode_request(ep)``, recognises a hold on the current set even when
    a stale save had put an older set's mirror back.
    """
    from fastapi.responses import JSONResponse

    image_set = view.image_set_id
    episode_changed = False
    if view.version and view.request:
        before = copy.deepcopy((ep.get("stages") or {}).get("wednesday"))
        photo_review.apply_request_mirror(ep, view.request)
        episode_changed = before != ep["stages"]["wednesday"]
    # An earlier Sunday that failed after its claim (editorial QA, dialogue)
    # left a failed stage. This run is deliberately waiting, which is the
    # current truth; the failure stays in events/editorial_qa. The monitor
    # does not recognise a hold beside a Sunday stage, so the stale one goes.
    stale_sunday = (ep.get("stages") or {}).get("sunday")
    if isinstance(stale_sunday, dict) and stale_sunday.get("status") != "complete":
        ep["stages"].pop("sunday", None)
        ep.setdefault("events", []).append("sunday: earlier failed attempt superseded by hold")
        episode_changed = True
    prior = ep.get("publish_hold") if isinstance(ep.get("publish_hold"), dict) else {}
    prior_attempt = prior.get("last_attempt") if isinstance(prior.get("last_attempt"), dict) else {}
    if prior_attempt.get("outcome") == "failed":
        # This run reached the hold decision, so the earlier failed attempt
        # is superseded; the hold is quiet again.
        episode_changed = True
    if episode_changed or not (
        prior.get("reason") == reason and prior.get("image_set_id") == image_set and prior.get("notified")
    ):
        same_hold = prior.get("reason") == reason and prior.get("image_set_id") == image_set
        hold = {
            "reason": reason,
            "image_set_id": image_set,
            "since": prior.get("since") or datetime.now(timezone.utc).isoformat(),
            "notified": bool(same_hold and prior.get("notified")),
            "last_attempt": {"at": datetime.now(timezone.utc).isoformat(), "outcome": "held"},
        }
        ep["publish_hold"] = hold
        ep.setdefault("events", []).append(f"sunday: held ({reason})")
        storage.save_episode(episode_id, ep)
        if not hold["notified"]:
            hold["notified"] = bool(notify_publish_held(
                episode_id, reason, namespace=photo_review_namespace(storage.prefix),
            ))
            storage.save_episode(episode_id, ep)

    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={
        "status": reason,
        "stage": "sunday",
        "episode_id": episode_id,
        "concept": concept,
        "published": False,
        "message": _HOLD_MESSAGES[reason],
    })


def _publication_underway_response(episode_id: str, concept: str, detail: str):
    """Another Sunday holds (or finished) the claim. Nothing spent or saved."""
    from fastapi.responses import JSONResponse

    logger.warning(f"Sunday {episode_id}: {detail}")
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={
        "status": "publication_underway",
        "stage": "sunday",
        "episode_id": episode_id,
        "concept": concept,
        "published": False,
        "message": "Not started: another Sunday run holds the publication claim.",
        "detail": detail,
    })


def _release_photo_claim(episode_id: str, concept: str, claimed, reason: str) -> None:
    """Pre-publication failure: hand the approval back. Never raises.

    If the release itself fails the control stays visibly ``claimed``;
    RUNBOOK's photo-control reconciliation is the recovery.
    """
    try:
        photo_review.release_claim(storage, claimed, reason)
    except Exception as exc:
        detail = (
            f"Sunday {episode_id} stopped before publishing ({reason}) but could not release "
            f"its photo claim: {type(exc).__name__}: {exc}. Nothing was published. The admin "
            "page shows publishing underway until it is reconciled "
            "(RUNBOOK: photo-control reconciliation)."
        )
        logger.error(detail)
        notify_pipeline_failure(
            recipe_id="unknown", concept=concept or "unknown", stage="sunday (photo claim)",
            error_message=detail,
        )


def _reconcile_photo_control(episode_id: str, ep: dict) -> None:
    """Already-published fast path: record a publish the control missed. Never raises."""
    try:
        if photo_review.reconcile_published(storage, episode_id, ep):
            logger.info(f"Sunday {episode_id}: photo control reconciled to published")
    except Exception as exc:
        logger.error(f"Photo control reconcile failed for {episode_id}: {type(exc).__name__}: {exc}")


def _complete_checkpointed_publication(episode_id: str, ep: dict, concept: str, body):
    """Finish a publication the photo control has already committed to (#7936).

    Runs when the verified episode is NOT published but the control is
    ``publishing``/``published``: the publishing save failed, its outcome
    was unknown, or a stale whole-episode save (a slow daily run, a Sunday
    whose claim was released) later removed the published fields. The
    control's immutable checkpoint is the exact published episode, so this
    writes that SAME publication again: no dialogue, QA, image or other paid
    call, and no new decision. The static source handoff, the advisory alert
    and IndexNow run as on the already-published path.

    Returns a response, or None when there is nothing to complete. A control
    read failure is an alerted 503 with nothing written.
    """
    try:
        view = photo_review.read_view(storage, episode_id, ep)
    except Exception as exc:
        detail = (
            f"Sunday could not read the photo control for {episode_id}: "
            f"{type(exc).__name__}: {exc}. Nothing was published or spent."
        )
        raise _sunday_storage_failure(
            episode_id, concept, detail + _record_failed_hold_attempt(episode_id, ep, detail),
        )
    if view.state not in (photo_review.PUBLISHING, photo_review.PUBLISHED):
        return None
    pub = view.publication
    if pub is None:
        return _publication_underway_response(
            episode_id, concept,
            f"photo control is {view.state} without a publication checkpoint; investigate by hand "
            "(RUNBOOK photo approval)",
        )
    pub = copy.deepcopy(pub)
    # Sources are (re)written below; the checkpoint already says pending.
    _set_static_deploy_state(pub, "pending")
    pub.setdefault("events", []).append(
        f"sunday: publication completed from photo-control checkpoint v{view.version}"
    )
    try:
        storage.save_episode(episode_id, pub)
    except Exception as exc:
        raise _sunday_storage_failure(
            episode_id, concept,
            f"Sunday {episode_id}: saving the checkpointed publication failed "
            f"({type(exc).__name__}: {exc}). Run Sunday again; nothing paid was repeated.",
            status_code=500,
        )
    ep.clear()
    ep.update(pub)
    if view.state == photo_review.PUBLISHING:
        try:
            photo_review.mark_published(storage, view, ep["published_at"])
        except Exception as exc:
            logger.error(f"Photo control not marked published for {episode_id}: {type(exc).__name__}: {exc}")
    _complete_static_source_handoff(episode_id, ep)
    _announce_advisory_publication(episode_id, ep, "sunday", concept)
    if ep.get("indexnow_pending") and not body.test and not storage.prefix:
        _submit_sunday_indexnow(ep, episode_id, concept)
    sunday_stage = ep.get("stages", {}).get("sunday", {})
    return _stage_response("sunday", episode_id, concept, {
        "published": True,
        "completed_from_checkpoint": True,
        "published_at": ep.get("published_at"),
        "dialogue_messages": len(sunday_stage.get("dialogue", [])),
    })


def _published_copy(ep: dict, claimed, dialogue, judge_verdict, concept: str) -> dict:
    """The episode as it will be published, built on a COPY (#7936).

    ``ep`` itself never carries published_at or a complete Sunday stage
    until the publishing save has succeeded, so any failure path that saves
    ``ep`` (stage-failure saves, QA failure, holds) persists it unpublished.
    """
    approved = claimed.selected
    pub = copy.deepcopy(ep)
    # The control's request is the image set Erik approved. A stale
    # Thursday-Saturday whole-episode save may have put an older set back
    # into the mirror; the published page, its gallery and the recorded
    # evaluation must be the approved set's.
    photo_review.apply_request_mirror(pub, claimed.request or {})
    decision = claimed.decision or {}
    # Pin the hero the published page is about to render (Erik, 2026-08-22:
    # published heroes are frozen). The renderer honours hero_image_url
    # before any picking logic, so a later re-render — full rebuild,
    # encoding fix — can never swap the picture. Without this, the first
    # live full rebuild (2026-09-05) changed the hero on 20 of 25 pages.
    # The episode is not published yet, so any existing pin is from before
    # the review and must not override Erik's choice (#7936).
    pub["hero_image_url"] = approved["url"]
    pub["photo_approval"] = {
        "control_version": claimed.version,
        "claim_id": (claimed.claim or {}).get("claim_id"),
        "image_set_id": claimed.image_set_id,
        "decided_at": decision.get("decided_at"),
        "decided_by": decision.get("decided_by"),
        "decided_by_id": decision.get("decided_by_id"),
        **{k: approved[k] for k in ("path", "url", "variant")},
    }
    pub["published_at"] = datetime.now(timezone.utc).isoformat()
    pub.pop("publish_hold", None)
    # Saved with published_at, so a run that dies before the IndexNow
    # outcome is persisted leaves it owed; the already-published path
    # retries it (#7806, same shape as announce_pending, #7403).
    pub["indexnow_pending"] = True
    pub["stages"]["sunday"] = {
        "stage": "publish",
        "status": "complete",
        "concept": concept,
        "published": True,
        "image_cleaned": False,
        "dialogue": dialogue,
        "judge_verdict": judge_verdict,
        **_judge_meta_fields(pub, "sunday"),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    pub["events"].append("sunday: complete (published)")
    return pub


def _write_featured_photo(ep: dict, approved: dict, concept: str, episode_id: str) -> None:
    """Wire Erik's approved photo into the legacy Recipe featured_photo (#7936)."""
    featured_image_path = approved["path"]
    if not featured_image_path:
        return
    # Strip src/ prefix for web serving
    web_image_path = featured_image_path.removeprefix("src/")
    recipe_id = ep.get("recipe_id")
    if not recipe_id:
        return
    # Update the recipe's featured_photo so the publishing pipeline picks it up
    from pathlib import Path as _Path
    data_dir = _Path(__file__).resolve().parents[1] / "data" / "recipes"
    from backend.data.recipe import Recipe, RecipeStatus
    for rs in RecipeStatus:
        recipe_path = data_dir / rs.value / f"{recipe_id}.json"
        if recipe_path.exists():
            try:
                recipe = Recipe.load_from_file(recipe_path)
                recipe.featured_photo = web_image_path
                recipe.save_to_file(data_dir)
                logger.info(f"Set featured_photo={web_image_path} on recipe {recipe_id}")
            except Exception as e:
                # TRIAGE (#6856): NON-FATAL, now alerted. This
                # writes the legacy Recipe record; the reader page
                # takes its hero from the pinned hero_image_url. So the publish
                # is still correct, but a silent failure here
                # leaves the two stores disagreeing — worth a ping.
                logger.error(
                    f"Failed to set featured_photo on recipe {recipe_id}: "
                    f"{type(e).__name__}: {e}"
                )
                notify_pipeline_failure(
                    recipe_id=recipe_id,
                    concept=concept,
                    stage="sunday (featured_photo)",
                    error_message=(
                        f"Could not write featured_photo={web_image_path} "
                        f"to the Recipe record for {episode_id}. The "
                        f"published page still uses the episode hero; "
                        f"the legacy record is now out of sync."
                    ),
                )
            break


@router.api_route("/sunday", methods=["GET", "POST"])
async def cron_sunday(request: Request):
    _verify_cron_secret(request)
    paused = _scheduled_w39_pause(request, "sunday")
    if paused is not None:
        return paused
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      # Verified fresh (#7936): no paid work starts from a CDN copy older
      # than the stored episode. A missing episode still reaches the Monday
      # gate below (409, alerted); an unverifiable read is an alerted 503.
      ep = _read_episode_verified(
          episode_id, body.concept or PLACEHOLDER_CONCEPT, "at the start of Sunday",
      ) or _new_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT

      # Legacy-aware (#7936): a historical week with a complete Sunday and no
      # published_at is published and is never re-decided or overwritten.
      if episode_is_published(ep):
        handoff_status = _static_deploy_state(ep).get("status")
        if handoff_status == "pending" or (
            handoff_status == "failed"
            and _static_deploy_state(ep).get("phase") == "sources"
        ):
            _complete_static_source_handoff(episode_id, ep)
        # A crash between saving published=True and sending the advisory
        # alert used to lose the alert forever, because this fast path never
        # retried it (#7403). The helper only sends while the record is
        # announce_pending and the handoff reached source_ready, so this is a
        # no-op once delivered and for records older code already announced.
        _announce_advisory_publication(episode_id, ep, "sunday", concept)
        # #7806: an IndexNow outcome that was never persisted (the run died,
        # or its save failed) is retried here. Records published before the
        # flag existed never carry it, so old weeks are not resubmitted.
        if ep.get("indexnow_pending") and not body.test and not storage.prefix:
            _submit_sunday_indexnow(ep, episode_id, concept)
        # #7936: a run that died after its publishing save leaves the photo
        # control claimed/publishing; the episode proves which claim won.
        _reconcile_photo_control(episode_id, ep)
        sunday_stage = ep.get("stages", {}).get("sunday", {})
        return _stage_response("sunday", episode_id, concept, {
            "published": True,
            "already_published": True,
            "published_at": ep.get("published_at"),
            "dialogue_messages": len(sunday_stage.get("dialogue", [])),
        })

      # #7936: a publication the control already committed to (publishing
      # or published, with its checkpoint) is completed, never re-decided or
      # re-generated, even when the episode no longer shows it. It runs before
      # the #7630 week-off note so a committed publication is never noted off.
      completed = _complete_checkpointed_publication(episode_id, ep, concept, body)
      if completed is not None:
          return completed

      if not _monday_recipe_ready(ep):
          _note_week_off_without_a_recipe(episode_id, ep)
      _require_monday_recipe(ep, "sunday")

      with _run_stage(ep, "sunday"):
        # Verify critical prior stages completed before spending on dialogue.
        # (Monday is already enforced by _require_monday_recipe above.)
        required_stages = ["wednesday"]
        for day in required_stages:
            stage_status = ep.get("stages", {}).get(day, {}).get("status")
            if stage_status != "complete":
                # #7630: this IS the week's Sunday window closing without a
                # publish — the card's own motivating case — so set the
                # "kitchen took the week off" note right here rather than
                # waiting for next Monday's cron to notice it. Every OTHER
                # way Sunday can fail to publish (an uncaught exception
                # later in this stage, after this check passes) is instead
                # picked up by next Monday's _apply_week_off_note, same as
                # any other failure mode — duplicating this for every
                # failure surface inside the stage would not be simple.
                # That includes a week held for Erik's photo approval
                # (#7936): it is waiting on him, not off, so Sunday shows no
                # note for it (Erik, 2026-10-05, accepted behaviour).
                # Only once the week's own Sunday cron time has arrived: an
                # early manual force=true re-fire still has time to recover,
                # so announcing the week off then would be premature (Codex,
                # #7630). The scheduled run fires at that time; the tolerance
                # covers a cron that fires a little early.
                if not _sunday_window_reached(episode_id):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot publish: {day} stage incomplete (status={stage_status!r})",
                    )
                ep["week_off_note"] = {
                    "message": WEEK_OFF_MESSAGE, "missed_week": episode_id,
                }
                # Best-effort: the note is cosmetic, and a failed save or
                # render must not turn the 400 refusal below into a 500
                # (Codex, #7630).
                try:
                    storage.save_episode(episode_id, ep)
                    # regenerate_and_upload itself only ever touches
                    # pages/latest.json for the CURRENT ISO week (#7630) — a
                    # manual force=true retry of an older incomplete week
                    # still renders/uploads that week's own page here, but
                    # can no longer replace the live homepage teaser with
                    # stale content. The writer is the single source of
                    # truth for the invariant.
                    regenerate_and_upload(ep)
                except Exception as exc:  # noqa: BLE001 - cosmetic note; the refusal stands
                    logger.warning(
                        f"week-off note for refused {episode_id} not written: "
                        f"{type(exc).__name__}: {exc}"
                    )
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot publish: {day} stage incomplete (status={stage_status!r})",
                )

        # #7853: the published description is Marcus's Thursday intro. A week
        # without one (Thursday failed or never ran) must not publish the
        # placeholder or an empty description; refuse before any paid work.
        # Weeks whose Monday ran before #7853 carry Monday's one-liner here
        # and publish as they always did. No week-off note: the week has a
        # recipe (Erik, 2026-10-05, decision 1).
        _recipe = ep.get("stages", {}).get("monday", {}).get("recipe_data", {}) or {}
        _published_description = (_recipe.get("description") or "").strip()
        _thursday_copy = ep.get("stages", {}).get("thursday", {}).get("copy_text")
        _thursday_intro = (
            (_thursday_copy.get("body") or "").strip()
            if isinstance(_thursday_copy, dict) and _thursday_copy.get("kind") == "intro"
            else ""
        )
        # A recipe Monday wrote with #7853 always carries a `pitch` key; its
        # description must be exactly the intro Thursday recorded, never other
        # text that happens to be there (e.g. a fallback recipe's line).
        if not _published_description or (
            "pitch" in _recipe and _published_description != _thursday_intro
        ):
            _no_intro = (
                "Cannot publish: Marcus's intro is missing (Thursday did not write it). "
                "Re-fire /api/cron/thursday, then Sunday."
            )
            # _run_stage passes an HTTPException through untouched, so record
            # the failed Sunday and alert here: a scheduled Sunday's 400 goes to
            # a cron runner that discards it.
            _save_stage_failure(ep, "sunday", RuntimeError(_no_intro))
            raise HTTPException(status_code=400, detail=_no_intro)

        # #7936: nothing is published, and no paid dialogue or QA runs, until
        # this run holds the exclusive publication claim on an approval of
        # the CURRENT photos. No reply and a rejection both hold; there is no
        # force/test bypass. From the claim on, Erik's choices are frozen and
        # Wednesday cannot replace the set, so nothing has to be re-checked
        # after the paid steps; a rival Sunday cannot claim twice.
        try:
            claimed = photo_review.claim_for_publication(storage, episode_id, ep)
        except photo_review.PhotoHold as hold:
            return _hold_for_photo_approval(episode_id, ep, concept, hold.reason, hold.view)
        except photo_review.NoReviewablePhotos as exc:
            raise _no_reviewable_photos_failure(episode_id, ep, concept, str(exc))
        except photo_review.PublicationUnderway as exc:
            return _publication_underway_response(episode_id, concept, str(exc))
        except photo_review.ReviewConflict as exc:
            # The control kept changing under three claim attempts. That is a
            # conflict, not a rival publisher: alerted, and recorded on any
            # earlier hold so the monitor does not read the week as waiting.
            detail = (
                f"Sunday {episode_id} could not claim the photo control: {exc}. Nothing was "
                "published or spent; run Sunday again."
            )
            raise _sunday_storage_failure(
                episode_id, concept, detail + _record_failed_hold_attempt(episode_id, ep, detail),
                status_code=409,
            )
        except Exception as exc:
            detail = (
                f"Sunday could not read or claim the photo control for {episode_id}: "
                f"{type(exc).__name__}: {exc}. Nothing was published or spent."
            )
            raise _sunday_storage_failure(
                episode_id, concept, detail + _record_failed_hold_attempt(episode_id, ep, detail),
            )
        # #7936: the hold an earlier Sunday recorded while waiting is obsolete
        # the moment an approval is claimed. It is cleared in storage FIRST,
        # before any other check or paid step, so every failure from here on
        # (no usable selection, dialogue, editorial QA, a lost claim) is
        # reported as that failure and never read back as "awaiting photo
        # approval". If the clear cannot be saved, the claim is handed back
        # and nothing is spent.
        hold_before = ep.pop("publish_hold", None)
        if hold_before is not None:
            cleared_event = "sunday: hold cleared (approved photo claimed)"
            events = ep.setdefault("events", [])
            cleared_index = len(events)  # the slot THIS run's event occupies
            events.append(cleared_event)
            try:
                storage.save_episode(episode_id, ep)
            except Exception as exc:
                # The clear did not persist: the audit trail must not say it
                # did. Only this run's appended event is withdrawn; an earlier
                # identical event from a hold that really was cleared is history.
                if cleared_index < len(events) and events[cleared_index] == cleared_event:
                    events.pop(cleared_index)
                events.append("sunday: hold clear NOT persisted (save failed)")
                _release_photo_claim(episode_id, concept, claimed, "could not clear the previous hold")
                detail = (
                    f"Sunday {episode_id}: could not clear the previous photo hold before "
                    f"publishing ({type(exc).__name__}: {exc}). The approval was handed back; "
                    "nothing was spent. Run Sunday again in a minute."
                )
                # The hold is still in storage; put the failed attempt on it
                # (same store, so this usually fails too and the alert says so).
                ep["publish_hold"] = hold_before
                raise _sunday_storage_failure(
                    episode_id, concept, detail + _record_failed_hold_attempt(episode_id, ep, detail),
                )
        approved_photo = claimed.selected
        if approved_photo is None:
            # claim_for_publication only claims an approval of a current
            # candidate; refuse rather than publish without a photo.
            _release_photo_claim(episode_id, concept, claimed, "no approved photo")
            raise RuntimeError("claimed photo control has no approved candidate")

        publishing_started = False
        try:
            dialogue, judge_verdict = _generate_and_judge_dialogue(
                "sunday", concept, ep, model=body.model,
                photography_context={"human_review": photo_review.dialogue_context(claimed)},
                injected_event=body.injected_event,
                recipe_data=ep.get("stages", {}).get("monday", {}).get("recipe_data"),
                # The judge scores Sunday but does not gate it (#7394). A weak
                # sign-off scene is a tuning problem; withholding the recipe from
                # readers over it is how W38 published nothing at all.
                advisory=True,
            )

            # Editorial QA gate with auto-fix retry loop
            qa_passed, qa_report = _editorial_qa_review(ep)
            fix_attempts = 0
            while (
                not qa_passed
                and fix_attempts < MAX_QA_FIX_ATTEMPTS
                # An unreachable reviewer is not a fixable recipe. Auto-fixing here
                # would burn LLM calls against the same dead provider and then fail
                # anyway, so skip straight to the hard stop below (#6505).
                and QA_UNAVAILABLE_MARKER not in qa_report
            ):
                fix_attempts += 1
                ep["events"].append(
                    f"sunday: editorial QA FAILED (attempt {fix_attempts}), auto-fixing"
                )
                logger.info(f"Editorial QA failed, attempting auto-fix {fix_attempts}/{MAX_QA_FIX_ATTEMPTS}")

                if _auto_fix_recipe(ep, qa_report):
                    ep["events"].append(f"sunday: auto-fix applied (attempt {fix_attempts})")
                    qa_passed, qa_report = _editorial_qa_review(ep)
                else:
                    ep["events"].append(f"sunday: auto-fix failed (attempt {fix_attempts})")
                    break

            ep["editorial_qa"] = {
                "passed": qa_passed,
                "report": qa_report,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
                "fix_attempts": fix_attempts,
            }
            if not qa_passed:
                ep["events"].append("sunday: editorial QA FAILED (exhausted retries)")
                # Recorded as a failed Sunday on the UNPUBLISHED episode so
                # the monitor reports the QA failure (#7936): the raise below
                # is an HTTPException, which _run_stage deliberately does not
                # record. No publication field is touched.
                ep["stages"]["sunday"] = {
                    "stage": "publish",
                    "status": "failed",
                    "published": False,
                    "error": (
                        f"Editorial QA failed after {fix_attempts} auto-fix attempts: "
                        f"{qa_report[:300]}"
                    ),
                }
                storage.save_episode(episode_id, ep)
                notify_judge_failure(
                    concept=concept,
                    stage="sunday (editorial QA)",
                    verdict=f"Failed after {fix_attempts} auto-fix attempts.\n{qa_report[:500]}",
                    episode_id=episode_id,
                    attempts=fix_attempts + 1,
                )
                raise HTTPException(
                    status_code=400,
                    detail=f"Editorial QA failed after {fix_attempts} auto-fix attempts.\n{qa_report}",
                )
            if fix_attempts > 0:
                ep["events"].append(f"sunday: editorial QA PASSED after {fix_attempts} auto-fix(es)")
            else:
                ep["events"].append("sunday: editorial QA PASSED")

            _write_featured_photo(ep, approved_photo, concept, episode_id)
            pub = _published_copy(ep, claimed, dialogue, judge_verdict, concept)
            # No automatic variant cleanup (#7936, Codex cycle 2). It used to
            # run here, on the provisional copy, BEFORE mark_publishing: a
            # failed mark_publishing handed the approval back with the other
            # candidates already in the trash, and the page then offered them.
            # Every candidate stays on disk for as long as the request can
            # become editable; scripts/cleanup_image_backlog.py is the
            # explicit sweep and never trashes a protected photo.

            # Generate per-character memories from the week's dialogue (#5027, #6968)
            try:
                memory_outcome = _generate_episode_memories(pub, concept)
                if memory_outcome["saved"]:
                    pub["events"].append(
                        "sunday: memory saved for " + ", ".join(sorted(memory_outcome["saved"]))
                    )
                if memory_outcome["absent"]:
                    pub["events"].append(
                        "sunday: memory absent (no dialogue) for "
                        + ", ".join(sorted(memory_outcome["absent"]))
                    )
                if memory_outcome["failed"]:
                    pub["events"].append(
                        "sunday: memory generation failed for "
                        + ", ".join(sorted(memory_outcome["failed"]))
                    )
            except Exception as e:
                # TRIAGE (#6856): GENUINELY NON-FATAL, logged and recorded.
                # Character memories only feed next week's prompts, and
                # per-character failures are already handled inside; this outer
                # guard catches a total failure (e.g. the roster lookup itself
                # raising). Recorded as an episode event so it is visible in the
                # JSON rather than only in a Lambda log.
                logger.error(f"Memory generation failed (non-fatal): {type(e).__name__}: {e}")
                pub["events"].append(f"sunday: memory generation failed ({type(e).__name__})")

            # The page exists now, so the advisory record can claim it (#7394).
            advisory = pub.get("judge_advisory", {}).get("sunday")
            if advisory and not advisory.get("published"):
                advisory["published"] = True
                advisory["published_at"] = pub["published_at"]
                # Saved in the same write as `published` (#7403): the alert is
                # owed from here until a delivery is confirmed.
                advisory["announce_pending"] = True

            # Last step before publication: move the control claimed ->
            # publishing. It fails if the claim was released by the operator
            # reconciliation, so a stale run cannot publish over it.
            # The checkpoint is the exact body the publishing save writes, so
            # a run that completes it later writes the same episode.
            _set_static_deploy_state(pub, "pending")
            try:
                publishing = photo_review.mark_publishing(storage, claimed, pub)
            except Exception as exc:
                raise _sunday_storage_failure(
                    episode_id, concept,
                    f"Sunday {episode_id} lost or could not confirm its photo claim before "
                    f"publishing: {type(exc).__name__}: {exc}. Nothing was published.",
                    status_code=409 if isinstance(exc, photo_review.PhotoControlConflict) else 503,
                )
            publishing_started = True

            # Persist the published episode before writing the catalog so a crash
            # between authoritative writes and the manual deployment handoff is
            # retryable.
            try:
                storage.save_episode(episode_id, pub)
            except Exception as exc:
                # The write may have landed. Saving the unpublished copy now
                # (as _run_stage would) could undo a real publication, so
                # nothing more is written; the control stays "publishing"
                # with its checkpoint, which the next Sunday run completes.
                raise _sunday_storage_failure(
                    episode_id, concept,
                    f"Sunday {episode_id}: the publishing save failed ({type(exc).__name__}: {exc}). "
                    "Publication may or may not have happened; photo choices stay frozen. "
                    "Run Sunday again: it completes this same publication from the photo-control "
                    "checkpoint with no paid work (RUNBOOK photo approval).",
                    status_code=500,
                )
            # From here ``ep`` IS the published episode, so a later failure
            # save in _run_stage keeps published_at.
            ep.clear()
            ep.update(pub)
            try:
                photo_review.mark_published(storage, publishing, ep["published_at"])
            except Exception as exc:
                # The episode proves the publication; the already-published
                # fast path reconciles the control on the next run.
                logger.error(f"Photo control not marked published for {episode_id}: {type(exc).__name__}: {exc}")
        except BaseException:
            if not publishing_started:
                _release_photo_claim(episode_id, concept, claimed, "Sunday stopped before publishing")
            raise

        _complete_static_source_handoff(episode_id, ep)
        # Last, because the handoff is what writes the reader-facing pages
        # and catalog. It raises on failure with its own alert saying the
        # episode is marked published but the pages were NOT written
        # (_complete_static_source_handoff), so announcing before it could
        # put "the recipe is live" and "the pages were not written" in the
        # same inbox. Skipping the advisory alert on that path loses it —
        # carded — which is the lesser harm of the two.
        _announce_advisory_publication(episode_id, ep, "sunday", concept)
        # #7630 (Erik, 2026-10-05, option c): a late publish does NOT clear
        # a successor week's note that blamed this week. That clear could
        # erase a recipe-less successor's own valid note. A stale note is
        # dropped instead at the successor's next stage write, which
        # re-checks it (episode_renderer._week_off_note_still_true).

        # Last of all: IndexNow is a crawler courtesy, never a publish
        # requirement, and must never fire against test data (RUNBOOK
        # Incident 1 was exactly this shape of leak — test-mode data reaching
        # a real destination). Both `body.test` and the storage prefix are
        # checked because they are set together by `_test_mode_scope` above,
        # but the prefix is the structural guarantee; this is belt-and-braces.
        if not body.test and not storage.prefix:
            _submit_sunday_indexnow(ep, episode_id, concept)

    return _stage_response("sunday", episode_id, concept, {
        "published": True,
        "dialogue_messages": len(dialogue),
    })


# ---------------------------------------------------------------------------
# Direct-call dispatcher (used by admin run-compressed-week)
# ---------------------------------------------------------------------------


async def execute_cron_stage_stub(stage: str, episode_id: str, concept: str, model: str | None = None) -> dict:
    """SIMULATION ONLY — a LOCAL, dialogue-only stage stub (no HTTP round-trip).

    IMPORTANT: This function does NOT run the real orchestrator or generate
    recipes/images. It records simulated dialogue in the episode JSON for the
    admin 'Run Compressed Week' button in single-worker local dev. Since
    #7936 it runs ONLY against local production data: it refuses cloud
    storage, any storage prefix, a published week and a claimed/publishing
    photo control, so it can never reach Vercel/Blob data or the human photo
    gate. Monday-Saturday are stored as ``complete``; Sunday is stored as
    ``simulated`` (never ``complete``), because the site builder, renderer and
    maintenance scripts read a complete Sunday as a publication. A simulated
    Wednesday keeps the existing photos and review mirror.

    For the real per-stage pipeline, use the /api/cron/{stage} HTTP endpoints.

    Args:
        model: Override dialogue model (e.g. "openai/gpt-5.1"). Falls back to config.
    """
    valid_stages = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    if stage not in valid_stages:
        raise ValueError(f"Unknown cron stage: {stage!r}")
    # Local production data only (#7936, Codex cycle 2). The simulation
    # writes whole stages through the storage singleton with no namespace of
    # its own; offered from a test-namespace page it overwrote production.
    # Refused before anything is read, generated or written.
    has_cloud = getattr(storage, "_has_cloud", None)
    if storage.prefix or (callable(has_cloud) and has_cloud()):
        raise RuntimeError(
            "The compressed-week simulation runs only against local production data; "
            "refused for cloud storage and for a namespaced store."
        )
    # Bypass cron secret verification — caller is already auth'd via admin UI
    ep = _load_or_create_episode(episode_id, concept, stage)
    if episode_is_published(ep):
        # published_at OR a complete Sunday (legacy weeks): the shared predicate
        # the static builder uses, so a historical week is never demoted.
        raise RuntimeError(f"Episode {episode_id} is published; the simulation will not rewrite its history.")
    # A week whose publication is claimed or underway is frozen; a simulated
    # stage must not race Sunday's publishing save (raises PublicationUnderway).
    photo_review.ensure_not_frozen(storage, episode_id)
    ep_concept: str = concept or ep.get("concept") or PLACEHOLDER_CONCEPT
    recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data")
    dialogue = _generate_dialogue(
        stage, ep_concept, model=model,
        recipe_context=_build_recipe_context(recipe_data) or None,
    )
    if not dialogue:
        # _generate_dialogue returns [] when generation failed. Saving that as
        # a "complete" stage would overwrite the episode with an empty day and
        # report success to the admin run; raise so the caller records it.
        raise RuntimeError(
            f"Dialogue simulation produced no messages for {stage}. "
            f"See the Dialogue generation FAILED log line for the cause."
        )
    # Paid photos and their review mirror survive a simulated Wednesday
    # (#7936): the simulation replaces dialogue, never the image set.
    prior = ep.get("stages", {}).get(stage)
    preserved = (
        {k: prior[k] for k in _PRESERVED_ON_FAILURE.get(stage, ()) if k in prior}
        if isinstance(prior, dict) else {}
    )
    # A simulated Sunday is dialogue only. It is stored as "simulated", NOT
    # "complete": the site builder, the renderer, backfill and fix_encoding
    # all read ``stages.sunday.status == "complete"`` as "published" (legacy
    # weeks have no published_at), so a "complete" stub Sunday would publish
    # a local static build past the human photo gate. The other days keep
    # "complete" so the week reads as in progress.
    ep.setdefault("stages", {})[stage] = {
        **preserved,
        "stage": stage,
        "status": "simulated" if stage == "sunday" else "complete",
        "simulation": True,
        "published": False,
        "concept": ep_concept,
        "dialogue": dialogue,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    ep.setdefault("events", []).append(f"{stage}: {'simulated' if stage == 'sunday' else 'complete'} (simulation)")
    storage.save_episode(episode_id, ep)
    return {
        "stage": stage,
        "episode_id": episode_id,
        "dialogue_messages": len(dialogue),
        "mode": "simulation",
        "note": "Simulation-only: no orchestrator run, no recipes/images generated",
    }
