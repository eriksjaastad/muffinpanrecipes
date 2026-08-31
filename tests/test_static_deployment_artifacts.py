"""The reader's static pages ship as committed artifacts, not as a build step.

Vercel ignores Build & Development Settings — ``buildCommand`` included —
whenever a top-level ``builds`` array exists in ``vercel.json``:

    WARNING! Due to "builds" existing in your configuration file, the Build
    and Development Settings defined in your Project Settings will not apply.

So ``npm run build:site`` never ran on Vercel and every reader route added in
PR #88 pointed at a file that was never generated: /recipes, /recipes/<slug>
and /this-week all returned 404 on the preview deployment (card #6793).

The fix is to generate the artifacts locally
(``doppler run -- uv run python -m scripts.build_site --full-rebuild``) and
commit them. These tests are the tripwire: delete or forget to regenerate an
artifact and CI fails instead of the live site.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

# One page per shape the builder emits, so a regression in any single code
# path is caught by name rather than only by the generic sweep below.
SEED_RECIPE_SLUG = "classic-blueberry-muffins"  # from src/seed_recipes.json
PUBLISHED_EPISODE_SLUG = "mini-lemon-meringue-cups"  # cron-published, 2026-W10

REQUIRED_ARTIFACTS = (
    "recipes/index.html",
    f"recipes/{SEED_RECIPE_SLUG}/index.html",
    f"recipes/{PUBLISHED_EPISODE_SLUG}/index.html",
    "this-week/index.html",
    "sitemap.xml",
    "recipes.json",
)

# "<slug>-2026-w10" style aliases are what the builder used to mint for a
# published episode whose catalog entry predates episode_id stamping. They are
# duplicate pages for one recipe; see site_builder._catalog_and_episode_slugs.
_EPISODE_ALIAS_SLUG = re.compile(r"-\d{4}-w\d{2}(-\d+)?$")


def _catalog() -> list[dict]:
    data = json.loads((SRC / "recipes.json").read_text(encoding="utf-8"))
    return data["recipes"]


@pytest.mark.parametrize("relative_path", REQUIRED_ARTIFACTS)
def test_required_static_artifact_is_committed_and_non_empty(relative_path: str) -> None:
    artifact = SRC / relative_path

    assert artifact.is_file(), (
        f"src/{relative_path} is missing. Regenerate the committed reader "
        "artifacts with: doppler run -- uv run python -m scripts.build_site "
        "--full-rebuild"
    )
    assert artifact.stat().st_size > 0, f"src/{relative_path} is empty"


def test_recipe_pages_are_real_html_documents() -> None:
    for relative_path in REQUIRED_ARTIFACTS:
        if not relative_path.endswith(".html"):
            continue
        page = (SRC / relative_path).read_text(encoding="utf-8")
        assert page.lstrip().startswith("<!DOCTYPE html>"), relative_path
        assert "Muffin Pan Recipes" in page, relative_path


def test_every_catalog_recipe_has_a_committed_page() -> None:
    missing = [
        recipe["slug"]
        for recipe in _catalog()
        if not (SRC / "recipes" / str(recipe["slug"]) / "index.html").is_file()
    ]

    assert not missing, (
        "src/recipes.json advertises recipes with no committed page: "
        f"{', '.join(missing)}"
    )


def test_catalog_has_no_duplicate_or_aliased_recipe_slugs() -> None:
    slugs = [str(recipe["slug"]) for recipe in _catalog()]

    assert len(slugs) == len(set(slugs)), "src/recipes.json contains duplicate slugs"
    aliases = [slug for slug in slugs if _EPISODE_ALIAS_SLUG.search(slug)]
    assert not aliases, (
        "One published recipe is listed twice, once under an episode-suffixed "
        f"alias: {', '.join(aliases)}"
    )


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
        'vercel.json has a top-level "builds" array, so Vercel ignores '
        "buildCommand entirely. A buildCommand here is dead configuration "
        "that reads as if the site is built on deploy when it is not — the "
        "reader pages are committed artifacts under src/."
    )


def test_static_routes_resolve_to_committed_files() -> None:
    routes = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))["routes"]

    for route in routes:
        dest = str(route.get("dest") or "")
        if not dest.startswith("/src/") or "$" in dest or "*" in dest:
            continue
        target = ROOT / dest.lstrip("/")
        assert target.is_file() and target.stat().st_size > 0, (
            f"vercel.json routes {route['src']} to {dest}, which is not a "
            "committed non-empty file"
        )
