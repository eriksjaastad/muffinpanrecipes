"""The single alert dispatcher (backend/utils/alerts.py).

Erik does not read Discord — "I've noticed all of the Discord notifications,
but I just don't look. Emails do show up." Phase 1 added a lot of new alerts,
and an alert nobody reads is as silent as no alert at all. Email is landing as
a second channel, so every alert in the project now goes through `send_alert`
and adding that channel is a change to one file.

These tests pin the properties that make that true: nothing else owns a
transport, one dead channel cannot suppress a live one, and the pytest gate
sits in front of ALL channels rather than being re-implemented per notifier.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.utils import alerts, discord


@pytest.fixture
def _outside_pytest(monkeypatch):
    """Drop the gate so delivery itself can be exercised.

    Patch the gate rather than deleting PYTEST_CURRENT_TEST: pytest re-sets
    that variable for the call phase, AFTER fixtures run, so a monkeypatch
    .delenv here would be silently undone before the test body executes.
    """
    monkeypatch.setattr(alerts, "_pytest_gate", lambda: False)
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://discord.test/webhook")
    # RESEND_API_KEY/ALERT_EMAIL_TO stay unset here, so the real email backend
    # (now in _BACKENDS) raises inside config on every send in this file's
    # Discord-focused tests. That is enforced by conftest's autouse
    # _no_live_alert_credentials fixture, not by an assumption about the
    # ambient shell - before #7097 this comment described a hope, and
    # test_missing_webhook_is_logged_not_raised below would have mailed a real
    # inbox under `doppler run`. Suppress the
    # once-per-process unconfigured notice so it can't post an extra,
    # unmocked Discord call and steal `post.call_args` out from under a test
    # that's asserting on the *original* alert's payload — test_alert_email.py
    # covers the notice itself with this flag reset to False.
    monkeypatch.setattr(alerts, "_email_unconfigured_notice_sent", True)


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


# ---------------------------------------------------------------------------
# The gate covers every channel at once
# ---------------------------------------------------------------------------

def test_pytest_gate_blocks_delivery() -> None:
    """We are inside pytest right now, so this needs no setup."""
    with patch.object(alerts.httpx, "post") as post:
        assert alerts.send_alert("subject", "body", "critical") is False
    post.assert_not_called()


def test_the_gate_is_in_front_of_the_backends_not_inside_them(_outside_pytest) -> None:
    """A new channel inherits the gate instead of having to remember it.

    Registering a backend that ignores the gate would still be blocked,
    because send_alert returns before any backend runs.
    """
    calls: list[tuple] = []
    with patch.object(alerts, "_BACKENDS", (lambda *a: calls.append(a) or True,)):
        assert alerts.send_alert("s", "b") is True
        assert len(calls) == 1

        # Put the gate back and the same backend never runs.
        with patch.object(alerts, "_pytest_gate", return_value=True):
            assert alerts.send_alert("s", "b") is False
        assert len(calls) == 1, "a backend ran despite the gate"


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------

def test_every_backend_is_attempted_even_when_one_fails(_outside_pytest) -> None:
    """A dead Discord webhook must not suppress email once it lands."""
    attempted: list[str] = []

    def _dead(*_args):
        attempted.append("dead")
        return False

    def _live(*_args):
        attempted.append("live")
        return True

    with patch.object(alerts, "_BACKENDS", (_dead, _live)):
        assert alerts.send_alert("s", "b", "critical") is True

    assert attempted == ["dead", "live"]


def test_send_alert_reports_false_when_no_channel_accepts(_outside_pytest) -> None:
    with patch.object(alerts, "_BACKENDS", (lambda *a: False,)):
        assert alerts.send_alert("s", "b") is False


def test_a_raising_channel_does_not_propagate(_outside_pytest) -> None:
    """An alert channel must never take down the thing it reports on."""
    with patch.object(alerts.httpx, "post", side_effect=RuntimeError("dns")):
        assert alerts.send_alert("s", "b", "critical") is False


def test_missing_webhook_is_logged_not_raised(monkeypatch) -> None:
    monkeypatch.setattr(alerts, "_pytest_gate", lambda: False)
    monkeypatch.delenv("MUFFINPAN_DISCORD_WEBHOOK", raising=False)
    assert alerts.send_alert("s", "b") is False


def test_webhook_is_read_at_call_time_not_import_time(monkeypatch) -> None:
    """Doppler injects the env after import; a module-level capture would
    read None forever."""
    monkeypatch.setattr(alerts, "_pytest_gate", lambda: False)
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://set-later.test/hook")
    with patch.object(alerts.httpx, "post", return_value=_Resp(204)) as post:
        assert alerts.send_alert("s", "b") is True
    assert post.call_args.args[0] == "https://set-later.test/hook"


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------

def test_severity_maps_to_a_discord_color(_outside_pytest) -> None:
    seen = {}
    for severity in ("info", "warning", "critical"):
        with patch.object(alerts.httpx, "post", return_value=_Resp(204)) as post:
            alerts.send_alert("s", "b", severity)
        seen[severity] = post.call_args.kwargs["json"]["embeds"][0]["color"]

    assert len(set(seen.values())) == 3, seen
    assert seen["critical"] == alerts._SEVERITY_COLORS["critical"]


def test_fields_survive_with_their_inline_hint(_outside_pytest) -> None:
    with patch.object(alerts.httpx, "post", return_value=_Resp(204)) as post:
        alerts.send_alert(
            "s", "b", "critical",
            fields=[("Episode", "2026-W36", True), ("Error", "boom", False)],
        )

    embed = post.call_args.kwargs["json"]["embeds"][0]
    assert embed["fields"] == [
        {"name": "Episode", "value": "2026-W36", "inline": True},
        {"name": "Error", "value": "boom", "inline": False},
    ]


def test_a_non_204_response_is_reported_as_undelivered(_outside_pytest) -> None:
    with patch.object(alerts.httpx, "post", return_value=_Resp(429, "rate limited")):
        assert alerts.send_alert("s", "b") is False


def test_empty_field_values_do_not_break_the_embed(_outside_pytest) -> None:
    """Discord rejects an empty field value; alerts must not build one."""
    with patch.object(alerts.httpx, "post", return_value=_Resp(204)) as post:
        alerts.send_alert("s", "b", fields=[("Error", "", False)])

    assert post.call_args.kwargs["json"]["embeds"][0]["fields"][0]["value"] == "-"


# ---------------------------------------------------------------------------
# Nothing else owns a transport
# ---------------------------------------------------------------------------

def test_notifiers_only_format_and_delegate(_outside_pytest) -> None:
    """discord.py must route through send_alert, not post for itself."""
    with patch.object(alerts, "_BACKENDS", ()):
        with patch.object(discord, "send_alert", return_value=True) as send:
            discord.notify_pipeline_failure(
                recipe_id="abc12345",
                concept="Custard Tarts",
                stage="monday",
                error_message="boom",
            )

    send.assert_called_once()
    assert send.call_args.kwargs["severity"] == "critical"
    assert "Custard Tarts" in send.call_args.kwargs["body"]


def test_email_is_wired_into_the_backend_list() -> None:
    """#6860: adding email was appending it to _BACKENDS, not a parallel path.

    Payload/routing/missing-key behaviour for _send_email itself lives in
    tests/test_alert_email.py — this just pins that send_alert actually
    fans out to it.
    """
    assert alerts._send_email in alerts._BACKENDS


def test_no_module_outside_alerts_posts_to_the_webhook() -> None:
    """The property the email card depends on, enforced mechanically.

    If someone adds a direct webhook POST somewhere else, adding email stops
    being a one-file change and that channel silently misses those alerts.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    offenders = []
    for path in list((root / "backend").rglob("*.py")) + list((root / "scripts").rglob("*.py")):
        if path.name == "alerts.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"MUFFINPAN_DISCORD_WEBHOOK", text):
            offenders.append(str(path.relative_to(root)))

    assert not offenders, (
        "these modules reference the Discord webhook directly; route them "
        "through backend/utils/alerts.py::send_alert instead: " + ", ".join(offenders)
    )
