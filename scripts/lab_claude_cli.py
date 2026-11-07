"""Run the conversation lab's model calls on the Claude subscription (#8934).

The lab's dialogue and judge calls go through ``backend.utils.model_router``,
whose only network path is ``_generate_openrouter``. ``installed()`` swaps
that one function (plus the judge-instrument description and the Jev stop
check, which has no subscription equivalent) for a ``claude -p`` subprocess
for the duration of a lab run. Production never enters this context, so the
router itself is unchanged and the Vercel crons keep calling OpenRouter.

``claude -p`` bills the subscription only when no API key is in its
environment, so the child gets ANTHROPIC_API_KEY, OPENAI_API_KEY and
OPENROUTER_API_KEY stripped. ``--safe-mode`` keeps the user's CLAUDE.md,
hooks and plugins out of the prompt; ``--tools ""`` makes it a plain
completion. The CLI has no temperature setting, so judge and dialogue run at
the model's default.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from backend.utils import model_router, stop_check

# Paid-API keys, plus the settings that would send `claude` somewhere other
# than the subscription (a custom endpoint or token, Bedrock, Vertex).
STRIPPED_ENV_KEYS = (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
)
CALL_TIMEOUT_SECONDS = 600

_active = False
# The subprocess call, a module name so tests/conftest.py can refuse it.
_run = subprocess.run


def is_active() -> bool:
    """True while a lab run is routed to ``claude -p``."""
    return _active


def cli_model(openrouter_id: str) -> str:
    """The ``claude --model`` name for an OpenRouter Anthropic id:
    ``anthropic/claude-opus-4.6`` -> ``claude-opus-4-6``. Any other vendor
    has no subscription path and raises."""
    vendor, _, name = openrouter_id.partition("/")
    if vendor != "anthropic" or not name:
        raise RuntimeError(f"{openrouter_id!r} is not an Anthropic model; it cannot run on the Claude subscription")
    return name.replace(".", "-")


def child_env() -> dict[str, str]:
    """This process's environment without any paid-API key."""
    return {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV_KEYS}


def generate(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
    images: Optional[list[bytes]] = None,
    timeout_s: float | None = None,
) -> str:
    """Drop-in for ``model_router._generate_openrouter``: one ``claude -p``
    completion. ``temperature`` is accepted for the signature and unused."""
    if images:
        raise RuntimeError("claude -p lab transport does not take images")
    cmd = [
        "claude", "-p", "--safe-mode", "--tools", "", "--no-session-persistence",
        "--model", cli_model(model), "--output-format", "json",
    ]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    # rejudge counts calls by sent requests; everything else by cost-log entries.
    model_router._count_sent_request(None)
    with tempfile.TemporaryDirectory() as cwd:
        proc = _run(
            cmd, input=prompt, capture_output=True, text=True, cwd=cwd,
            env=child_env(), timeout=timeout_s or CALL_TIMEOUT_SECONDS,
        )
    # A $0 cost-log entry per call: the lab's mid-arm --max-calls guard counts these.
    model_router.record_external_cost("claude-cli", model, 0.0)
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p {model} exited {proc.returncode}: {(proc.stderr or proc.stdout)[:300]}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"claude -p {model} printed non-JSON output: {proc.stdout[:200]}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"claude -p {model} printed JSON that is not an object: {proc.stdout[:200]}")
    text = payload.get("result")
    if payload.get("is_error") or not isinstance(text, str) or not text.strip():
        raise RuntimeError(f"claude -p {model} returned no result: {proc.stdout[:300]}")
    return text


def judge_request_settings(model: str, temperature: float) -> dict[str, Any]:
    """Judge instrument for a ``claude -p`` judge, saved with every verdict
    so ``rejudge`` never compares it to an OpenRouter verdict."""
    routed = model_router.parse_model(model)
    return {
        "provider": "claude-cli",
        "model": cli_model(model_router.openrouter_model_id(routed.provider, routed.model)),
        "flags": "-p --safe-mode --tools '' --no-session-persistence --output-format json",
        "temperature": None,
        "request_shape": "fixed",
    }


def _refuse_jev(state: str, objective: str) -> stop_check.StopCheckResult:
    raise RuntimeError("STOP_CHECK provider 'jev' calls OpenRouter; use 'haiku' on the claude -p lab sets")


@contextmanager
def installed() -> Iterator[None]:
    """Route every lab model call to ``claude -p`` until the block exits."""
    global _active
    if shutil.which("claude") is None:
        raise SystemExit("conversation_lab: the `claude` CLI is not on PATH; this model set runs on the subscription")
    saved = (model_router._generate_openrouter, model_router.judge_request_settings, stop_check._check_jev)
    model_router._generate_openrouter = generate
    model_router.judge_request_settings = judge_request_settings
    stop_check._check_jev = _refuse_jev
    _active = True
    try:
        yield
    finally:
        model_router._generate_openrouter, model_router.judge_request_settings, stop_check._check_jev = saved
        _active = False
