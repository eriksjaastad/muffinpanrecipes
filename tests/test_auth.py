"""Tests for authentication system."""

import pytest
from datetime import datetime, timedelta

from backend.auth.oauth import GoogleOAuth
from backend.auth.session import SessionManager, Session


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


class TestSession:
    """Test Session model."""
    
    def test_session_creation(self):
        """Test session object creation."""
        now = datetime.now()
        expires = now + timedelta(hours=24)
        
        session = Session(
            session_id="test_session_123",
            email="user@example.com",
            user_info={"name": "Test User"},
            created_at=now,
            expires_at=expires
        )
        
        assert session.session_id == "test_session_123"
        assert session.email == "user@example.com"
        assert session.is_valid() is True
    
    def test_session_expiry(self):
        """Test session expiry checking."""
        now = datetime.now()
        expired = now - timedelta(hours=1)  # Expired 1 hour ago
        
        session = Session(
            session_id="expired_session",
            email="user@example.com",
            user_info={},
            created_at=now - timedelta(hours=25),
            expires_at=expired
        )
        
        assert session.is_valid() is False
    
    def test_session_serialization(self):
        """Test session to/from dict conversion."""
        now = datetime.now()
        expires = now + timedelta(hours=24)
        
        session = Session(
            session_id="test_123",
            email="user@example.com",
            user_info={"name": "Test"},
            created_at=now,
            expires_at=expires
        )
        
        # Serialize
        data = session.to_dict()
        assert data["session_id"] == "test_123"
        assert data["email"] == "user@example.com"
        
        # Deserialize
        restored = Session.from_dict(data)
        assert restored.session_id == session.session_id
        assert restored.email == session.email
        assert restored.user_info == session.user_info


class TestSessionManager:
    """Test SessionManager functionality."""
    
    def test_session_creation(self):
        """Test creating a new session."""
        manager = SessionManager(session_duration_hours=24)
        
        session = manager.create_session(
            email="admin@example.com",
            user_info={"name": "Admin User"}
        )
        
        assert session.email == "admin@example.com"
        assert session.is_valid() is True
        assert len(session.session_id) > 20  # Should be secure random
    
    def test_session_retrieval(self):
        """Test retrieving a session."""
        manager = SessionManager()
        
        # Create a session
        created = manager.create_session(
            email="user@example.com",
            user_info={}
        )
        
        # Retrieve it
        retrieved = manager.get_session(created.session_id)
        
        assert retrieved is not None
        assert retrieved.session_id == created.session_id
        assert retrieved.email == created.email
    
    def test_session_validation(self):
        """Test session validation."""
        manager = SessionManager()
        
        session = manager.create_session("user@example.com", {})
        
        # Valid session
        assert manager.validate_session(session.session_id) is True
        
        # Invalid session ID
        assert manager.validate_session("nonexistent_session") is False
    
    def test_session_deletion(self):
        """Test session deletion (logout)."""
        manager = SessionManager()
        
        session = manager.create_session("user@example.com", {})
        session_id = session.session_id
        
        # Session exists
        assert manager.validate_session(session_id) is True
        
        # Delete session
        deleted = manager.delete_session(session_id)
        assert deleted is True
        
        # Session no longer exists
        assert manager.validate_session(session_id) is False
    
    def test_expired_session_cleanup(self):
        """Test cleanup of expired sessions."""
        manager = SessionManager()
        
        # Create a session and manually expire it
        session = manager.create_session("user@example.com", {})
        # Directly modify the session in the manager's dict
        manager.sessions[session.session_id].expires_at = datetime.now() - timedelta(hours=1)
        
        # Cleanup should remove it
        cleaned = manager.cleanup_expired_sessions()
        assert cleaned == 1
        
        # Session should no longer exist
        assert manager.get_session(session.session_id) is None
    
    def test_multiple_sessions(self):
        """Test managing multiple sessions."""
        manager = SessionManager()
        
        # Create multiple sessions
        session1 = manager.create_session("user1@example.com", {})
        session2 = manager.create_session("user2@example.com", {})
        session3 = manager.create_session("user3@example.com", {})
        
        # All should be valid
        assert manager.validate_session(session1.session_id) is True
        assert manager.validate_session(session2.session_id) is True
        assert manager.validate_session(session3.session_id) is True
        
        # Delete one
        manager.delete_session(session2.session_id)
        
        # Others still valid
        assert manager.validate_session(session1.session_id) is True
        assert manager.validate_session(session2.session_id) is False
        assert manager.validate_session(session3.session_id) is True
