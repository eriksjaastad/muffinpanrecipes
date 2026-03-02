"""
FastAPI application for the admin dashboard.

Provides a web interface for:
- Reviewing and approving recipes
- Publishing recipes to the live site
- Monitoring agent status
- Triggering new recipe generation
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

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

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN001
        # --- startup ---
        logger.info("Admin dashboard starting...")
        logger.info(f"Project root: {app.state.project_root}")
        yield
        # --- shutdown (nothing to tear down) ---

    app = FastAPI(
        title="Muffin Pan Recipes - Admin Dashboard",
        description="Admin interface for recipe review and publishing",
        version="0.1.0",
        lifespan=lifespan,
    )

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

    # Serve /assets/images/ for local dev (Vercel handles this via rewrites in production)
    assets_root = static_root / "assets"
    if assets_root.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_root)), name="assets")

    # Serve compiled Tailwind CSS for admin UI
    admin_static = Path(__file__).parent / "static"
    if admin_static.exists():
        app.mount("/admin/static", StaticFiles(directory=str(admin_static)), name="admin_static")

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # CSP: safe for admin dashboard (Tailwind CDN, Google Fonts)
        csp_parts = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' cdn.tailwindcss.com",
            "style-src 'self' 'unsafe-inline' cdn.tailwindcss.com fonts.googleapis.com",
            "font-src 'self' fonts.gstatic.com",
            "img-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_parts)

        # HSTS: Only when serving over HTTPS
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

        return response

    # Include admin UI routes
    create_routes(app)

    # Include cron API routes (/api/cron/monday ... /api/cron/sunday)
    app.include_router(cron_router)

    # Health check endpoint
    @app.get("/health")
    async def health_check():
        return {"status": "healthy", "service": "admin_dashboard"}

    return app


# ---------------------------------------------------------------------------
# Module-level app instance — Vercel's @vercel/python runtime imports this
# directly via the `app` name. Also used by `uvicorn backend.admin.app:app`
# for local dev.
#
# Use a lazy wrapper to avoid import-time side effects (OAuth, middleware,
# static mounts) when importing in tests or other contexts. The wrapper
# creates the app on first access.
# ---------------------------------------------------------------------------

_app_instance: Optional[FastAPI] = None


def get_app() -> FastAPI:
    """Return the singleton admin app, creating it lazily on first call.

    Use this in tests and tooling to avoid import-time side effects.
    Vercel and uvicorn use the module-level `app` variable directly.
    """
    global _app_instance
    if _app_instance is None:
        _app_instance = create_admin_app()
    return _app_instance


class _LazyApp:
    """Lazy app wrapper that defers creation until first access.
    
    This allows importing the module without triggering side effects
    (OAuth, middleware, static mounts). The app is created on first
    attribute access, which Vercel/uvicorn will trigger when they
    try to use it.
    """

    def __getattr__(self, name: str):
        """Forward all attribute access to the real app, creating it if needed."""
        app = get_app()
        return getattr(app, name)

    def __call__(self, *args, **kwargs):
        """Allow calling the app as ASGI app."""
        return get_app()(*args, **kwargs)


# Module-level `app` for Vercel / uvicorn — lazily created on first access.
app = _LazyApp()  # type: ignore


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        get_app(),
        host="0.0.0.0",
        port=8000,
        reload=True
    )
