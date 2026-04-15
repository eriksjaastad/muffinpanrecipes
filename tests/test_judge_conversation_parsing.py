import json


def test_parse_day_verdict_extracts_scores():
    from scripts.judge_conversation import _parse_day_verdict

    raw = (
        "VERDICT: SOFT FAIL\n"
        "QUALITY_SCORE: 6\n"
        "QA_SCORE: 72\n"
        "REASON: Some issues.\n"
        "PROBLEM_LINES: None"
    )
    parsed = _parse_day_verdict(raw)
    assert parsed["verdict"] == "SOFT FAIL"
    assert parsed["quality_score"] == 6
    assert parsed["qa_score"] == 72


def test_parse_day_verdict_extracts_rationale():
    from scripts.judge_conversation import _parse_day_verdict

    raw = (
        "VERDICT: PASS\n"
        "QUALITY_SCORE: 8\n"
        "QA_SCORE: 84\n"
        "RATIONALE: Clean ensemble, real tension between Margaret and Julian, no hallucinations.\n"
        "PROBLEM_LINES: None"
    )
    parsed = _parse_day_verdict(raw)
    assert parsed["quality_score"] == 8
    assert parsed["qa_score"] == 84
    assert parsed["rationale"] is not None
    assert "Margaret" in parsed["rationale"]
    # rationale must not contain the trailing PROBLEM_LINES label
    assert "PROBLEM_LINES" not in parsed["rationale"]


def test_parse_day_verdict_reason_fallback():
    """Old-format blocks using REASON: still parse into the rationale field."""
    from scripts.judge_conversation import _parse_day_verdict

    raw = (
        "VERDICT: HARD FAIL\n"
        "QUALITY_SCORE: 3\n"
        "QA_SCORE: 40\n"
        "REASON: Hallucinated ingredient (brown butter) and missing sign-off.\n"
        "PROBLEM_LINES: 'The brown butter itself carries the story.'"
    )
    parsed = _parse_day_verdict(raw)
    assert parsed["rationale"] is not None
    assert "brown butter" in parsed["rationale"]


def test_judge_prompt_requests_numeric_fields():
    """Regression guard: the judge system prompt must explicitly ask for the numeric scales."""
    from scripts.judge_conversation import JUDGE_SYSTEM_PROMPT

    assert "QUALITY_SCORE" in JUDGE_SYSTEM_PROMPT
    assert "QA_SCORE" in JUDGE_SYSTEM_PROMPT
    assert "1-10" in JUDGE_SYSTEM_PROMPT
    assert "0-100" in JUDGE_SYSTEM_PROMPT
    assert "RATIONALE" in JUDGE_SYSTEM_PROMPT


def test_weekly_rollup_from_days_averages_numerics():
    from scripts.judge_conversation import _weekly_rollup_from_days

    day_verdicts = {
        "monday":    {"quality_score": 6, "qa_score": 70},
        "tuesday":   {"quality_score": 8, "qa_score": 90},
        "wednesday": {"quality_score": 7, "qa_score": 80},
    }
    rollup = _weekly_rollup_from_days(day_verdicts)
    assert rollup["avg_quality_score"] == 7.0
    assert rollup["avg_qa_score"] == 80.0


def test_weekly_rollup_handles_missing_scores():
    from scripts.judge_conversation import _weekly_rollup_from_days

    day_verdicts = {
        "monday": {"quality_score": None, "qa_score": None},
    }
    rollup = _weekly_rollup_from_days(day_verdicts)
    assert rollup["avg_quality_score"] is None
    assert rollup["avg_qa_score"] is None


def test_load_judgment_v1_raw_strings(tmp_path):
    """v1 judgment files store day_verdicts as raw strings — loader must not crash."""
    from scripts.judge_conversation import load_judgment

    v1 = [
        {
            "source_file": "data/simulations/foo.json",
            "concept": "Test",
            "judge_model": "anthropic/claude-opus-4-6",
            "judged_at": "2026-03-05T19:41:57.674451+00:00",
            "day_verdicts": {
                "monday": "VERDICT: PASS\nsome raw text",
                "tuesday": "VERDICT: SOFT FAIL\nsome other raw text",
            },
            "overall_verdict": "raw overall",
            "qa_score": 78,
        }
    ]
    p = tmp_path / "v1.json"
    p.write_text(json.dumps(v1))

    loaded = load_judgment(p)
    assert isinstance(loaded, list)
    rec = loaded[0]
    assert rec["schema_version"] == 1
    mon = rec["day_verdicts"]["monday"]
    assert mon["quality_score"] is None
    assert mon["qa_score"] is None
    assert mon["raw"] == "VERDICT: PASS\nsome raw text"
    # Old weekly qa_score must still be present
    assert rec["qa_score"] == 78


def test_load_judgment_v2_structured(tmp_path):
    from scripts.judge_conversation import load_judgment

    v2 = {
        "schema_version": 2,
        "source_file": "data/simulations/foo.json",
        "concept": "Test",
        "judge_model": "anthropic/claude-opus-4-6",
        "judged_at": "2026-04-15T00:00:00+00:00",
        "day_verdicts": {
            "monday": {
                "verdict": "PASS",
                "quality_score": 8,
                "qa_score": 84,
                "rationale": "Solid opening.",
            },
            "tuesday": {
                "verdict": "SOFT FAIL",
                "quality_score": 5,
                "qa_score": 62,
                "rationale": "Flat consensus, no tension.",
            },
        },
        "weekly_rollup": {"avg_quality_score": 6.5, "avg_qa_score": 73.0},
        "overall_verdict": "PUBLISH WITH NOTES",
        "qa_score": 73,
    }
    p = tmp_path / "v2.json"
    p.write_text(json.dumps(v2))

    loaded = load_judgment(p)
    assert isinstance(loaded, dict)
    assert loaded["schema_version"] == 2
    mon = loaded["day_verdicts"]["monday"]
    assert mon["quality_score"] == 8
    assert mon["qa_score"] == 84
    assert mon["rationale"] == "Solid opening."
    # Weekly rollup preserved from file
    assert loaded["weekly_rollup"]["avg_quality_score"] == 6.5
