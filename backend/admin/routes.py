"""
Admin dashboard routes and endpoints.

Provides:
- Dashboard home with statistics
- Recipe list and detail views
- Recipe approval/rejection
- Agent status monitoring
- Recipe generation triggers
"""

from typing import Optional
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

class _SlidingWindowRateLimiter:
    """Per-client in-memory sliding-window limit: ``limit`` requests per minute.

    State lives in the process, so on serverless each warm instance counts on its
    own; that bounds a single client's burst without needing shared storage.
    Expired clients are swept every few minutes and the table is capped so a
    flood of distinct IPs cannot grow it unbounded.
    """

    window = 60.0
    sweep_interval = 300.0
    max_entries = 500

    def __init__(self, name: str, limit: int):
        self.name = name
        self.limit = limit
        self.hits: dict[str, list[float]] = {}
        self.last_swept = 0.0

    def check(self, request: Request) -> None:
        """Record a request from this client, raising HTTP 429 once over the limit."""
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        if now - self.last_swept > self.sweep_interval:
            stale = [ip for ip, ts in self.hits.items() if not any(now - t < self.window for t in ts)]
            for ip in stale:
                del self.hits[ip]
            excess = len(self.hits) - self.max_entries
            for ip in list(self.hits)[:max(excess, 0)]:
                del self.hits[ip]
            self.last_swept = now

        timestamps = [t for t in self.hits.get(client_ip, []) if now - t < self.window]
        if len(timestamps) >= self.limit:
            logger.warning(f"{self.name} rate limit exceeded for IP: {client_ip}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )
        timestamps.append(now)
        self.hits[client_ip] = timestamps


_SUBSCRIBE_LIMITER = _SlidingWindowRateLimiter("Newsletter", limit=3)
# Each endpoint gets its own budget; one sign-in uses one request of each.
_LOGIN_LIMITER = _SlidingWindowRateLimiter("Auth login", limit=10)
_CALLBACK_LIMITER = _SlidingWindowRateLimiter("Auth callback", limit=10)

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
        _LOGIN_LIMITER.check(request)
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
        _CALLBACK_LIMITER.check(request)
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
    
    _DASHBOARD_RECENT_EPISODES = 4

    def _dashboard_episode(data: dict) -> dict:
        """One dashboard card (#8604): title, Mon-Sun statuses, judge result, and the top actions.

        The actions are the photo action (a pending choice, or a rejected set
        that needs reshooting), then every failed stage and every due stage
        with no complete result, in day order. A legitimately held Sunday is
        neither failed nor missing.
        """
        from backend.utils import photo_review
        from backend.utils.episode_integrity import (
            _recipe_title,
            episode_is_published,
            recorded_photo_hold,
            stages_due,
        )

        episode_id = str(data.get("episode_id") or "")
        stages = data.get("stages")
        if not isinstance(stages, dict):
            stages = {}
        days = []
        for day in STAGE_ORDER:
            entry = stages.get(day)
            days.append({
                "day": day,
                "label": STAGE_LABELS[day],
                "status": entry.get("status", "unknown") if isinstance(entry, dict) else "not_started",
            })

        published = episode_is_published(data)
        episode_url = f"/admin/episodes/{episode_id}"

        try:
            due = stages_due(episode_id)
        except ValueError:  # governance: allow-silent SF002: a non-ISO-week id has no cron schedule, so no stage can be known to be missing
            due = []
        # One authoritative photo read per unpublished episode, shared by status and actions.
        view = None if published else _load_photo_view(episode_id, data)
        # A recorded photo hold is a deliberate wait only while the current control
        # still warrants it; once the photos are approved, Sunday needs a rerun.
        if view and recorded_photo_hold(data) and view.state in (photo_review.AWAITING, photo_review.REJECTED):
            due = [d for d in due if d != "sunday"]
        # Stages that are due and have no complete result. A published week is history.
        missing = set() if published else {d["day"] for d in days if d["day"] in due and d["status"] != "complete"}

        photo_actions = []
        if view:
            if view.state == photo_review.AWAITING:
                photo_actions.append({"label": "Pending photo choice", "url": f"{episode_url}#photo-review"})
            elif view.state == photo_review.REJECTED:
                # Publication is blocked until new photos are shot and chosen.
                photo_actions.append({"label": "Reshoot needed: photos rejected", "url": f"{episode_url}#photo-review"})

        if published:
            status = "Published"
        elif any(d["status"] == "failed" for d in days) or missing:
            status = "Failed"
        else:
            status = "In progress"

        actions = list(photo_actions)
        # Every failed stage, and every due stage with no complete result, in day order.
        for d in ([] if published else days):
            if d["status"] == "failed":
                kind = "Failed"
            elif d["day"] in missing:
                kind = "Missing"
            else:
                continue
            actions.append({"label": f"{kind}: {d['label']}", "url": episode_url})

        judged = [
            (day, stages[day]["judge_verdict"])
            for day in STAGE_ORDER
            if isinstance(stages.get(day), dict) and stages[day].get("judge_verdict")
        ]
        judge = {"day": STAGE_LABELS[judged[-1][0]], "verdict": judged[-1][1]} if judged else None

        return {
            "episode_id": episode_id,
            "title": _recipe_title(data) or str(data.get("concept") or episode_id),
            "status": status,
            "days": days,
            "judge": judge,
            "actions": actions,
            "url": episode_url,
        }

    @app.get("/admin/", response_class=HTMLResponse)
    async def admin_dashboard(
        request: Request,
        user: dict = Depends(require_auth)
    ):
        """Current and recent episodes from cloud storage (#8604)."""
        from backend.utils.episode_integrity import current_episode_id

        templates = app.state.templates
        store = _cloud_storage()
        lister = getattr(store, "list_episodes_strict", None) or store.list_episodes
        try:
            with store.prefix_scope(""):
                raw = lister()
        except Exception as exc:
            logger.error(f"Dashboard episode list failed: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not list episodes from storage")

        episodes = sorted(
            (d for d in raw if isinstance(d, dict)),
            key=lambda d: (str(d.get("episode_id") or ""), str(d.get("created_at") or "")),
            reverse=True,
        )
        # Summarize every stored episode once; cards and the queue both read these summaries.
        summaries = [_dashboard_episode(d) for d in episodes]
        current_id = current_episode_id()
        current = next((s for s in summaries if s["episode_id"] == current_id), None)
        recent = [s for s in summaries if s["episode_id"] != current_id][:_DASHBOARD_RECENT_EPISODES]

        # The queue spans the full episode list, newest first, not just the cards shown.
        action_queue = [
            {**action, "episode_id": s["episode_id"], "title": s["title"]}
            for s in summaries
            for action in s["actions"]
        ]

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "user_email": user.get("email", "Unknown"),
                "current_id": current_id,
                "current": current,
                "recent": recent,
                "action_queue": action_queue,
            }
        )
    
    _SAFE_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

    def _published_catalog() -> list[dict]:
        """Published recipes from the Blob catalog (pages/recipes.json), in catalog order.

        Catalog order is newest first. Any read failure, absent catalog, invalid
        JSON or wrong shape raises 503: an unreadable catalog must never render
        as a successful empty list.
        """
        store = _cloud_storage()
        try:
            with store.prefix_scope(""):
                raw = store.load_page("pages/recipes.json")
        except Exception as exc:
            logger.error(f"Recipes catalog read failed: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not read the published catalog")
        if raw is None:
            logger.error("Recipes catalog is absent from storage")
            raise HTTPException(status_code=503, detail="Published catalog not found in storage")
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.error("Recipes catalog JSON is unparseable")
            raise HTTPException(status_code=503, detail="Published catalog is not valid JSON")
        recipes = data.get("recipes") if isinstance(data, dict) else None
        if not isinstance(recipes, list):
            logger.error("Recipes catalog has no 'recipes' list")
            raise HTTPException(status_code=503, detail="Published catalog has an unexpected shape")
        return [
            {
                "slug": r["slug"],
                "title": r["title"].strip(),
                "category": str(r.get("category") or ""),
                "episode_id": str(r.get("episode_id") or ""),
            }
            for r in recipes
            if isinstance(r, dict)
            and isinstance(r.get("slug"), str)
            and _SAFE_SLUG_RE.fullmatch(r["slug"])
            and isinstance(r.get("title"), str)
            and r["title"].strip()
        ]

    @app.get("/admin/recipes", response_class=HTMLResponse)
    async def list_recipes(
        request: Request,
        user: dict = Depends(require_auth)
    ):
        """The Published catalog, read from cloud storage (#8604)."""
        return app.state.templates.TemplateResponse(
            "recipes.html",
            {"request": request, "recipes": _published_catalog()},
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
        _SUBSCRIBE_LIMITER.check(request)

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

    # #7936: the admin viewer serves production ("") or the test namespace
    # ("test/") only. Every review read and write runs inside that prefix.
    _NAMESPACES = {"": "", "test": "test/"}

    def _namespace_prefix(ns: Optional[str]) -> str:
        if ns is None:
            return ""
        if not isinstance(ns, str) or ns not in _NAMESPACES:
            raise HTTPException(status_code=400, detail="Unknown namespace")
        prefix = _NAMESPACES[ns]
        # Local filesystem episodes ignore the storage prefix, so a local
        # "test" view would show production's episode file. Refuse it
        # rather than claim an isolation that does not exist.
        if prefix and not _namespaces_episodes(_cloud_storage()):
            raise HTTPException(
                status_code=400,
                detail="The test namespace exists only on cloud storage; local episodes are not separated.",
            )
        return prefix

    def _namespaces_episodes(store) -> bool:
        check = getattr(store, "namespaces_episodes", None)
        return bool(check()) if callable(check) else False

    def _is_cloud_store(store) -> bool:
        has_cloud = getattr(store, "_has_cloud", None)
        return bool(has_cloud()) if callable(has_cloud) else False

    def _load_cloud_episode(episode_id: str, prefix: str = "") -> dict:
        """Read the stored episode for DISPLAY; a storage failure is an error, never stale disk data.

        Skips this process's episode cache. Decisions never rely on this
        copy: they go through the photo control and a verified read.
        """
        store = _cloud_storage()
        strict = getattr(store, "load_episode_strict", None)
        try:
            with store.prefix_scope(prefix):
                data = strict(episode_id, use_cache=False) if strict else store.load_episode(episode_id)
        except Exception as exc:
            logger.error(f"Episode read failed for {episode_id}: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not read the episode from storage")
        if not data:
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")
        return data

    def _load_photo_view(episode_id: str, data: dict, prefix: str = ""):
        """The authoritative photo control view; a storage failure is a 503."""
        from backend.utils import photo_review

        store = _cloud_storage()
        try:
            with store.prefix_scope(prefix):
                return photo_review.read_view(store, episode_id, data)
        except Exception as exc:
            logger.error(f"Photo control read failed for {episode_id}: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not read the photo review from storage")

    def _sunday_run_has_passed(episode_id: str) -> Optional[bool]:
        """Whether the episode's scheduled Sunday cron time is behind us, or None if unknown."""
        from backend.utils.episode_integrity import stage_deadline

        try:
            return datetime.now(timezone.utc) >= stage_deadline(episode_id, "sunday")
        except (ValueError, KeyError, TypeError):  # governance: allow-silent SF002: a non-ISO-week episode id has no schedule; the caller then says publication needs a Sunday run without promising a time
            return None

    def _rerunnable_stages(episode_id: str, data: dict) -> list[str]:
        """Stages the admin may re-run: failed, or absent once their cron window passed.

        Never for a published week, and never for a Sunday that
        ``recorded_photo_hold`` says is deliberately waiting on photo approval.
        A non-ISO-week id has no schedule, so nothing is offered.
        """
        from backend.utils.episode_integrity import (
            DAY_ORDER, episode_is_published, recorded_photo_hold, stages_due,
        )

        if episode_is_published(data):
            return []
        try:
            due = set(stages_due(episode_id))
        except ValueError:  # governance: allow-silent SF002: a non-ISO-week id has no cron schedule, so no stage is offered
            return []
        recorded = data.get("stages") or {}
        held = recorded_photo_hold(data)
        return [
            day for day in DAY_ORDER
            if not (day == "sunday" and held)
            and (
                (recorded.get(day) or {}).get("status") == "failed"
                or (not recorded.get(day) and day in due)
            )
        ]

    def _approval_message(episode_id: str) -> str:
        passed = _sunday_run_has_passed(episode_id)
        if passed is False:
            return "Approved. Sunday's scheduled run will publish this photo."
        return "Approved. Sunday's scheduled run has passed; publishing needs a manual Sunday run (RUNBOOK)."

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

                from backend.utils.episode_integrity import episode_is_published

                episodes.append({
                    "episode_id": data.get("episode_id", ""),
                    "concept": data.get("concept", "Unknown"),
                    "created_at": data.get("created_at", ""),
                    "published_at": data.get("published_at"),
                    # Legacy-aware (published_at OR complete Sunday); no timestamp is invented.
                    "published": episode_is_published(data),
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

    def _build_episode_detail(
        data: dict, view=None, ns: str = "", can_run: bool = False, rerunnable: tuple = (),
    ) -> dict:
        """Build template-friendly detail from raw episode JSON."""
        stages_raw = data.get("stages", {})
        stages = []
        for key in STAGE_ORDER:
            entry = stages_raw.get(key)
            if entry is None:
                stages.append({
                    "key": key,
                    "rerunnable": key in rerunnable,
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
                "rerunnable": key in rerunnable,
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
        from backend.utils.episode_integrity import episode_is_published

        review = _photo_review_view(data, view, ns)
        published = episode_is_published(data)
        if data.get("published_at"):
            published_label = str(data["published_at"])
        elif published:
            # A pre-published_at week: published (complete Sunday) with no recorded time.
            published_label = "Published (legacy week, no timestamp recorded)"
        else:
            published_label = "Not published"
        # The review is rendered from the authoritative control, whether or
        # not the episode's Wednesday mirror exists (#7936, Codex cycle 2): a
        # registration whose episode save failed, or a stale stage save that
        # dropped Wednesday, still has a set to decide on. With no request
        # the section appears only where a Wednesday record exists.
        review["show"] = bool(view is not None and view.request is not None) \
            or isinstance(stages_raw.get("wednesday"), dict)
        return {
            "episode_id": data.get("episode_id", ""),
            "concept": data.get("concept", ""),
            "created_at": data.get("created_at", ""),
            "published_at": data.get("published_at"),
            "published": published,
            "published_label": published_label,
            "dry_run": data.get("dry_run", False),
            "recipe_id": data.get("recipe_id"),
            "events": data.get("events", []),
            "stages": stages,
            "photo_review": review,
            "image_display_urls": _image_display_urls(data, view, ns),
            "ns": ns,
            "can_run": can_run,
        }

    def _image_display_urls(data: dict, view=None, ns: str = "") -> dict:
        """Same-origin gallery URLs for the Wednesday rounds, by path.

        In the test namespace only images that are candidates of the CURRENT
        review set (same path and URL) are mapped, through the set-bound
        proxy; any other path is absent and the template says so instead of
        showing a broken or another set's image.
        """
        from backend.publishing.episode_renderer import _to_local_image_url

        wed = data.get("stages", {}).get("wednesday", {}) or {}
        pairs = [(p, u) for p, u in zip(wed.get("image_paths") or [], wed.get("image_urls") or []) if p and u]
        if ns != "test":
            return {p: _to_local_image_url(u) for p, u in pairs}
        if view is None or view.request is None:
            return {}
        candidates = {c.get("path"): c for c in view.request.get("candidates") or []}
        return {
            p: _candidate_display_url(view.episode_id, candidates[p], ns, view.image_set_id)
            for p, u in pairs
            if p in candidates and candidates[p].get("url") == u
        }

    def _review_evaluation(data: dict, view) -> Optional[dict]:
        """The automated evaluation OF THE AUTHORITATIVE SET, or None (#7936).

        The control request's own snapshot when it has one. Otherwise the
        episode's Wednesday fields, but only when the episode mirror is for
        the same image set: another set's team pick, defects or status is
        never shown against these photos.
        """
        from backend.utils import photo_review

        snap = photo_review.request_snapshot(view.request)
        if snap is not None:
            return snap
        mirrored = photo_review.episode_request(data)
        if mirrored and mirrored.get("image_set_id") == view.image_set_id:
            return data.get("stages", {}).get("wednesday", {}) or {}
        return None

    def _is_evaluated(evaluation: Optional[dict]) -> bool:
        """Whether the set carries any automated verdict (a remade set has none, #8073)."""
        if evaluation is None:
            return False
        photo = evaluation.get("photography_data")
        photo = photo if isinstance(photo, dict) else {}
        return bool(
            evaluation.get("confirmed_winner") or photo.get("automated_review_status")
            or any(isinstance(r, dict) and r.get("vision_evaluation") for r in photo.get("rounds") or [])
        )

    def _automated_notes(wed: dict) -> tuple[Optional[str], dict]:
        """The automated pick and each candidate's recorded defects, by path."""
        winner = wed.get("confirmed_winner") or {}
        photo = wed.get("photography_data") if isinstance(wed.get("photography_data"), dict) else {}
        defects: dict = {}
        for rnd in photo.get("rounds", []) or []:
            for v in (rnd.get("variants") or []) if isinstance(rnd, dict) else []:
                scores = v.get("scores") if isinstance(v, dict) else None
                if isinstance(scores, dict) and isinstance(scores.get("defects"), list):
                    defects[v.get("path")] = [str(d)[:160] for d in scores["defects"] if d][:5]
        return (winner.get("path") if isinstance(winner, dict) else None), defects

    def _candidate_display_url(episode_id: str, candidate: dict, ns: str, image_set: Optional[str]) -> str:
        """Same-origin image URL for a candidate (CSP allows only 'self').

        Production URLs map to the /blob-images/ rewrite. Test-namespace
        images live under test/images/, which that rewrite cannot reach, so
        they go through the authenticated proxy below. The proxy URL names
        the image set: a replacement set's index 1 is a different URL, so a
        page reviewing one set can never be shown another set's pixels.
        """
        from backend.publishing.episode_renderer import _to_local_image_url

        if ns == "test":
            return f"/admin/episodes/{episode_id}/photos/{image_set}/{candidate['index']}/image?ns=test"
        return _to_local_image_url(candidate["url"])

    def _photo_review_view(data: dict, view, ns: str = "") -> dict:
        from backend.utils import photo_review

        if view is None or view.request is None:
            return {"status": None, "candidates": [], "writable": False,
                    "note": "No current photo set to review."}
        from backend.utils.episode_integrity import episode_is_published

        selected = view.selected
        published = episode_is_published(data) or view.state == photo_review.PUBLISHED
        frozen = view.frozen and not published
        evaluation = _review_evaluation(data, view)
        mirrored = photo_review.episode_request(data) if view.version else None
        mirror_agrees = bool(view.version == 0 or (mirrored and mirrored.get("image_set_id") == view.image_set_id))
        recommended_path, defects = _automated_notes(evaluation) if evaluation is not None else (None, {})
        automated = (evaluation.get("photography_data") or {}).get("automated_review_status") \
            if evaluation is not None and isinstance(evaluation.get("photography_data"), dict) else None
        # An "approved" version whose decision approves nothing valid is
        # shown as Sunday treats it: still awaiting a choice.
        if view.state == photo_review.APPROVED:
            status_value = photo_review.APPROVED if selected else photo_review.AWAITING
        elif view.state in photo_review.DECIDABLE:
            status_value = view.state
        else:
            status_value = photo_review.APPROVED if selected else view.state
        note = ""
        if published:
            note = "Published. The hero is pinned; changes are editorial overrides."
        elif frozen:
            note = "Publishing is underway. Choices are frozen."
        elif selected:
            note = _approval_message(str(data.get("episode_id") or view.episode_id))
        elif view.state == photo_review.REJECTED:
            note = "Rejected. Sunday will not publish. Rejecting makes no new photos; use Reshoot for a new set."
        elif view.legacy:
            note = ("These photos were made before photo review existed, so no "
                    "\"photos ready\" email went out. Sunday publishes only after you choose.")
        elif _sunday_run_has_passed(view.episode_id):
            note = "Sunday's scheduled run has passed. After you choose, publishing needs a manual Sunday run."
        return {
            "status": status_value,
            "frozen": frozen,
            "image_set_id": view.image_set_id,
            "selected_path": selected["path"] if selected else None,
            "decided_at": (view.decision or {}).get("decided_at"),
            "automated_status": automated,
            "evaluation_unavailable": not _is_evaluated(evaluation),
            "mirror_agrees": mirror_agrees,
            "legacy": view.legacy,
            "writable": not published and not frozen,
            "reshoot_calls_left": view.reshoot_calls_left,
            "reshoot_cap": photo_review.RESHOOT_CALL_CAP,
            "can_reshoot": view.reshoot_calls_left >= photo_review.RESHOOT_CALLS,
            "sunday_passed": bool(_sunday_run_has_passed(view.episode_id)),
            "note": note,
            "candidates": [
                {**c, "display_url": _candidate_display_url(view.episode_id, c, ns, view.image_set_id),
                 "recommended": c["path"] == recommended_path,
                 "defects": defects.get(c["path"], [])}
                for c in view.request.get("candidates", [])
            ],
        }

    @app.get("/admin/episodes", response_class=HTMLResponse)
    async def admin_episodes(
        request: Request,
        user: dict = Depends(require_auth),
        page: int = 1,
        page_size: int = 20,
        ns: Optional[str] = None,
    ):
        """Browse full-week episode production logs."""
        templates = app.state.templates
        prefix = _namespace_prefix(ns)
        store = _cloud_storage()
        lister = getattr(store, "list_episodes_strict", None) or store.list_episodes
        try:
            with store.prefix_scope(prefix):
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
                "ns": ns or "",
            },
        )

    @app.get("/admin/episodes/{episode_id}", response_class=HTMLResponse)
    async def admin_episode_detail(
        request: Request,
        episode_id: str,
        user: dict = Depends(require_auth),
        ns: Optional[str] = None,
    ):
        """Full stage-by-stage viewer for a single episode."""
        _sanitize_id(episode_id, "episode_id")
        prefix = _namespace_prefix(ns)
        templates = app.state.templates
        from backend.utils.episode_integrity import episode_is_published

        data = _load_cloud_episode(episode_id, prefix)
        local_production = not _is_cloud_store(_cloud_storage()) and not prefix
        episode = _build_episode_detail(
            data, _load_photo_view(episode_id, data, prefix), ns=ns or "",
            # The compressed-week simulation writes production stages with no
            # namespace of its own (#7936): local, production view, and never a
            # published week (published_at OR complete Sunday, the legacy-aware
            # predicate the static builder uses).
            can_run=local_production and not episode_is_published(data),
            rerunnable=tuple(() if prefix else _rerunnable_stages(episode_id, data)),
        )

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
        episode_id: str, user: dict, *, action: str, image_set: str, path: Optional[str], prefix: str,
    ) -> dict:
        """Validate against the CURRENT photo control and append the decision (#7936).

        The episode is read freshness-verified (its published state, and the
        request when no control exists yet). The decision is a compare-and-
        swap on the control: it cannot interleave with Sunday's claim.
        Never writes the episode, never generates or copies images.
        """
        from backend.storage import EpisodeReadStale
        from backend.utils import photo_review

        _sanitize_id(episode_id, "episode_id")
        store = _cloud_storage()
        try:
            with store.prefix_scope(prefix):
                data = store.load_episode_verified(episode_id)
        except EpisodeReadStale as exc:
            logger.error(f"Episode read not verified for {episode_id}: {exc}")
            raise HTTPException(status_code=503, detail="Storage is still updating. Try again in a minute.")
        except Exception as exc:
            logger.error(f"Episode read failed for {episode_id}: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=503, detail="Could not read the episode from storage")
        if not data:
            raise HTTPException(status_code=404, detail=f"Episode not found: {episode_id}")
        # The control must be readable and well-formed BEFORE anything is
        # decided (#7936, Codex cycle 2): a malformed version is a 503 that
        # says so, never a decision and never a "could not confirm the save".
        _load_photo_view(episode_id, data, prefix)
        try:
            with store.prefix_scope(prefix):
                view = photo_review.record_decision(
                    store, episode_id, data,
                    action=action, image_set=image_set, path=path,
                    **_decision_actor(user),
                )
        except photo_review.PhotoReviewError as exc:
            from backend.utils.episode_integrity import episode_is_published

            raise HTTPException(status_code=409 if episode_is_published(data) else 400, detail=str(exc))
        except photo_review.PublicationUnderway:
            raise HTTPException(status_code=409, detail="Publishing is underway. Choices are frozen.")
        except photo_review.ReviewConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except Exception as exc:
            logger.error(f"Photo decision save failed for {episode_id}: {type(exc).__name__}: {exc}")
            # The write may have landed before the error: do not promise
            # that nothing changed.
            raise HTTPException(
                status_code=503,
                detail="Could not confirm the save. Reload to check your selection.",
            )
        status_value = view.state
        if status_value == photo_review.APPROVED:
            message = _approval_message(episode_id)
        else:
            message = "Rejected. Sunday will not publish. Rejecting makes no new photos; use Reshoot for a new set."
        logger.info(f"Photo review for {episode_id}: {status_value} (control v{view.version})")
        return {"success": True, "status": status_value, "message": message, "version": view.version}

    @app.post("/admin/episodes/{episode_id}/photos/review")
    async def admin_photo_review(
        episode_id: str,
        request_data: PhotoReviewRequest,
        request: Request,
        user: dict = Depends(require_auth),
        ns: Optional[str] = None,
    ):
        """Select one candidate photo, or reject all. Sunday publishes only an approval."""
        _require_same_origin(request)
        prefix = _namespace_prefix(ns)
        return _record_photo_decision(
            episode_id, user,
            action=request_data.action,
            image_set=request_data.image_set_id,
            path=request_data.path,
            prefix=prefix,
        )

    class PhotoReshootRequest(BaseModel):
        image_set_id: str

    class PhotoRemakeRequest(PhotoReshootRequest):
        path: str

    def _run_reshoot(episode_id: str, request: Request, ns: Optional[str], run, what: str) -> dict:
        """The checks and error mapping both reshoot routes share (#8073).

        ``run(cron_routes)`` makes the photos. Refusals (frozen, stale page, a
        shoot running, call cap) are 409 with the reason.
        """
        from backend.admin import cron_routes
        from backend.storage import EpisodeReadStale

        _require_same_origin(request)
        _sanitize_id(episode_id, "episode_id")
        prefix = _namespace_prefix(ns)
        store = _cloud_storage()
        try:
            with store.prefix_scope(prefix):
                result = run(cron_routes)
        except HTTPException:
            raise
        except EpisodeReadStale as exc:
            logger.error(f"Episode read not verified for {episode_id}: {exc}")
            raise HTTPException(status_code=503, detail="Storage is still updating. Try again in a minute.")
        except Exception as exc:
            logger.error(f"Photo {what} failed for {episode_id}: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=502, detail=f"The {what} failed ({type(exc).__name__}). Reload to check.")
        message = f"New photos are up for review. {result['calls_left']} reshoot image calls left."
        if _sunday_run_has_passed(episode_id):
            message += " Sunday has already run its schedule; it holds until a manual run (RUNBOOK)."
        logger.info(f"Photo {what} for {episode_id}: set {result['image_set_id']}")
        return {"success": True, "message": message, **result}

    @app.post("/admin/episodes/{episode_id}/photos/reshoot")
    async def admin_photo_reshoot(
        episode_id: str,
        request_data: PhotoReshootRequest,
        request: Request,
        user: dict = Depends(require_auth),
        ns: Optional[str] = None,
    ):
        """Shoot a new set of Wednesday photos for this week (#8073). Photos only.

        Async like the review POST and every cron: the storage prefix is a plain
        attribute on the shared singleton, so the minutes-long shoot must not
        interleave with another request on a worker thread (RUNBOOK Incident 1).
        Success returns the new set and the calls left.
        """
        return _run_reshoot(
            episode_id, request, ns,
            lambda cron: cron.reshoot_wednesday_photos(episode_id, request_data.image_set_id), "reshoot",
        )

    @app.post("/admin/episodes/{episode_id}/photos/remake")
    async def admin_photo_remake(
        episode_id: str,
        request_data: PhotoRemakeRequest,
        request: Request,
        user: dict = Depends(require_auth),
        ns: Optional[str] = None,
    ):
        """Remake ONE photo of the current set (#8073): one image call, async like the reshoot.

        A ``path`` outside the current set is a 400.
        """
        return _run_reshoot(
            episode_id, request, ns,
            lambda cron: cron.remake_wednesday_photo(episode_id, request_data.image_set_id, request_data.path),
            "remake",
        )

    @app.post("/admin/episodes/{episode_id}/stages/{stage}/rerun")
    async def admin_stage_rerun(
        episode_id: str,
        stage: str,
        request: Request,
        user: dict = Depends(require_auth),
        ns: Optional[str] = None,
    ):
        """Re-run one failed or missing stage by calling its real cron handler once.

        Production namespace only. Eligibility is re-read from storage here, not
        trusted from the page. The handler gets a synthetic POST carrying the
        CRON_SECRET (never logged) and ``force``; no cron logic is duplicated.
        """
        import os

        from starlette.requests import Request as StarletteRequest

        from backend.admin import cron_routes
        from backend.config import config
        from backend.utils.episode_integrity import DAY_ORDER, parse_episode_id

        _require_same_origin(request)
        _sanitize_id(episode_id, "episode_id")
        if ns:
            raise HTTPException(status_code=400, detail="Stage re-runs are production-only")
        try:
            parse_episode_id(episode_id)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Not an ISO week episode id: {episode_id}")
        if stage not in DAY_ORDER:
            raise HTTPException(status_code=400, detail=f"Unknown stage: {stage}")

        data = _load_cloud_episode(episode_id, "")
        if stage not in _rerunnable_stages(episode_id, data):
            raise HTTPException(
                status_code=409,
                detail=f"{stage} is not re-runnable for {episode_id}: it must be failed or missing, and the week unpublished and not held for photos.",
            )

        secret = os.environ.get("CRON_SECRET", "")
        if not secret and not config.is_local_dev:
            raise HTTPException(status_code=503, detail="CRON_SECRET is not configured; cannot dispatch the stage")
        payload = json.dumps({"episode_id": episode_id, "force": True}).encode()

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        cron_request = StarletteRequest(
            {
                "type": "http",
                "method": "POST",
                "scheme": "https",
                "server": ("localhost", 443),
                "root_path": "",
                "path": f"/api/cron/{stage}",
                "query_string": b"",
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"authorization", f"Bearer {secret}".encode()),
                ],
            },
            receive,
        )
        logger.info(f"Admin re-run of {stage} for {episode_id}")
        return await getattr(cron_routes, f"cron_{stage}")(cron_request)

    # Test-namespace images are stored under test/images/ in the same public
    # store; only exact URLs under this origin are ever fetched (#7936).
    from backend.publishing.episode_renderer import BLOB_CDN_PREFIX as _BLOB_IMAGES
    _TEST_IMAGES_PREFIX = _BLOB_IMAGES.removesuffix("images/") + "test/images/"

    @app.get("/admin/episodes/{episode_id}/photos/{image_set}/{index}/image")
    async def admin_test_photo(
        episode_id: str,
        image_set: str,
        index: int,
        user: dict = Depends(require_auth),
        ns: Optional[str] = None,
    ):
        """Serve one TEST-namespace candidate same-origin, for the review page.

        Production candidates use the public /blob-images/ rewrite; that
        rewrite maps to images/ only, and the admin CSP allows only
        same-origin images, so test candidates come through here. The URL
        names the image set, which must be the authoritative current one:
        a page still showing a replaced set gets a refusal, never the new
        set's pixels. The image URL is taken from the control request by
        index, never from the client, and must sit under the store's
        test/images/ path. Not cached: each load re-checks the set.
        """
        import re

        from fastapi.responses import Response
        import requests as _requests

        _sanitize_id(episode_id, "episode_id")
        prefix = _namespace_prefix(ns)
        if prefix != "test/":
            raise HTTPException(status_code=404, detail="Only test-namespace photos are served here")
        if not re.fullmatch(r"[0-9a-f]{16}", image_set):
            raise HTTPException(status_code=404, detail="No such photo set")
        data = _load_cloud_episode(episode_id, prefix)
        view = _load_photo_view(episode_id, data, prefix)
        if not view.image_set_id or view.image_set_id != image_set:
            raise HTTPException(status_code=409, detail="The photos changed since this page loaded; reload.")
        candidate = next(
            (c for c in (view.request or {}).get("candidates") or [] if c.get("index") == index), None,
        )
        url = str((candidate or {}).get("url") or "")
        rest = url[len(_TEST_IMAGES_PREFIX):]
        if (
            not url.startswith(_TEST_IMAGES_PREFIX)
            or not rest
            or any(bad in rest for bad in ("..", "?", "#", "//", "\\"))
        ):
            raise HTTPException(status_code=404, detail="No such test-namespace photo")
        try:
            resp = _requests.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as exc:
            logger.error(f"Test photo fetch failed for {episode_id} #{index}: {type(exc).__name__}: {exc}")
            raise HTTPException(status_code=502, detail="Could not load the photo from storage")
        media_type = str(resp.headers.get("Content-Type") or "").split(";")[0].strip()
        if not media_type.startswith("image/"):
            raise HTTPException(status_code=502, detail="Storage did not return an image")
        return Response(content=resp.content, media_type=media_type,
                        headers={"Cache-Control": "private, no-store"})

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
    async def admin_rerun_photography(episode_id: str, user: dict = Depends(require_auth)):
        """Retired (#7936): it wrote a local stage no cloud review could see.

        Generates nothing and writes nothing. A reshoot is the Reshoot button
        on the photo review (``admin_photo_reshoot``).
        """
        _sanitize_id(episode_id, "episode_id")
        raise HTTPException(
            status_code=410,
            detail=(
                "Retired. Existing photos are reviewed at "
                f"/admin/episodes/{episode_id}#photo-review, where the Reshoot button makes "
                "a new set (#8073)."
            ),
        )

    @app.delete("/admin/episodes/{episode_id}")
    async def admin_episode_delete(
        episode_id: str,
        user: dict = Depends(require_auth),
    ):
        """Retired (#7936, Codex cycle 3). Deletes nothing.

        This trashed a LOCAL episode file and its whole image directory
        after a check of ``published_at`` only. That check-then-trash could
        run while the photo control was claimed/publishing (a failed
        publishing save leaves the episode unpublished but its checkpoint
        committed), and raced a concurrent claim, so the next Sunday restored
        a publication whose images were in the trash. The action is retired
        rather than fenced: local cleanup is a manual operator step
        (RUNBOOK, "Photo approval", local cleanup).
        """
        _sanitize_id(episode_id, "episode_id")
        raise HTTPException(
            status_code=410,
            detail=(
                "Retired. The admin viewer no longer deletes episodes or images. Local cleanup is a "
                "manual operator step after checking the photo control (RUNBOOK: photo approval)."
            ),
        )

    @app.post("/admin/episodes/{episode_id}/run")
    async def admin_episode_run(
        episode_id: str,
        user: dict = Depends(require_auth),
        ns: Optional[str] = None,
    ):
        """Simulate a compressed week on LOCAL production data (dialogue stubs only).

        The simulation writes whole stages through the storage singleton
        with no namespace of its own (#7936, Codex cycle 2): offered from a
        test-namespace page it overwrote production. Like Delete it is a
        local-filesystem action: refused (409) on cloud storage, in any
        namespace and for a published week, before anything runs.
        """
        _sanitize_id(episode_id, "episode_id")
        prefix = _namespace_prefix(ns)
        store = _cloud_storage()
        if prefix or getattr(store, "prefix", ""):
            raise HTTPException(
                status_code=409,
                detail="The compressed-week simulation is local production-only; it is not available in the test namespace.",
            )
        if _is_cloud_store(store):
            raise HTTPException(
                status_code=409,
                detail="The compressed-week simulation is local-only and not available for cloud-stored episodes.",
            )

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
            from backend.utils.episode_integrity import episode_is_published

            if episode_is_published(ep_data):
                raise HTTPException(
                    status_code=409,
                    detail=f"Episode {episode_id} is published; the simulation will not rewrite its history.",
                )

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
