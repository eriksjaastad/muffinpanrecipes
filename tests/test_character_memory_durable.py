"""Durable per-character memory (#6968).

Design (redesigned in the 2026-09-30 round-3 Codex review of f7a3b1f): ONE
durable blob per character PER WEEK — character_memory/<slug>/<YYYY-Www>.json
— written with a single PUT and never read first. There is no
read-modify-write and no merge, so a write can never be built on a stale or
missing read, and nothing is ever evicted or lost: retention ("show the
latest 2") is a read-time choice over the Blob LIST API, never a write-time
truncation. The legacy bundled backend/data/characters/<slug>/memory.json
files remain a READ-ONLY display fallback for a character with zero weeks
in durable storage — shown exactly as stored, never deduped (their
same-labelled "week" entries are distinct real histories mislabelled by the
old buggy writer), never merged into durable storage.

Round-3 review findings this file covers:
  1. A read-modify-write let a stale CDN-served body (Vercel documents up
     to 60s staleness after an overwrite) cause a write to silently drop
     another week. Fixed structurally: writes never read first.
  2. Deduping the legacy seed's 3 same-labelled "week" entries destroyed 2
     of 3 real, distinct histories. Fixed: the legacy fallback is shown
     exactly as stored, never deduped.
  3. A schema-valid-but-wrong-shape body (e.g. `{}`) was accepted as usable,
     empty history. Fixed: every write and read validates against
     validate_character_memory_entry; a violation is
     CharacterMemoryUnavailable, never quietly-empty.

Earlier rounds' findings this design also carries forward (now structural
rather than patched): prefix isolation (test/ vs production), no bundle
writes, per-character saved/absent/failed events, prompt-cache clearing per
run, repair command dry-run default + refuses unpublished, and the removed
false first-week fallback (known-coworker text unless MEMORY_ONBOARDING_PILOT).
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from pathlib import Path

from backend.admin import cron_routes
from tests.photo_review_helpers import approved_wednesday
from backend.storage import (
    CharacterMemoryUnavailable,
    is_valid_iso_week,
    parse_iso_week,
    validate_character_memory_entry,
)


@pytest.fixture(autouse=True)
def _test_env(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    # Bypass config.dialogue_model's Doppler requirement (same pattern as
    # tests/test_pick_concept.py's CONCEPT_MODEL fixture) — every real call
    # site here mocks generate_response, so the actual model string is
    # never sent anywhere.
    monkeypatch.setenv("DIALOGUE_MODEL", "test/fake-model")


# ---------------------------------------------------------------------------
# ISO week parsing/validation
# ---------------------------------------------------------------------------


def test_parse_iso_week_parses_real_weeks():
    assert parse_iso_week("2026-W40") == (2026, 40)
    assert parse_iso_week("2026-W01") == (2026, 1)


@pytest.mark.parametrize(
    "week",
    ["test-week", "not-a-week", "2026-40", "2026-W", "2026-W99", "", None, "2026-W1"],
)
def test_parse_iso_week_rejects_non_iso_labels(week):
    with pytest.raises(ValueError):
        parse_iso_week(week)


def test_is_valid_iso_week_matches_parse_iso_week():
    assert is_valid_iso_week("2026-W40") is True
    assert is_valid_iso_week("test-week") is False
    assert is_valid_iso_week(None) is False
    assert is_valid_iso_week(12345) is False


# ---------------------------------------------------------------------------
# validate_character_memory_entry — the schema gate for every write AND read
# (#6968 round-3 review finding 3)
# ---------------------------------------------------------------------------


def _entry(**overrides) -> dict:
    base = {"week": "2026-W40", "episode_id": "2026-W40", "summary": "A calm week."}
    base.update(overrides)
    return base


def test_validate_character_memory_entry_accepts_well_formed_entry():
    entry = _entry(concept="Test Concept", key_moment="Something happened.")
    assert validate_character_memory_entry(entry) == entry


@pytest.mark.parametrize(
    "bad_entry",
    [
        {},  # round-3 finding 3's exact example: valid JSON, wrong schema
        {"week": "2026-W40"},  # missing episode_id, summary
        {"week": "2026-W40", "episode_id": "2026-W40", "summary": ""},  # empty summary
        {"week": "2026-W40", "episode_id": "2026-W40", "summary": "   "},  # whitespace-only
        {"week": "test-week", "episode_id": "test-week", "summary": "x"},  # non-ISO week
        {"week": "2026-W40", "episode_id": 123, "summary": "x"},  # wrong type
        "not a dict",
        None,
        [],
    ],
)
def test_validate_character_memory_entry_rejects_malformed_shapes(bad_entry):
    with pytest.raises(ValueError):
        validate_character_memory_entry(bad_entry)


# ---------------------------------------------------------------------------
# Filesystem backend: per-week round-trip, prefix isolation, junk filenames,
# schema-invalid stored body, idempotent same-week overwrite
# ---------------------------------------------------------------------------


def test_filesystem_backend_character_memory_week_roundtrip(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    backend = storage_module._FilesystemBackend()

    assert backend.list_character_memory_weeks("margaret-chen") == []

    backend.save_character_memory_week("margaret-chen", "2026-W40", _entry(week="2026-W40", episode_id="2026-W40"))

    assert backend.list_character_memory_weeks("margaret-chen") == ["2026-W40"]
    loaded = backend.load_character_memory_week("margaret-chen", "2026-W40")
    assert loaded["week"] == "2026-W40"

    # Never touched the bundled, read-only source directory.
    bundled = storage_module.ROOT / "backend" / "data" / "characters" / "margaret-chen" / "memory.json"
    assert "2026-W40" not in bundled.read_text()


def test_filesystem_backend_keeps_multiple_weeks_no_eviction(tmp_path, monkeypatch):
    """Nothing is ever deleted - retention is a read-time choice (#6968 round 3)."""
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    backend = storage_module._FilesystemBackend()

    for week in ("2026-W11", "2026-W37", "2026-W38", "2026-W39", "2026-W40"):
        backend.save_character_memory_week("margaret-chen", week, _entry(week=week, episode_id=week))

    assert backend.list_character_memory_weeks("margaret-chen") == [
        "2026-W11", "2026-W37", "2026-W38", "2026-W39", "2026-W40",
    ]


def test_filesystem_backend_same_week_rewrite_is_idempotent_overwrite(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    backend = storage_module._FilesystemBackend()

    backend.save_character_memory_week("margaret-chen", "2026-W40", _entry(summary="first pass"))
    backend.save_character_memory_week("margaret-chen", "2026-W40", _entry(summary="second pass, same week"))

    assert backend.list_character_memory_weeks("margaret-chen") == ["2026-W40"]
    assert backend.load_character_memory_week("margaret-chen", "2026-W40")["summary"] == "second pass, same week"


def test_filesystem_backend_scopes_by_prefix(tmp_path, monkeypatch):
    """Round-3 (carried forward): prod and test-prefix data must not cross."""
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    backend = storage_module._FilesystemBackend()

    backend.save_character_memory_week("margaret-chen", "2026-W40", _entry())
    with backend.prefix_scope("test/"):
        assert backend.list_character_memory_weeks("margaret-chen") == []
        backend.save_character_memory_week(
            "margaret-chen", "2026-W41", _entry(week="2026-W41", episode_id="2026-W41")
        )
        assert backend.list_character_memory_weeks("margaret-chen") == ["2026-W41"]

    # Back under prod scope: the test-prefix write is invisible, prod data intact.
    assert backend.list_character_memory_weeks("margaret-chen") == ["2026-W40"]


def test_filesystem_backend_ignores_junk_filenames_in_listing(tmp_path, monkeypatch):
    """Round-3 review: a listed name that can't be parsed as ISO is ignored,
    not treated as absence for the real weeks alongside it."""
    import backend.storage as storage_module

    mem_dir = tmp_path / "character_memory"
    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", mem_dir)
    backend = storage_module._FilesystemBackend()

    backend.save_character_memory_week("margaret-chen", "2026-W40", _entry())
    junk_dir = mem_dir / "margaret-chen"
    junk_dir.mkdir(parents=True, exist_ok=True)
    (junk_dir / "test-week.json").write_text(json.dumps(_entry(week="test-week", episode_id="test-week")))
    (junk_dir / "not-a-week-either.json").write_text("{}")

    weeks = backend.list_character_memory_weeks("margaret-chen")

    assert weeks == ["2026-W40"]


def test_filesystem_backend_schema_invalid_stored_body_raises_unavailable(tmp_path, monkeypatch):
    """Round-3 review finding 3: a valid-JSON-but-wrong-shape stored body
    (e.g. `{}`) must raise, never be treated as usable empty history."""
    import backend.storage as storage_module

    mem_dir = tmp_path / "character_memory"
    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", mem_dir)
    backend = storage_module._FilesystemBackend()

    char_dir = mem_dir / "margaret-chen"
    char_dir.mkdir(parents=True, exist_ok=True)
    (char_dir / "2026-W40.json").write_text("{}")

    with pytest.raises(CharacterMemoryUnavailable):
        backend.load_character_memory_week("margaret-chen", "2026-W40")


def test_filesystem_backend_corrupted_json_raises_unavailable(tmp_path, monkeypatch):
    import backend.storage as storage_module

    mem_dir = tmp_path / "character_memory"
    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", mem_dir)
    backend = storage_module._FilesystemBackend()

    char_dir = mem_dir / "margaret-chen"
    char_dir.mkdir(parents=True, exist_ok=True)
    (char_dir / "2026-W40.json").write_text("{not valid json")

    with pytest.raises(CharacterMemoryUnavailable):
        backend.load_character_memory_week("margaret-chen", "2026-W40")


def test_filesystem_backend_save_rejects_malformed_entry():
    import backend.storage as storage_module

    backend = storage_module._FilesystemBackend()
    with pytest.raises(ValueError):
        backend.save_character_memory_week("margaret-chen", "2026-W40", {})


def test_filesystem_backend_save_rejects_week_mismatch():
    import backend.storage as storage_module

    backend = storage_module._FilesystemBackend()
    with pytest.raises(ValueError):
        backend.save_character_memory_week("margaret-chen", "2026-W41", _entry(week="2026-W40"))


# ---------------------------------------------------------------------------
# Cloud backend
# ---------------------------------------------------------------------------


@pytest.fixture
def cloud_backend():
    from backend.storage import _CloudBackend

    with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake-token-for-test"}):
        return _CloudBackend()


def test_cloud_save_character_memory_week_uses_prefixed_key(cloud_backend):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"url": "https://example.com/character_memory/test/margaret-chen/2026-W40.json"}
    mock_resp.raise_for_status = MagicMock()

    cloud_backend.set_prefix("test/")
    with patch("requests.put", return_value=mock_resp) as mock_put:
        cloud_backend.save_character_memory_week("margaret-chen", "2026-W40", _entry())

    url = mock_put.call_args[0][0]
    assert "test/character_memory/margaret-chen/2026-W40.json" in url


def test_cloud_save_character_memory_week_rejects_malformed_entry(cloud_backend):
    with patch("requests.put") as mock_put:
        with pytest.raises(ValueError):
            cloud_backend.save_character_memory_week("margaret-chen", "2026-W40", {})
    mock_put.assert_not_called()


def test_cloud_save_character_memory_week_raises_on_failure(cloud_backend):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("500 Server Error")

    with patch("requests.put", return_value=mock_resp):
        with pytest.raises(Exception, match="500 Server Error"):
            cloud_backend.save_character_memory_week("margaret-chen", "2026-W40", _entry())


def test_cloud_list_character_memory_weeks_ignores_junk_names(cloud_backend):
    """Round-3 review: a listed pathname that isn't a real ISO week is
    logged and ignored, not treated as absence for the real weeks."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "blobs": [
            {"pathname": "character_memory/margaret-chen/2026-W40.json"},
            {"pathname": "character_memory/margaret-chen/test-week.json"},
            {"pathname": "character_memory/margaret-chen/not-a-week.json"},
        ],
        "hasMore": False,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        weeks = cloud_backend.list_character_memory_weeks("margaret-chen")

    assert weeks == ["2026-W40"]


def test_cloud_list_character_memory_weeks_sorts_chronologically(cloud_backend):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "blobs": [
            {"pathname": "character_memory/margaret-chen/2026-W40.json"},
            {"pathname": "character_memory/margaret-chen/2026-W11.json"},
            {"pathname": "character_memory/margaret-chen/2026-W38.json"},
        ],
        "hasMore": False,
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        weeks = cloud_backend.list_character_memory_weeks("margaret-chen")

    assert weeks == ["2026-W11", "2026-W38", "2026-W40"]


def test_cloud_list_character_memory_weeks_empty_is_genuine_not_found(cloud_backend):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"blobs": [], "hasMore": False}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        assert cloud_backend.list_character_memory_weeks("margaret-chen") == []


def test_cloud_list_character_memory_weeks_malformed_payload_raises_unavailable(cloud_backend):
    """Round-3 review finding 3 extends the round-2 malformed-payload guard
    to the per-week listing call too."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"unexpected": "shape"}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.list_character_memory_weeks("margaret-chen")


def _list_page(blobs, has_more, cursor=None):
    resp = MagicMock()
    payload = {"blobs": blobs, "hasMore": has_more}
    if cursor is not None:
        payload["cursor"] = cursor
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.parametrize("bad_cursor", [None, "", 7])
def test_cloud_list_has_more_without_a_usable_cursor_raises_not_loops(cloud_backend, bad_cursor):
    """Codex round 4: hasMore with no new cursor used to re-request page one
    forever inside a live cron."""
    page = _list_page([{"pathname": "character_memory/margaret-chen/2026-W40.json"}], True, bad_cursor)
    with patch("requests.get", return_value=page) as get:
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.list_character_memory_weeks("margaret-chen")
    assert get.call_count == 1


def test_cloud_list_repeated_cursor_raises_not_loops(cloud_backend):
    page = _list_page([], True, "same")
    with patch("requests.get", return_value=page) as get:
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.list_character_memory_weeks("margaret-chen")
    assert get.call_count == 2


def test_cloud_list_follows_a_real_cursor_across_pages(cloud_backend):
    pages = [
        _list_page([{"pathname": "character_memory/margaret-chen/2026-W39.json"}], True, "c1"),
        _list_page([{"pathname": "character_memory/margaret-chen/2026-W40.json"}], False),
    ]
    with patch("requests.get", side_effect=pages):
        assert cloud_backend.list_character_memory_weeks("margaret-chen") == ["2026-W39", "2026-W40"]


def test_local_memory_files_are_gitignored():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    for path in ("data/character_memory/margaret-chen/2026-W40.json",
                 "data/character_memory/test/margaret-chen/2026-W40.json"):
        proc = subprocess.run(["git", "check-ignore", "-q", path], cwd=root, capture_output=True, timeout=30)
        assert proc.returncode == 0, path


def test_cloud_list_character_memory_weeks_network_failure_raises_unavailable(cloud_backend):
    with patch("requests.get", side_effect=ConnectionError("blob API unreachable")):
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.list_character_memory_weeks("margaret-chen")


def test_cloud_load_character_memory_week_fetches_and_validates(cloud_backend):
    mock_list = MagicMock()
    mock_list.json.return_value = {"blobs": [{"url": "https://cdn.example.com/margaret-2026-W40.json", "pathname": "character_memory/margaret-chen/2026-W40.json"}]}
    mock_list.raise_for_status = MagicMock()
    mock_content = MagicMock()
    mock_content.json.return_value = _entry()
    mock_content.raise_for_status = MagicMock()

    with patch("requests.get", side_effect=[mock_list, mock_content]):
        result = cloud_backend.load_character_memory_week("margaret-chen", "2026-W40")

    assert result == _entry()


def test_cloud_load_character_memory_week_not_found_raises_unavailable(cloud_backend):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"blobs": [], "hasMore": False}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.load_character_memory_week("margaret-chen", "2026-W40")


def test_cloud_load_character_memory_week_schema_violation_raises_unavailable(cloud_backend):
    """Round-3 review finding 3, on the fetch side: `{}` is valid JSON but
    not a valid memory body."""
    mock_list = MagicMock()
    mock_list.json.return_value = {"blobs": [{"url": "https://cdn.example.com/margaret-2026-W40.json", "pathname": "character_memory/margaret-chen/2026-W40.json"}]}
    mock_list.raise_for_status = MagicMock()
    mock_content = MagicMock()
    mock_content.json.return_value = {}
    mock_content.raise_for_status = MagicMock()

    with patch("requests.get", side_effect=[mock_list, mock_content]):
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.load_character_memory_week("margaret-chen", "2026-W40")


def test_cloud_load_character_memory_week_content_fetch_failure_raises_unavailable(cloud_backend):
    mock_list = MagicMock()
    mock_list.json.return_value = {"blobs": [{"url": "https://cdn.example.com/margaret-2026-W40.json", "pathname": "character_memory/margaret-chen/2026-W40.json"}]}
    mock_list.raise_for_status = MagicMock()
    mock_content = MagicMock()
    mock_content.raise_for_status.side_effect = Exception("503 Service Unavailable")

    with patch("requests.get", side_effect=[mock_list, mock_content]):
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.load_character_memory_week("margaret-chen", "2026-W40")


def test_cloud_backend_has_no_character_memory_cache(cloud_backend):
    """Round-3 review finding 1: the cache itself is removed, not just
    bypassed — there is nothing left that could ever serve stale data
    across calls, because writes never read first."""
    assert not hasattr(cloud_backend, "_character_memory_cache")


# ---------------------------------------------------------------------------
# The stale-CDN scenario is now structurally impossible (#6968 round-3
# review finding 1): a write never reads any existing data, so a repair
# writing an older/different week cannot drop a week a different process
# just wrote, regardless of CDN staleness.
# ---------------------------------------------------------------------------


def test_save_character_memory_week_never_reads_before_writing(cloud_backend):
    """The exact round-3 finding 1 scenario: repair writes W39, Sunday
    writes W40 shortly after. Proven structurally by asserting the save
    path never calls requests.get at all — there is no read to be stale."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"url": "https://example.com/character_memory/margaret-chen/2026-W40.json"}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.put", return_value=mock_resp), patch("requests.get") as mock_get:
        cloud_backend.save_character_memory_week("margaret-chen", "2026-W40", _entry())

    mock_get.assert_not_called()


def test_repair_then_sunday_write_different_weeks_do_not_interact(tmp_path, monkeypatch):
    """A repair writing W39 followed shortly by Sunday writing W40 leaves
    both blobs intact — there is no merge step for a stale read to corrupt."""
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    backend = storage_module._FilesystemBackend()

    backend.save_character_memory_week(
        "margaret-chen", "2026-W39",
        _entry(week="2026-W39", episode_id="2026-W39", summary="repair wrote W39"),
    )
    backend.save_character_memory_week(
        "margaret-chen", "2026-W40",
        _entry(week="2026-W40", episode_id="2026-W40", summary="sunday wrote W40"),
    )

    assert backend.list_character_memory_weeks("margaret-chen") == ["2026-W39", "2026-W40"]
    assert backend.load_character_memory_week("margaret-chen", "2026-W39")["summary"] == "repair wrote W39"
    assert backend.load_character_memory_week("margaret-chen", "2026-W40")["summary"] == "sunday wrote W40"


# ---------------------------------------------------------------------------
# cron_routes._generate_episode_memories: per-character saved/absent/failed,
# dry-run, write failure, never reads the bundled file, never reads before
# writing
# ---------------------------------------------------------------------------


def _episode_with_dialogue(episode_id: str, speakers: dict[str, list[str]]) -> dict:
    stages: dict = {day: {"dialogue": []} for day in cron_routes.DAY_ORDER}
    for day, chars in speakers.items():
        stages[day] = {
            "dialogue": [
                {"character": name, "day": day, "message": f"{name} says something about {episode_id}."}
                for name in chars
            ]
        }
    return {"episode_id": episode_id, "stages": stages, "events": []}


def test_generate_episode_memories_reports_saved_absent_and_failed(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")

    # Full roster minus Devon Park and Julian Torres so they show up absent.
    episode = _episode_with_dialogue(
        "2026-W40",
        {
            "monday": ["Margaret Chen", "Marcus Reid", "Stephanie 'Steph' Whitmore", "Ria Castillo"],
            "thursday": ["Marcus Reid"],
        },
    )

    def _flaky_generate(prompt, **_kw):
        if "Marcus Reid" in prompt:
            raise RuntimeError("provider outage")
        return "A calm week. Everyone agreed quickly."

    with patch.object(cron_routes, "generate_response", side_effect=_flaky_generate):
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept")

    assert set(outcome["saved"]) == {"Margaret Chen", "Stephanie 'Steph' Whitmore", "Ria Castillo"}
    assert set(outcome["failed"]) == {"Marcus Reid"}
    assert "Devon Park" in outcome["absent"]
    assert "Julian Torres" in outcome["absent"]

    saved = storage_module.storage.list_character_memory_weeks("margaret-chen")
    assert saved == ["2026-W40"]


def test_generate_episode_memories_dry_run_writes_nothing(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})

    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."):
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept", dry_run=True)

    assert "Margaret Chen" in outcome["saved"]
    assert storage_module.storage.list_character_memory_weeks("margaret-chen") == []


def test_generate_episode_memories_write_failure_is_reported_not_claimed(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})

    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."), \
         patch.object(storage_module.storage, "save_character_memory_week", side_effect=RuntimeError("blob down")):
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept")

    assert "Margaret Chen" in outcome["failed"]
    assert "Margaret Chen" not in outcome["saved"]


def test_generate_episode_memories_never_reads_bundled_file(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    bundled = storage_module.ROOT / "backend" / "data" / "characters" / "margaret-chen" / "memory.json"
    before = bundled.read_text()
    before_mtime = bundled.stat().st_mtime

    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})
    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."):
        cron_routes._generate_episode_memories(episode, "Test Concept")

    assert bundled.read_text() == before
    assert bundled.stat().st_mtime == before_mtime


def test_generate_episode_memories_does_not_read_before_writing(tmp_path, monkeypatch):
    """Round-3 review finding 1, at the production call site: the writer
    never lists or fetches existing memory before saving."""
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})

    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."), \
         patch.object(storage_module.storage, "list_character_memory_weeks") as mock_list, \
         patch.object(storage_module.storage, "load_character_memory_week") as mock_load:
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept")

    assert "Margaret Chen" in outcome["saved"]
    mock_list.assert_not_called()
    mock_load.assert_not_called()


def test_generate_episode_memories_replay_same_week_is_idempotent(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})

    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."):
        cron_routes._generate_episode_memories(episode, "Test Concept")
        cron_routes._generate_episode_memories(episode, "Test Concept")

    assert storage_module.storage.list_character_memory_weeks("margaret-chen") == ["2026-W40"]


def test_cron_sunday_records_memory_events(monkeypatch):
    """cron_sunday folds the outcome dict into episode events (#6968)."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    episode = {
        "episode_id": "2026-W40",
        "concept": "Test Concept",
        "recipe_id": None,
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Test"}},
            "wednesday": approved_wednesday(episode_id="2026-W40"),
        },
        "events": [],
    }

    def _request():
        return SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/cron/sunday"))

    def _body():
        return cron_routes.StageRequest(episode_id="2026-W40", force=True)

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=_body())), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=([], "STATUS: PASS")), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_complete_static_source_handoff"), \
         patch.object(
             cron_routes,
             "_generate_episode_memories",
             return_value={"saved": ["Margaret Chen"], "absent": ["Devon Park"], "failed": ["Marcus Reid"]},
         ), \
         patch.object(cron_routes, "_indexnow_submit_urls"):  # #7806: no network in tests
        asyncio.run(cron_routes.cron_sunday(_request()))

    events = " | ".join(episode["events"])
    assert "memory saved for Margaret Chen" in events
    assert "memory absent (no dialogue) for Devon Park" in events
    assert "memory generation failed for Marcus Reid" in events


# ---------------------------------------------------------------------------
# simulate_dialogue_week: legacy fallback (undeduped), unavailable vs
# genuinely-empty, first-episode determination, prompt cache invalidation
# ---------------------------------------------------------------------------


def _persona(name: str) -> dict:
    return {
        "name": name,
        "role": "Social Media Manager",
        "communication_style": {"signature_phrases": []},
        "internal_contradictions": [],
        "relationships": {},
        "triggers": [],
        "backstory": "x" * 10,
    }


def test_legacy_seed_three_distinct_recipes_shown_without_loss(tmp_path, monkeypatch):
    """Round-3 review finding 2, the exact worked example: margaret-chen's
    real legacy file has 3 entries all labelled "week": "2026-W11" that are
    genuinely 3 different recipes. Durable storage is empty, so the
    fallback must show them AS STORED - not deduped down to 1."""
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")

    legacy = json.loads(
        (storage_module.ROOT / "backend" / "data" / "characters" / "margaret-chen" / "memory.json").read_text()
    )
    assert len(legacy["episodes"]) == 3
    assert len({e["concept"] for e in legacy["episodes"]}) == 3  # genuinely distinct

    entries = sdw._load_legacy_memory_entries("margaret-chen")

    # The last 2 in file order, exactly as stored - concepts intact, week
    # labels left exactly as-is (known-bad, never parsed/sorted here).
    assert len(entries) == 2
    assert entries == legacy["episodes"][-2:]
    assert entries[0]["week"] == "2026-W11"
    assert entries[1]["week"] == "2026-W11"
    assert entries[0]["concept"] != entries[1]["concept"]


def test_load_memories_or_unavailable_falls_back_to_legacy_when_durable_empty(tmp_path, monkeypatch):
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")

    episodes, unavailable = sdw._load_memories_or_unavailable("Margaret Chen")

    assert unavailable is False
    assert len(episodes) == 2  # legacy fallback, undeduped


def test_load_memories_or_unavailable_uses_durable_once_it_has_weeks(tmp_path, monkeypatch):
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    for week in ("2026-W38", "2026-W39", "2026-W40"):
        storage_module.storage.save_character_memory_week(
            "margaret-chen", week, _entry(week=week, episode_id=week, summary=f"summary-{week}")
        )

    episodes, unavailable = sdw._load_memories_or_unavailable("Margaret Chen")

    assert unavailable is False
    # Durable storage is authoritative once it has anything; legacy is not
    # consulted. Shows the 2 most recent weeks.
    assert [e["week"] for e in episodes] == ["2026-W39", "2026-W40"]


def test_load_memories_or_unavailable_out_of_order_repair_shows_correctly(tmp_path, monkeypatch):
    """An older week written after newer ones (a repair) is still ordered
    correctly for display - listing sorts by parsed week, not write order."""
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    storage_module.storage.save_character_memory_week(
        "margaret-chen", "2026-W40", _entry(week="2026-W40", episode_id="2026-W40")
    )
    storage_module.storage.save_character_memory_week(
        "margaret-chen", "2026-W39", _entry(week="2026-W39", episode_id="2026-W39")
    )  # written second, but chronologically earlier

    episodes, unavailable = sdw._load_memories_or_unavailable("Margaret Chen")

    assert unavailable is False
    assert [e["week"] for e in episodes] == ["2026-W39", "2026-W40"]


def test_load_memories_or_unavailable_listing_failure_is_unavailable(monkeypatch):
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    with patch.object(
        storage_module.storage, "list_character_memory_weeks",
        side_effect=CharacterMemoryUnavailable("simulated Blob outage"),
    ):
        episodes, unavailable = sdw._load_memories_or_unavailable("Margaret Chen")

    assert episodes == []
    assert unavailable is True


def test_load_memories_or_unavailable_unreadable_legacy_seed_is_unavailable(tmp_path, monkeypatch):
    """A corrupt legacy seed is a read failure, not "no history" — it must not
    feed the false first-meeting opener (silent-failure sweep, #7587)."""
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    chars = tmp_path / "characters"
    (chars / "margaret-chen").mkdir(parents=True)
    (chars / "margaret-chen" / "memory.json").write_text("{not json")
    monkeypatch.setattr(sdw, "CHARACTERS_DIR", chars)

    episodes, unavailable = sdw._load_memories_or_unavailable("Margaret Chen")

    assert episodes == []
    assert unavailable is True


def test_load_memories_or_unavailable_absent_legacy_seed_is_genuinely_empty(tmp_path, monkeypatch):
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    monkeypatch.setattr(sdw, "CHARACTERS_DIR", tmp_path / "characters")

    episodes, unavailable = sdw._load_memories_or_unavailable("Margaret Chen")

    assert episodes == []
    assert unavailable is False


def test_load_memories_or_unavailable_fetch_failure_is_unavailable(tmp_path, monkeypatch):
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    storage_module.storage.save_character_memory_week(
        "margaret-chen", "2026-W40", _entry(week="2026-W40", episode_id="2026-W40")
    )

    with patch.object(
        storage_module.storage, "load_character_memory_week",
        side_effect=CharacterMemoryUnavailable("simulated fetch failure"),
    ):
        episodes, unavailable = sdw._load_memories_or_unavailable("Margaret Chen")

    assert episodes == []
    assert unavailable is True


def test_load_memories_falls_back_to_known_coworker_on_unavailable(monkeypatch):
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    with patch.object(
        storage_module.storage, "list_character_memory_weeks",
        side_effect=CharacterMemoryUnavailable("simulated Blob outage"),
    ):
        assert sdw._load_memories("Margaret Chen") == []


def test_is_genuinely_first_episode_true_only_when_all_empty_and_none_unavailable():
    import scripts.simulate_dialogue_week as sdw

    assert sdw._is_genuinely_first_episode([([], False), ([], False)]) is True
    assert sdw._is_genuinely_first_episode([([], False), ([{"week": "2026-W40"}], False)]) is False


def test_is_genuinely_first_episode_false_on_any_unavailable_read():
    """The round-2 finding 3 scenario, still true under the redesign: every
    character's read failing must not look like a genuine premiere week."""
    import scripts.simulate_dialogue_week as sdw

    assert sdw._is_genuinely_first_episode([([], True), ([], True), ([], True)]) is False
    assert sdw._is_genuinely_first_episode([([], False), ([], False), ([], True)]) is False


def test_empty_memory_gets_known_coworker_fallback_not_first_week(monkeypatch):
    import scripts.simulate_dialogue_week as sdw

    monkeypatch.setattr(sdw, "MEMORY_ONBOARDING_PILOT", False)
    monkeypatch.setattr(sdw, "_load_memories", lambda name: [])
    sdw._system_prompt_cache.clear()

    prompt = sdw.build_system_prompt(_persona("Ria Castillo"))

    assert "THIS IS YOUR FIRST WEEK" not in prompt
    assert "established coworkers" in prompt


def test_pilot_flag_restores_first_week_block(monkeypatch):
    import scripts.simulate_dialogue_week as sdw

    monkeypatch.setattr(sdw, "MEMORY_ONBOARDING_PILOT", True)
    monkeypatch.setattr(sdw, "_load_memories", lambda name: [])
    sdw._system_prompt_cache.clear()

    prompt = sdw.build_system_prompt(_persona("Ria Castillo"))

    assert "THIS IS YOUR FIRST WEEK ON THE JOB" in prompt


def test_returning_character_with_memory_never_sees_first_week_text(monkeypatch):
    import scripts.simulate_dialogue_week as sdw

    monkeypatch.setattr(sdw, "MEMORY_ONBOARDING_PILOT", False)
    monkeypatch.setattr(
        sdw, "_load_memories",
        lambda name: [{"concept": "Lemon Tarts", "summary": "Ria pushed for bolder captions."}],
    )
    sdw._system_prompt_cache.clear()

    prompt = sdw.build_system_prompt(_persona("Ria Castillo"))

    assert "THIS IS YOUR FIRST WEEK" not in prompt
    assert "Ria pushed for bolder captions" in prompt


def test_system_prompt_cache_cleared_between_runs_picks_up_new_memory(monkeypatch):
    """A warm process must never serve a stale prompt across runs (#6968)."""
    import scripts.simulate_dialogue_week as sdw

    persona = _persona("Ria Castillo")
    memories = [[]]  # first run: no memory

    monkeypatch.setattr(sdw, "_load_memories", lambda name: memories[0])
    monkeypatch.setattr(sdw, "MEMORY_ONBOARDING_PILOT", False)

    sdw._system_prompt_cache.clear()
    first = sdw.build_system_prompt(persona)
    assert "established coworkers" in first

    # New week's memory becomes available — simulate what run_simulation()
    # does at the top of every call.
    memories[0] = [{"concept": "Lemon Tarts", "summary": "Ria shipped a strong caption."}]
    sdw._system_prompt_cache.clear()
    second = sdw.build_system_prompt(persona)

    assert "Ria shipped a strong caption" in second
    assert second != first


# ---------------------------------------------------------------------------
# Repair / backfill command
# ---------------------------------------------------------------------------


def test_repair_script_dry_run_prints_without_writing(tmp_path, monkeypatch, capsys):
    import backend.storage as storage_module
    from scripts import repair_character_memory as repair

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")

    episode = {
        "episode_id": "2026-W40",
        "published_at": "2026-10-04T12:00:00+00:00",
        "concept": "Test Concept",
        "stages": {"monday": {"dialogue": [{"character": "Margaret Chen", "day": "monday", "message": "Locked it in."}]}},
    }

    with patch.object(storage_module.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."):
        exit_code = repair.main(["2026-W40"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "would save: Margaret Chen" in out
    assert storage_module.storage.list_character_memory_weeks("margaret-chen") == []


def test_repair_script_refuses_unpublished_episode(monkeypatch, capsys):
    import backend.storage as storage_module
    from scripts import repair_character_memory as repair

    episode = {"episode_id": "2026-W41", "stages": {}}  # no published_at

    with patch.object(storage_module.storage, "load_episode", return_value=episode):
        exit_code = repair.main(["2026-W41"])

    assert exit_code == 1
    assert "SKIPPED" in capsys.readouterr().out


def test_repair_script_apply_actually_writes(tmp_path, monkeypatch):
    import backend.storage as storage_module
    from scripts import repair_character_memory as repair

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")

    episode = {
        "episode_id": "2026-W40",
        "published_at": "2026-10-04T12:00:00+00:00",
        "concept": "Test Concept",
        "stages": {"monday": {"dialogue": [{"character": "Margaret Chen", "day": "monday", "message": "Locked it in."}]}},
    }

    with patch.object(storage_module.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."):
        exit_code = repair.main(["2026-W40", "--apply"])

    assert exit_code == 0
    assert storage_module.storage.list_character_memory_weeks("margaret-chen") == ["2026-W40"]


def test_repair_script_same_week_rewrite_is_idempotent(tmp_path, monkeypatch):
    """Round-3 review: replaying the same week's repair overwrites only
    that week's own blob - no growth, no duplication, no loss of other weeks."""
    import backend.storage as storage_module
    from scripts import repair_character_memory as repair

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    # A different, already-existing week must survive untouched.
    storage_module.storage.save_character_memory_week(
        "margaret-chen", "2026-W39", _entry(week="2026-W39", episode_id="2026-W39", summary="untouched")
    )

    episode = {
        "episode_id": "2026-W40",
        "published_at": "2026-10-04T12:00:00+00:00",
        "concept": "Test Concept",
        "stages": {"monday": {"dialogue": [{"character": "Margaret Chen", "day": "monday", "message": "Locked it in."}]}},
    }

    with patch.object(storage_module.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."):
        repair.main(["2026-W40", "--apply"])
        repair.main(["2026-W40", "--apply"])

    assert storage_module.storage.list_character_memory_weeks("margaret-chen") == ["2026-W39", "2026-W40"]
    assert storage_module.storage.load_character_memory_week("margaret-chen", "2026-W39")["summary"] == "untouched"
