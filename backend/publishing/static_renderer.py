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
from backend.publishing.episode_renderer import SEO_DESCRIPTION_MAX_LENGTH, _seo_description, _to_webp_srcset

SITE_BASE = "https://muffinpanrecipes.com"
RECIPES_CANONICAL = f"{SITE_BASE}/recipes"
CATEGORY_ORDER = ["Breakfast", "Savory", "Sweet", "Party"]

# The collection page has no single recipe photo of its own, so it reuses the
# same brand image the homepage falls back to (#6822 — /recipes had no
# og:image at all; a shared card beats none). Dimensions match the source
# asset so social crawlers don't have to fetch it to lay out the preview.
_RECIPES_SOCIAL_IMAGE = f"{SITE_BASE}/assets/images/classic-blueberry-muffins.webp"
_RECIPES_SOCIAL_IMAGE_SIZE = 1024
_RECIPES_TITLE = "All Muffin Pan Recipes | Breakfast, Savory & Sweet"


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
    <title>{_RECIPES_TITLE}</title>
    <meta name="description" content="{html.escape(description)}">
    <link rel="canonical" href="{RECIPES_CANONICAL}">
    <meta property="og:type" content="website">
    <meta property="og:url" content="{RECIPES_CANONICAL}">
    <meta property="og:title" content="{_RECIPES_TITLE}">
    <meta property="og:description" content="{html.escape(description)}">
    <meta property="og:image" content="{_RECIPES_SOCIAL_IMAGE}">
    <meta property="og:image:width" content="{_RECIPES_SOCIAL_IMAGE_SIZE}">
    <meta property="og:image:height" content="{_RECIPES_SOCIAL_IMAGE_SIZE}">
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:image" content="{_RECIPES_SOCIAL_IMAGE}">
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>&#x1f9c1;</text></svg>">
    <link rel="stylesheet" href="/assets/site.css">
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
        <p class="site-footer__copy"><a href="/about">About</a> &middot; &copy; 2026 Muffin Pan Recipes</p>
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


# ── Homepage (#6821) ──────────────────────────────────────────────────────
#
# src/index.html used to be hand-authored, static markup: the hero and the
# recipe grid were both empty containers filled entirely by client-side JS
# from /recipes.json. A raw-HTML crawler (no JS execution) measured 43 words
# on the page — the least-content page on a site that otherwise renders
# 1400+ words server-side everywhere else, on the URL most likely to be a
# crawler's or a link-sharer's entry point.
#
# render_home bakes the exact same page (same head, nav, footer, styles,
# script) but with the featured recipe and grid populated server-side from
# the build's catalog. The client JS is unchanged in spirit: it still fetches
# /recipes.json on load and repaints both sections from the live catalog —
# every repaint is a full `.innerHTML =` replace (not an append), so it
# can't double the server-rendered cards it finds already sitting there,
# and a same-day cron publish still shows up without a rebuild.

_HOME_TITLE = "Muffin Pan Recipes | Editorial Cookery"
_HOME_DESCRIPTION = (
    "Gourmet, mathematically-scaled recipes designed exclusively for muffin "
    "tins. Breakfast, Savory, and Sweet single-serving meals."
)
_HOME_SOCIAL_IMAGE = f"{SITE_BASE}/assets/images/classic-blueberry-muffins.webp"


def _home_image_dims(recipe: dict) -> tuple[int, int]:
    """Mirror the client JS's `imageDimension()` fallback for missing sizes.

    Seed images are 1024px square; generated photography is 1536px square.
    The catalog stores real dimensions for current entries — this fallback
    only matters for older catalog records built before that field existed.
    """
    image = str(recipe.get("image") or "")
    is_seed_asset = "assets/images/" in image
    fallback = 1024 if is_seed_asset else 1536
    dims = []
    for key in ("image_width", "image_height"):
        value = recipe.get(key)
        dims.append(int(value) if isinstance(value, (int, float)) and value > 0 else fallback)
    return dims[0], dims[1]


def _home_featured_markup(recipe: dict) -> str:
    width, height = _home_image_dims(recipe)
    title = html.escape(str(recipe.get("title", "")))
    return f"""<a href="/recipes/{html.escape(str(recipe.get("slug", "")))}" class="featured-link">
                    <div class="featured__grid">
                        <div class="featured__image">
                            <img src="{html.escape(str(recipe.get("image", "")))}"{_srcset_attrs(recipe, "(max-width: 768px) 100vw, 720px")}
                                 width="{width}"
                                 height="{height}"
                                 onerror="handleImageError(this)"
                                 alt="{title}">
                        </div>
                        <div>
                            <p class="featured__eyebrow">Featured Recipe</p>
                            <h2 class="featured__title">{title}</h2>
                            <p class="featured__desc">{html.escape(str(recipe.get("description", "")))}</p>
                            <div class="featured__meta">
                                <span>⏱️ {html.escape(str(recipe.get("prep", "")))}</span>
                                <span>\U0001f525 {html.escape(str(recipe.get("cook", "")))}</span>
                                <span>\U0001f9c1 {html.escape(str(recipe.get("yield", "")))}</span>
                            </div>
                            <span class="featured__cta">View Recipe →</span>
                        </div>
                    </div>
                </a>"""


def _home_grid_card(recipe: dict) -> str:
    width, height = _home_image_dims(recipe)
    title = html.escape(str(recipe.get("title", "")))
    return f"""            <a href="/recipes/{html.escape(str(recipe.get("slug", "")))}" class="recipe-grid-card">
                <div class="recipe-grid-card__image">
                    <div class="recipe-grid-card__overlay"></div>
                    <img src="{html.escape(str(recipe.get("image", "")))}"{_srcset_attrs(recipe, "(max-width: 640px) 100vw, 400px")}
                         width="{width}"
                         height="{height}"
                         onerror="handleImageError(this)"
                         loading="lazy"
                         alt="{title}">
                </div>
                <p class="recipe-grid-card__category">{html.escape(str(recipe.get("category", "")))}</p>
                <h3 class="recipe-title">{title}</h3>
                <p class="recipe-grid-card__desc">{html.escape(str(recipe.get("description", "")))}</p>
            </a>"""


def _srcset_attrs(recipe: dict, sizes: str) -> str:
    """srcset/sizes for a catalog image when width variants exist (#6755).

    The catalog stores each hero as its .webp sibling; _to_webp_srcset derives
    the PNG, asks storage whether the 400w/800w variants exist and returns a
    single candidate when they do not — in which case no attribute is emitted,
    so a page never advertises a URL that would 404.
    """
    image = str(recipe.get("image") or "")
    if not image.lower().endswith(".webp"):
        return ""
    srcset = _to_webp_srcset(image)
    if "," not in srcset:
        return ""
    return f' srcset="{html.escape(srcset)}" sizes="{sizes}"'


def render_home(recipes: list[dict]) -> str:
    """Render the homepage with the hero and grid baked in from the catalog."""
    usable = [r for r in recipes if r.get("slug") and r.get("title")]
    featured = usable[0] if usable else None
    grid_items = usable[1:]

    featured_markup = (
        _home_featured_markup(featured)
        if featured
        else (
            '<div class="featured__grid" aria-hidden="true">\n'
            '                <div class="skeleton skeleton--img"></div>\n'
            "                <div>\n"
            '                    <div class="skeleton" style="height:0.75rem;width:8rem;margin-bottom:1.5rem"></div>\n'
            '                    <div class="skeleton" style="height:3rem;width:75%;margin-bottom:1.5rem"></div>\n'
            '                    <div class="skeleton skeleton--soft" style="height:1rem;width:100%;margin-bottom:0.75rem"></div>\n'
            '                    <div class="skeleton skeleton--soft" style="height:1rem;width:83.3333%;margin-bottom:2rem"></div>\n'
            '                    <div class="skeleton" style="height:3rem;width:11rem"></div>\n'
            "                </div>\n"
            "            "
        )
    )
    grid_markup = "\n".join(_home_grid_card(recipe) for recipe in grid_items)

    return f"""<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    {GA4_TAG}

    <title>{_HOME_TITLE}</title>
    <meta name="description" content="{html.escape(_HOME_DESCRIPTION)}">
    <link rel="canonical" href="{SITE_BASE}/">

    <!-- Open Graph / Facebook -->
    <meta property="og:type" content="website">
    <meta property="og:url" content="{SITE_BASE}/">
    <meta property="og:title" content="{_HOME_TITLE}">
    <meta property="og:description" content="{html.escape(_HOME_DESCRIPTION)}">
    <meta property="og:image" content="{_HOME_SOCIAL_IMAGE}">
    <meta property="og:image:width" content="1024">
    <meta property="og:image:height" content="1024">

    <!-- Twitter -->
    <meta name="twitter:card" content="summary_large_image">
    <meta name="twitter:url" content="{SITE_BASE}/">
    <meta name="twitter:title" content="{_HOME_TITLE}">
    <meta name="twitter:description" content="Gourmet, mathematically-scaled recipes designed exclusively for muffin tins.">
    <meta name="twitter:image" content="{_HOME_SOCIAL_IMAGE}">

    <!-- Structured data: site identity. Gives Google a stable WebSite +
         Organization entity to attach the brand to (knowledge-panel /
         brand-query signals) and links every Recipe page's publisher back
         here. Honest fields only — no logo/sameAs until those assets exist;
         no SearchAction since the site has no on-site search endpoint. -->
    <script type="application/ld+json">
    {{
      "@context": "https://schema.org",
      "@graph": [
        {{
          "@type": "WebSite",
          "@id": "{SITE_BASE}/#website",
          "url": "{SITE_BASE}/",
          "name": "Muffin Pan Recipes",
          "description": "Gourmet, mathematically-scaled recipes designed exclusively for muffin tins.",
          "inLanguage": "en-US",
          "publisher": {{ "@id": "{SITE_BASE}/#organization" }}
        }},
        {{
          "@type": "Organization",
          "@id": "{SITE_BASE}/#organization",
          "name": "Muffin Pan Recipes",
          "url": "{SITE_BASE}/"
        }}
      ]
    }}
    </script>

    <!-- Favicon -->
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>\U0001f9c1</text></svg>">
    <!-- Editorial Typography: Playfair Display for Serifs, Inter for Sans-Serif.
         Self-hosted via @font-face in site.css (#6433). -->
    <link rel="stylesheet" href="/assets/site.css">
</head>

<body class="home">

    <!-- Minimal Editorial Header -->
    <header class="home-header">
        <h1 class="home-header__title">Muffin Pan Recipes</h1>
        <p class="home-header__tagline">The Art of the Single Serving</p>
    </header>

    <!-- Category Filter -->
    <nav class="filter-nav">
        <div class="filter-nav__list" id="filter-buttons">
            <button class="filter-btn is-active" onclick="setFilter('All')">All</button>
            <button class="filter-btn" onclick="setFilter('Breakfast')">Breakfast</button>
            <button class="filter-btn" onclick="setFilter('Savory')">Savory</button>
            <button class="filter-btn" onclick="setFilter('Sweet')">Sweet</button>
            <button class="filter-btn" onclick="setFilter('Party')">Party</button>
        </div>
    </nav>

    <main class="site-main site-main--wide">

        <!-- Featured Recipe Hero — server-rendered from the build catalog
             (#6821); the JS below repaints this on load from the live
             /recipes.json, replacing (not appending to) this markup. -->
        <section id="featured-recipe" class="featured" aria-live="polite">
            {featured_markup}
        </section>


        <!-- This Week's Episode Teaser -->
        <section id="episode-teaser" class="teaser" hidden>
            <a href="/this-week" class="teaser__card">
                <div class="teaser__head">
                    <p class="teaser__stage" id="teaser-stage">This Week</p>
                    <span class="teaser__more">See the conversation &rarr;</span>
                </div>
                <h3 class="teaser__title" id="teaser-title"></h3>
                <div class="teaser__msg-row">
                    <div class="teaser__avatar" id="teaser-avatar"></div>
                    <div class="teaser__bubble">
                        <p id="teaser-message"></p>
                    </div>
                </div>
            </a>
        </section>

        <!-- Editorial Grid — server-rendered from the build catalog (#6821);
             the JS below repaints this on load from the live /recipes.json,
             replacing (not appending to) these cards. -->
        <h2 id="recipes-heading" class="recipes-heading">Recipes</h2>
        <div class="recipe-card-grid" id="recipe-grid">
{grid_markup}
        </div>

        <!-- Crawlable hub link: a real anchor so the recipe index is reachable
             without JS (the grid above is JS-refreshed). -->
        <div class="home-cta">
            <a href="/recipes" class="btn-outline">View All Recipes</a>
        </div>
    </main>

    <footer class="site-footer site-footer--home">
        <p class="site-footer__motto">Everything fits in a muffin pan.</p>
        <p class="site-footer__copy"><a href="/about">About</a> &middot; &copy; 2026 Muffin Pan Recipes</p>
    </footer>

    <!-- Simple JS to handle recipes and filtering. Runs after the page
         already has real content (see featured/grid markup above) — it
         refreshes both sections from the live catalog, it doesn't create
         them, so it must fully replace what's there rather than append. -->
    <script>
        let recipes = [];
        let retryCount = 0;
        const MAX_RETRIES = 3;

        async function loadRecipes() {{
            const grid = document.getElementById('recipe-grid');

            // Only show a loading placeholder if the grid is actually empty
            // (JS disabled/blocked JSON, or a retry after failure). The
            // normal case already has server-rendered cards in place, and
            // this must not blank them out while the refresh fetch is
            // in flight.
            if (retryCount === 0 && grid.children.length === 0) {{
                grid.innerHTML = `
                    <div class="grid-state">
                        <p class="grid-state__loading">Baking Recipes...</p>
                    </div>
                `;
            }}

            try {{
                const response = await fetch('recipes.json');

                if (!response.ok) {{
                    throw new Error(`HTTP error! status: ${{response.status}}`);
                }}

                const data = await response.json();
                recipes = data.recipes;
                retryCount = 0;
                renderGrid();
                // Render the hero here, not chained on the first loadRecipes()
                // call — that promise resolves before scheduled retries run,
                // so a retried success used to leave the hero empty (and now
                // would leave the skeleton pulsing forever).
                renderFeaturedRecipe();
            }} catch (error) {{
                console.error('Error loading recipes:', error);

                if (retryCount < MAX_RETRIES) {{
                    retryCount++;
                    console.log(`Retrying recipe load (${{retryCount}}/${{MAX_RETRIES}})...`);
                    setTimeout(loadRecipes, 1000 * retryCount);
                }} else if (grid.children.length === 0) {{
                    // Only show the error state if there is no server-rendered
                    // content to fall back on — a build-time catalog that's
                    // merely stale is still a better page than an error banner.
                    document.getElementById('featured-recipe').innerHTML = '';
                    grid.innerHTML = `
                        <div class="grid-state">
                            <p class="grid-state__error">Oven Error: Could not load recipes.</p>
                            <button onclick="retryLoad()" class="btn-outline">
                                Retry Baking
                            </button>
                        </div>
                    `;
                }}
            }}
        }}

        function retryLoad() {{
            retryCount = 0;
            loadRecipes();
        }}

        function handleImageError(img) {{
            const container = img.parentElement;
            img.style.display = 'none';
            const placeholder = document.createElement('div');
            placeholder.className = 'image-placeholder';
            placeholder.innerHTML = '<span>Photo Coming Soon</span>';
            container.appendChild(placeholder);
        }}

        function getFeaturedRecipe() {{
            return recipes.length > 0 ? recipes[0] : null;
        }}

        // Build recipe URLs by concatenating the path and slug rather than
        // writing the full path-plus-placeholder as one template token.
        // Googlebot's link-discovery pass scrapes URL-shaped string literals
        // straight out of inline scripts: an un-interpolated recipe href got
        // crawled as a real path and 404'd (Search Console, first detected
        // 6/13/26). Splitting path from slug keeps rendered output identical
        // while leaving no crawlable URL literal in the source.
        function recipeHref(slug) {{
            return '/recipes/' + slug;
        }}

        function imageDimension(recipe, key) {{
            const value = Number(recipe[key]);
            if (Number.isFinite(value) && value > 0) return value;
            // Seed images are 1024px square; generated photography is 1536px
            // square. The catalog stores these values for new entries, while
            // this fallback keeps older catalog records valid during rollout.
            const isSeedAsset = recipe.image && (
                recipe.image.startsWith('assets/images/') ||
                recipe.image.includes('/assets/images/')
            );
            return isSeedAsset ? 1024 : 1536;
        }}

        function renderGrid(filter = 'All') {{
            const grid = document.getElementById('recipe-grid');
            grid.innerHTML = '';

            const featured = getFeaturedRecipe();
            recipes
                .filter(r => r !== featured)
                .filter(r => filter === 'All' || r.category === filter)
                .forEach(recipe => {{
                const card = `
                    <a href="${{recipeHref(recipe.slug)}}" class="recipe-grid-card">
                        <div class="recipe-grid-card__image">
                            <div class="recipe-grid-card__overlay"></div>
                            <img src="${{recipe.image}}"
                                 width="${{imageDimension(recipe, 'image_width')}}"
                                 height="${{imageDimension(recipe, 'image_height')}}"
                                 onerror="handleImageError(this)"
                                 loading="lazy"
                                 alt="${{recipe.title}}">
                        </div>
                        <p class="recipe-grid-card__category">${{recipe.category}}</p>
                        <h3 class="recipe-title">${{recipe.title}}</h3>
                        <p class="recipe-grid-card__desc">${{recipe.description}}</p>
                    </a>
                `;
                grid.innerHTML += card;
            }});
        }}

        function setFilter(filter) {{
            renderGrid(filter);
            const buttons = document.querySelectorAll('#filter-buttons button');
            buttons.forEach(btn => {{
                if (btn.innerText.toLowerCase() === filter.toLowerCase()) {{
                    btn.classList.add('is-active');
                }} else {{
                    btn.classList.remove('is-active');
                }}
            }});
            // The grid lives well below the fold on phones. Without this
            // scroll a filter tap changes nothing on screen, which reads
            // as "the navigation doesn't work".
            document.getElementById('recipes-heading')?.scrollIntoView({{ behavior: 'smooth' }});
        }}

        function renderFeaturedRecipe() {{
            if (recipes.length === 0) return;

            // Pick the first recipe as featured (or you can randomize, or pick by slug)
            const featured = getFeaturedRecipe();
            const featuredSection = document.getElementById('featured-recipe');

            featuredSection.innerHTML = `
                <a href="${{recipeHref(featured.slug)}}" class="featured-link">
                    <div class="featured__grid">
                        <div class="featured__image">
                            <img src="${{featured.image}}"
                                 width="${{imageDimension(featured, 'image_width')}}"
                                 height="${{imageDimension(featured, 'image_height')}}"
                                 onerror="handleImageError(this)"
                                 alt="${{featured.title}}">
                        </div>
                        <div>
                            <p class="featured__eyebrow">Featured Recipe</p>
                            <h2 class="featured__title">${{featured.title}}</h2>
                            <p class="featured__desc">${{featured.description}}</p>
                            <div class="featured__meta">
                                <span>⏱️ ${{featured.prep}}</span>
                                <span>\U0001f525 ${{featured.cook}}</span>
                                <span>\U0001f9c1 ${{featured.yield}}</span>
                            </div>
                            <span class="featured__cta">View Recipe →</span>
                        </div>
                    </div>
                </a>
            `;
        }}

        // Character avatar colors (must match episode_renderer.py)
        const AVATAR_STYLES = {{
            margaret: {{ bg: '#C9B99A', fg: '#5C4E2F' }},
            marcus: {{ bg: '#9AABBF', fg: '#2F3D5C' }},
            steph: {{ bg: '#BF9AB5', fg: '#5C2F4E' }},
            julian: {{ bg: '#9ABFA3', fg: '#2F5C3D' }},
            devon: {{ bg: '#ABABAB', fg: '#3D3D3D' }},
            ria: {{ bg: '#FFFFFF', fg: '#2A2A2A' }},
        }};

        async function loadTeaser() {{
            try {{
                const resp = await fetch('/api/episodes/teaser');
                if (!resp.ok) return;
                const data = await resp.json();
                if (data.status === 'no_episode' || !data.title) return;

                document.getElementById('teaser-stage').innerHTML = data.stage_label || 'This Week';
                document.getElementById('teaser-title').textContent = data.title;
                document.getElementById('teaser-message').textContent = data.message_preview;

                const avatar = document.getElementById('teaser-avatar');
                const style = AVATAR_STYLES[data.character_slug] || AVATAR_STYLES.devon;
                avatar.style.backgroundColor = style.bg;
                avatar.style.color = style.fg;
                avatar.textContent = data.character_initials || '??';

                document.getElementById('episode-teaser').hidden = false;
            }} catch (e) {{
                // Silently fail — teaser is non-critical
            }}
        }}

        // Initial Load — renderFeaturedRecipe runs inside loadRecipes'
        // success path so retried loads also render the hero.
        loadRecipes();
        loadTeaser();
    </script>
</body>

</html>
"""
