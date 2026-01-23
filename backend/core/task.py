"""Task definitions for agent execution."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime
from uuid import uuid4


class Task(BaseModel):
    """Represents a task assigned to an agent."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: str = Field(description="Type of task (e.g., 'create_recipe', 'review', 'deploy')")
    content: str = Field(description="Task description and requirements")
    context: Dict[str, Any] = Field(
        default_factory=dict, description="Additional context for task execution"
    )
    default_strategy: str = Field(
        default="standard", description="Default approach for this task type"
    )
    assigned_to: Optional[str] = Field(default=None, description="Agent role assigned to task")
    created_at: datetime = Field(default_factory=datetime.now)


class TaskApproach(BaseModel):
    """Defines how an agent will approach a task based on personality."""

    base_strategy: str = Field(description="Starting strategy for the task")
    modifications: List[str] = Field(
        default_factory=list, description="Personality-driven modifications to approach"
    )
    emotional_reactions: List[str] = Field(
        default_factory=list, description="Emotional responses triggered by task content"
    )
    extra_steps: List[str] = Field(
        default_factory=list, description="Additional steps added due to personality traits"
    )

    def add_extra_validation_steps(self) -> None:
        """Add extra validation steps (e.g., for perfectionists)."""
        self.extra_steps.append("extra_quality_validation")
        self.modifications.append("increased_perfectionism")

    def prefer_established_methods(self) -> None:
        """Prefer traditional/established methods (e.g., for traditionalists)."""
        self.modifications.append("prefer_traditional_approach")

    def add_emotional_reaction(self, trigger: str) -> None:
        """Add an emotional reaction to a trigger."""
        self.emotional_reactions.append(f"triggered_by_{trigger}")


class TaskResult(BaseModel):
    """Result of task execution by an agent."""

    task_id: str = Field(description="ID of the completed task")
    success: bool = Field(description="Whether the task was completed successfully")
    output: Any = Field(description="Output produced by the task")
    insights: List[str] = Field(
        default_factory=list, description="Lessons learned or insights gained"
    )
    personality_notes: List[str] = Field(
        default_factory=list, description="Notes about how personality influenced execution"
    )
    completed_at: datetime = Field(default_factory=datetime.now)
