"""Synthetic Wednesday stages and photo decisions (#7936).

Sunday publishes only an approved current photo, so every offline Sunday
fixture that expects a publish must carry one. Decisions go to DECISIONS,
the in-memory store tests/conftest.py installs on the storage singleton for
every test. No storage or provider calls.
"""

from backend.storage import new_photo_decision_id
from backend.utils import photo_review

VARIANTS = ("macro_closeup", "overhead_flatlay", "hero_threequarter")
BLOB = "https://store.public.blob.vercel-storage.com/images"


class MemoryDecisions:
    """Append-only, like the real backends; keyed by (episode, image set)."""

    def __init__(self):
        self.records: dict[tuple[str, str], list[dict]] = {}
        self.fail_reads = False

    def clear(self):
        self.records.clear()
        self.fail_reads = False

    def add_photo_decision(self, episode_id, image_set_id, record):
        record_id = new_photo_decision_id()
        self.records.setdefault((episode_id, image_set_id), []).append({**record, "record_id": record_id})
        return record_id

    def latest_photo_decision(self, episode_id, image_set_id):
        if self.fail_reads:
            from backend.storage import PhotoDecisionUnavailable
            raise PhotoDecisionUnavailable("blob down")
        found = self.records.get((episode_id, image_set_id))
        return dict(found[-1]) if found else None


DECISIONS = MemoryDecisions()


def wednesday_stage(recipe_id: str = "r1", **overrides) -> dict:
    """A complete Wednesday with three uploaded candidates and a review request."""
    paths = [f"src/assets/images/{recipe_id}/round_1/{v}.png" for v in VARIANTS]
    urls = [f"{BLOB}/{recipe_id}/round_1/{v}.png" for v in VARIANTS]
    wed = {
        "status": "complete",
        "confirmed_winner": {"variant": VARIANTS[0], "path": paths[0],
                             "featured_image": f"src/assets/images/{recipe_id}.png"},
        "image_status": "auto_selected",
        "photography_data": {"rounds": [{
            "round": 1,
            "variants": [{"variant": v, "path": p} for v, p in zip(VARIANTS, paths)],
        }]},
        "image_paths": paths,
        "image_urls": urls,
    }
    wed.update(overrides)
    wed["photo_review"] = photo_review.new_review(wed)
    return wed


def decide(ep: dict, action: str = "select", pick: int = 1, store=None) -> dict:
    """Record a decision for ``ep``'s current set, as the admin endpoint does."""
    wed = ep["stages"]["wednesday"]
    record = photo_review.make_decision(
        ep, action=action, image_set=wed["photo_review"]["image_set_id"],
        path=wed["image_paths"][pick - 1] if action == "select" else None, decided_by="test",
    )
    return photo_review.save_decision(store or DECISIONS, ep, record)


def approved_wednesday(recipe_id: str = "r1", pick: int = 1, episode_id: str = "2026-W41",
                       action: str = "select", **overrides) -> dict:
    """A Wednesday stage whose current set already has a decision in DECISIONS.

    ``episode_id`` must match the episode the stage is placed in.
    """
    wed = wednesday_stage(recipe_id, **overrides)
    decide({"episode_id": episode_id, "stages": {"wednesday": wed}}, action=action, pick=pick)
    return wed
