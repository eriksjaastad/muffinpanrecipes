"""Tests for scripts/conversation_lab.py.

ZERO paid API calls: scripts.simulate_dialogue_week.run_simulation and
backend.utils.model_router.generate_judge_response are monkeypatched in
every test that would otherwise call them. `--dry-run` tests additionally
assert the judge is never invoked at all. Every results/EXPERIMENTS.md path
is redirected under tmp_path via --results-dir so no test writes into
docs/conversation-lab/.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw
from backend.utils import model_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _messages(tag: str, count: int = 2) -> list[dict]:
    return [
        {"day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
         "message": f"{tag} line {i}", "timestamp": "", "model": "test", "attachments": []}
        for i in range(count)
    ]


def _write_variant(tmp_path, payload: dict) -> "cl.Path":
    path = tmp_path / "variant.json"
    path.write_text(json.dumps(payload))
    return path


def _fail_urlopen(*_args, **_kwargs):
    raise AssertionError("urlopen must never be called in a test")


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Every test in this file must be network-free by default."""
    monkeypatch.setattr(cl.urllib.request, "urlopen", _fail_urlopen)


# ---------------------------------------------------------------------------
# Variant apply/restore mechanism
# ---------------------------------------------------------------------------


def test_apply_variant_refuses_unknown_attribute():
    with pytest.raises(cl.ConversationLabError, match="unknown attribute"):
        cl._apply_variant(sdw, {"_DOES_NOT_EXIST": "x"})


def test_apply_variant_refuses_callable_attribute():
    with pytest.raises(cl.ConversationLabError, match="not a string/dict"):
        cl._apply_variant(sdw, {"build_system_prompt": "x"})


def test_apply_and_restore_variant_round_trips():
    original = sdw._SHARED_CHARACTER_RULES
    saved = cl._apply_variant(sdw, {"_SHARED_CHARACTER_RULES": "VARIANT TEXT"})
    assert sdw._SHARED_CHARACTER_RULES == "VARIANT TEXT"
    cl._restore_variant(sdw, saved)
    assert sdw._SHARED_CHARACTER_RULES == original


def test_apply_variant_clears_prompt_cache():
    sdw._system_prompt_cache["Margaret Chen"] = "stale cached prompt"
    saved = cl._apply_variant(sdw, {"_SHARED_CHARACTER_RULES": "VARIANT TEXT"})
    assert "Margaret Chen" not in sdw._system_prompt_cache
    cl._restore_variant(sdw, saved)
    assert "Margaret Chen" not in sdw._system_prompt_cache


# ---------------------------------------------------------------------------
# ab: pairing, variant-arm-only patching, restore-on-error
# ---------------------------------------------------------------------------


def test_ab_pairs_control_then_variant_per_run_and_restores_after_each(tmp_path, monkeypatch):
    original_rules = sdw._SHARED_CHARACTER_RULES
    seen: list[tuple[int, str]] = []

    def fake_run_simulation(*, concept, default_model, run_index, stage_only, mode, recipe_context, **kwargs):
        current = sdw._SHARED_CHARACTER_RULES
        seen.append((run_index, current))
        tag = "VARIANT" if current == "VARIANT_RULES" else "CONTROL"
        return {"messages": _messages(tag)}

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)

    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "2",
        "--variant", str(variant_path), "--recipe-context", "This week's recipe: Test Muffins.",
        "--dry-run", "--results-dir", str(results_dir),
    ])

    # control_1, variant_1, control_2, variant_2 - in that exact order.
    assert [s[1] for s in seen] == [original_rules, "VARIANT_RULES", original_rules, "VARIANT_RULES"]
    assert [s[0] for s in seen] == [1, 1, 2, 2]
    # The module is left exactly as it started.
    assert sdw._SHARED_CHARACTER_RULES == original_rules


def test_ab_restores_variant_even_when_variant_arm_raises(tmp_path, monkeypatch):
    original_rules = sdw._SHARED_CHARACTER_RULES
    call_count = {"n": 0}

    def flaky_run_simulation(*, concept, default_model, run_index, stage_only, mode, recipe_context, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:  # the variant arm of the first pair
            raise RuntimeError("boom")
        return {"messages": _messages("CONTROL")}

    monkeypatch.setattr(sdw, "run_simulation", flaky_run_simulation)
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    with pytest.raises(RuntimeError, match="boom"):
        cl.main([
            "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(variant_path), "--recipe-context", "anchor",
            "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])

    assert sdw._SHARED_CHARACTER_RULES == original_rules


# ---------------------------------------------------------------------------
# ab: --dry-run makes zero judge calls
# ---------------------------------------------------------------------------


def test_ab_dry_run_never_calls_the_judge(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})

    def fail_judge(**kwargs):
        raise AssertionError("generate_judge_response must not be called in --dry-run")

    monkeypatch.setattr(model_router, "generate_judge_response", fail_judge)

    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "2",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["dry_run"] is True
    assert report["completed_pairs"] == 2
    for pair in report["pairs"]:
        assert pair["judge"]["overall"] == "tie"
        assert all(pair["judge"][d] == "tie" for d in cl.JUDGE_DIMENSIONS)


# ---------------------------------------------------------------------------
# ab: swapped-position agreement rule
# ---------------------------------------------------------------------------


def test_ab_pairwise_judge_agreement_counts_as_win(tmp_path, monkeypatch):
    """A judge that correctly identifies the variant regardless of A/B
    position should agree across both swapped orientations, so the
    combined verdict is a real win, not a tie."""
    monkeypatch.setattr(sdw, "run_simulation", _make_fake_run_simulation())

    def content_aware_judge(*, prompt, system_prompt, model, temperature):
        # Whichever transcript contains the VARIANT marker wins - regardless
        # of whether it is labeled A or B. This simulates an unbiased judge.
        a_block = prompt.split("TRANSCRIPT A:")[1].split("TRANSCRIPT B:")[0]
        winner = "A" if "VARIANT_TAG" in a_block else "B"
        return json.dumps({
            "winner": winner,
            "per_dimension": {d: winner for d in cl.JUDGE_DIMENSIONS},
            "reason": "variant transcript is more grounded",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", content_aware_judge)
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--target", "turn_taking", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["overall_counts"] == {"variant": 1}
    assert report["per_dimension_counts"]["turn_taking"] == {"variant": 1}
    assert report["target_win_rate"] == 1.0


def test_ab_pairwise_judge_disagreement_results_in_tie(tmp_path, monkeypatch):
    """A positionally-biased judge that always picks "A" regardless of
    content disagrees between the two swapped orientations, so the
    combined verdict must be a tie, never a spurious win."""
    monkeypatch.setattr(sdw, "run_simulation", _make_fake_run_simulation())

    def positionally_biased_judge(*, prompt, system_prompt, model, temperature):
        return json.dumps({
            "winner": "A",
            "per_dimension": {d: "A" for d in cl.JUDGE_DIMENSIONS},
            "reason": "always A",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", positionally_biased_judge)
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["overall_counts"] == {"tie": 1}
    assert all(counts == {"tie": 1} for counts in report["per_dimension_counts"].values())


def _make_fake_run_simulation():
    def fake_run_simulation(*, concept, default_model, run_index, stage_only, mode, recipe_context, **kwargs):
        tag = "VARIANT_TAG" if sdw._SHARED_CHARACTER_RULES == "VARIANT_RULES" else "CONTROL_TAG"
        return {"messages": _messages(tag)}
    return fake_run_simulation


# ---------------------------------------------------------------------------
# ab: --max-calls abort writes a partial, aborted result
# ---------------------------------------------------------------------------


def test_ab_aborts_before_exceeding_max_calls(tmp_path, monkeypatch):
    # Each arm returns a 2-message transcript -> 2 calls; a pair costs 4.
    # max_calls=4 means pair 1 completes exactly at budget, and pair 2's
    # control call is refused before it happens.
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "2",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--max-calls", "4", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert report["completed_pairs"] == 1
    assert report["requested_runs"] == 2
    assert report["calls_used"] == 4


# ---------------------------------------------------------------------------
# ab: results file schema
# ---------------------------------------------------------------------------


def test_ab_results_file_has_expected_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    for key in (
        "command", "concept", "stage", "variant_file", "variant_name", "variant_keys",
        "requested_runs", "completed_pairs", "aborted", "max_calls", "calls_used",
        "dry_run", "target_dimension", "overall_counts", "per_dimension_counts",
        "target_win_rate", "worst_other_dimension_loss_rate", "metric_deltas",
        "cost_summary", "decision_rule", "pairs", "results_file", "generated_at",
    ):
        assert key in report, f"missing results key: {key}"
    assert report["command"] == "ab"


# ---------------------------------------------------------------------------
# ab: EXPERIMENTS.md append + --no-log
# ---------------------------------------------------------------------------


def test_ab_appends_experiments_row_with_header_on_first_write(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "lab" / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(results_dir),
    ])

    log_path = tmp_path / "lab" / "EXPERIMENTS.md"
    text = log_path.read_text()
    assert text.count("| Date | Experiment ID |") == 1
    assert text.count("\n") >= 3  # title + header + separator + >=1 row


def test_ab_no_log_does_not_touch_experiments_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "lab" / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    assert not (tmp_path / "lab" / "EXPERIMENTS.md").exists()


def test_ab_tolerates_and_appends_to_an_existing_experiments_file(tmp_path, monkeypatch):
    """Simulates another implementer's already-authored EXPERIMENTS.md:
    the tool must not rewrite its header, only append."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    existing = "# Conversation Lab Experiments\n\nSome pre-existing prose and a table.\n"
    (lab_dir / "EXPERIMENTS.md").write_text(existing)

    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(lab_dir / "results"),
    ])

    text = (lab_dir / "EXPERIMENTS.md").read_text()
    assert text.startswith(existing)
    assert "| Test Muffins |" not in text  # concept isn't a column; sanity the row format
    assert text.count("\n") > existing.count("\n")  # a row was appended


# ---------------------------------------------------------------------------
# ab: model resolution fails loud without --dry-run
# ---------------------------------------------------------------------------


def test_ab_without_dry_run_fails_loud_when_dialogue_model_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("DIALOGUE_MODEL", raising=False)
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    with pytest.raises(SystemExit, match="DIALOGUE_MODEL is not set"):
        cl.main([
            "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(variant_path), "--recipe-context", "anchor",
            "--results-dir", str(tmp_path / "results"),
        ])


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def test_baseline_local_reads_real_episode_and_writes_results(tmp_path):
    results_dir = tmp_path / "results"
    cl.main(["baseline", "2026-W36", "--local", "--results-dir", str(results_dir)])

    result_file = results_dir / "2026-W36-baseline.json"
    assert result_file.exists()
    report = json.loads(result_file.read_text())
    assert report["command"] == "baseline"
    assert report["source"] == "local"
    assert "monday" in report["days"]
    monday = report["days"]["monday"]
    assert monday["summary"]["message_count"] == monday["message_count"]
    assert "judge" in monday


def test_load_episode_falls_back_to_local_on_cdn_failure(monkeypatch, capsys):
    def raising_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("no network in test sandbox")

    monkeypatch.setattr(cl.urllib.request, "urlopen", raising_urlopen)
    episode = cl._load_episode("2026-W36", local=False)
    assert episode["_lab_source"] == "local"
    assert "STALE MIRROR" in capsys.readouterr().err


def test_load_episode_cdn_success_path_is_monkeypatched(monkeypatch):
    payload = json.dumps({"concept": "From CDN", "stages": {}}).encode("utf-8")

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(cl.urllib.request, "urlopen", lambda *a, **k: _FakeResponse())
    episode = cl._load_episode("does-not-matter", local=False)
    assert episode["_lab_source"] == "cdn"
    assert episode["concept"] == "From CDN"


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


def test_calibrate_degradations_are_deterministic_for_a_seed():
    dialogue = [
        {"character": "Margaret Chen", "message": "one"},
        {"character": "Marcus Reid", "message": "two"},
        {"character": "Julian Torres", "message": "three"},
        {"character": "Devon Park", "message": "four"},
        {"character": "Stephanie 'Steph' Whitmore", "message": "five"},
    ]
    shuffled_a = cl._shuffle_turns(dialogue, seed=7)
    shuffled_b = cl._shuffle_turns(dialogue, seed=7)
    assert shuffled_a == shuffled_b

    rotated_a = cl._rotate_speakers(dialogue, seed=1)
    rotated_b = cl._rotate_speakers(dialogue, seed=99)
    assert rotated_a == rotated_b
    assert [m["character"] for m in rotated_a] == [
        "Marcus Reid", "Julian Torres", "Devon Park", "Stephanie 'Steph' Whitmore", "Margaret Chen",
    ]
    assert [m["message"] for m in rotated_a] == [m["message"] for m in dialogue]


def test_calibrate_dry_run_makes_zero_judge_calls_and_writes_results(tmp_path, monkeypatch):
    def fail_judge(**kwargs):
        raise AssertionError("generate_judge_response must not be called in --dry-run")

    monkeypatch.setattr(model_router, "generate_judge_response", fail_judge)
    results_dir = tmp_path / "results"

    cl.main([
        "calibrate", "--from-episode", "2026-W36", "--stage", "monday", "--local",
        "--runs", "2", "--dry-run", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-calibrate-*.json"))
    report = json.loads(result_file.read_text())
    assert report["dry_run"] is True
    assert set(report["degradations"].keys()) == {"shuffled_order", "rotated_speakers"}
    for info in report["degradations"].values():
        assert info["attempted"] == 2
