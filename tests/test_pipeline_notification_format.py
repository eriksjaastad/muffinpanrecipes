"""Pipeline alerts are a short status and identifiers only (#7930).

No verdict, score, error dump, recipe title or fields array; a malformed
argument must never make an alert raise.
"""

from unittest.mock import patch

import pytest

from backend.utils import discord


def _sent(fn, **kwargs) -> dict:
    with patch.object(discord, "send_alert", return_value=True) as send:
        assert fn(**kwargs) is True
    send.assert_called_once()
    return send.call_args.kwargs


def test_judge_failure_is_episode_paused_with_week_and_day():
    sent = _sent(
        discord.notify_judge_failure,
        concept="Brazilian Pao de Queijo Bites",
        stage="friday",
        verdict="FAIL - long verdict text",
        episode_id="2026-W40",
        attempts=3,
    )
    assert sent == {"subject": "Episode paused", "body": "2026-W40 · Friday", "severity": "warning"}


def test_advisory_is_published_below_bar_with_week_and_day():
    sent = _sent(
        discord.notify_judge_advisory,
        concept="Cardamom Spiral Bites",
        stage="sunday",
        verdict="FAIL",
        episode_id="2026-W40",
        attempts=3,
        scores={"voice": 2},
        weakest=["voice"],
    )
    assert sent == {
        "subject": "Published · Dialogue below bar",
        "body": "2026-W40 · Sunday",
        "severity": "warning",
    }


def test_pipeline_failure_drops_the_error_and_title_but_keeps_stage_and_id():
    sent = _sent(
        discord.notify_pipeline_failure,
        recipe_id="abc12345",
        concept="Custard Tarts",
        stage="sunday (editorial QA)",
        error_message="Traceback ...\n" * 200,
    )
    assert sent == {
        "subject": "Pipeline failed",
        "body": "sunday (editorial QA) · abc12345",
        "severity": "critical",
    }


@pytest.mark.parametrize("junk", [None, 7, {"a": 1}, ["x", None], "", "  \n "])
def test_malformed_metadata_never_raises(junk):
    sent = _sent(
        discord.notify_judge_advisory,
        concept=junk,
        stage=junk,
        verdict=junk,
        episode_id=junk,
        attempts=junk,
        scores=junk if isinstance(junk, dict) else {1: None},
        weakest=junk if isinstance(junk, list) else [None, 3],
    )
    assert sent["subject"] == "Published · Dialogue below bar"
    assert "\n" not in sent["body"]
    sent = _sent(discord.notify_judge_failure, concept=junk, stage=junk, verdict=junk,
                 episode_id=junk, attempts=junk)
    assert sent["subject"] == "Episode paused"


def test_every_pipeline_alert_is_a_few_words():
    for fn, kwargs in (
        (discord.notify_judge_failure, dict(concept="c", stage="friday", verdict="v",
                                             episode_id="2026-W40", attempts=3)),
        (discord.notify_judge_advisory, dict(concept="c", stage="sunday", verdict="v",
                                              episode_id="2026-W40", attempts=3)),
    ):
        sent = _sent(fn, **kwargs)
        words = f"{sent['subject']} {sent['body']}".replace("2026-W40", "").split()
        assert len([w for w in words if w != "·"]) <= 12
