"""SEO surface: dynamic sitemap + recipe-page meta/JSON-LD completeness.

Audit (2026-06-12): the static sitemap froze at the 10 seed recipes —
every cron-published recipe was invisible to crawlers — and cron-rendered
pages lacked og:image (required for social cards), canonical (the same
HTML serves /this-week and /recipes/{slug}), twitter:card, and the
JSON-LD image/author/datePublished/totalTime fields Google wants for
Recipe rich results.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.admin import episode_routes
from backend.publishing.episode_renderer import _step_name, render_episode_page


def _published_episode() -> dict:
    return {
        "episode_id": "2026-W24",
        "concept": "Cheddar Broccoli Egg Bites",
        "published_at": "2026-06-14T12:00:00+00:00",
        "image_urls": ["/blob-images/ffd2aff5/round_1/macro_closeup.png"],
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {
                    "title": "Cheddar Broccoli Egg Bites",
                    "description": "Sturdy egg bites with a hash brown base.",
                    "category": "breakfast",
                    "prep_time": 20,
                    "cook_time": 30,
                    "servings": 12,
                    "ingredients": [{"item": "eggs", "amount": "8"}],
                    "instructions": ["Press bases.", "Bake."],
                },
            },
            "sunday": {"status": "complete"},
        },
    }


# ---------------------------------------------------------------------------
# Recipe page head
# ---------------------------------------------------------------------------

def test_published_page_has_canonical_and_og_url() -> None:
    html = render_episode_page(_published_episode())
    canonical = 'https://muffinpanrecipes.com/recipes/cheddar-broccoli-egg-bites'
    assert f'<link rel="canonical" href="{canonical}">' in html
    assert f'<meta property="og:url" content="{canonical}">' in html


def test_published_page_has_social_image_and_twitter_card() -> None:
    html = render_episode_page(_published_episode())
    abs_img = "https://muffinpanrecipes.com/blob-images/ffd2aff5/round_1/macro_closeup.png"
    assert f'<meta property="og:image" content="{abs_img}">' in html
    assert f'<meta property="twitter:image" content="{abs_img}">' in html
    assert 'twitter:card" content="summary_large_image"' in html


def test_unpublished_page_has_no_canonical() -> None:
    ep = _published_episode()
    ep["stages"].pop("sunday")
    ep.pop("published_at")
    html = render_episode_page(ep)
    assert 'rel="canonical"' not in html


# ---------------------------------------------------------------------------
# JSON-LD
# ---------------------------------------------------------------------------

def _extract_json_ld(html: str) -> dict:
    start = html.index('application/ld+json">') + len('application/ld+json">')
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def test_json_ld_has_rich_result_fields() -> None:
    ld = _extract_json_ld(render_episode_page(_published_episode()))
    assert ld["image"] == [
        "https://muffinpanrecipes.com/blob-images/ffd2aff5/round_1/macro_closeup.png"
    ]
    assert ld["author"] == {"@type": "Organization", "name": "Muffin Pan Recipes"}
    assert ld["datePublished"] == "2026-06-14"
    assert ld["totalTime"] == "PT50M"
    assert "muffin pan" in ld["keywords"]


def test_json_ld_omits_image_when_none_exists() -> None:
    ep = _published_episode()
    ep["image_urls"] = []
    ld = _extract_json_ld(render_episode_page(ep))
    assert "image" not in ld


def test_json_ld_has_cuisine_and_named_steps() -> None:
    """Google flagged missing recipeCuisine and unnamed HowToSteps."""
    ld = _extract_json_ld(render_episode_page(_published_episode()))
    assert ld["recipeCuisine"] == "American"
    steps = ld["recipeInstructions"]
    assert steps and all(s.get("name") and s.get("text") for s in steps)


# ---------------------------------------------------------------------------
# _step_name helper
# ---------------------------------------------------------------------------

def test_step_name_uses_short_step_verbatim() -> None:
    assert _step_name("Preheat oven to 325°F.", 0) == "Preheat oven to 325°F"


def test_step_name_truncates_long_step_at_word_boundary() -> None:
    long = (
        "In a medium bowl, combine the thawed well-squeezed hash browns with "
        "melted butter and salt until evenly coated."
    )
    name = _step_name(long, 2)
    assert len(name) <= 60
    assert not name.endswith(" ")
    assert long.startswith(name)


def test_step_name_falls_back_to_step_number_when_empty() -> None:
    assert _step_name("", 4) == "Step 5"


# ---------------------------------------------------------------------------
# Static seed pages — the 10 hand-coded files Google actually flagged
# ---------------------------------------------------------------------------

def _seed_pages() -> list:
    root = Path(__file__).resolve().parents[1] / "src" / "recipes"
    return sorted(root.glob("*/index.html"))


def test_seed_pages_exist() -> None:
    assert len(_seed_pages()) == 10


@pytest.mark.parametrize("page", _seed_pages(), ids=lambda p: p.parent.name)
def test_seed_page_json_ld_is_rich_result_complete(page) -> None:
    html_text = page.read_text(encoding="utf-8")
    ld = _extract_json_ld(html_text)
    assert ld["@type"] == "Recipe"
    # Critical field Google flagged.
    assert ld.get("image"), f"{page.parent.name} missing image"
    assert ld["author"] == {"@type": "Organization", "name": "Muffin Pan Recipes"}
    assert ld["recipeCuisine"] == "American"
    steps = ld.get("recipeInstructions", [])
    assert steps, f"{page.parent.name} has no instructions"
    for s in steps:
        assert s.get("name"), f"{page.parent.name} step missing name"
        assert s.get("text"), f"{page.parent.name} step missing text"


@pytest.mark.parametrize("page", _seed_pages(), ids=lambda p: p.parent.name)
def test_seed_page_json_ld_image_matches_og_image(page) -> None:
    """The JSON-LD image must be the real asset, not a guess."""
    html_text = page.read_text(encoding="utf-8")
    og = re.search(r'<meta property="og:image" content="([^"]+)"', html_text)
    assert og, f"{page.parent.name} has no og:image to source from"
    ld = _extract_json_ld(html_text)
    assert og.group(1) in ld["image"]


# ---------------------------------------------------------------------------
# Dynamic sitemap
# ---------------------------------------------------------------------------

_CATALOG = json.dumps({
    "recipes": [
        {"slug": "savory-bacon-biscuit-rounds", "episode_id": "2026-W23"},
        {"slug": "spinach-feta-egg-bites"},
        {"title": "No slug entry — skipped"},
    ]
})


def test_sitemap_lists_catalog_recipes_and_site_roots() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value=_CATALOG):
        resp = asyncio.run(episode_routes.sitemap_xml())
    xml = bytes(resp.body).decode()
    assert resp.media_type == "application/xml"
    assert "<loc>https://muffinpanrecipes.com/</loc>" in xml
    assert "<loc>https://muffinpanrecipes.com/this-week</loc>" in xml
    assert "<loc>https://muffinpanrecipes.com/recipes/savory-bacon-biscuit-rounds</loc>" in xml
    assert "<loc>https://muffinpanrecipes.com/recipes/spinach-feta-egg-bites</loc>" in xml
    assert xml.count("<url>") == 4  # slug-less entry skipped


def test_sitemap_lastmod_is_the_weeks_sunday() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value=_CATALOG):
        resp = asyncio.run(episode_routes.sitemap_xml())
    xml = bytes(resp.body).decode()
    # 2026-W23's Sunday is 2026-06-07
    assert "<lastmod>2026-06-07</lastmod>" in xml


def test_sitemap_falls_back_to_static_catalog_when_blob_empty() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value=None):
        resp = asyncio.run(episode_routes.sitemap_xml())
    xml = bytes(resp.body).decode()
    # Pin to the real static seed file: 2 site roots + one URL per seed recipe
    static = json.loads(
        (episode_routes.Path(episode_routes.__file__).resolve().parents[2]
         / "src" / "recipes.json").read_text()
    )
    seed_count = len([r for r in static["recipes"] if r.get("slug")])
    assert seed_count >= 10  # guard: the seed file itself went missing/empty
    assert xml.count("<url>") == 2 + seed_count
    assert "spinach-feta-egg-bites" in xml


def test_sitemap_survives_corrupt_catalog() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value="{not json"):
        resp = asyncio.run(episode_routes.sitemap_xml())
    xml = bytes(resp.body).decode()
    assert "<loc>https://muffinpanrecipes.com/</loc>" in xml
    assert xml.count("<url>") == 2
