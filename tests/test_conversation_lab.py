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
def _block_network(monkeypatch, tmp_path):
    """Every test in this file must be network-free by default, and must
    never reach docs/conversation-lab/ even when a test omits
    --results-dir/--no-log/--experiments-log - the slice-2 suite appended
    ~15 junk rows to the TRACKED EXPERIMENTS.md per run this way, because
    the module's real DEFAULT_RESULTS_DIR/DEFAULT_EXPERIMENTS_LOG point
    there and every omitted flag falls back to them.
    """
    monkeypatch.setattr(cl.urllib.request, "urlopen", _fail_urlopen)
    monkeypatch.setattr(cl, "DEFAULT_RESULTS_DIR", tmp_path / "_default_results")
    monkeypatch.setattr(cl, "DEFAULT_EXPERIMENTS_LOG", tmp_path / "_default_results" / "EXPERIMENTS.md")
    monkeypatch.setattr(cl, "_cost_summary_failure_warned", False)


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
    log_path = tmp_path / "lab" / "EXPERIMENTS.md"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(results_dir), "--experiments-log", str(log_path),
    ])

    text = log_path.read_text()
    assert text.count("| Date | Experiment ID |") == 1
    assert text.count("\n") >= 3  # title + header + separator + >=1 row


def test_ab_experiments_log_is_independent_of_results_dir(tmp_path, monkeypatch):
    """--results-dir must never be used to derive the log path (#6492
    review finding c) - pointing results somewhere unrelated must not
    silently redirect EXPERIMENTS.md too."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "somewhere" / "unrelated" / "results"
    log_path = tmp_path / "lab" / "EXPERIMENTS.md"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(results_dir), "--experiments-log", str(log_path),
    ])

    assert log_path.exists()
    assert not (results_dir.parent / "EXPERIMENTS.md").exists()


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


def test_ab_without_experiments_log_or_no_log_never_touches_tracked_file(tmp_path, monkeypatch):
    """Regression guard for the bug this slice fixes: prior to redirecting
    DEFAULT_RESULTS_DIR/DEFAULT_EXPERIMENTS_LOG in the autouse fixture
    above, a test that omitted --experiments-log/--no-log/--results-dir
    appended a junk row straight into the TRACKED
    docs/conversation-lab/EXPERIMENTS.md every time it ran. The real file
    must be byte-identical before and after an `ab` invocation that passes
    only --results-dir."""
    real_log = cl.DEFAULT_LAB_DIR / "EXPERIMENTS.md"
    before = real_log.read_bytes()

    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(tmp_path / "results"),
        # Deliberately no --experiments-log and no --no-log.
    ])

    after = real_log.read_bytes()
    assert after == before


def test_append_experiments_row_inserts_into_table_not_after_later_sections(tmp_path, monkeypatch):
    """finding (2): _append_experiments_row used to append unconditionally
    at end-of-file, which lands a new row AFTER whatever section follows
    the Experiments table (e.g. '## Heat map baseline') instead of inside
    the table itself."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    log_path = lab_dir / "EXPERIMENTS.md"
    log_path.write_text(
        "# Conversation Lab Experiments\n\n"
        "## Experiments\n\n"
        + cl._EXPERIMENTS_TABLE_HEADER_LINE
        + cl._EXPERIMENTS_TABLE_SEPARATOR_LINE
        + "\n"
        "## Heat map baseline v0\n\n"
        "Some unrelated section content that must stay AFTER the table.\n"
    )

    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(lab_dir / "results"),
        "--experiments-log", str(log_path),
    ])

    text = log_path.read_text()
    experiments_idx = text.index("## Experiments")
    heat_map_idx = text.index("## Heat map baseline")
    row_idx = text.index("| _SHARED_CHARACTER_RULES |")
    assert experiments_idx < row_idx < heat_map_idx


def test_append_experiments_row_finds_table_after_prose_paragraph(tmp_path, monkeypatch):
    """slice-3 review (high): the real docs/conversation-lab/EXPERIMENTS.md has a
    prose paragraph between '## Experiments' and the table. Looking only at
    the first non-blank line under the heading synthesized a SECOND header
    and split the table. The row must land in the existing table, and the
    file must end up with exactly one header line."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    log_path = lab_dir / "EXPERIMENTS.md"
    log_path.write_text(
        "# Conversation Lab Experiments\n\n"
        "## Experiments\n\n"
        "Empty except this header - the tool appends a row here each time an\n"
        "offline A/B experiment completes. Do not hand-edit rows.\n\n"
        + cl._EXPERIMENTS_TABLE_HEADER_LINE
        + cl._EXPERIMENTS_TABLE_SEPARATOR_LINE
        + "\n"
        "## Heat map baseline v0\n\n"
        "Section content that must stay AFTER the table.\n"
    )
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(tmp_path / "results"),
        "--experiments-log", str(log_path),
    ])
    text = log_path.read_text()
    assert text.count("| Date") == 1, "a second table header was synthesized"
    lines = text.splitlines()
    header_i = next(i for i, l in enumerate(lines) if l.startswith("| Date"))
    assert lines[header_i + 1].startswith("|--")
    assert lines[header_i + 2].startswith("| "), "row did not land right after the separator"
    assert text.index("| 20") < text.index("## Heat map baseline")
    assert "Do not hand-edit rows." in text


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
        "--experiments-log", str(lab_dir / "EXPERIMENTS.md"),
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


# ---------------------------------------------------------------------------
# JUDGE_MODEL fails loud (never backend.config.config.judge_model's silent
# sonnet default) - finding (a)
# ---------------------------------------------------------------------------


def test_ab_without_dry_run_fails_loud_when_judge_model_unset(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.delenv("JUDGE_MODEL", raising=False)
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    with pytest.raises(SystemExit, match="JUDGE_MODEL is not set"):
        cl.main([
            "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(variant_path), "--recipe-context", "anchor",
            "--results-dir", str(tmp_path / "results"),
        ])


def test_calibrate_without_dry_run_fails_loud_when_judge_model_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("JUDGE_MODEL", raising=False)

    with pytest.raises(SystemExit, match="JUDGE_MODEL is not set"):
        cl.main([
            "calibrate", "--from-episode", "2026-W36", "--stage", "monday", "--local",
            "--results-dir", str(tmp_path / "results"),
        ])


# ---------------------------------------------------------------------------
# Placeholder-concept fallback - finding (b). 2026-W32/W27/W33/W11 are real
# local mirrors whose top-level `concept` is still the placeholder even
# though the baker already picked a real dish name.
# ---------------------------------------------------------------------------


def test_baseline_reports_real_title_not_placeholder_concept(tmp_path):
    results_dir = tmp_path / "results"
    cl.main(["baseline", "2026-W32", "--local", "--results-dir", str(results_dir)])

    report = json.loads((results_dir / "2026-W32-baseline.json").read_text())
    assert report["concept"] != cl.PLACEHOLDER_CONCEPT
    assert report["concept"] == "Miso Ginger Donburi Cups"


def test_calibrate_judge_prompt_never_contains_placeholder_concept(tmp_path, monkeypatch):
    captured_prompts: list[str] = []

    def capture_judge(*, prompt, system_prompt, model, temperature):
        captured_prompts.append(prompt)
        return json.dumps({
            "winner": "tie",
            "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "ok",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", capture_judge)
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    cl.main([
        "calibrate", "--from-episode", "2026-W32", "--stage", "monday", "--local",
        "--runs", "1", "--results-dir", str(tmp_path / "results"),
    ])

    assert captured_prompts, "the judge should have been called"
    for prompt in captured_prompts:
        assert cl.PLACEHOLDER_CONCEPT not in prompt


def test_calibrate_falls_back_to_monday_recipe_data_for_recipe_context(tmp_path, monkeypatch):
    """2026-W32's tuesday stage carries dialogue but no recipe_data of its
    own - calibrate must fall back to monday's, exactly like ab's
    _resolve_recipe_context."""
    captured_prompts: list[str] = []

    def capture_judge(*, prompt, system_prompt, model, temperature):
        captured_prompts.append(prompt)
        return json.dumps({
            "winner": "tie",
            "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "ok",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", capture_judge)
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    cl.main([
        "calibrate", "--from-episode", "2026-W32", "--stage", "tuesday", "--local",
        "--runs", "1", "--results-dir", str(tmp_path / "results"),
    ])

    assert captured_prompts, "the judge should have been called"
    assert "This week's recipe:" in captured_prompts[0]


# ---------------------------------------------------------------------------
# _judge_info_for_stage treats an empty stage-level dict as missing -
# finding (g)
# ---------------------------------------------------------------------------


def test_judge_info_for_stage_treats_empty_stage_dict_as_missing():
    episode = {
        "judge_scores": {"monday": {"title_fidelity": 4}},
        "judge_weakest": {"monday": ["voice_distinctiveness"]},
        "judge_reason": {"monday": "ok"},
    }
    stage_data = {"judge_scores": {}, "judge_weakest": [], "judge_reason": ""}

    info = cl._judge_info_for_stage(episode, stage_data, "monday")

    assert info["scores"] == {"title_fidelity": 4}
    assert info["weakest"] == ["voice_distinctiveness"]
    assert info["reason"] == "ok"


# ---------------------------------------------------------------------------
# _judge_orientation normalises case/whitespace and rejects garbage verdicts
# instead of silently mapping them to "tie" - finding (e)
# ---------------------------------------------------------------------------


def test_normalize_verdict_value_accepts_case_and_whitespace_variants():
    assert cl._normalize_verdict_value(" a ", field="winner") == "A"
    assert cl._normalize_verdict_value("b", field="winner") == "B"
    assert cl._normalize_verdict_value("Tie", field="winner") == "tie"
    assert cl._normalize_verdict_value(" TIE", field="winner") == "tie"


def test_normalize_verdict_value_raises_on_garbage():
    with pytest.raises(cl.ConversationLabError, match="invalid winner verdict"):
        cl._normalize_verdict_value("C", field="winner")
    with pytest.raises(cl.ConversationLabError, match=r"invalid per_dimension\.turn_taking verdict"):
        cl._normalize_verdict_value("unsure", field="per_dimension.turn_taking")


# ---------------------------------------------------------------------------
# Any exception mid-run writes a partial, error-flagged result before
# re-raising - finding (d). Also exercises finding (e): an invalid judge
# verdict is what triggers the exception here.
# ---------------------------------------------------------------------------


def test_ab_writes_partial_result_on_exception_mid_run(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    call_count = {"n": 0}

    def flaky_judge(**kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:  # both orientations of pair 1: clean tie
            return json.dumps({
                "winner": "tie",
                "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
                "reason": "ok",
            })
        # pair 2's first orientation: a garbage winner value.
        return json.dumps({
            "winner": "C",
            "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "bad",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", flaky_judge)

    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    with pytest.raises(SystemExit):
        cl.main([
            "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "2",
            "--variant", str(variant_path), "--recipe-context", "anchor",
            "--results-dir", str(results_dir),
        ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert "error" in report
    assert "invalid winner verdict" in report["error"]
    assert report["completed_pairs"] == 1  # pair 1 survives; pair 2 never finished


def test_calibrate_writes_partial_result_on_exception_mid_run(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")
    call_count = {"n": 0}

    def flaky_judge(**kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:  # shuffled_order's first run: clean tie
            return json.dumps({
                "winner": "tie",
                "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
                "reason": "ok",
            })
        return json.dumps({
            "winner": "nope",
            "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "bad",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", flaky_judge)
    results_dir = tmp_path / "results"

    with pytest.raises(SystemExit):
        cl.main([
            "calibrate", "--from-episode", "2026-W36", "--stage", "monday", "--local",
            "--runs", "2", "--results-dir", str(results_dir),
        ])

    [result_file] = list(results_dir.glob("*-calibrate-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert "error" in report
    # shuffled_order's one successful pair (run 1) must have survived the
    # crash in its second run - "attempted" counts the started run (2),
    # but only 1 pair actually finished and was recorded.
    assert len(report["degradations"]["shuffled_order"]["pairs"]) == 1


# ---------------------------------------------------------------------------
# --max-calls estimates the next generation's cost from the last observed
# control/variant call count, not a flat 1 - finding (f)
# ---------------------------------------------------------------------------


def test_ab_max_calls_estimate_prevents_a_second_pair_from_starting(tmp_path, monkeypatch):
    """Each arm returns a 5-message transcript (5 calls via the message-
    count fallback in --dry-run). With max_calls=11, pair 1 costs 10
    (unavoidable - there is no observation yet before it runs). The fix
    under test is what happens next: pair 2's control-arm check must use
    the *observed* 5-call cost from pair 1, not a flat 1, so it refuses to
    start pair 2 at all instead of overshooting into it."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X", count=5)})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "3",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--max-calls", "11", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert report["completed_pairs"] == 1
    assert report["calls_used"] == 10  # no overshoot into pair 2 at all


# ---------------------------------------------------------------------------
# --max-cost aborts once total_cost has already reached the cap - finding
# (h)
# ---------------------------------------------------------------------------


def test_would_exceed_cost_true_once_at_or_over_cap(monkeypatch):
    monkeypatch.setattr(model_router, "get_cost_summary", lambda: {"total_cost": 5.0})
    assert cl._would_exceed_cost(5.0) is True
    assert cl._would_exceed_cost(5.01) is False


def test_would_exceed_cost_fails_open_on_a_read_error(monkeypatch):
    def raising_get_cost_summary():
        raise RuntimeError("cost log unavailable")

    monkeypatch.setattr(model_router, "get_cost_summary", raising_get_cost_summary)
    assert cl._would_exceed_cost(1.0) is False


def test_would_exceed_cost_respects_a_per_variant_baseline(monkeypatch):
    """ab --sweep's per-variant cap: (total - baseline) >= max_cost, not
    the absolute total - a $6 baseline with a $5 cap and a $9 total should
    NOT trip (delta is only $3), even though $9 alone would."""
    monkeypatch.setattr(model_router, "get_cost_summary", lambda: {"total_cost": 9.0})
    assert cl._would_exceed_cost(5.0) is True  # absolute cap: 9 >= 5
    assert cl._would_exceed_cost(5.0, baseline=6.0) is False  # delta: 9-6=3 < 5
    assert cl._would_exceed_cost(5.0, baseline=3.0) is True  # delta: 9-3=6 >= 5


def test_would_exceed_cost_warns_once_on_stderr_on_first_failure(monkeypatch, capsys):
    def raising_get_cost_summary():
        raise RuntimeError("cost log unavailable")

    monkeypatch.setattr(model_router, "get_cost_summary", raising_get_cost_summary)

    assert cl._would_exceed_cost(1.0) is False
    first_err = capsys.readouterr().err
    assert "WARNING" in first_err
    assert "get_cost_summary" in first_err

    assert cl._would_exceed_cost(1.0) is False
    second_err = capsys.readouterr().err
    assert second_err == ""


def test_ab_aborts_when_cost_cap_already_reached(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    monkeypatch.setattr(model_router, "get_cost_summary", lambda: {"total_cost": 10.0})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "2",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--max-cost", "5.0", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert report["completed_pairs"] == 0
    assert report["max_cost"] == 5.0


def test_ab_default_max_cost_is_five_dollars(tmp_path, monkeypatch):
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
    assert report["max_cost"] == 5.00


# ---------------------------------------------------------------------------
# --max-calls is DERIVED per mode when omitted - finding (3)
# ---------------------------------------------------------------------------


def test_max_turns_for_stage_floors_at_ten():
    # monday's TICKS_RANGE upper bound is already 10.
    assert cl._max_turns_for_stage("monday") == 10
    # thursday's upper bound is 5 - floored up to 10.
    assert cl._max_turns_for_stage("thursday") == 10
    # An unknown stage falls back to (4, 6) - still floored to 10.
    assert cl._max_turns_for_stage("not-a-real-day") == 10


def test_ab_single_concept_default_max_calls_is_120(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "50",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["max_calls"] == 120


def test_ab_testbed_default_max_calls_is_derived_from_panel_size_and_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
        {"id": "s2", "concept": "Scenario Two", "recipe_context": "anchor two"},
    ])
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--stage", "monday", "--variant", str(variant_path),
        "--testbed", str(testbed_path), "--runs", "2", "--dry-run",
        "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-testbed-*.json"))
    report = json.loads(result_file.read_text())
    # monday's max_turns is 10 -> 2 scenarios * 2 runs * (3*10 + 2) = 128
    # (3x, not 2x, so live-mode retries do not abort a normal variant).
    assert report["max_calls"] == 128
    assert report["max_calls_derived"] is True
    assert report["panel_size"] == 2


def test_ab_testbed_explicit_max_calls_is_not_rederived(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
    ])
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--stage", "monday", "--variant", str(variant_path),
        "--testbed", str(testbed_path), "--runs", "1", "--dry-run",
        "--max-calls", "4", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-testbed-*.json"))
    report = json.loads(result_file.read_text())
    assert report["max_calls"] == 4
    assert report["max_calls_derived"] is False


# ---------------------------------------------------------------------------
# calibrate --dry-run reports "DRY RUN - no signal", never GRADER OK/SUSPECT
# - finding (4)
# ---------------------------------------------------------------------------


def test_calibrate_dry_run_verdict_is_dry_run_not_grader(tmp_path):
    results_dir = tmp_path / "results"
    cl.main([
        "calibrate", "--from-episode", "2026-W36", "--stage", "monday", "--local",
        "--runs", "1", "--dry-run", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-calibrate-*.json"))
    report = json.loads(result_file.read_text())
    for info in report["degradations"].values():
        assert info["verdict"] == "DRY RUN - no signal"
        assert "GRADER" not in info["verdict"]


# ---------------------------------------------------------------------------
# ab --concept/--runs are required unless --testbed is passed
# ---------------------------------------------------------------------------


def test_ab_requires_concept_without_testbed(tmp_path):
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})
    with pytest.raises(SystemExit, match="--concept is required"):
        cl.main([
            "ab", "--stage", "monday", "--runs", "1", "--variant", str(variant_path),
            "--recipe-context", "anchor", "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_requires_runs_without_testbed(tmp_path):
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})
    with pytest.raises(SystemExit, match="--runs is required"):
        cl.main([
            "ab", "--concept", "X", "--stage", "monday", "--variant", str(variant_path),
            "--recipe-context", "anchor", "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_requires_variant_or_sweep(tmp_path):
    with pytest.raises(SystemExit, match="--variant is required unless --sweep"):
        cl.main([
            "ab", "--concept", "X", "--stage", "monday", "--runs", "1",
            "--recipe-context", "anchor", "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_variant_and_sweep_are_mutually_exclusive(tmp_path):
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    with pytest.raises(SystemExit, match="pass --variant or --sweep, not both"):
        cl.main([
            "ab", "--stage", "monday", "--variant", str(variant_path), "--sweep", str(sweep_dir),
            "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])


# ---------------------------------------------------------------------------
# ab --testbed - finding (i)
# ---------------------------------------------------------------------------


def _write_testbed(tmp_path, scenarios: list[dict]) -> "cl.Path":
    path = tmp_path / "testbed.json"
    path.write_text(json.dumps({"scenarios": scenarios}))
    return path


def test_ab_testbed_runs_every_scenario_and_aggregates(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", _make_fake_run_simulation())

    def content_aware_judge(*, prompt, system_prompt, model, temperature):
        a_block = prompt.split("TRANSCRIPT A:")[1].split("TRANSCRIPT B:")[0]
        winner = "A" if "VARIANT_TAG" in a_block else "B"
        return json.dumps({
            "winner": winner,
            "per_dimension": {d: winner for d in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "variant is more grounded",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", content_aware_judge)
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "category": "sweet", "cuisine": "French",
         "recipe_context": "anchor one", "source_episode": "2026-W01"},
        {"id": "s2", "concept": "Scenario Two", "category": "savory", "cuisine": None,
         "recipe_context": "anchor two", "source_episode": "2026-W02"},
    ])
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--stage", "monday", "--variant", str(variant_path),
        "--testbed", str(testbed_path), "--runs", "2", "--target", "turn_taking",
        "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-testbed-*.json"))
    report = json.loads(result_file.read_text())
    assert report["mode"] == "testbed"
    assert report["scenario_count"] == 2
    assert len(report["pairs"]) == 4  # 2 scenarios x 2 runs each
    assert {p["scenario_id"] for p in report["pairs"]} == {"s1", "s2"}
    assert report["completed_pairs"] == 4
    assert report["overall_counts"] == {"variant": 4}
    assert len(report["scenarios"]) == 2
    for scenario in report["scenarios"]:
        assert scenario["completed_pairs"] == 2
        assert scenario["target_win_rate"] == 1.0


def test_ab_testbed_default_runs_is_three(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
    ])
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--stage", "monday", "--variant", str(variant_path),
        "--testbed", str(testbed_path), "--dry-run", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-testbed-*.json"))
    report = json.loads(result_file.read_text())
    assert report["runs_per_scenario"] == 3
    assert report["completed_pairs"] == 3


def test_ab_testbed_forbids_concept_and_recipe_context(tmp_path):
    testbed_path = _write_testbed(tmp_path, [{"id": "s1", "concept": "C", "recipe_context": "r"}])
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})

    with pytest.raises(SystemExit, match="cannot be combined"):
        cl.main([
            "ab", "--stage", "monday", "--variant", str(variant_path), "--testbed", str(testbed_path),
            "--concept", "Not Allowed", "--runs", "1", "--dry-run",
            "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_testbed_missing_file_exits(tmp_path):
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "V"})
    with pytest.raises(SystemExit, match="testbed file not found"):
        cl.main([
            "ab", "--stage", "monday", "--variant", str(variant_path),
            "--testbed", str(tmp_path / "does-not-exist.json"), "--dry-run",
            "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_testbed_writes_partial_result_on_exception_in_a_later_scenario(tmp_path, monkeypatch):
    """finding (d) in the --testbed loop specifically: scenario 1 must
    survive intact in the written partial result even though scenario 2
    is what crashes."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    call_count = {"n": 0}

    def flaky_judge(**kwargs):
        call_count["n"] += 1
        if call_count["n"] <= 2:  # scenario s1's one pair, both orientations: clean tie
            return json.dumps({
                "winner": "tie",
                "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
                "reason": "ok",
            })
        return json.dumps({  # scenario s2's first orientation: garbage
            "winner": "nope",
            "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "bad",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", flaky_judge)

    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
        {"id": "s2", "concept": "Scenario Two", "recipe_context": "anchor two"},
    ])
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    with pytest.raises(SystemExit):
        cl.main([
            "ab", "--stage", "monday", "--variant", str(variant_path),
            "--testbed", str(testbed_path), "--runs", "1",
            "--results-dir", str(results_dir),
        ])

    [result_file] = list(results_dir.glob("*-ab-testbed-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert "error" in report
    assert report["completed_pairs"] == 1
    assert report["pairs"][0]["scenario_id"] == "s1"
    assert {s["id"] for s in report["scenarios"]} == {"s1", "s2"}
    assert report["scenarios"][1]["completed_pairs"] == 0  # s2 never finished a pair


def test_default_testbed_file_has_five_scenarios_with_real_titles():
    scenarios = cl._load_testbed(cl.DEFAULT_TESTBED_PATH)
    assert len(scenarios) == 5
    assert {s["id"] for s in scenarios} == {"2026-W36", "2026-W32", "2026-W27", "2026-W33", "2026-W11"}
    for scenario in scenarios:
        assert cl.PLACEHOLDER_CONCEPT not in scenario["concept"]
        assert scenario["recipe_context"]


# ---------------------------------------------------------------------------
# Lab-only judge dimensions - finding (j)
# ---------------------------------------------------------------------------


def test_ab_target_accepts_a_lab_only_dimension(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--target", "emotional_range", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["target_dimension"] == "emotional_range"
    assert "emotional_range" in report["per_dimension_counts"]
    assert "register_naturalness" in report["per_dimension_counts"]


def test_pairwise_judge_prompt_mentions_lab_only_dimensions():
    assert "emotional_range" in cl.PAIRWISE_JUDGE_SYSTEM_PROMPT
    assert "register_naturalness" in cl.PAIRWISE_JUDGE_SYSTEM_PROMPT
    assert set(cl.LAB_ONLY_JUDGE_DIMENSIONS) <= set(cl.ALL_JUDGE_DIMENSIONS)
    assert set(cl.JUDGE_DIMENSIONS) & set(cl.LAB_ONLY_JUDGE_DIMENSIONS) == set()


# ---------------------------------------------------------------------------
# `pairs`: blind human read + agreement scoring - finding (k)
# ---------------------------------------------------------------------------


def test_blind_order_is_deterministic_and_only_two_shapes():
    assert cl._blind_order(3) == cl._blind_order(3)
    orders = {cl._blind_order(i) for i in range(20)}
    assert orders <= {("control", "variant"), ("variant", "control")}


def test_pairs_show_prints_unlabeled_transcripts(tmp_path, capsys):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "pairs": [
            {"run_index": 1, "control_messages": _messages("CTRL"), "variant_messages": _messages("VAR"),
             "judge": {"overall": "variant"}},
        ],
    }))

    cl.main(["pairs", "--from", str(result_path), "--show"])

    out = capsys.readouterr().out
    assert "Pair 1" in out
    assert "--- A ---" in out
    assert "--- B ---" in out


def test_pairs_pick_records_and_computes_agreement(tmp_path):
    result_path = tmp_path / "result.json"
    order1 = cl._blind_order(1)
    order2 = cl._blind_order(2)
    pairs = [
        {"run_index": 1, "control_messages": _messages("C1"), "variant_messages": _messages("V1"),
         "judge": {"overall": "variant"}},
        {"run_index": 2, "control_messages": _messages("C2"), "variant_messages": _messages("V2"),
         "judge": {"overall": "control"}},
    ]
    result_path.write_text(json.dumps({"pairs": pairs}))

    cl.main(["pairs", "--from", str(result_path), "--pick", "1:A,2:A"])

    report = json.loads(result_path.read_text())
    assert report["human_picks"]["1"] == order1[0]
    assert report["human_picks"]["2"] == order2[0]

    judge_overall = {1: "variant", 2: "control"}
    mapped = {1: order1[0], 2: order2[0]}
    compared = 2
    agreements = sum(1 for i in (1, 2) if judge_overall[i] == mapped[i])
    assert report["human_judge_agreement_rate"] == round(agreements / compared, 4)
    assert report["human_judge_agreed_count"] + report["human_judge_disagreed_count"] == compared
    assert report["human_judge_tie_count"] == 0


def test_pairs_pick_agreement_rate_excludes_judge_ties(tmp_path):
    """finding (6): a judge "tie" carries no direction to agree or
    disagree with, so it must be reported separately (judge-tie count) and
    excluded from the agreement RATE's denominator entirely - not folded
    into either the agreed or disagreed bucket."""
    result_path = tmp_path / "result.json"
    order1 = cl._blind_order(1)
    order2 = cl._blind_order(2)
    order3 = cl._blind_order(3)
    pairs = [
        {"run_index": 1, "control_messages": _messages("C1"), "variant_messages": _messages("V1"),
         "judge": {"overall": "variant"}},
        {"run_index": 2, "control_messages": _messages("C2"), "variant_messages": _messages("V2"),
         "judge": {"overall": "tie"}},
        {"run_index": 3, "control_messages": _messages("C3"), "variant_messages": _messages("V3"),
         "judge": {"overall": "control"}},
    ]
    result_path.write_text(json.dumps({"pairs": pairs}))

    # Pick "A" for every position.
    cl.main(["pairs", "--from", str(result_path), "--pick", "1:A,2:A,3:A"])

    report = json.loads(result_path.read_text())
    mapped = {1: order1[0], 2: order2[0], 3: order3[0]}
    judge_overall = {1: "variant", 2: "tie", 3: "control"}

    expected_agreed = sum(
        1 for i in (1, 3) if judge_overall[i] == mapped[i]
    )
    expected_disagreed = 2 - expected_agreed

    assert report["human_judge_tie_count"] == 1
    assert report["human_judge_agreed_count"] == expected_agreed
    assert report["human_judge_disagreed_count"] == expected_disagreed
    assert report["human_judge_agreed_count"] + report["human_judge_disagreed_count"] == 2
    assert report["human_judge_agreement_rate"] == round(expected_agreed / 2, 4)


def test_pairs_pick_rejects_bad_spec(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "pairs": [{"run_index": 1, "control_messages": [], "variant_messages": [], "judge": {"overall": "tie"}}],
    }))
    with pytest.raises(SystemExit, match="bad --pick"):
        cl.main(["pairs", "--from", str(result_path), "--pick", "garbage"])


def test_pairs_pick_rejects_unknown_run_index(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "pairs": [{"run_index": 1, "control_messages": [], "variant_messages": [], "judge": {"overall": "tie"}}],
    }))
    with pytest.raises(SystemExit, match="unknown pair position"):
        cl.main(["pairs", "--from", str(result_path), "--pick", "99:A"])


def test_pairs_requires_show_or_pick(tmp_path):
    result_path = tmp_path / "result.json"
    result_path.write_text(json.dumps({
        "pairs": [{"run_index": 1, "control_messages": [], "variant_messages": [], "judge": {"overall": "tie"}}],
    }))
    with pytest.raises(SystemExit, match="pass --show, --pick"):
        cl.main(["pairs", "--from", str(result_path)])


def test_ab_result_stores_both_transcripts_per_pair(tmp_path, monkeypatch):
    """`pairs` needs the raw messages, not just the summaries - finding
    (k)'s prerequisite."""
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
    pair = report["pairs"][0]
    assert pair["control_messages"]
    assert pair["variant_messages"]


def test_pairs_addresses_by_position_not_run_index_in_a_testbed_result(tmp_path, monkeypatch):
    """A --testbed result's `pairs` list has every scenario's run_index
    restart at 1 - `pairs --show`/`--pick` must address by the pair's
    position in the file instead, or picks silently collide across
    scenarios."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
        {"id": "s2", "concept": "Scenario Two", "recipe_context": "anchor two"},
    ])
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--stage", "monday", "--variant", str(variant_path),
        "--testbed", str(testbed_path), "--runs", "1", "--dry-run",
        "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-testbed-*.json"))
    report = json.loads(result_file.read_text())
    # Both pairs share run_index=1 (one per scenario) - the collision this
    # test guards against.
    assert [p["run_index"] for p in report["pairs"]] == [1, 1]
    assert [p["scenario_id"] for p in report["pairs"]] == ["s1", "s2"]

    cl.main(["pairs", "--from", str(result_file), "--pick", "1:A,2:B"])

    updated = json.loads(result_file.read_text())
    assert set(updated["human_picks"].keys()) == {"1", "2"}
    assert updated["human_judge_agreement_rate"] is not None


# ---------------------------------------------------------------------------
# Metric-delta section stays generic over whatever summarize() returns -
# finding (l), already true in slice 1's implementation; confirmed here.
# ---------------------------------------------------------------------------


def test_metric_deltas_are_generic_over_every_numeric_summary_key(tmp_path, monkeypatch):
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
    pair = report["pairs"][0]
    expected_keys = cl._numeric_keys(pair["control_summary"])
    assert expected_keys  # summarize() returns at least one numeric key
    assert set(report["metric_deltas"].keys()) == set(expected_keys)


# ---------------------------------------------------------------------------
# ab --sweep: one shared control, many variants ranked against it - NEW (7)
# ---------------------------------------------------------------------------


def test_ab_sweep_generates_control_exactly_once_per_scenario_run(tmp_path, monkeypatch):
    """The whole point of --sweep: the control transcript for a given
    (scenario, run) is generated ONCE, then reused against every variant -
    not regenerated per variant."""
    original_rules = sdw._SHARED_CHARACTER_RULES
    call_log: list[str] = []

    def fake_run_simulation(*, concept, default_model, run_index, stage_only, mode, recipe_context, **kwargs):
        current = sdw._SHARED_CHARACTER_RULES
        call_log.append(current)
        tag = "VARIANT" if current != original_rules else "CONTROL"
        return {"messages": _messages(tag)}

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)

    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
        {"id": "s2", "concept": "Scenario Two", "recipe_context": "anchor two"},
    ])
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    (sweep_dir / "variant_a.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "A_RULES"}))
    (sweep_dir / "variant_b.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "B_RULES"}))

    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--sweep", str(sweep_dir), "--testbed", str(testbed_path), "--stage", "monday",
        "--runs", "1", "--dry-run", "--results-dir", str(results_dir),
    ])

    control_calls = [c for c in call_log if c == original_rules]
    assert len(control_calls) == 2  # 2 scenarios x 1 run, regardless of 2 variants
    assert len(call_log) == 6  # 2 control + 2 (variant_a) + 2 (variant_b)

    [result_file] = list(results_dir.glob("*-ab-sweep-*.json"))
    report = json.loads(result_file.read_text())
    assert report["control_transcripts_generated"] == 2


def test_ab_sweep_ranks_three_variants_and_result_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", _make_fake_run_simulation())

    def content_aware_judge(*, prompt, system_prompt, model, temperature):
        a_block = prompt.split("TRANSCRIPT A:")[1].split("TRANSCRIPT B:")[0]
        winner = "A" if "VARIANT_TAG" in a_block else "B"
        return json.dumps({
            "winner": winner,
            "per_dimension": {d: winner for d in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "variant is more grounded",
        })

    monkeypatch.setattr(model_router, "generate_judge_response", content_aware_judge)
    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")

    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
    ])
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (sweep_dir / f"{name}.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT_RULES"}))

    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--sweep", str(sweep_dir), "--testbed", str(testbed_path), "--stage", "monday",
        "--runs", "1", "--target", "turn_taking", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-sweep-*.json"))
    report = json.loads(result_file.read_text())

    assert report["command"] == "ab"
    assert report["mode"] == "sweep"
    assert set(report["variant_names"]) == {"alpha", "beta", "gamma"}
    assert report["control_transcripts_generated"] == 1
    assert len(report["ranking"]) == 3
    for entry in report["ranking"]:
        assert entry["target_wins"] == 1
        assert entry["overall_wins"] == 1
        assert entry["aborted"] is False

    for key in (
        "command", "mode", "stage", "sweep_dir", "variant_names", "testbed_path", "scenario_count",
        "panel_size", "runs_per_scenario", "aborted", "max_calls", "max_calls_derived",
        "max_cost_per_variant", "control_aborted", "control_calls_used", "control_cost",
        "control_transcripts_generated", "dry_run", "target_dimension", "decision_rule",
        "cost_summary", "variants", "ranking", "results_file", "generated_at",
    ):
        assert key in report, f"missing sweep results key: {key}"

    for variant_name in ("alpha", "beta", "gamma"):
        variant_report = report["variants"][variant_name]
        assert variant_report["completed_pairs"] == 1
        assert variant_report["pairs"][0]["control_messages"]
        assert variant_report["pairs"][0]["variant_messages"]


def test_ab_sweep_per_variant_cost_cap_abort_writes_partial(tmp_path, monkeypatch):
    """Erik's --max-cost cap is PER VARIANT: a variant's own spend is
    measured relative to the total observed when IT started, not from
    zero and not shared with any other variant."""
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})

    # Every generation call costs $6 (against a $5-per-variant cap): the
    # control loop's 2 calls put the running total at $12 before any
    # variant starts, then the lone variant's own first call pushes it to
    # $18 (delta so far: $6 >= $5, so its SECOND scenario never runs).
    state = {"total": 0.0}
    monkeypatch.setattr(model_router, "get_cost_summary", lambda: {"total_cost": state["total"], "total_calls": 0})

    real_run_arm_and_count = cl._run_arm_and_count

    def counting_run_arm_and_count(*args, **kwargs):
        state["total"] += 6.0
        return real_run_arm_and_count(*args, **kwargs)

    monkeypatch.setattr(cl, "_run_arm_and_count", counting_run_arm_and_count)

    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
        {"id": "s2", "concept": "Scenario Two", "recipe_context": "anchor two"},
    ])
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    (sweep_dir / "only.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "V"}))

    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--sweep", str(sweep_dir), "--testbed", str(testbed_path), "--stage", "monday",
        "--runs", "1", "--dry-run", "--max-cost", "5.0", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-sweep-*.json"))
    report = json.loads(result_file.read_text())
    assert report["aborted"] is True
    assert report["control_aborted"] is False
    assert report["control_transcripts_generated"] == 2

    variant_report = report["variants"]["only"]
    assert variant_report["aborted"] is True
    assert variant_report["completed_pairs"] == 1  # only the first scenario finished


def test_ab_sweep_dry_run_makes_zero_judge_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})

    def fail_judge(**kwargs):
        raise AssertionError("generate_judge_response must not be called in --dry-run")

    monkeypatch.setattr(model_router, "generate_judge_response", fail_judge)

    testbed_path = _write_testbed(tmp_path, [
        {"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"},
    ])
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    (sweep_dir / "a.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "V"}))
    (sweep_dir / "b.json").write_text(json.dumps({"_REACTION_DIRECTIVE": "extra beat"}))

    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--sweep", str(sweep_dir), "--testbed", str(testbed_path), "--stage", "monday",
        "--runs", "1", "--dry-run", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-sweep-*.json"))
    report = json.loads(result_file.read_text())
    assert report["dry_run"] is True
    for variant_report in report["variants"].values():
        for pair in variant_report["pairs"]:
            assert pair["judge"]["overall"] == "tie"


def test_ab_sweep_missing_directory_exits(tmp_path):
    with pytest.raises(SystemExit, match="--sweep directory not found"):
        cl.main([
            "ab", "--sweep", str(tmp_path / "does-not-exist"), "--stage", "monday",
            "--runs", "1", "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_sweep_empty_directory_exits(tmp_path):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    with pytest.raises(SystemExit, match="no \\*.json variant files"):
        cl.main([
            "ab", "--sweep", str(sweep_dir), "--stage", "monday",
            "--runs", "1", "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_sweep_forbids_concept(tmp_path):
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    (sweep_dir / "a.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "V"}))
    with pytest.raises(SystemExit, match="cannot be combined"):
        cl.main([
            "ab", "--sweep", str(sweep_dir), "--stage", "monday", "--concept", "Not Allowed",
            "--runs", "1", "--dry-run", "--results-dir", str(tmp_path / "results"),
        ])


def test_pairs_from_sweep_result_requires_variant_name(tmp_path, monkeypatch):
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages("X")})
    testbed_path = _write_testbed(tmp_path, [{"id": "s1", "concept": "Scenario One", "recipe_context": "anchor one"}])
    sweep_dir = tmp_path / "sweep"
    sweep_dir.mkdir()
    (sweep_dir / "only.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "V"}))
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--sweep", str(sweep_dir), "--testbed", str(testbed_path), "--stage", "monday",
        "--runs", "1", "--dry-run", "--results-dir", str(results_dir),
    ])
    [result_file] = list(results_dir.glob("*-ab-sweep-*.json"))

    with pytest.raises(SystemExit, match="pass --variant-name"):
        cl.main(["pairs", "--from", str(result_file), "--show"])

    cl.main(["pairs", "--from", str(result_file), "--pick", "1:A", "--variant-name", "only"])
    updated = json.loads(result_file.read_text())
    assert updated["variants"]["only"]["human_picks"]["1"] in ("control", "variant")
