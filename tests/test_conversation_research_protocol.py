"""Conversation-lab research protocol: the CLI and offline tooling used to run
and re-score A/B dialogue experiments must measure what it claims to and fail
closed.

- `calibrate --models NAME` resolves a lab model set before touching an episode.
- `rejudge RESULT.json` re-scores saved transcripts without regenerating them,
  and refuses any change to the judge instrument (prompt, model, request
  settings, A/B order) unless `--allow-instrument-change` is given; only an
  unchanged instrument is reported as a V3 test-retest.
- `scripts/lab_offline_metrics.py` computes metrics from saved JSON with zero
  API calls and refuses sweep or empty results.
- The SPEAKERS_SEE_JUDGE_RECIPE_FACTS variant lever (R3) reaches the prompt.
- Cost caps, prices, usage costs and balances that are non-finite or negative
  block rather than admit; the atomic pick writer trashes its temp file.

Every test is network-free: OPENROUTER_API_KEY is a fixture string and the
OpenRouter endpoints and judge/generation calls are monkeypatched. Model-set
routing and pricing (P0) live in tests/test_lab_models.py.

History: merged from test_research_prereqs_7791.py (card #7791, RESEARCH_PLAN.md
section 3.2 prerequisites P1-P4) and test_review_fixes_7791.py (Codex review
regressions for #7791 / #7793).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import scripts.conversation_lab as cl
import scripts.lab_offline_metrics as lom
import scripts.simulate_dialogue_week as sdw
from backend.utils import model_router
from tests.conversation_research_helpers import (
    fake_ab_result as _fake_ab_result,
    fake_pair as _fake_pair,
    mock_openrouter_preflight as _mock_openrouter_preflight,
    no_judge_calls as _no_judge_calls,
)
from tests.test_openrouter_lab import _fake_openrouter


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
        "--runs", "1", "--models", "deepseek-o55", "--results-dir", str(results_dir),
    ])

    assert seen_models, "generate_judge_response was never called"
    assert set(seen_models) == {"openrouter/anthropic/claude-opus-5.5"}


# ---------------------------------------------------------------------------
# P2: rejudge - refusal paths (no judge call may happen in any of these)
# ---------------------------------------------------------------------------

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
        "rejudge", str(source_path), "--models", "deepseek-o55", "--results-dir", str(results_dir),
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
        "rejudge", str(source_path), "--models", "deepseek-o55", "--max-calls", "1",
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


# ---------------------------------------------------------------------------
# 1. rejudge instrument integrity
# ---------------------------------------------------------------------------

def _with_old_prompt_sha(pair: dict) -> dict:
    """A pair whose saved instrument records an earlier judge prompt."""
    for orientation in pair["judge_orientations"]:
        orientation["evidence"]["judge_instrument"] = {
            **orientation["evidence"]["judge_instrument"], "evaluator_prompt_sha256": "0" * 64,
        }
    return pair


def test_rejudge_refuses_a_source_judged_under_an_earlier_prompt(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    path = _fake_ab_result(tmp_path, pairs=[_with_old_prompt_sha(_fake_pair())])
    with pytest.raises(SystemExit, match=r"differ from the source's: evaluator_prompt_sha256\."):
        cl.main(["rejudge", str(path), "--models", "deepseek-o55"])


def test_allow_instrument_change_runs_a_deliberate_prompt_revision_comparison(tmp_path, monkeypatch, capsys):
    """Codex round 4 P2: the opt-in must reach a changed prompt, not be
    pre-empted by the loader."""
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    path = _fake_ab_result(tmp_path, pairs=[_with_old_prompt_sha(_fake_pair())])
    out = tmp_path / "out"
    cl.main(["rejudge", str(path), "--models", "deepseek-o55", "--allow-instrument-change", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "instrument_changed"
    assert report["instrument_changed_fields"] == ["evaluator_prompt_sha256"]
    assert "V3 test-retest agreement" not in capsys.readouterr().out


@pytest.mark.parametrize("missing", ["first_arm", "second_arm"])
def test_rejudge_refuses_a_missing_arm_record_instead_of_defaulting(tmp_path, monkeypatch, missing):
    # Before the fix a missing first/second_arm defaulted to control/variant,
    # which scores the variant_first orientation with the wrong A/B order.
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    del pair["judge_orientations"][1]["evidence"][missing]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="refusing to guess the A/B order"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_arm_record_that_contradicts_the_mapping(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    pair["judge_orientations"][1]["evidence"]["mapping"] = {"A": "control", "B": "variant", "tie": "tie"}
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="refusing to guess the A/B order"):
        cl.main(["rejudge", str(path)])


def test_rejudge_refuses_two_orientations_with_the_same_order(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    first = pair["judge_orientations"][0]["evidence"]
    second = pair["judge_orientations"][1]["evidence"]
    second.update(first_arm=first["first_arm"], second_arm=first["second_arm"], mapping=dict(first["mapping"]))
    path = _fake_ab_result(tmp_path, pairs=[pair])
    with pytest.raises(SystemExit, match="not a position-swapped pair"):
        cl.main(["rejudge", str(path)])


def test_rejudge_passes_each_saved_arm_order_to_the_judge(tmp_path, monkeypatch):
    """The legitimate path still works and hands the judge the saved order."""
    _mock_openrouter_preflight(monkeypatch)
    seen: list[tuple[str, str]] = []
    real = cl._run_judge_orientation

    def spy(**kwargs):
        seen.append((kwargs["first_arm"], kwargs["second_arm"]))
        return real(**kwargs)

    monkeypatch.setattr(cl, "_run_judge_orientation", spy)
    monkeypatch.setattr(
        model_router, "generate_judge_response",
        lambda **_k: json.dumps({
            "winner": "A", "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS}, "reason": "x",
        }),
    )
    path = _fake_ab_result(tmp_path)
    cl.main(["rejudge", str(path), "--models", "deepseek-o55", "--results-dir", str(tmp_path / "out")])
    assert seen == [("control", "variant"), ("variant", "control")]


# ---------------------------------------------------------------------------
# 2. non-finite money values fail closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["NaN", "nan", "inf", "-Infinity", "-0.000001"])
def test_price_fetch_leaves_non_finite_or_negative_prices_unpriced(monkeypatch, bad):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    payload = {"data": [
        {"id": "anthropic/claude-opus-5.5", "pricing": {"prompt": bad, "completion": "0.00002"}},
        {"id": "anthropic/claude-haiku-4.5", "pricing": {"prompt": "0.000001", "completion": "0.000005"}},
    ]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload))
    prices = cl._fetch_openrouter_model_prices()
    assert "anthropic/claude-opus-5.5" not in prices
    assert prices["anthropic/claude-haiku-4.5"] == (0.000001, 0.000005)
    # Unpriced means the per-call reservation is an unconditional block.
    monkeypatch.setattr(cl, "_OPENROUTER_MODEL_PRICES", prices)
    assert cl._openrouter_worst_case_call_cost("anthropic/claude-opus-5.5", 10) == float("inf")


@pytest.mark.parametrize("bad_cost", [float("nan"), float("inf"), -0.01])
def test_non_finite_usage_cost_is_recorded_as_absent_and_blocks_the_next_call(monkeypatch, bad_cost):
    model_router.reset_cost_log()
    _fake_openrouter(monkeypatch, content="a line", cost=bad_cost)
    model = "deepseek/deepseek-v4.1-flash"
    monkeypatch.setattr(cl, "_OPENROUTER_MODEL_PRICES", {model: (0.0000003, 0.0000003)})

    model_router.generate_response(prompt="x", model=f"openrouter/{model}", temperature=0.2)
    [entry] = model_router.get_cost_entries()
    assert entry.get("actual_cost") is None

    budget = cl.CallBudget(max_calls=1000)
    with cl._installed_budget_guard(budget, 100.0):
        with pytest.raises(cl.LabBudgetAbort):
            model_router.generate_response(prompt="x", model=f"openrouter/{model}", temperature=0.2)


def test_guard_blocks_when_the_cap_itself_is_nan(monkeypatch):
    model_router.reset_cost_log()
    _fake_openrouter(monkeypatch, content="a line")
    model = "deepseek/deepseek-v4.1-flash"
    monkeypatch.setattr(cl, "_OPENROUTER_MODEL_PRICES", {model: (0.0000003, 0.0000003)})
    budget = cl.CallBudget(max_calls=1000)
    with cl._installed_budget_guard(budget, float("nan")):
        with pytest.raises(cl.LabBudgetAbort):
            model_router.generate_response(prompt="x", model=f"openrouter/{model}", temperature=0.2)


def test_would_exceed_cost_treats_a_nan_total_or_cap_as_exceeded(monkeypatch):
    monkeypatch.setattr(cl, "_lab_cost_total", lambda: float("nan"))
    assert cl._would_exceed_cost(5.0) is True
    monkeypatch.setattr(cl, "_lab_cost_total", lambda: 1.0)
    assert cl._would_exceed_cost(float("nan")) is True
    assert cl._would_exceed_cost(5.0) is False
    assert cl._would_exceed_cost(1.0) is True  # reached counts as exceeded, unchanged


# ---------------------------------------------------------------------------
# 3. atomic writer trashes, never deletes
# ---------------------------------------------------------------------------

def test_atomic_pick_writer_trashes_temp_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "result.json"
    target.write_text('{"keep": true}')
    trashed: list[str] = []
    monkeypatch.setattr(cl, "send2trash", lambda p: trashed.append(p))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(cl.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        cl._write_pairs_report_atomic(target, {"new": True})

    assert len(trashed) == 1
    assert trashed[0].startswith(str(tmp_path / ".result.json."))
    assert json.loads(target.read_text()) == {"keep": True}


def test_atomic_pick_writer_success_leaves_no_temp_file(tmp_path):
    target = tmp_path / "result.json"
    cl._write_pairs_report_atomic(target, {"picked": 1})
    assert json.loads(target.read_text()) == {"picked": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["result.json"]


# ---------------------------------------------------------------------------
# Round 2 (Codex FAIL 4e4217e): the whole instrument, sweep input, balances
# ---------------------------------------------------------------------------

def _verdict_json(winner: str = "A") -> str:
    return json.dumps({
        "winner": winner, "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS}, "reason": "x",
    })


def test_rejudge_refuses_a_default_judge_that_differs_from_the_source_judge(tmp_path, monkeypatch):
    # The fixture was scored by Opus 5.5; with no --models the CLI default set
    # judges with Opus 4.6. That is a different instrument, refused before any call.
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match=r"differ from the source's: .*\bmodel\b"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path))])


def test_rejudge_same_instrument_is_reported_as_v3_retest(tmp_path, monkeypatch, capsys):
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    out = tmp_path / "out"
    cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--models", "deepseek-o55", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "retest"
    assert report["instrument_changed_fields"] == []
    assert report["judge_instrument"] == report["source_judge_instrument"]
    assert "V3 test-retest agreement" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("field", "attr", "value"),
    [
        # Each is a judge setting that is not the model, prompt or temperature.
        ("max_tokens", "_OPENROUTER_ANTHROPIC_MAX_TOKENS", 8192),
        ("extra_body", "openrouter_provider_route", lambda _m: {"order": ["anthropic"], "allow_fallbacks": True}),
        ("empty_stop_max_attempts", "_OPENROUTER_EMPTY_STOP_MAX_ATTEMPTS", 5),
    ],
)
def test_rejudge_refuses_when_any_judge_request_setting_changed(tmp_path, monkeypatch, field, attr, value):
    """Codex round 3: the route, token ceiling and retry bound are part of
    the instrument too. The source fixture recorded today's values; change
    one and the same-model rejudge must refuse, naming that field."""
    path = _fake_ab_result(tmp_path)  # records the instrument BEFORE the change
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    monkeypatch.setattr(model_router, attr, value)
    with pytest.raises(SystemExit, match=rf"differ from the source's: {field}\."):
        cl.main(["rejudge", str(path), "--models", "deepseek-o55"])


def test_rejudge_refuses_when_the_json_retry_bound_changed(tmp_path, monkeypatch):
    path = _fake_ab_result(tmp_path)
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    monkeypatch.setattr(cl, "_JUDGE_JSON_MAX_RETRIES", 5)
    with pytest.raises(SystemExit, match=r"differ from the source's: json_max_retries\."):
        cl.main(["rejudge", str(path), "--models", "deepseek-o55"])


def test_allow_instrument_change_runs_and_is_never_labelled_v3(tmp_path, monkeypatch, capsys):
    _mock_openrouter_preflight(monkeypatch)
    models_seen: list[str] = []

    def judge(*, model, **_k):
        models_seen.append(model)
        return _verdict_json()

    monkeypatch.setattr(model_router, "generate_judge_response", judge)
    out = tmp_path / "out"
    cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--allow-instrument-change", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "instrument_changed"
    assert "model" in report["instrument_changed_fields"]
    assert set(models_seen) == {report["judge_model"]} != {report["source_judge_model"]}
    printed = capsys.readouterr().out
    assert "CHANGED-INSTRUMENT" in printed and "V3 test-retest agreement" not in printed


def test_rejudge_of_a_source_without_a_recorded_instrument_needs_the_opt_in(tmp_path, monkeypatch, capsys):
    pair = _fake_pair()
    for orientation in pair["judge_orientations"]:
        del orientation["evidence"]["judge_instrument"]
    path = _fake_ab_result(tmp_path, pairs=[pair])
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match="does not record its judge instrument"):
        cl.main(["rejudge", str(path), "--models", "deepseek-o55"])

    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    out = tmp_path / "out"
    cl.main(["rejudge", str(path), "--models", "deepseek-o55", "--allow-instrument-change", "--results-dir", str(out)])
    report = json.loads(next(out.glob("*-rejudge-*.json")).read_text())
    assert report["rejudge_mode"] == "instrument_unverified"
    assert "V3 test-retest agreement" not in capsys.readouterr().out


def test_rejudge_refuses_a_source_with_inconsistent_instruments(tmp_path, monkeypatch):
    pair = _fake_pair()
    pair["judge_orientations"][1]["evidence"]["judge_instrument"] = {
        **pair["judge_orientations"][1]["evidence"]["judge_instrument"], "max_tokens": 1,
    }
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match="different judge instruments"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path, pairs=[pair])), "--models", "deepseek-o55"])


def test_rejudge_refuses_a_source_scored_by_two_judge_models(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    second = _fake_pair(run_index=2)
    for orientation in second["judge_orientations"]:
        orientation["evidence"]["model"] = "openrouter/anthropic/claude-opus-4.6"
    path = _fake_ab_result(tmp_path, pairs=[_fake_pair(), second])
    with pytest.raises(SystemExit, match="more than one judge model"):
        cl.main(["rejudge", str(path), "--models", "deepseek-o55"])


def test_rejudge_refuses_a_source_that_does_not_record_its_judge_model(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    pair = _fake_pair()
    del pair["judge_orientations"][1]["evidence"]["model"]
    with pytest.raises(SystemExit, match="which judge model"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path, pairs=[pair])), "--models", "deepseek-o55"])


def test_new_judge_calls_save_the_full_instrument_in_evidence():
    evidence: dict = {}
    import unittest.mock as um
    with um.patch.object(model_router, "generate_judge_response", return_value=_verdict_json()):
        cl._judge_orientation(
            judge_model="openrouter/anthropic/claude-opus-5.5", concept="c", stage="monday",
            recipe_context=None, expected_cast=[], first_arm="control", first_messages=[],
            second_arm="variant", second_messages=[], evidence=evidence,
        )
    inst = evidence["judge_instrument"]
    assert inst == cl._judge_instrument("openrouter/anthropic/claude-opus-5.5")
    assert inst["model"] == "anthropic/claude-opus-5.5"
    assert inst["extra_body"]["provider"] == {"order": ["anthropic"], "allow_fallbacks": False}
    assert inst["max_tokens"] == 4096 and inst["temperature"] == 0.2
    assert inst["request_shape"] == "fixed"
    assert inst["evaluator_prompt_sha256"] and inst["json_max_retries"] == cl._JUDGE_JSON_MAX_RETRIES


def test_preflight_prices_only_the_judge_for_rejudge(tmp_path, monkeypatch):
    """Codex round 3 P2: rejudge never calls the dialogue model, so a missing
    dialogue-model price must not block it; a missing judge price still does."""
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(cl, "_fetch_openrouter_model_prices",
                        lambda: {"anthropic/claude-opus-5.5": (0.000004, 0.00002)})
    monkeypatch.setattr(model_router, "generate_judge_response", lambda **_k: _verdict_json())
    out = tmp_path / "out"
    cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--models", "deepseek-o55", "--results-dir", str(out)])
    assert list(out.glob("*-rejudge-*.json"))

    monkeypatch.setattr(cl, "_fetch_openrouter_model_prices",
                        lambda: {"anthropic/claude-haiku-4.5": (0.000001, 0.000005)})
    with pytest.raises(SystemExit, match="no published price for anthropic/claude-opus-5.5"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path, filename="b.json")), "--models", "deepseek-o55"])


def test_preflight_still_prices_both_models_for_ab(tmp_path, monkeypatch):
    _mock_openrouter_preflight(monkeypatch)
    monkeypatch.setattr(cl, "_fetch_openrouter_model_prices",
                        lambda: {"anthropic/claude-opus-5.5": (0.000004, 0.00002)})
    args = SimpleNamespace(command="ab", max_cost=5.0, models="claude-o55", provider="openrouter", dry_run=False)
    with pytest.raises(SystemExit, match="anthropic/claude-haiku-4.5"):
        cl._openrouter_preflight(args)


def test_offline_metrics_refuses_a_sweep_result(tmp_path):
    import scripts.lab_offline_metrics as lom
    path = _fake_ab_result(tmp_path, mode="sweep", pairs=[])
    data = json.loads(path.read_text())
    data["variants"] = {"v1": {"pairs": [_fake_pair()]}}
    path.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="sweep"):
        lom.compute([path])


def test_offline_metrics_refuses_a_result_with_no_pairs(tmp_path):
    import scripts.lab_offline_metrics as lom
    with pytest.raises(SystemExit, match="no pairs"):
        lom.compute([_fake_ab_result(tmp_path, pairs=[])])


def _fake_http(monkeypatch, payload):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(httpx, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: payload))


@pytest.mark.parametrize("field", ["total_credits", "total_usage"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_account_balance_rejects_non_finite_amounts(monkeypatch, field, bad):
    data = {"total_credits": 150.0, "total_usage": 116.0}
    data[field] = bad
    _fake_http(monkeypatch, {"data": data})
    with pytest.raises(cl.ConversationLabError, match=field):
        cl._openrouter_fetch_account_balance()


@pytest.mark.parametrize("field", ["limit", "limit_remaining"])
def test_key_check_rejects_non_finite_amounts(monkeypatch, field):
    data = {"limit": 100.0, "limit_remaining": 32.7, "usage": 67.3}
    data[field] = float("nan")
    _fake_http(monkeypatch, {"data": data})
    with pytest.raises(cl.ConversationLabError, match=field):
        cl._openrouter_fetch_key()


def test_balance_fetch_still_returns_a_normal_balance(monkeypatch):
    _fake_http(monkeypatch, {"data": {"total_credits": 150, "total_usage": 116.36}})
    assert cl._openrouter_fetch_account_balance() == pytest.approx(33.64)


@pytest.mark.parametrize("bad", ["nan", "inf", "0", "-1"])
def test_cli_refuses_an_unenforceable_max_cost(tmp_path, monkeypatch, bad):
    _mock_openrouter_preflight(monkeypatch)
    _no_judge_calls(monkeypatch)
    with pytest.raises(SystemExit, match="--max-cost must be a positive finite amount"):
        cl.main(["rejudge", str(_fake_ab_result(tmp_path)), "--models", "deepseek-o55", "--max-cost", bad])


# ---------------------------------------------------------------------------
# Round 4 (Codex FAIL 58580c7): the description IS the request
# ---------------------------------------------------------------------------

def test_openrouter_instrument_equals_the_kwargs_actually_sent(monkeypatch):
    """The recorded settings are built by the same function the send path
    uses; prove it against what the SDK actually receives."""
    captured = _fake_openrouter(monkeypatch, content="ok")
    model = "anthropic/claude-opus-5.5"
    model_router.allow_openrouter_models(dialogue="anthropic/claude-haiku-4.5", judge=model)
    model_router.generate_judge_response(prompt="p", system_prompt="s", model=f"openrouter/{model}", temperature=0.2)
    sent = {k: v for k, v in captured["create_kwargs"].items() if k != "messages"}
    settings = model_router.judge_request_settings(f"openrouter/{model}", 0.2)
    assert {k: settings[k] for k in sent} == sent


def test_a_variable_shape_judge_is_never_a_verified_retest(tmp_path, monkeypatch):
    """A result judged through the old direct-OpenAI path recorded
    request_shape "variable" (its request changed at run time), so it is
    unverifiable by design."""
    variable = {**cl._judge_instrument("openai/gpt-5.1"), "request_shape": "variable"}
    mode, _ = cl._rejudge_mode(False, "openai/gpt-5.1", variable)
    assert mode == "instrument_unverified"
    fixed = cl._judge_instrument("openrouter/anthropic/claude-opus-5.5")
    assert cl._rejudge_mode(False, "openrouter/anthropic/claude-opus-5.5", fixed) == ("retest", [])
