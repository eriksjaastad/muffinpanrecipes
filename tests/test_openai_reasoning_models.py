"""gpt-6-astra support in the model router (Erik, 2026-09-16).

Reasoning models differ from the chat models already wired here in two ways that
both cost money if handled naively: they take a `reasoning.effort` parameter, and
they REJECT `temperature`. The pre-existing code discovered the temperature
rejection by sending it, catching the 400, and retrying - one wasted round trip
per turn, 43 turns a week.
"""

from __future__ import annotations

import importlib
import os

import pytest

import backend.utils.model_router as router


def _reload(**env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return importlib.reload(router)


@pytest.fixture(autouse=True)
def _restore_env():
    before = os.environ.get("OPENAI_REASONING_EFFORT")
    yield
    _reload(OPENAI_REASONING_EFFORT=before)


def test_astra_is_allowlisted():
    router.ensure_openai_model_allowed("gpt-6-astra")


def test_astra_is_not_hard_blocked():
    assert "gpt-6-astra" not in router.HARD_BLOCKED_OPENAI_MODELS


def test_astra_pricing_matches_published_rates():
    """$10 / $50 per M, verified against OpenAI's published rates 2026-09-16."""
    assert router._COST_PER_M_TOKENS["gpt-6-astra"] == (10.00, 50.00)


def test_reasoning_kwargs_only_for_reasoning_models():
    assert router._reasoning_kwargs("gpt-6-astra") == {"reasoning": {"effort": "high"}}
    assert router._reasoning_kwargs("gpt-5.1") == {}
    assert router._reasoning_kwargs("gpt-5-mini") == {}


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_every_documented_effort_level_is_accepted(effort):
    r = _reload(OPENAI_REASONING_EFFORT=effort)
    assert r._reasoning_kwargs("gpt-6-astra") == {"reasoning": {"effort": effort}}


def test_an_invalid_effort_fails_loudly_rather_than_silently_defaulting():
    r = _reload(OPENAI_REASONING_EFFORT="very-high")
    with pytest.raises(RuntimeError, match="not valid"):
        r._reasoning_kwargs("gpt-6-astra")


def test_effort_is_case_and_space_insensitive():
    r = _reload(OPENAI_REASONING_EFFORT="  HIGH  ")
    assert r._reasoning_kwargs("gpt-6-astra") == {"reasoning": {"effort": "high"}}


def test_cost_estimate_for_a_realistic_weekly_workload():
    """Sanity-check the pricing entry against the measured workload.

    ~2,783 input tokens/turn x 43 turns = ~120K input, ~30K output per week.
    """
    tin, tout = router._COST_PER_M_TOKENS["gpt-6-astra"]
    weekly = (120_000 * tin + 30_000 * tout) / 1_000_000
    assert 2.0 < weekly < 4.0, f"unexpected weekly cost {weekly}"
    assert weekly * 52 < 200, "annual cost should stay well under $200"
