"""Personality configuration system for AI agents."""

from typing import Dict, List
from pydantic import BaseModel, Field
from backend.core.task import Task, TaskApproach
from backend.core.types import MemoryContext, MessageType


class CommunicationStyle(BaseModel):
    """Defines how an agent communicates with others."""

    formality: float = Field(ge=0.0, le=1.0, description="How formal is the communication")
    verbosity: float = Field(ge=0.0, le=1.0, description="How wordy is the communication")
    directness: float = Field(ge=0.0, le=1.0, description="How direct vs. indirect")
    emotional_expressiveness: float = Field(
        ge=0.0, le=1.0, description="How much emotion is shown"
    )
    signature_phrases: List[str] = Field(
        default_factory=list, description="Common phrases this agent uses"
    )


class PersonalityConfig(BaseModel):
    """Configuration defining an agent's personality."""

    # Core identity
    name: str = Field(description="Agent's name")
    age: int = Field(description="Agent's age")
    role: str = Field(description="Agent's role in the team")

    # Personality traits (0.0 to 1.0 scale)
    core_traits: Dict[str, float] = Field(
        description="Core personality traits that define behavior"
    )

    # Character background
    backstory: str = Field(description="Agent's background and history")
    communication_style: CommunicationStyle = Field(
        description="How the agent communicates"
    )

    # Unique characteristics
    quirks: List[str] = Field(
        default_factory=list, description="Unique behavioral quirks"
    )
    triggers: List[str] = Field(
        default_factory=list, description="Things that cause strong reactions"
    )

    def influence_approach(self, task: Task, context: MemoryContext) -> TaskApproach:
        """
        Apply personality traits to modify task execution approach.

        Args:
            task: The task to be executed
            context: Memory context from past experiences

        Returns:
            TaskApproach with personality-influenced modifications
        """
        approach = TaskApproach(base_strategy=task.default_strategy)

        # Apply perfectionism
        if self.core_traits.get("perfectionism", 0) > 0.7:
            approach.add_extra_validation_steps()

        # Apply traditionalism
        if self.core_traits.get("traditionalism", 0) > 0.6:
            approach.prefer_established_methods()

        # Check for triggers in task content
        for trigger in self.triggers:
            if trigger.lower() in task.content.lower():
                approach.add_emotional_reaction(trigger)

        # Apply grumpiness modifier
        if self.core_traits.get("grumpiness", 0) > 0.6:
            approach.modifications.append("grumpy_undertone")

        # Apply anxiety/pressure
        if self.core_traits.get("anxiety", 0) > 0.7:
            approach.modifications.append("overthinking_tendency")

        return approach

    def style_message(
        self, content: str, recipient: str, message_type: MessageType
    ) -> str:
        """
        Apply personality to message styling.

        Args:
            content: The message content
            recipient: Who the message is for
            message_type: Type of message being sent

        Returns:
            Styled message with personality applied
        """
        styled = content

        # Add signature phrases occasionally
        if self.communication_style.signature_phrases:
            # For now, just return the content - we'll add more sophisticated
            # styling when we have the actual LLM integration
            pass

        return styled
