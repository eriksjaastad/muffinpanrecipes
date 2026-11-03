"""The admin dashboard shows the current episode from cloud storage (#8604).

A fake in-memory store stands in for the storage singleton, so nothing reads
Blob or the network. The fake list holds the current week plus five earlier
weeks. Only the current week and the four newest earlier weeks render as
cards; the fifth older week is off the cards but still actionable, so it must
appear in Needs attention. Earlier week ids come from ``week_before`` (derived
from each episode id, never the clock), so the test is date-stable.
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
from backend.utils.episode_integrity import current_episode_id, week_before

TEMPLATES = Path(__file__).resolve().parents[1] / "backend" / "admin" / "templates"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


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


def _current_episode() -> dict:
    """Pending photo choice plus two failed stages (Thursday and Friday)."""
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


def _complete_week(episode_id: str, title: str) -> dict:
    """Every day complete, including Sunday, so the week is published."""
    stages = {day: {"status": "complete"} for day in DAYS}
    stages["monday"] = {"status": "complete", "recipe_data": {"title": title}}
    return {"episode_id": episode_id, "stages": stages}


def _episodes() -> tuple[list[dict], dict[str, str]]:
    current = _current_episode()
    failed_id = week_before(current["episode_id"])  # recent card (c)
    published_id = week_before(failed_id)  # recent card (b)
    filler_a_id = week_before(published_id)  # recent card, no actions
    filler_b_id = week_before(filler_a_id)  # recent card, no actions
    older_id = week_before(filler_b_id)  # fifth week: queue only (d)

    failed_week = {
        "episode_id": failed_id,
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Lemon Muffins"}},
            "tuesday": {"status": "failed", "error": "recipe dev timed out"},
        },
    }
    older_week = {
        "episode_id": older_id,
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Old Soup Muffins"}},
            "tuesday": {"status": "complete"},
            "wednesday": {"status": "failed", "error": "photo shoot failed"},
            "thursday": {"status": "complete"},
            "friday": {"status": "complete"},
            "saturday": {"status": "complete"},
            # sunday absent: a past due stage with no result
        },
    }
    episodes = [
        current,
        failed_week,
        _complete_week(published_id, "Maple Muffins"),
        _complete_week(filler_a_id, "Bran Muffins"),
        _complete_week(filler_b_id, "Corn Muffins"),
        older_week,
    ]
    ids = {"failed": failed_id, "published": published_id, "older": older_id}
    return episodes, ids


def test_dashboard_queue_covers_all_episodes_while_cards_show_current_and_four_recent():
    episodes, ids = _episodes()
    current = episodes[0]
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES))
    create_routes(app)
    app.dependency_overrides[require_auth] = lambda: {"email": "admin@example.com"}

    import backend.storage

    with patch.object(backend.storage, "storage", FakeStore(episodes)):
        response = TestClient(app).get("/admin/")

    assert response.status_code == 200
    body = response.text
    current_id = current["episode_id"]
    base = f"/admin/episodes/{current_id}"
    assert "Hash Brown Cups" in body
    # Needs attention is the first section of main, ahead of the episode cards.
    assert body.index("Needs attention") < body.index("Current episode")

    # (a) current pending photo plus multiple failures, each queued once.
    assert "Pending photo choice" in body
    assert body.count(f'href="{base}#photo-review"') == 1
    assert "Failed: Copywriting" in body  # Thursday's stage label
    assert body.count("Failed: Copywriting") == 1
    assert "Failed: Final Review" in body  # Friday's stage label, the second failed stage
    assert body.count("Failed: Final Review") == 1
    assert f"Hash Brown Cups · {current_id}" in body  # queue labels the episode
    # Every stage label renders on the card.
    for label in (
        "Brainstorm",
        "Recipe Dev",
        "Photography",
        "Copywriting",
        "Final Review",
        "Deployment",
        "Publish",
    ):
        assert label in body
    assert "PASS" in body  # Tuesday's judge verdict
    assert "Pending Review" not in body and "Recent Pending Recipes" not in body

    # (b) a recent published week's card says Published.
    assert (
        f'Recent episode · {ids["published"]} · <span class="font-semibold">Published</span>'
        in body
    )

    # (c) a recent failed week: its card says Failed, and a due-but-missing stage is queued.
    assert f'Recent episode · {ids["failed"]} · <span class="font-semibold">Failed</span>' in body
    assert "Failed: Recipe Dev →" in body
    assert "Missing: Copywriting →" in body
    assert f"Lemon Muffins · {ids['failed']}" in body

    # (d) the fifth, older week is not a card but its action is in Needs attention,
    # ahead of the cards and after the newer queue items (newest first).
    assert f"Recent episode · {ids['older']}" not in body
    assert "Failed: Photography →" in body
    older_label = body.index(f"Old Soup Muffins · {ids['older']}")
    assert older_label < body.index("Current episode")
    assert body.index("Missing: Copywriting →") < older_label
    assert body.index("Failed: Photography →") < older_label
