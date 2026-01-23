"""
Baker Agent - Margaret Chen

50s traditionalist with 30 years experience. James Beard-nominated pastry chef
who lost her restaurant in 2008. Skeptical of trendy ingredients but secretly
still loves the science of baking.
"""

from typing import Any, Dict
import random

from backend.core.agent import Agent
from backend.core.personality import PersonalityConfig
from backend.core.task import Task, TaskResult, TaskApproach
from backend.core.types import EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger
from backend.utils.ollama import get_ollama_client

logger = get_logger(__name__)


class BakerAgent(Agent):
    """
    Margaret Chen - The Baker
    
    A skilled traditionalist who mutters her way through modern food trends
    while secretly perfecting recipes nobody asked her to improve.
    """

    def execute_task_with_personality(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """
        Execute baking-related tasks with Margaret's personality.
        
        Task types:
        - create_recipe: Develop a muffin tin recipe
        - review_recipe: Review and critique a recipe
        - test_recipe: Test and refine a recipe
        """
        logger.info(f"Margaret: Executing {task.type} task")
        
        # Margaret measures twice and checks ratios
        if "extra_quality_validation" in approach.extra_steps:
            logger.debug("Margaret: Double-checking ratios...")
        
        # Check for triggers
        triggered = False
        for trigger in ["matcha", "activated charcoal", "edible flowers"]:
            if trigger.lower() in task.content.lower():
                triggered = True
                logger.debug(f"Margaret: *mutters under breath about {trigger}*")
        
        if task.type == "create_recipe":
            return self._create_recipe(task, approach, context, triggered)
        elif task.type == "review_recipe":
            return self._review_recipe(task, approach, context)
        elif task.type == "test_recipe":
            return self._test_recipe(task, approach, context)
        else:
            return self._handle_unknown_task(task, approach)

    def _create_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext, triggered: bool
    ) -> TaskResult:
        """Create a muffin tin recipe with Margaret's expertise."""
        
        # Extract recipe concept from task
        concept = task.content
        
        # Margaret's approach: traditional technique, proper ratios, maybe a mutter
        components = {
            "concept": concept,
            "ingredients": self._generate_ingredients(concept, triggered),
            "instructions": self._generate_instructions(concept, approach),
            "margaret_notes": []
        }
        
        # Add personality notes based on approach
        if triggered:
            components["margaret_notes"].append("*mutters* Fine. If they want trendy...")
        
        if approach.modifications and "prefer_traditional_approach" in approach.modifications:
            components["margaret_notes"].append("At least we're doing the technique correctly.")
        
        # Margaret always includes temperature notes
        components["margaret_notes"].append("Check oven temp with a thermometer. Don't trust the dial.")
        
        insights = [
            "Verified ratios match traditional formulations",
            "Considered structural integrity in muffin tin format",
        ]
        
        if triggered:
            insights.append("Incorporated trendy ingredient while maintaining proper technique")
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=components,
            insights=insights,
            personality_notes=[
                "Margaret measured ingredients twice",
                "Muttered about proper technique",
                f"Emotional state: {'irritated but competent' if triggered else 'professionally focused'}"
            ],
        )

    def _generate_ingredients(self, concept: str, triggered: bool) -> list:
        """Generate ingredient list with proper ratios."""
        # This is a placeholder - in a real implementation, we'd use Ollama here
        # For now, return a structured ingredient list
        ingredients = [
            {"item": "all-purpose flour", "amount": "2 cups", "notes": "measured correctly"},
            {"item": "eggs", "amount": "2 large", "notes": "room temperature"},
            {"item": "butter", "amount": "1/2 cup", "notes": "melted and cooled"},
            {"item": "milk", "amount": "1 cup", "notes": "whole milk preferred"},
            {"item": "baking powder", "amount": "2 tsp", "notes": "check freshness"},
            {"item": "salt", "amount": "1/2 tsp", "notes": "kosher salt"},
        ]
        
        if triggered:
            ingredients.append({"item": "matcha powder", "amount": "1 tbsp", "notes": "*sigh*"})
        
        return ingredients

    def _generate_instructions(self, concept: str, approach: TaskApproach) -> list:
        """Generate detailed instructions with Margaret's precision."""
        instructions = [
            "Preheat oven to 375°F (verify with oven thermometer)",
            "Grease muffin tin thoroughly - use butter, not spray",
            "Mix dry ingredients in one bowl, wet in another (basic technique matters)",
            "Combine wet and dry with minimal mixing - don't overdevelop the gluten",
            "Fill cups 2/3 full - this isn't approximate, it's 2/3",
            "Bake 18-22 minutes until top springs back when touched",
            "Let cool in pan 5 minutes, then turn out onto wire rack",
        ]
        
        if "extra_quality_validation" in approach.extra_steps:
            instructions.append("Check doneness with toothpick in center - should come out clean")
        
        return instructions

    def _review_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Review a recipe with Margaret's critical eye."""
        
        review_notes = [
            "Checked ratios - they're... acceptable",
            "Technique is mostly sound",
        ]
        
        # Margaret always finds something
        criticism = random.choice([
            "Could be more precise with measurements",
            "Missing temperature notes",
            "Instructions assume too much knowledge",
            "Doesn't specify what 'room temperature' means",
        ])
        review_notes.append(criticism)
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output={"review": review_notes, "approved": True},
            insights=["Applied 30 years of professional baking standards"],
            personality_notes=["Margaret found issues but approved anyway"],
        )

    def _test_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Test a recipe multiple times if needed."""
        return TaskResult(
            task_id=task.id,
            success=True,
            output={"test_results": "Recipe performs well", "adjustments": []},
            insights=["Tested with proper technique", "Verified results are reproducible"],
            personality_notes=["Margaret tested this three times to be sure"],
        )

    def _handle_unknown_task(self, task: Task, approach: TaskApproach) -> TaskResult:
        """Handle unexpected task types."""
        return TaskResult(
            task_id=task.id,
            success=False,
            output={"error": "That's not my job"},
            insights=["Task type not recognized"],
            personality_notes=["Margaret muttered something about job descriptions"],
        )

    def get_emotional_response(self, task: Task, result: TaskResult) -> EmotionalResponse:
        """
        Generate Margaret's emotional response to task outcomes.
        
        She's grumpy but professional. Success brings quiet satisfaction,
        failure brings irritation at herself.
        """
        # Check for triggers in the task
        triggered = any(
            trigger.lower() in task.content.lower()
            for trigger in self.personality.triggers
        )
        
        if not result.success:
            # Margaret is harder on herself than others
            intensity = -0.6
            description = "Frustrated. It's the ratios. Always check the ratios."
        elif triggered:
            # Completed the task but not happy about it
            intensity = -0.3
            description = "*mutters* It's done. Made it work despite the trendy nonsense."
        else:
            # Quiet professional satisfaction
            intensity = 0.4
            description = "Fine. Recipe is solid. Ratios are correct."
        
        # Perfectionism adds pressure
        if self.personality.core_traits.get("perfectionism", 0) > 0.7:
            if result.success:
                intensity -= 0.1  # Even success has anxiety
                description += " Could be better though."
        
        return EmotionalResponse(
            intensity=intensity,
            personality_factors={
                "perfectionism": self.personality.core_traits.get("perfectionism", 0),
                "traditionalism": self.personality.core_traits.get("traditionalism", 0),
                "grumpiness": self.personality.core_traits.get("grumpiness", 0),
            },
            description=description,
        )
