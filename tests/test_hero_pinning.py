"""A published week's hero is pinned data, not a render-time pick (Erik, 2026-08-22).

The first live full rebuild (2026-09-05) swapped the hero on 20 of 25
published pages: the renderer's "prefer the art director's confirmed winner"
rule post-dates those pages, and several winners point at a top-level copy
that was never uploaded. ``hero_image_url`` on the episode now wins over any
picking logic; Sunday writes it at publish and scripts/pin_published_heroes.py
backfills it from the live pages.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.publishing import episode_renderer
from scripts import pin_published_heroes as pin

CDN = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/"


def _episode(**extra) -> dict:
    return {
        "episode_id": "2026-W28",
        "image_urls": [CDN + "2068c0cc/round_1/macro_closeup.png"],
        "stages": {
            "monday": {"recipe_data": {"title": "Spanakopita Phyllo Cups"}},
            "wednesday": {"confirmed_winner": {"featured_image": "src/assets/images/2068c0cc.png"}},
        },
        **extra,
    }


def test_pinned_hero_wins_over_the_confirmed_winner() -> None:
    ep = _episode(hero_image_url=CDN + "2068c0cc/round_1/macro_closeup.png")
    with patch.object(episode_renderer.storage, "get_image_url") as picker:
        assert episode_renderer._hero_image_url(ep) == CDN + "2068c0cc/round_1/macro_closeup.png"
    picker.assert_not_called()


def test_unpinned_episode_still_prefers_the_confirmed_winner() -> None:
    ep = _episode()
    with patch.object(episode_renderer.storage, "get_image_url", return_value=CDN + "2068c0cc.png"):
        assert episode_renderer._hero_image_url(ep) == CDN + "2068c0cc.png"


def test_blank_pin_is_ignored() -> None:
    ep = _episode(hero_image_url="   ")
    with patch.object(episode_renderer.storage, "get_image_url", return_value=CDN + "2068c0cc.png"):
        assert episode_renderer._hero_image_url(ep) == CDN + "2068c0cc.png"


def test_pin_plan_reads_the_live_page_and_matches_unstamped_rows_by_title() -> None:
    catalog = {"recipes": [
        {"slug": "spanakopita-phyllo-cups", "image": "/blob-images/2068c0cc/round_1/macro_closeup.webp"},  # no episode_id
        {"slug": "classic-blueberry-muffins", "image": "assets/images/classic-blueberry-muffins.webp"},  # seed
        {"slug": "kimchi-cheddar-rice-cups", "episode_id": "2026-W33", "image": "/blob-images/2a2615c6/round_1/macro_closeup.webp"},
    ]}
    episodes = {
        "2026-W28": _episode(),
        "2026-W33": {"episode_id": "2026-W33", "hero_image_url": CDN + "2a2615c6.png",
                     "stages": {"monday": {"recipe_data": {"title": "Kimchi Cheddar Rice Cups"}}}},
    }
    pages = {
        "spanakopita-phyllo-cups": '<div class="recipe-hero__image"><picture><img src="/blob-images/2068c0cc/round_1/macro_closeup.png" width="1"></picture></div>',
        "kimchi-cheddar-rice-cups": '<div class="recipe-hero__image"><img src="/blob-images/2a2615c6.png"></div>',
    }
    rows = pin.plan(catalog, episodes, lambda slug: pages[slug])
    assert rows == [
        ("2026-W28", "spanakopita-phyllo-cups", CDN + "2068c0cc/round_1/macro_closeup.png", None),
        ("2026-W33", "kimchi-cheddar-rice-cups", CDN + "2a2615c6.png", CDN + "2a2615c6.png"),
    ]


def test_pin_plan_stores_the_source_png_when_the_live_page_serves_the_jpeg_fallback() -> None:
    """#7185 review round 2, HIGH, the exact scenario the finding describes:
    a page rendered AFTER the JPEG fallback (#7185) was backfilled serves
    '-1200w.jpg' as its <img src>. The planned pin must still be the
    canonical PNG — never the JPEG — or the next render would treat a lossy
    1200px sibling as the hero's source of truth."""
    catalog = {"recipes": [
        {"slug": "spanakopita-phyllo-cups", "image": "/blob-images/2068c0cc/round_1/macro_closeup.webp"},
    ]}
    episodes = {"2026-W28": _episode()}
    pages = {
        "spanakopita-phyllo-cups": (
            '<div class="recipe-hero__image"><picture>'
            '<source srcset="/blob-images/2068c0cc/round_1/macro_closeup.webp" type="image/webp">'
            '<img src="/blob-images/2068c0cc/round_1/macro_closeup-1200w.jpg" width="1200">'
            '</picture></div>'
        ),
    }
    rows = pin.plan(catalog, episodes, lambda slug: pages[slug])
    assert rows == [
        ("2026-W28", "spanakopita-phyllo-cups", CDN + "2068c0cc/round_1/macro_closeup.png", None),
    ]


def test_hero_src_extraction_handles_old_webp_and_suffixed_names() -> None:
    page = '<div class="recipe-hero__image"><img src="/blob-images/fe1a35bf/round_1/macro_closeup-Id7qzqIvqMV4id0oV26sRWxUpMf1T8.png"></div>'
    assert pin.hero_url_for_episode(pin.hero_src_from_page(page)) == CDN + "fe1a35bf/round_1/macro_closeup-Id7qzqIvqMV4id0oV26sRWxUpMf1T8.png"
    assert pin.hero_src_from_page("<html>no hero</html>") is None


def test_hero_src_bare_webp_inverts_to_png_when_it_actually_exists() -> None:
    """#7185 review round 3, MEDIUM: a bare full-size '.webp' is ambiguous
    (storage._source_png_key deliberately leaves it un-inverted), so
    pin_published_heroes resolves it itself with a real existence check —
    when Blob confirms the candidate PNG is there, use it."""
    response = MagicMock()
    response.status_code = 200
    with patch("scripts.pin_published_heroes.requests.head", return_value=response) as mock_head:
        assert pin.hero_url_for_episode("/blob-images/8a79d045.webp") == CDN + "8a79d045.png"
    assert mock_head.call_args_list[0].args[0] == (
        "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/8a79d045.png"
    )


def test_hero_src_bare_webp_stays_webp_when_no_png_exists() -> None:
    """The other branch of the same fix: an original, hand-authored webp
    hero with no PNG behind it must NOT be rewritten to a PNG that was
    never uploaded — Blob's 404 for the candidate keeps the webp URL."""
    response = MagicMock()
    response.status_code = 404
    with patch("scripts.pin_published_heroes.requests.head", return_value=response):
        assert pin.hero_url_for_episode("/blob-images/8a79d045.webp") == CDN + "8a79d045.webp"


def test_hero_src_bare_webp_stays_webp_on_network_error() -> None:
    with patch("scripts.pin_published_heroes.requests.head", side_effect=Exception("timeout")):
        assert pin.hero_url_for_episode("/blob-images/8a79d045.webp") == CDN + "8a79d045.webp"


def test_hero_src_jpeg_fallback_inverts_to_the_source_png() -> None:
    """#7185 review round 2, HIGH, the core regression: once the JPEG
    fallback ships, a live page's <img src> can be the sized '-1200w.jpg'
    instead of the raw PNG. Pinning that verbatim would permanently swap the
    hero's identity to a lossy JPEG and silently drop the WebP <source> and
    every variant on every future render."""
    page = '<div class="recipe-hero__image"><img src="/blob-images/foo/hero-1200w.jpg"></div>'
    assert pin.hero_url_for_episode(pin.hero_src_from_page(page)) == CDN + "foo/hero.png"


def test_static_builder_uses_the_pinned_hero_not_its_own_pick() -> None:
    from backend.publishing.site_builder import StaticSiteBuilder

    class _Storage:
        def get_image_url(self, path):  # the "confirmed winner" resolver
            return CDN + "2068c0cc.png"

    builder = StaticSiteBuilder.__new__(StaticSiteBuilder)
    builder.storage = _Storage()
    pinned = _episode(hero_image_url=CDN + "2068c0cc/round_1/macro_closeup.png")
    unpinned = _episode()
    assert builder._episode_image_url(pinned) == "/blob-images/2068c0cc/round_1/macro_closeup.png"
    assert builder._episode_image_url(unpinned) == "/blob-images/2068c0cc.png"
