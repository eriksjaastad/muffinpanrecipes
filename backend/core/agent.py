"""Base Agent class for the AI Creative Team system."""

from typing import Optional, Dict, Any
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field

from backend.core.personality import PersonalityConfig
from backend.core.task import Task, TaskResult
from backend.core.types import MessageType, EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class Message(BaseModel):
    """Represents a message between agents."""

    sender: str = Field(description="Role of the sending agent")
    recipient: str = Field(description="Role of the receiving agent")
    content: str = Field(description="Message content")
    message_type: MessageType = Field(description="Type of message")
    context: Dict[str, Any] = Field(default_factory=dict, description="Additional context")
    timestamp: str = Field(description="When the message was sent")
    id: str = Field(description="Unique message identifier")


class Agent(ABC):
    """
    Base class for all AI agents in the creative team.

    Each agent has a distinct personality that influences decision-making,
    communication style, and creative output.
    """

    def __init__(self, role: str, personality_config: PersonalityConfig):
        """
        Initialize an agent with a role and personality.

        Args:
            role: The agent's role in the team (e.g., 'baker', 'creative_director')
            personality_config: Configuration defining the agent's personality
        """
        self.role = role
        self.personality = personality_config

        # These will be initialized when the full system is set up
        self.memory: Optional[Any] = None
        self.message_handler: Optional[Any] = None

        logger.info(f"Initialized {self.role} agent: {self.personality.name}")

    def set_memory(self, memory: Any) -> None:
        """Set the agent's memory system."""
        self.memory = memory
        logger.debug(f"{self.role}: Memory system connected")

    def set_message_handler(self, handler: Any) -> None:
        """Set the agent's message handler."""
        self.message_handler = handler
        logger.debug(f"{self.role}: Message handler connected")

    def process_task(self, task: Task) -> TaskResult:
        """
        Process a task with personality-driven behavior.

        This default implementation provides the framework for task processing.
        Subclasses should override execute_task_with_personality() to provide
        specific task execution logic.

        Args:
            task: The task to process

        Returns:
            TaskResult containing the output and metadata
        """
        logger.info(f"{self.role}: Processing task '{task.type}'")

        # Step 1: Consult memory for relevant context
        context = MemoryContext()
        if self.memory is not None:
            context = self.memory.get_relevant_context(task)
            logger.debug(
                f"{self.role}: Retrieved memory context (emotional state: {context.emotional_state:.2f})"
            )

        # Step 2: Apply personality traits to decision-making
        approach = self.personality.influence_approach(task, context)
        logger.debug(
            f"{self.role}: Task approach modified by personality: {approach.modifications}"
        )

        # Step 3: Execute the task with personality influence
        result = self.execute_task_with_personality(task, approach, context)

        # Step 4: Generate emotional response
        emotion = self.get_emotional_response(task, result)
        logger.debug(f"{self.role}: Emotional response - {emotion.description} (intensity: {emotion.intensity:.2f})")

        # Step 5: Record the experience in memory
        if self.memory is not None:
            self.memory.record_experience(task, result, emotion)

        return result

    @abstractmethod
    def execute_task_with_personality(
        self, task: Task, approach: Any, context: MemoryContext
    ) -> TaskResult:
        """
        Execute a specific task with personality-driven modifications.

        Subclasses must implement this to provide task-specific logic.

        Args:
            task: The task to execute
            approach: The personality-influenced approach
            context: Memory context for this task

        Returns:
            TaskResult with the output
        """
        pass

    @abstractmethod
    def get_emotional_response(self, task: Task, result: TaskResult) -> EmotionalResponse:
        """
        Generate an emotional response to a task outcome.

        Args:
            task: The task that was executed
            result: The result of task execution

        Returns:
            EmotionalResponse describing the agent's reaction
        """
        pass

    def send_message(
        self, recipient: str, content: str, message_type: MessageType, context: Dict[str, Any] = None
    ) -> None:
        """
        Send a message to another agent.

        Args:
            recipient: The role of the recipient agent
            content: The message content
            message_type: The type of message being sent
            context: Optional additional context
        """
        if self.message_handler is None:
            raise RuntimeError(f"Message handler not set for {self.role}")

        # Style the message with personality
        styled_content = self.personality.style_message(content, recipient, message_type)

        # Send through the message handler
        self.message_handler.send(
            sender=self.role,
            recipient=recipient,
            content=styled_content,
            message_type=message_type,
            context=context or {},
        )

        # Record the interaction in memory
        if self.memory is not None:
            # Determine emotional valence based on message type
            valence_map = {
                MessageType.APPROVAL_NOTIFICATION: 0.5,
                MessageType.CREATIVE_SUGGESTION: 0.3,
                MessageType.TASK_ASSIGNMENT: 0.0,
                MessageType.FEEDBACK_REQUEST: 0.1,
                MessageType.REVISION_REQUEST: -0.3,
            }
            valence = valence_map.get(message_type, 0.0)

            self.memory.record_interaction(
                other_agent=recipient,
                interaction_type=message_type.value,
                valence=valence,
                description=f"Sent {message_type.value}: {content[:100]}",
            )

    def receive_message(self, sender: str, message: Message) -> Optional[Message]:
        """
        Process an incoming message and optionally generate a response.

        Args:
            sender: The role of the sending agent
            message: The message received

        Returns:
            Optional response message
        """
        logger.info(f"{self.role}: Received {message.message_type.value} from {sender}")

        # Record the interaction in memory
        if self.memory is not None:
            valence_map = {
                MessageType.APPROVAL_NOTIFICATION: 0.6,
                MessageType.CREATIVE_SUGGESTION: 0.2,
                MessageType.TASK_ASSIGNMENT: -0.1,
                MessageType.FEEDBACK_REQUEST: 0.0,
                MessageType.REVISION_REQUEST: -0.4,
            }
            valence = valence_map.get(message.message_type, 0.0)

            self.memory.record_interaction(
                other_agent=sender,
                interaction_type=message.message_type.value,
                valence=valence,
                description=f"Received {message.message_type.value}: {message.content[:100]}",
            )

        # Subclasses can override to provide automatic responses
        return None
