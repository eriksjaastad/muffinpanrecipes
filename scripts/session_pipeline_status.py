#!/usr/bin/env python3
"""One-line pipeline verdict for a fresh agent's first context (#6857).

Erik, 2026-09-05: "Anytime I start you up, you should know of any and all
failures in this project."

A Monday failure went undetected until the following Saturday, and only then
because a pre-flight happened to open the episode JSON by hand. Every status
was `complete`, the judge PASSed all six days, and the site rendered fine.
This prints the one line that would have said otherwise.

Design constraints, in priority order:
  1. NEVER block a session. Exits 0 no matter what, including on total
     network failure, and bounds itself with a hard timeout.
  2. Cheap. Two public blob GETs, no credentials, no Doppler, no API calls.
  3. Quiet when healthy. One line. Detail only on failure.

Usage:
    uv run python scripts/session_pipeline_status.py
    uv run python scripts/session_pipeline_status.py --episode 2026-W36
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.episode_integrity import (  # noqa: E402
    current_episode_id,
    episode_integrity_failures,
    episode_summary,
)

BLOB_CDN = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
TIMEOUT_SECONDS = 6
LABEL = "muffinpanrecipes pipeline"


def _get_json(url: str) -> object | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "muffinpanrecipes-session-status/1.0"}
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        # Deliberately silent: a flaky network must never turn into noise in
        # every session banner. The "unknown" verdict below says enough.
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--episode",
        help="ISO week to inspect, e.g. 2026-W36 (default: the current week)",
    )
    args = parser.parse_args()

    episode_id = args.episode or current_episode_id()
    episode = _get_json(f"{BLOB_CDN}/episodes/{episode_id}.json")

    if not isinstance(episode, dict):
        print(f"{LABEL}: unknown — could not read {episode_id} from blob")
        return 0

    raw_catalog = _get_json(f"{BLOB_CDN}/pages/recipes.json")
    catalog: list[dict] | None
    if isinstance(raw_catalog, list):
        catalog = raw_catalog
    elif isinstance(raw_catalog, dict):
        catalog = raw_catalog.get("recipes", [])
    else:
        catalog = None  # skips only the title-collision assertion

    failures = episode_integrity_failures(episode, catalog=catalog)
    summary = episode_summary(episode)

    if not failures:
        print(f"{LABEL}: OK — {summary}")
        return 0

    print(f"{LABEL}: DEGRADED — {summary}")
    for failure in failures:
        print(f"  x {failure}")
    print(
        "  -> RUNBOOK.md, and `doppler run -- uv run python "
        "scripts/health_check.py --no-alert` for the full picture"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover - last-resort guard
        # Constraint 1 wins over everything else: a crash in the status
        # banner must not be the reason a session fails to start.
        print(f"{LABEL}: unknown ({type(exc).__name__})")
        sys.exit(0)
