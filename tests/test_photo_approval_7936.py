"""Human photo approval gates Sunday's publish (#7936). Offline only."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from backend.admin import cron_routes
from backend.utils import photo_review
from tests.photo_review_helpers import BLOB, DECISIONS, approved_wednesday, decide, wednesday_stage


def _episode(wed: dict, **extra) -> dict:
    ep = {
        "episode_id": "2026-W41",
        "concept": "Spinach Feta Egg Cups",
        "recipe_id": "r1",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {
                "title": "Spinach Feta Egg Cups",
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


def _request(method="POST", path="/api/cron/sunday"):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


@contextmanager
def _sunday_env(episode, body):
    saves = []
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", return_value=body), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode",
                      side_effect=lambda _id, d: saves.append(copy.deepcopy(d))), \
         patch.object(cron_routes.storage, "save_page"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue",
                      return_value=([{"character": "Devon Park", "message": "Ready."}], "PASS")) as dialogue, \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "PASS")) as qa, \
         patch.object(cron_routes, "_generate_episode_memories",
                      return_value={"saved": [], "absent": [], "failed": []}), \
         patch.object(cron_routes, "regenerate_and_upload", return_value="ok") as regen, \
         patch.object(cron_routes, "_indexnow_submit_urls"), \
         patch.object(cron_routes, "notify_publish_held", return_value=True) as held, \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog") as catalog, \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        yield SimpleNamespace(dialogue=dialogue, qa=qa, regen=regen, held=held,
                              catalog=catalog, saves=saves)


def _run_sunday(episode, test=False):
    body = cron_routes.StageRequest(episode_id="2026-W41", force=True, test=test)
    with _sunday_env(episode, body) as env:
        result = asyncio.run(cron_routes.cron_sunday(_request()))
    return result, env


# --- Sunday gate -----------------------------------------------------------

@pytest.mark.parametrize("test_mode", [False, True])
def test_awaiting_review_holds_sunday_before_any_paid_call(test_mode):
    episode = _episode(wednesday_stage())
    result, env = _run_sunday(episode, test=test_mode)

    assert result.status_code == 202
    payload = json.loads(result.body)
    assert payload["status"] == "awaiting_photo_approval"
    assert payload["published"] is False
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    env.regen.assert_not_called()
    env.catalog.assert_not_called()
    assert "published_at" not in episode
    assert episode["publish_hold"]["reason"] == "awaiting_photo_approval"
    # Persisted before the one alert.
    assert env.saves[0]["publish_hold"]["notified"] is False
    env.held.assert_called_once()


def test_rejected_review_holds_and_repeat_runs_stay_quiet():
    ep = _episode(wednesday_stage())
    decide(ep, action="reject")
    result, env = _run_sunday(ep)
    assert json.loads(result.body)["status"] == "photos_rejected"
    env.held.assert_called_once()

    result, env = _run_sunday(ep)
    assert result.status_code == 202
    env.held.assert_not_called()
    assert env.saves == []


def test_failed_hold_alert_is_not_recorded_as_sent():
    ep = _episode(wednesday_stage())
    body = cron_routes.StageRequest(episode_id="2026-W41", force=True)
    with _sunday_env(ep, body) as env:
        env.held.return_value = False
        asyncio.run(cron_routes.cron_sunday(_request()))
    assert ep["publish_hold"]["notified"] is False


def test_approved_photo_publishes_and_overrides_a_stale_pin():
    ep = _episode(approved_wednesday(pick=2), hero_image_url=f"{BLOB}/stale.png")
    result, env = _run_sunday(ep)

    assert result["published"] is True
    chosen = ep["stages"]["wednesday"]["image_urls"][1]
    assert ep["hero_image_url"] == chosen
    published_ep = env.catalog.call_args.args[0]
    assert published_ep["hero_image_url"] == chosen
    assert "publish_hold" not in ep
    assert ep["photo_approval"]["url"] == chosen and ep["photo_approval"]["record_id"]


def test_unreadable_decision_fails_the_stage_and_publishes_nothing():
    ep = _episode(approved_wednesday())
    DECISIONS.fail_reads = True
    body = cron_routes.StageRequest(episode_id="2026-W41", force=True)
    with _sunday_env(ep, body) as env, \
         patch.object(cron_routes, "notify_pipeline_failure", return_value=True) as alert:
        with pytest.raises(cron_routes.HTTPException) as exc:
            asyncio.run(cron_routes.cron_sunday(_request()))
    assert exc.value.status_code == 500
    env.dialogue.assert_not_called()
    env.catalog.assert_not_called()
    assert "published_at" not in ep
    assert ep["stages"]["sunday"]["status"] == "failed"
    alert.assert_called_once()


@pytest.mark.parametrize("change", ["reject", "other_photo"])
def test_decision_changed_during_paid_steps_holds_instead_of_publishing(change):
    """Two actors: Sunday passed the gate on an approval, Erik changed it mid-run."""
    ep = _episode(approved_wednesday(pick=1))

    def _qa_while_erik_changes_his_mind(_ep):
        if change == "reject":
            decide(ep, action="reject")
        else:
            decide(ep, action="select", pick=3)
        return True, "PASS"

    body = cron_routes.StageRequest(episode_id="2026-W41", force=True)
    with _sunday_env(ep, body) as env:
        env.qa.side_effect = _qa_while_erik_changes_his_mind
        result = asyncio.run(cron_routes.cron_sunday(_request()))
    assert result.status_code == 202
    assert json.loads(result.body)["status"] == "photo_review_changed"
    env.catalog.assert_not_called()
    env.regen.assert_not_called()
    assert "published_at" not in ep and "hero_image_url" not in ep
    assert ep["publish_hold"]["reason"] == "photo_review_changed"


def test_rerun_invalidates_an_earlier_approval():
    wed = approved_wednesday()
    ep = _episode(wed)
    assert photo_review.hold_status(ep, photo_review.load_decision(DECISIONS, ep)) is None
    # Changed images without a new request: the old request no longer matches.
    wed["image_urls"] = [u.replace("round_1", "round_1_v2") for u in wed["image_urls"]]
    assert photo_review.current_review(ep) is None
    assert photo_review.hold_status(ep, photo_review.load_decision(DECISIONS, ep)) == "awaiting_photo_approval"
    # A real Wednesday rerun starts a new request; the old decision does not carry over.
    wed["photo_review"] = photo_review.new_review(wed)
    assert photo_review.load_decision(DECISIONS, ep) is None
    assert photo_review.hold_status(ep, None) == "awaiting_photo_approval"


def test_already_published_week_keeps_its_idempotent_fast_path():
    ep = _episode(wednesday_stage(), published_at="2026-10-04T00:00:00+00:00",
                  hero_image_url=f"{BLOB}/pinned.png")
    result, env = _run_sunday(ep)
    assert result["already_published"] is True
    env.held.assert_not_called()
    env.dialogue.assert_not_called()
    assert ep["hero_image_url"] == f"{BLOB}/pinned.png"


def _rerun_wednesday(wed: dict) -> dict:
    """The same week after a Wednesday rerun: new photos, a new review request."""
    rerun = copy.deepcopy(wed)
    for key in ("image_paths", "image_urls"):
        rerun[key] = [u.replace("round_1", "round_2") for u in rerun[key]]
    for v in rerun["photography_data"]["rounds"][0]["variants"]:
        v["path"] = v["path"].replace("round_1", "round_2")
    rerun["photo_review"] = photo_review.new_review(rerun)
    return rerun


@contextmanager
def _cloud_sunday(warm_ep, cloud, body=None):
    """Sunday in a warm process: the cached read is ``warm_ep``, the uncached
    read is whatever ``cloud["ep"]`` holds at that moment (a separate copy)."""
    body = body or cron_routes.StageRequest(episode_id="2026-W41", force=True)
    reads = []

    def _strict(episode_id, use_cache=True):
        reads.append(use_cache)
        if isinstance(cloud["ep"], Exception):
            raise cloud["ep"]
        return copy.deepcopy(cloud["ep"])

    with _sunday_env(warm_ep, body) as env, \
         patch.object(cron_routes.storage, "load_episode_strict", side_effect=_strict), \
         patch.object(cron_routes, "notify_pipeline_failure", return_value=True) as alert:
        env.reads, env.alert = reads, alert
        yield env


def _published_saves(env):
    return [s for s in env.saves if s.get("published_at")]


def test_warm_process_does_not_publish_its_cached_image_set():
    """A warm Sunday cached set A; Wednesday reran to B; Erik approved A earlier."""
    warm = _episode(approved_wednesday(pick=1))
    current = _episode(_rerun_wednesday(warm["stages"]["wednesday"]))
    with _cloud_sunday(warm, {"ep": current}) as env:
        result = asyncio.run(cron_routes.cron_sunday(_request()))
    assert result.status_code == 202
    assert json.loads(result.body)["status"] == "awaiting_photo_approval"
    assert env.reads and all(use_cache is False for use_cache in env.reads)
    env.dialogue.assert_not_called()
    env.catalog.assert_not_called()
    assert not _published_saves(env)
    # Nothing from the stale copy was written over the rerun's photos.
    current_set = current["stages"]["wednesday"]["photo_review"]["image_set_id"]
    for saved in env.saves:
        assert saved["stages"]["wednesday"]["photo_review"]["image_set_id"] == current_set


@pytest.mark.parametrize("window", ["qa", "memories"])
def test_image_set_replaced_mid_run_holds_on_the_current_episode(window):
    ep = _episode(approved_wednesday(pick=1))
    cloud = {"ep": copy.deepcopy(ep)}
    rerun = _episode(_rerun_wednesday(ep["stages"]["wednesday"]))

    def _wednesday_reruns(*_a, **_k):
        cloud["ep"] = rerun
        return (True, "PASS") if window == "qa" else {"saved": [], "absent": [], "failed": []}

    with _cloud_sunday(ep, cloud) as env, \
         patch.object(cron_routes, "_generate_episode_memories",
                      side_effect=_wednesday_reruns if window == "memories" else
                      (lambda *_a, **_k: {"saved": [], "absent": [], "failed": []})):
        if window == "qa":
            env.qa.side_effect = _wednesday_reruns
        result = asyncio.run(cron_routes.cron_sunday(_request()))
    assert result.status_code == 202
    assert json.loads(result.body)["status"] == "photo_review_changed"
    env.catalog.assert_not_called()
    env.regen.assert_not_called()
    assert not _published_saves(env)
    rerun_set = rerun["stages"]["wednesday"]["photo_review"]["image_set_id"]
    assert env.saves and all(
        s["stages"]["wednesday"]["photo_review"]["image_set_id"] == rerun_set for s in env.saves
    )
    assert env.saves[-1]["publish_hold"] == {**env.saves[-1]["publish_hold"],
                                             "reason": "photo_review_changed", "image_set_id": rerun_set}


def test_week_published_by_another_run_is_not_published_again():
    ep = _episode(approved_wednesday(pick=1))
    cloud = {"ep": copy.deepcopy(ep)}

    def _other_run_publishes(_ep):
        cloud["ep"] = {**copy.deepcopy(ep), "published_at": "2026-10-04T07:00:00+00:00"}
        return True, "PASS"

    with _cloud_sunday(ep, cloud) as env:
        env.qa.side_effect = _other_run_publishes
        result = asyncio.run(cron_routes.cron_sunday(_request()))
    assert result["already_published"] is True
    assert result["published_at"] == "2026-10-04T07:00:00+00:00"
    assert env.saves == []
    env.catalog.assert_not_called()


@pytest.mark.parametrize("when", ["start", "before_publish"])
def test_episode_read_failure_alerts_and_saves_nothing(when):
    ep = _episode(approved_wednesday(pick=1))
    cloud = {"ep": RuntimeError("blob down") if when == "start" else copy.deepcopy(ep)}

    def _blob_goes_down(_ep):
        cloud["ep"] = RuntimeError("blob down")
        return True, "PASS"

    with _cloud_sunday(ep, cloud) as env:
        env.qa.side_effect = _blob_goes_down
        with pytest.raises(cron_routes.HTTPException) as exc:
            asyncio.run(cron_routes.cron_sunday(_request()))
    assert exc.value.status_code == 500
    assert env.saves == []
    env.catalog.assert_not_called()
    env.alert.assert_called_once()
    if when == "start":
        env.dialogue.assert_not_called()


def test_missing_episode_is_the_monday_gate_not_an_empty_success():
    with _cloud_sunday(_episode(wednesday_stage()), {"ep": None}) as env:
        with pytest.raises(cron_routes.HTTPException) as exc:
            asyncio.run(cron_routes.cron_sunday(_request()))
    assert exc.value.status_code == 409
    env.dialogue.assert_not_called()
    assert not _published_saves(env)


# --- Decision validation ---------------------------------------------------

@pytest.mark.parametrize("image_set", ["old", "", None])
def test_decision_refuses_stale_or_missing_image_set(image_set):
    wed = wednesday_stage()
    with pytest.raises(photo_review.PhotoReviewError):
        photo_review.make_decision(_episode(wed), action="select", image_set=image_set,
                                   path=wed["image_paths"][0], decided_by="x")


def test_decision_refuses_noncandidate_and_published():
    wed = wednesday_stage()
    ep = _episode(wed)
    set_id = wed["photo_review"]["image_set_id"]
    with pytest.raises(photo_review.PhotoReviewError):
        photo_review.make_decision(ep, action="select", image_set=set_id,
                                   path="../../etc/passwd", decided_by="x")
    published = _episode(wednesday_stage(), published_at="2026-10-04")
    with pytest.raises(photo_review.PhotoReviewError):
        photo_review.make_decision(published, action="reject",
                                   image_set=published["stages"]["wednesday"]["photo_review"]["image_set_id"],
                                   path=None, decided_by="x")
    before = copy.deepcopy(ep)
    decide(ep, pick=3)
    # A decision never touches the episode or its automated record.
    assert ep == before


def test_decision_for_a_vanished_candidate_is_invalid_not_approved():
    ep = _episode(wednesday_stage())
    record = decide(ep, pick=2)
    tampered = {**record, "selected": {**record["selected"], "url": "https://evil.example/x.png"}}
    assert photo_review.review_state(ep, tampered)["status"] == photo_review.INVALID
    assert photo_review.hold_status(ep, tampered) == "awaiting_photo_approval"


def test_catalog_entry_uses_the_pinned_human_choice():
    from backend.publishing import episode_renderer

    ep = _episode(approved_wednesday(pick=2))
    ep["hero_image_url"] = ep["stages"]["wednesday"]["image_urls"][1]
    written = {}
    with patch.object(episode_renderer.storage, "load_page", return_value=json.dumps({"recipes": []})), \
         patch.object(episode_renderer.storage, "save_page",
                      side_effect=lambda k, v: written.setdefault(k, v) or "url"):
        episode_renderer.publish_recipe_to_catalog(ep)
    catalog = json.loads(written["pages/recipes.json"])
    assert "r1/round_1/overhead_flatlay" in catalog["recipes"][0]["image"]


# --- Admin review endpoint -------------------------------------------------

class _FakeStore:
    def __init__(self, episodes, fail_load=False, fail_save=False):
        self.episodes = episodes
        self.fail_load = fail_load
        self.fail_save = fail_save
        self.prefix = "test/"
        self.saved = {}
        self.save_prefixes = []

    @contextmanager
    def prefix_scope(self, prefix):
        old, self.prefix = self.prefix, prefix
        try:
            yield
        finally:
            self.prefix = old

    def load_episode_strict(self, episode_id, use_cache=True):
        self.used_cache = use_cache
        if self.fail_load:
            raise RuntimeError("blob down")
        return copy.deepcopy(self.episodes.get(episode_id))

    def list_episodes_strict(self):
        return list(self.episodes.values())

    def save_episode(self, episode_id, data):
        raise AssertionError("the review endpoint must never rewrite the episode")

    def add_photo_decision(self, episode_id, image_set_id, record):
        if self.fail_save == "after_write":
            DECISIONS.add_photo_decision(episode_id, image_set_id, record)
            raise RuntimeError("timeout after write")
        if self.fail_save:
            raise RuntimeError("blob down")
        self.save_prefixes.append(self.prefix)
        self.saved[episode_id] = record
        return DECISIONS.add_photo_decision(episode_id, image_set_id, record)

    def latest_photo_decision(self, episode_id, image_set_id):
        return DECISIONS.latest_photo_decision(episode_id, image_set_id)


def _client(store, authed=True):
    from backend.admin.routes import create_routes
    from backend.auth.middleware import require_auth

    app = FastAPI()
    app.state.project_root = Path(".")
    app.state.templates = Jinja2Templates(directory="backend/admin/templates")
    create_routes(app)
    if authed:
        app.dependency_overrides[require_auth] = lambda: {"email": "erik@example.com"}
    client = TestClient(app, base_url="https://admin.test")
    patcher = patch("backend.storage.storage", store)
    patcher.start()
    return client, patcher


_SAME_ORIGIN = {"Origin": "https://admin.test"}


def _post(client, payload, headers=_SAME_ORIGIN):
    return client.post("/admin/episodes/2026-W41/photos/review", json=payload, headers=headers)


def test_select_saves_to_production_prefix_with_provenance():
    ep = _episode(wednesday_stage())
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "select", "image_set_id": ep["stages"]["wednesday"]["photo_review"]["image_set_id"],
                              "path": ep["stages"]["wednesday"]["image_paths"][1]})
    finally:
        patcher.stop()
    assert resp.status_code == 200, resp.text
    saved = store.saved["2026-W41"]
    assert saved["status"] == "approved"
    assert saved["selected"]["path"] == ep["stages"]["wednesday"]["image_paths"][1]
    # Decision records are on a public Blob store: no email or OAuth subject.
    assert saved["decided_by"] == "site editor"
    assert saved["decided_by_id"].startswith("editor-") and len(saved["decided_by_id"]) == 23
    assert "erik@example.com" not in json.dumps(saved)
    assert store.save_prefixes == [""]
    assert store.used_cache is False


@pytest.mark.parametrize("payload,headers,code", [
    ({"action": "select", "image_set_id": "SET", "path": "https://evil.example/x.png"}, _SAME_ORIGIN, 400),
    ({"action": "select", "image_set_id": "SET", "path": "src/assets/images/r1.png"}, _SAME_ORIGIN, 400),
    ({"action": "approve-everything", "image_set_id": "SET"}, _SAME_ORIGIN, 400),
    ({"path": "x"}, _SAME_ORIGIN, 422),
    ({"action": "select", "path": "src/assets/images/r1/round_1/macro_closeup.png"}, _SAME_ORIGIN, 422),
    ({"action": "reject", "image_set_id": "SET"}, {"Origin": "https://evil.example"}, 403),
    ({"action": "reject", "image_set_id": "SET"}, {"Origin": "http://admin.test"}, 403),
    ({"action": "reject", "image_set_id": "SET"},
     {"Origin": "https://evil.example", "X-Forwarded-Host": "evil.example"}, 403),
    ({"action": "reject", "image_set_id": "SET"}, {"Referer": "https://evil.example/admin.test"}, 403),
    ({"action": "reject", "image_set_id": "SET"}, {}, 403),
])
def test_bad_writes_are_refused_and_nothing_saved(payload, headers, code):
    ep = _episode(wednesday_stage())
    payload = {k: (ep["stages"]["wednesday"]["photo_review"]["image_set_id"] if v == "SET" else v)
               for k, v in payload.items()}
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        resp = _post(client, payload, headers)
    finally:
        patcher.stop()
    assert resp.status_code == code
    assert store.saved == {}


def test_unauthenticated_write_is_blocked(monkeypatch):
    from backend.auth import middleware

    ep = _episode(wednesday_stage())
    store = _FakeStore({"2026-W41": ep})
    monkeypatch.setattr(middleware, "_session_manager", MagicMock(verify_token=lambda _t: None))
    with patch("backend.config._Config.auth_bypass", new=property(lambda self: False)):
        client, patcher = _client(store, authed=False)
        try:
            resp = client.post("/admin/episodes/2026-W41/photos/review",
                               json={"action": "reject", "image_set_id": "x"},
                               headers=_SAME_ORIGIN, follow_redirects=False)
        finally:
            patcher.stop()
    assert resp.status_code in (401, 403, 307)
    assert store.saved == {}


@pytest.mark.parametrize("fail_load,fail_save", [(True, False), (False, True), (False, "after_write")])
def test_storage_failure_is_reported_not_hidden(fail_load, fail_save):
    ep = _episode(wednesday_stage())
    store = _FakeStore({"2026-W41": ep}, fail_load=fail_load, fail_save=fail_save)
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "reject",
                              "image_set_id": ep["stages"]["wednesday"]["photo_review"]["image_set_id"]})
        page = client.get("/admin/episodes/2026-W41") if fail_save == "after_write" else None
    finally:
        patcher.stop()
    assert resp.status_code == 503
    assert "success" not in resp.json()
    if fail_save:
        # The write may have landed: never claim nothing changed.
        assert resp.json()["detail"] == "Could not confirm the save. Reload to check your selection."
    if page is not None:
        assert "None usable</span>" in page.text


@pytest.mark.parametrize("route", ["confirm", "override", "rerun"])
def test_legacy_image_controls_are_retired(route):
    ep = _episode(wednesday_stage())
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        resp = client.post(f"/admin/episodes/2026-W41/images/{route}",
                           json={"variant_path": ep["stages"]["wednesday"]["image_paths"][1]},
                           headers=_SAME_ORIGIN)
    finally:
        patcher.stop()
    assert resp.status_code == 410
    assert "#photo-review" in resp.json()["detail"]
    assert store.saved == {} and DECISIONS.records == {}


def test_late_approval_says_publication_is_still_pending():
    ep = _episode(wednesday_stage(), publish_hold={"reason": "awaiting_photo_approval"})
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        resp = _post(client, {"action": "select",
                              "image_set_id": ep["stages"]["wednesday"]["photo_review"]["image_set_id"],
                              "path": ep["stages"]["wednesday"]["image_paths"][0]})
        page = client.get("/admin/episodes/2026-W41")
    finally:
        patcher.stop()
    expected = "Approved. The scheduled publishing time has passed; publication is still pending."
    assert resp.json()["message"] == expected
    assert expected in page.text
    assert "re-fire" not in page.text and "RUNBOOK" not in page.text


def test_review_page_renders_cloud_candidates_and_controls():
    wed = wednesday_stage()
    wed["photography_data"]["rounds"][0]["variants"][1]["scores"] = {"defects": ["wells fused together"]}
    wed["confirmed_winner"] = {"variant": "hero_threequarter", "path": wed["image_paths"][2]}
    ep = _episode(wed)
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        page = client.get("/admin/episodes/2026-W41")
        listing = client.get("/admin/episodes")
    finally:
        patcher.stop()
    assert page.status_code == 200
    html = page.text
    assert 'id="photo-review"' in html
    assert html.count('data-action="photo-select"') == 3
    assert 'data-action="photo-reject"' in html
    assert "rerun-photography" not in html
    for url in ep["stages"]["wednesday"]["image_urls"]:
        assert url in html
    assert html.count("Team pick") == 1 and "3. hero threequarter · r1 · <span" in html
    assert "wells fused together" in html
    assert listing.status_code == 200 and "2026-W41" in listing.text


def test_automated_rounds_label_unavailable_and_incomplete_never_passed():
    wed = wednesday_stage()
    base = wed["photography_data"]["rounds"][0]
    wed["photography_data"]["rounds"] = [
        {**base, "round": 1, "passed": False, "vision_evaluation": {"review_status": "unavailable"}},
        {**base, "round": 2, "passed": True, "vision_evaluation": {"review_status": "incomplete"}},
        {**base, "round": 3, "passed": False, "vision_evaluation": {"review_status": "failed"}},
        {**base, "round": 4, "passed": True, "vision_evaluation": {"review_status": "passed"}},
    ]
    store = _FakeStore({"2026-W41": _episode(wed)})
    client, patcher = _client(store)
    try:
        html = client.get("/admin/episodes/2026-W41").text
    finally:
        patcher.stop()
    assert ">UNAVAILABLE<" in html and ">INCOMPLETE<" in html
    assert html.count(">PASSED</span>") == 1
    assert "FAILED" in html


def test_rejected_week_never_shows_as_approved():
    ep = _episode(wednesday_stage())
    decide(ep, pick=2)
    decide(ep, action="reject")
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        html = client.get("/admin/episodes/2026-W41").text
    finally:
        patcher.stop()
    assert "None usable</span>" in html
    assert "Approved</span>" not in html and ">Selected<" not in html


# --- Wednesday request + notification -------------------------------------

def _photography_result(tmp_path):
    variants = []
    for v in ("macro_closeup", "overhead_flatlay", "hero_threequarter"):
        local = tmp_path / f"{v}.png"
        local.write_bytes(b"png")
        variants.append({"variant": v, "path": f"src/assets/images/r1/round_1/{v}.png",
                         "local_path": str(local)})
    return {"rounds": [{"round": 1, "variants": variants, "passed": True}],
            "winner": {**variants[0], "round": 1},
            "selected_shots": [v["path"] for v in variants]}


def _run_wednesday(tmp_path, dialogue_side_effect=None, notify_result=True):
    ep = _episode({})
    ep["stages"].pop("wednesday")
    order = []
    orch = MagicMock()
    orch.return_value._execute_stage_photography.return_value = _photography_result(tmp_path)
    body = cron_routes.StageRequest(episode_id="2026-W41", force=True)
    dialogue = MagicMock(return_value=([{"character": "Julian Torres", "message": "Done."}], "PASS"))
    if dialogue_side_effect:
        dialogue.side_effect = dialogue_side_effect
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", return_value=body), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_get_orchestrator", return_value=orch), \
         patch.object(cron_routes.storage, "load_episode", return_value=ep), \
         patch.object(cron_routes.storage, "save_episode",
                      side_effect=lambda _i, d: order.append(("save", copy.deepcopy(d)))), \
         patch.object(cron_routes.storage, "save_image",
                      side_effect=lambda p, _b: f"{BLOB}/{p.split('images/')[1]}"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", dialogue), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "notify_pipeline_failure", return_value=True), \
         patch.object(cron_routes, "notify_photos_ready",
                      side_effect=lambda *a: order.append(("notify", a)) or notify_result):
        try:
            asyncio.run(cron_routes.cron_wednesday(_request(path="/api/cron/wednesday")))
        except Exception as exc:  # noqa: BLE001 - asserted by caller
            return ep, order, exc
    return ep, order, None


def test_wednesday_persists_review_before_one_photos_ready_alert(tmp_path):
    ep, order, exc = _run_wednesday(tmp_path)
    assert exc is None
    kinds = [k for k, _ in order]
    assert kinds.index("save") < kinds.index("notify")
    assert kinds.count("notify") == 1
    first_save = order[0][1]["stages"]["wednesday"]
    assert first_save["status"] == "photos_ready" and len(first_save["photo_review"]["candidates"]) == 3
    assert order[kinds.index("notify")][1] == ("2026-W41", 3)
    review = ep["stages"]["wednesday"]["photo_review"]
    assert review["notification"]["status"] == "sent"
    assert ep["stages"]["wednesday"]["status"] == "complete"


def test_wednesday_dialogue_failure_keeps_photos_and_review(tmp_path):
    ep, order, exc = _run_wednesday(tmp_path, dialogue_side_effect=RuntimeError("dialogue down"),
                                    notify_result=False)
    assert exc is not None
    wed = ep["stages"]["wednesday"]
    assert wed["status"] == "failed"
    assert len(wed["photo_review"]["candidates"]) == 3
    assert wed["photo_review"]["notification"]["status"] == "failed"
    assert len(wed["image_urls"]) == 3


def test_photos_ready_alert_shape():
    from backend.utils import discord

    with patch.object(discord, "send_alert", return_value=False) as send:
        assert discord.notify_photos_ready("2026-W41", 3) is False
    kwargs = send.call_args.kwargs
    assert kwargs["subject"] == "Photos ready"
    assert kwargs["url"].endswith("/admin/episodes/2026-W41#photo-review")
    assert len(kwargs["body"].splitlines()) <= 2


# --- Later-day context -----------------------------------------------------

def test_friday_context_reports_actual_review_state():
    from scripts.simulate_dialogue_week import _build_dynamic_arc

    base = {"winner": {"variant": "macro_closeup"}, "rounds": []}
    awaiting = _build_dynamic_arc("friday", "X", {**base, "human_review": {"status": "awaiting"}})
    assert "waiting on the site editor" in awaiting and "approved Wednesday" not in awaiting
    approved = _build_dynamic_arc("friday", "X", {**base, "human_review": {
        "status": "approved", "selected_variant": "overhead_flatlay"}})
    assert "'overhead_flatlay'" in approved
    # Historical/lab contexts without a review are unchanged.
    assert "approved Wednesday" in _build_dynamic_arc("friday", "X", base)


# --- Evaluator criteria and winner handling --------------------------------

@pytest.fixture
def art_director():
    from backend.agents.factory import create_agent

    return create_agent("art_director")


def _vision(art_director, tmp_path, response):
    img = tmp_path / "a.png"
    img.write_bytes(b"png")
    paths = [{"variant": "macro_closeup", "path": "x", "local_path": str(img)}]
    with patch("backend.agents.art_director.generate_vision_response", return_value=json.dumps(response)) as call, \
         patch("backend.utils.discord.notify_pipeline_failure"):
        result = art_director._evaluate_images_vision(paths, "Egg Cups", "Ingredients: eggs, spinach")
    return result, call.call_args.kwargs["prompt"]


_GOOD = {"defects": [], "variety": 4, "quality": 4, "style_adherence": 4, "food_appeal": 4,
         "composition": 4, "muffin_pan_form": 5, "physical_realism": 5}


def test_prompt_names_pan_and_food_defects_and_recipe_facts(art_director, tmp_path):
    _, prompt = _vision(art_director, tmp_path, {"per_image": [{"image": 1, **_GOOD}], "set_diversity": 4})
    for phrase in ("non-overlapping", "fused", "underfilled", "liquid", "invented pastry",
                   "Empty wells alone are fine", "Ingredients: eggs, spinach", "physical_realism"):
        assert phrase in prompt


def test_physical_defect_fails_the_set(art_director, tmp_path):
    bad = {**_GOOD, "defects": ["wells fused together"], "physical_realism": 2}
    result, _ = _vision(art_director, tmp_path, {"per_image": [{"image": 1, **bad}], "set_diversity": 4})
    assert result["passed"] is False and result["review_status"] == "failed"


def test_missing_defect_list_is_not_an_automated_pass(art_director, tmp_path):
    partial = {k: v for k, v in _GOOD.items() if k != "defects"}
    result, _ = _vision(art_director, tmp_path, {"per_image": [{"image": 1, **partial}], "set_diversity": 4})
    assert result["review_status"] == "incomplete"


def _photograph(art_director, tmp_path, monkeypatch, evaluation):
    from backend.core.task import Task

    monkeypatch.setattr(art_director, "_repo_root", lambda: tmp_path)
    monkeypatch.setenv("STABILITY_API_KEY", "test-key")
    monkeypatch.setattr(art_director, "_call_stability", lambda *_a, **_k: b"png")
    monkeypatch.setattr("backend.agents.art_director._check_visual_diversity", lambda _p: True)
    calls = []
    monkeypatch.setattr(art_director, "_evaluate_images_vision",
                        lambda *_a: calls.append(1) or dict(evaluation))
    with patch("backend.storage.storage.save_image", return_value="u"):
        out = art_director.process_task(Task(
            type="photograph_recipe", content="shoot",
            context={"recipe_id": "rid-7", "recipe_data": {"title": "Egg Cups"}},
        )).output
    return out, calls


def test_recommended_winner_one_is_honoured(art_director, tmp_path, monkeypatch):
    out, _ = _photograph(art_director, tmp_path, monkeypatch,
                         {"passed": True, "review_status": "passed", "recommended_winner": 1})
    assert out["winner"]["variant"] == out["rounds"][0]["variants"][0]["variant"]
    assert out["automated_review_status"] == "passed"


def test_unavailable_review_stops_without_reshoot_and_is_not_approved(art_director, tmp_path, monkeypatch):
    out, calls = _photograph(art_director, tmp_path, monkeypatch,
                             {"passed": False, "review_status": "unavailable", "fallback": True})
    assert len(calls) == 1 and len(out["rounds"]) == 1
    assert out["automated_review_status"] == "unavailable"


def test_failed_rounds_never_report_approved(art_director, tmp_path, monkeypatch):
    out, calls = _photograph(art_director, tmp_path, monkeypatch,
                             {"passed": False, "review_status": "failed", "reason": "fused wells"})
    assert len(calls) == art_director._MAX_ROUNDS
    assert out["automated_review_status"] == "failed"


# --- Two actors: cron whole-episode writes vs. Erik's decision --------------

def test_wednesday_finishing_after_erik_approves_keeps_his_approval(tmp_path):
    """Erik approves from the Photos-ready alert while Wednesday's dialogue runs."""
    saved = {}

    def _dialogue_while_erik_approves(*_a, **_k):
        decide(saved["photos_ready"], pick=2)
        return [{"character": "Julian Torres", "message": "Done."}], "PASS"

    ep = _episode({})
    ep["stages"].pop("wednesday")
    orch = MagicMock()
    orch.return_value._execute_stage_photography.return_value = _photography_result(tmp_path)
    body = cron_routes.StageRequest(episode_id="2026-W41", force=True)

    def _save(_id, data):
        saved.setdefault("photos_ready", copy.deepcopy(data))
        saved["last"] = copy.deepcopy(data)

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", return_value=body), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_get_orchestrator", return_value=orch), \
         patch.object(cron_routes.storage, "load_episode", return_value=ep), \
         patch.object(cron_routes.storage, "save_episode", side_effect=_save), \
         patch.object(cron_routes.storage, "save_image",
                      side_effect=lambda p, _b: f"{BLOB}/{p.split('images/')[1]}"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", side_effect=_dialogue_while_erik_approves), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "notify_photos_ready", return_value=True):
        asyncio.run(cron_routes.cron_wednesday(_request(path="/api/cron/wednesday")))

    final = saved["last"]
    assert final["stages"]["wednesday"]["status"] == "complete"
    chosen = photo_review.approved_selection(final, photo_review.load_decision(DECISIONS, final))
    assert chosen and chosen["path"] == final["stages"]["wednesday"]["image_paths"][1]


def _fs_backend(tmp_path, monkeypatch):
    from backend import storage as storage_mod

    monkeypatch.setattr(storage_mod, "EPISODES_DIR", tmp_path / "episodes")
    monkeypatch.setattr(storage_mod, "PHOTO_REVIEWS_DIR", tmp_path / "photo_reviews")
    return storage_mod._FilesystemBackend()


def test_later_stage_saving_an_old_snapshot_cannot_erase_the_decision(tmp_path, monkeypatch):
    backend = _fs_backend(tmp_path, monkeypatch)
    ep = _episode(wednesday_stage())
    backend.save_episode("2026-W41", ep)
    thursday_snapshot = backend.load_episode("2026-W41")   # Thursday loads ...
    decide(ep, pick=3, store=backend)                     # ... Erik approves ...
    thursday_snapshot["stages"]["thursday"] = {"status": "complete"}
    backend.save_episode("2026-W41", thursday_snapshot)    # ... Thursday saves its old copy.

    sunday_view = backend.load_episode("2026-W41")
    chosen = photo_review.approved_selection(sunday_view, photo_review.load_decision(backend, sunday_view))
    assert chosen["path"] == ep["stages"]["wednesday"]["image_paths"][2]


def test_filesystem_decisions_are_append_only_and_prefix_scoped(tmp_path, monkeypatch):
    backend = _fs_backend(tmp_path, monkeypatch)
    ep = _episode(wednesday_stage())
    set_id = ep["stages"]["wednesday"]["photo_review"]["image_set_id"]
    first = decide(ep, pick=1, store=backend)
    second = decide(ep, action="reject", store=backend)
    assert first["record_id"] < second["record_id"]
    assert len(list((tmp_path / "photo_reviews" / "2026-W41" / set_id).glob("*.json"))) == 2
    assert backend.latest_photo_decision("2026-W41", set_id)["status"] == "rejected"
    with backend.prefix_scope("test/"):
        assert backend.latest_photo_decision("2026-W41", set_id) is None
    with pytest.raises(ValueError):
        backend.latest_photo_decision("../2026-W41", set_id)


class _FakeBlob:
    """Vercel Blob over requests, with the CDN behaviour that bites: a URL's
    body is cached on first read, so an OVERWRITTEN key serves the old body."""

    API = "https://blob.vercel-storage.com"

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.cdn: dict[str, bytes] = {}

    @staticmethod
    def _resp(code, payload=None, raw=None):
        def _raise():
            if code >= 400:
                raise RuntimeError(f"HTTP {code}")
        return SimpleNamespace(ok=code < 400, status_code=code, raise_for_status=_raise,
                               json=lambda: payload if raw is None else json.loads(raw))

    def put(self, url, data, headers, timeout):
        pathname = url.removeprefix(self.API + "/")
        if pathname in self.objects and headers.get("x-allow-overwrite") == "0":
            return self._resp(409)
        self.objects[pathname] = data
        return self._resp(200, {"url": f"https://cdn.test/{pathname}"})

    def get(self, url, params=None, headers=None, timeout=None):
        if url == self.API:
            prefix = params["prefix"]
            blobs = [{"pathname": k, "url": f"https://cdn.test/{k}"}
                     for k in sorted(self.objects) if k.startswith(prefix)]
            return self._resp(200, {"blobs": blobs, "hasMore": False})
        pathname = url.removeprefix("https://cdn.test/")
        if url not in self.cdn:
            self.cdn[url] = self.objects[pathname]
        return self._resp(200, raw=self.cdn[url])


def test_warm_cached_process_sees_a_later_reject(monkeypatch):
    from backend import storage as storage_mod

    blob = _FakeBlob()
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")
    monkeypatch.setattr("requests.put", blob.put)
    monkeypatch.setattr("requests.get", blob.get)
    sunday_lambda = storage_mod._CloudBackend()
    admin_lambda = storage_mod._CloudBackend()

    ep = _episode(wednesday_stage())
    sunday_lambda.save_episode("2026-W41", ep)
    warm_ep = sunday_lambda.load_episode_strict("2026-W41")
    decide(warm_ep, pick=1, store=admin_lambda)
    assert photo_review.hold_status(warm_ep, photo_review.load_decision(sunday_lambda, warm_ep)) is None

    decide(admin_lambda.load_episode_strict("2026-W41", use_cache=False), action="reject", store=admin_lambda)
    # Same warm process, same cached episode: the decision is still read fresh.
    assert photo_review.hold_status(warm_ep, photo_review.load_decision(sunday_lambda, warm_ep)) == "photos_rejected"


def test_cloud_decision_failures_raise_instead_of_guessing(monkeypatch):
    from backend import storage as storage_mod

    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fake-token")
    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))
    monkeypatch.setattr("requests.put", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("slow")))
    backend = storage_mod._CloudBackend()
    with pytest.raises(storage_mod.PhotoDecisionUnavailable):
        backend.latest_photo_decision("2026-W41", "abc")
    with pytest.raises(storage_mod.PhotoDecisionUnavailable):
        backend.add_photo_decision("2026-W41", "abc", {"status": "rejected"})


# --- Later-day context -------------------------------------------------------

@pytest.mark.parametrize("day", ["thursday", "saturday", "sunday"])
def test_thu_sat_sun_state_the_actual_review(day):
    from scripts.simulate_dialogue_week import _build_dynamic_arc

    plain = _build_dynamic_arc(day, "X")
    assert _build_dynamic_arc(day, "X", None) == plain
    rejected = _build_dynamic_arc(day, "X", {"human_review": {"status": "rejected"}})
    assert rejected.startswith(plain) and "rejected every Wednesday photo" in rejected
    unknown = _build_dynamic_arc(day, "X", {"human_review": {"status": "unknown"}})
    assert "not confirmed" in unknown and "picked" not in unknown


def test_wednesday_pick_is_only_a_recommendation_when_review_exists():
    from scripts.simulate_dialogue_week import _build_dynamic_arc, _build_photography_scene_direction

    ctx = {"winner": {"variant": "macro_closeup"}, "rounds": [], "reshoot_happened": False}
    reviewed = {**ctx, "human_review": {"status": "awaiting", "selected_variant": None}}
    assert "recommendation; the site editor makes the final pick" in _build_dynamic_arc("wednesday", "X", reviewed)
    assert "the site editor makes the final pick" in _build_photography_scene_direction(reviewed, "wednesday")
    # Historical/lab prompts are unchanged.
    assert "they land on 'macro_closeup' as the hero." in _build_photography_scene_direction(ctx, "wednesday")
    assert "site editor" not in _build_dynamic_arc("wednesday", "X", ctx)


def test_dialogue_context_never_reports_an_unread_decision_as_approved():
    ep = _episode(approved_wednesday())
    assert cron_routes._photo_review_dialogue_context(ep)["status"] == "approved"
    DECISIONS.fail_reads = True
    assert cron_routes._photo_review_dialogue_context(ep) == {"status": "unknown", "selected_variant": None}
    assert cron_routes._photo_review_only_context(_episode({})) is None


# --- Evaluator: defects are recorded, never a reshoot trigger ---------------

def _vision_n(art_director, tmp_path, response, n=2, missing=()):
    paths = []
    for i in range(n):
        img = tmp_path / f"img{i}.png"
        if i not in missing:
            img.write_bytes(b"png")
        paths.append({"variant": f"v{i}", "path": f"nowhere/v{i}.png", "local_path": str(img)})
    with patch("backend.agents.art_director.generate_vision_response",
               return_value=json.dumps(response)) as call, \
         patch("backend.utils.discord.notify_pipeline_failure"):
        result = art_director._evaluate_images_vision(paths, "Egg Cups")
    return result, call


def test_listed_defect_blocks_a_pass_even_with_a_perfect_realism_score(art_director, tmp_path):
    lying = {**_GOOD, "defects": ["custard shells underfilled"], "physical_realism": 5}
    result, _ = _vision_n(art_director, tmp_path, {
        "per_image": [{"image": 1, **_GOOD}, {"image": 2, **lying}], "set_diversity": 4})
    assert result["passed"] is False and result["review_status"] == "failed"
    assert result["quality_defects"] == {"2": ["custard shells underfilled"]}
    # Original criteria passed, so no paid reshoot over the new standard.
    assert result["criteria_passed"] is True and result["retry_eligible"] is False


@pytest.mark.parametrize("per_image", [
    [{"image": 1, **_GOOD}],                                   # missing record
    [{"image": 1, **_GOOD}, {"image": 1, **_GOOD}],            # duplicate
    [{"image": 1, **_GOOD}, {"image": 3, **_GOOD}],            # out of range
    [{"image": 1, **_GOOD}, {"image": "2", **_GOOD}],          # not an int
    [{"image": 1, **_GOOD}, {"image": 2, **{k: v for k, v in _GOOD.items() if k != "physical_realism"}}],
    [{"image": 1, **_GOOD}, {"image": 2, **_GOOD, "quality": "high"}],
    "not a list",
])
def test_malformed_per_image_is_incomplete_never_passed_never_reshot(art_director, tmp_path, per_image):
    result, _ = _vision_n(art_director, tmp_path, {"per_image": per_image, "set_diversity": 4})
    assert result["review_status"] == "incomplete"
    assert result["passed"] is False and result["retry_eligible"] is False


def test_original_criteria_failure_still_buys_the_reshoot(art_director, tmp_path):
    weak = {**_GOOD, "quality": 2, "food_appeal": 2}
    result, _ = _vision_n(art_director, tmp_path, {
        "per_image": [{"image": 1, **weak}, {"image": 2, **weak}], "set_diversity": 4})
    assert result["review_status"] == "failed" and result["retry_eligible"] is True


def test_partial_image_set_is_not_ranked_or_reshot(art_director, tmp_path):
    result, call = _vision_n(art_director, tmp_path, {"per_image": []}, n=3, missing=(1,))
    call.assert_not_called()
    assert result["review_status"] == "incomplete"
    assert result["passed"] is False and result["retry_eligible"] is False
    assert "v1" in result["reason"]


@pytest.mark.parametrize("evaluation", [
    {"passed": False, "review_status": "failed", "retry_eligible": False, "reason": "fused wells"},
    {"passed": False, "review_status": "incomplete", "retry_eligible": False},
])
def test_quality_only_or_incomplete_review_stops_after_one_round(art_director, tmp_path, monkeypatch, evaluation):
    out, calls = _photograph(art_director, tmp_path, monkeypatch, evaluation)
    assert len(calls) == 1 and len(out["rounds"]) == 1
    assert out["automated_review_status"] == evaluation["review_status"]


# --- Monitoring: an expected hold is not a missing Sunday ---------------------

def _held_week(**hold_overrides):
    from datetime import datetime, timezone

    stages = {d: {"status": "complete"} for d in ("monday", "tuesday", "thursday", "friday", "saturday")}
    stages["monday"] = {"status": "complete", "target_category": "savory",
                        "recipe_data": {"title": "Spinach Feta Egg Cups"}}
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
    from datetime import datetime, timezone
    from backend.utils.episode_integrity import episode_integrity_failures, episode_summary

    failures = episode_integrity_failures(ep, now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    return failures, episode_summary(ep)


def test_recorded_hold_reads_as_awaiting_approval_not_missing():
    failures, summary = _integrity(_held_week())
    assert not [f for f in failures if f.startswith("sunday")]
    assert summary.endswith("not published: awaiting photo approval")
    assert " published" not in summary.replace("not published", "")


def test_hold_does_not_hide_an_unrelated_stage_failure():
    ep = _held_week()
    ep["stages"]["friday"] = {"status": "failed", "error": "judge down"}
    failures, _ = _integrity(ep)
    assert any(f.startswith("friday stage is 'failed'") for f in failures)
    assert not [f for f in failures if f.startswith("sunday")]


@pytest.mark.parametrize("tamper", [
    {"image_set_id": "0000000000000000"},       # stale: an earlier image set
    {"reason": "everything_is_fine"},           # forged reason
    {"since": "yesterday"},                     # malformed
    "sunday_failed",                            # an independent Sunday failure
    "published",
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


# --- Weeks whose Wednesday completed before #7936 ---------------------------

_PRE_DEPLOY_COMPLETED_AT = "2026-10-07T17:04:12.551203+00:00"


def _pre_deploy_wednesday(**overrides) -> dict:
    """Wednesday exactly as cron_wednesday stored it before #7936: no photo_review."""
    wed = wednesday_stage()
    del wed["photo_review"]
    wed.update({
        "stage": "photography",
        "concept": "Spinach Feta Egg Cups",
        "reshoot_happened": False,
        "dialogue": [{"character": "Julian Torres", "message": "The macro reads best."}],
        "judge_verdict": "PASS",
        "completed_at": _PRE_DEPLOY_COMPLETED_AT,
    })
    wed.update(overrides)
    return wed


def _legacy_set_id(ep: dict) -> str:
    return photo_review.current_review(ep)["image_set_id"]


def test_pre_deploy_wednesday_gets_a_stable_derived_review():
    ep = _episode(_pre_deploy_wednesday())
    before = copy.deepcopy(ep)
    review = photo_review.current_review(ep)
    assert review["legacy"] is True
    assert [c["url"] for c in review["candidates"]] == ep["stages"]["wednesday"]["image_urls"]
    assert review["generated_at"] == _PRE_DEPLOY_COMPLETED_AT
    # No request was sent for it, and none is claimed.
    assert review["requested_at"] is None and "notification" not in review
    # Deterministic for every reader, and derived without writing anything.
    assert photo_review.current_review(copy.deepcopy(ep))["image_set_id"] == review["image_set_id"]
    assert ep == before
    assert photo_review.hold_status(ep, None) == "awaiting_photo_approval"
    with patch.object(cron_routes.photo_review, "load_decision", return_value=None):
        assert cron_routes._photo_review_dialogue_context(ep) == {"status": "awaiting", "selected_variant": None}


def test_pre_deploy_week_shows_controls_and_an_approval_persists_and_publishes():
    ep = _episode(_pre_deploy_wednesday())
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        page = client.get("/admin/episodes/2026-W41")
        resp = _post(client, {"action": "select", "image_set_id": _legacy_set_id(ep),
                              "path": ep["stages"]["wednesday"]["image_paths"][1]})
        after = client.get("/admin/episodes/2026-W41")
    finally:
        patcher.stop()
    assert page.status_code == 200
    assert page.text.count('data-action="photo-select"') == 3
    assert 'data-action="photo-reject"' in page.text
    assert "made before photo review existed" in page.text
    assert resp.status_code == 200, resp.text
    assert store.saved["2026-W41"]["image_set_id"] == _legacy_set_id(ep)
    # The episode was never rewritten (the fake store raises on save_episode).
    assert "photo_review" not in ep["stages"]["wednesday"]
    assert "Approved</span>" in after.text

    result, env = _run_sunday(ep)
    assert result["published"] is True
    chosen = ep["stages"]["wednesday"]["image_urls"][1]
    assert ep["hero_image_url"] == chosen
    assert env.catalog.call_args.args[0]["hero_image_url"] == chosen
    env.held.assert_not_called()


def test_pre_deploy_week_without_a_choice_holds_and_reads_as_awaiting():
    ep = _episode(_pre_deploy_wednesday())
    result, env = _run_sunday(ep)
    assert result.status_code == 202
    assert json.loads(result.body)["status"] == "awaiting_photo_approval"
    env.dialogue.assert_not_called()
    assert ep["publish_hold"]["image_set_id"] == _legacy_set_id(ep)

    ep["stages"].update({d: {"status": "complete"} for d in ("tuesday", "thursday", "friday", "saturday")})
    ep["stages"]["monday"]["target_category"] = ep["target_category"] = "savory"
    failures, summary = _integrity(ep)
    assert not [f for f in failures if f.startswith("sunday")]
    assert summary.endswith("not published: awaiting photo approval")


@pytest.mark.parametrize("rerun", ["new_photos", "same_urls_overwritten", "explicit_wednesday_rerun"])
def test_pre_deploy_approval_does_not_survive_a_changed_photo_set(rerun):
    wed = _pre_deploy_wednesday()
    ep = _episode(wed)
    record = photo_review.make_decision(ep, action="select", image_set=_legacy_set_id(ep),
                                        path=wed["image_paths"][0], decided_by="test")
    photo_review.save_decision(DECISIONS, ep, record)
    assert photo_review.hold_status(ep, photo_review.load_decision(DECISIONS, ep)) is None

    if rerun == "new_photos":
        wed["image_urls"] = [u.replace("round_1", "round_1_v2") for u in wed["image_urls"]]
        wed["completed_at"] = "2026-10-08T09:00:00+00:00"
    elif rerun == "same_urls_overwritten":
        # Reshot images saved over the same canonical blob paths.
        wed["completed_at"] = "2026-10-08T09:00:00+00:00"
    else:
        ep["stages"]["wednesday"] = wed = _rerun_wednesday(wed)
    assert photo_review.load_decision(DECISIONS, ep) is None
    assert photo_review.hold_status(ep, photo_review.load_decision(DECISIONS, ep)) == "awaiting_photo_approval"


@pytest.mark.parametrize("explicit", [None, "not-a-dict", {}, {"image_set_id": "0000000000000000"}])
def test_explicit_request_is_authoritative_even_when_malformed_or_stale(explicit):
    ep = _episode(_pre_deploy_wednesday(photo_review=explicit))
    implied = photo_review.legacy_review(ep["stages"]["wednesday"])["image_set_id"]
    assert photo_review.current_review(ep) is None
    assert photo_review.hold_status(ep, None) == "awaiting_photo_approval"
    # The id a missing request would have derived approves nothing here.
    with pytest.raises(photo_review.PhotoReviewError):
        photo_review.make_decision(ep, action="reject", image_set=implied, path=None, decided_by="test")


@pytest.mark.parametrize("overrides", [
    {"image_urls": ["", "", ""]},       # nothing was uploaded
    {"image_urls": []},
    {"status": "failed"},
    {"completed_at": None},
])
def test_pre_deploy_week_without_uploaded_photos_has_nothing_to_approve(overrides):
    ep = _episode(_pre_deploy_wednesday(**overrides))
    assert photo_review.current_review(ep) is None
    assert photo_review.hold_status(ep, None) == "awaiting_photo_approval"
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        page = client.get("/admin/episodes/2026-W41")
        resp = _post(client, {"action": "reject", "image_set_id": "0000000000000000"})
    finally:
        patcher.stop()
    assert "No current photo set to review." in page.text
    assert 'data-action="photo-select"' not in page.text
    assert resp.status_code == 400 and store.saved == {}


def test_published_pre_deploy_week_cannot_be_edited():
    ep = _episode(_pre_deploy_wednesday(), published_at="2026-10-04T00:01:00+00:00",
                  hero_image_url=f"{BLOB}/pinned.png")
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    try:
        page = client.get("/admin/episodes/2026-W41")
        resp = _post(client, {"action": "select", "image_set_id": _legacy_set_id(ep),
                              "path": ep["stages"]["wednesday"]["image_paths"][1]})
    finally:
        patcher.stop()
    assert 'data-action="photo-select"' not in page.text
    assert 'data-action="photo-reject"' not in page.text
    assert "Published. The hero is pinned" in page.text
    assert resp.status_code == 409 and store.saved == {} and DECISIONS.records == {}

    result, env = _run_sunday(ep)
    assert result["already_published"] is True
    env.held.assert_not_called()
    assert ep["hero_image_url"] == f"{BLOB}/pinned.png"


def test_retired_rerun_endpoint_generates_and_writes_nothing(tmp_path):
    ep = _episode(_pre_deploy_wednesday())
    store = _FakeStore({"2026-W41": ep})
    client, patcher = _client(store)
    client.app.state.project_root = tmp_path
    (tmp_path / "data" / "episodes").mkdir(parents=True)
    local = tmp_path / "data" / "episodes" / "2026-W41.json"
    local.write_text(json.dumps(ep))
    try:
        with patch("backend.orchestrator.RecipeOrchestrator") as orchestrator:
            resp = client.post("/admin/episodes/2026-W41/images/rerun", headers=_SAME_ORIGIN)
    finally:
        patcher.stop()
    assert resp.status_code == 410
    assert "Wednesday re-fire" in resp.json()["detail"]
    orchestrator.assert_not_called()
    assert json.loads(local.read_text()) == ep
    assert store.saved == {}
