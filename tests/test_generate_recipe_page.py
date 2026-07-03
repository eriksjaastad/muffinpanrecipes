"""generate_recipe_page previews must be self-contained.

The preview generator delegates to the production renderer, which links the
stylesheet root-relative (`/assets/site.css`) and renders images under
`/blob-images/`. That is correct for production (served from the site origin)
but a preview is viewed at the raw Vercel Blob URL or a local --output file,
where those root-relative paths 404. So the generated preview must inline the
stylesheet and absolutize image paths. (Regression guard for the 2026-07-03
Codex finding.)
"""
from __future__ import annotations

from scripts.generate_recipe_page import PUBLIC_BLOB_CDN, generate_page

_EPISODE = {
    "concept": "Test Cups",
    "image_urls": ["https://blob.vercel-storage.com/images/abc123.png"],
    "stages": {
        "monday": {
            "status": "complete",
            "recipe_data": {
                "title": "Test Cups",
                "description": "d",
                "category": "Savory",
                "ingredients": ["1 egg"],
                "instructions": ["Bake."],
            },
        },
        "sunday": {"status": "complete"},
    },
}


def test_preview_inlines_stylesheet_not_root_relative_link():
    html = generate_page(_EPISODE)
    assert '<link rel="stylesheet" href="/assets/site.css">' not in html
    # site.css inlined (a token that only exists in the stylesheet).
    assert "<style>" in html and "--terracotta" in html


def test_preview_absolutizes_blob_image_paths():
    html = generate_page(_EPISODE)
    # No bare root-relative image path survives on a visible <img>/<source>.
    assert '"/blob-images/' not in html
    assert f'"{PUBLIC_BLOB_CDN}/images/' in html


def test_preview_has_no_tailwind_cdn():
    html = generate_page(_EPISODE)
    # The runtime CDN is a <script> load, not the bare string (site.css's own
    # comment documents what it replaced, and that comment is now inlined).
    assert '<script src="https://cdn.tailwindcss.com"' not in html
