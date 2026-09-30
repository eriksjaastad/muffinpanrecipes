"""Backfill sized image variants for already-published images (#6755, #7185).

episode_renderer._to_webp_srcset only emits 400w/800w candidates for an
image once storage.image_variants_available confirms the smaller blobs
exist — a srcset candidate that 404s breaks the image for whichever browser
picks it (see backend/storage.py's _upload_webp_variant docstring).
episode_renderer's hero <img> fallback likewise only points at the sized
JPEG sibling once storage.jpeg_fallback_available confirms it exists (#7185)
— otherwise it keeps pointing at the raw ~1536px PNG, same missing-means-
not-backfilled-yet fallback behavior. Every PNG uploaded before either
feature shipped needs its variants generated once, here, or its page keeps
the old behavior forever.

Walks the PUBLIC CATALOG (pages/recipes.json) and each catalog entry's full
episode JSON to find every currently-referenced PNG — read-only, no writes
to episode data or the catalog. For each PNG missing a 400w/800w WebP
sibling or its 1200w JPEG fallback sibling, downloads the canonical PNG
from Blob, encodes the missing variant(s) with storage._encode_webp /
storage._encode_jpeg_fallback, and uploads them at the same deterministic
keys backend/storage.py's save_image path uses.

Defaults to a dry run that only lists what is missing. Pass --apply to
actually download/encode/upload.

    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/backfill_image_variants.py
    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/backfill_image_variants.py --apply

HAZARD (RUNBOOK Incident 1): Blob storage is shared between Vercel preview
and production, and storage prefix is '' for anything not test mode. A run
from a preview deployment writes to LIVE paths. Run this only from a
controlled local/prod context — never from a preview deploy.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from backend.publishing.episode_renderer import _to_local_image_url  # noqa: E402
from backend.storage import (  # noqa: E402
    JPEG_FALLBACK_WIDTH,
    WEBP_VARIANT_WIDTHS,
    _encode_jpeg_fallback,
    _encode_webp,
    _jpeg_fallback_key,
    _webp_variant_key,
)

BLOB_API = "https://blob.vercel-storage.com"

# Every variant this script can backfill, as (kind, width) pairs. "webp"
# widths feed the <source> srcset (#6755); the single "jpeg" width is the
# <img> fallback for browsers that cannot decode WebP at all (#7185).
VARIANT_SPECS: list[tuple[str, int]] = [
    *[("webp", width) for width in WEBP_VARIANT_WIDTHS],
    ("jpeg", JPEG_FALLBACK_WIDTH),
]


def _variant_key_for(png_key: str, kind: str, width: int) -> str:
    if kind == "jpeg":
        return _jpeg_fallback_key(png_key)
    return _webp_variant_key(png_key, width)


def _encode_variant(png_bytes: bytes, kind: str, width: int) -> bytes:
    if kind == "jpeg":
        return _encode_jpeg_fallback(png_bytes)
    return _encode_webp(png_bytes, width=width)


def _content_type_for(kind: str) -> str:
    return "image/jpeg" if kind == "jpeg" else "image/webp"


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def list_blobs(token: str, prefix: str) -> list[dict]:
    """Page through the Blob list API and return every matching blob."""
    results: list[dict] = []
    cursor: str | None = None
    while True:
        params: dict[str, str] = {"prefix": prefix, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(BLOB_API, params=params, headers=_auth_headers(token), timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("blobs", []))
        if not data.get("hasMore"):
            break
        cursor = data.get("cursor")
    return results


def fetch_json_blob(token: str, prefix: str) -> dict | None:
    """List for an exact JSON pathname and fetch its content, or None."""
    resp = requests.get(
        BLOB_API, params={"prefix": prefix, "limit": "1"},
        headers=_auth_headers(token), timeout=30,
    )
    resp.raise_for_status()
    blobs = resp.json().get("blobs", [])
    if not blobs:
        return None
    content = requests.get(blobs[0]["url"], timeout=30)
    content.raise_for_status()
    return content.json()


def fetch_blob_bytes(url: str) -> bytes:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def upload_variant(token: str, pathname: str, variant_bytes: bytes, content_type: str) -> str:
    """Upload one variant (WebP or JPEG) at its deterministic Blob pathname.

    Same x-add-random-suffix=0 / x-allow-overwrite=1 contract as every
    other sibling upload (#5251) — required for the renderer's string
    rewrite (srcset or <img> fallback) to resolve the URL.
    """
    upload_url = f"{BLOB_API}/{pathname}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "x-vercel-access": "public",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
    }
    resp = requests.put(upload_url, data=variant_bytes, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json().get("url", "")


def _blob_key_from_url(image_url: str) -> str:
    """Recover the 'images/...' blob key from any historical URL shape.

    Reuses the renderer's own CDN/rewrite normalization (_to_local_image_url)
    rather than re-deriving it, so this stays in sync with whatever URL
    shapes episode JSON actually contains.
    """
    local = _to_local_image_url(str(image_url or ""))
    if local.startswith("/blob-images/"):
        return "images/" + local[len("/blob-images/"):]
    return ""


def collect_published_png_keys(token: str) -> list[str]:
    """Read-only walk of the catalog and every published episode's images.

    Returns the deduplicated, sorted set of PNG blob keys currently
    referenced by a published recipe page — hero and BTS gallery images
    alike. Never writes to the catalog or any episode.
    """
    catalog = fetch_json_blob(token, "pages/recipes.json")
    if catalog is None:
        raise RuntimeError("Could not find pages/recipes.json in Blob storage")
    recipes = catalog.get("recipes", [])

    # Episodes named by the catalog first, then EVERY other published episode
    # blob: 11 of the live catalog entries predate episode_id stamping, so a
    # walk keyed only on catalog episode_ids left their pages without variants
    # on the first live rebuild (2026-09-05). Published = Sunday completed.
    episodes: dict[str, dict] = {}
    for recipe in recipes:
        episode_id = recipe.get("episode_id")
        if episode_id and episode_id not in episodes:
            episode = fetch_json_blob(token, f"episodes/{episode_id}.json")
            if episode is not None:
                episodes[str(episode_id)] = episode
    for blob in list_blobs(token, "episodes/"):
        pathname = str(blob.get("pathname") or "")
        episode_id = pathname.removeprefix("episodes/").removesuffix(".json")
        if not pathname.endswith(".json") or episode_id in episodes:
            continue
        content = requests.get(blob["url"], timeout=30)
        content.raise_for_status()
        episode = content.json()
        sunday = (episode.get("stages") or {}).get("sunday") or {}
        if episode.get("published_at") or sunday.get("status") == "complete":
            episodes[episode_id] = episode

    keys: set[str] = set()
    for episode in episodes.values():
        urls = list(episode.get("image_urls", []) or [])
        wed = episode.get("stages", {}).get("wednesday", {})
        urls += list(wed.get("image_urls", []) or [])

        for url in urls:
            key = _blob_key_from_url(url)
            if key.lower().endswith(".png"):
                keys.add(key)

        # The recipe-page HERO is not in image_urls: episode_renderer._hero_image_url
        # prefers the art director's confirmed winner, stored as a repo-relative
        # path ('src/assets/images/<id>.png') and mapped to the blob key the same
        # way storage._blob_key does. It is the LCP image, so it matters most.
        winner = wed.get("confirmed_winner")
        featured = str((winner or {}).get("featured_image") or "") if isinstance(winner, dict) else ""
        if featured.lower().endswith(".png"):
            keys.add(featured.removeprefix("src/").removeprefix("assets/").lstrip("/"))

        # A PINNED hero (#7185 review finding 3) takes priority over BOTH of
        # the above in episode_renderer._hero_image_url — it is checked
        # FIRST, before confirmed_winner or image_urls[0] — written by
        # scripts/pin_published_heroes.py and the Sunday cron as a full CDN
        # URL (same shape as image_urls). A pin can name a photo that
        # differs from confirmed_winner/image_urls entirely (that is the
        # whole point of pinning a page's hero once it is published), so it
        # must be walked explicitly rather than assumed to already be
        # covered by the two collections above.
        hero_url = str(episode.get("hero_image_url") or "").strip()
        if hero_url:
            hero_key = _blob_key_from_url(hero_url)
            if hero_key.lower().endswith(".png"):
                keys.add(hero_key)
            elif hero_url.lower().endswith(".png"):
                # Defensive: same repo-relative normalization as
                # confirmed_winner.featured_image above, in case hero_image_url
                # is ever set to a raw path instead of a resolved CDN URL.
                keys.add(hero_url.removeprefix("src/").removeprefix("assets/").lstrip("/"))

    return sorted(keys)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Download, encode, and upload missing variants. Default is a dry run.",
    )
    args = parser.parse_args(argv)

    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        print("ERROR: BLOB_READ_WRITE_TOKEN not set (use: doppler run --)", file=sys.stderr)
        return 2

    print("Reading public catalog + episode JSON (read-only)...")
    try:
        png_keys = collect_published_png_keys(token)
    except Exception as e:  # noqa: BLE001 - keep credentials/connection detail out of output
        print(f"ERROR: could not walk catalog/episodes: {e}", file=sys.stderr)
        return 1
    print(f"  {len(png_keys)} distinct published PNGs referenced")

    all_blobs = list_blobs(token, "images/")
    existing = {b.get("pathname", "") for b in all_blobs}
    blobs_by_pathname = {b.get("pathname", ""): b for b in all_blobs}

    todo: list[tuple[str, str, int]] = []
    for png_key in png_keys:
        if png_key not in blobs_by_pathname:
            print(f"  WARNING: referenced PNG not found in Blob: {png_key}")
            continue
        for kind, width in VARIANT_SPECS:
            if _variant_key_for(png_key, kind, width) not in existing:
                todo.append((png_key, kind, width))

    mode = "apply" if args.apply else "dry-run"
    print(
        f"Variant backfill ({mode}): {len(todo)} missing variants "
        f"across {len(png_keys)} referenced PNGs"
    )

    if not args.apply:
        for png_key, kind, width in todo:
            print(f"  would create: {_variant_key_for(png_key, kind, width)}")
        return 0

    by_png: dict[str, list[tuple[str, int]]] = {}
    for png_key, kind, width in todo:
        by_png.setdefault(png_key, []).append((kind, width))

    converted = 0
    failed = 0
    for i, (png_key, specs) in enumerate(by_png.items(), 1):
        source = blobs_by_pathname[png_key]
        try:
            png_bytes = fetch_blob_bytes(source["url"])
        except Exception as e:  # noqa: BLE001 - continue the idempotent batch after one bad image
            print(f"  [{i}/{len(by_png)}] SKIP {png_key}: fetch failed ({e})")
            failed += len(specs)
            continue
        for kind, width in specs:
            variant_key = _variant_key_for(png_key, kind, width)
            try:
                variant_bytes = _encode_variant(png_bytes, kind, width)
                upload_variant(token, variant_key, variant_bytes, _content_type_for(kind))
                print(f"  [{i}/{len(by_png)}] {png_key} -> {variant_key}")
                converted += 1
            except Exception as e:  # noqa: BLE001 - one bad variant must not stop the others
                print(f"  [{i}/{len(by_png)}] FAILED {variant_key}: {e}")
                failed += 1

    print(f"Done. Uploaded {converted}, failed {failed}, total {len(todo)}.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
