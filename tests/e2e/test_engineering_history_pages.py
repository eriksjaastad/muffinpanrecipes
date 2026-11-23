"""Browser check for the draft engineering-history pages (#9131).

Static provenance and privacy checks live in tests/test_engineering_history_pages.py.
"""

from __future__ import annotations

import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture
def site_url():
    """Serve src/ on localhost, as the static host serves it."""

    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, _format, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(SRC)))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_history_pages_render_headings_sources_and_stay_out_of_navigation(
    page: Page, site_url: str
) -> None:
    """In a real browser: each page's h1 and history headings render in
    chronological order, source notes are visible, and the public About page
    links nowhere into the draft engineering pages. Off-site requests (the
    analytics tag) are aborted so the test never touches the network."""

    def local_only(route: Route) -> None:
        if route.request.url.startswith(site_url):
            route.continue_()
        else:
            route.abort()

    page.route("**/*", local_only)
    expected = {
        "/about.html": ("About Muffin Pan Recipes", "h2.about-heading", [
            "January 2026: a recipe site first",
            "Late January: one agent per character",
            "February 2026: the bakery sitcom",
            "What changed",
        ]),
        "/engineering/from-six-voices-to-one-author/index.html": ("From six voices to one author", "main h2", [
            "January 3: a recipe site first",
            "January: the writers' room and one agent per character",
            "February: the bakery sitcom and the name-strip test",
        ]),
        "/engineering/index.html": ("Engineering Notes", "main h2", ["From six voices to one author"]),
    }
    for path, (title, heading_selector, headings) in expected.items():
        page.goto(site_url + path, wait_until="domcontentloaded")
        expect(page.locator("h1")).to_have_text(title)
        assert page.locator(heading_selector).all_inner_texts()[: len(headings)] == headings, path
        if path != "/engineering/index.html":
            origin_note = page.locator(".source-note", has_text="1b1d080")
            expect(origin_note).to_be_visible()
            expect(origin_note).to_contain_text("10beea9")
        else:
            expect(page.locator(".engineering-list__status").first).to_have_text("Draft post")

    page.goto(site_url + "/about.html", wait_until="domcontentloaded")
    expect(page.locator('a[href^="/engineering"]')).to_have_count(0)
