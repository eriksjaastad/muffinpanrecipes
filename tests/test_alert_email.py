"""The email backend for backend/utils/alerts.py (#6860).

Erik, 2026-09-05: "I've noticed all of the Discord notifications, but I just
don't look. Emails do show up." This pins the behaviour that makes email a
usable *second* channel rather than a second way to be ignored:

- only `critical` reaches the inbox (warning/info stay Discord-only, so email
  doesn't become the thing he tunes out too),
- a missing RESEND_API_KEY / ALERT_EMAIL_TO is loud (logged + a one-time
  Discord notice) but never raises out of send_alert or drops the original
  Discord alert,
- a Resend HTTP failure is logged, not raised — same swallow-and-log contract
  as _send_discord.

test_alerts.py's `_outside_pytest` fixture suppresses the unconfigured-notice
flag by default so its Discord-payload assertions aren't disturbed by this
backend; the tests here reset that flag explicitly to exercise it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.utils import alerts


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def _outside_pytest(monkeypatch):
    """Same drop-the-gate move as test_alerts.py, plus a real email config."""
    monkeypatch.setattr(alerts, "_pytest_gate", lambda: False)
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://discord.test/webhook")
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setenv("ALERT_EMAIL_TO", "erik@example.com")
    monkeypatch.delenv("ALERT_EMAIL_FROM", raising=False)
    monkeypatch.setattr(alerts, "_email_unconfigured_notice_sent", False)


@pytest.fixture
def _unconfigured(monkeypatch):
    """Same as _outside_pytest but with the email env deliberately absent."""
    monkeypatch.setattr(alerts, "_pytest_gate", lambda: False)
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://discord.test/webhook")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("ALERT_EMAIL_TO", raising=False)
    monkeypatch.setattr(alerts, "_email_unconfigured_notice_sent", False)


# ---------------------------------------------------------------------------
# Severity routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("severity", ["info", "warning"])
def test_non_critical_severities_never_touch_resend(severity, _outside_pytest) -> None:
    with patch.object(alerts.httpx, "post", return_value=_Resp(204)) as post:
        alerts.send_alert("s", "b", severity)

    # Discord still posts; only the Resend call must be absent.
    assert all(call.args[0] != alerts._RESEND_URL for call in post.call_args_list)


def test_critical_severity_calls_resend(_outside_pytest) -> None:
    with patch.object(alerts.httpx, "post", return_value=_Resp(200)) as post:
        assert alerts.send_alert("s", "b", "critical") is True

    resend_calls = [c for c in post.call_args_list if c.args[0] == alerts._RESEND_URL]
    assert len(resend_calls) == 1


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------

def test_email_payload_shape(_outside_pytest) -> None:
    with patch.object(alerts.httpx, "post", return_value=_Resp(200)) as post:
        alerts.send_alert(
            "Grader failed",
            "Recipe pipeline stopped for Custard Tarts",
            "critical",
            fields=[("Episode", "2026-W36", True), ("Error", "boom", False)],
            url="https://muffinpanrecipes.com/admin/recipes/abc",
        )

    resend_call = next(c for c in post.call_args_list if c.args[0] == alerts._RESEND_URL)
    payload = resend_call.kwargs["json"]
    headers = resend_call.kwargs["headers"]

    assert payload["subject"] == "[muffinpanrecipes] critical: Grader failed"
    assert payload["from"] == "alerts@send.synthinsightlabs.com"
    assert payload["to"] == ["erik@example.com"]
    assert "html" not in payload
    assert "Recipe pipeline stopped for Custard Tarts" in payload["text"]
    assert "Episode: 2026-W36" in payload["text"]
    assert "Error: boom" in payload["text"]
    assert "https://muffinpanrecipes.com/admin/recipes/abc" in payload["text"]
    assert headers["Authorization"] == "Bearer re_test_key"


def test_alert_email_from_is_overridable(_outside_pytest, monkeypatch) -> None:
    monkeypatch.setenv("ALERT_EMAIL_FROM", "custom@muffinpanrecipes.com")
    with patch.object(alerts.httpx, "post", return_value=_Resp(200)) as post:
        alerts.send_alert("s", "b", "critical")

    resend_call = next(c for c in post.call_args_list if c.args[0] == alerts._RESEND_URL)
    assert resend_call.kwargs["json"]["from"] == "custom@muffinpanrecipes.com"


# ---------------------------------------------------------------------------
# Missing configuration — loud, not fatal
# ---------------------------------------------------------------------------

def test_missing_key_logs_notifies_discord_once_and_keeps_original_alert(
    _unconfigured,
) -> None:
    with patch.object(alerts.httpx, "post", return_value=_Resp(204)) as post:
        with patch.object(alerts.logger, "error") as log_error:
            # Two alerts in the same "process" (flag is process-wide).
            first = alerts.send_alert("first", "b", "critical")
            second = alerts.send_alert("second", "b", "critical")

    # The original Discord alert always goes out regardless of email config.
    assert first is True
    assert second is True

    # _send_discord fires twice for the real alerts, plus exactly one extra
    # call for the unconfigured notice — never one per alert.
    assert post.call_count == 3
    notice_calls = [
        c for c in post.call_args_list
        if "email channel unconfigured" in c.kwargs["json"]["embeds"][0]["description"]
    ]
    assert len(notice_calls) == 1

    assert any("RESEND_API_KEY" in call.args[0] for call in log_error.call_args_list)


def test_missing_key_does_not_raise(_unconfigured) -> None:
    with patch.object(alerts.httpx, "post", return_value=_Resp(204)):
        # Must not raise RuntimeError out of send_alert.
        alerts.send_alert("s", "b", "critical")


# ---------------------------------------------------------------------------
# Resend failure handling
# ---------------------------------------------------------------------------

def test_resend_4xx_is_logged_not_raised(_outside_pytest) -> None:
    def _fake_post(url, **kwargs):
        if url == alerts._RESEND_URL:
            return _Resp(422, "invalid `to` field")
        return _Resp(204)

    with patch.object(alerts.httpx, "post", side_effect=_fake_post):
        with patch.object(alerts.logger, "error") as log_error:
            alerts.send_alert("s", "b", "critical")

    assert any("Resend rejected" in call.args[0] for call in log_error.call_args_list)


def test_resend_5xx_is_logged_not_raised(_outside_pytest) -> None:
    def _fake_post(url, **kwargs):
        if url == alerts._RESEND_URL:
            return _Resp(500, "internal error")
        return _Resp(204)

    with patch.object(alerts.httpx, "post", side_effect=_fake_post):
        alerts.send_alert("s", "b", "critical")  # must not raise


def test_resend_transport_exception_is_swallowed(_outside_pytest) -> None:
    def _fake_post(url, **kwargs):
        if url == alerts._RESEND_URL:
            raise RuntimeError("dns")
        return _Resp(204)

    with patch.object(alerts.httpx, "post", side_effect=_fake_post):
        # Discord must still succeed even though email's transport blew up.
        assert alerts.send_alert("s", "b", "critical") is True


# ---------------------------------------------------------------------------
# Independence of the two backends
# ---------------------------------------------------------------------------

def test_dead_discord_does_not_suppress_email(_outside_pytest) -> None:
    def _fake_post(url, **kwargs):
        if url == alerts._RESEND_URL:
            return _Resp(200)
        raise RuntimeError("discord is down")

    with patch.object(alerts.httpx, "post", side_effect=_fake_post):
        assert alerts.send_alert("s", "b", "critical") is True


def test_dead_email_does_not_suppress_discord(_outside_pytest) -> None:
    def _fake_post(url, **kwargs):
        if url == alerts._RESEND_URL:
            raise RuntimeError("resend is down")
        return _Resp(204)

    with patch.object(alerts.httpx, "post", side_effect=_fake_post):
        assert alerts.send_alert("s", "b", "critical") is True


# ---------------------------------------------------------------------------
# email_channel_status() — pure, side-effect-free
# ---------------------------------------------------------------------------

def test_email_channel_status_reports_configured(_outside_pytest) -> None:
    assert alerts.email_channel_status() == {"configured": True, "missing": []}


def test_email_channel_status_lists_missing_names(_unconfigured) -> None:
    status = alerts.email_channel_status()
    assert status["configured"] is False
    assert set(status["missing"]) == {"RESEND_API_KEY", "ALERT_EMAIL_TO"}


def test_email_channel_status_has_no_side_effects(_unconfigured) -> None:
    """Calling it must never touch the network or flip the notice flag."""
    with patch.object(alerts.httpx, "post") as post:
        alerts.email_channel_status()
        alerts.email_channel_status()

    post.assert_not_called()
    assert alerts._email_unconfigured_notice_sent is False
