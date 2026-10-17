"""Pytest configuration and shared fixtures for testing."""

import os

# Tests always run in local dev context — set before any backend imports
# so that _Config singleton picks it up and JWT fallback key is allowed.
os.environ.setdefault("LOCAL_DEV", "true")

import pytest
from pathlib import Path
from hypothesis import HealthCheck, settings, Verbosity

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


# IndexNow's key (#7806) is a PUBLIC constant, not a credential — there is no
# env var to strip the way _no_live_alert_credentials does above, so that
# pattern can't protect this one. Every Sunday-publish test that reaches
# `_submit_sunday_indexnow` has to mock `cron_routes._indexnow_submit_urls`
# itself (missing that once already sent a real POST from a memory-events
# test that had nothing to do with IndexNow). This fixture is the backstop
# for the next one that forgets: it turns a silent real network call into a
# loud, specific test failure instead. A test that wants to exercise the
# actual HTTP call (tests/test_indexnow.py) patches
# `backend.utils.indexnow.requests.post` itself, which shadows this for the
# duration of that `with` block.
@pytest.fixture(autouse=True)
def _no_live_indexnow_submission(monkeypatch):
    def _blocked(*_args, **_kwargs):
        # pytest.fail raises a BaseException, so the Sunday hook's
        # `except Exception` cannot swallow it into a recorded failure and
        # let the test pass (Codex round 1).
        pytest.fail(
            "backend.utils.indexnow.requests.post was called without being "
            "mocked. IndexNow submissions must never touch the network in "
            "tests — patch backend.utils.indexnow.requests.post (to test the "
            "client itself) or cron_routes._indexnow_submit_urls (to test a "
            "caller)."
        )
    monkeypatch.setattr("backend.utils.indexnow.requests.post", _blocked)


# The filesystem backend's local data directories. The photo-approval control
# log (#7936) is read by Wednesday-Sunday on every run, and a cloud backend
# also writes a local cache copy of every episode it saves (#8169): without
# this, tests wrote fixture episodes into the repo's data/episodes/, where
# test_local_episode_corpus_is_clear_when_present then read them. Every test
# gets empty directories under tmp_path instead, served by the REAL filesystem
# backend code (exclusive lock + atomic rename), so tests exercise the actual
# compare-and-swap. Tests of the cloud backend build their own instance
# over a fake Blob API.
@pytest.fixture(autouse=True)
def _isolated_local_data(monkeypatch, tmp_path):
    import backend.storage as storage_module

    for name, sub in (
        ("PHOTO_CONTROL_DIR", "photo_control"),
        ("EPISODES_DIR", "episodes"),
        ("SIMULATIONS_DIR", "simulations"),
        ("CHARACTER_MEMORY_DIR", "character_memory"),
    ):
        monkeypatch.setattr(storage_module, name, tmp_path / sub)


# backend.utils.model_router._COST_LOG is a module-level global, and several
# tests (tests/test_lab_models.py's cost-by-model tests in particular) record
# synthetic entries into it and reset it only at their OWN start, not at
# their end. scripts/conversation_lab.py._lab_cost_total() sums every
# get_cost_entries() actual_cost and treats an entry without one as
# untrusted - fail CLOSED, not $0 - so a leaked entry from an unrelated earlier test can now abort a
# LATER test's `ab` run that never touches the cost log itself. Resetting
# the log before AND after every test removes that ordering dependency
# regardless of which test runs first.
@pytest.fixture(autouse=True)
def _reset_model_router_cost_log():
    from backend.utils import model_router

    model_router.reset_cost_log()
    yield
    model_router.reset_cost_log()


# Configure hypothesis for property-based testing
#
# too_slow is off because it measures the machine, not the test. The first
# property test in a process pays one-off generation costs (Hypothesis's unicode
# tables, the pydantic builders), and with several suites running at once that
# first test took 1.8 s for two inputs and failed FailedHealthCheck in 3 of 5
# parallel full-suite runs, with every assertion untouched (#8395). Deadlines and
# the other health checks stay on.
_HEALTH = [HealthCheck.too_slow]
settings.register_profile("default", max_examples=100, verbosity=Verbosity.normal, suppress_health_check=_HEALTH)
settings.register_profile("ci", max_examples=200, verbosity=Verbosity.verbose, suppress_health_check=_HEALTH)
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


@pytest.fixture
def openrouter_lab(monkeypatch):
    """Let conversation_lab's paid commands run offline (#8343).

    Every paid command routes through OpenRouter, so it needs a key and the
    key/balance/price preflight. Tests that are not about that preflight stub
    it: a fake key, no network, and zero prices for the default model set so
    the per-call cost reservation adds nothing.
    """
    import scripts.conversation_lab as cl

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    monkeypatch.setattr(cl, "_openrouter_preflight", lambda args: None)
    default = cl._DEFAULT_LAB_MODEL_SET
    monkeypatch.setattr(
        cl, "_OPENROUTER_MODEL_PRICES", {default.dialogue: (0.0, 0.0), default.judge: (0.0, 0.0)},
    )
