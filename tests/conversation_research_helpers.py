"""Shared network-free builders for conversation research contract tests."""

from __future__ import annotations

import hashlib
import json

import scripts.conversation_lab as cl
from backend.utils import model_router


def fake_pair(
    run_index: int = 1,
    scenario_id: str | None = None,
    overall: str = "control",
    control_text: str = "Control line for the fixture pair.",
    variant_text: str = "Variant line for the fixture pair.",
) -> dict:
    dims_tie = {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS}
    control_messages = [{
        "day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
        "message": control_text, "timestamp": "2026-09-30T09:00:00+00:00",
        "model": "openrouter/anthropic/claude-haiku-4.5", "attachments": [],
    }]
    variant_messages = [{
        "day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
        "message": variant_text, "timestamp": "2026-09-30T09:00:00+00:00",
        "model": "openrouter/anthropic/claude-haiku-4.5", "attachments": [],
    }]
    orientation_control_first = {
        "orientation": "control_first", "status": "invoked",
        "evidence": {
            "prompt": f"PROMPT control_first fixture pair {run_index}",
            "system_prompt": cl.PAIRWISE_JUDGE_SYSTEM_PROMPT,
            "model": "openrouter/anthropic/claude-opus-5.5", "temperature": 0.2,
            "judge_instrument": cl._judge_instrument("openrouter/anthropic/claude-opus-5.5"),
            "mapping": {"A": "control", "B": "variant", "tie": "tie"},
            "first_arm": "control", "second_arm": "variant",
        },
        "result": {"overall": overall, **dims_tie},
    }
    orientation_variant_first = {
        "orientation": "variant_first", "status": "invoked",
        "evidence": {
            "prompt": f"PROMPT variant_first fixture pair {run_index}",
            "system_prompt": cl.PAIRWISE_JUDGE_SYSTEM_PROMPT,
            "model": "openrouter/anthropic/claude-opus-5.5", "temperature": 0.2,
            "judge_instrument": cl._judge_instrument("openrouter/anthropic/claude-opus-5.5"),
            "mapping": {"A": "variant", "B": "control", "tie": "tie"},
            "first_arm": "variant", "second_arm": "control",
        },
        "result": {"overall": overall, **dims_tie},
    }
    pair = {
        "run_index": run_index,
        "control_messages": control_messages,
        "variant_messages": variant_messages,
        "control_summary": {"length_mean_words": 5.0},
        "variant_summary": {"length_mean_words": 5.0},
        "judge": {"overall": overall, **dims_tie},
        "judge_orientations": [orientation_control_first, orientation_variant_first],
        "dry_run": False,
    }
    if scenario_id is not None:
        pair["scenario_id"] = scenario_id
    return pair


def fake_ab_result(
    tmp_path,
    *,
    pairs=None,
    aborted=False,
    partial_pairs=None,
    dry_run=False,
    mode="single",
    scenarios=None,
    command="ab",
    stage="monday",
    concept="Test Muffins",
    filename="source.json",
):
    pairs = pairs if pairs is not None else [fake_pair()]
    data = {
        "command": command,
        "mode": mode,
        "evaluator_prompt_version": cl.PAIRWISE_JUDGE_PROMPT_VERSION,
        "evaluator_prompt_sha256": hashlib.sha256(
            cl.PAIRWISE_JUDGE_SYSTEM_PROMPT.encode("utf-8")
        ).hexdigest(),
        "concept": concept,
        "stage": stage,
        "target_dimension": "turn_taking",
        "aborted": aborted,
        "dry_run": dry_run,
        "partial_pairs": partial_pairs or [],
        "pairs": pairs,
    }
    if scenarios is not None:
        data["scenarios"] = scenarios
    path = tmp_path / filename
    path.write_text(json.dumps(data))
    return path


def mock_openrouter_preflight(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(cl, "_openrouter_fetch_account_balance", lambda: 100.0)
    monkeypatch.setattr(
        cl, "_openrouter_fetch_key",
        lambda: {"limit": 10.0, "limit_remaining": 10.0, "usage": 0.0},
    )
    monkeypatch.setattr(
        cl, "_fetch_openrouter_model_prices",
        lambda: {
            "anthropic/claude-haiku-4.5": (0.0000008, 0.000004),
            "anthropic/claude-opus-4.6": (0.000005, 0.000025),
            "anthropic/claude-opus-5.5": (0.000004, 0.00002),
            "deepseek/deepseek-v4.1-flash": (0.0000002, 0.0000008),
        },
    )


def no_judge_calls(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("rejudge must refuse before making any judge call")
    monkeypatch.setattr(model_router, "generate_judge_response", fail)
