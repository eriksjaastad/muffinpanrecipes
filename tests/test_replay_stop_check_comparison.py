import json
import os
import sys
from types import SimpleNamespace

import pytest

from backend.utils import model_router
from scripts import replay_stop_check_comparison as replay
from scripts import simulate_dialogue_week as simulate_module

HISTORICAL_OBJECTIVE = "Choose the hero shot"


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
        "objective": HISTORICAL_OBJECTIVE,
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

    def fake_check(lines, provider, *, day, objective, haiku_model, timeout_s):
        seen.update(
            lines=lines,
            provider=provider,
            day=day,
            objective=objective,
            haiku_model=haiku_model,
            timeout_s=timeout_s,
        )
        return SimpleNamespace(decided=0.4, pushback=0.9, model=haiku_model)

    monkeypatch.setattr(replay, "check_scene_done", fake_check)
    result = _replay(_source())

    assert seen == {
        "lines": ["Julian: Use the side rake.", "Ria: Agreed. Side rake wins."],
        "provider": "haiku",
        "day": "wednesday",
        "objective": HISTORICAL_OBJECTIVE,
        "haiku_model": "openrouter/anthropic/claude-haiku-4.5",
        "timeout_s": replay.REPLAY_HAIKU_TIMEOUT_S,
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
            "--objective", HISTORICAL_OBJECTIVE,
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


def test_replay_sends_one_attempt_per_sample_within_the_20_call_cap(monkeypatch):
    # The replay timeout disables empty-stop retries in the router, so each
    # sample is exactly one admitted attempt. 20 samples -> 20 attempts, no more.
    source = _source()
    source["pairs"] = [{**source["pairs"][0], "run_index": i} for i in range(1, 21)]
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

    result = _replay(source, max_samples=20, max_errors=20)

    assert len(sent) == replay.MAX_REPLAY_CALLS == len(prior_calls)
    assert result["call_budget_exhausted"] is False
    assert result["model_call_attempts"] == 20
    assert result["sample_count"] == 20
    assert result["haiku_error_count"] == 20
    assert model_router._PRE_CALL_HOOK is prior_hook
    with pytest.raises(replay.ReplayError, match="between 1 and 20"):
        _replay(_source(), max_samples=21)


def test_replay_haiku_request_disables_sdk_retries_with_finite_timeout(monkeypatch):
    # End to end through check_scene_done -> generate_response -> the real OpenAI
    # client; only the HTTP attempt is replaced, so no network is used.
    clients = []

    def fake_attempt(client, model, messages, temperature):
        clients.append(client)
        return '{"decided": 0.9, "pushback": 0.9}', "stop", 1

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(model_router, "_openrouter_attempt", fake_attempt)

    result = _replay(_source())

    assert result["samples"][0]["haiku"]["done"] is True
    assert len(clients) == 1
    assert clients[0].max_retries == 0
    assert clients[0].timeout == replay.REPLAY_HAIKU_TIMEOUT_S


def test_replay_refuses_non_haiku_transcript_with_zero_calls(monkeypatch):
    # A Jev-scored DeepSeek or local transcript must not be replayed as Haiku:
    # no Haiku check and no provider attempt may happen.
    attempts = []
    checks = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(model_router, "_openrouter_attempt", lambda *a, **k: attempts.append(a))
    monkeypatch.setattr(replay, "check_scene_done", lambda *a, **k: checks.append(a))

    for model in ("deepseek/deepseek-v4.1-flash", "local/qwen2.5-coder:14b"):
        source = _source()
        for message in source["pairs"][0]["variant_messages"]:
            message["model"] = model
        with pytest.raises(replay.ReplayError, match="not the supported Haiku"):
            _replay(source)

    assert attempts == []
    assert checks == []


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


def _cli(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "replay", *args, "--objective", HISTORICAL_OBJECTIVE,
            "--decided-threshold", "0.7", "--pushback-threshold", "0.5",
        ],
    )


def test_cli_refuses_existing_output_before_any_paid_call(monkeypatch, tmp_path):
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(_source()))
    output_path = tmp_path / "receipt.json"
    output_path.write_bytes(b'{"paid": "receipt"}\n')
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))
    _cli(monkeypatch, str(source_path), str(output_path))

    with pytest.raises(replay.ReplayError, match="will not be overwritten"):
        replay.main()

    assert calls == []
    assert output_path.read_bytes() == b'{"paid": "receipt"}\n'


def test_cli_records_explicit_historical_objective_not_current_config(monkeypatch, tmp_path):
    # The current config objective is swapped for a sentinel, so the only way the
    # checker can see the historical objective is if the CLI argument is used.
    monkeypatch.setitem(simulate_module.DAY_MEETING_GOAL["wednesday"], "objective", "CURRENT CONFIG")
    historical = "Pick the side rake"
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(_source()))
    output_path = tmp_path / "receipt.json"
    seen = []

    def fake_check(lines, provider, *, day, objective, haiku_model, timeout_s):
        seen.append(objective)
        return SimpleNamespace(decided=0.9, pushback=0.9, model=haiku_model)

    monkeypatch.setattr(replay, "check_scene_done", fake_check)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "replay", str(source_path), str(output_path), "--objective", historical,
            "--decided-threshold", "0.7", "--pushback-threshold", "0.5",
        ],
    )

    replay.main()

    receipt = json.loads(output_path.read_text())
    assert seen == [historical]
    assert receipt["objective"] == historical
    assert receipt["samples"][0]["objective"] == historical


def test_replay_rejects_blank_objective_before_first_paid_call(monkeypatch):
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))

    with pytest.raises(replay.ReplayError, match="non-blank string"):
        _replay(_source(), objective="  \t\n")

    assert calls == []


def test_cli_refuses_missing_output_parent_before_reading_source_or_calling(monkeypatch, tmp_path):
    # The source path does not exist either: a ReplayError proves the output
    # check ran before the source read, and the missing parent is not created.
    output_path = tmp_path / "missing" / "receipt.json"
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))
    _cli(monkeypatch, str(tmp_path / "no-such-source.json"), str(output_path))

    with pytest.raises(replay.ReplayError, match="output directory does not exist"):
        replay.main()

    assert calls == []
    assert not (tmp_path / "missing").exists()


def test_cli_reserves_output_before_checker_then_full_receipt_replaces_marker(monkeypatch, tmp_path):
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(_source()))
    output_path = tmp_path / "receipt.json"
    marker_during_call = []

    def fake_check(*args, **kwargs):
        marker_during_call.append(json.loads(output_path.read_text()))
        return SimpleNamespace(decided=0.9, pushback=0.9, model="openrouter/anthropic/claude-haiku-4.5")

    monkeypatch.setattr(replay, "check_scene_done", fake_check)
    _cli(monkeypatch, str(source_path), str(output_path))

    replay.main()

    assert marker_during_call == [{"status": "reserved", "pid": os.getpid()}]
    receipt = json.loads(output_path.read_text())
    assert "status" not in receipt
    assert receipt["sample_count"] == 1
    assert receipt["source_sha256"]


def test_cli_failure_after_reservation_leaves_marker_as_evidence(monkeypatch, tmp_path):
    source = _source()
    source["prior_days"] = {"path": "frozen.json", "sha256": "abc"}
    source_path = tmp_path / "source.json"
    source_path.write_text(json.dumps(source))
    output_path = tmp_path / "receipt.json"
    calls = []
    monkeypatch.setattr(replay, "check_scene_done", lambda *args, **kwargs: calls.append(1))
    _cli(monkeypatch, str(source_path), str(output_path))

    with pytest.raises(replay.ReplayError, match="used seeded prior days"):
        replay.main()

    assert calls == []
    assert json.loads(output_path.read_text()) == {"status": "reserved", "pid": os.getpid()}


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
