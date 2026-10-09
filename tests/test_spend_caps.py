"""The two spending caps that had no test proving they trip (#8149).

Every other lab cap already has one; tests/test_conversation_lab.py,
test_conversation_lab_budget.py, test_conversation_budget.py and test_openrouter_lab.py
cover --max-calls (ab, bench, rejudge), --max-cost, the mid-arm guard, the OpenRouter
preflights and the ledger guard's denials.
"""

import argparse

import pytest

import scripts.conversation_lab as cl
from scripts.conversation_budget import AnthropicBudgetGuard


def test_ledger_guard_refuses_a_budget_over_the_five_dollar_ceiling(tmp_path):
    with pytest.raises(ValueError, match=r"approved \$5\.00 combined ceiling"):
        AnthropicBudgetGuard(tmp_path / "ledger.json", budget_usd="5.000001")


def test_ledger_guard_accepts_exactly_five_dollars(tmp_path):
    guard = AnthropicBudgetGuard(tmp_path / "ledger.json", budget_usd="5.00")
    assert guard.budget_microusd == 5_000_000


def _subcommand_default(command: str, dest: str):
    parser = cl._build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices[command]
            return next(a.default for a in sub._actions if a.dest == dest)
    raise AssertionError(f"no {command} subcommand")


def test_calibrate_caps_calls_at_forty_by_default():
    assert _subcommand_default("calibrate", "max_calls") == 40
