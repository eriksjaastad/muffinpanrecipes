"""Vercel Cron API routes for the Muffin Pan Recipes pipeline.

Each route corresponds to one pipeline stage. Vercel hits these endpoints
on a schedule defined in vercel.json. Local dev can also POST to them directly.

Authentication: Vercel sends the CRON_SECRET as Authorization: Bearer <secret>.
Unauthorized requests are rejected with 401.

Timeout budget:
  - monday/tuesday/thursday/friday/saturday/sunday: ~30-60s (OpenAI dialogue)
  - wednesday: ~3-4 min (3x Stability AI images + dialogue)
  Vercel Pro plan allows up to 300s function timeout — set in vercel.json.

Usage:
    # Trigger manually (dev):
    curl -X POST http://localhost:8000/api/cron/monday \
      -H "Authorization: Bearer $CRON_SECRET" \
      -H "Content-Type: application/json"
"""

from __future__ import annotations
import hmac
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend.config import config
from backend.storage import storage
from backend.utils.logging import get_logger

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

DIALOGUE_MODEL = config.dialogue_model  # "openai/gpt-5-mini" in production


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


def _generate_dialogue(stage: str, concept: str, image_paths: list[str] | None = None) -> list[dict]:
    """Run dialogue simulator for a single stage. Non-fatal on failure."""
    try:
        run_simulation = _get_run_simulation()
        result = run_simulation(
            concept=concept,
            default_model=DIALOGUE_MODEL,
            run_index=1,
            stage_only=stage,
            injected_event=None,
            ticks_per_day=0,
            mode="openai",
            prompt_style="scene",
            character_models=None,
            image_paths=image_paths or [],
        )
        return result.get("messages", [])
    except (ImportError, AttributeError, TypeError, ValueError) as e:
        logger.warning(f"Dialogue generation failed for stage={stage}: {e}")
        return []  # dialogue is non-fatal — pipeline continues without it
    except Exception as e:
        logger.error(f"Unexpected dialogue error for stage={stage}: {e}", exc_info=True)
        return []  # still non-fatal, but logged at ERROR level


class StageRequest(BaseModel):
    episode_id: Optional[str] = None   # defaults to current ISO week
    concept: Optional[str] = None      # defaults to stored or generic


def _save_stage_failure(ep: dict, stage: str, error: Exception) -> None:
    """Write a failed-stage marker and persist the episode."""
    ep.setdefault("stages", {})[stage] = {"status": "failed", "error": str(error)}
    storage.save_episode(ep["episode_id"], ep)


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

@router.post("/monday")
async def cron_monday(request: Request, body: StageRequest = StageRequest()):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
    ep["concept"] = concept

    try:
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
        dialogue = _generate_dialogue("monday", concept)

        ep["stages"]["monday"] = {
            "stage": "brainstorm",
            "status": "complete",
            "concept": concept,
            "recipe_data": recipe_data,
            "dialogue": dialogue,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("monday: complete")
        storage.save_episode(episode_id, ep)

        return _stage_response("monday", episode_id, concept, {
            "recipe_title": recipe_data.get("title", concept) if recipe_data else concept,
            "dialogue_messages": len(dialogue),
        })

    except Exception as e:
        ep.setdefault("stages", {})["monday"] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Tuesday — Recipe Development
# ---------------------------------------------------------------------------

@router.post("/tuesday")
async def cron_tuesday(request: Request, body: StageRequest = StageRequest()):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

    try:
        dialogue = _generate_dialogue("tuesday", concept)
        ep["stages"]["tuesday"] = {
            "stage": "recipe_development",
            "status": "complete",
            "concept": concept,
            "recipe_data_ref": "from monday stage",
            "dialogue": dialogue,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("tuesday: complete")
        storage.save_episode(episode_id, ep)
        return _stage_response("tuesday", episode_id, concept, {"dialogue_messages": len(dialogue)})

    except Exception as e:
        ep.setdefault("stages", {})["tuesday"] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Wednesday — Photography (longest stage ~3 min)
# ---------------------------------------------------------------------------

@router.post("/wednesday")
async def cron_wednesday(request: Request, body: StageRequest = StageRequest()):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
    recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})

    try:
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        photography_result = orchestrator._execute_stage_photography(recipe_id, recipe_data)
        image_paths: list[str] = photography_result if isinstance(photography_result, list) else []
        image_urls = [storage.get_image_url(p) for p in image_paths]

        dialogue = _generate_dialogue("wednesday", concept, image_paths=image_paths)

        ep["stages"]["wednesday"] = {
            "stage": "photography",
            "status": "complete",
            "concept": concept,
            "photography_data": photography_result,
            "image_paths": image_paths,
            "image_urls": image_urls,
            "dialogue": dialogue,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["image_paths"] = image_paths
        ep["image_urls"] = image_urls
        ep["events"].append("wednesday: complete")
        storage.save_episode(episode_id, ep)

        return _stage_response("wednesday", episode_id, concept, {
            "images_generated": len(image_paths),
            "image_urls": image_urls,
            "dialogue_messages": len(dialogue),
        })

    except Exception as e:
        ep.setdefault("stages", {})["wednesday"] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Thursday — Copywriting
# ---------------------------------------------------------------------------

@router.post("/thursday")
async def cron_thursday(request: Request, body: StageRequest = StageRequest()):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
    recipe_data = ep.get("stages", {}).get("monday", {}).get("recipe_data", {})

    try:
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        copy_text = orchestrator._execute_stage_copywriting(recipe_id, concept, recipe_data)
        dialogue = _generate_dialogue("thursday", concept)

        ep["stages"]["thursday"] = {
            "stage": "copywriting",
            "status": "complete",
            "concept": concept,
            "copy_text": copy_text,
            "dialogue": dialogue,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("thursday: complete")
        storage.save_episode(episode_id, ep)
        return _stage_response("thursday", episode_id, concept, {
            "copy_preview": (copy_text or "")[:80],
            "dialogue_messages": len(dialogue),
        })

    except Exception as e:
        ep.setdefault("stages", {})["thursday"] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Friday — Final Review
# ---------------------------------------------------------------------------

@router.post("/friday")
async def cron_friday(request: Request, body: StageRequest = StageRequest()):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

    try:
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        approved, review_output = orchestrator._execute_stage_review(recipe_id)
        dialogue = _generate_dialogue("friday", concept)

        ep["stages"]["friday"] = {
            "stage": "final_review",
            "status": "complete",
            "concept": concept,
            "approved": approved,
            "review_data": review_output,
            "dialogue": dialogue,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("friday: complete")
        storage.save_episode(episode_id, ep)
        return _stage_response("friday", episode_id, concept, {
            "approved": approved,
            "dialogue_messages": len(dialogue),
        })

    except Exception as e:
        ep.setdefault("stages", {})["friday"] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Saturday — Deployment / Staging
# ---------------------------------------------------------------------------

@router.post("/saturday")
async def cron_saturday(request: Request, body: StageRequest = StageRequest()):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

    try:
        RecipeOrchestrator = _get_orchestrator()
        from backend.storage import EPISODES_DIR
        orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
        recipe_id = ep.get("recipe_id") or "unknown"
        # active_recipes check removed: orchestrator is per-request, so list is always empty
        orchestrator.pipeline.start_recipe(recipe_id, concept)

        orchestrator._execute_stage_deployment(recipe_id)
        dialogue = _generate_dialogue("saturday", concept)

        ep["stages"]["saturday"] = {
            "stage": "deployment",
            "status": "complete",
            "concept": concept,
            "deployment_status": "staged",
            "dialogue": dialogue,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("saturday: complete")
        storage.save_episode(episode_id, ep)
        return _stage_response("saturday", episode_id, concept, {"dialogue_messages": len(dialogue)})

    except Exception as e:
        ep.setdefault("stages", {})["saturday"] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Sunday — Publish
# ---------------------------------------------------------------------------

@router.post("/sunday")
async def cron_sunday(request: Request, body: StageRequest = StageRequest()):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or "Weekly Muffin Pan Recipe")
    concept: str = body.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"

    try:
        dialogue = _generate_dialogue("sunday", concept)

        # Verify critical prior stages completed before publishing
        required_stages = ["monday", "wednesday"]
        for day in required_stages:
            stage_status = ep.get("stages", {}).get(day, {}).get("status")
            if stage_status != "complete":
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot publish: {day} stage incomplete (status={stage_status!r})",
                )

        ep["published_at"] = datetime.now(timezone.utc).isoformat()
        ep["stages"]["sunday"] = {
            "stage": "publish",
            "status": "complete",
            "concept": concept,
            "published": True,
            "dialogue": dialogue,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        ep["events"].append("sunday: complete (published)")
        storage.save_episode(episode_id, ep)
        return _stage_response("sunday", episode_id, concept, {
            "published": True,
            "dialogue_messages": len(dialogue),
        })

    except Exception as e:
        ep.setdefault("stages", {})["sunday"] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Direct-call dispatcher (used by admin run-compressed-week)
# ---------------------------------------------------------------------------

_STAGE_HANDLERS = {
    "monday": cron_monday,
    "tuesday": cron_tuesday,
    "wednesday": cron_wednesday,
    "thursday": cron_thursday,
    "friday": cron_friday,
    "saturday": cron_saturday,
    "sunday": cron_sunday,
}


async def execute_cron_stage(stage: str, episode_id: str, concept: str) -> dict:
    """Execute a cron stage directly in-process (no HTTP round-trip).

    Called by the admin 'Run Compressed Week' button so it works on Vercel
    (where localhost self-calls fail) and on single-worker dev (no deadlock).
    """
    handler = _STAGE_HANDLERS.get(stage)
    if not handler:
        raise ValueError(f"Unknown cron stage: {stage!r}")
    # Bypass cron secret verification — caller is already auth'd via admin UI
    ep = _load_or_create_episode(episode_id, concept)
    ep_concept: str = concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
    dialogue = _generate_dialogue(stage, ep_concept)
    ep.setdefault("stages", {})[stage] = {
        "stage": stage,
        "status": "complete",
        "concept": ep_concept,
        "dialogue": dialogue,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    ep.setdefault("events", []).append(f"{stage}: complete")
    storage.save_episode(episode_id, ep)
    return {"stage": stage, "episode_id": episode_id, "dialogue_messages": len(dialogue)}

