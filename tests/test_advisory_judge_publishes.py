"""A weak conversation must not withhold a finished recipe (#7394, #7352).

W38 produced a recipe, a hero image and six complete days, then published
nothing at all: Sunday's dialogue failed the judge three times and the raise
happened before the publish block. The judge now scores Sunday without
gating it, and the request body can hand a stage a narrative problem so a
re-fire is a cron call rather than a hand-written script.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.admin import cron_routes
from backend.utils.indexnow import IndexNowResult
from tests.photo_review_helpers import approved_wednesday


def _record_indexnow(order: list[str]):
    """Stub for cron_routes._indexnow_submit_urls that records call order
    instead of making a real network call (#7806: no IndexNow submission in
    tests)."""
    def _fake(*_args, **_kwargs):
        order.append("indexnow")
        return IndexNowResult(ok=True, status_code=200, detail="submitted")
    return _fake


def _dialogue(tag: str) -> list[dict]:
    return [
        {"character": "Margaret Chen", "message": f"attempt {tag}"},
        {"character": "Marcus Reid", "message": f"the glaze in {tag} still worries me"},
    ]


def _episode() -> dict:
    return {"episode_id": "2026-W99", "stages": {}, "events": []}


def _run_advisory(episode: dict, scores_per_attempt: list[dict], **kwargs):
    """Drive the judge to exhaustion on the advisory path."""
    calls = {"n": 0}
    alerts: list[dict] = []

    def _gen(stage, concept, **kw):
        calls["n"] += 1
        return _dialogue(str(calls["n"]))

    def _judge(concept, stage, dialogue, episode_, **kw):
        scores = scores_per_attempt[calls["n"] - 1]
        episode_.setdefault("judge_scores", {})[stage] = scores
        episode_.setdefault("judge_weakest", {})[stage] = [f"weak {calls['n']}"]
        episode_.setdefault("judge_reason", {})[stage] = f"reason {calls['n']}"
        return False, f"FAIL - attempt {calls['n']}"

    with patch.object(cron_routes, "_generate_dialogue", _gen), \
         patch.object(cron_routes, "_judge_dialogue", _judge), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}), \
         patch.object(cron_routes, "notify_judge_advisory",
                      lambda **kw: alerts.append(kw) or True), \
         patch.object(cron_routes, "notify_judge_failure") as hard_alert:
        dialogue, verdict = cron_routes._generate_and_judge_dialogue(
            "sunday", "Cardamom Cinnamon Spiral Bites", episode,
            advisory=True, **kwargs,
        )
    hard_alert.assert_not_called()
    # Selecting a dialogue is not publishing one: the helper must stay quiet
    # until the handler confirms the page exists.
    assert alerts == []
    return dialogue, verdict, alerts


def test_advisory_exhaustion_publishes_instead_of_raising():
    episode = _episode()
    dialogue, verdict, _ = _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    # Attempt 2 scored highest, so attempt 2 is what readers get.
    assert dialogue[0]["message"] == "attempt 2"
    assert verdict == "FAIL - attempt 2"


def test_the_published_attempt_carries_its_own_scores():
    """The loop leaves the LAST attempt's scores on the episode.

    Publishing attempt 2 while the stage record reports attempt 3's numbers
    would make the tuning log describe a conversation nobody can read.
    """
    episode = _episode()
    _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    assert episode["judge_scores"]["sunday"] == {"x": 9}
    assert episode["judge_weakest"]["sunday"] == ["weak 2"]
    assert episode["judge_reason"]["sunday"] == "reason 2"


def test_advisory_selection_is_recorded_on_the_episode():
    episode = _episode()
    _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    record = episode["judge_advisory"]["sunday"]
    assert record["selected_below_bar"] is True
    assert record["published"] is False
    assert record["attempts"] == 3
    assert record["attempt_selected"] == 2
    assert record["scores"] == {"x": 9}
    assert record["recorded_at"]
    assert any("advisory gate" in e for e in episode["events"])


def test_a_selected_dialogue_that_never_publishes_never_claims_it_did():
    """Editorial QA can still reject the recipe after the judge gave up.

    If the helper announced publication, that run would persist
    published=True and email Erik that a page is live which does not exist.
    """
    episode = _episode()
    _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    record = episode["judge_advisory"]["sunday"]
    assert record["published"] is False
    assert "published_at" not in record

    with patch.object(cron_routes, "notify_judge_advisory") as alert:
        cron_routes._announce_advisory_publication(
            episode.get("episode_id", "2026-W99"), episode, "sunday", "Cardamom Cinnamon Spiral Bites",
        )
    alert.assert_not_called()


def test_the_alert_fires_once_the_page_exists():
    """Not gating is not the same as not telling Erik."""
    episode = _episode()
    _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])
    _mark_published(episode)

    with patch.object(cron_routes, "notify_judge_advisory") as alert, \
         patch.object(cron_routes.storage, "save_episode") as save_episode:
        cron_routes._announce_advisory_publication(
            episode.get("episode_id", "2026-W99"), episode, "sunday", "Cardamom Cinnamon Spiral Bites",
        )

    kwargs = alert.call_args.kwargs
    assert kwargs["stage"] == "sunday"
    assert kwargs["attempts"] == 3
    assert kwargs["scores"] == {"x": 9}
    assert kwargs["weakest"] == ["weak 2"]
    # announced_at (#7403) is what makes a retry of this same record a no-op.
    assert episode["judge_advisory"]["sunday"]["announced_at"]
    save_episode.assert_called_once()


# ---------------------------------------------------------------------------
# Exactly-once announce (#7403): a crash between the publish save and the
# alert used to lose the alert forever, because the already-published fast
# path in cron_sunday never retried it.
# ---------------------------------------------------------------------------


def test_announce_is_a_no_op_once_already_announced():
    """Calling the helper again after a successful send must not resend."""
    episode = _episode()
    _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])
    _mark_published(episode)

    with patch.object(cron_routes, "notify_judge_advisory") as alert, \
         patch.object(cron_routes.storage, "save_episode"):
        cron_routes._announce_advisory_publication(
            episode.get("episode_id", "2026-W99"), episode, "sunday", "Cardamom Cinnamon Spiral Bites",
        )
    alert.assert_called_once()
    first_announced_at = episode["judge_advisory"]["sunday"]["announced_at"]

    with patch.object(cron_routes, "notify_judge_advisory") as alert_again, \
         patch.object(cron_routes.storage, "save_episode") as save_again:
        cron_routes._announce_advisory_publication(
            episode.get("episode_id", "2026-W99"), episode, "sunday", "Cardamom Cinnamon Spiral Bites",
        )
    alert_again.assert_not_called()
    save_again.assert_not_called()
    assert episode["judge_advisory"]["sunday"]["announced_at"] == first_announced_at


def _mark_published(episode: dict) -> None:
    """What the publish path writes (#7403): published + announce_pending in
    one save, then the source handoff reaches source_ready."""
    record = episode["judge_advisory"]["sunday"]
    record["published"] = True
    record["announce_pending"] = True
    episode["static_deploy"] = {"status": "source_ready", "phase": "manual_deploy"}


def _crashed_after_publish_episode() -> dict:
    """What disk holds if the process died right after saving published=True
    but before the alert ever went out - the exact window #7403 fixes.
    """
    return {
        "episode_id": "2026-W99",
        "concept": "Cardamom Cinnamon Spiral Bites",
        "published_at": "2026-09-21T00:00:00+00:00",
        "stages": {"sunday": {"status": "complete", "dialogue": []}},
        "events": [],
        "judge_advisory": {
            "sunday": {
                "selected_below_bar": True,
                "published": True,
                "published_at": "2026-09-21T00:00:00+00:00",
                "attempts": 3,
                "attempt_selected": 2,
                "verdict": "FAIL - attempt 2",
                "scores": {"natural_progression": 2},
                "weakest": ["natural_progression"],
                "recorded_at": "2026-09-21T00:00:00+00:00",
                # Saved with published=True by the publish path; the alert
                # never went out, so it is still owed.
                "announce_pending": True,
            }
        },
        # The source handoff finished before the crash.
        "static_deploy": {"status": "source_ready", "phase": "manual_deploy"},
    }


def test_a_crashed_run_s_alert_is_sent_on_the_next_already_published_retry():
    """The already-published fast path must retry a missing announce."""
    episode = _crashed_after_publish_episode()
    body = cron_routes.StageRequest(episode_id="2026-W99", force=True)

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "notify_judge_advisory") as alert:
        result = asyncio.run(cron_routes.cron_sunday(_sunday_request()))

    assert result["already_published"] is True
    alert.assert_called_once()
    assert alert.call_args.kwargs["stage"] == "sunday"
    assert episode["judge_advisory"]["sunday"]["announced_at"]
    save_episode.assert_called_once()


def test_a_second_retry_after_the_alert_sent_does_not_resend():
    """Once the delivered alert is recorded on disk (announce_pending cleared,
    announced_at set), further already-published hits are quiet."""
    episode = _crashed_after_publish_episode()
    episode["judge_advisory"]["sunday"]["announce_pending"] = False
    episode["judge_advisory"]["sunday"]["announced_at"] = "2026-09-21T00:05:00+00:00"
    body = cron_routes.StageRequest(episode_id="2026-W99", force=True)

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "notify_judge_advisory") as alert:
        result = asyncio.run(cron_routes.cron_sunday(_sunday_request()))

    assert result["already_published"] is True
    alert.assert_not_called()
    save_episode.assert_not_called()


def test_a_non_string_weakest_entry_cannot_break_the_alert():
    """The judge's JSON is model output; only the list-ness is checked.

    cron_routes.py:621-622 keeps whatever entries the model returned, and
    the verdict builder beside it already coerces. An alert that raises
    while formatting would escape into _run_stage and fail the publish —
    the exact failure this gate exists to prevent.
    """
    from backend.utils import discord

    with patch.object(discord, "send_alert", return_value=True) as sent:
        discord.notify_judge_advisory(
            concept="Cardamom Cinnamon Spiral Bites",
            stage="sunday",
            verdict="FAIL",
            episode_id="2026-W99",
            attempts=3,
            scores={"natural_progression": 2, 7: "odd"},
            weakest=[1, None, "turn_taking"],
        )

    # Status only (#7930): scores, weakest and verdict are on the episode.
    assert sent.call_args.kwargs == {
        "subject": "Published · Dialogue below bar",
        "body": "2026-W99 · Sunday",
        "severity": "warning",
    }


def test_advisory_keeps_the_forensics_of_every_attempt():
    episode = _episode()
    _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    kept = episode["rejected_dialogues"]["sunday"]
    assert [r["attempt"] for r in kept] == [1, 2, 3]
    assert [r.get("best_of_run", False) for r in kept] == [False, True, False]


def test_advisory_records_qa_scores_for_the_published_attempt():
    episode = _episode()
    calls = {"n": 0}

    def _gen(stage, concept, **kw):
        calls["n"] += 1
        return _dialogue(str(calls["n"]))

    def _judge(concept, stage, dialogue, episode_, **kw):
        episode_.setdefault("judge_scores", {})[stage] = {"x": 2}
        return False, "FAIL"

    with patch.object(cron_routes, "_generate_dialogue", _gen), \
         patch.object(cron_routes, "_judge_dialogue", _judge), \
         patch.object(cron_routes, "_score_dialogue_qa",
                      lambda *a, **kw: {"score": 61}), \
         patch.object(cron_routes, "notify_judge_advisory", lambda **kw: True):
        cron_routes._generate_and_judge_dialogue(
            "sunday", "Cardamom Cinnamon Spiral Bites", episode, advisory=True,
        )

    assert episode["qa_scores"]["sunday"] == {"score": 61}


def test_advisory_does_not_soften_the_empty_generation_guard():
    """A generator that returns nothing still raises inside the loop.

    This is the pre-existing fail-closed guard, not the advisory fallback —
    it fires on attempt 1, before anything is rejected.
    """
    episode = _episode()

    with patch.object(cron_routes, "_generate_dialogue", lambda *a, **kw: []), \
         patch.object(cron_routes, "notify_judge_advisory") as alert:
        with pytest.raises(RuntimeError, match="no messages"):
            cron_routes._generate_and_judge_dialogue(
                "sunday", "Cardamom Cinnamon Spiral Bites", episode, advisory=True,
            )
    alert.assert_not_called()


def test_the_advisory_fallback_alerts_before_it_raises():
    """Nothing to publish is a different failure from something weak.

    JudgeFailedError.already_notified is True, so _save_stage_failure skips
    its own alert on the promise that the raiser sent a better one. If this
    path ever raises without alerting first, Sunday dies in total silence.
    max_retries=-1 drives the loop zero times, which is the one way to reach
    the fallback with no attempt to publish.
    """
    episode = _episode()

    with patch.object(cron_routes, "_generate_dialogue", lambda *a, **kw: []), \
         patch.object(cron_routes, "notify_judge_failure") as alert, \
         patch.object(cron_routes, "notify_judge_advisory") as soft_alert:
        with pytest.raises(cron_routes.JudgeFailedError):
            cron_routes._generate_and_judge_dialogue(
                "sunday", "Cardamom Cinnamon Spiral Bites", episode,
                advisory=True, max_retries=-1,
            )

    alert.assert_called_once()
    soft_alert.assert_not_called()
    assert cron_routes.JudgeFailedError.already_notified is True


def test_an_unscored_attempt_is_never_the_one_that_publishes():
    """An unscored attempt is one the judge never assessed.

    It cannot win the ranking, and the attempt that does publish carries
    its own numbers — the loop leaves the LAST attempt's on the episode,
    and _judge_meta_fields spreads whatever is there into the published
    stage record.
    """
    episode = _episode()
    calls = {"n": 0}

    def _gen(stage, concept, **kw):
        calls["n"] += 1
        return _dialogue(str(calls["n"]))

    def _judge(concept, stage, dialogue, episode_, **kw):
        # Attempt 2 is the only one the judge scored. Attempts 1 and 3
        # errored, so they record empty meta for themselves.
        if calls["n"] == 2:
            episode_.setdefault("judge_scores", {})[stage] = {"x": 4}
            episode_.setdefault("judge_weakest", {})[stage] = ["turn_taking"]
            episode_.setdefault("judge_reason", {})[stage] = "thin close"
        else:
            episode_.setdefault("judge_scores", {})[stage] = {}
            episode_.setdefault("judge_weakest", {})[stage] = []
            episode_.setdefault("judge_reason", {})[stage] = "judge error: RuntimeError"
        return False, f"FAIL - attempt {calls['n']}"

    with patch.object(cron_routes, "_generate_dialogue", _gen), \
         patch.object(cron_routes, "_judge_dialogue", _judge), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}), \
         patch.object(cron_routes, "notify_judge_advisory", lambda **kw: True):
        dialogue, _ = cron_routes._generate_and_judge_dialogue(
            "sunday", "Cardamom Cinnamon Spiral Bites", episode, advisory=True,
        )

    assert dialogue[0]["message"] == "attempt 2"
    assert cron_routes._judge_meta_fields(episode, "sunday") == {
        "judge_scores": {"x": 4},
        "judge_weakest": ["turn_taking"],
        "judge_reason": "thin close",
    }


def test_the_gate_is_still_a_gate_everywhere_else():
    """Only the caller that asks for advisory gets it. Default is unchanged."""
    episode = _episode()

    with patch.object(cron_routes, "_generate_dialogue",
                      lambda stage, concept, **kw: _dialogue("x")), \
         patch.object(cron_routes, "_judge_dialogue",
                      lambda *a, **kw: (False, "FAIL - nope")), \
         patch.object(cron_routes, "notify_judge_failure", lambda **kw: True), \
         patch.object(cron_routes, "notify_judge_advisory") as advisory_alert:
        with pytest.raises(cron_routes.JudgeFailedError):
            cron_routes._generate_and_judge_dialogue(
                "friday", "Cardamom Cinnamon Spiral Bites", episode,
            )
    advisory_alert.assert_not_called()


# ---------------------------------------------------------------------------
# Wiring: the route has to ask for it, and the body has to reach the prompt
# ---------------------------------------------------------------------------


def _sunday_request() -> SimpleNamespace:
    return SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/cron/sunday"))


def _sunday_episode() -> dict:
    return {
        "episode_id": "2026-W99",
        "concept": "Cardamom Cinnamon Spiral Bites",
        "recipe_id": "abc123",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Spiral Bites"}},
            "wednesday": approved_wednesday(episode_id="2026-W99"),
        },
        "events": [],
    }


def test_the_sunday_route_asks_for_the_advisory_judge():
    episode = _sunday_episode()
    body = cron_routes.StageRequest(
        episode_id="2026-W99", force=True,
        injected_event="The glaze set too thin overnight.",
    )

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue",
                      return_value=([], "FAIL")) as judged, \
         patch.object(cron_routes, "notify_pipeline_failure"), \
         patch.object(cron_routes, "_editorial_qa_review",
                      side_effect=RuntimeError("stop after the dialogue")):
        # _run_stage turns the sentinel into a 500; we only care that the
        # dialogue call happened first, and with what.
        with pytest.raises(HTTPException, match="stop after the dialogue"):
            asyncio.run(cron_routes.cron_sunday(_sunday_request()))

    kwargs = judged.call_args.kwargs
    assert kwargs["advisory"] is True
    assert kwargs["injected_event"] == "The glaze set too thin overnight."


def test_an_injected_event_reaches_the_simulator():
    """The parameter has existed since W15; nothing could ever supply one."""
    seen: dict = {}

    def _run_simulation(**kw):
        seen.update(kw)
        return {"messages": _dialogue("1")}

    with patch.object(cron_routes, "_get_run_simulation", lambda: _run_simulation):
        cron_routes._generate_dialogue(
            "sunday", "Cardamom Cinnamon Spiral Bites",
            model="test/model",
            injected_event="The glaze set too thin overnight.",
        )

    assert seen["injected_event"] == "The glaze set too thin overnight."


def test_every_stage_threads_the_body_event():
    """A field nothing reads is the bug we are fixing; assert each handler."""
    import inspect
    source = inspect.getsource(cron_routes)
    for day in cron_routes.DAY_ORDER:
        handler = inspect.getsource(getattr(cron_routes, f"cron_{day}"))
        assert "injected_event=body.injected_event" in handler, day
    assert source.count("injected_event=body.injected_event") == len(cron_routes.DAY_ORDER)


def test_the_handler_claims_publication_only_after_the_episode_is_saved():
    """The record is flipped, persisted, and only then announced.

    Announcing before the save could email Erik about a page whose episode
    write then failed; flipping after the save would persist published=False
    on a week that did publish.
    """
    episode = _sunday_episode()
    episode["judge_advisory"] = {
        "sunday": {
            "selected_below_bar": True,
            "published": False,
            "attempts": 3,
            "attempt_selected": 2,
            "verdict": "FAIL - attempt 2",
            "scores": {"natural_progression": 2},
            "weakest": ["natural_progression"],
            "recorded_at": "2026-09-21T00:00:00+00:00",
        }
    }
    body = cron_routes.StageRequest(episode_id="2026-W99", force=True)
    order: list[str] = []

    def _save(_episode_id, ep):
        order.append(f"save:published={ep['judge_advisory']['sunday']['published']}")

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode", side_effect=_save), \
         patch.object(cron_routes, "_generate_and_judge_dialogue",
                      return_value=([{"character": "Devon Park", "message": "live"}], "FAIL")), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "clean")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "_set_static_deploy_state"), \
         patch.object(cron_routes, "_complete_static_source_handoff",
                      side_effect=lambda *a, **kw: order.append("handoff")), \
         patch.object(cron_routes, "regenerate_and_upload", create=True), \
         patch.object(cron_routes, "_announce_advisory_publication",
                      side_effect=lambda *a, **kw: order.append("announce")), \
         patch.object(cron_routes, "_indexnow_submit_urls", side_effect=_record_indexnow(order)):
        result = asyncio.run(cron_routes.cron_sunday(_sunday_request()))

    assert result["published"] is True
    record = episode["judge_advisory"]["sunday"]
    assert record["published"] is True
    assert record["published_at"] == episode["published_at"]
    # Saved carrying published=True, then the reader-facing pages written,
    # only then announced, and IndexNow submitted last of all (#7806) — a
    # crawler courtesy, never a publish requirement. _submit_sunday_indexnow
    # saves the episode again afterward to persist its outcome event, hence
    # the trailing second save.
    assert order[-5:] == [
        "save:published=True", "handoff", "announce", "indexnow", "save:published=True",
    ]


def test_no_advisory_alert_when_the_reader_pages_fail_to_write():
    """A source-write failure must not be followed by "the recipe is live"."""
    episode = _sunday_episode()
    episode["judge_advisory"] = {"sunday": _stale_record()}
    body = cron_routes.StageRequest(episode_id="2026-W99", force=True)

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue",
                      return_value=([{"character": "Devon Park", "message": "live"}], "FAIL")), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "clean")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "_set_static_deploy_state"), \
         patch.object(cron_routes, "notify_pipeline_failure"), \
         patch.object(cron_routes, "_complete_static_source_handoff",
                      side_effect=HTTPException(status_code=500, detail="source write failed")), \
         patch.object(cron_routes, "notify_judge_advisory") as alert:
        with pytest.raises(HTTPException, match="source write failed"):
            asyncio.run(cron_routes.cron_sunday(_sunday_request()))

    alert.assert_not_called()


def _stale_record() -> dict:
    """What a judge-failed, then QA-failed, Sunday leaves behind."""
    return {
        "selected_below_bar": True,
        "published": False,
        "attempts": 3,
        "attempt_selected": 2,
        "verdict": "FAIL - attempt 2 (from the abandoned run)",
        "scores": {"natural_progression": 2},
        "weakest": ["natural_progression"],
        "recorded_at": "2026-09-21T00:00:00+00:00",
    }


def test_a_clean_retry_clears_the_previous_run_s_advisory_record():
    """Sunday can fail the judge, fail QA, get fixed, and be re-fired.

    The second run's dialogue passes on attempt 1. If the abandoned run's
    record survives, the publish flips it to published=True and emails its
    verdict and scores for a week that was never judge-rejected.
    """
    episode = _episode()
    episode["judge_advisory"] = {"sunday": _stale_record()}

    with patch.object(cron_routes, "_generate_dialogue",
                      lambda stage, concept, **kw: _dialogue("clean")), \
         patch.object(cron_routes, "_judge_dialogue", lambda *a, **kw: (True, "PASS")), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}):
        dialogue, verdict = cron_routes._generate_and_judge_dialogue(
            "sunday", "Cardamom Cinnamon Spiral Bites", episode, advisory=True,
        )

    assert verdict == "PASS"
    assert dialogue[0]["message"] == "attempt clean"
    assert "sunday" not in episode.get("judge_advisory", {})

    with patch.object(cron_routes, "notify_judge_advisory") as alert:
        cron_routes._announce_advisory_publication(
            episode.get("episode_id", "2026-W99"), episode, "sunday", "Cardamom Cinnamon Spiral Bites",
        )
    alert.assert_not_called()


def test_a_clean_retry_leaves_another_stage_s_record_alone():
    """Clearing is per stage, not a wipe."""
    episode = _episode()
    episode["judge_advisory"] = {"sunday": _stale_record(), "friday": _stale_record()}

    with patch.object(cron_routes, "_generate_dialogue",
                      lambda stage, concept, **kw: _dialogue("clean")), \
         patch.object(cron_routes, "_judge_dialogue", lambda *a, **kw: (True, "PASS")), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}):
        cron_routes._generate_and_judge_dialogue(
            "sunday", "Cardamom Cinnamon Spiral Bites", episode, advisory=True,
        )

    assert "sunday" not in episode["judge_advisory"]
    assert episode["judge_advisory"]["friday"]["selected_below_bar"] is True


def test_a_clean_retry_clears_the_record_on_the_gated_stages_too():
    """Nothing about this is Sunday-specific; a re-fired Friday is the same."""
    episode = _episode()
    episode["judge_advisory"] = {"friday": _stale_record()}

    with patch.object(cron_routes, "_generate_dialogue",
                      lambda stage, concept, **kw: _dialogue("clean")), \
         patch.object(cron_routes, "_judge_dialogue", lambda *a, **kw: (True, "PASS")), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}):
        cron_routes._generate_and_judge_dialogue(
            "friday", "Cardamom Cinnamon Spiral Bites", episode,
        )

    assert "friday" not in episode["judge_advisory"]


def test_the_sunday_route_sends_no_alert_when_the_retry_passes():
    """End to end: the stale record must not survive into the publish.

    #7404: the original version of this test replaced
    _generate_and_judge_dialogue wholesale with a fake whose side_effect did
    `ep.get("judge_advisory", {}).pop(stage, None)` itself — the exact line
    this test exists to protect, duplicated into the mock instead of
    exercised. It passed even with that real line reverted to a no-op,
    because the real function never ran. This version only fakes the
    judge's two collaborators (_generate_dialogue, _judge_dialogue), so the
    real _generate_and_judge_dialogue — including its real clear-on-pass
    pop — is what executes.
    """
    episode = _sunday_episode()
    episode["judge_advisory"] = {"sunday": _stale_record()}
    body = cron_routes.StageRequest(episode_id="2026-W99", force=True)

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "_generate_dialogue",
                      lambda stage, concept, **kw: _dialogue("clean")), \
         patch.object(cron_routes, "_judge_dialogue", lambda *a, **kw: (True, "PASS")), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "clean")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "_set_static_deploy_state"), \
         patch.object(cron_routes, "_complete_static_source_handoff"), \
         patch.object(cron_routes, "_indexnow_submit_urls",
                      return_value=IndexNowResult(ok=True, status_code=200, detail="submitted")), \
         patch.object(cron_routes, "notify_judge_advisory") as alert:
        result = asyncio.run(cron_routes.cron_sunday(_sunday_request()))

    assert result["published"] is True
    alert.assert_not_called()
    assert "sunday" not in episode.get("judge_advisory", {})


# ---------------------------------------------------------------------------
# "The judge said it is weak" vs "the judge never spoke" (#7394)
# ---------------------------------------------------------------------------


def _judge_erroring_run(episode: dict, outcomes: list[dict | None], advisory: bool = True):
    """Each entry: a scores dict the judge recorded, or None for an error.

    None models the real error path — _judge_dialogue returns early and
    records EMPTY meta for that attempt.
    """
    calls = {"n": 0}

    def _gen(stage, concept, **kw):
        calls["n"] += 1
        return _dialogue(str(calls["n"]))

    def _judge(concept, stage, dialogue, episode_, **kw):
        outcome = outcomes[calls["n"] - 1]
        if outcome is None:
            episode_.setdefault("judge_scores", {})[stage] = {}
            episode_.setdefault("judge_weakest", {})[stage] = []
            episode_.setdefault("judge_reason", {})[stage] = "judge error: RuntimeError"
            return False, "JUDGE ERROR: RuntimeError: provider down"
        episode_.setdefault("judge_scores", {})[stage] = outcome
        episode_.setdefault("judge_weakest", {})[stage] = ["voice_distinctiveness"]
        episode_.setdefault("judge_reason", {})[stage] = f"reason {calls['n']}"
        return False, f"FAIL - attempt {calls['n']}"

    with patch.object(cron_routes, "_generate_dialogue", _gen), \
         patch.object(cron_routes, "_judge_dialogue", _judge), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}), \
         patch.object(cron_routes, "notify_judge_advisory") as soft, \
         patch.object(cron_routes, "notify_judge_failure") as hard:
        try:
            result = cron_routes._generate_and_judge_dialogue(
                "sunday", "Cardamom Cinnamon Spiral Bites", episode, advisory=advisory,
            )
        except cron_routes.JudgeFailedError as exc:
            return None, exc, soft, hard
    return result, None, soft, hard


def test_a_judge_outage_does_not_publish_unjudged_dialogue():
    """PR #45's contract: an outage pauses the week, it does not ship blind.

    Advisory relaxes the verdict, never the requirement that there be one.
    """
    episode = _episode()
    result, exc, soft, hard = _judge_erroring_run(episode, [None, None, None])

    assert result is None
    assert isinstance(exc, cron_routes.JudgeFailedError)
    soft.assert_not_called()
    hard.assert_called_once()
    assert "sunday" not in episode.get("judge_advisory", {})


def test_an_unjudged_attempt_cannot_outrank_a_judged_one():
    """An errored attempt carries {}; it must not win on a sum of nothing."""
    episode = _episode()
    (dialogue, _), exc, soft, _ = _judge_erroring_run(episode, [{"x": 3}, None, {"x": 1}])

    assert exc is None
    assert dialogue[0]["message"] == "attempt 1"
    assert episode["judge_scores"]["sunday"] == {"x": 3}
    assert soft.call_count == 0  # the handler alerts, not the helper
    kept = episode["rejected_dialogues"]["sunday"]
    assert [r.get("best_of_run", False) for r in kept] == [True, False, False]


def test_an_unjudged_attempt_does_not_inherit_the_previous_one_s_scores():
    """The bug Codex named: judge_scores[stage] survives a failed call."""
    episode = _episode()
    _judge_erroring_run(episode, [{"x": 9}, None, None])

    kept = episode["rejected_dialogues"]["sunday"]
    assert kept[0]["scores"] == {"x": 9}
    assert kept[1]["scores"] == {}
    assert kept[2]["scores"] == {}


def test_the_gated_stages_still_fail_closed_on_a_judge_outage():
    episode = _episode()
    result, exc, _, hard = _judge_erroring_run(episode, [None, None, None], advisory=False)

    assert result is None
    assert isinstance(exc, cron_routes.JudgeFailedError)
    hard.assert_called_once()


def test_the_judge_records_empty_meta_when_its_provider_errors():
    """At the source: a failed judge call must not leave stale numbers."""
    episode = _episode()
    episode["judge_scores"] = {"sunday": {"natural_progression": 5}}
    episode["judge_weakest"] = {"sunday": ["nothing"]}
    episode["judge_reason"] = {"sunday": "from the run before"}

    with patch.dict(os.environ, {"JUDGE_MODEL": "anthropic/claude-opus-4-6"}), \
         patch.object(cron_routes, "generate_judge_response",
                      side_effect=RuntimeError("provider down")):
        passed, verdict = cron_routes._judge_dialogue(
            "Cardamom Cinnamon Spiral Bites", "sunday", _dialogue("x"), episode,
        )

    assert passed is False
    assert "JUDGE ERROR" in verdict
    assert episode["judge_scores"]["sunday"] == {}
    assert episode["judge_weakest"]["sunday"] == []
    assert "judge error" in episode["judge_reason"]["sunday"]


def test_announce_persists_announced_at_under_the_callers_episode_id():
    """The exactly-once record is saved under the episode the caller named,
    never a fallback id (#7403 review)."""
    episode = {
        "episode_id": "2026-W99",
        "judge_advisory": {"sunday": {
            "published": True, "announce_pending": True, "scores": {"voice_distinctiveness": 2}, "weakest": [],
        }},
        "static_deploy": {"status": "source_ready", "phase": "manual_deploy"},
    }
    with patch.object(cron_routes, "notify_judge_advisory") as notify, \
         patch.object(cron_routes.storage, "save_episode") as save:
        cron_routes._announce_advisory_publication("2026-W41", episode, "sunday", "Test Cups")
        cron_routes._announce_advisory_publication("2026-W41", episode, "sunday", "Test Cups")
    notify.assert_called_once()
    save.assert_called_once_with("2026-W41", episode)
    assert episode["judge_advisory"]["sunday"]["announced_at"]



def _owed_episode() -> dict:
    episode = _crashed_after_publish_episode()
    return episode


def test_an_undelivered_alert_stays_owed_and_is_sent_on_the_next_try():
    """Codex review of 2c6200f: notify_judge_advisory returns False without
    raising when every channel is down. That must not mark the alert sent."""
    episode = _owed_episode()
    with patch.object(cron_routes, "notify_judge_advisory", return_value=False) as alert, \
         patch.object(cron_routes.storage, "save_episode") as save:
        cron_routes._announce_advisory_publication("2026-W99", episode, "sunday", "C")
    alert.assert_called_once()
    save.assert_not_called()
    record = episode["judge_advisory"]["sunday"]
    assert record["announce_pending"] is True and "announced_at" not in record

    with patch.object(cron_routes, "notify_judge_advisory", return_value=True) as alert, \
         patch.object(cron_routes.storage, "save_episode") as save:
        cron_routes._announce_advisory_publication("2026-W99", episode, "sunday", "C")
    alert.assert_called_once()
    save.assert_called_once_with("2026-W99", episode)
    assert record["announce_pending"] is False and record["announced_at"]


def test_a_legacy_published_advisory_is_never_re_announced():
    """Codex review of 2c6200f: a record published by older code (no
    announce_pending) may already have been announced; never resend it."""
    episode = _owed_episode()
    del episode["judge_advisory"]["sunday"]["announce_pending"]
    with patch.object(cron_routes, "notify_judge_advisory") as alert, \
         patch.object(cron_routes.storage, "save_episode") as save:
        cron_routes._announce_advisory_publication("2026-W99", episode, "sunday", "C")
    alert.assert_not_called()
    save.assert_not_called()


def test_no_announce_before_the_source_handoff_reaches_source_ready():
    episode = _owed_episode()
    episode["static_deploy"] = {"status": "pending"}
    with patch.object(cron_routes, "notify_judge_advisory") as alert:
        cron_routes._announce_advisory_publication("2026-W99", episode, "sunday", "C")
    alert.assert_not_called()
    assert episode["judge_advisory"]["sunday"]["announce_pending"] is True


def test_a_failed_marker_save_after_delivery_does_not_raise():
    """Codex review of 2c6200f: the recipe already published and the alert
    went out; a failed save of the marker must not fail the Sunday stage."""
    episode = _owed_episode()
    with patch.object(cron_routes, "notify_judge_advisory", return_value=True) as alert, \
         patch.object(cron_routes.storage, "save_episode", side_effect=OSError("blob down")):
        cron_routes._announce_advisory_publication("2026-W99", episode, "sunday", "C")
    alert.assert_called_once()
    # Codex review of 89430c3: the live object is what a warm process's
    # storage cache hands back on the next load, so it must still say the
    # alert is owed, exactly as storage does.
    record = episode["judge_advisory"]["sunday"]
    assert record["announce_pending"] is True and "announced_at" not in record

    with patch.object(cron_routes, "notify_judge_advisory", return_value=True) as alert, \
         patch.object(cron_routes.storage, "save_episode") as save:
        cron_routes._announce_advisory_publication("2026-W99", episode, "sunday", "C")
    alert.assert_called_once()
    saved = save.call_args.args[1]
    assert saved["judge_advisory"]["sunday"]["announce_pending"] is False
    assert record["announce_pending"] is False and record["announced_at"]


def test_fast_path_finishes_a_pending_handoff_then_announces_once():
    """Crash after the publish save but before the handoff: the retry
    completes the handoff first, then sends the owed alert."""
    episode = _owed_episode()
    episode["static_deploy"] = {"status": "pending"}
    body = cron_routes.StageRequest(episode_id="2026-W99", force=True)
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "_publish_sunday_sources") as publish_sources, \
         patch.object(cron_routes, "notify_judge_advisory", return_value=True) as alert:
        result = asyncio.run(cron_routes.cron_sunday(_sunday_request()))
    assert result["already_published"] is True
    publish_sources.assert_called_once()
    alert.assert_called_once()
    assert episode["static_deploy"]["status"] == "source_ready"
    assert episode["judge_advisory"]["sunday"]["announce_pending"] is False
