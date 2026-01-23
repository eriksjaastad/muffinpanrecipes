"""
Newsletter subscription manager.

Handles:
- Email validation and subscription
- Unsubscribe functionality
- Subscriber list management
- Integration with email service (Buttondown/Resend/etc)
"""

import os
import json
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

import httpx

from backend.utils.logging import get_logger

logger = get_logger(__name__)


class NewsletterManager:
    """
    Manages newsletter subscriptions.
    
    Supports multiple backend services:
    - Buttondown (recommended)
    - Resend
    - Custom SMTP
    - JSON file storage (development/fallback)
    """
    
    EMAIL_REGEX = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    def __init__(
        self,
        service: Optional[str] = None,
        api_key: Optional[str] = None,
        storage_path: Optional[Path] = None
    ):
        """
        Initialize newsletter manager.
        
        Args:
            service: Email service (buttondown, resend, or file)
            api_key: API key for the service
            storage_path: Path to JSON file for file-based storage
        """
        self.service = service or os.getenv("NEWSLETTER_SERVICE", "file")
        self.api_key = api_key or os.getenv("NEWSLETTER_API_KEY")
        self.storage_path = storage_path or Path("data/newsletter/subscribers.json")
        
        if self.service != "file" and not self.api_key:
            logger.warning(f"Newsletter service '{self.service}' configured but no API key provided")
        
        logger.info(f"NewsletterManager initialized with service: {self.service}")
    
    def validate_email(self, email: str) -> bool:
        """
        Validate email format.
        
        Args:
            email: Email address to validate
            
        Returns:
            True if valid
        """
        if not email:
            return False
        
        return bool(re.match(self.EMAIL_REGEX, email))
    
    async def subscribe(self, email: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Subscribe an email to the newsletter.
        
        Args:
            email: Email address
            metadata: Optional metadata (name, source, etc.)
            
        Returns:
            Dictionary with success status and message
        """
        # Validate email
        if not self.validate_email(email):
            return {
                "success": False,
                "error": "Invalid email format"
            }
        
        # Check if already subscribed
        if await self._check_if_subscribed(email):
            return {
                "success": False,
                "error": "Email already subscribed"
            }
        
        # Subscribe based on service
        if self.service == "buttondown":
            result = await self._subscribe_buttondown(email, metadata)
        elif self.service == "resend":
            result = await self._subscribe_resend(email, metadata)
        else:
            # File-based storage
            result = await self._subscribe_file(email, metadata)
        
        if result["success"]:
            logger.info(f"New newsletter subscriber: {email}")
        
        return result
    
    async def unsubscribe(self, email: str, token: Optional[str] = None) -> Dict[str, Any]:
        """
        Unsubscribe an email from the newsletter.
        
        Args:
            email: Email address
            token: Optional unsubscribe token for verification
            
        Returns:
            Dictionary with success status
        """
        if self.service == "buttondown":
            result = await self._unsubscribe_buttondown(email)
        elif self.service == "resend":
            result = await self._unsubscribe_resend(email)
        else:
            result = await self._unsubscribe_file(email)
        
        if result["success"]:
            logger.info(f"Newsletter unsubscribe: {email}")
        
        return result
    
    async def list_subscribers(self) -> List[Dict[str, Any]]:
        """
        List all subscribers.
        
        Returns:
            List of subscriber dictionaries
        """
        if self.service == "buttondown":
            return await self._list_buttondown()
        elif self.service == "resend":
            return await self._list_resend()
        else:
            return await self._list_file()
    
    # ==================== Service-specific implementations ====================
    
    async def _check_if_subscribed(self, email: str) -> bool:
        """Check if email is already subscribed."""
        subscribers = await self.list_subscribers()
        return any(s.get("email", "").lower() == email.lower() for s in subscribers)
    
    async def _subscribe_buttondown(self, email: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Subscribe via Buttondown API."""
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    "https://api.buttondown.email/v1/subscribers",
                    headers={
                        "Authorization": f"Token {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "email": email,
                        "metadata": metadata or {},
                        "tags": ["muffinpanrecipes"]
                    },
                    timeout=10.0
                )
                
                if response.status_code == 201:
                    return {"success": True, "message": "Subscribed successfully"}
                else:
                    logger.error(f"Buttondown subscribe failed: {response.status_code} - {response.text}")
                    return {"success": False, "error": "Subscription failed"}
                    
            except Exception as e:
                logger.error(f"Buttondown subscribe error: {e}")
                return {"success": False, "error": str(e)}
    
    async def _subscribe_resend(self, email: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Subscribe via Resend API."""
        # Placeholder - implement Resend API integration
        return {"success": False, "error": "Resend integration not yet implemented"}
    
    async def _subscribe_file(self, email: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Subscribe using file-based storage."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing subscribers
        subscribers = []
        if self.storage_path.exists():
            with open(self.storage_path, "r") as f:
                data = json.load(f)
                subscribers = data.get("subscribers", [])
        
        # Add new subscriber
        subscribers.append({
            "email": email,
            "metadata": metadata or {},
            "subscribed_at": datetime.now().isoformat(),
            "status": "active"
        })
        
        # Save
        with open(self.storage_path, "w") as f:
            json.dump({"subscribers": subscribers}, f, indent=2)
        
        return {"success": True, "message": "Subscribed successfully"}
    
    async def _unsubscribe_buttondown(self, email: str) -> Dict[str, Any]:
        """Unsubscribe via Buttondown API."""
        # Placeholder - implement Buttondown unsubscribe
        return {"success": False, "error": "Not implemented"}
    
    async def _unsubscribe_resend(self, email: str) -> Dict[str, Any]:
        """Unsubscribe via Resend API."""
        return {"success": False, "error": "Not implemented"}
    
    async def _unsubscribe_file(self, email: str) -> Dict[str, Any]:
        """Unsubscribe using file-based storage."""
        if not self.storage_path.exists():
            return {"success": False, "error": "No subscribers found"}
        
        with open(self.storage_path, "r") as f:
            data = json.load(f)
            subscribers = data.get("subscribers", [])
        
        # Mark as unsubscribed
        updated = False
        for sub in subscribers:
            if sub.get("email", "").lower() == email.lower():
                sub["status"] = "unsubscribed"
                sub["unsubscribed_at"] = datetime.now().isoformat()
                updated = True
        
        if updated:
            with open(self.storage_path, "w") as f:
                json.dump({"subscribers": subscribers}, f, indent=2)
            return {"success": True, "message": "Unsubscribed successfully"}
        else:
            return {"success": False, "error": "Email not found"}
    
    async def _list_buttondown(self) -> List[Dict[str, Any]]:
        """List subscribers from Buttondown."""
        # Placeholder - implement Buttondown list
        return []
    
    async def _list_resend(self) -> List[Dict[str, Any]]:
        """List subscribers from Resend."""
        return []
    
    async def _list_file(self) -> List[Dict[str, Any]]:
        """List subscribers from file storage."""
        if not self.storage_path.exists():
            return []
        
        with open(self.storage_path, "r") as f:
            data = json.load(f)
            return [
                s for s in data.get("subscribers", [])
                if s.get("status") == "active"
            ]
