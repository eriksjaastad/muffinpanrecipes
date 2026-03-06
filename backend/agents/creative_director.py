"""
Creative Director Agent - Stephanie 'Steph' Whitmore

28yo in her first CD role. Trust fund background causing impostor syndrome.
Good intentions but struggles with decisiveness. Wants everyone to feel heard.
"""

import random

from backend.core.agent import Agent
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
        """Review a complete recipe package using LLM."""
        from backend.utils.model_router import generate_response
        
        package_data = task.context.get("package", task.context)
        recipe_title = package_data.get("recipe", {}).get("title", "this recipe")
        
        logger.info(f"Steph: Reviewing package for '{recipe_title}'")

        system_prompt = f"""You are {self.personality.name}, the Creative Director. 
{self.personality.backstory}
Your personality: {self.personality.core_traits}
Your quirks: {self.personality.quirks}

Review this recipe package (recipe, image prompts, copy). 
You have good instincts but you are very anxious about making the wrong call.
You always find something positive to say first, then offer constructive (but apologetic) feedback.
"""

        user_prompt = f"""Review this package:
{package_data}

Provide your feedback as a list of specific creative observations.
End your response with 'VERDICT: APPROVED' or 'VERDICT: REVISE'.
"""

        try:
            response = generate_response(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=os.getenv("RECIPE_MODEL", "openai/gpt-5-mini"),
                temperature=0.5
            )
            
            lines = response.strip().split("\n")
            feedback = [l.strip() for l in lines if l.strip() and not l.startswith("VERDICT:")]
            verdict = "APPROVED" if "VERDICT: APPROVED" in response else "REVISE"
            
        except Exception as e:
            logger.error(f"Steph review failed: {e}")
            feedback = ["I'm so sorry, the system is glitching. It looks great? I think?"]
            verdict = "APPROVED"

        review = {
            "feedback": feedback,
            "verdict": verdict,
            "confidence": "moderate-low",
            "notes": "I hope this is the right direction!"
        }
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=review,
            insights=[
                f"Steph verdict: {verdict}",
                "Balanced supportive tone with critical needs",
            ],
            personality_notes=[
                "Steph's heart was racing while she typed this",
                "Re-read the entire package twice",
                "Wondered if Julian would be mad about the feedback"
            ],
        )

    def _provide_feedback(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Provide feedback while apologizing profusely using LLM."""
        from backend.utils.model_router import generate_response

        work_to_review = task.context.get("work", task.content)
        
        system_prompt = f"""You are {self.personality.name}. 
You need to provide feedback on some work. You are very polite and apologize often.
"""
        user_prompt = f"Provide feedback on this: {work_to_review}"

        try:
            response = generate_response(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=os.getenv("RECIPE_MODEL", "openai/gpt-5-mini"),
                temperature=0.7
            )
        except Exception as e:
            response = "I'm so sorry, I can't quite see the work right now. But I'm sure it's lovely!"

        return TaskResult(
            task_id=task.id,
            success=True,
            output={"feedback": response},
            insights=["Apologetic but useful feedback delivered"],
            personality_notes=[
                "Steph wishes she could just be direct",
                "Worried recipient thinks she's incompetent",
            ],
        )

    def _approve_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Make final approval decision using LLM for the final gut-check."""
        from backend.utils.model_router import generate_response

        recipe_data = task.context.get("recipe_data", {})
        
        system_prompt = f"""You are {self.personality.name}. 
Decide if this recipe is ready for the world. You are anxious.
"""
        user_prompt = f"Final approval for: {recipe_data}"

        try:
            response = generate_response(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=os.getenv("RECIPE_MODEL", "openai/gpt-5-mini"),
                temperature=0.4
            )
            approved = "approve" in response.lower() and "don't" not in response.lower()
        except Exception as e:
            logger.error(f"Steph approval gut-check failed: {e}")
            response = "I'm so sorry, I'm having trouble deciding. But let's go for it!"
            approved = True

        decision = {
            "approved": approved,
            "message": response,
            "confidence_level": 0.4 if not approved else 0.6
        }
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=decision,
            insights=[f"Final decision: {'Approved' if approved else 'Rejected'}"],
            personality_notes=[
                "Checked with team members before deciding",
                "Re-read the package 4 times",
                "Wore her 'lucky' leadership cardigan"
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
