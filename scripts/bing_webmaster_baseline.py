#!/usr/bin/env python3
"""Bing Webmaster API baseline: crawl stats, index status, and query stats.

Card #7806. Bing Webmaster Tools is already set up — the apex
https://muffinpanrecipes.com is the verified property — and
``BING_WEBMASTER_API_KEY`` lives in Doppler (project muffinpanrecipes,
configs dev and prd). This script is the fourth SEO measurement source
alongside Screaming Frog / GSC / GA4 / Ahrefs described in SEO_RUNBOOK.md,
and follows the same on-disk convention as ``scripts/seo_weekly_crawl.sh``:
a dated folder under ``seo-audits/weekly/``, committed.

Endpoints (JSON/HTTP, the supported protocol — legacy SOAP/POX retired
2026-08-31): https://learn.microsoft.com/en-us/bingwebmaster/api-protocols

    GET https://ssl.bing.com/webmaster/api.svc/json/GetCrawlStats?siteUrl=...&apikey=...
    GET https://ssl.bing.com/webmaster/api.svc/json/GetQueryStats?siteUrl=...&apikey=...
    GET https://ssl.bing.com/webmaster/api.svc/json/GetUrlInfo?siteUrl=...&url=...&apikey=...

GetUrlInfo normally reports index details for one page, but its documented
Remarks say a "domain:" prefix on ``url`` returns index details for the whole
property instead:
https://learn.microsoft.com/en-us/dotnet/api/microsoft.bing.webmaster.api.interfaces.iwebmasterapi.geturlinfo
That is what this script uses for "index status".

THE API KEY IS A QUERY PARAMETER, not a header. ``requests`` embeds the full
request URL (key included) in its exception messages and in
``response.request.url`` — every error path here scrubs the key out before it
reaches a log, stdout, or a file.

Bounded: one request per method (no pagination loop), a 15s timeout each, and
a 429 is reported as a failure rather than retried — no sleep-and-retry loop.
A missing key or any API error exits nonzero with a message on stderr; there
is no "empty results" fallback.

Usage:
    doppler run --project muffinpanrecipes --config dev -- \\
        uv run python scripts/bing_webmaster_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus, urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]

SITE_URL = "https://muffinpanrecipes.com"
_API_BASE = "https://ssl.bing.com/webmaster/api.svc/json"
_TIMEOUT_SECONDS = 15


def _audit_dir() -> Path:
    """Same convention as scripts/seo_weekly_crawl.sh's $AUDIT_DIR: read at
    call time (not import time) so tests can point this at a temp directory
    via monkeypatch.setenv without the module already having cached the
    default.
    """
    return Path(os.environ.get("SEO_AUDIT_DIR", REPO_ROOT / "seo-audits"))


class BingApiError(RuntimeError):
    """Any Bing Webmaster API failure. Message is guaranteed key-scrubbed."""


def _scrub(text: str, api_key: str) -> str:
    """Remove the apikey query value, raw or URL-encoded, from anything about
    to be logged/printed."""
    for form in {api_key, quote(api_key, safe=""), quote_plus(api_key)}:
        text = text.replace(form, "***")
    return text


def _call(method: str, api_key: str, expected: type, **params: str):
    """GET one Bing Webmaster API JSON method and return its ``d`` payload,
    which must be of the documented type ``expected``.

    Every BingApiError is raised OUTSIDE the ``except`` block that caught the
    underlying error, so it carries no ``__cause__``/``__context__``: the
    requests exception embeds the full URL, apikey included, and a chained
    traceback would print it unscrubbed.
    """
    query = {"apikey": api_key, "siteUrl": SITE_URL, **params}
    failure = None
    try:
        response = requests.get(f"{_API_BASE}/{method}", params=query, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        failure = f"{method} request failed: {_scrub(f'{type(exc).__name__}: {exc}', api_key)}"
    if failure:
        raise BingApiError(failure)

    if response.status_code == 429:
        # No sleep-and-retry: a rate limit is a stop-and-report, not a loop.
        raise BingApiError(f"{method}: rate limited (HTTP 429); not retrying")
    if response.status_code != 200:
        raise BingApiError(
            f"{method} failed: HTTP {response.status_code}: "
            f"{_scrub(response.text[:500], api_key)}"
        )

    body = None
    try:
        body = response.json()
    except ValueError as exc:
        failure = f"{method}: response was not valid JSON: {type(exc).__name__}"
    if failure:
        raise BingApiError(failure)
    if not isinstance(body, dict) or "d" not in body:
        raise BingApiError(f"{method}: unexpected response shape (no 'd' key)")
    # A null or wrong-typed payload is a malformed response, not an empty
    # result: GetCrawlStats/GetQueryStats return lists, GetUrlInfo an object
    # (Microsoft's IWebmasterApi reference, cited in the module docstring).
    if not isinstance(body["d"], expected):
        raise BingApiError(
            f"{method}: expected {expected.__name__} in 'd', got {type(body['d']).__name__}"
        )
    return body["d"]


def fetch_baseline(api_key: str) -> dict:
    """Pull crawl stats, query stats, and domain-level index status. Raises on any failure."""
    domain = urlparse(SITE_URL).netloc
    return {
        "crawl_stats": _call("GetCrawlStats", api_key, list),
        "query_stats": _call("GetQueryStats", api_key, list),
        "index_status": _call("GetUrlInfo", api_key, dict, url=f"domain:{domain}"),
    }


def main() -> int:
    api_key = os.environ.get("BING_WEBMASTER_API_KEY")
    if not api_key:
        print(
            "FATAL: BING_WEBMASTER_API_KEY is not set. Run via:\n"
            "  doppler run --project muffinpanrecipes --config dev -- "
            "uv run python scripts/bing_webmaster_baseline.py",
            file=sys.stderr,
        )
        return 1

    try:
        data = fetch_baseline(api_key)
    except BingApiError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    out_dir = _audit_dir() / "weekly" / now.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "bing_webmaster.json"
    out_path.write_text(
        json.dumps(
            {
                "captured_at": now.isoformat(),
                "site_url": SITE_URL,
                **data,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
