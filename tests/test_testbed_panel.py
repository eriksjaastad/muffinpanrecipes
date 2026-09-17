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


# --- Codex review of 7a0bfba: the lab judge could not see the method ----------

def test_every_scenario_carries_judge_ground_truth():
    """Without it the lab judge scores technique against the same abbreviated
    anchor that produced the claim - the #7104 blind spot, fixed in production
    and left in the lab, which made the W38 regression scenario decorative."""
    for sc in _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]:
        facts = sc.get("judge_recipe_facts") or ""
        assert "RECIPE GROUND TRUTH" in facts, sc["id"]
        assert "Method:" in facts, f'{sc["id"]} has no method for the judge to check against'


def test_the_lamination_regression_scenario_is_actually_detectable():
    """W38 is in the panel to catch an affirmative lamination claim.

    The recipe rolls a soft dough once and never laminates, so the word must be
    absent from its ground truth - otherwise a judge reading the facts would see
    lamination mentioned and have no reason to flag the claim.
    """
    scenarios = {s["id"]: s for s in _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]}
    assert "laminat" not in scenarios["2026-W38"]["judge_recipe_facts"].lower()


def test_the_panel_has_a_true_positive_for_lamination_too():
    """W36 is puff pastry, which genuinely IS laminated.

    A panel that only contains the negative case cannot tell a judge that
    correctly flags lamination from one that flags it always.
    """
    scenarios = {s["id"]: s for s in _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]}
    assert "laminat" in scenarios["2026-W36"]["judge_recipe_facts"].lower()


def test_judge_instruction_does_not_prime_a_regression_technique():
    """The 'acceptable hypothetical' example must not name a real regression case.

    It used 'we could laminate this...' - which, on the W38 scenario, primed the
    judge with lamination as an acceptable thing to say.
    """
    from backend.admin.cron_routes import _build_judge_recipe_facts

    facts = _build_judge_recipe_facts({
        "title": "T", "category": "sweet", "instructions": ["Mix.", "Bake."],
    }).lower()
    assert "laminat" not in facts
    assert "deep-fry" in facts


def test_lab_judge_prompt_includes_the_ground_truth():
    prompt = cl._build_pairwise_prompt(
        "Spiral Bites", "tuesday", "This week's recipe: Spiral Bites (sweet).",
        ["Margaret Chen"], [{"character": "Margaret Chen", "message": "A."}],
        [{"character": "Margaret Chen", "message": "B."}],
        recipe_facts="RECIPE GROUND TRUTH - Spiral Bites\nMethod: 1. Roll into a log.",
    )
    assert "RECIPE GROUND TRUTH" in prompt
    assert "Roll into a log" in prompt


def test_lab_judge_prompt_is_unchanged_without_facts():
    prompt = cl._build_pairwise_prompt(
        "X", "tuesday", "anchor", ["Margaret Chen"],
        [{"character": "Margaret Chen", "message": "A."}],
        [{"character": "Margaret Chen", "message": "B."}],
    )
    assert "RECIPE GROUND TRUTH" not in prompt
    assert "anchor" in prompt


# --- Codex re-review: facts must reach the JUDGE, through the real runners ----
#
# The previous fix added a recipe_facts parameter and wired nobody to it. These
# tests capture the prompt the judge is actually handed, through the runner, in
# both modes. A test of the prompt BUILDER would still have passed with the
# parameter unsupplied - that is exactly how this shipped broken.

def _capture_judge_prompts(monkeypatch):
    """Patch generation + judging; return the list of judge prompts actually sent."""
    prompts: list[str] = []

    def fake_sim(**kw):
        return {"messages": [{"character": "Margaret Chen", "message": "A line."}]}

    def fake_judge(prompt, system_prompt=None, model=None, temperature=None, **_kw):
        prompts.append(prompt)
        return json.dumps({
            "winner": "A", "scores": {}, "reason": "stub",
            "dimensions": {"voice_distinctiveness": {"A": 4, "B": 3}},
        })

    monkeypatch.setenv("DIALOGUE_MODEL", "anthropic/claude-haiku-4-5-20251001")
    monkeypatch.setenv("JUDGE_MODEL", "anthropic/claude-sonnet-4-6")
    monkeypatch.setattr(cl.simulate_module, "run_simulation", fake_sim)
    monkeypatch.setattr(cl.model_router, "generate_judge_response", fake_judge)
    return prompts


def test_testbed_run_hands_the_judge_the_recipe_method(tmp_path, monkeypatch):
    """0 of 2 judge prompts carried ground truth before this fix."""
    prompts = _capture_judge_prompts(monkeypatch)
    variant = tmp_path / "v.json"
    variant.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT"}))

    cl.main([
        "ab", "--stage", "tuesday", "--testbed", "--runs", "1",
        "--variant", str(variant), "--results-dir", str(tmp_path / "r"),
        "--max-calls", "500",
    ])

    assert prompts, "no judge prompts were captured"
    grounded = [p for p in prompts if "RECIPE GROUND TRUTH" in p]
    assert len(grounded) == len(prompts), (
        f"only {len(grounded)}/{len(prompts)} judge prompts carried ground truth"
    )
    assert any("Method:" in p for p in prompts)


def test_sweep_run_hands_the_judge_the_recipe_method(tmp_path, monkeypatch):
    prompts = _capture_judge_prompts(monkeypatch)
    sweep = tmp_path / "sweep"
    sweep.mkdir()
    (sweep / "a.json").write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT_A"}))

    cl.main([
        "ab", "--stage", "tuesday", "--sweep", str(sweep), "--runs", "1",
        "--results-dir", str(tmp_path / "r"), "--max-calls", "500",
    ])

    assert prompts, "no judge prompts were captured"
    grounded = [p for p in prompts if "RECIPE GROUND TRUTH" in p]
    assert len(grounded) == len(prompts), (
        f"only {len(grounded)}/{len(prompts)} sweep judge prompts carried ground truth"
    )


def test_the_w38_method_reaches_the_judge_verbatim(tmp_path, monkeypatch):
    """The point of the W38 scenario: the judge must see a dough rolled ONCE."""
    prompts = _capture_judge_prompts(monkeypatch)
    variant = tmp_path / "v.json"
    variant.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT"}))

    cl.main([
        "ab", "--stage", "tuesday", "--testbed", "--runs", "1",
        "--variant", str(variant), "--results-dir", str(tmp_path / "r"),
        "--max-calls", "500",
    ])

    w38 = [p for p in prompts if "Cardamom Cinnamon Spiral Bites" in p]
    assert w38, "W38 never reached the judge"
    assert any("roll the dough up tightly into a log" in p.lower() for p in w38)
    assert not any("laminat" in p.lower() for p in w38), (
        "W38's judge prompt must not mention lamination - that is the error to catch"
    )
