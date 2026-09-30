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


# ---------------------------------------------------------------------------
# week_off_note forwarding (#7630) — cron_routes stamps this on the episode
# (Monday, and cron_sunday's own refuse-to-publish path); regenerate_and_upload
# just has to carry it into whatever it writes to pages/latest.json, and never
# let it leak into the published marker.
# ---------------------------------------------------------------------------

_WEEK_OFF_NOTE = {
    "message": "The kitchen took the week off — back next Sunday.",
    "missed_week": "2026-W17",
}


def test_week_off_note_is_forwarded_into_the_teaser():
    episode = _episode({"monday": _stage_with_dialogue(), "saturday": _stage_with_dialogue()})
    episode["week_off_note"] = _WEEK_OFF_NOTE

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save):
        episode_renderer.regenerate_and_upload(episode)

    payload = json.loads(writes["pages/latest.json"])
    assert payload["week_off_note"] == _WEEK_OFF_NOTE
    assert payload.get("title")  # the ordinary mid-week teaser is still there too


def test_week_off_note_absent_from_teaser_when_not_set():
    episode = _episode({"monday": _stage_with_dialogue(), "saturday": _stage_with_dialogue()})

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save):
        episode_renderer.regenerate_and_upload(episode)

    payload = json.loads(writes["pages/latest.json"])
    assert "week_off_note" not in payload


def test_week_off_note_never_reaches_the_published_marker():
    """A successful Sunday publish clears the note: the published branch is
    a bare {"status": "published"} regardless of what's still on the episode."""
    episode = _episode({"monday": _stage_with_dialogue(), "sunday": _stage_with_dialogue()})
    episode["week_off_note"] = _WEEK_OFF_NOTE

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save):
        episode_renderer.regenerate_and_upload(episode)

    payload = json.loads(writes["pages/latest.json"])
    assert payload == {"status": "published"}


def test_week_off_note_reaches_homepage_even_with_no_teaser_dialogue():
    """No dialogue anywhere yet, but week_off_note is set (e.g. right after
    Sunday's own refuse-to-publish path fires before any dialogue exists) —
    the note must still reach pages/latest.json, with episode_id present so
    health_check's teaser check needs no special-casing for this state."""
    episode = _episode({})
    episode["week_off_note"] = _WEEK_OFF_NOTE

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save), \
         patch.object(episode_renderer, "render_episode_page", return_value="<html></html>"):
        episode_renderer.regenerate_and_upload(episode)

    payload = json.loads(writes["pages/latest.json"])
    assert payload == {"episode_id": "2026-W18", "week_off_note": _WEEK_OFF_NOTE}
