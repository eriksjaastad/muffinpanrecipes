"""Agent memory system for personality-focused storage and development."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field

from backend.core.task import Task, TaskResult
from backend.core.types import EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class Experience(BaseModel):
    """Represents a single experience in agent memory."""

    timestamp: datetime = Field(default_factory=datetime.now)
    task_type: str = Field(description="Type of task that created this experience")
    outcome: bool = Field(description="Whether the task was successful")
    emotional_impact: float = Field(
        ge=-1.0, le=1.0, description="Intensity of emotional response"
    )
    personality_factors: Dict[str, float] = Field(
        default_factory=dict, description="Personality traits that influenced this experience"
    )
    lessons_learned: List[str] = Field(
        default_factory=list, description="Insights gained from this experience"
    )
    description: str = Field(default="", description="Description of what happened")


class RelationshipEvent(BaseModel):
    """Represents an interaction with another agent."""

    timestamp: datetime = Field(default_factory=datetime.now)
    other_agent: str = Field(description="The other agent in this interaction")
    interaction_type: str = Field(description="Type of interaction (message, feedback, etc)")
    emotional_valence: float = Field(
        ge=-1.0, le=1.0, description="How positive/negative the interaction felt"
    )
    description: str = Field(description="What happened in the interaction")


class AgentMemory:
    """
    Agent memory system focused on personality development.

    Stores emotional experiences, formative moments, creative preferences,
    and relationship dynamics rather than operational data.
    """

    def __init__(self, agent_role: str, storage_path: Optional[Path] = None):
        """
        Initialize agent memory.

        Args:
            agent_role: The role of the agent (e.g., 'baker', 'creative_director')
            storage_path: Optional path to store memory files (defaults to data/agent_memories/)
        """
        self.agent_role = agent_role
        self.storage_path = storage_path or Path("data/agent_memories")
        self.storage_path.mkdir(parents=True, exist_ok=True)

        # Memory storage
        self.formative_experiences: List[Experience] = []
        self.emotional_responses: List[Experience] = []
        self.creative_preferences: Dict[str, Any] = {}
        self.relationship_history: Dict[str, List[RelationshipEvent]] = {}
        self.character_growth_moments: List[Dict[str, Any]] = []

        # Load existing memory if available
        self._load_memory()

    def record_experience(
        self, task: Task, result: TaskResult, emotion: EmotionalResponse
    ) -> None:
        """
        Record an experience with emotional context.

        High-impact experiences (|intensity| > 0.7) are stored as formative experiences.
        Others are stored as general emotional responses.

        Args:
            task: The task that was executed
            result: The result of the task
            emotion: The emotional response to the outcome
        """
        experience = Experience(
            task_type=task.type,
            outcome=result.success,
            emotional_impact=emotion.intensity,
            personality_factors=emotion.personality_factors,
            lessons_learned=result.insights,
            description=emotion.description,
        )

        if abs(emotion.intensity) > 0.7:
            # High emotional impact - formative experience
            self.formative_experiences.append(experience)
            logger.info(
                f"{self.agent_role}: Recorded formative experience - {emotion.description}"
            )
        else:
            # Regular emotional response
            self.emotional_responses.append(experience)

        # Save to disk
        self._save_memory()

    def record_interaction(
        self, other_agent: str, interaction_type: str, valence: float, description: str
    ) -> None:
        """
        Record an interaction with another agent.

        Args:
            other_agent: The role of the other agent
            interaction_type: Type of interaction (e.g., 'message', 'feedback')
            valence: Emotional valence of interaction (-1.0 to 1.0)
            description: What happened
        """
        if other_agent not in self.relationship_history:
            self.relationship_history[other_agent] = []

        event = RelationshipEvent(
            other_agent=other_agent,
            interaction_type=interaction_type,
            emotional_valence=valence,
            description=description,
        )

        self.relationship_history[other_agent].append(event)
        self._save_memory()

    def get_relevant_context(self, task: Task) -> MemoryContext:
        """
        Retrieve memory context relevant to a task.

        Args:
            task: The task being executed

        Returns:
            MemoryContext with relevant experiences and emotional state
        """
        # Find experiences related to this task type
        relevant_experiences = [
            {
                "description": exp.description,
                "outcome": exp.outcome,
                "lessons": exp.lessons_learned,
            }
            for exp in self.formative_experiences
            if exp.task_type == task.type
        ]

        # Calculate current emotional state (recent average)
        recent_emotions = [exp.emotional_impact for exp in self.emotional_responses[-10:]]
        emotional_state = sum(recent_emotions) / len(recent_emotions) if recent_emotions else 0.0

        # Get relationship factors if task involves other agents
        relationship_factors = {}
        if "assigned_by" in task.context:
            assigned_by = task.context["assigned_by"]
            if assigned_by in self.relationship_history:
                events = self.relationship_history[assigned_by]
                avg_valence = sum(e.emotional_valence for e in events) / len(events)
                relationship_factors[assigned_by] = avg_valence

        return MemoryContext(
            relevant_experiences=relevant_experiences,
            emotional_state=emotional_state,
            relationship_factors=relationship_factors,
        )

    def get_relationship_score(self, other_agent: str) -> float:
        """
        Get the current relationship score with another agent.

        Args:
            other_agent: The role of the other agent

        Returns:
            Relationship score from -1.0 to 1.0
        """
        if other_agent not in self.relationship_history:
            return 0.0

        events = self.relationship_history[other_agent]
        if not events:
            return 0.0

        # Weight more recent interactions higher
        weights = [0.5 ** (len(events) - i - 1) for i in range(len(events))]
        weighted_sum = sum(e.emotional_valence * w for e, w in zip(events, weights))
        total_weight = sum(weights)

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def _load_memory(self) -> None:
        """Load memory from disk if it exists."""
        memory_file = self.storage_path / f"{self.agent_role}_memory.json"

        if not memory_file.exists():
            logger.info(f"{self.agent_role}: No existing memory found, starting fresh")
            return

        try:
            with open(memory_file, "r") as f:
                data = json.load(f)

            self.formative_experiences = [
                Experience(**exp) for exp in data.get("formative_experiences", [])
            ]
            self.emotional_responses = [
                Experience(**exp) for exp in data.get("emotional_responses", [])
            ]
            self.creative_preferences = data.get("creative_preferences", {})
            self.character_growth_moments = data.get("character_growth_moments", [])

            # Load relationship history
            rel_data = data.get("relationship_history", {})
            for agent, events in rel_data.items():
                self.relationship_history[agent] = [
                    RelationshipEvent(**event) for event in events
                ]

            logger.info(f"{self.agent_role}: Loaded memory from {memory_file}")

        except Exception as e:
            logger.error(f"{self.agent_role}: Error loading memory: {e}")

    def _save_memory(self) -> None:
        """Save memory to disk."""
        memory_file = self.storage_path / f"{self.agent_role}_memory.json"

        try:
            data = {
                "formative_experiences": [exp.model_dump(mode="json") for exp in self.formative_experiences],
                "emotional_responses": [exp.model_dump(mode="json") for exp in self.emotional_responses],
                "creative_preferences": self.creative_preferences,
                "relationship_history": {
                    agent: [event.model_dump(mode="json") for event in events]
                    for agent, events in self.relationship_history.items()
                },
                "character_growth_moments": self.character_growth_moments,
            }

            with open(memory_file, "w") as f:
                json.dump(data, f, indent=2, default=str)

            logger.debug(f"{self.agent_role}: Saved memory to {memory_file}")

        except Exception as e:
            logger.error(f"{self.agent_role}: Error saving memory: {e}")
