#!/usr/bin/env python3
"""Run a full Mon-Sun cron pipeline with read-after-write verification.

Fires each cron stage sequentially, then polls Vercel Blob to confirm the
stage data actually propagated before moving to the next day. This prevents
the CDN-propagation race condition that causes stage data to be overwritten.

Usage:
    # Run full week against production
    doppler run -- python scripts/run_full_week.py

    # Custom episode ID
    doppler run -- python scripts/run_full_week.py --episode test-001

    # Specific base URL (e.g. preview deploy)
    doppler run -- python scripts/run_full_week.py --base-url https://muffinpanrecipes-abc123.vercel.app

    # Skip days that already completed
    doppler run -- python scripts/run_full_week.py --skip-completed

    # Run only specific days
    doppler run -- python scripts/run_full_week.py --days monday,wednesday,sunday

    # Dry run (check current state only)
    doppler run -- python scripts/run_full_week.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Wednesday generates images via Stability AI — needs more time
DAY_TIMEOUTS = {
    "monday": 120,
    "tuesday": 120,
    "wednesday": 300,
    "thursday": 120,
    "friday": 120,
    "saturday": 120,
    "sunday": 120,
}

# How long to wait for blob CDN propagation between stages
DEFAULT_STAGE_DELAY = 5  # seconds — Vercel Blob CDN typically invalidates in 1-3s
PROPAGATION_MAX_WAIT = 30  # seconds (for size-based check)
PROPAGATION_POLL_INTERVAL = 3  # seconds


def _current_iso_week() -> str:
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


# ---------------------------------------------------------------------------
# Blob verification
# ---------------------------------------------------------------------------

def load_episode_from_blob(episode_id: str, blob_token: str) -> dict | None:
    """Load episode from Vercel Blob via list API.

    Note: Private blob content URLs are only accessible from within Vercel
    serverless functions. From external clients, we list blobs (to confirm
    existence) and read metadata, but cannot fetch content directly.
    """
    resp = requests.get(
        "https://blob.vercel-storage.com",
        params={"prefix": f"episodes/{episode_id}.json", "limit": "1"},
        headers={"Authorization": f"Bearer {blob_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    blobs = resp.json().get("blobs", [])
    if not blobs:
        return None

    # Return metadata (we can't read content from outside Vercel)
    blob = blobs[0]
    return {
        "_blob_exists": True,
        "_uploaded_at": blob.get("uploadedAt", ""),
        "_size": blob.get("size", 0),
        "_pathname": blob.get("pathname", ""),
    }


def load_episode_via_cron(
    episode_id: str, base_url: str, cron_secret: str,
) -> dict | None:
    """Load episode state by querying a lightweight status endpoint.

    Falls back to checking blob metadata if no status endpoint exists.
    """
    # Try the admin API (may require auth)
    try:
        resp = requests.get(
            f"{base_url}/api/cron/status",
            params={"episode_id": episode_id},
            headers={"Authorization": f"Bearer {cron_secret}"},
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def wait_for_propagation(
    episode_id: str,
    blob_token: str,
    expected_size_min: int = 0,
    max_wait: int = PROPAGATION_MAX_WAIT,
) -> bool:
    """Wait for blob write to propagate by checking blob size increases.

    After a stage completes, the episode blob size should increase.
    We poll until the size exceeds expected_size_min.
    """
    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        ep = load_episode_from_blob(episode_id, blob_token)
        if ep and ep.get("_size", 0) > expected_size_min:
            return True
        time.sleep(PROPAGATION_POLL_INTERVAL)
    return False


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

def run_stage(
    day: str,
    episode_id: str,
    base_url: str,
    cron_secret: str,
    blob_token: str,
    prev_blob_size: int = 0,
    model: str | None = None,
) -> dict:
    """Fire one cron stage and verify blob propagation. Returns result dict."""
    url = f"{base_url}/api/cron/{day}"
    timeout = DAY_TIMEOUTS.get(day, 120)

    payload: dict = {"episode_id": episode_id}
    if model:
        payload["model"] = model

    result = {
        "day": day,
        "status": "pending",
        "http_code": None,
        "response": None,
        "propagated": False,
        "blob_size": 0,
        "duration_s": 0,
        "error": None,
    }

    print(f"\n{'='*60}")
    print(f"  {day.upper()} — firing (timeout {timeout}s)")
    print(f"{'='*60}")

    t0 = time.monotonic()

    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {cron_secret}"},
            json=payload,
            timeout=timeout,
        )
        result["http_code"] = resp.status_code
        result["duration_s"] = round(time.monotonic() - t0, 1)

        try:
            result["response"] = resp.json()
        except Exception:
            result["response"] = resp.text[:500]

        if resp.status_code != 200:
            result["status"] = "failed"
            result["error"] = result["response"]
            print(f"  FAILED — HTTP {resp.status_code}")
            print(f"  {json.dumps(result['response'], indent=2)[:300]}")
            return result

        print(f"  HTTP 200 in {result['duration_s']}s")

    except requests.Timeout:
        result["status"] = "timeout"
        result["error"] = f"Request timed out after {timeout}s"
        result["duration_s"] = round(time.monotonic() - t0, 1)
        print(f"  TIMEOUT after {timeout}s")
        return result

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        result["duration_s"] = round(time.monotonic() - t0, 1)
        print(f"  ERROR: {e}")
        return result

    # Verify blob propagation via size increase
    print(f"  Verifying blob write (prev size: {prev_blob_size})...", end="", flush=True)
    if wait_for_propagation(episode_id, blob_token, expected_size_min=prev_blob_size):
        ep_meta = load_episode_from_blob(episode_id, blob_token)
        new_size = ep_meta.get("_size", 0) if ep_meta else 0
        result["propagated"] = True
        result["blob_size"] = new_size
        result["status"] = "complete"
        print(f" confirmed (size: {prev_blob_size} -> {new_size})")
    else:
        result["status"] = "propagation_timeout"
        result["error"] = f"Blob size didn't increase after {PROPAGATION_MAX_WAIT}s"
        print(f" FAILED (size unchanged after {PROPAGATION_MAX_WAIT}s)")

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run full Mon-Sun cron pipeline with propagation checks")
    parser.add_argument("--episode", default=None, help="Episode ID (default: current ISO week)")
    parser.add_argument("--base-url", default="https://muffinpanrecipes.com", help="Base URL for cron endpoints")
    parser.add_argument("--days", default=None, help="Comma-separated days to run (default: all)")
    parser.add_argument("--skip-completed", action="store_true", help="Skip days already marked complete")
    parser.add_argument("--dry-run", action="store_true", help="Check current state without firing stages")
    parser.add_argument("--model", default=None, help="Override dialogue model for all stages")
    parser.add_argument("--stage-delay", type=int, default=DEFAULT_STAGE_DELAY,
                        help=f"Seconds to wait between stages for CDN propagation (default: {DEFAULT_STAGE_DELAY})")
    args = parser.parse_args()

    # Required env vars
    cron_secret = os.environ.get("CRON_SECRET", "")
    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")

    if not cron_secret:
        print("ERROR: CRON_SECRET not set. Run with: doppler run -- python scripts/run_full_week.py")
        sys.exit(1)
    if not blob_token:
        print("ERROR: BLOB_READ_WRITE_TOKEN not set. Run with: doppler run -- python scripts/run_full_week.py")
        sys.exit(1)

    episode_id = args.episode or _current_iso_week()
    days_to_run = args.days.split(",") if args.days else DAYS

    # Validate day names
    for d in days_to_run:
        if d not in DAYS:
            print(f"ERROR: Unknown day '{d}'. Valid: {', '.join(DAYS)}")
            sys.exit(1)

    print(f"\n{'#'*60}")
    print(f"  FULL WEEK PIPELINE RUN")
    print(f"  Episode:  {episode_id}")
    print(f"  Base URL: {args.base_url}")
    print(f"  Days:     {', '.join(days_to_run)}")
    if args.model:
        print(f"  Model:    {args.model}")
    print(f"{'#'*60}")

    # Check current state
    print("\nChecking current episode state...")
    ep_meta = load_episode_from_blob(episode_id, blob_token)
    current_blob_size = 0
    if ep_meta and ep_meta.get("_blob_exists"):
        current_blob_size = ep_meta.get("_size", 0)
        print(f"  Blob exists: {current_blob_size} bytes, uploaded {ep_meta.get('_uploaded_at', '?')}")
    else:
        print("  (no existing episode)")

    if args.dry_run:
        print("\n--dry-run: exiting without firing stages")
        return

    # Run stages — track blob size to detect propagation
    results = []
    blob_size = current_blob_size

    for day in days_to_run:
        result = run_stage(
            day=day,
            episode_id=episode_id,
            base_url=args.base_url,
            cron_secret=cron_secret,
            blob_token=blob_token,
            prev_blob_size=blob_size,
            model=args.model,
        )
        results.append(result)

        # Update blob size for next stage's check
        if result.get("blob_size", 0) > blob_size:
            blob_size = result["blob_size"]

        # Stop on failure (don't waste API calls on downstream stages)
        if result["status"] not in ("complete", "skipped"):
            print(f"\n  Stopping — {day} failed with status '{result['status']}'")
            break

        # Wait for CDN propagation before next stage reads the episode
        if day != days_to_run[-1] and result["status"] == "complete" and args.stage_delay > 0:
            print(f"  Waiting {args.stage_delay}s for CDN propagation...")
            time.sleep(args.stage_delay)

    # Summary
    print(f"\n{'#'*60}")
    print(f"  RESULTS")
    print(f"{'#'*60}")

    total_cost_s = 0
    for r in results:
        status_icon = {
            "complete": "pass",
            "skipped": "skip",
            "failed": "FAIL",
            "timeout": "TOUT",
            "error": "ERR",
            "propagation_timeout": "PROP",
        }.get(r["status"], "????")

        duration = r.get("duration_s", 0)
        total_cost_s += duration
        http = r.get("http_code", "—")
        print(f"  [{status_icon:4s}] {r['day']:12s}  HTTP {str(http):4s}  {duration:6.1f}s")

    passed = sum(1 for r in results if r["status"] == "complete")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = len(results) - passed - skipped

    print(f"\n  {passed} passed, {skipped} skipped, {failed} failed")
    print(f"  Total API time: {total_cost_s:.1f}s")
    print(f"  Episode: {episode_id}")

    # Final state verification
    print("\nFinal blob state:")
    ep_meta = load_episode_from_blob(episode_id, blob_token)
    if ep_meta and ep_meta.get("_blob_exists"):
        print(f"  Blob size: {ep_meta.get('_size', 0)} bytes")
        print(f"  Last updated: {ep_meta.get('_uploaded_at', '?')}")
    else:
        print("  (episode not found in blob)")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
