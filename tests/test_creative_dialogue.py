"""Hermetic story rendering for the screenwriter dialogue feed."""

from backend.data.recipe import CreationStory
from backend.orchestrator import RecipeOrchestrator


def test_story_contains_screenwriter_dialogue_feed(tmp_path):
    orchestrator = RecipeOrchestrator(
        data_dir=tmp_path / "output",
        message_storage=tmp_path / "messages",
        memory_storage=tmp_path / "memories",
    )
    story = CreationStory(
        story_id="story",
        recipe_id="recipe",
        title="How We Made: Savory Zucchini Cornbread Bites",
        summary="",
        full_story="",
        personality_highlights=[
            "Screenwriter: Margaret challenged Steph's first idea.",
            "Screenwriter: Steph found the final direction.",
        ],
    )
    story.add_contribution(
        agent_name="Margaret Chen",
        agent_role="baker",
        contribution_type="Recipe Development",
        personality_moments=["Margaret insisted on a crisp edge."],
    )
    orchestrator.current_story = story

    orchestrator._finalize_story()

    text = story.full_story.lower()
    assert "how we made this recipe" in text
    assert "writers room feed" in text
    assert "screenwriter" in text
    assert "margaret" in text
    assert "steph" in text
