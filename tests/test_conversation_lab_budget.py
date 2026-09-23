"""Offline integration checks for conversation_lab's optional spend ledger."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from anthropic.resources.messages import Messages

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw
from backend.utils import model_router


@pytest.fixture
def fake_sdk(monkeypatch):
    calls = {"count": [], "create": [], "retries": []}

    def count_tokens(self, **kwargs):
        calls["count"].append(kwargs)
        return SimpleNamespace(input_tokens=100)

    def create(self, **kwargs):
        calls["create"].append(kwargs)
        usage = {
            "input_tokens": 80,
            "output_tokens": 4,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
            "service_tier": "standard",
            "inference_geo": "global" if kwargs["model"] == "claude-opus-4-6" else "not_available",
        }
        return SimpleNamespace(
            usage=SimpleNamespace(model_dump=lambda exclude_none=True: usage),
            content=[SimpleNamespace(text='{"winner":"tie","per_dimension":{}}')],
        )

    def make_client(*, api_key=None, max_retries=2, **kwargs):
        calls["retries"].append(max_retries)
        return SimpleNamespace(messages=Messages.__new__(Messages))

    monkeypatch.setattr(Messages, "count_tokens", count_tokens)
    monkeypatch.setattr(Messages, "create", create)
    monkeypatch.setattr(anthropic, "Anthropic", make_client)
    monkeypatch.setattr(model_router, "_central_track", lambda *args, **kwargs: None)
    model_router._COST_LOG.clear()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-opus-4-6")
    return calls


def _messages(tag: str = "sample") -> list[dict]:
    return [
        {
            "day": "monday", "stage": "brainstorm", "character": name,
            "message": f"{tag} line {i} with a concrete recipe detail.",
            "timestamp": "", "model": "fixture", "attachments": [],
        }
        for i, name in enumerate(("Margaret Chen", "Marcus Reid"))
    ]


def _generate(model: str = "anthropic/claude-haiku-4-5-20251001"):
    return model_router.generate_response(
        "A short fixture prompt.", system_prompt="fixture system",
        model=model, temperature=0.3,
    )


def _ledger(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_cli_generation(_args):
    assert _generate() == '{"winner":"tie","per_dimension":{}}'


def _run_guarded_ab_until_judge_stops(tmp_path, monkeypatch, cap):
    variant = tmp_path / "variant.json"
    variant.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "fixture variant"}))
    generated = []

    def simulation(**kwargs):
        generated.append(kwargs)
        _generate()
        return {"messages": _messages(f"generation-{len(generated)}")}

    monkeypatch.setattr(sdw, "run_simulation", simulation)
    results = tmp_path / "results"
    ledger = tmp_path / "ledger.json"
    with pytest.raises(SystemExit):
        cl.main([
            "ab", "--concept", "Fixture", "--stage", "monday", "--runs", "1",
            "--variant", str(variant), "--recipe-context", "Fixture anchor", "--no-log",
            "--results-dir", str(results), "--max-cost", str(cap), "--budget-ledger",
            str(ledger), "--create-budget-ledger",
        ])
    [result_path] = results.glob("*.json")
    return json.loads(result_path.read_text(encoding="utf-8")), generated, ledger


def test_cli_uses_one_resumed_ledger_for_calibrate_bench_and_ab(tmp_path, monkeypatch, fake_sdk):
    ledger = tmp_path / "combined.json"
    variant = tmp_path / "variant.json"
    variant.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "fixture variant"}))
    monkeypatch.setattr(cl, "cmd_calibrate", _run_cli_generation)
    monkeypatch.setattr(cl, "cmd_bench", _run_cli_generation)
    monkeypatch.setattr(cl, "cmd_ab", _run_cli_generation)
    sdk_constructor = anthropic.Anthropic
    sdk_create = Messages.create
    sdk_count = Messages.count_tokens

    cl.main([
        "calibrate", "--from-episode", "fixture", "--stage", "monday",
        "--budget-ledger", str(ledger), "--create-budget-ledger",
    ])
    cl.main([
        "bench", "--stage", "monday", "--runs", "1", "--concept", "Fixture",
        "--recipe-context", "Fixture anchor", "--budget-ledger", str(ledger),
    ])
    cl.main([
        "ab", "--stage", "monday", "--runs", "1", "--concept", "Fixture",
        "--recipe-context", "Fixture anchor", "--variant", str(variant),
        "--budget-ledger", str(ledger),
    ])

    saved = _ledger(ledger)
    assert saved["totals"]["generation_attempts"] == 3
    assert [saved["phases"][phase]["generation_attempts"] for phase in ("calibration", "bench", "ab")] == [1, 1, 1]
    assert cl._ACTIVE_BUDGET_GUARD.get() is None
    assert anthropic.Anthropic is sdk_constructor
    assert Messages.create is sdk_create
    assert Messages.count_tokens is sdk_count
    assert fake_sdk["retries"] == [0, 0, 0]


@pytest.mark.parametrize(
    ("role", "model", "stop_reason"),
    [
        ("judge", "anthropic/claude-unsupported-test", "unsupported_judge_model"),
        ("judge", "anthropic/claude-haiku-4-5-20251001", "judge_model_not_allowlisted"),
        ("judge", "bad-model", "invalid_judge_model"),
        ("dialogue", "openai/gpt-5.1", "unsupported_dialogue_provider"),
        ("dialogue", "anthropic/claude-opus-4-6", "dialogue_model_not_allowlisted"),
    ],
)
def test_guarded_bench_latches_invalid_role_models_before_dispatch(
    tmp_path, monkeypatch, fake_sdk, role, model, stop_reason,
):
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-opus-4-6")
    monkeypatch.setenv("DIALOGUE_MODEL" if role == "dialogue" else "JUDGE_MODEL", model)
    monkeypatch.setattr(cl, "cmd_bench", lambda _args: pytest.fail("invalid guarded route reached dispatch"))
    ledger = tmp_path / "ledger.json"

    with pytest.raises(SystemExit, match="request rejected by conversation budget guard"):
        cl.main([
            "bench", "--stage", "monday", "--runs", "1", "--concept", "Fixture",
            "--recipe-context", "Fixture anchor", "--budget-ledger", str(ledger),
            "--create-budget-ledger",
        ])

    stopped = _ledger(ledger)
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == stop_reason
    assert fake_sdk["create"] == []
    assert fake_sdk["count"] == []


def test_guarded_calibrate_preflights_judge_role_before_dispatch(tmp_path, monkeypatch, fake_sdk):
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setattr(cl, "cmd_calibrate", lambda _args: pytest.fail("invalid judge route reached dispatch"))
    ledger = tmp_path / "ledger.json"

    with pytest.raises(SystemExit, match="request rejected by conversation budget guard"):
        cl.main([
            "calibrate", "--from-episode", "fixture", "--stage", "monday",
            "--budget-ledger", str(ledger), "--create-budget-ledger",
        ])

    stopped = _ledger(ledger)
    assert stopped["status"] == "stopped"
    assert stopped["stop_reason"] == "judge_model_not_allowlisted"
    assert fake_sdk["create"] == []
    assert fake_sdk["count"] == []


def test_guarded_bench_prevents_real_production_judge_from_swallowing_bad_route(
    tmp_path, monkeypatch, fake_sdk,
):
    from backend.admin.cron_routes import _judge_dialogue

    assert cl.judge_dialogue is _judge_dialogue
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-unsupported-test")
    generation_calls = []

    def simulation(**kwargs):
        generation_calls.append(kwargs)
        _generate()
        return {"messages": _messages("would-have-been-judged")}

    monkeypatch.setattr(sdw, "run_simulation", simulation)
    with pytest.raises(SystemExit, match="unsupported_judge_model"):
        cl.main([
            "bench", "--stage", "monday", "--runs", "2", "--concept", "Fixture",
            "--recipe-context", "Fixture anchor", "--no-log", "--results-dir", str(tmp_path / "results"),
            "--budget-ledger", str(tmp_path / "ledger.json"), "--create-budget-ledger",
        ])

    assert generation_calls == []
    assert fake_sdk["create"] == []
    assert fake_sdk["count"] == []
    assert _ledger(tmp_path / "ledger.json")["stop_reason"] == "unsupported_judge_model"


def test_guarded_supported_model_roles_pass_actual_sdk_route_with_mock_http(tmp_path, monkeypatch):
    import anthropic

    calls = []
    original_client = anthropic.Anthropic

    def transport(request):
        body = json.loads(request.content)
        calls.append((request.url.path, body))
        if request.url.path.endswith("count_tokens"):
            return httpx.Response(200, json={"input_tokens": 100})
        usage = {"input_tokens": 80, "output_tokens": 4, "service_tier": "standard"}
        if body["model"] == "claude-opus-4-6":
            usage["inference_geo"] = "global"
            text = json.dumps({
                "scores": {dimension: 4 for dimension in (
                    "title_fidelity", "arc_resolution", "voice_distinctiveness",
                    "technical_credibility", "natural_progression", "promise_delivery",
                    "turn_taking", "cast_coverage",
                )},
                "verdict": "PASS", "weakest": [], "reason": "fixture verdict",
            })
        else:
            text = "fixture"
        return httpx.Response(200, json={
            "id": "msg_fixture", "type": "message", "role": "assistant",
            "model": body["model"], "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn", "stop_sequence": None, "usage": usage,
        })

    def mock_http_client(*args, **kwargs):
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(transport))
        return original_client(*args, **kwargs)

    monkeypatch.setattr(anthropic, "Anthropic", mock_http_client)
    monkeypatch.setattr(model_router, "_central_track", lambda *args, **kwargs: None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-opus-4-6")
    ledger = tmp_path / "ledger.json"

    with cl.AnthropicBudgetGuard(ledger, budget_usd=1, create=True) as guard:
        guard.validate_configured_models(
            dialogue_model=os.environ["DIALOGUE_MODEL"], judge_model=os.environ["JUDGE_MODEL"],
        )
        with guard.phase("bench"):
            assert _generate() == "fixture"
            passed, verdict = cl.judge_dialogue(
                "Fixture", "monday", _messages("actual production judge"), {},
            )

    create_calls = [body for path, body in calls if path.endswith("messages")]
    assert [body["model"] for body in create_calls] == [
        "claude-haiku-4-5-20251001", "claude-opus-4-6",
    ]
    assert all(body["service_tier"] == "standard_only" for body in create_calls)
    assert create_calls[1]["inference_geo"] == "global"
    assert passed is True and verdict == "PASS - fixture verdict"
    assert _ledger(ledger)["status"] == "active"


def test_create_without_path_and_resume_missing_ledger_fail_before_dispatch(tmp_path, monkeypatch, fake_sdk):
    monkeypatch.setattr(cl, "cmd_bench", lambda _args: pytest.fail("command ran before guard preflight"))
    with pytest.raises(SystemExit, match="requires --budget-ledger"):
        cl.main(["bench", "--stage", "monday", "--runs", "1", "--create-budget-ledger"])

    with pytest.raises(SystemExit, match="missing"):
        cl.main([
            "bench", "--stage", "monday", "--runs", "1", "--budget-ledger",
            str(tmp_path / "missing.json"),
        ])
    assert fake_sdk["create"] == []
    assert fake_sdk["count"] == []


def test_guarded_reports_include_relative_authoritative_ledger_snapshot(tmp_path, fake_sdk):
    ledger = tmp_path / "ledger.json"
    first = tmp_path / "plain.json"
    second = tmp_path / "atomic.json"
    with cl.AnthropicBudgetGuard(ledger, create=True) as guard:
        token = cl._ACTIVE_BUDGET_GUARD.set(guard)
        try:
            with guard.phase("bench"):
                _generate()
            cl._write_json_result(first, {"command": "bench", "cost_summary": {"total_cost": 0.0}})
            cl._publish_json_atomically(second, {"command": "ab", "cost_summary": {"total_cost": 0.0}})
        finally:
            cl._ACTIVE_BUDGET_GUARD.reset(token)

    expected_ref = os.path.relpath(ledger.resolve(), cl.ROOT)
    for result_path in (first, second):
        report = json.loads(result_path.read_text(encoding="utf-8"))
        entry = report["budget_guard_ledger"]
        assert entry["ledger_ref"] == expected_ref
        assert entry["authoritative"] is True
        assert entry["summary"]["totals"]["actual_microusd"] == 100
        assert "router cost_summary is an estimate" in entry["cost_summary_note"]


def test_swallowed_production_judge_budget_error_saves_aborted_bench_and_stops_calls(tmp_path, monkeypatch, fake_sdk):
    def generated_simulation(**kwargs):
        _generate()
        return {"messages": _messages("generated")}

    def production_judge_that_swallows_guard_error(*_args, **_kwargs):
        try:
            model_router.generate_judge_response(
                prompt="judge prompt", system_prompt="judge rules",
                model="anthropic/claude-opus-4-6", temperature=0.2,
            )
        except Exception:
            return False, "FAIL"
        return True, "PASS"

    monkeypatch.setattr(sdw, "run_simulation", generated_simulation)
    monkeypatch.setattr(cl, "judge_dialogue", production_judge_that_swallows_guard_error)
    results = tmp_path / "results"
    ledger = tmp_path / "ledger.json"

    with pytest.raises(SystemExit, match="partial result"):
        cl.main([
            "bench", "--stage", "monday", "--runs", "2", "--concept", "Fixture",
            "--recipe-context", "Fixture anchor", "--no-log", "--results-dir", str(results),
            "--max-cost", "0.03", "--budget-ledger", str(ledger), "--create-budget-ledger",
        ])

    [result_path] = results.glob("bench-*.json")
    report = json.loads(result_path.read_text(encoding="utf-8"))
    assert report["aborted"] is True
    assert "budget guard stopped" in report["error"]
    assert report["completed_runs"] == 1
    assert "judge" not in report["runs"][0]
    assert report["budget_guard_ledger"]["summary"]["status"] == "stopped"
    assert report["budget_guard_ledger"]["summary"]["stop_reason"] == "budget_exhausted"
    assert len(fake_sdk["create"]) == 1, "denied judge and later run must make no generation request"
    assert cl._ACTIVE_BUDGET_GUARD.get() is None


def test_corrupt_ledger_after_a_paid_generation_still_publishes_transcript_evidence(tmp_path, monkeypatch, fake_sdk):
    ledger = tmp_path / "ledger.json"
    calls = {"runs": 0}
    judge_calls = []

    def corrupt_after_first_paid_generation(**kwargs):
        _generate()
        calls["runs"] += 1
        ledger.write_text("not-json", encoding="utf-8")
        return {"messages": _messages("paid transcript")}

    monkeypatch.setattr(sdw, "run_simulation", corrupt_after_first_paid_generation)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *args, **kwargs: judge_calls.append(1))
    results = tmp_path / "results"

    with pytest.raises(SystemExit):
        cl.main([
            "bench", "--stage", "monday", "--runs", "2", "--concept", "Fixture",
            "--recipe-context", "Fixture anchor", "--no-log", "--results-dir", str(results),
            "--budget-ledger", str(ledger), "--create-budget-ledger",
        ])

    [result_path] = results.glob("bench-*.json")
    report = json.loads(result_path.read_text(encoding="utf-8"))
    assert report["aborted"] is True
    assert report["completed_runs"] == 1
    assert report["runs"][0]["transcript"] == _messages("paid transcript")
    unavailable = report["budget_guard_ledger"]
    assert unavailable["summary"] is None
    assert unavailable["status"] == "unavailable"
    assert "BudgetLedgerError" in unavailable["summary_error"]
    assert judge_calls == [], "a stopped ledger must be checked before production judging"
    assert len(fake_sdk["create"]) == 1
    assert calls["runs"] == 1


def test_ab_first_judge_denial_preserves_both_paid_arms_as_unscored_partial(tmp_path, monkeypatch, fake_sdk):
    report, generated, ledger = _run_guarded_ab_until_judge_stops(tmp_path, monkeypatch, 0.05)

    assert len(generated) == 2
    assert report["completed_pairs"] == 0
    assert report["pairs"] == []
    [partial] = report["partial_pairs"]
    assert partial["status"] == "awaiting_judges"
    assert partial["run_index"] == 1
    assert partial["control_messages"] == _messages("generation-1")
    assert partial["variant_messages"] == _messages("generation-2")
    assert partial["judge_orientations"] == []
    assert [call["model"] for call in fake_sdk["create"]] == [
        "claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001",
    ]
    assert _ledger(ledger)["status"] == "stopped"


def test_ab_second_judge_denial_preserves_first_orientation_separately(tmp_path, monkeypatch, fake_sdk):
    report, generated, ledger = _run_guarded_ab_until_judge_stops(tmp_path, monkeypatch, 0.1375)

    assert len(generated) == 2
    assert report["completed_pairs"] == 0
    assert report["pairs"] == []
    [partial] = report["partial_pairs"]
    assert partial["control_messages"] == _messages("generation-1")
    assert partial["variant_messages"] == _messages("generation-2")
    assert [item["orientation"] for item in partial["judge_orientations"]] == ["control_first"]
    assert partial["judge_orientations"][0]["result"]["overall"] == "tie"
    assert [call["model"] for call in fake_sdk["create"]] == [
        "claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001", "claude-opus-4-6",
    ]
    assert _ledger(ledger)["status"] == "stopped"


def test_testbed_partial_pair_keeps_scenario_provenance_without_aggregating(tmp_path, monkeypatch, fake_sdk):
    scenario = {
        "id": "scenario-1", "concept": "Fixture", "recipe_context": "Fixture anchor",
        "recipe_facts": None, "category": "savory", "cuisine": "test", "source_episode": "fixture",
    }
    monkeypatch.setattr(cl, "_load_testbed", lambda _path: [scenario])
    monkeypatch.setattr(sdw, "run_simulation", lambda **kwargs: (_generate(), {"messages": _messages("testbed")} )[1])
    variant = tmp_path / "variant.json"
    variant.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "fixture variant"}))
    results = tmp_path / "results"
    ledger = tmp_path / "ledger.json"

    with pytest.raises(SystemExit):
        cl.main([
            "ab", "--testbed", str(tmp_path / "testbed.json"), "--stage", "monday",
            "--runs", "1", "--variant", str(variant), "--no-log", "--results-dir", str(results),
            "--max-cost", "0.05", "--budget-ledger", str(ledger), "--create-budget-ledger",
        ])

    [result_path] = results.glob("*.json")
    report = json.loads(result_path.read_text(encoding="utf-8"))
    assert report["completed_pairs"] == 0
    assert report["pairs"] == []
    [partial] = report["partial_pairs"]
    assert partial["scenario_id"] == "scenario-1"
    assert partial["run_index"] == 1
    assert partial["control_messages"] == _messages("testbed")
    assert partial["variant_messages"] == _messages("testbed")
    assert report["scenarios"][0]["completed_pairs"] == 0
    assert report["scenarios"][0]["partial_pairs"] == [partial]
    assert len(fake_sdk["create"]) == 2


def test_sweep_partial_pair_keeps_shared_control_and_variant_transcripts(tmp_path, monkeypatch, fake_sdk):
    scenario = {
        "id": "scenario-1", "concept": "Fixture", "recipe_context": "Fixture anchor",
        "recipe_facts": None, "category": "savory", "cuisine": "test", "source_episode": "fixture",
    }
    monkeypatch.setattr(cl, "_load_testbed", lambda _path: [scenario])
    runs = []

    def simulation(**kwargs):
        _generate()
        runs.append(1)
        return {"messages": _messages(f"sweep-{len(runs)}")}

    monkeypatch.setattr(sdw, "run_simulation", simulation)
    sweep_dir = tmp_path / "variants"
    sweep_dir.mkdir()
    (sweep_dir / "sample.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "fixture variant"}))
    results = tmp_path / "results"
    ledger = tmp_path / "ledger.json"

    with pytest.raises(SystemExit):
        cl.main([
            "ab", "--sweep", str(sweep_dir), "--testbed", str(tmp_path / "testbed.json"),
            "--stage", "monday", "--runs", "1", "--no-log", "--results-dir", str(results),
            "--max-cost", "0.05", "--budget-ledger", str(ledger), "--create-budget-ledger",
        ])

    [result_path] = results.glob("*.json")
    report = json.loads(result_path.read_text(encoding="utf-8"))
    variant_report = report["variants"]["sample"]
    assert variant_report["completed_pairs"] == 0
    assert variant_report["pairs"] == []
    [partial] = variant_report["partial_pairs"]
    assert partial["scenario_id"] == "scenario-1"
    assert partial["control_messages"] == _messages("sweep-1")
    assert partial["variant_messages"] == _messages("sweep-2")
    assert report["unpaired_control_transcripts"] == []
    assert len(runs) == 2 and len(fake_sdk["create"]) == 2


def test_sweep_control_checkpoint_preserves_unpaired_paid_transcript(tmp_path, monkeypatch, fake_sdk):
    ledger = tmp_path / "ledger.json"
    transcripts = {}

    def corrupt_after_generation(*_args, **kwargs):
        _generate()
        ledger.write_text("not-json", encoding="utf-8")
        return {"messages": _messages("shared-control")}

    monkeypatch.setattr(cl, "_run_arm", corrupt_after_generation)
    with cl.AnthropicBudgetGuard(ledger, create=True) as guard:
        token = cl._ACTIVE_BUDGET_GUARD.set(guard)
        try:
            with guard.phase("ab"), pytest.raises(cl.BudgetGuardError):
                cl._generate_sweep_control(
                    scenarios=[{"id": "scenario-1", "concept": "Fixture", "recipe_context": "anchor"}],
                    stage="monday", runs=1, mode="openai", default_model="anthropic/claude-haiku-4-5-20251001",
                    budget=cl.CallBudget(max_calls=40), transcripts=transcripts,
                )
        finally:
            cl._ACTIVE_BUDGET_GUARD.reset(token)

    assert transcripts[("scenario-1", 1)]["messages"] == _messages("shared-control")
    report = cl._build_sweep_report(
        cl._build_parser().parse_args([
            "ab", "--sweep", str(tmp_path), "--stage", "monday", "--no-log",
        ]),
        tmp_path, {}, [{"id": "scenario-1"}], 1, transcripts, False, None,
        cl.CallBudget(max_calls=40), {}, True, tmp_path / "report.json", False,
    )
    assert report["unpaired_control_transcripts"] == [{
        "scenario_id": "scenario-1", "run_index": 1, "messages": _messages("shared-control"),
    }]


def test_calibrate_second_orientation_denial_keeps_first_unscored(tmp_path, monkeypatch, fake_sdk):
    original_create = Messages.create

    def near_reservation_opus_usage(self, **kwargs):
        response = original_create(self, **kwargs)
        if kwargs["model"] == "claude-opus-4-6":
            usage = response.usage.model_dump(exclude_none=True)
            usage["output_tokens"] = 4000
            response.usage = SimpleNamespace(model_dump=lambda exclude_none=True: usage)
        return response

    monkeypatch.setattr(Messages, "create", near_reservation_opus_usage)
    episode = {
        "concept": "Fixture",
        "stages": {"monday": {"dialogue": _messages("calibrate"), "recipe_data": {"title": "Fixture"}}},
    }
    monkeypatch.setattr(cl, "_load_episode", lambda *_args, **_kwargs: episode)
    results = tmp_path / "results"
    ledger = tmp_path / "ledger.json"

    with pytest.raises(SystemExit):
        cl.main([
            "calibrate", "--from-episode", "fixture", "--stage", "monday", "--local",
            "--runs", "1", "--results-dir", str(results), "--max-cost", "0.2",
            "--budget-ledger", str(ledger), "--create-budget-ledger",
        ])

    [result_path] = results.glob("*-calibrate-*.json")
    report = json.loads(result_path.read_text(encoding="utf-8"))
    degradation = report["degradations"][cl._DEGRADATIONS[0][0]]
    assert degradation["pairs"] == []
    assert degradation["completed_pairs"] == degradation["scored_pairs"] == 0
    assert degradation["real_preference_rate"] is None
    assert degradation["verdict"] == "INCOMPLETE - grader readiness unavailable"
    [partial] = degradation["partial_pairs"]
    assert partial["run_index"] == 1
    assert partial["status"] == "awaiting_judges"
    assert [item["orientation"] for item in partial["judge_orientations"]] == ["real_first"]
    assert len(fake_sdk["create"]) == 1
    assert fake_sdk["create"][0]["model"] == "claude-opus-4-6"


def test_ab_structural_arm_reservation_denies_tiny_cap_before_generation(monkeypatch):
    generated = []
    monkeypatch.setattr(cl, "_run_arm_and_count", lambda *args, **kwargs: generated.append(1))
    pairs: list[dict] = []
    aborted = cl._generate_and_judge_pairs(
        concept="Fixture", stage="monday", recipe_context="anchor", runs=1,
        variant={"_SHARED_CHARACTER_RULES": "variant"}, mode="openai", default_model="d",
        judge_model="j", expected_cast=["Margaret Chen"], budget=cl.CallBudget(max_calls=1),
        max_cost=5.0, dry_run=False, pairs=pairs,
    )
    assert aborted is True
    assert generated == []
    assert pairs == []


def test_ab_structural_reservation_allows_one_complete_arm_and_reports_actual_calls(monkeypatch):
    generated = []

    def fake_arm(*_args, **_kwargs):
        generated.append(1)
        return {"messages": _messages(str(len(generated)))}, 1

    monkeypatch.setattr(cl, "_run_arm_and_count", fake_arm)
    budget = cl.CallBudget(max_calls=4 * cl._max_turns_for_stage("monday"))
    pairs: list[dict] = []
    aborted = cl._generate_and_judge_pairs(
        concept="Fixture", stage="monday", recipe_context="anchor", runs=1,
        variant={"_SHARED_CHARACTER_RULES": "variant"}, mode="openai", default_model="d",
        judge_model="j", expected_cast=["Margaret Chen"], budget=budget,
        max_cost=5.0, dry_run=False, pairs=pairs,
    )
    assert aborted is True
    assert len(generated) == 1
    assert budget.used == 1
    assert pairs == []


def test_ab_call_budget_allows_complete_pair_when_structural_allowance_fits(monkeypatch):
    generated = []

    def fake_arm(*_args, **_kwargs):
        generated.append(1)
        return {"messages": _messages(str(len(generated)))}, 1

    tie = {"overall": "tie", **{dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS}}
    monkeypatch.setattr(cl, "_run_arm_and_count", fake_arm)
    monkeypatch.setattr(cl, "_judge_orientation", lambda *args, **kwargs: tie)
    cap = 2 * 4 * cl._max_turns_for_stage("monday") + 2
    budget = cl.CallBudget(max_calls=cap)
    pairs: list[dict] = []
    aborted = cl._generate_and_judge_pairs(
        concept="Fixture", stage="monday", recipe_context="anchor", runs=1,
        variant={"_SHARED_CHARACTER_RULES": "variant"}, mode="openai", default_model="d",
        judge_model="j", expected_cast=["Margaret Chen"], budget=budget,
        max_cost=5.0, dry_run=False, pairs=pairs,
    )
    assert aborted is False
    assert len(generated) == 2
    assert budget.used == 4
    assert len(pairs) == 1


def test_sweep_control_and_variant_use_structural_reservations(monkeypatch):
    generated = []

    def fake_arm(*_args, **_kwargs):
        generated.append(1)
        return {"messages": _messages(str(len(generated)))}, 1

    tie = {"overall": "tie", **{dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS}}
    monkeypatch.setattr(cl, "_run_arm_and_count", fake_arm)
    monkeypatch.setattr(cl, "_judge_orientation", lambda *args, **kwargs: tie)
    scenarios = [{"id": "s1", "concept": "Fixture", "recipe_context": "anchor"}]
    denied_control = cl.CallBudget(max_calls=1)
    transcripts = {}
    assert cl._generate_sweep_control(
        scenarios=scenarios, stage="monday", runs=1, mode="openai", default_model="d",
        budget=denied_control, transcripts=transcripts,
    ) is True
    assert generated == []

    allowed_control = cl.CallBudget(max_calls=4 * cl._max_turns_for_stage("monday"))
    assert cl._generate_sweep_control(
        scenarios=scenarios, stage="monday", runs=1, mode="openai", default_model="d",
        budget=allowed_control, transcripts=transcripts,
    ) is False
    assert len(generated) == 1
    assert allowed_control.used == 1

    denied, calls, _ = cl._run_sweep_variant(
        variant={"_SHARED_CHARACTER_RULES": "variant"}, scenarios=scenarios,
        stage="monday", runs=1, mode="openai", default_model="d", judge_model="j",
        expected_cast=["Margaret Chen"], control_transcripts=transcripts,
        max_calls=1, max_cost=5.0, dry_run=False, pairs=[],
    )
    assert denied is True and calls == 0
    assert len(generated) == 1

    pairs: list[dict] = []
    denied, calls, _ = cl._run_sweep_variant(
        variant={"_SHARED_CHARACTER_RULES": "variant"}, scenarios=scenarios,
        stage="monday", runs=1, mode="openai", default_model="d", judge_model="j",
        expected_cast=["Margaret Chen"], control_transcripts=transcripts,
        max_calls=4 * cl._max_turns_for_stage("monday"), max_cost=5.0,
        dry_run=False, pairs=pairs,
    )
    assert denied is False and calls == 3
    assert len(generated) == 2
    assert len(pairs) == 1


def test_ab_dry_run_has_zero_call_reservations_even_with_tiny_call_cap(monkeypatch):
    generated = []
    monkeypatch.setattr(
        cl, "_run_arm_and_count",
        lambda *args, **kwargs: (generated.append(1) or {"messages": _messages()}, 2),
    )
    pairs: list[dict] = []
    budget = cl.CallBudget(max_calls=1)
    aborted = cl._generate_and_judge_pairs(
        concept="Fixture", stage="monday", recipe_context="anchor", runs=1,
        variant={"_SHARED_CHARACTER_RULES": "variant"}, mode="template", default_model="template",
        judge_model="template", expected_cast=["Margaret Chen"], budget=budget,
        max_cost=5.0, dry_run=True, pairs=pairs,
    )
    assert aborted is False
    assert len(generated) == 2
    assert budget.used == 0
    assert len(pairs) == 1
