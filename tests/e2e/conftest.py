"""
Pytest conftest for Playwright e2e smoke tests.

How to run:
    playwright install chromium
    LOCAL_DEV=true pytest tests/e2e/ -v

The fixture starts the admin app with uvicorn, waits for it to be
accepting connections, yields the base URL, then tears it down.
"""

import os
import socket
import subprocess
import sys
import time
from typing import Generator

import pytest
from playwright.sync_api import Page, sync_playwright

# Force LOCAL_DEV so OAuth is bypassed and auto-login is active.
os.environ.setdefault("LOCAL_DEV", "true")

_HOST = "127.0.0.1"
_DEFAULT_PORT = 18999  # unlikely collision with real dev server


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_server(host: str, port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Server did not start on {host}:{port} within {timeout}s")


@pytest.fixture(scope="session")
def live_server_url() -> Generator[str, None, None]:
    """Start the admin app in a subprocess and yield its base URL."""
    port = _find_free_port()
    env = {**os.environ, "LOCAL_DEV": "true"}

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.admin.app:app",
            "--host",
            _HOST,
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        _wait_for_server(_HOST, port, timeout=30)
        yield f"http://{_HOST}:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def browser_page(live_server_url: str) -> Generator[Page, None, None]:
    """Playwright browser page shared across all e2e tests in the session."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        # Collect console errors
        page.on("console", lambda msg: None)  # suppress output; collected per-test
        yield page
        ctx.close()
        browser.close()
