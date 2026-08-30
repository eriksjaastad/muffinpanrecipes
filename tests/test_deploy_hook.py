"""Focused tests for the static Sunday deployment handoff."""

from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from backend.admin import cron_routes
from backend.publishing.deploy_hook import (
    DeployHookConfigurationError,
    DeployHookError,
    DeployHookResult,
    trigger_vercel_deploy_hook,
)

HOOK_URL = "https://vercel.example/hooks/static-secret"


def _successful_response():
    response = Mock()
    response.raise_for_status.return_value = None
    return response


def test_trigger_success_uses_configured_url_without_logging_url(monkeypatch, caplog):
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", HOOK_URL)
    post = Mock(return_value=_successful_response())

    with caplog.at_level(logging.INFO):
        result = trigger_vercel_deploy_hook(environment="production", post=post)

    assert result.triggered is True
    post.assert_called_once_with(HOOK_URL, timeout=10)
    assert all(HOOK_URL not in record.getMessage() for record in caplog.records)


def test_missing_hook_skips_outside_production(monkeypatch, caplog):
    monkeypatch.delenv("VERCEL_DEPLOY_HOOK_URL", raising=False)

    with caplog.at_level(logging.WARNING):
        result = trigger_vercel_deploy_hook(environment="preview", post=Mock())

    assert result == DeployHookResult(triggered=False, skipped=True)
    assert any("continuing outside production" in record.getMessage() for record in caplog.records)


def test_failed_hook_skips_outside_production(monkeypatch, caplog):
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", HOOK_URL)
    post = Mock(side_effect=RuntimeError("request failed"))

    with caplog.at_level(logging.WARNING):
        result = trigger_vercel_deploy_hook(environment="preview", post=post)

    assert result == DeployHookResult(triggered=False, skipped=False)
    assert any(
        "request failed outside production" in record.getMessage()
        for record in caplog.records
    )
    assert all(HOOK_URL not in record.getMessage() for record in caplog.records)


def test_missing_hook_raises_in_production(monkeypatch):
    monkeypatch.delenv("VERCEL_DEPLOY_HOOK_URL", raising=False)

    with pytest.raises(
        DeployHookConfigurationError,
        match="Production publish blocked",
    ) as exc_info:
        trigger_vercel_deploy_hook(environment="production", post=Mock())

    assert HOOK_URL not in str(exc_info.value)


def test_failed_hook_raises_in_production(monkeypatch):
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", HOOK_URL)
    post = Mock(side_effect=RuntimeError("request failed"))

    with pytest.raises(DeployHookError, match="deploy-hook request failed") as exc_info:
        trigger_vercel_deploy_hook(environment="production", post=post)

    assert HOOK_URL not in str(exc_info.value)


def test_test_mode_skips_even_when_hook_is_configured(monkeypatch):
    monkeypatch.setenv("VERCEL_DEPLOY_HOOK_URL", HOOK_URL)
    post = Mock()

    result = trigger_vercel_deploy_hook(
        test_mode=True,
        environment="production",
        post=post,
    )

    assert result == DeployHookResult(triggered=False, skipped=True)
    post.assert_not_called()


def test_sunday_handoff_writes_source_state_before_triggering_hook(monkeypatch):
    episode = {
        "episode_id": "2026-W20",
        "static_deploy": {"status": "pending"},
    }
    events = []

    def publish_sources(data):
        assert data is episode
        events.append("sources")

    def save_episode(episode_id, data):
        assert episode_id == "2026-W20"
        events.append(f"save:{data['static_deploy']['status']}")

    def trigger_hook(*, test_mode):
        assert test_mode is False
        events.append("hook")
        return DeployHookResult(triggered=True)

    monkeypatch.setattr(cron_routes, "_publish_sunday_sources", publish_sources)
    monkeypatch.setattr(cron_routes.storage, "save_episode", save_episode)
    monkeypatch.setattr(cron_routes, "trigger_vercel_deploy_hook", trigger_hook)

    result = cron_routes._complete_static_deploy_handoff(
        "2026-W20",
        episode,
        test_mode=False,
    )

    assert result == DeployHookResult(triggered=True)
    assert events == ["sources", "save:source_ready", "hook", "save:triggered"]
    assert episode["static_deploy"]["status"] == "triggered"
