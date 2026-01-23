"""
Recipe and CreationStory data models.

These models represent the final output of the AI Creative Team.
"""

from typing import List, Dict, Optional, Any
from datetime import datetime
from pathlib import Path
import json

from pydantic import BaseModel, Field

from backend.utils.logging import get_logger

logger = get_logger(__name__)


class Recipe(BaseModel):
    """
    A muffin tin recipe created by the AI Creative Team.
    
    This is the primary output product of the system.
    """
    
    # Identity
    recipe_id: str = Field(description="Unique identifier")
    title: str = Field(description="Recipe title")
    concept: str = Field(description="The original concept/idea")
    
    # Recipe Content
    description: str = Field(description="Editorial description by Marcus")
    ingredients: List[Dict[str, str]] = Field(
        description="List of ingredients with amounts and notes"
    )
    instructions: List[str] = Field(description="Step-by-step instructions")
    
    # Metadata
    servings: int = Field(default=12, description="Number of muffin portions")
    prep_time_minutes: int = Field(description="Preparation time")
    cook_time_minutes: int = Field(description="Cooking time")
    difficulty: str = Field(default="medium", description="Difficulty level")
    
    # Media
    photos: List[str] = Field(
        default_factory=list, description="Photo filenames from Julian"
    )
    featured_photo: Optional[str] = Field(default=None, description="Main photo")
    
    # Tags and categorization
    tags: List[str] = Field(default_factory=list, description="Recipe tags")
    category: str = Field(default="savory", description="Recipe category")
    
    # SEO and web
    slug: str = Field(description="URL-friendly slug")
    seo_description: Optional[str] = None
    
    # Creation tracking
    created_by: str = Field(default="AI Creative Team", description="Creator attribution")
    created_at: datetime = Field(default_factory=datetime.now)
    published_at: Optional[datetime] = None
    
    # Story reference
    story_id: Optional[str] = Field(
        default=None, description="Reference to CreationStory"
    )
    
    def save_to_file(self, output_dir: Path) -> Path:
        """
        Save recipe to JSON file.
        
        Args:
            output_dir: Directory to save recipe
            
        Returns:
            Path to saved file
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / f"{self.slug}.json"
        
        with open(filepath, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, default=str)
        
        logger.info(f"Saved recipe: {filepath}")
        return filepath
    
    @classmethod
    def load_from_file(cls, filepath: Path) -> "Recipe":
        """Load recipe from JSON file."""
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls(**data)


class AgentContribution(BaseModel):
    """Represents one agent's contribution to the recipe."""
    
    agent_name: str = Field(description="Agent's name (e.g., 'Margaret Chen')")
    agent_role: str = Field(description="Agent's role (e.g., 'baker')")
    contribution_type: str = Field(description="What they did")
    key_decisions: List[str] = Field(
        default_factory=list, description="Important decisions they made"
    )
    personality_moments: List[str] = Field(
        default_factory=list, description="Characteristic personality moments"
    )
    quotes: List[str] = Field(
        default_factory=list, description="Direct quotes from this agent"
    )


class CreationStory(BaseModel):
    """
    The behind-the-scenes story of how a recipe was created.
    
    This captures the AI agents' personalities, conflicts, and creative process.
    Entertainment value for users.
    """
    
    # Identity
    story_id: str = Field(description="Unique identifier")
    recipe_id: str = Field(description="Associated recipe ID")
    title: str = Field(description="Story title")
    
    # Summary
    summary: str = Field(
        description="Brief summary of the creation process (for featured display)"
    )
    full_story: str = Field(
        description="Complete narrative compiled from agent interactions"
    )
    
    # Agent contributions
    agent_contributions: List[AgentContribution] = Field(
        default_factory=list, description="What each agent did"
    )
    
    # Interesting moments
    key_conflicts: List[Dict[str, str]] = Field(
        default_factory=list, description="Interesting disagreements or tensions"
    )
    revision_history: List[Dict[str, Any]] = Field(
        default_factory=list, description="What got revised and why"
    )
    personality_highlights: List[str] = Field(
        default_factory=list, description="Memorable personality moments"
    )
    
    # Metadata
    total_messages: int = Field(default=0, description="Total agent messages")
    total_revisions: int = Field(default=0, description="How many revision rounds")
    time_to_complete_minutes: Optional[int] = None
    
    # Timeline
    started_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    def save_to_file(self, output_dir: Path) -> Path:
        """
        Save creation story to JSON file.
        
        Args:
            output_dir: Directory to save story
            
        Returns:
            Path to saved file
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath = output_dir / f"story_{self.story_id}.json"
        
        with open(filepath, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, default=str)
        
        logger.info(f"Saved creation story: {filepath}")
        return filepath
    
    @classmethod
    def load_from_file(cls, filepath: Path) -> "CreationStory":
        """Load creation story from JSON file."""
        with open(filepath, "r") as f:
            data = json.load(f)
        return cls(**data)
    
    def add_contribution(
        self,
        agent_name: str,
        agent_role: str,
        contribution_type: str,
        decisions: List[str] = None,
        personality_moments: List[str] = None,
        quotes: List[str] = None
    ) -> None:
        """Add an agent's contribution to the story."""
        contribution = AgentContribution(
            agent_name=agent_name,
            agent_role=agent_role,
            contribution_type=contribution_type,
            key_decisions=decisions or [],
            personality_moments=personality_moments or [],
            quotes=quotes or []
        )
        self.agent_contributions.append(contribution)
    
    def add_conflict(self, description: str, between: List[str], resolution: str) -> None:
        """Record an interesting conflict."""
        self.key_conflicts.append({
            "description": description,
            "between": between,
            "resolution": resolution
        })
    
    def add_revision(self, stage: str, reason: str, requested_by: str, outcome: str) -> None:
        """Record a revision request."""
        self.revision_history.append({
            "stage": stage,
            "reason": reason,
            "requested_by": requested_by,
            "outcome": outcome
        })
        self.total_revisions += 1
