"""A malformed COST_SUMMARY line must be reported, not silently dropped from
the cost table (silent-failure sweep, #7587)."""

from __future__ import annotations

from scripts.run_compressed_week import _extract_cost_data


def test_valid_cost_summary_is_parsed():
    output = 'stage log\nCOST_SUMMARY: {"by_model": {"m": {"calls": 1}}}\n'
    assert _extract_cost_data(output) == {"by_model": {"m": {"calls": 1}}}


def test_malformed_cost_summary_warns_on_stderr(capsys):
    assert _extract_cost_data("COST_SUMMARY: {not json") is None
    assert "unparseable COST_SUMMARY" in capsys.readouterr().err


def test_absent_cost_summary_is_silent(capsys):
    assert _extract_cost_data("no cost line here") is None
    assert capsys.readouterr().err == ""
