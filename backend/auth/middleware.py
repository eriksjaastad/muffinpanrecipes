"""
FastAPI middleware for authentication and authorization.

Provides:
- require_auth dependency for protecting routes
- JWT token validation from cookies
- Automatic redirect to login for unauthenticated users
"""

from typing import Optional, Dict, Any
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from backend.auth.session import JWTSessionManager
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Global session manager instance
_session_manager: Optional[JWTSessionManager] = None


def init_auth_middleware(session_manager: JWTSessionManager):
    """
    Initialize the auth middleware with a JWT session manager.

    This should be called once when the FastAPI app starts.
    """
    global _session_manager
    _session_manager = session_manager
    logger.info("Auth middleware initialized (JWT mode)")


def get_session_manager() -> JWTSessionManager:
    """Dependency to get the session manager."""
    if _session_manager is None:
        raise RuntimeError("Session manager not initialized. Call init_auth_middleware() first.")
    return _session_manager


async def get_current_user(
    request: Request,
    session_manager: JWTSessionManager = Depends(get_session_manager),
) -> Optional[Dict[str, Any]]:
    """
    Decode the JWT from the session cookie.

    Returns:
        Decoded JWT claims if valid, None otherwise
    """
    token = request.cookies.get("session_token")
    if not token:
        return None

    claims = session_manager.verify_token(token)
    return claims


async def require_auth(
    request: Request,
    user: Optional[Dict[str, Any]] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Dependency to require authentication on a route.

    Returns the decoded JWT claims (email, name, picture, etc.)
    so routes can access user info without extra lookups.

    Raises:
        HTTPException: 307 redirect to login if not authenticated
    """
    from backend.config import config

    if config.auth_bypass:
        return {"email": "dev@localhost", "name": "Local Dev", "sub": "dev@localhost"}

    if not user:
        logger.warning(f"Unauthorized access attempt to {request.url.path}")
        redirect_target = quote(request.url.path, safe="/")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            detail="Authentication required",
            headers={"Location": f"/auth/login?redirect={redirect_target}"},
        )

    return user


async def optional_auth(
    user: Optional[Dict[str, Any]] = Depends(get_current_user),
) -> Optional[Dict[str, Any]]:
    """
    Dependency for optional authentication.

    Unlike require_auth, this doesn't raise an exception if not authenticated.
    """
    return user


def create_session_cookie(response: Response, token: str, max_age: int = 86400) -> None:
    """
    Set JWT session cookie on response.

    Args:
        response: FastAPI response object
        token: Signed JWT token
        max_age: Cookie max age in seconds (default: 24 hours)
    """
    response.set_cookie(
        key="session_token",
        value=token,
        max_age=max_age,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    """Clear session cookie (logout)."""
    response.delete_cookie(key="session_token")
