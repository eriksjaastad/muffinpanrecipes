"""Render episode JSON into a viewable HTML recipe page.

Called after each cron stage to regenerate the page with the latest content.
The page is uploaded to Vercel Blob and served via a rewrite rule.

Progressive rendering:
  - Monday: Title + brainstorm conversation, "Photo coming Wednesday" placeholder
  - Tuesday: + recipe development conversation
  - Wednesday: + hero image + photography conversation with inline shots
  - Thursday-Saturday: + additional conversations
  - Sunday: Full recipe page with card, ingredients, instructions, all conversations
"""

from __future__ import annotations

import html
import json
import os
from typing import Optional

from backend.storage import storage
from backend.utils.logging import get_logger
from backend.utils.text_sanitize import sanitize_text

logger = get_logger(__name__)


# Character metadata for chat bubble styling
CHARACTERS = {
    "Margaret Chen": {"slug": "margaret", "initials": "MC", "role": "Head Baker"},
    "Marcus Reid": {"slug": "marcus", "initials": "MR", "role": "Copywriter"},
    "Stephanie 'Steph' Whitmore": {"slug": "steph", "initials": "SW", "role": "Project Manager"},
    "Steph Whitmore": {"slug": "steph", "initials": "SW", "role": "Project Manager"},
    "Julian Torres": {"slug": "julian", "initials": "JT", "role": "Art Director"},
    "Devon Park": {"slug": "devon", "initials": "DP", "role": "Site Architect"},
}

STAGE_LABELS = {
    "monday": "Monday &middot; Brainstorm",
    "tuesday": "Tuesday &middot; Recipe Development",
    "wednesday": "Wednesday &middot; Photography",
    "thursday": "Thursday &middot; Copywriting",
    "friday": "Friday &middot; Final Review",
    "saturday": "Saturday &middot; Deployment",
    "sunday": "Sunday &middot; Published",
}

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

BLOB_CDN_PREFIX = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/"


def _to_local_image_url(blob_url: str) -> str:
    """Convert a raw blob CDN URL to a relative /blob-images/ path.

    e.g. https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/a9b98b08/round_1/hero.png
      -> /blob-images/a9b98b08/round_1/hero.png

    Also handles old-format URLs without store ID prefix:
    https://blob.vercel-storage.com/images/... -> /blob-images/...
    """
    if not blob_url:
        return blob_url
    if blob_url.startswith(BLOB_CDN_PREFIX):
        return "/blob-images/" + blob_url[len(BLOB_CDN_PREFIX):]
    # Old-format URLs (W10 era) without store ID
    old_prefix = "https://blob.vercel-storage.com/images/"
    if blob_url.startswith(old_prefix):
        return "/blob-images/" + blob_url[len(old_prefix):]
    return blob_url


def _char_info(name: str) -> dict:
    """Look up character info, falling back to generic."""
    for key, info in CHARACTERS.items():
        if key.lower() in name.lower() or name.lower() in key.lower():
            return {**info, "name": name}
    slug = name.lower().split()[0] if name else "unknown"
    initials = "".join(w[0].upper() for w in name.split()[:2]) if name else "??"
    return {"slug": slug, "initials": initials, "name": name, "role": "Team Member"}


def _render_chat_message(msg: dict, image_url_map: dict[str, str] | None = None) -> str:
    """Render a single chat message as HTML."""
    char_name = msg.get("character", "Unknown")
    message = html.escape(sanitize_text(msg.get("message", "")))
    info = _char_info(char_name)
    slug = info["slug"]
    attachments = msg.get("attachments", [])

    images_html = ""
    if attachments and image_url_map:
        img_tags = []
        for att in attachments:
            url = image_url_map.get(att, "")
            if url:
                img_tags.append(
                    f'<img src="{html.escape(url)}" class="rounded-lg w-full max-w-sm" '
                    f'alt="Photography option" loading="lazy">'
                )
        if img_tags:
            images_html = f'<div class="flex flex-wrap gap-3 mt-3">{"".join(img_tags)}</div>'

    role = info.get("role", "")
    role_html = f' <span class="text-gray-300">&middot;</span> <span class="text-gray-300">{html.escape(role)}</span>' if role else ""

    return f"""
            <div class="flex items-start gap-3">
                <div class="avatar avatar-{slug} mt-1">{info['initials']}</div>
                <div>
                    <p class="text-[11px] text-gray-400 mb-1">{html.escape(char_name)}{role_html}</p>
                    <div class="chat-bubble chat-{slug} rounded-2xl rounded-tl-sm px-4 py-3">
                        <p class="text-sm leading-relaxed">{message}</p>{images_html}
                    </div>
                </div>
            </div>"""


def _build_image_url_map(episode: dict) -> dict[str, str]:
    """Build mapping from relative image paths to blob CDN URLs."""
    wed = episode.get("stages", {}).get("wednesday", {})
    paths = wed.get("image_paths", []) or episode.get("image_paths", [])
    urls = wed.get("image_urls", []) or episode.get("image_urls", [])

    url_map = {}
    for path, url in zip(paths, urls):
        if path and url:
            url_map[path] = _to_local_image_url(url)
    return url_map


def _render_conversation_section(episode: dict) -> str:
    """Render the full week's conversation as HTML sections."""
    sections = []
    has_any_dialogue = False
    image_url_map = _build_image_url_map(episode)

    for day in DAYS:
        stage = episode.get("stages", {}).get(day, {})
        dialogue = stage.get("dialogue", [])
        if not dialogue:
            continue

        has_any_dialogue = True
        label = STAGE_LABELS.get(day, day.title())
        messages_html = "\n".join(_render_chat_message(m, image_url_map) for m in dialogue)

        sections.append(f"""
        <div class="stage-divider mb-8">
            <span class="text-xs uppercase tracking-[0.3em] text-sage font-bold whitespace-nowrap">{label}</span>
        </div>

        <div class="space-y-4 mb-20">
{messages_html}
        </div>""")

    if not has_any_dialogue:
        return """
        <div class="text-center py-16 text-gray-400">
            <p class="font-serif text-2xl italic mb-2">The conversation hasn't started yet.</p>
            <p class="text-sm">Check back as the team develops this recipe throughout the week.</p>
        </div>"""

    return "\n".join(sections)


def render_episode_page(episode: dict, image_url: Optional[str] = None) -> str:
    """Generate the full recipe page HTML from episode data.

    This is the main entry point. Called after each cron stage to
    regenerate the page with whatever content exists so far.
    """
    concept = episode.get("concept", "Muffin Pan Recipe")
    episode_id = episode.get("episode_id", "unknown")

    # Extract recipe data from Monday stage
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    title = sanitize_text(recipe.get("title", concept))
    description = sanitize_text(recipe.get("description", ""))
    category = recipe.get("category", "savory").title()
    prep_time = recipe.get("prep_time", 15)
    cook_time = recipe.get("cook_time", 20)
    servings = recipe.get("servings", 12)
    difficulty = recipe.get("difficulty", "medium").title()
    ingredients = recipe.get("ingredients", [])
    instructions = recipe.get("instructions", [])
    chef_notes = sanitize_text(recipe.get("chef_notes", ""))

    # Image — use provided URL, or fall back to episode data
    if not image_url:
        image_urls = episode.get("image_urls", [])
        image_url = image_urls[0] if image_urls else ""
    if image_url:
        image_url = _to_local_image_url(image_url)

    # Determine what stage we're at (for progressive rendering)
    completed_stages = [d for d in DAYS if episode.get("stages", {}).get(d, {}).get("status") == "complete"]
    is_published = "sunday" in completed_stages
    has_recipe = bool(ingredients and instructions)
    has_image = bool(image_url)

    # Render ingredients and instructions
    ingredients_html = ""
    for ing in ingredients:
        amount = ing.get("amount", "") if isinstance(ing, dict) else ""
        item = ing.get("item", str(ing)) if isinstance(ing, dict) else str(ing)
        notes = ing.get("notes", "") if isinstance(ing, dict) else ""
        text = f"{amount} {item}".strip()
        if notes:
            text += f" ({notes})"
        ingredients_html += f'<li class="border-b border-gray-50 pb-2">{html.escape(sanitize_text(text))}</li>\n'

    instructions_html = ""
    for i, step in enumerate(instructions):
        step_text = step if isinstance(step, str) else str(step)
        instructions_html += (
            f'<li class="flex gap-4">'
            f'<span class="font-serif text-terracotta font-bold italic text-xl">{i + 1:02d}</span>'
            f'<span>{html.escape(sanitize_text(step_text))}</span></li>\n'
        )

    # Image block
    if has_image:
        image_block = f'<img src="{html.escape(image_url)}" class="w-full h-full object-cover" alt="{html.escape(title)}">'
    else:
        image_block = '<div class="flex items-center justify-center h-full text-gray-400 font-serif italic text-xl">Photo coming Wednesday</div>'

    # Chef notes
    chef_notes_html = ""
    if chef_notes:
        chef_notes_html = f"""
            <div class="mt-12 pt-8 border-t border-gray-100">
                <h3 class="font-serif text-xl mb-4 italic text-sage">Chef's Notes</h3>
                <p class="text-gray-600 leading-relaxed">{html.escape(chef_notes)}</p>
            </div>"""

    # Conversation section (feature-flagged)
    show_bts = os.environ.get("ENABLE_BEHIND_THE_SCENES", "true").lower() != "false"
    conversation_html = _render_conversation_section(episode) if show_bts else ""

    # Recipe card — only show full card when we have ingredients
    recipe_card_html = ""
    if has_recipe:
        recipe_card_html = f"""
        <!-- THE RECIPE CARD -->
        <div id="recipe-card" class="bg-linen border border-gray-100 p-8 md:p-12 mt-16 shadow-2xl shadow-gray-200/50">
            <div class="flex flex-wrap justify-between items-center border-b border-gray-100 pb-8 mb-12 gap-6">
                <div class="text-center px-4">
                    <span class="block text-[10px] uppercase tracking-widest text-sage font-bold mb-1">Prep</span>
                    <span class="text-xl font-medium">{prep_time} mins</span>
                </div>
                <div class="text-center px-4">
                    <span class="block text-[10px] uppercase tracking-widest text-sage font-bold mb-1">Cook</span>
                    <span class="text-xl font-medium">{cook_time} mins</span>
                </div>
                <div class="text-center px-4">
                    <span class="block text-[10px] uppercase tracking-widest text-sage font-bold mb-1">Yield</span>
                    <span class="text-xl font-medium">{servings} servings</span>
                </div>
                <div class="text-center px-4">
                    <span class="block text-[10px] uppercase tracking-widest text-sage font-bold mb-1">Difficulty</span>
                    <span class="text-xl font-medium">{html.escape(difficulty)}</span>
                </div>
            </div>

            <div class="mb-12">
                <h3 class="font-serif text-2xl mb-8 italic border-l-4 border-terracotta pl-4 uppercase tracking-tighter">Ingredients</h3>
                <ul class="grid md:grid-cols-2 gap-x-12 gap-y-4 list-none p-0 text-gray-700">
                    {ingredients_html}
                </ul>
            </div>

            <div class="border-t border-gray-100 pt-12">
                <h3 class="font-serif text-2xl mb-8 italic border-l-4 border-terracotta pl-4 uppercase tracking-tighter">Instructions</h3>
                <ol class="space-y-8 list-none p-0 text-gray-700 max-w-2xl">
                    {instructions_html}
                </ol>
            </div>
{chef_notes_html}
        </div>"""

    # Progress indicator showing which days are done
    progress_dots = ""
    for day in DAYS:
        short = day[:3].title()
        done = day in completed_stages
        color = "bg-terracotta text-white" if done else "bg-gray-100 text-gray-400"
        progress_dots += f'<div class="text-center"><div class="w-8 h-8 rounded-full {color} flex items-center justify-center text-[10px] font-bold mx-auto mb-1">{short[:2]}</div><p class="text-[9px] text-gray-400">{short}</p></div>\n'

    # JSON-LD (only on published pages with full recipe data)
    json_ld_html = ""
    if is_published and has_recipe:
        ld_ingredients = [
            f"{ing.get('amount', '')} {ing.get('item', '')}".strip()
            if isinstance(ing, dict) else str(ing)
            for ing in ingredients
        ]
        ld_json = json.dumps({
            "@context": "https://schema.org/",
            "@type": "Recipe",
            "name": title,
            "description": description,
            "prepTime": f"PT{prep_time}M",
            "cookTime": f"PT{cook_time}M",
            "recipeYield": f"{servings} servings",
            "recipeCategory": category,
            "recipeIngredient": ld_ingredients,
            "recipeInstructions": [
                {"@type": "HowToStep", "text": s} for s in instructions
            ],
        })
        json_ld_html = f'<script type="application/ld+json">{ld_json}</script>'

    # Status banner for in-progress episodes
    status_banner = ""
    if not is_published:
        current_day = completed_stages[-1].title() if completed_stages else "Starting"
        status_banner = f"""
        <div class="bg-linen border border-gray-200 rounded-lg p-4 mb-8 text-center">
            <p class="text-sm text-sage"><span class="font-bold">In Progress</span> &mdash; Last updated: {html.escape(current_day)}</p>
        </div>"""

    title_escaped = html.escape(title)
    desc_escaped = html.escape(description)

    bts_jump = ""
    bts_section = ""
    if show_bts:
        bts_jump = '<a href="#behind-the-scenes" class="block w-full py-8 border-4 border-gray-900 font-bold uppercase tracking-[0.4em] text-sm hover:bg-gray-900 hover:text-white transition-all transform active:scale-[0.98]">Jump to Behind the Scenes</a>'
        bts_section = f"""<!-- BEHIND THE SCENES -->
        <div id="behind-the-scenes" class="mt-24">
            <div class="text-center mb-16">
                <p class="text-xs uppercase tracking-[0.3em] text-terracotta font-bold mb-4">Behind the Scenes</p>
                <h2 class="font-serif text-4xl mb-4 italic">How This Recipe Was Made</h2>
                <p class="text-gray-500 max-w-lg mx-auto">Follow the creative team's conversation as they developed, photographed, and published this recipe.</p>
            </div>

            <div class="flex justify-center gap-4 mb-8">
                {progress_dots}
            </div>

            <div class="flex justify-center gap-6 mb-16">
                <div class="text-center">
                    <div class="avatar avatar-margaret mx-auto mb-2">MC</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage font-bold">Margaret</p>
                    <p class="text-[9px] text-gray-400">Head Baker</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-marcus mx-auto mb-2">MR</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage font-bold">Marcus</p>
                    <p class="text-[9px] text-gray-400">Copywriter</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-steph mx-auto mb-2">SW</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage font-bold">Steph</p>
                    <p class="text-[9px] text-gray-400">Project Manager</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-julian mx-auto mb-2">JT</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage font-bold">Julian</p>
                    <p class="text-[9px] text-gray-400">Art Director</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-devon mx-auto mb-2">DP</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage font-bold">Devon</p>
                    <p class="text-[9px] text-gray-400">Site Architect</p>
                </div>
            </div>

{conversation_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{desc_escaped}">
    <title>{title_escaped} | Muffin Pan Recipes</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>&#x1f9c1;</text></svg>">

    <meta property="og:type" content="article">
    <meta property="og:title" content="{title_escaped} | Muffin Pan Recipes">
    <meta property="og:description" content="{desc_escaped}">

    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,700;1,400;1,700&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    fontFamily: {{
                        serif: ['"Playfair Display"', 'serif'],
                        sans: ['Inter', 'sans-serif'],
                    }},
                    colors: {{
                        sage: '#717D7E',
                        terracotta: '#C5705D',
                        linen: '#F9F7F2',
                    }}
                }}
            }}
        }}
    </script>
    <style>
        .chat-bubble {{ max-width: 85%; position: relative; }}
        .chat-margaret {{ background-color: #F0EDE6; }}
        .chat-marcus {{ background-color: #E8EDF2; }}
        .chat-steph {{ background-color: #F2EBF0; }}
        .chat-julian {{ background-color: #E6EDE8; }}
        .chat-devon {{ background-color: #EDEDED; }}
        .avatar {{
            width: 36px; height: 36px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 14px; font-weight: 700; flex-shrink: 0;
        }}
        .avatar-margaret {{ background: #C9B99A; color: #5C4E2F; }}
        .avatar-marcus {{ background: #9AABBF; color: #2F3D5C; }}
        .avatar-steph {{ background: #BF9AB5; color: #5C2F4E; }}
        .avatar-julian {{ background: #9ABFA3; color: #2F5C3D; }}
        .avatar-devon {{ background: #ABABAB; color: #3D3D3D; }}
        .stage-divider {{
            display: flex; align-items: center; gap: 1rem;
        }}
        .stage-divider::before, .stage-divider::after {{
            content: ''; flex: 1; height: 1px; background: #E5E7EB;
        }}
    </style>
    {json_ld_html}
</head>
<body class="text-gray-900 font-sans antialiased bg-white">

    <nav class="max-w-screen-xl mx-auto px-6 py-8">
        <a href="/" class="group inline-flex items-center text-xs uppercase tracking-[0.3em] text-sage font-bold hover:text-terracotta transition-colors">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 mr-2 transform group-hover:-translate-x-1 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            Return to Library
        </a>
    </nav>

    <main class="max-w-screen-md mx-auto px-6 pb-24">

        {status_banner}

        <!-- HERO SECTION -->
        <div class="text-center mb-16">
            <p class="text-xs uppercase tracking-[0.3em] text-terracotta font-bold mb-4">{html.escape(category)}</p>
            <h1 class="font-serif text-5xl md:text-7xl mb-8 leading-tight">{title_escaped}</h1>
            <div class="aspect-video bg-gray-50 mb-12 flex items-center justify-center overflow-hidden">
                {image_block}
            </div>

            {bts_jump}
        </div>

        <!-- EDITORIAL INTRO -->
        <div class="prose prose-lg max-w-none">
            <h2 class="font-serif text-3xl mb-6">Why Muffin Pans?</h2>
            <p class="text-gray-600 mb-12 italic">{desc_escaped}</p>
        </div>

{recipe_card_html}

        {bts_section}

        <!-- FOOTER NAVIGATION -->
        <div class="mt-24 text-center border-t border-gray-100 pt-12">
            <a href="/" class="font-serif text-2xl italic text-sage hover:text-terracotta transition-colors">
                Explore more Muffin Pan Masterpieces &rarr;
            </a>
        </div>
    </main>

    <footer class="py-16 px-6 bg-gray-50 text-center border-t border-gray-100">
        <p class="font-serif text-xl mb-3 italic">The struggle is the story.</p>
        <p class="text-sage text-xs uppercase tracking-widest">&copy; 2026 Muffin Pan Recipes</p>
    </footer>

</body>
</html>
"""


def _slugify(title: str) -> str:
    """Convert a recipe title to a URL slug.

    'Make-Ahead Veggie & Sausage Egg Cups (Weekly Muffin Pan Breakfast)'
    -> 'make-ahead-veggie-sausage-egg-cups'
    """
    import re
    # Remove parenthetical suffixes
    title = re.sub(r'\s*\(.*?\)\s*', '', title)
    # Lowercase, replace non-alphanumeric with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    # Collapse multiple hyphens
    slug = re.sub(r'-+', '-', slug)
    return slug


def _clean_title(title: str) -> str:
    """Strip parenthetical qualifiers from recipe titles.

    'Make-Ahead Veggie & Sausage Egg Cups (Weekly Muffin Pan Breakfast)'
    -> 'Make-Ahead Veggie & Sausage Egg Cups'
    """
    import re
    return re.sub(r'\s*\(.*?\)\s*$', '', title).strip()


def publish_recipe_to_catalog(episode: dict) -> str | None:
    """Add the finished recipe to recipes.json and upload to blob.

    Called by Sunday cron after publish. Prepends the new recipe to the list
    so it becomes the featured recipe (recipes[0]) on the main page.
    Returns the blob URL or None on failure.
    """
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    title = _clean_title(recipe.get("title", ""))
    if not title:
        logger.warning("No recipe title — skipping catalog publish")
        return None

    slug = _slugify(title)

    # Build the hero image URL using /blob-images/ rewrite
    image_url = ""
    image_urls = episode.get("image_urls", [])
    if image_urls:
        image_url = _to_local_image_url(image_urls[0])

    # Build ingredients as flat strings (matching existing recipes.json format)
    ingredients = []
    for ing in recipe.get("ingredients", []):
        if isinstance(ing, dict):
            text = f"{ing.get('amount', '')} {ing.get('item', '')}".strip()
            if ing.get("notes"):
                text += f" ({ing['notes']})"
            ingredients.append(text)
        else:
            ingredients.append(str(ing))

    instructions = [
        s if isinstance(s, str) else str(s)
        for s in recipe.get("instructions", [])
    ]

    new_entry = {
        "slug": slug,
        "title": title,
        "category": recipe.get("category", "Savory").title(),
        "image": image_url,
        "description": recipe.get("description", ""),
        "prep": f"{recipe.get('prep_time', 15)} mins",
        "cook": f"{recipe.get('cook_time', 20)} mins",
        "yield": f"{recipe.get('servings', 12)} servings",
        "ingredients": ingredients,
        "instructions": instructions,
    }

    # Load existing catalog from blob (or fall back to static seed file)
    catalog_json = storage.load_page("pages/recipes.json")
    if catalog_json:
        try:
            catalog = json.loads(catalog_json)
        except json.JSONDecodeError:
            catalog = {"recipes": []}
    else:
        # First run — seed from static file
        static_path = os.path.join(os.path.dirname(__file__), "..", "..", "src", "recipes.json")
        try:
            with open(static_path) as f:
                catalog = json.loads(f.read())
        except Exception:
            catalog = {"recipes": []}

    # Don't add duplicates (idempotent re-run)
    existing_slugs = {r.get("slug") for r in catalog.get("recipes", [])}
    if slug in existing_slugs:
        logger.info(f"Recipe '{slug}' already in catalog — skipping")
        return None

    # Prepend new recipe (becomes featured)
    catalog["recipes"].insert(0, new_entry)

    # Upload
    try:
        content = json.dumps(catalog, indent=2)
        url = storage.save_page("pages/recipes.json", content)
        logger.info(f"Published recipe '{slug}' to catalog ({len(catalog['recipes'])} total)")
        return url
    except Exception as e:
        logger.error(f"Failed to publish recipe catalog: {e}")
        return None


def regenerate_and_upload(episode: dict) -> str | None:
    """Regenerate the episode page HTML and teaser JSON, upload both to blob.

    Called after each cron stage. Returns the page URL or None on failure.
    Also uploads a teaser JSON at pages/latest.json for the main page to fetch.
    """
    episode_id = episode.get("episode_id", "unknown")

    # Find hero image URL from episode data
    image_urls = episode.get("image_urls", [])
    image_url = image_urls[0] if image_urls else None

    try:
        # 1. Render and upload the episode page
        page_html = render_episode_page(episode, image_url=image_url)
        pathname = f"pages/{episode_id}/index.html"
        url = storage.save_page(pathname, page_html)
        logger.info(f"Uploaded episode page: {pathname} ({len(page_html)} bytes)")

        # 2. Upload teaser JSON for main page
        teaser = get_latest_teaser(episode)
        if teaser:
            teaser["page_url"] = f"/this-week"
            teaser_json = json.dumps(teaser)
            storage.save_page("pages/latest.json", teaser_json)
            logger.info(f"Uploaded teaser: {teaser.get('title', '?')}")

        return url
    except Exception as e:
        logger.error(f"Failed to regenerate episode page for {episode_id}: {e}")
        return None


def get_latest_teaser(episode: dict) -> dict | None:
    """Extract teaser data for the main page from the current episode.

    Returns a dict with title, latest_message, episode_id, stage, or None.
    """
    episode_id = episode.get("episode_id", "unknown")
    concept = episode.get("concept", "")

    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    title = recipe.get("title", concept)

    # Find the latest dialogue message
    latest_msg = None
    latest_stage = None
    for day in DAYS:
        stage = episode.get("stages", {}).get(day, {})
        dialogue = stage.get("dialogue", [])
        if dialogue:
            latest_msg = dialogue[-1]
            latest_stage = day

    if not latest_msg:
        return None

    char_name = latest_msg.get("character", "")
    message = latest_msg.get("message", "")
    info = _char_info(char_name)

    return {
        "episode_id": episode_id,
        "title": title,
        "stage": latest_stage,
        "stage_label": STAGE_LABELS.get(latest_stage or "", ""),
        "character": char_name,
        "character_slug": info["slug"],
        "character_initials": info["initials"],
        "message_preview": message[:200] + ("..." if len(message) > 200 else ""),
    }
