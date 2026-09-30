"""Durable per-character memory (#6968).

Covers: idempotent same-week replay, three-distinct-week retention with a
two-week prompt display, prefix isolation (RUNBOOK Incident 1), the
read-only-bundle-causes-no-local-write contract, per-character
success/absence/failure visibility, the removed false first-week fallback,
system-prompt-cache invalidation across runs, and the repair/backfill
command.

Also covers the 2026-09-30 Codex review of 29d89bb (three findings, all
fixed in this file's companion commit):
  1. A failed durable-memory READ (not a genuine not-found) must never be
     treated as "seed from the legacy file" — CharacterMemoryUnavailable.
  2. Cloud-mode reads/writes must never fall back to or mirror onto the
     prefix-blind local filesystem path; filesystem-mode itself must scope
     its path by prefix.
  3. Retention and display order by the week key, not write order — an
     out-of-order repair of an older week must not evict a newer one.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.admin import cron_routes
from backend.storage import CharacterMemoryUnavailable, merge_character_memory


# ---------------------------------------------------------------------------
# merge_character_memory — pure logic, no I/O
# ---------------------------------------------------------------------------


def test_merge_character_memory_same_week_replay_is_idempotent():
    existing = {"episodes": [{"week": "2026-W40", "summary": "first pass"}], "last_updated": "2026-W40"}
    replayed = merge_character_memory(existing, {"week": "2026-W40", "summary": "second pass, same week"})

    assert len(replayed["episodes"]) == 1
    assert replayed["episodes"][0]["summary"] == "second pass, same week"


def test_merge_character_memory_keeps_three_distinct_weeks():
    data = None
    for week in ("2026-W37", "2026-W38", "2026-W39", "2026-W40"):
        data = merge_character_memory(data, {"week": week, "summary": f"summary for {week}"})

    weeks = [e["week"] for e in data["episodes"]]
    assert weeks == ["2026-W38", "2026-W39", "2026-W40"]
    assert data["last_updated"] == "2026-W40"


def test_merge_character_memory_requires_week_key():
    with pytest.raises(ValueError):
        merge_character_memory(None, {"summary": "no week key"})


def test_merge_character_memory_orders_by_week_not_insertion_order():
    """Review finding 3: retention/order is by the week key, not write order."""
    data = None
    for week in ("2026-W39", "2026-W37", "2026-W40", "2026-W38"):
        data = merge_character_memory(data, {"week": week, "summary": f"summary for {week}"})

    weeks = [e["week"] for e in data["episodes"]]
    # Only 3 are retained, and they are the 3 chronologically newest, in
    # chronological order - not the last 3 inserted.
    assert weeks == ["2026-W38", "2026-W39", "2026-W40"]
    assert data["last_updated"] == "2026-W40"


def test_merge_character_memory_repair_of_older_week_does_not_evict_newer(tmp_path):
    """Review finding 3, worked example: store W38-W40, repair W11.

    W11 must not displace any of the three newer weeks, and must not become
    last_updated even though it was the entry just written.
    """
    data = None
    for week in ("2026-W38", "2026-W39", "2026-W40"):
        data = merge_character_memory(data, {"week": week, "summary": f"summary for {week}"})

    repaired = merge_character_memory(data, {"week": "2026-W11", "summary": "backfilled ancient week"})

    weeks = [e["week"] for e in repaired["episodes"]]
    assert weeks == ["2026-W38", "2026-W39", "2026-W40"]
    assert "2026-W11" not in weeks
    assert repaired["last_updated"] == "2026-W40"


def test_merge_character_memory_repair_of_middle_week_is_kept_and_shown_third():
    """Review finding 3, worked example: only W39-W40 exist, repair W37.

    W37 is older than both but must be kept (only 2 slots occupied) and
    take its chronological place (shown third when there is a third slot).
    """
    data = None
    for week in ("2026-W39", "2026-W40"):
        data = merge_character_memory(data, {"week": week, "summary": f"summary for {week}"})

    repaired = merge_character_memory(data, {"week": "2026-W37", "summary": "backfilled W37"})

    weeks = [e["week"] for e in repaired["episodes"]]
    assert weeks == ["2026-W37", "2026-W39", "2026-W40"]
    assert repaired["last_updated"] == "2026-W40"


# ---------------------------------------------------------------------------
# Filesystem backend round-trip + read-only-bundle isolation
# ---------------------------------------------------------------------------


def test_filesystem_backend_character_memory_roundtrip(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    backend = storage_module._FilesystemBackend()

    assert backend.load_character_memory("margaret-chen") is None

    backend.save_character_memory("margaret-chen", {"episodes": [{"week": "2026-W40"}], "last_updated": "2026-W40"})
    loaded = backend.load_character_memory("margaret-chen")
    assert loaded["last_updated"] == "2026-W40"

    # And it never touched the bundled, read-only source directory.
    bundled = storage_module.ROOT / "backend" / "data" / "characters" / "margaret-chen" / "memory.json"
    original = bundled.read_text()
    assert "2026-W40" not in original


def test_filesystem_backend_scopes_character_memory_by_prefix(tmp_path, monkeypatch):
    """Review finding 2: prod and test-prefix data must not cross, filesystem mode."""
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    backend = storage_module._FilesystemBackend()

    backend.save_character_memory("margaret-chen", {"episodes": [{"week": "2026-W40"}], "last_updated": "2026-W40"})
    with backend.prefix_scope("test/"):
        # Saved under prod ("") is not visible under test/.
        assert backend.load_character_memory("margaret-chen") is None
        backend.save_character_memory("margaret-chen", {"episodes": [{"week": "test-week"}], "last_updated": "test-week"})
        assert backend.load_character_memory("margaret-chen")["last_updated"] == "test-week"

    # Back under prod scope, the test-prefix write is not visible, and the
    # original prod data is unchanged.
    prod_data = backend.load_character_memory("margaret-chen")
    assert prod_data["last_updated"] == "2026-W40"


def test_filesystem_backend_read_failure_raises_not_none(tmp_path, monkeypatch):
    """Review finding 1: a corrupted local file is a read failure, not "not found"."""
    import backend.storage as storage_module

    mem_dir = tmp_path / "character_memory"
    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", mem_dir)
    backend = storage_module._FilesystemBackend()

    mem_dir.mkdir(parents=True)
    (mem_dir / "margaret-chen.json").write_text("{not valid json")

    with pytest.raises(storage_module.CharacterMemoryUnavailable):
        backend.load_character_memory("margaret-chen")


def test_cron_routes_never_writes_bundled_memory_file(tmp_path, monkeypatch):
    """A read-only bundle must cause no local write (#6968 verification).

    The bundled backend/data/characters/margaret-chen/memory.json is
    read-only in the Vercel Lambda; the production writer must never touch
    it at all, writing only through durable storage (redirected to
    tmp_path here).
    """
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")

    bundled = storage_module.ROOT / "backend" / "data" / "characters" / "margaret-chen" / "memory.json"
    before = bundled.read_text()
    before_mtime = bundled.stat().st_mtime

    episode = {
        "episode_id": "2026-W40",
        "stages": {
            "monday": {"dialogue": [{"character": "Margaret Chen", "day": "monday", "message": "Let's do it."}]},
        },
    }
    with patch.object(cron_routes, "generate_response", return_value="Margaret led the week. She felt proud."):
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept")

    assert "Margaret Chen" in outcome["saved"]
    assert bundled.read_text() == before
    assert bundled.stat().st_mtime == before_mtime

    saved = storage_module.storage.load_character_memory("margaret-chen")
    assert saved["episodes"][-1]["week"] == "2026-W40"


# ---------------------------------------------------------------------------
# Prefix isolation (RUNBOOK Incident 1)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_vercel_env(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    # Bypass config.dialogue_model's Doppler requirement (same pattern as
    # tests/test_pick_concept.py's CONCEPT_MODEL fixture) — every real call
    # site here mocks generate_response, so the actual model string is never
    # sent anywhere.
    monkeypatch.setenv("DIALOGUE_MODEL", "test/fake-model")


@pytest.fixture
def cloud_backend():
    from backend.storage import _CloudBackend

    with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake-token-for-test"}):
        return _CloudBackend()


def test_cloud_character_memory_save_uses_prefix(cloud_backend):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"url": "https://example.com/character_memory/ria-castillo.json"}
    mock_resp.raise_for_status = MagicMock()

    cloud_backend.set_prefix("test/")
    with patch("requests.put", return_value=mock_resp) as mock_put:
        with patch.object(cloud_backend._fs, "save_character_memory"):
            cloud_backend.save_character_memory("ria-castillo", {"episodes": [], "last_updated": None})

    url = mock_put.call_args[0][0]
    assert "test/character_memory/ria-castillo.json" in url


def test_cloud_character_memory_load_does_not_leak_across_prefixes(cloud_backend):
    prod_data = {"episodes": [{"week": "2026-W40"}], "last_updated": "2026-W40"}
    test_data = {"episodes": [{"week": "test-week"}], "last_updated": "test-week"}
    cloud_backend._character_memory_cache[("", "margaret-chen")] = prod_data

    mock_list = MagicMock()
    mock_list.json.return_value = {"blobs": [{"url": "https://cdn.example.com/test-margaret.json"}]}
    mock_list.raise_for_status = MagicMock()
    mock_content = MagicMock()
    mock_content.json.return_value = test_data
    mock_content.raise_for_status = MagicMock()

    with patch("requests.get", side_effect=[mock_list, mock_content]) as mock_get:
        with cloud_backend.prefix_scope("test/"):
            result = cloud_backend.load_character_memory("margaret-chen")

    assert result == test_data
    assert mock_get.call_args_list[0].kwargs["params"]["prefix"] == "test/character_memory/margaret-chen.json"


def test_cloud_character_memory_save_raises_on_failure_never_claims_success(cloud_backend):
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("500 Server Error")

    with patch("requests.put", return_value=mock_resp):
        with pytest.raises(Exception, match="500 Server Error"):
            cloud_backend.save_character_memory("ria-castillo", {"episodes": []})


def test_cloud_character_memory_genuine_not_found_returns_none_no_fs_fallback(cloud_backend):
    """Review finding 1 + 2: an empty blob list is a genuine not-found, and
    must not touch the prefix-blind local filesystem at all."""
    mock_list = MagicMock()
    mock_list.json.return_value = {"blobs": []}
    mock_list.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_list), \
         patch.object(cloud_backend._fs, "load_character_memory") as fs_load:
        result = cloud_backend.load_character_memory("ria-castillo")

    assert result is None
    fs_load.assert_not_called()


def test_cloud_character_memory_list_failure_raises_unavailable_no_fs_fallback(cloud_backend):
    """Review finding 1: a list-API failure is CharacterMemoryUnavailable,
    never silently downgraded to "not found" via a filesystem fallback."""
    from backend.storage import CharacterMemoryUnavailable

    with patch("requests.get", side_effect=ConnectionError("blob API unreachable")), \
         patch.object(cloud_backend._fs, "load_character_memory") as fs_load:
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.load_character_memory("margaret-chen")

    fs_load.assert_not_called()


def test_cloud_character_memory_content_fetch_failure_raises_unavailable(cloud_backend):
    """Review finding 1: the blob exists per the list call, but fetching its
    content fails — still a read failure, never a "not found"."""
    mock_list = MagicMock()
    mock_list.json.return_value = {"blobs": [{"url": "https://cdn.example.com/margaret.json"}]}
    mock_list.raise_for_status = MagicMock()
    mock_content = MagicMock()
    mock_content.raise_for_status.side_effect = Exception("503 Service Unavailable")

    with patch("requests.get", side_effect=[mock_list, mock_content]), \
         patch.object(cloud_backend._fs, "load_character_memory") as fs_load:
        with pytest.raises(CharacterMemoryUnavailable):
            cloud_backend.load_character_memory("margaret-chen")

    fs_load.assert_not_called()


def test_cloud_character_memory_save_does_not_mirror_to_filesystem(cloud_backend):
    """Review finding 2: a cloud save must not also write the prefix-blind
    local file — that mirror is what let test/prod data cross before."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"url": "https://example.com/character_memory/margaret-chen.json"}
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.put", return_value=mock_resp), \
         patch.object(cloud_backend._fs, "save_character_memory") as fs_save:
        cloud_backend.save_character_memory("margaret-chen", {"episodes": [{"week": "2026-W40"}]})

    fs_save.assert_not_called()


# ---------------------------------------------------------------------------
# Caller-level effect of a read failure (#6968 review finding 1)
# ---------------------------------------------------------------------------


def test_generate_episode_memories_read_failure_skips_write_and_preserves_existing(tmp_path, monkeypatch):
    """A durable-store read failure must land in "failed", and must NEVER
    write a merge built on the missing read (that would silently regress
    good durable memory back to the legacy seed plus one new week)."""
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    # Seed durable storage with real, current memory for Margaret.
    storage_module.storage.save_character_memory(
        "margaret-chen",
        {"episodes": [{"week": "2026-W39", "summary": "The real, current memory."}], "last_updated": "2026-W39"},
    )

    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})

    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."), \
         patch.object(
             storage_module.storage, "load_character_memory",
             side_effect=storage_module.CharacterMemoryUnavailable("simulated Blob outage"),
         ):
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept")

    assert "Margaret Chen" in outcome["failed"]
    assert "Margaret Chen" not in outcome["saved"]

    # And the durable record is untouched — no merge was written on top of
    # the failed read.
    still_there = storage_module.storage.load_character_memory("margaret-chen")
    assert still_there["episodes"] == [{"week": "2026-W39", "summary": "The real, current memory."}]


# ---------------------------------------------------------------------------
# Per-character success / absence / failure visibility (cron_routes)
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


def test_generate_episode_memories_dry_run_writes_nothing(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})

    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."):
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept", dry_run=True)

    assert "Margaret Chen" in outcome["saved"]
    assert storage_module.storage.load_character_memory("margaret-chen") is None


def test_generate_episode_memories_write_failure_is_reported_not_claimed(tmp_path, monkeypatch):
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    episode = _episode_with_dialogue("2026-W40", {"monday": ["Margaret Chen"]})

    with patch.object(cron_routes, "generate_response", return_value="A calm week. Nice."), \
         patch.object(storage_module.storage, "save_character_memory", side_effect=RuntimeError("blob down")):
        outcome = cron_routes._generate_episode_memories(episode, "Test Concept")

    assert "Margaret Chen" in outcome["failed"]
    assert "Margaret Chen" not in outcome["saved"]


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
            "wednesday": {"status": "complete", "confirmed_winner": {}, "image_status": ""},
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
         patch.object(cron_routes, "_hero_image_url", return_value="https://example.com/hero.png"), \
         patch.object(
             cron_routes,
             "_generate_episode_memories",
             return_value={"saved": ["Margaret Chen"], "absent": ["Devon Park"], "failed": ["Marcus Reid"]},
         ):
        asyncio.run(cron_routes.cron_sunday(_request()))

    events = " | ".join(episode["events"])
    assert "memory saved for Margaret Chen" in events
    assert "memory absent (no dialogue) for Devon Park" in events
    assert "memory generation failed for Marcus Reid" in events


# ---------------------------------------------------------------------------
# simulate_dialogue_week: no false first-week fallback, cache invalidation
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


def test_empty_memory_gets_known_coworker_fallback_not_first_week(monkeypatch):
    import scripts.simulate_dialogue_week as sdw

    monkeypatch.setattr(sdw, "MEMORY_ONBOARDING_PILOT", False)
    monkeypatch.setattr(sdw, "_load_memories", lambda name: [])
    sdw._system_prompt_cache.clear()

    prompt = sdw.build_system_prompt(_persona("Ria Castillo"))

    assert "THIS IS YOUR FIRST WEEK" not in prompt
    assert "known coworker" not in prompt.lower() or "coworkers" in prompt.lower()
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


def test_load_memories_shows_latest_two_of_three_retained(tmp_path, monkeypatch):
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")

    data = None
    for week in ("2026-W38", "2026-W39", "2026-W40"):
        data = merge_character_memory(data, {"week": week, "concept": f"concept-{week}", "summary": f"summary-{week}"})
    storage_module.storage.save_character_memory("ria-castillo", data)

    shown = sdw._load_memories("Ria Castillo")

    assert [m["week"] for m in shown] == ["2026-W39", "2026-W40"]


def test_load_memories_shows_two_most_recent_by_week_out_of_insertion_order(tmp_path, monkeypatch):
    """Review finding 3: display order is by week key, independent of the
    order episodes happen to sit in storage (e.g. a stale legacy seed file
    written by the old, unsorted appender)."""
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    monkeypatch.setattr(storage_module, "CHARACTER_MEMORY_DIR", tmp_path / "character_memory")
    # Stored out of chronological order, as the pre-#6968 writer could leave
    # it (no sort on write).
    storage_module.storage.save_character_memory(
        "ria-castillo",
        {
            "episodes": [
                {"week": "2026-W40", "summary": "latest"},
                {"week": "2026-W11", "summary": "ancient"},
                {"week": "2026-W39", "summary": "second latest"},
            ],
            "last_updated": "2026-W39",
        },
    )

    shown = sdw._load_memories("Ria Castillo")

    assert [m["week"] for m in shown] == ["2026-W39", "2026-W40"]


def test_load_memories_read_failure_falls_back_to_known_coworker(tmp_path, monkeypatch):
    """Review finding 1: a durable-store read failure must degrade to the
    known-coworker prompt fallback, never raise into build_system_prompt and
    never seed from the legacy file."""
    import scripts.simulate_dialogue_week as sdw
    import backend.storage as storage_module

    with patch.object(
        storage_module.storage, "load_character_memory",
        side_effect=storage_module.CharacterMemoryUnavailable("simulated Blob outage"),
    ):
        shown = sdw._load_memories("Margaret Chen")

    assert shown == []


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
    assert storage_module.storage.load_character_memory("margaret-chen") is None


def test_repair_script_refuses_unpublished_episode(monkeypatch, capsys):
    import backend.storage as storage_module
    from scripts import repair_character_memory as repair

    episode = {"episode_id": "2026-W41", "stages": {}}  # no published_at

    with patch.object(storage_module.storage, "load_episode", return_value=episode):
        exit_code = repair.main(["2026-W41"])

    assert exit_code == 1
    assert "SKIPPED" in capsys.readouterr().out


def test_repair_script_apply_actually_writes(tmp_path, monkeypatch, capsys):
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
    saved = storage_module.storage.load_character_memory("margaret-chen")
    assert saved["episodes"][-1]["week"] == "2026-W40"


def test_repair_script_is_idempotent_on_replay(tmp_path, monkeypatch):
    """Replaying the same week's repair must not keep growing the record.

    The legacy backend/data/characters/margaret-chen/memory.json seed
    (real repo data, left in place as item 7's initial seed) already
    carries pre-existing weeks, so the assertion here is stability between
    two applies of the SAME week — not an assumption that the record
    starts empty.
    """
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
        repair.main(["2026-W40", "--apply"])
        after_first = storage_module.storage.load_character_memory("margaret-chen")
        repair.main(["2026-W40", "--apply"])
        after_second = storage_module.storage.load_character_memory("margaret-chen")

    assert after_first == after_second
    weeks = [e["week"] for e in after_second["episodes"]]
    assert weeks.count("2026-W40") == 1
    assert len(after_second["episodes"]) <= 3
