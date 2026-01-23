"""Agent Profile storage system for persistent agent state."""

from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime
import json

from pydantic import BaseModel, Field

from backend.core.personality import PersonalityConfig
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class AgentProfile(BaseModel):
    """
    Persistent profile for an AI agent.
    
    Stores personality configuration, current state, and metadata.
    """
    
    # Identity
    agent_id: str = Field(description="Unique agent identifier")
    role: str = Field(description="Agent role (baker, creative_director, etc)")
    name: str = Field(description="Agent's name")
    
    # Personality configuration
    personality_data: Dict[str, Any] = Field(
        description="Serialized personality configuration"
    )
    
    # Current state
    current_emotional_state: float = Field(
        default=0.0, description="Current emotional baseline"
    )
    total_tasks_completed: int = Field(default=0)
    total_messages_sent: int = Field(default=0)
    
    # Relationships (agent_role -> relationship_score)
    relationship_scores: Dict[str, float] = Field(
        default_factory=dict, description="Current relationship with other agents"
    )
    
    # Statistics
    favorite_tasks: Dict[str, int] = Field(
        default_factory=dict, description="Task types completed count"
    )
    success_rate: float = Field(default=1.0, description="Task success rate")
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    last_active: datetime = Field(default_factory=datetime.now)
    version: str = Field(default="1.0.0", description="Profile version")
    
    def update_from_memory(self, memory: Any) -> None:
        """
        Update profile from agent memory.
        
        Args:
            memory: AgentMemory instance
        """
        # Update emotional state from recent experiences
        if hasattr(memory, 'emotional_responses') and memory.emotional_responses:
            recent = memory.emotional_responses[-10:]
            avg_emotion = sum(exp.emotional_impact for exp in recent) / len(recent)
            self.current_emotional_state = avg_emotion
        
        # Update relationship scores
        if hasattr(memory, 'relationship_history'):
            for agent_role, events in memory.relationship_history.items():
                if events:
                    # Weighted  average with recent events having more weight
                    weights = [0.5 ** (len(events) - i - 1) for i in range(len(events))]
                    weighted_sum = sum(
                        e.emotional_valence * w 
                        for e, w in zip(events, weights)
                    )
                    total_weight = sum(weights)
                    self.relationship_scores[agent_role] = weighted_sum / total_weight if total_weight > 0 else 0.0
        
        self.last_active = datetime.now()
    
    def save_to_file(self, output_dir: Path) -> Path:
        """
        Save agent profile to JSON file.
        
        Args:
            output_dir: Directory to save profile
            
        Returns:
            Path to saved file
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / f"profile_{self.agent_id}.json"
        
        with open(filepath, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, default=str)
        
        logger.debug(f"Saved agent profile: {filepath}")
        return filepath
    
    @classmethod
    def load_from_file(cls, filepath: Path) -> "AgentProfile":
        """Load agent profile from JSON file."""
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls(**data)
    
    @classmethod
    def create_from_agent(cls, agent: Any, agent_id: str) -> "AgentProfile":
        """
        Create profile from an existing agent.
        
        Args:
            agent: Agent instance
            agent_id: Unique identifier for this agent
            
        Returns:
            AgentProfile instance
        """
        profile = cls(
            agent_id=agent_id,
            role=agent.role,
            name=agent.personality.name,
            personality_data=agent.personality.model_dump(mode="json")
        )
        
        # Update from memory if available
        if agent.memory:
            profile.update_from_memory(agent.memory)
        
        return profile
    
    def to_personality_config(self) -> PersonalityConfig:
        """
        Reconstruct PersonalityConfig from stored data.
        
        Returns:
            PersonalityConfig instance
        """
        return PersonalityConfig(**self.personality_data)
