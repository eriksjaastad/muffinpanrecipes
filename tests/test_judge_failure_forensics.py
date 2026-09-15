"""Judge failure must leave the rejected dialogues behind (#7100).

W38 failed Monday's judge twice on 2026-09-14 — six generated conversations
in one day — and the only surviving evidence either time was one sentence of
verdict. The stage record held exactly {"status", "error"}. You cannot tune a
prompt against that.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.admin import cron_routes


def _dialogue(tag: str) -> list[dict]:
    return [
        {"character": "Margaret Chen", "message": f"attempt {tag}"},
        {"character": "Marcus Reid", "message": f"there is a story in {tag}"},
    ]


def _episode() -> dict:
    return {"episode_id": "2026-W99", "stages": {}}


def _run_failing_judge(episode: dict, scores_per_attempt: list[dict]):
    """Drive _generate_and_judge_dialogue to exhaustion with a failing judge."""
    calls = {"n": 0}

    def _gen(stage, concept, **kw):
        calls["n"] += 1
        return _dialogue(str(calls["n"]))

    def _judge(concept, stage, dialogue, episode_, **kw):
        idx = calls["n"] - 1
        scores = scores_per_attempt[idx]
        episode_.setdefault("judge_scores", {})[stage] = scores
        episode_.setdefault("judge_weakest", {})[stage] = ["voice_distinctiveness"]
        episode_.setdefault("judge_reason", {})[stage] = f"reason {calls['n']}"
        return False, f"FAIL - attempt {calls['n']}"

    with patch.object(cron_routes, "_generate_dialogue", _gen), \
         patch.object(cron_routes, "_judge_dialogue", _judge), \
         patch.object(cron_routes, "notify_judge_failure", lambda **kw: True):
        with pytest.raises(cron_routes.JudgeFailedError):
            cron_routes._generate_and_judge_dialogue(
                "monday", "Cinnamon Apple Streusel Cakes", episode,
            )


def test_every_rejected_attempt_is_kept():
    episode = _episode()
    _run_failing_judge(episode, [{"a": 1}, {"a": 2}, {"a": 3}])

    kept = episode["rejected_dialogues"]["monday"]
    assert len(kept) == 3
    assert [r["attempt"] for r in kept] == [1, 2, 3]
    # Each attempt keeps ITS OWN dialogue, not the last one three times.
    assert [r["dialogue"][0]["message"] for r in kept] == [
        "attempt 1", "attempt 2", "attempt 3",
    ]


def test_each_attempt_keeps_its_own_score_before_the_next_overwrites_it():
    """_judge_dialogue writes judge_scores[stage] and the next attempt clobbers it."""
    episode = _episode()
    _run_failing_judge(episode, [{"x": 3}, {"x": 5}, {"x": 1}])

    kept = episode["rejected_dialogues"]["monday"]
    assert [r["scores"] for r in kept] == [{"x": 3}, {"x": 5}, {"x": 1}]
    assert [r["reason"] for r in kept] == ["reason 1", "reason 2", "reason 3"]


def test_the_best_scoring_attempt_is_marked():
    episode = _episode()
    _run_failing_judge(episode, [{"x": 3}, {"x": 9}, {"x": 1}])

    kept = episode["rejected_dialogues"]["monday"]
    assert [r.get("best_of_run", False) for r in kept] == [False, True, False]


def test_rejected_dialogues_survive_save_stage_failure():
    """The whole point. _save_stage_failure blind-overwrites the STAGE.

    Anything written under ep["stages"][stage] is destroyed moments later, so
    this has to live at episode level to reach the blob at all.
    """
    episode = _episode()
    _run_failing_judge(episode, [{"x": 1}, {"x": 2}, {"x": 3}])

    saved: dict = {}
    with patch.object(cron_routes.storage, "save_episode",
                      lambda eid, ep: saved.update(ep)), \
         patch.object(cron_routes, "notify_pipeline_failure", lambda **kw: True):
        err = cron_routes.JudgeFailedError(stage="monday", verdict="FAIL", attempts=3)
        cron_routes._save_stage_failure(episode, "monday", err)

    assert saved["stages"]["monday"] == {"status": "failed", "error": str(err)}
    assert len(saved["rejected_dialogues"]["monday"]) == 3


def test_rejected_dialogues_are_not_reachable_as_stage_dialogue():
    """episode_renderer gates reader-facing output on stage["dialogue"].

    A rejected attempt under that key would publish failed dialogue to the
    live site, which is strictly worse than losing it.
    """
    episode = _episode()
    _run_failing_judge(episode, [{"x": 1}, {"x": 2}, {"x": 3}])

    assert "dialogue" not in episode["stages"].get("monday", {})
    assert "rejected_dialogues" not in episode["stages"].get("monday", {})
    for day_stage in episode.get("stages", {}).values():
        assert not day_stage.get("dialogue")


def test_a_passing_judge_records_nothing():
    """No failure, no forensics. Only rejected attempts are kept."""
    episode = _episode()

    def _judge(concept, stage, dialogue, episode_, **kw):
        return True, "PASS"

    with patch.object(cron_routes, "_generate_dialogue",
                      lambda stage, concept, **kw: _dialogue("ok")), \
         patch.object(cron_routes, "_judge_dialogue", _judge), \
         patch.object(cron_routes, "_score_dialogue_qa", lambda *a, **kw: {}):
        dialogue, verdict = cron_routes._generate_and_judge_dialogue(
            "monday", "Cinnamon Apple Streusel Cakes", episode,
        )

    assert verdict == "PASS"
    assert "rejected_dialogues" not in episode
