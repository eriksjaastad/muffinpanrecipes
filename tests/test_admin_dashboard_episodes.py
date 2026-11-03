"""The admin dashboard shows the current episode from cloud storage (#8604).

A fake in-memory store stands in for the storage singleton, so nothing reads
Blob or the network. The episode has a pending photo choice and a failed
Thursday stage; the dashboard must link to both and show no recipe counters.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from backend.admin.routes import create_routes
from backend.auth.middleware import require_auth
from backend.utils.episode_integrity import current_episode_id

TEMPLATES = Path(__file__).resolve().parents[1] / "backend" / "admin" / "templates"


class FakeStore:
    def __init__(self, episodes: list[dict]) -> None:
        self._episodes = episodes

    @contextmanager
    def prefix_scope(self, prefix: str):
        yield

    def list_episodes_strict(self) -> list[dict]:
        return [dict(e) for e in self._episodes]

    def read_photo_control(self, episode_id: str):
        return None  # no control stored yet: the photo view derives from the episode


def _episode() -> dict:
    episode_id = current_episode_id()
    candidate = {"variant": "a", "round": 1}
    return {
        "episode_id": episode_id,
        "created_at": "2026-11-02T12:00:00+00:00",
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {"title": "Hash Brown Cups"},
            },
            "tuesday": {"status": "complete", "judge_verdict": "PASS"},
            "wednesday": {
                "status": "complete",
                "completed_at": "2026-11-04T12:00:00+00:00",
                "image_paths": ["src/assets/images/round-1/a.png"],
                "image_urls": ["https://blob.example.com/images/round-1/a.png"],
                "photography_data": {
                    "rounds": [
                        {
                            "round": 1,
                            **candidate,
                            "variants": [
                                {"path": "src/assets/images/round-1/a.png", "variant": "a"},
                            ],
                        }
                    ]
                },
            },
            "thursday": {"status": "failed", "error": "copy stage timed out"},
            "friday": {"status": "failed", "error": "final review crashed"},
        },
    }


def test_dashboard_links_pending_photo_and_failed_stage_without_recipe_counters():
    episode = _episode()
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES))
    create_routes(app)
    app.dependency_overrides[require_auth] = lambda: {"email": "admin@example.com"}

    import backend.storage

    with patch.object(backend.storage, "storage", FakeStore([episode])):
        response = TestClient(app).get("/admin/")

    assert response.status_code == 200
    body = response.text
    base = f"/admin/episodes/{episode['episode_id']}"
    assert "Hash Brown Cups" in body
    # Needs attention is the first section of main, ahead of the episode cards.
    assert body.index("Needs attention") < body.index("Current episode")
    # Each queued action is linked once: no per-card duplicate rendering.
    assert "Pending photo choice" in body
    assert body.count(f'href="{base}#photo-review"') == 1
    assert "Failed: Copywriting" in body  # Thursday's stage label
    assert body.count("Failed: Copywriting") == 1
    assert "Failed: Final Review" in body  # Friday's stage label, the second failed stage
    assert body.count("Failed: Final Review") == 1
    assert f"Hash Brown Cups · {episode['episode_id']}" in body  # queue labels the episode
    # Every stage label renders on the card.
    for label in ("Brainstorm", "Recipe Dev", "Photography", "Copywriting",
                  "Final Review", "Deployment", "Publish"):
        assert label in body
    assert "PASS" in body  # Tuesday's judge verdict
    assert "Failed" in body  # episode status and Thursday's stage status
    assert "Pending Review" not in body and "Recent Pending Recipes" not in body
