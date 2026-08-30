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
