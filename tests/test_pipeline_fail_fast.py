from pathlib import Path
import tempfile

import pytest

from backend.agents import baker as baker_module
from backend.orchestrator import RecipeOrchestrator


def _recipe(concept, **_kwargs):
    """The baker's recipe call succeeds, so the run reaches photography. It
    used to get there on the baker's canned fallback recipe, removed in #7988."""
    return {
        "title": concept,
        "pitch": "",
        "description": "",
        "category": "sweet",
        "ingredients": [{"item": "flour", "amount": "2 cups", "notes": ""}],
        "instructions": ["Preheat the oven to 375F.", "Bake until set."],
        "chef_notes": "",
    }


def test_pipeline_stops_when_photography_stage_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr(baker_module, "generate_recipe", _recipe)

        orchestrator = RecipeOrchestrator(
            data_dir=tmp / "output",
            message_storage=tmp / "messages",
            memory_storage=tmp / "memories",
        )
        # The art director writes its jobs file under its repo root before it
        # checks the key; keep it out of the repo's data/ (#8169).
        monkeypatch.setattr(orchestrator.agents["art_director"], "_repo_root", lambda: tmp)

        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            orchestrator.produce_recipe("Fail Fast Test Muffins")
