"""
JWT-based session management for authenticated admin users.

Stateless sessions using signed JWT tokens stored in httponly cookies.
No server-side state — works on Vercel serverless where cold starts
wipe in-memory storage.

Requires JWT_SECRET environment variable (generated once, stored in Doppler).
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from jose import jwt, JWTError

from backend.utils.logging import get_logger

logger = get_logger(__name__)

# JWT configuration
JWT_ALGORITHM = "HS256"
DEFAULT_SESSION_HOURS = 24


def _get_jwt_secret() -> str:
    """Get JWT signing secret from environment.

    In production (Vercel), raises RuntimeError if JWT_SECRET is missing.
    In local dev, falls back to an insecure key with a loud warning.
    """
    secret = os.environ.get("JWT_SECRET", "")
    if not secret:
        # Import here to avoid circular dependency at module load
        from backend.config import config

        if not config.is_local_dev:
            raise RuntimeError(
                "JWT_SECRET env var is not set. This is required in production. "
                "Check Doppler sync or Vercel environment variables."
            )
        logger.warning("JWT_SECRET not set — using insecure fallback (local dev only)")
        return "dev-insecure-fallback-key-do-not-use-in-production"
    return secret


class JWTSessionManager:
    """
    Stateless JWT session manager.

    Creates and verifies JWT tokens containing user info.
    No server-side storage — the token IS the session.
    """

    def __init__(self, session_duration_hours: int = DEFAULT_SESSION_HOURS):
        self.session_duration_hours = session_duration_hours
        logger.info(f"JWTSessionManager initialized (duration: {session_duration_hours}h)")

    def create_token(self, email: str, user_info: Dict[str, Any]) -> str:
        """
        Create a signed JWT token for a user.

        Args:
            email: User's email address
            user_info: User information from OAuth (name, picture, etc.)

        Returns:
            Signed JWT token string
        """
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.session_duration_hours)

        payload = {
            "sub": email,
            "email": email,
            "name": user_info.get("name", ""),
            "picture": user_info.get("picture", ""),
            "iat": int(now.timestamp()),
            "exp": int(expires.timestamp()),
        }

        token = jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)
        logger.info(f"Created JWT for {email} (expires: {expires.isoformat()})")
        return token

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """
        Verify and decode a JWT token.

        Args:
            token: JWT token string from cookie

        Returns:
            Decoded claims dict if valid, None if expired/invalid
        """
        try:
            claims = jwt.decode(
                token,
                _get_jwt_secret(),
                algorithms=[JWT_ALGORITHM],
            )
            return claims
        except JWTError as e:
            logger.debug(f"JWT verification failed: {e}")
            return None

    def get_user_email(self, token: str) -> Optional[str]:
        """Extract email from a valid token."""
        claims = self.verify_token(token)
        if claims:
            return claims.get("email")
        return None
