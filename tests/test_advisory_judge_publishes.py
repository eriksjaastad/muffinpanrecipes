"""A weak conversation must not withhold a finished recipe (#7394, #7352).

W38 produced a recipe, a hero image and six complete days, then published
nothing at all: Sunday's dialogue failed the judge three times and the raise
happened before the publish block. The judge now scores Sunday without
gating it, and the request body can hand a stage a narrative problem so a
re-fire is a cron call rather than a hand-written script.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.admin import cron_routes


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


def test_advisory_publication_is_recorded_on_the_episode():
    episode = _episode()
    _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    record = episode["judge_advisory"]["sunday"]
    assert record["published_below_bar"] is True
    assert record["attempts"] == 3
    assert record["attempt_published"] == 2
    assert record["scores"] == {"x": 9}
    assert record["recorded_at"]
    assert any("advisory gate" in e for e in episode["events"])


def test_advisory_publication_still_alerts():
    """Not gating is not the same as not telling Erik."""
    episode = _episode()
    _, _, alerts = _run_advisory(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    assert len(alerts) == 1
    assert alerts[0]["stage"] == "sunday"
    assert alerts[0]["attempts"] == 3
    assert alerts[0]["scores"] == {"x": 9}
    assert alerts[0]["weakest"] == ["weak 2"]


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

    with patch.object(cron_routes, "_generate_dialogue", _gen), \
         patch.object(cron_routes, "_judge_dialogue",
                      lambda *a, **kw: (False, "FAIL")), \
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


def test_a_published_attempt_without_scores_does_not_inherit_another_ones():
    """The restore must overwrite, not skip on empty.

    _judge_dialogue can fail to parse structured output for one attempt. If
    that attempt wins, skipping the restore would leave the LAST attempt's
    numbers on the episode, and _judge_meta_fields would spread them into
    the published stage record — the exact mismatch the restore prevents.
    """
    episode = _episode()
    calls = {"n": 0}

    def _gen(stage, concept, **kw):
        calls["n"] += 1
        return _dialogue(str(calls["n"]))

    def _judge(concept, stage, dialogue, episode_, **kw):
        # Attempt 1 wins the max-sum tie with no parsed scores at all;
        # attempts 2 and 3 leave real numbers behind on the episode.
        if calls["n"] > 1:
            episode_.setdefault("judge_scores", {})[stage] = {"x": -1}
            episode_.setdefault("judge_weakest", {})[stage] = ["stale"]
            episode_.setdefault("judge_reason", {})[stage] = "stale reason"
        return False, f"FAIL - attempt {calls['n']}"

    with patch.object(cron_routes, "_generate_dialogue", _gen), \
         patch.object(cron_routes, "_judge_dialogue", _judge), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}), \
         patch.object(cron_routes, "notify_judge_advisory", lambda **kw: True):
        dialogue, _ = cron_routes._generate_and_judge_dialogue(
            "sunday", "Cardamom Cinnamon Spiral Bites", episode, advisory=True,
        )

    assert dialogue[0]["message"] == "attempt 1"
    assert episode["judge_scores"]["sunday"] == {}
    assert episode["judge_weakest"]["sunday"] == []
    assert episode["judge_reason"]["sunday"] == ""
    assert cron_routes._judge_meta_fields(episode, "sunday") == {
        "judge_scores": {}, "judge_weakest": [], "judge_reason": "",
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
    return SimpleNamespace(url=SimpleNamespace(path="/api/cron/sunday"))


def _sunday_episode() -> dict:
    return {
        "episode_id": "2026-W99",
        "concept": "Cardamom Cinnamon Spiral Bites",
        "recipe_id": "abc123",
        "stages": {
            "monday": {"status": "complete", "recipe_data": {"title": "Spiral Bites"}},
            "wednesday": {"status": "complete"},
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
