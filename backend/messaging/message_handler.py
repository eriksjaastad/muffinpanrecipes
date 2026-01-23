"""Message handler for inter-agent communication."""

from typing import Dict, Any
from uuid import uuid4
from datetime import datetime

from backend.core.agent import Message
from backend.core.types import MessageType
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class MessageHandler:
    """
    Handles message sending and routing for an individual agent.

    Each agent gets its own handler that connects to the global message system.
    """

    def __init__(self, agent_role: str, message_system: Any):
        """
        Initialize message handler.

        Args:
            agent_role: The role of the agent this handler belongs to
            message_system: Reference to the global MessageSystem
        """
        self.agent_role = agent_role
        self.message_system = message_system

    def send(
        self,
        sender: str,
        recipient: str,
        content: str,
        message_type: MessageType,
        context: Dict[str, Any] = None,
    ) -> None:
        """
        Send a message to another agent.

        Args:
            sender: Role of the sending agent
            recipient: Role of the receiving agent
            content: Message content
            message_type: Type of message
            context: Additional context
        """
        message = Message(
            id=str(uuid4()),
            sender=sender,
            recipient=recipient,
            content=content,
            message_type=message_type,
            context=context or {},
            timestamp=datetime.now().isoformat(),
        )

        logger.info(f"Message: {sender} -> {recipient} [{message_type.value}]")
        logger.debug(f"Content: {content[:100]}...")

        self.message_system.send_message(message)
