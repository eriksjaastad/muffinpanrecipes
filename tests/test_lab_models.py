"""Tests for the swappable lab model sets (#7714): scripts/lab_models.json,
`ab --models NAME`, and the OpenRouter provider-route/allowlist machinery in
backend/utils/model_router.py that it depends on.

Zero paid API calls anywhere in this file: every generation/judge call is
monkeypatched, matching the rest of the conversation-lab test suite.
"""

from __future__ import annotations

import builtins
import importlib
import json
from types import SimpleNamespace

import pytest

import scripts.conversation_lab as cl
import scripts.simulate_dialogue_week as sdw
from backend.utils import model_router


# ---------------------------------------------------------------------------
# scripts/lab_models.json - the real, checked-in file
# ---------------------------------------------------------------------------

def test_real_lab_models_file_has_claude_default_and_deepseek_set():
    models = cl._load_lab_models_file(cl.LAB_MODELS_PATH)
    assert models.default == "claude"
    assert models.sets["claude"].dialogue == "anthropic/claude-haiku-4.5"
    assert models.sets["claude"].judge == "anthropic/claude-opus-4.6"
    assert "deepseek" in models.sets
    assert models.sets["deepseek"].dialogue == "deepseek/deepseek-v4.1-flash"
    assert models.sets["deepseek"].judge == "deepseek/deepseek-v4-pro-0813"


def test_module_aliases_reproduce_the_default_sets_ids():
    """OPENROUTER_DIALOGUE_MODEL/OPENROUTER_JUDGE_MODEL are kept because other
    tests/code import them - they must always equal the DEFAULT set's ids,
    "openrouter/"-scheme-prefixed (model_router.parse_model splits on the
    FIRST "/" only)."""
    assert cl.OPENROUTER_DIALOGUE_MODEL == "openrouter/anthropic/claude-haiku-4.5"
    assert cl.OPENROUTER_JUDGE_MODEL == "openrouter/anthropic/claude-opus-4.6"


def test_lab_models_file_get_unknown_set_raises_clear_message():
    with pytest.raises(SystemExit, match="not defined"):
        cl._LAB_MODELS.get("bogus")


# ---------------------------------------------------------------------------
# _load_lab_models_file validation - a bad file must fail loud
# ---------------------------------------------------------------------------

def test_load_lab_models_file_missing(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        cl._load_lab_models_file(tmp_path / "missing.json")


def test_load_lab_models_file_bad_json(tmp_path):
    path = tmp_path / "lab_models.json"
    path.write_text("{not json")
    with pytest.raises(SystemExit, match="not valid JSON"):
        cl._load_lab_models_file(path)


def test_load_lab_models_file_not_an_object(tmp_path):
    path = tmp_path / "lab_models.json"
    path.write_text(json.dumps(["not", "an", "object"]))
    with pytest.raises(SystemExit, match="must be a JSON object"):
        cl._load_lab_models_file(path)


def test_load_lab_models_file_missing_default_key(tmp_path):
    path = tmp_path / "lab_models.json"
    path.write_text(json.dumps({
        "sets": {"claude": {"dialogue": "anthropic/claude-haiku-4.5", "judge": "anthropic/claude-opus-4.6"}},
    }))
    with pytest.raises(SystemExit, match="'default' must be a non-empty string"):
        cl._load_lab_models_file(path)


def test_load_lab_models_file_empty_sets(tmp_path):
    path = tmp_path / "lab_models.json"
    path.write_text(json.dumps({"default": "claude", "sets": {}}))
    with pytest.raises(SystemExit, match="'sets' must be a non-empty object"):
        cl._load_lab_models_file(path)


def test_load_lab_models_file_default_not_a_set(tmp_path):
    path = tmp_path / "lab_models.json"
    path.write_text(json.dumps({
        "default": "bogus",
        "sets": {"claude": {"dialogue": "anthropic/claude-haiku-4.5", "judge": "anthropic/claude-opus-4.6"}},
    }))
    with pytest.raises(SystemExit, match="not one of 'sets'"):
        cl._load_lab_models_file(path)


def test_load_lab_models_file_set_missing_judge_key(tmp_path):
    path = tmp_path / "lab_models.json"
    path.write_text(json.dumps({
        "default": "claude",
        "sets": {"claude": {"dialogue": "anthropic/claude-haiku-4.5"}},
    }))
    with pytest.raises(SystemExit, match="exactly 'dialogue' and 'judge'"):
        cl._load_lab_models_file(path)


def test_load_lab_models_file_rejects_non_object_set_body(tmp_path):
    path = tmp_path / "lab_models.json"
    path.write_text(json.dumps({"default": "claude", "sets": {"claude": ["not", "an", "object"]}}))
    with pytest.raises(SystemExit, match="exactly 'dialogue' and 'judge'"):
        cl._load_lab_models_file(path)


@pytest.mark.parametrize("bad_id", ["no-slash-at-all", "", "vendor/", "/model", 42, None])
def test_load_lab_models_file_rejects_bad_model_id_shape(tmp_path, bad_id):
    path = tmp_path / "lab_models.json"
    path.write_text(json.dumps({
        "default": "claude",
        "sets": {"claude": {"dialogue": bad_id, "judge": "anthropic/claude-opus-4.6"}},
    }))
    with pytest.raises(SystemExit, match="vendor/model"):
        cl._load_lab_models_file(path)


# ---------------------------------------------------------------------------
# model_router.openrouter_provider_route - per-vendor, not one global
# ---------------------------------------------------------------------------

def test_openrouter_provider_route_matches_legacy_constant_for_anthropic():
    assert model_router.openrouter_provider_route("anthropic/claude-haiku-4.5") == {
        "order": ["anthropic"], "allow_fallbacks": False,
    }
    assert model_router.openrouter_provider_route("anthropic/claude-haiku-4.5") == model_router.OPENROUTER_PROVIDER_ROUTE


def test_openrouter_provider_route_derives_deepseek_vendor():
    assert model_router.openrouter_provider_route("deepseek/deepseek-v4.1-flash") == {
        "order": ["deepseek"], "allow_fallbacks": False,
    }


def test_openrouter_provider_route_rejects_id_without_vendor():
    with pytest.raises(RuntimeError, match="Cannot derive"):
        model_router.openrouter_provider_route("no-vendor-prefix")


def test_generate_openrouter_derives_route_from_the_model_being_called(monkeypatch):
    """The route sent to OpenRouter must follow the model actually being
    called, not a fixed global - a deepseek call must never be routed with
    order=["anthropic"]."""
    captured: dict = {}
    model_router.reset_cost_log()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(model_router, "_central_track", lambda response, provider, **kwargs: response)
    model_router.allow_openrouter_models(dialogue="deepseek/deepseek-v4.1-flash", judge="deepseek/deepseek-v4-pro-0813")

    class FakeCompletions:
        def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=0.0001)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=usage, provider="DeepSeek",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)

    model_router.generate_response(prompt="hi", model="openrouter/deepseek/deepseek-v4.1-flash")
    assert captured["create_kwargs"]["extra_body"]["provider"] == {"order": ["deepseek"], "allow_fallbacks": False}


# ---------------------------------------------------------------------------
# allow_openrouter_models - additive registration, never a filesystem read
# ---------------------------------------------------------------------------

def test_allow_openrouter_models_extends_both_allowlists(monkeypatch):
    monkeypatch.delenv("OPENROUTER_MODEL_ALLOWLIST", raising=False)
    with pytest.raises(RuntimeError, match="not allowlisted"):
        model_router.ensure_openrouter_model_allowed("acme/unregistered-dialogue-model")

    model_router.allow_openrouter_models(dialogue="acme/registered-dialogue-model", judge="acme/registered-judge-model")
    model_router.ensure_openrouter_model_allowed("acme/registered-dialogue-model")  # must not raise

    model_router.reset_cost_log()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(model_router, "_central_track", lambda response, provider, **kwargs: response)

    class FakeCompletions:
        def create(self, **kwargs):
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=0.0001)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=usage, provider="Acme",
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    text = model_router.generate_judge_response(prompt="x", model="openrouter/acme/registered-judge-model")
    assert text == "ok"

    with pytest.raises(RuntimeError, match="judge allowlist"):
        model_router.generate_judge_response(prompt="x", model="openrouter/acme/still-unregistered-judge-model")


def test_model_router_does_not_touch_the_filesystem_at_import(monkeypatch):
    """backend/ ships in the Vercel Lambda bundle; scripts/lab_models.json
    does not (see .vercelignore). A file read at import time in model_router
    would 404 in production, so importing/reloading it must never touch
    the filesystem for anything lab_models-shaped."""
    real_open = builtins.open

    def guard_open(path, *args, **kwargs):
        if "lab_models" in str(path):
            raise AssertionError(f"model_router touched a lab_models file at import: {path}")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guard_open)
    importlib.reload(model_router)


# ---------------------------------------------------------------------------
# conversation_lab: --models flag resolution and refusals
# ---------------------------------------------------------------------------

def _args(models=None, provider="openrouter", dry_run=False, command="ab"):
    return SimpleNamespace(models=models, provider=provider, dry_run=dry_run, command=command)


def test_resolve_lab_model_set_defaults_to_claude():
    model_set = cl._resolve_lab_model_set(_args())
    assert model_set.name == "claude"
    assert model_set.dialogue == "anthropic/claude-haiku-4.5"


def test_resolve_lab_model_set_honors_explicit_deepseek():
    model_set = cl._resolve_lab_model_set(_args(models="deepseek"))
    assert model_set.name == "deepseek"
    assert model_set.dialogue == "deepseek/deepseek-v4.1-flash"
    assert model_set.judge == "deepseek/deepseek-v4-pro-0813"


def test_resolve_lab_model_set_refuses_non_default_with_anthropic_provider():
    with pytest.raises(SystemExit, match="only meaningful with --provider openrouter"):
        cl._resolve_lab_model_set(_args(models="deepseek", provider="anthropic"))


def test_resolve_lab_model_set_allows_default_with_anthropic_provider():
    model_set = cl._resolve_lab_model_set(_args(models="claude", provider="anthropic"))
    assert model_set.name == "claude"


def test_provider_route_for_openrouter_follows_the_resolved_set():
    claude_set = cl._resolve_lab_model_set(_args())
    deepseek_set = cl._resolve_lab_model_set(_args(models="deepseek"))
    assert cl._provider_route_for("openrouter", claude_set) == {"order": ["anthropic"], "allow_fallbacks": False}
    assert cl._provider_route_for("openrouter", deepseek_set) == {"order": ["deepseek"], "allow_fallbacks": False}
    assert cl._provider_route_for("anthropic", deepseek_set) is None


# ---------------------------------------------------------------------------
# STOP_CHECK provider="haiku" is out of scope for #7714 (lab convention is
# provider="jev") - combining it with a non-default model set must refuse
# instead of silently spending on real Anthropic Claude. But STOP_CHECK is
# only ever consulted when WINDDOWN_TRIGGER == "check" (see
# scripts/simulate_dialogue_week.py's run_simulation), so the refusal must
# key off each arm's EFFECTIVE (trigger, provider) pair, not the provider
# alone - the production default WINDDOWN_TRIGGER is "regex", under which a
# "haiku" STOP_CHECK is never actually called (#7680 over-strict refusal).
# ---------------------------------------------------------------------------

def test_stop_check_haiku_refuses_with_non_default_models(monkeypatch):
    """Real conflict: control's effective trigger is "check" (not the
    "regex" default) AND its provider is "haiku" - check_scene_done would
    call real Anthropic Claude Haiku on every tick."""
    monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", "check")
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "haiku"})
    with pytest.raises(SystemExit, match="haiku"):
        cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant={})


def test_stop_check_haiku_control_allowed_when_trigger_stays_regex(monkeypatch):
    """Was `test_stop_check_haiku_refuses_with_non_default_models` before the
    #7680 fix: it monkeypatched STOP_CHECK provider="haiku" but never touched
    WINDDOWN_TRIGGER, leaving it at the module default "regex" - under which
    check_scene_done is never called, so there is nothing to conflict with.
    The old test asserted this refused; that was the over-strict bug."""
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "haiku"})
    cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant={})  # must not raise


def test_stop_check_jev_control_allowed_with_non_default_models(monkeypatch):
    monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", "check")
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "jev"})
    cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant={})  # must not raise


def test_stop_check_variant_check_jev_allowed_with_non_default_models(monkeypatch):
    """The exact reproduced-bug shape: control stays at the production
    default (WINDDOWN_TRIGGER="regex", STOP_CHECK provider="haiku"), and the
    variant sets WINDDOWN_TRIGGER="check" with STOP_CHECK provider="jev".
    Neither arm ever calls the haiku stop check, so this must be allowed."""
    variant = {
        "WINDDOWN_TRIGGER": "check",
        "STOP_CHECK": {
            "provider": "jev", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5,
        },
    }
    cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant=variant)  # must not raise


def test_stop_check_variant_check_haiku_refuses(monkeypatch):
    """A variant that sets WINDDOWN_TRIGGER="check" but STOP_CHECK provider
    stays "haiku" (explicitly, or inherited because the variant never
    mentions STOP_CHECK at all) is a real conflict."""
    variant = {
        "WINDDOWN_TRIGGER": "check",
        "STOP_CHECK": {
            "provider": "haiku", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5,
        },
    }
    with pytest.raises(SystemExit, match="haiku"):
        cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant=variant)


def test_stop_check_variant_check_only_inherits_haiku_and_refuses(monkeypatch):
    """A variant that sets WINDDOWN_TRIGGER="check" but does not mention
    STOP_CHECK at all inherits the module default provider ("haiku") for
    that arm - still a real conflict."""
    variant = {"WINDDOWN_TRIGGER": "check"}
    with pytest.raises(SystemExit, match="haiku"):
        cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant=variant)


def test_stop_check_variant_override_to_jev_still_refuses_if_control_stays_haiku(monkeypatch):
    """Control is never variant-patched, so a variant that fixes STOP_CHECK
    for the variant arm alone does not save a control arm whose effective
    trigger/provider pair is still (check, haiku)."""
    monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", "check")
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "haiku"})
    variant = {"STOP_CHECK": {
        "provider": "jev", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5,
    }}
    with pytest.raises(SystemExit, match="haiku"):
        cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant=variant)


def test_stop_check_variant_override_to_haiku_refuses_even_if_control_is_jev(monkeypatch):
    """The variant arm alone can trip the refusal: it sets its own
    WINDDOWN_TRIGGER="check" and STOP_CHECK provider="haiku" even though the
    control's own STOP_CHECK (unused, since control stays "regex") is jev."""
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "jev"})
    variant = {
        "WINDDOWN_TRIGGER": "check",
        "STOP_CHECK": {
            "provider": "haiku", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5,
        },
    }
    with pytest.raises(SystemExit, match="haiku"):
        cl._refuse_if_stop_check_conflicts_with_models(_args(models="deepseek"), variant=variant)


def test_stop_check_haiku_ok_with_default_models_set(monkeypatch):
    monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", "check")
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "haiku"})
    cl._refuse_if_stop_check_conflicts_with_models(_args(models=None), variant={})
    cl._refuse_if_stop_check_conflicts_with_models(_args(models="claude"), variant={})


def test_stop_check_conflict_check_is_a_noop_under_anthropic_provider(monkeypatch):
    monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", "check")
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "haiku"})
    cl._refuse_if_stop_check_conflicts_with_models(_args(models=None, provider="anthropic"), variant={})


# ---------------------------------------------------------------------------
# End-to-end `ab --models deepseek`: every real generation/judge call gets
# the deepseek ids, and the report records the set + both resolved ids.
# ---------------------------------------------------------------------------

def _write_variant(tmp_path, extra=None):
    body = {"_SHARED_CHARACTER_RULES": "VARIANT_RULES"}
    if extra:
        body.update(extra)
    path = tmp_path / "variant.json"
    path.write_text(json.dumps(body))
    return path


def _messages(tag="lab-models-fixture"):
    return [{"character": "Margaret Chen", "message": f"{tag} line {i}"} for i in range(2)]


def test_ab_dry_run_reports_deepseek_provider_route_and_models_without_any_calls(tmp_path, monkeypatch):
    """--dry-run makes zero calls (so no OpenRouter key/balance check runs),
    but the report must still show the REAL deepseek ids - --models is
    resolved independently of --dry-run's "template" collapse used for
    actual dispatch. The stop-check/--models conflict guard DOES still run
    under --dry-run (see test_dry_run_* below), but this variant/control
    pairing (module defaults: WINDDOWN_TRIGGER="regex") has no conflict to
    catch, so it passes through silently either way."""
    model_router.reset_cost_log()
    sdw.STOP_CHECK_LOG.clear()
    monkeypatch.setattr(
        cl, "_openrouter_fetch_key",
        lambda: (_ for _ in ()).throw(AssertionError("key check must not run under --dry-run")),
    )
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages()})
    variant_path = _write_variant(tmp_path)
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--models", "deepseek", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["provider_route"] == {"order": ["deepseek"], "allow_fallbacks": False}
    assert report["models"] == {
        "set": "deepseek",
        "dialogue": "deepseek/deepseek-v4.1-flash",
        "judge": "deepseek/deepseek-v4-pro-0813",
    }


def test_ab_models_deepseek_sends_deepseek_ids_to_dialogue_and_judge_calls(tmp_path, monkeypatch):
    """A paid (mocked) run under --models deepseek must call run_simulation
    (which fans default_model out to every character line, CoT-leak retry,
    fault rewrite, and rewrite retry - scripts/simulate_dialogue_week.py's
    `default_model` parameter) and the judge with the deepseek ids, never
    the claude default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    # Lab convention: STOP_CHECK provider="jev" - out of #7714's scope, but a
    # non-default set refuses outright while it's "haiku" (module default).
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "jev"})
    monkeypatch.setattr(cl, "_openrouter_fetch_key", lambda: {"limit": 10.0, "limit_remaining": 9.5, "usage": 0.0})
    monkeypatch.setattr(cl, "_openrouter_fetch_account_balance", lambda: 100.0)
    model_router.reset_cost_log()

    seen_default_models = []

    def fake_run_simulation(*, default_model, **kwargs):
        sdw.STOP_CHECK_LOG.clear()
        seen_default_models.append(default_model)
        return {"messages": _messages()}

    seen_judge_models = []

    def fake_judge(*, model, **kwargs):
        seen_judge_models.append(model)
        return json.dumps({
            "winner": "tie",
            "per_dimension": {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "fixture",
        })

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)
    variant_path = _write_variant(tmp_path)
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--models", "deepseek",
        "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--no-log", "--results-dir", str(results_dir),
    ])

    assert seen_default_models == ["openrouter/deepseek/deepseek-v4.1-flash"] * 2
    assert seen_judge_models == ["openrouter/deepseek/deepseek-v4-pro-0813"] * 2

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["provider_route"] == {"order": ["deepseek"], "allow_fallbacks": False}
    assert report["models"]["set"] == "deepseek"


def test_ab_models_flag_rejects_unknown_set_name(tmp_path):
    with pytest.raises(SystemExit):
        cl.main([
            "ab", "--models", "bogus",
            "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(_write_variant(tmp_path)), "--recipe-context", "anchor",
            "--dry-run", "--no-log", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_models_deepseek_with_provider_anthropic_refuses(tmp_path):
    with pytest.raises(SystemExit, match="only meaningful with --provider openrouter"):
        cl.main([
            "ab", "--models", "deepseek", "--provider", "anthropic",
            "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(_write_variant(tmp_path)), "--recipe-context", "anchor",
            "--dry-run", "--no-log", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_models_deepseek_refuses_before_any_call_when_stop_check_is_haiku(tmp_path, monkeypatch):
    """Real conflict: control's effective WINDDOWN_TRIGGER is "check" (not
    the "regex" default), so its "haiku" STOP_CHECK would actually be
    called."""
    monkeypatch.setattr(sdw, "WINDDOWN_TRIGGER", "check")
    monkeypatch.setattr(sdw, "STOP_CHECK", {**sdw.STOP_CHECK, "provider": "haiku"})
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(cl, "_openrouter_fetch_key", lambda: {"limit": 10.0, "limit_remaining": 9.5, "usage": 0.0})
    monkeypatch.setattr(cl, "_openrouter_fetch_account_balance", lambda: 100.0)

    def fail_generation(**kwargs):
        raise AssertionError("generation must not start when STOP_CHECK/--models conflict")

    monkeypatch.setattr(sdw, "run_simulation", fail_generation)
    with pytest.raises(SystemExit, match="haiku"):
        cl.main([
            "ab", "--models", "deepseek",
            "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(_write_variant(tmp_path)), "--recipe-context", "anchor",
            "--no-log", "--results-dir", str(tmp_path / "results"),
        ])


def test_ab_testbed_models_deepseek_with_check_jev_variant_proceeds_past_stop_check_guard(tmp_path, monkeypatch):
    """Reproduces the exact #7680 bug shape: `ab --testbed --provider
    openrouter --models deepseek --variant <file>` where the variant sets
    WINDDOWN_TRIGGER="check" and STOP_CHECK provider="jev". The control
    stays at the untouched module defaults (WINDDOWN_TRIGGER="regex",
    STOP_CHECK provider="haiku"), which used to look like a conflict but
    never actually calls the haiku stop check. Generation and judging are
    mocked; this only asserts the run proceeds past
    `_refuse_if_stop_check_conflicts_with_models` and reaches run_simulation."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(cl, "_openrouter_fetch_key", lambda: {"limit": 10.0, "limit_remaining": 9.5, "usage": 0.0})
    monkeypatch.setattr(cl, "_openrouter_fetch_account_balance", lambda: 100.0)
    model_router.reset_cost_log()

    run_simulation_calls = []

    def fake_run_simulation(*, default_model, **kwargs):
        sdw.STOP_CHECK_LOG.clear()
        run_simulation_calls.append(default_model)
        return {"messages": _messages()}

    def fake_judge(*, model, **kwargs):
        return json.dumps({
            "winner": "tie",
            "per_dimension": {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "fixture",
        })

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)

    variant_path = _write_variant(tmp_path, extra={
        "WINDDOWN_TRIGGER": "check",
        "STOP_CHECK": {
            "provider": "jev", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5,
        },
    })
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--testbed", "--provider", "openrouter", "--models", "deepseek",
        "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path),
        "--no-log", "--results-dir", str(results_dir),
    ])

    assert run_simulation_calls, "run_simulation was never reached - the stop-check guard blocked it"
    assert all(model == "openrouter/deepseek/deepseek-v4.1-flash" for model in run_simulation_calls)


# ---------------------------------------------------------------------------
# The stop-check/--models conflict guard is pure computation (lab-models
# registry + module attributes already in memory) - no network, no
# credential read - so it must run under --dry-run too, unlike the
# OpenRouter key/balance preflight. #7680's bug could not be caught by a
# dry run before this fix, because the guard used to return immediately on
# `args.dry_run`.
# ---------------------------------------------------------------------------

def test_dry_run_with_real_conflict_still_refuses(tmp_path, monkeypatch):
    """A --dry-run with a genuine (check, haiku) conflict must refuse before
    touching anything real - no key check, no generation."""
    monkeypatch.setattr(
        cl, "_openrouter_fetch_key",
        lambda: (_ for _ in ()).throw(AssertionError("key check must not run under --dry-run")),
    )

    def fail_generation(**kwargs):
        raise AssertionError("generation must not start under --dry-run either")

    monkeypatch.setattr(sdw, "run_simulation", fail_generation)

    variant_path = _write_variant(tmp_path, extra={
        "WINDDOWN_TRIGGER": "check",
        "STOP_CHECK": {
            "provider": "haiku", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5,
        },
    })
    results_dir = tmp_path / "results"

    with pytest.raises(SystemExit, match="haiku"):
        cl.main([
            "ab", "--models", "deepseek", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
            "--variant", str(variant_path), "--recipe-context", "anchor",
            "--dry-run", "--no-log", "--results-dir", str(results_dir),
        ])

    assert not list(results_dir.glob("*-ab-*.json")), "no result file should be written on refusal"


def test_dry_run_without_conflict_makes_zero_calls(tmp_path, monkeypatch):
    """The exact previously-buggy shape (WINDDOWN_TRIGGER='check' +
    STOP_CHECK provider='jev' + --models deepseek) run under --dry-run: no
    conflict, so it must proceed all the way through with zero real calls -
    no key/balance check, no generation, no judge - and still report the
    real deepseek ids."""
    monkeypatch.setattr(
        cl, "_openrouter_fetch_key",
        lambda: (_ for _ in ()).throw(AssertionError("key check must not run under --dry-run")),
    )
    monkeypatch.setattr(sdw, "run_simulation", lambda **kw: {"messages": _messages()})
    variant_path = _write_variant(tmp_path, extra={
        "WINDDOWN_TRIGGER": "check",
        "STOP_CHECK": {
            "provider": "jev", "decided_threshold": 0.7, "require_pushback": True, "pushback_threshold": 0.5,
        },
    })
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--models", "deepseek", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--dry-run", "--no-log", "--results-dir", str(results_dir),
    ])

    [result_file] = list(results_dir.glob("*-ab-*.json"))
    report = json.loads(result_file.read_text())
    assert report["dry_run"] is True
    assert report["models"]["set"] == "deepseek"


# ---------------------------------------------------------------------------
# Cost reporting must work for a model id with no _COST_PER_M_TOKENS entry -
# OpenRouter's usage.cost is the real number, never the token-based estimate,
# and a call that returns no usage.cost must stay "missing", never $0 (#7714
# item 5: confirm this already holds for a model outside the price table).
# ---------------------------------------------------------------------------

def test_cost_by_model_totals_a_model_with_no_price_table_entry():
    assert "deepseek/deepseek-v4.1-flash" not in model_router._COST_PER_M_TOKENS
    model_router.reset_cost_log()
    model_router._record_cost(
        "openrouter", "deepseek/deepseek-v4.1-flash", 100, 20,
        actual_cost=0.00042, served_provider="DeepSeek",
    )
    totals = cl._openrouter_router_cost_by_model()
    assert totals == {"openrouter/deepseek/deepseek-v4.1-flash": 0.00042}


def test_cost_by_model_still_flags_missing_usage_cost_for_unpriced_model(capsys):
    assert "deepseek/deepseek-v4-pro-0813" not in model_router._COST_PER_M_TOKENS
    model_router.reset_cost_log()
    model_router._record_cost("openrouter", "deepseek/deepseek-v4-pro-0813", 100, 20)  # no actual_cost
    totals = cl._openrouter_router_cost_by_model()
    assert totals == {}
    assert "no usage.cost" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# #7791 P0: claude-o55 / deepseek-o55 sets pair each dialogue model with the
# fixed Opus 5.5 lab judge. The routing/pricing machinery is generic per
# vendor prefix (see model_router.openrouter_provider_route/
# openrouter_max_tokens above), so a NEW anthropic id needs no code change -
# these tests confirm that stays true for anthropic/claude-opus-5.5 specifically.
# ---------------------------------------------------------------------------

def test_real_lab_models_file_has_o55_sets():
    models = cl._load_lab_models_file(cl.LAB_MODELS_PATH)
    assert models.sets["claude-o55"].dialogue == "anthropic/claude-haiku-4.5"
    assert models.sets["claude-o55"].judge == "anthropic/claude-opus-5.5"
    assert models.sets["deepseek-o55"].dialogue == "deepseek/deepseek-v4.1-flash"
    assert models.sets["deepseek-o55"].judge == "anthropic/claude-opus-5.5"
    # Adding the new sets must never change the file's default.
    assert models.default == "claude"


def test_opus_55_gets_the_same_anthropic_max_tokens_ceiling_as_opus_46():
    assert model_router.openrouter_max_tokens("anthropic/claude-opus-5.5") == 4096
    assert (
        model_router.openrouter_max_tokens("anthropic/claude-opus-5.5")
        == model_router.openrouter_max_tokens("anthropic/claude-opus-4.6")
    )


def test_opus_55_gets_the_same_anthropic_pinned_route_as_opus_46():
    assert model_router.openrouter_provider_route("anthropic/claude-opus-5.5") == {
        "order": ["anthropic"], "allow_fallbacks": False,
    }
    assert (
        model_router.openrouter_provider_route("anthropic/claude-opus-5.5")
        == model_router.openrouter_provider_route("anthropic/claude-opus-4.6")
    )


def test_opus_55_is_registered_in_the_openrouter_judge_allowlist():
    """conversation_lab's module-level loop calls `allow_openrouter_models`
    once per lab_models.json set (including claude-o55/deepseek-o55) at cl's
    own import time. Reproduce that loop directly against the CURRENT
    model_router module rather than relying on it having already run against
    this exact module object - test_model_router_does_not_touch_the_
    filesystem_at_import (above) reloads model_router, which resets its
    _EXTRA_OPENROUTER_* globals without re-triggering cl's registration."""
    for model_set in cl._LAB_MODELS.sets.values():
        model_router.allow_openrouter_models(dialogue=model_set.dialogue, judge=model_set.judge)
    allowed = model_router.OPENROUTER_JUDGE_ALLOWLIST | model_router._EXTRA_OPENROUTER_JUDGE_MODELS
    assert "anthropic/claude-opus-5.5" in allowed


def test_ab_resolves_claude_o55_judge_model(tmp_path, monkeypatch):
    """--models claude-o55 routes the judge through openrouter/anthropic/claude-opus-5.5
    end to end, the same way --models deepseek already does for its judge."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(cl, "_openrouter_fetch_account_balance", lambda: 100.0)
    monkeypatch.setattr(
        cl, "_openrouter_fetch_key",
        lambda: {"limit": 10.0, "limit_remaining": 10.0, "usage": 0.0},
    )
    monkeypatch.setattr(
        cl, "_fetch_openrouter_model_prices",
        lambda: {
            "anthropic/claude-haiku-4.5": (0.0000008, 0.000004),
            "anthropic/claude-opus-5.5": (0.000004, 0.00002),
        },
    )

    seen_judge_models = []

    def fake_run_simulation(*, default_model, **kwargs):
        sdw.STOP_CHECK_LOG.clear()
        return {"messages": [{
            "day": "monday", "stage": "brainstorm", "character": "Margaret Chen",
            "message": "Fixture line.", "timestamp": "2026-09-30T09:00:00+00:00",
            "model": default_model, "attachments": [],
        }]}

    def fake_judge(*, model, **kwargs):
        seen_judge_models.append(model)
        return json.dumps({
            "winner": "tie",
            "per_dimension": {dim: "tie" for dim in cl.ALL_JUDGE_DIMENSIONS},
            "reason": "fixture",
        })

    monkeypatch.setattr(sdw, "run_simulation", fake_run_simulation)
    monkeypatch.setattr(model_router, "generate_judge_response", fake_judge)
    variant_path = tmp_path / "variant.json"
    variant_path.write_text(json.dumps({"WORD_CAPS": True}))
    results_dir = tmp_path / "results"

    cl.main([
        "ab", "--concept", "Test Muffins", "--stage", "monday", "--runs", "1",
        "--variant", str(variant_path), "--recipe-context", "anchor",
        "--models", "claude-o55", "--no-log", "--results-dir", str(results_dir),
    ])

    assert seen_judge_models == ["openrouter/anthropic/claude-opus-5.5"] * 2
