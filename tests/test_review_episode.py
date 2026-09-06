"""Tests for the zero-cost weekly measurement CLI (#6492).

No network anywhere. `--local` is asserted to never call urllib (the
production loader's CDN branch is exercised only implicitly, by NOT calling
it -- these tests never set `local=False` against a live host). Every
LLM/network entry point that could reach one is nonexistent in this module
by construction: review_episode.py never imports model_router or
simulate_dialogue_week.run_simulation, only the pure helpers
(DAY_ORDER, participants_for_day).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.review_episode as review_episode
from backend.utils.episode_integrity import PLACEHOLDER_CONCEPT
from scripts.review_episode import (
    build_coverage_rows,
    build_coverage_summary,
    build_day_report,
    build_header,
    build_rubric_report,
    find_orphan_episode_ids,
    find_title_mismatches,
    judge_meta,
    local_episode_ids,
    normalize_first_name,
    qa_summary,
    render_rubric_text,
)


# ---------------------------------------------------------------------------
# Fixtures: (a) legacy shape (W10-W36), (b) W37-shape with structured judge
# ---------------------------------------------------------------------------


def _legacy_episode() -> dict:
    """Pre-#6861 shape: judge_verdict is a plain string, nothing structured.
    Wednesday is `complete` with zero messages, mirroring real W10.
    """
    return {
        "episode_id": "2026-W99",
        "concept": PLACEHOLDER_CONCEPT,  # the trap this tool must never print
        "published_at": "2026-05-01T00:00:00+00:00",
        "stages": {
            "monday": {
                "stage": "brainstorm",
                "status": "complete",
                "recipe_data": {
                    "title": "Legacy Lemon Cups",
                    "category": "sweet",
                    "cuisine": "American",
                    "ingredients": [{"item": "lemon"}, {"item": "flour"}],
                },
                "dialogue": [
                    {"character": "Margaret Chen", "message": "Alright.  Let's\n go."},
                    {"character": "Marcus Reid", "message": "Sounds   good."},
                ],
                "judge_verdict": "PASS - fine.",
            },
            "tuesday": {
                "stage": "recipe_development",
                "status": "complete",
                "dialogue": [
                    {"character": "Margaret Chen", "message": "Batch two."},
                    # Not in tuesday's expected roster -> unexpected speaker.
                    {"character": "Julian Torres", "message": "Snuck in a photo."},
                ],
                "judge_verdict": "PASS - ok.",
            },
            "wednesday": {
                "stage": "photography",
                "status": "complete",
                "dialogue": [],  # zero-message complete day, like real W10
                "judge_verdict": "PASS - nothing to say.",
            },
            "thursday": {"status": "missing"},
            "friday": {"status": "missing"},
            "saturday": {"status": "missing"},
            "sunday": {"status": "missing"},
        },
    }


def _w37_episode() -> dict:
    """Post-#6861 shape: structured judge_scores/weakest/reason live on the
    stage dict AND at episode['judge_scores'][stage] (cron_routes.py:401-404,
    470-482). Monday's dialogue includes one unexpected speaker (Devon, not
    in Monday's roster) so the cast-mismatch path is exercised both ways.
    """
    stage_scores = {"title_fidelity": 5, "arc_resolution": 4, "turn_taking": 4}
    return {
        "episode_id": "2026-W37",
        "concept": "A real weekly concept",
        "published_at": None,
        "qa_scores": {
            "monday": {
                "score": 91,
                "details": {
                    "score": 91,
                    "prompt_echo_hits": 0,
                    "min_content_failures": 0,
                    "cross_character_overlap_penalty": 0.0,
                },
            },
        },
        "judge_scores": {"monday": stage_scores},
        "judge_weakest": {"monday": ["arc_resolution"]},
        "judge_reason": {"monday": "solid week"},
        "stages": {
            "monday": {
                "stage": "brainstorm",
                "status": "complete",
                "concept_source": "auto_pick",
                "gate_trace": [
                    {"attempt": 1, "gate": "title", "result": "conflict"},
                    {"attempt": 2, "gate": "ingredients", "status": "ok"},
                ],
                "recipe_data": {
                    "title": "Real Recipe Title Cups",
                    "category": "savory",
                    "cuisine": "Mexican",
                    "ingredients": [{"item": "corn"}] * 5,
                },
                "dialogue": [
                    {"character": "Margaret Chen", "message": "Hi."},
                    {"character": "Marcus Reid", "message": "Hi back."},
                    {"character": "Stephanie 'Steph' Whitmore", "message": "Hey all."},
                    {"character": "Julian Torres", "message": "Ready."},
                    {"character": "Ria Castillo", "message": "Let's go."},
                    {"character": "Devon Park", "message": "Dropping in."},
                ],
                "judge_verdict": "PASS - great week",
                "judge_scores": stage_scores,
                "judge_weakest": ["arc_resolution"],
                "judge_reason": "solid week",
            },
        },
    }


# ---------------------------------------------------------------------------
# Title never the placeholder
# ---------------------------------------------------------------------------


def test_title_is_never_the_placeholder_concept() -> None:
    episode = _legacy_episode()
    header = build_header(episode)
    assert header["title"] == "Legacy Lemon Cups"
    assert header["title"] != PLACEHOLDER_CONCEPT
    # The placeholder really is sitting on episode['concept'] in this fixture
    # -- proves the assertion above is testing the right thing.
    assert episode["concept"] == PLACEHOLDER_CONCEPT


def test_header_reads_category_cuisine_ingredient_count_and_gate_trace() -> None:
    header = build_header(_w37_episode())
    assert header["category"] == "savory"
    assert header["cuisine"] == "Mexican"
    assert header["ingredient_count"] == 5
    assert header["concept_source"] == "auto_pick"
    assert header["gate_trace_entries"] == 2
    assert header["gate_trace_attempts"] == 2  # max(attempt) across the trace


def test_header_defaults_are_explicit_when_absent() -> None:
    header = build_header(_legacy_episode())
    assert header["concept_source"] is None
    assert header["gate_trace_attempts"] == 0
    assert header["gate_trace_entries"] == 0


# ---------------------------------------------------------------------------
# Cast normalization + expected vs actual
# ---------------------------------------------------------------------------


def test_normalize_first_name_handles_quoted_nickname() -> None:
    assert normalize_first_name("Stephanie 'Steph' Whitmore") == "Stephanie"
    assert normalize_first_name("Margaret Chen") == "Margaret"
    assert normalize_first_name("") == "?"


def test_expected_vs_actual_cast_flags_missing_and_unexpected() -> None:
    episode = _legacy_episode()
    day_report = build_day_report("tuesday", episode)
    cast = day_report["cast"]
    # Tuesday's real roster is Margaret, Stephanie, Marcus, Devon.
    assert "Devon" in cast["expected"]
    assert cast["actual"] == ["Margaret", "Julian"]
    assert "Devon" in cast["missing"]
    assert cast["unexpected"] == ["Julian"]


def test_w37_monday_has_one_unexpected_speaker() -> None:
    day_report = build_day_report("monday", _w37_episode())
    assert day_report["cast"]["unexpected"] == ["Devon"]
    assert day_report["cast"]["missing"] == []


def test_transcript_collapses_whitespace() -> None:
    episode = _legacy_episode()
    day_report = build_day_report("monday", episode)
    assert day_report["transcript"][0] == "Margaret: Alright. Let's go."
    assert day_report["transcript"][1] == "Marcus: Sounds good."


def test_zero_message_complete_day_is_reported_not_dropped() -> None:
    day_report = build_day_report("wednesday", _legacy_episode())
    assert day_report["status"] == "complete"
    assert day_report["message_count"] == 0
    assert day_report["transcript"] == []
    assert day_report["judge_verdict"] == "PASS - nothing to say."


# ---------------------------------------------------------------------------
# Judge scores: legacy (verdict string only) vs W37 (structured, both spots)
# ---------------------------------------------------------------------------


def test_judge_meta_is_empty_on_a_legacy_verdict_only_stage() -> None:
    episode = _legacy_episode()
    stage = episode["stages"]["monday"]
    meta = judge_meta(stage, episode, "monday")
    assert meta == {"judge_scores": {}, "judge_weakest": [], "judge_reason": ""}
    # But judge_verdict itself is still surfaced.
    day_report = build_day_report("monday", episode)
    assert day_report["judge_verdict"] == "PASS - fine."


def test_judge_meta_reads_structured_scores_from_the_stage() -> None:
    episode = _w37_episode()
    stage = episode["stages"]["monday"]
    meta = judge_meta(stage, episode, "monday")
    assert meta["judge_scores"] == {"title_fidelity": 5, "arc_resolution": 4, "turn_taking": 4}
    assert meta["judge_weakest"] == ["arc_resolution"]
    assert meta["judge_reason"] == "solid week"


def test_judge_meta_falls_back_to_episode_level_when_stage_lacks_it() -> None:
    """A stage dict missing judge_scores/weakest/reason (e.g. written by an
    older code path) must still read the values _judge_dialogue stashed at
    episode['judge_scores'][stage] etc (cron_routes.py:401-404).
    """
    episode = _w37_episode()
    stage_without_meta = {"status": "complete", "judge_verdict": "PASS - great week"}
    meta = judge_meta(stage_without_meta, episode, "monday")
    assert meta["judge_scores"] == {"title_fidelity": 5, "arc_resolution": 4, "turn_taking": 4}
    assert meta["judge_weakest"] == ["arc_resolution"]
    assert meta["judge_reason"] == "solid week"


# ---------------------------------------------------------------------------
# QA scores
# ---------------------------------------------------------------------------


def test_qa_summary_reads_nested_details() -> None:
    summary = qa_summary(_w37_episode(), "monday")
    assert summary == {
        "score": 91,
        "prompt_echo_hits": 0,
        "min_content_failures": 0,
        "cross_character_overlap_penalty": 0.0,
    }


def test_qa_summary_is_none_when_absent() -> None:
    assert qa_summary(_legacy_episode(), "monday") is None


# ---------------------------------------------------------------------------
# --coverage math
# ---------------------------------------------------------------------------


TINY_CATALOG = [
    {"slug": "seed-one", "title": "Seed One", "episode_id": None},
    {"slug": "found-recipe", "title": "Found Recipe Cups", "episode_id": "2026-W50"},
    {"slug": "missing-recipe", "title": "Missing Recipe Cups", "episode_id": "2026-W51"},
]


def test_coverage_math_on_a_tiny_catalog() -> None:
    found_episode = {
        "episode_id": "2026-W50",
        "stages": {
            "monday": {"dialogue": [{"character": "Margaret Chen", "message": "hi"}]},
            "tuesday": {"dialogue": [{"character": "Margaret Chen", "message": "hi2"}]},
        },
    }
    episodes_by_id = {"2026-W50": found_episode, "2026-W51": None}

    rows = build_coverage_rows(TINY_CATALOG, episodes_by_id)
    summary = build_coverage_summary(rows)

    assert summary == {"total_recipes": 3, "with_episode_id": 2, "with_dialogue": 1}

    seed_row = next(r for r in rows if r["seed"])
    assert seed_row["episode_id"] is None

    missing_row = next(r for r in rows if r["episode_id"] == "2026-W51")
    assert missing_row["episode_found"] is False
    assert missing_row["message_count"] == 0

    found_row = next(r for r in rows if r["episode_id"] == "2026-W50")
    assert found_row["episode_found"] is True
    assert found_row["days_with_dialogue"] == 2
    assert found_row["message_count"] == 2


def test_title_mismatch_detection() -> None:
    rows = [
        {
            "seed": False, "episode_found": True, "episode_id": "2026-W50",
            "catalog_title": "Found Recipe Cups", "episode_title": "Found Recipe Prep Cups",
        },
        {
            "seed": False, "episode_found": True, "episode_id": "2026-W51",
            "catalog_title": "Same Title", "episode_title": "Same Title",
        },
        {
            "seed": True, "episode_found": None, "episode_id": None,
            "catalog_title": "Seed", "episode_title": None,
        },
    ]
    mismatches = find_title_mismatches(rows)
    assert [m["episode_id"] for m in mismatches] == ["2026-W50"]


def test_orphan_episode_ids() -> None:
    catalog = [{"episode_id": "2026-W50"}, {"episode_id": None}]
    local_ids = {"2026-W50", "2026-W51", "2026-W52"}
    assert find_orphan_episode_ids(catalog, local_ids) == ["2026-W51", "2026-W52"]


def test_local_episode_ids_ignores_suffixed_test_files(tmp_path: Path) -> None:
    (tmp_path / "2026-W50.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2026-W50-bookend-a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "2026-W09-tiramisu-1.json").write_text("{}", encoding="utf-8")
    assert local_episode_ids(tmp_path) == {"2026-W50"}


def test_local_episode_ids_on_missing_directory(tmp_path: Path) -> None:
    assert local_episode_ids(tmp_path / "does-not-exist") == set()


# ---------------------------------------------------------------------------
# --rubric
# ---------------------------------------------------------------------------


def test_rubric_report_has_six_dimensions_and_template_row() -> None:
    report = build_rubric_report()
    assert len(report["dimensions"]) == 6
    assert "Title fidelity" in report["dimensions"]
    assert "Promise/delivery alignment" in report["dimensions"]
    text = render_rubric_text(report)
    assert "promptV" in text
    assert "Six dimensions:" in text


# ---------------------------------------------------------------------------
# CLI: --local never touches the network, and warns loudly that it read one
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("network touched under --local")

    monkeypatch.setattr(review_episode.urllib.request, "urlopen", _boom)


def test_local_single_episode_never_touches_network_and_warns_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    episodes_dir = tmp_path / "episodes"
    episodes_dir.mkdir()
    _write_json(episodes_dir / "2026-W99.json", _legacy_episode())
    monkeypatch.setattr(review_episode, "EPISODES_DIR", episodes_dir)
    _forbid_network(monkeypatch)

    exit_code = review_episode.main(["2026-W99", "--local"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Legacy Lemon Cups" in captured.out
    assert "STALE MIRROR" in captured.err
    assert "write-through mirrors" in captured.err


def test_local_coverage_never_touches_network(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    episodes_dir = tmp_path / "episodes"
    episodes_dir.mkdir()
    _write_json(episodes_dir / "2026-W99.json", _legacy_episode())
    recipes_path = tmp_path / "recipes.json"
    _write_json(
        recipes_path,
        {
            "recipes": [
                {"slug": "seed", "title": "Seed", "episode_id": None},
                {"slug": "legacy", "title": "Legacy Lemon Cups", "episode_id": "2026-W99"},
            ]
        },
    )
    monkeypatch.setattr(review_episode, "EPISODES_DIR", episodes_dir)
    monkeypatch.setattr(review_episode, "RECIPES_JSON_PATH", recipes_path)
    _forbid_network(monkeypatch)

    exit_code = review_episode.main(["--coverage", "--local"])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Totals: 2 recipes, 1 with episode_id, 1 with dialogue" in captured.out
    assert "STALE MIRROR" in captured.err


def test_json_output_is_valid_json_and_matches_text_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    episodes_dir = tmp_path / "episodes"
    episodes_dir.mkdir()
    _write_json(episodes_dir / "2026-W99.json", _legacy_episode())
    monkeypatch.setattr(review_episode, "EPISODES_DIR", episodes_dir)
    _forbid_network(monkeypatch)

    exit_code = review_episode.main(["2026-W99", "--local", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["header"]["title"] == "Legacy Lemon Cups"
    assert payload["days"][0]["day"] == "monday"


def test_cli_requires_episode_id_unless_coverage_or_rubric() -> None:
    with pytest.raises(SystemExit) as exc_info:
        review_episode.main([])
    assert exc_info.value.code == 2


def test_rubric_mode_needs_no_episode_id_or_files() -> None:
    # No episode_id, no --local, no monkeypatched files: --rubric must not
    # try to load anything at all.
    exit_code = review_episode.main(["--rubric"])
    assert exit_code == 0
