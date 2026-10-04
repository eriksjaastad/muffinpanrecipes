"""Codex cycle-3 regressions for the photo approval gate (#7936): shared invariants.

1. ONE legacy-aware publication predicate (``published_at`` OR a complete
   Sunday, as the static builder decides it) behind every guard that protects
   a published week from simulation, editing, release or re-decision.
2. The local Delete admin action is retired (410, no button, no JS).
3. Frozen control versions are checked as a whole at the single read
   boundary and before every frozen write: a real approved candidate of the
   current set, a valid claim, and a checkpoint that is this claim's own
   publication. Inconsistent records fail closed everywhere.
4. A recorded photo hold is quiet only while the LAST Sunday attempt
   confirmed it; an attempt that cannot read or claim the authority is
   recorded on the hold and reported by the monitor, and an alert that could
   not record it says so.

Offline only: no provider, storage, cron, webhook or notification call.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.admin import cron_routes
from backend.publishing import site_builder
from backend.storage import PhotoControlUnavailable
from backend.utils import photo_review
from backend.utils.episode_integrity import (
    episode_integrity_failures,
    episode_is_published,
    episode_summary,
    failed_hold_attempt,
    recorded_photo_hold,
    stage_deadline,
)
from tests.photo_review_helpers import decide, register, wednesday_stage
from tests.test_photo_approval_7936 import (
    EP_ID,
    _Store,
    _approved_store,
    _client,
    _control,
    _episode,
    _never_published,
    _post,
    _status,
    _sunday,
)
from tests.test_photo_approval_cycle2_7936 import _LocalStore, _monitor, _page, _section

AFTER_SUNDAY = stage_deadline(EP_ID, "sunday") + timedelta(hours=2)
PUBLISHED_AT = "2026-10-11T00:05:00+00:00"


def _published_body(store, claimed):
    return cron_routes._published_copy(store.get(EP_ID), claimed, [], "PASS", "Spinach Feta Egg Cups")


def _write_version(store, view, **fields):
    body = {
        "schema": 1, "episode_id": EP_ID, "version": view.version + 1, "write_id": "cafecafecafecafe",
        "state": view.state, "request": view.request, "decision": view.decision, "claim": view.claim,
        "event": "synthetic", "at": PUBLISHED_AT,
    }
    body.update(fields)
    store.create_photo_control_version(EP_ID, view.version + 1, body)
    return body


# =============================================================================
# 1. One legacy-aware publication predicate
# =============================================================================

@pytest.mark.parametrize("episode,expected", [
    ({"published_at": PUBLISHED_AT, "stages": {}}, True),
    ({"stages": {"sunday": {"status": "complete"}}}, True),                       # legacy, no published_at
    ({"stages": {"sunday": {"status": "complete", "published": True}}}, True),
    ({"stages": {"sunday": {"status": "simulated", "simulation": True}}}, False),
    ({"stages": {"sunday": {"status": "failed"}}}, False),
    ({"stages": {"sunday": {"status": "in_progress"}}}, False),
    ({"stages": {}}, False),
    ({}, False),
    (None, False),
    ("not an episode", False),
])
def test_episode_is_published_matches_the_site_builder(episode, expected):
    assert episode_is_published(episode) is expected
    if isinstance(episode, dict):
        assert site_builder._episode_is_published(episode) is expected


def _legacy_published_episode():
    """A real pre-published_at week: complete Sunday, no published_at, no photo control."""
    wed = wednesday_stage()
    wed.pop("photo_review")
    ep = _episode(wed)
    ep["stages"]["sunday"] = {"status": "complete", "published": True, "dialogue": [{"character": "Devon Park",
                                                                                    "message": "Live."}]}
    ep["hero_image_url"] = wed["image_urls"][0]
    return ep


def test_legacy_published_week_is_never_simulated_or_demoted(tmp_path):
    store = _LocalStore()
    ep = _legacy_published_episode()
    store.put(EP_ID, ep)
    # The endpoint reads the LOCAL episode file for its own guard; the
    # dispatcher reads the store. Both must refuse a legacy published week.
    episodes = tmp_path / "data" / "episodes"
    episodes.mkdir(parents=True)
    (episodes / f"{EP_ID}.json").write_text(json.dumps(ep))
    before = copy.deepcopy(store.episodes)
    generate = MagicMock(return_value=[{"character": "Devon Park", "message": "Simulated."}])
    with patch.object(cron_routes, "storage", store), patch.object(cron_routes, "_generate_dialogue", generate):
        with pytest.raises(RuntimeError, match="published"):
            asyncio.run(cron_routes.execute_cron_stage_stub("sunday", EP_ID, "Spinach Feta Egg Cups"))
    generate.assert_not_called()
    assert store.episodes == before and store.saves == []
    assert site_builder._episode_is_published(store.get(EP_ID)) is True
    # The endpoint and the page agree (local filesystem data, production view).
    client, patcher = _client(store)
    client.app.state.project_root = tmp_path
    stub = AsyncMock(return_value={"mode": "simulation"})
    try:
        page = _page(client).text
        with patch.object(cron_routes, "storage", store), patch.object(cron_routes, "execute_cron_stage_stub", stub):
            resp = client.post(f"/admin/episodes/{EP_ID}/run")
    finally:
        patcher.stop()
    assert 'id="run-btn"' not in page
    assert resp.status_code == 409 and "published" in resp.json()["detail"]
    stub.assert_not_called()


def test_legacy_published_week_is_read_only_for_review_and_sunday():
    store = _Store()
    ep = _legacy_published_episode()
    store.put(EP_ID, ep)
    client, patcher = _client(store)
    try:
        page = _page(client).text
        wed_request = photo_review.read_view(store, EP_ID, ep).request
        resp = _post(client, {"action": "select", "image_set_id": wed_request["image_set_id"],
                              "path": wed_request["candidates"][1]["path"]})
    finally:
        patcher.stop()
    section = _section(page)
    assert "Published. The hero is pinned" in section
    assert 'data-action="photo-select"' not in section and 'data-action="photo-reject"' not in section
    assert resp.status_code == 409 and "already published" in resp.json()["detail"]
    assert store.read_photo_control(EP_ID) is None and store.saves == []
    # Sunday takes the already-published path: no claim, no hold, no paid call, no rewrite.
    env = _sunday(store)
    assert env.result["already_published"] is True
    env.dialogue.assert_not_called()
    assert env.held.call_count == 0
    assert store.read_photo_control(EP_ID) is None
    assert store.get(EP_ID)["stages"]["sunday"]["status"] == "complete"
    assert "publish_hold" not in store.get(EP_ID)


def test_wednesday_refuses_to_reshoot_a_legacy_published_week(tmp_path):
    from tests.test_photo_approval_7936 import _wednesday

    store = _Store()
    store.put(EP_ID, _legacy_published_episode())
    before = copy.deepcopy(store.get(EP_ID))
    run = _wednesday(store, tmp_path)
    assert getattr(run.error, "status_code", None) == 409
    run.orch.return_value._execute_stage_photography.assert_not_called()
    assert store.get(EP_ID) == before and store.read_photo_control(EP_ID) is None


def test_operator_release_refuses_a_legacy_published_week():
    store, ep, wed = _approved_store()
    photo_review.claim_for_publication(store, EP_ID, ep)
    legacy = store.get(EP_ID)
    legacy["stages"]["sunday"] = {"status": "complete"}
    store.put(EP_ID, legacy)
    with pytest.raises(photo_review.PhotoReviewError, match="published"):
        photo_review.operator_release(store, EP_ID, store.get(EP_ID))
    assert _control(store)["state"] == "claimed"


def test_recorded_hold_is_not_recognised_on_a_legacy_published_week():
    ep = _legacy_published_episode()
    ep["publish_hold"] = {"reason": "awaiting_photo_approval", "image_set_id": "0123456789abcdef",
                          "since": PUBLISHED_AT, "notified": True}
    assert recorded_photo_hold(ep) is None
    # The summary uses the shared predicate too: a legacy week reads "published", never "awaiting".
    assert "awaiting" not in episode_summary(ep) and episode_summary(ep).endswith("published")


# =============================================================================
# 2. Delete is retired
# =============================================================================

@pytest.mark.parametrize("state", ["awaiting", "approved", "claimed", "publishing", "published_episode"])
def test_delete_is_retired_in_every_control_state(state):
    store, ep, wed = _approved_store()
    if state == "awaiting":
        store, wed = _Store(), wednesday_stage()
        store.put(EP_ID, _episode(wed))
        register(EP_ID, wed, store)
    if state in ("claimed", "publishing"):
        claimed = photo_review.claim_for_publication(store, EP_ID, ep)
        if state == "publishing":
            photo_review.mark_publishing(store, claimed, _published_body(store, claimed))
    if state == "published_episode":
        assert _sunday(store).result["published"] is True
    client, patcher = _client(store)
    try:
        page = _page(client).text
        with patch("send2trash.send2trash") as trash:
            resp = client.delete(f"/admin/episodes/{EP_ID}")
    finally:
        patcher.stop()
    assert resp.status_code == 410 and "Retired" in resp.json()["detail"]
    trash.assert_not_called()
    assert "delete-btn" not in page and "delete-episode" not in page and "deleteEpisode" not in page
    assert "DELETE" not in page.split("<script>", 1)[1]
    # The control is exactly as it was: the retired endpoint touches nothing.
    assert store.saves == [] or state == "published_episode"


# =============================================================================
# 3. Frozen invariants at one boundary
# =============================================================================

def _frozen_cases():
    """(name, builder) pairs; each builder writes ONE inconsistent frozen version and returns it."""
    def codex_repro(store, ep, claimed):
        # publishing, rejected decision, no claim, checkpoint with published_at
        body = _published_body(store, claimed)
        decision = {**claimed.decision, "status": "rejected", "selected": None}
        return _write_version(store, claimed, state="publishing", decision=decision, claim=None, publication=body)

    def wrong_hero(store, ep, claimed):
        body = _published_body(store, claimed)
        body["hero_image_url"] = claimed.request["candidates"][2]["url"]
        return _write_version(store, claimed, state="publishing", publication=body)

    def foreign_claim(store, ep, claimed):
        body = _published_body(store, claimed)
        body["photo_approval"]["claim_id"] = "feedfeedfeedfeed"
        return _write_version(store, claimed, state="publishing", publication=body)

    def other_candidate(store, ep, claimed):
        body = _published_body(store, claimed)
        other = claimed.request["candidates"][2]
        body["photo_approval"].update(path=other["path"], url=other["url"])
        return _write_version(store, claimed, state="publishing", publication=body)

    def other_set(store, ep, claimed):
        body = _published_body(store, claimed)
        body["photo_approval"]["image_set_id"] = "0123456789abcdef"
        return _write_version(store, claimed, state="publishing", publication=body)

    def other_episode(store, ep, claimed):
        body = _published_body(store, claimed)
        body["episode_id"] = "2026-W40"
        return _write_version(store, claimed, state="publishing", publication=body)

    def no_photo_approval(store, ep, claimed):
        body = _published_body(store, claimed)
        body.pop("photo_approval")
        return _write_version(store, claimed, state="publishing", publication=body)

    def no_published_at(store, ep, claimed):
        body = _published_body(store, claimed)
        body.pop("published_at")
        return _write_version(store, claimed, state="publishing", publication=body)

    def no_checkpoint(store, ep, claimed):
        return _write_version(store, claimed, state="publishing", publication=None)

    def published_at_disagrees(store, ep, claimed):
        body = _published_body(store, claimed)
        return _write_version(store, claimed, state="published", publication=body,
                              published_at="2027-01-01T00:00:00+00:00")

    def claim_without_version(store, ep, claimed):
        return _write_version(store, claimed, claim={"claim_id": claimed.claim["claim_id"],
                                                     "claimed_at": PUBLISHED_AT, "from_version": True})

    def claim_without_id(store, ep, claimed):
        return _write_version(store, claimed, claim={"claimed_at": PUBLISHED_AT, "from_version": 2})

    def claimed_rejected(store, ep, claimed):
        return _write_version(store, claimed, decision={**claimed.decision, "status": "rejected"})

    def claimed_selection_not_in_set(store, ep, claimed):
        sel = {**claimed.decision["selected"], "path": "src/assets/images/r1/round_9/ghost.png"}
        return _write_version(store, claimed, decision={**claimed.decision, "selected": sel})

    def published_decision_other_set(store, ep, claimed):
        body = _published_body(store, claimed)
        return _write_version(store, claimed, state="published", publication=body,
                              published_at=body["published_at"],
                              decision={**claimed.decision, "image_set_id": "0123456789abcdef"})

    return {f.__name__: f for f in (
        codex_repro, wrong_hero, foreign_claim, other_candidate, other_set, other_episode,
        no_photo_approval, no_published_at, no_checkpoint, published_at_disagrees,
        claim_without_version, claim_without_id, claimed_rejected, claimed_selection_not_in_set,
        published_decision_other_set,
    )}


@pytest.mark.parametrize("case", sorted(_frozen_cases()))
def test_inconsistent_frozen_record_fails_closed_everywhere(case):
    """Codex repro: a publishing version with a rejected decision and no claim completed as published."""
    store, ep, wed = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    body = _frozen_cases()[case](store, ep, claimed)
    version = body["version"]
    with pytest.raises(PhotoControlUnavailable, match="inconsistent"):
        photo_review.read_view(store, EP_ID, store.get(EP_ID))
    # Recovery: nothing restored, nothing written, nothing paid, one alert.
    env = _sunday(store)
    assert _status(env) == 503, (case, env.result, env.error)
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    assert env.failure.call_count == 1
    _never_published(store)
    assert store.saves == []
    assert store.read_photo_control(EP_ID)["version"] == version
    # Admin: unavailable, not editable, not legacy.
    client, patcher = _client(store)
    try:
        page = _page(client)
        post = _post(client, {"action": "reject", "image_set_id": wed["photo_review"]["image_set_id"]})
    finally:
        patcher.stop()
    assert page.status_code == 503 and post.status_code == 503
    assert store.read_photo_control(EP_ID)["version"] == version
    # Operator paths refuse too; nothing is released or reconciled.
    with pytest.raises(PhotoControlUnavailable):
        photo_review.operator_release(store, EP_ID, store.get(EP_ID))
    with pytest.raises(PhotoControlUnavailable):
        photo_review.reconcile_published(store, EP_ID, {**store.get(EP_ID), "published_at": PUBLISHED_AT})
    assert store.read_photo_control(EP_ID)["version"] == version
    # Wednesday will not replace the set of a record it cannot trust either.
    with pytest.raises(PhotoControlUnavailable):
        photo_review.ensure_not_frozen(store, EP_ID)


@pytest.mark.parametrize("tamper", ["hero", "claim_id", "image_set_id", "path", "episode_id", "no_published_at",
                                    "no_photo_approval", "not_a_dict"])
def test_mark_publishing_refuses_a_checkpoint_that_is_not_this_claims_publication(tamper):
    store, ep, wed = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    body = _published_body(store, claimed)
    if tamper == "hero":
        body["hero_image_url"] = claimed.request["candidates"][1]["url"]
    elif tamper == "claim_id":
        body["photo_approval"]["claim_id"] = "feedfeedfeedfeed"
    elif tamper == "image_set_id":
        body["photo_approval"]["image_set_id"] = "0123456789abcdef"
    elif tamper == "path":
        body["photo_approval"]["path"] = claimed.request["candidates"][1]["path"]
    elif tamper == "episode_id":
        body["episode_id"] = "2026-W40"
    elif tamper == "no_published_at":
        body.pop("published_at")
    elif tamper == "no_photo_approval":
        body.pop("photo_approval")
    else:
        body = "not an episode"
    with pytest.raises(photo_review.PhotoReviewError, match="refusing to publish"):
        photo_review.mark_publishing(store, claimed, body)
    assert _control(store)["state"] == "claimed" and _control(store)["version"] == claimed.version


def test_mark_published_only_from_publishing_with_its_own_published_at():
    store, ep, wed = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    with pytest.raises(photo_review.PhotoReviewError):
        photo_review.mark_published(store, claimed, PUBLISHED_AT)
    publishing = photo_review.mark_publishing(store, claimed, _published_body(store, claimed))
    with pytest.raises(photo_review.PhotoReviewError, match="does not match"):
        photo_review.mark_published(store, publishing, "2027-01-01T00:00:00+00:00")
    assert _control(store)["state"] == "publishing"
    done = photo_review.mark_published(store, publishing, publishing.publication["published_at"])
    assert done.state == "published" and photo_review.frozen_inconsistency(done) is None
    assert photo_review.read_view(store, EP_ID, None).publication == publishing.publication


def test_reconcile_requires_the_episode_to_be_this_claims_publication():
    store, ep, wed = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    good = _published_body(store, claimed)
    foreign = copy.deepcopy(good)
    foreign["photo_approval"]["claim_id"] = "feedfeedfeedfeed"
    wrong_hero = copy.deepcopy(good)
    wrong_hero["hero_image_url"] = claimed.request["candidates"][2]["url"]
    only_claim_id = {**store.get(EP_ID), "published_at": PUBLISHED_AT,
                     "photo_approval": {"claim_id": claimed.claim["claim_id"]}}
    for proof in (foreign, wrong_hero, only_claim_id):
        assert photo_review.reconcile_published(store, EP_ID, proof) is None
        assert _control(store)["state"] == "claimed"
    # The genuine article records published WITH the episode as its checkpoint,
    # and the result passes the boundary check.
    done = photo_review.reconcile_published(store, EP_ID, good)
    assert done.state == "published" and done.publication == good
    view = photo_review.read_view(store, EP_ID, good)
    assert view.state == "published" and view.publication["published_at"] == good["published_at"]


def test_reconcile_from_publishing_keeps_the_original_checkpoint():
    store, ep, wed = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    body = _published_body(store, claimed)
    photo_review.mark_publishing(store, claimed, body)
    proof = copy.deepcopy(body)
    proof["events"] = proof["events"] + ["later: unrelated save"]
    done = photo_review.reconcile_published(store, EP_ID, proof)
    assert done.state == "published" and done.publication == body


def test_legitimate_publication_and_crash_replay_pass_the_boundary():
    """The real Sunday path writes consistent frozen versions; a failed publishing save replays them."""
    store, ep, wed = _approved_store(pick=2)
    attempted = []

    def fail_publishing(data):
        if data.get("published_at"):
            attempted.append(copy.deepcopy(data))
            return True
        return False

    store.fail_save = fail_publishing
    assert _status(_sunday(store)) == 500
    publishing = photo_review.read_view(store, EP_ID, store.get(EP_ID))
    assert publishing.state == "publishing" and photo_review.frozen_inconsistency(publishing) is None
    assert photo_review.checkpoint_mismatch(publishing, publishing.publication) is None
    store.fail_save = None
    env = _sunday(store)
    assert env.result["completed_from_checkpoint"] is True
    env.dialogue.assert_not_called()
    published = photo_review.read_view(store, EP_ID, store.get(EP_ID))
    assert published.state == "published" and photo_review.frozen_inconsistency(published) is None
    assert store.get(EP_ID)["hero_image_url"] == wed["image_urls"][1]


def test_frozen_inconsistency_is_none_for_decidable_states():
    store, ep, wed = _approved_store()
    view = photo_review.read_view(store, EP_ID, ep)
    assert view.state == "approved" and photo_review.frozen_inconsistency(view) is None
    decide(EP_ID, ep, action="reject", store=store)
    view = photo_review.read_view(store, EP_ID, ep)
    assert view.state == "rejected" and photo_review.frozen_inconsistency(view) is None


# =============================================================================
# 4. A hold is quiet only while the last attempt confirmed it
# =============================================================================

def _held_store():
    wed = wednesday_stage()
    ep = _episode(wed)
    store = _Store()
    store.put(EP_ID, ep)
    register(EP_ID, wed, store)
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    saved = store.get(EP_ID)
    assert saved["publish_hold"]["last_attempt"]["outcome"] == "held"
    assert recorded_photo_hold(saved) == "awaiting_photo_approval"
    assert env.held.call_count == 1
    return store, wed


def _break_authority(store, how):
    if how == "read_error":
        store.fail_control_read = PhotoControlUnavailable("blob list failed")
    elif how == "malformed_control":
        view = photo_review.read_view(store, EP_ID, None)
        _write_version(store, view, request={**view.request, "candidates": [{}]})
    elif how == "inconsistent_frozen":
        view = photo_review.read_view(store, EP_ID, None)
        _write_version(store, view, state="claimed", claim={"claim_id": "x"})
    else:
        raise AssertionError(how)


@pytest.mark.parametrize("how", ["read_error", "malformed_control", "inconsistent_frozen"])
def test_authority_failure_after_a_hold_is_reported_not_hidden(how):
    store, wed = _held_store()
    _break_authority(store, how)
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    _never_published(store)
    assert env.failure.call_count == 1
    assert "was recorded on the episode" in env.failure.call_args.kwargs["error_message"]
    assert env.held.call_count == 0
    saved = store.get(EP_ID)
    attempt = saved["publish_hold"]["last_attempt"]
    assert attempt["outcome"] == "failed" and "photo control" in attempt["detail"]
    assert failed_hold_attempt(saved)
    assert recorded_photo_hold(saved) is None
    failures, summary = _monitor(saved)
    assert any(f.startswith("sunday attempt after the photo hold failed") for f in failures), failures
    assert len([f for f in failures if f.startswith("sunday")]) == 1, "one Sunday line, not a duplicate"
    assert "awaiting photo approval" not in summary
    # Before the Sunday deadline the failure is still reported: it happened.
    early = episode_integrity_failures(saved, now=stage_deadline(EP_ID, "sunday") - timedelta(days=1))
    assert any(f.startswith("sunday attempt after the photo hold failed") for f in early)
    # The hold itself is untouched apart from the attempt record.
    assert saved["publish_hold"]["reason"] == "awaiting_photo_approval" and saved["publish_hold"]["notified"]


def test_a_confirmed_hold_after_a_failed_attempt_is_quiet_again_without_a_second_alert():
    store, wed = _held_store()
    store.fail_control_read = PhotoControlUnavailable("blob list failed")
    assert _status(_sunday(store)) == 503
    assert recorded_photo_hold(store.get(EP_ID)) is None
    store.fail_control_read = None
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "awaiting_photo_approval"
    assert env.held.call_count == 0, "the hold was already notified; only the attempt record changed"
    saved = store.get(EP_ID)
    assert saved["publish_hold"]["last_attempt"]["outcome"] == "held"
    assert recorded_photo_hold(saved) == "awaiting_photo_approval"
    failures, summary = _monitor(saved)
    assert not [f for f in failures if f.startswith("sunday")]
    assert summary.endswith("not published: awaiting photo approval")


def test_failed_attempt_that_cannot_be_persisted_is_declared_in_the_alert():
    store, wed = _held_store()
    store.fail_control_read = PhotoControlUnavailable("blob list failed")
    before = copy.deepcopy(store.get(EP_ID))
    store.fail_save = lambda data: True
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    message = env.failure.call_args.kwargs["error_message"]
    assert "could NOT be recorded" in message and "monitor will keep showing the earlier photo hold" in message
    assert "was recorded" not in message.replace("could NOT be recorded", "")
    # Storage is exactly as before: the monitor still shows the hold (the alert is the record).
    assert store.get(EP_ID) == before
    assert recorded_photo_hold(store.get(EP_ID)) == "awaiting_photo_approval"


def test_hold_clear_failure_after_approval_records_the_attempt_or_says_it_could_not():
    store, wed = _held_store()
    decide(EP_ID, store.get(EP_ID), store=store)
    store.fail_save = lambda data: "sunday: hold cleared (approved photo claimed)" in data.get("events", [])
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    message = env.failure.call_args.kwargs["error_message"]
    assert "could not clear the previous photo hold" in message
    # Only the clear itself was refused (the predicate keys on its event); the failed attempt
    # is then recorded on the restored hold, and the alert says so truthfully.
    assert "was recorded on the episode" in message
    assert _control(store)["state"] == "approved" and _control(store)["claim"] is None
    saved = store.get(EP_ID)
    assert saved["publish_hold"]["last_attempt"]["outcome"] == "failed"
    assert "sunday: hold cleared (approved photo claimed)" not in saved["events"]
    assert recorded_photo_hold(saved) is None, "the monitor reports the failed attempt, not a quiet hold"
    store.fail_save = None
    assert _sunday(store).result["published"] is True


def test_genuine_hold_and_rejection_stay_quiet_and_unrelated_failures_stay_visible():
    store, wed = _held_store()
    saved = store.get(EP_ID)
    saved["stages"]["friday"] = {"status": "failed", "error": "judge down"}
    store.put(EP_ID, saved)
    failures, summary = _monitor(store.get(EP_ID))
    assert any(f.startswith("friday stage is 'failed'") for f in failures)
    assert not [f for f in failures if f.startswith("sunday")]
    assert summary.endswith("not published: awaiting photo approval")
    decide(EP_ID, store.get(EP_ID), action="reject", store=store)
    env = _sunday(store)
    assert json.loads(env.result.body)["status"] == "photos_rejected"
    saved = store.get(EP_ID)
    assert saved["publish_hold"]["last_attempt"]["outcome"] == "held"
    failures, summary = _monitor(saved)
    assert not [f for f in failures if f.startswith("sunday")]
    assert summary.endswith("not published: photos rejected, awaiting new photos")


def test_post_claim_qa_failure_after_a_hold_is_a_failed_sunday_not_a_hold():
    store, wed = _held_store()
    decide(EP_ID, store.get(EP_ID), store=store)
    with patch.object(cron_routes, "_auto_fix_recipe", return_value=False):
        env = _sunday(store, qa=(False, "STATUS: FAIL"))
    assert _status(env) == 400
    saved = store.get(EP_ID)
    assert "publish_hold" not in saved and saved["stages"]["sunday"]["status"] == "failed"
    failures, summary = _monitor(saved)
    assert any(f.startswith("sunday stage is 'failed'") for f in failures)
    assert "awaiting" not in summary
    assert env.held.call_count == 0


def test_hold_record_shape_without_last_attempt_is_still_a_hold():
    """Holds written before last_attempt existed keep working (no false failure)."""
    store, wed = _held_store()
    saved = store.get(EP_ID)
    saved["publish_hold"].pop("last_attempt")
    store.put(EP_ID, saved)
    assert recorded_photo_hold(store.get(EP_ID)) == "awaiting_photo_approval"
    assert failed_hold_attempt(store.get(EP_ID)) is None


# =============================================================================
# Manager follow-up: before-write validation, claim/version ties, failure priority
# =============================================================================

def _creates(store):
    """Spy on the only write primitive; returns the list of (version, state) it was asked to create."""
    calls = []
    real = store.create_photo_control_version

    def spy(episode_id, version, body):
        calls.append((version, body.get("state")))
        return real(episode_id, version, body)

    store.create_photo_control_version = spy
    return calls


@pytest.mark.parametrize("transition", [
    "claimed_without_decision", "claimed_wrong_version_claim", "publishing_without_checkpoint",
    "publishing_wrong_hero", "published_without_claim", "awaiting_bad_request",
])
def test_append_validates_the_proposed_version_before_the_log_is_written(transition):
    """The write boundary: an inconsistent body never reaches the immutable log, whatever the transition."""
    store, ep, wed = _approved_store()
    view = photo_review.read_view(store, EP_ID, ep)
    calls = _creates(store)
    kwargs = dict(request=view.request, decision=view.decision, claim=None, event="t")
    good_claim = {"claim_id": "abcdabcdabcdabcd", "claimed_at": PUBLISHED_AT, "from_version": view.version}
    if transition == "claimed_without_decision":
        kwargs.update(state="claimed", decision=None, claim=good_claim)
    elif transition == "claimed_wrong_version_claim":
        kwargs.update(state="claimed", claim={**good_claim, "from_version": view.version + 3})
    elif transition == "publishing_without_checkpoint":
        kwargs.update(state="publishing", claim=good_claim)
    elif transition == "publishing_wrong_hero":
        claimed = photo_review.claim_for_publication(store, EP_ID, ep)
        calls.clear()
        view = claimed
        body = _published_body(store, claimed)
        body["hero_image_url"] = claimed.request["candidates"][1]["url"]
        kwargs = dict(request=view.request, decision=view.decision, claim=view.claim, event="t",
                      state="publishing", publication=body)
    elif transition == "published_without_claim":
        kwargs.update(state="published", publication={"published_at": PUBLISHED_AT}, published_at=PUBLISHED_AT)
    else:
        kwargs.update(state="awaiting", request={**view.request, "candidates": [{}]})
    before = store.read_photo_control(EP_ID)["version"]
    with pytest.raises(photo_review.PhotoReviewError, match="refusing to write"):
        photo_review._append(store, view, **kwargs)
    assert calls == [], "create_photo_control_version was never asked to persist the bad body"
    assert store.read_photo_control(EP_ID)["version"] == before


def test_legitimate_transitions_still_write_exactly_once_each():
    store, ep, wed = _approved_store()
    calls = _creates(store)
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    body = _published_body(store, claimed)
    publishing = photo_review.mark_publishing(store, claimed, body)
    published = photo_review.mark_published(store, publishing, body["published_at"])
    assert calls == [(claimed.version, "claimed"), (publishing.version, "publishing"), (published.version, "published")]
    assert claimed.version == claimed.claim["from_version"] + 1
    assert body["photo_approval"]["control_version"] == claimed.version
    assert body["stages"]["sunday"]["status"] == "complete" and body["stages"]["sunday"]["published"] is True
    for v in (claimed, publishing, published):
        assert photo_review.frozen_inconsistency(v) is None


def test_release_and_reconcile_from_a_claim_write_exactly_once_and_stay_consistent():
    store, ep, _ = _approved_store()
    calls = _creates(store)
    c2 = photo_review.claim_for_publication(store, EP_ID, ep)
    released = photo_review.release_claim(store, c2, "test")
    assert released.state == "approved" and released.claim is None
    c3 = photo_review.claim_for_publication(store, EP_ID, ep)
    assert c3.version == c3.claim["from_version"] + 1 == released.version + 1
    done = photo_review.reconcile_published(store, EP_ID, _published_body(store, c3))
    assert done.state == "published" and photo_review.frozen_inconsistency(done) is None
    assert [state for _, state in calls] == ["claimed", "approved", "claimed", "published"]


def _version_tie_cases():
    def claim_from_future(store, claimed):
        return _write_version(store, claimed, claim={**claimed.claim, "from_version": claimed.version + 5})

    def claim_not_previous(store, claimed):
        # claimed at version N must come from N-1; a claim "from" N-3 is not this transition's
        return _write_version(store, claimed, claim={**claimed.claim, "from_version": claimed.version - 3})

    def claim_from_version_bool(store, claimed):
        return _write_version(store, claimed, claim={**claimed.claim, "from_version": False})

    def claim_from_version_str(store, claimed):
        return _write_version(store, claimed, claim={**claimed.claim, "from_version": str(claimed.version - 1)})

    def publishing_claim_not_earlier(store, claimed):
        body = _published_body(store, claimed)
        return _write_version(store, claimed, state="publishing", publication=body,
                              claim={**claimed.claim, "from_version": claimed.version})

    def checkpoint_other_control_version(store, claimed):
        body = _published_body(store, claimed)
        body["photo_approval"]["control_version"] = claimed.version + 1
        return _write_version(store, claimed, state="publishing", publication=body)

    def checkpoint_control_version_zero(store, claimed):
        body = _published_body(store, claimed)
        body["photo_approval"]["control_version"] = 0
        return _write_version(store, claimed, state="publishing", publication=body)

    def checkpoint_control_version_missing(store, claimed):
        body = _published_body(store, claimed)
        body["photo_approval"].pop("control_version")
        return _write_version(store, claimed, state="publishing", publication=body)

    def checkpoint_control_version_bool(store, claimed):
        body = _published_body(store, claimed)
        body["photo_approval"]["control_version"] = True  # == 1 in Python; must not pass as version 1
        return _write_version(store, claimed, state="publishing", publication=body)

    def checkpoint_sunday_missing(store, claimed):
        body = _published_body(store, claimed)
        body["stages"].pop("sunday")
        return _write_version(store, claimed, state="publishing", publication=body)

    def checkpoint_sunday_failed(store, claimed):
        body = _published_body(store, claimed)
        body["stages"]["sunday"]["status"] = "failed"
        return _write_version(store, claimed, state="publishing", publication=body)

    def checkpoint_sunday_not_published(store, claimed):
        body = _published_body(store, claimed)
        body["stages"]["sunday"]["published"] = False
        return _write_version(store, claimed, state="publishing", publication=body)

    return {f.__name__: f for f in (
        claim_from_future, claim_not_previous, claim_from_version_bool, claim_from_version_str,
        publishing_claim_not_earlier, checkpoint_other_control_version, checkpoint_control_version_zero,
        checkpoint_control_version_missing, checkpoint_control_version_bool, checkpoint_sunday_missing,
        checkpoint_sunday_failed, checkpoint_sunday_not_published,
    )}


@pytest.mark.parametrize("case", sorted(_version_tie_cases()))
def test_claim_and_checkpoint_versions_must_tie_to_the_control(case):
    store, ep, wed = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    if case == "checkpoint_control_version_bool" and claimed.version != 1:
        # make the bool trap real: True == 1 only matters when the claimed version is 1
        pass
    body = _version_tie_cases()[case](store, claimed)
    with pytest.raises(PhotoControlUnavailable, match="inconsistent"):
        photo_review.read_view(store, EP_ID, store.get(EP_ID))
    env = _sunday(store)
    assert _status(env) == 503
    env.dialogue.assert_not_called()
    _never_published(store)
    assert store.read_photo_control(EP_ID)["version"] == body["version"]


def test_checkpoint_bool_control_version_cannot_pass_as_version_one():
    """True == 1 in Python; a claimed version 1 checkpoint stamped True is still refused."""
    wed = wednesday_stage()
    ep = _episode(wed)
    store = _Store()
    store.put(EP_ID, ep)
    # version 1 is the derived-bootstrap decision; the claim is then version 2, so build a
    # control whose CLAIMED version is 1 by hand: from_version 0 (derived view).
    view = photo_review._derived_view(EP_ID, ep)
    decision = {"status": "approved", "image_set_id": view.image_set_id, "selected": view.request["candidates"][0],
                "decided_at": PUBLISHED_AT, "decided_by": "site editor", "decided_by_id": None}
    claim = {"claim_id": "abcdabcdabcdabcd", "claimed_at": PUBLISHED_AT, "from_version": 0}
    claimed = photo_review.ControlView(EP_ID, "claimed", view.request, decision, claim, 1, None)
    body = _published_body(store, claimed)
    assert body["photo_approval"]["control_version"] == 1
    assert photo_review.checkpoint_mismatch(claimed, body) is None
    body["photo_approval"]["control_version"] = True
    assert photo_review.checkpoint_mismatch(claimed, body) is not None


def test_mark_publishing_refuses_a_checkpoint_naming_another_control_version():
    store, ep, wed = _approved_store()
    claimed = photo_review.claim_for_publication(store, EP_ID, ep)
    body = _published_body(store, claimed)
    body["photo_approval"]["control_version"] = claimed.version - 1
    with pytest.raises(photo_review.PhotoReviewError, match="control_version"):
        photo_review.mark_publishing(store, claimed, body)
    assert _control(store)["state"] == "claimed"


def test_recorded_sunday_stage_outranks_an_older_failed_hold_attempt():
    """A later real Sunday stage is the most recent truth; the attempt record does not duplicate or mask it."""
    store, wed = _held_store()
    store.fail_control_read = PhotoControlUnavailable("down")
    assert _status(_sunday(store)) == 503
    saved = store.get(EP_ID)
    assert failed_hold_attempt(saved)
    # A stale save (or a later run) leaves a real failed Sunday stage beside the old hold.
    saved["stages"]["sunday"] = {"status": "failed", "error": "editorial QA exhausted retries"}
    store.put(EP_ID, saved)
    assert failed_hold_attempt(store.get(EP_ID)) is None
    assert recorded_photo_hold(store.get(EP_ID)) is None
    failures, summary = _monitor(store.get(EP_ID))
    sunday_lines = [f for f in failures if f.startswith("sunday")]
    assert len(sunday_lines) == 1 and "editorial QA exhausted retries" in sunday_lines[0]
    assert "attempt after the photo hold" not in sunday_lines[0]
    assert "awaiting" not in summary


def test_failed_attempt_is_invisible_on_a_published_week_and_cleared_by_a_successful_claim():
    store, wed = _held_store()
    store.fail_control_read = PhotoControlUnavailable("down")
    assert _status(_sunday(store)) == 503
    store.fail_control_read = None
    # Legacy fast path: a published week never reports the stale attempt.
    legacy = copy.deepcopy(store.get(EP_ID))
    legacy["stages"]["sunday"] = {"status": "complete"}
    assert failed_hold_attempt(legacy) is None
    assert not [f for f in episode_integrity_failures(legacy, now=AFTER_SUNDAY) if f.startswith("sunday")]
    # A successful claim clears the hold (and its attempt) entirely before any paid step.
    decide(EP_ID, store.get(EP_ID), store=store)
    env = _sunday(store)
    assert env.result["published"] is True
    assert "publish_hold" not in store.get(EP_ID)
    assert not [f for f in episode_integrity_failures(store.get(EP_ID), now=AFTER_SUNDAY) if f.startswith("sunday")]
