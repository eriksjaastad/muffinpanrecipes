"""Sunday-publish teaser suppression — homepage Featured + teaser must not duplicate."""

import json
from unittest.mock import patch

import pytest

from backend.publishing import episode_renderer


@pytest.fixture(autouse=True)
def _missed_week_unpublished(monkeypatch):
    """The writer re-checks a week_off_note against the missed week's own
    episode (#7630). Default every test to "that week never published" so
    nothing here reads the real local episode store; tests of the re-check
    override it."""
    monkeypatch.setattr(episode_renderer.storage, "load_episode_verified", lambda _eid: None, raising=False)


@pytest.fixture(autouse=True)
def _pin_current_week(monkeypatch):
    """Every fixture in this file uses episode_id "2026-W18". Pin
    current_episode_id() to match so regenerate_and_upload's current-week
    gate (#7630) writes pages/latest.json exactly like it used to, whatever
    the real calendar date is when the suite runs. Tests exercising the gate
    itself override this within their own `with patch.object(...)` block."""
    monkeypatch.setattr(episode_renderer.episode_integrity, "current_episode_id", lambda: "2026-W18")


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



def test_no_teaser_and_no_note_still_overwrites_a_stale_latest_json():
    """Codex round 6 on #7630: with neither a teaser nor a note, the writer
    used to write nothing, so a note from an earlier write stayed on the
    homepage after it was cleared. It now writes the neutral shape."""
    episode = _episode({})

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save), \
         patch.object(episode_renderer, "render_episode_page", return_value="<html></html>"):
        episode_renderer.regenerate_and_upload(episode)

    assert json.loads(writes["pages/latest.json"]) == {"episode_id": "2026-W18"}


def _write_with(episode, missed_episode=None, missed_error=None):
    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    def fake_load(_eid):
        if missed_error:
            raise missed_error
        return missed_episode

    with patch.object(episode_renderer.storage, "save_page", side_effect=fake_save), \
         patch.object(episode_renderer.storage, "load_episode_verified", side_effect=fake_load), \
         patch.object(episode_renderer, "render_episode_page", return_value="<html></html>"):
        episode_renderer.regenerate_and_upload(episode)
    return json.loads(writes["pages/latest.json"])


def test_a_note_whose_missed_week_has_since_published_is_not_written():
    """Codex (#7630): a late publish of the missed week can clear the note
    while this cron is in flight; the write re-checks and drops it."""
    episode = _episode({})
    episode["week_off_note"] = _WEEK_OFF_NOTE
    payload = _write_with(episode, missed_episode={"published_at": "2026-04-26T23:00:00Z"})
    assert payload == {"episode_id": "2026-W18"}


def test_a_note_whose_missed_week_is_still_unpublished_is_written():
    episode = _episode({})
    episode["week_off_note"] = _WEEK_OFF_NOTE
    payload = _write_with(episode, missed_episode={"episode_id": "2026-W17"})
    assert payload["week_off_note"] == _WEEK_OFF_NOTE


def test_a_recheck_read_error_keeps_the_cron_time_decision():
    episode = _episode({})
    episode["week_off_note"] = _WEEK_OFF_NOTE
    payload = _write_with(episode, missed_error=RuntimeError("blob down"))
    assert payload["week_off_note"] == _WEEK_OFF_NOTE


def test_an_own_week_refusal_note_is_written_until_that_week_publishes():
    own = {"message": "x", "missed_week": "2026-W18"}
    episode = _episode({})
    episode["week_off_note"] = own
    assert _write_with(episode)["week_off_note"] == own

# ---------------------------------------------------------------------------
# pages/latest.json is global and belongs to the CURRENT ISO week only
# (#7630 — Codex review of 2d0567b/6b86ede). regenerate_and_upload is the
# single source of truth for this invariant: every caller (each day's cron,
# a manual force=true retry, scripts/fix_w36_category_cuisine.py) can hand it
# any episode, and it must render/upload THAT episode's own page regardless,
# but only ever touch the global teaser/published marker for the current week.
# ---------------------------------------------------------------------------

def test_regenerate_and_upload_skips_latest_json_for_a_non_current_week():
    """A manual force=true retry (or late publish) of an OLDER — or a
    not-yet-current — week must still render/upload that episode's own page,
    but must never replace the live homepage teaser."""
    episode = _episode({"monday": _stage_with_dialogue(), "sunday": _stage_with_dialogue()})

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.episode_integrity, "current_episode_id", return_value="2026-W19"), \
         patch.object(episode_renderer.storage, "save_page", side_effect=fake_save):
        url = episode_renderer.regenerate_and_upload(episode)

    assert "pages/2026-W18/index.html" in writes  # its own page, always
    assert "pages/latest.json" not in writes       # the global teaser, never
    assert url == "https://blob/pages/2026-W18/index.html"


def test_regenerate_and_upload_writes_latest_json_for_the_current_week():
    """Sanity check: the gate only skips a NON-current week — the ordinary
    case (this episode IS the current week) is unaffected."""
    episode = _episode({"monday": _stage_with_dialogue(), "saturday": _stage_with_dialogue()})

    writes: dict[str, str] = {}

    def fake_save(path, content):
        writes[path] = content
        return f"https://blob/{path}"

    with patch.object(episode_renderer.episode_integrity, "current_episode_id", return_value="2026-W18"), \
         patch.object(episode_renderer.storage, "save_page", side_effect=fake_save):
        episode_renderer.regenerate_and_upload(episode)

    assert "pages/latest.json" in writes
