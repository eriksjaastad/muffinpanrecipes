"""Hermetic Playwright fixtures for the local browser gate.

Run with ``scripts/test_browser.sh``. The server receives only synthetic
authentication values and an isolated temporary data root; provider API
credentials from the caller are deliberately not inherited.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Generator

import pytest

_HOST = "127.0.0.1"
_EPISODE_ID = "2026-W01"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_HOST, 0))
        return int(sock.getsockname()[1])


def _wait_for_server(host: str, port: int, proc: subprocess.Popen[bytes], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            details = (stderr or stdout).decode(errors="replace")
            raise RuntimeError(f"E2E server exited before startup:\n{details}")
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"E2E server did not start on {host}:{port} within {timeout}s")


def _seed_isolated_project(root: Path) -> None:
    episodes = root / "data" / "episodes"
    images = root / "src" / "assets" / "images"
    recipes = root / "data" / "recipes"
    episodes.mkdir(parents=True)
    images.mkdir(parents=True)
    for status in ("pending", "approved", "rejected", "published"):
        (recipes / status).mkdir(parents=True)

    (images / "e2e-browser-fixture.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
        '<rect width="16" height="16" fill="#d97706"/></svg>',
        encoding="utf-8",
    )
    episode = {
        "episode_id": _EPISODE_ID,
        "concept": "Deterministic Browser Fixture",
        "created_at": "2026-01-01T00:00:00+00:00",
        "events": [],
        "stages": {
            "monday": {
                "status": "complete",
                "completed_at": "2026-01-01T01:00:00+00:00",
                "recipe_data": {"title": "E2E Browser Cups", "ingredients": []},
            },
            "wednesday": {
                "status": "complete",
                "completed_at": "2026-01-03T01:00:00+00:00",
                "image_paths": ["src/assets/images/e2e-browser-fixture.svg"],
            },
        },
    }
    (episodes / f"{_EPISODE_ID}.json").write_text(
        json.dumps(episode), encoding="utf-8"
    )


def _server_environment(project_root: Path) -> dict[str, str]:
    """Return a minimal environment with no inherited provider credentials."""
    inherited = ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR", "VIRTUAL_ENV")
    env = {name: os.environ[name] for name in inherited if name in os.environ}
    env.update(
        {
            "LOCAL_DEV": "true",
            "STORAGE_BACKEND": "filesystem",
            "MUFFIN_E2E_PROJECT_ROOT": str(project_root),
            "GOOGLE_CLIENT_ID": "e2e-local-client",
            "GOOGLE_CLIENT_SECRET": "e2e-local-secret",
            "GOOGLE_AUTHORIZED_EMAILS": "e2e@localhost",
            "JWT_SECRET": "e2e-local-jwt-secret-not-for-production",
            "CRON_SECRET": "e2e-local-cron-secret",
        }
    )
    return env


@pytest.fixture(scope="session")
def live_server_url(tmp_path_factory: pytest.TempPathFactory) -> Generator[str, None, None]:
    """Start an isolated admin app and yield its localhost URL."""
    project_root = tmp_path_factory.mktemp("muffin-browser-project")
    _seed_isolated_project(project_root)
    port = _find_free_port()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.e2e.server_app:app",
            "--host",
            _HOST,
            "--port",
            str(port),
            "--no-access-log",
            "--log-level",
            "warning",
        ],
        env=_server_environment(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_server(_HOST, port, proc)
        yield f"http://{_HOST}:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
