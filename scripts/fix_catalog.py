"""One-time script to add missing W10 and W12 recipes to the catalog.

W10 "Mini Lemon Meringue Cups" — Sunday published but catalog publish wasn't
wired up yet during the March 6 debugging session.

W12 "Roasted Chicken Potato Cups" (cleaned from "Sunday Sheet-Pan Dinner…In A
Muffin Tin (Roasted Chicken, Veg & Herbed Potato Cups)") — editorial QA failed
on title/ingredient issues; recipe itself is solid.

Usage:
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/fix_catalog.py [--dry-run]
"""
from __future__ import annotations

import json
import sys

from backend.storage import storage
from backend.publishing.episode_renderer import _slugify, _to_local_image_url, _clean_title


def _fix_image_url(url: str) -> str:
    """Convert any blob URL format to /blob-images/ path.

    Handles both old-format (blob.vercel-storage.com) and new-format
    (gtczmjysc51nh8fq.public.blob.vercel-storage.com) URLs.
    """
    result = _to_local_image_url(url)
    # Old W10 URLs use https://blob.vercel-storage.com/images/... (no store ID)
    if result.startswith("https://blob.vercel-storage.com/images/"):
        result = "/blob-images/" + result.removeprefix("https://blob.vercel-storage.com/images/")
    return result


def _resolve_w10_image(original_url: str) -> str:
    """Resolve W10's image to its actual blob path via list API.

    W10 images were uploaded with old naming (random suffix), so the
    episode's stored URLs don't match any real blob path. We use the
    blob list API to find the actual file.
    """
    import os
    import requests

    # Extract the image ID prefix (e.g. "8a79d045") from the URL
    # URL pattern: https://blob.vercel-storage.com/images/8a79d045/...
    parts = original_url.rstrip("/").split("/")
    # Find "images" segment and take next part
    try:
        idx = parts.index("images")
        image_id = parts[idx + 1]
    except (ValueError, IndexError):
        return _fix_image_url(original_url)

    token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    resp = requests.get(
        "https://blob.vercel-storage.com",
        params={"prefix": f"images/{image_id}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    blobs = resp.json().get("blobs", [])
    if blobs:
        # Use the CDN URL from the first matching blob
        cdn_url = blobs[0].get("url", "")
        return _to_local_image_url(cdn_url)

    print(f"  WARNING: No blob found for image prefix images/{image_id}")
    return _fix_image_url(original_url)


def load_catalog() -> dict:
    """Load current catalog from blob."""
    content = storage.load_page("pages/recipes.json")
    if content:
        return json.loads(content)
    raise RuntimeError("Could not load pages/recipes.json from blob")


def build_w10_entry(episode: dict) -> dict:
    """Build catalog entry for W10 Mini Lemon Meringue Cups."""
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    title = _clean_title(recipe.get("title", ""))
    image_urls = episode.get("image_urls", [])
    # W10's images were uploaded with old naming convention.
    # The actual blob is images/8a79d045-DhjkN1qxCEXg2F0D3xq7c60Pe11hvk.png
    # Use the blob list API to find the real path.
    image_url = _resolve_w10_image(image_urls[0]) if image_urls else ""

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

    return {
        "slug": _slugify(title),
        "title": title,
        "category": recipe.get("category", "Dessert").title(),
        "image": image_url,
        "description": recipe.get("description", ""),
        "prep": f"{recipe.get('prep_time', 35)} mins",
        "cook": f"{recipe.get('cook_time', 30)} mins",
        "yield": f"{recipe.get('servings', 12)} servings",
        "ingredients": ingredients,
        "instructions": instructions,
    }


def build_w12_entry(episode: dict) -> dict:
    """Build catalog entry for W12 with cleaned-up title."""
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})

    # Override the bad title — QA flagged "Sunday Sheet-Pan Dinner…In A Muffin Tin
    # (Roasted Chicken, Veg & Herbed Potato Cups)" as violating title rules.
    title = "Roasted Chicken Potato Cups"

    image_urls = episode.get("image_urls", [])
    image_url = _fix_image_url(image_urls[0]) if image_urls else ""

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

    return {
        "slug": _slugify(title),
        "title": title,
        "category": recipe.get("category", "Savory").title(),
        "image": image_url,
        "description": recipe.get("description", ""),
        "prep": f"{recipe.get('prep_time', 30)} mins",
        "cook": f"{recipe.get('cook_time', 35)} mins",
        "yield": f"{recipe.get('servings', 6)} servings",
        "ingredients": ingredients,
        "instructions": instructions,
    }


def main():
    dry_run = "--dry-run" in sys.argv

    # Load episodes from blob
    w10_json = storage.load_episode("2026-W10")
    w12_json = storage.load_episode("2026-W12")

    if not w10_json:
        print("ERROR: Could not load W10 episode from blob")
        sys.exit(1)
    if not w12_json:
        print("ERROR: Could not load W12 episode from blob")
        sys.exit(1)

    # Load current catalog
    catalog = load_catalog()
    existing_slugs = {r.get("slug") for r in catalog.get("recipes", [])}
    print(f"Current catalog: {len(catalog['recipes'])} recipes")
    print(f"Existing slugs: {sorted(existing_slugs)}")
    print()

    added = []

    # Add W10
    w10_entry = build_w10_entry(w10_json)
    print(f"W10: '{w10_entry['title']}' (slug: {w10_entry['slug']})")
    if w10_entry["slug"] in existing_slugs:
        print("  SKIP: already in catalog")
    else:
        added.append(("W10", w10_entry))
        print(f"  WILL ADD: {w10_entry['category']}, {len(w10_entry['ingredients'])} ingredients")

    # Add W12
    w12_entry = build_w12_entry(w12_json)
    print(f"W12: '{w12_entry['title']}' (slug: {w12_entry['slug']})")
    if w12_entry["slug"] in existing_slugs:
        print("  SKIP: already in catalog")
    else:
        added.append(("W12", w12_entry))
        print(f"  WILL ADD: {w12_entry['category']}, {len(w12_entry['ingredients'])} ingredients")

    if not added:
        print("\nNothing to add.")
        return

    # Insert new recipes at the front (newest first)
    # W12 is newer, so insert it first, then W10 before it
    for _week, entry in reversed(added):
        catalog["recipes"].insert(0, entry)

    print(f"\nUpdated catalog: {len(catalog['recipes'])} recipes")
    for i, r in enumerate(catalog["recipes"]):
        print(f"  {i}: {r['slug']}")

    if dry_run:
        print("\n[DRY RUN] Would upload to pages/recipes.json")
    else:
        content = json.dumps(catalog, indent=2)
        url = storage.save_page("pages/recipes.json", content)
        print(f"\nUploaded to blob: {url}")
        print("Done! New recipes should appear on the main page.")


if __name__ == "__main__":
    main()
