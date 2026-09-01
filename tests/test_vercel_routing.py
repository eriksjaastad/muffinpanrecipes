"""Regression tests for Vercel's public route ordering and fallback behavior."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _routes() -> list[dict]:
    with (ROOT / "vercel.json").open(encoding="utf-8") as config_file:
        return json.load(config_file)["routes"]


def test_unknown_urls_use_branded_404_instead_of_homepage() -> None:
    routes = _routes()
    catch_all = routes[-1]

    assert catch_all["src"] == "/(.*)"
    assert catch_all["dest"] == "/src/404.html"
    assert catch_all["status"] == 404
    assert catch_all["dest"] != "/src/index.html"


def test_public_routes_are_present_and_ordered() -> None:
    routes = _routes()
    sources = [route["src"] for route in routes]

    assert sources == [
        "/(.*)",
        "/(.*)",
        "/api/(.*)",
        "/admin/static/(.*)",
        "/admin/(.*)",
        "/auth/(.*)",
        "/health",
        "/recipes/([^/]+)$",
        "/recipes/?$",
        "/blob-images/(.*)",
        "/assets/(.*)",
        "/sitemap\\.xml",
        "/robots\\.txt",
        "/recipes\\.json",
        "/this-week/?$",
        "/",
        "/(.*)",
    ]

    assert routes[1]["has"] == [{"type": "host", "value": "www.muffinpanrecipes.com"}]
    assert routes[1]["status"] == 301
    assert routes[-2] == {"src": "/", "dest": "/src/index.html"}


def test_legacy_headers_run_before_existing_routes() -> None:
    routes = _routes()

    assert routes[0] == {
        "src": "/(.*)",
        "headers": {
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://www.google-analytics.com https://*.google-analytics.com; connect-src 'self' https://www.google-analytics.com https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        },
        "continue": True,
    }


def test_404_page_is_branded() -> None:
    page = (ROOT / "src/404.html").read_text(encoding="utf-8")

    assert "<title>Page Not Found | Muffin Pan Recipes</title>" in page
    assert "Muffin Pan Recipes" in page
    assert "Return to the recipe collection" in page
    assert page.count("https://www.googletagmanager.com/gtag/js?id=G-05P73D3237") == 1
    assert page.count("gtag('config', 'G-05P73D3237')") == 1


def test_homepage_js_reserves_intrinsic_space_for_seed_and_generated_images() -> None:
    page = (ROOT / "src/index.html").read_text(encoding="utf-8")

    assert 'width="${imageDimension(recipe, \'image_width\')}"' in page
    assert "recipe.image.startsWith('assets/images/')" in page
    assert "return isSeedAsset ? 1024 : 1536;" in page


def test_every_static_dest_points_at_a_file_that_exists() -> None:
    """The PR #88 regression: a `dest` may not name an artifact nothing builds.

    PR #88 repointed five reader routes at `src/recipes/`, `src/this-week/` and
    `src/sitemap.xml`, none of which are ever generated — `vercel.json` has a
    top-level `builds` array, and Vercel ignores `buildCommand` whenever
    `builds` is present, so the build step that was meant to create them never
    ran. A missing artifact behind an explicit `dest` returns Vercel's raw
    NOT_FOUND rather than falling through to a later route, so all five 404'd.

    The pre-existing ordering test could not catch this: it asserts `src`
    values only and never looks at `dest`.
    """
    missing: list[str] = []
    for route in _routes():
        dest = route.get("dest", "")
        if not dest.startswith("/src/"):
            continue  # lambda, external rewrite, or no dest
        if "$" in dest:
            continue  # capture-group substitution; path is not statically known
        if not (ROOT / dest.lstrip("/")).exists():
            missing.append(f"{route.get('src')} -> {dest}")

    assert not missing, (
        "vercel.json routes point at static artifacts that do not exist and "
        "are never built; these return NOT_FOUND in production: " + "; ".join(missing)
    )


def test_reader_routes_are_served_by_the_lambda() -> None:
    """Reader routes must reach the FastAPI app, not a frozen static file.

    These five serve content the weekly cron writes to Blob. Pointing them at
    committed artifacts freezes them at whatever was last committed — that is
    how `/recipes.json` came to serve 10 seed recipes while the live catalog
    had 34, and how the dynamic sitemap from PR #55 was replaced by a file that
    PR #55 had deleted.
    """
    dests = {route["src"]: route.get("dest") for route in _routes()}

    for src in (
        "/recipes/([^/]+)$",
        "/recipes/?$",
        "/sitemap\\.xml",
        "/recipes\\.json",
        "/this-week/?$",
    ):
        assert src in dests, f"reader route {src!r} disappeared from vercel.json"
        assert dests[src] == "backend/admin/app.py", (
            f"reader route {src!r} must be served by the lambda, got {dests[src]!r}"
        )
