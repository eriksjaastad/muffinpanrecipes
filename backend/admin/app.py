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
from backend.auth.session import SessionManager
from backend.auth.middleware import init_auth_middleware
from backend.admin.routes import create_routes
from backend.admin.cron_routes import router as cron_router
from backend.config import config
from backend.utils.logging import get_logger

logger = get_logger(__name__)


def create_admin_app(
    project_root: Optional[Path] = None,
    session_manager: Optional[SessionManager] = None,
    oauth_client: Optional[GoogleOAuth] = None
) -> FastAPI:
    """
    Create and configure the admin dashboard FastAPI application.
    
    Args:
        project_root: Project root directory
        session_manager: Configured SessionManager (creates new if None)
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
    # On Vercel, serverless functions have a read-only filesystem — sessions stay in-memory only.
    # On local dev, persist sessions to disk so they survive server restarts.
    if not session_manager:
        session_manager = SessionManager(
            session_duration_hours=24,
            persist_to_file=config.is_local_dev,
            storage_path=resolved_project_root / "data" / "sessions.json" if config.is_local_dev else None,
        )
    
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
    templates_dir.mkdir(exist_ok=True)
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
