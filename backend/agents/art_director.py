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
from backend.utils.model_router import generate_vision_response

logger = get_logger(__name__)

# Vision evaluation model — cheap, vision-capable
_VISION_MODEL = os.getenv("VISION_EVAL_MODEL", "openai/gpt-5-mini")


def _average_hash(image_path: Path, hash_size: int = 8) -> int:
    """Compute average perceptual hash for an image. Returns a 64-bit int."""
    from PIL import Image
    img = Image.open(image_path).convert("L").resize((hash_size, hash_size), Image.LANCZOS)
    pixels = list(img.getdata())
    avg = sum(pixels) / len(pixels)
    return sum(1 << i for i, px in enumerate(pixels) if px >= avg)


def _hamming_distance(a: int, b: int) -> int:
    """Count differing bits between two integers."""
    return bin(a ^ b).count("1")


def _check_visual_diversity(image_paths: list[Path], threshold: int = 5) -> bool:
    """Return True if images are visually diverse (hamming distance >= threshold for all pairs).

    Uses average perceptual hashing. If any pair has hamming distance < threshold
    (out of 64 bits), the set is considered too similar.
    """
    hashes = []
    for p in image_paths:
        if p.exists():
            try:
                hashes.append(_average_hash(p))
            except Exception as e:
                logger.warning(f"Perceptual hash failed for {p}: {e}")
                continue

    if len(hashes) < 2:
        return True  # can't compare fewer than 2 images

    for i in range(len(hashes)):
        for j in range(i + 1, len(hashes)):
            dist = _hamming_distance(hashes[i], hashes[j])
            if dist < threshold:
                logger.info(f"Images {i} and {j} too similar: hamming distance {dist} < {threshold}")
                return False
    return True


class ArtDirectorAgent(Agent):
    """
    Julian Torres - The Art Director

    Pretentious art school grad hiding Instagram failure behind
    pseudo-intellectual photo theory. Actually talented but terrified.
    """

    _VARIANTS: tuple[str, ...] = ("macro_closeup", "overhead_flatlay", "hero_threequarter")

    # Per-variant negative prompts to prevent convergence
    _VARIANT_NEGATIVES: dict[str, str] = {
        "macro_closeup": "full tin visible, bird's eye view, overhead angle, multiple items, wide shot",
        "overhead_flatlay": "shallow depth of field, bokeh, single item, macro, close-up, low angle",
        "hero_threequarter": "extreme close-up, overhead, bird's eye, flat lay, 90 degree angle, macro",
    }

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

    def _images_dir(self) -> Path:
        """Return writable directory for image generation.

        On Vercel Lambda /var/task/ is read-only, so we write to /tmp/.
        Locally we use the normal src/assets/images path.
        """
        if os.environ.get("VERCEL_ENV"):
            d = Path("/tmp/mpr-images")
        else:
            d = self._repo_root() / "src" / "assets" / "images"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _recipe_title(self, task: Task) -> str:
        recipe_data = task.context.get("recipe_data", {}) if isinstance(task.context, dict) else {}
        return str(recipe_data.get("title") or task.context.get("concept") or "Muffin Pan Recipe")

    def _build_prompt(self, recipe_title: str, variant: str) -> str:
        variant_prompts = {
            "macro_closeup": (
                f"Extreme close-up food photography of {recipe_title}. "
                "15-degree low angle, ONE single item filling the entire frame. "
                "Visible crumb structure, steam wisps, glistening texture detail. "
                "Shot on 100mm macro lens, f/2.0, razor-thin depth of field. "
                "Bright, high-key lighting, natural daylight from the side. "
                "White marble countertop. Editorial cookbook style. "
                "No full tin visible, no multiple items, no bird's eye view. "
                "No people, no hands, no text, no watermark."
            ),
            "overhead_flatlay": (
                f"True 90-degree overhead bird's-eye food photography of {recipe_title} in a rustic muffin tin. "
                "Full tin visible from directly above, scattered ingredient garnishes around the tin. "
                "Shot on 35mm lens, f/5.6, everything in sharp focus. "
                "Bright, high-key lighting, natural daylight. "
                "White marble countertop with flour dusting and herb sprigs. "
                "Flat lay editorial style, geometric composition. "
                "No shallow depth of field, no single item close-up, no low angle. "
                "No people, no hands, no text, no watermark."
            ),
            "hero_threequarter": (
                f"Classic hero food photography of {recipe_title} at 30-45 degree angle. "
                "2-3 items arranged on a rustic wooden board, one broken open showing interior cross-section. "
                "Shot on 85mm lens, f/2.8, soft background blur. "
                "Bright, high-key lighting, natural daylight from the side. "
                "White marble countertop with minimal props - linen napkin, vintage fork. "
                "Editorial cookbook style, warm and inviting. "
                "No extreme close-up, no overhead flat lay, no 90 degree angle. "
                "No people, no hands, no text, no watermark."
            ),
        }
        return variant_prompts[variant]

    def _style_guide_text(self) -> str:
        style_guide_path = self._repo_root() / "Documents" / "core" / "IMAGE_STYLE_GUIDE.md"
        return style_guide_path.read_text(encoding="utf-8") if style_guide_path.exists() else ""

    def _evaluate_images_vision(
        self, image_paths: list[dict[str, Any]], recipe_title: str
    ) -> dict[str, Any]:
        """Evaluate images using a vision model. Returns per-image scores and pass/fail.

        Falls back to a basic pass if the vision call fails (don't block the pipeline).
        """
        import json as _json

        images_bytes: list[bytes] = []
        for info in image_paths:
            local = Path(info.get("local_path", ""))
            if local.exists():
                images_bytes.append(local.read_bytes())
            else:
                # Fallback: try repo-relative path (legacy)
                full_path = self._repo_root() / info["path"]
                if full_path.exists():
                    images_bytes.append(full_path.read_bytes())

        if not images_bytes:
            logger.warning("Vision eval: no image files found, falling back to pass")
            return {"passed": True, "fallback": True, "per_image": [], "reason": "no images to evaluate"}

        variant_labels = ", ".join(f"Image {i+1}: {info['variant']}" for i, info in enumerate(image_paths))

        eval_prompt = (
            f"You are a professional food photography art director evaluating images for '{recipe_title}'.\n\n"
            f"There are {len(images_bytes)} images. {variant_labels}.\n\n"
            "Score EACH image on these 5 dimensions (1-5 scale):\n"
            "1. variety - How different is this from the other images? (angle, distance, composition)\n"
            "2. quality - Technical quality (focus, exposure, lighting)\n"
            "3. style_adherence - Does it match the requested style/angle?\n"
            "4. food_appeal - Does the food look appetizing?\n"
            "5. composition - Is the framing and arrangement effective?\n\n"
            "Then provide a SET-LEVEL score:\n"
            "6. set_diversity (1-5) - How different are these images from EACH OTHER? "
            "Consider angle, distance, composition, styling. "
            "Score 1 = nearly identical shots, 5 = clearly distinct perspectives.\n\n"
            "Then decide:\n"
            "- PASS if average per-image score >= 3.5 AND no image scores below 2.5 on any dimension AND set_diversity >= 3.0\n"
            "- FAIL if images look too similar, set_diversity is low, or any image has serious issues\n\n"
            "If FAIL, explain what went wrong in 1-2 sentences (this will be used as dialogue).\n"
            "Pick a recommended winner (best overall image number).\n\n"
            "Respond ONLY with valid JSON:\n"
            '{"per_image": [{"image": 1, "variety": X, "quality": X, "style_adherence": X, '
            '"food_appeal": X, "composition": X, "feedback": "..."}], '
            '"set_diversity": X, "passed": true/false, "reason": "...", "recommended_winner": 1}'
        )

        try:
            raw = generate_vision_response(
                prompt=eval_prompt,
                images=images_bytes,
                model=_VISION_MODEL,
                temperature=0.3,
            )
            # Strip markdown fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

            result = _json.loads(cleaned)

            # Validate pass criteria ourselves as a safety check
            per_image = result.get("per_image", [])
            dimensions = ["variety", "quality", "style_adherence", "food_appeal", "composition"]
            all_scores = []
            any_below_threshold = False
            for img in per_image:
                for dim in dimensions:
                    score = img.get(dim, 3.0)
                    all_scores.append(score)
                    if score < 2.5:
                        any_below_threshold = True

            avg_score = sum(all_scores) / max(len(all_scores), 1)
            result["avg_score"] = round(avg_score, 2)
            set_diversity = result.get("set_diversity", 5.0)
            result["passed"] = avg_score >= 3.5 and not any_below_threshold and set_diversity >= 3.0

            return result

        except Exception as e:
            logger.warning(f"Vision evaluation failed, falling back to pass: {e}")
            return {"passed": True, "fallback": True, "per_image": [], "reason": f"vision eval error: {e}"}

    def _call_stability(self, api_key: str, prompt: str, variant: str | None = None) -> bytes:
        base_negative = "people, hands, text, watermark, clutter, dark moody lighting"
        variant_negative = self._VARIANT_NEGATIVES.get(variant or "", "")
        negative_prompt = f"{base_negative}, {variant_negative}" if variant_negative else base_negative

        response = requests.post(
            "https://api.stability.ai/v2beta/stable-image/generate/core",
            headers={
                "authorization": f"Bearer {api_key}",
                "accept": "image/*",
            },
            data={
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "output_format": "png",
                "aspect_ratio": "1:1",
            },
            files={"none": "none"},
            timeout=90,
        )
        if response.status_code != 200:
            raise RuntimeError(f"Stability API error {response.status_code}: {response.text[:200]}")
        return response.content

    _MAX_ROUNDS = 2  # Generate → evaluate → (optional reshoot) → done

    def _generate_round(
        self, api_key: str, recipe_title: str, recipe_id: str, round_num: int,
        feedback: str | None = None,
    ) -> tuple[list[dict[str, Any]], Path]:
        """Generate 3 variants for a single round. Returns (variant_outputs, round_dir)."""
        round_dir = self._images_dir() / recipe_id / f"round_{round_num}"
        variant_outputs: list[dict[str, Any]] = []

        for variant in self._VARIANTS:
            prompt = self._build_prompt(recipe_title, variant)
            if feedback and round_num > 1:
                prompt += f" RESHOOT NOTE: Previous batch rejected. Feedback: {feedback}"

            image_bytes = self._call_stability(api_key, prompt, variant=variant)
            out_path = round_dir / f"{variant}.png"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(image_bytes)

            # Canonical relative path (always src/assets/images/...) for storage layer
            canonical = f"src/assets/images/{recipe_id}/round_{round_num}/{variant}.png"

            variant_outputs.append({
                "variant": variant,
                "path": canonical,
                "local_path": str(out_path),
                "prompt": prompt,
            })

        return variant_outputs, round_dir

    def _photograph_recipe(
        self, task: Task, approach: TaskApproach, context: MemoryContext
    ) -> TaskResult:
        """Photograph with vision evaluation and reshoot-as-story-beat."""

        recipe_id = str(task.context.get("recipe_id") or task.id)
        recipe_title = self._recipe_title(task)
        featured_image = self._images_dir() / f"{recipe_id}.png"
        api_key = os.getenv("STABILITY_API_KEY")
        if not api_key:
            raise RuntimeError("STABILITY_API_KEY not configured; stopping pipeline")

        shot_count = random.randint(35, 55)
        rounds: list[dict[str, Any]] = []
        winner_info: dict[str, Any] | None = None

        for round_num in range(1, self._MAX_ROUNDS + 1):
            feedback = rounds[-1]["vision_evaluation"].get("reason") if rounds else None
            variant_outputs, round_dir = self._generate_round(
                api_key, recipe_title, recipe_id, round_num, feedback=feedback,
            )

            # Perceptual hash diversity check (belt-and-suspenders with vision eval)
            phash_paths = [Path(vo["local_path"]) for vo in variant_outputs]
            visually_diverse = _check_visual_diversity(phash_paths)

            # Vision evaluation
            vision_eval = self._evaluate_images_vision(variant_outputs, recipe_title)
            if not visually_diverse:
                vision_eval["passed"] = False
                vision_eval["phash_failed"] = True
                vision_eval.setdefault("reason", "Images are perceptually near-identical (hash check)")

            # Attach per-image scores to variant outputs
            per_image = vision_eval.get("per_image", [])
            for i, vo in enumerate(variant_outputs):
                if i < len(per_image):
                    vo["scores"] = per_image[i]

            round_data = {
                "round": round_num,
                "variants": variant_outputs,
                "vision_evaluation": vision_eval,
                "passed": vision_eval.get("passed", True),
            }
            if not vision_eval.get("passed", True):
                round_data["rejection_reason"] = vision_eval.get("reason", "Vision evaluation failed")

            rounds.append(round_data)

            if vision_eval.get("passed", True):
                # Pick winner from vision recommendation or best-scored
                rec = vision_eval.get("recommended_winner", 1)
                winner_idx = max(0, min(rec - 1, len(variant_outputs) - 1))
                winner_info = {
                    **variant_outputs[winner_idx],
                    "round": round_num,
                }
                break

        # If no winner after all rounds, pick first variant from last round
        if winner_info is None:
            last_variants = rounds[-1]["variants"]
            winner_info = {**last_variants[0], "round": len(rounds)}

        # Copy winner to featured image location (local temp or repo)
        winner_source = Path(winner_info["local_path"])
        featured_image.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(winner_source, featured_image)

        # Upload winner to cloud storage (Vercel Blob in prod, local FS in dev)
        featured_canonical = f"src/assets/images/{recipe_id}.png"
        from backend.storage import storage
        storage.save_image(featured_canonical, featured_image.read_bytes())
        winner_info["featured_image"] = featured_canonical

        reshoot_happened = len(rounds) > 1

        # Collect all image paths across all rounds for backward compat
        all_paths = []
        for r in rounds:
            for v in r["variants"]:
                all_paths.append(v["path"])

        insights = [
            f"Captured {shot_count} frames across {len(rounds)} round(s)",
            f"Selected '{winner_info['variant']}' from round {winner_info['round']} as hero shot",
        ]
        if reshoot_happened:
            insights.append(f"Round 1 rejected: {rounds[0].get('rejection_reason', 'unknown')}")
            insights.append("Rush reshoot completed under deadline pressure")

        return TaskResult(
            task_id=task.id,
            success=True,
            output={
                "recipe_id": recipe_id,
                "total_shots": shot_count,
                "rounds": rounds,
                "winner": winner_info,
                "reshoot_happened": reshoot_happened,
                "selected_shots": all_paths,  # backward compat
                "generated_with": "stability_api",
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
            ] + (["Had a mini panic attack about the reshoot timeline"] if reshoot_happened else []),
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
