"""No test may reach the real API cost tracker (#8065)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from backend.utils import image_generation, model_router

REPO = Path(__file__).resolve().parent.parent

# The two tests that leaked a row per run before the conftest fixture existed.
_FORMER_LEAKERS = [
    "tests/test_openrouter_routing.py::test_vision_call_sends_images_to_openrouter_as_data_urls",
    "tests/test_image_generation.py::test_image_comes_back_from_openrouter_as_bytes",
]


def test_backend_entry_points_are_not_the_real_client():
    """Identity only: never calls them, so this test cannot leak even if the
    conftest fixture were missing."""
    client = pytest.importorskip("api_cost_tracker")  # in the dev extra
    assert model_router._central_track is not client.track
    assert image_generation.log_call is not client.log_call


def test_reaching_the_real_client_fails_loudly(tmp_path, monkeypatch):
    tracker = pytest.importorskip("api_cost_tracker.tracker")  # in the dev extra
    buffer = pytest.importorskip("api_cost_tracker.buffer")
    # If the conftest backstop were missing, the call below must still go
    # nowhere: an unreachable URL and a buffer under tmp_path.
    monkeypatch.setattr(tracker, "COST_TRACKER_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(buffer, "BUFFER_DB_PATH", tmp_path / "buffer.db")
    with pytest.raises(pytest.fail.Exception, match="#8065"):
        tracker.log_call("stability", service="image", project="muffinpanrecipes", caller="probe")
    assert not (tmp_path / "buffer.db").exists()


def test_former_leakers_write_nothing_to_a_fresh_home(tmp_path):
    """Runs the two tests that used to leak in a child pytest with an empty HOME
    and an unreachable tracker URL: they must pass and leave no buffer.db."""
    pytest.importorskip("api_cost_tracker")  # without the dev extra there is no client to leak to
    env = {k: v for k, v in os.environ.items() if k != "COST_TRACKER_API_KEY"}
    env.update(HOME=str(tmp_path), COST_TRACKER_URL="http://127.0.0.1:9")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *_FORMER_LEAKERS],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=180,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert not (tmp_path / ".local" / "share" / "api_trust_tracker" / "buffer.db").exists()


_SEED_PENDING_ROW = (
    "from api_cost_tracker.buffer import buffer_record; "
    "buffer_record('/track', {'project': 'muffinpanrecipes', 'caller': 'seed'})"
)
_COUNT_UNTOUCHED = (
    "import sqlite3; from api_cost_tracker.buffer import BUFFER_DB_PATH; "
    "print(sqlite3.connect(BUFFER_DB_PATH).execute("
    "'select count(*) from pending_records where attempts = 0').fetchone()[0])"
)


def test_faked_successful_post_leaves_the_machine_buffer_alone(tmp_path):
    """Since 0.2.1 a successful POST drains up to 5 buffered rows (#8167). A test
    that fakes that success must not send this machine's buffered rows. A child
    pytest gets an empty HOME holding one pending row and an unreachable tracker
    URL; afterwards the row must still be pending and never attempted."""
    pytest.importorskip("api_cost_tracker")  # without the dev extra there is no drain
    env = {k: v for k, v in os.environ.items() if k != "COST_TRACKER_API_KEY"}
    env.update(HOME=str(tmp_path), COST_TRACKER_URL="http://127.0.0.1:9")

    def child(*args):
        return subprocess.run(
            [sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=180
        )

    seeded = child("-c", _SEED_PENDING_ROW)
    assert seeded.returncode == 0, seeded.stderr[-2000:]
    result = child(
        "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "tests/test_model_router_tracking.py::test_openrouter_response_is_recorded_centrally_with_its_billed_cost",
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    untouched = child("-c", _COUNT_UNTOUCHED)
    assert untouched.stdout.strip() == "1", untouched.stdout + untouched.stderr[-2000:]
