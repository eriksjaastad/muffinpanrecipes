from pathlib import Path
import tempfile

from backend.orchestrator import RecipeOrchestrator


def test_story_contains_screenwriter_dialogue_feed():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        orchestrator = RecipeOrchestrator(
            data_dir=tmp / "output",
            message_storage=tmp / "messages",
            memory_storage=tmp / "memories",
        )

        _, story = orchestrator.produce_recipe("Savory Zucchini Cornbread Bites")

        text = story.full_story.lower()
        assert "writers room feed" in text
        assert "screenwriter" in text
        assert "margaret" in text
        assert "steph" in text
