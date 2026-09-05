"""The single door every operational alert goes through.

Erik, 2026-09-05: *"I've noticed all of the Discord notifications, but I just
don't look. Emails do show up."*

Phase 1 ("make failures loud", cards #6855-#6857) added a lot of new alerts.
Loud is only half the problem — an alert nobody reads is as silent as no alert
at all. Email is coming as a second channel on a follow-up card, and the point
of this module is that adding it is a change to THIS FILE ONLY: append a
backend to `_BACKENDS`.

So nothing else in the codebase talks to a webhook. `backend/utils/discord.py`
now only *formats* the four notification types; `scripts/health_check.py` only
formats its monitor summary. Both hand the result to `send_alert`.

Severity is the channel-agnostic knob. Each backend decides what it means —
Discord maps it to an embed color; email would map it to a subject prefix or a
routing rule.
"""

from __future__ import annotations

import os
from typing import Callable, Literal, Sequence

import httpx

from backend.utils.logging import get_logger

logger = get_logger(__name__)

Severity = Literal["info", "warning", "critical"]

# (name, value, inline) — `inline` is a rendering hint a backend may ignore.
AlertField = tuple[str, str, bool]

_SEVERITY_COLORS: dict[str, int] = {
    "critical": 0xE74C3C,  # red — the pipeline stopped
    "warning": 0xE67E22,   # orange — degraded, or a gate refused
    "info": 0x27AE60,      # green — informational, nothing is wrong
}

_WEBHOOK_ENV = "MUFFINPAN_DISCORD_WEBHOOK"


def _pytest_gate() -> bool:
    """Return True if we should skip notifying (we're inside pytest).

    pytest auto-sets PYTEST_CURRENT_TEST for every test run. #5911 surfaced
    this: test_pipeline_fail_fast.py legitimately exercises the failure path,
    but the notify call inside orchestrator re-raised after firing, so
    pytest.raises caught it and Discord had already been pinged.

    Checked once here rather than once per notification type, so a new alert
    channel or a new caller cannot forget it.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _webhook_url() -> str | None:
    """Read the webhook at call time, not import time.

    Doppler injects the environment for the process, and scripts that import
    backend.utils before that has settled would otherwise capture None
    forever. Reading per call also makes monkeypatch.setenv work in tests.
    """
    return os.environ.get(_WEBHOOK_ENV)


def _send_discord(
    subject: str,
    body: str,
    severity: Severity,
    fields: Sequence[AlertField],
    url: str | None,
) -> bool:
    webhook = _webhook_url()
    if not webhook:
        logger.warning(f"No {_WEBHOOK_ENV} configured; dropping alert: {subject}")
        return False

    embed: dict = {
        "title": subject[:256],
        "description": body[:4000],
        "color": _SEVERITY_COLORS.get(severity, _SEVERITY_COLORS["warning"]),
    }
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = [
            {"name": name[:256], "value": (value or "-")[:1024], "inline": inline}
            for name, value, inline in fields
        ]

    try:
        response = httpx.post(webhook, json={"embeds": [embed]}, timeout=10.0)
        if response.status_code == 204:
            logger.info(f"Alert sent to Discord: {subject}")
            return True
        logger.error(
            f"Discord webhook rejected alert {subject!r}: "
            f"{response.status_code} - {response.text[:200]}"
        )
        return False
    except Exception as e:
        # An alert channel that raises must not take down the thing it is
        # reporting on. This is the one place in the project where swallowing
        # is correct, and it is logged at ERROR.
        logger.error(f"Discord alert failed for {subject!r}: {type(e).__name__}: {e}")
        return False


# Ordered list of delivery backends. Adding email is appending one entry here
# plus its sender above — no caller anywhere else changes.
_BACKENDS: tuple[Callable[..., bool], ...] = (_send_discord,)


def send_alert(
    subject: str,
    body: str,
    severity: Severity = "warning",
    *,
    fields: Sequence[AlertField] | None = None,
    url: str | None = None,
) -> bool:
    """Deliver one alert to every configured channel.

    Returns True if at least one backend accepted it. Every backend is
    attempted regardless of the others' results, so one dead channel never
    suppresses a live one.
    """
    if _pytest_gate():
        return False

    delivered = False
    for backend in _BACKENDS:
        if backend(subject, body, severity, fields or (), url):
            delivered = True
    return delivered
