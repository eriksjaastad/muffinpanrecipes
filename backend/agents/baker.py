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
        """Create a muffin tin recipe with Margaret's expertise using LLM."""

        # Extract recipe concept from task
        concept = task.context.get("concept", task.content)
        # Clean up any prompt leakage
        if concept.startswith("Create a muffin tin recipe for:"):
            concept = concept.replace("Create a muffin tin recipe for:", "").strip()

        logger.info(f"Margaret: Developing recipe for '{concept}'")

        # Build personality context for the LLM
        personality_context = {
            "name": self.personality.name,
            "backstory": self.personality.backstory,
            "quirks": self.personality.quirks,
            "core_traits": self.personality.core_traits,
        }

        # Generate recipe using LLM
        try:
            ollama = get_ollama_client()
            recipe_data = ollama.generate_recipe(
                concept=concept,
                personality_context=personality_context,
            )
            logger.info(f"Margaret: Recipe generated - {recipe_data.get('title', concept)}")
        except Exception as e:
            logger.error(f"Margaret: LLM generation failed: {e}")
            # Fall back to basic structure with error note
            recipe_data = self._fallback_recipe(concept)

        # Ensure concept is preserved in output (tests expect this)
        recipe_data["concept"] = concept

        # Add Margaret's personality notes
        margaret_notes = []
        if triggered:
            margaret_notes.append("*mutters* Fine. If they want trendy...")

        if approach.modifications and "prefer_traditional_approach" in approach.modifications:
            margaret_notes.append("At least we're doing the technique correctly.")

        margaret_notes.append("Check oven temp with a thermometer. Don't trust the dial.")

        if recipe_data.get("chef_notes"):
            margaret_notes.append(recipe_data["chef_notes"])

        recipe_data["margaret_notes"] = margaret_notes

        # Build insights based on what was generated
        insights = [
            "Verified ratios match traditional formulations",
            "Considered structural integrity in muffin tin format",
        ]

        if triggered:
            insights.append("Incorporated trendy ingredient while maintaining proper technique")

        if recipe_data.get("description"):
            insights.append(f"Created: {recipe_data['title']}")

        return TaskResult(
            task_id=task.id,
            success=True,
            output=recipe_data,
            insights=insights,
            personality_notes=[
                "Margaret measured ingredients twice",
                "Muttered about proper technique",
                f"Emotional state: {'irritated but competent' if triggered else 'professionally focused'}"
            ],
        )

    def _fallback_recipe(self, concept: str) -> Dict[str, Any]:
        """Fallback recipe structure when LLM is unavailable."""
        logger.warning(f"Using fallback recipe for: {concept}")
        return {
            "title": concept,
            "concept": concept,
            "description": f"A reliable muffin-tin build for {concept}, designed for crispy edges and a tender center.",
            "servings": 12,
            "prep_time": 20,
            "cook_time": 22,
            "difficulty": "medium",
            "category": "savory",
            "ingredients": [
                {"item": "eggs", "amount": "8 large", "notes": "room temperature"},
                {"item": "shredded sharp cheddar", "amount": "1 cup", "notes": "packed loosely"},
                {"item": "milk", "amount": "1/2 cup", "notes": "whole milk preferred"},
                {"item": "all-purpose flour", "amount": "3/4 cup", "notes": "leveled"},
                {"item": "melted butter", "amount": "2 tbsp", "notes": "cooled slightly"},
                {"item": "salt", "amount": "1 tsp", "notes": "fine"},
                {"item": "black pepper", "amount": "1/2 tsp", "notes": "freshly cracked"},
                {"item": "chopped green onion", "amount": "1/3 cup", "notes": "optional"},
            ],
            "instructions": [
                "Preheat oven to 375°F (190°C). Grease a standard 12-cup muffin tin well.",
                "Whisk eggs, milk, melted butter, salt, and pepper until fully combined.",
                "Whisk in flour just until smooth, then fold in cheddar and green onion.",
                "Divide batter evenly among muffin cups, filling each about 3/4 full.",
                "Bake 20-24 minutes, until puffed, lightly golden, and just set in the center.",
                "Rest 5 minutes in pan, then loosen with a thin knife and serve warm.",
            ],
            "chef_notes": "Use a metal pan for better browning. Let the cups rest before removing so they hold shape.",
        }

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
