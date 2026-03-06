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

# How long to wait for blob propagation
PROPAGATION_MAX_WAIT = 30  # seconds
PROPAGATION_POLL_INTERVAL = 3  # seconds


def _current_iso_week() -> str:
    now = datetime.now(timezone.utc)
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


# ---------------------------------------------------------------------------
# Blob verification
# ---------------------------------------------------------------------------

def load_episode_from_blob(episode_id: str, blob_token: str) -> dict | None:
    """Load episode directly from Vercel Blob, bypassing CDN cache."""
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

    content = requests.get(blobs[0]["url"], timeout=15)
    content.raise_for_status()
    return content.json()


def wait_for_stage(
    episode_id: str,
    day: str,
    blob_token: str,
    max_wait: int = PROPAGATION_MAX_WAIT,
) -> bool:
    """Poll blob until the stage appears as 'complete'. Returns True on success."""
    start = time.monotonic()
    while time.monotonic() - start < max_wait:
        ep = load_episode_from_blob(episode_id, blob_token)
        if ep:
            stage = ep.get("stages", {}).get(day, {})
            if stage.get("status") == "complete":
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

    # Verify blob propagation
    print(f"  Verifying blob propagation (max {PROPAGATION_MAX_WAIT}s)...", end="", flush=True)
    if wait_for_stage(episode_id, day, blob_token):
        result["propagated"] = True
        result["status"] = "complete"
        print(" confirmed")
    else:
        result["status"] = "propagation_timeout"
        result["error"] = f"Stage returned 200 but not visible in blob after {PROPAGATION_MAX_WAIT}s"
        print(f" FAILED (not in blob after {PROPAGATION_MAX_WAIT}s)")

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
    ep = load_episode_from_blob(episode_id, blob_token)
    if ep:
        stages = ep.get("stages", {})
        for day in DAYS:
            status = stages.get(day, {}).get("status", "—")
            marker = "skip" if (args.skip_completed and status == "complete") else ""
            print(f"  {day:12s} {status:12s} {marker}")
    else:
        print("  (no existing episode)")

    if args.dry_run:
        print("\n--dry-run: exiting without firing stages")
        return

    # Run stages
    results = []
    for day in days_to_run:
        # Skip completed if requested
        if args.skip_completed and ep:
            existing = ep.get("stages", {}).get(day, {})
            if existing.get("status") == "complete":
                print(f"\n  {day.upper()} — already complete, skipping")
                results.append({"day": day, "status": "skipped"})
                continue

        result = run_stage(
            day=day,
            episode_id=episode_id,
            base_url=args.base_url,
            cron_secret=cron_secret,
            blob_token=blob_token,
            model=args.model,
        )
        results.append(result)

        # Stop on failure (don't waste API calls on downstream stages)
        if result["status"] not in ("complete", "skipped"):
            print(f"\n  Stopping — {day} failed with status '{result['status']}'")
            break

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
    print("\nFinal episode state:")
    ep = load_episode_from_blob(episode_id, blob_token)
    if ep:
        all_complete = True
        for day in DAYS:
            status = ep.get("stages", {}).get(day, {}).get("status", "—")
            print(f"  {day:12s} {status}")
            if status != "complete":
                all_complete = False
        if all_complete:
            print("\n  ALL STAGES COMPLETE")
    else:
        print("  (episode not found)")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
