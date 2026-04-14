"""Tests for storage prefix isolation in cron handlers (#5917).

RUNBOOK Incident 1 (#5911) root cause: `_configure_test_mode` set the
storage prefix to `test/` for the duration of a cron handler, but there
was no structural guarantee the prefix was restored. Warm Lambda
invocations could leak `test/` into subsequent non-test calls.

These tests lock in the fix: `storage.prefix_scope()` is a context
manager that snapshots and restores `prefix`, and every cron handler
wraps its body in `_test_mode_scope(body)`.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_vercel_env(monkeypatch):
    """Ensure _CloudBackend init doesn't crash on missing env."""
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)


class TestPrefixScope:
    """Direct tests for storage.prefix_scope context manager."""

    def test_resets_prefix_after_normal_exit(self):
        from backend.storage import _FilesystemBackend

        backend = _FilesystemBackend()
        assert backend.prefix == ""

        with backend.prefix_scope("test/"):
            assert backend.prefix == "test/"

        assert backend.prefix == ""

    def test_resets_prefix_after_exception(self):
        from backend.storage import _FilesystemBackend

        backend = _FilesystemBackend()
        backend.prefix = ""

        with pytest.raises(RuntimeError, match="boom"):
            with backend.prefix_scope("test/"):
                assert backend.prefix == "test/"
                raise RuntimeError("boom")

        assert backend.prefix == ""

    def test_nested_scopes_restore_correctly(self):
        from backend.storage import _FilesystemBackend

        backend = _FilesystemBackend()
        backend.prefix = ""

        with backend.prefix_scope("outer/"):
            assert backend.prefix == "outer/"
            with backend.prefix_scope("inner/"):
                assert backend.prefix == "inner/"
            assert backend.prefix == "outer/"
        assert backend.prefix == ""

    def test_restores_non_empty_previous_prefix(self):
        """If someone calls prefix_scope while a prefix is already set,
        the previous (non-empty) value must be restored — not blanked."""
        from backend.storage import _FilesystemBackend

        backend = _FilesystemBackend()
        backend.prefix = "legacy/"

        with backend.prefix_scope("test/"):
            assert backend.prefix == "test/"

        assert backend.prefix == "legacy/"

    def test_cloud_backend_has_prefix_scope(self):
        from backend.storage import _CloudBackend

        with patch.dict(os.environ, {"BLOB_READ_WRITE_TOKEN": "fake"}):
            backend = _CloudBackend()

        assert backend.prefix == ""
        with backend.prefix_scope("test/"):
            assert backend.prefix == "test/"
        assert backend.prefix == ""


class TestTestModeScopeHelper:
    """Tests for the cron_routes._test_mode_scope wrapper."""

    def test_test_true_sets_test_prefix(self):
        from backend.admin.cron_routes import _test_mode_scope
        from backend.storage import storage

        body = MagicMock()
        body.test = True

        previous = storage.prefix
        with _test_mode_scope(body):
            assert storage.prefix == "test/"
        assert storage.prefix == previous

    def test_test_false_sets_empty_prefix(self):
        from backend.admin.cron_routes import _test_mode_scope
        from backend.storage import storage

        body = MagicMock()
        body.test = False

        previous = storage.prefix
        with _test_mode_scope(body):
            assert storage.prefix == ""
        assert storage.prefix == previous

    def test_prefix_restored_on_handler_exception(self):
        """The critical incident scenario: a handler that raises must not
        leak test-mode prefix into the next invocation on the same warm
        Lambda. Simulates the #5911 contamination path."""
        from backend.admin.cron_routes import _test_mode_scope
        from backend.storage import storage

        body = MagicMock()
        body.test = True

        # Ensure a clean starting point
        storage.set_prefix("")

        with pytest.raises(ValueError, match="simulated handler failure"):
            with _test_mode_scope(body):
                assert storage.prefix == "test/"
                raise ValueError("simulated handler failure")

        # Previously, prefix would remain "test/" and leak into the
        # next non-test call. With prefix_scope it is guaranteed to reset.
        assert storage.prefix == ""
