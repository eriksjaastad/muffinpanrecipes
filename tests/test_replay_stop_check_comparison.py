from types import SimpleNamespace

import pytest

from scripts import replay_stop_check_comparison as replay


def _source() -> dict:
    return {
        "pairs": [
            {
                "scenario_id": "2026-W36",
                "run_index": 1,
                "variant_messages": [
                    {"character": "Julian Torres", "message": "Use the side rake."},
                    {"character": "Ria Castillo", "message": "Agreed. Side rake wins."},
                ],
                "variant_stop_check_log": [
                    {
                        "day": "wednesday",
                        "provider": "jev",
                        "decided": 0.8,
                        "pushback": 0.6,
                        "model": "typesafe/jev-1.13-20260917",
                    }
                ],
            }
        ]
    }


def test_replay_compares_same_transcript_and_reports_disagreement(monkeypatch):
    seen = {}

    def fake_check(lines, provider, *, day, objective):
        seen.update(lines=lines, provider=provider, day=day, objective=objective)
        return SimpleNamespace(
            decided=0.4,
            pushback=0.9,
            model="anthropic/claude-haiku-4-5-20251001",
        )

    monkeypatch.setattr(replay, "check_scene_done", fake_check)
    result = replay.replay(_source(), max_samples=1)

    assert seen == {
        "lines": ["Julian: Use the side rake.", "Ria: Agreed. Side rake wins."],
        "provider": "haiku",
        "day": "wednesday",
        "objective": "Choose the hero shot",
    }
    assert result["sample_count"] == 1
    assert result["agreement_rate"] == 0
    assert result["samples"][0]["jev"]["done"] is True
    assert result["samples"][0]["haiku"]["done"] is False
    assert result["samples"][0]["agrees_on_done"] is False


def test_replay_refuses_more_than_the_task_call_cap():
    with pytest.raises(replay.ReplayError, match="between 1 and 20"):
        replay.replay(_source(), max_samples=21)


def test_replay_requires_a_jev_scored_pair(monkeypatch):
    source = _source()
    source["pairs"][0]["variant_stop_check_log"] = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: None)

    with pytest.raises(replay.ReplayError, match="no Jev-scored pairs"):
        replay.replay(source, max_samples=1)


def test_replay_records_strict_json_failure_and_continues(monkeypatch):
    def fail_check(*args, **kwargs):
        raise replay.StopCheckError("haiku stop check returned malformed JSON")

    monkeypatch.setattr(replay, "check_scene_done", fail_check)
    result = replay.replay(_source(), max_samples=1)

    assert result["comparable_count"] == 0
    assert result["haiku_error_count"] == 1
    assert result["agreement_rate"] is None
    assert result["samples"][0]["haiku"] is None
    assert result["samples"][0]["agrees_on_done"] is None
    assert "malformed JSON" in result["samples"][0]["haiku_error"]
