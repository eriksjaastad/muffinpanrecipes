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
  - wednesday: ~3-4 min (3x Stability AI images + dialogue)
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
import hmac
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend.config import config
from backend.publishing.episode_renderer import regenerate_and_upload
from backend.storage import storage
from backend.utils import episode_integrity
from backend.utils.catalog import (
    VALID_CATEGORIES,
    catalog_recipes as _catalog_recipes,
    catalog_titles as _catalog_titles,
    load_published_catalog,
    normalize_category,
)
from backend.utils.logging import get_logger
from backend.utils.muffin_pan_form import check_muffin_pan_form
from backend.utils.recipe_overlap import check_ingredient_overlap
from backend.utils.title_validator import check_title_conflict
from backend.utils.discord import notify_judge_failure, notify_pipeline_failure
from backend.utils.model_router import generate_judge_response, generate_response
from backend.utils.text_sanitize import sanitize_text, has_encoding_issues

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


class ConceptSelectionError(RuntimeError):
    """Monday could not select a real concept for the week.

    Deliberately fatal. A week with no concept has no duplicate avoidance,
    so it must stop at Monday rather than publish something the novelty
    scorer never saw.
    """


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
    """Return the ISO week episode ID, e.g. '2026-W09'."""
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _load_or_create_episode(episode_id: str, concept: str) -> dict:
    ep = storage.load_episode(episode_id)
    if ep:
        return ep
    return {
        "episode_id": episode_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "concept": concept,
        "stages": {},
        "events": [],
        "recipe_id": None,
    }


def _build_recipe_context(recipe_data: dict | None) -> str:
    """One-line recipe summary for dialogue + judge prompts.

    Light by design — title + category + up to 5 hero ingredients. Heavier
    injection causes characters to recite recipe details instead of
    holding a real conversation. Empty string if recipe_data is missing
    or shapeless (e.g., Monday before the baker has run, or test runs
    that skip the baker).
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
    hero_items: list[str] = []
    for ing in (recipe_data.get("ingredients") or [])[:5]:
        if isinstance(ing, dict):
            item = (ing.get("item") or "").strip()
        else:
            item = str(ing).strip()
        # Strip trailing parentheticals/notes so the summary stays short.
        item = item.split("(", 1)[0].strip(", ").strip()
        if item:
            hero_items.append(item)
    summary = " ".join(parts) + "."
    if hero_items:
        summary += f" Key ingredients: {', '.join(hero_items)}."
    return summary


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
    except Exception as e:
        # TRIAGE (#6856): non-fatal HERE, fail-closed in the caller.
        # This helper has two callers with different contracts:
        # _generate_and_judge_dialogue (the cron path) treats an empty list as
        # a hard stage failure, and execute_cron_stage_stub (admin simulation)
        # tolerates it. Returning [] keeps that split honest; do NOT "fix" the
        # cron path by swallowing it further down.
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
    "CHARACTER RULES:\n"
    "- Margaret: Blunt, short sentences, zero fluff, standards enforcer\n"
    "- Steph: Warm, diplomatic, NOT a nervous intern\n"
    "- Julian: Visual thinker, theatrical, cares about light/composition\n"
    "- Marcus: Literary, verbose, metaphor-heavy\n"
    "- Devon: Efficient, understated, speaks only when needed\n"
    "- Ria: Direct, platform-savvy, thinks in hooks and engagement, impatient with process\n\n"
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
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _judge_dialogue(
    concept: str,
    stage: str,
    dialogue: list[dict],
    episode: dict,
    recipe_context: str | None = None,
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
    """
    judge_model = config.judge_model

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

    # Expected cast for today, so cast_coverage is judgeable (#6861/#6832).
    # Lazy import mirrors _get_run_simulation()'s pattern above — the judge
    # path only needs one function out of the simulator module.
    from scripts.simulate_dialogue_week import participants_for_day
    roster_line = f"Expected cast for today ({stage}): {', '.join(participants_for_day(stage))}\n"

    prompt = (
        f"Recipe concept: {concept}\n"
        f"{recipe_section}"
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
        return False, f"JUDGE ERROR: {type(e).__name__}: {e}"

    parsed = _parse_judge_json(raw)
    if parsed is None:
        # One retry with a stricter reminder — models occasionally wrap the
        # JSON in a markdown fence or add a sentence of preamble despite
        # being told not to (#6861).
        retry_prompt = (
            f"{prompt}\n\nCRITICAL: your previous response could not be parsed as JSON. "
            "Return ONLY the JSON object described above. No markdown fences, no prose "
            "before or after it."
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
            return False, f"JUDGE ERROR: {type(e).__name__}: {e}"
        parsed = _parse_judge_json(raw)

    if parsed is None:
        # FAIL CLOSED (#6861). This is the publish gate — an unparseable
        # verdict must never be waved through as a PASS by default.
        reason = "judge output unparseable"
        _record_judge_meta({}, [], reason)
        logger.error(f"Judge output unparseable for {stage} after retry: {raw[:200]!r}")
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
) -> tuple[list[dict], str]:
    """Generate dialogue and run judge. Retry on FAIL up to max_retries.

    Returns (dialogue, verdict_str).
    Raises JudgeFailedError if all retries exhausted — caller should
    save episode as judge_failed and NOT publish.

    Pass recipe_data (typically `episode['stages']['monday']['recipe_data']`)
    to anchor both the simulator and the judge to the actual dish. Without
    it, characters drift off-recipe and the judge can only enforce internal
    consistency.
    """
    verdict = ""
    dialogue: list[dict] = []
    total_attempts = 1 + max_retries
    recipe_context = _build_recipe_context(recipe_data)

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
        )
        if passed:
            # Run QA scoring on the accepted dialogue
            qa_scores = _score_dialogue_qa(dialogue, stage, concept)
            if qa_scores:
                episode.setdefault("qa_scores", {})[stage] = qa_scores
                logger.info(f"QA score for {stage}: {qa_scores.get('score', '?')}/100")
            return dialogue, verdict

        logger.warning(f"Judge FAILED {stage} attempt {attempt + 1}/{total_attempts}: {verdict[:200]}")

    # Exhausted retries — notify Erik and pause the episode
    episode_id = episode.get("episode_id", "unknown")
    notify_judge_failure(
        concept=concept,
        stage=stage,
        verdict=verdict,
        episode_id=episode_id,
        attempts=total_attempts,
    )
    logger.error(f"Judge failed all {total_attempts} attempts for {stage}. Episode paused.")
    raise JudgeFailedError(stage=stage, verdict=verdict, attempts=total_attempts)


# ---------------------------------------------------------------------------
# Editorial QA gate — runs before Sunday publish
# ---------------------------------------------------------------------------

_EDITORIAL_QA_SYSTEM_PROMPT = (
    "You are the editorial director for Muffin Pan Recipes, a food website. "
    "You review every recipe before publication. You care deeply about quality, "
    "originality, and making readers hungry.\n\n"
    "CHECK FOR:\n"
    "1. ENCODING: Any garbled characters, mojibake, or broken symbols (e.g. Ã¢, ÃÂ°)\n"
    "2. PUNCTUATION: Mismatched quotes, missing apostrophes, broken dashes\n"
    "3. REPEATED WORDS: Duplicate adjacent words ('the the', 'and and')\n"
    "4. AI ARTIFACTS: Placeholder text, 'as an AI', instruction-like text that leaked in\n"
    "5. RECIPE COHERENCE: Title, description, ingredients, and instructions should align\n"
    "6. MEASUREMENTS: Temperatures in °F, US customary units (cups, tbsp, tsp, oz, lbs)\n"
    "7. PLAUSIBILITY: Oven temps 250-500°F, cook times reasonable, yields make sense for 12-cup muffin tin\n"
    "8. INGREDIENT-INSTRUCTION MATCH: Every ingredient should be used, no phantom ingredients\n"
    "9. BRAND VOICE: Warm, professional food writing — not robotic or generic\n"
    "10. TITLE RULES (automatic FAIL if violated):\n"
    "    a. Must be 3-6 words. No subtitles, parentheticals, or days of the week.\n"
    "    b. Must sound APPETIZING — like something you'd see on a food magazine cover.\n"
    "       FAIL words: 'prep', 'meal prep', 'make-ahead', 'batch', 'easy', 'quick',\n"
    "       'simple', 'weeknight', 'freezer', 'budget', 'healthy'. These are SEO slop,\n"
    "       not food writing.\n"
    "    c. Must NOT repeat key words from recently published recipes (provided below).\n"
    "       If the title shares a distinctive word (not 'mini'/'cups'/'bites') with\n"
    "       ANY of the last 3 published recipes, it FAILS for lack of variety.\n"
    "    d. The title should evoke the DISH, not the METHOD. 'Roasted Veggie Egg Cups'\n"
    "       is borderline. 'Herbed Vegetable Frittata Cups' is better.\n\n"
    "Respond with EXACTLY this format:\n"
    "STATUS: PASS or FAIL\n"
    "ISSUES: (list each issue on its own line, or 'None' if passing)\n"
    "RECOMMENDATION: (one sentence)\n\n"
    "Be strict. A recipe with ANY encoding issue, factual error, or title rule "
    "violation is an automatic FAIL."
)

MAX_QA_FIX_ATTEMPTS = 2

_RECIPE_FIX_SYSTEM_PROMPT = (
    "You are a meticulous recipe editor. You are given a recipe that failed editorial QA, "
    "along with the specific issues identified by the reviewer.\n\n"
    "Fix ALL identified issues while preserving the recipe's character and intent.\n\n"
    "RULES:\n"
    "1. TITLE: Must be 3-6 words. No subtitles, parentheticals, days of the week, or ellipsis.\n"
    "   The title must sound APPETIZING — like a food magazine cover. Never use utilitarian\n"
    "   words like 'prep', 'meal prep', 'make-ahead', 'batch', 'easy', 'quick', 'simple',\n"
    "   'weeknight', 'freezer', 'budget', 'healthy'. Evoke the DISH, not the METHOD.\n"
    "   If the title repeats key words from recent recipes, choose completely different words.\n"
    "2. INGREDIENTS: Consolidate duplicates. If the same ingredient appears multiple times, "
    "either combine into one entry with the total amount, or group under clear sub-headings "
    "(e.g. 'For the filling:', 'For the topping:').\n"
    "3. INSTRUCTIONS: Must reference every ingredient. Fix any quantity mismatches.\n"
    "4. MUFFIN-PAN FORM: Envision the pan — twelve ROUND, flared wells that mold "
    "the food. The dish must bind into self-contained cups, bites, nests, or mini "
    "loaves that hold shape after removal from the pan. No loose fillings or "
    "recipes where the tin is incidental. The title's shape word must be one a "
    "round muffin cup can produce (Cups, Bites, Nests, Tassies) — NEVER Squares, "
    "Bars, Slabs, Slices, or Wedges; a muffin pan cannot make those shapes.\n"
    "5. MEASUREMENTS: US customary only (cups, tbsp, tsp, oz, lbs, °F).\n"
    "6. Keep the recipe's personality and voice intact.\n\n"
    "Output the fixed recipe in EXACTLY this JSON format:\n"
    "```json\n"
    '{"title": "...", "description": "...", "servings": 12, "prep_time": 15, '
    '"cook_time": 20, "difficulty": "medium", "category": "savory", '
    '"ingredients": [{"item": "...", "amount": "...", "notes": "..."}], '
    '"instructions": ["Step 1...", "Step 2..."], '
    '"chef_notes": "..."}\n'
    "```\n"
    "Return ONLY the JSON block, no other text."
)


def _auto_fix_recipe(episode: dict, qa_report: str) -> bool:
    """Attempt to fix recipe issues identified by editorial QA.

    Modifies episode['stages']['monday']['recipe_data'] in place.
    Returns True if fixes were applied, False if unable to fix.
    """
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    if not recipe:
        return False

    # Format current recipe for the fixer
    ing_text = "\n".join(
        f"- {ing.get('amount', '')} {ing.get('item', '')} ({ing.get('notes', '')})"
        if isinstance(ing, dict) else f"- {ing}"
        for ing in recipe.get("ingredients", [])
    )
    inst_text = "\n".join(
        f"{i+1}. {s}" for i, s in enumerate(recipe.get("instructions", []))
    )
    repetition_guidance = ""
    if "TITLE REPETITION" in qa_report.upper():
        from backend.utils.title_validator import distinctive_title_words

        rejected_title = recipe.get("title", "")
        blocked_words = sorted(distinctive_title_words(rejected_title))
        blocked_text = ", ".join(blocked_words) if blocked_words else rejected_title
        repetition_guidance = (
            "\nTITLE REPETITION FIX:\n"
            f"The current title '{rejected_title}' was rejected for repeating "
            "recently published recipe language. You MUST create a completely "
            "new title with a different food angle. Do not reuse these title "
            f"words: {blocked_text}.\n"
        )

    fix_prompt = (
        f"RECIPE THAT FAILED QA:\n\n"
        f"Title: {recipe.get('title', '')}\n"
        f"Description: {recipe.get('description', '')}\n"
        f"Servings: {recipe.get('servings', 12)}\n"
        f"Prep Time: {recipe.get('prep_time', 15)} mins\n"
        f"Cook Time: {recipe.get('cook_time', 20)} mins\n"
        f"Difficulty: {recipe.get('difficulty', 'medium')}\n"
        f"Category: {recipe.get('category', 'savory')}\n\n"
        f"Ingredients:\n{ing_text}\n\n"
        f"Instructions:\n{inst_text}\n\n"
        f"Chef's Notes: {recipe.get('chef_notes', '')}\n\n"
        f"---\n\n"
        f"QA FAILURE REPORT:\n{qa_report}\n\n"
        f"{repetition_guidance}"
        f"Fix all issues listed above and return the corrected recipe as JSON."
    )

    try:
        response = generate_response(
            prompt=fix_prompt,
            system_prompt=_RECIPE_FIX_SYSTEM_PROMPT,
            model=config.recipe_model,
            temperature=0.3,
        )

        # Extract JSON from response
        import json as _json
        # Try to find JSON block in markdown code fence or raw
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            fixed = _json.loads(json_match.group(1))
        else:
            # Try raw JSON
            fixed = _json.loads(response.strip())

        # Apply title enforcement as extra safety
        from backend.utils.recipe_prompts import _enforce_title_rules
        fixed["title"] = _enforce_title_rules(fixed.get("title", recipe.get("title", "")))

        # Validate we got something reasonable
        if not fixed.get("title") or not fixed.get("ingredients") or not fixed.get("instructions"):
            logger.warning("Auto-fix returned incomplete recipe — skipping")
            return False

        # Update recipe data in place
        monday["recipe_data"] = fixed
        logger.info(f"Auto-fixed recipe: '{fixed['title']}' ({len(fixed['ingredients'])} ingredients)")
        return True

    except Exception as e:
        # TRIAGE (#6856): NON-FATAL, and the surrounding gate fails closed.
        # Returning False breaks the Sunday auto-fix loop, which then raises
        # HTTP 400 + notify_judge_failure because editorial QA never passed.
        # The recipe is left untouched, so nothing half-fixed can publish.
        logger.error(f"Auto-fix failed (recipe left unmodified): {type(e).__name__}: {e}")
        return False


# Sentinel prefix marking a QA report that means "the reviewer never ran",
# as opposed to "the reviewer ran and rejected the recipe". The Sunday loop
# checks for it so it doesn't burn auto-fix LLM calls trying to repair a
# recipe nobody actually reviewed.
QA_UNAVAILABLE_MARKER = "EDITORIAL QA UNAVAILABLE"


def _recent_catalog_titles(limit: int = 8) -> list[str]:
    """Return the most recently published recipe titles, or [] if unavailable.

    Two independent readers, because this feeds a duplicate defense:
      1. the storage layer (respects the test/ prefix, so test runs review
         against test data), and
      2. title_validator.load_catalog_titles, which reads the public blob CDN
         directly and itself falls back to the static src/recipes.json.

    Every failure is logged. The caller alerts when the result is empty.
    """
    try:
        catalog_raw = storage.load_page("pages/recipes.json")
        if catalog_raw:
            catalog = json.loads(catalog_raw)
            titles = [
                str(r.get("title", "")).strip()
                for r in catalog.get("recipes", [])[:limit]
            ]
            titles = [t for t in titles if t]
            if titles:
                return titles
        logger.warning("Catalog read via storage returned no recipe titles")
    except Exception as e:
        logger.error(
            f"Catalog read via storage FAILED: {type(e).__name__}: {e}. "
            f"Falling back to the public CDN reader."
        )

    try:
        from backend.utils.title_validator import load_catalog_titles

        return load_catalog_titles()[:limit]
    except Exception as e:
        logger.error(f"Public-CDN catalog fallback FAILED: {type(e).__name__}: {e}")
        return []


def _editorial_qa_review(episode: dict) -> tuple[bool, str]:
    """Run editorial QA on the complete recipe before publish.

    Returns (passed: bool, report: str).
    """
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})

    title = recipe.get("title", "")
    description = recipe.get("description", "")
    ingredients = recipe.get("ingredients", [])
    instructions = recipe.get("instructions", [])
    chef_notes = recipe.get("chef_notes", "")

    # Pre-check: scan for encoding issues programmatically
    encoding_flags = []
    all_text_fields = [
        ("title", title),
        ("description", description),
        ("chef_notes", chef_notes),
    ]
    for ing in ingredients:
        if isinstance(ing, dict):
            text = f"{ing.get('amount', '')} {ing.get('item', '')} {ing.get('notes', '')}".strip()
        else:
            text = str(ing)
        all_text_fields.append(("ingredient", text))
    for i, step in enumerate(instructions):
        step_text = step if isinstance(step, str) else str(step)
        all_text_fields.append((f"instruction_{i+1}", step_text))

    # Check dialogue too
    for day in DAY_ORDER:
        dialogue = episode.get("stages", {}).get(day, {}).get("dialogue", [])
        for msg in dialogue:
            all_text_fields.append((f"dialogue_{day}", msg.get("message", "")))

    for field_name, text in all_text_fields:
        if has_encoding_issues(text):
            encoding_flags.append(f"Encoding issue in {field_name}: {text[:80]!r}")

    if encoding_flags:
        report = (
            "STATUS: FAIL\n"
            "ISSUES:\n" + "\n".join(f"  - {f}" for f in encoding_flags) + "\n"
            "RECOMMENDATION: Fix encoding issues before publish. "
            "Text contains double-encoded UTF-8 (mojibake)."
        )
        logger.warning(f"Editorial QA FAIL (encoding): {len(encoding_flags)} issues found")
        return False, report

    from backend.utils.muffin_pan_form import check_muffin_pan_form

    form_issue = check_muffin_pan_form(recipe)
    if form_issue:
        report = (
            "STATUS: FAIL\n"
            f"ISSUES:\n  - MUFFIN PAN FORM: {form_issue}\n"
            "RECOMMENDATION: Rewrite the recipe so the muffin tin shapes a "
            "cohesive cup, bite, nest, or mini loaf that holds together after "
            "removal from the pan."
        )
        logger.warning(f"Editorial QA FAIL (muffin-pan form): {form_issue}")
        return False, report

    # Format content for LLM review
    ing_text = "\n".join(
        f"- {ing.get('amount', '')} {ing.get('item', '')}" if isinstance(ing, dict) else f"- {ing}"
        for ing in ingredients
    )
    inst_text = "\n".join(
        f"{i+1}. {s}" if isinstance(s, str) else f"{i+1}. {s}"
        for i, s in enumerate(instructions)
    )

    # Load recently published recipe titles for the repetition check.
    #
    # TRIAGE (#6856): this is a DUPLICATE DEFENSE, so it must never vanish
    # quietly. It used to be a bare `except Exception: pass` with no log line
    # at all — the most invisible failure in this file. When it fired,
    # catalog_context became "" and rule 10c of the QA prompt ("must NOT
    # repeat key words from recently published recipes") silently reviewed
    # against an empty list. Now: the blob read is backed by the public-CDN
    # reader in title_validator (which has its own static fallback), and an
    # empty result on a site that has published before is alerted, not shrugged off.
    recent_titles = _recent_catalog_titles(limit=8)

    catalog_context = ""
    if recent_titles:
        catalog_context = (
            "RECENTLY PUBLISHED RECIPES (check title for repetition):\n"
            + "\n".join(f"  - {t}" for t in recent_titles)
            + "\n\n"
        )
    elif not episode.get("catalog_context_degraded_at"):
        # Alert once per episode, not once per auto-fix retry: this function
        # runs up to three times in the fix loop and the catalog is just as
        # absent each time. The stamp is persisted with the episode, so it
        # also records for later that this publish's duplicate detection was
        # running blind.
        episode["catalog_context_degraded_at"] = datetime.now(timezone.utc).isoformat()
        logger.error(
            "Editorial QA has NO catalog context: the title-repetition rule "
            "is running blind for episode %s",
            episode.get("episode_id"),
        )
        notify_pipeline_failure(
            recipe_id=episode.get("recipe_id") or "unknown",
            concept=episode.get("concept") or "unknown",
            stage="sunday (editorial QA)",
            error_message=(
                "Editorial QA could not load any published recipe titles. "
                "The title-repetition check reviewed this recipe against an "
                "empty catalog — duplicate detection is DEGRADED for this "
                "publish. Verify the recipe title by hand."
            ),
        )

    review_prompt = (
        f"{catalog_context}"
        f"RECIPE TO REVIEW:\n\n"
        f"Title: {title}\n\n"
        f"Description: {description}\n\n"
        f"Ingredients:\n{ing_text}\n\n"
        f"Instructions:\n{inst_text}\n\n"
        f"Chef's Notes: {chef_notes}\n\n"
        f"Review this recipe for publication."
    )

    try:
        verdict = generate_judge_response(
            prompt=review_prompt,
            system_prompt=_EDITORIAL_QA_SYSTEM_PROMPT,
            model=config.judge_model,
            temperature=0.2,
        ).strip()
        passed = "STATUS: PASS" in verdict.upper() or verdict.upper().startswith("PASS")
        logger.info(f"Editorial QA verdict: {verdict[:300]}")
        return passed, verdict
    except Exception as e:
        # FAIL CLOSED (#6505, #6856). This used to return
        # (True, "EDITORIAL QA ERROR (defaulting to PASS)"), so a provider
        # outage on a Sunday published an unreviewed recipe with a green
        # `editorial_qa.passed: true` in the episode JSON. The dialogue judge
        # was made fail-closed in PR #45; the publish gate is now symmetric.
        logger.error(f"Editorial QA review FAILED (treated as FAIL): {type(e).__name__}: {e}")
        return False, (
            "STATUS: FAIL\n"
            f"ISSUES:\n  - {QA_UNAVAILABLE_MARKER}: {type(e).__name__}: {e}\n"
            "RECOMMENDATION: The editorial reviewer could not run, so this "
            "recipe has NOT been reviewed. Do not publish until the reviewer "
            "is reachable; re-fire /api/cron/sunday once it is."
        )


# ---------------------------------------------------------------------------
# Per-character episode memories (#5027)
# ---------------------------------------------------------------------------

_CHARACTERS_DIR = Path(__file__).resolve().parents[1] / "data" / "characters"

_CHAR_SLUG_OVERRIDES: dict[str, str] = {
    "Stephanie 'Steph' Whitmore": "steph-whitmore",
}


def _char_dir_slug(name: str) -> str:
    import re as _re
    if name in _CHAR_SLUG_OVERRIDES:
        return _CHAR_SLUG_OVERRIDES[name]
    return _re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _generate_episode_memories(episode: dict, concept: str) -> None:
    """Generate per-character memories from a completed episode.

    Called after Sunday publish. One LLM call per character (~100 tokens each).
    Memories are stored in backend/data/characters/<slug>/memory.json.
    """
    import json as _json

    all_dialogue: list[dict] = []
    for day in DAY_ORDER:
        day_data = episode.get("stages", {}).get(day, {})
        all_dialogue.extend(day_data.get("dialogue", []))

    if not all_dialogue:
        logger.warning("No dialogue in episode — skipping memory generation")
        return

    # Group messages by character
    by_char: dict[str, list[str]] = {}
    for m in all_dialogue:
        char = m.get("character", "")
        if char:
            by_char.setdefault(char, []).append(f"[{m.get('day', '?')}] {' '.join((m.get('message') or '').split())}")

    week_label = episode.get("episode_id", "unknown")
    model = config.dialogue_model  # cheap model for summaries
    failed_chars: list[str] = []

    for char_name, char_msgs in by_char.items():
        transcript_excerpt = "\n".join(char_msgs[-15:])

        prompt = (
            f"Summarize {char_name}'s week in 2 sentences.\n"
            f"Concept: {concept}\n\n"
            f"Their messages:\n{transcript_excerpt}\n\n"
            "Rules:\n"
            "- Write from their POV in third person past tense\n"
            "- Focus on relationships and emotions, NOT technical specs\n"
            "- Include one specific interpersonal moment\n"
            "- Keep it under 40 words total\n"
            "- Use plain hyphens and straight quotes only"
        )

        try:
            summary = generate_response(
                prompt=prompt,
                system_prompt="You write concise character summaries. Exactly 2 sentences, under 40 words.",
                model=model,
                temperature=0.4,
            ).strip()
            summary = sanitize_text(summary)
        except Exception as e:
            # TRIAGE (#6856): GENUINELY NON-FATAL, logged and recorded.
            # Character memories are flavour for next week's prompts; a gap
            # costs continuity, never correctness, and the publish already
            # happened by the time this runs. Recorded on the episode below
            # so a missing memory is visible in the JSON rather than only in
            # a Lambda log nobody reads.
            logger.error(f"Memory generation failed for {char_name}: {type(e).__name__}: {e}")
            failed_chars.append(char_name)
            continue

        sentences = summary.split(". ")
        key_moment = sentences[-1].rstrip(".") + "." if len(sentences) > 1 else ""

        mem_entry = {
            "week": week_label,
            "concept": concept,
            "summary": summary,
            "key_moment": key_moment,
        }

        slug = _char_dir_slug(char_name)
        mem_path = _CHARACTERS_DIR / slug / "memory.json"
        try:
            data = _json.loads(mem_path.read_text()) if mem_path.exists() else {"episodes": []}
        except (_json.JSONDecodeError, KeyError):
            data = {"episodes": []}

        data["episodes"].append(mem_entry)
        data["episodes"] = data["episodes"][-3:]  # keep last 3
        data["last_updated"] = week_label

        mem_path.parent.mkdir(parents=True, exist_ok=True)
        mem_path.write_text(_json.dumps(data, indent=2))
        logger.info(f"Saved memory for {char_name}: {summary[:80]}")

    if failed_chars:
        episode.setdefault("events", []).append(
            "sunday: memory generation failed for " + ", ".join(sorted(failed_chars))
        )


class StageRequest(BaseModel):
    episode_id: Optional[str] = None   # defaults to current ISO week
    concept: Optional[str] = None      # defaults to stored or generic
    # Breakfast | Savory | Sweet | Party. Only meaningful alongside an explicit
    # concept: it names the shelf the operator chose the dish for (#6858).
    target_category: Optional[str] = None
    model: Optional[str] = None        # override dialogue model (e.g. "openai/gpt-5.1")
    test: bool = False                 # test mode: saves to test/ prefix in blob
    force: bool = False                # skip day-of-week check (manual catch-ups only)


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
    except json.JSONDecodeError:
        return False

    recipe = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})
    title = str(recipe.get("title") or "").strip()
    episode_id = str(ep.get("episode_id") or "")
    if not title or not episode_id:
        return False

    from backend.publishing.episode_renderer import _slugify

    slug = _slugify(title)
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
    from backend.publishing.episode_renderer import _slugify, render_episode_page

    monday = ep.get("stages", {}).get("monday", {})
    recipe_title = monday.get("recipe_data", {}).get("title", "")
    if not recipe_title:
        raise RuntimeError("Sunday publish has no recipe title; cannot write reader page")

    slug = _slugify(recipe_title)
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


def _save_stage_failure(ep: dict, stage: str, error: Exception) -> None:
    """Write a failed-stage marker, persist the episode, and alert.

    The Discord ping is the point (#6856). Before it, a stage that blew up
    wrote {"status": "failed"} into a blob file nobody opens and returned a
    500 to a Vercel cron runner that discards the response. Monday could fail
    on a Monday and the first human signal was Tuesday's 409 — or, if nothing
    downstream tripped, nothing at all.
    """
    ep.setdefault("stages", {})[stage] = {"status": "failed", "error": str(error)}
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
# Monday concept selection (#6855)
# ---------------------------------------------------------------------------

# Two attempts, no sleep. pick_concept() spends one LLM brainstorm call plus at
# most ~6s of optional, concurrent inspiration scraping (#6858), and Monday's
# Lambda budget is 300s; a second pass is worth it for a transient throw (a
# provider blip, a catalog fetch that timed out), a third is not.
_CONCEPT_PICK_ATTEMPTS = 2


def _pick_weekly_concept() -> tuple[str, str | None, str]:
    """Pick this week's concept and target category, or fail closed.

    Returns (concept, target_category, source) where source is "brainstorm"
    or "curated" — the Monday stage records it, so a brainstorm that has been
    failing for weeks shows up in the episode JSON, not only in a Lambda log
    line. Raises ConceptSelectionError rather
    than falling back to PLACEHOLDER_CONCEPT: pick_concept() is where
    duplicate avoidance lives (it scores candidates against the entire
    published catalog), so a week that skips it has one weak defense left.

    pick_concept() no longer depends on third-party recipe sites (#6858):
    candidates come from an LLM brainstorm over the published catalog with a
    curated per-category pool behind it, and the scrape is optional
    inspiration that can fail with no effect. So "sources unreachable" is a
    non-event; what reaches the raise below is "the catalog could not be read"
    or "no candidate survived the category / form / novelty filters" — both of
    which mean nothing is standing between this week and a duplicate.
    """
    try:
        from scripts.pick_concept import pick_concept_candidates, pick_target_category
    except ImportError as exc:
        # NOT transient. scripts/ is excluded from the Vercel bundle except
        # for the explicit re-includes in .vercelignore, and pick_concept.py
        # was not one of them — so this import raised on EVERY production
        # Monday and the old `except Exception` turned it into the
        # placeholder concept. 2026-W30 through 2026-W35 all stored
        # "Weekly Muffin Pan Recipe" with target_category None. See #6855.
        raise ConceptSelectionError(
            f"Concept picker is not importable in this deployment: {exc}. "
            f"This is a packaging bug, not a transient one — check the "
            f"scripts/ re-includes in .vercelignore."
        ) from exc

    last_error: Exception | None = None
    for attempt in range(1, _CONCEPT_PICK_ATTEMPTS + 1):
        try:
            # One catalog read per attempt, shared by the category pick and the
            # concept pick — each used to fetch (and retry) on its own.
            catalog = load_published_catalog()
            target_category = pick_target_category(catalog)
            picks = pick_concept_candidates(
                count=1, target_category=target_category, catalog=catalog,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"Concept pick attempt {attempt}/{_CONCEPT_PICK_ATTEMPTS} "
                f"raised {type(exc).__name__}: {exc}"
            )
            continue

        concept = str(picks[0][1].concept).strip() if picks else ""
        source = str(picks[0][1].source) if picks else ""
        if concept and concept != PLACEHOLDER_CONCEPT:
            if source == "curated":
                # Loud on purpose: the curated pool is eight concepts per shelf.
                # One week of this is fine; several in a row means the LLM
                # brainstorm is broken and variety is silently capped.
                logger.warning(
                    f"Concept {concept!r} came from the CURATED fallback pool — "
                    f"the brainstorm failed or every candidate was rejected. "
                    f"Check the picker if this repeats."
                )
            logger.info(
                f"Auto-selected concept={concept!r}, category={target_category}, "
                f"source={source}"
            )
            return concept, target_category, source

        last_error = ConceptSelectionError(
            "concept picker returned no candidate: every brainstormed and "
            "curated candidate was filtered out by the category, form and "
            "novelty checks"
        )
        logger.warning(
            f"Concept pick attempt {attempt}/{_CONCEPT_PICK_ATTEMPTS} "
            f"produced no usable concept"
        )

    raise ConceptSelectionError(
        f"Could not select a concept after {_CONCEPT_PICK_ATTEMPTS} attempts: "
        f"{type(last_error).__name__}: {last_error}. Re-fire "
        f"/api/cron/monday with an explicit 'concept' in the body to proceed."
    ) from last_error


def _resolve_monday_concept(body: StageRequest, ep: dict) -> tuple[str, str | None, str]:
    """Decide which concept this Monday run uses.

    Returns (concept, target_category, source); source is one of "explicit",
    "stored", "brainstorm" or "curated" and is written to the Monday stage.

    Precedence:
      1. An explicit body.concept always wins — that is the documented manual
         override and the RUNBOOK recovery path. Its category is
         body.target_category, else the stored one, else None — and None is
         resolved AFTER the baker runs, from the baker's own classification.
      2. A real stored concept is kept, so re-firing Monday mid-week does not
         swap the dish out from under stages that already ran.
      3. Otherwise pick fresh.

    A stored PLACEHOLDER_CONCEPT is NOT a real concept, so it never blocks a
    re-pick. That was the second half of the W36 bug: the old line

        concept = body.concept or ep.get("concept") or concept

    let a stored placeholder override a concept we had just picked
    successfully, so re-running Monday on an existing episode could never
    repair the week. `force=true` — already the flag for "I am deliberately
    re-running this stage" — also re-picks.
    """
    stored_category = ep.get("target_category") or ep.get("stages", {}).get(
        "monday", {}
    ).get("target_category")

    if body.concept:
        # An explicit concept is the operator's choice of dish, so its category
        # is the dish's own — never a guess at the thinnest shelf. The old code
        # ran pick_target_category() here, which would have labelled W36's
        # Portuguese custard tarts by whatever shelf happened to be thinnest
        # (#6858, #6877) — and now that Monday enforces the target category on
        # the recipe, that guess would have overwritten the right answer.
        #
        # Precedence: body.target_category (validated to one of
        # VALID_CATEGORIES in _parse_body), then a category already stored on
        # the episode, else None. cron_monday resolves None from the baker's
        # own CATEGORY: line after baking, so target_category is still written
        # — the integrity check reads a null as "the picker never ran", and the
        # RUNBOOK INCIDENT 4 recovery must leave a clean week.
        category = normalize_category(body.target_category) or stored_category
        return body.concept.strip(), category, "explicit"

    stored = str(ep.get("concept") or "").strip()
    if stored and stored != PLACEHOLDER_CONCEPT and not body.force:
        logger.info(
            f"Keeping stored concept {stored!r} for {ep.get('episode_id')} "
            f"(pass force=true or an explicit concept to re-pick)"
        )
        return stored, stored_category, "stored"

    return _pick_weekly_concept()


# ---------------------------------------------------------------------------
# Monday baker gates — title (#5911), form, ingredients (#6854), category (#6858)
# ---------------------------------------------------------------------------

# First bake plus two retries. Same worst case as the old sequential
# title-retry-then-form-retry code (three baker calls), but every gate now runs
# on every attempt, so a retry that satisfies one gate cannot slip past another.
_MAX_BAKER_ATTEMPTS = 3


def _category_from_recipe(recipe_data: dict | None) -> str | None:
    """The baker's own CATEGORY: classification, title-cased, if it is valid."""
    if not isinstance(recipe_data, dict):
        return None
    return normalize_category(recipe_data.get("category"))


def _enforce_target_category(
    recipe_data: dict | None, target_category: str | None
) -> dict | None:
    """Make the recipe's category agree with the category the week was picked for.

    The picker chooses the concept FOR a category (the thinnest shelf), so the
    category is decided before the baker runs. The baker's CATEGORY: line is
    its own classification of its output, and it gets that wrong: W36's
    Portuguese custard tart came back "savory" (#6877). The Sunday publisher
    copies recipe_data.category into the catalog verbatim, so a wrong label
    both mis-shelves the recipe on /recipes and under-reports the count the
    next category pick reads — the imbalance quietly feeds itself (#6858).

    No-op without a target (explicit-concept path with no category supplied
    or stored): then the baker's classification is the honest one and stands.
    """
    if not isinstance(recipe_data, dict) or not target_category:
        return recipe_data
    want = target_category.strip().lower()
    have = str(recipe_data.get("category") or "").strip().lower()
    if have != want:
        logger.warning(
            f"Baker labelled '{recipe_data.get('title', '')}' as "
            f"{have or 'nothing'!r}; overriding to this week's target category "
            f"{want!r}"
        )
        recipe_data["category"] = want
    return recipe_data


def _bake_through_gates(
    orchestrator,
    ep: dict,
    concept: str,
    target_category: str | None,
    recent_cuisines: list[str],
    catalog: dict,
) -> tuple[dict, list[dict]]:
    """Run the baker until its recipe clears every Monday gate, or fail closed.

    Gates, on EVERY attempt, in order:
      1. title uniqueness against the catalog (#5911 — deliberately relaxed
         threshold; read RUNBOOK INCIDENT 3 before touching it),
      2. muffin-pan form (the pan must shape the food),
      3. ingredient overlap against the catalog (#6854) — the gate that would
         have caught W36, whose title was fine and whose dish was not.

    Each failure appends a targeted constraint to the concept for the next
    attempt, so the baker is told exactly what to change. After
    _MAX_BAKER_ATTEMPTS the last failure is raised as RuntimeError, which
    _run_stage turns into a failed-stage record, an alert and a 500. Nothing
    off-brand or duplicate is ever written to the episode.

    Returns (recipe_data, gate_trace). The trace is stored on the stage so the
    episode JSON shows that the gates ran and what they saw — the lesson of
    INCIDENT 4 is that a duplicate check which skips itself leaves no mark.
    """
    catalog_titles = _catalog_titles(catalog)
    catalog_recipes = _catalog_recipes(catalog)
    episode_id = str(ep.get("episode_id") or "")

    constraints: list[str] = []
    trace: list[dict] = []
    last_failure = "baker never ran"

    for attempt in range(1, _MAX_BAKER_ATTEMPTS + 1):
        attempt_concept = (
            concept if not constraints else f"{concept}. " + " ".join(constraints)
        )
        recipe_data = orchestrator._execute_stage_baker(
            ep["recipe_id"], attempt_concept, target_category=target_category,
            recent_cuisines=recent_cuisines,
        )
        recipe_data = _enforce_target_category(recipe_data, target_category)
        baker_title = recipe_data.get("title", "") if recipe_data else ""

        conflict = check_title_conflict(baker_title, catalog_titles)
        if conflict:
            last_failure = f"duplicate title '{baker_title}': {conflict}"
            trace.append({"attempt": attempt, "gate": "title", "result": conflict})
            logger.warning(
                f"Baker attempt {attempt}: {last_failure}. Retrying with an "
                f"explicit blacklist."
            )
            blacklist = ", ".join(f"'{t}'" for t in catalog_titles[:15])
            constraints.append(
                f"CRITICAL: the title must NOT be similar to any of these "
                f"already-published recipes: {blacklist}. Pick a distinctive "
                f"angle with different key words."
            )
            continue

        form_issue = check_muffin_pan_form(recipe_data)
        if form_issue:
            last_failure = f"off-brand muffin-pan form '{baker_title}': {form_issue}"
            trace.append({"attempt": attempt, "gate": "form", "result": form_issue})
            logger.warning(
                f"Baker attempt {attempt}: {last_failure}. Retrying with explicit "
                f"form constraints."
            )
            constraints.append(
                "CRITICAL MUFFIN-PAN FORM: the finished dish must bind into "
                "self-contained cups, bites, nests, or mini loaves that hold "
                "shape after removal from the pan. The muffin tin must shape "
                "the food, not merely portion loose fillings. Previous attempt "
                f"failed because: {form_issue}."
            )
            continue

        verdict = check_ingredient_overlap(
            recipe_data, catalog_recipes, exclude_episode_id=episode_id or None,
        )
        best = verdict.best
        trace.append({
            "attempt": attempt,
            "gate": "ingredients",
            "status": verdict.status,
            "new_items": verdict.new_items,
            "closest": best.title if best else None,
            "score": best.score if best else None,
        })
        if verdict.status == "duplicate" and best is not None:
            last_failure = (
                f"same dish as a published recipe — '{baker_title}': {verdict.reason}"
            )
            logger.warning(
                f"Baker attempt {attempt}: {last_failure}. Retrying with the "
                f"match named."
            )
            shared = ", ".join(best.shared[:8])
            constraints.append(
                f"CRITICAL: an earlier attempt produced the same dish as the "
                f"already-published '{best.title}' (ingredient overlap "
                f"{best.score:.0%}; shared: {shared}). This week's recipe must "
                f"be a genuinely different dish — change the core ingredients "
                f"and the construction, not just the name."
            )
            continue
        if verdict.status == "skipped_thin":
            # Visible, not silent: a recipe too thin to compare is itself a
            # quality signal, and the trace above records the skip.
            logger.warning(
                f"Ingredient gate skipped for '{baker_title}': {verdict.reason}"
            )

        if attempt > 1:
            logger.info(f"Baker attempt {attempt} cleared every gate: '{baker_title}'")
        return recipe_data, trace

    raise RuntimeError(
        f"Baker could not clear the Monday gates in {_MAX_BAKER_ATTEMPTS} "
        f"attempts. Last failure: {last_failure}. Re-fire /api/cron/monday "
        f"with an explicit 'concept' in the body to bypass auto-pick."
    )


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
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)

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
            injected_event=injected_event,
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
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "tuesday")

      with _run_stage(ep, "tuesday"):
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "tuesday", concept, ep, model=body.model,
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
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "wednesday")
      recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})

      with _run_stage(ep, "wednesday"):
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        photography_result = orchestrator._execute_stage_photography(recipe_id, recipe_data)
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

        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "wednesday", concept, ep,
            image_paths=image_paths,
            photography_context=photography_result if isinstance(photography_result, dict) else None,
            model=body.model,
            recipe_data=ep.get("stages", {}).get("monday", {}).get("recipe_data"),
        )

        ep["stages"]["wednesday"] = {
            "stage": "photography",
            "status": "complete",
            "concept": concept,
            "photography_data": photography_result,
            "reshoot_happened": photography_result.get("reshoot_happened", False) if isinstance(photography_result, dict) else False,
            "image_paths": image_paths,
            "image_urls": image_urls,
            "image_status": "auto_selected",
            "confirmed_winner": photography_result.get("winner", {}) if isinstance(photography_result, dict) else {},
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "wednesday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["image_paths"] = image_paths
        ep["image_urls"] = image_urls
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
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT
      _require_monday_recipe(ep, "thursday")
      recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})

      with _run_stage(ep, "thursday"):
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        copy_text = orchestrator._execute_stage_copywriting(recipe_id, concept, recipe_data)
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "thursday", concept, ep, model=body.model,
            recipe_data=recipe_data,
        )

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
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)
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
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "friday", concept, ep,
            photography_context=friday_photo_ctx,
            model=body.model,
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
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)
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


# ---------------------------------------------------------------------------
# Sunday — Publish
# ---------------------------------------------------------------------------

@router.api_route("/sunday", methods=["GET", "POST"])
async def cron_sunday(request: Request):
    _verify_cron_secret(request)
    body = await _parse_body(request)
    _verify_day_of_week(request.url.path.rstrip("/").rsplit("/", 1)[-1], body)
    with _test_mode_scope(body):
      episode_id = body.episode_id or _current_episode_id()
      ep = _load_or_create_episode(episode_id, body.concept or PLACEHOLDER_CONCEPT)
      concept: str = body.concept or ep.get("concept") or PLACEHOLDER_CONCEPT

      if ep.get("published_at"):
        handoff_status = _static_deploy_state(ep).get("status")
        if handoff_status == "pending" or (
            handoff_status == "failed"
            and _static_deploy_state(ep).get("phase") == "sources"
        ):
            _complete_static_source_handoff(episode_id, ep)
        sunday_stage = ep.get("stages", {}).get("sunday", {})
        return _stage_response("sunday", episode_id, concept, {
            "published": True,
            "already_published": True,
            "published_at": ep.get("published_at"),
            "dialogue_messages": len(sunday_stage.get("dialogue", [])),
        })

      _require_monday_recipe(ep, "sunday")

      with _run_stage(ep, "sunday"):
        # Verify critical prior stages completed before spending on dialogue.
        # (Monday is already enforced by _require_monday_recipe above.)
        required_stages = ["wednesday"]
        for day in required_stages:
            stage_status = ep.get("stages", {}).get(day, {}).get("status")
            if stage_status != "complete":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot publish: {day} stage incomplete (status={stage_status!r})",
                )

        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "sunday", concept, ep, model=body.model,
            recipe_data=ep.get("stages", {}).get("monday", {}).get("recipe_data"),
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

        # Wire winner image into recipe featured_photo
        wed_stage = ep.get("stages", {}).get("wednesday", {})
        confirmed_winner = wed_stage.get("confirmed_winner", {})
        featured_image_path = confirmed_winner.get("featured_image", "")
        if featured_image_path:
            # Strip src/ prefix for web serving
            web_image_path = featured_image_path.removeprefix("src/")
            recipe_id = ep.get("recipe_id")
            if recipe_id:
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
                            # takes its hero from ep["image_urls"], which the
                            # Wednesday gate above guarantees. So the publish
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

        # Post-publish cleanup: trash variant directories if image was confirmed/overridden
        image_status = wed_stage.get("image_status", "")
        published_image_cleaned = False
        if image_status in ("confirmed", "overridden"):
            recipe_id = ep.get("recipe_id")
            if recipe_id:
                try:
                    cleaned = storage.cleanup_image_variants(recipe_id)
                    if cleaned:
                        logger.info(f"Cleaned up image variants for {recipe_id}: {cleaned}")
                        wed_stage["image_status"] = "cleaned"
                        published_image_cleaned = True
                except Exception as e:
                    # TRIAGE (#6856): GENUINELY NON-FATAL, logged. This only
                    # trashes losing image variants after the winner is
                    # published. Failing leaves orphaned blobs — a storage
                    # cost, never a reader-visible defect — and blocking the
                    # publish over it would be strictly worse. Not alerted:
                    # a weekly ping about janitorial work is how alerts get
                    # ignored. scripts/cleanup_image_backlog.py sweeps these.
                    logger.error(
                        f"Image cleanup failed for {recipe_id} (orphaned variants "
                        f"left in blob): {type(e).__name__}: {e}"
                    )

        ep["published_at"] = datetime.now(timezone.utc).isoformat()
        ep["stages"]["sunday"] = {
            "stage": "publish",
            "status": "complete",
            "concept": concept,
            "published": True,
            "image_cleaned": published_image_cleaned,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            **_judge_meta_fields(ep, "sunday"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("sunday: complete (published)")

        # Generate per-character memories from the week's dialogue (#5027)
        try:
            _generate_episode_memories(ep, concept)
            ep["events"].append("sunday: memories generated")
        except Exception as e:
            # TRIAGE (#6856): GENUINELY NON-FATAL, logged and recorded. This
            # runs AFTER published_at is set — the recipe is already live and
            # correct. Character memories only feed next week's prompts, and
            # per-character failures are already handled inside; this outer
            # guard catches a total failure (e.g. a read-only filesystem).
            # Recorded as an episode event so it is visible in the JSON.
            logger.error(f"Memory generation failed (non-fatal): {type(e).__name__}: {e}")
            ep["events"].append(f"sunday: memory generation failed ({type(e).__name__})")

        # Persist the published episode before writing the catalog so a crash
        # between authoritative writes and the manual deployment handoff is
        # retryable.
        _set_static_deploy_state(ep, "pending")
        storage.save_episode(episode_id, ep)
        _complete_static_source_handoff(episode_id, ep)

    return _stage_response("sunday", episode_id, concept, {
        "published": True,
        "dialogue_messages": len(dialogue),
    })


# ---------------------------------------------------------------------------
# Direct-call dispatcher (used by admin run-compressed-week)
# ---------------------------------------------------------------------------


async def execute_cron_stage_stub(stage: str, episode_id: str, concept: str, model: str | None = None) -> dict:
    """SIMULATION ONLY — Execute a cron stage stub in-process (no HTTP round-trip).

    IMPORTANT: This function does NOT run the real orchestrator or generate
    recipes/images. It records dialogue simulation and marks the stage complete
    in the episode JSON. It exists so the admin 'Run Compressed Week' button
    works on Vercel (localhost self-calls fail) and in single-worker dev
    (no deadlock), simulating a week without actual pipeline costs.

    For the real per-stage pipeline, use the /api/cron/{stage} HTTP endpoints.

    Args:
        model: Override dialogue model (e.g. "openai/gpt-5.1"). Falls back to config.
    """
    valid_stages = {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}
    if stage not in valid_stages:
        raise ValueError(f"Unknown cron stage: {stage!r}")
    # Bypass cron secret verification — caller is already auth'd via admin UI
    ep = _load_or_create_episode(episode_id, concept)
    ep_concept: str = concept or ep.get("concept") or PLACEHOLDER_CONCEPT
    recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data")
    dialogue = _generate_dialogue(
        stage, ep_concept, model=model,
        recipe_context=_build_recipe_context(recipe_data) or None,
    )
    ep.setdefault("stages", {})[stage] = {
        "stage": stage,
        "status": "complete",
        "concept": ep_concept,
        "dialogue": dialogue,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    ep.setdefault("events", []).append(f"{stage}: complete")
    storage.save_episode(episode_id, ep)
    return {
        "stage": stage,
        "episode_id": episode_id,
        "dialogue_messages": len(dialogue),
        "mode": "simulation",
        "note": "Simulation-only: no orchestrator run, no recipes/images generated",
    }
