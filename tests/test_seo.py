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
import html
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.admin import episode_routes
from backend.publishing.episode_renderer import (
    SEO_DESCRIPTION_MAX_LENGTH,
    _seo_description,
    _slugify,
    _step_name,
    render_episode_page,
)
from backend.publishing.static_renderer import render_home, render_recipes_index


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
    social_img = "https://muffinpanrecipes.com/blob-images/ffd2aff5/round_1/macro_closeup.social.jpg"
    assert f'<meta property="og:image" content="{social_img}">' in html
    # Twitter Card spec uses name=, not property= (X reads both; this is correct).
    assert f'<meta name="twitter:image" content="{social_img}">' in html
    assert 'twitter:card" content="summary_large_image"' in html
    assert 'property="twitter:' not in html  # no stray property= twitter tags


def test_social_metadata_accepts_safe_existing_asset_without_changing_recipe_image() -> None:
    social_image = "/blob-images/ffd2aff5/social/cheddar-broccoli.jpg"
    html = render_episode_page(_published_episode(), social_image_url=social_image)

    social_abs = "https://muffinpanrecipes.com" + social_image
    hero_abs = "https://muffinpanrecipes.com/blob-images/ffd2aff5/round_1/macro_closeup.png"
    assert f'<meta property="og:image" content="{social_abs}">' in html
    assert f'<meta name="twitter:image" content="{social_abs}">' in html
    assert _extract_json_ld(html)["image"] == [hero_abs]


def test_unsupported_social_format_uses_png_social_sibling() -> None:
    html = render_episode_page(
        _published_episode(),
        social_image_url="/blob-images/ffd2aff5/social/cheddar-broccoli.webp",
    )
    fallback = "https://muffinpanrecipes.com/blob-images/ffd2aff5/round_1/macro_closeup.social.jpg"
    assert f'<meta property="og:image" content="{fallback}">' in html
    assert ".webp\">" not in html.split('property="og:image"', 1)[1].split(">", 1)[0]


def test_description_metadata_is_word_bounded_but_editorial_copy_is_complete() -> None:
    full_description = (
        "These sturdy egg bites layer a crisp hash brown base with tender eggs, "
        "cheddar, and broccoli for a make-ahead breakfast that belongs in every "
        "muffin pan. Serve them warm, pack them for the week, or freeze a batch "
        "for an easy savory start to busy mornings."
    )
    episode = _published_episode()
    episode["stages"]["monday"]["recipe_data"]["description"] = full_description
    html = render_episode_page(episode)
    bounded = _seo_description(full_description)

    assert len(bounded) <= SEO_DESCRIPTION_MAX_LENGTH
    assert bounded.endswith("…")
    assert full_description.startswith(bounded[:-1])
    assert full_description[len(bounded) - 1].isspace()
    assert full_description in html
    assert f'<meta name="description" content="{bounded}">' in html
    assert f'<meta property="og:description" content="{bounded}">' in html
    assert f'<meta name="twitter:description" content="{bounded}">' in html
    assert _extract_json_ld(html)["description"] == full_description


def test_short_description_is_not_padded_or_truncated() -> None:
    short_description = "Crisp, cheesy egg bites for a quick breakfast."
    episode = _published_episode()
    episode["stages"]["monday"]["recipe_data"]["description"] = short_description
    html = render_episode_page(episode)

    assert _seo_description(short_description) == short_description
    assert f'<meta name="description" content="{short_description}">' in html
    assert f'<meta property="og:description" content="{short_description}">' in html
    assert f'<meta name="twitter:description" content="{short_description}">' in html


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
# HowToStep url — each step must link to the anchor it describes
# ---------------------------------------------------------------------------

def test_json_ld_steps_link_to_anchors_that_exist_on_the_page() -> None:
    """Every HowToStep url points at a `step-N` id actually rendered in the
    <ol class="instructions">, on the page's own canonical URL."""
    html_text = render_episode_page(_published_episode())
    ld = _extract_json_ld(html_text)
    canonical = "https://muffinpanrecipes.com/recipes/cheddar-broccoli-egg-bites"
    steps = ld["recipeInstructions"]
    assert [s["url"] for s in steps] == [
        f"{canonical}#step-1",
        f"{canonical}#step-2",
    ]
    for i, _ in enumerate(steps):
        assert f'<li id="step-{i + 1}">' in html_text


def test_step_urls_use_the_served_slug_not_the_title_slug() -> None:
    """Seed recipes are served under a catalog slug that can differ from
    _slugify(title); a title-derived step url would anchor into a 404."""
    seeds = _seed_recipes()
    slug = next(s for s in sorted(seeds) if s != _slugify(seeds[s]["recipe_data"]["title"]))
    rec = seeds[slug]
    html_text = render_seed_recipe_page(rec["recipe_data"], rec.get("image", ""), slug)
    ld = _extract_json_ld(html_text)
    for i, step in enumerate(ld["recipeInstructions"]):
        assert step["url"] == f"https://muffinpanrecipes.com/recipes/{slug}#step-{i + 1}"
        assert f'<li id="step-{i + 1}">' in html_text


# ---------------------------------------------------------------------------
# Nutrition — JSON-LD must never claim a figure the page doesn't show
# ---------------------------------------------------------------------------

def test_nutrition_rendered_and_marked_up_when_calories_exist() -> None:
    ep = _published_episode()
    ep["stages"]["monday"]["recipe_data"]["calories"] = 210
    html_text = render_episode_page(ep)
    assert _extract_json_ld(html_text)["nutrition"] == {
        "@type": "NutritionInformation",
        "calories": "210 calories",
    }
    # Visible on the page, labelled as an approximation.
    assert "Calories (approx)" in html_text
    assert "210 per serving" in html_text


def test_nutrition_absent_when_recipe_has_no_calories() -> None:
    """Recipes generated before CALORIES: shipped carry no figure — omit the
    field entirely rather than defaulting to a fabricated number."""
    html_text = render_episode_page(_published_episode())
    assert "nutrition" not in _extract_json_ld(html_text)
    assert "Calories (approx)" not in html_text


@pytest.mark.parametrize("bad", ["", None, "not a number", 0, -50])
def test_nutrition_omitted_for_unusable_calorie_values(bad) -> None:
    """A junk value must degrade to "no nutrition", never to a broken stat or
    a JSON-LD field with no visible counterpart."""
    ep = _published_episode()
    ep["stages"]["monday"]["recipe_data"]["calories"] = bad
    html_text = render_episode_page(ep)
    assert "nutrition" not in _extract_json_ld(html_text)
    assert "Calories (approx)" not in html_text


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


def test_seed_meta_descriptions_fit_the_preview_window() -> None:
    """Seed copy should not rely on renderer truncation for basic SERP copy."""
    descriptions = [
        rec["recipe_data"]["description"]
        for rec in _seed_recipes().values()
    ]
    assert all(70 <= len(description) <= SEO_DESCRIPTION_MAX_LENGTH for description in descriptions)


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
            "monday": {"status": "complete",
                       "recipe_data": {
                           "title": "X Cups", "description": "d", "category": "Savory",
                           "ingredients": ["1 egg"], "instructions": ["Bake."],
                       },
                       "dialogue": [{"character": "Margaret Chen", "message": "Let's bake."}]},
            "sunday": {"status": "complete"},
        },
    }
    with_bts = render_episode_page(episode, with_conversation=True)
    without = render_episode_page(episode, with_conversation=False)
    assert "Behind the Scenes" in with_bts
    assert "Behind the Scenes" not in without


def test_published_recipe_without_dialogue_suppresses_bts() -> None:
    """A finished recipe that never had a conversation (W10 lemon-meringue)
    must NOT render the 'conversation hasn't started yet' placeholder — the
    behind-the-scenes section is dropped entirely when there is no dialogue."""
    episode = {
        "concept": "Mini Lemon Meringue Cups",
        "image_urls": [],
        "stages": {
            "monday": {"status": "complete", "recipe_data": {
                "title": "Mini Lemon Meringue Cups", "description": "d", "category": "Sweet",
                "ingredients": ["1 lemon"], "instructions": ["Bake."],
            }},
            "sunday": {"status": "complete"},
        },
    }
    html = render_episode_page(episode, with_conversation=True)
    assert "Behind the Scenes" not in html
    assert "conversation hasn't started" not in html


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
    assert "<loc>https://muffinpanrecipes.com/recipes</loc>" in xml
    assert "<loc>https://muffinpanrecipes.com/this-week</loc>" in xml
    assert "<loc>https://muffinpanrecipes.com/recipes/savory-bacon-biscuit-rounds</loc>" in xml
    assert "<loc>https://muffinpanrecipes.com/recipes/spinach-feta-egg-bites</loc>" in xml
    assert xml.count("<url>") == 5  # 3 site roots + 2 recipes; slug-less entry skipped


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
    assert xml.count("<url>") == 3 + seed_count  # /, /recipes, /this-week + seeds
    assert "spinach-feta-egg-bites" in xml


def test_sitemap_survives_corrupt_catalog() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value="{not json"):
        resp = asyncio.run(episode_routes.sitemap_xml())
    xml = bytes(resp.body).decode()
    assert "<loc>https://muffinpanrecipes.com/</loc>" in xml
    assert xml.count("<url>") == 3  # site roots only: /, /recipes, /this-week


# ---------------------------------------------------------------------------
# /recipes — server-rendered crawlable hub index
# ---------------------------------------------------------------------------

def test_recipes_index_renders_crawlable_links() -> None:
    catalog = json.dumps({"recipes": [
        {"slug": "alpha-cups", "title": "Alpha Cups", "category": "Savory", "description": "d"},
        {"slug": "beta-bites", "title": "Beta Bites", "category": "Sweet", "description": "d"},
        {"title": "No slug — skipped"},
    ]})
    with patch.object(episode_routes.storage, "load_page", return_value=catalog):
        resp = asyncio.run(episode_routes.recipes_index())
    assert resp.status_code == 200
    body = bytes(resp.body).decode()
    assert '<a href="/recipes/alpha-cups"' in body
    assert '<a href="/recipes/beta-bites"' in body
    assert body.count('href="/recipes/') == 2  # slug-less entry skipped, no self/extra links
    assert 'rel="canonical" href="https://muffinpanrecipes.com/recipes"' in body
    assert '"@type": "CollectionPage"' in body


def test_recipes_index_route_uses_the_single_shared_renderer() -> None:
    """The Lambda route and the build-time site_builder must render /recipes
    through the same function (#6822) — two independent HTML builders is how
    the old inline copy silently missed og:image while the other got it."""
    assert episode_routes.recipes_index.__globals__["render_recipes_index"] is render_recipes_index


def test_recipes_index_has_social_card_and_descriptive_title() -> None:
    """#6822 (og:image) + #6823 (thin 22-char title): the collection page is
    what people share when linking the whole library, and it had neither."""
    catalog = json.dumps({"recipes": [
        {"slug": "alpha-cups", "title": "Alpha Cups", "category": "Savory", "description": "d"},
    ]})
    with patch.object(episode_routes.storage, "load_page", return_value=catalog):
        resp = asyncio.run(episode_routes.recipes_index())
    body = bytes(resp.body).decode()

    title = re.search(r"<title>([^<]+)</title>", body).group(1)
    assert 40 <= len(title) <= 60, f"title is {len(title)} chars: {title!r}"
    assert re.search(r'<meta property="og:title" content="[^"]{40,60}">', body)

    og_image = re.search(r'<meta property="og:image" content="([^"]+)">', body)
    assert og_image and og_image.group(1).startswith("https://muffinpanrecipes.com/")
    assert re.search(r'<meta property="og:image:width" content="\d+">', body)
    assert re.search(r'<meta property="og:image:height" content="\d+">', body)
    assert 'name="twitter:card" content="summary_large_image"' in body
    twitter_image = re.search(r'<meta name="twitter:image" content="([^"]+)">', body)
    assert twitter_image and twitter_image.group(1).startswith("https://muffinpanrecipes.com/")


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
    """"Dessert" is a stray label for "Sweet" in category tie-breaking."""
    rel = build_related_recipes(_RELATED_CATALOG, "stray-dessert", "Dessert")
    assert "sweet-one" in [recipe["slug"] for recipe in rel]


def test_related_recipes_same_category_excludes_self() -> None:
    rel = build_related_recipes(_RELATED_CATALOG, "alpha-cups", "Savory")
    slugs = [r["slug"] for r in rel]
    assert "alpha-cups" not in slugs
    assert slugs == ["beta-bites", "delta-dish", "echo-eggs", "gamma-gratin"]


def test_related_recipes_balance_inbound_distribution() -> None:
    """The shared ring must not give early alphabetic titles extra authority."""
    from collections import Counter

    inbound = Counter()
    for current in _RELATED_CATALOG:
        related = build_related_recipes(
            _RELATED_CATALOG,
            current["slug"],
            current["category"],
        )
        for recipe in related:
            inbound[recipe["slug"]] += 1

    assert set(inbound) == {recipe["slug"] for recipe in _RELATED_CATALOG}
    assert max(inbound.values()) == min(inbound.values())


def test_related_recipes_identical_absent_or_present_in_catalog() -> None:
    """THE regression test for the Sunday split-footer bug.

    Sunday renders the same recipe twice, straddling the catalog insert:
    regenerate_and_upload() runs before publish_recipe_to_catalog() (slug NOT
    yet in the catalog) and render_episode_page() runs after (slug present).
    Two structurally different algorithms used to sit behind that branch, so
    /this-week and /recipes/{slug} shipped different related-recipe footers
    for the same recipe on every publish. Absent and present must agree.
    """
    for entry in _RELATED_CATALOG:
        without_self = [r for r in _RELATED_CATALOG if r["slug"] != entry["slug"]]

        absent = build_related_recipes(without_self, entry["slug"], entry["category"])
        present = build_related_recipes(_RELATED_CATALOG, entry["slug"], entry["category"])

        assert absent == present, f"footer diverges for {entry['slug']}"


def test_related_recipes_deterministic_across_repeated_calls() -> None:
    """Same inputs, same bytes — a rebuild that diffs old vs new HTML per page
    is useless if re-rendering churns the footer."""
    for catalog, slug in (
        (_RELATED_CATALOG, "gamma-gratin"),
        ([r for r in _RELATED_CATALOG if r["slug"] != "gamma-gratin"], "gamma-gratin"),
    ):
        runs = [build_related_recipes(catalog, slug, "Savory") for _ in range(5)]
        assert all(run == runs[0] for run in runs)


def test_related_recipes_never_self_links() -> None:
    for entry in _RELATED_CATALOG:
        related = build_related_recipes(
            _RELATED_CATALOG, entry["slug"], entry["category"]
        )
        assert entry["slug"] not in [r["slug"] for r in related]


def test_related_recipes_returns_exactly_min_n_and_available() -> None:
    """Never silently short: exactly min(n, len(other_recipes))."""
    catalog = _RELATED_CATALOG  # 8 entries -> 7 others for a member slug
    assert len(build_related_recipes(catalog, "alpha-cups", "Savory", n=3)) == 3
    assert len(build_related_recipes(catalog, "alpha-cups", "Savory", n=7)) == 7
    assert len(build_related_recipes(catalog, "alpha-cups", "Savory", n=99)) == 7
    # Absent slug: all 8 catalog entries are "other".
    assert len(build_related_recipes(catalog, "not-in-catalog", "Savory", n=99)) == 8
    assert len(build_related_recipes(catalog[:2], "not-in-catalog", "Savory", n=4)) == 2


_CATEGORY_PREFERENCE_CATALOG = [
    # Slug order puts the source first, so every candidate is at zero inbound
    # links and category is the only thing left to break the tie.
    {"title": "A Self", "slug": "a-self", "category": "Sweet"},
    {"title": "B Savory", "slug": "b-savory", "category": "Savory"},
    {"title": "C Savory", "slug": "c-savory", "category": "Savory"},
    {"title": "D Savory", "slug": "d-savory", "category": "Savory"},
    {"title": "E Sweet", "slug": "e-sweet", "category": "Sweet"},
    {"title": "F Sweet", "slug": "f-sweet", "category": "Sweet"},
]


def test_related_recipes_same_category_preference_is_real() -> None:
    """The deleted rotation branch CLAIMED category preference and then threw
    it away with a rotation offset. This fails if the preference is a no-op:
    slug order alone would pick b/c/d/e, the preference pulls e/f to the front.
    """
    picks = [
        r["slug"]
        for r in build_related_recipes(_CATEGORY_PREFERENCE_CATALOG, "a-self", "Sweet", n=4)
    ]
    assert picks[:2] == ["e-sweet", "f-sweet"]
    assert picks[2:] == ["b-savory", "c-savory"]


def test_related_recipes_edge_cases() -> None:
    assert build_related_recipes([], "alpha-cups", "Savory") == []
    assert build_related_recipes(None, "alpha-cups", "Savory") == []
    # Catalog of one, which is the page itself.
    solo = [{"title": "Alpha Cups", "slug": "alpha-cups", "category": "Savory"}]
    assert build_related_recipes(solo, "alpha-cups", "Savory") == []
    # n <= 0.
    assert build_related_recipes(_RELATED_CATALOG, "alpha-cups", "Savory", n=0) == []
    assert build_related_recipes(_RELATED_CATALOG, "alpha-cups", "Savory", n=-3) == []
    # Entries missing slug or title are skipped, never rendered as blanks.
    ragged = [
        {"title": "No Slug", "category": "Savory"},
        {"slug": "no-title", "category": "Savory"},
        {"title": "Real One", "slug": "real-one", "category": "Savory"},
    ]
    assert build_related_recipes(ragged, "alpha-cups", "Savory") == [
        {"title": "Real One", "slug": "real-one"}
    ]
    # A category no recipe shares still fills the footer.
    unknown_cat = build_related_recipes(_RELATED_CATALOG, "alpha-cups", "Interstellar")
    assert len(unknown_cat) == 4
    # Missing category on catalog entries is not a crash.
    no_cats = [{"title": "One", "slug": "one"}, {"title": "Two", "slug": "two"}]
    assert len(build_related_recipes(no_cats, "one", "Savory")) == 1


# ---------------------------------------------------------------------------
# Analytics: the GA4 tag must reach every public page and no admin page
# ---------------------------------------------------------------------------

from backend.publishing.analytics import GA4_MEASUREMENT_ID, GA4_TAG  # noqa: E402


def _tagged(html: str) -> bool:
    """Both halves must be present: the loader AND the config call. A page
    with only the loader collects nothing."""
    return (
        f"googletagmanager.com/gtag/js?id={GA4_MEASUREMENT_ID}" in html
        and f"gtag('config', '{GA4_MEASUREMENT_ID}')" in html
    )


def test_recipe_page_carries_ga4_tag() -> None:
    assert _tagged(render_episode_page(_published_episode()))


def test_unpublished_this_week_page_carries_ga4_tag() -> None:
    """Mid-week the page is still public and still worth measuring, even
    though it deliberately has no canonical yet."""
    ep = _published_episode()
    ep["stages"].pop("sunday")
    ep.pop("published_at")
    assert _tagged(render_episode_page(ep))


def test_this_week_placeholder_carries_ga4_tag() -> None:
    assert _tagged(episode_routes._placeholder_page("2026-W33"))


def test_recipes_index_carries_ga4_tag() -> None:
    catalog = json.dumps({"recipes": [
        {"slug": "alpha-cups", "title": "Alpha Cups", "category": "Savory", "description": "d"},
    ]})
    with patch.object(episode_routes.storage, "load_page", return_value=catalog):
        resp = asyncio.run(episode_routes.recipes_index())
    assert _tagged(bytes(resp.body).decode())


def test_static_homepage_tag_matches_shared_constant() -> None:
    """src/index.html is served statically and cannot import the constant, so
    the two are asserted to agree here. Drift = a silently untagged homepage."""
    index = Path(__file__).resolve().parents[1] / "src" / "index.html"
    assert _tagged(index.read_text())


# ---------------------------------------------------------------------------
# Homepage — server-rendered hero + grid (#6821)
#
# src/index.html used to be a JS-only shell: the hero and grid were both
# empty containers filled entirely by client-side fetch(recipes.json). A
# non-JS crawler measured 43 words on the page — the least content on the
# site's most-linked URL. render_home bakes the featured recipe and the
# full grid in from the catalog at build time; the client JS still runs to
# refresh both from the live catalog (progressive enhancement).
# ---------------------------------------------------------------------------

def _home_catalog() -> list[dict]:
    return [
        {
            "slug": "alpha-cups",
            "title": "Alpha Cups",
            "category": "Savory",
            "description": "Crisp savory cups with a rich, buttery crumb and a peppery finish.",
            "image": "assets/images/alpha-cups.webp",
            "prep": "10 mins",
            "cook": "20 mins",
            "yield": "12 muffins",
        },
        {
            "slug": "beta-bites",
            "title": "Beta Bites",
            "category": "Sweet",
            "description": "Tender, honey-glazed bites finished with a dusting of flaky sea salt.",
            "image": "assets/images/beta-bites.webp",
            "prep": "15 mins",
            "cook": "18 mins",
            "yield": "12 muffins",
        },
        {
            "slug": "gamma-gratin",
            "title": "Gamma Gratin",
            "category": "Breakfast",
            "description": "A make-ahead breakfast gratin layered with potatoes, cheese, and herbs.",
            "image": "assets/images/gamma-gratin.webp",
            "prep": "12 mins",
            "cook": "25 mins",
            "yield": "12 muffins",
        },
    ]


def _visible_word_count(html_text: str) -> int:
    """Word count of what a non-JS crawler sees: no <script>/<style> bodies,
    tags stripped, entities unescaped."""
    stripped = re.sub(r"<(script|style)\b.*?</\1>", " ", html_text, flags=re.S | re.I)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return len([w for w in html.unescape(stripped).split() if w.strip()])


def test_render_home_has_substantive_visible_text() -> None:
    """The regression guard for #6821: raw HTML must carry real content, not
    the 43-word shell a non-JS crawler used to see."""
    assert _visible_word_count(render_home(_home_catalog())) >= 90


def test_committed_homepage_has_substantive_visible_text() -> None:
    """The actual acceptance criterion (#6821): a non-JS crawler hitting the
    real, committed src/index.html — built from the real catalog, not a
    3-item fixture — must see >= 150 words in the raw HTML response."""
    index = Path(__file__).resolve().parents[1] / "src" / "index.html"
    assert _visible_word_count(index.read_text(encoding="utf-8")) >= 150


def test_render_home_links_every_recipe_exactly_once() -> None:
    page = render_home(_home_catalog())
    anchors = re.findall(r'<a href="/recipes/([^"]+)"', page)
    assert sorted(anchors) == ["alpha-cups", "beta-bites", "gamma-gratin"]


def test_committed_homepage_has_one_link_per_recipe() -> None:
    """render_home output for the real catalog must not drop or double a
    recipe's link — the featured hero and the grid share one pool.

    The committed page was built with `--full-rebuild` against whatever
    catalog + published-episode sources were available on the build
    machine, which is a superset of src/recipes.json ∪ src/seed_recipes.json
    (it can also pick up locally-stored published episodes) — so this
    checks the invariant that must hold regardless of build machine: every
    catalog/seed recipe is linked, and no recipe is linked twice."""
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "src" / "recipes.json").read_text(encoding="utf-8"))
    recipes = catalog["recipes"] if isinstance(catalog, dict) else catalog
    slugs = {r["slug"] for r in recipes if r.get("slug") and r.get("title")}
    seeds = json.loads((root / "src" / "seed_recipes.json").read_text(encoding="utf-8"))
    slugs |= set(seeds.keys())

    index = root / "src" / "index.html"
    anchors = re.findall(r'<a href="/recipes/([^"]+)"', index.read_text(encoding="utf-8"))
    assert slugs <= set(anchors), f"missing from homepage: {slugs - set(anchors)}"
    assert len(anchors) == len(set(anchors)), "a recipe link is duplicated on the homepage"


def test_render_home_carries_ga4_tag_exactly_once() -> None:
    page = render_home(_home_catalog())
    assert _tagged(page)
    assert page.count(f"gtag('config', '{GA4_MEASUREMENT_ID}')") == 1


def test_render_home_featured_recipe_is_not_duplicated_in_the_grid() -> None:
    """The featured hero is recipes[0]; the grid must show the rest, not
    recipes[0] again (would double-count it in the "one <a> per recipe" link
    graph and mislead a reader into thinking it's two different recipes)."""
    page = render_home(_home_catalog())
    assert page.count('href="/recipes/alpha-cups"') == 1


def test_render_home_handles_empty_catalog_without_crashing() -> None:
    """Defensive only — build_site.py itself refuses an empty catalog before
    ever calling this, but the renderer must not assume a non-empty list."""
    page = render_home([])
    assert "<!DOCTYPE html>" in page
    assert _tagged(page)


def test_homepage_links_to_about() -> None:
    index = Path(__file__).resolve().parents[1] / "src" / "index.html"
    assert 'href="/about"' in index.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# /about — E-E-A-T disclosure page (#6667)
# ---------------------------------------------------------------------------

_ABOUT_TEAM = [
    ("Margaret Chen", "Baker"),
    ("Stephanie", "Creative Director"),
    ("Julian Torres", "Art Director"),
    ("Marcus Reid", "Copywriter"),
    ("Devon Park", "Site Architect"),
    ("Ria Castillo", "Social Media Manager"),
]


def _about_html() -> str:
    return (Path(__file__).resolve().parents[1] / "src" / "about.html").read_text(encoding="utf-8")


def test_about_page_names_every_character_and_role() -> None:
    """E-E-A-T: who actually makes this food. Names must match the source of
    truth in backend/data/agent_personalities.json, not paraphrase it."""
    about = _about_html()
    for name, role in _ABOUT_TEAM:
        assert name in about, f"missing character name: {name}"
        assert role in about, f"missing character role: {role}"


def test_about_page_discloses_ai_generation_and_automated_publish() -> None:
    """The other half of E-E-A-T: the process must be disclosed, not implied.
    A reader must be able to tell this isn't human-tested unless stated."""
    about = _about_html().lower()
    assert "generated by ai" in about
    assert "not tested in a physical kitchen" in about
    assert "sunday" in about
    assert "erik sjaastad" in about


def test_about_page_has_substantive_body_copy() -> None:
    """Card asks for ~300-400 words; give real headroom either side of that
    so small copy edits don't make this test the bottleneck."""
    word_count = _visible_word_count(_about_html())
    assert 250 <= word_count <= 550, word_count


def test_about_page_carries_ga4_tag_exactly_once() -> None:
    about = _about_html()
    assert _tagged(about)
    assert about.count(f"gtag('config', '{GA4_MEASUREMENT_ID}')") == 1


def test_about_page_has_canonical_og_and_aboutpage_jsonld() -> None:
    about = _about_html()
    canonical = "https://muffinpanrecipes.com/about"
    assert f'<link rel="canonical" href="{canonical}">' in about
    assert f'<meta property="og:url" content="{canonical}">' in about
    assert 'property="og:image"' in about
    assert 'name="twitter:card" content="summary_large_image"' in about

    ld = _extract_json_ld(about)
    types = {node["@type"] for node in ld["@graph"]}
    assert "AboutPage" in types
    assert "Organization" in types


def test_recipes_index_and_about_page_footers_link_to_about() -> None:
    """The card asks for a link from the homepage/footer and the recipe
    pages' footer where one is shared; /recipes shares static_renderer's
    footer, so it's covered here alongside the standalone about page."""
    recipes_html = render_recipes_index([{"slug": "a", "title": "A", "category": "Savory"}])
    assert 'href="/about"' in recipes_html
    assert 'href="/about"' in _about_html()


def test_admin_pages_do_not_carry_ga4_tag() -> None:
    """Internal traffic must stay out of the property — on a site this size a
    few admin sessions a day would visibly distort engagement metrics."""
    templates = Path(__file__).resolve().parents[1] / "backend" / "admin" / "templates"
    tagged = [p.name for p in templates.glob("*.html") if GA4_MEASUREMENT_ID in p.read_text()]
    assert tagged == [], f"admin templates must not carry the GA4 tag: {tagged}"


def test_ga4_tag_constant_is_well_formed() -> None:
    assert _tagged(GA4_TAG)
    assert GA4_MEASUREMENT_ID.startswith("G-")


# ---------------------------------------------------------------------------
# Legacy blob pages: serve-time GA4 injection
# ---------------------------------------------------------------------------

from backend.publishing.analytics import ensure_ga4_tag  # noqa: E402

_LEGACY_PAGE = (
    "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
    "    <meta charset=\"UTF-8\">\n"
    "    <title>Spanakopita Phyllo Cups | Muffin Pan Recipes</title>\n"
    "</head>\n<body><h1>Spanakopita Phyllo Cups</h1></body>\n</html>"
)


def test_injects_tag_into_legacy_page() -> None:
    """The 21 pages published before the tag shipped are frozen in blob and
    must still be measured."""
    out = ensure_ga4_tag(_LEGACY_PAGE)
    assert _tagged(out)
    assert "<title>Spanakopita Phyllo Cups" in out  # original content preserved
    assert out.index(GA4_MEASUREMENT_ID) < out.index("</head>")  # landed in <head>


def test_injection_is_idempotent() -> None:
    """Freshly rendered pages already carry the tag — no double-tagging, which
    would double-count every pageview."""
    already = render_episode_page(_published_episode())
    assert ensure_ga4_tag(already) == already
    assert ensure_ga4_tag(ensure_ga4_tag(_LEGACY_PAGE)) == ensure_ga4_tag(_LEGACY_PAGE)
    assert ensure_ga4_tag(_LEGACY_PAGE).count(f"gtag('config', '{GA4_MEASUREMENT_ID}')") == 1


def test_injection_never_breaks_a_page() -> None:
    """Analytics must never take down a live recipe page."""
    assert ensure_ga4_tag("") == ""
    assert ensure_ga4_tag("<html><body>no head</body></html>") == "<html><body>no head</body></html>"


def test_missing_head_is_logged_not_silent(caplog) -> None:
    """Serving untagged is acceptable; doing it silently is not — an untagged
    page looks healthy in a browser and is invisible in GA4."""
    import logging

    with caplog.at_level(logging.WARNING):
        ensure_ga4_tag("<html><body>no head</body></html>")
    assert any("no <head>" in r.getMessage() for r in caplog.records)


def test_recipe_route_does_not_mutate_legacy_page() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value=_LEGACY_PAGE):
        resp = asyncio.run(episode_routes.recipe_page("spanakopita-phyllo-cups"))
    assert resp.status_code == 200
    assert bytes(resp.body).decode() == _LEGACY_PAGE


def test_this_week_route_does_not_mutate_legacy_page() -> None:
    with patch.object(episode_routes.storage, "load_page", return_value=_LEGACY_PAGE):
        resp = asyncio.run(episode_routes.this_week_page())
    assert resp.status_code == 200
    assert bytes(resp.body).decode() == _LEGACY_PAGE


def test_csp_allows_ga4(monkeypatch) -> None:
    """The security middleware applies to public recipe pages too. A CSP that
    omits googletagmanager silently blocks the tag on every lambda-served page
    while the static homepage keeps tracking — which reads as a broken install.
    """
    from fastapi.testclient import TestClient
    from backend.admin.app import create_admin_app

    monkeypatch.delenv("VERCEL_ENV", raising=False)
    with TestClient(create_admin_app()) as client:
        csp = client.get("/health").headers.get("content-security-policy", "")

    assert "https://www.googletagmanager.com" in csp, "gtag.js would be blocked"
    connect = next(p for p in csp.split("; ") if p.startswith("connect-src"))
    assert "google-analytics.com" in connect, "GA4 could not send collected data"


def test_vercel_lambda_leaves_csp_to_global_route_header(monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from backend.admin.app import create_admin_app

    monkeypatch.setenv("VERCEL_ENV", "production")
    with TestClient(create_admin_app()) as client:
        csp = client.get("/health").headers.get("content-security-policy")

    assert csp is None


# ---------------------------------------------------------------------------
# robots.txt — AI crawler directives (#6820)
#
# Training and retrieval crawlers are independently controllable, and the
# repo's own robots.txt used to name zero of either kind (a bare "User-agent:
# *" group). Retrieval bots feed cited AI-search answers, so allowing them
# is the point; training bots are a deliberate allow per Erik's SEO notes
# (Google-Extended opt-out costs nothing in Search, and buys nothing here).
# ---------------------------------------------------------------------------

_RETRIEVAL_BOTS = (
    "OAI-SearchBot",
    "ChatGPT-User",
    "Claude-SearchBot",
    "Claude-User",
    "PerplexityBot",
)
_TRAINING_BOTS = (
    "GPTBot",
    "ClaudeBot",
    "Google-Extended",
    "Bytespider",
    "Applebot-Extended",
    "Meta-ExternalAgent",
    "CCBot",
)


def _robots_txt() -> str:
    return (Path(__file__).resolve().parents[1] / "src" / "robots.txt").read_text(encoding="utf-8")


def _agent_block(robots: str, agent: str) -> str:
    """Return the directive lines for one 'User-agent: <agent>' group."""
    pattern = re.compile(
        rf"^User-agent: {re.escape(agent)}$(.*?)(?=^User-agent:|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(robots)
    assert match, f"no 'User-agent: {agent}' group in robots.txt"
    return match.group(1)


@pytest.mark.parametrize("agent", _RETRIEVAL_BOTS + _TRAINING_BOTS)
def test_named_ai_bot_group_allows_and_disallows_api_and_admin(agent) -> None:
    """Every named bot gets its own group, explicitly allowed, with the same
    /api/ and /admin/ guard every other group carries."""
    block = _agent_block(_robots_txt(), agent)
    assert "Allow: /" in block
    assert "Disallow: /api/" in block
    assert "Disallow: /admin/" in block


def test_wildcard_catch_all_group_still_present() -> None:
    """Named-bot groups must not replace the fallback for every other
    crawler (search engines, unlisted bots)."""
    block = _agent_block(_robots_txt(), "*")
    assert "Allow: /" in block
    assert "Disallow: /api/" in block
    assert "Disallow: /admin/" in block


def test_robots_txt_declares_sitemap() -> None:
    assert "Sitemap: https://muffinpanrecipes.com/sitemap.xml" in _robots_txt()


# ---------------------------------------------------------------------------
# Responsive images (#6755) — the recipe hero's <picture> must always carry
# a sizes attribute, whether or not width-variant blobs exist yet.
# ---------------------------------------------------------------------------

def test_published_hero_picture_source_has_sizes_attribute() -> None:
    html = render_episode_page(_published_episode())
    assert 'sizes="(max-width: 768px) 100vw, 720px"' in html


def test_published_hero_srcset_uses_width_descriptors_once_variants_exist() -> None:
    with patch(
        "backend.publishing.episode_renderer.storage.image_variants_available",
        return_value=True,
    ):
        html = render_episode_page(_published_episode())

    assert (
        'srcset="/blob-images/ffd2aff5/round_1/macro_closeup-400w.webp 400w, '
        '/blob-images/ffd2aff5/round_1/macro_closeup-800w.webp 800w, '
        '/blob-images/ffd2aff5/round_1/macro_closeup.webp 1536w"'
    ) in html


def test_homepage_emits_srcset_only_when_variants_exist() -> None:
    """The homepage hero is its LCP image; it must get 400w/800w candidates when
    the variants exist and must never advertise one that would 404 (#6755)."""
    from unittest.mock import patch

    from backend.publishing import static_renderer

    recipes = [
        {"slug": "a-cups", "title": "A Cups", "image": "/blob-images/aa11/round_1/macro_closeup.webp", "category": "Sweet"},
        {"slug": "b-cups", "title": "B Cups", "image": "/blob-images/bb22/round_1/macro_closeup.webp", "category": "Party"},
    ]
    with patch("backend.publishing.episode_renderer._variants_available", side_effect=lambda url, cache=None: "aa11" in url):
        page = static_renderer.render_home(recipes)

    assert 'srcset="/blob-images/aa11/round_1/macro_closeup-400w.webp 400w, /blob-images/aa11/round_1/macro_closeup-800w.webp 800w' in page
    assert "bb22/round_1/macro_closeup-400w.webp" not in page
    assert page.count("sizes=") == 1


def test_every_robots_group_disallows_the_rewrite_continuation_path() -> None:
    """/src/ is the path Vercel continues routing with after a check:true miss
    and is reachable directly; it must never be crawled as a duplicate."""
    robots = _robots_txt()
    groups = [g for g in robots.split("User-agent:")[1:] if g.strip()]  # [0] is the preamble comment
    assert groups
    for group in groups:
        assert "Disallow: /src/" in group, group.splitlines()[0]
