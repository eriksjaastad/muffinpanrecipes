#!/usr/bin/env python3
"""Generate a recipe page from episode JSON data.

Merges the recipe template (hero image, ingredients, instructions) with
the episode conversation (chat bubbles, day dividers) into a single
viewable HTML page.

Usage:
    # Generate from blob episode and upload to blob as viewable HTML
    doppler run --config prd -- uv run python scripts/generate_recipe_page.py \
        --episode test-20260307-163154 --upload

    # Generate from blob episode, test prefix
    doppler run --config prd -- uv run python scripts/generate_recipe_page.py \
        --episode test-20260307-163154 --prefix test/ --upload

    # Output to local file
    doppler run --config prd -- uv run python scripts/generate_recipe_page.py \
        --episode test-20260307-163154 --prefix test/ --output /tmp/preview.html
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from typing import Optional

import requests


BLOB_API = "https://blob.vercel-storage.com"

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


def _char_info(name: str) -> dict:
    """Look up character info, falling back to generic."""
    for key, info in CHARACTERS.items():
        if key.lower() in name.lower() or name.lower() in key.lower():
            return {**info, "name": name}
    # Fallback
    slug = name.lower().split()[0] if name else "unknown"
    initials = "".join(w[0].upper() for w in name.split()[:2]) if name else "??"
    return {"slug": slug, "initials": initials, "name": name, "role": "Team Member"}


def _render_chat_message(msg: dict, image_url_map: dict[str, str] | None = None) -> str:
    """Render a single chat message as HTML, including any image attachments."""
    char_name = msg.get("character", "Unknown")
    message = html.escape(msg.get("message", ""))
    info = _char_info(char_name)
    slug = info["slug"]
    attachments = msg.get("attachments", [])

    # Render image attachments if present
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

    return f"""
            <div class="flex items-start gap-3">
                <div class="avatar avatar-{slug} mt-1">{info['initials']}</div>
                <div>
                    <p class="text-[11px] text-gray-400 mb-1">{html.escape(char_name)}</p>
                    <div class="chat-bubble chat-{slug} rounded-2xl rounded-tl-sm px-4 py-3">
                        <p class="text-sm leading-relaxed">{message}</p>{images_html}
                    </div>
                </div>
            </div>"""


def _build_image_url_map(episode: dict, blob_token: str, prefix: str = "") -> dict[str, str]:
    """Build a mapping from relative image paths to full blob CDN URLs.

    Uses the Blob LIST API to get proper public CDN URLs (the ones stored
    in the episode JSON may use the API endpoint instead of the CDN).
    """
    recipe_id = episode.get("recipe_id", "")
    if not recipe_id or not blob_token:
        return {}

    # List all image blobs for this recipe
    resp = requests.get(
        BLOB_API,
        params={"prefix": f"{prefix}images/{recipe_id}", "limit": "20"},
        headers={"Authorization": f"Bearer {blob_token}"},
        timeout=15,
    )
    if not resp.ok:
        return {}

    blobs = resp.json().get("blobs", [])
    # Build map: pathname suffix → CDN URL
    # Blob pathnames look like: test/images/9874fa11/round_1/macro_closeup.png
    # Episode image_paths look like: src/assets/images/9874fa11/round_1/macro_closeup.png
    cdn_by_suffix: dict[str, str] = {}
    for b in blobs:
        # Extract the part after images/ (e.g. "9874fa11/round_1/macro_closeup.png")
        pathname = b["pathname"]
        parts = pathname.split("images/", 1)
        if len(parts) == 2:
            cdn_by_suffix[parts[1]] = b["url"]

    # Map episode image_paths to CDN URLs
    wed = episode.get("stages", {}).get("wednesday", {})
    paths = wed.get("image_paths", []) or episode.get("image_paths", [])
    url_map = {}
    for p in paths:
        # Extract suffix from "src/assets/images/9874fa11/round_1/macro_closeup.png"
        parts = p.split("images/", 1)
        if len(parts) == 2 and parts[1] in cdn_by_suffix:
            url_map[p] = cdn_by_suffix[parts[1]]

    return url_map


def _render_conversation_section(episode: dict, blob_token: str = "", prefix: str = "") -> str:
    """Render the full week's conversation as HTML sections."""
    sections = []
    has_any_dialogue = False
    image_url_map = _build_image_url_map(episode, blob_token, prefix)

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


def _render_ingredient(ing: dict) -> str:
    """Render one ingredient as an <li>."""
    amount = ing.get("amount", "")
    item = ing.get("item", "")
    notes = ing.get("notes", "")
    text = f"{amount} {item}".strip()
    if notes:
        text += f" ({notes})"
    return f'<li class="border-b border-gray-50 pb-2">{html.escape(text)}</li>'


def _render_instruction(idx: int, text: str) -> str:
    """Render one instruction step."""
    return (
        f'<li class="flex gap-4">'
        f'<span class="font-serif text-terracotta font-bold italic text-xl">{idx:02d}</span>'
        f'<span>{html.escape(text)}</span></li>'
    )


def generate_page(episode: dict, image_url: Optional[str] = None,
                   blob_token: str = "", prefix: str = "") -> str:
    """Generate the full recipe page HTML from episode data."""
    concept = episode.get("concept", "Muffin Pan Recipe")
    episode_id = episode.get("episode_id", "unknown")

    # Extract recipe data from Monday stage
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    title = recipe.get("title", concept)
    description = recipe.get("description", "")
    category = recipe.get("category", "savory").title()
    prep_time = recipe.get("prep_time", 15)
    cook_time = recipe.get("cook_time", 20)
    servings = recipe.get("servings", 12)
    difficulty = recipe.get("difficulty", "medium").title()
    ingredients = recipe.get("ingredients", [])
    instructions = recipe.get("instructions", [])
    chef_notes = recipe.get("chef_notes", "")

    # Image
    if not image_url:
        # Try to get from episode data
        image_urls = episode.get("image_urls", [])
        image_url = image_urls[0] if image_urls else ""

    # Render ingredients and instructions
    ingredients_html = "\n".join(_render_ingredient(ing) for ing in ingredients)
    instructions_html = "\n".join(
        _render_instruction(i + 1, step)
        for i, step in enumerate(instructions)
    )

    # Image block
    if image_url:
        image_block = f'<img src="{html.escape(image_url)}" class="w-full h-full object-cover" alt="{html.escape(title)}">'
    else:
        image_block = f'<div class="flex items-center justify-center h-full text-gray-400 font-serif italic text-xl">Photo coming Wednesday</div>'

    # Chef notes block
    chef_notes_html = ""
    if chef_notes:
        chef_notes_html = f"""
            <div class="mt-12 pt-8 border-t border-gray-100">
                <h3 class="font-serif text-xl mb-4 italic text-sage">Chef's Notes</h3>
                <p class="text-gray-600 leading-relaxed">{html.escape(chef_notes)}</p>
            </div>"""

    # Conversation section
    conversation_html = _render_conversation_section(episode, blob_token, prefix)

    # JSON-LD
    ld_ingredients = [
        f"{ing.get('amount', '')} {ing.get('item', '')}".strip()
        for ing in ingredients
    ]
    ld_instructions = [
        {"@type": "HowToStep", "text": step} for step in instructions
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
        "recipeInstructions": ld_instructions,
    })

    title_escaped = html.escape(title)
    desc_escaped = html.escape(description)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="description" content="{desc_escaped}">
    <title>{title_escaped} | Muffin Pan Recipes</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>&#x1f9c1;</text></svg>">
    <script defer src="/_vercel/speed-insights/script.js"></script>

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

        <script type="application/ld+json">{ld_json}</script>

        <!-- HERO SECTION -->
        <div class="text-center mb-16">
            <p class="text-xs uppercase tracking-[0.3em] text-terracotta font-bold mb-4">{html.escape(category)}</p>
            <h1 class="font-serif text-5xl md:text-7xl mb-8 leading-tight">{title_escaped}</h1>
            <div class="aspect-video bg-gray-50 mb-12 flex items-center justify-center overflow-hidden">
                {image_block}
            </div>

            <a href="#behind-the-scenes" class="block w-full py-8 border-4 border-gray-900 font-bold uppercase tracking-[0.4em] text-sm hover:bg-gray-900 hover:text-white transition-all transform active:scale-[0.98]">
                Jump to Behind the Scenes
            </a>
        </div>

        <!-- EDITORIAL INTRO -->
        <div class="prose prose-lg max-w-none">
            <h2 class="font-serif text-3xl mb-6">Why Muffin Pans?</h2>
            <p class="text-gray-600 mb-12 italic">{desc_escaped}</p>
        </div>

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
        </div>

        <!-- BEHIND THE SCENES -->
        <div id="behind-the-scenes" class="mt-24">
            <div class="text-center mb-16">
                <p class="text-xs uppercase tracking-[0.3em] text-terracotta font-bold mb-4">Behind the Scenes</p>
                <h2 class="font-serif text-4xl mb-4 italic">How This Recipe Was Made</h2>
                <p class="text-gray-500 max-w-lg mx-auto">Follow the creative team's conversation as they developed, photographed, and published this recipe.</p>
            </div>

            <!-- THE TEAM -->
            <div class="flex justify-center gap-6 mb-16">
                <div class="text-center">
                    <div class="avatar avatar-margaret mx-auto mb-2">MC</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage">Margaret</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-marcus mx-auto mb-2">MR</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage">Marcus</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-steph mx-auto mb-2">SW</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage">Steph</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-julian mx-auto mb-2">JT</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage">Julian</p>
                </div>
                <div class="text-center">
                    <div class="avatar avatar-devon mx-auto mb-2">DP</div>
                    <p class="text-[10px] uppercase tracking-widest text-sage">Devon</p>
                </div>
            </div>

            <!-- CONVERSATION -->
{conversation_html}
        </div>

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


def _load_episode_from_blob(
    episode_id: str, blob_token: str, prefix: str = "",
) -> Optional[dict]:
    """Load full episode JSON from Vercel Blob."""
    pathname = f"{prefix}episodes/{episode_id}.json"
    resp = requests.get(
        BLOB_API,
        params={"prefix": pathname, "limit": "1"},
        headers={"Authorization": f"Bearer {blob_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    blobs = resp.json().get("blobs", [])
    if not blobs:
        return None

    content_resp = requests.get(blobs[0]["url"], timeout=15)
    content_resp.raise_for_status()
    return content_resp.json()


def _find_image_url(episode: dict, blob_token: str, prefix: str = "") -> Optional[str]:
    """Find the hero image URL from blob storage."""
    recipe_id = episode.get("recipe_id", "")
    if not recipe_id:
        return None

    resp = requests.get(
        BLOB_API,
        params={"prefix": f"{prefix}images/{recipe_id}", "limit": "5"},
        headers={"Authorization": f"Bearer {blob_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    blobs = resp.json().get("blobs", [])
    if blobs:
        return blobs[0]["url"]
    return None


def _upload_page(page_html: str, pathname: str, blob_token: str) -> str:
    """Upload HTML page to Vercel Blob. Returns public URL."""
    headers = {
        "Authorization": f"Bearer {blob_token}",
        "Content-Type": "text/html",
        "x-api-version": "7",
        "x-content-type": "text/html",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
    }
    resp = requests.put(
        f"{BLOB_API}/{pathname}",
        data=page_html.encode("utf-8"),
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("url", "")


def main():
    parser = argparse.ArgumentParser(description="Generate recipe page from episode JSON")
    parser.add_argument("--episode", required=True, help="Episode ID to render")
    parser.add_argument("--prefix", default="", help="Blob prefix (e.g. 'test/')")
    parser.add_argument("--upload", action="store_true", help="Upload to Vercel Blob")
    parser.add_argument("--output", default=None, help="Write to local file instead")
    args = parser.parse_args()

    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not blob_token:
        print("ERROR: BLOB_READ_WRITE_TOKEN not set")
        sys.exit(1)

    # Load episode
    print(f"Loading episode {args.episode} from blob (prefix: '{args.prefix}')...")
    episode = _load_episode_from_blob(args.episode, blob_token, args.prefix)
    if not episode:
        print(f"ERROR: Episode '{args.episode}' not found in blob")
        sys.exit(1)

    print(f"  Concept: {episode.get('concept', '?')}")
    title = episode.get("stages", {}).get("monday", {}).get("recipe_data", {}).get("title", "?")
    print(f"  Recipe: {title}")

    # Find image
    image_url = _find_image_url(episode, blob_token, args.prefix)
    if image_url:
        print(f"  Image: found")
    else:
        print(f"  Image: not found (placeholder will be shown)")

    # Generate page
    print("Generating recipe page...")
    page_html = generate_page(episode, image_url=image_url, blob_token=blob_token, prefix=args.prefix)
    print(f"  Generated {len(page_html)} bytes of HTML")

    # Output
    if args.output:
        with open(args.output, "w") as f:
            f.write(page_html)
        print(f"  Written to: {args.output}")

    if args.upload:
        pathname = f"{args.prefix}pages/{args.episode}/index.html"
        print(f"  Uploading to blob: {pathname}")
        url = _upload_page(page_html, pathname, blob_token)
        print(f"\n  Page URL: {url}")


if __name__ == "__main__":
    main()
