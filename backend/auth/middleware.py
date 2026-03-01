"""
FastAPI middleware for authentication and authorization.

Provides:
- require_auth dependency for protecting routes
- Session validation from cookies
- Automatic redirect to login for unauthenticated users
"""

import os
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from backend.auth.session import SessionManager
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Global session manager instance
# This will be initialized by the FastAPI app
_session_manager: Optional[SessionManager] = None


def init_auth_middleware(session_manager: SessionManager):
    """
    Initialize the auth middleware with a session manager.
    
    This should be called once when the FastAPI app starts.
    
    Args:
        session_manager: Configured SessionManager instance
    """
    global _session_manager
    _session_manager = session_manager
    logger.info("Auth middleware initialized")


def get_session_manager() -> SessionManager:
    """
    Dependency to get the session manager.
    
    Returns:
        SessionManager instance
    """
    if _session_manager is None:
        raise RuntimeError("Session manager not initialized. Call init_auth_middleware() first.")
    return _session_manager


async def get_current_session(
    request: Request,
    session_manager: SessionManager = Depends(get_session_manager)
) -> Optional[str]:
    """
    Get the current session ID from cookies.
    
    Args:
        request: FastAPI request object
        session_manager: Session manager dependency
        
    Returns:
        Session ID if found in cookies, None otherwise
    """
    session_id = request.cookies.get("session_id")
    
    if not session_id:
        return None
    
    # Validate session
    if not session_manager.validate_session(session_id):
        return None
    
    return session_id


async def require_auth(
    request: Request,
    session_id: Optional[str] = Depends(get_current_session)
) -> str:
    """
    Dependency to require authentication on a route.
    
    Usage:
        @app.get("/admin/dashboard")
        async def dashboard(session_id: str = Depends(require_auth)):
            # This route is protected
            return {"message": "Protected content"}
    
    Args:
        request: FastAPI request object
        session_id: Current session ID from get_current_session
        
    Returns:
        Session ID if authenticated
        
    Raises:
        HTTPException: 401 if not authenticated (with redirect to login)
    """
    # LOCAL_DEV bypass — no OAuth required when running locally
    if os.environ.get("LOCAL_DEV") == "true":
        return "local-dev-session"

    if not session_id:
        logger.warning(f"Unauthorized access attempt to {request.url.path}")

        # Browser-friendly redirect to login, preserving destination.
        redirect_target = quote(request.url.path, safe="/")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            detail="Authentication required",
            headers={"Location": f"/auth/login?redirect={redirect_target}"},
        )
    
    return session_id


async def optional_auth(
    session_id: Optional[str] = Depends(get_current_session)
) -> Optional[str]:
    """
    Dependency for optional authentication.
    
    Unlike require_auth, this doesn't raise an exception if not authenticated.
    Useful for routes that change behavior based on auth status.
    
    Args:
        session_id: Current session ID from get_current_session
        
    Returns:
        Session ID if authenticated, None otherwise
    """
    return session_id


def create_session_cookie(response: Response, session_id: str, max_age: int = 86400) -> None:
    """
    Set session cookie on response.
    
    Args:
        response: FastAPI response object
        session_id: Session ID to set
        max_age: Cookie max age in seconds (default: 24 hours)
    """
    response.set_cookie(
        key="session_id",
        value=session_id,
        max_age=max_age,
        httponly=True,  # Prevent JavaScript access
        secure=False,   # Set to True in production with HTTPS
        samesite="lax"  # CSRF protection
    )


def clear_session_cookie(response: Response) -> None:
    """
    Clear session cookie (logout).
    
    Args:
        response: FastAPI response object
    """
    response.delete_cookie(key="session_id")
