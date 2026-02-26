"""
Art Director Agent - Julian Torres

26yo RISD grad and failed Instagram influencer. Talks about "visual language"
and "negative space." Takes 47 shots to get crumb structure right. Both
pretentious artist and desperate for validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import random
import shutil

import requests

from backend.core.agent import Agent
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

    _VARIANTS: tuple[str, ...] = ("editorial", "action_steam", "the_spread")

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

    def _repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def _recipe_title(self, task: Task) -> str:
        recipe_data = task.context.get("recipe_data", {}) if isinstance(task.context, dict) else {}
        return str(recipe_data.get("title") or task.context.get("concept") or "Muffin Pan Recipe")

    def _build_prompt(self, recipe_title: str, variant: str) -> str:
        base = (
            f"Professional food photography of {recipe_title} served in a rustic muffin tin. "
            "Bright, high-key lighting, natural daylight coming from the side. "
            "Soft shadows on a white marble countertop. "
            "Shot on 85mm macro lens, f/2.8. Editorial cookbook style, clean, minimalist, highly appetizing. "
            "No people, no hands, no text, no watermark, no clutter, no dark moody lighting."
        )

        variant_additions = {
            "editorial": (
                " 45-degree angle, tight crop, macro detail on top texture and crumb structure. "
                "Single hero item with subtle negative space."
            ),
            "action_steam": (
                " Low side angle emphasizing architecture and layering, visible gentle steam plume from fresh bake, "
                "still clean editorial styling with minimal props."
            ),
            "the_spread": (
                " 45-degree overhead spread with 3-4 pieces in a rustic weathered muffin tin, "
                "light dusting of flour and one subtle herb accent, uncluttered composition."
            ),
        }
        return base + variant_additions[variant]

    def _style_guide_text(self) -> str:
        style_guide_path = self._repo_root() / "Documents" / "core" / "IMAGE_STYLE_GUIDE.md"
        return style_guide_path.read_text(encoding="utf-8") if style_guide_path.exists() else ""

    def _score_variant(self, prompt: str, variant: str, style_guide_text: str) -> tuple[int, str]:
        normalized = prompt.lower()
        score = 0
        hits: list[str] = []
        criteria = [
            ("high-key", "high-key lighting"),
            ("natural daylight", "natural daylight"),
            ("side", "side lighting"),
            ("white marble", "white marble surface"),
            ("rustic muffin tin", "rustic muffin tin"),
            ("85mm", "85mm macro lens"),
            ("f/2.8", "f/2.8 shallow depth"),
            ("no people", "no people"),
            ("no text", "no text/watermark"),
            ("no clutter", "minimal clean styling"),
        ]
        for keyword, label in criteria:
            if keyword in normalized:
                score += 10
                hits.append(label)

        if variant == "editorial":
            score += 5
            hits.append("strong hero-frame emphasis")

        if "cross-section" in style_guide_text.lower() and "layering" in normalized:
            score += 5
            hits.append("structural emphasis")

        rationale = f"Matched {len(hits)} style guide cues: {', '.join(hits[:5])}{'...' if len(hits) > 5 else ''}."
        return score, rationale

    def _call_stability(self, api_key: str, prompt: str) -> bytes:
        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/core",
            headers={
                "authorization": f"Bearer {api_key}",
                "accept": "image/*",
            },
            data={
                "prompt": prompt,
                "negative_prompt": "people, hands, text, watermark, clutter, dark moody lighting",
                "output_format": "png",
                "aspect_ratio": "1:1",
            },
            files={"none": "none"},
            timeout=90,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Stability API error {response.status_code}: {response.text[:200]}")
        return response.content

    def _photograph_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Photograph with excessive perfectionism and pretentious commentary."""

        recipe_id = str(task.context.get("recipe_id") or task.id)
        recipe_title = self._recipe_title(task)
        images_dir = self._repo_root() / "data" / "images" / recipe_id
        featured_image = self._repo_root() / "src" / "assets" / "images" / f"{recipe_id}.png"
        style_guide_text = self._style_guide_text()
        api_key = os.getenv("STABILITY_API_KEY")
        if not api_key:
            raise RuntimeError("STABILITY_API_KEY not configured; stopping pipeline")

        shot_count = random.randint(35, 55)
        generated_with = "stability_api"
        variant_outputs: list[dict[str, Any]] = []

        for variant in self._VARIANTS:
            prompt = self._build_prompt(recipe_title, variant)
            out_path = images_dir / f"{variant}.png"
            score, rationale = self._score_variant(prompt, variant, style_guide_text)

            image_bytes = self._call_stability(api_key, prompt)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(image_bytes)

            variant_outputs.append(
                {
                    "variant": variant,
                    "path": str(out_path.relative_to(self._repo_root())),
                    "prompt": prompt,
                    "style_score": score,
                    "rationale": rationale,
                }
            )

        winner = max(variant_outputs, key=lambda item: (item["style_score"], -self._VARIANTS.index(item["variant"])))
        winner_source = self._repo_root() / winner["path"]
        featured_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(winner_source, featured_image)

        insights = [
            f"Captured {shot_count} frames to get the crumb structure right (emotionally necessary, obviously)",
            f"Generated 3 styled variants in data/images/{recipe_id}/ and selected '{winner['variant']}' against IMAGE_STYLE_GUIDE",
            f"Winner rationale: {winner['rationale']}",
        ]
        return TaskResult(
            task_id=task.id,
            success=True,
            output={
                "recipe_id": recipe_id,
                "total_shots": shot_count,
                "selected_shots": [v["path"] for v in variant_outputs],
                "variants": variant_outputs,
                "winner": {
                    "variant": winner["variant"],
                    "path": winner["path"],
                    "style_score": winner["style_score"],
                    "rationale": winner["rationale"],
                    "featured_image": str(featured_image.relative_to(self._repo_root())),
                },
                "generated_with": generated_with,
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
                "outcome": "exceptional_quality",
            },
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
            "verdict": "approved_with_creative_reservations",
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
            "julian_special": [],
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
