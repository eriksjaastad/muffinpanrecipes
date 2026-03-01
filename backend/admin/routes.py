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
import json
from datetime import datetime

from fastapi import FastAPI, Request, Response, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from backend.data.recipe import Recipe, RecipeStatus
from backend.publishing.pipeline import PublishingPipeline
from backend.newsletter.manager import NewsletterManager
from backend.auth.middleware import require_auth, create_session_cookie, clear_session_cookie
from backend.utils.logging import get_logger

logger = get_logger(__name__)

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

        # Generate authorization URL
        auth_url, state = oauth.get_authorization_url()

        # NOTE: Session middleware is not wired yet, so we do not persist oauth_state
        # in request.session. Callback currently validates token + authorized email.
        # TODO: Add SessionMiddleware and strict state verification.

        return RedirectResponse(url=auth_url)
    
    @app.get("/auth/callback")
    async def oauth_callback(
        request: Request,
        code: str,
        state: str
    ):
        """Handle OAuth callback from Google."""
        oauth = app.state.oauth_client
        session_manager = app.state.session_manager
        
        # Verify state (CSRF protection)
        # In production, check against stored state
        
        # Exchange code for tokens and get user info
        user_info = await oauth.handle_callback(code, state)
        
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed"
            )
        
        # Create session
        session = session_manager.create_session(
            email=user_info["email"],
            user_info=user_info
        )
        
        # Redirect to dashboard + set session cookie on that response
        redirect_response = RedirectResponse(url="/admin/")
        create_session_cookie(redirect_response, session.session_id)
        return redirect_response
    
    @app.get("/auth/logout")
    async def logout(
        request: Request,
        response: Response,
        session_id: Optional[str] = Depends(require_auth)
    ):
        """Logout and clear session."""
        if session_id:
            app.state.session_manager.delete_session(session_id)
        
        clear_session_cookie(response)
        return RedirectResponse(url="/")
    
    # ==================== ADMIN DASHBOARD ====================
    
    @app.get("/admin/", response_class=HTMLResponse)
    async def admin_dashboard(
        request: Request,
        session_id: str = Depends(require_auth)
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
        
        # Get session info
        session = app.state.session_manager.get_session(session_id)
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "stats": stats,
                "user_email": session.email if session else "Unknown",
                "recent_pending": pending_recipes[:5],  # Show 5 most recent
            }
        )
    
    @app.get("/admin/recipes")
    async def list_recipes(
        request: Request,
        status_filter: Optional[str] = None,
        session_id: str = Depends(require_auth)
    ):
        """
        List recipes, optionally filtered by status.
        
        Query params:
            status_filter: pending, approved, published, or rejected
        """
        data_dir = app.state.project_root / "data" / "recipes"
        
        if status_filter:
            try:
                status_enum = RecipeStatus(status_filter)
                recipes = Recipe.list_by_status(data_dir, status_enum)
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: {status_filter}"
                )
        else:
            # Get all recipes
            recipes = []
            for status in RecipeStatus:
                recipes.extend(Recipe.list_by_status(data_dir, status))
        
        # Sort by updated_at desc
        recipes.sort(key=lambda r: r.updated_at, reverse=True)
        
        return {
            "recipes": [
                {
                    "recipe_id": r.recipe_id,
                    "title": r.title,
                    "status": r.status.value,
                    "created_at": r.created_at.isoformat(),
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in recipes
            ]
        }
    
    @app.get("/admin/recipes/{recipe_id}")
    async def get_recipe_detail(
        recipe_id: str,
        session_id: str = Depends(require_auth)
    ):
        """Get full recipe details as JSON."""
        data_dir = app.state.project_root / "data" / "recipes"

        # Find recipe in any status directory
        recipe = None
        for status in RecipeStatus:
            try:
                filepath = data_dir / status.value / f"{recipe_id}.json"
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
        session_id: str = Depends(require_auth)
    ):
        """Render recipe detail review page."""
        data_dir = app.state.project_root / "data" / "recipes"
        templates = app.state.templates

        recipe = None
        for status in RecipeStatus:
            filepath = data_dir / status.value / f"{recipe_id}.json"
            if filepath.exists():
                recipe = Recipe.load_from_file(filepath)
                break

        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")

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
                    image_url = f"/static/assets/images/{featured}"

        recipe_payload["image_url"] = image_url

        return templates.TemplateResponse(
            "recipe_detail.html",
            {
                "request": request,
                "recipe": recipe_payload,
            },
        )
    
    @app.post("/admin/recipes/{recipe_id}/approve")
    async def approve_recipe(
        recipe_id: str,
        request_data: ApproveRequest,
        session_id: str = Depends(require_auth)
    ):
        """Move recipe from pending to approved."""
        data_dir = app.state.project_root / "data" / "recipes"
        
        # Load recipe
        filepath = data_dir / "pending" / f"{recipe_id}.json"
        if not filepath.exists():
            raise HTTPException(status_code=404, detail="Recipe not found in pending")
        
        recipe = Recipe.load_from_file(filepath)
        
        # Transition to approved
        new_path = recipe.transition_status(
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
        session_id: str = Depends(require_auth)
    ):
        """Move recipe to rejected with notes."""
        data_dir = app.state.project_root / "data" / "recipes"
        
        # Find recipe (could be pending or approved)
        recipe = None
        for status in [RecipeStatus.PENDING, RecipeStatus.APPROVED]:
            filepath = data_dir / status.value / f"{recipe_id}.json"
            if filepath.exists():
                recipe = Recipe.load_from_file(filepath)
                break
        
        if not recipe:
            raise HTTPException(status_code=404, detail="Recipe not found")
        
        # Transition to rejected
        new_path = recipe.transition_status(
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
        session_id: str = Depends(require_auth)
    ):
        """Publish approved recipe to live site."""
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
        session_id: str = Depends(require_auth),
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
    async def get_agent_status(session_id: str = Depends(require_auth)):
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
    async def trigger_generation(session_id: str = Depends(require_auth)):
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
    
    @app.post("/api/newsletter/subscribe")
    async def newsletter_subscribe(request_data: NewsletterSubscribeRequest):
        """Public endpoint for newsletter subscription."""
        manager = NewsletterManager()
        result = await manager.subscribe(request_data.email)
        
        if result["success"]:
            return {"success": True, "message": "Successfully subscribed to newsletter!"}
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Subscription failed"))
    
    @app.get("/admin/newsletter/subscribers")
    async def newsletter_list_subscribers(session_id: str = Depends(require_auth)):
        """Admin endpoint to list all newsletter subscribers."""
        manager = NewsletterManager()
        subscribers = await manager.list_subscribers()
        
        return {
            "subscribers": subscribers,
            "total": len(subscribers)
        }
    
    logger.info("Admin routes configured")
