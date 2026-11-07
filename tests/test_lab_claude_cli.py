"""The conversation lab's `claude -p` transport (#8934)."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import scripts.conversation_lab as cl
import scripts.lab_claude_cli as lab_claude_cli
from backend.utils import model_router
from tests.conversation_research_helpers import fake_ab_result, fake_pair


class _CliReply:
    """Stands in for `claude -p`: records each call and prints `text` as its result."""

    def __init__(self, text: str):
        self.text = text
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"is_error": False, "result": self.text}), stderr="")


def test_generate_strips_api_keys_and_returns_the_result(monkeypatch):
    for key in lab_claude_cli.STRIPPED_ENV_KEYS:
        monkeypatch.setenv(key, "sk-should-not-reach-the-child")
    run = _CliReply("hello")
    monkeypatch.setattr(lab_claude_cli, "_run", run)
    before = model_router.sent_request_count()

    text = lab_claude_cli.generate("the prompt", "the system", "anthropic/claude-opus-4.6", 0.3)

    [(cmd, kwargs)] = run.calls
    assert text == "hello"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"
    assert cmd[cmd.index("--system-prompt") + 1] == "the system"
    assert kwargs["input"] == "the prompt"
    assert not set(lab_claude_cli.STRIPPED_ENV_KEYS) & set(kwargs["env"])
    assert model_router.sent_request_count() == before + 1  # counts toward --max-calls


def test_generate_raises_on_a_failed_cli_call(monkeypatch):
    monkeypatch.setattr(
        lab_claude_cli, "_run",
        lambda cmd, **_: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in"),
    )
    with pytest.raises(RuntimeError, match="exited 1: not logged in"):
        lab_claude_cli.generate("p", None, "anthropic/claude-haiku-4.5", 0.8)


def test_a_claude_cli_lab_run_never_builds_an_openrouter_client(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def no_openrouter(*_args, **_kwargs):
        raise AssertionError("the lab built an OpenRouter client")

    monkeypatch.setattr(model_router, "_generate_openrouter_request", no_openrouter)
    monkeypatch.setattr(cl, "_openrouter_preflight", no_openrouter)
    verdict = {"winner": "A", "per_dimension": {d: "A" for d in cl.ALL_JUDGE_DIMENSIONS}, "reason": "r"}
    run = _CliReply(json.dumps(verdict))
    monkeypatch.setattr(lab_claude_cli, "_run", run)
    source = fake_ab_result(tmp_path, pairs=[fake_pair(run_index=1, overall="control")])

    cl.main([
        "rejudge", str(source), "--models", "claude-o55", "--allow-instrument-change",
        "--results-dir", str(tmp_path / "out"),
    ])

    assert len(run.calls) == 2  # both judge orientations went to `claude -p`
    assert {cmd[cmd.index("--model") + 1] for cmd, _ in run.calls} == {"claude-opus-5-5"}
    [report] = [json.loads(p.read_text()) for p in (tmp_path / "out").glob("*-rejudge-*.json")]
    assert report["judge_instrument"]["provider"] == "claude-cli"


def test_max_calls_stops_a_claude_cli_arm_mid_run(monkeypatch):
    monkeypatch.setattr(lab_claude_cli, "_run", _CliReply("a line"))
    with lab_claude_cli.installed(), cl._installed_budget_guard(cl.CallBudget(max_calls=2), None):
        for _ in range(2):
            model_router.generate_response("p", model="openrouter/anthropic/claude-haiku-4.5")
        with pytest.raises(cl.LabBudgetAbort):
            model_router.generate_response("p", model="openrouter/anthropic/claude-haiku-4.5")


def test_claude_cli_sets_refuse_jev_and_accept_haiku_stop_checks():
    args = SimpleNamespace(models="claude-o55")
    with pytest.raises(SystemExit, match="'jev' only exists on OpenRouter"):
        cl._refuse_if_stop_check_conflicts_with_models(
            args, {"WINDDOWN_TRIGGER": "check", "STOP_CHECK": {"provider": "jev"}},
        )
    cl._refuse_if_stop_check_conflicts_with_models(
        args, {"WINDDOWN_TRIGGER": "check", "STOP_CHECK": {"provider": "haiku"}},
    )
