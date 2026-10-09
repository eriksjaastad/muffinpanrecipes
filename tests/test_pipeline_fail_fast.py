from pathlib import Path
import tempfile

import pytest

from backend.orchestrator import RecipeOrchestrator


def test_pipeline_stops_when_photography_stage_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

        orchestrator = RecipeOrchestrator(
            data_dir=tmp / "output",
            message_storage=tmp / "messages",
            memory_storage=tmp / "memories",
        )
        # The art director writes its jobs file under its repo root before it
        # checks the key; keep it out of the repo's data/ (#8169).
        monkeypatch.setattr(orchestrator.agents["art_director"], "_repo_root", lambda: tmp)

        with pytest.raises(RuntimeError, match="GOOGLE_API_KEY"):
            orchestrator.produce_recipe("Fail Fast Test Muffins")
