"""The admin dashboard shows the current episode from cloud storage (#8604).

A fake in-memory store stands in for the storage singleton, so nothing reads
Blob or the network. The fake list holds the current week plus six earlier
weeks, and its photo control log is the real photo_review transitions' store.
Only the current week and the four newest earlier weeks render as cards. Needs
attention is operational, so it contains the current week only; completed and
unfinished history remains inspectable through those cards and Episode Log.
Earlier week ids come from ``week_before`` (derived from each episode id, never
the clock), so the test is date-stable.
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from backend.admin.routes import create_routes
from backend.auth.middleware import require_auth
from backend.storage import PhotoControlConflict
from backend.utils.episode_integrity import current_episode_id, week_before
from tests.photo_review_helpers import decide, register, wednesday_stage

TEMPLATES = Path(__file__).resolve().parents[1] / "backend" / "admin" / "templates"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class FakeStore:
    """Episodes plus an in-memory photo control log with the real version rules.

    ``create_photo_control_version`` mirrors the storage contract: a version is
    written once, and only after the one before it, so the real photo_review
    transitions (register, decide) run unchanged against this log.
    """

    def __init__(self, episodes: list[dict], *, test_episodes: list[dict] | None = None) -> None:
        self._episodes = {"": episodes, "test/": test_episodes if test_episodes is not None else episodes}
        self._prefix = ""
        self._control: dict[str, dict[int, dict]] = {}

    @contextmanager
    def prefix_scope(self, prefix: str):
        previous = self._prefix
        self._prefix = prefix
        try:
            yield
        finally:
            self._prefix = previous

    def namespaces_episodes(self) -> bool:
        return True

    def list_episodes_strict(self) -> list[dict]:
        return [dict(e) for e in self._episodes[self._prefix]]

    def read_photo_control(self, episode_id: str):
        versions = self._control.get(episode_id)
        if not versions:
            return None  # no control yet: the photo view derives from the episode
        return dict(versions[max(versions)])

    def create_photo_control_version(self, episode_id: str, version: int, body: dict) -> None:
        versions = self._control.setdefault(episode_id, {})
        if version in versions:
            raise PhotoControlConflict(f"photo control {episode_id} v{version} already exists")
        if version > 1 and version - 1 not in versions:
            raise PhotoControlConflict(f"photo control {episode_id} v{version - 1} does not exist")
        versions[version] = dict(body)


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
    # recent card: due stages missing, no explicit failure (f)
    filler_b_id = week_before(filler_a_id)
    older_id = week_before(filler_b_id)  # fifth week: queue only (d)
    held_id = week_before(older_id)  # sixth week: queue only, a legitimate photo hold (e)
    rejected_id = week_before(held_id)  # seventh week: queue only, photos rejected (g)
    approved_id = week_before(rejected_id)  # eighth week: hold recorded, photos since approved (h)

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
    held_wed = wednesday_stage(recipe_id="held-soup")
    held_week = {
        "episode_id": held_id,
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Held Soup Muffins"}},
            "tuesday": {"status": "complete", "judge_verdict": "PASS"},
            "wednesday": held_wed,
            "thursday": {"status": "complete"},
            "friday": {"status": "complete"},
            "saturday": {"status": "complete"},
            # sunday absent, but held: cron_sunday recorded the photo hold for this image set
        },
        "publish_hold": {
            "reason": "awaiting_photo_approval",
            "image_set_id": held_wed["photo_review"]["image_set_id"],
            "since": "2026-10-28T12:00:00+00:00",
            "notified": True,
            "last_attempt": {"at": "2026-11-01T12:00:00+00:00", "outcome": "held"},
        },
    }
    # Seventh week: Wednesday's photos were rejected, so cron_sunday recorded the
    # photos_rejected hold on this image set. The control is written by the test.
    rejected_wed = wednesday_stage(recipe_id="rejected-soup")
    rejected_week = {
        "episode_id": rejected_id,
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Rejected Soup Muffins"}},
            "tuesday": {"status": "complete", "judge_verdict": "PASS"},
            "wednesday": rejected_wed,
            "thursday": {"status": "complete"},
            "friday": {"status": "complete"},
            "saturday": {"status": "complete"},
            # sunday absent, held: the photos were rejected, so no Sunday run can publish
        },
        "publish_hold": {
            "reason": "photos_rejected",
            "image_set_id": rejected_wed["photo_review"]["image_set_id"],
            "since": "2026-10-21T12:00:00+00:00",
            "notified": True,
            "last_attempt": {"at": "2026-10-28T12:00:00+00:00", "outcome": "held"},
        },
    }
    # Eighth week: a recorded hold, but Erik has since approved the photos, so the
    # hold no longer warrants the missing Sunday. The control is written by the test.
    approved_wed = wednesday_stage(recipe_id="approved-soup")
    approved_week = {
        "episode_id": approved_id,
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Approved Soup Muffins"}},
            "tuesday": {"status": "complete"},
            "wednesday": approved_wed,
            "thursday": {"status": "complete"},
            "friday": {"status": "complete"},
            "saturday": {"status": "complete"},
        },
        "publish_hold": {
            "reason": "awaiting_photo_approval",
            "image_set_id": approved_wed["photo_review"]["image_set_id"],
            "since": "2026-10-14T12:00:00+00:00",
            "notified": True,
            "last_attempt": {"at": "2026-10-18T12:00:00+00:00", "outcome": "held"},
        },
    }
    # Recent week with only due stages missing (Photography and Publish), no explicit failure.
    filler_b_week = {
        "episode_id": filler_b_id,
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Corn Muffins"}},
            "tuesday": {"status": "complete"},
            "thursday": {"status": "complete"},
            "friday": {"status": "complete"},
            "saturday": {"status": "complete"},
            # wednesday and sunday absent: both due, neither held
        },
    }
    # A published week keeps old history: a failed Deployment and an absent Final Review.
    published_week = _complete_week(published_id, "Maple Muffins")
    published_week["stages"]["saturday"] = {"status": "failed", "error": "old deploy failure"}
    del published_week["stages"]["friday"]
    episodes = [
        current,
        failed_week,
        published_week,
        _complete_week(filler_a_id, "Bran Muffins"),
        filler_b_week,
        older_week,
        held_week,
        rejected_week,
        approved_week,
    ]
    ids = {
        "failed": failed_id,
        "published": published_id,
        "filler_b": filler_b_id,
        "older": older_id,
        "held": held_id,
        "rejected": rejected_id,
        "approved": approved_id,
    }
    return episodes, ids


def test_dashboard_queue_covers_current_week_while_cards_show_recent_history():
    episodes, ids = _episodes()
    current = episodes[0]
    store = FakeStore(episodes)
    # The rejected week's photos go through the real transitions: Wednesday
    # registers the set, then Erik rejects it. Both write the in-memory log.
    rejected = next(e for e in episodes if e["episode_id"] == ids["rejected"])
    register(ids["rejected"], rejected["stages"]["wednesday"], store=store)
    decide(ids["rejected"], rejected, action="reject", store=store)
    approved = next(e for e in episodes if e["episode_id"] == ids["approved"])
    register(ids["approved"], approved["stages"]["wednesday"], store=store)
    decide(ids["approved"], approved, store=store)

    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES))
    create_routes(app)
    app.dependency_overrides[require_auth] = lambda: {"email": "admin@example.com"}
    assert not any(route.path == "/admin/simulations" for route in app.routes)

    import backend.storage

    with patch.object(backend.storage, "storage", store):
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
    assert "/admin/simulations" not in body
    assert "Simulations" not in body

    # (b) a recent published week's card says Published.
    assert (
        f'Recent episode · {ids["published"]} · <span class="font-semibold">Published</span>'
        in body
    )

    # (c) a recent failed week remains visible as history, but it is not an
    # active request for Erik after its ISO week has ended.
    assert f'Recent episode · {ids["failed"]} · <span class="font-semibold">Failed</span>' in body
    assert f"Lemon Muffins · {ids['failed']}" not in body

    # (d) older history is neither a recent card nor an active queue item.
    assert f"Recent episode · {ids['older']}" not in body
    assert f"Old Soup Muffins · {ids['older']}" not in body

    # (e) historical photo decisions and holds remain out of active attention.
    assert f"Recent episode · {ids['held']}" not in body
    held_items = [li for li in re.findall(r"<li>(.*?)</li>", body, re.DOTALL) if ids["held"] in li]
    assert held_items == []

    # (f) a recent incomplete week still has a truthful card, not queue actions.
    assert f'Recent episode · {ids["filler_b"]} · <span class="font-semibold">Failed</span>' in body
    assert f"Corn Muffins · {ids['filler_b']}" not in body

    # (g) an older rejected photo control remains historical.
    assert f"Recent episode · {ids['rejected']}" not in body
    rejected_items = [
        li for li in re.findall(r"<li>(.*?)</li>", body, re.DOTALL) if ids["rejected"] in li
    ]
    assert rejected_items == []

    # (h) a recorded historical hold whose photos were approved is archived too.
    approved_items = [
        li for li in re.findall(r"<li>(.*?)</li>", body, re.DOTALL) if ids["approved"] in li
    ]
    assert approved_items == []

    # (i) a published week never queues its historical failed or missing stages.
    assert "Maple Muffins" in body
    assert not [li for li in re.findall(r"<li>(.*?)</li>", body, re.DOTALL) if ids["published"] in li]


def test_production_admin_hides_leaked_e2e_records_but_test_namespace_lists_them():
    """A root-level synthetic record cannot pollute real admin work (#incident)."""
    real = _current_episode()
    leaked = {
        "episode_id": "test-e2e-006",
        "created_at": "2026-11-11T12:00:00+00:00",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Synthetic Test"}},
            "wednesday": {
                "status": "complete",
                "image_paths": ["src/assets/images/round-1/fake.png"],
                "image_urls": ["https://blob.vercel-storage.com/placeholder/fake.png"],
            },
        },
    }
    store = FakeStore([leaked, real], test_episodes=[leaked])
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES))
    create_routes(app)
    app.dependency_overrides[require_auth] = lambda: {"email": "admin@example.com"}

    import backend.storage

    with patch.object(backend.storage, "storage", store):
        client = TestClient(app)
        dashboard = client.get("/admin/")
        production_list = client.get("/admin/episodes")
        test_list = client.get("/admin/episodes?ns=test")

    assert dashboard.status_code == production_list.status_code == test_list.status_code == 200
    assert real["episode_id"] in dashboard.text and real["episode_id"] in production_list.text
    assert leaked["episode_id"] not in dashboard.text
    assert leaked["episode_id"] not in production_list.text
    assert leaked["episode_id"] in test_list.text
