"""
FastAPI application for the admin dashboard.

Provides a web interface for:
- Reviewing and approving recipes
- Publishing recipes to the live site
- Monitoring agent status
- Triggering new recipe generation
"""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

from backend.auth.oauth import GoogleOAuth
from backend.auth.session import JWTSessionManager
from backend.auth.middleware import init_auth_middleware
from backend.admin.routes import create_routes
from backend.admin.cron_routes import router as cron_router
from backend.utils.logging import get_logger

logger = get_logger(__name__)


def create_admin_app(
    project_root: Optional[Path] = None,
    session_manager: Optional[JWTSessionManager] = None,
    oauth_client: Optional[GoogleOAuth] = None
) -> FastAPI:
    """
    Create and configure the admin dashboard FastAPI application.
    
    Args:
        project_root: Project root directory
        session_manager: Configured JWTSessionManager (creates new if None)
        oauth_client: Configured GoogleOAuth client (creates new if None)
        
    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="Muffin Pan Recipes - Admin Dashboard",
        description="Admin interface for recipe review and publishing",
        version="0.1.0"
    )

    resolved_project_root = project_root or Path.cwd()
    
    # Initialize auth components
    # JWT sessions are stateless — no server-side storage needed.
    # Works on Vercel serverless (no cold-start session loss).
    if not session_manager:
        session_manager = JWTSessionManager(session_duration_hours=24)
    
    if not oauth_client:
        oauth_client = GoogleOAuth()
    
    # Initialize middleware
    init_auth_middleware(session_manager)
    
    # Store components in app state
    app.state.session_manager = session_manager
    app.state.oauth_client = oauth_client
    app.state.project_root = resolved_project_root
    
    # Set up Jinja2 templates
    templates_dir = Path(__file__).parent / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    # Serve project static assets for admin previews (images, css, etc.)
    static_root = app.state.project_root / "src"
    if static_root.exists():
        app.mount("/static", StaticFiles(directory=str(static_root)), name="static")

    # Include admin UI routes
    create_routes(app)

    # Include cron API routes (/api/cron/monday ... /api/cron/sunday)
    app.include_router(cron_router)
    
    # Startup event
    @app.on_event("startup")
    async def startup_event():
        logger.info("Admin dashboard starting...")
        logger.info(f"Project root: {app.state.project_root}")
    
    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "admin_dashboard"}
    
    return app


# Module-level app instance — Vercel's @vercel/python runtime imports this directly.
# Also used by `uvicorn backend.admin.app:app` for local dev.
app = create_admin_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True
    )
