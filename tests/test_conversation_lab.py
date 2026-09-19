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
import os
import re
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


def _fail_episode_load(*_args, **_kwargs):
    raise AssertionError("episode load was unexpected")


def _raise_snapshot_unavailable(*_args, **_kwargs):
    raise RuntimeError("snapshot unavailable")


def _fail_generation(*_args, **_kwargs):
    raise AssertionError("generated before load failure")


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


def _snapshot_episode() -> dict:
    recipe = {
        "title": "Snapshot Spiral Bites",
        "category": "sweet",
        "description": "A tender spiral with crisp edges.",
        "ingredients": [{"amount": "2 cups", "item": "flour"}],
        "instructions": ["Roll the dough into a log.", "Bake until golden."],
    }
    return {
        "episode_id": "snapshot-week",
        "concept": "Weekly Muffin Pan Recipe",
        "stages": {
            "monday": {"recipe_data": recipe},
            "tuesday": {"dialogue": _messages("real")},
        },
    }


def _judge_stub() -> str:
    return json.dumps({
        "winner": "tie",
        "per_dimension": {d: "tie" for d in cl.ALL_JUDGE_DIMENSIONS},
        "reason": "stub",
    })


def test_ab_uses_one_episode_snapshot_for_anchor_and_every_judge_orientation(tmp_path, monkeypatch):
    """The generator and both judge orientations must see one recipe revision."""
    episode = _snapshot_episode()
    episode["stages"]["tuesday"]["recipe_data"] = {
        "title": "Tuesday Override Bites",
        "category": "savory",
        "description": "A stage-specific crisp and tender bite.",
        "ingredients": [{"amount": "1 cup", "item": "cornmeal"}],
        "instructions": ["Stir the Tuesday batter.", "Bake until crisp."],
    }
    load_count = {"n": 0}
    anchors: list[str | None] = []
    prompts: list[str] = []

    def load_once(*_args, **_kwargs):
        load_count["n"] += 1
        if load_count["n"] > 1:
            raise AssertionError("episode snapshot was loaded more than once")
        return episode

    def fake_simulation(**kwargs):
        anchors.append(kwargs["recipe_context"])
        return {"messages": _messages("generated", count=1)}

    def fake_judge(*, prompt, **_kwargs):
        prompts.append(prompt)
        return _judge_stub()

    monkeypatch.setattr(cl, "_load_episode", load_once)
    monkeypatch.setattr(sdw, "run_simulation", fake_simulation)
    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)
    monkeypatch.setenv("DIALOGUE_MODEL", "test-dialogue")
    monkeypatch.setenv("JUDGE_MODEL", "test-judge")
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    cl.main([
        "ab", "--concept", "Snapshot Spiral Bites", "--stage", "tuesday", "--runs", "2",
        "--variant", str(variant_path), "--from-episode", "snapshot-week", "--local",
        "--max-calls", "20", "--no-log", "--results-dir", str(tmp_path / "results"),
    ])

    assert load_count["n"] == 1
    assert len(anchors) == 4  # control + variant for each of two runs
    assert len(set(anchors)) == 1
    assert anchors[0] == "This week's recipe: Tuesday Override Bites (savory). What it is: A stage-specific crisp and tender bite."
    assert len(prompts) == 4  # both A/B orientations for both runs
    assert len({p.split("RECIPE GROUND TRUTH", 1)[1].split("Expected cast", 1)[0] for p in prompts}) == 1
    assert all("Stir the Tuesday batter." in p for p in prompts)


def test_calibrate_uses_one_snapshot_and_monday_fallback_for_all_judges(tmp_path, monkeypatch):
    episode = _snapshot_episode()
    load_count = {"n": 0}
    prompts: list[str] = []

    def load_once(*_args, **_kwargs):
        load_count["n"] += 1
        if load_count["n"] > 1:
            raise AssertionError("calibration reloaded the episode")
        return episode

    monkeypatch.setattr(cl, "_load_episode", load_once)
    monkeypatch.setattr(model_router, "generate_judge_response", lambda *, prompt, **_kwargs: (prompts.append(prompt) or _judge_stub()))
    monkeypatch.setenv("JUDGE_MODEL", "test-judge")

    cl.main([
        "calibrate", "--from-episode", "snapshot-week", "--stage", "tuesday", "--runs", "2",
        "--max-calls", "20", "--results-dir", str(tmp_path / "results"),
    ])

    assert load_count["n"] == 1
    assert len(prompts) == 8  # 2 degradations x 2 runs x 2 orientations
    assert len({p.split("RECIPE GROUND TRUTH", 1)[1].split("Expected cast", 1)[0] for p in prompts}) == 1
    assert all("This week's recipe: Snapshot Spiral Bites" in p for p in prompts)
    assert all("Roll the dough into a log." in p for p in prompts)


def test_manual_recipe_context_does_not_load_or_supply_facts(tmp_path, monkeypatch):
    anchors: list[str | None] = []
    prompts: list[str] = []

    def fake_simulation(**kwargs):
        anchors.append(kwargs["recipe_context"])
        tag = "VARIANT" if sdw._SHARED_CHARACTER_RULES == "VARIANT_RULES" else "CONTROL"
        return {"messages": [{"character": "Margaret Chen", "message": f"{tag} generated"}]}

    def fake_judge(*, prompt, **_kwargs):
        prompts.append(prompt)
        return _judge_stub()

    monkeypatch.setattr(cl, "_load_episode", _fail_episode_load)
    monkeypatch.setattr(sdw, "run_simulation", fake_simulation)
    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)
    monkeypatch.setenv("DIALOGUE_MODEL", "test-dialogue")
    monkeypatch.setenv("JUDGE_MODEL", "test-judge")
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    cl.main([
        "ab", "--concept", "Manual Context", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "manual anchor",
        "--no-log", "--results-dir", str(tmp_path / "results"),
    ])

    assert len(anchors) == 2  # control + variant
    assert anchors == ["manual anchor", "manual anchor"]
    assert len(prompts) == 2  # both judge orientations
    assert all("RECIPE GROUND TRUTH" not in prompt for prompt in prompts)
    orientations = []
    for prompt in prompts:
        transcript_a = prompt.split("TRANSCRIPT A:\n", 1)[1].split("\n\nTRANSCRIPT B:", 1)[0]
        transcript_b = prompt.split("TRANSCRIPT B:\n", 1)[1].split("\n\nScore", 1)[0]
        orientations.append(("CONTROL generated" in transcript_a, "CONTROL generated" in transcript_b))
    assert orientations == [(True, False), (False, True)]


def test_episode_load_failure_propagates_before_generation(tmp_path, monkeypatch):
    monkeypatch.setenv("DIALOGUE_MODEL", "test-dialogue")
    monkeypatch.setenv("JUDGE_MODEL", "test-judge")
    monkeypatch.setattr(cl, "_load_episode", _raise_snapshot_unavailable)
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    variant_path = _write_variant(tmp_path, {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"})

    with pytest.raises(RuntimeError, match="snapshot unavailable"):
        cl.main([
            "ab", "--concept", "Load Failure", "--stage", "monday", "--runs", "1",
            "--variant", str(variant_path), "--from-episode", "missing", "--local",
            "--no-log", "--results-dir", str(tmp_path / "results"),
        ])


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


def test_legacy_v1_testbed_still_has_its_five_scenarios():
    """v1 is kept so pre-2026-09-15 results stay interpretable (#7201)."""
    scenarios = cl._load_testbed(cl.LEGACY_TESTBED_PATH)
    assert len(scenarios) == 5
    assert {s["id"] for s in scenarios} == {"2026-W36", "2026-W32", "2026-W27", "2026-W33", "2026-W11"}
    for scenario in scenarios:
        assert cl.PLACEHOLDER_CONCEPT not in scenario["concept"]
        assert scenario["recipe_context"]


def test_default_testbed_is_v2_with_real_titles():
    scenarios = cl._load_testbed(cl.DEFAULT_TESTBED_PATH)
    assert len(scenarios) >= 6
    # v1's five all survive into v2, plus the accented and long-method additions.
    assert {"2026-W36", "2026-W32", "2026-W27", "2026-W33", "2026-W11"} <= {s["id"] for s in scenarios}
    assert {"2026-W37", "2026-W38"} <= {s["id"] for s in scenarios}
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


# --- validation must precede any paid generation (Codex audit) -----------------

def test_validate_variant_rejects_a_partial_history_depth():
    """A missing 'late' key used to KeyError on the first Friday, mid-run."""
    with pytest.raises(cl.ConversationLabError) as exc:
        cl.validate_variant(sdw, {"HISTORY_DEPTH": {"early": (8, 4)}})
    assert "late" in str(exc.value)


def test_validate_variant_rejects_booleans_as_depths():
    """bool is a subclass of int in Python; True is not a history depth."""
    with pytest.raises(cl.ConversationLabError):
        cl.validate_variant(sdw, {"HISTORY_DEPTH": {"early": (True, 4), "late": (8, 4)}})


@pytest.mark.parametrize(
    "bad",
    [
        "not-a-dict",
        {"early": (8, 4)},
        {"early": (8, 4), "late": "nope"},
        {"early": (8, 4), "late": (0, 4)},
        {"early": (8, 4), "late": (8, 4, 2)},
    ],
)
def test_validate_variant_rejects_malformed_shapes(bad):
    with pytest.raises(cl.ConversationLabError):
        cl.validate_variant(sdw, {"HISTORY_DEPTH": bad})


def test_validate_variant_accepts_a_well_formed_override():
    cl.validate_variant(sdw, {"HISTORY_DEPTH": {"early": (10, 6), "late": (14, 10)}})


def test_apply_variant_validates_every_key_before_mutating_any():
    """An invalid second key must not leave the first one installed.

    _apply_variant raises before returning its restore map, so a partially
    applied variant left the module patched with nothing to restore from.
    """
    before = sdw._REACTION_DIRECTIVE
    with pytest.raises(cl.ConversationLabError):
        cl._apply_variant(
            sdw,
            {"_REACTION_DIRECTIVE": "patched", "HISTORY_DEPTH": {"early": (8, 4)}},
        )
    assert sdw._REACTION_DIRECTIVE == before, "module was left partially patched"


def test_apply_variant_round_trips_a_valid_multi_key_variant():
    before_directive = sdw._REACTION_DIRECTIVE
    before_depth = sdw.HISTORY_DEPTH
    original = cl._apply_variant(
        sdw,
        {"_REACTION_DIRECTIVE": "patched", "HISTORY_DEPTH": {"early": (10, 6), "late": (14, 10)}},
    )
    assert sdw._REACTION_DIRECTIVE == "patched"
    assert sdw.HISTORY_DEPTH == {"early": (10, 6), "late": (14, 10)}
    cl._restore_variant(sdw, original)
    assert sdw._REACTION_DIRECTIVE == before_directive
    assert sdw.HISTORY_DEPTH == before_depth


def test_a_malformed_variant_costs_nothing(monkeypatch):
    """Drive the REAL experiment path, not the validator directly.

    An earlier version of this test called validate_variant() itself, which
    proved nothing about ordering - it would still have passed if validation
    moved back behind paid generation, which is the exact bug (Codex). This one
    calls _generate_and_judge_pairs and asserts the generation function is never
    reached.
    """
    calls: list[str] = []

    def _record(*args, **kwargs):
        calls.append("generated")
        raise AssertionError("no generation may happen for a malformed variant")

    monkeypatch.setattr(cl, "_run_arm_and_count", _record)

    pairs: list[dict] = []
    with pytest.raises(cl.ConversationLabError):
        cl._generate_and_judge_pairs(
            concept="Test Concept",
            stage="tuesday",
            recipe_context=None,
            runs=2,
            variant={"HISTORY_DEPTH": {"early": (8, 4)}},   # missing "late"
            mode="openai",
            default_model="test-model",
            judge_model="test-judge",
            expected_cast=sdw.participants_for_day("tuesday"),
            budget=cl.CallBudget(max_calls=100),
            max_cost=5.0,
            dry_run=False,
            pairs=pairs,
        )

    assert calls == [], "a malformed variant reached a generation call"
    assert pairs == []


def test_a_valid_variant_does_reach_generation(monkeypatch):
    """Counterpart to the test above: prove it is the VALIDITY that gates spend.

    Without this, the test above would also pass if _generate_and_judge_pairs
    simply never generated anything.
    """
    calls: list[str] = []

    def _record(*args, **kwargs):
        calls.append("generated")
        raise RuntimeError("stop after the first generation attempt")

    monkeypatch.setattr(cl, "_run_arm_and_count", _record)

    pairs: list[dict] = []
    with pytest.raises(RuntimeError):
        cl._generate_and_judge_pairs(
            concept="Test Concept",
            stage="tuesday",
            recipe_context=None,
            runs=1,
            variant={"HISTORY_DEPTH": {"early": (10, 6), "late": (14, 10)}},
            mode="openai",
            default_model="test-model",
            judge_model="test-judge",
            expected_cast=sdw.participants_for_day("tuesday"),
            budget=cl.CallBudget(max_calls=100),
            max_cost=5.0,
            dry_run=False,
            pairs=pairs,
        )

    assert calls == ["generated"], "a valid variant should have reached generation"
    # and the module must not be left patched by the aborted run
    assert sdw.HISTORY_DEPTH != {"early": (10, 6), "late": (14, 10)}


# --- output-contract guards are sweepable levers -----------------------------

@pytest.mark.parametrize(
    "variant",
    [
        {"SHAPE_WINDOW": 0},
        {"SHAPE_WINDOW": True},
        {"SHAPE_WINDOW": "three"},
        {"SHAPE_MAX_IN_WINDOW": 0},
        {"WORD_BUDGET_TOLERANCE": 0.5},
        {"WORD_BUDGET_TOLERANCE": True},
        {"WORD_BUDGET_TOLERANCE": "loose"},
    ],
)
def test_guard_levers_reject_malformed_values(variant):
    with pytest.raises(cl.ConversationLabError):
        cl.validate_variant(sdw, variant)


def test_guard_levers_accept_a_strict_enforcement_sweep():
    """WORD_BUDGET_TOLERANCE 1.0 is the "hold them to the stated max" arm."""
    cl.validate_variant(
        sdw, {"WORD_BUDGET_TOLERANCE": 1.0, "SHAPE_WINDOW": 4, "SHAPE_MAX_IN_WINDOW": 1}
    )


def test_guard_levers_round_trip_through_apply_and_restore():
    before = (sdw.SHAPE_WINDOW, sdw.SHAPE_MAX_IN_WINDOW, sdw.WORD_BUDGET_TOLERANCE)
    original = cl._apply_variant(
        sdw, {"SHAPE_WINDOW": 5, "SHAPE_MAX_IN_WINDOW": 1, "WORD_BUDGET_TOLERANCE": 1.0}
    )
    assert (sdw.SHAPE_WINDOW, sdw.SHAPE_MAX_IN_WINDOW, sdw.WORD_BUDGET_TOLERANCE) == (5, 1, 1.0)
    cl._restore_variant(sdw, original)
    assert (sdw.SHAPE_WINDOW, sdw.SHAPE_MAX_IN_WINDOW, sdw.WORD_BUDGET_TOLERANCE) == before


# ---------------------------------------------------------------------------
# bench (#7314): single-arm characterization
# ---------------------------------------------------------------------------


def _bench_args(tmp_path, **overrides):
    args = {
        "stage": "saturday",
        "runs": 3,
        "concept": "Spiral Bites",
        "from_episode": None,
        "recipe_context": "Spiral Bites - yeasted dough in a muffin pan",
        "local": False,
        "label": None,
        "compare": None,
        "allow_mismatched_baseline": False,
        "max_calls": None,
        "max_cost": 5.0,
        "dry_run": False,
        "no_log": True,
        "experiments_log": None,
        "results_dir": str(tmp_path / "results"),
    }
    args.update(overrides)
    return cl.argparse.Namespace(**args)


def _patch_bench_generation(monkeypatch, sdw_module, *, turns=4):
    def fake_run_simulation(*, concept, default_model, run_index, stage_only, mode, recipe_context, **kwargs):
        return {"messages": _messages(f"run{run_index}", count=turns)}

    monkeypatch.setattr(sdw_module, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "dialogue-model", "judge-model"))


def _bench_path(tmp_path, label="saturday-n3"):
    """Resolve a bench result by label prefix - filenames carry a UTC stamp."""
    matches = sorted((tmp_path / "results").glob(f"bench-{label}-*.json"))
    assert matches, f"no bench result written for label {label!r}"
    return matches[-1]


def _read_bench(tmp_path, label="saturday-n3"):
    return json.loads(_bench_path(tmp_path, label).read_text())


def test_bench_runs_n_times_and_aggregates(tmp_path, monkeypatch):
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    cl.cmd_bench(_bench_args(tmp_path, runs=5))

    report = _read_bench(tmp_path, "saturday-n5")
    assert report["completed_runs"] == 5
    assert len(report["runs"]) == 5
    assert report["aggregate"]["metrics"]["message_count"]["n"] == 5
    assert report["aggregate"]["metrics"]["message_count"]["mean"] == 4.0


def test_bench_dry_run_never_calls_the_judge(tmp_path, monkeypatch):
    _patch_bench_generation(monkeypatch, sdw)

    def _fail_judge(*_args, **_kwargs):
        raise AssertionError("--dry-run must never judge")

    monkeypatch.setattr(cl, "judge_dialogue", _fail_judge)
    cl.cmd_bench(_bench_args(tmp_path, dry_run=True))

    report = _read_bench(tmp_path)
    assert report["dry_run"] is True
    assert report["aggregate"]["pass_rate"] is None
    assert all("judge" not in run for run in report["runs"])


def test_bench_gives_the_judge_a_fresh_episode_each_run(tmp_path, monkeypatch):
    """Regression guard: _judge_dialogue WRITES its scores onto the episode
    dict it is handed, so a reused dict would let run N read run N-1's
    verdict and every run after the first would look identical."""
    _patch_bench_generation(monkeypatch, sdw)
    seen_ids: list[int] = []

    def fake_judge(concept, stage, dialogue, episode, **kwargs):
        assert "judge_scores" not in episode, "episode arrived carrying a previous run's scores"
        seen_ids.append(id(episode))
        episode.setdefault("judge_scores", {})[stage] = {"turn_taking": len(seen_ids)}
        episode.setdefault("judge_weakest", {})[stage] = ["turn_taking"]
        return True, "PASS"

    monkeypatch.setattr(cl, "judge_dialogue", fake_judge)
    cl.cmd_bench(_bench_args(tmp_path, runs=3))

    report = _read_bench(tmp_path)
    assert len(set(seen_ids)) == 3
    assert [run["judge"]["scores"]["turn_taking"] for run in report["runs"]] == [1, 2, 3]


def test_bench_pass_rate_comes_from_the_production_judge(tmp_path, monkeypatch):
    _patch_bench_generation(monkeypatch, sdw)
    verdicts = iter([True, False, False, True])

    def fake_judge(concept, stage, dialogue, episode, **kwargs):
        passed = next(verdicts)
        episode.setdefault("judge_scores", {})[stage] = {"natural_progression": 4 if passed else 2}
        episode.setdefault("judge_weakest", {})[stage] = [] if passed else ["natural_progression"]
        return passed, "PASS" if passed else "FAIL - thin"

    monkeypatch.setattr(cl, "judge_dialogue", fake_judge)
    cl.cmd_bench(_bench_args(tmp_path, runs=4))

    agg = _read_bench(tmp_path, "saturday-n4")["aggregate"]
    assert agg["judged_runs"] == 4
    assert agg["pass_count"] == 2
    assert agg["pass_rate"] == 0.5
    assert agg["weakest_counts"] == {"natural_progression": 2}
    assert agg["dimensions"]["natural_progression"]["mean"] == 3.0


def _patch_varying_generation(monkeypatch, sdw_module, turn_counts):
    counts = list(turn_counts)

    def fake_run_simulation(*, run_index, **kwargs):
        return {"messages": _messages(f"run{run_index}", count=counts[(run_index - 1) % len(counts)])}

    monkeypatch.setattr(sdw_module, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))


def test_bench_compare_reports_a_moved_average(tmp_path, monkeypatch):
    # Real spread in both arms, so a z can actually be formed - a metric
    # that never varies is the separate zero-variance case below.
    _patch_varying_generation(monkeypatch, sdw, [3, 4, 5, 4])
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=4, label="control"))

    _patch_varying_generation(monkeypatch, sdw, [7, 8, 9, 8])
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(
        _bench_args(
            tmp_path, runs=4, label="longer",
            compare=str(_bench_path(tmp_path, "control")),
        )
    )

    comparison = _read_bench(tmp_path, "longer")["comparison"]
    assert comparison["baseline_label"] == "control"
    row = comparison["metrics"]["message_count"]
    assert row["baseline_mean"] == 4.0
    assert row["mean"] == 8.0
    assert row["delta"] == 4.0
    assert row["stderr_diff"] > 0
    assert abs(row["z"]) >= cl._BENCH_MOVED_Z
    assert row["moved"] is True


def test_bench_compare_flags_a_shift_that_has_no_variance_to_divide_by(tmp_path, monkeypatch):
    """A perfectly repeatable shift still moved, even though z is undefined.

    Both arms are constant, so the standard error of the difference is 0
    and delta/se does not exist. Reporting z=0.0 here would file a clean,
    reproducible change under 'did not move'.
    """
    _patch_bench_generation(monkeypatch, sdw, turns=4)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=3, label="flat-control"))

    _patch_bench_generation(monkeypatch, sdw, turns=9)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(
        _bench_args(
            tmp_path, runs=3, label="flat-longer",
            compare=str(_bench_path(tmp_path, "flat-control")),
        )
    )

    row = _read_bench(tmp_path, "flat-longer")["comparison"]["metrics"]["message_count"]
    assert row["stderr_diff"] == 0.0
    assert row["z"] is None
    assert row["moved"] is True


def test_bench_compare_rejects_a_result_that_is_not_a_bench(tmp_path, monkeypatch):
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    not_a_bench = tmp_path / "ab-result.json"
    not_a_bench.write_text(json.dumps({"command": "ab", "aggregate": {}}))

    with pytest.raises(cl.ConversationLabError, match="expects a bench result"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(not_a_bench)))


def test_bench_keeps_completed_runs_when_a_later_run_raises(tmp_path, monkeypatch):
    """Finished runs are paid work and must reach disk even if run N+1 dies."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    calls = {"n": 0}

    def flaky_run_simulation(*, run_index, **kwargs):
        calls["n"] += 1
        if run_index == 3:
            raise RuntimeError("provider blew up")
        return {"messages": _messages(f"run{run_index}", count=4)}

    monkeypatch.setattr(sdw, "run_simulation", flaky_run_simulation)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    # The partial report is still written and the failure is still recorded,
    # but the process must not exit 0 as though the bench completed.
    with pytest.raises(SystemExit, match="provider blew up"):
        cl.cmd_bench(_bench_args(tmp_path, runs=5))

    report = _read_bench(tmp_path, "saturday-n5")
    assert report["completed_runs"] == 2
    assert "provider blew up" in report["error"]
    assert report["aggregate"]["metrics"]["message_count"]["n"] == 2


def test_bench_aborts_on_max_calls_and_still_writes_a_report(tmp_path, monkeypatch):
    _patch_bench_generation(monkeypatch, sdw, turns=4)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    cl.cmd_bench(_bench_args(tmp_path, runs=10, max_calls=6))

    report = _read_bench(tmp_path, "saturday-n10")
    assert report["aborted"] is True
    assert report["completed_runs"] < 10
    assert report["calls_used"] <= 6


def test_bench_requires_concept_with_an_explicit_recipe_context(tmp_path):
    with pytest.raises(cl.ConversationLabError, match="needs --concept"):
        cl.cmd_bench(_bench_args(tmp_path, concept=None))


def test_bench_requires_exactly_one_scenario_source(tmp_path):
    with pytest.raises(cl.ConversationLabError, match="exactly one"):
        cl.cmd_bench(_bench_args(tmp_path, from_episode="2026-W38"))


def test_bench_logs_to_the_benchmarks_table_not_the_experiments_table(tmp_path, monkeypatch):
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    log = tmp_path / "EXPERIMENTS.md"

    cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log)))

    text = log.read_text()
    assert cl._BENCH_SECTION_HEADING in text
    # The A/B table's own header must be left untouched above it.
    assert text.index(cl._EXPERIMENTS_TABLE_HEADER_LINE) < text.index(cl._BENCH_SECTION_HEADING)
    assert "| saturday-n3 | saturday | 3 | 100% |" in text


# ---------------------------------------------------------------------------
# bench helpers
# ---------------------------------------------------------------------------


def test_frozen_prior_stages_stops_at_the_stage_and_skips_empty_days():
    episode = {
        "stages": {
            "monday": {"dialogue": [{"character": "Margaret Chen", "message": "a"}]},
            "tuesday": {"dialogue": []},
            "wednesday": {"dialogue": [{"character": "Julian Torres", "message": "b"}]},
            "friday": {"dialogue": [{"character": "Devon Park", "message": "c"}]},
            "saturday": {"dialogue": [{"character": "Devon Park", "message": "today"}]},
            "sunday": {"dialogue": [{"character": "Margaret Chen", "message": "later"}]},
        }
    }
    prior = cl._frozen_prior_stages(episode, "saturday")
    assert sorted(prior) == ["friday", "monday", "wednesday"]


def test_distribution_uses_sample_stdev_and_standard_error():
    dist = cl._distribution([2, 4, 4, 4, 5, 5, 7, 9])
    assert dist["n"] == 8
    assert dist["mean"] == 5.0
    # sample stdev (n-1) of this classic set is 2.13809...; the population
    # stdev would be exactly 2.0, so this asserts which one bench reports.
    # Deliberately approx, not equality: these are stored UNROUNDED because
    # _bench_delta divides by stderr, and rounding both arms' errors to 4dp
    # could turn two genuinely nonzero errors into 0.0 and misreport a
    # delta as a perfectly repeatable shift.
    assert dist["stdev"] == pytest.approx(2.13809, rel=1e-4)
    assert dist["stderr"] == pytest.approx(2.13809 / (8 ** 0.5), rel=1e-4)
    assert dist["stdev"] != 2.0, "population stdev would be exactly 2.0"


def test_distribution_ignores_non_numeric_and_empty_input():
    assert cl._distribution([]) is None
    assert cl._distribution([None, "x", True]) is None
    single = cl._distribution([3])
    assert single["n"] == 1 and single["stdev"] == 0.0 and single["stderr"] == 0.0


def test_bench_from_episode_uses_the_episode_recipe_concept_and_prior_days(tmp_path, monkeypatch):
    """The --from-episode path end to end.

    PROTOCOL.md calls this bench's primary mode - it is what makes runs
    comparable to each other AND predictive of the live gate - but every
    other cmd_bench test drives --recipe-context, so this branch
    (episode load -> _stage_recipe_data -> _build_recipe_context /
    _build_judge_recipe_facts -> _episode_concept fallback ->
    _frozen_prior_stages) was never exercised through cmd_bench.
    """
    episode = _snapshot_episode()
    episode["stages"]["monday"]["dialogue"] = _messages("mon", count=2)
    episode["stages"]["wednesday"] = {"dialogue": _messages("wed", count=2)}
    episode["stages"]["sunday"] = {"dialogue": _messages("sun", count=2)}

    anchors: list[str | None] = []
    judged_prior: list[list[str]] = []

    def fake_simulation(**kwargs):
        anchors.append(kwargs["recipe_context"])
        return {"messages": _messages("generated", count=5)}

    def fake_judge(concept, stage, dialogue, ep, **kwargs):
        judged_prior.append(sorted((ep.get("stages") or {}).keys()))
        assert kwargs.get("recipe_facts"), "judge facts were not derived from the episode"
        return True, "PASS"

    monkeypatch.setattr(cl, "_load_episode", lambda *a, **k: episode)
    monkeypatch.setattr(sdw, "run_simulation", fake_simulation)
    monkeypatch.setattr(cl, "judge_dialogue", fake_judge)
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))

    cl.cmd_bench(_bench_args(tmp_path, runs=2, concept=None, recipe_context=None, from_episode="snapshot-week"))

    report = _read_bench(tmp_path, "saturday-n2")
    # concept fell back to the real recipe title, not the placeholder.
    assert report["concept"] == "Snapshot Spiral Bites"
    assert "Snapshot Spiral Bites" in (report["recipe_context"] or "")
    assert anchors and all(a == report["recipe_context"] for a in anchors)
    # Sunday comes AFTER saturday and must not leak into the judge's context.
    assert report["judged_against_prior_days"] == ["monday", "tuesday", "wednesday"]
    assert judged_prior == [["monday", "tuesday", "wednesday"]] * 2


def test_bench_from_episode_fails_loud_when_the_recipe_data_is_unusable(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_load_episode", lambda *a, **k: {"episode_id": "empty", "stages": {}})
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)

    with pytest.raises(cl.ConversationLabError, match="no usable recipe_data"):
        cl.cmd_bench(_bench_args(tmp_path, concept=None, recipe_context=None, from_episode="empty"))


def test_bench_appends_to_an_already_populated_benchmarks_table(tmp_path, monkeypatch):
    """Two benches must produce two rows under ONE Benchmarks heading."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    log = tmp_path / "EXPERIMENTS.md"

    cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log), label="first"))
    cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log), label="second"))

    text = log.read_text()
    assert text.count(cl._BENCH_SECTION_HEADING) == 1
    assert text.count(cl._BENCH_TABLE_HEADER_LINE) == 1
    assert "| first | saturday |" in text
    assert "| second | saturday |" in text
    assert text.index("| first |") < text.index("| second |")


def test_bench_derives_max_calls_from_runs_when_the_flag_is_omitted(tmp_path, monkeypatch):
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    cl.cmd_bench(_bench_args(tmp_path, runs=3, max_calls=None))

    # Pinned to a literal, not to a re-typed copy of the implementation's
    # own expression: 3 runs x (4 calls/turn x 10 turns + 2 judge calls)
    # = 126. 10 is _MIN_MAX_TURNS_FLOOR (saturday's TICKS_RANGE upper bound
    # of 6 is below it); 4 is _MAX_CALLS_PER_TURN, traced through
    # generate_turn as initial + CoT retry + fault rewrite + CoT retry on
    # the rewrite. Re-stating `runs * (N * max_turns + 2)` here would
    # absorb a coordinated change silently - this literal was 66 while the
    # per-turn factor was wrongly 2, and Codex caught that, not this test.
    # If this fails, re-derive the budget deliberately rather than pasting
    # the new expression in.
    assert _read_bench(tmp_path)["max_calls"] == 126


# ---------------------------------------------------------------------------
# bench: the five findings from Codex's review of PR #119
# ---------------------------------------------------------------------------


def test_bench_refuses_to_start_an_arm_that_cannot_fit_the_call_budget(tmp_path, monkeypatch):
    """Codex P1: the cap must be a real upper bound, not a last-observed guess.

    `run_simulation` makes one paid call PER TURN plus rewrite retries. The
    old check reserved the PREVIOUS arm's count, seeded at 1, so
    `--max-calls 1` passed the check and then spent a whole day of turns.
    """
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    cl.cmd_bench(_bench_args(tmp_path, runs=5, max_calls=1))

    report = _read_bench(tmp_path, "saturday-n5")
    assert report["aborted"] is True
    assert report["completed_runs"] == 0
    assert report["calls_used"] == 0


def test_bench_never_overwrites_an_earlier_paid_result(tmp_path, monkeypatch):
    """Codex P1: two benches at the same stage/N must not collide.

    The documented cycle is bench -> change one thing -> bench -> compare.
    A filename derived only from the label meant the second run destroyed
    the baseline the third step needs.
    """
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    cl.cmd_bench(_bench_args(tmp_path, runs=2, label="same-label"))
    first = sorted((tmp_path / "results").glob("bench-same-label-*.json"))
    cl.cmd_bench(_bench_args(tmp_path, runs=2, label="same-label"))
    both = sorted((tmp_path / "results").glob("bench-same-label-*.json"))

    assert len(first) == 1
    assert len(both) == 2, "the second bench overwrote the first paid result"
    assert json.loads(both[0].read_text())["results_file"] != json.loads(both[1].read_text())["results_file"]


def test_bench_validates_compare_before_spending_anything(tmp_path, monkeypatch):
    """Codex P2: a typo'd --compare used to cost a full bench first."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    with pytest.raises(cl.ConversationLabError, match="not found"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(tmp_path / "typo.json")))

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json")
    with pytest.raises(cl.ConversationLabError, match="not valid JSON"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(bad_json)))


def test_bench_keeps_a_paid_transcript_when_the_judge_raises(tmp_path, monkeypatch):
    """Codex P2: the record is appended before judging, so a judge failure
    cannot discard a transcript that has already been generated and paid for."""
    _patch_bench_generation(monkeypatch, sdw, turns=4)

    calls = {"n": 0}

    def flaky_judge(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("judge provider blew up")
        return True, "PASS"

    monkeypatch.setattr(cl, "judge_dialogue", flaky_judge)

    with pytest.raises(SystemExit, match="judge provider blew up"):
        cl.cmd_bench(_bench_args(tmp_path, runs=4))

    report = _read_bench(tmp_path, "saturday-n4")
    # Two generations happened; the second one's judge died. Both transcripts
    # must survive, with the unjudged one simply carrying no judge result.
    assert report["completed_runs"] == 2
    assert [len(r["transcript"]) for r in report["runs"]] == [4, 4]
    assert "judge" in report["runs"][0]
    assert "judge" not in report["runs"][1]
    assert report["aggregate"]["judged_runs"] == 1
    assert report["aggregate"]["metrics"]["message_count"]["n"] == 2


def test_bench_refuses_a_baseline_from_a_different_stage(tmp_path, monkeypatch):
    """Codex P2: a cross-stage delta varies cast, turn range and rubric too."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, stage="monday", runs=2, label="monday-base"))
    baseline = str(_bench_path(tmp_path, "monday-base"))

    with pytest.raises(cl.ConversationLabError, match="not comparable"):
        cl.cmd_bench(_bench_args(tmp_path, stage="saturday", runs=2, compare=baseline))

    # The override exists for a deliberate cross-scenario read.
    cl.cmd_bench(
        _bench_args(
            tmp_path, stage="saturday", runs=2, label="override",
            compare=baseline, allow_mismatched_baseline=True,
        )
    )
    assert _read_bench(tmp_path, "override")["comparison"]["baseline_label"] == "monday-base"


def test_bench_refuses_to_compare_a_dry_run_against_a_paid_baseline(tmp_path, monkeypatch):
    """Template dialogue is not a control for real dialogue."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=2, label="paid-base"))
    baseline = str(_bench_path(tmp_path, "paid-base"))

    with pytest.raises(cl.ConversationLabError, match="dry_run"):
        cl.cmd_bench(_bench_args(tmp_path, runs=2, dry_run=True, compare=baseline))


# ---------------------------------------------------------------------------
# bench: Codex's second review of PR #119 (findings on the first round's fixes)
# ---------------------------------------------------------------------------


def test_bench_reserves_four_calls_per_turn_not_two(tmp_path, monkeypatch):
    """A turn can cost 4 paid calls, so 2x turns was never a worst case.

    generate_turn: initial response, _guard_cot_leak retry, fault rewrite,
    _guard_cot_leak retry on the rewrite. With a 10-turn ceiling an arm can
    spend 40, so `--max-calls 20` must refuse to start it rather than admit
    it and blow the documented hard bound.
    """
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    assert cl._MAX_CALLS_PER_TURN == 4
    cl.cmd_bench(_bench_args(tmp_path, runs=2, max_calls=20))

    report = _read_bench(tmp_path, "saturday-n2")
    assert report["aborted"] is True
    assert report["completed_runs"] == 0


def test_bench_records_calls_spent_by_a_generation_arm_that_raises(tmp_path, monkeypatch):
    """Codex: recording only on the success path under-reported real spend.

    run_simulation can raise after paid requests (a CoT leak that survives
    its retry), and a first-arm failure then reported calls_used: 0.
    """
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    spent = {"n": 0}

    def exploding_run(**kwargs):
        spent["n"] += 7  # seven paid turns happened before the guard gave up
        raise RuntimeError("CoT leak after retry")

    monkeypatch.setattr(sdw, "run_simulation", exploding_run)
    monkeypatch.setattr(cl, "_calls_now", lambda: spent["n"])

    with pytest.raises(SystemExit, match="CoT leak after retry"):
        cl.cmd_bench(_bench_args(tmp_path, runs=3))

    report = _read_bench(tmp_path)
    assert report["completed_runs"] == 0
    assert report["calls_used"] == 7, "calls spent by the failed arm were not recorded"


def test_bench_rejects_a_structurally_broken_baseline_before_spending(tmp_path, monkeypatch):
    """Codex: command == 'bench' alone let a broken payload through, and the
    TypeError landed in _bench_delta after everything was paid for."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    cases = {
        "aggregate-is-a-list.json": {"command": "bench", "aggregate": []},
        "metrics-missing.json": {"command": "bench", "aggregate": {"metrics": "nope"}},
        "dist-not-an-object.json": {"command": "bench", "aggregate": {"metrics": {"qa_rate": 3}}},
        "stderr-missing.json": {"command": "bench", "aggregate": {"metrics": {"qa_rate": {"mean": 1.0}}}},
    }
    for name, payload in cases.items():
        bad = tmp_path / name
        bad.write_text(json.dumps(payload))
        with pytest.raises(cl.ConversationLabError):
            cl.cmd_bench(_bench_args(tmp_path, compare=str(bad)))


def test_bench_refuses_a_baseline_with_a_different_recipe_or_model(tmp_path, monkeypatch):
    """Codex: stage + dry_run was too narrow. A different dish or a different
    dialogue model moves both the metrics and the judge on its own."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=2, label="base"))
    baseline = str(_bench_path(tmp_path, "base"))

    # Same stage, different dish.
    with pytest.raises(cl.ConversationLabError, match="concept"):
        cl.cmd_bench(_bench_args(tmp_path, runs=2, concept="A Different Dish", compare=baseline))

    # Same stage and dish, different dialogue model.
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "other-model", "j"))
    with pytest.raises(cl.ConversationLabError, match="models"):
        cl.cmd_bench(_bench_args(tmp_path, runs=2, compare=baseline))


def test_bench_claims_its_result_filename_atomically(tmp_path, monkeypatch):
    """Codex: exists()-then-write is a TOCTOU race. _unique_result_path must
    claim the name, so a second caller in the same second gets a different one."""
    results = tmp_path / "results"
    results.mkdir()

    first = cl._unique_result_path(results, "bench-x-20260919T000000Z")
    second = cl._unique_result_path(results, "bench-x-20260919T000000Z")

    assert first != second
    assert first.name == "bench-x-20260919T000000Z.json"
    assert second.name == "bench-x-20260919T000000Z-2.json"
    # The claim is staked on a sidecar, so the name is reserved against a
    # concurrent caller WITHOUT a zero-byte .json ever becoming visible - a
    # later glob or --compare would have read that as a real result.
    assert first.with_name(first.name + ".partial").exists()
    assert not first.exists(), "no empty .json may be visible before the write"
    assert not second.exists()


# ---------------------------------------------------------------------------
# bench: Codex's third review of PR #119 (seven P2s, no P1s)
# ---------------------------------------------------------------------------


def test_bench_compare_calls_a_one_sample_shift_indeterminate(tmp_path, monkeypatch):
    """Codex: at --runs 1 the stderr is zero because variance was never
    ESTIMATED, not because the result repeats. Calling that 'moved' turns a
    coin flip into a finding."""
    _patch_bench_generation(monkeypatch, sdw, turns=4)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=1, label="n1-a"))

    _patch_bench_generation(monkeypatch, sdw, turns=9)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(
        _bench_args(tmp_path, runs=1, label="n1-b", compare=str(_bench_path(tmp_path, "n1-a")))
    )

    row = _read_bench(tmp_path, "n1-b")["comparison"]["metrics"]["message_count"]
    assert row["delta"] == 5.0
    assert row["estimable"] is False
    assert row["moved"] is None, "a single sample per arm cannot establish movement"


def test_bench_dry_run_spends_and_reserves_nothing(tmp_path, monkeypatch):
    """Codex: a template run makes zero API calls, so a plumbing check must
    not report calls_used, nor be refused by a low --max-calls."""
    _patch_bench_generation(monkeypatch, sdw)

    def _fail_judge(*_a, **_k):
        raise AssertionError("--dry-run must never judge")

    monkeypatch.setattr(cl, "judge_dialogue", _fail_judge)
    cl.cmd_bench(_bench_args(tmp_path, runs=2, dry_run=True, max_calls=1))

    report = _read_bench(tmp_path, "saturday-n2")
    assert report["calls_used"] == 0
    assert report["aborted"] is False
    assert report["completed_runs"] == 2, "a dry run cannot spend, so a low cap must not stop it"


def test_bench_result_is_published_atomically(tmp_path, monkeypatch):
    """Codex: writing into the claimed zero-byte file leaves a truncated
    .json behind when serialization fails. The claimed name must only ever
    hold a complete document."""
    results = tmp_path / "results"
    results.mkdir()
    target = cl._unique_result_path(results, "bench-atomic")
    assert not target.exists()

    class Unserializable:
        def __repr__(self):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        cl._publish_json_atomically(target, {"bad": Unserializable()})
    assert not target.exists(), "a failed write must leave NO .json behind at all"

    cl._publish_json_atomically(target, {"command": "bench"})
    assert json.loads(target.read_text()) == {"command": "bench"}


def test_bench_log_append_preserves_both_rows_under_a_lock(tmp_path, monkeypatch):
    """Codex: the audit append is a read-modify-write; a lost row means a
    paid result file with no required log trace."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    log = tmp_path / "EXPERIMENTS.md"

    cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log), label="row-a"))
    cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log), label="row-b"))

    text = log.read_text()
    assert "| row-a |" in text and "| row-b |" in text
    assert text.count(cl._BENCH_SECTION_HEADING) == 1


def test_bench_rejects_a_baseline_with_a_non_numeric_pass_rate(tmp_path, monkeypatch):
    """Codex: _print_bench_report formats pass_rate with :.0%, so a string
    crashed after everything was paid for and written."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    bad = tmp_path / "bad-rate.json"
    bad.write_text(json.dumps({
        "command": "bench",
        "aggregate": {"metrics": {"qa_rate": {"mean": 1.0, "stderr": 0.0}}, "pass_rate": "unknown"},
    }))
    with pytest.raises(cl.ConversationLabError, match="pass_rate"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(bad)))


def test_bench_scenario_digest_notices_changed_prior_day_dialogue():
    """Codex: the day NAMES stay identical when an episode's earlier
    dialogue is regenerated, but the judge sees different text."""
    cast = ["Devon Park", "Margaret Chen"]
    before = {"monday": {"dialogue": [{"character": "Margaret Chen", "message": "original"}]}}
    after = {"monday": {"dialogue": [{"character": "Margaret Chen", "message": "regenerated"}]}}

    assert sorted(before) == sorted(after), "the day-name check cannot tell these apart"
    assert cl._judge_input_digest(before, "facts", cast) != cl._judge_input_digest(after, "facts", cast)
    # recipe_facts drives technical_credibility and was not represented at all.
    assert cl._judge_input_digest(before, "facts", cast) != cl._judge_input_digest(before, "other", cast)
    # Same inputs must still agree, or every comparison would be refused.
    assert cl._judge_input_digest(before, "facts", cast) == cl._judge_input_digest(before, "facts", cast)


# ---------------------------------------------------------------------------
# bench: Codex's fourth review — bugs in the third round's own fixes
# ---------------------------------------------------------------------------


def test_bench_delta_is_indeterminate_when_only_one_arm_has_samples(tmp_path, monkeypatch):
    """Codex: `estimable` was consulted only AFTER `if se`.

    A one-run arm contributes stderr 0, so if the other arm varies, `se` is
    nonzero and a big delta was still reported as moved - with half the
    comparison having no variance estimate at all.
    """
    single = {"n": 1, "mean": 4.0, "stdev": 0.0, "stderr": 0.0, "min": 4.0, "max": 4.0}
    varied = {"n": 4, "mean": 9.0, "stdev": 1.0, "stderr": 0.5, "min": 8.0, "max": 10.0}

    row = cl._bench_delta({"metrics": {"m": varied}}, {"metrics": {"m": single}})["m"]
    assert row["stderr_diff"] > 0, "the varied arm really does supply a nonzero error"
    assert row["delta"] == 5.0
    assert row["estimable"] is False
    assert row["moved"] is None, "one arm never had its variance estimated"


def test_bench_delta_uses_unrounded_standard_errors(tmp_path):
    """Codex: stderr was rounded to 4dp before being used as a denominator.

    Two genuinely nonzero errors can both round to 0.0, and the
    zero-variance branch then calls a tiny delta a perfectly repeatable
    shift. These errors round to 0.0000 but must still produce a z.
    """
    tiny_a = {"n": 5, "mean": 1.00000, "stdev": 0.0001, "stderr": 0.00002, "min": 1.0, "max": 1.0}
    tiny_b = {"n": 5, "mean": 1.00003, "stdev": 0.0001, "stderr": 0.00002, "min": 1.0, "max": 1.0}

    assert round(tiny_a["stderr"], 4) == 0.0, "these are exactly the values that used to break it"
    row = cl._bench_delta({"metrics": {"m": tiny_b}}, {"metrics": {"m": tiny_a}})["m"]
    # Unrounded, a z exists and comes out near 1.06 - under the threshold.
    # Rounded, both errors became 0.0, no z could be formed, and the
    # zero-variance branch declared this tiny delta a perfectly repeatable
    # shift. The fix turns a false positive into an honest "did not move".
    assert row["z"] is not None, "a z must still be computable from unrounded errors"
    assert row["z"] == pytest.approx(1.06, abs=0.05)
    assert row["moved"] is False


def test_distribution_keeps_full_precision():
    """The stored values feed a division, so they are not rounded."""
    dist = cl._distribution([1.00001, 1.00002, 1.00003])
    assert dist["stderr"] > 0
    assert round(dist["stderr"], 4) == 0.0, "rounding would have destroyed this"


def test_bench_keeps_the_transcript_when_summarize_raises(tmp_path, monkeypatch):
    """Codex: the summary was computed while building the record, so a
    summarize() failure discarded a transcript already paid for."""
    _patch_bench_generation(monkeypatch, sdw, turns=4)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    calls = {"n": 0}
    real_summarize = cl.summarize

    def flaky_summarize(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("metric blew up")
        return real_summarize(*args, **kwargs)

    monkeypatch.setattr(cl, "summarize", flaky_summarize)

    with pytest.raises(SystemExit, match="metric blew up"):
        cl.cmd_bench(_bench_args(tmp_path, runs=4))

    report = _read_bench(tmp_path, "saturday-n4")
    assert report["completed_runs"] == 2
    assert [len(r["transcript"]) for r in report["runs"]] == [4, 4]
    assert "summary" in report["runs"][0]
    assert "summary" not in report["runs"][1], "unsummarized, but NOT discarded"


def test_bench_preserves_partial_results_on_ctrl_c(tmp_path, monkeypatch):
    """Codex: KeyboardInterrupt is a BaseException, so `except Exception`
    skipped the handler and threw away every completed paid run."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    def interrupt_on_third(*, run_index, **kwargs):
        if run_index == 3:
            raise KeyboardInterrupt
        return {"messages": _messages(f"run{run_index}", count=4)}

    monkeypatch.setattr(sdw, "run_simulation", interrupt_on_third)

    # Re-raised as KeyboardInterrupt, not converted to SystemExit: Ctrl-C
    # should still read as Ctrl-C to whatever is running this.
    with pytest.raises(KeyboardInterrupt):
        cl.cmd_bench(_bench_args(tmp_path, runs=6))

    report = _read_bench(tmp_path, "saturday-n6")
    assert report["completed_runs"] == 2
    assert "KeyboardInterrupt" in report["error"]
    assert report["aggregate"]["metrics"]["message_count"]["n"] == 2


def test_bench_creates_the_results_directory_before_spending(tmp_path, monkeypatch):
    """Codex: mkdir ran after the loop, so a path error lost every run."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    seen: list[bool] = []

    real_run = sdw.run_simulation

    def record_dir_state(**kwargs):
        seen.append((tmp_path / "results").is_dir())
        return real_run(**kwargs)

    monkeypatch.setattr(sdw, "run_simulation", record_dir_state)
    cl.cmd_bench(_bench_args(tmp_path, runs=2))

    assert seen and all(seen), "the results dir must exist before the first paid call"


# ---------------------------------------------------------------------------
# bench: Codex's fifth review
# ---------------------------------------------------------------------------


def test_bench_lock_never_lands_in_the_worktree(tmp_path, monkeypatch):
    """Codex: a sibling EXPERIMENTS.md.lock is untracked, un-ignored repo
    noise, and the locked hygiene contract's session-end gate refuses to
    close on a dirty tree - so the lab would have blocked the end of every
    session that used it."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    log_dir = tmp_path / "docs-like"
    log_dir.mkdir()
    log = log_dir / "EXPERIMENTS.md"

    cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log)))

    assert log.exists()
    assert not list(log_dir.glob("*.lock")), "the lock must not live next to the log"
    assert not list(log_dir.glob("*.partial")), "no temp artifact may survive a clean run"


def test_bench_log_is_replaced_atomically(tmp_path, monkeypatch):
    """Codex: a truncated write_text destroys prior audit rows. The lock
    prevents concurrent writers; it does not make a partial write safe."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    log = tmp_path / "EXPERIMENTS.md"
    cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log), label="first"))
    before = log.read_text()

    real_replace = cl.os.replace

    def fail_on_log_replace(src, dst):
        if str(dst).endswith("EXPERIMENTS.md"):
            raise OSError("disk full")
        return real_replace(src, dst)

    monkeypatch.setattr(cl.os, "replace", fail_on_log_replace)
    with pytest.raises(OSError):
        cl.cmd_bench(_bench_args(tmp_path, no_log=False, experiments_log=str(log), label="second"))

    assert log.read_text() == before, "a failed append must not truncate the audit trail"


def test_bench_rejects_a_baseline_with_a_non_integer_sample_count(tmp_path, monkeypatch):
    """Codex: _bench_delta evaluates `n >= 2`, which raises on a string -
    after the whole bench has been paid for."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    bad = tmp_path / "bad-n.json"
    bad.write_text(json.dumps({
        "command": "bench",
        "aggregate": {"metrics": {"qa_rate": {"n": "2", "mean": 1.0, "stderr": 0.0}}},
    }))
    with pytest.raises(cl.ConversationLabError, match="sample count"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(bad)))


def test_bench_refuses_an_unwritable_results_directory_before_spending(tmp_path, monkeypatch):
    """Codex: mkdir(exist_ok=True) succeeds on an existing read-only dir,
    so the whole bench ran and only the claim failed."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    results = tmp_path / "results"
    results.mkdir()
    results.chmod(0o500)  # r-x: exists, but nothing can be created in it
    try:
        with pytest.raises(cl.ConversationLabError, match="not writable"):
            cl.cmd_bench(_bench_args(tmp_path))
    finally:
        results.chmod(0o700)


def test_generator_input_digest_notices_rewritten_character_memory(tmp_path, monkeypatch):
    """Codex: the Sunday stage rewrites memory.json every published week,
    so two benches a week apart ran different system prompts while every
    scenario field matched."""
    import scripts.simulate_dialogue_week as sdw_mod

    chars = tmp_path / "characters"
    (chars / "margaret-chen").mkdir(parents=True)
    (chars / "margaret-chen" / "bio.md").write_text("Blunt. Short sentences.")
    mem = chars / "margaret-chen" / "memory.json"
    mem.write_text(json.dumps({"episodes": [{"concept": "W1", "summary": "a"}]}))
    monkeypatch.setattr(sdw_mod, "CHARACTERS_DIR", chars)

    before = cl._generator_input_digest(["Margaret Chen"], "monday")
    mem.write_text(json.dumps({"episodes": [{"concept": "W2", "summary": "b"}]}))
    after = cl._generator_input_digest(["Margaret Chen"], "monday")

    assert before != after, "a rewritten memory must break the comparison"
    assert cl._generator_input_digest(["Margaret Chen"], "monday") == after, "and be stable otherwise"


def test_evaluator_digest_notices_a_changed_judge_rubric(monkeypatch):
    """Codex: the inputs were fingerprinted but the SCORERS were not.

    A rubric edit between two benches means _bench_delta subtracts
    aggregates computed under different definitions, and the pass-rate
    movement can come from the rewritten rubric rather than the lever.
    """
    import backend.admin.cron_routes as cron

    before = cl._evaluator_digest()
    monkeypatch.setattr(cron, "_JUDGE_SYSTEM_PROMPT", cron._JUDGE_SYSTEM_PROMPT + "\nNEW RULE.")
    after = cl._evaluator_digest()

    assert before != after, "a rewritten rubric must break the comparison"
    monkeypatch.undo()
    assert cl._evaluator_digest() == before, "and be stable when nothing changed"


def test_evaluator_digest_notices_a_changed_metric_definition(monkeypatch, tmp_path):
    """The deterministic metrics are the other half of the scorer."""
    fake = tmp_path / "conversation_metrics.py"
    fake.write_text("# v1\n")
    monkeypatch.setattr(cl.conversation_metrics, "__file__", str(fake))
    before = cl._evaluator_digest()

    fake.write_text("# v2 - question_rate now counts rhetorical questions\n")
    assert cl._evaluator_digest() != before


def test_bench_refuses_a_baseline_scored_by_a_different_evaluator(tmp_path, monkeypatch):
    """End to end: the digest has to actually gate the comparison."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=2, label="old-rubric"))
    baseline = str(_bench_path(tmp_path, "old-rubric"))

    monkeypatch.setattr(cl, "_evaluator_digest", lambda: "different0000000")
    with pytest.raises(cl.ConversationLabError, match="evaluator_digest"):
        cl.cmd_bench(_bench_args(tmp_path, runs=2, compare=baseline))


# ---------------------------------------------------------------------------
# bench: Codex's seventh review
# ---------------------------------------------------------------------------


def test_spend_charges_the_reservation_when_the_counter_is_unreadable(monkeypatch):
    """Codex: an unreadable cost log made --max-calls stop being a cap.

    _calls_now() used to return 0 on failure, indistinguishable from "no
    call happened", so a generation that can really cost 40 was recorded
    as 10 and the budget let another full arm through.
    """
    monkeypatch.setattr(cl, "_calls_now", lambda: None)
    budget = cl.CallBudget(max_calls=60)

    cl._spend(budget, lambda: "ok", fallback=10, reservation=40)

    assert budget.used == 40, "an invisible spend must be charged at the reservation"
    assert budget.would_exceed(40), "and a second worst-case arm must no longer fit"


def test_spend_still_records_the_real_delta_when_the_counter_works(monkeypatch):
    counts = iter([5, 12])
    monkeypatch.setattr(cl, "_calls_now", lambda: next(counts))
    budget = cl.CallBudget(max_calls=100)

    cl._spend(budget, lambda: "ok", fallback=1, reservation=40)

    assert budget.used == 7, "a readable counter must charge actual spend, not the reservation"


def test_evaluator_digest_pins_scoring_code_but_not_the_prompt_levers(monkeypatch, tmp_path):
    """The digest must catch a changed SCORER without refusing the workflow.

    Hashing all of simulate_dialogue_week.py broke the documented loop -
    bench, change one prompt lever IN THAT FILE, bench again, compare -
    because every such change invalidated the baseline, and the only
    escape also switched off the judge-rubric and judge-context checks
    (Codex). Both halves are pinned here: scoring changes must be caught,
    lever changes must not.
    """
    import backend.admin.cron_routes as cron

    baseline = cl._evaluator_digest()

    # A changed prompt LEVER must NOT invalidate the comparison - that is
    # the thing under test.
    monkeypatch.setattr(sdw, "_DAY_OPENER_CONTEXT", {"monday": "totally different"})
    monkeypatch.setattr(sdw, "HISTORY_DEPTH", {"early": (99, 99), "late": (99, 99)})
    assert cl._evaluator_digest() == baseline, "a prompt lever change must not refuse the compare"
    monkeypatch.undo()

    # A changed SCORER must.
    monkeypatch.setattr(cron, "_JUDGE_SYSTEM_PROMPT", cron._JUDGE_SYSTEM_PROMPT + "\nNEW RULE.")
    assert cl._evaluator_digest() != baseline, "the judge rubric is not pinned"
    monkeypatch.undo()

    fake = tmp_path / "metrics.py"
    fake.write_text("# question_rate now counts rhetorical questions\n")
    monkeypatch.setattr(cl.conversation_metrics, "__file__", str(fake))
    assert cl._evaluator_digest() != baseline, "the metrics module is not pinned"
    monkeypatch.undo()

    assert cl._evaluator_digest() == baseline


def test_evaluator_digest_pins_the_named_scoring_functions():
    """score_quality feeds legacy_quality's reported numbers, and
    _judge_dialogue / _parse_judge_json assemble and parse the verdict, so
    all three are hashed by source rather than by whole module."""
    import backend.admin.cron_routes as cron

    digest_inputs = {
        "judge_dialogue": cron._judge_dialogue,
        "parse_judge_json": cron._parse_judge_json,
        "score_quality": sdw.score_quality,
    }
    for name, obj in digest_inputs.items():
        assert cl.inspect.getsource(obj), f"{name} source must be readable for the digest"


def test_bench_persists_its_cost_summary(tmp_path, monkeypatch):
    """Codex: the cost log is process-local, so without this the result
    retains no dollar spend once the command exits."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    monkeypatch.setattr(
        cl.model_router, "get_cost_summary",
        lambda: {"total_cost": 1.23, "total_calls": 42},
    )

    cl.cmd_bench(_bench_args(tmp_path, runs=2))

    report = _read_bench(tmp_path, "saturday-n2")
    assert report["cost_summary"]["total_cost"] == 1.23
    assert report["cost_summary"]["total_calls"] == 42


def test_bench_survives_an_unreadable_cost_summary(tmp_path, monkeypatch):
    """A broken cost log must not take the whole report down with it."""
    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))

    def boom():
        raise RuntimeError("cost log unavailable")

    monkeypatch.setattr(cl.model_router, "get_cost_summary", boom)
    cl.cmd_bench(_bench_args(tmp_path, runs=1))

    report = _read_bench(tmp_path, "saturday-n1")
    assert report["cost_summary"] is None
    assert report["completed_runs"] == 1


# ---------------------------------------------------------------------------
# bench: Codex's eighth review
# ---------------------------------------------------------------------------


def test_bench_compare_reports_moved_judge_dimensions(tmp_path, monkeypatch):
    """Codex: --compare walked only aggregate.metrics.

    A prompt change that moves a judge dimension while the deterministic
    metrics stay flat was reported as "nothing moved" - the tool failing
    at the exact job it exists for. voice_distinctiveness sitting at 3 for
    weeks is precisely the number this has to be able to see move.
    """
    scores = {"low": 3, "high": 5}

    def judge_with(level):
        def fake_judge(concept, stage, dialogue, episode, **kwargs):
            episode.setdefault("judge_scores", {})[stage] = {
                "voice_distinctiveness": scores[level],
                # jitter so the arm has a variance estimate
                "natural_progression": 3 + (len(dialogue) % 2),
            }
            episode.setdefault("judge_weakest", {})[stage] = []
            return True, "PASS"
        return fake_judge

    # Identical transcripts in both arms: the ONLY thing that changes is
    # the judge's score, so a metrics-only delta sees nothing at all.
    _patch_varying_generation(monkeypatch, sdw, [4, 5, 4, 5])
    monkeypatch.setattr(cl, "judge_dialogue", judge_with("low"))
    cl.cmd_bench(_bench_args(tmp_path, runs=4, label="before"))

    _patch_varying_generation(monkeypatch, sdw, [4, 5, 4, 5])
    monkeypatch.setattr(cl, "judge_dialogue", judge_with("high"))
    cl.cmd_bench(
        _bench_args(tmp_path, runs=4, label="after", compare=str(_bench_path(tmp_path, "before")))
    )

    comparison = _read_bench(tmp_path, "after")["comparison"]
    assert "dimensions" in comparison, "judge dimensions must be compared"
    row = comparison["dimensions"]["voice_distinctiveness"]
    assert row["baseline_mean"] == 3.0
    assert row["mean"] == 5.0
    assert row["delta"] == 2.0
    assert row["moved"] is True

    # And the metrics table really would have shown nothing.
    assert not any(v["moved"] for v in comparison["metrics"].values())


def test_bench_rejects_a_baseline_with_nan_or_infinite_values(tmp_path, monkeypatch):
    """Codex: json.loads accepts NaN/Infinity and both pass an isinstance
    check. A NaN stderr makes z NaN, and abs(NaN) >= 2 is False - an
    uncomputable measurement silently reported as "did not move"."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)

    for raw in ("NaN", "Infinity", "-Infinity"):
        bad = tmp_path / f"bad-{raw}.json"
        bad.write_text(
            '{"command": "bench", "aggregate": {"metrics": {"qa_rate": '
            '{"n": 3, "mean": 1.0, "stderr": ' + raw + "}}}}"
        )
        # Sanity: the parser really does accept it, which is the whole problem.
        assert not cl.isfinite(json.loads(bad.read_text())["aggregate"]["metrics"]["qa_rate"]["stderr"])
        with pytest.raises(cl.ConversationLabError, match="non-finite"):
            cl.cmd_bench(_bench_args(tmp_path, compare=str(bad)))


def test_bench_rejects_a_negative_standard_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    bad = tmp_path / "neg.json"
    bad.write_text(json.dumps({
        "command": "bench",
        "aggregate": {"metrics": {"qa_rate": {"n": 3, "mean": 1.0, "stderr": -0.5}}},
    }))
    with pytest.raises(cl.ConversationLabError, match="negative"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(bad)))


def test_bench_rejects_an_overlong_label_before_spending(tmp_path, monkeypatch):
    """Codex: ENAMETOOLONG fired at write time, after the whole bench was
    paid for and after partial-result recovery had ended."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)

    with pytest.raises(cl.ConversationLabError, match="too long"):
        cl.cmd_bench(_bench_args(tmp_path, label="x" * 300))

    assert not list((tmp_path / "results").glob("*.json")) if (tmp_path / "results").is_dir() else True


# ---------------------------------------------------------------------------
# bench: Codex's ninth review — fallout from the dimension-comparison fix
# ---------------------------------------------------------------------------


def test_distribution_drops_non_finite_live_samples():
    """Codex: only BASELINE values were checked for finiteness.

    The production judge's parser accepts JSON NaN/Infinity and stores the
    scores unchecked, so a single bad verdict would persist a non-finite
    aggregate on a one-run bench, or raise ValueError out of stdev on a
    multi-run one - after every call was paid for and outside the
    partial-result recovery.
    """
    dist = cl._distribution([3.0, float("nan"), 5.0, float("inf")])
    assert dist["n"] == 2, "n must reflect the samples actually used"
    assert dist["mean"] == 4.0
    assert cl.isfinite(dist["stdev"]) and cl.isfinite(dist["stderr"])

    assert cl._distribution([float("nan"), float("-inf")]) is None


def test_bench_survives_a_non_finite_judge_score(tmp_path, monkeypatch):
    """End to end: a poisoned verdict must not take the bench down."""
    _patch_varying_generation(monkeypatch, sdw, [4, 5, 4])

    def poisoned_judge(concept, stage, dialogue, episode, **kwargs):
        episode.setdefault("judge_scores", {})[stage] = {
            "turn_taking": float("nan"),
            "natural_progression": 3,
        }
        episode.setdefault("judge_weakest", {})[stage] = []
        return True, "PASS"

    monkeypatch.setattr(cl, "judge_dialogue", poisoned_judge)
    cl.cmd_bench(_bench_args(tmp_path, runs=3))

    report = _read_bench(tmp_path)
    assert report["completed_runs"] == 3
    assert report["error"] is None
    # Present as an explicit placeholder, NOT absent: a dropped key
    # produced no comparison row and a summary saying nothing moved, which
    # inverts a total measurement failure into "no change".
    unusable = report["aggregate"]["dimensions"]["turn_taking"]
    assert unusable["n"] == 0
    assert unusable["no_valid_samples"] is True
    assert report["aggregate"]["dimensions"]["natural_progression"]["mean"] == 3.0


def test_bench_validates_baseline_dimensions_before_spending(tmp_path, monkeypatch):
    """Codex: _bench_delta reads dimensions now, but preflight did not."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    good_metrics = {"qa_rate": {"n": 3, "mean": 1.0, "stderr": 0.1}}

    bad = tmp_path / "bad-dims.json"
    bad.write_text(json.dumps({
        "command": "bench",
        "aggregate": {"metrics": good_metrics, "dimensions": ["not", "a", "dict"]},
    }))
    with pytest.raises(cl.ConversationLabError, match="aggregate.dimensions"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(bad)))

    worse = tmp_path / "bad-dim-dist.json"
    worse.write_text(json.dumps({
        "command": "bench",
        "aggregate": {
            "metrics": good_metrics,
            "dimensions": {"turn_taking": {"n": 3, "mean": 4.0}},  # no stderr
        },
    }))
    with pytest.raises(cl.ConversationLabError, match="turn_taking"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(worse)))


def test_bench_never_prints_nothing_moved_when_a_dimension_moved(tmp_path, monkeypatch, capsys):
    """Codex: the summary line contradicted the table directly above it.

    That line is the one an operator might read INSTEAD of the tables, so
    it saying "nothing moved" under a flagged dimension is the worst place
    for the contradiction to live.
    """
    def judge_scoring(value):
        def fake_judge(concept, stage, dialogue, episode, **kwargs):
            episode.setdefault("judge_scores", {})[stage] = {
                "voice_distinctiveness": value,
                "turn_taking": 3 + (len(dialogue) % 2),
            }
            episode.setdefault("judge_weakest", {})[stage] = []
            return True, "PASS"
        return fake_judge

    _patch_varying_generation(monkeypatch, sdw, [4, 5, 4, 5])
    monkeypatch.setattr(cl, "judge_dialogue", judge_scoring(3))
    cl.cmd_bench(_bench_args(tmp_path, runs=4, label="lo"))

    _patch_varying_generation(monkeypatch, sdw, [4, 5, 4, 5])
    monkeypatch.setattr(cl, "judge_dialogue", judge_scoring(5))
    capsys.readouterr()
    cl.cmd_bench(
        _bench_args(tmp_path, runs=4, label="hi", compare=str(_bench_path(tmp_path, "lo")))
    )
    out = capsys.readouterr().out

    assert "no DETERMINISTIC metric moved" in out
    assert "judge dimension(s) did" in out
    assert "(nothing moved" not in out, "the summary must not contradict the table above it"


# ---------------------------------------------------------------------------
# bench: Codex's tenth review
# ---------------------------------------------------------------------------


def test_bench_compare_survives_a_changed_prompt_lever(tmp_path, monkeypatch):
    """The documented workflow, end to end: bench, change a lever, compare.

    Hashing the whole simulator made this exact loop impossible - the
    comparison was refused because the file changed, which is the ONE
    thing the operator came to do (Codex).
    """
    _patch_varying_generation(monkeypatch, sdw, [4, 5, 4, 5])
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=4, label="control"))
    baseline = str(_bench_path(tmp_path, "control"))

    # The lever under test changes between benches.
    monkeypatch.setattr(sdw, "_REACTION_DIRECTIVE", "A COMPLETELY DIFFERENT DIRECTIVE\n")
    _patch_varying_generation(monkeypatch, sdw, [4, 5, 4, 5])
    monkeypatch.setattr(cl, "judge_dialogue", lambda *a, **k: (True, "PASS"))
    cl.cmd_bench(_bench_args(tmp_path, runs=4, label="variant", compare=baseline))

    assert _read_bench(tmp_path, "variant")["comparison"]["baseline_label"] == "control"


def test_bench_drops_judge_scores_outside_the_one_to_five_contract(tmp_path, monkeypatch):
    """Codex: the verdict parser stores whatever JSON it is handed, so a
    malformed 50 would drag a dimension mean far enough to invent or hide
    movement."""
    scores = iter([5, 50, 5, 0])

    def sloppy_judge(concept, stage, dialogue, episode, **kwargs):
        episode.setdefault("judge_scores", {})[stage] = {"turn_taking": next(scores)}
        episode.setdefault("judge_weakest", {})[stage] = []
        return True, "PASS"

    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", sloppy_judge)
    cl.cmd_bench(_bench_args(tmp_path, runs=4))

    dist = _read_bench(tmp_path, "saturday-n4")["aggregate"]["dimensions"]["turn_taking"]
    assert dist["n"] == 2, "only the two in-contract scores are data"
    assert dist["mean"] == 5.0, "a 50 would have dragged this to 15.0"


def test_bench_labels_indeterminate_judge_dimensions(tmp_path, monkeypatch, capsys):
    """Codex: an unlabeled n/a in the dimension table plus a summary that
    said nothing moved reads as 'no change' when the truth is 'we could
    not tell'."""
    def judge_scoring(value):
        def fake_judge(concept, stage, dialogue, episode, **kwargs):
            episode.setdefault("judge_scores", {})[stage] = {"voice_distinctiveness": value}
            episode.setdefault("judge_weakest", {})[stage] = []
            return True, "PASS"
        return fake_judge

    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", judge_scoring(3))
    cl.cmd_bench(_bench_args(tmp_path, runs=1, label="one-a"))

    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", judge_scoring(5))
    capsys.readouterr()
    cl.cmd_bench(
        _bench_args(tmp_path, runs=1, label="one-b", compare=str(_bench_path(tmp_path, "one-a")))
    )
    out = capsys.readouterr().out

    row = _read_bench(tmp_path, "one-b")["comparison"]["dimensions"]["voice_distinctiveness"]
    assert row["moved"] is None
    assert "indeterminate (n < 2)" in out
    assert "INDETERMINATE, not unchanged" in out
    assert "(nothing moved" not in out


# ---------------------------------------------------------------------------
# bench: Codex's eleventh review
# ---------------------------------------------------------------------------


def test_evaluator_digest_follows_scorers_into_their_helpers(monkeypatch):
    """Codex: hashing only score_quality's body missed the ten helpers it
    calls and the constants those read, so editing _voice_pattern_score
    changed the reported legacy metrics without changing the digest."""
    baseline = cl._evaluator_digest()

    real = sdw._voice_pattern_score

    def altered_helper(*args, **kwargs):
        """A different implementation entirely."""
        return real(*args, **kwargs)

    monkeypatch.setattr(sdw, "_voice_pattern_score", altered_helper)
    assert cl._evaluator_digest() != baseline, "a changed scoring helper must be caught"
    monkeypatch.undo()
    assert cl._evaluator_digest() == baseline


def test_evaluator_digest_still_ignores_the_levers_under_test(monkeypatch):
    """The walk must skip ALLOWED_VARIANT_ATTRS, or it reintroduces the
    whole-module problem one level down."""
    baseline = cl._evaluator_digest()
    for lever in cl.ALLOWED_VARIANT_ATTRS:
        current = getattr(sdw, lever)
        replacement = (
            current + "\nDIFFERENT\n" if isinstance(current, str) else {"changed": True}
        )
        monkeypatch.setattr(sdw, lever, replacement)
        assert cl._evaluator_digest() == baseline, f"{lever} must not affect the digest"
        monkeypatch.undo()


def test_bench_compare_shows_a_dimension_the_judge_never_scored(tmp_path, monkeypatch, capsys):
    """Codex: an all-invalid dimension used to vanish from the aggregate,
    so the comparison had no row, no indeterminate count, and a summary
    saying nothing moved - a total measurement failure reported as no
    change."""
    def judge_missing_one(concept, stage, dialogue, episode, **kwargs):
        episode.setdefault("judge_scores", {})[stage] = {
            "turn_taking": 4,
            "voice_distinctiveness": None,  # never scored, every run
        }
        episode.setdefault("judge_weakest", {})[stage] = []
        return True, "PASS"

    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", judge_missing_one)
    cl.cmd_bench(_bench_args(tmp_path, runs=3, label="base"))

    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", judge_missing_one)
    capsys.readouterr()
    cl.cmd_bench(
        _bench_args(tmp_path, runs=3, label="next", compare=str(_bench_path(tmp_path, "base")))
    )
    out = capsys.readouterr().out

    dims = _read_bench(tmp_path, "next")["comparison"]["dimensions"]
    assert "voice_distinctiveness" in dims, "the dimension must not vanish"
    assert dims["voice_distinctiveness"]["no_valid_samples"] is True
    assert "NOT SCORED" in out


# ---------------------------------------------------------------------------
# bench: Codex's twelfth review
# ---------------------------------------------------------------------------


def test_all_referenced_names_reaches_into_nested_code_objects():
    """Codex: co_names covers only the OUTER code object.

    score_quality references PROHIBITED inside a generator expression, so
    it lives in a nested code object under co_consts and the walk never
    saw it - meaning an edit to PROHIBITED changed legacy_prohibited_hits
    while evaluator_digest stayed identical.
    """
    outer = set(sdw.score_quality.__code__.co_names)
    everything = cl._all_referenced_names(sdw.score_quality.__code__)

    assert "PROHIBITED" not in outer, "this is precisely why the shallow walk missed it"
    assert "PROHIBITED" in everything
    assert outer < everything


def test_evaluator_digest_catches_a_changed_scoring_constant(monkeypatch):
    """The end-to-end consequence of the fix above."""
    baseline = cl._evaluator_digest()
    monkeypatch.setattr(sdw, "PROHIBITED", list(sdw.PROHIBITED) + ["newly-banned-phrase"])
    assert cl._evaluator_digest() != baseline, "a changed scoring constant must be caught"
    monkeypatch.undo()
    assert cl._evaluator_digest() == baseline


def test_bench_main_table_does_not_fabricate_a_score_for_an_unscored_dimension(
    tmp_path, monkeypatch, capsys
):
    """Codex: the comparison table honoured no_valid_samples but the
    PRIMARY table printed mean 0.00 - a number outside the judge's own 1-5
    scale, for a dimension it never scored."""
    def judge_missing_one(concept, stage, dialogue, episode, **kwargs):
        episode.setdefault("judge_scores", {})[stage] = {
            "turn_taking": 4,
            "voice_distinctiveness": None,
        }
        episode.setdefault("judge_weakest", {})[stage] = []
        return True, "PASS"

    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", judge_missing_one)
    capsys.readouterr()
    cl.cmd_bench(_bench_args(tmp_path, runs=3))
    out = capsys.readouterr().out

    dim_line = next(l for l in out.splitlines() if l.startswith("voice_distinctiveness"))
    assert "NOT SCORED" in dim_line
    assert "0.00" not in dim_line, "a 1-5 dimension must never be reported as 0.00"
    # The dimension that WAS scored still reports normally.
    assert "4.00" in next(l for l in out.splitlines() if l.startswith("turn_taking"))


# ---------------------------------------------------------------------------
# bench: Codex's thirteenth review (a P1 - the first since round two)
# ---------------------------------------------------------------------------


def test_canonical_repr_is_stable_for_unordered_containers():
    """P1 (Codex): repr() on a set is hash-seed dependent, and Python
    randomizes that seed PER PROCESS.

    The baseline bench and the follow-up bench are separate processes, so
    hashing repr(a_set) produced a different evaluator_digest for
    IDENTICAL code and refused a valid comparison at random. Measured
    before the fix: three runs of the same module, three different digests.
    """
    a = {"gamma", "alpha", "beta", "delta"}
    b = {"delta", "beta", "alpha", "gamma"}
    assert cl._canonical_repr(a) == cl._canonical_repr(b)

    # Nested, and dicts too - iteration order must not leak anywhere.
    assert cl._canonical_repr({"x": {3, 1, 2}, "y": 1}) == cl._canonical_repr({"y": 1, "x": {2, 3, 1}})
    assert cl._canonical_repr(frozenset({"b", "a"})) == cl._canonical_repr(frozenset({"a", "b"}))

    # Ordered containers must NOT be reordered - order is meaningful there.
    assert cl._canonical_repr([1, 2]) != cl._canonical_repr([2, 1])


def test_evaluator_digest_ignores_globals_derived_from_a_lever(monkeypatch):
    """Codex: _SHARED_RULES_SHINGLES is _word_shingles(_SHARED_CHARACTER_RULES).

    A fresh import under a changed lever recomputes it, so hashing it made
    the digest move for the very lever an experiment is allowed to change.
    The earlier test missed this because monkeypatching the lever leaves
    the derived cache stale - so this one changes the DERIVED value
    directly, which is what a real re-import would do.
    """
    baseline = cl._evaluator_digest()
    monkeypatch.setattr(sdw, "_SHARED_RULES_SHINGLES", {"completely", "different", "shingles"})
    assert cl._evaluator_digest() == baseline, "a lever-derived cache must not move the digest"


def test_bench_log_row_survives_pipes_and_newlines(tmp_path, monkeypatch):
    """Codex: a pipe in --label, or in the judge's unvalidated `weakest`
    text, split the audit row into extra columns or rows - leaving a paid
    run's required log entry malformed."""
    def judge_with_nasty_weakest(concept, stage, dialogue, episode, **kwargs):
        episode.setdefault("judge_scores", {})[stage] = {"turn_taking": 3}
        episode.setdefault("judge_weakest", {})[stage] = ["turn|taking\nand more"]
        return False, "FAIL"

    _patch_bench_generation(monkeypatch, sdw)
    monkeypatch.setattr(cl, "judge_dialogue", judge_with_nasty_weakest)
    log = tmp_path / "EXPERIMENTS.md"

    cl.cmd_bench(
        _bench_args(tmp_path, runs=2, label="a|b\nc", no_log=False, experiments_log=str(log))
    )

    rows = [l for l in log.read_text().splitlines() if l.startswith("| 2")]
    assert len(rows) == 1, "the row must not split into several"
    # Count only UNESCAPED delimiters - an escaped `\|` still contains a
    # pipe character, so a raw count would include the very thing the
    # escaping added.
    cells = [c for c in re.split(r"(?<!\\)\|", rows[0]) if c.strip()]
    assert len(cells) == 7, f"expected 7 columns, got {len(cells)}: {cells}"
    assert "\\|" in rows[0], "the delimiter in the value is escaped, not dropped"
    assert "a\\|b c" in rows[0], "the label survives, flattened and escaped"


# ---------------------------------------------------------------------------
# bench: Codex's fourteenth review — the targeted digest pass I asked for
# ---------------------------------------------------------------------------


def test_judge_input_digest_preserves_cast_ORDER():
    """Codex: I was sorting expected_cast and hiding a real difference.

    run_simulation passes participants_for_day()'s ORDERED list into
    _select_next_speaker, whose weighted selection iterates it, and
    _judge_dialogue renders the roster in that order. Two orderings really
    do produce different conversations and different judge input.
    """
    a = ["Devon Park", "Margaret Chen"]
    b = ["Margaret Chen", "Devon Park"]
    assert sorted(a) == sorted(b), "same people - only the order differs"
    assert cl._judge_input_digest({}, "facts", a) != cl._judge_input_digest({}, "facts", b)
    assert cl._generator_input_digest(a, "saturday") != cl._generator_input_digest(b, "saturday")


def test_generator_digest_tracks_the_RENDERED_prompt_not_raw_fields(monkeypatch):
    """Codex round 15: hashing raw persona records refused comparisons over
    edits the model never saw.

    build_system_prompt ignores `backstory` whenever a bio exists, and
    ignores `age` and `core_traits` entirely. Verified against the real
    persona: only communication_style of these reaches the prompt. The
    digest tracks the rendered string, so it matters exactly when the
    character is actually told something different.
    """
    real = sdw.load_personas()
    cast = ["Devon Park", "Margaret Chen"]
    baseline = cl._generator_input_digest(cast, "saturday")

    # Off-cast persona: never rendered for this bench.
    off = dict(real)
    victim = next(n for n in off if n not in cast)
    off[victim] = {
        **off[victim],
        "communication_style": {
            **off[victim]["communication_style"],
            "signature_phrases": ["utterly different"],
        },
    }
    monkeypatch.setattr(sdw, "load_personas", lambda: off)
    assert cl._generator_input_digest(cast, "saturday") == baseline, "an off-cast persona must not matter"
    monkeypatch.undo()

    # On-cast but PROMPT-INVISIBLE: backstory is ignored when a bio exists.
    ignored = dict(real)
    ignored["Devon Park"] = {**ignored["Devon Park"], "backstory": "completely rewritten"}
    monkeypatch.setattr(sdw, "load_personas", lambda: ignored)
    assert cl._generator_input_digest(cast, "saturday") == baseline, (
        "backstory never reaches the prompt when a bio exists - refusing on it "
        "was the bug"
    )
    monkeypatch.undo()

    # On-cast AND prompt-visible. Of communication_style's five keys, only
    # signature_phrases is rendered - formality, verbosity, directness and
    # emotional_expressiveness reach the prompt not at all, which is card
    # #6966 ("make the numeric dials bind") confirmed empirically.
    visible = dict(real)
    visible["Devon Park"] = {
        **visible["Devon Park"],
        "communication_style": {
            **visible["Devon Park"]["communication_style"],
            "signature_phrases": ["a brand new catchphrase"],
        },
    }
    monkeypatch.setattr(sdw, "load_personas", lambda: visible)
    assert cl._generator_input_digest(cast, "saturday") != baseline, "a rendered change MUST be caught"


def test_generator_digest_tracks_off_cast_first_episode_state(monkeypatch):
    """Codex: run_simulation computes first_episode across EVERY persona and
    uses it to pick Monday's opener, so off-cast Devon's memory changes a
    Monday prompt even when the seated cast has none of its own."""
    cast = ["Margaret Chen"]
    monkeypatch.setattr(sdw, "_load_memories", lambda name: [])
    none_at_all = cl._generator_input_digest(cast, "monday")

    # Only an OFF-cast character gains a memory.
    monkeypatch.setattr(
        sdw, "_load_memories",
        lambda name: [{"concept": "W1", "summary": "s"}] if name == "Devon Park" else [],
    )
    assert cl._generator_input_digest(cast, "monday") != none_at_all, (
        "an off-cast memory flips first_episode and changes Monday's opener"
    )
    # ...and must NOT matter on any other day, where generate_turn never
    # consults the flag.
    monkeypatch.setattr(sdw, "_load_memories", lambda name: [])
    saturday_none = cl._generator_input_digest(cast, "saturday")
    monkeypatch.setattr(
        sdw, "_load_memories",
        lambda name: [{"concept": "W1", "summary": "s"}] if name == "Devon Park" else [],
    )
    assert cl._generator_input_digest(cast, "saturday") == saturday_none


def test_judge_input_digest_normalises_whitespace_like_the_judge(monkeypatch):
    """Codex: _judge_dialogue renders ' '.join(msg.split()) and the first
    token of the name, so collapsing a double space refused a comparison
    whose judge context was byte-for-byte identical."""
    tidy = {"monday": {"dialogue": [{"character": "Margaret Chen", "message": "The ratio is off."}]}}
    messy = {"monday": {"dialogue": [{"character": "Margaret Chen", "message": "The   ratio\n is off."}]}}
    cast = ["Margaret Chen"]

    assert cl._judge_input_digest(tidy, "f", cast) == cl._judge_input_digest(messy, "f", cast)

    # A real content change must still be caught.
    different = {"monday": {"dialogue": [{"character": "Margaret Chen", "message": "The ratio is fine."}]}}
    assert cl._judge_input_digest(tidy, "f", cast) != cl._judge_input_digest(different, "f", cast)


def test_bench_refuses_a_non_dry_baseline_missing_its_dimensions(tmp_path, monkeypatch):
    """Codex: waving through a missing dimensions block meant every judge
    comparison was silently absent while the report said nothing moved."""
    monkeypatch.setattr(cl, "_resolve_models", lambda dry_run: ("openai", "d", "j"))
    monkeypatch.setattr(sdw, "run_simulation", _fail_generation)
    metrics = {"qa_rate": {"n": 3, "mean": 1.0, "stderr": 0.1}}

    bad = tmp_path / "no-dims.json"
    bad.write_text(json.dumps({
        "command": "bench", "dry_run": False, "aggregate": {"metrics": metrics},
    }))
    with pytest.raises(cl.ConversationLabError, match="silently absent"):
        cl.cmd_bench(_bench_args(tmp_path, compare=str(bad)))

    # A genuine dry-run baseline legitimately has none.
    ok = tmp_path / "dry.json"
    ok.write_text(json.dumps({
        "command": "bench", "dry_run": True, "aggregate": {"metrics": metrics},
    }))
    cl._load_bench_baseline(cl.argparse.Namespace(
        compare=str(ok), allow_mismatched_baseline=True, stage="saturday", dry_run=True,
    ))


def test_evaluator_digest_covers_this_modules_own_aggregation_layer(monkeypatch):
    """Codex: the persisted means depend on OUR filtering and aggregation,
    not only on the upstream scorers. Changing the valid-score predicate
    changes which samples reach a mean with nothing else moving."""
    baseline = cl._evaluator_digest()
    real = cl._is_valid_judge_score

    def wider_predicate(value):
        """A deliberately different valid-score rule."""
        return real(value)

    monkeypatch.setattr(cl, "_is_valid_judge_score", wider_predicate)
    assert cl._evaluator_digest() != baseline, "our own score filter must be pinned"
    monkeypatch.undo()

    monkeypatch.setattr(cl, "JUDGE_DIMENSIONS", cl.JUDGE_DIMENSIONS + ("new_dimension",))
    assert cl._evaluator_digest() != baseline, "the dimension list must be pinned"
    monkeypatch.undo()

    assert cl._evaluator_digest() == baseline


def test_all_digests_are_stable_across_processes():
    """The P1 guard, as a property rather than a one-off measurement.

    Every digest must be reproducible under a randomized hash seed, since
    a baseline bench and its follow-up are always separate processes.
    """
    import subprocess
    import sys as _sys

    script = (
        "import scripts.conversation_lab as cl;"
        "print(cl._evaluator_digest(),"
        "cl._generator_input_digest(['Devon Park','Margaret Chen'],'saturday'),"
        "cl._judge_input_digest({}, 'facts', ['Devon Park','Margaret Chen']))"
    )
    outs = {
        subprocess.run(
            [_sys.executable, "-c", script],
            capture_output=True, text=True, check=True,
            env={**os.environ, "PYTHONHASHSEED": str(seed)},
        ).stdout.strip()
        for seed in (0, 1, 12345)
    }
    assert len(outs) == 1, f"digests differ across hash seeds: {outs}"


# ---------------------------------------------------------------------------
# bench: Codex's sixteenth review
# ---------------------------------------------------------------------------


def test_generator_digest_masks_the_lever_it_renders(monkeypatch):
    """Codex: the rendered prompt CONTAINS _SHARED_CHARACTER_RULES.

    Switching to rendered prompts fixed three bugs and introduced this
    one - the sixth time this digest has refused the documented workflow.
    The lever must be masked before rendering, while everything else in
    the prompt still counts.
    """
    cast = ["Devon Park", "Margaret Chen"]
    baseline = cl._generator_input_digest(cast, "saturday")

    monkeypatch.setattr(sdw, "_SHARED_CHARACTER_RULES", "COMPLETELY DIFFERENT SHARED RULES\n")
    assert cl._generator_input_digest(cast, "saturday") == baseline, (
        "the lever under test must not refuse the comparison"
    )
    monkeypatch.undo()

    # Non-lever prompt content still counts.
    real = sdw.load_personas()
    changed = dict(real)
    changed["Devon Park"] = {
        **changed["Devon Park"],
        "communication_style": {
            **changed["Devon Park"]["communication_style"],
            "signature_phrases": ["something new"],
        },
    }
    monkeypatch.setattr(sdw, "load_personas", lambda: changed)
    assert cl._generator_input_digest(cast, "saturday") != baseline


def test_generator_digest_restores_every_lever_it_masked():
    """The mask mutates module globals, so a leak would silently change
    what the NEXT bench generates."""
    before = {lever: getattr(sdw, lever) for lever in cl.ALLOWED_VARIANT_ATTRS}
    cl._generator_input_digest(["Devon Park", "Margaret Chen"], "saturday")
    after = {lever: getattr(sdw, lever) for lever in cl.ALLOWED_VARIANT_ATTRS}
    assert before == after
    assert cl._LEVER_MASK not in sdw._SHARED_CHARACTER_RULES


def test_generator_digest_restores_levers_even_when_rendering_raises(monkeypatch):
    """A masked lever left in place would be a silent, lasting corruption
    of the generator - worse than any digest error."""
    original = sdw._SHARED_CHARACTER_RULES

    def boom(_persona):
        raise RuntimeError("render exploded")

    monkeypatch.setattr(sdw, "build_system_prompt", boom)
    cl._generator_input_digest(["Devon Park"], "saturday")  # must not raise
    assert sdw._SHARED_CHARACTER_RULES == original, "the lever must be restored on the error path"


def test_which_levers_get_masked_is_derived_not_hardcoded():
    """Masking is computed from what build_system_prompt references, so a
    future lever landing in the system prompt is picked up with no edit."""
    referenced = cl._all_referenced_names(sdw.build_system_prompt.__code__)
    masked = referenced & set(cl.ALLOWED_VARIANT_ATTRS)
    assert masked == {"_SHARED_CHARACTER_RULES"}, (
        f"today only that lever reaches the system prompt; got {masked}"
    )
