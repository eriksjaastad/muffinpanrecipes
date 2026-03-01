"""Tests for authentication system."""

import os
import time
import pytest

from backend.auth.oauth import GoogleOAuth
from backend.auth.session import JWTSessionManager


class TestGoogleOAuth:
    """Test Google OAuth functionality."""

    def test_initialization(self):
        """Test OAuth client initialization."""
        oauth = GoogleOAuth(
            client_id="test_client_id",
            client_secret="test_secret",
            authorized_emails={"admin@example.com"}
        )

        assert oauth.client_id == "test_client_id"
        assert oauth.client_secret == "test_secret"
        assert "admin@example.com" in oauth.authorized_emails

    def test_authorization_url_generation(self):
        """Test generation of authorization URL."""
        oauth = GoogleOAuth(
            client_id="test_client_id",
            client_secret="test_secret"
        )

        auth_url, state = oauth.get_authorization_url()

        assert "accounts.google.com" in auth_url
        assert "client_id=test_client_id" in auth_url
        assert f"state={state}" in auth_url
        assert "scope=openid+email+profile" in auth_url
        assert len(state) > 20  # State should be a secure random string

    def test_email_authorization_check(self):
        """Test email whitelist checking."""
        oauth = GoogleOAuth(
            client_id="test",
            client_secret="test",
            authorized_emails={"admin@example.com", "erik@example.com"}
        )

        assert oauth.is_email_authorized("admin@example.com") is True
        assert oauth.is_email_authorized("Admin@Example.com") is True  # Case insensitive
        assert oauth.is_email_authorized("unauthorized@example.com") is False
        assert oauth.is_email_authorized("") is False
        assert oauth.is_email_authorized(None) is False


class TestJWTSessionManager:
    """Test JWT session management."""

    def test_create_and_verify_token(self):
        """Test creating a JWT and verifying it."""
        manager = JWTSessionManager(session_duration_hours=24)

        token = manager.create_token(
            email="admin@example.com",
            user_info={"name": "Admin User", "picture": "https://example.com/pic.jpg"}
        )

        assert isinstance(token, str)
        assert len(token) > 50  # JWTs are long strings

        claims = manager.verify_token(token)
        assert claims is not None
        assert claims["email"] == "admin@example.com"
        assert claims["name"] == "Admin User"
        assert claims["sub"] == "admin@example.com"

    def test_get_user_email(self):
        """Test extracting email from token."""
        manager = JWTSessionManager()
        token = manager.create_token("user@example.com", {})

        email = manager.get_user_email(token)
        assert email == "user@example.com"

    def test_invalid_token(self):
        """Test that invalid tokens return None."""
        manager = JWTSessionManager()

        assert manager.verify_token("garbage.token.here") is None
        assert manager.verify_token("") is None
        assert manager.get_user_email("invalid") is None

    def test_expired_token(self):
        """Test that expired tokens are rejected."""
        # Create a manager with 0-hour duration (instant expiry)
        manager = JWTSessionManager(session_duration_hours=0)
        token = manager.create_token("user@example.com", {})

        # Token should already be expired (or expire within the same second)
        time.sleep(1)
        claims = manager.verify_token(token)
        assert claims is None

    def test_wrong_secret_rejected(self):
        """Test that tokens signed with different secrets are rejected."""
        manager = JWTSessionManager()
        token = manager.create_token("user@example.com", {})

        # Tamper with environment to change the secret
        old_secret = os.environ.get("JWT_SECRET", "")
        try:
            os.environ["JWT_SECRET"] = "completely-different-secret"
            manager2 = JWTSessionManager()
            assert manager2.verify_token(token) is None
        finally:
            if old_secret:
                os.environ["JWT_SECRET"] = old_secret
            else:
                os.environ.pop("JWT_SECRET", None)
