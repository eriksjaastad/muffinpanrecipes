"""Codex cycle-2 regressions for the photo approval gate (#7936).

Five finding families, each reproduced offline against the real handlers,
the real filesystem photo-control code and an in-memory episode store:

1. the admin "Run Compressed Week" simulation wrote production from a
   test-namespace page (and from any cloud viewer);
2. automatic variant cleanup ran before the publication was committed, so a
   failed mark_publishing offered trashed candidates for selection;
3. an obsolete publish hold survived a post-claim failure, so the monitor
   read an editorial-QA failure as "awaiting photo approval";
4. a control request with unusable candidates (``[{}]``) was accepted,
   approved with no path, claimed and paid for;
5. the review controls were nested under the Wednesday stage card, so a
   valid authority with no Wednesday mirror rendered no review at all.

No provider, storage, cron, webhook or notification call leaves the machine.
"""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import backend.storage as storage_module
from backend.admin import cron_routes
from backend.storage import PhotoControlConflict, PhotoControlUnavailable, _FilesystemBackend
from backend.utils import photo_review
from backend.utils.episode_integrity import (
    episode_integrity_failures,
    episode_summary,
    recorded_photo_hold,
    stage_deadline,
)
from tests.photo_review_helpers import BLOB, VARIANTS, decide, register, wednesday_stage
from tests.test_photo_approval_7936 import (
    EP_ID,
    _Store,
    _approved_store,
    _client,
    _control,
    _episode,
    _never_published,
    _post,
    _registered_store,
    _status,
    _sunday,
)

AFTER_SUNDAY = stage_deadline(EP_ID, "sunday") + timedelta(hours=2)
SET_ID = "0123456789abcdef"
NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc).isoformat()


class _LocalStore(_Store):
    """A local filesystem-like store: episodes are NOT separated by namespace."""

    def namespaces_episodes(self):
        return False


def _page(client, ns=None):
    query = f"?ns={ns}" if ns else ""
    return client.get(f"/admin/episodes/{EP_ID}{query}")


def _run(client, ns=None):
    query = f"?ns={ns}" if ns else ""
    return client.post(f"/admin/episodes/{EP_ID}/run{query}")


def _section(page: str) -> str:
    return page.split('<section id="photo-review"', 1)[1].split("</section>", 1)[0]


def _candidate_urls(page: str) -> list[str]:
    return [c.split('"', 1)[0] for c in _section(page).split('data-lightbox-src="')[1:]]


def _stage_header(page: str, day: str) -> str:
    """The stage card header for ``day`` (badges only)."""
    marker = f'<span class="text-xs text-slate-500 capitalize">{day}</span>'
    return page.split(marker, 1)[1].split("</div>", 1)[0]


# =============================================================================
# 1. The compressed-week simulation never writes production from a test or
#    cloud view
# =============================================================================

@pytest.mark.parametrize("ns", [None, "test"])
def test_cloud_viewer_neither_offers_nor_runs_the_compressed_week(ns):
    """A Blob-backed viewer (production or test namespace): no button, 409 on POST, nothing written."""
    prefix = "test/" if ns else ""
    store, wed = _registered_store(prefix=prefix)
    store.cloud = True
    # Production's own copy of the week sits beside the test copy, as on Blob.
    store.put(EP_ID, _episode(wednesday_stage(generation="g20260101T000000Z-prod00")), "")
    before = copy.deepcopy(store.episodes)
    stub = AsyncMock(return_value={"mode": "simulation"})
    client, patcher = _client(store)
    try:
        page = _page(client, ns)
        with patch.object(cron_routes, "storage", store), \
                patch.object(cron_routes, "execute_cron_stage_stub", stub), \
                patch.object(cron_routes, "_generate_dialogue") as generate:
            namespaced = _run(client, ns)
            # The old JavaScript posted WITHOUT the namespace from a test page.
            bare = _run(client)
    finally:
        patcher.stop()
    assert page.status_code == 200
    assert 'id="run-btn"' not in page.text and 'data-action="run-week"' not in page.text
    assert namespaced.status_code == 409 and bare.status_code == 409
    assert "local" in namespaced.json()["detail"] and "local" in bare.json()["detail"]
    stub.assert_not_called()
    generate.assert_not_called()
    assert store.saves == [] and store.episodes == before
    # The test namespace's review controls are still there; only the simulation is gone.
    if ns:
        assert 'data-action="photo-select"' in page.text


def test_local_production_view_runs_the_simulation_and_keeps_wednesdays_photos():
    """The legitimate local action still works; a local test namespace does not exist (400)."""
    store = _LocalStore()
    wed = wednesday_stage()
    store.put(EP_ID, _episode(wed))
    register(EP_ID, wed, store)
    generate = MagicMock(return_value=[{"character": "Devon Park", "message": "Simulated."}])
    client, patcher = _client(store)
    try:
        page = _page(client)
        with patch.object(cron_routes, "storage", store), \
                patch.object(cron_routes, "_generate_dialogue", generate), \
                patch("backend.admin.routes.asyncio.sleep", AsyncMock()):
            test_view = _run(client, "test")
            resp = _run(client)
    finally:
        patcher.stop()
    assert 'id="run-btn"' in page.text and 'data-ns=""' in page.text
    assert test_view.status_code == 400 and "cloud storage" in test_view.json()["detail"]
    assert resp.status_code == 200
    assert [s["ok"] for s in resp.json()["stages"]] == [True] * 7
    assert generate.call_count == 7
    saved = store.get(EP_ID)
    simulated = saved["stages"]["wednesday"]
    # Dialogue is simulated; the paid photos and their review mirror are untouched.
    assert simulated["simulation"] is True and simulated["dialogue"]
    assert simulated["image_urls"] == wed["image_urls"] and simulated["image_paths"] == wed["image_paths"]
    assert simulated["photo_review"]["image_set_id"] == wed["photo_review"]["image_set_id"]
    assert simulated["photography_data"] == wed["photography_data"]
    # A simulated Sunday is not a publication and does not touch the gate.
    assert not saved.get("published_at") and saved["stages"]["sunday"].get("published") is not True
    assert "hero_image_url" not in saved and "photo_approval" not in saved
    ctrl = _control(store)
    assert ctrl["version"] == 1 and ctrl["state"] == "awaiting"
    _assert_not_published_anywhere(saved)


def _assert_not_published_anywhere(saved: dict) -> None:
    """The REAL predicates every static/reader surface uses: none may read the stub as published."""
    from backend.publishing import episode_renderer, site_builder
    from backend.utils.episode_integrity import episode_page_is_due

    sunday = saved["stages"]["sunday"]
    assert sunday["status"] == "simulated" and sunday["simulation"] is True
    assert site_builder._episode_is_published(saved) is False
    # episode_renderer.render_episode_page: is_published = "sunday" in completed_stages
    completed = [d for d in episode_renderer.DAYS
                 if saved.get("stages", {}).get(d, {}).get("status") == "complete"]
    assert "sunday" not in completed and completed
    # episode_renderer.publish_episode_page teaser clearing, scripts/backfill_image_variants.py and
    # scripts/fix_encoding.py all use the same expression.
    assert saved.get("stages", {}).get("sunday", {}).get("status") != "complete"
    assert not (saved.get("published_at") or sunday.get("status") == "complete")
    assert episode_page_is_due(saved) is True, "the week still reads as in progress (Monday is complete)"


def test_simulated_sunday_is_not_published_for_the_static_builder_or_renderer():
    """A legacy week WITHOUT published_at but with a complete Sunday stays published; the stub does not."""
    from backend.publishing import site_builder

    store = _LocalStore()
    wed = wednesday_stage()
    store.put(EP_ID, _episode(wed))
    generate = MagicMock(return_value=[{"character": "Devon Park", "message": "Simulated."}])
    with patch.object(cron_routes, "storage", store), patch.object(cron_routes, "_generate_dialogue", generate):
        result = asyncio.run(cron_routes.execute_cron_stage_stub("sunday", EP_ID, "Spinach Feta Egg Cups"))
    assert result["mode"] == "simulation"
    saved = store.get(EP_ID)
    _assert_not_published_anywhere(saved)
    assert "sunday: simulated (simulation)" in saved["events"]
    # Legacy compatibility: a real pre-#7936 publication (complete Sunday, no published_at) is still published.
    legacy = {"stages": {"sunday": {"status": "complete", "published": True}}}
    assert site_builder._episode_is_published(legacy) is True
    assert site_builder._episode_is_published({"published_at": "2026-01-01T00:00:00+00:00", "stages": {}}) is True
    # The admin page says so.
    client, patcher = _client(store)
    try:
        page = _page(client).text
    finally:
        patcher.stop()
    assert "Simulated (not published)" in _stage_header(page, "sunday")
    assert "✅ Complete" not in _stage_header(page, "sunday")


def test_published_week_is_not_offered_the_simulation_locally(tmp_path):
    store = _LocalStore()
    store.put(EP_ID, _episode(wednesday_stage(), published_at="2026-10-11T00:00:00+00:00"))
    episodes = tmp_path / "data" / "episodes"
    episodes.mkdir(parents=True)
    (episodes / f"{EP_ID}.json").write_text(json.dumps({"concept": "x", "published_at": "2026-10-11T00:00:00+00:00"}))
    stub = AsyncMock(return_value={"mode": "simulation"})
    client, patcher = _client(store)
    client.app.state.project_root = tmp_path
    try:
        page = _page(client)
        with patch.object(cron_routes, "execute_cron_stage_stub", stub):
            resp = _run(client)
    finally:
        patcher.stop()
    assert 'id="run-btn"' not in page.text
    assert resp.status_code == 409 and "published" in resp.json()["detail"]
    stub.assert_not_called()


@pytest.mark.parametrize("why", ["test_prefix", "cloud", "published", "frozen"])
def test_simulation_stub_refuses_unsafe_targets_before_generating_or_saving(why):
    """The dispatcher itself refuses, so no caller can reach production or the gate through it."""
    store, ep, _ = _approved_store()
    if why == "frozen":
        photo_review.claim_for_publication(store, EP_ID, ep)
    if why == "published":
        store.put(EP_ID, {**ep, "published_at": "2026-10-11T00:00:00+00:00"})
    store.cloud = why == "cloud"
    before = copy.deepcopy(store.episodes)
    generate = MagicMock(return_value=[{"character": "Devon Park", "message": "Simulated."}])
    scope = store.prefix_scope("test/") if why == "test_prefix" else nullcontext()
    with patch.object(cron_routes, "storage", store), \
            patch.object(cron_routes, "_generate_dialogue", generate), \
            scope, pytest.raises((RuntimeError, photo_review.PublicationUnderway)):
        asyncio.run(cron_routes.execute_cron_stage_stub("sunday", EP_ID, "Spinach Feta Egg Cups"))
    generate.assert_not_called()
    assert store.saves == [] and store.episodes == before
    assert _control(store)["state"] == ("claimed" if why == "frozen" else "approved")


# =============================================================================
# 2. Candidates stay on disk until the publication is committed
# =============================================================================

class _DiskStore(_Store):
    """In-memory episodes, REAL filesystem variant cleanup against a temp images dir."""

    def cleanup_image_variants(self, recipe_id, keep_paths=()):
        self.cleanups.append((recipe_id, list(keep_paths)))
        return _FilesystemBackend.cleanup_image_variants(self, recipe_id, keep_paths=keep_paths)


def _trash_into(tmp_path):
    """A recoverable stand-in for send2trash: MOVE the target into a per-test trash directory.

    Nothing is ever deleted; a trashed candidate would simply stop existing at
    its original path, which is what the assertions below check.
    """
    trash = tmp_path / "trash"
    trash.mkdir(exist_ok=True)

    def _move(target):
        shutil.move(str(target), str(trash / f"{len(list(trash.iterdir()))}-{Path(target).name}"))

    return _move


def _candidate_files(tmp_path, monkeypatch, wed):
    """Create the three candidate PNGs where the filesystem backend looks for them."""
    images = tmp_path / "images"
    monkeypatch.setattr(storage_module, "IMAGES_DIR", images)
    files = []
    for p in wed["image_paths"]:
        rel = p.removeprefix("src/assets/images/")
        f = images / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"png")
        files.append(f)
    (images / "r1.png").write_bytes(b"winner")  # a legacy winner, so the old rule WOULD have trashed
    return files


def _legacy_disk_store(tmp_path, monkeypatch, pick=2):
    wed = wednesday_stage(image_status="confirmed")
    files = _candidate_files(tmp_path, monkeypatch, wed)
    ep = _episode(wed)
    store = _DiskStore()
    store.put(EP_ID, ep)
    register(EP_ID, wed, store)
    decide(EP_ID, ep, pick=pick, store=store)
    return store, ep, wed, files


def test_candidates_survive_a_lost_claim_and_stay_selectable(tmp_path, monkeypatch):
    """Codex repro: 503/409 before the frozen publish, then the OTHER candidate is chosen and published."""
    store, ep, wed, files = _legacy_disk_store(tmp_path, monkeypatch, pick=2)
    with patch("send2trash.send2trash", side_effect=_trash_into(tmp_path)), \
            patch.object(photo_review, "mark_publishing", side_effect=PhotoControlConflict("released")):
        env = _sunday(store)
    assert _status(env) == 409
    _never_published(store)
    assert _control(store)["state"] == "approved" and _control(store)["claim"] is None
    # Nothing was trashed: every reviewable candidate is still on disk.
    assert all(f.exists() for f in files)
    assert store.cleanups == []
    # Erik changes his mind to candidate 1, which must still exist and be selectable.
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "select", "image_set_id": wed["photo_review"]["image_set_id"],
                              "path": wed["image_paths"][0]})
    finally:
        patcher.stop()
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    assert files[0].exists()
    with patch("send2trash.send2trash", side_effect=_trash_into(tmp_path)):
        env = _sunday(store)
    assert env.result["published"] is True
    published = store.get(EP_ID)
    assert published["hero_image_url"] == wed["image_urls"][0]
    assert published["stages"]["sunday"]["image_cleaned"] is False
    assert all(f.exists() for f in files), "Sunday does not trash candidates even after publishing"
    assert store.cleanups == []


def test_checkpoint_recovery_writes_the_same_body_and_cleans_nothing(tmp_path, monkeypatch):
    """A failed publishing save, then a re-fire: the checkpoint is replayed byte for byte, no cleanup."""
    store, ep, wed, files = _legacy_disk_store(tmp_path, monkeypatch, pick=2)
    attempted = []

    def fail_publishing(data):
        if data.get("published_at"):
            attempted.append(copy.deepcopy(data))
            return True
        return False

    store.fail_save = fail_publishing
    with patch("send2trash.send2trash", side_effect=_trash_into(tmp_path)):
        assert _status(_sunday(store)) == 500
        store.fail_save = None
        env = _sunday(store)
    assert env.result["completed_from_checkpoint"] is True
    checkpoint = _control(store)["publication"]
    assert checkpoint == attempted[0]
    assert checkpoint["stages"]["sunday"]["image_cleaned"] is False
    assert all(f.exists() for f in files)
    assert store.cleanups == []


# =============================================================================
# 3. A hold is obsolete once the approval is claimed; a later failure is
#    reported as that failure
# =============================================================================

def _held_then_approved():
    """Sunday held the week (hold saved, one alert); Erik then approved candidate 1."""
    wed = wednesday_stage()
    ep = _episode(wed)
    store = _Store()
    store.put(EP_ID, ep)
    register(EP_ID, wed, store)
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    assert recorded_photo_hold(store.get(EP_ID)) == "awaiting_photo_approval"
    assert env.held.call_count == 1
    decide(EP_ID, store.get(EP_ID), store=store)
    return store, wed


def _monitor(ep):
    return episode_integrity_failures(ep, now=AFTER_SUNDAY), episode_summary(ep)


# "no usable selection after the claim" is no longer a reachable post-claim
# failure: since Codex cycle 3 a claimed version without a valid approved
# candidate is inconsistent and fails closed at the read boundary
# (tests/test_photo_approval_cycle3_7936.py), so Sunday never holds such a claim.
@pytest.mark.parametrize("failure", ["editorial_qa", "dialogue", "claim_lost"])
def test_post_claim_failure_is_reported_not_masked_by_the_old_hold(failure):
    store, wed = _held_then_approved()
    kw: dict = {}
    extra = nullcontext()
    if failure == "editorial_qa":
        kw = {"qa": (False, "STATUS: FAIL bad yield")}
        extra = patch.object(cron_routes, "_auto_fix_recipe", return_value=False)
    elif failure == "dialogue":
        kw = {"dialogue_effect": RuntimeError("provider down")}
    else:
        extra = patch.object(photo_review, "mark_publishing", side_effect=PhotoControlConflict("released"))
    with extra:
        env = _sunday(store, **kw)
    assert _status(env) in (400, 409, 500)
    _never_published(store)
    saved = store.get(EP_ID)
    assert "publish_hold" not in saved, "the obsolete hold was cleared when the approval was claimed"
    assert recorded_photo_hold(saved) is None
    failures, summary = _monitor(saved)
    assert any(f.startswith("sunday stage is") for f in failures), failures
    assert "awaiting photo approval" not in summary
    assert env.held.call_count == 0, "no hold alert for a failure"
    # No provisional publication field reached storage.
    assert "hero_image_url" not in saved and "photo_approval" not in saved
    if failure == "editorial_qa":
        sunday = saved["stages"]["sunday"]
        assert sunday["status"] == "failed" and "Editorial QA" in sunday["error"]
        assert any("Editorial QA" in f for f in failures)
        assert saved["editorial_qa"]["passed"] is False
    assert _control(store)["state"] == "approved" and _control(store)["claim"] is None


def test_clearing_the_hold_is_saved_before_any_paid_step_or_nothing_is_spent():
    store, wed = _held_then_approved()
    store.fail_save = lambda data: "sunday: hold cleared (approved photo claimed)" in data.get("events", [])
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    assert env.failure.call_count == 1
    _never_published(store)
    assert _control(store)["state"] == "approved" and _control(store)["claim"] is None
    # Storage still holds the earlier record unchanged; the next run retries.
    store.fail_save = None
    assert _sunday(store).result["published"] is True
    assert "publish_hold" not in store.get(EP_ID)


def test_a_legitimate_hold_after_a_failed_attempt_supersedes_the_failed_stage():
    """QA fails after a claim; Erik rejects the photos; the next Sunday is an honest hold."""
    store, wed = _held_then_approved()
    with patch.object(cron_routes, "_auto_fix_recipe", return_value=False):
        assert _status(_sunday(store, qa=(False, "STATUS: FAIL"))) == 400
    assert store.get(EP_ID)["stages"]["sunday"]["status"] == "failed"
    decide(EP_ID, store.get(EP_ID), action="reject", store=store)
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "photos_rejected"
    assert env.held.call_count == 1
    saved = store.get(EP_ID)
    assert "sunday" not in saved["stages"]
    assert "sunday: earlier failed attempt superseded by hold" in saved["events"]
    assert saved["editorial_qa"]["passed"] is False, "the QA record itself is kept"
    assert recorded_photo_hold(saved) == "photos_rejected"
    failures, summary = _monitor(saved)
    assert not [f for f in failures if f.startswith("sunday")]
    assert summary.endswith("not published: photos rejected, awaiting new photos")
    # A repeat of the same true hold: no second alert, nothing changes.
    env = _sunday(store)
    assert env.held.call_count == 0 and store.get(EP_ID)["publish_hold"] == saved["publish_hold"]
    _never_published(store)


def test_hold_then_approval_then_publication_clears_the_hold_and_reads_published():
    store, wed = _held_then_approved()
    env = _sunday(store)
    assert env.result["published"] is True
    published = store.get(EP_ID)
    assert "publish_hold" not in published
    assert "sunday: hold cleared (approved photo claimed)" in published["events"]
    failures, summary = _monitor(published)
    assert not [f for f in failures if f.startswith("sunday")] and summary.endswith("published")
    # Idempotent already-published status, no second hold handling.
    again = _sunday(store)
    assert again.result["already_published"] is True and again.held.call_count == 0


# =============================================================================
# 4. Unusable candidates never reach a decision, a claim or a paid call
# =============================================================================

def _good_candidates(wed):
    return photo_review.build_candidates(wed)


def _control_body(request, state="awaiting", decision=None):
    return {
        "schema": 1, "episode_id": EP_ID, "version": 1, "write_id": "deadbeefdeadbeef",
        "state": state, "request": request, "decision": decision, "claim": None,
        "event": "test", "at": NOW,
    }


def _good_request(wed):
    return {
        "image_set_id": SET_ID, "generated_at": NOW, "requested_at": NOW,
        "candidates": _good_candidates(wed),
        "image_paths": list(wed["image_paths"]), "image_urls": list(wed["image_urls"]),
        "wednesday_snapshot": photo_review._wednesday_snapshot(wed),
    }


def _malformed_requests(wed):
    good = _good_request(wed)
    c = copy.deepcopy(good["candidates"])
    cases = {
        "empty_dict_candidate": [{}],
        "empty_path": [{**c[0], "path": ""}],
        "none_path": [{**c[0], "path": None}],
        "missing_path": [{k: v for k, v in c[0].items() if k != "path"}],
        "whitespace_path": [{**c[0], "path": "  "}],
        "traversal_path": [{**c[0], "path": "src/assets/images/../../x.png"}],
        "empty_url": [{**c[0], "url": ""}],
        "none_url": [{**c[0], "url": None}],
        "int_url": [{**c[0], "url": 7}],
        "javascript_url": [{**c[0], "url": "javascript:alert(1)"}],
        "root_url": [{**c[0], "url": "/"}],
        "bare_relative_url": [{**c[0], "url": "/x"}],
        "relative_no_images_segment": [{**c[0], "url": "/assets/x.png"}],
        "no_host_url": [{**c[0], "url": "https:///images/r1/round_1/a.png"}],
        "scheme_relative_url": [{**c[0], "url": "//evil.example/images/r1/round_1/a.png"}],
        "credentials_url": [{**c[0], "url": "https://user:pw@store.example/images/r1/round_1/a.png"}],
        "query_url": [{**c[0], "url": c[0]["url"] + "?download=1"}],
        "fragment_url": [{**c[0], "url": c[0]["url"] + "#x"}],
        "no_extension_url": [{**c[0], "url": c[0]["url"].removesuffix(".png")}],
        "html_extension_url": [{**c[0], "url": c[0]["url"].replace(".png", ".html")}],
        "dot_segment_url": [{**c[0], "url": "https://store.example/images/../images/r1/a.png"}],
        "backslash_url": [{**c[0], "url": "https://store.example/images\\r1\\a.png"}],
        "angle_url": [{**c[0], "url": "https://store.example/images/r1/<a>.png"}],
        "ftp_url": [{**c[0], "url": "ftp://store.example/images/r1/round_1/a.png"}],
        "data_url": [{**c[0], "url": "data:image/png;base64,AAAA"}],
        "bare_path": [{**c[0], "path": "x"}],
        "non_canonical_path": [{**c[0], "path": "data/images/r1/round_1/a.png"}],
        "no_extension_path": [{**c[0], "path": "src/assets/images/r1/round_1/a"}],
        "non_image_path": [{**c[0], "path": "src/assets/images/r1/round_1/a.svg"}],
        "dot_segment_path": [{**c[0], "path": "src/assets/images/r1/./a.png"}],
        "dots_only_segment_path": [{**c[0], "path": "src/assets/images/r1/.../a.png"}],
        "absolute_path": [{**c[0], "path": "/src/assets/images/r1/round_1/a.png"}],
        "url_as_path": [{**c[0], "path": c[0]["url"]}],
        "bool_index": [{**c[0], "index": True}],
        "str_index": [{**c[0], "index": "1"}],
        "zero_index": [{**c[0], "index": 0}],
        "duplicate_index": [c[0], {**c[1], "index": 1}],
        "duplicate_path": [c[0], {**c[1], "path": c[0]["path"]}],
        "duplicate_url": [c[0], {**c[1], "url": c[0]["url"]}],
        "non_dict_element": [c[0], "src/assets/images/r1/x.png"],
        "none_element": [c[0], None],
        "bool_round": [{**c[0], "round": True}],
        "list_variant": [{**c[0], "variant": ["macro"]}],
        "candidates_string": "src/assets/images/r1/x.png",
        "candidates_dict": {"path": c[0]["path"], "url": c[0]["url"]},
        "candidates_empty": [],
    }
    out = {name: {**good, "candidates": cands} for name, cands in cases.items()}
    out["set_id_empty"] = {**good, "image_set_id": ""}
    out["set_id_short"] = {**good, "image_set_id": "0123456789abcde"}
    out["set_id_uppercase"] = {**good, "image_set_id": "0123456789ABCDEF"}
    out["set_id_int"] = {**good, "image_set_id": 1234567890123456}
    out["generated_at_missing"] = {k: v for k, v in good.items() if k != "generated_at"}
    out["generated_at_none"] = {**good, "generated_at": None}
    return out


_MALFORMED = sorted(_malformed_requests(wednesday_stage()))


@pytest.mark.parametrize("case", _MALFORMED)
def test_malformed_control_fails_closed_everywhere(case):
    """Codex repro: select with no path matched a path-less candidate, Sunday claimed and paid."""
    wed = wednesday_stage()
    request = _malformed_requests(wed)[case]
    store = _Store()
    store.put(EP_ID, _episode(wed))
    store.create_photo_control_version(EP_ID, 1, _control_body(request))
    with pytest.raises(PhotoControlUnavailable):
        photo_review.read_view(store, EP_ID, store.get(EP_ID))
    with pytest.raises(PhotoControlUnavailable):
        photo_review.claim_for_publication(store, EP_ID, store.get(EP_ID))

    candidates = request.get("candidates")
    first = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else {}
    client, patcher = _client(store)
    try:
        page = _page(client)
        no_path = _post(client, {"action": "select", "image_set_id": request.get("image_set_id") or SET_ID})
        the_path = _post(client, {"action": "select", "image_set_id": request.get("image_set_id") or SET_ID,
                                  "path": first.get("path")})
        reject = _post(client, {"action": "reject", "image_set_id": request.get("image_set_id") or SET_ID})
    finally:
        patcher.stop()
    assert page.status_code == 503 and "photo review" in page.json()["detail"]
    for resp in (no_path, the_path, reject):
        assert resp.status_code in (422, 503), (case, resp.status_code, resp.text)
        assert "approved" not in resp.text
    # Nothing decided: still the single malformed version, nothing written.
    raw = store.read_photo_control(EP_ID)
    assert raw["version"] == 1 and raw["state"] == "awaiting" and raw["decision"] is None
    assert store.saves == []

    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    assert env.failure.call_count == 1 and env.held.call_count == 0
    _never_published(store)
    raw = store.read_photo_control(EP_ID)
    assert raw["version"] == 1 and raw["claim"] is None


@pytest.mark.parametrize("selected", [
    {}, None, {"path": "", "url": ""}, {"path": None, "url": None},
    {"url": f"{BLOB}/r1/g20260101T000000Z-aaaaaa/round_1/macro_closeup.png"},
    {"path": "src/assets/images/r1/g20260101T000000Z-aaaaaa/round_1/macro_closeup.png"},
    {"path": "src/assets/images/r1/g20260101T000000Z-aaaaaa/round_1/macro_closeup.png", "url": ""},
])
def test_an_approval_with_an_unusable_selection_selects_nothing(selected):
    wed = wednesday_stage()
    store = _Store()
    store.put(EP_ID, _episode(wed))
    decision = {"status": "approved", "image_set_id": SET_ID, "selected": selected,
                "decided_at": NOW, "decided_by": "site editor", "decided_by_id": None}
    store.create_photo_control_version(EP_ID, 1, _control_body(_good_request(wed), "approved", decision))
    view = photo_review.read_view(store, EP_ID, store.get(EP_ID))
    assert view.selected is None
    assert photo_review.hold_reason(view) == "awaiting_photo_approval"
    assert photo_review.protected_image_paths(view) == []
    assert photo_review.dialogue_context(view)["status"] == "awaiting"
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    env.dialogue.assert_not_called()
    assert _control(store)["version"] == 2 or _control(store)["version"] == 1  # hold writes no control
    assert _control(store)["state"] == "approved"


def test_register_refuses_an_unusable_request_without_writing():
    store = _Store()
    for case, request in _malformed_requests(wednesday_stage()).items():
        with pytest.raises(photo_review.PhotoReviewError):
            photo_review.register_request(store, EP_ID, request)
    assert store.read_photo_control(EP_ID) is None


def test_select_without_a_path_on_a_valid_set_is_a_plain_refusal():
    store, wed = _registered_store()
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "select", "image_set_id": wed["photo_review"]["image_set_id"]})
        blank = _post(client, {"action": "select", "image_set_id": wed["photo_review"]["image_set_id"], "path": " "})
    finally:
        patcher.stop()
    assert resp.status_code == 400 and "Choose one" in resp.json()["detail"]
    assert blank.status_code == 400
    assert _control(store)["version"] == 1 and _control(store)["state"] == "awaiting"


def test_legitimate_candidate_shapes_are_accepted():
    """Public store URLs, local-filesystem URLs, empty variant, None round, derived legacy requests."""
    wed = wednesday_stage()
    good = _good_request(wed)
    assert photo_review._valid_request(good)
    assert photo_review._valid_request(photo_review.new_review(wed))
    legacy_wed = {k: v for k, v in wednesday_stage().items() if k != "photo_review"}
    assert photo_review._valid_request(photo_review.legacy_review(legacy_wed))
    assert photo_review._valid_request(photo_review.episode_request(_episode(wed)))
    sparse = copy.deepcopy(good)
    sparse["candidates"][0].update({"variant": "", "round": None})
    assert photo_review._valid_request(sparse)


@pytest.mark.parametrize("path", [
    "src/assets/images/r1/round_1/hero_threequarter.png",                      # pre-generation legacy form
    "src/assets/images/r1/g20260101T000000Z-aaaaaa/round_2/macro_closeup.png",  # generation-scoped form
    "src/assets/images/a9b98b08/round_1/Hero.PNG",                              # W10-era, upper-case extension
    "src/assets/images/r1/round_1/hero.jpg",
    "src/assets/images/r1/round_1/hero.webp",
    "src/assets/images/r1.png",                                                 # featured image form
])
def test_historical_and_new_image_paths_are_canonical(path):
    assert photo_review._valid_image_path(path)


@pytest.mark.parametrize("url", [
    "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/a9b98b08/round_1/hero.png",
    "https://blob.vercel-storage.com/images/a9b98b08/round_1/hero.png",           # W10-era, no store id
    "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/test/images/r1/round_1/a.png",
    "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/r1/round_1/a-Ab12Cd34.png",  # Blob suffix
    "/assets/images/r1/g20260101T000000Z-aaaaaa/round_1/a.png",                 # local filesystem backend
    "http://localhost:8000/assets/images/r1/round_1/a.PNG",
])
def test_historical_and_new_image_urls_are_usable(url):
    assert photo_review._valid_image_url(url)


@pytest.mark.parametrize("url", [
    "/", "/x", "/assets/x.png", "//evil.example/images/a.png", "https:///images/a.png",
    "https://u:p@h/images/a.png", "https://h/images/a.png?x=1", "https://h/images/a.png#f",
    "https://h/images/a", "https://h/images/a.html", "https://h/images/../a.png", "ftp://h/images/a.png",
    "data:image/png;base64,AAAA", "javascript:alert(1)", "https://h/images/a png.png",
    "https://h/images/<a>.png", "https://h\\images\\a.png", " https://h/images/a.png",
])
def test_structurally_bad_urls_are_not_usable(url):
    assert not photo_review._valid_image_url(url)


def test_local_filesystem_urls_review_and_publish():
    """The local backend returns "/assets/images/..." URLs; they are usable candidates."""
    wed = wednesday_stage()
    wed["image_urls"] = [u.replace(BLOB, "/assets/images") for u in wed["image_urls"]]
    assert all(u.startswith("/assets/images/") for u in wed["image_urls"])
    request = photo_review.new_review(wed)
    wed["photo_review"] = {k: request[k] for k in ("image_set_id", "generated_at", "requested_at")}
    ep = _episode(wed)
    store = _Store()
    store.put(EP_ID, ep)
    register(EP_ID, wed, store)
    client, patcher = _client(store)
    try:
        page = _page(client)
        resp = _post(client, {"action": "select", "image_set_id": wed["photo_review"]["image_set_id"],
                              "path": wed["image_paths"][2]})
    finally:
        patcher.stop()
    assert _candidate_urls(page.text) == wed["image_urls"]
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    env = _sunday(store)
    assert env.result["published"] is True
    assert store.get(EP_ID)["hero_image_url"] == wed["image_urls"][2]


def test_derived_request_that_does_not_add_up_is_no_request():
    """A bootstrap view never fabricates a reviewable set from inconsistent episode fields."""
    wed = wednesday_stage()
    wed.pop("photo_review")
    wed["image_urls"] = ["", "", ""]  # uploads recorded with no URLs
    ep = _episode(wed)
    store = _Store()
    store.put(EP_ID, ep)
    view = photo_review.read_view(store, EP_ID, ep)
    assert view.request is None and view.state is None
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    env.dialogue.assert_not_called()
    assert store.read_photo_control(EP_ID) is None


# =============================================================================
# 5. The review renders from the authority, not from the Wednesday mirror
# =============================================================================

@pytest.mark.parametrize("wednesday", ["absent", "failed", "photos_ready", "stale_other_set"])
def test_review_renders_and_decides_from_the_control_without_a_wednesday_mirror(wednesday):
    """Registration succeeded, the episode save did not (or a stale save removed/replaced Wednesday)."""
    wed = wednesday_stage()
    ep = _episode(wed)
    if wednesday == "absent":
        ep["stages"].pop("wednesday")
    elif wednesday == "failed":
        ep["stages"]["wednesday"] = {"status": "failed", "error": "episode save failed after registration"}
    elif wednesday == "photos_ready":
        ep["stages"]["wednesday"] = {**wed, "status": "photos_ready"}
    else:
        ep["stages"]["wednesday"] = wednesday_stage(generation="g20251201T000000Z-oldold")
    store = _Store()
    store.put(EP_ID, ep)
    register(EP_ID, wed, store)
    set_id = wed["photo_review"]["image_set_id"]
    client, patcher = _client(store)
    try:
        page = _page(client).text
        resp = _post(client, {"action": "select", "image_set_id": set_id, "path": wed["image_paths"][2]})
        after = _page(client).text
    finally:
        patcher.stop()
    section = _section(page)
    assert _candidate_urls(page) == wed["image_urls"]
    assert section.count('data-action="photo-select"') == 3
    assert section.count(f'data-image-set="{set_id}"') == 4  # three picks + None usable
    assert "Awaiting your pick" in section
    assert "No current photo set to review" not in section
    # The Wednesday card tells the truth about the stage; nothing is fabricated.
    header = _stage_header(page, "wednesday")
    assert "✅ Complete" not in header or wednesday == "stale_other_set"
    if wednesday == "absent":
        assert "Not Started" in header
    if wednesday == "failed":
        assert "❌ Failed" in header and "episode save failed after registration" in page
    if wednesday in ("absent", "failed", "stale_other_set"):
        assert "does not show this set" in section
    else:
        assert "does not show this set" not in section
    # The decision targets the current set and lands on the control, not the episode.
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    ctrl = _control(store)
    assert ctrl["state"] == "approved" and ctrl["decision"]["selected"]["path"] == wed["image_paths"][2]
    assert store.saves == []
    assert ">Selected<" in _section(after) and "Approved</span>" in _section(after)


def test_evaluation_snapshot_is_shown_without_a_wednesday_mirror():
    wed = wednesday_stage()
    wed["photography_data"]["rounds"][0]["variants"][1]["scores"] = {"defects": ["pan rim warped"]}
    wed["photography_data"]["automated_review_status"] = "failed"
    ep = _episode(wed)
    ep["stages"].pop("wednesday")
    store = _Store()
    store.put(EP_ID, ep)
    register(EP_ID, wed, store)
    client, patcher = _client(store)
    try:
        page = _page(client).text
    finally:
        patcher.stop()
    section = _section(page)
    assert "pan rim warped" in section and "Automated check: failed" in section
    assert "Team pick" in section
    assert "not available" not in section


def test_no_review_section_without_a_request_or_a_wednesday_record():
    ep = _episode(wednesday_stage())
    ep["stages"].pop("wednesday")
    store = _Store()
    store.put(EP_ID, ep)
    client, patcher = _client(store)
    try:
        page = _page(client).text
    finally:
        patcher.stop()
    assert 'id="photo-review"' not in page
    assert "Not Started" in _stage_header(page, "wednesday")


def test_wednesday_record_without_a_request_still_shows_the_empty_review():
    wed = wednesday_stage()
    wed["image_urls"] = ["", "", ""]
    wed.pop("photo_review")
    store = _Store()
    store.put(EP_ID, _episode(wed))
    client, patcher = _client(store)
    try:
        page = _page(client).text
    finally:
        patcher.stop()
    section = _section(page)
    assert "No current review" in section and "No current photo set to review" in section
    assert 'data-action="photo-select"' not in section


def test_published_and_frozen_reviews_stay_read_only_outside_the_stage_card():
    store, ep, wed = _approved_store(pick=2)
    env = _sunday(store)
    assert env.result["published"] is True
    client, patcher = _client(store)
    try:
        page = _page(client).text
        resp = _post(client, {"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]})
    finally:
        patcher.stop()
    section = _section(page)
    assert "Published. The hero is pinned" in section
    assert 'data-action="photo-select"' not in section and 'data-action="photo-reject"' not in section
    assert ">Selected<" in section
    assert resp.status_code == 409


def test_claimed_week_without_a_wednesday_mirror_is_frozen_but_inspectable():
    store, ep, wed = _approved_store()
    photo_review.claim_for_publication(store, EP_ID, ep)
    bare = copy.deepcopy(ep)
    bare["stages"].pop("wednesday")
    store.put(EP_ID, bare)
    client, patcher = _client(store)
    try:
        page = _page(client).text
        resp = _post(client, {"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]})
    finally:
        patcher.stop()
    section = _section(page)
    assert "Publishing is underway" in section and _candidate_urls(page) == wed["image_urls"]
    assert 'data-action="photo-select"' not in section and 'data-action="photo-reject"' not in section
    assert "does not show this set" in section
    assert resp.status_code == 409 and _control(store)["state"] == "claimed"


def test_test_namespace_proxy_mapping_is_unchanged_by_the_section_move():
    """The review section above the stages still uses the set-bound proxy for test images."""
    from tests.test_photo_approval_7936 import _real_store

    store, wed = _real_store("test/")
    client, patcher = _client(store)
    try:
        page = _page(client, "test").text
    finally:
        patcher.stop()
    set_id = wed["photo_review"]["image_set_id"]
    expected = [f"/admin/episodes/{EP_ID}/photos/{set_id}/{i}/image?ns=test" for i in (1, 2, 3)]
    assert [u.replace("&amp;", "&") for u in _candidate_urls(page)] == expected
    assert 'data-ns="test"' in _section(page)
    assert VARIANTS[0].replace("_", " ") in _section(page)
