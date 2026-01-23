"""
Editorial Copywriter Agent - Marcus Reid

31yo Columbia MFA whose experimental novel sold 347 copies. Writes 800-word
backstories for muffins. Secretly good at food writing but can't accept it
because it wasn't the dream.
"""

from typing import Any
import random

from backend.core.agent import Agent
from backend.core.personality import PersonalityConfig
from backend.core.task import Task, TaskResult, TaskApproach
from backend.core.types import EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger

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
        """Write recipe description with literary pretension."""
        
        # Marcus writes FAR too much
        target_words = task.context.get("target_word_count", 200)
        actual_words = random.randint(int(target_words * 3), int(target_words * 5))
        
        # His writing has layers (too many layers)
        description_elements = {
            "opening": "There's a literary tradition...",
            "cultural_context": "Proust wrote about madeleines, and what is a muffin but a madeleine's more democratic cousin?",
            "personal_reflection": "I've been thinking about how food shapes memory, how a single bite can transport us...",
            "actual_recipe_info": "This recipe yields 12 portions of comfort.",
            "literary_reference": random.choice([
                "As MFK Fisher once observed about eggs...",
                "Nigel Slater understood that simplicity...",
                "Elizabeth David knew that the best recipes...",
            ]),
            "word_count": actual_words,
        }
        
        # Sometimes Marcus is brilliant
        is_brilliant = random.random() < 0.3  # 30% of the time
        
        if is_brilliant:
            description_elements["quality"] = "exceptional"
            description_elements["note"] = "This is actually beautiful. People will share this."
        else:
            description_elements["quality"] = "overwrought but competent"
        
        insights = [
            f"Wrote {actual_words} words (target was {target_words})",
            "Referenced three food writers",
            "Included personal anecdote about childhood",
        ]
        
        if is_brilliant:
            insights.append("Despite the length, this is genuinely moving")
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=description_elements,
            insights=insights,
            personality_notes=[
                "Marcus used 'whom' twice",
                "Referenced his thesis research",
                "Brought Moleskine to typing session",
                "Checked thesaurus 7 times",
            ],
        )

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
        elif quality == "exceptional" or "brilliant" in result.insights:
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
