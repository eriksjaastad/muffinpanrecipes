"""
Recipe Pipeline Controller

Orchestrates the multi-agent recipe creation process through defined stages.
"""

from enum import Enum
from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field

from backend.core.task import Task
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class PipelineStage(str, Enum):
    """Stages in the recipe creation pipeline."""
    IDEATION = "ideation"
    RECIPE_DEVELOPMENT = "recipe_development"
    PHOTOGRAPHY = "photography"
    COPYWRITING = "copywriting"
    CREATIVE_REVIEW = "creative_review"
    revisions = "revisions"
    FINAL_APPROVAL = "final_approval"
    DEPLOYMENT = "deployment"
    COMPLETE = "complete"


class RecipeContext(BaseModel):
    """Context for a recipe as it moves through the pipeline."""
    
    recipe_id: str = Field(description="Unique identifier for this recipe")
    concept: str = Field(description="The recipe concept or idea")
    current_stage: PipelineStage = Field(default=PipelineStage.IDEATION)
    
    # Work products from each stage
    recipe_data: Dict[str, Any] = Field(default_factory=dict)
    photos: List[str] = Field(default_factory=list)
    recipe_copy: Dict[str, str] = Field(default_factory=dict)  # Renamed from 'copy' to avoid shadowing
    
    # Review and approval tracking
    reviews: List[Dict[str, Any]] = Field(default_factory=list)
    revision_requests: List[Dict[str, Any]] = Field(default_factory=list)
    revision_count: int = Field(default=0)
    
    # Creative story (agent interactions)
    messages: List[str] = Field(default_factory=list)
    agent_contributions: Dict[str, List[str]] = Field(default_factory=dict)
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    
    def add_review(self, reviewer: str, approved: bool, feedback: str) -> None:
        """Add a review from an agent."""
        self.reviews.append({
            "reviewer": reviewer,
            "approved": approved,
            "feedback": feedback,
            "timestamp": datetime.now().isoformat()
        })
    
    def add_revision_request(self, stage: str, reason: str, requested_by: str) -> None:
        """Add a revision request."""
        self.revision_requests.append({
            "stage": stage,
            "reason": reason,
            "requested_by": requested_by,
            "timestamp": datetime.now().isoformat()
        })
        self.revision_count += 1
    
    def record_contribution(self, agent: str, contribution: str) -> None:
        """Record an agent's contribution."""
        if agent not in self.agent_contributions:
            self.agent_contributions[agent] = []
        self.agent_contributions[agent].append(contribution)


class RecipePipeline:
    """
    Orchestrates the recipe creation process through all stages.
    
    The pipeline moves a recipe concept through:
    1. Ideation
    2. Recipe Development (Baker)
    3. Photography (Art Director) 
    4. Copywriting (Editorial Copywriter)
    5. Creative Review (Creative Director)
    6. Revisions (if needed)
    7. Final Approval
    8. Deployment (Site Architect)
    9. Complete
    """
    
    # Define stage transitions
    STAGE_FLOW = {
        PipelineStage.IDEATION: PipelineStage.RECIPE_DEVELOPMENT,
        PipelineStage.RECIPE_DEVELOPMENT: PipelineStage.PHOTOGRAPHY,
        PipelineStage.PHOTOGRAPHY: PipelineStage.COPYWRITING,
        PipelineStage.COPYWRITING: PipelineStage.CREATIVE_REVIEW,
        PipelineStage.CREATIVE_REVIEW: PipelineStage.FINAL_APPROVAL,  # or REVISIONS
        PipelineStage.revisions: PipelineStage.CREATIVE_REVIEW,  # Back to review
        PipelineStage.FINAL_APPROVAL: PipelineStage.DEPLOYMENT,
        PipelineStage.DEPLOYMENT: PipelineStage.COMPLETE,
    }
    
    # Which agent is responsible for each stage
    STAGE_OWNERS = {
        PipelineStage.IDEATION: "creative_director",
        PipelineStage.RECIPE_DEVELOPMENT: "baker",
        PipelineStage.PHOTOGRAPHY: "art_director",
        PipelineStage.COPYWRITING: "copywriter",
        PipelineStage.CREATIVE_REVIEW: "creative_director",
        PipelineStage.revisions: None,  # Depends on what needs revision
        PipelineStage.FINAL_APPROVAL: "creative_director",
        PipelineStage.DEPLOYMENT: "site_architect",
    }
    
    def __init__(self):
        """Initialize the pipeline."""
        self.active_recipes: Dict[str, RecipeContext] = {}
        self.completed_recipes: List[RecipeContext] = []
        
        logger.info("RecipePipeline initialized")
    
    def start_recipe(self, recipe_id: str, concept: str) -> RecipeContext:
        """
        Start a new recipe in the pipeline.
        
        Args:
            recipe_id: Unique identifier
            concept: The recipe concept/idea
            
        Returns:
            RecipeContext for this recipe
        """
        context = RecipeContext(recipe_id=recipe_id, concept=concept)
        self.active_recipes[recipe_id] = context
        
        logger.info(f"Started recipe pipeline: {recipe_id} - {concept}")
        return context
    
    def advance_stage(self, recipe_id: str, work_product: Optional[Dict[str, Any]] = None) -> PipelineStage:
        """
        Advance a recipe to the next stage.
        
        Args:
            recipe_id: The recipe to advance
            work_product: Optional output from the current stage
            
        Returns:
            The new current stage
        """
        if recipe_id not in self.active_recipes:
            raise ValueError(f"Recipe {recipe_id} not found in active recipes")
        
        context = self.active_recipes[recipe_id]
        current_stage = context.current_stage
        
        # Store work product
        if work_product:
            if current_stage == PipelineStage.RECIPE_DEVELOPMENT:
                context.recipe_data = work_product
            elif current_stage == PipelineStage.PHOTOGRAPHY:
                context.photos = work_product.get("photos", [])
            elif current_stage == PipelineStage.COPYWRITING:
                context.recipe_copy = work_product
        
        # Advance to next stage
        next_stage = self.STAGE_FLOW.get(current_stage)
        if next_stage is None:
            raise ValueError(f"No next stage defined for {current_stage}")
        
        context.current_stage = next_stage
        context.updated_at = datetime.now()
        
        logger.info(f"Recipe {recipe_id}: {current_stage.value} → {next_stage.value}")
        
        # Check if complete
        if next_stage == PipelineStage.COMPLETE:
            context.completed_at = datetime.now()
            self.completed_recipes.append(context)
            del self.active_recipes[recipe_id]
            logger.info(f"Recipe {recipe_id} COMPLETE!")
        
        return next_stage
    
    def request_revisions(
        self, 
        recipe_id: str, 
        stage_to_revise: PipelineStage, 
        reason: str,
        requested_by: str
    ) -> PipelineStage:
        """
        Request revisions to a specific stage.
        
        Args:
            recipe_id: The recipe needing revisions
            stage_to_revise: Which stage needs to redo work
            reason: Why revisions are needed
            requested_by: Who requested revisions
            
        Returns:
            The stage the recipe was sent back to
        """
        if recipe_id not in self.active_recipes:
            raise ValueError(f"Recipe {recipe_id} not found")
        
        context = self.active_recipes[recipe_id]
        context.add_revision_request(stage_to_revise.value, reason, requested_by)
        context.current_stage = stage_to_revise
        context.updated_at = datetime.now()
        
        logger.warning(
            f"Recipe {recipe_id}: Revisions requested for {stage_to_revise.value} by {requested_by}"
        )
        logger.debug(f"Reason: {reason}")
        
        return stage_to_revise
    
    def get_current_owner(self, recipe_id: str) -> Optional[str]:
        """
        Get the agent responsible for the current stage.
        
        Args:
            recipe_id: The recipe to check
            
        Returns:
            Agent role responsible for current stage, or None
        """
        if recipe_id not in self.active_recipes:
            return None
        
        context = self.active_recipes[recipe_id]
        return self.STAGE_OWNERS.get(context.current_stage)
    
    def create_task_for_stage(self, recipe_id: str) -> Optional[Task]:
        """
        Create a task for the current pipeline stage.
        
        Args:
            recipe_id: The recipe to create a task for
            
        Returns:
            Task for the current stage owner, or None if no owner
        """
        if recipe_id not in self.active_recipes:
            return None
        
        context = self.active_recipes[recipe_id]
        stage = context.current_stage
        owner = self.STAGE_OWNERS.get(stage)
        
        if owner is None:
            return None
        
        # Create stage-appropriate task
        task_map = {
            PipelineStage.RECIPE_DEVELOPMENT: Task(
                type="create_recipe",
                content=f"Create muffin tin recipe for: {context.concept}",
                context={"recipe_id": recipe_id, "concept": context.concept}
            ),
            PipelineStage.PHOTOGRAPHY: Task(
                type="photograph_recipe",
                content=f"Photograph {context.concept}",
                context={"recipe_id": recipe_id, "recipe_data": context.recipe_data}
            ),
            PipelineStage.COPYWRITING: Task(
                type="write_description",
                content=f"Write description for {context.concept}",
                context={
                    "recipe_id": recipe_id,
                    "recipe_data": context.recipe_data,
                    "target_word_count": 200
                }
            ),
            PipelineStage.CREATIVE_REVIEW: Task(
                type="review_package",
                content=f"Review complete recipe package for: {context.concept}",
                context={
                    "recipe_id": recipe_id,
                    "recipe_data": context.recipe_data,
                    "photos": context.photos,
                    "recipe_copy": context.recipe_copy
                }
            ),
            PipelineStage.DEPLOYMENT: Task(
                type="deploy_recipe",
                content=f"Deploy {context.concept} to website",
                context={"recipe_id": recipe_id}
            ),
        }
        
        return task_map.get(stage)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get pipeline statistics."""
        stage_counts = {}
        for context in self.active_recipes.values():
            stage = context.current_stage.value
            stage_counts[stage] = stage_counts.get(stage, 0) + 1
        
        return {
            "active_recipes": len(self.active_recipes),
            "completed_recipes": len(self.completed_recipes),
            "by_stage": stage_counts,
            "total_revisions": sum(c.revision_count for c in self.active_recipes.values()),
        }
