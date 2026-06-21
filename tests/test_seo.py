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
                    "cuisine": "Korean",
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
    # Twitter Card spec uses name=, not property= (X reads both; this is correct).
    assert f'<meta name="twitter:image" content="{abs_img}">' in html
    assert 'twitter:card" content="summary_large_image"' in html
    assert 'property="twitter:' not in html  # no stray property= twitter tags


def test_published_page_has_breadcrumb_jsonld() -> None:
    html = render_episode_page(_published_episode())
    assert '"@type": "BreadcrumbList"' in html
    assert '"name": "Home"' in html
    assert '"name": "Recipes"' in html
    assert '"name": "Cheddar Broccoli Egg Bites"' in html  # current page, position 3


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


def test_json_ld_uses_declared_cuisine_and_named_steps() -> None:
    """recipeCuisine reflects the recipe's declared cuisine, not a blanket value."""
    ld = _extract_json_ld(render_episode_page(_published_episode()))
    assert ld["recipeCuisine"] == "Korean"
    steps = ld["recipeInstructions"]
    assert steps and all(s.get("name") and s.get("text") for s in steps)


def test_json_ld_omits_cuisine_when_absent() -> None:
    """No fabricated cuisine: omit recipeCuisine when the recipe declares none."""
    ep = _published_episode()
    ep["stages"]["monday"]["recipe_data"].pop("cuisine")
    ld = _extract_json_ld(render_episode_page(ep))
    assert "recipeCuisine" not in ld


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


def test_step_name_handles_long_unbroken_token() -> None:
    """No whitespace to break on: cap at 60 chars, never return empty."""
    name = _step_name("x" * 80, 0)
    assert name == "x" * 60


# ---------------------------------------------------------------------------
# Seed recipes — the original 10, now data rendered through the shared
# renderer (no more hand-coded HTML). These guard the Google fix on the
# unified path AND that the migration preserved each recipe's content.
# ---------------------------------------------------------------------------

from backend.publishing.episode_renderer import render_seed_recipe_page  # noqa: E402


def _seed_recipes() -> dict:
    path = Path(__file__).resolve().parents[1] / "src" / "seed_recipes.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_seed_recipes_file_has_ten_complete_entries() -> None:
    seeds = _seed_recipes()
    assert len(seeds) == 10
    for slug, rec in seeds.items():
        assert rec.get("image"), f"{slug} missing image"
        rd = rec["recipe_data"]
        assert rd.get("title") and rd.get("ingredients") and rd.get("instructions"), slug


@pytest.mark.parametrize("slug", sorted(_seed_recipes().keys()))
def test_seed_recipe_renders_rich_result_complete(slug) -> None:
    rec = _seed_recipes()[slug]
    html_text = render_seed_recipe_page(rec["recipe_data"], rec.get("image", ""), slug)
    ld = _extract_json_ld(html_text)
    assert ld["@type"] == "Recipe"
    assert ld.get("image"), f"{slug} missing JSON-LD image"
    assert ld["author"] == {"@type": "Organization", "name": "Muffin Pan Recipes"}
    # recipeCuisine reflects each seed's declared cuisine (e.g. lasagna=Italian).
    assert ld["recipeCuisine"] == rec["recipe_data"]["cuisine"]
    steps = ld.get("recipeInstructions", [])
    assert steps, f"{slug} has no instructions"
    for s in steps:
        assert s.get("name") and s.get("text"), f"{slug} step missing name/text"
    # Content preserved from the migrated data.
    for ing in rec["recipe_data"]["ingredients"]:
        assert ing in html_text, f"{slug} dropped ingredient {ing!r}"
    for step in rec["recipe_data"]["instructions"]:
        assert step in html_text, f"{slug} dropped step {step!r}"
    # Canonical MUST point at the served slug, not a title-derived one that
    # would 404 (7 of 10 seed slugs differ from _slugify(title)).
    canonical = f"https://muffinpanrecipes.com/recipes/{slug}"
    assert f'<link rel="canonical" href="{canonical}">' in html_text
    assert f'<meta property="og:url" content="{canonical}">' in html_text
    assert "og:image" in html_text
    assert "conversation hasn't started" not in html_text
    assert "Behind the Scenes" not in html_text


def test_with_conversation_false_suppresses_bts() -> None:
    """The flag the seed path relies on actually removes the dialogue block."""
    episode = {
        "concept": "X",
        "image_urls": [],
        "stages": {
            "monday": {"status": "complete", "recipe_data": {
                "title": "X Cups", "description": "d", "category": "Savory",
                "ingredients": ["1 egg"], "instructions": ["Bake."],
            }},
            "sunday": {"status": "complete"},
        },
    }
    with_bts = render_episode_page(episode, with_conversation=True)
    without = render_episode_page(episode, with_conversation=False)
    assert "Behind the Scenes" in with_bts
    assert "Behind the Scenes" not in without


# ---------------------------------------------------------------------------
# /recipes/{slug} route — seed recipes served from data, no static HTML
# ---------------------------------------------------------------------------

def test_route_serves_seed_recipe_when_blob_empty() -> None:
    slug = sorted(_seed_recipes().keys())[0]
    # No blob page for this slug → route must render it from seed data.
    with patch.object(episode_routes.storage, "load_page", return_value=None):
        resp = asyncio.run(episode_routes.recipe_page(slug))
    assert resp.status_code == 200
    body = bytes(resp.body).decode()
    assert _seed_recipes()[slug]["recipe_data"]["title"] in body
    assert 'application/ld+json' in body
    # Served page's canonical points at its own URL.
    assert f'href="https://muffinpanrecipes.com/recipes/{slug}"' in body


def test_route_404s_for_unknown_recipe() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value=None):
        resp = asyncio.run(episode_routes.recipe_page("no-such-recipe"))
    assert resp.status_code == 404


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


# ---------------------------------------------------------------------------
# Internal linking: crawlable related-recipe footer links
# ---------------------------------------------------------------------------

import re  # noqa: E402

from backend.publishing.episode_renderer import build_related_recipes  # noqa: E402

_RELATED_CATALOG = [
    {"title": "Alpha Cups", "slug": "alpha-cups", "category": "Savory"},
    {"title": "Beta Bites", "slug": "beta-bites", "category": "Savory"},
    {"title": "Gamma Gratin", "slug": "gamma-gratin", "category": "Savory"},
    {"title": "Delta Dish", "slug": "delta-dish", "category": "Savory"},
    {"title": "Echo Eggs", "slug": "echo-eggs", "category": "Savory"},
    {"title": "Sweet One", "slug": "sweet-one", "category": "Sweet"},
    {"title": "Lonely Party", "slug": "lonely-party", "category": "Party"},
    {"title": "Stray Dessert", "slug": "stray-dessert", "category": "Dessert"},
]


def test_recipe_page_has_crawlable_internal_recipe_link() -> None:
    """Every recipe page must carry >=1 real <a href="/recipes/..."> in raw HTML
    (no JS), so the internal link graph exists for crawlers."""
    html = render_episode_page(_published_episode(), catalog=_RELATED_CATALOG)
    anchors = re.findall(r'<a href="(/recipes/[^"]+)"', html)
    assert len(anchors) >= 1
    assert "More Muffin Pan Recipes" in html


def test_related_recipes_deterministic_no_self_and_fills() -> None:
    """Thin category (Party-of-one) still fills to N=4, never self-links, stable."""
    a = build_related_recipes(_RELATED_CATALOG, "lonely-party", "Party")
    b = build_related_recipes(_RELATED_CATALOG, "lonely-party", "Party")
    assert [r["slug"] for r in a] == [r["slug"] for r in b]
    assert len(a) == 4
    assert all(r["slug"] != "lonely-party" for r in a)


def test_related_recipes_dessert_groups_with_sweet() -> None:
    """"Dessert" is a stray label for "Sweet" — it must group with Sweet first."""
    rel = build_related_recipes(_RELATED_CATALOG, "stray-dessert", "Dessert")
    assert rel[0]["slug"] == "sweet-one"


def test_related_recipes_same_category_excludes_self() -> None:
    rel = build_related_recipes(_RELATED_CATALOG, "alpha-cups", "Savory")
    slugs = [r["slug"] for r in rel]
    assert "alpha-cups" not in slugs
    assert slugs == ["beta-bites", "delta-dish", "echo-eggs", "gamma-gratin"]
