"""Synthetic Wednesday stages and photo approvals (#7936).

Sunday publishes only an approved current photo it can claim, so every
offline Sunday fixture that expects a publish must carry one. Approvals go
through the real photo-control transitions on the storage singleton, which
tests/conftest.py points at a per-test temporary directory. No storage or
provider calls leave the machine.
"""

import pytest
from fastapi import HTTPException

from backend.admin import cron_routes
from backend.storage import storage
from backend.utils import photo_review

VARIANTS = ("macro_closeup", "overhead_flatlay", "hero_threequarter")
BLOB = "https://store.public.blob.vercel-storage.com/images"


@pytest.fixture
def sunday_reshoot_refused(monkeypatch):
    """Sunday's automatic reshoot (#8606) is refused, as at the call cap.

    For tests whose subject is the hold itself: a set with no team winner
    holds, and the refusal is the alerted failure the real cap sends. Tests of
    the reshoot itself do not use this and run the real path with a fake shoot.
    """
    def _refused(*_args, **_kwargs):
        raise HTTPException(status_code=409, detail="Reshoot limit reached: 9 of 9 reshoot image calls used for this episode.")

    monkeypatch.setattr(cron_routes, "reshoot_wednesday_photos", _refused)


def wednesday_stage(recipe_id: str = "r1", generation: str = "g20260101T000000Z-aaaaaa", **overrides) -> dict:
    """A complete Wednesday with three uploaded candidates and a request mirror.

    The full request is on ``wed["_request"]`` for ``register``; it is not
    part of what Wednesday stores on the episode.
    """
    paths = [f"src/assets/images/{recipe_id}/{generation}/round_1/{v}.png" for v in VARIANTS]
    urls = [f"{BLOB}/{recipe_id}/{generation}/round_1/{v}.png" for v in VARIANTS]
    wed = {
        "status": "complete",
        "completed_at": "2026-01-01T12:00:00+00:00",
        "confirmed_winner": {"variant": VARIANTS[0], "path": paths[0],
                             "featured_image": f"src/assets/images/{recipe_id}.png",
                             "team_recommended": True},
        "image_status": "auto_selected",
        "photography_data": {"rounds": [{
            "round": 1,
            "variants": [{"variant": v, "path": p} for v, p in zip(VARIANTS, paths)],
        }]},
        "image_paths": paths,
        "image_urls": urls,
    }
    wed.update(overrides)
    request = photo_review.new_review(wed)
    wed["photo_review"] = {k: request[k] for k in ("image_set_id", "generated_at", "requested_at")}
    return wed


def register(episode_id: str, wed: dict, store=None):
    """Make ``wed``'s set the episode's current request, as Wednesday does."""
    review = wed["photo_review"]
    request = {
        **review,
        "candidates": photo_review.build_candidates(wed),
        "image_paths": list(wed["image_paths"]),
        "image_urls": list(wed["image_urls"]),
        # Wednesday freezes its evaluation into the request (new_review).
        "wednesday_snapshot": photo_review._wednesday_snapshot(wed),
    }
    return photo_review.register_request(store or storage, episode_id, request)


def decide(episode_id: str, ep: dict, action: str = "select", pick: int = 1, store=None):
    """Record a decision on the current control, as the admin endpoint does."""
    store = store or storage
    view = photo_review.read_view(store, episode_id, ep)
    path = view.request["candidates"][pick - 1]["path"] if action == "select" else None
    return photo_review.record_decision(
        store, episode_id, ep, action=action, image_set=view.image_set_id, path=path,
        decided_by="site editor", decided_by_id="editor-test",
    )


def approved_wednesday(recipe_id: str = "r1", pick: int = 1, episode_id: str = "2026-W41",
                       action: str = "select", prefix: str = "", **overrides) -> dict:
    """A Wednesday stage whose set is registered and decided in the control.

    ``episode_id`` must match the episode the stage is placed in, and
    ``prefix`` the storage namespace Sunday runs in ("test/" for test=True).
    """
    wed = wednesday_stage(recipe_id, **overrides)
    with storage.prefix_scope(prefix):
        register(episode_id, wed)
        decide(episode_id, {"episode_id": episode_id, "stages": {"wednesday": wed}}, action=action, pick=pick)
    return wed
