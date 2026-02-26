from pathlib import Path
import tempfile

import pytest

from backend.orchestrator import RecipeOrchestrator


def test_pipeline_stops_when_photography_stage_fails(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        monkeypatch.delenv("STABILITY_API_KEY", raising=False)

        orchestrator = RecipeOrchestrator(
            data_dir=tmp / "output",
            message_storage=tmp / "messages",
            memory_storage=tmp / "memories",
        )

        with pytest.raises(RuntimeError, match="STABILITY_API_KEY"):
            orchestrator.produce_recipe("Fail Fast Test Muffins")
