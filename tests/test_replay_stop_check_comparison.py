from types import SimpleNamespace

import pytest

from scripts import replay_stop_check_comparison as replay


def _source() -> dict:
    return {
        "stage": "wednesday",
        "pairs": [
            {
                "scenario_id": "2026-W36",
                "run_index": 1,
                "variant_messages": [
                    {
                        "character": "Julian Torres",
                        "message": "Use the side rake.",
                        "model": "openrouter/anthropic/claude-haiku-4.5",
                    },
                    {
                        "character": "Ria Castillo",
                        "message": "Agreed. Side rake wins.",
                        "model": "openrouter/anthropic/claude-haiku-4.5",
                    },
                    {
                        "character": "Stephanie Whitmore",
                        "message": "Closing message Jev never saw.",
                        "model": "openrouter/anthropic/claude-haiku-4.5",
                    },
                ],
                "variant_stop_check_log": [
                    {
                        "day": "wednesday",
                        "tick": 1,
                        "provider": "jev",
                        "decided": 0.8,
                        "pushback": 0.6,
                        "model": "typesafe/jev-1.13-20260917",
                    }
                ],
            }
        ]
    }


def _replay(source: dict, **overrides) -> dict:
    kwargs = {
        "max_samples": 1,
        "max_errors": 1,
        "decided_threshold": 0.7,
        "pushback_threshold": 0.5,
    }
    kwargs.update(overrides)
    return replay.replay(source, **kwargs)


def test_replay_uses_only_messages_through_jev_tick_and_same_haiku_model(monkeypatch):
    seen = {}

    def fake_check(lines, provider, *, day, objective, haiku_model):
        seen.update(
            lines=lines,
            provider=provider,
            day=day,
            objective=objective,
            haiku_model=haiku_model,
        )
        return SimpleNamespace(decided=0.4, pushback=0.9, model=haiku_model)

    monkeypatch.setattr(replay, "check_scene_done", fake_check)
    result = _replay(_source())

    assert seen == {
        "lines": ["Julian: Use the side rake.", "Ria: Agreed. Side rake wins."],
        "provider": "haiku",
        "day": "wednesday",
        "objective": "Choose the hero shot",
        "haiku_model": "openrouter/anthropic/claude-haiku-4.5",
    }
    sample = result["samples"][0]
    assert sample["source_message_count"] == 3
    assert sample["scored_tick"] == 1
    assert sample["scored_message_count"] == 2
    assert sample["jev"]["done"] is True
    assert sample["haiku"]["done"] is False
    assert sample["agrees_on_done"] is False


def test_replay_refuses_more_than_the_task_call_cap():
    with pytest.raises(replay.ReplayError, match="between 1 and 20"):
        _replay(_source(), max_samples=21)


def test_replay_requires_a_jev_scored_pair(monkeypatch):
    source = _source()
    source["pairs"][0]["variant_stop_check_log"] = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: None)

    with pytest.raises(replay.ReplayError, match="no Jev-scored pairs"):
        _replay(source)


def test_replay_refuses_tick_beyond_available_messages(monkeypatch):
    source = _source()
    source["pairs"][0]["variant_stop_check_log"][-1]["tick"] = 3
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: None)

    with pytest.raises(replay.ReplayError, match="cannot index 3 variant messages"):
        _replay(source)


def test_replay_records_strict_json_failure_and_stops_at_error_limit(monkeypatch):
    source = _source()
    source["pairs"].append({**source["pairs"][0], "run_index": 2})

    def fail_check(*args, **kwargs):
        raise replay.StopCheckError("haiku stop check returned malformed JSON")

    monkeypatch.setattr(replay, "check_scene_done", fail_check)
    result = _replay(source, max_samples=2, max_errors=1)

    assert result["sample_count"] == 1
    assert result["comparable_count"] == 0
    assert result["haiku_error_count"] == 1
    assert result["agreement_rate"] is None
    assert result["samples"][0]["haiku"] is None
    assert result["samples"][0]["agrees_on_done"] is None
    assert "malformed JSON" in result["samples"][0]["haiku_error"]


def test_replay_refuses_non_jev_final_verdict(monkeypatch):
    source = _source()
    source["pairs"][0]["variant_stop_check_log"][-1]["provider"] = "haiku"
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: None)

    with pytest.raises(replay.ReplayError, match="not from Jev"):
        _replay(source)


def test_replay_refuses_unembedded_seeded_prior_days(monkeypatch):
    source = _source()
    source["prior_days"] = {"path": "frozen.json", "sha256": "abc"}
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: None)

    with pytest.raises(replay.ReplayError, match="used seeded prior days"):
        _replay(source)


def test_replay_requires_verdict_day_to_match_source_stage(monkeypatch):
    source = _source()
    source["stage"] = "tuesday"
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: None)

    with pytest.raises(replay.ReplayError, match="does not match source stage"):
        _replay(source)


def test_replay_validates_all_pairs_before_first_paid_call(monkeypatch):
    source = _source()
    invalid = {**source["pairs"][0], "run_index": 2}
    invalid["variant_stop_check_log"] = [
        {**source["pairs"][0]["variant_stop_check_log"][0], "provider": "haiku"}
    ]
    source["pairs"].append(invalid)
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))

    with pytest.raises(replay.ReplayError, match="not from Jev"):
        _replay(source, max_samples=2, max_errors=2)

    assert calls == []


def test_replay_stops_at_sample_cap_when_checks_succeed(monkeypatch):
    source = _source()
    source["pairs"].append({**source["pairs"][0], "run_index": 2})
    monkeypatch.setattr(
        replay,
        "check_scene_done",
        lambda *args, **kwargs: SimpleNamespace(
            decided=0.8,
            pushback=0.6,
            model="openrouter/anthropic/claude-haiku-4.5",
        ),
    )

    result = _replay(source, max_samples=1, max_errors=1)

    assert result["sample_count"] == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("decided", None), ("pushback", "0.8"), ("model", "")],
)
def test_replay_validates_jev_fields_before_first_paid_call(monkeypatch, field, value):
    source = _source()
    invalid = {**source["pairs"][0], "run_index": 2}
    invalid["variant_stop_check_log"] = [
        {**source["pairs"][0]["variant_stop_check_log"][0], field: value}
    ]
    source["pairs"].append(invalid)
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))

    with pytest.raises(replay.ReplayError, match=f"final Jev {field}"):
        _replay(source, max_samples=2, max_errors=2)

    assert calls == []
