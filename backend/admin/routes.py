"""
Admin dashboard routes and endpoints.

Provides:
- Dashboard home with statistics
- Recipe list and detail views
- Recipe approval/rejection
- Recipe publishing
- Agent status monitoring
- Recipe generation triggers
"""

from pathlib import Path
from typing import Optional, List
import hashlib
import hmac
import json
import re
import asyncio
import time
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel, field_validator

from backend.data.recipe import Recipe, RecipeStatus
from backend.publishing.pipeline import PublishingPipeline
from backend.newsletter.manager import NewsletterManager
from backend.auth.middleware import require_auth, create_session_cookie, clear_session_cookie
from backend.auth.session import _get_jwt_secret
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Module-level rate limit storage for newsletter subscription (3 req / IP / min)
_SUBSCRIBE_RATE_LIMITS: dict[str, list[float]] = {}
_SUBSCRIBE_RATE_LIMITS_LAST_SWEPT: float = 0.0
_SUBSCRIBE_RATE_LIMITS_SWEEP_INTERVAL: float = 300.0  # sweep every 5 minutes
_SUBSCRIBE_RATE_LIMITS_MAX_ENTRIES: int = 500         # hard cap on dict size

# ---------------------------------------------------------------------------
# Path-safety helpers (Governance H4)
# ---------------------------------------------------------------------------
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")

_OAUTH_STATE_MAX_AGE = 600  # 10 minutes


def _sanitize_id(value: str, label: str = "id") -> str:
    """Reject IDs that contain path separators or traversal sequences.

    Governance H4 requires all user-input paths to be sanitized.
    """
    if not value or not _SAFE_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {label}: must be alphanumeric, hyphens, or underscores only",
        )
    return value


def _sign_oauth_state(state: str) -> str:
    """Create an HMAC-signed, timestamped state cookie value."""
    ts = str(int(time.time()))
    payload = f"{state}|{ts}"
    sig = hmac.new(_get_jwt_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _verify_oauth_state(cookie_value: str, callback_state: str) -> bool:
    """Verify the signed state cookie matches the callback state parameter."""
    if not cookie_value:
        return False
    parts = cookie_value.split("|", 2)
    if len(parts) != 3:
        return False
    stored_state, ts_str, sig = parts

    # Verify HMAC signature
    expected_payload = f"{stored_state}|{ts_str}"
    expected_sig = hmac.new(
        _get_jwt_secret().encode(), expected_payload.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        logger.warning("OAuth state cookie signature mismatch")
        return False

    # Verify state matches
    if not hmac.compare_digest(stored_state, callback_state):
        logger.warning("OAuth state parameter mismatch")
        return False

    # Verify not expired
    try:
        ts = int(ts_str)
        if time.time() - ts > _OAUTH_STATE_MAX_AGE:
            logger.warning("OAuth state cookie expired")
            return False
    except ValueError:
        return False

    return True

STAGE_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
STAGE_LABELS = {
    "monday": "Brainstorm",
    "tuesday": "Recipe Dev",
    "wednesday": "Photography",
    "thursday": "Copywriting",
    "friday": "Final Review",
    "saturday": "Deployment",
    "sunday": "Publish",
}


def _safe_parse_iso(value: Optional[str]) -> datetime:
    """Best-effort parser for ISO-ish timestamps."""
    if not value:
        return datetime.min

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return datetime.min


def _load_simulation_runs(sim_dir: Path, limit: int = 100) -> List[dict]:
    """Load simulation JSON files and return newest-first summary rows."""
    runs: List[dict] = []

    if not sim_dir.exists():
        return runs

    for path in sim_dir.glob('*.json'):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception as exc:
            logger.warning(f"Skipping invalid simulation file {path.name}: {exc}")
            continue

        messages = payload.get('messages') or []
        if not isinstance(messages, list):
            messages = []

        # Skip benchmark comparison/summary sidecars — they have no messages
        if not messages and 'results' in payload:
            continue

        message_models = {m.get('model') for m in messages if isinstance(m, dict) and m.get('model')}

        character_models = payload.get('character_models') or {}
        character_model_values = set()
        if isinstance(character_models, dict):
            character_model_values = {v for v in character_models.values() if isinstance(v, str) and v}

        models = sorted({
            *message_models,
            *(payload.get('models') or []),
            payload.get('default_model'),
            *character_model_values,
        } - {None, ''})

        generated_at = payload.get('generated_at')
        runs.append({
            'filename': path.name,
            'filepath': str(path),
            'generated_at': generated_at,
            'generated_at_dt': _safe_parse_iso(generated_at),
            'concept': payload.get('concept') or 'Unknown concept',
            'prompt_style': payload.get('prompt_style') or 'unknown',
            'default_model': payload.get('default_model'),
            'models': models,
            'message_count': len(messages),
            'has_messages': len(messages) > 0,
            'raw': payload,
        })

    runs.sort(key=lambda r: r['generated_at_dt'], reverse=True)

    # Governance E4: zero-result sanity check
    if sim_dir.exists() and not runs:
        logger.warning(f"Simulation directory {sim_dir} exists but yielded 0 valid runs")

    return runs[:limit]


def _build_selected_simulation(run: dict) -> dict:
    """Expand a run row into template-friendly details."""
    payload = run['raw']
    messages = payload.get('messages') or []

    day_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        day = msg.get('day')
        stage = msg.get('stage')
        if day:
            day_counts[day] = day_counts.get(day, 0) + 1
        if stage:
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

    return {
        'filename': run['filename'],
        'concept': run['concept'],
        'generated_at': run['generated_at'],
        'prompt_style': run['prompt_style'],
        'default_model': payload.get('default_model'),
        'character_models': payload.get('character_models') or {},
        'models': run['models'],
        'messages': messages,
        'day_counts': day_counts,
        'stage_counts': stage_counts,
        'message_count': len(messages),
        'run': payload.get('run'),
        'mode': payload.get('mode'),
    }


# Request/Response models
class ApproveRequest(BaseModel):
    """Request to approve a recipe."""
    notes: Optional[str] = None


class RejectRequest(BaseModel):
    """Request to reject a recipe."""
    notes: str  # Rejection reason is required


class PublishRequest(BaseModel):
    """Request to publish a recipe."""
    send_notification: bool = True


def create_routes(app: FastAPI):
    """
    Add all admin routes to the FastAPI app.
    
    Args:
        app: FastAPI application instance
    """
    
    @app.get("/", response_class=HTMLResponse)
    async def root():
        """Redirect root to admin dashboard."""
        return RedirectResponse(url="/admin/")
    
    # ==================== AUTH ROUTES ====================
    
    @app.get("/auth/login")
    async def login(request: Request, redirect: Optional[str] = None):
        """Initiate Google OAuth login flow."""
        oauth = app.state.oauth_client

        # Generate authorization URL with CSRF state parameter
        auth_url, state = oauth.get_authorization_url()

        # Store signed state in a short-lived cookie for verification on callback.
        # This avoids needing server-side sessions (works on Vercel serverless).
        response = RedirectResponse(url=auth_url)
        from backend.config import config
        response.set_cookie(
            key="oauth_state",
            value=_sign_oauth_state(state),
            max_age=_OAUTH_STATE_MAX_AGE,
            httponly=True,
            secure=not config.is_local_dev,
            samesite="lax",
        )
        return response
    
    @app.get("/auth/callback")
    async def oauth_callback(
        request: Request,
        code: str,
        state: str
    ):
        """Handle OAuth callback from Google."""
        oauth = app.state.oauth_client
        session_manager = app.state.session_manager

        # Verify CSRF state parameter against signed cookie
        state_cookie = request.cookies.get("oauth_state", "")
        if not _verify_oauth_state(state_cookie, state):
            logger.warning("OAuth callback rejected: state verification failed")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid OAuth state — possible CSRF. Please try logging in again.",
            )

        # Exchange code for tokens and get user info
        user_info = await oauth.handle_callback(code, state)

        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed"
            )

        # Create JWT token (stateless — no server-side session)
        token = session_manager.create_token(
            email=user_info["email"],
            user_info=user_info
        )

        # Redirect to dashboard + set JWT cookie; clear the one-time state cookie
        redirect_response = RedirectResponse(url="/admin/")
        create_session_cookie(redirect_response, token)
        redirect_response.delete_cookie(key="oauth_state")
        return redirect_response
    
    @app.get("/auth/logout")
    async def logout(request: Request):
        """Logout and clear JWT session cookie."""
        response = RedirectResponse(url="/")
        clear_session_cookie(response)
        return response
    
    # ==================== ADMIN DASHBOARD ====================
    
    @app.get("/admin/", response_class=HTMLResponse)
    async def admin_dashboard(
        request: Request,
        user: dict = Depends(require_auth)
    ):
        """Admin dashboard home with statistics."""
        templates = app.state.templates
        data_dir = app.state.project_root / "data" / "recipes"

        # Get counts by status
        pending_recipes = Recipe.list_by_status(data_dir, RecipeStatus.PENDING)
        approved_recipes = Recipe.list_by_status(data_dir, RecipeStatus.APPROVED)
        published_recipes = Recipe.list_by_status(data_dir, RecipeStatus.PUBLISHED)
        rejected_recipes = Recipe.list_by_status(data_dir, RecipeStatus.REJECTED)

        stats = {
            "pending": len(pending_recipes),
            "approved": len(approved_recipes),
            "published": len(published_recipes),
            "rejected": len(rejected_recipes),
            "total": len(pending_recipes) + len(approved_recipes) + len(published_recipes) + len(rejected_recipes)
        }

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "stats": stats,
                "user_email": user.get("email", "Unknown"),
                "recent_pending": pending_recipes[:5],
            }
        )
    
    @app.get("/admin/recipes", response_class=HTMLResponse)
    async def list_recipes(
        request: Request,
        status_filter: Optional[str] = None,
        user: dict = Depends(require_auth)
    ):
        """
        Render recipes list page, optionally filtered by status.

        Query params:
            status_filter: pending, approved, published, or rejected
        """
        data_dir = app.state.project_root / "data" / "recipes"
        templates = app.state.templates

        if status_filter:
            try:
                status_enum = RecipeStatus(status_filter)
                recipes = Recipe.list_by_status(data_dir, status_enum)
            except ValueError:
                return templates.TemplateResponse(
                    "admin_error.html",
                    {
                        "request": request,
                        "title": "Invalid Filter",
                        "message": f"'{status_filter}' is not a valid status. Use: pending, approved, published, or rejected.",
                        "status_code": 400,
                    },
                    status_code=400,
                )
        else:
            # Get all recipes
            recipes = []
            for s in RecipeStatus:
                recipes.extend(Recipe.list_by_status(data_dir, s))

        # Sort by updated_at desc
        recipes.sort(key=lambda r: r.updated_at, reverse=True)

        recipe_rows = [
            {
                "recipe_id": r.recipe_id,
                "title": r.title,
                "status": r.status.value,
                "created_at": r.created_at.isoformat(),
                "updated_at": r.updated_at.isoformat(),
            }
            for r in recipes
        ]

        return templates.TemplateResponse(
            "recipes.html",
            {
                "request": request,
                "recipes": recipe_rows,
                "status_filter": status_filter,
            },
        )
    
    @app.get("/admin/recipes/{recipe_id}")
    async def get_recipe_detail(
        recipe_id: str,
        user: dict = Depends(require_auth)
    ):
        """Get full recipe details as JSON."""
        _sanitize_id(recipe_id, "recipe_id")
        data_dir = app.state.project_root / "data" / "recipes"

        # Find recipe in any status directory
        recipe = None
        for recipe_status in RecipeStatus:
            try:
                filepath = data_dir / recipe_status.value / f"{recipe_id}.json"
                if filepath.exists():
                    recipe = Recipe.load_from_file(filepath)
                    break
            except Exception as e:
                logger.error(f"Error loading recipe {recipe_id}: {e}")

        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")

        return recipe.model_dump(mode="json")

    @app.get("/admin/recipes/{recipe_id}/view", response_class=HTMLResponse)
    async def view_recipe_detail(
        request: Request,
        recipe_id: str,
        user: dict = Depends(require_auth)
    ):
        """Render recipe detail review page."""
        data_dir = app.state.project_root / "data" / "recipes"
        templates = app.state.templates

        try:
            _sanitize_id(recipe_id, "recipe_id")
        except HTTPException as e:
            # Invalid ID format — render error template instead of raising
            return templates.TemplateResponse(
                "recipe_detail.html",
                {
                    "request": request,
                    "recipe": None,
                    "error": e.detail,
                },
                status_code=e.status_code,
            )

        try:

            recipe = None
            for recipe_status in RecipeStatus:
                filepath = data_dir / recipe_status.value / f"{recipe_id}.json"
                if filepath.exists():
                    try:
                        recipe = Recipe.load_from_file(filepath)
                    except Exception as e:
                        logger.error(f"Error loading recipe {recipe_id}: {e}")
                        return templates.TemplateResponse(
                            "recipe_detail.html",
                            {
                                "request": request,
                                "recipe": None,
                                "error": f"Failed to load recipe data: {e}",
                            },
                            status_code=500,
                        )
                    break

            if not recipe:
                return templates.TemplateResponse(
                    "recipe_detail.html",
                    {
                        "request": request,
                        "recipe": None,
                        "error": f"Recipe '{recipe_id}' not found.",
                    },
                    status_code=404,
                )

            recipe_payload = recipe.model_dump(mode="json")
            image_url = None
            featured = recipe_payload.get("featured_photo")

            if isinstance(featured, str) and featured.strip():
                featured = featured.strip()
                if featured.startswith("http://") or featured.startswith("https://"):
                    image_url = featured
                else:
                    candidate = app.state.project_root / "src" / "assets" / "images" / featured
                    if candidate.exists():
                        image_url = f"/assets/images/{featured}"

            recipe_payload["image_url"] = image_url

            return templates.TemplateResponse(
                "recipe_detail.html",
                {
                    "request": request,
                    "recipe": recipe_payload,
                },
            )
        except Exception as e:
            # Catch any other unexpected exceptions and render error template
            logger.error(f"Error rendering recipe detail: {e}", exc_info=True)
            return templates.TemplateResponse(
                "recipe_detail.html",
                {
                    "request": request,
                    "recipe": None,
                    "error": "An unexpected error occurred while loading the recipe.",
                },
                status_code=500,
            )
    
    @app.post("/admin/recipes/{recipe_id}/approve")
    async def approve_recipe(
        recipe_id: str,
        request_data: ApproveRequest,
        user: dict = Depends(require_auth)
    ):
        """Move recipe from pending to approved."""
        _sanitize_id(recipe_id, "recipe_id")
        data_dir = app.state.project_root / "data" / "recipes"
        
        # Load recipe
        filepath = data_dir / "pending" / f"{recipe_id}.json"
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Recipe not found in pending")
        
        recipe = Recipe.load_from_file(filepath)
        
        # Transition to approved
        _new_path = recipe.transition_status(
            RecipeStatus.APPROVED,
            data_dir,
            notes=request_data.notes
        )
        
        logger.info(f"Recipe approved: {recipe.title}")
        
        return {
            "success": True,
            "message": f"Recipe approved: {recipe.title}",
            "new_status": "approved"
        }
    
    @app.post("/admin/recipes/{recipe_id}/reject")
    async def reject_recipe(
        recipe_id: str,
        request_data: RejectRequest,
        user: dict = Depends(require_auth)
    ):
        """Move recipe to rejected with notes."""
        _sanitize_id(recipe_id, "recipe_id")
        data_dir = app.state.project_root / "data" / "recipes"
        
        # Find recipe (could be pending or approved)
        recipe = None
        for recipe_status in [RecipeStatus.PENDING, RecipeStatus.APPROVED]:
            filepath = data_dir / recipe_status.value / f"{recipe_id}.json"
            if filepath.exists():
                recipe = Recipe.load_from_file(filepath)
                break
        
        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")
        
        # Transition to rejected
        _new_path = recipe.transition_status(
            RecipeStatus.REJECTED,
            data_dir,
            notes=request_data.notes
        )
        
        logger.info(f"Recipe rejected: {recipe.title} - {request_data.notes}")
        
        return {
            "success": True,
            "message": f"Recipe rejected: {recipe.title}",
            "new_status": "rejected"
        }
    
    @app.post("/admin/recipes/{recipe_id}/publish")
    async def publish_recipe(
        recipe_id: str,
        request_data: PublishRequest,
        user: dict = Depends(require_auth)
    ):
        """Publish approved recipe to live site."""
        _sanitize_id(recipe_id, "recipe_id")
        # Initialize publishing pipeline
        pipeline = PublishingPipeline(
            project_root=app.state.project_root,
            auto_commit=True,
            auto_push=True
        )
        
        # Publish
        success = pipeline.publish_recipe(
            recipe_id,
            send_notification=request_data.send_notification
        )
        
        if not success:
            raise HTTPException(
                status_code=500,
                detail="Publishing failed. Check logs for details."
            )
        
        return {
            "success": True,
            "message": "Recipe published successfully",
            "new_status": "published"
        }
    
    @app.get("/admin/simulations", response_class=HTMLResponse)
    async def admin_simulations(
        request: Request,
        selected_file: Optional[str] = None,
        concept_filter: Optional[str] = None,
        model_filter: Optional[str] = None,
        user: dict = Depends(require_auth),
    ):
        """Browse local simulation transcripts in a chat-style UI."""
        templates = app.state.templates
        simulations_dir = app.state.project_root / 'data' / 'simulations'

        all_runs = _load_simulation_runs(simulations_dir)

        filtered_runs = all_runs
        if concept_filter:
            needle = concept_filter.strip().lower()
            filtered_runs = [r for r in filtered_runs if needle in (r['concept'] or '').lower()]

        if model_filter:
            needle = model_filter.strip().lower()
            filtered_runs = [
                r
                for r in filtered_runs
                if any(needle in model.lower() for model in r['models'])
            ]

        selected_run = None
        if filtered_runs:
            if selected_file:
                selected_run = next((r for r in filtered_runs if r['filename'] == selected_file), None)
            if selected_run is None:
                selected_run = filtered_runs[0]

        selected_payload = _build_selected_simulation(selected_run) if selected_run else None

        return templates.TemplateResponse(
            'simulations.html',
            {
                'request': request,
                'runs': filtered_runs,
                'selected_run': selected_payload,
                'selected_file': selected_file or (selected_run['filename'] if selected_run else None),
                'concept_filter': concept_filter or '',
                'model_filter': model_filter or '',
                'total_runs': len(all_runs),
                'filtered_count': len(filtered_runs),
            },
        )

    @app.get("/admin/agents")
    async def get_agent_status(user: dict = Depends(require_auth)):
        """Get status of AI agents."""
        # Placeholder - in future, this could check agent mood/status from files
        return {
            "agents": [
                {
                    "name": "Margaret Chen (Baker)",
                    "role": "baker",
                    "status": "ready",
                    "mood": "creative"
                },
                {
                    "name": "Marcus Webb (Copywriter)",
                    "role": "copywriter",
                    "status": "ready",
                    "mood": "witty"
                },
                {
                    "name": "Julian Park (Art Director)",
                    "role": "art_director",
                    "status": "ready",
                    "mood": "perfectionist"
                }
            ]
        }
    
    @app.post("/admin/generate")
    async def trigger_generation(user: dict = Depends(require_auth)):
        """Trigger new recipe generation."""
        # Placeholder - would call orchestrator
        return {
            "success": True,
            "message": "Recipe generation triggered",
            "note": "This is a placeholder. Connect to orchestrator in future."
        }
    
    # ==================== NEWSLETTER ROUTES ====================
    
    class NewsletterSubscribeRequest(BaseModel):
        """Newsletter subscription request."""
        email: str

        @field_validator("email")
        @classmethod
        def validate_email(cls, v: str) -> str:
            v = v.strip().lower()
            if len(v) > 254:
                raise ValueError("Email address too long.")
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
                raise ValueError("Invalid email address.")
            return v
    
    @app.post("/api/newsletter/subscribe")
    async def newsletter_subscribe(request: Request, request_data: NewsletterSubscribeRequest):
        """Public endpoint for newsletter subscription with rate limiting (3 req/IP/min)."""
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        # Periodic sweep: evict all IPs whose timestamps are fully expired
        global _SUBSCRIBE_RATE_LIMITS_LAST_SWEPT
        if now - _SUBSCRIBE_RATE_LIMITS_LAST_SWEPT > _SUBSCRIBE_RATE_LIMITS_SWEEP_INTERVAL:
            stale_keys = [
                ip for ip, ts_list in _SUBSCRIBE_RATE_LIMITS.items()
                if not any(now - t < 60 for t in ts_list)
            ]
            for k in stale_keys:
                del _SUBSCRIBE_RATE_LIMITS[k]
            # Hard cap: if still over limit, evict oldest entries
            if len(_SUBSCRIBE_RATE_LIMITS) > _SUBSCRIBE_RATE_LIMITS_MAX_ENTRIES:
                excess = len(_SUBSCRIBE_RATE_LIMITS) - _SUBSCRIBE_RATE_LIMITS_MAX_ENTRIES
                for k in list(_SUBSCRIBE_RATE_LIMITS.keys())[:excess]:
                    del _SUBSCRIBE_RATE_LIMITS[k]
            _SUBSCRIBE_RATE_LIMITS_LAST_SWEPT = now

        # Sliding-window: keep only timestamps within the last 60 seconds
        timestamps = _SUBSCRIBE_RATE_LIMITS.get(client_ip, [])
        timestamps = [ts for ts in timestamps if now - ts < 60]
        if not timestamps:
            _SUBSCRIBE_RATE_LIMITS.pop(client_ip, None)

        if len(timestamps) >= 3:
            logger.warning(f"Newsletter rate limit exceeded for IP: {client_ip}")
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
            )

        timestamps.append(now)
        _SUBSCRIBE_RATE_LIMITS[client_ip] = timestamps

        manager = NewsletterManager()
        result = await manager.subscribe(request_data.email)

        if result["success"]:
            return {"success": True, "message": "Successfully subscribed to newsletter!"}
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Subscription failed"))
    
    @app.get("/admin/newsletter/subscribers")
    async def newsletter_list_subscribers(user: dict = Depends(require_auth)):
        """Admin endpoint to list all newsletter subscribers."""
        manager = NewsletterManager()
        subscribers = await manager.list_subscribers()
        
        return {
            "subscribers": subscribers,
            "total": len(subscribers)
        }
    
    # ==================== EPISODE VIEWER ====================

    def _load_episodes(episodes_dir: Path) -> list[dict]:
        """Load and summarize all episode JSON files, newest first."""
        episodes = []
        if not episodes_dir.exists():
            return episodes
        for path in sorted(episodes_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text())
                stages = data.get("stages", {})
                completed = sum(1 for s in stages.values() if s.get("status") == "complete")
                failed = sum(1 for s in stages.values() if s.get("status") == "failed")
                total = 7  # mon–sun

                # Determine overall episode status
                if failed:
                    ep_status = "partial"
                elif completed == total:
                    ep_status = "complete"
                elif completed > 0:
                    # Check if stale — last activity > 2 hours ago
                    last_activity = max(
                        (s.get("completed_at") or s.get("started_at") or "")
                        for s in stages.values()
                    )
                    if last_activity:
                        try:
                            last_dt = datetime.fromisoformat(last_activity)
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=timezone.utc)
                            stale = datetime.now(timezone.utc) - last_dt > timedelta(hours=2)
                        except (ValueError, TypeError):
                            stale = False
                    else:
                        stale = False
                    ep_status = "stopped" if stale else "in_progress"
                else:
                    ep_status = "empty"

                # Build per-stage status map for progress bar
                stages_map = {}
                for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"):
                    if day in stages:
                        stages_map[day] = stages[day].get("status", "unknown")
                    else:
                        stages_map[day] = "not_started"

                episodes.append({
                    "episode_id": data.get("episode_id", path.stem),
                    "concept": data.get("concept", "Unknown"),
                    "created_at": data.get("created_at", ""),
                    "published_at": data.get("published_at"),
                    "dry_run": data.get("dry_run", False),
                    "recipe_id": data.get("recipe_id"),
                    "completed_stages": completed,
                    "failed_stages": failed,
                    "total_stages": total,
                    "status": ep_status,
                    "stages_map": stages_map,
                    "filename": path.name,
                })
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning(f"Skipping invalid episode file {path.name}: {exc}")
            except Exception as exc:
                logger.error(f"Unexpected error reading episode file {path.name}: {exc}", exc_info=True)
        return episodes

    def _build_episode_detail(data: dict) -> dict:
        """Build template-friendly detail from raw episode JSON."""
        stages_raw = data.get("stages", {})
        stages = []
        for key in STAGE_ORDER:
            entry = stages_raw.get(key)
            if entry is None:
                stages.append({
                    "key": key,
                    "label": STAGE_LABELS.get(key, key),
                    "present": False,
                    "status": "not_started",
                    "data": {},
                })
                continue

            dialogue = entry.get("dialogue", [])
            # Normalise dialogue: could be list[str] or list[dict]
            dialogue_lines = []
            for msg in dialogue:
                if isinstance(msg, dict):
                    character = msg.get("character") or msg.get("speaker") or "Agent"
                    text = msg.get("message") or msg.get("text") or str(msg)
                    day = msg.get("day", "")
                    model = msg.get("model", "")
                    dialogue_lines.append({"character": character, "text": text, "day": day, "model": model})
                elif isinstance(msg, str):
                    dialogue_lines.append({"character": "Agent", "text": msg, "day": "", "model": ""})

            recipe_data = entry.get("recipe_data", {})
            image_paths_raw = entry.get("image_paths") or []
            stages.append({
                "key": key,
                "label": STAGE_LABELS.get(key, key),
                "present": True,
                "status": entry.get("status", "unknown"),
                "started_at": entry.get("started_at", ""),
                "completed_at": entry.get("completed_at", ""),
                "error": entry.get("error"),
                "recipe_title": recipe_data.get("title") if recipe_data else None,
                "ingredient_count": len(recipe_data.get("ingredients", [])) if recipe_data else 0,
                "dialogue": dialogue_lines,
                "dialogue_count": len(dialogue_lines),
                "image_paths": image_paths_raw,
                "approved": entry.get("approved"),
                "deployment_status": entry.get("deployment_status"),
                "published": entry.get("published"),
                "data": entry,
            })
        return {
            "episode_id": data.get("episode_id", ""),
            "concept": data.get("concept", ""),
            "created_at": data.get("created_at", ""),
            "published_at": data.get("published_at"),
            "dry_run": data.get("dry_run", False),
            "recipe_id": data.get("recipe_id"),
            "events": data.get("events", []),
            "stages": stages,
        }

    @app.get("/admin/episodes", response_class=HTMLResponse)
    async def admin_episodes(
        request: Request,
        user: dict = Depends(require_auth),
        page: int = 1,
        page_size: int = 20,
    ):
        """Browse full-week episode production logs."""
        templates = app.state.templates
        episodes_dir = app.state.project_root / "data" / "episodes"
        all_episodes = _load_episodes(episodes_dir)

        # Sort newest-first by created_at
        all_episodes.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        total = len(all_episodes)

        # Clamp page bounds
        max_page = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, max_page))
        start = (page - 1) * page_size
        episodes = all_episodes[start : start + page_size]

        return templates.TemplateResponse(
            "episodes.html",
            {
                "request": request,
                "episodes": episodes,
                "total": total,
                "page": page,
                "page_size": page_size,
                "max_page": max_page,
                "stage_keys": STAGE_ORDER,
                "stage_labels": {k: STAGE_LABELS.get(k, k[:3].title()) for k in STAGE_ORDER},
            },
        )

    @app.get("/admin/episodes/{episode_id}", response_class=HTMLResponse)
    async def admin_episode_detail(
        request: Request,
        episode_id: str,
        user: dict = Depends(require_auth),
    ):
        """Full stage-by-stage viewer for a single episode."""
        _sanitize_id(episode_id, "episode_id")
        templates = app.state.templates
        episodes_dir = app.state.project_root / "data" / "episodes"
        ep_path = episodes_dir / f"{episode_id}.json"

        if not ep_path.exists():
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")

        data = json.loads(ep_path.read_text())
        episode = _build_episode_detail(data)

        return templates.TemplateResponse(
            "episode_detail.html",
            {
                "request": request,
                "episode": episode,
            },
        )





    # ==================== IMAGE REVIEW ENDPOINTS ====================

    class ImageOverrideRequest(BaseModel):
        """Request to override image selection with a different variant."""
        variant_path: str  # relative path to the selected variant image

    @app.post("/admin/episodes/{episode_id}/images/confirm")
    async def admin_confirm_image(
        episode_id: str,
        user: dict = Depends(require_auth),
    ):
        """Lock in the auto-selected winner. Sets image_status → confirmed."""
        _sanitize_id(episode_id, "episode_id")
        episodes_dir = app.state.project_root / "data" / "episodes"
        ep_path = episodes_dir / f"{episode_id}.json"

        if not ep_path.exists():
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")

        data = json.loads(ep_path.read_text())
        wed = data.get("stages", {}).get("wednesday")
        if not wed:
            raise HTTPException(status_code=400, detail="Wednesday stage not complete")

        current_status = wed.get("image_status", "")
        if current_status in ("cleaned",):
            raise HTTPException(status_code=400, detail=f"Cannot confirm: images already {current_status}")

        wed["image_status"] = "confirmed"
        ep_path.write_text(json.dumps(data, indent=2))
        logger.info(f"Image confirmed for episode {episode_id}")

        return {"success": True, "image_status": "confirmed"}

    @app.post("/admin/episodes/{episode_id}/images/override")
    async def admin_override_image(
        episode_id: str,
        request_data: ImageOverrideRequest,
        user: dict = Depends(require_auth),
    ):
        """Pick a different variant as winner. Copies it to {recipe_id}.png."""
        import shutil

        _sanitize_id(episode_id, "episode_id")
        episodes_dir = app.state.project_root / "data" / "episodes"
        ep_path = episodes_dir / f"{episode_id}.json"

        if not ep_path.exists():
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")

        data = json.loads(ep_path.read_text())
        wed = data.get("stages", {}).get("wednesday")
        if not wed:
            raise HTTPException(status_code=400, detail="Wednesday stage not complete")

        current_status = wed.get("image_status", "")
        if current_status in ("cleaned",):
            raise HTTPException(status_code=400, detail=f"Cannot override: images already {current_status}")

        recipe_id = data.get("recipe_id")
        if not recipe_id:
            raise HTTPException(status_code=400, detail="No recipe_id on episode")

        # Validate the variant path exists
        variant_source = app.state.project_root / request_data.variant_path
        if not variant_source.exists():
            # Also try with src/ prefix
            variant_source = app.state.project_root / "src" / request_data.variant_path
        if not variant_source.exists():
            raise HTTPException(status_code=404, detail=f"Variant image not found: {request_data.variant_path}")

        # Validate path stays under project root (prevent traversal)
        try:
            variant_source.resolve().relative_to(app.state.project_root.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid variant path")

        # Copy to featured image location
        featured_dest = app.state.project_root / "src" / "assets" / "images" / f"{recipe_id}.png"
        featured_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(variant_source, featured_dest)

        # Update episode data
        wed["image_status"] = "overridden"
        wed["confirmed_winner"] = {
            "variant": variant_source.stem,
            "path": str(variant_source.relative_to(app.state.project_root)),
            "featured_image": str(featured_dest.relative_to(app.state.project_root)),
        }
        ep_path.write_text(json.dumps(data, indent=2))
        logger.info(f"Image overridden for episode {episode_id}: {request_data.variant_path}")

        return {"success": True, "image_status": "overridden", "new_winner": request_data.variant_path}

    @app.post("/admin/episodes/{episode_id}/images/rerun")
    async def admin_rerun_photography(
        episode_id: str,
        user: dict = Depends(require_auth),
    ):
        """Re-run the art director photography stage for this episode."""
        _sanitize_id(episode_id, "episode_id")
        episodes_dir = app.state.project_root / "data" / "episodes"
        ep_path = episodes_dir / f"{episode_id}.json"

        if not ep_path.exists():
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")

        data = json.loads(ep_path.read_text())
        concept = data.get("concept", "Weekly Muffin Pan Recipe")
        recipe_data = data.get("stages", {}).get("monday", {}).get("recipe_data", {})
        recipe_id = data.get("recipe_id")

        if not recipe_id:
            raise HTTPException(status_code=400, detail="No recipe_id on episode")

        current_status = data.get("stages", {}).get("wednesday", {}).get("image_status", "")
        if current_status in ("cleaned",):
            raise HTTPException(status_code=400, detail=f"Cannot re-run: images already {current_status}")

        try:
            from backend.orchestrator import RecipeOrchestrator
            from backend.storage import EPISODES_DIR
            orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)
            orchestrator.pipeline.start_recipe(recipe_id, concept)

            photography_result = orchestrator._execute_stage_photography(recipe_id, recipe_data)
            image_paths = photography_result.get("selected_shots", []) if isinstance(photography_result, dict) else []

            from backend.storage import storage
            image_urls = [storage.get_image_url(p) for p in image_paths]

            data["stages"]["wednesday"] = {
                "stage": "photography",
                "status": "complete",
                "concept": concept,
                "photography_data": photography_result,
                "reshoot_happened": photography_result.get("reshoot_happened", False) if isinstance(photography_result, dict) else False,
                "image_paths": image_paths,
                "image_urls": image_urls,
                "image_status": "auto_selected",
                "confirmed_winner": photography_result.get("winner", {}) if isinstance(photography_result, dict) else {},
                "dialogue": data.get("stages", {}).get("wednesday", {}).get("dialogue", []),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "rerun": True,
            }
            data["image_paths"] = image_paths
            data["image_urls"] = image_urls
            data.setdefault("events", []).append("wednesday: re-run photography")
            ep_path.write_text(json.dumps(data, indent=2))

            logger.info(f"Photography re-run complete for episode {episode_id}: {len(image_paths)} images")
            return {
                "success": True,
                "images_generated": len(image_paths),
                "image_status": "auto_selected",
            }

        except Exception as e:
            logger.error(f"Photography re-run failed for {episode_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Re-run failed: {e}")

    @app.delete("/admin/episodes/{episode_id}")
    async def admin_episode_delete(
        episode_id: str,
        user: dict = Depends(require_auth),
    ):
        """Delete a non-published episode and its associated images (moved to trash)."""
        _sanitize_id(episode_id, "episode_id")
        episodes_dir = app.state.project_root / "data" / "episodes"
        ep_path = episodes_dir / f"{episode_id}.json"

        if not ep_path.exists():
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")

        data = json.loads(ep_path.read_text())
        if data.get("published_at"):
            raise HTTPException(status_code=403, detail="Cannot delete a published episode.")

        from send2trash import send2trash
        trashed: list[str] = []
        errors: list[str] = []

        recipe_id = data.get("recipe_id")
        if recipe_id:
            recipe_id = _sanitize_id(recipe_id, "recipe_id")
        images_base = app.state.project_root / "src" / "assets" / "images"
        paths_to_trash: list[Path] = [ep_path]
        if recipe_id:
            image_dir = images_base / recipe_id
            featured = images_base / f"{recipe_id}.png"
            if image_dir.exists():
                paths_to_trash.append(image_dir)
            if featured.exists():
                paths_to_trash.append(featured)

        for p in paths_to_trash:
            try:
                send2trash(str(p))
                trashed.append(str(p))
            except Exception as exc:
                errors.append(f"{p}: {exc}")

        if errors:
            logger.warning(f"Episode delete partial errors for {episode_id}: {errors}")

        return JSONResponse({
            "message": f"Episode {episode_id} deleted ({len(trashed)} items trashed).",
            "trashed": trashed,
            "errors": errors,
        })

    @app.post("/admin/episodes/{episode_id}/run")
    async def admin_episode_run(
        episode_id: str,
        user: dict = Depends(require_auth),
    ):
        """Trigger a compressed full-week run by calling /api/cron/{stage} routes sequentially."""
        _sanitize_id(episode_id, "episode_id")

        # Read episode file to get the concept
        project_root = app.state.project_root
        ep_path = project_root / "data" / "episodes" / f"{episode_id}.json"
        concept = "Weekly Muffin Pan Recipe"
        if ep_path.exists():
            try:
                ep_data = json.loads(ep_path.read_text())
                concept = ep_data.get("concept", concept)
            except Exception:
                pass

        # Call each cron stage in order via HTTP (same as Vercel would).
        # In LOCAL_DEV the CRON_SECRET check is bypassed, any bearer value works.
        from backend.admin.cron_routes import execute_cron_stage_stub

        stages = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        stage_results = []

        for stage in stages:
            try:
                result = await execute_cron_stage_stub(stage, episode_id, concept)
                stage_results.append({
                    "stage": stage,
                    "ok": True,
                    "detail": result,
                })
                logger.info(f"Cron stage {stage} -> complete")
            except Exception as exc:
                logger.warning(f"Cron stage {stage} failed: {exc}")
                stage_results.append({"stage": stage, "ok": False, "error": str(exc)})

            # Brief pause between stages for sequential pacing
            if stage != stages[-1]:
                await asyncio.sleep(2)

        completed = sum(1 for r in stage_results if r.get("ok"))
        return JSONResponse({
            "message": f"Compressed week run complete for episode {episode_id}: {completed}/{len(stages)} stages succeeded.",
            "stages": stage_results,
        })

    @app.get("/admin/{path:path}", response_class=HTMLResponse)
    async def admin_404(request: Request, path: str, user: dict = Depends(require_auth)):
        """Catch-all for unknown /admin/* paths — renders styled 404 page."""
        templates = app.state.templates
        return templates.TemplateResponse(
            "admin_error.html",
            {
                "request": request,
                "title": "Page Not Found",
                "message": f"/admin/{path} doesn't exist.",
                "status_code": 404,
            },
            status_code=404,
        )

    logger.info("Admin routes configured")
