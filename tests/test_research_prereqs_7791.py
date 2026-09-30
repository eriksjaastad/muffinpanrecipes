"""Tests for card #7791 (RESEARCH_PLAN.md section 3.2 prerequisites P1-P4):

- P1: `calibrate --models NAME`
- P2: `rejudge RESULT.json --models NAME` (re-judge saved transcripts, no regeneration)
- P3: `scripts/lab_offline_metrics.py` (zero-API-call offline metrics + V4)
- P4: the SPEAKERS_SEE_JUDGE_RECIPE_FACTS variant lever (R3)

P0 (scripts/lab_models.json's claude-o55/deepseek-o55 sets and the generic
per-vendor routing/pricing/max-tokens machinery they rely on) is covered in
tests/test_lab_models.py, next to the rest of the lab-models-set tests.

Every test here is network-free: OPENROUTER_API_KEY is a fixture string, the
OpenRouter key/balance/price endpoints and `generate_judge_response`/
`generate_response` are monkeypatched, and `scripts/lab_offline_metrics.py`
makes no API calls at all by construction (it only reads saved JSON).
"""

from __future__ import annotations

import hashlib
import json

import pytest

import scripts.conversation_lab as cl
import scripts.lab_offline_metrics as lom
import scripts.simulate_dialogue_week as sdw
from backend.utils import model_router


# ---------------------------------------------------------------------------
# Shared fixtures: a synthetic `ab` result file, small and hand-checkable.
# ---------------------------------------------------------------------------

def _fake_pair(
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
        "orientation": "control_first",
        "status": "invoked",
        "evidence": {
            "prompt": f"PROMPT control_first fixture pair {run_index}",
            "system_prompt": cl.PAIRWISE_JUDGE_SYSTEM_PROMPT,
            "model": "openrouter/anthropic/claude-opus-5.5",
            "temperature": 0.2,
            "mapping": {"A": "control", "B": "variant", "tie": "tie"},
            "first_arm": "control",
            "second_arm": "variant",
        },
        "result": {"overall": overall, **dims_tie},
    }
    orientation_variant_first = {
        "orientation": "variant_first",
        "status": "invoked",
        "evidence": {
            "prompt": f"PROMPT variant_first fixture pair {run_index}",
            "system_prompt": cl.PAIRWISE_JUDGE_SYSTEM_PROMPT,
            "model": "openrouter/anthropic/claude-opus-5.5",
            "temperature": 0.2,
            "mapping": {"A": "variant", "B": "control", "tie": "tie"},
            "first_arm": "variant",
            "second_arm": "control",
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


def _fake_ab_result(
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
    pairs = pairs if pairs is not None else [_fake_pair()]
    data = {
        "command": command,
        "mode": mode,
        "evaluator_prompt_version": cl.PAIRWISE_JUDGE_PROMPT_VERSION,
        "evaluator_prompt_sha256": hashlib.sha256(cl.PAIRWISE_JUDGE_SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
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


def _mock_openrouter_preflight(monkeypatch):
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


# ---------------------------------------------------------------------------
# P1: calibrate --models
# ---------------------------------------------------------------------------

def test_calibrate_models_unknown_set_errors_before_touching_an_episode(monkeypatch):
    _mock_openrouter_preflight(monkeypatch)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("cmd_calibrate must not run when --models fails to resolve")

    monkeypatch.setattr(cl, "_load_episode", fail_if_called)
    # argparse itself rejects an unrecognised --models value (its `choices`
    # is `tuple(sorted(_LAB_MODELS.sets))`) before any dispatch happens -
    # this is caught at the parser level, not inside cmd_calibrate.
    with pytest.raises(SystemExit) as excinfo:
        cl.main([
            "calibrate", "--from-episode", "whatever", "--stage", "monday",
            "--models", "bogus-set", "--max-cost", "1.0",
        ])
    assert excinfo.value.code == 2


def test_resolve_lab_model_set_for_calibrate_namespace_rejects_unknown_set():
    """The underlying resolver's own error message (argparse's `choices`
    normally forecloses this for the CLI, but `_resolve_lab_model_set` is
    still called directly by internal/test callers per its docstring)."""
    from types import SimpleNamespace
    args = SimpleNamespace(models="bogus-set", provider="openrouter", command="calibrate")
    with pytest.raises(SystemExit, match="not defined"):
        cl._resolve_lab_model_set(args)


def _snapshot_episode_for_calibrate() -> dict:
    return {
        "episode_id": "snapshot-week",
        "concept": "Weekly Muffin Pan Recipe",
        "stages": {
            "monday": {"recipe_data": {"title": "Snapshot Spiral Bites", "category": "sweet"}},
            "tuesday": {"dialogue": [
                {"day": "tuesday", "stage": "prep", "character": "Margaret Chen", "message": "Real line one."},
                {"day": "tuesday", "stage": "prep", "character": "Marcus Reid", "message": "Real line two."},
            ]},
        },
    }


def test_calibrate_models_option_routes_the_judge_through_the_named_set(tmp_path, monkeypatch):
    """calibrate had no --models option before #7791 P1; `_resolve_judge_model_for_args`
    already branched on --models for `ab` - adding the flag to calibrate's own
    parser is the entire fix, and this proves the judge model it resolves
    actually reaches generate_judge_response."""
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(cl, "_load_episode", lambda *_a, **_k: _snapshot_episode_for_calibrate())

    seen_models: list[str] = []

    def fake_judge(*, model, **kwargs):
        seen_models.append(model)
        return json.dumps({
            "winner": "A",
            "per_dimension": {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "fixture",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)
    results_dir = tmp_path / "results"

    cl.main([
        "calibrate", "--from-episode", "snapshot-week", "--stage", "tuesday",
        "--runs", "1", "--models", "claude-o55", "--results-dir", str(results_dir),
    ])

    assert seen_models, "generate_judge_response was never called"
    assert set(seen_models) == {"openrouter/anthropic/claude-opus-5.5"}


# ---------------------------------------------------------------------------
# P2: rejudge - refusal paths (no judge call may happen in any of these)
# ---------------------------------------------------------------------------

def _no_judge_calls(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("rejudge must refuse before making any judge call")
    monkeypatch.setattr(model_router, "generate_judge_response", fail)


def test_rejudge_refuses_aborted_source(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path, aborted=True)
    with pytest.raises(SystemExit, match="ABORTED"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_partial_pairs(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path, partial_pairs=[{"run_index": 2, "status": "aborted_mid_arm"}])
    with pytest.raises(SystemExit, match="partial pair"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_dry_run_source(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path, dry_run=True)
    with pytest.raises(SystemExit, match="dry-run result"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_non_ab_source(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path, command="calibrate")
    with pytest.raises(SystemExit, match="not an"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_sweep_source(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path, mode="sweep")
    with pytest.raises(SystemExit, match="sweep"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_missing_transcript(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    pair["variant_messages"] = []
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="missing a transcript"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_wrong_orientation_count(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    pair["judge_orientations"] = pair["judge_orientations"][:1]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="does not have exactly 2"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_incomplete_orientation(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    pair["judge_orientations"][0]["status"] = "accounting_unknown"
    del pair["judge_orientations"][0]["result"]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="never completed"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_missing_saved_prompt(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    del pair["judge_orientations"][0]["evidence"]["prompt"]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="missing its saved"):
        cl.main(["rejudge", str(path)])


# ---------------------------------------------------------------------------
# P2: rejudge - the success path
# ---------------------------------------------------------------------------

def test_rejudge_replays_saved_prompts_and_reports_agreement(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    pair = _fake_pair(run_index=1, overall="control")
    source_path = _fake_ab_result(tmp_path, pairs=[pair])

    seen_calls: list[tuple[str, str]] = []

    def fake_judge(*, prompt, model, **kwargs):
        seen_calls.append((prompt, model))
        # Every orientation now says "B" - see the mapping arithmetic below.
        return json.dumps({
            "winner": "B",
            "per_dimension": {dim: "B" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "new judge disagrees with itself across orientations",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)
    results_dir = tmp_path / "results"

    cl.main([
        "rejudge", str(source_path), "--models", "claude-o55", "--results-dir", str(results_dir),
    ])

    # Exactly the two saved prompts were replayed, verbatim, against the NEW judge.
    assert len(seen_calls) == 2
    assert {model for _, model in seen_calls} == {"openrouter/anthropic/claude-opus-5.5"}
    assert {prompt for prompt, _ in seen_calls} == {
        "PROMPT control_first fixture pair 1",
        "PROMPT variant_first fixture pair 1",
    }

    [result_file] = list(results_dir.glob("*-rejudge-*.json"))
    report = json.loads(result_file.read_text())
    assert report["judge_model"] == "openrouter/anthropic/claude-opus-5.5"
    assert report["source_result_file"] == str(source_path)
    assert report["rejudged_pairs_count"] == 1
    assert report["aborted"] is False

    new_pair = report["pairs"][0]
    # control_first: mapping {"A":"control","B":"variant"} -> winner B -> "variant"
    # variant_first: mapping {"A":"variant","B":"control"} -> winner B -> "control"
    # the two orientations disagree on every key -> combined is "tie" everywhere.
    assert new_pair["judge"]["overall"] == "tie"
    assert new_pair["judge"]["title_fidelity"] == "tie"
    assert new_pair["previous_judge"]["overall"] == "control"

    # V3 test-retest agreement: old overall was "control", new is "tie" -> mismatch;
    # every dimension was "tie" both before and after -> agreement 1.0 there.
    assert report["agreement"]["pairs_compared"] == 1
    assert report["agreement"]["rates"]["overall"] == 0.0
    assert report["agreement"]["rates"]["title_fidelity"] == 1.0
    assert report["overall_counts"] == {"tie": 1}


def test_rejudge_dry_run_makes_zero_judge_calls(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    source_path = _fake_ab_result(tmp_path, pairs=[_fake_pair()])
    results_dir = tmp_path / "results"

    cl.main(["rejudge", str(source_path), "--dry-run", "--results-dir", str(results_dir)])

    [result_file] = list(results_dir.glob("*-rejudge-*.json"))
    report = json.loads(result_file.read_text())
    assert report["dry_run"] is True
    assert report["calls_used"] == 0
    assert report["pairs"][0]["judge"]["overall"] == "tie"


def test_rejudge_aborts_and_writes_partial_report_when_max_calls_is_too_low(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    pair1 = _fake_pair(run_index=1)
    pair2 = _fake_pair(run_index=2)
    source_path = _fake_ab_result(tmp_path, pairs=[pair1, pair2])

    monkeypatch.setattr(
        model_router, "generate_judge_response",
        lambda **_kwargs: json.dumps({
            "winner": "tie",
            "per_dimension": {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "fixture",
        }),
    )
    results_dir = tmp_path / "results"

    cl.main([
        "rejudge", str(source_path), "--models", "claude-o55", "--max-calls", "1",
        "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-rejudge-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert report["rejudged_pairs_count"] < report["requested_pairs"]


# ---------------------------------------------------------------------------
# P3: scripts/lab_offline_metrics.py - unit tests for the new aggregation logic
# ---------------------------------------------------------------------------

def test_speaker_share_computes_max_share_and_silent_roster_members():
    messages = [
        {"character": "Margaret Chen", "message": "one"},
        {"character": "Margaret Chen", "message": "two"},
        {"character": "Stephanie 'Steph' Whitmore", "message": "three"},
    ]
    expected_cast = ["Margaret Chen", "Marcus Reid", "Stephanie 'Steph' Whitmore"]
    result = lom._speaker_share(messages, expected_cast)
    assert result["max_speaker"] == "Margaret"
    assert result["max_share"] == round(2 / 3, 4)
    assert result["silent_roster_members"] == ["Marcus"]


def test_speaker_share_handles_empty_transcript():
    result = lom._speaker_share([], ["Margaret Chen", "Marcus Reid"])
    assert result["max_share"] == 0.0
    assert result["max_speaker"] is None
    assert result["silent_roster_members"] == sorted(["Margaret", "Marcus"])


def test_average_metrics_hand_checked():
    per_pair = [
        {"dash_clause_rate": 0.2, "length_stdev": 5.0, "short_line_rate": 0.1,
         "frame_claim_rate": 0.3, "agree_opener_rate": 0.1, "question_rate": 0.2,
         "length_mean_words": 20.0, "max_share": 0.6, "silent_roster_members": ["A", "B"]},
        {"dash_clause_rate": 0.3, "length_stdev": 6.0, "short_line_rate": 0.2,
         "frame_claim_rate": 0.2, "agree_opener_rate": 0.15, "question_rate": 0.25,
         "length_mean_words": 22.0, "max_share": 0.4, "silent_roster_members": ["B", "C"]},
    ]
    averaged = lom._average_metrics(per_pair)
    assert averaged["dash_clause_rate"] == pytest.approx(0.25)
    assert averaged["length_stdev"] == pytest.approx(5.5)
    assert averaged["short_line_rate"] == pytest.approx(0.15)
    assert averaged["frame_claim_rate"] == pytest.approx(0.25)
    assert averaged["agree_opener_rate"] == pytest.approx(0.125)
    assert averaged["question_rate"] == pytest.approx(0.225)
    assert averaged["length_mean_words"] == pytest.approx(21.0)
    assert averaged["max_share"] == pytest.approx(0.5)
    # A member missing from EVERY pair, not just one: only "B" is silent in both.
    assert averaged["silent_roster_members_in_every_pair"] == ["B"]
    assert averaged["pair_count"] == 2


def test_average_metrics_empty_input():
    assert lom._average_metrics([]) == {}


def test_orientation_disagreement_true_when_overalls_differ():
    pair = {"judge_orientations": [
        {"result": {"overall": "control"}},
        {"result": {"overall": "variant"}},
    ]}
    assert lom._orientation_disagreement(pair) is True


def test_orientation_disagreement_false_when_overalls_agree():
    pair = {"judge_orientations": [
        {"result": {"overall": "control"}},
        {"result": {"overall": "control"}},
    ]}
    assert lom._orientation_disagreement(pair) is False


def test_orientation_disagreement_none_when_data_is_missing():
    assert lom._orientation_disagreement({"judge_orientations": []}) is None
    assert lom._orientation_disagreement({"judge_orientations": [{"result": {"overall": "control"}}]}) is None
    assert lom._orientation_disagreement({"judge_orientations": [{}, {}]}) is None


# ---------------------------------------------------------------------------
# P3: end-to-end - real conversation_metrics.summarize(), grouped by recipe,
# pooled across recipes and across files, --json output.
# ---------------------------------------------------------------------------

def _pair_with_text(run_index: int, scenario_id: str | None, control_text: str, variant_text: str) -> dict:
    return _fake_pair(
        run_index=run_index, scenario_id=scenario_id,
        control_text=control_text, variant_text=variant_text,
    )


def test_compute_groups_by_scenario_and_pools_with_real_summarize(tmp_path, capsys):
    # One question in the control line, none in the variant - question_rate is
    # unambiguous (a single line, "?" present or not) and easy to hand-check.
    scenarios = [
        {"id": "recipe-a", "concept": "Recipe A"},
        {"id": "recipe-b", "concept": "Recipe B"},
    ]
    pairs = [
        _pair_with_text(1, "recipe-a", "Is this ready?", "This is ready."),
        _pair_with_text(2, "recipe-b", "Is this ready?", "This is ready."),
    ]
    path = _fake_ab_result(tmp_path, pairs=pairs, mode="testbed", scenarios=scenarios, stage="monday")

    report = lom.compute([path])
    file_report = report["files"][0]
    assert set(file_report["recipes"]) == {"recipe-a", "recipe-b"}
    for recipe_id in ("recipe-a", "recipe-b"):
        recipe = file_report["recipes"][recipe_id]
        assert recipe["control"]["question_rate"] == pytest.approx(1.0)
        assert recipe["variant"]["question_rate"] == pytest.approx(0.0)
        # Both saved orientations in every fixture pair AGREE ("overall" identical) -> V4 is 0.
        assert recipe["v4_position_disagreement_rate"] == pytest.approx(0.0)

    pooled = file_report["pooled"]
    assert pooled["control"]["question_rate"] == pytest.approx(1.0)
    assert pooled["variant"]["question_rate"] == pytest.approx(0.0)
    assert pooled["control"]["pair_count"] == 2

    pooled_all = report["pooled_across_files"]
    assert pooled_all["control"]["pair_count"] == 2

    # --json / human printing must not explode on this report shape.
    lom._print_human(report)
    assert "recipe-a" in capsys.readouterr().out


def test_compute_single_concept_result_is_one_recipe_group(tmp_path):
    path = _fake_ab_result(tmp_path, pairs=[_fake_pair()], mode="single", concept="Solo Muffins")
    report = lom.compute([path])
    recipes = report["files"][0]["recipes"]
    assert list(recipes) == ["Solo Muffins"]
    assert recipes["Solo Muffins"]["pair_count"] == 1


def test_main_json_flag_prints_valid_json(tmp_path, capsys):
    path = _fake_ab_result(tmp_path, pairs=[_fake_pair()])
    lom.main([str(path), "--json"])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["files"][0]["result_file"] == str(path)


def test_iter_result_paths_expands_glob(tmp_path):
    _fake_ab_result(tmp_path, filename="one.json")
    _fake_ab_result(tmp_path, filename="two.json")
    paths = lom._iter_result_paths([str(tmp_path / "*.json")])
    assert {p.name for p in paths} == {"one.json", "two.json"}


def test_iter_result_paths_errors_on_no_match(tmp_path):
    with pytest.raises(SystemExit, match="no file matches"):
        lom._iter_result_paths([str(tmp_path / "missing-*.json")])


def test_load_ab_result_rejects_non_ab_command(tmp_path):
    path = _fake_ab_result(tmp_path, command="calibrate")
    with pytest.raises(SystemExit, match="not an"):
        lom._load_ab_result(path)


# ---------------------------------------------------------------------------
# P4: SPEAKERS_SEE_JUDGE_RECIPE_FACTS variant lever
# ---------------------------------------------------------------------------

def _persona(name: str = "Margaret Chen") -> dict:
    return {
        "name": name,
        "role": "Head Recipe Developer",
        "communication_style": {"signature_phrases": ["Right."], "verbosity": "low"},
        "internal_contradictions": [],
        "relationships": {},
        "triggers": [],
    }


def _capture_generate_turn_prompt(monkeypatch, *, flag: bool, recipe_facts: str | None):
    monkeypatch.setattr(sdw, "SPEAKERS_SEE_JUDGE_RECIPE_FACTS", flag)
    monkeypatch.setattr(sdw, "REWRITE_LOG", [])
    prompts: list[str] = []

    def fake_generate(prompt, system_prompt=None, model=None, temperature=None, **_kw):
        prompts.append(prompt)
        return "A fine reply that is long enough to pass the shape guards here today."

    monkeypatch.setattr(sdw, "generate_response", fake_generate)
    monkeypatch.setattr(sdw, "_guard_cot_leak", lambda m, **_kw: m)
    monkeypatch.setattr(sdw, "build_system_prompt", lambda persona: "SYS")
    sdw.generate_turn(
        persona=_persona(),
        concept="Spiral Bites",
        day="monday",
        stage="brainstorm",
        deadline="5 pm",
        recent_lines=[],
        event=None,
        model="test-model",
        mode="openai",
        prompt_style="scene",
        day_turn=1,
        is_last_turn=False,
        recipe_context="Spiral Bites, a laminated pastry.",
        recipe_facts=recipe_facts,
    )
    return prompts[0]


def test_speakers_do_not_see_judge_recipe_facts_by_default(monkeypatch):
    assert sdw.SPEAKERS_SEE_JUDGE_RECIPE_FACTS is False
    prompt = _capture_generate_turn_prompt(
        monkeypatch, flag=False, recipe_facts="Ground truth: 2 cups flour, bake 22 minutes.",
    )
    assert "Recipe anchor: Spiral Bites, a laminated pastry." in prompt
    assert "Recipe facts:" not in prompt
    assert "Ground truth: 2 cups flour" not in prompt


def test_flag_off_prompt_is_byte_identical_regardless_of_recipe_facts_value(monkeypatch):
    """The lever is a strict pass-through gate: with it off, whatever
    recipe_facts the caller passes must never reach the prompt."""
    prompt_without = _capture_generate_turn_prompt(monkeypatch, flag=False, recipe_facts=None)
    prompt_with_facts_but_off = _capture_generate_turn_prompt(
        monkeypatch, flag=False, recipe_facts="Ground truth: 2 cups flour, bake 22 minutes.",
    )
    assert prompt_without == prompt_with_facts_but_off


def test_speakers_see_judge_recipe_facts_when_flag_is_on(monkeypatch):
    prompt = _capture_generate_turn_prompt(
        monkeypatch, flag=True, recipe_facts="Ground truth: 2 cups flour, bake 22 minutes.",
    )
    assert "Recipe anchor: Spiral Bites, a laminated pastry." in prompt
    assert "Recipe facts: Ground truth: 2 cups flour, bake 22 minutes." in prompt
    # Comes right after the recipe anchor line, before the rest of the prompt.
    anchor_index = prompt.index("Recipe anchor:")
    facts_index = prompt.index("Recipe facts:")
    assert anchor_index < facts_index


def test_flag_on_with_no_recipe_facts_leaves_prompt_unchanged(monkeypatch):
    prompt = _capture_generate_turn_prompt(monkeypatch, flag=True, recipe_facts=None)
    assert "Recipe facts:" not in prompt


def test_speakers_see_judge_recipe_facts_is_an_allowed_variant_lever():
    assert "SPEAKERS_SEE_JUDGE_RECIPE_FACTS" in cl.ALLOWED_VARIANT_ATTRS


def test_variant_validation_accepts_bool_and_rejects_other_types():
    cl.validate_variant(sdw, {"SPEAKERS_SEE_JUDGE_RECIPE_FACTS": True})  # must not raise
    cl.validate_variant(sdw, {"SPEAKERS_SEE_JUDGE_RECIPE_FACTS": False})  # must not raise
    with pytest.raises(cl.ConversationLabError, match="must be a bool"):
        cl.validate_variant(sdw, {"SPEAKERS_SEE_JUDGE_RECIPE_FACTS": "yes"})


def test_run_arm_forwards_recipe_facts_to_run_simulation(monkeypatch):
    """conversation_lab.py's own plumbing (_run_arm -> run_simulation) must
    carry recipe_facts through unchanged - generate_turn's own use of it is
    covered above."""
    captured: dict = {}

    def fake_run_simulation(**kwargs):
        captured.update(kwargs)
        return {"messages": []}

    monkeypatch.setattr(cl.simulate_module, "run_simulation", fake_run_simulation)
    cl._run_arm(
        concept="Spiral Bites", stage="monday", run_index=1,
        recipe_context="anchor", mode="template", default_model="test-model",
        recipe_facts="Ground truth facts.",
    )
    assert captured["recipe_facts"] == "Ground truth facts."


def test_run_arm_default_recipe_facts_is_none(monkeypatch):
    captured: dict = {}

    def fake_run_simulation(**kwargs):
        captured.update(kwargs)
        return {"messages": []}

    monkeypatch.setattr(cl.simulate_module, "run_simulation", fake_run_simulation)
    cl._run_arm(
        concept="Spiral Bites", stage="monday", run_index=1,
        recipe_context="anchor", mode="template", default_model="test-model",
    )
    assert captured["recipe_facts"] is None
