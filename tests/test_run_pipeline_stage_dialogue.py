"""A dialogue simulation failure must fail the stage, not complete it with
"Dialogue: skipped" (silent-failure sweep #7587, same ruling as #6856:
the dialogue IS the product). No real Blob, orchestrator, or alerts."""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

rps = importlib.import_module("scripts.run_pipeline_stage")


def _run_tuesday(simulation):
    episode = {
        "episode_id": "2026-W18",
        "recipe_id": "abcd1234",
        "concept": "Hash brown cups",
        "stages": {"monday": {"status": "complete", "recipe_data": {"title": "Hash Brown Cups"}}},
        "events": [],
    }
    saved: list[dict] = []
    notify_failure = MagicMock()
    notify_ready = MagicMock()

    def fake_save(episode_id, ep):
        # Snapshot the stage status at each save; the dict is mutated later.
        saved.append({"episode_id": episode_id, "tuesday": dict(ep["stages"]["tuesday"])})

    argv = ["run_pipeline_stage.py", "--stage", "tuesday", "--episode", "2026-W18",
            "--concept", "Hash brown cups"]
    with patch.object(rps, "load_episode", return_value=episode), \
         patch.object(rps, "RecipeOrchestrator", MagicMock()), \
         patch.object(rps, "_bootstrap_orchestrator"), \
         patch.object(rps, "run_simulation", simulation), \
         patch.object(rps, "save_episode", fake_save), \
         patch.object(rps, "notify_pipeline_failure", notify_failure), \
         patch.object(rps, "notify_recipe_ready", notify_ready), \
         patch.object(sys, "argv", argv):
        try:
            rps.main()
            error = None
        except Exception as exc:
            error = exc
    return episode, saved, notify_failure, notify_ready, error


def test_simulation_exception_fails_the_stage_and_notifies():
    episode, saved, notify_failure, notify_ready, error = _run_tuesday(
        MagicMock(side_effect=RuntimeError("provider down"))
    )

    assert isinstance(error, RuntimeError) and "provider down" in str(error)
    stage = episode["stages"]["tuesday"]
    assert stage["status"] == "failed"
    assert "provider down" in stage["error"]
    assert "dialogue" not in stage
    assert "tuesday: complete" not in episode["events"]
    assert [s["tuesday"]["status"] for s in saved] == ["failed"]
    notify_failure.assert_called_once()
    assert notify_failure.call_args.kwargs["stage"] == "tuesday"
    notify_ready.assert_not_called()


def test_successful_simulation_still_completes_the_stage():
    episode, saved, notify_failure, _ready, error = _run_tuesday(
        MagicMock(return_value={"messages": [{"character": "Margaret", "message": "Crisp edges."}]})
    )

    assert error is None
    assert episode["stages"]["tuesday"]["status"] == "complete"
    assert episode["stages"]["tuesday"]["dialogue"][0]["character"] == "Margaret"
    assert [s["tuesday"]["status"] for s in saved] == ["complete"]
    notify_failure.assert_not_called()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__]))
