"""
Admin dashboard routes and endpoints.

Provides:
- Dashboard home with statistics
- Recipe list and detail views
- Recipe approval/rejection
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
    except ValueError:  # governance: allow-silent SF002: a non-integer timestamp is a forged/corrupt state cookie; False makes the OAuth callback reject the login (fail closed)
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
    
    # NOTE: the manual /admin/recipes/{id}/publish endpoint was retired with
    # the legacy static-build PublishingPipeline (#6304). Publishing now runs
    # automatically through the Sunday cron + unified episode_renderer.

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
                    "name": "Julian Torres (Art Director)",
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

    def _cloud_storage():
        from backend.storage import storage
        return storage

    def _load_cloud_episode(episode_id: str) -> dict:
        """Read the production episode; a storage failure is an error, never stale disk data.

        Skips this process's episode cache, so a warm Lambda cannot show the
        review page an image set it cached before a Wednesday rerun.
        """
        store = _cloud_storage()
        strict = getattr(store, "load_episode_strict", None)
        try:
            with store.prefix_scope(""):
                data = strict(episode_id, use_cache=False) if strict else store.load_episode(episode_id)
        except Exception as exc:
            logger.error(f"Episode read failed for {episode_id}: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not read the episode from storage")
        if not data:
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")
        return data

    def _load_photo_decision(data: dict) -> Optional[dict]:
        """The current decision, read fresh; a storage failure is a 503."""
        from backend.utils import photo_review

        store = _cloud_storage()
        try:
            with store.prefix_scope(""):
                return photo_review.load_decision(store, data)
        except Exception as exc:
            logger.error(f"Photo decision read failed for {data.get('episode_id')}: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not read the photo review from storage")

    def _origin_of(url: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}".lower()

    def _require_same_origin(request: Request) -> None:
        """Refuse cross-site writes: Origin (or Referer) must be this site.

        Scheme and host both count. Allowed: the configured admin base URL,
        the origin the app itself was addressed at, and, on Vercel (which
        terminates TLS and routes by Host), https on the request's Host.
        X-Forwarded-Host is never trusted.
        """
        import os

        from backend.utils.discord import ADMIN_BASE_URL

        source = _origin_of(request.headers.get("origin") or request.headers.get("referer") or "")
        host = (request.headers.get("host") or "").lower()
        allowed = {_origin_of(ADMIN_BASE_URL), _origin_of(str(request.base_url))}
        if host and os.environ.get("VERCEL"):
            allowed.add(f"https://{host}")
        if not source or source not in allowed:
            raise HTTPException(status_code=403, detail="Cross-origin request refused")

    def _load_episodes(raw_episodes: list[dict]) -> list[dict]:
        """Summarize episode dicts for the list page, newest first."""
        episodes = []
        for data in raw_episodes:
            try:
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
                    "episode_id": data.get("episode_id", ""),
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
                })
            except (AttributeError, KeyError, ValueError, TypeError) as exc:
                logger.warning(f"Skipping invalid episode {data.get('episode_id') if isinstance(data, dict) else '?'}: {exc}")
        return episodes

    def _build_episode_detail(data: dict, decision: Optional[dict] = None) -> dict:
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
            "photo_review": _photo_review_view(data, decision),
            "image_display_urls": _image_display_urls(data),
        }

    def _image_display_urls(data: dict) -> dict:
        from backend.publishing.episode_renderer import _to_local_image_url

        wed = data.get("stages", {}).get("wednesday", {}) or {}
        return {
            p: _to_local_image_url(u)
            for p, u in zip(wed.get("image_paths") or [], wed.get("image_urls") or [])
            if p and u
        }

    def _automated_notes(data: dict) -> tuple[Optional[str], dict]:
        """The automated pick and each candidate's recorded defects, by path."""
        wed = data.get("stages", {}).get("wednesday", {}) or {}
        winner = wed.get("confirmed_winner") or {}
        photo = wed.get("photography_data") if isinstance(wed.get("photography_data"), dict) else {}
        defects: dict = {}
        for rnd in photo.get("rounds", []) or []:
            for v in (rnd.get("variants") or []) if isinstance(rnd, dict) else []:
                scores = v.get("scores") if isinstance(v, dict) else None
                if isinstance(scores, dict) and isinstance(scores.get("defects"), list):
                    defects[v.get("path")] = [str(d)[:160] for d in scores["defects"] if d][:5]
        return (winner.get("path") if isinstance(winner, dict) else None), defects

    def _photo_review_view(data: dict, decision: Optional[dict]) -> dict:
        from backend.publishing.episode_renderer import _to_local_image_url
        from backend.utils import photo_review

        state = photo_review.review_state(data, decision)
        review = state["review"]
        if review is None:
            return {"status": None, "candidates": [], "writable": False,
                    "note": "No current photo set to review."}
        selected = state["selected"]
        published = bool(data.get("published_at"))
        recommended_path, defects = _automated_notes(data)
        wed = data.get("stages", {}).get("wednesday", {}) or {}
        automated = (wed.get("photography_data") or {}).get("automated_review_status") \
            if isinstance(wed.get("photography_data"), dict) else None
        note = ""
        if published:
            note = "Published. The hero is pinned; changes are editorial overrides."
        elif selected and data.get("publish_hold"):
            note = "Approved. The scheduled publishing time has passed; publication is still pending."
        elif data.get("publish_hold"):
            note = "Sunday did not publish: it is waiting for your photo choice."
        elif state["status"] == photo_review.INVALID:
            note = "The saved choice does not match these photos. Choose again."
        return {
            "status": state["status"],
            "image_set_id": review.get("image_set_id"),
            "selected_path": selected["path"] if selected else None,
            "decided_at": state.get("decided_at"),
            "automated_status": automated,
            "writable": not published,
            "note": note,
            "candidates": [
                {**c, "display_url": _to_local_image_url(c["url"]),
                 "recommended": c["path"] == recommended_path,
                 "defects": defects.get(c["path"], [])}
                for c in review.get("candidates", [])
            ],
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
        store = _cloud_storage()
        lister = getattr(store, "list_episodes_strict", None) or store.list_episodes
        try:
            with store.prefix_scope(""):
                raw = lister()
        except Exception as exc:
            logger.error(f"Episode list failed: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not list episodes from storage")
        all_episodes = _load_episodes(raw)

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
        data = _load_cloud_episode(episode_id)
        episode = _build_episode_detail(data, _load_photo_decision(data))

        return templates.TemplateResponse(
            "episode_detail.html",
            {
                "request": request,
                "episode": episode,
            },
        )





    # ==================== IMAGE REVIEW ENDPOINTS ====================

    class PhotoReviewRequest(BaseModel):
        action: str  # "select" | "reject"
        image_set_id: str
        path: Optional[str] = None

    def _decision_actor(user: dict) -> dict:
        """Provenance safe for a public decision record (#7936).

        A role label plus a keyed hash of the account, so two decisions can
        be told apart by account without storing the email or OAuth subject.
        """
        import hashlib
        import hmac

        from backend.auth.session import _get_jwt_secret

        account = str(user.get("sub") or user.get("email") or "")
        digest = (
            hmac.new(_get_jwt_secret().encode(), account.encode(), hashlib.sha256).hexdigest()[:16]
            if account else None
        )
        return {"decided_by": "site editor", "decided_by_id": f"editor-{digest}" if digest else None}

    def _record_photo_decision(
        episode_id: str, user: dict, *, action: str, image_set: str, path: Optional[str],
    ) -> dict:
        """Validate against the cloud episode's candidates, then append the decision (#7936).

        Writes only the separate decision record, never the episode, so no
        cron save can erase it. Never generates or copies images.
        """
        from backend.utils import photo_review

        _sanitize_id(episode_id, "episode_id")
        data = _load_cloud_episode(episode_id)
        data.setdefault("episode_id", episode_id)
        try:
            record = photo_review.make_decision(
                data,
                action=action,
                image_set=image_set,
                path=path,
                **_decision_actor(user),
            )
        except photo_review.PhotoReviewError as exc:
            raise HTTPException(status_code=409 if data.get("published_at") else 400, detail=str(exc))
        store = _cloud_storage()
        try:
            with store.prefix_scope(""):
                photo_review.save_decision(store, data, record)
        except Exception as exc:
            logger.error(f"Photo decision save failed for {episode_id}: {type(exc).__name__}: {exc}")
            # The write may have landed before the error: do not promise
            # that nothing changed.
            raise HTTPException(
                status_code=503,
                detail="Could not confirm the save. Reload to check your selection.",
            )
        status_value = record["status"]
        if status_value == "approved" and data.get("publish_hold"):
            message = "Approved. The scheduled publishing time has passed; publication is still pending."
        elif status_value == "approved":
            message = "Approved. Sunday will publish this photo."
        else:
            message = "Rejected. Sunday will not publish. No new photos are made."
        logger.info(f"Photo review for {episode_id}: {status_value}")
        return {"success": True, "status": status_value, "message": message}

    @app.post("/admin/episodes/{episode_id}/photos/review")
    async def admin_photo_review(
        episode_id: str,
        request_data: PhotoReviewRequest,
        request: Request,
        user: dict = Depends(require_auth),
    ):
        """Select one candidate photo, or reject all. Sunday publishes only an approval."""
        _require_same_origin(request)
        return _record_photo_decision(
            episode_id, user,
            action=request_data.action,
            image_set=request_data.image_set_id,
            path=request_data.path,
        )

    def _retired_image_control(episode_id: str) -> None:
        raise HTTPException(
            status_code=410,
            detail=f"Retired. Choose the photo at /admin/episodes/{episode_id}#photo-review.",
        )

    @app.post("/admin/episodes/{episode_id}/images/confirm")
    async def admin_confirm_image(episode_id: str, user: dict = Depends(require_auth)):
        """Retired (#7936): approval goes through the photo review."""
        _sanitize_id(episode_id, "episode_id")
        _retired_image_control(episode_id)

    @app.post("/admin/episodes/{episode_id}/images/override")
    async def admin_override_image(episode_id: str, user: dict = Depends(require_auth)):
        """Retired (#7936): approval goes through the photo review."""
        _sanitize_id(episode_id, "episode_id")
        _retired_image_control(episode_id)

    @app.post("/admin/episodes/{episode_id}/images/rerun")
    async def admin_rerun_photography(
        episode_id: str,
        user: dict = Depends(require_auth),
    ):
        """Re-run the art director photography stage for this episode.

        PAID (image generation). Local-data only and not linked from the photo
        review page (#7936); the rebuilt stage has no photo review, so Sunday
        holds until the new set is reviewed.
        """
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
            except (OSError, json.JSONDecodeError) as exc:
                # An unreadable episode file used to fall through to the
                # placeholder concept and run the whole week on it.
                raise HTTPException(
                    status_code=500,
                    detail=f"Episode file for {episode_id} is unreadable: {type(exc).__name__}: {exc}",
                ) from exc

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
