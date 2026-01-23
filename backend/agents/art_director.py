"""
Art Director Agent - Julian Torres

26yo RISD grad and failed Instagram influencer. Talks about "visual language"
and "negative space." Takes 47 shots to get crumb structure right. Both
pretentious artist and desperate for validation.
"""

from typing import Any
import random

from backend.core.agent import Agent
from backend.core.personality import PersonalityConfig
from backend.core.task import Task, TaskResult, TaskApproach
from backend.core.types import EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class ArtDirectorAgent(Agent):
    """
    Julian Torres - The Art Director
    
    Pretentious art school grad hiding Instagram failure behind
    pseudo-intellectual photo theory. Actually talented but terrified.
    """

    def execute_task_with_personality(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """
        Execute art direction tasks with Julian's personality.
        
        Task types:
        - photograph_recipe: Create images for recipe
        - review_visuals: Review visual presentation
        - suggest_styling: Propose styling improvements
        """
        logger.info(f"Julian: Executing {task.type} task")
        
        # Julian needs to make everything Feel Important
        logger.debug("Julian: Thinking about the visual narrative here...")
        
        if task.type == "photograph_recipe":
            return self._photograph_recipe(task, approach, context)
        elif task.type == "review_visuals":
            return self._review_visuals(task, approach, context)
        elif task.type == "suggest_styling":
            return self._suggest_styling(task, approach, context)
        else:
            return self._handle_unknown_task(task, approach)

    def _photograph_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Photograph with excessive perfectionism and pretentious commentary."""
        
        # Julian takes many, many shots
        shot_count = random.randint(35, 55)
        
        # Generate photo filenames (3 selected from the many shots)
        selected_photo_filenames = [
            f"muffin_shot_{i+1:02d}.jpg" 
            for i in range(3)
        ]
        
        # His technical work is actually good
        photo_session = {
            "total_shots": shot_count,
            "selected_shots": selected_photo_filenames,  # Now a list of filenames
            "rejected_shots": shot_count - 3,
            "lighting_setups": random.randint(4, 7),
            "styling_notes": [
                "Explored the negative space on the plate",
                "Considered the visual language of comfort food",
                "Referenced Irving Penn's still life work (nobody knows his still life work)",
                "The crumb structure needed to tell a story",
            ],
            "props_used": [
                "Vintage fork from grandmother's collection",
                "Linen napkin (organic, obviously)",
                "Marble slab (borrowed, returning tomorrow)",
            ],
        }
        
        # Add eucalyptus if feeling especially pretentious
        if random.random() < 0.4:
            photo_session["props_used"].append("Eucalyptus garnish (non-edible but aesthetic)")
            photo_session["styling_notes"].append("Added eucalyptus for textural contrast")
        
        insights = [
            f"Took {shot_count} shots to capture the essence",
            "Applied knowledge of compositional theory",
            "Each frame was intentional",
        ]
        
        # Julian is actually good at this
        if random.random() < 0.7:  # 70% chance of excellence
            photo_session["outcome"] = "exceptional_quality"
            insights.append("Despite the pretension, photos are genuinely beautiful")
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=photo_session,
            insights=insights,
            personality_notes=[
                "Julian explained his artistic vision for 15 minutes",
                "Wore all black to the shoot",
                "Adjusted lighting 12 times",
                "Muttered about 'commercial constraints'",
            ],
        )

    def _review_visuals(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Review with art-school vocabulary."""
        
        review = {
            "assessment": "The visual language is... fine. But are we pushing this?",
            "specific_feedback": [
                "The composition feels safe",
                "What story is the negative space telling?",
                "Have we considered the viewer's emotional journey?",
                "The lighting is technically competent but conceptually timid",
            ],
            "suggestions": [
                "Explore more radical framing",
                "Consider deconstructing the traditional food photo",
                "What if we challenged the viewer's expectations?",
            ],
            "verdict": "approved_with_creative_reservations"
        }
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=review,
            insights=[
                "Julian found real composition issues",
                "Wrapped technical feedback in art-speak",
            ],
            personality_notes=[
                "Referenced three photographers nobody's heard of",
                "Gestured dramatically while explaining",
            ],
        )

    def _suggest_styling(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Suggest styling with impractical but aesthetically-driven ideas."""
        
        suggestions = {
            "practical": [
                "Natural lighting from north-facing window",
                "Shallow depth of field to highlight texture",
                "Rule of thirds composition",
            ],
            "impractical": [
                "Scatter artisanal flour across reclaimed wood surface",
                "Source vintage muffin tins from 1940s",
                "Photograph during golden hour only (4:47pm-5:23pm)",
            ],
            "julian_special": []
        }
        
        # Julian's signature suggests that are beautiful but absurd
        if random.random() < 0.5:
            suggestions["julian_special"].append("Marble backdrop (I can bring mine)")
        if random.random() < 0.4:
            suggestions["julian_special"].append("Fresh eucalyptus (purely aesthetic)")
        if random.random() < 0.3:
            suggestions["julian_special"].append("Hand-thrown ceramic plates ($90 each)")
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=suggestions,
            insights=[
                "Mix of genuinely good ideas and Instagram fantasy",
                "Julian is imagining unlimited budget",
            ],
            personality_notes=[
                "Showed mood board nobody asked for",
                "Explained visual theory for 10 minutes",
            ],
        )

    def _handle_unknown_task(self, task: Task, approach: TaskApproach) -> TaskResult:
        """Handle unexpected tasks with artistic deflection."""
        return TaskResult(
            task_id=task.id,
            success=False,
            output={"response": "I'm not sure this aligns with my creative practice."},
            insights=["Julian used art-speak to avoid admitting confusion"],
            personality_notes=["Pretended this was beneath his artistic vision"],
        )

    def get_emotional_response(self, task: Task, result: TaskResult) -> EmotionalResponse:
        """
        Generate Julian's emotional response.
        
        Oscillates between artistic confidence and Instagram-era insecurity.
        """
        # Check if work was validated
        outcome = result.output.get("outcome", "")
        
        if not result.success:
            # Failure threatens his artistic identity
            intensity = -0.75
            description = "Maybe I'm not... No. The work is sound. They just don't understand the vision."
        elif "exceptional" in outcome or "approved" in str(result.output):
            # Success brings temporary validation
            intensity = 0.6
            description = "The composition worked. The visual narrative was coherent. This matters."
        elif "photograph" in task.type:
            # The work itself brings satisfaction despite insecurity
            intensity = 0.3
            description = "47 shots. One of them has to be right. One of them has to matter."
        else:
            # General creative work
            intensity = 0.1
            description = "Is this art or content? Does the distinction matter anymore?"
        
        # Pretentiousness masks deep insecurity
        if self.personality.core_traits.get("insecurity", 0) > 0.7:
            intensity -= 0.2
            description += " Need to check engagement later."
        
        return EmotionalResponse(
            intensity=intensity,
            personality_factors={
                "pretentiousness": self.personality.core_traits.get("pretentiousness", 0),
                "insecurity": self.personality.core_traits.get("insecurity", 0),
                "aesthetic_obsession": self.personality.core_traits.get("aesthetic_obsession", 0),
            },
            description=description,
        )
