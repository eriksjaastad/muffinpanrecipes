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
