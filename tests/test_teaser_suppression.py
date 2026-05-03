"""Sunday-publish teaser suppression — homepage Featured + teaser must not duplicate."""

import json
from unittest.mock import patch

from backend.publishing import episode_renderer


def _episode(stages):
    return {
        "episode_id": "2026-W18",
        "concept": "Hash Brown Nests",
        "stages": stages,
        "image_urls": [],
    }


def _stage_with_dialogue(status="complete"):
    return {
        "status": status,
        "dialogue": [{"character": "Margaret Chen", "message": "Recipe is live."}],
    }


def test_sunday_complete_suppresses_teaser():
    """When Sunday stage is complete, teaser must be cleared so the homepage
    Featured hero (top of recipes.json) doesn't duplicate the teaser card."""
    episode = _episode({"monday": _stage_with_dialogue(), "sunday": _stage_with_dialogue()})

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save):
        episode_renderer.regenerate_and_upload(episode)

    payload = json.loads(writes["pages/latest.json"])
    assert payload == {"status": "published"}, payload


def test_pre_sunday_writes_teaser():
    """Before Sunday completes, the teaser still publishes normally."""
    episode = _episode({"monday": _stage_with_dialogue(), "saturday": _stage_with_dialogue()})

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save):
        episode_renderer.regenerate_and_upload(episode)

    payload = json.loads(writes["pages/latest.json"])
    assert payload.get("title")
    assert payload.get("status") != "published"
