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
