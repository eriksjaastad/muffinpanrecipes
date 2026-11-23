"""Draft engineering-history pages: /about rewrite, /engineering index, first post.

Focused on what these pages must get right before anyone reads them: metadata,
attribution to the human author, cross-links, no publication into the sitemap
or site-wide navigation, and no private paths or secrets in the copy.
"""

from __future__ import annotations

import functools
import json
import re
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.sync_api import Page, Route, expect

from backend.publishing.analytics import GA4_MEASUREMENT_ID

SRC = Path(__file__).resolve().parents[1] / "src"
ABOUT = SRC / "about.html"
INDEX = SRC / "engineering" / "index.html"
POST = SRC / "engineering" / "from-six-voices-to-one-author" / "index.html"
PAGES = {"index": INDEX, "post": POST}
POST_URL = "https://muffinpanrecipes.com/engineering/from-six-voices-to-one-author"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json_ld(page: str) -> dict:
    start = page.index('application/ld+json">') + len('application/ld+json">')
    return json.loads(page[start : page.index("</script>", start)])


def test_engineering_pages_have_canonical_og_and_ga4() -> None:
    expected_canonical = {
        "index": "https://muffinpanrecipes.com/engineering",
        "post": POST_URL,
    }
    for key, path in PAGES.items():
        page = _read(path)
        assert f'<link rel="canonical" href="{expected_canonical[key]}">' in page, key
        assert f'<meta property="og:url" content="{expected_canonical[key]}">' in page, key
        assert 'name="twitter:card" content="summary_large_image"' in page, key
        assert f"googletagmanager.com/gtag/js?id={GA4_MEASUREMENT_ID}" in page, key
        assert page.count(f"gtag('config', '{GA4_MEASUREMENT_ID}')") == 1, key


def test_engineering_pages_credit_erik_sjaastad() -> None:
    """Attribution: the human author is named in structured data, and the post
    carries a visible byline. Nothing is credited to a character or a model."""
    for key, path in PAGES.items():
        assert _json_ld(_read(path))["author"] == {"@type": "Person", "name": "Erik Sjaastad"}, key
    assert "By Erik Sjaastad." in _read(POST)
    assert "Erik Sjaastad" in _read(ABOUT)


def test_engineering_pages_cross_link_each_other_and_about() -> None:
    about = _read(ABOUT)
    index = _read(INDEX)
    post = _read(POST)
    assert 'href="/engineering' not in about
    assert 'href="/engineering/from-six-voices-to-one-author"' in index
    assert 'href="/about"' in index
    assert 'href="/engineering"' in post
    assert 'href="/about"' in post


def test_engineering_links_have_persistent_underline() -> None:
    """Links must be distinguishable without hover: the global `a` rule removes
    underlines, so each engineering link selector needs its own underline rule."""
    css = _read(SRC / "assets" / "site.css")
    rules = [
        (set(sel.strip() for sel in selectors.split(",")), body)
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    ]
    required = [
        ".engineering-index .engineering-list__title a",
        ".engineering-index .about-copy a",
        ".engineering-post .about-copy a",
    ]
    for selector in required:
        bodies = [body for sels, body in rules if selector in sels]
        assert any(re.search(r"text-decoration(-line)?:\s*underline", b) for b in bodies), selector
    assert 'class="site-main engineering-index"' in _read(INDEX)


REPO = SRC.parent
SOURCE_FILES = [
    "docs/PRD.md",
    "docs/CREATIVE_BIBLE.md",
    "backend/data/agent_personalities.json",
    "docs/conversation-lab/DIALS.md",
    "docs/conversation-lab/RESEARCH_PLAN.md",
    "DECISIONS.md",
    "RUNBOOK.md",
    "scripts/simulate_dialogue_week.py",
]
# Chronological: repository start (Jan 3), launch (Jan 4), character build
# (Jan 22), bakery sitcom (Feb 25); then the later dialogue commits.
ORIGIN_COMMITS = ["1b1d080", "10beea9", "0a5d74a", "dd97a4d"]
SOURCE_COMMITS = ORIGIN_COMMITS + ["5eb63f6", "bf44248", "c601773"]
REQUIRED_SOURCES = SOURCE_FILES[:3] + SOURCE_COMMITS[:5]


def _sections(path: Path) -> list[str]:
    """Main-content chunks split at each history heading; [0] is the intro."""
    page = _read(path)
    main = page[page.index("<main") : page.index("</main>")]
    heading = '<h2 class="about-heading">' if path == ABOUT else "<h2>"
    return main.split(heading)


def _source_notes(chunk: str) -> list[str]:
    return re.findall(r'class="about-copy source-note"><small>(.*?)</small>', chunk)


def test_history_pages_put_the_repository_origin_before_the_sitcom() -> None:
    """The repository began on Jan 3 as a recipe engine and editorial site
    (1b1d080) and launched Jan 4 (10beea9); the characters (0a5d74a) and the
    explicit bakery sitcom (dd97a4d) came later. The intro still describes the
    sitcom, as the origin of the current identity, but no page may claim the
    show came first."""
    for path in (ABOUT, POST):
        sections = _sections(path)
        assert "workplace sitcom" in sections[0] and "bakery" in sections[0], path.name
        first_notes = " ".join(_source_notes(sections[1]))
        assert "1b1d080" in first_notes and "10beea9" in first_notes, path.name
        history = "".join(sections[1:])
        positions = [history.index(commit) for commit in ORIGIN_COMMITS]
        assert positions == sorted(positions), (path.name, positions)
        assert "current identity" in history, path.name
    for path in (ABOUT, INDEX, POST):
        page = _read(path)
        for claim in ("The show came first", "started as a show", "six specialized roles", "six named roles"):
            assert claim not in page, (path.name, claim)


def test_history_sections_name_their_repository_sources() -> None:
    """Every history section ends in a visible source note naming a real repo
    file or commit, and the required origin sources are all cited."""
    for path in SOURCE_FILES:
        assert (REPO / path).is_file(), path
    known = SOURCE_FILES + SOURCE_COMMITS
    about = _sections(ABOUT)  # intro + January + February + What changed
    post = _sections(POST)[1:-1]  # every post section except "What is next"
    cited = ""
    for chunk in about + post:
        notes = _source_notes(chunk)
        assert notes, chunk[:80]
        assert all(any(src in note for src in known) for note in notes), notes
        cited += " ".join(notes)
    for src in REQUIRED_SOURCES:
        assert src in cited, src


def test_one_author_result_keeps_its_caveats() -> None:
    """bf44248 is one same-week judge result, and the Monday human comparison
    was a 7-7 split; neither page may present them as more than that."""
    for path in (ABOUT, POST):
        page = _read(path)
        assert "1 of 6 days" in page and "6 of 6" in page, path.name
        assert "not proof" in page or "not universal proof" in page, path.name
        assert re.search(r"7 to 7|7 for the author and 7", page), path.name
        assert "7 of 7" not in page and "six of six" not in page, path.name


def test_per_line_path_is_described_as_separate_model_calls() -> None:
    """Before one author, each speaking character's line was its own model
    call (DIALS.md 2(c); the per-line path bf44248 switched off)."""
    for path in (ABOUT, POST):
        assert "separate model call" in _read(path), path.name


def test_engineering_pages_not_in_sitemap_or_site_nav() -> None:
    """Draft branch: nothing public points at these pages yet. The homepage
    and sitemap stay untouched, and the about page's site nav still only
    offers Home."""
    assert "/engineering" not in _read(SRC / "sitemap.xml")
    assert "/engineering" not in _read(SRC / "index.html")
    about = _read(ABOUT)
    nav = about[about.index('<nav class="site-nav">') : about.index("</nav>")]
    assert "/engineering" not in nav


_FORBIDDEN = re.compile(
    r"/Users/|/home/|~/|\bMac ?Mini\b|MacBook|doppler|secret|api[_ -]?key|token"
    r"|BLOB_READ_WRITE|sk-[A-Za-z0-9]|\bPR #|\bcard #|#\d{3,}|\$\d",
    re.IGNORECASE,
)


def _forbidden_match(text: str) -> str | None:
    """Return the first private path, machine name, tracker number, cost or
    key-material string in text, or None."""
    match = _FORBIDDEN.search(text)
    return match.group(0) if match else None


def test_history_pages_have_no_private_paths_or_secrets() -> None:
    """Scan visible copy and markup of all three history pages."""
    assert _forbidden_match('<p class="about-copy">See /Users/someone/projects/site.</p>') == "/Users/"
    for path in (ABOUT, INDEX, POST):
        found = _forbidden_match(_read(path))
        assert found is None, f"{path.name} contains {found!r}"


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
