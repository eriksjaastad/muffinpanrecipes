import json
import sys
from types import SimpleNamespace

import pytest

from backend.utils import model_router
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
        "require_pushback": True,
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
    assert result["thresholds"]["require_pushback"] is True


def test_cli_require_pushback_false_ignores_pushback_in_done_and_receipt(monkeypatch, tmp_path):
    source = _source()
    # Jev: decided passes, pushback fails. Haiku: both pass. Only the pushback
    # rule separates them, so disabling it must make both done and agree.
    source["pairs"][0]["variant_stop_check_log"][-1]["pushback"] = 0.1
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source))
    output_path = tmp_path / "receipt.json"
    monkeypatch.setattr(
        replay,
        "check_scene_done",
        lambda *args, **kwargs: SimpleNamespace(
            decided=0.9, pushback=0.6, model="openrouter/anthropic/claude-haiku-4.5"
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "replay", str(source_path), str(output_path),
            "--decided-threshold", "0.7", "--pushback-threshold", "0.5",
            "--no-require-pushback",
        ],
    )

    replay.main()

    receipt = json.loads(output_path.read_text())
    sample = receipt["samples"][0]
    assert receipt["thresholds"]["require_pushback"] is False
    assert sample["jev"]["done"] is True
    assert sample["haiku"]["done"] is True
    assert sample["agrees_on_done"] is True


def test_replay_attempt_budget_counts_empty_stop_retries_and_refuses_21st(monkeypatch):
    # Each sample's empty-stop reply makes 3 provider attempts, so the 7th
    # sample needs attempts 19-21. Only 20 may be sent.
    source = _source()
    source["pairs"] = [{**source["pairs"][0], "run_index": i} for i in range(1, 8)]
    sent = []
    prior_calls = []

    def empty_stop(client, model, messages, temperature):
        sent.append(model)
        return "", "stop", 1

    def prior_hook(provider, model, prompt_bytes):
        prior_calls.append(model)

    monkeypatch.setattr(model_router, "_PRE_CALL_HOOK", None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(model_router, "_openrouter_attempt", empty_stop)
    prior = model_router.set_pre_call_hook(prior_hook)
    assert prior is None

    result = _replay(source, max_samples=7, max_errors=7)

    assert len(sent) == replay.MAX_REPLAY_CALLS == len(prior_calls)
    assert result["call_budget_exhausted"] is True
    assert result["model_call_attempts"] == 20
    assert result["sample_count"] == 6
    assert result["haiku_error_count"] == 6
    assert model_router._PRE_CALL_HOOK is prior_hook
    with pytest.raises(replay.ReplayError, match="between 1 and 20"):
        _replay(_source(), max_samples=21)


def test_replay_requires_a_jev_scored_pair(monkeypatch):
    source = _source()
    source["pairs"][0]["variant_stop_check_log"] = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: None)

    with pytest.raises(replay.ReplayError, match="no Jev-scored pairs"):
        _replay(source)


@pytest.mark.parametrize("bad_log", ["jev", {"provider": "jev"}, None])
def test_replay_rejects_non_list_stop_check_log_before_first_paid_call(monkeypatch, bad_log):
    source = _source()
    invalid = {**source["pairs"][0], "run_index": 2, "variant_stop_check_log": bad_log}
    source["pairs"].append(invalid)
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))

    with pytest.raises(replay.ReplayError, match="must be a list of verdicts"):
        _replay(source, max_samples=2, max_errors=2)

    assert calls == []


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


def test_replay_rejects_empty_scored_message_before_first_paid_call(monkeypatch):
    source = _source()
    source["pairs"][0]["variant_messages"][1]["message"] = ""
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))

    with pytest.raises(replay.ReplayError, match="non-empty character, message, and model"):
        _replay(source)

    assert calls == []


@pytest.mark.parametrize("field", ["character", "message", "model"])
def test_replay_rejects_whitespace_only_scored_field_before_first_paid_call(monkeypatch, field):
    source = _source()
    source["pairs"][0]["variant_messages"][1][field] = " \t\n"
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))

    with pytest.raises(replay.ReplayError, match="non-empty character, message, and model"):
        _replay(source)

    assert calls == []


def test_replay_rejects_non_object_scored_message_before_first_paid_call(monkeypatch):
    source = _source()
    source["pairs"][0]["variant_messages"][1] = None
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))

    with pytest.raises(replay.ReplayError, match="must be a JSON object"):
        _replay(source)

    assert calls == []


def test_cli_refuses_existing_output_before_any_paid_call(monkeypatch, tmp_path):
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(_source()))
    output_path = tmp_path / "receipt.json"
    output_path.write_bytes(b'{"paid": "receipt"}\n')
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))
    monkeypatch.setattr(
        sys,
        "argv",
        ["replay", str(source_path), str(output_path), "--decided-threshold", "0.7", "--pushback-threshold", "0.5"],
    )

    with pytest.raises(replay.ReplayError, match="will not be overwritten"):
        replay.main()

    assert calls == []
    assert output_path.read_bytes() == b'{"paid": "receipt"}\n'


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
    [("decided", None), ("pushback", "0.8"), ("model", ""), ("model", " \t\n")],
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
