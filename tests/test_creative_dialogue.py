import os
from pathlib import Path
import tempfile

import pytest

from backend.orchestrator import RecipeOrchestrator

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_PROVIDER_TESTS", "").lower() != "true",
    reason="Requires API keys. Set RUN_LIVE_PROVIDER_TESTS=true to run.",
)


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
        assert "how we made this recipe" in text
        assert "margaret" in text
        assert "steph" in text
        if "writers room feed" in text:
            assert "screenwriter" in text
