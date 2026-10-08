"""Human photo approval gates Sunday's publish (#7936). Offline only.

Every test runs the REAL photo-control log (backend.storage filesystem
code: exclusive lock, create-if-absent, atomic rename) in a per-test
directory (tests/conftest.py), or the real cloud backend over a fake Blob
API. Nothing mocks the compare-and-swap away.
"""

from __future__ import annotations

import asyncio
import copy
import html
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import backend.storage as storage_module
from backend.admin import cron_routes
from backend.storage import (
    EpisodeReadStale,
    PhotoControlConflict,
    PhotoControlUnavailable,
    _CloudBackend,
    _FilesystemBackend,
)
from backend.utils import photo_review
from tests.photo_review_helpers import BLOB, VARIANTS, decide, register, wednesday_stage

EP_ID = "2026-W41"


# --- A storage double: in-memory episodes, REAL control log ------------------

class _Store(_FilesystemBackend):
    """Episodes in memory (keyed by prefix), photo control on the real fs code."""

    def __init__(self, episodes=None, cloud=False):
        self.prefix = ""
        self.episodes = {("", k): copy.deepcopy(v) for k, v in (episodes or {}).items()}
        self.saves: list[dict] = []
        self.pages: dict = {}
        self.cloud = cloud
        self.fail_verified: Exception | None = None
        self.fail_control_read: Exception | None = None
        self.fail_save = None          # predicate(data) -> bool
        self.before_save = None        # hook(data)
        self.cleanups: list = []

    def _has_cloud(self):
        return self.cloud

    def namespaces_episodes(self):
        # Episodes here are keyed by prefix, as Blob keys are.
        return True

    def put(self, episode_id, ep, prefix=""):
        self.episodes[(prefix, episode_id)] = copy.deepcopy(ep)

    def get(self, episode_id, prefix=""):
        return self.episodes.get((prefix, episode_id))

    def load_episode(self, episode_id):
        return copy.deepcopy(self.episodes.get((self.prefix, episode_id)))

    def load_episode_strict(self, episode_id, *, use_cache=True):
        return self.load_episode(episode_id)

    def load_episode_verified(self, episode_id):
        if self.fail_verified:
            raise self.fail_verified
        return self.load_episode(episode_id)

    def list_episodes_strict(self):
        return [copy.deepcopy(v) for (p, _), v in self.episodes.items() if p == self.prefix]

    def save_episode(self, episode_id, data):
        if self.before_save:
            self.before_save(data)
        if self.fail_save and self.fail_save(data):
            raise RuntimeError("blob write failed")
        self.saves.append(copy.deepcopy(data))
        self.episodes[(self.prefix, episode_id)] = copy.deepcopy(data)

    def read_photo_control(self, episode_id):
        if self.fail_control_read:
            raise self.fail_control_read
        return super().read_photo_control(episode_id)

    def save_page(self, pathname, html_content):
        self.pages[pathname] = html_content
        return f"/{pathname}"

    def cleanup_image_variants(self, recipe_id, keep_paths=()):
        self.cleanups.append((recipe_id, list(keep_paths)))
        return []


def _episode(wed: dict | None = None, **extra) -> dict:
    wed = wed if wed is not None else wednesday_stage()
    ep = {
        "episode_id": EP_ID,
        "concept": "Spinach Feta Egg Cups",
        "recipe_id": "r1",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {
                "title": "Spinach Feta Egg Cups",
                "description": "Marcus's intro for the test week.",
                "ingredients": [{"amount": "6", "item": "eggs"}],
                "instructions": ["Whisk and bake."],
            }},
            "wednesday": wed,
        },
        "events": [],
        "image_urls": wed.get("image_urls", []),
    }
    ep.update(extra)
    return ep


def _control(store, prefix=""):
    with store.prefix_scope(prefix):
        return store.read_photo_control(EP_ID)


def _request(method="POST", path="/api/cron/sunday"):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


@contextmanager
def _sunday_env(store, test=False, dialogue_effect=None, qa=(True, "PASS")):
    body = cron_routes.StageRequest(episode_id=EP_ID, force=True, test=test)
    dialogue = MagicMock(return_value=([{"character": "Devon Park", "message": "Ready."}], "PASS"))
    if dialogue_effect:
        dialogue.side_effect = dialogue_effect
    with patch.object(cron_routes, "storage", store), \
         patch("backend.storage.storage", store), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", return_value=body), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", dialogue), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=qa) as qa_mock, \
         patch.object(cron_routes, "_generate_episode_memories",
                      return_value={"saved": [], "absent": [], "failed": []}), \
         patch.object(cron_routes, "regenerate_and_upload", return_value="ok"), \
         patch.object(cron_routes, "_indexnow_submit_urls"), \
         patch.object(cron_routes, "notify_publish_held", return_value=True) as held, \
         patch.object(cron_routes, "notify_pipeline_failure", return_value=True) as failure, \
         patch.object(cron_routes, "notify_judge_failure", return_value=True), \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog") as catalog, \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        yield SimpleNamespace(dialogue=dialogue, qa=qa_mock, held=held, failure=failure, catalog=catalog)


def _sunday(store, **kw):
    with _sunday_env(store, **kw) as env:
        try:
            env.result = asyncio.run(cron_routes.cron_sunday(_request()))
            env.error = None
        except Exception as exc:  # noqa: BLE001 - asserted by the caller
            env.result, env.error = None, exc
    return env


def _status(env) -> int:
    if env.error is not None:
        return getattr(env.error, "status_code", 0)
    return getattr(env.result, "status_code", 200)


def _never_published(store):
    assert not any(s.get("published_at") for s in store.saves), "a save leaked published_at"
    assert not any((s.get("stages", {}).get("sunday") or {}).get("status") == "complete" for s in store.saves)


def _approved_store(pick=1, prefix="", wed=None):
    wed = wed or wednesday_stage()
    ep = _episode(wed)
    store = _Store()
    store.put(EP_ID, ep, prefix)
    with store.prefix_scope(prefix):
        register(EP_ID, wed, store)
        decide(EP_ID, ep, pick=pick, store=store)
    return store, ep, wed


# --- Sunday gate --------------------------------------------------------------

@pytest.mark.parametrize("prefix", ["", "test/"])
def test_awaiting_review_holds_before_any_paid_call_and_takes_no_claim(prefix):
    wed = wednesday_stage()
    store = _Store()
    store.put(EP_ID, _episode(wed), prefix)
    with store.prefix_scope(prefix):
        register(EP_ID, wed, store)
    env = _sunday(store, test=bool(prefix))
    assert _status(env) == 202
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    assert _control(store, prefix)["state"] == "awaiting"
    assert store.get(EP_ID, prefix)["publish_hold"]["reason"] == "awaiting_photo_approval"
    _never_published(store)


def test_rejected_holds_and_repeat_runs_send_one_alert():
    store, ep, _ = _approved_store()
    decide(EP_ID, ep, action="reject", store=store)
    first = _sunday(store)
    second = _sunday(store)
    assert json.loads(first.result.body)["status"] == "photos_rejected"
    assert first.held.call_count == 1 and second.held.call_count == 0
    first.dialogue.assert_not_called()
    _never_published(store)


def test_week_without_a_control_holds_from_the_derived_request_and_writes_no_control():
    wed = wednesday_stage()
    del wed["photo_review"]  # a pre-#7936 Wednesday
    store = _Store({EP_ID: _episode(wed)})
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    assert _control(store) is None
    env.dialogue.assert_not_called()


def test_approved_photo_is_claimed_then_published_everywhere():
    store, ep, wed = _approved_store(pick=2)
    store.episodes[("", EP_ID)]["hero_image_url"] = "https://old/stale-pin.png"
    env = _sunday(store)
    assert env.error is None and env.result["published"] is True
    published = store.get(EP_ID)
    assert published["hero_image_url"] == wed["image_urls"][1]
    ctrl = _control(store)
    assert ctrl["state"] == "published"
    assert published["photo_approval"]["claim_id"] == ctrl["claim"]["claim_id"]
    assert published["photo_approval"]["decided_by"] == "site editor"
    assert "@" not in json.dumps(ctrl)
    # Version history kept: registered, approved, claimed, publishing, published.
    states = [json.loads(p.read_text())["state"]
              for p in sorted((storage_module.PHOTO_CONTROL_DIR / "production" / EP_ID).glob("v*.json"))]
    assert states == ["awaiting", "approved", "claimed", "publishing", "published"]


def test_stale_stage_save_cannot_resurrect_an_earlier_approved_set():
    """Approve A, Wednesday reruns B, a stale Thursday save puts A back."""
    a = wednesday_stage(generation="g20260101T000000Z-aaaaaa")
    store, ep, _ = _approved_store(wed=a)
    b = wednesday_stage(generation="g20260102T000000Z-bbbbbb")
    register(EP_ID, b, store)
    # The stored episode still (again) carries set A and its mirror.
    stale = _episode(a)
    stale["stages"]["thursday"] = {"status": "complete"}
    store.put(EP_ID, stale)

    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    env.dialogue.assert_not_called()
    _never_published(store)

    # An approval of A's photos is refused: the current set is B.
    with pytest.raises(photo_review.PhotoReviewError):
        photo_review.record_decision(store, EP_ID, stale, action="select",
                                     image_set=a["photo_review"]["image_set_id"],
                                     path=a["image_paths"][0], decided_by="site editor")
    decide(EP_ID, stale, pick=3, store=store)
    env = _sunday(store)
    published = store.get(EP_ID)
    assert published["hero_image_url"] == b["image_urls"][2]
    assert published["image_urls"] == b["image_urls"]  # mirror repaired from the control


def test_reject_before_claim_prevents_publication():
    store, ep, _ = _approved_store()
    decide(EP_ID, ep, action="reject", store=store)
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "photos_rejected"
    _never_published(store)


def test_decision_after_claim_is_refused_and_the_claimed_photo_publishes():
    store, ep, wed = _approved_store(pick=1)
    refused = []

    def erik_changes_his_mind(*_a, **_k):
        with pytest.raises(photo_review.PublicationUnderway):
            decide(EP_ID, ep, action="reject", store=store)
        with pytest.raises(photo_review.PublicationUnderway):
            register(EP_ID, wednesday_stage(generation="g20260103T000000Z-cccccc"), store)
        refused.append(True)
        return [{"character": "Devon Park", "message": "Ready."}], "PASS"

    env = _sunday(store, dialogue_effect=erik_changes_his_mind)
    assert refused == [True]
    assert env.result["published"] is True
    assert store.get(EP_ID)["hero_image_url"] == wed["image_urls"][0]


def test_concurrent_sundays_claim_once():
    store, ep, _ = _approved_store()
    seen = []

    def second_sunday_starts(*_a, **_k):
        with pytest.raises(photo_review.PublicationUnderway):
            photo_review.claim_for_publication(store, EP_ID, store.get(EP_ID))
        seen.append(True)
        return [{"character": "Devon Park", "message": "Ready."}], "PASS"

    env = _sunday(store, dialogue_effect=second_sunday_starts)
    assert seen == [True] and env.result["published"] is True


def test_threads_racing_for_the_claim_have_exactly_one_winner():
    store, ep, _ = _approved_store()
    results, barrier = [], threading.Barrier(8)

    def worker():
        barrier.wait()
        try:
            results.append(photo_review.claim_for_publication(store, EP_ID, ep).claim["claim_id"])
        except (photo_review.PublicationUnderway, photo_review.ReviewConflict):
            results.append(None)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([r for r in results if r]) == 1
    assert _control(store)["state"] == "claimed"


def test_sunday_that_finds_a_claim_stops_before_spending():
    store, ep, _ = _approved_store()
    photo_review.claim_for_publication(store, EP_ID, ep)
    env = _sunday(store)
    assert _status(env) == 409
    assert json.loads(env.result.body)["status"] == "publication_underway"
    env.dialogue.assert_not_called()
    assert store.saves == []


@pytest.mark.parametrize("failure", ["qa", "dialogue"])
def test_pre_publication_failure_saves_unpublished_and_releases_the_claim(failure):
    store, ep, _ = _approved_store()
    kw = {"qa": (False, "STATUS: FAIL bad yield")} if failure == "qa" else \
        {"dialogue_effect": RuntimeError("provider down")}
    with patch.object(cron_routes, "_auto_fix_recipe", return_value=False):
        env = _sunday(store, **kw)
    assert _status(env) in (400, 500)
    _never_published(store)
    assert store.saves, "the failure is recorded on the episode"
    ctrl = _control(store)
    assert ctrl["state"] == "approved" and ctrl["claim"] is None
    # The released approval can be claimed by the next run.
    env = _sunday(store)
    assert env.result["published"] is True


def test_unexpected_error_after_the_published_copy_is_built_saves_unpublished():
    store, ep, _ = _approved_store()
    # _set_static_deploy_state runs on the published copy just before mark_publishing.
    with patch.object(cron_routes, "_set_static_deploy_state", side_effect=RuntimeError("bug")):
        env = _sunday(store)
    assert _status(env) == 500
    assert store.saves, "_run_stage recorded the failure"
    _never_published(store)
    assert store.get(EP_ID)["stages"]["sunday"]["status"] == "failed"
    assert _control(store)["state"] == "approved"


def test_failed_hold_write_never_leaks_publication():
    store, ep, _ = _approved_store()
    decide(EP_ID, ep, action="reject", store=store)
    calls = []
    store.fail_save = lambda data: not calls and not calls.append(1)  # the hold's own save fails once
    env = _sunday(store)
    assert _status(env) == 500
    _never_published(store)
    assert _control(store)["state"] == "rejected"


def test_failed_publishing_save_stays_frozen_and_is_completed_from_the_checkpoint():
    store, ep, wed = _approved_store(pick=2)
    attempted = []

    def fail_publishing(data):
        if data.get("published_at"):
            attempted.append(copy.deepcopy(data))
            return True
        return False

    store.fail_save = fail_publishing
    env = _sunday(store)
    assert _status(env) == 500
    assert "may or may not" in env.error.detail and "checkpoint" in env.error.detail
    _never_published(store)
    sunday = (store.get(EP_ID)["stages"].get("sunday") or {})
    assert sunday.get("status") != "failed", "no stage-failure save after an ambiguous publish write"
    ctrl = _control(store)
    assert ctrl["state"] == "publishing"
    # The checkpoint is exactly the body the failed save tried to write.
    assert attempted and ctrl["publication"] == attempted[0]
    # Erik cannot change the photo, and the operator can never release it.
    with pytest.raises(photo_review.PublicationUnderway):
        decide(EP_ID, ep, action="reject", store=store)
    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    with patch.object(photo_review, "_now", return_value=far_future.isoformat()), \
            pytest.raises(photo_review.PhotoReviewError, match="never released"):
        photo_review.operator_release(store, EP_ID, store.get(EP_ID))
    # A re-fire completes the SAME publication, with no paid step.
    store.fail_save = None
    env = _sunday(store)
    assert env.result["published"] is True and env.result["completed_from_checkpoint"] is True
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    published = store.get(EP_ID)
    assert published["hero_image_url"] == wed["image_urls"][1]
    assert published["photo_approval"]["claim_id"] == ctrl["claim"]["claim_id"]
    assert published["published_at"] == attempted[0]["published_at"]
    assert published["stages"]["sunday"]["dialogue"] == attempted[0]["stages"]["sunday"]["dialogue"]
    assert _control(store)["state"] == "published"
    # The next run is the ordinary already-published path.
    assert _sunday(store).result["already_published"] is True


def test_publishing_save_that_landed_despite_an_error_is_reconciled_not_republished():
    store, ep, _ = _approved_store()
    original = store.save_episode

    def lands_then_raises(episode_id, data):
        original(episode_id, data)
        if data.get("published_at") and not data.get("_raised"):
            raise RuntimeError("timeout after write")

    store.save_episode = lands_then_raises
    assert _status(_sunday(store)) == 500
    store.save_episode = original
    env = _sunday(store)
    assert env.result["already_published"] is True
    env.dialogue.assert_not_called()
    assert _control(store)["state"] == "published"


def test_claim_released_by_operator_mid_run_is_never_published():
    store, ep, _ = _approved_store()

    def operator_releases(*_a, **_k):
        # No age condition: the release is a compare-and-swap.
        photo_review.operator_release(store, EP_ID, store.get(EP_ID))
        return [{"character": "Devon Park", "message": "Ready."}], "PASS"

    env = _sunday(store, dialogue_effect=operator_releases)
    assert _status(env) == 409
    _never_published(store)
    assert _control(store)["state"] == "approved"


def test_publish_landed_but_control_not_marked_is_reconciled_by_the_fast_path():
    store, ep, _ = _approved_store()
    original = photo_review.mark_published
    with patch.object(photo_review, "mark_published", side_effect=PhotoControlUnavailable("blob down")):
        env = _sunday(store)
    assert env.result["published"] is True and _control(store)["state"] == "publishing"
    photo_review.mark_published = original
    env = _sunday(store)
    assert env.result["already_published"] is True
    assert _control(store)["state"] == "published"


@pytest.mark.parametrize("error", [EpisodeReadStale("served ETag differs"), RuntimeError("blob down")])
def test_unverifiable_episode_read_spends_and_saves_nothing(error):
    store, _, _ = _approved_store()
    store.fail_verified = error
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    assert store.saves == [] and env.failure.call_count == 1
    assert _control(store)["state"] == "approved"


def test_unreadable_control_spends_and_saves_nothing():
    store, _, _ = _approved_store()
    store.fail_control_read = PhotoControlUnavailable("list failed")
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    assert store.saves == []


def test_already_published_week_keeps_its_idempotent_fast_path():
    store, _, _ = _approved_store()
    ep = store.get(EP_ID)
    ep.update(published_at="2026-10-11T12:00:00+00:00", hero_image_url="https://pinned/hero.png")
    store.put(EP_ID, ep)
    env = _sunday(store)
    assert env.result["already_published"] is True
    env.dialogue.assert_not_called()
    assert store.get(EP_ID)["hero_image_url"] == "https://pinned/hero.png"
    assert _control(store)["state"] == "approved"  # not this claim's publication: untouched


def test_legacy_confirmed_week_publishes_without_automatic_cleanup():
    """Codex cycle 2: cleanup ran before mark_publishing; now nothing is trashed by Sunday at all."""
    store, ep, wed = _approved_store(pick=2, wed=wednesday_stage(image_status="confirmed"))
    env = _sunday(store)
    assert env.result["published"] is True
    assert store.cleanups == []
    assert store.get(EP_ID)["stages"]["sunday"]["image_cleaned"] is False
    assert store.get(EP_ID)["hero_image_url"] == wed["image_urls"][1]


# --- Storage: the filesystem control log ------------------------------------

def test_filesystem_control_is_create_only_and_gapless():
    fs = _FilesystemBackend()
    body = {"episode_id": EP_ID, "version": 1}
    fs.create_photo_control_version(EP_ID, 1, body)
    with pytest.raises(PhotoControlConflict):
        fs.create_photo_control_version(EP_ID, 1, {**body, "x": 2})
    with pytest.raises(PhotoControlConflict):
        fs.create_photo_control_version(EP_ID, 3, {"episode_id": EP_ID, "version": 3})
    assert fs.read_photo_control(EP_ID) == body


def test_local_test_namespace_is_refused_not_faked():
    """Local episodes ignore the prefix, so a local test control would sit beside production's episode."""
    fs = _FilesystemBackend()
    cloudless = _CloudBackend.__new__(_CloudBackend)
    cloudless._blob_token, cloudless._fs, cloudless.prefix = "", fs, ""
    fs.create_photo_control_version(EP_ID, 1, {"episode_id": EP_ID, "version": 1})
    for backend in (fs, cloudless):
        assert backend.namespaces_episodes() is False
        with backend.prefix_scope("test/"):
            with pytest.raises(PhotoControlUnavailable, match="cloud-only"):
                backend.read_photo_control(EP_ID)
            with pytest.raises(PhotoControlUnavailable, match="cloud-only"):
                backend.create_photo_control_version(EP_ID, 2, {"episode_id": EP_ID, "version": 2})
    # Production is untouched, and was never written through the test prefix.
    assert cloudless.read_photo_control(EP_ID)["version"] == 1
    assert sorted(p.name for p in storage_module.PHOTO_CONTROL_DIR.rglob("*.json")) == ["v000001.json"]


def test_filesystem_control_rejects_a_body_that_is_not_its_version():
    fs = _FilesystemBackend()
    fs.create_photo_control_version(EP_ID, 1, {"episode_id": EP_ID, "version": 7})
    with pytest.raises(PhotoControlUnavailable):
        fs.read_photo_control(EP_ID)


def test_ambiguous_write_that_landed_counts_as_success():
    store, ep, _ = _approved_store()
    real = _FilesystemBackend.create_photo_control_version

    def lands_then_errors(self, episode_id, version, body):
        real(self, episode_id, version, body)
        raise PhotoControlUnavailable("timeout after write")

    with patch.object(_FilesystemBackend, "create_photo_control_version", lands_then_errors):
        view = photo_review.claim_for_publication(store, EP_ID, ep)
    assert view.state == "claimed" and _control(store)["write_id"] == view.raw["write_id"]


# --- Storage: the cloud backend over a fake Blob API --------------------------

class _FakeBlob:
    """Blob API + CDN. The CDN can serve an overwritten key's previous body."""

    API = "https://blob.vercel-storage.com"

    def __init__(self):
        self.objects: dict[str, list[tuple[bytes, str]]] = {}
        self.stale_cdn = False
        self.drop_etag = False
        self.metadata_status = 200
        self.metadata_without_etag = False
        self.fail_list = False
        self.put_lands_then_errors = False
        self.n = 0

    def url(self, path):
        return f"https://store.public.blob.vercel-storage.com/{path}"

    def _resp(self, status, payload=None, content=b"", headers=None):
        import requests

        r = requests.Response()
        r.status_code = status
        r._content = json.dumps(payload).encode() if payload is not None else content
        r.headers.update(headers or {})
        return r

    def put(self, url, data=None, headers=None, timeout=None):
        import requests

        path = url[len(self.API) + 1:]
        if headers.get("x-allow-overwrite") == "0" and path in self.objects:
            return self._resp(400, {"error": {"code": "bad_request", "message": "This blob already exists"}})
        self.n += 1
        self.objects.setdefault(path, []).append((data, f'"etag-{self.n}"'))
        if self.put_lands_then_errors:
            raise requests.ConnectionError("reset after write")
        return self._resp(200, {"url": self.url(path), "pathname": path, "etag": f'"etag-{self.n}"'})

    def get(self, url, params=None, headers=None, timeout=None):
        if url == self.API and params and "url" in params:
            path = params["url"]
            if self.metadata_status != 200:
                return self._resp(self.metadata_status, {"error": {"code": "unknown_error"}})
            if path not in self.objects:
                return self._resp(404, {"error": {"code": "not_found"}})
            meta = {"pathname": path, "url": self.url(path)}
            if not self.metadata_without_etag:
                meta["etag"] = self.objects[path][-1][1]
            return self._resp(200, meta)
        if url == self.API:
            if self.fail_list:
                return self._resp(500, {"error": {"code": "unknown_error"}})
            prefix = params["prefix"]
            blobs = [{"pathname": p, "url": self.url(p)} for p in sorted(self.objects) if p.startswith(prefix)]
            return self._resp(200, {"blobs": blobs, "hasMore": False})
        path = url.split(".com/", 1)[1]
        versions = self.objects.get(path)
        if not versions:
            return self._resp(404)
        body, etag = versions[-2] if self.stale_cdn and len(versions) > 1 else versions[-1]
        return self._resp(200, content=body, headers={} if self.drop_etag else {"ETag": etag})


@pytest.fixture
def cloud(monkeypatch):
    fake = _FakeBlob()
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.setattr("requests.get", fake.get)
    monkeypatch.setattr("requests.put", fake.put)
    backend = _CloudBackend()
    return backend, fake


def test_verified_episode_read_accepts_only_the_current_body(cloud):
    backend, fake = cloud
    backend.save_episode(EP_ID, {"episode_id": EP_ID, "v": 1})
    backend.save_episode(EP_ID, {"episode_id": EP_ID, "v": 2})
    assert backend.load_episode_verified(EP_ID)["v"] == 2
    fake.stale_cdn = True
    with pytest.raises(EpisodeReadStale):
        backend.load_episode_verified(EP_ID)


@pytest.mark.parametrize("fault", ["drop_etag", "metadata_500", "metadata_without_etag"])
def test_verified_episode_read_fails_closed(cloud, fault):
    backend, fake = cloud
    backend.save_episode(EP_ID, {"episode_id": EP_ID})
    if fault == "drop_etag":
        fake.drop_etag = True
    elif fault == "metadata_500":
        fake.metadata_status = 500
    else:
        fake.metadata_without_etag = True
    with pytest.raises(EpisodeReadStale):
        backend.load_episode_verified(EP_ID)


def test_verified_read_of_a_missing_episode_is_none(cloud):
    backend, _ = cloud
    assert backend.load_episode_verified("2026-W01") is None


def test_cloud_control_compare_and_swap(cloud):
    backend, fake = cloud
    backend.create_photo_control_version(EP_ID, 1, {"episode_id": EP_ID, "version": 1, "write_id": "a"})
    with pytest.raises(PhotoControlConflict):
        backend.create_photo_control_version(EP_ID, 1, {"episode_id": EP_ID, "version": 1, "write_id": "b"})
    assert backend.read_photo_control(EP_ID)["write_id"] == "a"
    fake.put_lands_then_errors = True
    backend.create_photo_control_version(EP_ID, 2, {"episode_id": EP_ID, "version": 2, "write_id": "c"})
    assert backend.read_photo_control(EP_ID)["version"] == 2
    fake.fail_list = True
    with pytest.raises(PhotoControlUnavailable):
        backend.read_photo_control(EP_ID)
    with pytest.raises(PhotoControlUnavailable):
        backend.create_photo_control_version(EP_ID, 3, {"episode_id": EP_ID, "version": 3, "write_id": "d"})


def test_cloud_control_is_namespaced(cloud):
    backend, fake = cloud
    with backend.prefix_scope("test/"):
        backend.create_photo_control_version(EP_ID, 1, {"episode_id": EP_ID, "version": 1})
    assert backend.read_photo_control(EP_ID) is None
    assert all(p.startswith("test/photo_control/") for p in fake.objects)


def test_cloud_legacy_bootstrap_refuses_a_stale_episode(cloud):
    backend, fake = cloud
    wed = wednesday_stage()
    del wed["photo_review"]
    backend.save_episode(EP_ID, _episode(wed))
    backend.save_episode(EP_ID, _episode(wed, ))
    fake.stale_cdn = True
    with pytest.raises(EpisodeReadStale):
        backend.load_episode_verified(EP_ID)
    assert backend.read_photo_control(EP_ID) is None


# --- Admin review page and endpoint ------------------------------------------

def _client(store, authed=True):
    from backend.admin.routes import create_routes
    from backend.auth.middleware import require_auth

    app = FastAPI()
    app.state.project_root = Path(".")
    app.state.templates = Jinja2Templates(directory="backend/admin/templates")
    create_routes(app)
    if authed:
        app.dependency_overrides[require_auth] = lambda: {"email": "erik@example.com", "sub": "1234"}
    client = TestClient(app, base_url="https://admin.test")
    patcher = patch("backend.storage.storage", store)
    patcher.start()
    return client, patcher


_SAME_ORIGIN = {"Origin": "https://admin.test"}


def _post(client, payload, headers=_SAME_ORIGIN, ns=None):
    query = f"?ns={ns}" if ns is not None else ""
    return client.post(f"/admin/episodes/{EP_ID}/photos/review{query}", json=payload, headers=headers)


def _registered_store(prefix="", **ep_extra):
    wed = wednesday_stage()
    ep = _episode(wed, **ep_extra)
    store = _Store()
    store.put(EP_ID, ep, prefix)
    with store.prefix_scope(prefix):
        register(EP_ID, wed, store)
    return store, wed


def test_select_appends_to_the_control_without_identity_or_episode_writes():
    store, wed = _registered_store()
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "select", "image_set_id": wed["photo_review"]["image_set_id"],
                              "path": wed["image_paths"][1]})
        page = client.get(f"/admin/episodes/{EP_ID}").text
    finally:
        patcher.stop()
    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    assert store.saves == []
    ctrl = _control(store)
    assert ctrl["state"] == "approved" and ctrl["decision"]["selected"]["path"] == wed["image_paths"][1]
    assert "erik@example.com" not in json.dumps(ctrl) and "1234" not in json.dumps(ctrl)
    assert "Approved</span>" in page and ">Selected<" in page


def test_test_namespace_is_reviewed_in_place_without_touching_production():
    store, wed = _registered_store(prefix="test/")
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}?ns=test")
        resp = _post(client, {"action": "select", "image_set_id": wed["photo_review"]["image_set_id"],
                              "path": wed["image_paths"][0]}, ns="test")
        prod = client.get(f"/admin/episodes/{EP_ID}")
    finally:
        patcher.stop()
    assert page.status_code == 200 and 'data-ns="test"' in page.text and "Test namespace" in page.text
    assert resp.status_code == 200
    assert _control(store, "test/")["state"] == "approved"
    assert _control(store) is None and prod.status_code == 404
    # ...and that approval is what a test-mode Sunday claims.
    env = _sunday(store, test=True)
    assert env.result["published"] is True


@pytest.mark.parametrize("ns", ["prod", "test/", "../x", "TEST"])
def test_unknown_namespace_is_refused(ns):
    store, wed = _registered_store()
    client, patcher = _client(store)
    try:
        get = client.get(f"/admin/episodes/{EP_ID}", params={"ns": ns})
        post = client.post(f"/admin/episodes/{EP_ID}/photos/review", params={"ns": ns},
                           json={"action": "reject", "image_set_id": "x"}, headers=_SAME_ORIGIN)
        listing = client.get("/admin/episodes", params={"ns": ns})
    finally:
        patcher.stop()
    assert get.status_code == post.status_code == listing.status_code == 400
    assert _control(store)["state"] == "awaiting"


def test_photos_ready_link_carries_the_namespace():
    from backend.utils import discord

    with patch.object(discord, "send_alert", return_value=True) as send:
        discord.notify_photos_ready(EP_ID, 3, namespace="test")
        discord.notify_photos_ready(EP_ID, 3)
    test_url, prod_url = (c.kwargs["url"] for c in send.call_args_list)
    assert test_url.endswith(f"/admin/episodes/{EP_ID}?ns=test#photo-review")
    assert prod_url.endswith(f"/admin/episodes/{EP_ID}#photo-review")
    assert all(len(c.kwargs["body"].splitlines()) <= 2 for c in send.call_args_list)


@pytest.mark.parametrize("payload,headers,code", [
    ({"action": "select", "image_set_id": "stale", "path": "x"}, _SAME_ORIGIN, 400),
    ({"action": "select", "image_set_id": None, "path": "x"}, _SAME_ORIGIN, 422),
    ({"action": "select", "path": "src/assets/images/other.png"}, _SAME_ORIGIN, 400),
    ({"action": "delete"}, _SAME_ORIGIN, 400),
    ({"action": "reject"}, {"Origin": "https://evil.example"}, 403),
    ({"action": "reject"}, {"Origin": "http://admin.test"}, 403),
    ({"action": "reject"}, {}, 403),
])
def test_bad_writes_are_refused_and_nothing_is_written(payload, headers, code):
    store, wed = _registered_store()
    payload = {"image_set_id": wed["photo_review"]["image_set_id"], **payload}
    client, patcher = _client(store)
    try:
        resp = _post(client, payload, headers=headers)
    finally:
        patcher.stop()
    assert resp.status_code == code
    assert _control(store)["state"] == "awaiting" and _control(store)["version"] == 1


def test_unauthenticated_write_is_blocked(monkeypatch):
    from backend.auth import middleware

    store, wed = _registered_store()
    monkeypatch.setattr(middleware, "_session_manager", MagicMock(verify_token=lambda _t: None))
    with patch("backend.config._Config.auth_bypass", new=property(lambda self: False)):
        client, patcher = _client(store, authed=False)
        try:
            resp = client.post(f"/admin/episodes/{EP_ID}/photos/review",
                               json={"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]},
                               headers=_SAME_ORIGIN, follow_redirects=False)
        finally:
            patcher.stop()
    assert resp.status_code in (401, 403, 307)
    assert _control(store)["version"] == 1


def test_published_week_cannot_be_reviewed():
    store, wed = _registered_store(published_at="2026-10-11T12:00:00+00:00")
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]})
        page = client.get(f"/admin/episodes/{EP_ID}").text
    finally:
        patcher.stop()
    assert resp.status_code == 409
    assert "Published." in page and "Use this" not in page


def test_claimed_week_is_frozen_with_an_honest_message():
    store, wed = _registered_store()
    ep = store.get(EP_ID)
    decide(EP_ID, ep, store=store)
    photo_review.claim_for_publication(store, EP_ID, ep)
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]})
        page = client.get(f"/admin/episodes/{EP_ID}").text
    finally:
        patcher.stop()
    assert resp.status_code == 409 and resp.json()["detail"] == "Publishing is underway. Choices are frozen."
    assert "Publishing is underway. Choices are frozen." in page
    assert "Use this" not in page and "None usable</button>" not in page.replace("\n", "").replace(" ", "")


@pytest.mark.parametrize("fault,detail", [
    (EpisodeReadStale("etag differs"), "Storage is still updating. Try again in a minute."),
    (RuntimeError("blob down"), "Could not read the episode from storage"),
])
def test_unverified_episode_read_refuses_the_decision(fault, detail):
    store, wed = _registered_store()
    store.fail_verified = fault
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]})
    finally:
        patcher.stop()
    assert resp.status_code == 503 and resp.json()["detail"] == detail
    assert _control(store)["version"] == 1


def test_control_write_failure_is_reported_not_hidden():
    store, wed = _registered_store()
    client, patcher = _client(store)
    try:
        with patch.object(_FilesystemBackend, "create_photo_control_version",
                          side_effect=PhotoControlUnavailable("blob down")):
            resp = _post(client, {"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]})
    finally:
        patcher.stop()
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Could not confirm the save. Reload to check your selection."


def test_lost_race_asks_for_a_reload():
    store, wed = _registered_store()
    ep = store.get(EP_ID)
    stale_view = photo_review.read_view(store, EP_ID, ep)
    decide(EP_ID, ep, action="reject", store=store)  # someone else writes v2
    with patch.object(photo_review, "read_view", return_value=stale_view):
        with pytest.raises(photo_review.ReviewConflict):
            decide(EP_ID, ep, store=store)
    assert _control(store)["state"] == "rejected"


def test_legacy_week_bootstraps_version_one_and_never_replaces_a_control():
    wed = wednesday_stage()
    del wed["photo_review"]
    ep = _episode(wed)
    store = _Store({EP_ID: ep})
    legacy = photo_review.episode_request(ep)
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}").text
        resp = _post(client, {"action": "select", "image_set_id": legacy["image_set_id"],
                              "path": wed["image_paths"][2]})
    finally:
        patcher.stop()
    assert "before photo review existed" in page and page.count("Use this") == 3
    assert resp.status_code == 200
    ctrl = _control(store)
    assert ctrl["version"] == 1 and ctrl["request"]["legacy"] is True
    # A second bootstrap from the same derived view loses the create.
    with patch.object(photo_review, "read_view", return_value=photo_review._derived_view(EP_ID, ep)):
        with pytest.raises(photo_review.ReviewConflict):
            decide(EP_ID, ep, action="reject", store=store)
    assert _control(store)["version"] == 1 and _control(store)["state"] == "approved"
    assert _sunday(store).result["published"] is True


@pytest.mark.parametrize("episode_id,expect", [
    ("2026-W01", "Sunday's scheduled run has passed; publishing needs a manual Sunday run (RUNBOOK)."),
    ("2099-W01", "Approved. Sunday's scheduled run will publish this photo."),
])
def test_approval_message_follows_the_episode_schedule_not_a_saved_hold(episode_id, expect):
    global EP_ID
    saved, EP_ID = EP_ID, episode_id
    try:
        store, wed = _registered_store()
        client, patcher = _client(store)
        try:
            resp = _post(client, {"action": "select", "image_set_id": wed["photo_review"]["image_set_id"],
                                  "path": wed["image_paths"][0]})
            page = client.get(f"/admin/episodes/{EP_ID}").text
        finally:
            patcher.stop()
    finally:
        EP_ID = saved
    assert resp.status_code == 200 and expect in resp.json()["message"]
    assert "publish_hold" not in store.get(episode_id)
    assert expect in html.unescape(page)


def test_awaiting_note_after_the_scheduled_run_explains_manual_continuation():
    global EP_ID
    saved, EP_ID = EP_ID, "2026-W01"
    try:
        store, _ = _registered_store()
        client, patcher = _client(store)
        try:
            page = client.get(f"/admin/episodes/{EP_ID}").text
        finally:
            patcher.stop()
    finally:
        EP_ID = saved
    assert "After you choose, publishing needs a manual Sunday run." in html.unescape(page)


@pytest.mark.parametrize("cloud", [True, False])
def test_delete_is_retired_everywhere(cloud):
    """Codex cycle 3: the local Delete could trash a frozen publication's images; it is gone (410)."""
    store, _ = _registered_store()
    store.cloud = cloud
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}").text
        with patch("send2trash.send2trash") as trash:
            resp = client.delete(f"/admin/episodes/{EP_ID}")
    finally:
        patcher.stop()
    assert 'id="delete-btn"' not in page and "delete-episode" not in page and "deleteEpisode" not in page
    assert resp.status_code == 410 and "Retired" in resp.json()["detail"]
    trash.assert_not_called()


@pytest.mark.parametrize("route", ["confirm", "override", "rerun"])
def test_legacy_image_controls_are_retired(route):
    store, _ = _registered_store()
    client, patcher = _client(store)
    try:
        resp = client.post(f"/admin/episodes/{EP_ID}/images/{route}", json={"variant_path": "x"})
    finally:
        patcher.stop()
    assert resp.status_code == 410
    assert _control(store)["version"] == 1


# --- Wednesday ------------------------------------------------------------------

def _photography_result(tmp_path, generation="g20261007T000000Z-abcdef"):
    variants = []
    for v in VARIANTS:
        local = tmp_path / f"{generation}-{v}.png"
        local.write_bytes(b"png")
        variants.append({"variant": v, "path": f"src/assets/images/r1/{generation}/round_1/{v}.png",
                         "local_path": str(local)})
    return {"rounds": [{"round": 1, "variants": variants, "passed": True}],
            "winner": {**variants[0], "round": 1}, "generation_id": generation,
            "selected_shots": [v["path"] for v in variants]}


def _wednesday(store, tmp_path, dialogue_effect=None, notify_result=True, generation="g20261007T000000Z-abcdef",
               orch=None):
    ep = _episode({})
    ep["stages"].pop("wednesday")
    if store.get(EP_ID) is None:
        store.put(EP_ID, ep)
    order = []
    if orch is None:
        orch = MagicMock()
        orch.return_value._execute_stage_photography.return_value = _photography_result(tmp_path, generation)
    body = cron_routes.StageRequest(episode_id=EP_ID, force=True)
    dialogue = MagicMock(return_value=([{"character": "Julian Torres", "message": "Done."}], "PASS"))
    if dialogue_effect:
        dialogue.side_effect = dialogue_effect
    real_register = photo_review.register_request

    def register_spy(*a, **k):
        order.append(("register", None))
        return real_register(*a, **k)

    store.save_image = lambda p, _b: f"{BLOB}/{p.split('images/')[1]}"
    with patch.object(cron_routes, "storage", store), \
         patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", return_value=body), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_get_orchestrator", return_value=orch), \
         patch.object(photo_review, "register_request", side_effect=register_spy), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", dialogue), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "notify_pipeline_failure", return_value=True) as failure, \
         patch.object(cron_routes, "notify_photos_ready",
                      side_effect=lambda *a, **k: order.append(("notify", (a, k))) or notify_result):
        try:
            asyncio.run(cron_routes.cron_wednesday(_request(path="/api/cron/wednesday")))
            error = None
        except Exception as exc:  # noqa: BLE001 - asserted by caller
            error = exc
    return SimpleNamespace(order=order, error=error, orch=orch, failure=failure)


def test_wednesday_registers_the_request_before_one_photos_ready_alert(tmp_path):
    store = _Store()
    run = _wednesday(store, tmp_path)
    assert run.error is None
    kinds = [k for k, _ in run.order]
    assert kinds == ["register", "notify"]
    assert run.order[1][1] == ((EP_ID, 3), {"namespace": ""})
    ctrl = _control(store)
    assert ctrl["state"] == "awaiting" and len(ctrl["request"]["candidates"]) == 3
    wed = store.get(EP_ID)["stages"]["wednesday"]
    assert wed["status"] == "complete"
    assert wed["photo_review"]["image_set_id"] == ctrl["request"]["image_set_id"]
    assert wed["photo_review"]["notification"]["status"] == "sent"


def test_wednesday_passes_the_episode_id_so_the_pan_follows_the_week(tmp_path):
    store = _Store()
    orch = MagicMock()
    orch.return_value._execute_stage_photography.return_value = _photography_result(tmp_path, "g20261007T000000Z-abcdef")
    run = _wednesday(store, tmp_path, orch=orch)
    assert run.error is None
    _, kwargs = orch.return_value._execute_stage_photography.call_args
    assert kwargs["episode_id"] == EP_ID


def test_a_shoot_out_of_time_fails_the_wednesday_stage_and_alerts(tmp_path):
    from backend.agents.art_director import ShootBudgetExceeded

    store = _Store()
    orch = MagicMock()
    orch.return_value._execute_stage_photography.side_effect = ShootBudgetExceeded("image budget (180s) exhausted")
    run = _wednesday(store, tmp_path, orch=orch)
    assert run.error is not None
    wed = store.get(EP_ID)["stages"]["wednesday"]
    assert wed["status"] == "failed"
    assert "image budget" in str(wed.get("error", ""))
    assert run.failure.called


def test_wednesday_rerun_voids_the_approval_and_uses_new_urls(tmp_path):
    store = _Store()
    _wednesday(store, tmp_path, generation="g20261007T000000Z-aaaaaa")
    first = photo_review.read_view(store, EP_ID, None)
    decide(EP_ID, store.get(EP_ID), store=store)
    _wednesday(store, tmp_path, generation="g20261008T000000Z-bbbbbb")
    second = photo_review.read_view(store, EP_ID, None)
    assert second.state == "awaiting" and second.decision is None
    assert not {c["url"] for c in first.request["candidates"]} & {c["url"] for c in second.request["candidates"]}


def test_wednesday_refuses_a_claimed_week_before_paying_for_photos(tmp_path):
    store, ep, _ = _approved_store()
    photo_review.claim_for_publication(store, EP_ID, ep)
    run = _wednesday(store, tmp_path)
    assert getattr(run.error, "status_code", None) == 409
    run.orch.return_value._execute_stage_photography.assert_not_called()
    assert _control(store)["state"] == "claimed"


def test_wednesday_that_loses_to_a_claim_does_not_replace_the_set(tmp_path):
    store, ep, wed = _approved_store()

    def photography(*_a, **_k):
        photo_review.claim_for_publication(store, EP_ID, ep)  # Sunday claims meanwhile
        return _photography_result(tmp_path)

    orch = MagicMock()
    orch.return_value._execute_stage_photography.side_effect = photography
    run = _wednesday(store, tmp_path, orch=orch)
    assert getattr(run.error, "status_code", None) == 409
    ctrl = _control(store)
    assert ctrl["state"] == "claimed" and ctrl["request"]["image_set_id"] == wed["photo_review"]["image_set_id"]
    assert not [k for k, _ in run.order if k == "notify"]


def test_wednesday_dialogue_failure_keeps_photos_and_request(tmp_path):
    store = _Store()
    run = _wednesday(store, tmp_path, dialogue_effect=RuntimeError("dialogue down"), notify_result=False)
    assert run.error is not None
    wed = store.get(EP_ID)["stages"]["wednesday"]
    assert wed["status"] == "failed" and len(wed["image_urls"]) == 3
    assert wed["photo_review"]["notification"]["status"] == "failed"
    assert _control(store)["state"] == "awaiting"


# --- Later-day context ------------------------------------------------------------

def test_friday_context_reports_actual_review_state():
    from scripts.simulate_dialogue_week import _build_dynamic_arc

    base = {"winner": {"variant": "macro_closeup"}, "rounds": []}
    awaiting = _build_dynamic_arc("friday", "X", {**base, "human_review": {"status": "awaiting"}})
    assert "waiting on the site editor" in awaiting and "approved Wednesday" not in awaiting
    approved = _build_dynamic_arc("friday", "X", {**base, "human_review": {
        "status": "approved", "selected_variant": "overhead_flatlay"}})
    assert "'overhead_flatlay'" in approved
    assert "approved Wednesday" in _build_dynamic_arc("friday", "X", base)


def test_dialogue_context_comes_from_the_control_and_never_guesses():
    store, ep, wed = _approved_store(pick=2)
    with patch.object(cron_routes, "storage", store):
        assert cron_routes._photo_review_dialogue_context(EP_ID, _episode(wednesday_stage(
            generation="g20260101T000000Z-zzzzzz"))) == {"status": "approved", "selected_variant": VARIANTS[1]}
        store.fail_control_read = PhotoControlUnavailable("down")
        assert cron_routes._photo_review_dialogue_context(EP_ID, ep) == {
            "status": "unknown", "selected_variant": None}


# --- Cleanup never trashes the chosen photo -----------------------------------------

def test_filesystem_cleanup_keeps_the_approved_generation_and_siblings(tmp_path, monkeypatch):
    images = tmp_path / "images"
    keep_dir = images / "r1" / "gA" / "round_1"
    other = images / "r1" / "gB" / "round_1"
    for d in (keep_dir, other):
        d.mkdir(parents=True)
        for name in ("hero.png", "hero.webp", "hero-400w.webp", "hero.social.jpg"):
            (d / name).write_bytes(b"x")
    monkeypatch.setattr(storage_module, "IMAGES_DIR", images)
    trashed = []
    monkeypatch.setattr("send2trash.send2trash", lambda p: trashed.append(p))
    out = _FilesystemBackend().cleanup_image_variants(
        "r1", keep_paths=["src/assets/images/r1/gA/round_1/hero.png"])
    assert out == trashed == [str(images / "r1" / "gB")]
    trashed.clear()
    _FilesystemBackend().cleanup_image_variants("r1")
    assert trashed == [str(images / "r1")]  # no kept photo: old behaviour


def test_backlog_cleanup_skips_directories_holding_approved_or_published_photos(tmp_path, monkeypatch, capsys):
    from scripts import cleanup_image_backlog as backlog

    images, episodes, control = tmp_path / "images", tmp_path / "episodes", tmp_path / "photo_control"
    for rid in ("approved", "published", "legacy", "unused"):
        (images / rid / "round_1").mkdir(parents=True)
        (images / rid / "round_1" / "a.png").write_bytes(b"x")
        (images / f"{rid}.png").write_bytes(b"x")
    episodes.mkdir()
    (episodes / "e1.json").write_text(json.dumps({
        "published_at": "x", "image_paths": ["src/assets/images/published/round_1/a.png"]}))
    (episodes / "e2.json").write_text(json.dumps({"stages": {"wednesday": {
        "image_status": "confirmed", "confirmed_winner": {"path": "src/assets/images/legacy/round_1/a.png"}}}}))
    (control / "production" / "e3").mkdir(parents=True)
    (control / "production" / "e3" / "v000002.json").write_text(json.dumps({"decision": {
        "selected": {"path": "src/assets/images/approved/round_1/a.png"}}}))
    monkeypatch.setattr(backlog, "IMAGES_DIR", images)
    monkeypatch.setattr(backlog, "EPISODES_DIR", episodes)
    monkeypatch.setattr(backlog, "PHOTO_CONTROL_DIR", control)
    trashed = []
    monkeypatch.setattr("send2trash.send2trash", lambda p: trashed.append(Path(p).name))
    monkeypatch.setattr("sys.argv", ["cleanup_image_backlog.py", "--execute"])
    backlog.main()
    assert trashed == ["unused"]
    assert "3 directories holding an approved/published image" in capsys.readouterr().out


# --- Monitoring: an expected hold is not a missing Sunday -----------------------------

def _held_week(**hold_overrides):
    stages = {d: {"status": "complete"} for d in ("monday", "tuesday", "thursday", "friday", "saturday")}
    stages["monday"] = {"status": "complete", "target_category": "savory",
                        "recipe_data": {"title": "Spinach Feta Egg Cups", "description": "Marcus's intro for the test week."}}
    ep = _episode(wednesday_stage())
    ep["stages"].update(stages)
    ep["target_category"] = "savory"
    ep["publish_hold"] = {
        "reason": "awaiting_photo_approval",
        "image_set_id": ep["stages"]["wednesday"]["photo_review"]["image_set_id"],
        "since": datetime(2026, 10, 12, tzinfo=timezone.utc).isoformat(),
        "notified": True,
        **hold_overrides,
    }
    return ep


def _integrity(ep):
    from backend.utils.episode_integrity import episode_integrity_failures, episode_summary

    failures = episode_integrity_failures(ep, now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    return failures, episode_summary(ep)


def test_recorded_hold_reads_as_awaiting_approval_not_missing():
    failures, summary = _integrity(_held_week())
    assert not [f for f in failures if f.startswith("sunday")]
    assert summary.endswith("not published: awaiting photo approval")


def test_hold_does_not_hide_an_unrelated_stage_failure():
    ep = _held_week()
    ep["stages"]["friday"] = {"status": "failed", "error": "judge down"}
    failures, _ = _integrity(ep)
    assert any(f.startswith("friday stage is 'failed'") for f in failures)


@pytest.mark.parametrize("tamper", [
    {"image_set_id": "0000000000000000"}, {"reason": "everything_is_fine"}, {"since": "yesterday"},
    "sunday_failed", "published",
])
def test_stale_forged_or_superseded_hold_is_still_reported(tamper):
    ep = _held_week(**(tamper if isinstance(tamper, dict) else {}))
    if tamper == "sunday_failed":
        ep["stages"]["sunday"] = {"status": "failed", "error": "blob down"}
    if tamper == "published":
        ep["published_at"] = "2026-10-18T00:00:00+00:00"
    failures, summary = _integrity(ep)
    assert any(f.startswith("sunday stage is") for f in failures)
    assert "awaiting photo approval" not in summary


# --- Operator reconciliation script ---------------------------------------------------

def test_reconcile_script_is_dry_run_by_default_and_releases_only_claimed(capsys):
    from scripts import photo_control

    store, ep, _ = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    with patch("backend.storage.storage", store):
        assert photo_control.main(["reconcile", EP_ID]) == 0          # dry run
        assert _control(store)["state"] == "claimed"
        assert photo_control.main(["reconcile", EP_ID, "--execute"]) == 0
    assert _control(store)["state"] == "approved"
    # The run that held the claim now loses it before publishing.
    body = cron_routes._published_copy(store.get(EP_ID), claimed, [], "PASS", "Spinach Feta Egg Cups")
    with pytest.raises(PhotoControlConflict):
        photo_review.mark_publishing(store, claimed, body)


def test_reconcile_script_never_releases_publishing_or_published(capsys):
    from scripts import photo_control

    store, ep, _ = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    body = cron_routes._published_copy(store.get(EP_ID), claimed, [], "PASS", "Spinach Feta Egg Cups")
    photo_review.mark_publishing(store, claimed, body)
    with patch("backend.storage.storage", store):
        assert photo_control.main(["reconcile", EP_ID, "--execute"]) == 2
    assert _control(store)["state"] == "publishing"
    assert "never released" in capsys.readouterr().out


def test_reconcile_script_records_a_publication_the_episode_proves():
    from scripts import photo_control

    store, ep, _ = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    published = cron_routes._published_copy(store.get(EP_ID), claimed, [], "PASS", "Spinach Feta Egg Cups")
    store.put(EP_ID, published)
    with patch("backend.storage.storage", store):
        assert photo_control.main(["reconcile", EP_ID, "--execute"]) == 0
    assert _control(store)["state"] == "published"


# --- Publication checkpoint: stale saves after publication -------------------

def test_stale_save_after_publication_is_repaired_from_the_checkpoint_without_paid_work():
    """A slow daily run (or a Sunday whose claim was released) saves a pre-publication copy."""
    store, ep, wed = _approved_store(pick=3)
    stale = store.get(EP_ID)
    assert _sunday(store).result["published"] is True
    published = store.get(EP_ID)
    # The stale whole-episode save drops published_at, the hero pin and photo_approval.
    stale["stages"]["sunday"] = {"status": "failed", "error": "stale worker"}
    store.put(EP_ID, stale)
    from backend.utils import episode_integrity
    assert not episode_integrity.recorded_photo_hold(store.get(EP_ID))

    # Wednesday and Erik still see a frozen week, not a decidable one.
    with pytest.raises(photo_review.PublicationUnderway):
        decide(EP_ID, store.get(EP_ID), action="reject", store=store)

    env = _sunday(store)
    assert env.result["completed_from_checkpoint"] is True
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    repaired = store.get(EP_ID)
    for key in ("published_at", "hero_image_url", "photo_approval", "image_urls"):
        assert repaired[key] == published[key], key
    assert repaired["stages"]["sunday"]["status"] == "complete"
    assert repaired["hero_image_url"] == wed["image_urls"][2]
    assert _control(store)["state"] == "published"


def test_checkpoint_publication_is_the_approved_set_even_after_a_stale_mirror():
    a = wednesday_stage(generation="g20260101T000000Z-aaaaaa")
    store, _, _ = _approved_store(wed=a)
    b = wednesday_stage(generation="g20260102T000000Z-bbbbbb")
    register(EP_ID, b, store)
    decide(EP_ID, store.get(EP_ID), pick=2, store=store)
    store.put(EP_ID, _episode(a))  # stale mirror of A
    _sunday(store)
    pub = _control(store)["publication"]
    wed_pub = pub["stages"]["wednesday"]
    assert pub["hero_image_url"] == b["image_urls"][1]
    assert wed_pub["image_paths"] == b["image_paths"]
    assert wed_pub["photography_data"] == b["photography_data"]
    assert photo_review.episode_request(pub)["image_set_id"] == b["photo_review"]["image_set_id"]


# --- Authoritative set B, stale mirror A: hold record and admin display --------

def _stale_mirror_store(snapshot=True):
    """Set A approved, Wednesday registers B (awaiting), a stale save restores A's mirror."""
    a = wednesday_stage(generation="g20260101T000000Z-aaaaaa")
    a["photography_data"]["rounds"][0]["variants"][0]["scores"] = {"defects": ["A-only defect"]}
    a["photography_data"]["automated_review_status"] = "passed"
    store, _, _ = _approved_store(wed=a)
    b = wednesday_stage(generation="g20260102T000000Z-bbbbbb")
    b["confirmed_winner"] = {"variant": VARIANTS[2], "path": b["image_paths"][2]}
    b["photography_data"]["rounds"][0]["variants"][1]["scores"] = {"defects": ["B pan rim warped"]}
    b["photography_data"]["automated_review_status"] = "failed"
    if snapshot:
        register(EP_ID, b, store)
    else:
        view = photo_review.read_view(store, EP_ID, None)
        request = {**b["photo_review"], "candidates": photo_review.build_candidates(b),
                   "image_paths": b["image_paths"], "image_urls": b["image_urls"]}
        photo_review._append(store, view, state=photo_review.AWAITING, request=request,
                             decision=None, claim=None, event="pre-snapshot request")
    stale = _episode(a)
    stale["stages"]["thursday"] = {"status": "complete"}
    store.put(EP_ID, stale)
    return store, a, b


def test_hold_on_the_authoritative_set_is_recognised_by_the_integrity_monitor():
    from backend.utils import episode_integrity

    store, a, b = _stale_mirror_store()
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    saved = store.get(EP_ID)
    assert saved["publish_hold"]["image_set_id"] == b["photo_review"]["image_set_id"]
    # The saved episode is self-consistent with the hold it records.
    assert photo_review.episode_request(saved)["image_set_id"] == b["photo_review"]["image_set_id"]
    assert saved["stages"]["wednesday"]["photography_data"] == b["photography_data"]
    assert episode_integrity.recorded_photo_hold(saved) == "awaiting_photo_approval"
    after_sunday = episode_integrity.stage_deadline(EP_ID, "sunday") + timedelta(hours=2)
    failures = episode_integrity.episode_integrity_failures(saved, now=after_sunday)
    assert not [f for f in failures if f.startswith("sunday")], failures
    assert "awaiting photo approval" in episode_integrity.episode_summary(saved)
    assert env.held.call_count == 1

    # Another stale save restores A; the next Sunday re-records the mirror
    # without a second alert.
    stale = _episode(a)
    stale["publish_hold"] = saved["publish_hold"]
    store.put(EP_ID, stale)
    env = _sunday(store)
    assert episode_integrity.recorded_photo_hold(store.get(EP_ID)) == "awaiting_photo_approval"
    assert env.held.call_count == 0


def _candidate_chunks(page: str) -> list[str]:
    """One chunk per candidate in the Photo review section only."""
    section = page.split('<section id="photo-review"', 1)[1].split("</section>", 1)[0]
    return section.split('data-lightbox-src="')[1:]


def test_admin_shows_the_authoritative_sets_evaluation_not_the_stale_mirrors():
    store, a, b = _stale_mirror_store()
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}").text
    finally:
        patcher.stop()
    chunks = _candidate_chunks(page)
    assert [c.split('"', 1)[0] for c in chunks] == b["image_urls"]
    picks = [i for i, c in enumerate(chunks) if "Team pick" in c.split('data-lightbox-src="')[0]]
    assert picks == [2]
    assert "B pan rim warped" in page and "A-only defect" not in page
    assert "Automated check: failed" in page


def test_admin_marks_the_evaluation_unavailable_rather_than_borrowing_another_sets():
    store, a, b = _stale_mirror_store(snapshot=False)
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}").text
    finally:
        patcher.stop()
    assert [c.split('"', 1)[0] for c in _candidate_chunks(page)] == b["image_urls"]
    assert "Team pick" not in page and "A-only defect" not in page
    assert "automated review for this photo set is not available" in page


# --- Test-namespace images: same-origin, authenticated -------------------------

_STORE_ORIGIN = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/"


def _real_store_wed(prefix: str, generation: str = "g20260101T000000Z-aaaaaa"):
    wed = wednesday_stage(generation=generation)
    base = _STORE_ORIGIN + prefix + "images/"
    wed["image_urls"] = [u.replace(BLOB + "/", base) for u in wed["image_urls"]]
    request = photo_review.new_review(wed)
    wed["photo_review"] = {k: request[k] for k in ("image_set_id", "generated_at", "requested_at")}
    return wed


def _real_store(prefix: str):
    wed = _real_store_wed(prefix)
    store = _Store()
    store.put(EP_ID, _episode(wed), prefix)
    with store.prefix_scope(prefix):
        register(EP_ID, wed, store)
    return store, wed


def test_test_namespace_candidates_load_through_the_authenticated_same_origin_route():
    store, wed = _real_store("test/")
    upstream = MagicMock(status_code=200, content=b"\x89PNG-bytes", headers={"Content-Type": "image/png"})
    upstream.raise_for_status.return_value = None
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}?ns=test").text
        srcs = [c.split('"', 1)[0] for c in _candidate_chunks(page)]
        with patch("requests.get", return_value=upstream) as fetch:
            img = client.get(html.unescape(srcs[1]))
    finally:
        patcher.stop()
    set_id = wed["photo_review"]["image_set_id"]
    assert srcs == [f"/admin/episodes/{EP_ID}/photos/{set_id}/{i}/image?ns=test" for i in (1, 2, 3)]
    assert img.status_code == 200 and img.content == b"\x89PNG-bytes"
    assert img.headers["content-type"] == "image/png"
    assert "no-store" in img.headers["cache-control"] and "private" in img.headers["cache-control"]
    fetch.assert_called_once()
    assert fetch.call_args.args[0] == wed["image_urls"][1]
    assert fetch.call_args.args[0].startswith(_STORE_ORIGIN + "test/images/")


def test_same_origin_image_urls_are_allowed_by_the_csp_and_routed_by_vercel():
    """The page's image URLs are relative; vercel.json routes them to a source CSP 'self' allows."""
    config = json.loads(Path("vercel.json").read_text())
    routes = config["routes"]
    csp = next(r["headers"]["Content-Security-Policy"] for r in routes
               if "Content-Security-Policy" in r.get("headers", {}))
    img_src = next(d for d in csp.split(";") if d.strip().startswith("img-src")).split()
    assert "'self'" in img_src and not any("vercel-storage" in d for d in img_src)
    srcs = [r.get("src") for r in routes]
    assert routes[srcs.index("/admin/(.*)")]["dest"] == "backend/admin/app.py"
    blob = routes[srcs.index("/blob-images/(.*)")]["dest"]
    # Production candidates use the rewrite, which reaches images/ only.
    store, wed = _real_store("")
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}").text
    finally:
        patcher.stop()
    prod = [c.split('"', 1)[0] for c in _candidate_chunks(page)]
    assert prod == [u.replace(_STORE_ORIGIN + "images/", "/blob-images/") for u in wed["image_urls"]]
    assert [blob.replace("$1", u.removeprefix("/blob-images/")) for u in prod] == wed["image_urls"]
    assert not blob.replace("$1", "x").startswith(_STORE_ORIGIN + "test/")


@pytest.mark.parametrize("case", ["production_ns", "off_prefix_url", "no_such_index", "not_an_image",
                                  "unauthenticated", "malformed_set", "uppercase_set", "other_set"])
def test_test_photo_route_refuses_everything_else(case, monkeypatch):
    from backend.auth import middleware

    prefix = "" if case == "production_ns" else "test/"
    store, wed = _real_store(prefix)
    if case == "unauthenticated":
        monkeypatch.setattr(middleware, "_session_manager", MagicMock(verify_token=lambda _t: None))
        monkeypatch.setattr("backend.config._Config.auth_bypass", property(lambda self: False))
    if case == "off_prefix_url":
        with store.prefix_scope("test/"):
            view = photo_review.read_view(store, EP_ID, None)
            request = copy.deepcopy(view.request)
            request["candidates"][0]["url"] = "https://evil.example/test/images/x.png"
            photo_review._append(store, view, state=photo_review.AWAITING, request=request,
                                 decision=None, claim=None, event="tampered")
    upstream = MagicMock(status_code=200, content=b"<html>", headers={"Content-Type": "text/html"})
    upstream.raise_for_status.return_value = None
    client, patcher = _client(store, authed=case != "unauthenticated")
    index = 9 if case == "no_such_index" else 1
    query = "" if case == "production_ns" else "?ns=test"
    set_id = {
        "malformed_set": "not-a-set-id",
        "uppercase_set": wed["photo_review"]["image_set_id"].upper(),
        "other_set": "0123456789abcdef",
    }.get(case, wed["photo_review"]["image_set_id"])
    try:
        with patch("requests.get", return_value=upstream) as fetch:
            resp = client.get(f"/admin/episodes/{EP_ID}/photos/{set_id}/{index}/image{query}",
                              follow_redirects=False)
    finally:
        patcher.stop()
    if case == "not_an_image":
        assert resp.status_code == 502
        return
    expected = {"unauthenticated": (401, 403, 307), "other_set": (409,)}.get(case, (404,))
    assert resp.status_code in expected
    fetch.assert_not_called()


def test_test_photo_route_has_no_set_less_form():
    """The pre-binding URL (index only) no longer serves anything."""
    store, wed = _real_store("test/")
    client, patcher = _client(store)
    try:
        with patch("requests.get") as fetch:
            resp = client.get(f"/admin/episodes/{EP_ID}/photos/1/image?ns=test", follow_redirects=False)
    finally:
        patcher.stop()
    assert resp.status_code in (404, 405)
    fetch.assert_not_called()


def _image_response(content: bytes):
    upstream = MagicMock(status_code=200, content=content, headers={"Content-Type": "image/png"})
    upstream.raise_for_status.return_value = None
    return upstream


def test_a_replaced_sets_image_url_is_refused_and_never_serves_the_new_sets_pixels():
    """An old tab reviewing set A cannot be shown set B's index-1 photo."""
    store, a = _real_store("test/")
    client, patcher = _client(store)
    try:
        page_a = client.get(f"/admin/episodes/{EP_ID}?ns=test").text
        srcs_a = [html.unescape(c.split('"', 1)[0]) for c in _candidate_chunks(page_a)]
        b = _real_store_wed("test/", generation="g20260102T000000Z-bbbbbb")
        store.put(EP_ID, _episode(b), "test/")
        with store.prefix_scope("test/"):
            register(EP_ID, b, store)
        page_b = client.get(f"/admin/episodes/{EP_ID}?ns=test").text
        srcs_b = [html.unescape(c.split('"', 1)[0]) for c in _candidate_chunks(page_b)]
        with patch("requests.get", return_value=_image_response(b"B-pixels")) as fetch:
            stale = client.get(srcs_a[0])
            stale_calls = fetch.call_count
            fresh = client.get(srcs_b[0])
    finally:
        patcher.stop()
    assert a["photo_review"]["image_set_id"] != b["photo_review"]["image_set_id"]
    assert not set(srcs_a) & set(srcs_b)
    assert all(b["photo_review"]["image_set_id"] in u for u in srcs_b)
    assert stale.status_code == 409 and stale.content != b"B-pixels"
    assert stale_calls == 0
    assert fresh.status_code == 200 and fresh.content == b"B-pixels"
    assert fetch.call_args.args[0] == b["image_urls"][0]
    assert "no-store" in fresh.headers["cache-control"]


def test_test_photo_route_refuses_when_there_is_no_current_set():
    store = _Store()
    wed = _real_store_wed("test/")
    del wed["photo_review"]
    wed["status"] = "running"  # no legacy request can be derived either
    store.put(EP_ID, _episode(wed), "test/")
    client, patcher = _client(store)
    try:
        with patch("requests.get") as fetch:
            resp = client.get(f"/admin/episodes/{EP_ID}/photos/0123456789abcdef/1/image?ns=test")
    finally:
        patcher.stop()
    assert resp.status_code == 409
    fetch.assert_not_called()


def _gallery_srcs(page: str) -> list[str]:
    """Image sources in the per-stage gallery (after the review section)."""
    gallery = page.split('<section id="photo-review"', 1)[1].split("</section>", 1)[1]
    return [c.split('"', 1)[0] for c in gallery.split('<img src="')[1:]]


def test_test_namespace_gallery_uses_the_set_bound_proxy_for_current_candidates():
    store, wed = _real_store("test/")
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}?ns=test").text
    finally:
        patcher.stop()
    set_id = wed["photo_review"]["image_set_id"]
    gallery = [html.unescape(u) for u in _gallery_srcs(page)]
    assert gallery == [f"/admin/episodes/{EP_ID}/photos/{set_id}/{i}/image?ns=test" for i in (1, 2, 3)]
    assert "not in the current review set" not in page


def test_test_namespace_gallery_hides_images_that_are_not_the_current_set():
    """A stale mirror (set A) on the episode while the control holds B: no broken or A images."""
    store, a = _real_store("test/")
    b = _real_store_wed("test/", generation="g20260102T000000Z-bbbbbb")
    with store.prefix_scope("test/"):
        register(EP_ID, b, store)
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}?ns=test").text
    finally:
        patcher.stop()
    gallery = _gallery_srcs(page)
    assert gallery == []
    assert page.count("Not shown: not in the current review set") == 3
    assert "/test/images/" not in page.split('<section id="photo-review"', 1)[1].split("</section>", 1)[1]
    assert not any(src.startswith("/src/") or src.startswith("/assets/") for src in gallery)


def test_admin_refuses_the_test_namespace_on_local_storage():
    class _LocalStore(_Store):
        def namespaces_episodes(self):
            return False

    store = _LocalStore()
    store.put(EP_ID, _episode(wednesday_stage()))
    client, patcher = _client(store)
    try:
        page = client.get(f"/admin/episodes/{EP_ID}?ns=test")
        listing = client.get("/admin/episodes?ns=test")
        post = client.post(f"/admin/episodes/{EP_ID}/photos/review?ns=test",
                           json={"action": "reject", "image_set_id": "x"}, headers=_SAME_ORIGIN)
        prod = client.get(f"/admin/episodes/{EP_ID}")
    finally:
        patcher.stop()
    assert page.status_code == listing.status_code == post.status_code == 400
    assert "cloud storage" in page.json()["detail"]
    assert prod.status_code == 200


# --- Malformed approvals approve nothing ---------------------------------------

@pytest.mark.parametrize("bad", ["status_rejected", "status_missing", "other_set", "set_missing"])
@pytest.mark.parametrize("state", [photo_review.APPROVED, photo_review.CLAIMED])
def test_a_malformed_approval_selects_nothing_and_sunday_holds(bad, state):
    """State says approved/claimed, but the decision is not an approval of THIS set."""
    store, ep, wed = _approved_store()
    view = photo_review.read_view(store, EP_ID, None)
    assert view.selected is not None
    decision = copy.deepcopy(view.decision)
    if bad == "status_rejected":
        decision["status"] = photo_review.REJECTED
    elif bad == "status_missing":
        decision.pop("status")
    elif bad == "other_set":
        decision["image_set_id"] = "0123456789abcdef"
    else:
        decision.pop("image_set_id")
    claim = {"claim_id": "c1c1c1c1c1c1c1c1", "claimed_at": "2026-10-11T00:00:00+00:00", "from_version": 2}
    body = {"schema": 1, "episode_id": EP_ID, "version": view.version + 1, "write_id": "x", "state": state,
            "request": view.request, "decision": decision,
            "claim": claim if state == photo_review.CLAIMED else None, "event": "malformed", "at": "2026-10-11T00:00:00+00:00"}
    store.create_photo_control_version(EP_ID, view.version + 1, body)
    if state == photo_review.CLAIMED:
        # A claimed version that approves nothing is inconsistent: it fails
        # closed at the read boundary (Codex cycle 3) rather than reading as
        # "claimed but selects nothing".
        with pytest.raises(PhotoControlUnavailable, match="inconsistent"):
            photo_review.read_view(store, EP_ID, None)
        env = _sunday(store)
        assert _status(env) == 503
        env.dialogue.assert_not_called()
        _never_published(store)
        assert _control(store)["version"] == view.version + 1
        return
    bad_view = photo_review.read_view(store, EP_ID, None)
    assert bad_view.selected is None
    assert photo_review.protected_image_paths(bad_view) == []
    assert photo_review.dialogue_context(bad_view)["status"] != photo_review.APPROVED
    if state == photo_review.APPROVED:
        assert photo_review.hold_reason(bad_view) == "awaiting_photo_approval"
        with pytest.raises(photo_review.PhotoHold):
            photo_review.claim_for_publication(store, EP_ID, ep)
        client, patcher = _client(store)
        try:
            page = client.get(f"/admin/episodes/{EP_ID}").text
        finally:
            patcher.stop()
        section = page.split('<section id="photo-review"', 1)[1].split("</section>", 1)[0]
        assert "Approved." not in section and ">Approved<" not in section
        assert "Awaiting your pick" in section
