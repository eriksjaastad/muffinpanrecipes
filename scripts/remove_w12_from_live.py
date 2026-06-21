#!/usr/bin/env python3
"""One-time removal: pull QA-failed W12 (roasted-chicken-potato-cups) off the live site.

W12's Sunday editorial QA gate REJECTED this recipe (duplicate ingredients +
junk title; retroactive score 63.6). It only went live because a prior session
hand-forced it into the catalog via scripts/fix_catalog.py, bypassing the gate
(documented in ai-journal 2026-03-24, "the Sunday the recipes came back"). Per
Erik's decision (2026-06-21) and the W22 precedent, the gate's verdict stands:
remove it from the live surface, keep the episode JSON as a published_at=None
record.

Actions (prod blob):
  1. Remove the catalog entry  -> it drops out of the dynamic sitemap.
  2. Delete the two rendered blob pages -> /recipes/<slug> and /episodes/<id> 404.
  3. Leave episodes/2026-W12.json (published_at stays None) + append an audit event.

The catalog is read straight from the public blob CDN with a cache-buster to
dodge the documented Vercel-Blob read-after-write staleness.

Usage:
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/remove_w12_from_live.py --dry-run
    doppler run --project muffinpanrecipes --config prd -- uv run python scripts/remove_w12_from_live.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, ".")

from backend.storage import storage

SLUG = "roasted-chicken-potato-cups"
EPISODE_ID = "2026-W12"
PAGES = [f"pages/recipes/{SLUG}/index.html", f"pages/{EPISODE_ID}/index.html"]
PUBLIC_BASE = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
BLOB_API = "https://blob.vercel-storage.com"
REMOVAL_NOTE = (
    "operator 2026-06-21: removed from live catalog + deleted rendered pages. "
    "Sunday editorial QA FAILED (duplicate ingredients + title rules); was hand-published "
    "around the gate on 2026-03-24. Kept here as a published_at=None record (W22 precedent)."
)


def _auth() -> dict:
    # Crash loudly if the token is missing rather than silently no-op.
    return {"Authorization": f"Bearer {os.environ['BLOB_READ_WRITE_TOKEN']}"}


def _cb() -> str:
    # A UNIQUE token per read — a fixed cache-buster gets cached by the CDN and
    # serves the same (possibly pre-write) body back on the next read.
    return f"cb={time.time_ns()}"


def _fresh_catalog() -> dict | list:
    """Read pages/recipes.json from the public CDN, cache-busted past stale reads."""
    r = requests.get(f"{PUBLIC_BASE}/pages/recipes.json?{_cb()}", timeout=15)
    r.raise_for_status()
    return r.json()


def _page_exists(pathname: str) -> bool:
    return requests.head(f"{PUBLIC_BASE}/{pathname}?{_cb()}", timeout=15).status_code == 200


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    catalog = _fresh_catalog()
    recipes = catalog if isinstance(catalog, list) else catalog.get("recipes", [])
    before = len(recipes)
    matches = [r for r in recipes if r.get("slug") == SLUG]
    print(f"catalog: {before} recipes; {SLUG} present: {len(matches)}")
    assert len(matches) == 1, f"expected exactly 1 {SLUG} entry, found {len(matches)}"

    kept = [r for r in recipes if r.get("slug") != SLUG]
    assert len(kept) == before - 1

    for p in PAGES:
        print(f"  page {p}: {'present' if _page_exists(p) else 'absent'}")

    if args.dry_run:
        print(f"\n[DRY RUN] would write catalog with {len(kept)} recipes, "
              f"delete {len(PAGES)} page(s), keep {EPISODE_ID} as record.")
        return

    # 1) Write the trimmed catalog back (storage handles the authenticated PUT).
    if isinstance(catalog, list):
        new_catalog: object = kept
    else:
        catalog["recipes"] = kept
        new_catalog = catalog
    storage.save_page("pages/recipes.json", json.dumps(new_catalog, indent=2))
    print(f"catalog written: {before} -> {len(kept)} recipes")

    # 2) Delete the rendered pages (deterministic public URLs for our public store).
    urls = [f"{PUBLIC_BASE}/{p}" for p in PAGES]
    resp = requests.post(
        f"{BLOB_API}/delete",
        headers={**_auth(), "Content-Type": "application/json", "x-api-version": "7"},
        data=json.dumps({"urls": urls}),
        timeout=30,
    )
    resp.raise_for_status()
    print(f"deleted {len(urls)} page(s): {', '.join(PAGES)}")

    # 3) Keep the episode record; append an audit event. published_at stays None.
    ep = storage.load_episode(EPISODE_ID)
    if ep is not None:
        assert ep.get("published_at") is None, "published_at must stay None"
        ep.setdefault("events", []).append(REMOVAL_NOTE)
        storage.save_episode(EPISODE_ID, ep)
        print(f"episode {EPISODE_ID}: audit event appended, published_at still None")

    # 4) Verify catalog round-trip (cache-busted).
    after = _fresh_catalog()
    after_recipes = after if isinstance(after, list) else after.get("recipes", [])
    still = [r for r in after_recipes if r.get("slug") == SLUG]
    print(f"\nverify: catalog now {len(after_recipes)} recipes; {SLUG} present: {len(still)}")
    assert not still, "slug still in catalog after write"
    print("Done.")


if __name__ == "__main__":
    main()
