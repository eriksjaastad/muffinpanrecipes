#!/usr/bin/env python3
"""Run a single recipe through the full local pipeline (produce -> approve -> publish)."""

from pathlib import Path

from backend.orchestrator import RecipeOrchestrator
from backend.data.recipe import Recipe, RecipeStatus
from backend.publishing.pipeline import PublishingPipeline


def main() -> int:
    concept = "Crispy Hash Brown Egg Cups with Cheddar"
    project_root = Path(__file__).resolve().parent.parent

    orchestrator = RecipeOrchestrator(
        data_dir=project_root / "data",
        message_storage=project_root / "data" / "messages",
        memory_storage=project_root / "data" / "agent_memories",
    )

    recipe, story = orchestrator.produce_recipe(concept)
    print(f"Produced recipe: {recipe.recipe_id} | {recipe.title}")

    # Human-review simulation for local E2E verification
    pending_path = project_root / "data" / "recipes" / "pending" / f"{recipe.recipe_id}.json"
    loaded = Recipe.load_from_file(pending_path)
    loaded.transition_status(
        RecipeStatus.APPROVED,
        project_root / "data" / "recipes",
        notes="Approved via local E2E runner",
    )
    print(f"Approved recipe: {recipe.recipe_id}")

    publisher = PublishingPipeline(project_root=project_root, auto_commit=False, auto_push=False)
    published = publisher.publish_recipe(recipe.recipe_id, send_notification=False)
    print(f"Published: {published}")

    print(f"Story file: data/stories/story_{story.story_id}.json")
    return 0 if published else 1


if __name__ == "__main__":
    raise SystemExit(main())
