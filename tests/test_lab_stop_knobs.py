"""Hermetic tests for the standalone stop-check utility and lab reporting."""

from __future__ import annotations

import json

import httpx
import pytest

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw
from backend.utils import model_router
from backend.utils import stop_check as stop_check_module
from backend.utils.stop_check import (
    HAIKU_MODEL,
    StopCheckError,
    check_scene_done,
)

# ---------------------------------------------------------------------------
# backend.utils.stop_check
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


_JEV_VERIFIED_RESPONSE = {
    "model": "typesafe/jev-1.13-20260917",
    "answers": {
        "decided": {"type": "noul", "noul": 0.95},
        "pushback": {"type": "noul", "noul": 0.17},
    },
    "usage": {"input_tokens": 316, "output_tokens": 38, "cost": 0.000013272},
}


def test_jev_request_shape_and_parse(monkeypatch):
    captured: dict = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _FakeResponse(payload=_JEV_VERIFIED_RESPONSE)

    monkeypatch.setattr("backend.utils.stop_check.httpx.post", fake_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    result = check_scene_done(
        lines=["Margaret: I like this one.", "Steph: Let's do it."],
        provider="jev",
        day="monday",
        objective="Decide which concept the team is making this week",
    )

    assert captured["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert captured["headers"] == {"Authorization": "Bearer test-openrouter-key"}
    assert captured["json"]["model"] == "typesafe/jev-1.13"
    assert captured["json"]["state"] == "Margaret: I like this one.\nSteph: Let's do it."
    questions = captured["json"]["questions"]
    assert questions["decided"]["type"] == "noul"
    assert "Decide which concept the team is making this week" in questions["decided"]["instructions"]
    assert "disagree or push back" in questions["pushback"]["instructions"]
    assert captured["timeout"] == 30.0

    assert result.decided == 0.95
    assert result.pushback == 0.17
    assert result.provider == "jev"
    assert result.model == "typesafe/jev-1.13-20260917"
    assert result.cost_usd == 0.000013272


@pytest.mark.parametrize(
    "payload",
    [
        {"answers": _JEV_VERIFIED_RESPONSE["answers"]},  # missing model
        {"model": "typesafe/jev-1.13-20260917"},  # missing answers
        {"model": "typesafe/jev-1.13-20260917", "answers": {"decided": {"type": "noul", "noul": 0.9}}},  # missing pushback
        {"model": "typesafe/jev-1.13-20260917", "answers": {"decided": {"type": "noul", "noul": 0.9}, "pushback": {"type": "noul"}}},  # missing noul
    ],
)
def test_jev_missing_keys_raise(monkeypatch, payload):
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: _FakeResponse(payload=payload))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


def test_jev_persistent_5xx_raises_after_bounded_retries(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: calls.append(1) or _FakeResponse(payload={}, status_code=500))
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="500"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")
    assert len(calls) == 3  # 1 try + 2 retries, never more


def test_jev_transient_520_is_retried_then_succeeds(monkeypatch):
    """2026-09-27: one HTTP 520 aborted a four-arm sweep."""
    responses = iter([
        _FakeResponse(payload={}, status_code=520),
        _FakeResponse(payload=_JEV_VERIFIED_RESPONSE),
    ])
    monkeypatch.setattr("backend.utils.stop_check.httpx.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    result = check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")
    assert result.decided == 0.95


def test_jev_client_error_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: calls.append(1) or _FakeResponse(payload={}, status_code=402))
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="402"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")
    assert len(calls) == 1


def test_jev_repeated_timeouts_raise(monkeypatch):
    import httpx

    def timeout(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("backend.utils.stop_check.httpx.post", timeout)
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="after 3 attempts"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


def test_jev_malformed_json_raises(monkeypatch):
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: _FakeResponse(payload=ValueError("not json")))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="malformed JSON"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


def test_jev_timeout_raises(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("backend.utils.stop_check.httpx.post", timeout)
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    with pytest.raises(StopCheckError, match="timed out"):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")


# ---------------------------------------------------------------------------
# #7714 finding 2: every Jev HTTP attempt - success, retry, or terminal
# failure - counts as a call against --max-calls and costs against
# --max-cost, by feeding backend.utils.model_router's cost log via
# record_external_cost(). Previously these attempts never reached the
# router's call/cost log at all.
# ---------------------------------------------------------------------------

def test_jev_success_records_one_call_at_its_real_cost(monkeypatch):
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: _FakeResponse(payload=_JEV_VERIFIED_RESPONSE))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")

    entries = model_router.get_cost_entries()
    assert len(entries) == 1
    assert entries[0]["provider"] == "jev"
    assert entries[0]["actual_cost"] == 0.000013272
    assert model_router.get_cost_summary()["total_calls"] == 1


def test_jev_retried_failure_then_success_records_both_attempts(monkeypatch):
    """A 520 that gets retried, then succeeds, must charge for BOTH HTTP
    round trips - the failed one at the documented conservative estimate
    (it reports no real cost), the successful one at its real usage.cost -
    not just the one that finally returned a usable answer."""
    responses = iter([
        _FakeResponse(payload={}, status_code=520),
        _FakeResponse(payload=_JEV_VERIFIED_RESPONSE),
    ])
    monkeypatch.setattr("backend.utils.stop_check.httpx.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")

    entries = model_router.get_cost_entries()
    assert len(entries) == 2
    assert model_router.get_cost_summary()["total_calls"] == 2
    costs = {e["actual_cost"] for e in entries}
    assert costs == {stop_check_module._JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD, 0.000013272}


def test_reported_jev_cost_includes_a_retried_and_failed_attempt(monkeypatch):
    """#7714 round 2, finding 4: the REPORTED jev_cost_usd (cl._jev_cost_
    usd_total, sourced from the router ledger) must include the retried
    520's conservative-estimate charge, not just the eventual success's
    real cost - the old per-pair STOP_CHECK_LOG-derived total only ever
    saw the last successful check per tick, so it silently dropped the
    failed attempt even though --max-cost had already been charged for
    it."""
    responses = iter([
        _FakeResponse(payload={}, status_code=520),
        _FakeResponse(payload=_JEV_VERIFIED_RESPONSE),
    ])
    monkeypatch.setattr("backend.utils.stop_check.httpx.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")

    expected_total = round(stop_check_module._JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD + 0.000013272, 9)
    assert cl._jev_cost_usd_total() == expected_total
    # The reported total must equal what the cap actually saw - both draw
    # from the SAME ledger via get_cost_entries()/_lab_cost_total.
    assert cl._lab_cost_total() == pytest.approx(expected_total)


def test_jev_persistent_failure_charges_every_attempt_before_raising(monkeypatch):
    """A stop check that never succeeds still made _JEV_MAX_ATTEMPTS real
    HTTP round trips - each one must be charged, not just recorded as a
    single opaque failure (#7714 finding 2: 'failed checks leave no
    record')."""
    monkeypatch.setattr("backend.utils.stop_check.httpx.post",
                        lambda *a, **k: _FakeResponse(payload={}, status_code=500))
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    with pytest.raises(StopCheckError):
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")

    entries = model_router.get_cost_entries()
    assert len(entries) == stop_check_module._JEV_MAX_ATTEMPTS
    assert model_router.get_cost_summary()["total_calls"] == stop_check_module._JEV_MAX_ATTEMPTS
    assert all(e["actual_cost"] == stop_check_module._JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD for e in entries)


def test_jev_pre_attempt_hook_fires_once_per_attempt_including_retries(monkeypatch):
    calls = []
    responses = iter([
        _FakeResponse(payload={}, status_code=520),
        _FakeResponse(payload=_JEV_VERIFIED_RESPONSE),
    ])
    monkeypatch.setattr("backend.utils.stop_check.httpx.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("backend.utils.stop_check._sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    previous = stop_check_module.set_pre_attempt_hook(lambda: calls.append(1))
    try:
        check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")
    finally:
        stop_check_module.set_pre_attempt_hook(previous)

    assert len(calls) == 2  # one per HTTP attempt, including the retried 520


def test_jev_pre_attempt_hook_can_abort_before_any_http_call(monkeypatch):
    """A hook that raises must stop the attempt BEFORE the network call -
    this is the seam scripts/conversation_lab.py's mid-arm budget guard
    uses to abort inside a running stop check."""
    class _Abort(RuntimeError):
        pass

    def exploding_post(*a, **k):
        raise AssertionError("must not reach the network once the hook aborts")

    monkeypatch.setattr("backend.utils.stop_check.httpx.post", exploding_post)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")

    previous = stop_check_module.set_pre_attempt_hook(lambda: (_ for _ in ()).throw(_Abort()))
    try:
        with pytest.raises(_Abort):
            check_scene_done(lines=["a"], provider="jev", day="monday", objective="Pick it")
    finally:
        stop_check_module.set_pre_attempt_hook(previous)

    assert model_router.get_cost_entries() == []


def test_haiku_parse(monkeypatch):
    captured: dict = {}

    def fake_generate_response(*, prompt, system_prompt, model, temperature):
        captured["prompt"] = prompt
        captured["system_prompt"] = system_prompt
        captured["model"] = model
        captured["temperature"] = temperature
        return '{"decided": 0.9, "pushback": 0.4}'

    monkeypatch.setattr("backend.utils.stop_check.generate_response", fake_generate_response)

    result = check_scene_done(
        lines=["Margaret: yes", "Steph: agreed"],
        provider="haiku",
        day="tuesday",
        objective="Nail down the recipe",
    )

    assert result.decided == 0.9
    assert result.pushback == 0.4
    assert result.provider == "haiku"
    assert result.model == HAIKU_MODEL
    assert result.cost_usd is None
    assert captured["model"] == HAIKU_MODEL
    assert "Nail down the recipe" in captured["prompt"]
    assert "STRICT JSON" in captured["prompt"]
    assert "Margaret: yes" in captured["prompt"]


@pytest.mark.parametrize("raw", ["not json at all", '{"decided": 0.9}', '{"decided": 0.9, "pushback": "yes"}'])
def test_haiku_malformed_raises(monkeypatch, raw):
    monkeypatch.setattr("backend.utils.stop_check.generate_response", lambda **kwargs: raw)
    with pytest.raises(StopCheckError):
        check_scene_done(lines=["a"], provider="haiku", day="monday", objective="Pick it")


def test_unknown_provider_raises():
    with pytest.raises(StopCheckError, match="unknown stop check provider"):
        check_scene_done(lines=["a"], provider="oracle", day="monday", objective="Pick it")


def _fake_messages(tag: str) -> list[dict]:
    return [
        {"day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
         "message": f"{tag} line {i}", "timestamp": "", "model": "test", "attachments": []}
        for i in range(2)
    ]


def test_rewrite_guards_validation_accepts_subset_and_apply_merges_default():
    before = sdw.REWRITE_GUARDS
    cl.validate_variant(sdw, {"REWRITE_GUARDS": {"repetition": False}})
    original = cl._apply_variant(sdw, {"REWRITE_GUARDS": {"repetition": False}})
    try:
        assert sdw.REWRITE_GUARDS == {"repetition": False, "shape": True, "word_budget": True}
    finally:
        cl._restore_variant(sdw, original)
    assert sdw.REWRITE_GUARDS == before


def test_rewrite_summary_counts_lines_rewrites_faults_and_cot_retries():
    entries = [
        {"faults": ["repetition", "shape:dash_clause"], "rewritten": True, "cot_retry": True},
        {"faults": ["word_budget"], "rewritten": True, "cot_retry": False},
        {"faults": [], "rewritten": False, "cot_retry": False},
    ]
    summary = cl._rewrite_summary(entries)
    assert summary["lines"] == 3
    assert summary["rewritten"] == 2
    assert summary["rewrite_rate"] == round(2 / 3, 4)
    assert summary["fault_counts"] == {
        "repetition": 1,
        "shape:dash_clause": 1,
        "word_budget": 1,
    }
    assert summary["cot_retry"] == 1


def test_ab_result_carries_rewrite_logs_and_rewrite_summary(tmp_path, monkeypatch):
    """Each arm's REWRITE_LOG is copied into the pair JSON next to the stop-check
    and director logs, and the report carries a per-arm rewrite_summary."""
    def fake_run_simulation(*, default_model, **kwargs):
        arm = "variant" if sdw._SHARED_CHARACTER_RULES == "VARIANT_RULES" else "control"
        sdw.REWRITE_LOG[:] = [
            {"day": "monday", "speaker": "Margaret Chen", "faults": ["repetition"],
             "rewritten": True, "draft_words": 12, "final_words": 9,
             "draft": "draft text", "cot_retry": True},
            {"day": "monday", "speaker": "Steph", "faults": [],
             "rewritten": False, "draft_words": 8, "final_words": 8,
             "draft": "clean", "cot_retry": False},
        ]
        if arm == "variant":
            sdw.REWRITE_LOG.append(
                {"day": "monday", "speaker": "Julian", "faults": ["word_budget"],
                 "rewritten": True, "draft_words": 25, "final_words": 12,
                 "draft": "too long", "cot_retry": False}
            )
        return {"messages": _fake_messages(arm)}

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)

    variant_path = tmp_path / "variant.json"
    variant_path.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT_RULES"}))
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())

    pair = report["pairs"][0]
    assert [e["speaker"] for e in pair["control_rewrite_log"]] == ["Margaret Chen", "Steph"]
    assert [e["speaker"] for e in pair["variant_rewrite_log"]] == ["Margaret Chen", "Steph", "Julian"]
    assert report["rewrite_summary"]["control"] == {
        "lines": 2,
        "rewritten": 1,
        "rewrite_rate": 0.5,
        "fault_counts": {"repetition": 1},
        "cot_retry": 1,
    }
    assert report["rewrite_summary"]["variant"] == {
        "lines": 3,
        "rewritten": 2,
        "rewrite_rate": round(2 / 3, 4),
        "fault_counts": {"repetition": 1, "word_budget": 1},
        "cot_retry": 1,
    }
