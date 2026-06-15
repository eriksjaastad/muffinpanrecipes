#!/usr/bin/env python3
"""Production health check for muffinpanrecipes (#5918).

Read-only synthetic monitor. Asserts production invariants that would
have caught the #5911 test-mode contamination incident within 60 seconds.
Exits 0 on pass, non-zero on any failure. Optionally posts a Discord
alert when `MUFFINPAN_DISCORD_WEBHOOK` is set.

Run modes:
    # Manual
    doppler run -- uv run python scripts/health_check.py

    # Fail on catalog drop
    uv run python scripts/health_check.py --baseline 15

    # Post-deploy (CI)
    uv run python scripts/health_check.py --strict

See RUNBOOK.md Incident 1 for the incident this is designed to catch.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

import requests

# Persisted last-run status so we only ping "recovered" on an actual
# FAIL -> PASS transition (not on every healthy run). The synthetic monitor
# runs from a fixed machine, so a small on-disk file is enough; override the
# path with MUFFINPAN_HEALTH_STATE_FILE if it ever runs somewhere ephemeral.
STATE_FILE = Path(
    os.environ.get(
        "MUFFINPAN_HEALTH_STATE_FILE",
        str(Path.home() / ".local" / "state" / "muffinpanrecipes" / "health_status"),
    )
)

SITE_BASE = "https://muffinpanrecipes.com"
BLOB_CDN = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
CATALOG_SITE_URL = f"{SITE_BASE}/recipes.json"
CATALOG_BLOB_URL = f"{BLOB_CDN}/pages/recipes.json"
TEASER_URL = f"{SITE_BASE}/api/episodes/teaser"
THIS_WEEK_URL = f"{SITE_BASE}/this-week"


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    def check(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
            self.passed.append(name)
            print(f"  ✓ {name}")
        except AssertionError as e:
            self.failed.append((name, str(e)))
            print(f"  ✗ {name}: {e}")
        except Exception as e:
            self.failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"  ✗ {name}: {type(e).__name__}: {e}")

    @property
    def ok(self) -> bool:
        return not self.failed


def _fetch_json(url: str, timeout: int = 15) -> dict | list:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _fetch_text(url: str, timeout: int = 15) -> tuple[int, str]:
    resp = requests.get(url, timeout=timeout)
    return resp.status_code, resp.text


def current_iso_week_id() -> str:
    iso_year, iso_week, _ = date.today().isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def check_catalog_counts_match(report: Report, baseline: int) -> None:
    def _check():
        site_catalog = _fetch_json(CATALOG_SITE_URL)
        blob_catalog = _fetch_json(CATALOG_BLOB_URL)
        site_count = len(site_catalog) if isinstance(site_catalog, list) else len(site_catalog.get("recipes", []))
        blob_count = len(blob_catalog) if isinstance(blob_catalog, list) else len(blob_catalog.get("recipes", []))
        assert site_count == blob_count, (
            f"site catalog has {site_count} recipes but blob has {blob_count}. "
            f"Drift here means FastAPI is reading stale or prefixed data."
        )
        assert blob_count >= baseline, (
            f"blob catalog has {blob_count} recipes, expected >= {baseline}. "
            f"Catalog shrinkage means something deleted or overwrote entries."
        )

    report.check("catalog_counts_match_baseline", _check)


def check_teaser_current_week(report: Report) -> None:
    def _check():
        data = _fetch_json(TEASER_URL)
        assert isinstance(data, dict), f"teaser returned non-dict: {type(data).__name__}"
        # On Sunday after publish, the endpoint suppresses the teaser
        # (read-side check in episode_routes.py) so the homepage Featured
        # hero isn't duplicated. {"status":"published"} is a healthy state.
        if data.get("status") == "published":
            return
        episode_id = data.get("episode_id") or ""
        assert episode_id, "teaser response missing episode_id"
        assert not episode_id.startswith("test-"), (
            f"teaser episode_id starts with 'test-': {episode_id!r}. "
            f"This is the #5911 contamination signature — test-mode prefix "
            f"is leaking into production reads."
        )
        expected = current_iso_week_id()
        assert episode_id == expected, (
            f"teaser episode_id is {episode_id!r}, expected {expected!r} (current ISO week)"
        )

    report.check("teaser_is_current_iso_week", _check)


def check_this_week_page(report: Report) -> None:
    def _check():
        status, body = _fetch_text(THIS_WEEK_URL)
        assert status == 200, f"/this-week returned HTTP {status}"
        assert len(body) > 20_000, (
            f"/this-week body is {len(body)} bytes, expected > 20000. "
            f"Page is suspiciously thin — likely a render failure or empty episode."
        )

    report.check("this_week_renders", _check)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def read_last_status() -> str | None:
    """Return the previous run's status ('passed'/'failed'), or None if unknown."""
    try:
        return STATE_FILE.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"(health state read failed: {e})", file=sys.stderr)
        return None


def write_status(status: str) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(status, encoding="utf-8")
    except Exception as e:
        print(f"(health state write failed: {e})", file=sys.stderr)


def _post_discord(content: str) -> None:
    webhook = os.environ.get("MUFFINPAN_DISCORD_WEBHOOK")
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": content[:1900]}, timeout=10)
    except Exception as e:
        print(f"(Discord post failed: {e})", file=sys.stderr)


def post_discord_alert(report: Report) -> None:
    # Timestamp so a scrolled-back alert can't be mistaken for a live failure.
    lines = [f"🚨 **health_check.py FAILED** — {_utc_stamp()}", ""]
    for name, detail in report.failed:
        lines.append(f"• **{name}**: {detail[:300]}")
    _post_discord("\n".join(lines))


def post_discord_recovery(report: Report) -> None:
    names = ", ".join(report.passed)
    _post_discord(
        f"✅ **health_check.py RECOVERED** — {_utc_stamp()} — "
        f"all {len(report.passed)} checks passing again ({names})."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline", type=int, default=15,
        help="Minimum expected catalog count. Fails if blob catalog has fewer recipes.",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero on ANY failure. Without this, exits 0 but still prints.",
    )
    args = parser.parse_args()

    print(f"Health check against {SITE_BASE}")
    report = Report()
    check_catalog_counts_match(report, args.baseline)
    check_teaser_current_week(report)
    check_this_week_page(report)

    print()
    print(f"Passed: {len(report.passed)}  Failed: {len(report.failed)}")

    last_status = read_last_status()

    if not report.ok:
        post_discord_alert(report)
        write_status("failed")
        return 1  # Always non-zero on failure; --strict reserved for future nuance.

    # Healthy — only announce recovery when the previous run was failing,
    # so steady-state passes stay silent.
    if last_status == "failed":
        post_discord_recovery(report)
    write_status("passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
