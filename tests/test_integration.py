"""Hermetic integration tests for the legacy five-agent orchestrator.

The stage boundaries are deterministic fakes: this suite verifies orchestration,
pipeline, persistence, memory, messaging, and story assembly without network or
provider credentials. Model quality belongs in the explicit OAuth eval harness.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.core.agent import Message
from backend.core.task import Task, TaskResult
from backend.core.types import EmotionalResponse, MessageType
from backend.data.recipe import CreationStory, Recipe, RecipeStatus
from backend.orchestrator import RecipeOrchestrator
from backend.pipeline.recipe_pipeline import PipelineStage


def _orchestrator(tmp_path: Path) -> RecipeOrchestrator:
    return RecipeOrchestrator(
        data_dir=tmp_path / "output",
        message_storage=tmp_path / "messages",
        memory_storage=tmp_path / "memories",
    )


def _install_hermetic_stages(monkeypatch, orchestrator: RecipeOrchestrator) -> None:
    """Replace paid stage work while preserving the real orchestration path."""

    def contribute(role: str, kind: str, moment: str) -> None:
        assert orchestrator.current_story is not None
        agent = orchestrator.agents[role]
        orchestrator.current_story.add_contribution(
            agent_name=agent.personality.name,
            agent_role=role,
            contribution_type=kind,
            decisions=[f"Completed {kind.lower()}"],
            personality_moments=[moment],
        )

    def baker(_recipe_id: str, concept: str) -> dict:
        agent = orchestrator.agents["baker"]
        task = Task(type="create_recipe", content=concept)
        result = TaskResult(task_id=task.id, success=True, output={"title": concept})
        agent.memory.record_experience(
            task,
            result,
            EmotionalResponse(
                intensity=-0.2,
                personality_factors={"traditionalism": 0.8},
                description=f"Margaret completed {concept}",
            ),
        )
        contribute("baker", "Recipe Development", "Margaret muttered about trendy ingredients")
        return {
            "title": concept,
            "description": f"A muffin-tin recipe for {concept}.",
            "servings": 12,
            "prep_time": 15,
            "cook_time": 20,
            "difficulty": "easy",
            "category": "savory",
            "ingredients": [{"item": "flour", "amount": "2 cups", "notes": ""}],
            "instructions": ["Divide into muffin cups.", "Bake until golden."],
        }

    def photography(_recipe_id: str, _recipe_data: dict) -> dict:
        contribute("art_director", "Photography", "Julian adjusted the hero angle")
        return {"selected_shots": ["hero.png"]}

    def copywriting(_recipe_id: str, _concept: str, _recipe_data: dict) -> dict:
        contribute("copywriter", "Editorial Copy", "Marcus used whom twice")
        return {"body": "A focused recipe introduction."}

    def review(_recipe_id: str, _recipe_data: dict, _copy: dict) -> tuple[bool, dict]:
        contribute("creative_director", "Creative Review", "Steph approved with cautious confidence")
        return True, {"verdict": "APPROVED"}

    def deployment(_recipe_id: str) -> None:
        contribute("site_architect", "Deployment", "Devon automated the release")

    monkeypatch.setattr(orchestrator, "_execute_stage_baker", baker)
    monkeypatch.setattr(orchestrator, "_execute_stage_photography", photography)
    monkeypatch.setattr(orchestrator, "_execute_stage_copywriting", copywriting)
    monkeypatch.setattr(orchestrator, "_execute_stage_review", review)
    monkeypatch.setattr(orchestrator, "_execute_stage_deployment", deployment)
    monkeypatch.setattr("backend.orchestrator.notify_recipe_ready", lambda **_kwargs: None)


def test_complete_recipe_production_workflow(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    _install_hermetic_stages(monkeypatch, orchestrator)
    concept = "Savory Breakfast Muffins with Sausage and Cheddar"

    recipe, story = orchestrator.produce_recipe(concept)

    assert recipe.title == concept
    assert recipe.concept == concept
    assert recipe.ingredients and recipe.instructions
    assert story.recipe_id == recipe.recipe_id
    assert {item.agent_role for item in story.agent_contributions} == {
        "baker", "art_director", "copywriter", "creative_director", "site_architect",
    }
    recipe_file = tmp_path / "output" / "recipes" / "pending" / f"{recipe.recipe_id}.json"
    story_file = tmp_path / "output" / "stories" / f"story_{story.story_id}.json"
    assert recipe_file.exists() and story_file.exists()
    assert Recipe.load_from_file(recipe_file).status == RecipeStatus.PENDING


def test_agent_personalities_persist_through_production(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    _install_hermetic_stages(monkeypatch, orchestrator)

    _, story = orchestrator.produce_recipe("Matcha Green Tea Muffins with Edible Flowers")

    baker = next(item for item in story.agent_contributions if item.agent_role == "baker")
    assert "mutter" in " ".join(baker.personality_moments).lower()
    assert "Margaret" in story.full_story


def test_agent_memory_evolution(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    _install_hermetic_stages(monkeypatch, orchestrator)
    baker_memory = orchestrator.agents["baker"].memory

    orchestrator.produce_recipe("Classic Blueberry Muffins")
    after_first = len(baker_memory.emotional_responses)
    orchestrator.produce_recipe("Chocolate Chip Muffins")

    assert after_first == 1
    assert len(baker_memory.emotional_responses) == 2


def test_message_history_is_reflected_in_the_saved_story(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    _install_hermetic_stages(monkeypatch, orchestrator)
    orchestrator.message_system.send_message(Message(
        id="hermetic-message",
        sender="baker",
        recipient="copywriter",
        content="Keep the crumb description precise.",
        message_type=MessageType.CREATIVE_SUGGESTION,
        timestamp=datetime.now(timezone.utc).isoformat(),
    ))

    _, story = orchestrator.produce_recipe("Savory Herb Muffins")

    assert story.total_messages == 1
    assert orchestrator.message_system.get_statistics()["total_messages"] == 1


def test_agent_profiles_can_be_saved_without_producing_a_recipe(tmp_path):
    orchestrator = _orchestrator(tmp_path)

    orchestrator.save_agent_profiles()

    profiles = list((tmp_path / "output" / "agent_profiles").glob("profile_*.json"))
    assert len(profiles) == 5


def test_pipeline_completion(tmp_path, monkeypatch):
    orchestrator = _orchestrator(tmp_path)
    _install_hermetic_stages(monkeypatch, orchestrator)

    recipe, _story = orchestrator.produce_recipe("Pipeline Test Muffins")

    assert orchestrator.pipeline.active_recipes == {}
    assert len(orchestrator.pipeline.completed_recipes) == 1
    completed = orchestrator.pipeline.completed_recipes[0]
    assert completed.recipe_id == recipe.recipe_id
    assert completed.current_stage == PipelineStage.COMPLETE
    assert completed.completed_at is not None


@pytest.mark.parametrize("failure_point", ["compile", "recipe_save", "story_save"])
def test_pipeline_stays_deployable_until_both_outputs_persist(tmp_path, monkeypatch, failure_point):
    orchestrator = _orchestrator(tmp_path)
    _install_hermetic_stages(monkeypatch, orchestrator)
    failure = RuntimeError(f"{failure_point} failed")

    def fail(*_args, **_kwargs):
        raise failure

    if failure_point == "compile":
        monkeypatch.setattr(orchestrator, "_compile_recipe", fail)
    elif failure_point == "recipe_save":
        monkeypatch.setattr(Recipe, "save_to_file", fail)
    else:
        monkeypatch.setattr(CreationStory, "save_to_file", fail)

    alerts: list[dict] = []
    monkeypatch.setattr("backend.orchestrator.notify_pipeline_failure", lambda **data: alerts.append(data))

    with pytest.raises(RuntimeError, match=f"{failure_point} failed"):
        orchestrator.produce_recipe("Persistence Failure Muffins")

    assert orchestrator.pipeline.completed_recipes == []
    assert len(orchestrator.pipeline.active_recipes) == 1
    active = next(iter(orchestrator.pipeline.active_recipes.values()))
    assert active.current_stage == PipelineStage.DEPLOYMENT
    assert alerts[0]["stage"] == "deployment"
