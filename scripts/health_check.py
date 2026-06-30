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
import re
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
DEFAULT_STATE_FILE = str(
    Path.home() / ".local" / "state" / "muffinpanrecipes" / "health_status"
)


def _state_file() -> Path:
    # Resolved at call time so the env override actually takes effect (and so
    # tests can point it at a temp path) — a module-level constant would
    # freeze the path at import, before any override is set.
    return Path(os.environ.get("MUFFINPAN_HEALTH_STATE_FILE", DEFAULT_STATE_FILE))

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
        if len(body) > 20_000:
            return  # full episode page rendered — healthy

        # Thin page. Early in a new ISO week, /this-week is LEGITIMATELY a
        # placeholder until that week's Monday cron (14:30 UTC Mon) generates
        # the recipe. Only treat thin-ness as a failure once this week's Monday
        # stage is actually complete — otherwise it's the expected pre-cron
        # window and we must NOT alert (that was the Monday-morning false alarm).
        iso = datetime.now(timezone.utc).isocalendar()
        week_id = f"{iso.year}-W{iso.week:02d}"
        try:
            episode = _fetch_json(f"{BLOB_CDN}/episodes/{week_id}.json")
        except Exception:
            episode = None
        monday_done = (
            isinstance(episode, dict)
            and episode.get("stages", {}).get("monday", {}).get("status") == "complete"
        )
        assert not monday_done, (
            f"/this-week body is {len(body)} bytes, expected > 20000, and "
            f"{week_id} Monday IS complete — likely a real render failure."
        )
        print(
            f"    (this-week is the expected pre-cron placeholder for {week_id} "
            f"— Monday recipe not generated yet)"
        )

    report.check("this_week_renders", _check)


def _resolve_image_url(src: str) -> str:
    """Resolve a page image reference to an absolute URL we can HEAD."""
    if src.startswith("/blob-images/"):
        return f"{BLOB_CDN}/images/{src[len('/blob-images/'):]}"
    if src.startswith("/"):
        return f"{SITE_BASE}{src}"
    return src


def check_recipe_page_images(report: Report) -> None:
    """Every recipe page's hero image must actually load (HTTP 200).

    Checks the RENDERED pages, not the catalog: a recipe can carry a healthy
    catalog image while its pre-rendered blob page points at a stale/dead path.
    That is exactly the W10 lemon-meringue flat-vs-hierarchical bug — catalog
    image 200 but the rendered page 404s — which a catalog-only check misses.
    """
    def _check():
        catalog = _fetch_json(CATALOG_BLOB_URL)
        recipes = catalog if isinstance(catalog, list) else catalog.get("recipes", [])
        assert recipes, "catalog is empty — cannot verify recipe images"
        broken = []
        for r in recipes:
            slug = r.get("slug")
            if not slug:
                continue
            status, body = _fetch_text(f"{SITE_BASE}/recipes/{slug}")
            if status != 200:
                broken.append(f"{slug}: page HTTP {status}")
                continue
            # Hero image = the first real image asset(s) on the page. The nav
            # uses an inline SVG, so the hero <picture>/<img> is first. Matching
            # on the URL (not a CSS class) keeps this working before and after
            # the vanilla-CSS migration.
            srcs = re.findall(r'<(?:img[^>]+src|source[^>]+srcset)="([^"]+)"', body)
            hero = [
                s for s in srcs
                if "/blob-images/" in s or "/assets/images" in s
                or "blob.vercel-storage.com" in s
            ][:2]
            for s in hero:
                url = _resolve_image_url(s)
                try:
                    code = requests.head(url, timeout=12, allow_redirects=True).status_code
                except Exception as exc:
                    code = type(exc).__name__
                if code != 200:
                    broken.append(f"{slug}: [{code}] {s}")
        assert not broken, (
            f"{len(broken)} recipe hero image(s) failed to load:\n    "
            + "\n    ".join(broken[:10])
        )

    report.check("recipe_page_hero_images_load", _check)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def read_last_status() -> str | None:
    """Return the previous run's status ('passed'/'failed'), or None if unknown."""
    try:
        return _state_file().read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"(health state read failed: {e})", file=sys.stderr)
        return None


def write_status(status: str) -> None:
    try:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(status, encoding="utf-8")
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
    check_recipe_page_images(report)

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
