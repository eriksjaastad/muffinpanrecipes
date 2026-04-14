"""Discord notifications must not fire during pytest (#5923).

Prior to the gate, test_pipeline_fail_fast.py legitimately exercised
the failure path, but orchestrator.produce_recipe called
notify_pipeline_failure in an except block before re-raising. pytest.raises
caught the exception but Discord had already been pinged. #5911 session
surfaced 3 Discord alerts during a single pytest run.

These tests lock in the `_pytest_gate()` early-return in every notify_*
helper by mocking httpx.post and asserting it's never called.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _webhook_set(monkeypatch):
    """Make the gate the only reason a notification could be skipped."""
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://discord.example/webhook")


class TestPytestGate:
    def test_notify_recipe_ready_does_not_post(self):
        from backend.utils import discord

        with patch.object(discord.httpx, "post") as mock_post:
            result = discord.notify_recipe_ready(
                recipe_title="Gated Muffins",
                recipe_id="test-rid",
                description_preview="irrelevant",
                ingredient_count=5,
            )

        assert result is False
        mock_post.assert_not_called()

    def test_notify_pipeline_failure_does_not_post(self):
        from backend.utils import discord

        with patch.object(discord.httpx, "post") as mock_post:
            result = discord.notify_pipeline_failure(
                recipe_id="test-rid",
                concept="Fail Fast Test Muffins",
                stage="baker",
                error_message="boom",
            )

        assert result is False
        mock_post.assert_not_called()

    def test_notify_judge_failure_does_not_post(self):
        from backend.utils import discord

        with patch.object(discord.httpx, "post") as mock_post:
            result = discord.notify_judge_failure(
                concept="Gated",
                stage="monday",
                verdict="needs work",
                episode_id="ep-test",
                attempts=3,
            )

        assert result is False
        mock_post.assert_not_called()

    def test_notify_batch_complete_does_not_post(self):
        from backend.utils import discord

        with patch.object(discord.httpx, "post") as mock_post:
            result = discord.notify_batch_complete(
                recipe_count=2,
                recipe_titles=["A", "B"],
            )

        assert result is False
        mock_post.assert_not_called()

    def test_gate_is_pytest_current_test(self, monkeypatch):
        """The gate must key off the pytest env var. If it's unset, the
        notify_* functions should NOT be silently skipped by the gate
        (they'd still skip for lack of webhook in a real run, but the
        gate itself must not be the cause)."""
        from backend.utils import discord

        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        assert discord._pytest_gate() is False

        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_something")
        assert discord._pytest_gate() is True
