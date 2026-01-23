"""
Global Message System for agent-to-agent communication.

Handles message queuing, routing, history logging, and delivery.
"""

import json
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any
from datetime import datetime
from collections import defaultdict, deque

from backend.core.agent import Message
from backend.core.types import MessageType
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class MessageSystem:
    """
    Central message routing and logging system for all agents.
    
    Responsibilities:
    - Queue messages for delivery
    - Route messages to correct recipients
    - Log all communication for the creative story
    - Track message history for analysis
    """

    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize the message system.
        
        Args:
            storage_path: Path to store message history logs
        """
        self.storage_path = storage_path or Path("data/messages")
        self.storage_path.mkdir(parents=True, exist_ok=True)
        
        # Message queues per agent
        self.message_queues: Dict[str, deque] = defaultdict(deque)
        
        # Message history for the creative story
        self.message_history: List[Message] = []
        
        # Agent handlers (registered when agents connect)
        self.agent_handlers: Dict[str, Callable] = {}
        
        # Statistics
        self.message_count = 0
        
        # Load any existing message history
        self._load_history()
        
        logger.info("MessageSystem initialized")

    def register_agent(self, agent_role: str, receive_callback: Optional[Callable] = None) -> None:
        """
        Register an agent with the message system.
        
        Args:
            agent_role: The role of the agent (e.g., 'baker', 'creative_director')
            receive_callback: Optional callback function to handle incoming messages
        """
        if receive_callback:
            self.agent_handlers[agent_role] = receive_callback
        
        logger.debug(f"Registered agent: {agent_role}")

    def send_message(self, message: Message) -> None:
        """
        Send a message from one agent to another.
        
        Args:
            message: The message to send
        """
        # Validate recipient exists (at least has a queue)
        if message.recipient not in self.message_queues:
            logger.warning(f"Recipient {message.recipient} not yet registered, creating queue")
        
        # Add to recipient's queue
        self.message_queues[message.recipient].append(message)
        
        # Log to history
        self.message_history.append(message)
        self.message_count += 1
        
        # Save history periodically (every 10 messages)
        if self.message_count % 10 == 0:
            self._save_history()
        
        logger.info(
            f"Message queued: {message.sender} → {message.recipient} "
            f"[{message.message_type.value}] (ID: {message.id[:8]})"
        )
        
        # Deliver immediately if agent has a handler
        if message.recipient in self.agent_handlers:
            self._deliver_message(message)

    def _deliver_message(self, message: Message) -> None:
        """
        Deliver a message to an agent's handler.
        
        Args:
            message: The message to deliver
        """
        handler = self.agent_handlers.get(message.recipient)
        if handler:
            try:
                handler(message.sender, message)
                logger.debug(f"Delivered message {message.id[:8]} to {message.recipient}")
            except Exception as e:
                logger.error(f"Error delivering message to {message.recipient}: {e}")

    def get_messages_for(self, agent_role: str, clear: bool = True) -> List[Message]:
        """
        Get all queued messages for an agent.
        
        Args:
            agent_role: The role of the agent
            clear: Whether to clear the queue after retrieving
            
        Returns:
            List of messages for this agent
        """
        if clear:
            messages = list(self.message_queues[agent_role])
            self.message_queues[agent_role].clear()
            return messages
        else:
            return list(self.message_queues[agent_role])

    def get_conversation(self, agent1: str, agent2: str) -> List[Message]:
        """
        Get all messages between two agents.
        
        Args:
            agent1: First agent role
            agent2: Second agent role
            
        Returns:
            List of messages between the two agents
        """
        return [
            msg for msg in self.message_history
            if (msg.sender == agent1 and msg.recipient == agent2) or
               (msg.sender == agent2 and msg.recipient == agent1)
        ]

    def get_messages_by_type(self, message_type: MessageType) -> List[Message]:
        """
        Get all messages of a specific type.
        
        Args:
            message_type: The type of message to filter by
            
        Returns:
            List of messages of this type
        """
        return [msg for msg in self.message_history if msg.message_type == message_type]

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about message activity.
        
        Returns:
            Dictionary with message statistics
        """
        message_types = defaultdict(int)
        sender_counts = defaultdict(int)
        recipient_counts = defaultdict(int)
        
        for msg in self.message_history:
            message_types[msg.message_type.value] += 1
            sender_counts[msg.sender] += 1
            recipient_counts[msg.recipient] += 1
        
        return {
            "total_messages": len(self.message_history),
            "by_type": dict(message_types),
            "by_sender": dict(sender_counts),
            "by_recipient": dict(recipient_counts),
            "queued_messages": {
                agent: len(queue) for agent, queue in self.message_queues.items()
            }
        }

    def _save_history(self) -> None:
        """Save message history to disk."""
        history_file = self.storage_path / "message_history.json"
        
        try:
            data = {
                "messages": [
                    {
                        "id": msg.id,
                        "sender": msg.sender,
                        "recipient": msg.recipient,
                        "content": msg.content,
                        "message_type": msg.message_type.value,
                        "context": msg.context,
                        "timestamp": msg.timestamp,
                    }
                    for msg in self.message_history
                ],
                "statistics": self.get_statistics()
            }
            
            with open(history_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
            
            logger.debug(f"Saved message history: {len(self.message_history)} messages")
            
        except Exception as e:
            logger.error(f"Error saving message history: {e}")

    def _load_history(self) -> None:
        """Load message history from disk if it exists."""
        history_file = self.storage_path / "message_history.json"
        
        if not history_file.exists():
            logger.debug("No existing message history found")
            return
        
        try:
            with open(history_file, "r") as f:
                data = json.load(f)
            
            for msg_data in data.get("messages", []):
                message = Message(
                    id=msg_data["id"],
                    sender=msg_data["sender"],
                    recipient=msg_data["recipient"],
                    content=msg_data["content"],
                    message_type=MessageType(msg_data["message_type"]),
                    context=msg_data.get("context", {}),
                    timestamp=msg_data["timestamp"],
                )
                self.message_history.append(message)
            
            self.message_count = len(self.message_history)
            logger.info(f"Loaded message history: {self.message_count} messages")
            
        except Exception as e:
            logger.error(f"Error loading message history: {e}")

    def clear_history(self) -> None:
        """Clear all message history (use with caution!)."""
        self.message_history.clear()
        self.message_count = 0
        self.message_queues.clear()
        logger.warning("Message history cleared")
        self._save_history()
