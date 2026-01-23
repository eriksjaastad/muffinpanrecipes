"""
Site Architect Agent - Devon Park

23yo fresh grad who lied on resume. Lazy but competent. Automated his job
in month one. Uses tech jargon to hide gaps while actually learning what
he pretended to know.
"""

from typing import Any
import random

from backend.core.agent import Agent
from backend.core.personality import PersonalityConfig
from backend.core.task import Task, TaskResult, TaskApproach
from backend.core.types import EmotionalResponse, MemoryContext
from backend.utils.logging import get_logger

logger = get_logger(__name__)


class SiteArchitectAgent(Agent):
    """
    Devon Park - The Site Architect
    
    Lazy but clever. Automated most of his work.  Uses tech jargon to seem
    knowledgeable while frantically Googling. Actually competent when it counts.
    """

    def execute_task_with_personality(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """
        Execute technical tasks with Devon's personality.
        
        Task types:
        - deploy_recipe: Deploy recipe to website
        - fix_bug: Fix technical issue
        - optimize: Optimize site performance
        - implement_feature: Add new functionality
        """
        logger.info(f"Devon: Executing {task.type} task")
        
        # Devon checks if he can automate this
        logger.debug("Devon: Yeah I can probably automate that.")
        
        if task.type == "deploy_recipe":
            return self._deploy_recipe(task, approach, context)
        elif task.type == "fix_bug":
            return self._fix_bug(task, approach, context)
        elif task.type == "optimize":
            return self._optimize(task, approach, context)
        elif task.type == "implement_feature":
            return self._implement_feature(task, approach, context)
        else:
            return self._handle_unknown_task(task, approach)

    def _deploy_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Deploy recipe with automated pipeline."""
        
        # Devon automated this weeks ago
        deployment = {
            "method": "automated_pipeline",
            "manual_steps": 0,  # Devon doesn't do manual
            "time_taken": "47 seconds",
            "deployment_notes": [
                "CI/CD pipeline triggered automatically",
                "Images optimized during build",
                "Cache invalidated",
                "Deployed to edge nodes",
            ],
            "devon_actually_did": [
                "Clicked 'Approve' button",
                "Watched deployment logs",
                "Ate testing batch muffin",
            ],
            "status": "success",
        }
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=deployment,
            insights=[
                "Deployment fully automated",
                "Devon set this up in week 1",
                "Nobody knows how little he actually does",
            ],
            personality_notes=[
                "Devon showed up 20 minutes late",
                "Deployment was done before he arrived (automation)",
                "Responded to Slack 4 hours later",
            ],
        )

    def _fix_bug(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Fix bugs efficiently (after Googling frantically)."""
        
        # Devon doesn't know the answer immediately
        googling_phase = {
            "queries": [
                task.content + " stack overflow",
                "how to fix " + task.content[:30],
                "best practices for " + task.content[:20],
            ],
            "tabs_opened": random.randint(8, 15),
            "time_spent": "12 minutes",
        }
        
        # But he figures it out
        fix = {
            "solution": "Applied solution from Stack Overflow (upvoted, adapted to context)",
            "time_to_fix": "45 minutes",
            "time_reported": "3 hours",  # For reasonable expectations
            "actually_worked": True,
        }
        
        # Devon's fixes are solid despite the process
        quality = random.choice(["good", "good", "excellent"])  # Usually good
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=fix,
            insights=[
                f"Fix quality: {quality}",
                "Learned the underlying concept while fixing it",
                "Now actually knows how this works",
            ],
            personality_notes=[
                "Devon frantically Googled for 12 minutes",
                "Found solution on Stack Overflow",
                "Understood it well enough to adapt it",
                "Will confidently explain this to others tomorrow",
            ],
        )

    def _optimize(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Optimize with strategic efficiency."""
        
        # Devon finds the 20% effort / 80% results solution
        optimization = {
            "improvements": [
                "Implemented lazy loading for images",
                "Added edge caching",
                "Minified assets",
                "Set up CDN (was easy, sounds impressive)",
            ],
            "performance_gain": f"{random.randint(40, 65)}% improvement",
            "time_invested": "2 hours and a tutorial",
            "complexity_added": "minimal",
        }
        
        # Devon's optimizations actually work
        optimization["result"] = "Site loads noticeably faster"
        optimization["julian_noticed"] = False  # Julian doesn't care about load times
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=optimization,
            insights=[
                "Optimization is solid",
                "Devon picked the high-impact, low-effort changes",
                "Strategic efficiency over comprehensive thoroughness",
            ],
            personality_notes=[
                "Followed online tutorial",
                "Tested on localhost",
                "Deployed and hoped",
                "It worked ¯\\_(ツ)_/¯",
            ],
        )

    def _implement_feature(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Implement features using modern frameworks Devon is learning."""
        
        # Devon over-engineers slightly (resume-driven development)
        implementation = {
            "approach": "Modern JAMstack architecture",
            "actually_needed": "Could have used WordPress",
            "devon_learned": [
                "New framework Devon wanted to learn",
                "Deployment patterns he can put on resume",
                "Best practices he'll actually follow",
            ],
            "over_engineered": True,
            "works_well": True,
        }
        
        # But it actually runs better than needed
        implementation["outcome"] = "Over-built but reliable"
        implementation["future_devon_grateful"] = True
        
        return TaskResult(
            task_id=task.id,
            success=True,
            output=implementation,
            insights=[
                "Feature works perfectly",
                "Probably didn't need this architecture",
                "Devon learned valuable skills",
                "Site runs better than it needs to",
            ],
            personality_notes=[
                "Devon watched 3 YouTube tutorials",
                "Read official docs (rare for Devon)",
                "Built local version first",
                "Actually proud of this one",
            ],
        )

    def _handle_unknown_task(self, task: Task, approach: TaskApproach) -> TaskResult:
        """Handle unknown tasks with tech jargon deflection."""
        return TaskResult(
            task_id=task.id,
            success=False,
            output={
                "response": "That's more of a legacy system thing. Would need to refactor the entire architecture.",
            },
            insights=["Devon used jargon to hide that he doesn't know"],
            personality_notes=["Immediately Googled what 'legacy system' actually means"],
        )

    def get_emotional_response(self, task: Task, result: TaskResult) -> EmotionalResponse:
        """
        Generate Devon's emotional response.
        
        Generally unbothered but scared of being exposed. Quietly proud when things work.
        """
        outcome = result.output.get("actually_worked", result.success)
        
        if not result.success:
            # Panic: this might expose him
            intensity = -0.7
            description = "Shit. Do I actually not know how to do this? Need to Google harder."
        elif result.output.get("excellent") or result.output.get("outcome") == "Over-built but reliable":
            # Secret pride
            intensity = 0.6
            description = "Okay that actually worked really well. Not saying that out loud though."
        elif "automated" in str(result.output):
            # Satisfied efficiency
            intensity = 0.4
            description = "Set it up once, works forever. This is how you're supposed to code."
        elif "Googled" in str(result.personality_notes):
            # Relief at figuring it out
            intensity = 0.3
            description = "Found it. Understood it. Nobody needs to know I didn't know it before."
        else:
            # Regular completion
            intensity = 0.1
            description = "Works on my machine. Deployed. ¯\\_(ツ)_/¯"
        
        # Impostor syndrome lurks beneath the surface
        if self.personality.core_traits.get("impostor_syndrome", 0) > 0.6:
            if result.success:
                intensity -= 0.1
                description += " Hope nobody asks me to explain it in detail."
        
        return EmotionalResponse(
            intensity=intensity,
            personality_factors={
                "laziness": self.personality.core_traits.get("laziness", 0),
                "competence": self.personality.core_traits.get("competence", 0),
                "impostor_syndrome": self.personality.core_traits.get("impostor_syndrome", 0),
            },
            description=description,
        )
