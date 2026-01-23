"""Common type definitions for the AI Creative Team system."""

from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class MessageType(Enum):
    """Types of messages that can be sent between agents."""

    TASK_ASSIGNMENT = "task_assignment"
    FEEDBACK_REQUEST = "feedback_request"
    REVISION_REQUEST = "revision_request"
    APPROVAL_NOTIFICATION = "approval_notification"
    CREATIVE_SUGGESTION = "creative_suggestion"
    GOSSIP = "gossip"  # Future feature


class EmotionalResponse(BaseModel):
    """Represents an agent's emotional reaction to an experience."""

    intensity: float = Field(ge=-1.0, le=1.0, description="Emotional impact from -1 to 1")
    personality_factors: Dict[str, float] = Field(
        default_factory=dict, description="Personality traits that influenced this response"
    )
    description: str = Field(description="Textual description of the emotional response")


class MemoryContext(BaseModel):
    """Context retrieved from agent memory for task execution."""

    relevant_experiences: List[Dict[str, Any]] = Field(
        default_factory=list, description="Past experiences that might influence current task"
    )
    emotional_state: float = Field(
        default=0.0, ge=-1.0, le=1.0, description="Current emotional state"
    )
    relationship_factors: Dict[str, float] = Field(
        default_factory=dict, description="Relationships with other agents"
    )
