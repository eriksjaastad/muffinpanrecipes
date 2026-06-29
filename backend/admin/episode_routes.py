"""Public routes for serving episode pages, recipe pages, and teaser data.

These endpoints serve content from Vercel Blob to the public site.
No authentication required — the content is public.

Routes:
  /this-week           — Serves the current episode page HTML (progressive)
  /api/episodes/teaser — Returns teaser JSON for the main page
  /recipes.json        — Dynamic recipe catalog (blob-first, static fallback)
  /recipes/{slug}      — Individual recipe pages (blob-first, static fallback)
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, JSONResponse

from backend.storage import storage
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Two routers: one for the public page, one for the API
router = APIRouter(tags=["episodes"])


def _current_episode_id() -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@router.get("/this-week")
async def this_week_page():
    """Serve the current week's episode page HTML.

    The page is pre-rendered by each cron stage and stored in blob at
    pages/{episode_id}/index.html. Progressively grows through the week.
    """
    episode_id = _current_episode_id()
    page_html = storage.load_page(f"pages/{episode_id}/index.html")
    if page_html:
        return HTMLResponse(content=page_html)

    return HTMLResponse(content=_placeholder_page(episode_id), status_code=200)


@router.get("/api/episodes/teaser")
async def get_episode_teaser():
    """Return the latest episode teaser JSON for the main page.

    Once the Sunday stage completes, the recipe becomes the homepage
    Featured hero (top of recipes.json), so the teaser is suppressed
    here at read time. This is the authoritative check — keeping it
    on the read side means a code deploy is enough to fix prod even
    if a stale `pages/latest.json` blob still says `stage: sunday`.
    """
    teaser_json = storage.load_page("pages/latest.json")
    if teaser_json:
        try:
            data = json.loads(teaser_json)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict) and data.get("stage") == "sunday":
            return JSONResponse(content={"status": "published"}, status_code=200)
        return Response(content=teaser_json, media_type="application/json")

    return JSONResponse(content={"status": "no_episode"}, status_code=200)


@router.get("/recipes.json")
async def recipes_json():
    """Serve the recipe catalog JSON.

    Tries blob first (dynamically updated by Sunday cron),
    falls back to the static src/recipes.json for pre-cron compatibility.
    """
    content = storage.load_page("pages/recipes.json")
    if content:
        return Response(content=content, media_type="application/json")

    # Fallback: static seed file
    static = Path(__file__).resolve().parents[2] / "src" / "recipes.json"
    if static.exists():
        return Response(content=static.read_text(), media_type="application/json")

    return JSONResponse(content={"recipes": []}, status_code=200)


_SITE_BASE = "https://muffinpanrecipes.com"


@router.get("/sitemap.xml")
async def sitemap_xml():
    """Serve a sitemap generated from the live recipe catalog.

    The old static src/sitemap.xml froze at the 10 seed recipes — every
    cron-published recipe since W10 was invisible to crawlers (and one
    listed URL didn't exist at all). Building from the catalog keeps the
    sitemap in lockstep with what Sunday actually publishes.
    """
    import re as _re
    from datetime import date

    catalog_raw = storage.load_page("pages/recipes.json")
    if not catalog_raw:
        static = Path(__file__).resolve().parents[2] / "src" / "recipes.json"
        catalog_raw = static.read_text() if static.exists() else '{"recipes": []}'

    try:
        recipes = json.loads(catalog_raw).get("recipes", [])
    except Exception:
        logger.error("sitemap: catalog JSON unparseable, emitting site roots only")
        recipes = []

    def _week_lastmod(episode_id: str | None) -> str | None:
        """ISO week id (2026-W23) → that week's Sunday (publish day)."""
        if not episode_id:
            return None
        m = _re.fullmatch(r"(\d{4})-W(\d{2})", episode_id)
        if not m:
            return None
        year, week = int(m.group(1)), int(m.group(2))
        try:
            return date.fromisocalendar(year, week, 7).isoformat()
        except ValueError:
            return None

    # No <changefreq>: Google ignores it. <lastmod> on recipe URLs is the
    # signal that actually matters.
    entries = [
        f"  <url><loc>{_SITE_BASE}/</loc></url>",
        f"  <url><loc>{_SITE_BASE}/recipes</loc></url>",
        f"  <url><loc>{_SITE_BASE}/this-week</loc></url>",
    ]
    for r in recipes:
        slug = r.get("slug")
        if not slug:
            continue
        lastmod = _week_lastmod(r.get("episode_id"))
        lastmod_tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        entries.append(
            f"  <url><loc>{_SITE_BASE}/recipes/{slug}</loc>{lastmod_tag}</url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


_RECIPES_CANONICAL = f"{_SITE_BASE}/recipes"
# Canonical display order; any unexpected category falls in alphabetically after.
_CATEGORY_ORDER = ["Breakfast", "Savory", "Sweet", "Party"]


def _load_catalog_recipes() -> list:
    """Recipe catalog, blob-first with the static seed file as fallback."""
    raw = storage.load_page("pages/recipes.json")
    if not raw:
        static = Path(__file__).resolve().parents[2] / "src" / "recipes.json"
        raw = static.read_text() if static.exists() else '{"recipes": []}'
    try:
        data = json.loads(raw)
    except Exception:
        logger.error("recipes index: catalog JSON unparseable")
        return []
    return data if isinstance(data, list) else data.get("recipes", [])


def _render_recipes_index(recipes: list) -> str:
    """Server-rendered hub page: every recipe as a real crawlable <a href>,
    grouped by category, built live from the catalog (no JS needed)."""
    import html as _html

    groups: dict[str, list] = {}
    for r in recipes:
        slug, title = r.get("slug"), r.get("title")
        if not slug or not title:
            continue
        cat = (r.get("category") or "").strip() or "Other"
        if cat.lower() == "dessert":  # taxonomy stray -> Sweet
            cat = "Sweet"
        groups.setdefault(cat, []).append(
            {"slug": slug, "title": title, "description": r.get("description", "")}
        )

    ordered = [c for c in _CATEGORY_ORDER if c in groups] + sorted(
        c for c in groups if c not in _CATEGORY_ORDER
    )
    total = sum(len(v) for v in groups.values())

    sections, item_list, pos = "", [], 0
    for cat in ordered:
        cards = ""
        for it in sorted(groups[cat], key=lambda x: x["title"].lower()):
            pos += 1
            item_list.append({
                "@type": "ListItem", "position": pos,
                "url": f"{_SITE_BASE}/recipes/{it['slug']}", "name": it["title"],
            })
            cards += (
                f'                <li><a href="/recipes/{it["slug"]}" class="recipe-link">\n'
                f'                    <span class="recipe-link__title">{_html.escape(it["title"])}</span>\n'
                f'                    <span class="recipe-link__desc">{_html.escape(it["description"])}</span>\n'
                f'                </a></li>\n'
            )
        sections += (
            f'        <section class="recipe-section">\n'
            f'            <h2 class="recipe-section__title">{_html.escape(cat)}</h2>\n'
            f'            <ul class="recipe-grid">\n{cards}            </ul>\n'
            f'        </section>\n'
        )

    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "CollectionPage",
        "name": "All Recipes — Muffin Pan Recipes",
        "url": _RECIPES_CANONICAL,
        "mainEntity": {
            "@type": "ItemList", "numberOfItems": total, "itemListElement": item_list,
        },
    })
    desc = "Browse every muffin-pan recipe — gourmet, mathematically-scaled single-serving Breakfast, Savory, Sweet, and Party bakes."

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>All Recipes | Muffin Pan Recipes</title>
    <meta name="description" content="{desc}">
    <link rel="canonical" href="{_RECIPES_CANONICAL}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{_RECIPES_CANONICAL}">
    <meta property="og:title" content="All Recipes | Muffin Pan Recipes">
    <meta property="og:description" content="{desc}">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>&#x1f9c1;</text></svg>">
    <link rel="stylesheet" href="/assets/site.css">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script type="application/ld+json">{json_ld}</script>
</head>
<body>
    <nav class="site-nav">
        <a href="/" class="site-nav__back">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" /></svg>
            Home
        </a>
    </nav>
    <main class="site-main">
        <div class="page-head">
            <p class="page-head__eyebrow">The Library</p>
            <h1 class="page-head__title">All Recipes</h1>
            <p class="page-head__sub">{total} muffin-pan recipes, scaled for the tin and grouped by occasion.</p>
        </div>
{sections}    </main>
    <footer class="site-footer">
        <p class="site-footer__motto">The struggle is the story.</p>
        <p class="site-footer__copy">&copy; 2026 Muffin Pan Recipes</p>
    </footer>
</body>
</html>
"""


@router.get("/recipes")
async def recipes_index():
    """Server-rendered, crawlable hub linking to every recipe (internal-linking
    pass 2). Built live from the catalog so it's always current with zero
    weekly maintenance. Replaces the old bare-/recipes = JS homepage duplicate."""
    return HTMLResponse(content=_render_recipes_index(_load_catalog_recipes()))


_SEED_RECIPES_CACHE: dict | None = None


def _load_seed_recipes() -> dict:
    """Load the original 10 recipes' data (src/seed_recipes.json), cached.

    These replace the hand-coded static HTML pages — they're rendered
    through the same renderer as cron recipes so there is one source of
    truth for recipe-page structure and SEO.
    """
    global _SEED_RECIPES_CACHE
    cached = _SEED_RECIPES_CACHE
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parents[2] / "src" / "seed_recipes.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        # Do NOT cache the failure — a missing/corrupt file is a deploy bug
        # that should retry (and keep logging loudly) on the next request,
        # not silently 404 every seed recipe for the Lambda's lifetime.
        logger.error(f"seed recipes load failed (will retry next request): {exc}")
        return {}
    _SEED_RECIPES_CACHE = data
    return data


@router.get("/recipes/{slug}")
async def recipe_page(slug: str):
    """Serve an individual recipe page.

    Blob first (cron-generated recipes), then the original seed recipes
    rendered from data through the shared renderer.
    """
    # Try blob (cron-generated recipe pages)
    page = storage.load_page(f"pages/recipes/{slug}/index.html")
    if page:
        return HTMLResponse(content=page)

    # Seed recipes — rendered from src/seed_recipes.json (single renderer).
    seed = _load_seed_recipes().get(slug)
    if seed:
        from backend.publishing.episode_renderer import render_seed_recipe_page
        html = render_seed_recipe_page(seed["recipe_data"], seed.get("image", ""), slug)
        return HTMLResponse(content=html)

    return HTMLResponse(
        content="<h1>Recipe not found</h1>",
        status_code=404,
    )


def _placeholder_page(episode_id: str) -> str:
    """Simple placeholder when no episode has started this week."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>This Week's Recipe | Muffin Pan Recipes</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>&#x1f9c1;</text></svg>">
    <link rel="stylesheet" href="/assets/site.css">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,700&family=Inter:wght@400;500&display=swap" rel="stylesheet">
</head>
<body>
    <nav class="site-nav">
        <a href="/" class="site-nav__back site-nav__back--muted">
            &larr; Return to Library
        </a>
    </nav>
    <main class="site-main placeholder">
        <p class="placeholder__eyebrow">{episode_id}</p>
        <h1 class="placeholder__title">Something's Baking...</h1>
        <p class="placeholder__body">The team hasn't started this week's recipe yet. Check back Monday morning when the brainstorm begins.</p>
        <a href="/" class="btn-outline">
            Browse Recipes
        </a>
    </main>
</body>
</html>"""
