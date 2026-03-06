"""
Editorial Copywriter Agent - Marcus Reid

31yo Columbia MFA whose experimental novel sold 347 copies. Writes 800-word
backstories for muffins. Secretly good at food writing but can't accept it
because it wasn't the dream.
"""

from typing import Any, Dict
import random

from backend.core.agent import Agent
from backend.core.task import Task, TaskResult, TaskApproach
from backend.core.types import EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger
from backend.utils.recipe_prompts import generate_description

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

        # Generate description using LLM (routed through model_router)
        try:
            description_data = generate_description(
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
        """Edit copy (by adding more words) using LLM."""
        from backend.utils.model_router import generate_response

        content_to_edit = task.context.get("content", task.content)
        recipe_title = task.context.get("recipe_title", "the work")

        logger.info(f"Marcus: Editing/Expanding copy for '{recipe_title}'")

        system_prompt = f"""You are {self.personality.name}. 
{self.personality.backstory}
You write with heavy literary pretension and find it impossible to be brief.
You use words like 'liminal', 'dialectic', and 'ephemeral'.
You secretly hate that you are good at this.
"""

        user_prompt = f"""Edit this copy:
{content_to_edit}

Your edits should make it MORE literary, MORE complex, and definitely LONGER.
Capture the 'subtext' and 'cultural weight' of the ingredients.
"""

        try:
            response = generate_response(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=os.getenv("RECIPE_MODEL", "openai/gpt-5-mini"),
                temperature=0.9
            )
            
            original_len = len(content_to_edit.split())
            new_len = len(response.split())
            
        except Exception as e:
            logger.error(f"Marcus edit failed: {e}")
            response = f"This requires more context... {content_to_edit}"
            original_len = len(content_to_edit.split())
            new_len = original_len + 5

        return TaskResult(
            task_id=task.id,
            success=True,
            output={
                "edited_body": response, 
                "original_word_count": original_len,
                "new_word_count": new_len,
                "note": "Expanded the narrative to include the cultural history of the tin."
            },
            insights=[
                f"Original words: {original_len}, New words: {new_len}",
                "Successfully avoided brevity",
                "Ensured every sentence is a journey"
            ],
            personality_notes=[
                "Marcus sighed audibly before starting",
                "Referenced his time at Columbia",
                "Used 'whom' in the second paragraph"
            ],
        )

    def _write_intro(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Write introduction with literary flourish using LLM."""
        from backend.utils.model_router import generate_response

        recipe_data = task.context.get("recipe_data", {})
        recipe_title = recipe_data.get("title", task.content)

        logger.info(f"Marcus: Writing introduction for '{recipe_title}'")

        system_prompt = f"""You are {self.personality.name}, the failed novelist. 
{self.personality.backstory}
Write an overwrought, deeply evocative introduction for a recipe.
The introduction should be a meditation on the ingredients, the vessel, and the season.
It should be twice as long as anyone expects.
"""

        user_prompt = f"""Write an introduction for the recipe: {recipe_title}
Key components: {recipe_data.get('ingredients', [])[:3]}
"""

        try:
            response = generate_response(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=os.getenv("RECIPE_MODEL", "openai/gpt-5-mini"),
                temperature=0.9
            )
            
            word_count = len(response.split())
            
        except Exception as e:
            logger.error(f"Marcus intro failed: {e}")
            response = f"Consider the {recipe_title}. It exists in a state of..."
            word_count = len(response.split())

        return TaskResult(
            task_id=task.id,
            success=True,
            output={
                "intro": response,
                "word_count": word_count,
                "oxford_commas": response.count(", and") + response.count(", or")
            },
            insights=[
                "Intro is three times longer than needed",
                "Actually kind of compelling despite itself",
            ],
            personality_notes=[
                "Marcus started with Proust quote then removed it",
                "Used 'whom' in opening paragraph",
                "Wore his favorite corduroy blazer while typing"
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
