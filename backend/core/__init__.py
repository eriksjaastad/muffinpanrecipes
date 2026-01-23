"""Core interfaces and base classes for the AI Creative Team system."""

from backend.core.agent import Agent
from backend.core.personality import PersonalityConfig, CommunicationStyle
from backend.core.task import Task, TaskResult, TaskApproach

__all__ = [
    "Agent",
    "PersonalityConfig",
    "CommunicationStyle",
    "Task",
    "TaskResult",
    "TaskApproach",
]
