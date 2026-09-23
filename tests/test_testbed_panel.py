"""The lab must say which scenario panel it ran (#7201, #7441).

v1 (`testbed.json`) was hand-written and drifted: production stopped emitting the
"Key ingredients:" anchor on 2026-09-15 (#7104) and the panel kept it, so any
sweep run against v1 silently tested a context shape production no longer uses.

v2 (`testbed-v2.json`) was frozen 2026-09-17 but predates the ingredient boundary
added by #7441. Experiments using v2 would omit that boundary, so v2 measurements
do not transfer to production behaviour.

v3 (`testbed-v3.json`) rebuilds recipe_context from #7441-aware _build_recipe_context,
so it includes ingredient boundaries. Experiments against v3 measure what production
speakers actually receive. This fix has not deployed yet and the experiment log is empty.
"""

from __future__ import annotations

import json

import pytest

import scripts.conversation_lab as cl
from backend.admin.cron_routes import _build_recipe_context, _build_judge_recipe_facts


@pytest.fixture(autouse=True)
def _redirect_lab_outputs(monkeypatch, tmp_path):
    """Keep panel tests away from the tracked experiment log by default."""
    default_results = tmp_path / "_default_results"
    monkeypatch.setattr(cl, "DEFAULT_RESULTS_DIR", default_results)
    monkeypatch.setattr(cl, "DEFAULT_EXPERIMENTS_LOG", default_results / "EXPERIMENTS.md")


def _panel(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_default_panel_is_v3():
    assert cl.DEFAULT_TESTBED_PATH.name == "testbed-v3.json"


def test_v3_carries_no_v1_stale_anchors():
    """v3 recipe_context uses the modern 'What it is:' format, not old 'Key ingredients:'."""
    for sc in _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]:
        assert "Key ingredients:" not in sc["recipe_context"], sc["id"]
        assert "What it is:" in sc["recipe_context"], sc["id"]


def test_v3_contexts_are_reproducible_from_stored_recipe_data():
    """v3 recipe_context is built mechanically from recipe_data, not hand-written.

    This ensures reproducibility and prevents drift like v1/v2 had. Speakers
    and judges use the same ground-truth recipe facts built from recipe_data.
    """
    panel = _panel(cl.DEFAULT_TESTBED_PATH)
    assert panel["builder"] == "backend.admin.cron_routes._build_recipe_context"
    for sc in panel["scenarios"]:
        recipe_data = sc.get("recipe_data")
        assert recipe_data, f"{sc['id']} has no recipe_data for reproducibility check"
        rebuilt = _build_recipe_context(recipe_data)
        assert rebuilt == sc["recipe_context"], (
            f"{sc['id']} is not reproducible from recipe_data. "
            f"Built: {rebuilt[:100]}... vs stored: {sc['recipe_context'][:100]}..."
        )
        rebuilt_facts = _build_judge_recipe_facts(recipe_data)
        assert rebuilt_facts == sc.get("judge_recipe_facts"), (
            f"{sc['id']}: judge_recipe_facts mismatch. "
            f"Built: {rebuilt_facts[:100]}... vs stored: {sc.get('judge_recipe_facts', '')[:100]}..."
        )


def test_v3_provides_varied_scenarios():
    """The panel spans categories, method complexity, and character sets."""
    scenarios = _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]
    assert len(scenarios) == 7
    assert len({s["category"] for s in scenarios}) >= 3, "categories are not varied"
    assert any(s["title_has_non_ascii"] for s in scenarios), "no accented title in the panel"
    steps = [s["method_steps"] for s in scenarios]
    assert max(steps) >= 30, "no long-method scenario"
    assert min(steps) <= 12, "no short-method scenario"


def test_the_legacy_panel_is_kept_not_deleted():
    """Pre-2026-09-15 results must stay interpretable."""
    assert cl.LEGACY_TESTBED_PATH.exists()


def test_loading_the_legacy_panel_warns_loudly(capsys):
    cl._load_testbed(cl.LEGACY_TESTBED_PATH)
    out = capsys.readouterr().out
    assert "STALE-FORMAT SCENARIOS: 5" in out
    assert "do not transfer to production" in out


def test_loading_v2_warns_loudly(capsys):
    """v2 predates the #7441 ingredient boundary and warns when loaded."""
    cl._load_testbed(cl.LEGACY_TESTBED_V2_PATH)
    out = capsys.readouterr().out
    assert "panel=v2" in out
    assert "predates the #7441 ingredient boundary" in out
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


# --- v3 tests: ingredient-aware panel (#7441) -----


def test_v3_carries_ingredient_boundaries():
    """v3 recipe_context includes ingredient boundaries that speakers can see.

    This is the #7441 fix: W39 Tuesday failed because speakers could not see
    the ingredient list and invented ingredients the recipe does not use.
    """
    for sc in _panel(cl.DEFAULT_TESTBED_PATH)["scenarios"]:
        ctx = sc["recipe_context"]
        has_boundary = (
            "Listed ingredient names (amounts, optionality" in ctx or
            "Some listed ingredient names (amounts, optionality" in ctx
        )
        assert has_boundary, f"{sc['id']} has no ingredient boundary in recipe_context"




def test_v3_preserves_v2_judge_recipe_facts():
    """v3 judge_recipe_facts match v2 exactly (no regeneration).

    The judge facts are authority for the true recipe; only recipe_context
    changed (added ingredients). Preserving judge facts ensures technique
    claims are still checked against the canonical method.
    """
    v2_panel = _panel(cl.LEGACY_TESTBED_V2_PATH)
    v3_panel = _panel(cl.DEFAULT_TESTBED_PATH)

    v2_by_id = {s["id"]: s for s in v2_panel["scenarios"]}
    v3_by_id = {s["id"]: s for s in v3_panel["scenarios"]}

    assert set(v2_by_id.keys()) == set(v3_by_id.keys()), "scenario set changed"
    for sc_id in v2_by_id:
        assert (
            v3_by_id[sc_id]["judge_recipe_facts"] == v2_by_id[sc_id]["judge_recipe_facts"]
        ), f"{sc_id}: judge_recipe_facts changed"


def test_loading_v3_announces_its_version(capsys):
    """Every panel load announces which version ran."""
    cl._load_testbed(cl.DEFAULT_TESTBED_PATH)
    out = capsys.readouterr().out
    assert "panel=v3" in out
    assert "STALE-FORMAT" not in out


def test_v3_legacy_panel_paths_remain_available():
    """v2 and v1 stay at their original paths for historical comparison."""
    assert cl.LEGACY_TESTBED_V2_PATH.exists(), f"v2 path {cl.LEGACY_TESTBED_V2_PATH} missing"
    assert cl.LEGACY_TESTBED_PATH.exists(), f"v1 path {cl.LEGACY_TESTBED_PATH} missing"

    # Verify they have different versions
    v2_panel = _panel(cl.LEGACY_TESTBED_V2_PATH)
    v1_panel = _panel(cl.LEGACY_TESTBED_PATH)
    assert v2_panel.get("panel_version") == "v2"
    assert v1_panel.get("panel_version") == "v1" or "Key ingredients:" in str(v1_panel)


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
        "--max-calls", "500", "--no-log",
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
        "--results-dir", str(tmp_path / "r"), "--max-calls", "500", "--no-log",
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
        "--max-calls", "500", "--no-log",
    ])

    w38 = [p for p in prompts if "Cardamom Cinnamon Spiral Bites" in p]
    assert w38, "W38 never reached the judge"
    assert any("roll the dough up tightly into a log" in p.lower() for p in w38)
    assert not any("laminat" in p.lower() for p in w38), (
        "W38's judge prompt must not mention lamination - that is the error to catch"
    )


def test_testbed_no_log_keeps_the_module_default_log_untouched(tmp_path):
    variant = tmp_path / "variant.json"
    variant.write_text(json.dumps({"_SHARED_CHARACTER_RULES": "VARIANT"}))
    cl.main([
        "ab", "--stage", "tuesday", "--testbed", "--runs", "1", "--variant",
        str(variant), "--dry-run", "--no-log", "--max-calls", "4",
        "--results-dir", str(tmp_path / "results"),
    ])
    assert not (tmp_path / "_default_results" / "EXPERIMENTS.md").exists()
