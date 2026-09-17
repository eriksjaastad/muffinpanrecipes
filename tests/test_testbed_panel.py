"""The lab must say which scenario panel it ran (#7201).

v1 (`testbed.json`) was hand-written and drifted: production stopped emitting the
"Key ingredients:" anchor on 2026-09-15 (#7104) and the panel kept it, so any
sweep run against v1 silently tested a context shape production no longer uses.
An external audit flagged it and it stayed open for two days because nothing in
the output said which panel was in play.
"""

from __future__ import annotations

import json

import pytest

import scripts.conversation_lab as cl
from backend.admin.cron_routes import _build_recipe_context


def _panel(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_default_panel_is_v2():
    assert cl.DEFAULT_TESTBED_PATH.name == "testbed-v2.json"


def test_v2_carries_no_stale_anchors():
    for sc in _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]:
        assert "Key ingredients:" not in sc["recipe_context"], sc["id"]
        assert "What it is:" in sc["recipe_context"], sc["id"]


def test_v2_contexts_match_what_the_real_builder_produces():
    """Hand-written contexts are how v1 drifted. These must be reproducible."""
    panel = _panel(cl.DEFAULT_TESTBED_PATH)
    assert panel["builder"] == "backend.admin.cron_routes._build_recipe_context"
    for sc in panel["scenarios"]:
        rebuilt = _build_recipe_context({
            "title": sc["concept"],
            "category": sc["category"],
            "description": sc["recipe_context"].split("What it is: ", 1)[1],
        })
        assert rebuilt == sc["recipe_context"], f"{sc['id']} is not reproducible from the builder"


def test_v2_spans_real_contrast():
    """A panel of five similar weeks measures one thing five times."""
    scenarios = _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]
    assert len(scenarios) >= 6
    assert len({s["category"] for s in scenarios}) >= 3, "categories are not varied"
    assert any(s["title_has_non_ascii"] for s in scenarios), "no accented title in the panel"
    steps = [s["method_steps"] for s in scenarios]
    assert max(steps) >= 30, "no long-method scenario"
    assert min(steps) <= 12, "no short-method scenario"


def test_the_legacy_panel_is_kept_not_deleted():
    """Pre-2026-09-15 results must stay interpretable."""
    assert cl.LEGACY_TESTBED_PATH.exists()


def test_loading_a_panel_announces_its_version(capsys):
    cl._load_testbed(cl.DEFAULT_TESTBED_PATH)
    out = capsys.readouterr().out
    assert "panel=v2" in out
    assert "STALE-FORMAT" not in out


def test_loading_the_legacy_panel_warns_loudly(capsys):
    cl._load_testbed(cl.LEGACY_TESTBED_PATH)
    out = capsys.readouterr().out
    assert "STALE-FORMAT SCENARIOS: 5" in out
    assert "do not transfer to production" in out
