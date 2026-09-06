"""The single door every operational alert goes through.

Erik, 2026-09-05: *"I've noticed all of the Discord notifications, but I just
don't look. Emails do show up."*

Phase 1 ("make failures loud", cards #6855-#6857) added a lot of new alerts.
Loud is only half the problem — an alert nobody reads is as silent as no alert
at all. Email landed as the second channel (#6860): appending `_send_email` to
`_BACKENDS` below was the entire change — no caller anywhere else changed.

So nothing else in the codebase talks to a webhook. `backend/utils/discord.py`
now only *formats* the four notification types; `scripts/health_check.py` only
formats its monitor summary. Both hand the result to `send_alert`.

Severity is the channel-agnostic knob. Each backend decides what it means —
Discord maps it to an embed color; email (#6860) uses it as a routing filter:
only `critical` reaches Erik's inbox, because "if everything emails, he stops
reading email too" is the exact failure this card exists to prevent.
"""

from __future__ import annotations

import os
from typing import Callable, Literal, Sequence

import httpx

from backend.config import config
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


# Email only fires for these severities (#6860). Erik: "if everything emails,
# he stops reading email too" — Discord stays the durable log for
# warning/info, email is reserved for things that need a human right now.
_EMAIL_SEVERITIES: frozenset[str] = frozenset({"critical"})

# Env vars the email backend needs. Read by both _send_email (via config,
# which raises) and email_channel_status() (which doesn't) — kept in one
# place so the two can't drift on what "configured" means.
_EMAIL_ENV_NAMES: tuple[str, ...] = ("RESEND_API_KEY", "ALERT_EMAIL_TO")

_RESEND_URL = "https://api.resend.com/emails"

# Set once a process has already told Discord the email channel is
# unconfigured, so a run that fires 50 alerts doesn't post the same notice
# 50 times. Deliberately module-level (not per-call) — see _notify_email_unconfigured.
_email_unconfigured_notice_sent = False


def email_channel_status() -> dict:
    """Pure, side-effect-free read of whether the email channel can send.

    No network call, no logging, nothing mutated — a health-check hook
    (#6860) can call this on every request without consequence. Mirrors
    config.py's env names directly rather than calling config.resend_api_key
    / config.alert_email_to, which raise instead of reporting.
    """
    missing = [name for name in _EMAIL_ENV_NAMES if not os.environ.get(name, "").strip()]
    return {"configured": not missing, "missing": missing}


def _notify_email_unconfigured() -> None:
    """Tell Discord, once per process, that email alerts have nowhere to go.

    A missing RESEND_API_KEY / ALERT_EMAIL_TO must be loud (#6860 explicitly
    calls out "an alerting system that fails silently is the joke version of
    this card") but must not take the site down — so this logs at ERROR
    *and* posts here, rather than raising out of send_alert. Calls
    _send_discord directly, not send_alert: routing this through send_alert
    would re-run severity routing and could recurse if Discord is also down.
    """
    global _email_unconfigured_notice_sent
    if _email_unconfigured_notice_sent:
        return
    _email_unconfigured_notice_sent = True
    _send_discord(
        "Alert email channel unconfigured",
        "email channel unconfigured: set RESEND_API_KEY / ALERT_EMAIL_TO",
        "warning",
        (),
        None,
    )


def _send_email(
    subject: str,
    body: str,
    severity: Severity,
    fields: Sequence[AlertField],
    url: str | None,
) -> bool:
    """Second alert backend: Resend, reserved for severities in _EMAIL_SEVERITIES.

    Plain text, not HTML — this is an operational page, not a newsletter.
    Uses httpx (already a runtime dependency here, via _send_discord) rather
    than adding a Resend SDK.
    """
    if severity not in _EMAIL_SEVERITIES:
        return False

    try:
        api_key = config.resend_api_key
        to_addr = config.alert_email_to
    except RuntimeError as e:
        logger.error(f"Email alert channel misconfigured, dropping alert {subject!r}: {e}")
        _notify_email_unconfigured()
        return False

    lines = [body]
    for name, value, _inline in fields:
        lines.append(f"{name}: {value or '-'}")
    if url:
        lines.append(f"URL: {url}")

    try:
        response = httpx.post(
            _RESEND_URL,
            json={
                "from": config.alert_email_from,
                "to": [to_addr],
                "subject": f"[muffinpanrecipes] {severity}: {subject}",
                "text": "\n".join(lines),
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        if response.status_code < 300:
            logger.info(f"Alert emailed via Resend: {subject}")
            return True
        logger.error(
            f"Resend rejected alert {subject!r}: "
            f"{response.status_code} - {response.text[:200]}"
        )
        return False
    except Exception as e:
        # Same rule as _send_discord: an alert channel that raises must not
        # take down the thing it is reporting on.
        logger.error(f"Email alert failed for {subject!r}: {type(e).__name__}: {e}")
        return False


# Ordered list of delivery backends. Adding email (#6860) was appending one
# entry here plus its sender above — no caller anywhere else changed.
_BACKENDS: tuple[Callable[..., bool], ...] = (_send_discord, _send_email)


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
