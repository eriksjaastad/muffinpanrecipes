#!/usr/bin/env python3
"""Pin each published week's hero image to what its LIVE page already shows.

Why. A published page's hero is frozen (Erik, 2026-08-22: pin existing heroes).
But the hero is chosen at render time — ``episode_renderer._hero_image_url``
prefers the art director's confirmed winner — and that rule post-dates 20 of
the 25 published pages. The first live full rebuild (2026-09-05) therefore
swapped 20 heroes, several to a top-level copy that was never uploaded. This
script makes the pin DATA rather than a rule: it reads the hero ``<img src>``
from each recipe's live production page and writes it to the episode as
``hero_image_url``, which the renderer now honours before any picking logic.
Sunday's publish writes the same field for new weeks (cron_routes).

Dry-run by default; ``--apply`` writes. Writes are additive (one new field per
episode), inside ``storage.prefix_scope("")`` so a test prefix cannot leak.

    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/pin_published_heroes.py
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/pin_published_heroes.py --apply
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

from backend.publishing.episode_renderer import BLOB_CDN_PREFIX  # noqa: E402
from backend.recipe_model import slugify  # noqa: E402
from backend.storage import BLOB_PUBLIC_BASE, _source_png_key  # noqa: E402
from backend.utils.catalog import catalog_recipes, load_published_catalog  # noqa: E402

SITE = "https://muffinpanrecipes.com"
_HERO_RE = re.compile(r'recipe-hero__image.*?<img[^>]*\ssrc="([^"]*)"', re.S)


def hero_src_from_page(html: str) -> str | None:
    """The hero <img src> as the live page renders it, or None."""
    m = _HERO_RE.search(html)
    return m.group(1) if m else None


def _webp_source_png_if_it_exists(webp_key: str) -> str | None:
    """Confirm a bare full-size '.webp' key's would-be PNG sibling is real (#7185 review round 3, MEDIUM).

    storage._source_png_key deliberately leaves a width-less '.webp' key
    un-inverted: that exact name is also what _upload_webp_sibling gives the
    generated-photography full-size sibling, but a filename alone cannot
    prove it IS that sibling rather than an original, hand-authored
    'custom.webp' hero with no PNG behind it — inverting unconditionally
    would pin a hero to a PNG that was never uploaded. A script (unlike the
    pure storage-layer helpers) may do I/O, so this resolves the ambiguity
    for real: HEAD the PUBLIC Blob URL for the candidate PNG, same
    unauthenticated-public-host contract storage.image_variants_available
    uses (the API host 404s for blobs that DO exist, so this must hit the
    public CDN base, never the authenticated API host). Returns the
    candidate PNG key only when Blob confirms it with a 200; None on a 404,
    redirect, error status, or network failure — the caller keeps the
    original webp key in that case.
    """
    if not webp_key.lower().endswith(".webp"):
        return None
    candidate = webp_key[:-5] + ".png"
    try:
        resp = requests.head(
            f"{BLOB_PUBLIC_BASE}/images/{candidate}", timeout=10, allow_redirects=True
        )
    except Exception:  # governance: allow-silent SF002: documented contract; None means PNG not confirmed, so the caller keeps the original webp key and nothing is re-pinned
        return None
    return candidate if resp.status_code == 200 else None


def hero_url_for_episode(page_src: str) -> str:
    """Turn a rendered '/blob-images/<key>' src back into the CDN url the
    renderer stores and re-derives the same src from (_to_local_image_url).

    #7185 review round 2, HIGH: page_src is whatever sibling the LIVE page
    happened to be serving as the <img> fallback the day this ran — the raw
    PNG before backfill, or (once #7185's JPEG fallback exists) the sized
    '-1200w.jpg'. storage._source_png_key maps the UNAMBIGUOUS sibling
    shapes back to the canonical source PNG before it is stored as
    hero_image_url, so pinning a page that already has its JPEG fallback
    backfilled can never turn a future render's hero identity into a lossy
    1200px JPEG — which would silently drop the WebP <source> and every
    width/JPEG variant on every render after that, since they are all
    derived from a '.png'-shaped key.

    A bare full-size '.webp' is the one AMBIGUOUS shape _source_png_key
    leaves alone (round 3, MEDIUM) — resolved here with a real existence
    check via _webp_source_png_if_it_exists, since only this script (not the
    pure storage helper) may do that I/O.
    """
    if page_src.startswith("/blob-images/"):
        key = _source_png_key(page_src[len("/blob-images/"):])
        if key.lower().endswith(".webp"):
            key = _webp_source_png_if_it_exists(key) or key
        return BLOB_CDN_PREFIX + key
    return page_src


def plan(catalog: dict, episodes: dict[str, dict], fetch_page) -> list[tuple[str, str, str, str | None]]:
    """(episode_id, slug, hero_url, current_pin) for every published cron recipe.

    Catalog rows are matched to episodes by episode_id when present, else by
    the slug of the episode's recipe title (11 live rows predate stamping).
    """
    by_title_slug = {
        slugify(str((ep.get("stages", {}).get("monday", {}).get("recipe_data") or {}).get("title") or "")): eid
        for eid, ep in episodes.items()
    }
    rows = []
    for recipe in catalog_recipes(catalog):
        slug = str(recipe.get("slug") or "")
        if "/blob-images/" not in str(recipe.get("image") or ""):
            continue  # seed recipe: rendered from JSON, no episode, nothing to pin
        eid = str(recipe.get("episode_id") or "") or by_title_slug.get(slug, "")
        if not eid or eid not in episodes:
            print(f"  WARN no episode for {slug}; skipping", file=sys.stderr)
            continue
        src = hero_src_from_page(fetch_page(slug))
        if not src:
            print(f"  WARN no hero <img> found on live page /recipes/{slug}; skipping", file=sys.stderr)
            continue
        rows.append((eid, slug, hero_url_for_episode(src), episodes[eid].get("hero_image_url")))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write hero_image_url to each episode (default: dry-run).")
    args = parser.parse_args(argv)

    from backend.storage import storage

    catalog = load_published_catalog()
    episodes = {str(e.get("episode_id")): e for e in storage.list_episodes() if e.get("episode_id")}
    # list_episodes may return summaries; load each fully.
    episodes = {eid: (storage.load_episode(eid) or ep) for eid, ep in episodes.items()}

    def fetch_page(slug: str) -> str:
        with urllib.request.urlopen(f"{SITE}/recipes/{slug}", timeout=30) as r:
            return r.read().decode("utf-8", errors="replace")

    rows = plan(catalog, episodes, fetch_page)
    changed = [r for r in rows if r[3] != r[2]]
    mode = "apply" if args.apply else "dry-run"
    print(f"Hero pin ({mode}): {len(rows)} published recipes, {len(changed)} to write")
    for eid, slug, url, current in rows:
        flag = "" if current == url else ("  (was unpinned)" if not current else f"  (was {current})")
        print(f"  {eid}  {slug}  ->  {url}{flag}")
    if not args.apply:
        return 0

    with storage.prefix_scope(""):
        for eid, slug, url, _ in changed:
            ep = storage.load_episode(eid)
            if not ep:
                print(f"  ERROR could not load {eid}", file=sys.stderr)
                continue
            ep["hero_image_url"] = url
            ep.setdefault("events", []).append("hero pinned to the live page's image (scripts/pin_published_heroes.py)")
            storage.save_episode(eid, ep)
            print(f"  pinned {eid} ({slug})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
