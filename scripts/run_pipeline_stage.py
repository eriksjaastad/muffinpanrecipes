#!/usr/bin/env python3
"""Trigger a single pipeline stage for an episode (compressed or production)."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPISODES_DIR = ROOT / "data" / "episodes"

# Ensure project root is on path so backend.* and scripts.* resolve correctly
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.recipe import CreationStory  # noqa: E402
from backend.orchestrator import RecipeOrchestrator  # noqa: E402
from backend.utils.discord import notify_pipeline_failure, notify_recipe_ready  # noqa: E402
from scripts.simulate_dialogue_week import run_simulation  # noqa: E402


STAGE_TO_ROLE = {
    "monday": "brainstorm",
    "tuesday": "recipe_development",
    "wednesday": "photography",
    "thursday": "copywriting",
    "friday": "final_review",
    "saturday": "deployment",
    "sunday": "publish",
}

# Default model used for dialogue generation via run_simulation()
DIALOGUE_MODEL = "ollama/qwen3:32b"


def load_episode(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {
        "episode_id": None,
        "created_at": datetime.now().isoformat(),
        "stages": {},
        "events": [],
        "recipe_id": None,
        "concept": None,
        "dry_run": False,
    }


def save_episode(path: Path, ep: dict) -> None:
    path.write_text(json.dumps(ep, indent=2))


def _bootstrap_orchestrator(orchestrator: RecipeOrchestrator, recipe_id: str, concept: str) -> None:
    """Ensure orchestrator has pipeline context and current_story for standalone stage runs."""
    if recipe_id not in orchestrator.pipeline.active_recipes:
        orchestrator.pipeline.start_recipe(recipe_id, concept)

    if orchestrator.current_story is None:
        story_id = str(uuid.uuid4())[:8]
        orchestrator.current_story = CreationStory(
            story_id=story_id,
            recipe_id=recipe_id,
            title=f"How We Made: {concept}",
            summary="",
            full_story="",
        )
    orchestrator.current_recipe_id = recipe_id


def _generate_stage_dialogue(stage: str, concept: str, image_paths: list[str] | None = None) -> list[dict]:
    """Run the dialogue simulator for a single stage and return messages list."""
    try:
        result = run_simulation(
            concept=concept,
            default_model=DIALOGUE_MODEL,
            run_index=1,
            stage_only=stage,
            injected_event=None,
            ticks_per_day=0,       # 0 = use variable TICKS_RANGE per day (natural variation)
            mode="ollama",
            prompt_style="scene",
            character_models=None,
            image_paths=image_paths or [],
        )
        return result.get("messages", [])
    except Exception as e:
        # Dialogue failure is non-fatal — log and continue
        print(f"  [dialogue] Warning: simulation failed for stage '{stage}': {e}")
        return []


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=list(STAGE_TO_ROLE.keys()))
    parser.add_argument("--episode", required=True, help="e.g., 2026-W09")
    parser.add_argument("--concept", default=None, help="Recipe concept (required for monday stage)")
    parser.add_argument("--notify", action="store_true", help="Send Discord notifications on success")
    parser.add_argument("--dry-run", action="store_true", help="Skip publishing and git push; no success notifications")
    parser.add_argument("--fail", action="store_true", help="Test failure path")
    args = parser.parse_args()

    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    ep_path = EPISODES_DIR / f"{args.episode}.json"
    ep = load_episode(ep_path)
    ep["episode_id"] = args.episode
    if args.dry_run:
        ep["dry_run"] = True

    dry_run = ep.get("dry_run", False)
    prefix = "[DRY RUN] " if dry_run else ""

    # Lazy orchestrator init — data_dir=EPISODES_DIR.parent points to data/
    orchestrator = RecipeOrchestrator(data_dir=EPISODES_DIR.parent)

    # Ensure recipe_id exists
    if not ep.get("recipe_id"):
        ep["recipe_id"] = str(uuid.uuid4())[:8]

    recipe_id: str = ep["recipe_id"]

    # Concept: prefer CLI arg, fall back to stored episode concept
    concept: str = args.concept or ep.get("concept") or "Weekly Muffin Pan Recipe"
    ep["concept"] = concept

    stage_key = args.stage
    role = STAGE_TO_ROLE[stage_key]

    print(f"{prefix}Stage: {stage_key} → {role} | episode: {args.episode} | recipe_id: {recipe_id}")
    print(f"{prefix}Concept: {concept}")

    stage_entry: dict = {
        "stage": role,
        "started_at": datetime.now().isoformat(),
        "status": "in_progress",
        "concept": concept,
    }
    ep["stages"][stage_key] = stage_entry

    try:
        if args.fail:
            raise RuntimeError("forced failure for alert path testing")

        # Bootstrap orchestrator pipeline context for standalone stage calls
        _bootstrap_orchestrator(orchestrator, recipe_id, concept)

        if stage_key == "monday":
            result = orchestrator._execute_stage_baker(recipe_id, concept)
            stage_entry["recipe_data"] = result

        elif stage_key == "tuesday":
            # Tuesday = Recipe Development. The orchestrator does not have a dedicated
            # _execute_stage_recipe_dev() yet — dialogue is generated by the simulator
            # below. Store recipe_data reference so later stages can access it.
            recipe_data = ep["stages"].get("monday", {}).get("recipe_data", {})
            stage_entry["recipe_data_ref"] = "from monday stage"
            print(f"{prefix}Tuesday: recipe development (dialogue via simulator)")

        elif stage_key == "wednesday":
            # Wednesday = Photography: generate 3 image variants, capture paths
            recipe_data = ep["stages"].get("monday", {}).get("recipe_data", {})
            result = orchestrator._execute_stage_photography(recipe_id, recipe_data)
            # result is a list of selected shot paths from the art director
            image_paths: list[str] = result if isinstance(result, list) else []
            stage_entry["photography_data"] = result
            stage_entry["image_paths"] = image_paths
            # Store on episode for later stages to reference
            ep["image_paths"] = image_paths

        elif stage_key == "thursday":
            recipe_data = ep["stages"].get("monday", {}).get("recipe_data", {})
            result = orchestrator._execute_stage_copywriting(recipe_id, concept, recipe_data)
            stage_entry["copy_text"] = result

        elif stage_key == "friday":
            approved, review_output = orchestrator._execute_stage_review(recipe_id)
            stage_entry["review_data"] = review_output
            stage_entry["approved"] = approved
            # Note: _record_creative_dialogue() is now a no-op (deprecated).
            # Real Friday dialogue is generated by _generate_stage_dialogue() below.

        elif stage_key == "saturday":
            orchestrator._execute_stage_deployment(recipe_id)
            stage_entry["deployment_status"] = "staged"

        elif stage_key == "sunday":
            ep["published_at"] = datetime.now().isoformat()
            stage_entry["published"] = True
            if not dry_run:
                print(f"Publishing episode {args.episode}...")
            else:
                print(f"{prefix}Skipping actual publish (dry-run mode).")

        # --- Generate real dialogue for this stage via simulate_dialogue_week ---
        # Wednesday gets image paths passed in so Julian's messages include photo attachments.
        # For all other stages, image_paths is empty (no attachments).
        # Dialogue generation is non-fatal: failure is logged, stage still completes.
        print(f"{prefix}Generating dialogue for {stage_key}...")
        stage_image_paths = stage_entry.get("image_paths") or ep.get("image_paths") or []
        dialogue_messages = _generate_stage_dialogue(
            stage_key,
            concept,
            image_paths=stage_image_paths if stage_key == "wednesday" else None,
        )
        if dialogue_messages:
            stage_entry["dialogue"] = dialogue_messages
            print(f"{prefix}Dialogue: {len(dialogue_messages)} message(s) generated")
            if stage_key == "wednesday" and stage_image_paths:
                attached_count = sum(1 for m in dialogue_messages if m.get("attachments"))
                print(f"{prefix}Dialogue: {attached_count} message(s) contain image attachments")
        else:
            print(f"{prefix}Dialogue: skipped (no messages returned)")

        # Mark stage complete
        stage_entry["status"] = "complete"
        stage_entry["completed_at"] = datetime.now().isoformat()
        ep["events"].append(f"{stage_key}: complete")
        save_episode(ep_path, ep)

        # Discord success notification (only if --notify and not dry-run)
        if args.notify and not dry_run:
            notify_recipe_ready(
                recipe_title=f"{concept} [{args.episode}]",
                recipe_id=f"{args.episode}-{stage_key}",
                description_preview=f"Stage complete: {role}",
                ingredient_count=0,
            )

        print(f"{prefix}Stage complete: {stage_key} → {role}")
        print(f"{prefix}Episode file: {ep_path}")

    except Exception as e:
        stage_entry["status"] = "failed"
        stage_entry["error"] = str(e)
        stage_entry["failed_at"] = datetime.now().isoformat()
        ep["events"].append(f"{stage_key}: failed — {e}")
        save_episode(ep_path, ep)

        notify_pipeline_failure(
            recipe_id=f"{args.episode}-{stage_key}",
            concept=concept,
            stage=stage_key,
            error_message=str(e),
        )
        raise


if __name__ == "__main__":
    main()
