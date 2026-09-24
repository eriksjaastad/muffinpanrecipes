"""Offline validation and guarded scoring for the frozen voice reference panel."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.conversation_lab as cl
from backend.admin.cron_routes import _JUDGE_SYSTEM_PROMPT
from backend.utils import model_router


PANEL = Path(cl.__file__).resolve().parents[1] / "docs/conversation-lab/reference/voice-reference-panel-v0.json"


def _tie_response() -> str:
    return json.dumps({
        "winner": "tie",
        "per_dimension": {dimension: "tie" for dimension in cl.ALL_JUDGE_DIMENSIONS},
        "reason": "synthetic test response",
    })


def test_reference_panel_dry_run_validates_controls_without_judge_calls(tmp_path, monkeypatch):
    def no_judge(**_kwargs):
        pytest.fail("reference panel dry-run must not invoke a judge")

    monkeypatch.setattr(model_router, "generate_judge_response", no_judge)
    results_dir = tmp_path / "results"
    cl.main([
        "calibrate", "--reference-panel", str(PANEL), "--runs", "2", "--dry-run",
        "--results-dir", str(results_dir),
    ])

    [result_path] = results_dir.glob("*-calibrate-reference-panel-v0.json")
    report = json.loads(result_path.read_text())
    assert report["dry_run"] is True
    assert report["planned_pairs"] == 8
    assert report["planned_judge_orientations"] == 0
    assert report["panel_sha256"]
    assert set(report["source_sha256"]) == {"W25 Monday", "W38 Wednesday"}
    assert len(report["validation_checks"]) == 5
    assert set(report["cases"]) == {
        "identical-pair", "consistent-name-permutation",
        "one-turn-each-label-exchange", "w38-agreement-heavy-manual-variant",
    }
    assert "unanimous ties" in report["proposed_acceptance_plan"]["identical-pair"]["proposed_acceptance"]
    assert "diagnostic only" in report["proposed_acceptance_plan"]["consistent-name-permutation"]["proposed_acceptance"]
    assert report["evidence_summary"]["valid_response_rate"] is None


def test_reference_report_hashes_loaded_snapshot_and_uses_unique_result_names(tmp_path, monkeypatch):
    panel_path = tmp_path / "panel.json"
    original = PANEL.read_bytes()
    panel_path.write_bytes(original)
    expected_hash = hashlib.sha256(original).hexdigest()
    base_dry_run = cl._dry_run_combined
    changed = False

    def mutate_after_load():
        nonlocal changed
        if not changed:
            panel_path.write_text("mutated after load", encoding="utf-8")
            changed = True
        return base_dry_run()

    monkeypatch.setattr(cl, "_dry_run_combined", mutate_after_load)
    results_dir = tmp_path / "results"
    command = [
        "calibrate", "--reference-panel", str(panel_path), "--runs", "1", "--dry-run",
        "--results-dir", str(results_dir),
    ]
    cl.main(command)
    [first_path] = results_dir.glob("*-calibrate-reference-panel-v0.json")
    first_report = json.loads(first_path.read_text())
    assert first_report["panel_sha256"] == expected_hash
    assert hashlib.sha256(panel_path.read_bytes()).hexdigest() != expected_hash

    # Restore the fixture so the second invocation validates the same input.
    panel_path.write_bytes(original)
    monkeypatch.setattr(cl, "_dry_run_combined", base_dry_run)
    cl.main(command)
    result_paths = list(results_dir.glob("*-calibrate-reference-panel-v0.json"))
    assert len(result_paths) == 2
    assert len({path.name for path in result_paths}) == 2


def test_reference_panel_validator_rejects_changed_content_control(tmp_path):
    panel = json.loads(PANEL.read_text())
    identical = next(case for case in panel["cases"] if case["id"] == "identical-pair")
    identical["inputs"]["B"][0]["text"] += " changed"
    path = tmp_path / "changed-panel.json"
    path.write_text(json.dumps(panel))

    with pytest.raises(SystemExit, match="identical-pair A and B differ"):
        cl._load_reference_panel(path)


def test_reference_panel_rejects_zero_runs_before_writing_or_judging(tmp_path, monkeypatch):
    monkeypatch.setattr(
        model_router, "generate_judge_response",
        lambda **_kwargs: pytest.fail("zero-run reference calibration must not judge"),
    )
    results_dir = tmp_path / "results"
    with pytest.raises(SystemExit, match="--runs must be at least 1"):
        cl.main([
            "calibrate", "--reference-panel", str(PANEL), "--runs", "0",
            "--results-dir", str(results_dir),
        ])
    assert not results_dir.exists()


def test_reference_panel_rejects_local_episode_flag(tmp_path):
    results_dir = tmp_path / "results"
    with pytest.raises(SystemExit, match="--local is only valid with --from-episode"):
        cl.main([
            "calibrate", "--reference-panel", str(PANEL), "--local", "--dry-run",
            "--results-dir", str(results_dir),
        ])
    assert not results_dir.exists()


def test_lab_prompt_copies_production_character_rules_exactly():
    block = (
        "CHARACTER RULES:\n"
        "- Margaret: Blunt, short sentences, zero fluff, standards enforcer\n"
        "- Steph: Warm, diplomatic, NOT a nervous intern\n"
        "- Julian: Visual thinker, theatrical, cares about light/composition\n"
        "- Marcus: Literary, verbose, metaphor-heavy\n"
        "- Devon: Efficient, understated, speaks only when needed\n"
        "- Ria: Direct, platform-savvy, thinks in hooks and engagement, impatient with process\n"
    )
    assert block in _JUDGE_SYSTEM_PROMPT
    assert block in cl.PAIRWISE_JUDGE_SYSTEM_PROMPT


def test_all_ab_report_modes_record_pairwise_evaluator_version_and_hash(tmp_path):
    args = SimpleNamespace(
        concept="Fixture", stage="monday", runs=1, max_calls=10, max_cost=5.0,
        dry_run=True, target="overall", testbed=tmp_path / "panel.json",
    )
    budget = cl.CallBudget(max_calls=10)
    reports = [
        cl._build_ab_report(args, tmp_path / "variant.json", {}, [], False, budget, tmp_path / "single.json"),
        cl._build_testbed_ab_report(
            args, tmp_path / "variant.json", {}, [], [], False, budget,
            tmp_path / "testbed.json", False,
        ),
        cl._build_sweep_report(
            args, tmp_path, {}, [], 1, {}, False, None, budget, {}, False,
            tmp_path / "sweep.json", False,
        ),
    ]
    expected = cl._pairwise_evaluator_metadata()
    for report in reports:
        assert report["evaluator_prompt_version"] == expected["evaluator_prompt_version"]
        assert report["evaluator_prompt_sha256"] == expected["evaluator_prompt_sha256"]


def test_reference_panel_paid_path_uses_versioned_prompt_and_swapped_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "test-reference-judge")
    calls = []

    def judge(**kwargs):
        calls.append(kwargs)
        return _tie_response()

    monkeypatch.setattr(model_router, "generate_judge_response", judge)
    results_dir = tmp_path / "results"
    cl.main([
        "calibrate", "--reference-panel", str(PANEL), "--runs", "1",
        "--results-dir", str(results_dir),
    ])

    [result_path] = results_dir.glob("*-calibrate-reference-panel-v0.json")
    report = json.loads(result_path.read_text())
    assert report["calls_used"] == 8
    assert len(calls) == 8
    assert report["evaluator"]["prompt_version"] == cl.PAIRWISE_JUDGE_PROMPT_VERSION
    assert report["evaluator"]["prompt_sha256"]
    assert "Versioned v2" in report["evaluator"]["prompt_note"]
    identical = report["cases"]["identical-pair"]["completed_pairs"][0]
    assert [item["orientation"] for item in identical["judge_orientations"]] == ["left_first", "right_first"]
    assert all(item["evidence"]["raw_response"] == _tie_response() for item in identical["judge_orientations"])
    first, second = calls[:2]
    assert "Expected cast for reference scene: Margaret Chen, Ria Castillo" in first["prompt"]
    assert "Recipe concept: Weekly Muffin Pan Recipe" in first["prompt"]
    assert "This week's recipe: Harissa Chickpea Feta Cups (breakfast)." in first["prompt"]
    assert "Evaluation target for this comparison" not in first["prompt"]
    assert first["system_prompt"] == second["system_prompt"] == cl.PAIRWISE_JUDGE_SYSTEM_PROMPT
    assert "- Margaret: Blunt, short sentences, zero fluff, standards enforcer" in first["system_prompt"]
    assert "- Ria: Direct, platform-savvy, thinks in hooks and engagement, impatient with process" in first["system_prompt"]
    assert "Transcript A and Transcript B are two candidate versions of the same scene." in first["system_prompt"]
    assert first["prompt"].index("TRANSCRIPT A") < first["prompt"].index("TRANSCRIPT B")
    assert second["prompt"].index("TRANSCRIPT A") < second["prompt"].index("TRANSCRIPT B")
    assert report["evidence_summary"]["valid_response_rate"] == 1.0
    assert report["evidence_summary"]["attempted_valid_response_rate"] == 1.0
    assert report["evidence_summary"]["order_agreement_by_case_and_dimension"]["identical-pair"]["overall"]["rate"] == 1.0
    assert report["evidence_summary"]["repetition_stability_by_case_and_dimension"]["identical-pair"]["overall"]["stable"] is None
    assert report["evidence_summary"]["repetition_stability_by_case_and_dimension"]["identical-pair"]["overall"]["runs_with_result"] == 1
    assert report["cases"]["w38-agreement-heavy-manual-variant"]["concept"] == "Cinnamon Roll Spiral Bites"


def test_reference_panel_abort_keeps_first_orientation_as_partial_evidence(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JUDGE_MODEL", "test-reference-judge")
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_kwargs: _tie_response())
    results_dir = tmp_path / "results"
    cl.main([
        "calibrate", "--reference-panel", str(PANEL), "--runs", "1", "--max-calls", "1",
        "--results-dir", str(results_dir),
    ])

    [result_path] = results_dir.glob("*-calibrate-reference-panel-v0.json")
    report = json.loads(result_path.read_text())
    assert "status: ABORTED" in capsys.readouterr().out
    assert report["aborted"] is True
    assert report["calls_used"] == 1
    assert report["evidence_summary"]["attempted_valid_response_rate"] == 1.0
    assert report["evidence_summary"]["planned_orientation_count"] == 8
    assert report["evidence_summary"]["planned_orientation_coverage"] == 0.125
    info = report["cases"]["identical-pair"]
    assert info["completed_pairs"] == []
    [partial] = info["partial_pairs"]
    assert [item["orientation"] for item in partial["judge_orientations"]] == ["left_first"]
    assert partial["judge_orientations"][0]["evidence"]["raw_response"] == _tie_response()


def test_reference_panel_coverage_counts_attempted_malformed_orientation(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "test-reference-judge")
    malformed = '{"winner":"A","per_dimension":{}}'
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_kwargs: malformed)
    results_dir = tmp_path / "results"
    with pytest.raises(SystemExit, match="missing per_dimension"):
        cl.main([
            "calibrate", "--reference-panel", str(PANEL), "--runs", "1", "--max-calls", "1",
            "--results-dir", str(results_dir),
        ])

    [result_path] = results_dir.glob("*-calibrate-reference-panel-v0.json")
    report = json.loads(result_path.read_text())
    evidence = report["evidence_summary"]
    assert report["aborted"] is True
    assert report["calls_used"] == 1
    assert evidence["planned_orientation_count"] == 8
    assert evidence["attempted_orientations"] == 1
    assert evidence["valid_responses"] == 0
    assert evidence["attempted_valid_response_rate"] == 0.0
    assert evidence["planned_orientation_coverage"] == 0.125
    [partial] = report["cases"]["identical-pair"]["partial_pairs"]
    [orientation] = partial["judge_orientations"]
    assert orientation["status"] == "invoked"
    assert "missing per_dimension" in orientation["error"]
    assert orientation["evidence"]["raw_response"] == malformed
