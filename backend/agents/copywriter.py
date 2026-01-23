"""
Editorial Copywriter Agent - Marcus Reid

31yo Columbia MFA whose experimental novel sold 347 copies. Writes 800-word
backstories for muffins. Secretly good at food writing but can't accept it
because it wasn't the dream.
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


class CopywriterAgent(Agent):
    """
    Marcus Reid - The Editorial Copywriter

    Failed novelist who writes literary muffin descriptions.
    Bitter about view counts but occasionally brilliant when he
    stops trying to prove something.
    """

    def execute_task_with_personality(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """
        Execute copywriting tasks with Marcus's personality.

        Task types:
        - write_description: Write recipe description
        - edit_copy: Edit or refine copy
        - write_intro: Write introduction text
        """
        logger.info(f"Marcus: Executing {task.type} task")

        # Marcus always over-writes
        logger.debug("Marcus: Considering the narrative arc...")

        if task.type == "write_description":
            return self._write_description(task, approach, context)
        elif task.type == "edit_copy":
            return self._edit_copy(task, approach, context)
        elif task.type == "write_intro":
            return self._write_intro(task, approach, context)
        else:
            return self._handle_unknown_task(task, approach)

    def _write_description(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Write recipe description with literary pretension using LLM."""

        target_words = task.context.get("target_word_count", 200)
        recipe_data = task.context.get("recipe_data", {})
        recipe_title = recipe_data.get("title", task.content)

        logger.info(f"Marcus: Crafting description for '{recipe_title}'")

        # Build personality context for the LLM
        personality_context = {
            "name": self.personality.name,
            "backstory": self.personality.backstory,
            "quirks": self.personality.quirks,
            "core_traits": self.personality.core_traits,
        }

        # Generate description using LLM
        try:
            ollama = get_ollama_client()
            description_data = ollama.generate_description(
                recipe_title=recipe_title,
                recipe_data=recipe_data,
                personality_context=personality_context,
                target_word_count=target_words,
            )
            logger.info(f"Marcus: Wrote {description_data.get('word_count', 0)} words (target was {target_words})")
        except Exception as e:
            logger.error(f"Marcus: LLM generation failed: {e}")
            description_data = self._fallback_description(recipe_title, target_words)

        # Add Marcus's characteristic elements
        actual_words = description_data.get("word_count", 0)
        is_brilliant = actual_words >= target_words * 3 or random.random() < 0.3

        description_data["literary_references"] = random.choice([
            "MFK Fisher once observed about eggs...",
            "Nigel Slater understood that simplicity...",
            "Elizabeth David knew that the best recipes...",
            "Ruth Reichl wrote about comfort food...",
        ])

        if is_brilliant:
            description_data["note"] = "This is actually beautiful. People will share this."

        insights = [
            f"Wrote {actual_words} words (target was {target_words})",
            "Referenced food writing tradition",
            "Found the story in the ingredients",
        ]

        if is_brilliant:
            insights.append("Despite the length, this is genuinely moving")

        return TaskResult(
            task_id=task.id,
            success=True,
            output=description_data,
            insights=insights,
            personality_notes=[
                "Marcus used 'whom' twice",
                "Referenced his thesis research",
                "Brought Moleskine to typing session",
                "Checked thesaurus 7 times",
            ],
        )

    def _fallback_description(self, recipe_title: str, target_words: int) -> Dict[str, Any]:
        """Fallback description when LLM is unavailable."""
        logger.warning(f"Using fallback description for: {recipe_title}")
        return {
            "body": f"There is a certain poetry in the simple act of cooking. {recipe_title} represents more than mere sustenance—it is a meditation on the intersection of tradition and innovation, of comfort and creativity. The muffin tin, that most humble of vessels, transforms ingredients into something greater than the sum of their parts.",
            "word_count": 52,
            "target_word_count": target_words,
            "quality": "placeholder - LLM unavailable",
            "exceeded_target_by": 52 - target_words,
        }

    def _edit_copy(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Edit copy (by adding more words)."""

        # Marcus's edits somehow make things longer
        original_length = task.context.get("original_word_count", 200)
        edited_length = int(original_length * 1.3)  # 30% longer

        editing_notes = {
            "changes_made": [
                "Added contextualizing paragraph",
                "Strengthened temporal metaphors",
                "Enriched the subtext",
                "Included relevant literary antecedent",
            ],
            "removed": [],  # Marcus doesn't remove things
            "added": [
                "200 words of cultural background",
                "Reference to food writing tradition",
                "Personal reflection on the recipe's meaning",
            ],
            "new_word_count": edited_length,
            "original_word_count": original_length,
        }

        return TaskResult(
            task_id=task.id,
            success=True,
            output=editing_notes,
            insights=[
                "Editing made copy longer, not shorter",
                "Every edit was defensible on literary grounds",
            ],
            personality_notes=[
                "Marcus muttered about 'commercial constraints'",
                "Referenced workshop feedback from MFA",
            ],
        )

    def _write_intro(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Write introduction with literary flourish."""

        intro = {
            "opening_line": "Consider the muffin.",
            "body": "Not as in 'think about muffins' but consider—in the way one might consider a painting, a poem, a moment of unexpected grace in an ordinary afternoon.",
            "transition": "This recipe is an argument for attention, for presence, for the radical act of caring about your breakfast.",
            "word_count": random.randint(300, 500),
            "oxford_commas": 7,  # Marcus loves Oxford commas
        }

        return TaskResult(
            task_id=task.id,
            success=True,
            output=intro,
            insights=[
                "Intro is three times longer than needed",
                "Actually kind of compelling despite itself",
            ],
            personality_notes=[
                "Marcus started with Proust quote then removed it",
                "Used 'whom' in opening paragraph",
            ],
        )

    def _handle_unknown_task(self, task: Task, approach: TaskApproach) -> TaskResult:
        """Handle unexpected tasks with literary deflection."""
        return TaskResult(
            task_id=task.id,
            success=False,
            output={"response": "If I may offer some context on why this request is challenging..."},
            insights=["Marcus wrote 300 words explaining why he can't do it"],
            personality_notes=["Turned confusion into essay"],
        )

    def get_emotional_response(self, task: Task, result: TaskResult) -> EmotionalResponse:
        """
        Generate Marcus's emotional response.

        Bitter about writing muffin copy but occasionally proud despite himself.
        """
        quality = result.output.get("quality", "")
        word_count = result.output.get("word_count", 0)

        if not result.success:
            # Failure validates his belief this isn't real writing
            intensity = -0.5
            description = "Of course. Commercial constraints don't allow for actual craft."
        elif "exceptional" in quality or "brilliant" in str(result.insights):
            # The worst outcome: success at something he doesn't respect
            intensity = 0.3  # Positive but conflicted
            description = "47,000 people will read this. 347 read my novel. I hate that this matters."
        elif word_count > 500:
            # At least he got to write
            intensity = 0.2
            description = "They'll probably cut it. They always cut it. But I said what needed to be said."
        else:
            # Regular task completion
            intensity = -0.1
            description = "It's done. It's... fine. For a muffin description."

        # Literary pretension is a armor for bitterness
        if self.personality.core_traits.get("bitterness", 0) > 0.7:
            intensity -= 0.2
            description += " This wasn't the dream."

        return EmotionalResponse(
            intensity=intensity,
            personality_factors={
                "literary_pretension": self.personality.core_traits.get("literary_pretension", 0),
                "bitterness": self.personality.core_traits.get("bitterness", 0),
                "hidden_talent": self.personality.core_traits.get("hidden_talent", 0),
            },
            description=description,
        )
