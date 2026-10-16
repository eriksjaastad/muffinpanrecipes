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


def test_cost_summary_names_unpriced_calls_instead_of_counting_them_as_zero(capsys):
    """#8343: a call OpenRouter returned without usage.cost is unknown spend."""
    from scripts.run_compressed_week import print_cost_summary

    line = (
        'COST_SUMMARY: {"by_model": {"openrouter/anthropic/claude-haiku-4.5": '
        '{"calls": 3, "tokens_in": 10, "tokens_out": 5, "cost": 0.0, "unpriced_calls": 3}}}'
    )
    print_cost_summary([{"_output": line}])
    [total_row] = [r for r in capsys.readouterr().out.splitlines() if r.strip().startswith("TOTAL")]
    assert total_row.endswith("$0.0000 + 3 unpriced call(s)")
