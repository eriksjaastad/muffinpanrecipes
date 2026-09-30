"""Read-side suppression of the Sunday teaser, and the #7630 "kitchen took
the week off" homepage note.

The /api/episodes/teaser endpoint must hide the teaser whenever the loaded
blob payload represents a Sunday-published episode. This is the authoritative
check — read-side suppression means a code deploy alone fixes prod, even when
`pages/latest.json` still has stale Sunday data from before the writer fix.

It must also decide, from real data, whether the RELEVANT week (the most
recently CLOSED Sunday window — see relevant_week_id in episode_integrity.py,
NOT necessarily the current ISO week) ever published, and show the
"kitchen took the week off" note when it didn't — without adding an
unconditional extra Blob read to every homepage view. Route-level tests below
patch `relevant_week_id` directly rather than reconstructing it from a real
clock; its own time arithmetic (including surviving the Monday rollover) is
unit-tested in test_episode_integrity.py.
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
    """The legacy pre-#7403 writer format still gets suppressed, regardless
    of the relevant week — this check runs before anything week-off related."""
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
    """A normal mid-week teaser for the CURRENT week (episode_id 2026-W18,
    Saturday stage) passes through unchanged when the RELEVANT (previous,
    already-closed) week published fine — the common, healthy case."""
    saturday_blob = json.dumps({
        "episode_id": "2026-W18",
        "title": "Maple Hash Brown Nests",
        "stage": "saturday",
        "stage_label": "Saturday &middot; Deployment",
        "character": "Devon Park",
    })
    with patch.object(episode_routes, "relevant_week_id", return_value="2026-W17"), \
         patch.object(
             episode_routes.storage, "load_episode",
             return_value={"published_at": "2026-08-30T00:10:00+00:00"},
         ), \
         patch.object(episode_routes.storage, "load_page", return_value=saturday_blob):
        response = _call()
    body = _body(response)
    assert body["title"] == "Maple Hash Brown Nests"
    assert body["stage"] == "saturday"


def test_published_status_blob_passes_through():
    """Once the writer-side fix runs, the blob holds {"status":"published"}.
    That carries no episode_id, so it can't be matched to the relevant week
    directly; when the relevant week's own record confirms it published, the
    reader passes the blob through unchanged — frontend already hides on
    missing title."""
    published_blob = json.dumps({"status": "published"})
    with patch.object(episode_routes, "relevant_week_id", return_value="2026-W17"), \
         patch.object(
             episode_routes.storage, "load_episode",
             return_value={"published_at": "2026-08-30T00:10:00+00:00"},
         ), \
         patch.object(episode_routes.storage, "load_page", return_value=published_blob):
        response = _call()
    assert _body(response) == {"status": "published"}


def test_missing_blob_with_relevant_week_published_returns_no_episode():
    """Route-wiring coverage for the no_episode branch: no pages/latest.json
    at all, but the relevant week's own record says it published. (In
    practice a publish always writes latest.json, so this combination is
    hypothetical — this exercises the explicit else branch.)"""
    with patch.object(
        episode_routes.storage, "load_episode",
        return_value={"published_at": "2026-08-30T00:10:00+00:00"},
    ), patch.object(episode_routes.storage, "load_page", return_value=None):
        response = _call()
    assert _body(response) == {"status": "no_episode"}


def test_malformed_blob_passes_through_to_client():
    """If the blob isn't valid JSON, don't suppress and don't guess about the
    relevant week — let the client see the raw bytes and surface the problem
    rather than silently hiding it. No episode Blob read is needed or made."""
    with patch.object(episode_routes.storage, "load_episode") as load_episode, \
         patch.object(episode_routes.storage, "load_page", return_value="not-json{"):
        response = _call()
    load_episode.assert_not_called()
    assert response.body == b"not-json{"


# ---------------------------------------------------------------------------
# "Kitchen took the week off" homepage note (#7630)
# ---------------------------------------------------------------------------

def test_week_off_note_shown_from_latest_json_alone_with_no_extra_read():
    """When pages/latest.json is unambiguously ABOUT the relevant week (its
    episode_id matches) and never reached "sunday", the note is decided for
    free from data already in hand — this is the Sunday-evening-of-a-failed-
    week case, and it costs zero extra Blob reads."""
    stalled_blob = json.dumps({
        "episode_id": "2026-W40",
        "title": "Placeholder",
        "stage": "tuesday",
    })
    with patch.object(episode_routes, "relevant_week_id", return_value="2026-W40"), \
         patch.object(episode_routes.storage, "load_episode") as load_episode, \
         patch.object(episode_routes.storage, "load_page", return_value=stalled_blob):
        response = _call()
    load_episode.assert_not_called()
    assert _body(response) == {
        "status": "week_off",
        "episode_id": "2026-W40",
        "message": episode_routes.WEEK_OFF_MESSAGE,
    }


def test_week_off_note_shown_via_relevant_week_fallback_when_unpublished():
    """The bug this fixes: pages/latest.json has already moved on to a NEWER
    week's own progress (Monday of the week after the failure), but the
    RELEVANT (most recently closed) week's own record says it never
    published. The note must still show — not the newer week's teaser, and
    not silence — proving the Monday rollover doesn't hide a failed week."""
    newer_week_blob = json.dumps({
        "episode_id": "2026-W41",
        "title": "Next Week's Recipe",
        "stage": "monday",
    })
    with patch.object(episode_routes, "relevant_week_id", return_value="2026-W40"), \
         patch.object(episode_routes.storage, "load_episode", return_value=None) as load_episode, \
         patch.object(episode_routes.storage, "load_page", return_value=newer_week_blob):
        response = _call()
    load_episode.assert_called_once_with("2026-W40")
    assert _body(response) == {
        "status": "week_off",
        "episode_id": "2026-W40",
        "message": episode_routes.WEEK_OFF_MESSAGE,
    }


def test_week_off_note_shown_for_a_fully_paused_week_with_no_episode_file():
    """No pages/latest.json at all AND the relevant week's episode record
    doesn't exist either — a week that never even got as far as Monday's
    cron creating it. The homepage still owes the note, not a bare
    no_episode."""
    with patch.object(episode_routes, "relevant_week_id", return_value="2026-W40"), \
         patch.object(episode_routes.storage, "load_episode", return_value=None), \
         patch.object(episode_routes.storage, "load_page", return_value=None):
        response = _call()
    assert _body(response) == {
        "status": "week_off",
        "episode_id": "2026-W40",
        "message": episode_routes.WEEK_OFF_MESSAGE,
    }


def test_week_off_note_hidden_once_the_relevant_week_publishes():
    """The relevant week's own record shows it DID publish — the note must
    not show, and a newer week's own in-progress teaser passes through
    unchanged. The note disappears automatically; nothing to flip back."""
    newer_week_blob = json.dumps({
        "episode_id": "2026-W41",
        "title": "Next Week's Recipe",
        "stage": "monday",
    })
    with patch.object(episode_routes, "relevant_week_id", return_value="2026-W40"), \
         patch.object(
             episode_routes.storage, "load_episode",
             return_value={"published_at": "2026-10-04T00:10:00+00:00"},
         ), \
         patch.object(episode_routes.storage, "load_page", return_value=newer_week_blob):
        response = _call()
    body = _body(response)
    assert body["episode_id"] == "2026-W41"
    assert body["stage"] == "monday"


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
