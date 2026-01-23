"""
Session management for authenticated admin users.

Provides secure session handling with:
- Session creation and validation
- 24-hour session expiry
- Secure session storage
"""

import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import json
from pathlib import Path

from backend.utils.logging import get_logger

logger = get_logger(__name__)


class Session:
    """Represents an authenticated user session."""
    
    def __init__(
        self,
        session_id: str,
        email: str,
        user_info: Dict[str, Any],
        created_at: datetime,
        expires_at: datetime
    ):
        self.session_id = session_id
        self.email = email
        self.user_info = user_info
        self.created_at = created_at
        self.expires_at = expires_at
    
    def is_valid(self) -> bool:
        """Check if session is still valid (not expired)."""
        return datetime.now() < self.expires_at
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize session to dictionary."""
        return {
            "session_id": self.session_id,
            "email": self.email,
            "user_info": self.user_info,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat()
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        """Deserialize session from dictionary."""
        return cls(
            session_id=data["session_id"],
            email=data["email"],
            user_info=data["user_info"],
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"])
        )


class SessionManager:
    """
    Manages user sessions with in-memory and optional file-based persistence.
    
    Sessions expire after 24 hours by default.
    """
    
    def __init__(
        self,
        session_duration_hours: int = 24,
        persist_to_file: bool = False,
        storage_path: Optional[Path] = None
    ):
        """
        Initialize session manager.
        
        Args:
            session_duration_hours: How long sessions are valid
            persist_to_file: Whether to persist sessions to disk
            storage_path: Path to session storage file
        """
        self.session_duration_hours = session_duration_hours
        self.persist_to_file = persist_to_file
        self.storage_path = storage_path or Path("data/sessions.json")
        
        # In-memory session storage: {session_id: Session}
        self.sessions: Dict[str, Session] = {}
        
        # Load persisted sessions if enabled
        if self.persist_to_file:
            self._load_sessions()
        
        logger.info(f"SessionManager initialized (duration: {session_duration_hours}h)")
    
    def create_session(self, email: str, user_info: Dict[str, Any]) -> Session:
        """
        Create a new session for a user.
        
        Args:
            email: User's email address
            user_info: User information from OAuth
            
        Returns:
            New Session object
        """
        session_id = secrets.token_urlsafe(32)
        now = datetime.now()
        expires_at = now + timedelta(hours=self.session_duration_hours)
        
        session = Session(
            session_id=session_id,
            email=email,
            user_info=user_info,
            created_at=now,
            expires_at=expires_at
        )
        
        self.sessions[session_id] = session
        
        if self.persist_to_file:
            self._save_sessions()
        
        logger.info(f"Created session for {email} (expires: {expires_at})")
        return session
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """
        Get a session by ID.
        
        Args:
            session_id: Session identifier
            
        Returns:
            Session if found and valid, None otherwise
        """
        session = self.sessions.get(session_id)
        
        if not session:
            return None
        
        if not session.is_valid():
            # Session expired, remove it
            self.delete_session(session_id)
            return None
        
        return session
    
    def validate_session(self, session_id: str) -> bool:
        """
        Check if a session is valid.
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if session exists and is valid
        """
        session = self.get_session(session_id)
        return session is not None
    
    def delete_session(self, session_id: str) -> bool:
        """
        Delete a session (logout).
        
        Args:
            session_id: Session identifier
            
        Returns:
            True if session was deleted
        """
        if session_id in self.sessions:
            session = self.sessions[session_id]
            del self.sessions[session_id]
            
            if self.persist_to_file:
                self._save_sessions()
            
            logger.info(f"Deleted session for {session.email}")
            return True
        
        return False
    
    def cleanup_expired_sessions(self) -> int:
        """
        Remove all expired sessions.
        
        Returns:
            Number of sessions deleted
        """
        now = datetime.now()
        expired_ids = [
            sid for sid, session in self.sessions.items()
            if session.expires_at < now
        ]
        
        for sid in expired_ids:
            del self.sessions[sid]
        
        if expired_ids and self.persist_to_file:
            self._save_sessions()
        
        if expired_ids:
            logger.info(f"Cleaned up {len(expired_ids)} expired sessions")
        
        return len(expired_ids)
    
    def _save_sessions(self) -> None:
        """Save sessions to file."""
        if not self.persist_to_file:
            return
        
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            
            sessions_data = {
                sid: session.to_dict()
                for sid, session in self.sessions.items()
            }
            
            with open(self.storage_path, "w") as f:
                json.dump(sessions_data, f, indent=2)
            
            logger.debug(f"Saved {len(sessions_data)} sessions to {self.storage_path}")
            
        except Exception as e:
            logger.error(f"Failed to save sessions: {e}")
    
    def _load_sessions(self) -> None:
        """Load sessions from file."""
        if not self.persist_to_file or not self.storage_path.exists():
            return
        
        try:
            with open(self.storage_path, "r") as f:
                sessions_data = json.load(f)
            
            self.sessions = {
                sid: Session.from_dict(data)
                for sid, data in sessions_data.items()
            }
            
            # Clean up expired sessions on load
            self.cleanup_expired_sessions()
            
            logger.info(f"Loaded {len(self.sessions)} sessions from {self.storage_path}")
            
        except Exception as e:
            logger.error(f"Failed to load sessions: {e}")
            self.sessions = {}
