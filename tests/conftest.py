"""Pytest configuration and shared fixtures for testing."""

import os

# Tests always run in local dev context — set before any backend imports
# so that _Config singleton picks it up and JWT fallback key is allowed.
os.environ.setdefault("LOCAL_DEV", "true")

import pytest
from pathlib import Path
from hypothesis import settings, Verbosity

from backend.core.personality import PersonalityConfig, CommunicationStyle
from backend.core.task import Task


# Live alert credentials are stripped from every test, no exceptions (#7097).
#
# `alerts._pytest_gate()` normally stops all delivery under pytest, but tests
# that exercise delivery itself have to disable that gate, and several do. Those
# were safe only by accident: `_send_email` used to return early for any
# severity except "critical", so it never reached the network. Deleting that
# routing filter removed the accident, and
# `test_alerts.py::test_missing_webhook_is_logged_not_raised` — which drops the
# gate, mocks nothing, and sends at the default "warning" — would have POSTed to
# the real Resend API with Erik's real key the first time anyone ran the suite
# under `doppler run`, which is this project's documented way to run anything
# env-dependent.
#
# Ambient absence is not a safety mechanism. A test that wants the email channel
# configured sets these itself with monkeypatch.setenv; everything else cannot
# reach a live inbox no matter how it is invoked.
@pytest.fixture(autouse=True)
def _no_live_alert_credentials(monkeypatch):
    for name in ("RESEND_API_KEY", "ALERT_EMAIL_TO", "MUFFINPAN_DISCORD_WEBHOOK"):
        monkeypatch.delenv(name, raising=False)


# Configure hypothesis for property-based testing
settings.register_profile("default", max_examples=100, verbosity=Verbosity.normal)
settings.register_profile("ci", max_examples=200, verbosity=Verbosity.verbose)
settings.load_profile("default")


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def sample_personality_config() -> PersonalityConfig:
    """Create a sample personality configuration for testing."""
    return PersonalityConfig(
        name="Test Agent",
        age=30,
        role="test_role",
        core_traits={
            "grumpiness": 0.5,
            "perfectionism": 0.8,  # Above 0.7 to trigger extra validation
            "traditionalism": 0.4,
        },
        backstory="A test agent created for unit testing purposes.",
        communication_style=CommunicationStyle(
            formality=0.6,
            verbosity=0.5,
            directness=0.7,
            emotional_expressiveness=0.4,
            signature_phrases=["Indeed.", "As I've always said..."],
        ),
        quirks=["Always checks work twice", "Mutters under breath"],
        triggers=["shortcuts", "trendy ingredients"],
    )


@pytest.fixture
def sample_task() -> Task:
    """Create a sample task for testing."""
    return Task(
        type="test_task",
        content="This is a test task with some trendy ingredients.",
        context={"priority": "normal"},
        default_strategy="standard",
    )
