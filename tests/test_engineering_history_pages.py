"""Draft engineering-history pages: /about rewrite, /engineering index, first post.

Focused on what these pages must get right before anyone reads them: metadata,
attribution to the human author, cross-links, no publication into the sitemap
or site-wide navigation, and no private paths or secrets in the copy.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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


def test_engineering_pages_not_in_sitemap_or_site_nav() -> None:
    """Draft branch: nothing public points at these pages yet. The homepage
    and sitemap stay untouched, and the about page's site nav still only
    offers Home."""
    assert "/engineering" not in _read(SRC / "sitemap.xml")
    assert "/engineering" not in _read(SRC / "index.html")
    about = _read(ABOUT)
    nav = about[about.index('<nav class="site-nav">') : about.index("</nav>")]
    assert "/engineering" not in nav


def test_engineering_pages_have_no_private_paths_or_secrets() -> None:
    """Scan visible copy and markup for things that must never be published:
    local filesystem paths, machine names, tracker numbers, costs, and key
    material."""
    forbidden = re.compile(
        r"/Users/|/home/|~/|\bMac ?Mini\b|MacBook|doppler|secret|api[_ -]?key|token"
        r"|BLOB_READ_WRITE|sk-[A-Za-z0-9]|\bPR #|\bcard #|#\d{3,}|\$\d",
        re.IGNORECASE,
    )
    for key, path in PAGES.items():
        match = forbidden.search(_read(path))
        assert match is None, f"{key} contains {match.group(0)!r}"
