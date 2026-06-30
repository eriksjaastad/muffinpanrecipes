#!/usr/bin/env python3
"""Generate a recipe page from episode JSON data.

Renders the recipe template (hero image, ingredients, instructions) plus the
episode conversation (chat bubbles, day dividers) into a single viewable HTML
page — for test previews of generated episodes.

This is a thin wrapper around the production renderer
(backend.publishing.episode_renderer.render_episode_page): it loads an episode
from blob, resolves its hero image, and renders it through the SAME template
and stylesheet the live site uses. It used to carry its own copy of the
template (a second source of truth that drifted and still shipped the
cdn.tailwindcss.com Play CDN); that copy was removed in favor of delegation.

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
import os
import sys
from typing import Optional

import requests


BLOB_API = "https://blob.vercel-storage.com"


def generate_page(episode: dict, image_url: Optional[str] = None, **_unused) -> str:
    """Generate the full recipe page HTML from episode data.

    Delegates to the production renderer so test-preview pages use the exact
    same template and stylesheet (src/assets/site.css) as the live site — one
    source of truth, and no Tailwind Play CDN baked into generated artifacts.

    `**_unused` absorbs the legacy `blob_token`/`prefix` keyword args that
    callers still pass: render_episode_page resolves conversation/hero images
    from the episode's own image_paths/image_urls, so they are no longer needed.
    """
    from backend.publishing.episode_renderer import render_episode_page

    return render_episode_page(episode, image_url=image_url)


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
