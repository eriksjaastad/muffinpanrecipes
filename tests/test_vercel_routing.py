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

    assert routes[0]["has"] == [{"type": "host", "value": "www.muffinpanrecipes.com"}]
    assert routes[0]["status"] == 301
    assert routes[-2] == {"src": "/", "dest": "/src/index.html"}


def test_404_page_is_branded() -> None:
    page = (ROOT / "src/404.html").read_text(encoding="utf-8")

    assert "<title>Page Not Found | Muffin Pan Recipes</title>" in page
    assert "Muffin Pan Recipes" in page
    assert "Return to the recipe collection" in page
