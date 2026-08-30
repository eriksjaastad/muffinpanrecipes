"""Render the small set of non-recipe pages emitted by the static build.

The recipe page itself lives in :mod:`episode_renderer`.  This module keeps
the catalog hub, sitemap, and empty-week page on the same build-time path so
the public reader never needs the FastAPI application to assemble HTML.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date

from backend.publishing.analytics import GA4_TAG
from backend.publishing.episode_renderer import SEO_DESCRIPTION_MAX_LENGTH, _seo_description

SITE_BASE = "https://muffinpanrecipes.com"
RECIPES_CANONICAL = f"{SITE_BASE}/recipes"
CATEGORY_ORDER = ["Breakfast", "Savory", "Sweet", "Party"]


def render_recipes_index(recipes: list[dict]) -> str:
    """Render the crawlable recipe library from the build catalog."""
    groups: dict[str, list[dict]] = {}
    for recipe in recipes:
        slug, title = recipe.get("slug"), recipe.get("title")
        if not slug or not title:
            continue
        category = (recipe.get("category") or "").strip() or "Other"
        if category.lower() == "dessert":
            category = "Sweet"
        groups.setdefault(category, []).append(
            {
                "slug": slug,
                "title": title,
                "description": recipe.get("description", ""),
            }
        )

    ordered = [category for category in CATEGORY_ORDER if category in groups]
    ordered += sorted(category for category in groups if category not in CATEGORY_ORDER)
    total = sum(len(items) for items in groups.values())

    sections, item_list, position = "", [], 0
    for category in ordered:
        cards = ""
        for item in sorted(groups[category], key=lambda value: value["title"].lower()):
            position += 1
            item_list.append(
                {
                    "@type": "ListItem",
                    "position": position,
                    "url": f"{SITE_BASE}/recipes/{item['slug']}",
                    "name": item["title"],
                }
            )
            cards += (
                f'                <li><a href="/recipes/{html.escape(item["slug"])}" '
                f'class="recipe-link">\n'
                f'                    <span class="recipe-link__title">'
                f'{html.escape(item["title"])}</span>\n'
                f'                    <span class="recipe-link__desc">'
                f'{html.escape(item["description"])}</span>\n'
                f"                </a></li>\n"
            )
        sections += (
            '        <section class="recipe-section">\n'
            f'            <h2 class="recipe-section__title">{html.escape(category)}</h2>\n'
            f'            <ul class="recipe-grid">\n{cards}            </ul>\n'
            "        </section>\n"
        )

    json_ld = json.dumps(
        {
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            "name": "All Muffin Pan Recipes",
            "url": RECIPES_CANONICAL,
            "mainEntity": {
                "@type": "ItemList",
                "numberOfItems": total,
                "itemListElement": item_list,
            },
        }
    )
    description = (
        "Browse every muffin-pan recipe — gourmet, mathematically-scaled "
        "single-serving Breakfast, Savory, Sweet, and Party bakes."
    )
    description = _seo_description(description, SEO_DESCRIPTION_MAX_LENGTH)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {GA4_TAG}
    <title>All Muffin Pan Recipes</title>
    <meta name="description" content="{html.escape(description)}">
    <link rel="canonical" href="{RECIPES_CANONICAL}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{RECIPES_CANONICAL}">
    <meta property="og:title" content="All Muffin Pan Recipes">
    <meta property="og:description" content="{html.escape(description)}">
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
            <h1 class="page-head__title">All Muffin Pan Recipes</h1>
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


def _week_lastmod(episode_id: str | None) -> str | None:
    """Convert an ISO week identifier into that week's Sunday."""
    if not episode_id:
        return None
    match = re.fullmatch(r"(\d{4})-W(\d{2})", str(episode_id))
    if not match:
        return None
    try:
        return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 7).isoformat()
    except ValueError:
        return None


def render_sitemap(recipes: list[dict]) -> str:
    """Render a deterministic sitemap from the exact catalog being built."""
    entries = [
        f"  <url><loc>{SITE_BASE}/</loc></url>",
        f"  <url><loc>{RECIPES_CANONICAL}</loc></url>",
        f"  <url><loc>{SITE_BASE}/this-week</loc></url>",
    ]
    for recipe in recipes:
        slug = recipe.get("slug")
        if not slug:
            continue
        lastmod = _week_lastmod(recipe.get("episode_id"))
        lastmod_tag = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        entries.append(
            f"  <url><loc>{SITE_BASE}/recipes/{html.escape(str(slug))}</loc>"
            f"{lastmod_tag}</url>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )


def render_placeholder_page(episode_id: str) -> str:
    """Render the static empty-week page used before Monday's build."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {GA4_TAG}
    <title>This Week's Recipe | Muffin Pan Recipes</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>&#x1f9c1;</text></svg>">
    <link rel="stylesheet" href="/assets/site.css">
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,700&family=Inter:wght@400;500&display=swap" rel="stylesheet">
</head>
<body>
    <nav class="site-nav">
        <a href="/" class="site-nav__back site-nav__back--muted">&larr; Return to Library</a>
    </nav>
    <main class="site-main placeholder">
        <p class="placeholder__eyebrow">{html.escape(episode_id)}</p>
        <h1 class="placeholder__title">Something's Baking...</h1>
        <p class="placeholder__body">The team hasn't started this week's recipe yet. Check back Monday morning when the brainstorm begins.</p>
        <a href="/" class="btn-outline">Browse Recipes</a>
    </main>
</body>
</html>"""
