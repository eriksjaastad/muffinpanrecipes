"""Read-side suppression of the Sunday teaser.

The /api/episodes/teaser endpoint must hide the teaser whenever the loaded
blob payload represents a Sunday-published episode. This is the authoritative
check — read-side suppression means a code deploy alone fixes prod, even when
`pages/latest.json` still has stale Sunday data from before the writer fix.

The #7630 "kitchen took the week off" note is decided at CRON time (see
tests/test_cron_week_off_note.py and the week_off_note-forwarding tests in
tests/test_teaser_suppression.py) and simply passes through here like any
other field in the blob — this endpoint does no extra work and no extra Blob
reads to decide it, so it has no tests of its own beyond confirming the
passthrough is unconditional (see test_week_off_note_field_passes_through
below).
"""

import asyncio
import json
from unittest.mock import patch

from backend.admin import episode_routes


def _call():
    return asyncio.run(episode_routes.get_episode_teaser())


def _body(response) -> dict:
    raw = response.body if isinstance(response.body, (bytes, bytearray)) else response.body
    return json.loads(raw)


def test_sunday_stage_blob_is_suppressed():
    sunday_blob = json.dumps({
        "episode_id": "2026-W18",
        "title": "Maple Hash Brown Nests",
        "stage": "sunday",
        "stage_label": "Sunday &middot; Published",
        "character": "Margaret Chen",
    })
    with patch.object(episode_routes.storage, "load_page", return_value=sunday_blob):
        response = _call()
    assert _body(response) == {"status": "published"}


def test_pre_sunday_stage_blob_passes_through():
    saturday_blob = json.dumps({
        "episode_id": "2026-W18",
        "title": "Maple Hash Brown Nests",
        "stage": "saturday",
        "stage_label": "Saturday &middot; Deployment",
        "character": "Devon Park",
    })
    with patch.object(episode_routes.storage, "load_page", return_value=saturday_blob):
        response = _call()
    body = _body(response)
    assert body["title"] == "Maple Hash Brown Nests"
    assert body["stage"] == "saturday"


def test_published_status_blob_passes_through():
    """Once the writer-side fix runs, the blob holds {"status":"published"}.
    The reader should pass that through unchanged — frontend already hides
    on missing title."""
    published_blob = json.dumps({"status": "published"})
    with patch.object(episode_routes.storage, "load_page", return_value=published_blob):
        response = _call()
    assert _body(response) == {"status": "published"}


def test_missing_blob_returns_no_episode():
    with patch.object(episode_routes.storage, "load_page", return_value=None):
        response = _call()
    assert _body(response) == {"status": "no_episode"}


def test_malformed_blob_passes_through_to_client():
    """If the blob isn't valid JSON, don't suppress — let the client see the
    raw bytes and surface the problem rather than silently hiding it."""
    with patch.object(episode_routes.storage, "load_page", return_value="not-json{"):
        response = _call()
    assert response.body == b"not-json{"


def test_week_off_note_field_passes_through_unconditionally():
    """#7630: the note is decided at cron time and merely forwarded here.
    No extra Blob read: storage.load_episode is never called by this route
    at all any more."""
    stalled_blob = json.dumps({
        "episode_id": "2026-W41",
        "title": "Next Week's Recipe",
        "stage": "monday",
        "week_off_note": {
            "message": "The kitchen took the week off — back next Sunday.",
            "missed_week": "2026-W40",
        },
    })
    with patch.object(episode_routes.storage, "load_episode") as load_episode, \
         patch.object(episode_routes.storage, "load_page", return_value=stalled_blob):
        response = _call()
    load_episode.assert_not_called()
    body = _body(response)
    assert body["week_off_note"] == {
        "message": "The kitchen took the week off — back next Sunday.",
        "missed_week": "2026-W40",
    }
    assert body["episode_id"] == "2026-W41"


def test_week_off_note_adds_no_new_route():
    """Homepage note only — never a standalone page (#7630 constraint)."""
    paths = {getattr(route, "path", None) for route in episode_routes.router.routes}
    assert not any(path and "week-off" in path.lower() for path in paths if path)


def test_week_off_note_not_in_sitemap():
    """The note is inline homepage content, not a new indexable URL."""
    from backend.publishing.static_renderer import render_sitemap

    xml = render_sitemap([])
    assert "week-off" not in xml.lower()
    assert xml.count("<url>") == 3  # home, /recipes, /this-week — unchanged by #7630


def test_rewritten_static_path_is_served_like_the_recipe_page() -> None:
    """vercel.json's Lambda fallback for an uncommitted recipe page arrives on
    the REWRITTEN path /src/recipes/<slug>/index.html (#6684); the app must
    answer it exactly as it answers /recipes/<slug>."""
    from backend.admin import episode_routes as er

    with patch.object(er.storage, "load_page", return_value="<html>frozen page</html>"):
        direct = asyncio.run(er.recipe_page("pastel-de-nata-cups"))
        rewritten = asyncio.run(er.recipe_page_rewritten_path("pastel-de-nata-cups"))
    assert rewritten.status_code == direct.status_code == 200
    assert rewritten.body == direct.body
    assert rewritten.headers["x-robots-tag"] == "noindex"  # a duplicate URL, never indexed
