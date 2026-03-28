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
from backend.utils.logging import get_logger
from backend.utils.discord import notify_judge_failure
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

    cron_secret = os.environ.get("CRON_SECRET", "")
    if not cron_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="CRON_SECRET env var not configured",
        )

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


def _generate_dialogue(
    stage: str,
    concept: str,
    image_paths: list[str] | None = None,
    photography_context: dict | None = None,
    model: str | None = None,
) -> list[dict]:
    """Run dialogue simulator for a single stage. Non-fatal on failure.

    Args:
        model: Override dialogue model. Pass from API request body.
               Defaults to config.dialogue_model (DIALOGUE_MODEL env / Doppler).
    """
    use_model = model or config.dialogue_model
    try:
        run_simulation = _get_run_simulation()
        result = run_simulation(
            concept=concept,
            default_model=use_model,
            run_index=1,
            stage_only=stage,
            injected_event=None,
            ticks_per_day=0,
            mode="openai",
            prompt_style="scene",
            character_models=None,
            image_paths=image_paths or [],
            photography_context=photography_context,
        )
        return result.get("messages", [])
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Dialogue generation FAILED for stage={stage}: {type(e).__name__}: {e}\n{tb}")
        return []


DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

_JUDGE_SYSTEM_PROMPT = (
    "You are a senior editorial judge for a food content site. "
    "5 characters (Margaret, Steph, Julian, Marcus, Devon) collaborate on a muffin-tin recipe each week.\n\n"
    "CHARACTER RULES:\n"
    "- Margaret: Blunt, short sentences, zero fluff, standards enforcer\n"
    "- Steph: Warm, diplomatic, NOT a nervous intern\n"
    "- Julian: Visual thinker, theatrical, cares about light/composition\n"
    "- Marcus: Literary, verbose, metaphor-heavy\n"
    "- Devon: Efficient, understated, speaks only when needed\n\n"
    "CHECK FOR:\n"
    "1. HALLUCINATIONS: Wrong ingredients/details not matching the concept\n"
    "2. CHARACTER BREAKS: Someone wildly out of character\n"
    "3. CONTINUITY: References to previous days must be accurate\n"
    "4. NATURALNESS: Should sound like real coworkers\n\n"
    "Respond with EXACTLY one line: PASS or FAIL followed by a brief reason.\n"
    "Example: PASS - Characters are distinct, concept is consistent, good tension.\n"
    "Example: FAIL - Margaret mentions 'brown butter' but this is a corn dog recipe."
)


def _judge_dialogue(
    concept: str,
    stage: str,
    dialogue: list[dict],
    episode: dict,
) -> tuple[bool, str]:
    """Judge today's dialogue with growing context from previous days.

    Returns (passed: bool, verdict: str).
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

    prompt = (
        f"Recipe concept: {concept}\n\n"
        f"{context_section}"
        f"TODAY IS {stage.upper()}:\n"
        + "\n".join(today_lines)
        + "\n\nJudge this day. One line: PASS or FAIL with reason."
    )

    try:
        verdict = generate_judge_response(
            prompt=prompt,
            system_prompt=_JUDGE_SYSTEM_PROMPT,
            model=judge_model,
            temperature=0.2,
        ).strip()
        passed = verdict.upper().startswith("PASS")
        logger.info(f"Judge verdict for {stage}: {verdict[:200]}")
        return passed, verdict
    except Exception as e:
        logger.warning(f"Judge failed for {stage}, defaulting to PASS: {e}")
        return True, f"JUDGE ERROR (defaulting to PASS): {e}"


class JudgeFailedError(Exception):
    """Raised when dialogue fails judge review after all retries."""
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
        logger.warning(f"QA scoring failed (non-fatal): {e}")
        return {}


def _generate_and_judge_dialogue(
    stage: str,
    concept: str,
    episode: dict,
    model: str | None = None,
    image_paths: list[str] | None = None,
    photography_context: dict | None = None,
    max_retries: int = 2,
) -> tuple[list[dict], str]:
    """Generate dialogue and run judge. Retry on FAIL up to max_retries.

    Returns (dialogue, verdict_str).
    Raises JudgeFailedError if all retries exhausted — caller should
    save episode as judge_failed and NOT publish.
    """
    verdict = ""
    dialogue: list[dict] = []
    total_attempts = 1 + max_retries

    for attempt in range(total_attempts):
        dialogue = _generate_dialogue(
            stage, concept,
            image_paths=image_paths,
            photography_context=photography_context,
            model=model,
        )
        if not dialogue:
            return dialogue, "NO DIALOGUE GENERATED"

        passed, verdict = _judge_dialogue(concept, stage, dialogue, episode)
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
    "You are a meticulous editorial proofreader for a food recipe website. "
    "Review the recipe content below for quality before it goes live.\n\n"
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
    "10. TITLE: Must be 3-6 words, no subtitles, no parentheticals, no days of the week\n\n"
    "Respond with EXACTLY this format:\n"
    "STATUS: PASS or FAIL\n"
    "ISSUES: (list each issue on its own line, or 'None' if passing)\n"
    "RECOMMENDATION: (one sentence)\n\n"
    "Be strict. A recipe with ANY encoding issue or factual error is an automatic FAIL."
)

MAX_QA_FIX_ATTEMPTS = 2

_RECIPE_FIX_SYSTEM_PROMPT = (
    "You are a meticulous recipe editor. You are given a recipe that failed editorial QA, "
    "along with the specific issues identified by the reviewer.\n\n"
    "Fix ALL identified issues while preserving the recipe's character and intent.\n\n"
    "RULES:\n"
    "1. TITLE: Must be 3-6 words. No subtitles, parentheticals, days of the week, or ellipsis.\n"
    "2. INGREDIENTS: Consolidate duplicates. If the same ingredient appears multiple times, "
    "either combine into one entry with the total amount, or group under clear sub-headings "
    "(e.g. 'For the filling:', 'For the topping:').\n"
    "3. INSTRUCTIONS: Must reference every ingredient. Fix any quantity mismatches.\n"
    "4. MEASUREMENTS: US customary only (cups, tbsp, tsp, oz, lbs, °F).\n"
    "5. Keep the recipe's personality and voice intact.\n\n"
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
        logger.warning(f"Auto-fix failed: {e}")
        return False


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

    # Format content for LLM review
    ing_text = "\n".join(
        f"- {ing.get('amount', '')} {ing.get('item', '')}" if isinstance(ing, dict) else f"- {ing}"
        for ing in ingredients
    )
    inst_text = "\n".join(
        f"{i+1}. {s}" if isinstance(s, str) else f"{i+1}. {s}"
        for i, s in enumerate(instructions)
    )

    review_prompt = (
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
        logger.warning(f"Editorial QA review failed, defaulting to PASS: {e}")
        return True, f"EDITORIAL QA ERROR (defaulting to PASS): {e}"


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
            logger.warning(f"Memory generation failed for {char_name}: {e}")
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


class StageRequest(BaseModel):
    episode_id: Optional[str] = None   # defaults to current ISO week
    concept: Optional[str] = None      # defaults to stored or generic
    model: Optional[str] = None        # override dialogue model (e.g. "openai/gpt-5.1")
    test: bool = False                 # test mode: saves to test/ prefix in blob
    force: bool = False                # skip day-of-week check (manual catch-ups only)


async def _parse_body(request: Request) -> StageRequest:
    """Parse JSON body from POST, return defaults for GET (Vercel cron sends GET)."""
    if request.method == "POST":
        try:
            data = await request.json()
            return StageRequest(**data)
        except Exception:
            return StageRequest()
    return StageRequest()


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


def _configure_test_mode(body: StageRequest) -> None:
    """Set storage prefix for test mode. Resets to production on every call."""
    storage.set_prefix("test/" if body.test else "")


def _save_stage_failure(ep: dict, stage: str, error: Exception) -> None:
    """Write a failed-stage marker and persist the episode."""
    ep.setdefault("stages", {})[stage] = {"status": "failed", "error": str(error)}
    storage.save_episode(ep["episode_id"], ep)



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
        raise  # let explicit HTTP errors through unchanged
    except Exception as e:
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
    _configure_test_mode(body)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
    ep["concept"] = concept

    with _run_stage(ep, "monday"):
        import uuid
        if not ep.get("recipe_id"):
            ep["recipe_id"] = str(uuid.uuid4())[:8]

        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        # Note: active_recipes check removed — a new orchestrator instance is created
        # per request so active_recipes is always empty. start_recipe is idempotent.
        orchestrator.pipeline.start_recipe(ep["recipe_id"], concept)

        recipe_data = orchestrator._execute_stage_baker(ep["recipe_id"], concept)
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "monday", concept, ep, model=body.model,
        )

        ep["stages"]["monday"] = {
            "stage": "brainstorm",
            "status": "complete",
            "concept": concept,
            "recipe_data": recipe_data,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("monday: complete")
        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

    return _stage_response("monday", episode_id, concept, {
        "recipe_title": recipe_data.get("title", concept) if recipe_data else concept,
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
    _configure_test_mode(body)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

    with _run_stage(ep, "tuesday"):
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "tuesday", concept, ep, model=body.model,
        )
        ep["stages"]["tuesday"] = {
            "stage": "recipe_development",
            "status": "complete",
            "concept": concept,
            "recipe_data_ref": "from monday stage",
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
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
    _configure_test_mode(body)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
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

        image_urls = []
        for canonical_path in image_paths:
            local_path = local_to_canonical.get(canonical_path, "")
            if not local_path:
                logger.warning(f"No local_path for {canonical_path}")
                image_urls.append("")
                continue
            try:
                lp = Path(local_path)
                if lp.exists():
                    blob_url = storage.save_image(canonical_path, lp.read_bytes())
                    image_urls.append(blob_url)
                else:
                    logger.warning(f"Image file missing: {lp}")
                    image_urls.append("")
            except Exception as e:
                logger.warning(f"Failed to upload image {canonical_path}: {e}")
                image_urls.append("")

        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "wednesday", concept, ep,
            image_paths=image_paths,
            photography_context=photography_result if isinstance(photography_result, dict) else None,
            model=body.model,
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
    _configure_test_mode(body)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
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
        )

        ep["stages"]["thursday"] = {
            "stage": "copywriting",
            "status": "complete",
            "concept": concept,
            "copy_text": copy_text,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
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
    _configure_test_mode(body)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

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
        )

        ep["stages"]["friday"] = {
            "stage": "final_review",
            "status": "complete",
            "concept": concept,
            "approved": approved,
            "review_data": review_output,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
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
    _configure_test_mode(body)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

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
        )

        ep["stages"]["saturday"] = {
            "stage": "deployment",
            "status": "complete",
            "concept": concept,
            "deployment_status": "staged",
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
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
    _configure_test_mode(body)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

    with _run_stage(ep, "sunday"):
        dialogue, judge_verdict = _generate_and_judge_dialogue(
            "sunday", concept, ep, model=body.model,
        )

        # Verify critical prior stages completed before publishing
        required_stages = ["monday", "wednesday"]
        for day in required_stages:
            stage_status = ep.get("stages", {}).get(day, {}).get("status")
            if stage_status != "complete":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot publish: {day} stage incomplete (status={stage_status!r})",
                )

        # Editorial QA gate with auto-fix retry loop
        qa_passed, qa_report = _editorial_qa_review(ep)
        fix_attempts = 0
        while not qa_passed and fix_attempts < MAX_QA_FIX_ATTEMPTS:
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
                            logger.warning(f"Failed to set featured_photo on recipe {recipe_id}: {e}")
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
                    logger.warning(f"Image cleanup failed for {recipe_id}: {e}")

        ep["published_at"] = datetime.now(timezone.utc).isoformat()
        ep["stages"]["sunday"] = {
            "stage": "publish",
            "status": "complete",
            "concept": concept,
            "published": True,
            "image_cleaned": published_image_cleaned,
            "dialogue": dialogue,
            "judge_verdict": judge_verdict,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("sunday: complete (published)")

        # Generate per-character memories from the week's dialogue (#5027)
        try:
            _generate_episode_memories(ep, concept)
            ep["events"].append("sunday: memories generated")
        except Exception as e:
            logger.warning(f"Memory generation failed (non-fatal): {e}")

        storage.save_episode(episode_id, ep)
        regenerate_and_upload(ep)

        # Publish to the main page recipe catalog
        from backend.publishing.episode_renderer import publish_recipe_to_catalog
        try:
            publish_recipe_to_catalog(ep)
        except Exception as e:
            logger.warning(f"Recipe catalog publish failed (non-fatal): {e}")

        # Upload the recipe's standalone page (render fresh, don't copy from blob
        # to avoid encoding round-trip issues)
        from backend.publishing.episode_renderer import render_episode_page, _slugify
        monday = ep.get("stages", {}).get("monday", {})
        recipe_title = monday.get("recipe_data", {}).get("title", "")
        if recipe_title:
            slug = _slugify(recipe_title)
            image_urls = ep.get("image_urls", [])
            recipe_image = image_urls[0] if image_urls else None
            recipe_html = render_episode_page(ep, image_url=recipe_image)
            storage.save_page(f"pages/recipes/{slug}/index.html", recipe_html)
            logger.info(f"Published recipe page at /recipes/{slug}")

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
    ep_concept: str = concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
    dialogue = _generate_dialogue(stage, ep_concept, model=model)
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

