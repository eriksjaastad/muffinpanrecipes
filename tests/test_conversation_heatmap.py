"""Tests for scripts/conversation_heatmap.py.

Zero network, zero LLM calls - this module only reads local JSON fixture
files under tmp_path (never the real data/episodes/) and does deterministic
word counting, so nothing here needs monkeypatching.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path

from scripts import conversation_heatmap as ch
from scripts import conversation_metrics as cm


def _episode(dialogue: list[tuple[str, str]], published: bool = True) -> dict:
    """Build a minimal episode payload: one "monday" stage with `dialogue`
    turns as (character, message) pairs."""
    return {
        "episode_id": "test",
        "published_at": "2026-01-01T00:00:00+00:00" if published else None,
        "stages": {
            "monday": {
                "dialogue": [{"character": character, "message": message} for character, message in dialogue]
            }
        },
    }


def _write(tmp_path: Path, filename: str, payload: dict) -> Path:
    path = tmp_path / filename
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# discover_episode_files - filename pattern skips test/scratch files
# ---------------------------------------------------------------------------


def test_discover_episode_files_skips_test_and_scratch_names(tmp_path):
    _write(tmp_path, "2026-W11.json", _episode([("Margaret Chen", "Fine.")]))
    _write(tmp_path, "2026-W09-strawberry-test.json", _episode([("Margaret Chen", "Skip me.")]))
    _write(tmp_path, "2026-W10-bookend-a.json", _episode([("Margaret Chen", "Skip me too.")]))
    _write(tmp_path, "test-local-001.json", _episode([("Margaret Chen", "Also skip.")]))

    found = ch.discover_episode_files(tmp_path)
    assert found == {"W11": tmp_path / "2026-W11.json"}


def test_build_report_never_reads_lines_from_test_named_files(tmp_path):
    _write(
        tmp_path,
        "2026-W11.json",
        _episode([("Margaret Chen", "the cross section works well every single time")] * 4),
    )
    _write(
        tmp_path,
        "2026-W09-strawberry-test.json",
        _episode([("Margaret Chen", "banana peel accordion technique banana peel accordion technique")] * 10),
    )

    report = ch.build_report(tmp_path, min_weeks=1, top=60)
    all_phrases = " ".join(hp["phrase"] for hp in report["phrase_heat"])
    assert "banana peel" not in all_phrases
    assert report["weeks"] == ["W11"]


# ---------------------------------------------------------------------------
# Distinct-week counting - the whole point of "hot" phrases
# ---------------------------------------------------------------------------


def test_hot_phrase_counts_distinct_weeks_not_raw_occurrences(tmp_path):
    # "the cross section" appears 3 times in W11 alone, but only 2 DISTINCT
    # weeks (W11, W13) - it must not out-rank a phrase that appears once
    # each in 3 distinct weeks, and must be excluded entirely at min_weeks=3.
    _write(
        tmp_path,
        "2026-W11.json",
        _episode(
            [
                ("Margaret Chen", "Look at the cross section here."),
                ("Marcus Reid", "The cross section tells the story."),
                ("Julian Torres", "The cross section again, still crisp."),
            ]
        ),
    )
    _write(tmp_path, "2026-W13.json", _episode([("Margaret Chen", "The cross section looks perfect.")]))
    _write(tmp_path, "2026-W14.json", _episode([("Margaret Chen", "Nothing about sections in this one.")]))

    report_min2 = ch.build_report(tmp_path, min_weeks=2, top=60)
    hot = {hp["phrase"]: hp for hp in report_min2["phrase_heat"]}
    assert "the cross section" in hot
    assert hot["the cross section"]["groups"] == 2
    assert hot["the cross section"]["total_occurrences"] == 4

    report_min3 = ch.build_report(tmp_path, min_weeks=3, top=60)
    assert "the cross section" not in {hp["phrase"] for hp in report_min3["phrase_heat"]}


# ---------------------------------------------------------------------------
# AREA classification of a planted hot phrase
# ---------------------------------------------------------------------------


def test_planted_boilerplate_phrase_classified_boilerplate(tmp_path):
    # "staging the muffin pan" recurs across 3 distinct weeks despite each
    # week's recipe being different - a photography/production phrase, not
    # this week's specific recipe content.
    for i, week in enumerate(("2026-W11.json", "2026-W12.json", "2026-W13.json")):
        _write(
            tmp_path,
            week,
            _episode([("Margaret Chen", f"Staging the muffin pan for shot number {i}.")]),
        )

    report = ch.build_report(tmp_path, min_weeks=3, top=60)
    hot = {hp["phrase"]: hp for hp in report["phrase_heat"]}
    assert hot["staging the muffin pan"]["area"] == "Boilerplate"
    assert hot["staging the muffin pan"]["groups"] == 3


def test_planted_frame_phrase_classified_frames(tmp_path):
    for week in ("2026-W11.json", "2026-W12.json", "2026-W13.json"):
        _write(tmp_path, week, _episode([("Marcus Reid", "Honestly, that's the story right there.")]))

    report = ch.build_report(tmp_path, min_weeks=3, top=60)
    hot = {hp["phrase"]: hp for hp in report["phrase_heat"]}
    assert hot["that's the story"]["area"] == "Frames"


def test_planted_agree_opener_phrase_classified_agree_openers(tmp_path):
    for week in ("2026-W11.json", "2026-W12.json", "2026-W13.json"):
        _write(tmp_path, week, _episode([("Julian Torres", "Marcus is right about the ratio today.")]))

    report = ch.build_report(tmp_path, min_weeks=3, top=60)
    hot = {hp["phrase"]: hp for hp in report["phrase_heat"]}
    assert hot["marcus is right"]["area"] == "Agree-openers"


def test_planted_brand_phrase_classified_brand_reinforcement(tmp_path):
    # "each cup" recurs across 3 distinct weeks with no staging/photo
    # vocabulary and no pitch-vocab phrase in the line, so it should not be
    # swallowed by Boilerplate or Pitch vocabulary ahead of it.
    for week in ("2026-W11.json", "2026-W12.json", "2026-W13.json"):
        _write(tmp_path, week, _episode([("Margaret Chen", "Fill each cup evenly before it bakes.")]))

    report = ch.build_report(tmp_path, min_weeks=3, top=60)
    hot = {hp["phrase"]: hp for hp in report["phrase_heat"]}
    assert hot["each cup"]["area"] == "Brand reinforcement"


def test_staging_the_muffin_pan_still_wins_boilerplate_over_brand(tmp_path):
    # Regression guard: PROCESS_VOCAB's "staging" must still outrank the new
    # BRAND_TERM_PATTERNS "muffin pan" match - a line about staging a shot of
    # the pan is Boilerplate, not Brand reinforcement (see classify_line's
    # docstring for why).
    for i, week in enumerate(("2026-W11.json", "2026-W12.json", "2026-W13.json")):
        _write(
            tmp_path,
            week,
            _episode([("Margaret Chen", f"Staging the muffin pan for shot number {i}.")]),
        )

    report = ch.build_report(tmp_path, min_weeks=3, top=60)
    hot = {hp["phrase"]: hp for hp in report["phrase_heat"]}
    assert hot["staging the muffin pan"]["area"] == "Boilerplate"


# ---------------------------------------------------------------------------
# Per-character structure rates - show concentration, not just corpus average
# ---------------------------------------------------------------------------


def test_structure_rates_by_character_show_concentration(tmp_path):
    _write(
        tmp_path,
        "2026-W11.json",
        _episode(
            [
                ("Marcus Reid", "The crust holds up - crisp all the way through today."),
                ("Marcus Reid", "It bakes evenly - golden edges every single time."),
                ("Devon Park", "Looks good. Ship it now."),
                ("Devon Park", "Deployed and live already."),
            ]
        ),
    )

    report = ch.build_report(tmp_path, min_weeks=1, top=60)
    rates = report["structure_rates_by_character"]
    assert rates["Marcus"]["dash_clause_rate"] == 1.0
    assert rates["Devon"]["dash_clause_rate"] == 0.0


def test_structure_rates_by_week_present_for_every_used_week(tmp_path):
    _write(tmp_path, "2026-W11.json", _episode([("Margaret Chen", "Short simple line here today please.")]))
    _write(tmp_path, "2026-W13.json", _episode([("Margaret Chen", "Another short simple line here too.")]))

    report = ch.build_report(tmp_path, min_weeks=1, top=60)
    assert set(report["structure_rates_by_week"]) == {"W11", "W13"}
    assert report["structure_rates_by_week"]["W11"]["line_count"] == 1


# ---------------------------------------------------------------------------
# Publish-state filtering
# ---------------------------------------------------------------------------


def test_unpublished_week_excluded_by_default_and_included_on_flag(tmp_path):
    _write(tmp_path, "2026-W12.json", _episode([("Margaret Chen", "Draft only, not live yet.")], published=False))

    default_report = ch.build_report(tmp_path, min_weeks=1, top=60)
    assert "W12" not in default_report["weeks"]
    assert "W12" in default_report["excluded_weeks"]

    included_report = ch.build_report(tmp_path, include_unpublished=True, min_weeks=1, top=60)
    assert "W12" in included_report["weeks"]


def test_weeks_filter_restricts_to_requested_weeks(tmp_path):
    _write(tmp_path, "2026-W11.json", _episode([("Margaret Chen", "First week line here today.")]))
    _write(tmp_path, "2026-W13.json", _episode([("Margaret Chen", "Second week line here today.")]))
    _write(tmp_path, "2026-W14.json", _episode([("Margaret Chen", "Third week line here today.")]))

    report = ch.build_report(tmp_path, weeks="W11,14", min_weeks=1, top=60)
    assert report["weeks"] == ["W11", "W14"]
    assert "W13" in report["excluded_weeks"]


# ---------------------------------------------------------------------------
# classify_phrase / classify_line - direct unit checks
# ---------------------------------------------------------------------------


def test_classify_phrase_pitch_vocab():
    assert ch.classify_phrase("stops the scroll") == "Pitch vocabulary"


def test_classify_phrase_other_when_nothing_matches():
    assert ch.classify_phrase("bake at three fifty") == "Other"


def test_classify_line_matches_classify_phrase_priority():
    assert ch.classify_line("Fair, let's just ship it.") == "Agree-openers"
    assert ch.classify_line("The cross section is staging beautifully.") == "Boilerplate"


def test_classify_line_brand_term_when_nothing_else_matches():
    assert ch.classify_line("Every cup gets the same amount of batter.") == "Brand reinforcement"


# ---------------------------------------------------------------------------
# structure_rates_by_day - always 7 rows, brand_term_rate included
# ---------------------------------------------------------------------------


def test_structure_rates_by_day_has_seven_rows_and_brand_term_rate(tmp_path):
    _write(
        tmp_path,
        "2026-W11.json",
        {
            "episode_id": "test",
            "published_at": "2026-01-01T00:00:00+00:00",
            "stages": {
                "monday": {
                    "dialogue": [
                        {"character": "Margaret Chen", "message": "Fill each cup with batter today."},
                        {"character": "Marcus Reid", "message": "The muffin pan holds twelve fine."},
                    ]
                },
                "tuesday": {
                    "dialogue": [
                        {"character": "Margaret Chen", "message": "Nothing about pans in this line."},
                    ]
                },
            },
        },
    )

    report = ch.build_report(tmp_path, min_weeks=1, top=60)
    by_day = report["structure_rates_by_day"]
    assert set(by_day) == {
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    }
    assert len(by_day) == 7
    assert "brand_term_rate" in by_day["monday"]
    assert by_day["monday"]["brand_term_rate"] == 1.0
    assert by_day["monday"]["line_count"] == 2
    assert by_day["tuesday"]["brand_term_rate"] == 0.0
    # A day with zero lines across every used week still reports a row.
    assert by_day["wednesday"]["line_count"] == 0
    assert by_day["wednesday"]["brand_term_rate"] == 0.0


# ---------------------------------------------------------------------------
# phrase_heat - the public in-memory phrase matrix (card #6492 slice 3)
# ---------------------------------------------------------------------------


def test_phrase_heat_reports_a_phrase_present_in_two_labelled_arms():
    transcripts = {
        "control": [{"character": "Margaret Chen", "message": "It holds together well today."}],
        "variant-a": [{"character": "Marcus Reid", "message": "It holds together nicely too."}],
    }
    result = ch.phrase_heat(transcripts, min_groups=2, top=30)
    hot = {hp["phrase"]: hp for hp in result["phrases"]}
    assert "it holds together" in hot
    assert hot["it holds together"]["groups"] == 2
    assert hot["it holds together"]["group_labels"] == ["control", "variant-a"]
    assert result["labels"] == ["control", "variant-a"]


def test_phrase_heat_excludes_phrase_in_only_one_label():
    transcripts = {
        "control": [{"character": "Margaret Chen", "message": "It holds together well today."}],
        "variant-a": [{"character": "Marcus Reid", "message": "Completely different phrasing entirely."}],
    }
    result = ch.phrase_heat(transcripts, min_groups=2, top=30)
    assert "it holds together" not in {hp["phrase"] for hp in result["phrases"]}


def test_phrase_heat_default_min_groups_and_top():
    sig = inspect.signature(ch.phrase_heat)
    assert sig.parameters["min_groups"].default == 2
    assert sig.parameters["top"].default == 30


def test_build_report_file_based_path_uses_phrase_heat(tmp_path):
    # The file-based CLI path must go through phrase_heat(), not a separate
    # implementation - a phrase's "groups"/"group_labels" shape in
    # build_report's output IS phrase_heat's own output shape.
    _write(tmp_path, "2026-W11.json", _episode([("Margaret Chen", "It holds together well today.")]))
    _write(tmp_path, "2026-W12.json", _episode([("Marcus Reid", "It holds together nicely too.")]))

    report = ch.build_report(tmp_path, min_weeks=2, top=60)
    hot = {hp["phrase"]: hp for hp in report["phrase_heat"]}
    assert hot["it holds together"]["groups"] == 2
    assert hot["it holds together"]["group_labels"] == ["W11", "W12"]


# ---------------------------------------------------------------------------
# area_rates - thin wrapper over conversation_metrics.summarize + AREA_METRICS
# ---------------------------------------------------------------------------


def test_area_rates_keys_match_area_metrics():
    messages = [
        {"character": "Margaret Chen", "message": "Should we lock the ratio now?"},
        {"character": "Marcus Reid", "message": "Yes, the ratio finally works."},
    ]
    result = ch.area_rates(messages)
    assert set(result) == set(cm.AREA_METRICS)
    for area, keys in cm.AREA_METRICS.items():
        assert set(result[area]) == set(keys)


def test_area_rates_reads_real_numbers_off_summarize():
    messages = [
        {"character": "Margaret Chen", "message": "Fair, the muffin pan holds twelve fine."},
        {"character": "Marcus Reid", "message": "Every cup bakes evenly today."},
    ]
    result = ch.area_rates(messages)
    assert result["Brand reinforcement"]["brand_term_rate"] == 1.0
    assert result["Cast"]["cast_ratio"] == 1.0
