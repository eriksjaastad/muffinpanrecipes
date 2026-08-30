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
    """Inject GA4 into legacy stored HTML when explicitly requested.

    New reader pages carry ``GA4_TAG`` at build time.  This compatibility
    helper remains available for one-off legacy repair scripts, but public
    reader routes no longer call it, so normal requests never mutate HTML.
    """
    if not html or GA4_MEASUREMENT_ID in html:
        return html
    idx = html.find(_HEAD_OPEN)
    if idx == -1:
        logger.warning("GA4 tag not injected: no <head> in page HTML (%d bytes)", len(html))
        return html
    at = idx + len(_HEAD_OPEN)
    return f"{html[:at]}\n    {GA4_TAG}{html[at:]}"
