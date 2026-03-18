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

    Returns character name, message preview, stage, and link to full page.
    """
    teaser_json = storage.load_page("pages/latest.json")
    if teaser_json:
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


@router.get("/recipes/{slug}")
async def recipe_page(slug: str):
    """Serve an individual recipe page.

    Tries blob first (for cron-generated recipes),
    falls back to static src/recipes/{slug}/index.html (for the original 10).
    """
    # Try blob (cron-generated recipe pages)
    page = storage.load_page(f"pages/recipes/{slug}/index.html")
    if page:
        return HTMLResponse(content=page)

    # Fallback: static recipe page
    static = Path(__file__).resolve().parents[2] / "src" / "recipes" / slug / "index.html"
    if static.exists():
        return HTMLResponse(content=static.read_text(encoding="utf-8"))

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
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,700&family=Inter:wght@400;500&display=swap" rel="stylesheet">
</head>
<body class="text-gray-900 font-sans antialiased bg-white">
    <nav class="max-w-screen-xl mx-auto px-6 py-8">
        <a href="/" class="text-xs uppercase tracking-[0.3em] text-gray-400 font-bold hover:text-gray-900 transition-colors">
            &larr; Return to Library
        </a>
    </nav>
    <main class="max-w-screen-md mx-auto px-6 py-24 text-center">
        <p class="text-xs uppercase tracking-[0.3em] text-gray-400 font-bold mb-8">{episode_id}</p>
        <h1 class="font-serif text-5xl mb-8 italic" style="font-family: 'Playfair Display', serif">Something's Baking...</h1>
        <p class="text-gray-500 max-w-md mx-auto mb-12">The team hasn't started this week's recipe yet. Check back Monday morning when the brainstorm begins.</p>
        <a href="/" class="inline-block px-8 py-4 border-2 border-gray-900 font-bold uppercase tracking-widest text-xs hover:bg-gray-900 hover:text-white transition-all">
            Browse Recipes
        </a>
    </main>
</body>
</html>"""
