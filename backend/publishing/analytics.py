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

GA4_MEASUREMENT_ID = "G-05P73D3237"

GA4_TAG = f"""<!-- Google tag (gtag.js) -->
    <script async src="https://www.googletagmanager.com/gtag/js?id={GA4_MEASUREMENT_ID}"></script>
    <script>
      window.dataLayer = window.dataLayer || [];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js', new Date());

      gtag('config', '{GA4_MEASUREMENT_ID}');
    </script>"""
