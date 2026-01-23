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

from fastapi import FastAPI, Request, Response, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel

from backend.data.recipe import Recipe, RecipeStatus
from backend.publishing.pipeline import PublishingPipeline
from backend.newsletter.manager import NewsletterManager
from backend.auth.middleware import require_auth, create_session_cookie, clear_session_cookie
from backend.utils.logging import get_logger

logger = get_logger(__name__)


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
        
        # Store state in session for CSRF protection
        # (In production, store this in Redis or similar)
        request.session = {"oauth_state": state}
        if redirect:
            request.session["redirect_after_login"] = redirect
        
        return RedirectResponse(url=auth_url)
    
    @app.get("/auth/callback")
    async def oauth_callback(
        request: Request,
        response: Response,
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
        
        # Set session cookie
        create_session_cookie(response, session.session_id)
        
        # Redirect to dashboard
        redirect_url = getattr(request, "session", {}).get("redirect_after_login", "/admin/")
        return RedirectResponse(url=redirect_url)
    
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
        """Get full recipe details for review."""
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
