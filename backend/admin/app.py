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
    if not session_manager:
        session_manager = SessionManager(
            session_duration_hours=24,
            persist_to_file=True,
            storage_path=resolved_project_root / "data" / "sessions.json",
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

    # Include routes
    create_routes(app)
    
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


if __name__ == "__main__":
    # Development server
    import uvicorn
    
    app = create_admin_app()
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=True
    )
