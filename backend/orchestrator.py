"""
Integration Orchestrator - Coordinates the entire AI Creative Team system.

This is the main controller that brings together agents, messaging,
pipeline, and data models to produce recipes with creation stories.
"""

from typing import Dict, Optional, List
from pathlib import Path
from datetime import datetime
import uuid

from backend.agents.factory import create_agent
from backend.messaging.message_system import MessageSystem
from backend.messaging.message_handler import MessageHandler
from backend.pipeline.recipe_pipeline import RecipePipeline, PipelineStage
from backend.memory.agent_memory import AgentMemory
from backend.data.recipe import Recipe, CreationStory, AgentContribution
from backend.data.agent_profile import AgentProfile
from backend.core.task import Task
from backend.utils.logging import get_logger
from backend.utils.discord import notify_recipe_ready

logger = get_logger(__name__)


class RecipeOrchestrator:
    """
    Main orchestrator for AI Creative Team recipe production.
    
    Coordinates agents, messaging, pipeline, and output generation.
    """
    
    def __init__(
        self,
        data_dir: Path = None,
        message_storage: Path = None,
        memory_storage: Path = None
    ):
        """
        Initialize the orchestrator.

        Args:
            data_dir: Base directory for all data storage
            message_storage: Directory for message history
            memory_storage: Directory for agent memories
        """
        # Storage paths - PRD Section 10.5 structure
        self.data_dir = data_dir or Path("data")
        self.recipes_dir = self.data_dir / "recipes"  # Contains pending/, approved/, published/, rejected/
        self.stories_dir = self.data_dir / "stories"
        self.message_storage = message_storage or Path("data/messages")
        self.memory_storage = memory_storage or Path("data/agent_memories")

        # Create directories including status subdirectories
        for dir_path in [
            self.recipes_dir / "pending",
            self.recipes_dir / "approved",
            self.recipes_dir / "published",
            self.recipes_dir / "rejected",
            self.stories_dir,
            self.message_storage,
            self.memory_storage
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize systems
        self.message_system = MessageSystem(storage_path=self.message_storage)
        self.pipeline = RecipePipeline()
        
        # Initialize agents
        self.agents: Dict[str, any] = {}
        self._initialize_agents()
        
        # Track current recipe production
        self.current_recipe_id: Optional[str] = None
        self.current_story: Optional[CreationStory] = None
        
        logger.info("RecipeOrchestrator initialized")
    
    def _initialize_agents(self) -> None:
        """Initialize all five agents with memory and messaging."""
        agent_roles = ["baker", "creative_director", "art_director", "copywriter", "site_architect"]
        
        for role in agent_roles:
            # Create agent
            agent = create_agent(role)
            
            # Set up memory
            memory = AgentMemory(agent_role=role, storage_path=self.memory_storage)
            agent.set_memory(memory)
            
            # Set up message handler
            handler = MessageHandler(role, self.message_system)
            agent.set_message_handler(handler)
            
            # Register with message system
            self.message_system.register_agent(role, agent.receive_message)
            
            # Store agent
            self.agents[role] = agent
            
            logger.info(f"Initialized agent: {role} ({agent.personality.name})")
    
    def produce_recipe(self, concept: str) -> tuple[Recipe, CreationStory]:
        """
        Produce a complete recipe with creation story.
        
        This is the main entry point for recipe production.
        
        Args:
            concept: The recipe concept/idea
            
        Returns:
            Tuple of (Recipe, CreationStory)
        """
        logger.info(f"=" * 70)
        logger.info(f"STARTING RECIPE PRODUCTION: {concept}")
        logger.info(f"=" * 70)
        
        # Generate IDs
        recipe_id = str(uuid.uuid4())[:8]
        story_id = str(uuid.uuid4())[:8]
        
        self.current_recipe_id = recipe_id
        
        # Initialize creation story
        self.current_story = CreationStory(
            story_id=story_id,
            recipe_id=recipe_id,
            title=f"How We Made: {concept}",
            summary="",
            full_story=""
        )
        
        # Start recipe in pipeline
        context = self.pipeline.start_recipe(recipe_id, concept)
        
        # Execute pipeline stages
        recipe_data = {}
        photos = []
        copy_text = {}
        
        try:
            # Stage 1: Recipe Development (Baker)
            logger.info(f"\n{'='*70}")
            logger.info("STAGE 1: Recipe Development (Margaret Chen - Baker)")
            logger.info(f"{'='*70}")
            recipe_data = self._execute_stage_baker(recipe_id, concept)
            self.pipeline.advance_stage(recipe_id, work_product=recipe_data)
            
            # Stage 2: Photography (Art Director)
            logger.info(f"\n{'='*70}")
            logger.info("STAGE 2: Photography (Julian Torres - Art Director)")
            logger.info(f"{'='*70}")
            photos = self._execute_stage_photography(recipe_id, recipe_data)
            self.pipeline.advance_stage(recipe_id, work_product={"photos": photos})
            
            # Stage 3: Copywriting (Editorial Copywriter)
            logger.info(f"\n{'='*70}")
            logger.info("STAGE 3: Copywriting (Marcus Reid - Editorial Copywriter)")
            logger.info(f"{'='*70}")
            copy_text = self._execute_stage_copywriting(recipe_id, concept, recipe_data)
            self.pipeline.advance_stage(recipe_id, work_product=copy_text)
            
            # Stage 4: Creative Review (Creative Director)
            logger.info(f"\n{'='*70}")
            logger.info("STAGE 4: Creative Review (Steph Whitmore - Creative Director)")
            logger.info(f"{'='*70}")
            approved = self._execute_stage_review(recipe_id)
            
            if approved:
                self.pipeline.advance_stage(recipe_id)  # FINAL_APPROVAL
                self.pipeline.advance_stage(recipe_id)  # DEPLOYMENT
            
            # Stage 5: Deployment (Site Architect)
            logger.info(f"\n{'='*70}")
            logger.info("STAGE 5: Deployment (Devon Park - Site Architect)")
            logger.info(f"{' ='*70}")
            self._execute_stage_deployment(recipe_id)
            
            # Advance to COMPLETE
            logger.info(f"Advancing recipe {recipe_id} from DEPLOYMENT to COMPLETE")
            final_stage = self.pipeline.advance_stage(recipe_id)
            logger.info(f"Recipe {recipe_id} advanced to: {final_stage}")
            
            # Compile final recipe
            recipe = self._compile_recipe(recipe_id, concept, recipe_data, photos, copy_text)
            
            # Finalize creation story
            self._finalize_story()
            
            # Save outputs - recipes go to pending/ for human review
            recipe.save_to_file(self.recipes_dir, use_status_dir=True)
            self.current_story.save_to_file(self.stories_dir)
            
            logger.info(f"\n{'='*70}")
            logger.info(f"✅ RECIPE PRODUCTION COMPLETE: {recipe.title}")
            logger.info(f"   Recipe ID: {recipe_id}")
            logger.info(f"   Story ID: {story_id}")
            logger.info(f"{'='*70}\n")

            # Send Discord notification for review
            notify_recipe_ready(
                recipe_title=recipe.title,
                recipe_id=recipe_id,
                description_preview=recipe.description,
                ingredient_count=len(recipe.ingredients),
            )

            return recipe, self.current_story
            
        except Exception as e:
            logger.error(f"Error during recipe production: {e}", exc_info=True)
            raise
    
    def _execute_stage_baker(self, recipe_id: str, concept: str) -> Dict:
        """Execute baker's recipe development stage."""
        baker = self.agents["baker"]
        
        task = Task(
            type="create_recipe",
            content=f"Create a muffin tin recipe for: {concept}",
            context={"recipe_id": recipe_id, "concept": concept}
        )
        
        result = baker.process_task(task)
        
        # Record contribution
        self.current_story.add_contribution(
            agent_name=baker.personality.name,
            agent_role ="baker",
            contribution_type="Recipe Development",
            decisions=result.insights,
            personality_moments=result.personality_notes
        )
        
        return result.output
    
    def _execute_stage_photography(self, recipe_id: str, recipe_data: Dict) -> List[str]:
        """Execute art director's photography stage."""
        art_director = self.agents["art_director"]
        
        task = Task(
            type="photograph_recipe",
            content=f"Photograph this recipe",
            context={"recipe_id": recipe_id, "recipe_data": recipe_data}
        )
        
        result = art_director.process_task(task)
        
        # Record contribution
        self.current_story.add_contribution(
            agent_name=art_director.personality.name,
            agent_role="art_director",
            contribution_type="Photography",
            decisions=result.insights,
            personality_moments=result.personality_notes
        )
        
        return result.output.get("selected_shots", [])
    
    def _execute_stage_copywriting(self, recipe_id: str, concept: str, recipe_data: Dict) -> Dict:
        """Execute copywriter's description stage."""
        copywriter = self.agents["copywriter"]
        
        task = Task(
            type="write_description",
            content=f"Write description for {concept}",
            context={"recipe_id": recipe_id, "recipe_data": recipe_data, "target_word_count": 200}
        )
        
        result = copywriter.process_task(task)
        
        # Record contribution
        self.current_story.add_contribution(
            agent_name=copywriter.personality.name,
            agent_role="copywriter",
            contribution_type="Editorial Copy",
            decisions=result.insights,
            personality_moments=result.personality_notes
        )
        
        return result.output
    
    def _execute_stage_review(self, recipe_id: str) -> bool:
        """Execute creative director's review stage."""
        cd = self.agents["creative_director"]
        
        task = Task(
            type="review_package",
            content="Review complete recipe package",
            context={"recipe_id": recipe_id}
        )
        
        result = cd.process_task(task)
        
        # Record contribution
        self.current_story.add_contribution(
            agent_name=cd.personality.name,
            agent_role="creative_director",
            contribution_type="Creative Review",
            decisions=result.insights,
            personality_moments=result.personality_notes
        )
        
        # Check approval
        approved = result.output.get("approved", True)
        return approved
    
    def _execute_stage_deployment(self, recipe_id: str) -> None:
        """Execute site architect's deployment stage."""
        site_architect = self.agents["site_architect"]
        
        task = Task(
            type="deploy_recipe",
            content="Deploy recipe to website",
            context={"recipe_id": recipe_id}
        )
        
        result = site_architect.process_task(task)
        
        # Record contribution
        self.current_story.add_contribution(
            agent_name=site_architect.personality.name,
            agent_role="site_architect",
            contribution_type="Deployment",
            decisions=result.insights,
            personality_moments=result.personality_notes
        )
    
    def _compile_recipe(
        self,
        recipe_id: str,
        concept: str,
        recipe_data: Dict,
        photos: List[str],
        copy_text: Dict
    ) -> Recipe:
        """Compile final recipe from all stage outputs."""
        
        # Generate slug
        slug = concept.lower().replace(" ", "-")[:50]
        
        # Use baker's description if copywriter didn't provide one
        description = copy_text.get("body", "") or recipe_data.get("description", "")

        recipe = Recipe(
            recipe_id=recipe_id,
            title=recipe_data.get("title", concept),
            concept=concept,
            description=description,
            ingredients=recipe_data.get("ingredients", []),
            instructions=recipe_data.get("instructions", []),
            servings=recipe_data.get("servings", 12),
            prep_time_minutes=recipe_data.get("prep_time", 15),
            cook_time_minutes=recipe_data.get("cook_time", 20),
            difficulty=recipe_data.get("difficulty", "medium"),
            category=recipe_data.get("category", "savory"),
            photos=photos,
            featured_photo=photos[0] if photos else None,
            slug=slug,
            story_id=self.current_story.story_id if self.current_story else None
        )
        
        return recipe
    
    def _finalize_story(self) -> None:
        """Finalize the creation story."""
        if not self.current_story:
            return
        
        # Compile summary from contributions
        summary_parts = []
        for contrib in self.current_story.agent_contributions:
            if contrib.personality_moments:
                summary_parts.append(
                    f"{contrib.agent_name} ({contrib.contribution_type}): "
                    f"{contrib.personality_moments[0]}"
                )
        
        self.current_story.summary = " | ".join(summary_parts[:3])  # First 3
        
        # Compile full story
        story_parts = [f"# How We Made This Recipe\n"]
        for contrib in self.current_story.agent_contributions:
            story_parts.append(f"\n## {contrib.agent_name} - {contrib.contribution_type}\n")
            if contrib.personality_moments:
                story_parts.extend([f"- {moment}" for moment in contrib.personality_moments])
        
        self.current_story.full_story = "\n".join(story_parts)
        
        # Set completion time
        self.current_story.completed_at = datetime.now()
        
        # Get message stats
        stats = self.message_system.get_statistics()
        self.current_story.total_messages = stats.get("total_messages", 0)
    
    def get_agent_profiles(self) -> Dict[str, AgentProfile]:
        """Get current profiles for all agents."""
        profiles = {}
        for role, agent in self.agents.items():
            profile = AgentProfile.create_from_agent(agent, f"{role}_profile")
            profiles[role] = profile
        return profiles
    
    def save_agent_profiles(self) -> None:
        """Save all agent profiles to disk."""
        profiles_dir = self.data_dir / "agent_profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        
        for role, agent in self.agents.items():
            profile = AgentProfile.create_from_agent(agent, f"{role}_profile")
            profile.save_to_file(profiles_dir)
        
        logger.info(f"Saved {len(self.agents)} agent profiles")
