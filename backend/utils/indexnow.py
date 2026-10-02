"""IndexNow submission client (card #7806).

IndexNow tells participating search engines (Bing, Yandex, Seznam, and — via
Bing — pushed onward to others) about a URL the moment it changes, instead of
waiting for their next crawl. Spec: https://www.indexnow.org/documentation

The key is NOT a secret. IndexNow proves site ownership by making the key
fetchable from the site itself (``https://<host>/<key>.txt``), so anyone can
read it — that's the point, not a leak. It lives as a plain constant here,
and the matching file is committed at ``src/<INDEXNOW_KEY>.txt`` (served at
the site root via a `vercel.json` route next to `BingSiteAuth.xml`). This is
the opposite of `scripts/bing_webmaster_baseline.py`'s `BING_WEBMASTER_API_KEY`,
which is a real credential from Doppler and must never reach a log or an
exception message.

One attempt, one call, a short timeout, no retry loop — the portfolio Cost
Doctrine (max 3 retries, no sleep-and-retry on a rate limit) is satisfied
trivially here because there is nothing to retry: `submit_urls` never raises,
it reports. `backend/admin/cron_routes.py`'s Sunday publish hook is a live
publishing path that must never fail or block on this, so it reads the
returned `IndexNowResult` and logs/records the outcome itself rather than
catching an exception.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Generated once with `python3 -c "import secrets; print(secrets.token_hex(16))"`
# (32 hex chars, well within the spec's 8-128 hex character range). Public by
# design — see module docstring — so there is no rotation procedure and no
# Doppler entry.
INDEXNOW_KEY = "015dad665f3a6ddeeab4565e96d0dd50"
INDEXNOW_HOST = "muffinpanrecipes.com"
INDEXNOW_KEY_LOCATION = f"https://{INDEXNOW_HOST}/{INDEXNOW_KEY}.txt"

# The shared multi-engine endpoint from the spec: a single POST here is
# relayed to every participating search engine, so callers do not need a
# separate submission per engine.
_ENDPOINT = "https://api.indexnow.org/indexnow"
_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class IndexNowResult:
    """Outcome of one submission attempt. Never an exception — see module docstring."""

    ok: bool
    status_code: int | None
    detail: str


def submit_urls(urls: list[str]) -> IndexNowResult:
    """Submit changed URLs to IndexNow. One attempt; never raises.

    Per spec, 200 and 202 both mean the submission was accepted (202 = key
    validation still pending). Anything else, including 429, is reported as a
    failure and not retried — there is no sleep-and-retry here by
    construction, satisfying the portfolio rule against it.
    """
    if not urls:
        return IndexNowResult(ok=False, status_code=None, detail="no urls given")

    payload = {
        "host": INDEXNOW_HOST,
        "key": INDEXNOW_KEY,
        "keyLocation": INDEXNOW_KEY_LOCATION,
        "urlList": urls,
    }
    try:
        response = requests.post(_ENDPOINT, json=payload, timeout=_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        # Nothing to scrub here (the key is public), unlike the Bing
        # Webmaster API client in scripts/bing_webmaster_baseline.py.
        return IndexNowResult(ok=False, status_code=None, detail=f"{type(exc).__name__}: {exc}")

    if response.status_code in (200, 202):
        return IndexNowResult(ok=True, status_code=response.status_code, detail="submitted")

    return IndexNowResult(
        ok=False,
        status_code=response.status_code,
        detail=f"IndexNow returned HTTP {response.status_code}: {response.text[:200]}",
    )
