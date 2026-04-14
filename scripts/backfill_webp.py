#!/usr/bin/env python3
"""One-off backfill: generate WebP siblings for existing PNG blobs (#5251).

storage._upload_webp_sibling now auto-creates a `.webp` alongside every
new PNG upload. This script walks the existing blob store and creates
WebP siblings for every PNG that doesn't already have one, so the
<picture> tag in episode_renderer finds WebP for historical content.

Run once, manually. Idempotent.

    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/backfill_webp.py

Flags:
    --dry-run   List what would be processed without uploading
    --prefix    Restrict to blobs under a prefix (default: images/)
"""

from __future__ import annotations

import argparse
import os
import sys
from io import BytesIO
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402
from PIL import Image  # noqa: E402

BLOB_API = "https://blob.vercel-storage.com"


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def list_blobs(token: str, prefix: str) -> list[dict]:
    """Page through the blob list API and return every matching blob."""
    results: list[dict] = []
    cursor: Optional[str] = None
    while True:
        params: dict = {"prefix": prefix, "limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        resp = requests.get(
            BLOB_API, params=params, headers=_auth_headers(token), timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("blobs", []))
        if not data.get("hasMore"):
            break
        cursor = data.get("cursor")
    return results


def fetch_blob_bytes(url: str) -> bytes:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def encode_webp(png_bytes: bytes) -> bytes:
    with Image.open(BytesIO(png_bytes)) as im:
        buf = BytesIO()
        im.save(buf, format="WEBP", quality=82, method=6)
        return buf.getvalue()


def upload_webp(token: str, pathname: str, webp_bytes: bytes) -> str:
    upload_url = f"{BLOB_API}/{pathname}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "image/webp",
        "x-vercel-access": "public",
    }
    resp = requests.put(upload_url, data=webp_bytes, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json().get("url", "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prefix", default="images/")
    args = parser.parse_args()

    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        print("ERROR: BLOB_READ_WRITE_TOKEN not set (use: doppler run --)", file=sys.stderr)
        return 2

    print(f"Listing blobs under prefix={args.prefix!r}...")
    all_blobs = list_blobs(token, args.prefix)
    print(f"  found {len(all_blobs)} blobs")

    pngs = [b for b in all_blobs if b.get("pathname", "").lower().endswith(".png")]
    existing_webps = {
        b.get("pathname", "")
        for b in all_blobs
        if b.get("pathname", "").lower().endswith(".webp")
    }
    print(f"  {len(pngs)} PNG, {len(existing_webps)} WebP already present")

    todo: list[dict] = []
    for blob in pngs:
        png_pathname = blob.get("pathname", "")
        webp_pathname = png_pathname[:-4] + ".webp"
        if webp_pathname in existing_webps:
            continue
        todo.append(blob)

    print(f"  {len(todo)} PNGs missing a WebP sibling")
    if args.dry_run or not todo:
        for blob in todo:
            print(f"    would process: {blob.get('pathname')}")
        return 0

    converted = 0
    skipped = 0
    for i, blob in enumerate(todo, 1):
        png_pathname = blob.get("pathname", "")
        webp_pathname = png_pathname[:-4] + ".webp"
        url = blob.get("url", "")
        try:
            png_bytes = fetch_blob_bytes(url)
            webp_bytes = encode_webp(png_bytes)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] SKIP {png_pathname}: encode failed ({e})")
            skipped += 1
            continue
        try:
            upload_webp(token, webp_pathname, webp_bytes)
            ratio = 100 * len(webp_bytes) / max(len(png_bytes), 1)
            print(
                f"  [{i}/{len(todo)}] {png_pathname} -> {webp_pathname} "
                f"({len(webp_bytes)}B / {len(png_bytes)}B = {ratio:.0f}%)"
            )
            converted += 1
        except Exception as e:
            print(f"  [{i}/{len(todo)}] UPLOAD FAILED {webp_pathname}: {e}")
            skipped += 1

    print()
    print(f"Done. Converted {converted}, skipped {skipped}, total {len(todo)}.")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
