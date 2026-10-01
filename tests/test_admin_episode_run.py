"""Admin 'Run Compressed Week' must not run on a placeholder concept when the
episode file exists but cannot be read (silent-failure sweep, #7587)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.admin import cron_routes
from backend.admin.routes import create_routes
from backend.auth.middleware import require_auth


def _client(project_root) -> TestClient:
    app = FastAPI()
    app.state.project_root = project_root
    create_routes(app)
    app.dependency_overrides[require_auth] = lambda: {"email": "admin@example.com"}
    return TestClient(app)


def test_corrupt_episode_file_fails_the_run_instead_of_using_placeholder(tmp_path):
    episodes = tmp_path / "data" / "episodes"
    episodes.mkdir(parents=True)
    (episodes / "2026-W18.json").write_text("{not json")

    stub = AsyncMock(return_value={"mode": "simulation"})
    with patch.object(cron_routes, "execute_cron_stage_stub", stub):
        response = _client(tmp_path).post("/admin/episodes/2026-W18/run")

    assert response.status_code == 500
    assert "unreadable" in response.json()["detail"]
    stub.assert_not_called()


def test_readable_episode_file_passes_its_concept_to_every_stage(tmp_path):
    episodes = tmp_path / "data" / "episodes"
    episodes.mkdir(parents=True)
    (episodes / "2026-W18.json").write_text('{"concept": "Hash brown cups"}')

    stub = AsyncMock(return_value={"mode": "simulation"})
    with patch.object(cron_routes, "execute_cron_stage_stub", stub), \
         patch("backend.admin.routes.asyncio.sleep", AsyncMock()):
        response = _client(tmp_path).post("/admin/episodes/2026-W18/run")

    assert response.status_code == 200
    assert stub.await_count == 7
    assert {call.args[2] for call in stub.await_args_list} == {"Hash brown cups"}
