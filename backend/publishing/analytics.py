"""Google Analytics 4 tag for the public site.

Defined once and imported by every public page renderer so the tag can't
drift between `/`, `/recipes`, `/this-week`, and `/recipes/{slug}` — a
page that silently loses its tag is invisible in reporting but looks
perfectly healthy in the browser, which is exactly the kind of gap that
goes unnoticed for months.

Admin pages (`backend/admin/templates/`) deliberately do NOT carry the
tag: internal traffic would inflate sessions and skew engagement metrics
on a site whose real traffic is small enough for that to matter.

The Measurement ID is NOT a secret — gtag.js ships it in the page source
to every visitor by design — so it stays hardcoded here rather than in
Doppler. That also keeps it consistent with `src/index.html`, a static
file no Python process ever renders.
"""

from __future__ import annotations

from backend.utils.logging import get_logger

logger = get_logger(__name__)

GA4_MEASUREMENT_ID = "G-05P73D3237"

GA4_TAG = f"""<!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={GA4_MEASUREMENT_ID}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());

      gtag('config', '{GA4_MEASUREMENT_ID}');
    </script>"""

_HEAD_OPEN = "<head>"


def ensure_ga4_tag(html: str) -> str:
    """Inject the GA4 tag into stored page HTML that predates the tag.

    Recipe pages and /this-week are pre-rendered at publish time and frozen
    in blob storage, so a renderer change does NOT reach the 21 pages
    already published — they would serve untagged forever.

    Re-rendering them is the obvious fix and the wrong one: a fresh render
    also picks up every *other* renderer change since publish. As of PR #82
    that includes hero selection, which would swap 19 of 21 heroes from the
    macro close-up to the confirmed winner — a deliberate, still-open
    decision that has nothing to do with analytics. Injecting at serve time
    keeps the two concerns apart.

    Idempotent: freshly rendered pages already carry the tag and pass
    through untouched. Malformed HTML with no <head> is returned unchanged
    rather than raising — a missing analytics tag must never take down a
    live recipe page.
    """
    if not html or GA4_MEASUREMENT_ID in html:
        return html
    idx = html.find(_HEAD_OPEN)
    if idx == -1:
        # Serve the page regardless, but never silently: an untagged page
        # looks perfectly healthy in a browser and is invisible in GA4.
        logger.warning("GA4 tag not injected: no <head> in page HTML (%d bytes)", len(html))
        return html
    at = idx + len(_HEAD_OPEN)
    return f"{html[:at]}\n    {GA4_TAG}{html[at:]}"
