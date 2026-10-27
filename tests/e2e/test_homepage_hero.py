"""Browser regression tests for the stale homepage hero flash."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from backend.publishing.static_renderer import render_home


def _recipe(title: str, slug: str) -> dict:
    return {
        "slug": slug,
        "title": title,
        "category": "Savory",
        "description": f"Description for {title}.",
        "image": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
        "prep": "10 mins",
        "cook": "20 mins",
        "yield": "12 servings",
    }


@pytest.fixture
def homepage_server():
    stale = _recipe("Stale Black Sesame Cups", "stale-black-sesame-cups")
    live = _recipe("Live Golden Gruyere Cups", "live-golden-gruyere-cups")
    settings = {"delay": 0.0, "fail": False}
    page_html = render_home([stale]).encode()
    css = (Path(__file__).resolve().parents[2] / "src" / "assets" / "site.css").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                self._reply(200, "text/html; charset=utf-8", page_html)
                return
            if self.path == "/assets/site.css":
                self._reply(200, "text/css; charset=utf-8", css)
                return
            if self.path == "/recipes.json":
                time.sleep(settings["delay"])
                if settings["fail"]:
                    self._reply(503, "application/json", b'{"error":"catalog unavailable"}')
                else:
                    self._reply(200, "application/json", json.dumps({"recipes": [live]}).encode())
                return
            self._reply(404, "text/plain", b"not found")

        def _reply(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield settings, f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_delayed_live_catalog_never_paints_stale_hero(page: Page, homepage_server) -> None:
    settings, url = homepage_server
    settings["delay"] = 0.75

    page.goto(url, wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name="Stale Black Sesame Cups")).to_be_hidden()
    expect(page.get_by_role("heading", name="Live Golden Gruyere Cups")).to_be_visible(timeout=3000)


def test_failed_live_catalog_eventually_reveals_crawlable_fallback(page: Page, homepage_server) -> None:
    settings, url = homepage_server
    settings["fail"] = True

    page.goto(url, wait_until="domcontentloaded")
    fallback = page.get_by_role("heading", name="Stale Black Sesame Cups")
    expect(fallback).to_be_hidden()
    expect(fallback).to_be_visible(timeout=9000)


def test_stalled_catalog_is_aborted_and_reveals_fallback(page: Page, homepage_server) -> None:
    settings, url = homepage_server
    settings["delay"] = 10.0

    page.goto(url, wait_until="domcontentloaded")
    fallback = page.get_by_role("heading", name="Stale Black Sesame Cups")
    expect(fallback).to_be_hidden()
    expect(fallback).to_be_visible(timeout=6500)
    expect(page.get_by_role("heading", name="Live Golden Gruyere Cups")).to_have_count(0)


def test_no_javascript_browser_sees_crawlable_fallback(browser: Browser, homepage_server) -> None:
    _settings, url = homepage_server
    context = browser.new_context(java_script_enabled=False)
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Stale Black Sesame Cups")).to_be_visible()
    finally:
        context.close()
