"""The reader's recipe pages ship as committed artifacts, served filesystem-first.

Vercel ignores Build & Development Settings — ``buildCommand`` included —
whenever a top-level ``builds`` array exists in ``vercel.json``. That is why
PR #88's reader routes 404'd: they pointed at files a build step never
generated (card #6793, reverted in PR #90). The retry (#6684, #6688,
2026-09-05) is the HYBRID: ``scripts/build_site.py`` renders every published
recipe page (catalog entries and the ten seeds) and the homepage into
``src/`` and those files are COMMITTED; ``vercel.json`` serves
``/recipes/<slug>`` from the committed file when it exists (``check: true``
before ``handle: filesystem``) and falls through to the Lambda when it does
not — so a week published to Blob after the last deploy still renders.
``/this-week``, ``/recipes``, ``/recipes.json`` and ``/sitemap.xml`` stay on
the Lambda; the builder still emits them under ``src/`` as snapshots that are
never served.

These tests are the tripwire: forget to regenerate an artifact and CI fails
instead of the live site. Regenerate with

    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/build_site.py --full-rebuild --require-cloud
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

SEED_RECIPE_SLUG = "classic-blueberry-muffins"  # from src/seed_recipes.json
PUBLISHED_EPISODE_SLUG = "mini-lemon-meringue-cups"  # cron-published, 2026-W10

REQUIRED_ARTIFACTS = (
    "index.html",
    "about.html",
    "recipes/index.html",
    f"recipes/{SEED_RECIPE_SLUG}/index.html",
    f"recipes/{PUBLISHED_EPISODE_SLUG}/index.html",
    "this-week/index.html",
    "sitemap.xml",
    "recipes.json",
)

_EPISODE_ALIAS_SLUG = re.compile(r"-\d{4}-w\d{2}(-\d+)?$")
_REGEN = (
    "Regenerate the committed reader artifacts with: doppler run --project "
    "muffinpanrecipes --config prd -- uv run python scripts/build_site.py "
    "--full-rebuild --require-cloud"
)


def _catalog() -> list[dict]:
    return json.loads((SRC / "recipes.json").read_text(encoding="utf-8"))["recipes"]


@pytest.mark.parametrize("relative_path", REQUIRED_ARTIFACTS)
def test_required_static_artifact_is_committed_and_non_empty(relative_path: str) -> None:
    artifact = SRC / relative_path
    assert artifact.is_file(), f"src/{relative_path} is missing. {_REGEN}"
    assert artifact.stat().st_size > 0, f"src/{relative_path} is empty"


def test_html_artifacts_are_real_documents() -> None:
    for relative_path in REQUIRED_ARTIFACTS:
        if not relative_path.endswith(".html"):
            continue
        page = (SRC / relative_path).read_text(encoding="utf-8")
        assert page.lstrip().lower().startswith("<!doctype html>"), relative_path
        assert "Muffin Pan Recipes" in page, relative_path


def test_every_catalog_recipe_has_a_committed_page() -> None:
    missing = [
        recipe["slug"]
        for recipe in _catalog()
        if not (SRC / "recipes" / str(recipe["slug"]) / "index.html").is_file()
    ]
    assert not missing, f"src/recipes.json advertises recipes with no committed page: {missing}. {_REGEN}"


def test_no_committed_page_is_orphaned_from_the_catalog() -> None:
    """A page without a catalog entry is a stale artifact from a renamed slug."""
    catalog_slugs = {str(r["slug"]) for r in _catalog()}
    committed = {p.parent.name for p in (SRC / "recipes").glob("*/index.html")}
    orphans = sorted(committed - catalog_slugs)
    assert not orphans, f"committed pages with no catalog entry: {orphans}"


def test_catalog_has_no_duplicate_or_aliased_recipe_slugs() -> None:
    slugs = [str(recipe["slug"]) for recipe in _catalog()]
    assert len(slugs) == len(set(slugs)), "src/recipes.json contains duplicate slugs"
    aliases = [slug for slug in slugs if _EPISODE_ALIAS_SLUG.search(slug)]
    assert not aliases, f"one recipe listed twice under an episode-suffixed alias: {aliases}"


def test_committed_recipe_pages_carry_the_seo_surface() -> None:
    """The point of the rebuild (#6827): og:image, GA4 once, intrinsic image
    dimensions, self-hosted fonts — on every committed cron-published page."""
    page = (SRC / "recipes" / PUBLISHED_EPISODE_SLUG / "index.html").read_text(encoding="utf-8")
    assert 'property="og:image"' in page
    assert page.count("googletagmanager.com/gtag/js") == 1
    assert "fonts.googleapis.com" not in page
    imgs = re.findall(r"<img\b[^>]*>", page)
    assert imgs and all("width=" in tag and "height=" in tag for tag in imgs), "every <img> needs width/height"


def test_sitemap_lists_the_committed_recipe_pages() -> None:
    sitemap = (SRC / "sitemap.xml").read_text(encoding="utf-8")
    locations = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert len(locations) == len(set(locations)), "sitemap.xml has duplicate <loc> entries"
    for slug in (SEED_RECIPE_SLUG, PUBLISHED_EPISODE_SLUG):
        assert f"/recipes/{slug}" in sitemap, slug


def test_vercel_config_declares_no_build_command() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert "builds" in config
    assert "buildCommand" not in config, (
        'vercel.json has a top-level "builds" array, so Vercel ignores buildCommand '
        "entirely; the reader pages are committed artifacts under src/."
    )


def test_static_routes_resolve_to_committed_files() -> None:
    routes = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["routes"]
    for route in routes:
        dest = str(route.get("dest") or "")
        if not dest.startswith("/src/") or "$" in dest or "*" in dest:
            continue
        target = ROOT / dest.lstrip("/")
        assert target.is_file() and target.stat().st_size > 0, (
            f"vercel.json routes {route.get('src')} to {dest}, which is not a committed non-empty file"
        )
