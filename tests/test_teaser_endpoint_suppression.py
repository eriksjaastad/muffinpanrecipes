"""Read-side suppression of the Sunday teaser.

The /api/episodes/teaser endpoint must hide the teaser whenever the loaded
blob payload represents a Sunday-published episode. This is the authoritative
check — read-side suppression means a code deploy alone fixes prod, even when
`pages/latest.json` still has stale Sunday data from before the writer fix.
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


# These five tests exercise the pre-existing sunday-stage-blob suppression
# and must stay deterministic regardless of what real day it is when the
# suite runs. sunday_window_closed() is patched to False so the #7630
# week-off override never engages here — that behavior has its own tests
# below.
def test_sunday_stage_blob_is_suppressed():
    sunday_blob = json.dumps({
        "episode_id": "2026-W18",
        "title": "Maple Hash Brown Nests",
        "stage": "sunday",
        "stage_label": "Sunday &middot; Published",
        "character": "Margaret Chen",
    })
    with patch.object(episode_routes, "sunday_window_closed", return_value=False), \
         patch.object(episode_routes.storage, "load_page", return_value=sunday_blob):
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
    with patch.object(episode_routes, "sunday_window_closed", return_value=False), \
         patch.object(episode_routes.storage, "load_page", return_value=saturday_blob):
        response = _call()
    body = _body(response)
    assert body["title"] == "Maple Hash Brown Nests"
    assert body["stage"] == "saturday"


def test_published_status_blob_passes_through():
    """Once the writer-side fix runs, the blob holds {"status":"published"}.
    The reader should pass that through unchanged — frontend already hides
    on missing title."""
    published_blob = json.dumps({"status": "published"})
    with patch.object(episode_routes, "sunday_window_closed", return_value=False), \
         patch.object(episode_routes.storage, "load_page", return_value=published_blob):
        response = _call()
    assert _body(response) == {"status": "published"}


def test_missing_blob_returns_no_episode():
    with patch.object(episode_routes, "sunday_window_closed", return_value=False), \
         patch.object(episode_routes.storage, "load_page", return_value=None):
        response = _call()
    assert _body(response) == {"status": "no_episode"}


def test_malformed_blob_passes_through_to_client():
    """If the blob isn't valid JSON, don't suppress — let the client see the
    raw bytes and surface the problem rather than silently hiding it."""
    with patch.object(episode_routes, "sunday_window_closed", return_value=False), \
         patch.object(episode_routes.storage, "load_page", return_value="not-json{"):
        response = _call()
    assert response.body == b"not-json{"


# ---------------------------------------------------------------------------
# "Kitchen took the week off" homepage note (#7630)
# ---------------------------------------------------------------------------

def test_week_off_note_hidden_before_sunday_window_closes():
    """Mid-week (or early Sunday, inside the grace period), the teaser must
    behave exactly as it did before #7630 — no episode Blob read, no override."""
    with patch.object(episode_routes, "sunday_window_closed", return_value=False), \
         patch.object(episode_routes.storage, "load_episode") as load_episode, \
         patch.object(episode_routes.storage, "load_page", return_value=None):
        response = _call()
    load_episode.assert_not_called()
    assert _body(response) == {"status": "no_episode"}


def test_week_off_note_shown_when_unpublished_after_window_closes():
    """Sunday's window closed and the current week never published: the
    homepage owes the in-character note, not whatever pages/latest.json still
    holds. sunday_window_closed and week_off_note_due are each unit-tested
    for their own time arithmetic in test_episode_integrity.py; this test
    covers the ROUTE's wiring, so both are patched directly rather than
    reconstructed from a real clock (they live in different call sites —
    the route calls one, week_off_note_due calls its own copy — so patching
    only one would not control the other)."""
    with patch.object(episode_routes, "sunday_window_closed", return_value=True), \
         patch.object(episode_routes, "week_off_note_due", return_value=True), \
         patch.object(episode_routes.storage, "load_episode", return_value=None), \
         patch.object(episode_routes.storage, "load_page") as load_page:
        response = _call()
    load_page.assert_not_called()  # the note wins outright; no stale teaser read needed
    body = _body(response)
    assert body["status"] == "week_off"
    assert body["episode_id"] == episode_routes.current_episode_id()
    assert body["message"] == episode_routes.WEEK_OFF_MESSAGE


def test_week_off_note_hidden_once_the_week_publishes():
    """The note must disappear automatically the moment published_at is set —
    no separate flag to clear. Sunday's window is (still) closed, but
    week_off_note_due says no because the episode is published; the route
    must fall through to the ordinary teaser read."""
    published_episode = {"episode_id": "2026-W99", "published_at": "2026-09-06T00:10:00+00:00"}
    sunday_blob = json.dumps({"status": "published"})
    with patch.object(episode_routes, "sunday_window_closed", return_value=True), \
         patch.object(episode_routes, "week_off_note_due", return_value=False), \
         patch.object(episode_routes.storage, "load_episode", return_value=published_episode), \
         patch.object(episode_routes.storage, "load_page", return_value=sunday_blob):
        response = _call()
    assert _body(response) == {"status": "published"}


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
