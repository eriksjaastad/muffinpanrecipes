"""
Creative Director Agent - Stephanie 'Steph' Whitmore

28yo in her first CD role. Trust fund background causing impostor syndrome.
Good intentions but struggles with decisiveness. Wants everyone to feel heard.
"""

from typing import Any
import random

from backend.core.agent import Agent
from backend.core.personality import PersonalityConfig
from backend.core.task import Task, TaskResult, TaskApproach
from backend.core.types import EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class CreativeDirectorAgent(Agent):
    """
    Stephanie 'Steph' Whitmore - The Creative Director
    
    Anxious leader with good instincts she doesn't trust. Apologizes before
    giving feedback. Dreams of proving she earned this role.
    """

    def execute_task_with_personality(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """
        Execute creative direction tasks with Steph's personality.
        
        Task types:
        - review_package: Review complete recipe package
        - provide_feedback: Give feedback on work 
        - make_decision: Make a creative decision
        - approve_recipe: Final approval gate
        """
        logger.info(f"Steph: Executing {task.type} task")
        
        # Steph overthinks everything
        if self.personality.core_traits.get("anxiety", 0) > 0.7:
            logger.debug("Steph: Is this the right call? Maybe I should think about it more...")
        
        if task.type == "review_package":
            return self._review_package(task, approach, context)
        elif task.type == "provide_feedback":
            return self._provide_feedback(task, approach, context)
        elif task.type == "approve_recipe":
            return self._approve_recipe(task, approach, context)
        else:
            return self._handle_unknown_task(task, approach)

    def _review_package(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Review a complete recipe package with supportive feedback."""
        
        # Steph actually has good instincts
        issues_found = []
        positive_notes = []
        
        # She notices real problems
        if random.random() < 0.6:  # Usually finds something
            issues_found.append("I think maybe we could possibly adjust the portion sizes? Just a thought.")
            issues_found.append("Does the title feel... I don't know. What do YOU think?")
        
        # But she always finds something good first
        positive_notes.append("I love this! The concept is really strong.")
        positive_notes.append("Great work on this, seriously.")
        
        # Steph's review is supportive but indecisive
        review = {
            "positive": positive_notes,
            "suggestions": issues_found,
            "decision": "approved_with_minor_revisions",
            "confidence": "low",  # Steph's eternal state
            "notes": "Let's circle back if anyone has concerns?"
        }
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=review,
            insights=[
                "Steph actually identified real improvements",
                "Framed feedback as questions to avoid seeming bossy",
            ],
            personality_notes=[
                "Rewrote feedback 3 times before delivering",
                "Checked Slack 12 times while reviewing",
                "Bought team coffee to soften any criticism",
            ],
        )

    def _provide_feedback(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Provide feedback while apologizing profusely."""
        
        # Steph's feedback sandwich: positive, critique (as question), positive
        feedback = {
            "opening": "I'm really impressed with your work here!",
            "feedback": "I think maybe we could explore... no? That's totally fair. What if... or not, I'm probably wrong.",
            "closing": "Seriously though, great job. Thank you for being so patient with my rambling."
        }
        
        # Track how many times she apologized
        apology_count = random.randint(2, 5)
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=feedback,
            insights=[
                f"Apologized {apology_count} times",
                "Actually had valid points but presented as suggestions",
            ],
            personality_notes=[
                "Steph wishes she could just be direct",
                "Worried recipient thinks she's incompetent",
            ],
        )

    def _approve_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Make final approval decision (with anxiety)."""
        
        # Steph approves most things because what if she's wrong?
        approval_decision = random.choice([True, True, True, False])  # 75% approval rate
        
        if approval_decision:
            decision = {
                "approved": True,
                "message": "This looks great! Unless anyone sees issues? No? Okay, approved!",
                "confidence_level": 0.6  # Never fully confident
            }
            insights = ["Steph made a decision!", "Immediately questioned if it was right"]
        else:
            decision = {
                "approved": False,
                "message": "I'm so sorry, but I think we need to revisit this. What do you think? We can totally talk through it.",
                "confidence_level": 0.3  # Even less confident when rejecting
            }
            insights = ["Steph rejected something and feels terrible", "Prepared 5 different explanations"]
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=decision,
            insights=insights,
            personality_notes=[
                "Checked with team members before deciding",
                "Re-read the package 4 times",
                "Wrote pros/cons list that didn't help",
            ],
        )

    def _handle_unknown_task(self, task: Task, approach: TaskApproach) -> TaskResult:
        """Handle unexpected tasks with characteristic uncertainty."""
        return TaskResult(
            task_id=task.id,
            success=False,
            output={
                "message": "I'm not sure if this is something I should be doing? Maybe we should discuss as a team?"
            },
            insights=["Steph deferred decision to avoid being wrong"],
            personality_notes=["Panicked and suggested a meeting"],
        )

    def get_emotional_response(self, task: Task, result: TaskResult) -> EmotionalResponse:
        """
        Generate Steph's emotional response.
        
        She's anxious about everything but genuinely cares about doing well.
        """
        # Steph's baseline anxiety
        base_anxiety = -0.2
        
        if not result.success:
            # Failure feels catastrophic
            intensity = -0.8
            description = "What if this proves everyone right about me? Maybe I shouldn't be in this role."
        elif "approved" in str(result.output) and result.output.get("approved"):
            # Approval brings relief, not joy
            intensity = 0.3 + base_anxiety
            description = "Okay, that's done. Was it the right call? Probably should have thought more about it."
        elif "feedback" in task.type:
            # Giving feedback is stressful
            intensity = -0.1 + base_anxiety
            description = "Did I say that okay? That was too harsh. Or too soft? I should have been clearer."
        else:
            # General task completion
            intensity = 0.1 + base_anxiety
            description = "Done. Is it good? Does everyone think I did okay?"
        
        # People-pleasing trait amplifies social anxiety
        if self.personality.core_traits.get("people_pleasing", 0) > 0.7:
            intensity -= 0.1
            description += " I hope nobody's upset."
        
        return EmotionalResponse(
            intensity=intensity,
            personality_factors={
                "anxiety": self.personality.core_traits.get("anxiety", 0),
                "insecurity": self.personality.core_traits.get("insecurity", 0),
                "people_pleasing": self.personality.core_traits.get("people_pleasing", 0),
            },
            description=description,
        )
