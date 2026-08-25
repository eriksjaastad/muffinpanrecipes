"""Backfill deterministic JPEG siblings for existing PNG blobs.

New PNG uploads create same-dimensions ``.jpg`` and 1200x630 ``.social.jpg``
siblings in the storage layer. This script handles historical images and is
idempotent: existing siblings are skipped. It lists candidates by default and
only downloads/uploads when ``--upload`` is explicitly supplied.

Run with secrets injected by Doppler, for example::

    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/backfill_social_images.py
    doppler run --project muffinpanrecipes --config prd -- \
        uv run python scripts/backfill_social_images.py --upload

The ``BLOB_READ_WRITE_TOKEN`` value is used only in request headers and is
never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from backend.storage import (
    _encode_jpeg,
    _encode_social_jpeg,
    _jpeg_key,
    _social_jpeg_key,
)

BLOB_API = "https://blob.vercel-storage.com"


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
        response = requests.get(
            BLOB_API,
            params=params,
            headers=_auth_headers(token),
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        results.extend(data.get("blobs", []))
        if not data.get("hasMore"):
            break
        cursor = data.get("cursor")
    return results


def fetch_blob_bytes(url: str) -> bytes:
    """Fetch a source PNG from its Blob URL."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    return response.content


def upload_jpeg(token: str, pathname: str, jpeg_bytes: bytes) -> str:
    """Upload one JPEG at its deterministic Blob pathname."""
    upload_url = f"{BLOB_API}/{pathname}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "image/jpeg",
        "x-vercel-access": "public",
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
    }
    response = requests.put(
        upload_url,
        data=jpeg_bytes,
        headers=headers,
        timeout=120,
    )
    response.raise_for_status()
    return response.json().get("url", "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Download, convert, and upload missing siblings. Default is dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List missing siblings without downloading or uploading (default).",
    )
    parser.add_argument("--prefix", default="images/", help="Blob prefix to scan.")
    args = parser.parse_args(argv)

    if args.upload and args.dry_run:
        parser.error("--upload and --dry-run cannot be used together")

    token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    if not token:
        print("ERROR: BLOB_READ_WRITE_TOKEN not set (use: doppler run --)", file=sys.stderr)
        return 2

    try:
        all_blobs = list_blobs(token, args.prefix)
    except Exception:  # noqa: BLE001 - keep credentials and request details out of output
        # Do not echo exception text: request errors can contain connection
        # details, and credentials must never appear in command output.
        print("ERROR: could not list Blob images", file=sys.stderr)
        return 1

    pngs = [
        blob
        for blob in all_blobs
        if blob.get("pathname", "").lower().endswith(".png")
    ]
    existing_jpegs = {
        blob.get("pathname", "").lower()
        for blob in all_blobs
        if blob.get("pathname", "").lower().endswith(".jpg")
        and not blob.get("pathname", "").lower().endswith(".social.jpg")
    }
    existing_social = {
        blob.get("pathname", "").lower()
        for blob in all_blobs
        if blob.get("pathname", "").lower().endswith(".social.jpg")
    }
    todo = [
        blob
        for blob in pngs
        if (
            _jpeg_key(blob["pathname"]).lower() not in existing_jpegs
            or _social_jpeg_key(blob["pathname"]).lower() not in existing_social
        )
    ]

    mode = "upload" if args.upload else "dry-run"
    print(
        f"JPEG image backfill ({mode}): {len(pngs)} PNGs, "
        f"{len(existing_jpegs)} page JPEGs, {len(existing_social)} social JPEGs, "
        f"{len(todo)} PNGs with missing siblings"
    )

    if not args.upload:
        for blob in todo:
            print(
                f"  would process: {blob['pathname']} -> "
                f"{_jpeg_key(blob['pathname'])}, "
                f"{_social_jpeg_key(blob['pathname'])}"
            )
        return 0

    converted = 0
    failed = 0
    for index, blob in enumerate(todo, 1):
        png_pathname = blob["pathname"]
        social_pathname = _social_jpeg_key(png_pathname)
        try:
            png_bytes = fetch_blob_bytes(blob.get("url", ""))
            jpeg_pathname = _jpeg_key(png_pathname)
            if jpeg_pathname.lower() not in existing_jpegs:
                upload_jpeg(token, jpeg_pathname, _encode_jpeg(png_bytes))
            if social_pathname.lower() not in existing_social:
                upload_jpeg(token, social_pathname, _encode_social_jpeg(png_bytes))
        except Exception:  # noqa: BLE001 - continue the idempotent batch after one bad image
            # Continue so one corrupt image or transient upload does not stop
            # the rest of the idempotent backfill.
            print(f"  [{index}/{len(todo)}] FAILED {png_pathname}")
            failed += 1
            continue
        print(f"  [{index}/{len(todo)}] uploaded {social_pathname}")
        converted += 1

    print(f"Done. Uploaded {converted}, failed {failed}, total {len(todo)}.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
