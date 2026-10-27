"""Tests for the browser gate itself."""

from pathlib import Path

from tests.e2e.conftest import _server_environment


def test_server_environment_does_not_inherit_provider_credentials(
    monkeypatch, tmp_path: Path
) -> None:
    provider_names = {
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "VERCEL_API_TOKEN",
        "VERCEL_BLOB_READ_WRITE_TOKEN",
        "RUNPOD_API_KEY",
        "MUFFINPAN_DISCORD_WEBHOOK",
    }
    for name in provider_names:
        monkeypatch.setenv(name, "must-not-cross-test-boundary")

    child = _server_environment(tmp_path)

    assert provider_names.isdisjoint(child)
    assert child["STORAGE_BACKEND"] == "filesystem"
    assert child["MUFFIN_E2E_PROJECT_ROOT"] == str(tmp_path)
