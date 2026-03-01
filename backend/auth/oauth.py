"""
Google OAuth 2.0 authentication flow.

Handles the complete OAuth flow:
1. Generate authorization URL to redirect user to Google login
2. Handle callback with authorization code
3. Exchange code for tokens
4. Verify ID tokens
5. Check email against whitelist
"""

import os
import time
from typing import Optional, Dict, Any
from urllib.parse import urlencode
import secrets

import httpx
from jose import jwt, jwk, JWTError

from backend.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# JWKS cache — Google rotates keys infrequently; cache avoids hitting the
# JWKS endpoint on every login while still picking up rotations.
# ---------------------------------------------------------------------------
_jwks_cache: Optional[Dict[str, Any]] = None
_jwks_cache_expiry: float = 0.0
_JWKS_CACHE_TTL = 3600  # 1 hour


class GoogleOAuth:
    """
    Google OAuth 2.0 client for admin authentication.
    
    Requires environment variables:
    - GOOGLE_CLIENT_ID
    - GOOGLE_CLIENT_SECRET
    - GOOGLE_AUTHORIZED_EMAILS (comma-separated list)
    """
    
    # Google OAuth endpoints
    AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
    USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
    JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
    
    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: str = "http://localhost:8000/auth/callback",
        authorized_emails: Optional[set] = None
    ):
        """
        Initialize Google OAuth client.
        
        Args:
            client_id: Google OAuth client ID (defaults to env var)
            client_secret: Google OAuth client secret (defaults to env var)
            redirect_uri: OAuth callback URL
            authorized_emails: Set of authorized email addresses (defaults to env var)
        """
        self.client_id = client_id or os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("GOOGLE_CLIENT_SECRET")
        # GOOGLE_REDIRECT_URI env var overrides the default for Vercel/production deployments.
        self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", redirect_uri)
        
        # Parse authorized emails from env or parameter
        if authorized_emails:
            self.authorized_emails = authorized_emails
        else:
            emails_str = os.getenv("GOOGLE_AUTHORIZED_EMAILS", "")
            self.authorized_emails = {
                email.strip() for email in emails_str.split(",") if email.strip()
            }
        
        if not self.client_id or not self.client_secret:
            logger.warning("Google OAuth credentials not configured")
        
        if not self.authorized_emails:
            logger.warning("No authorized emails configured")
        
        logger.info(f"GoogleOAuth initialized with {len(self.authorized_emails)} authorized emails")
    
    def get_authorization_url(self, state: Optional[str] = None) -> tuple[str, str]:
        """
        Generate the Google OAuth authorization URL.
        
        Args:
            state: Optional state parameter for CSRF protection (auto-generated if not provided)
            
        Returns:
            Tuple of (authorization_url, state)
        """
        if not state:
            state = secrets.token_urlsafe(32)
        
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "consent"
        }
        
        auth_url = f"{self.AUTHORIZATION_ENDPOINT}?{urlencode(params)}"
        logger.info(f"Generated authorization URL with state: {state[:8]}...")
        
        return auth_url, state
    
    async def handle_callback(self, code: str, state: str) -> Optional[Dict[str, Any]]:
        """
        Handle OAuth callback and exchange code for tokens.
        
        Args:
            code: Authorization code from Google
            state: State parameter for CSRF validation
            
        Returns:
            User info dict if successful, None otherwise
        """
        try:
            # Exchange code for tokens
            token_data = await self._exchange_code_for_tokens(code)

            if not token_data:
                return None

            # Verify and decode ID token
            user_info = await self._verify_id_token(
                token_data.get("id_token"),
                access_token=token_data.get("access_token"),
            )

            if not user_info:
                return None

            # Check if email is authorized
            email = user_info.get("email")
            if not self.is_email_authorized(email):
                logger.warning(f"Unauthorized email attempted login: {email}")
                return None

            logger.info(f"Successful authentication for: {email}")
            return user_info

        except Exception as e:
            logger.error(f"OAuth callback error: {e}", exc_info=True)
            return None
    
    async def _exchange_code_for_tokens(self, code: str) -> Optional[Dict[str, Any]]:
        """
        Exchange authorization code for access and ID tokens.
        
        Args:
            code: Authorization code
            
        Returns:
            Token response dict
        """
        payload = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code"
        }
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    self.TOKEN_ENDPOINT,
                    data=payload,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Token exchange failed: {response.status_code} - {response.text}")
                    return None
                    
            except Exception as e:
                logger.error(f"Token exchange error: {e}", exc_info=True)
                return None
    
    async def _fetch_jwks(self) -> Dict[str, Any]:
        """Fetch Google's JWKS (JSON Web Key Set) with caching."""
        global _jwks_cache, _jwks_cache_expiry

        now = time.monotonic()
        if _jwks_cache is not None and now < _jwks_cache_expiry:
            return _jwks_cache

        async with httpx.AsyncClient() as client:
            response = await client.get(self.JWKS_URI, timeout=10.0)
            response.raise_for_status()
            _jwks_cache = response.json()
            _jwks_cache_expiry = now + _JWKS_CACHE_TTL
            logger.info("Refreshed Google JWKS cache")
            return _jwks_cache

    async def _verify_id_token(self, id_token: str, access_token: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Verify and decode Google ID token using JWKS signature verification.

        Args:
            id_token: JWT ID token from Google

        Returns:
            Decoded token claims if signature + claims are valid, None otherwise.
        """
        try:
            # Fetch Google's public keys and verify the signature.
            google_jwks = await self._fetch_jwks()

            decoded = jwt.decode(
                id_token,
                google_jwks,
                algorithms=["RS256"],
                audience=self.client_id,
                issuer=["https://accounts.google.com", "accounts.google.com"],
                access_token=access_token,
            )

            return decoded

        except JWTError as e:
            logger.error(f"ID token verification failed: {e}")
            return None
        except Exception as e:
            logger.error(f"JWKS fetch or token verification error: {e}", exc_info=True)
            return None
    
    def is_email_authorized(self, email: str) -> bool:
        """
        Check if an email is in the authorized list.
        
        Args:
            email: Email address to check
            
        Returns:
            True if authorized
        """
        if not email:
            return False
        
        return email.lower() in {e.lower() for e in self.authorized_emails}
    
    async def get_user_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Fetch user info from Google using access token.
        
        Args:
            access_token: OAuth access token
            
        Returns:
            User info dict
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    self.USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"User info fetch failed: {response.status_code}")
                    return None
                    
            except Exception as e:
                logger.error(f"User info fetch error: {e}", exc_info=True)
                return None
