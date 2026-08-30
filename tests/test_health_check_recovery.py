"""health_check.py recovery notification — ping 'all clear' only on FAIL->PASS.

A failing run alerts Discord; a healthy run should stay silent UNLESS the
previous run was failing, in which case it announces recovery. This is the
fix for a stale failure alert lingering for days with no 'working again'
counterpart (W24, 2026-06-10).
"""
from __future__ import annotations

import importlib
from unittest.mock import patch

hc = importlib.import_module("scripts.health_check")


def _valid_headers():
    return {
        "x-frame-options": "DENY",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin-when-cross-origin",
        "content-security-policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data: https://www.google-analytics.com https://*.google-analytics.com; "
            "connect-src 'self' https://www.google-analytics.com "
            "https://*.google-analytics.com https://*.analytics.google.com "
            "https://*.googletagmanager.com; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        ),
    }


def _run(monkeypatch, tmp_path, *, healthy: bool):
    """Run main() with the 3 network checks stubbed to pass/fail, capturing
    Discord posts. Returns the list of posted message strings."""
    monkeypatch.setenv("MUFFINPAN_HEALTH_STATE_FILE", str(tmp_path / "status"))
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://discord.test/webhook")

    def _catalog(report, baseline, *, base_url):
        report.passed.append("catalog_counts_match_baseline") if healthy \
            else report.failed.append(("catalog_counts_match_baseline", "drift"))

    def _teaser(report, *, base_url):
        report.passed.append("teaser_is_current_iso_week")

    def _this_week(report, *, base_url):
        report.passed.append("this_week_renders") if healthy \
            else report.failed.append(("this_week_renders", "thin page"))

    def _pass(report, *, base_url):
        report.passed.append("stubbed")

    posts: list[str] = []
    with patch.object(hc, "check_catalog_counts_match", _catalog), \
         patch.object(hc, "check_teaser_current_week", _teaser), \
         patch.object(hc, "check_this_week_page", _this_week), \
         patch.object(hc, "check_recipe_page_images", _pass), \
         patch.object(hc, "check_sitemap_pages", _pass), \
         patch.object(hc, "check_static_security_headers", _pass), \
         patch.object(hc, "check_unmatched_url_404", _pass), \
         patch.object(hc, "requests") as req, \
         patch.object(hc.sys, "argv", ["health_check.py"]):
        req.post.side_effect = lambda url, json, timeout: posts.append(json["content"])
        rc = hc.main()
    return rc, posts


def test_failing_run_alerts_and_records_failed(monkeypatch, tmp_path):
    rc, posts = _run(monkeypatch, tmp_path, healthy=False)
    assert rc == 1
    assert len(posts) == 1
    assert "FAILED" in posts[0]
    assert hc.read_last_status() == "failed"


def test_recovery_ping_fires_after_a_failure(monkeypatch, tmp_path):
    # First a failure...
    _run(monkeypatch, tmp_path, healthy=False)
    # ...then a healthy run must announce recovery.
    rc, posts = _run(monkeypatch, tmp_path, healthy=True)
    assert rc == 0
    assert len(posts) == 1
    assert "RECOVERED" in posts[0]
    assert "passing again" in posts[0]
    assert hc.read_last_status() == "passed"


def test_steady_healthy_runs_stay_silent(monkeypatch, tmp_path):
    _run(monkeypatch, tmp_path, healthy=True)         # first pass: no prior state
    rc, posts = _run(monkeypatch, tmp_path, healthy=True)  # second pass
    assert rc == 0
    assert posts == []  # no spam when already healthy


def test_first_ever_run_healthy_does_not_announce_recovery(monkeypatch, tmp_path):
    rc, posts = _run(monkeypatch, tmp_path, healthy=True)
    assert rc == 0
    assert posts == []  # no state file yet -> not a recovery


def test_messages_carry_a_utc_timestamp(monkeypatch, tmp_path):
    _, fail_posts = _run(monkeypatch, tmp_path, healthy=False)
    _, ok_posts = _run(monkeypatch, tmp_path, healthy=True)
    assert "UTC" in fail_posts[0]
    assert "UTC" in ok_posts[0]


# ---------------------------------------------------------------------------
# this_week_renders must NOT false-alarm on the Monday pre-cron placeholder
# (2026-06-22: two false alerts fired during the legitimate pre-cron window).
# ---------------------------------------------------------------------------

def _run_this_week(*, body_len: int, episode):
    """Run check_this_week_page with /this-week sized to body_len and the
    current-week episode JSON stubbed (episode=None simulates a 404)."""
    def _fake_json(url, timeout=15):
        if episode is None:
            raise RuntimeError("404 not found")
        return episode

    report = hc.Report()
    with patch.object(hc, "_fetch_text", lambda url, timeout=15: (200, "x" * body_len)), \
         patch.object(hc, "_fetch_json", _fake_json):
        hc.check_this_week_page(report)
    return report


def test_this_week_full_page_passes():
    r = _run_this_week(body_len=25_000, episode=None)
    assert "this_week_renders" in r.passed and not r.failed


def test_this_week_thin_before_monday_cron_passes():
    # New ISO week, episode not generated yet (404) -> placeholder is expected.
    r = _run_this_week(body_len=1585, episode=None)
    assert "this_week_renders" in r.passed and not r.failed


def test_this_week_thin_with_monday_incomplete_passes():
    # Episode exists but Monday not complete yet -> still the pre-cron window.
    ep = {"stages": {"monday": {"status": None}}}
    r = _run_this_week(body_len=1585, episode=ep)
    assert "this_week_renders" in r.passed and not r.failed


def test_this_week_thin_when_monday_complete_fails():
    # Monday IS complete but the page is thin -> a REAL render failure.
    ep = {"stages": {"monday": {"status": "complete"}}}
    r = _run_this_week(body_len=1585, episode=ep)
    assert r.failed and r.failed[0][0] == "this_week_renders"


# ---------------------------------------------------------------------------
# check_recipe_page_images must catch a broken HERO image on the RENDERED page
# even when the catalog image is fine (the W10 lemon-meringue 404, 2026-06-29).
# ---------------------------------------------------------------------------

_HERO = '<img src="/blob-images/abc/round_1/macro_closeup.png" alt="x">'


def _run_recipe_images(*, recipes, body="", page_status=200, head_status=200):
    """Run check_recipe_page_images with the catalog, recipe page, and image
    HEAD all stubbed. head_status applies to every HEADed image."""
    class _Resp:
        def __init__(self, code):
            self.status_code = code

    report = hc.Report()
    with patch.object(hc, "_fetch_json", lambda url, timeout=15: {"recipes": recipes}), \
         patch.object(hc, "_fetch_text", lambda url, timeout=15: (page_status, body)), \
         patch.object(hc, "requests") as req:
        req.head.return_value = _Resp(head_status)
        hc.check_recipe_page_images(report)
    return report


def test_recipe_images_all_ok_passes():
    r = _run_recipe_images(recipes=[{"slug": "good-cups"}], body=_HERO, head_status=200)
    assert "recipe_page_hero_images_load" in r.passed and not r.failed


def test_recipe_images_404_hero_fails_and_names_recipe():
    r = _run_recipe_images(
        recipes=[{"slug": "mini-lemon-meringue-cups"}], body=_HERO, head_status=404
    )
    assert r.failed and r.failed[0][0] == "recipe_page_hero_images_load"
    assert "mini-lemon-meringue-cups" in r.failed[0][1]


def test_recipe_images_page_itself_404_is_flagged():
    r = _run_recipe_images(recipes=[{"slug": "gone"}], body="", page_status=404)
    assert r.failed and "gone" in r.failed[0][1]


def test_recipe_images_placeholder_with_no_image_passes():
    # A page with no real image asset (e.g. "Photo coming Wednesday") has
    # nothing to HEAD -> it must NOT fail, even if HEADs would 500.
    r = _run_recipe_images(
        recipes=[{"slug": "pending"}], body="<div>Photo coming Wednesday</div>", head_status=500
    )
    assert "recipe_page_hero_images_load" in r.passed and not r.failed


class _Response:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _public_html(path: str, *, recipe: bool = False, duplicate_ga4: bool = False) -> str:
    base = "https://preview.example"
    ga4 = f"""
      <script async src="https://www.googletagmanager.com/gtag/js?id={hc.GA4_MEASUREMENT_ID}"></script>
      <script>gtag('config', '{hc.GA4_MEASUREMENT_ID}');</script>
    """
    if duplicate_ga4:
        ga4 += ga4
    recipe_json = ""
    hero = ""
    if recipe:
        recipe_json = """
        <script type="application/ld+json">{
          "@context": "https://schema.org",
          "@type": "Recipe",
          "name": "Apple Cups",
          "image": ["https://preview.example/blob-images/apple.jpg"],
          "recipeIngredient": ["1 apple"],
          "recipeInstructions": [{"@type": "HowToStep", "text": "Bake."}]
        }</script>
        """
        hero = '<img src="/blob-images/apple.jpg" width="1024" height="768" alt="Apple Cups">'
    return f"""
    <html><head>{ga4}
      <link rel="canonical" href="{base}{path}">
      <meta property="og:url" content="{base}{path}">
      {recipe_json}
    </head><body>{hero}</body></html>
    """


def test_sitemap_pages_rebases_absolute_locations_and_prints_each_url(capsys):
    base = "https://preview.example"
    sitemap = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://muffinpanrecipes.com/</loc></url>
      <url><loc>https://muffinpanrecipes.com/recipes</loc></url>
      <url><loc>https://muffinpanrecipes.com/recipes/apple-cups</loc></url>
    </urlset>
    """
    pages = {
        f"{base}/": _public_html("/"),
        f"{base}/recipes": _public_html("/recipes"),
        f"{base}/recipes/apple-cups": _public_html("/recipes/apple-cups", recipe=True),
    }
    requested = []

    def get(url, timeout):
        requested.append(url)
        if url == f"{base}/sitemap.xml":
            return _Response(text=sitemap)
        return _Response(text=pages[url], headers=_valid_headers())

    report = hc.Report()
    with patch.object(hc.requests, "get", side_effect=get), patch.object(
        hc.requests, "head", return_value=_Response()
    ):
        hc.check_sitemap_pages(report, base)

    assert report.ok
    assert requested == [
        f"{base}/sitemap.xml",
        f"{base}/",
        f"{base}/recipes",
        f"{base}/recipes/apple-cups",
    ]
    output = capsys.readouterr().out
    assert "PASS /recipes/apple-cups" in output
    assert "muffinpanrecipes.com/recipes/apple-cups" not in output


def test_sitemap_page_failure_reports_url_and_exact_ga4_failure(capsys):
    base = "https://preview.example"
    sitemap = '<urlset><url><loc>https://preview.example/recipes/bad</loc></url></urlset>'
    page = _public_html("/recipes/bad", recipe=True, duplicate_ga4=True)
    report = hc.Report()
    with patch.object(
        hc, "_fetch_text", return_value=(200, sitemap)
    ), patch.object(
        hc, "_fetch_page",
        return_value=(200, page, _valid_headers()),
    ), patch.object(hc.requests, "head", return_value=_Response()):
        hc.check_sitemap_pages(report, base)

    assert report.failed and report.failed[0][0] == "sitemap_pages"
    assert "/recipes/bad" in report.failed[0][1]
    assert "GA4 loader occurs 2 times" in report.failed[0][1]
    assert "FAIL /recipes/bad" in capsys.readouterr().out


def test_preview_main_failure_has_no_alert_or_status_side_effects(monkeypatch):
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://discord.test/webhook")

    def _fail(report, *args, **kwargs):
        report.failed.append(("preview_check", "expected failure"))

    with patch.object(hc, "check_catalog_counts_match", _fail), \
         patch.object(hc, "check_teaser_current_week", _fail), \
         patch.object(hc, "check_this_week_page", _fail), \
         patch.object(hc, "check_recipe_page_images", _fail), \
         patch.object(hc, "check_sitemap_pages", _fail), \
         patch.object(hc, "check_static_security_headers", _fail), \
         patch.object(hc, "check_unmatched_url_404", _fail), \
         patch.object(hc, "post_discord_alert") as alert, \
         patch.object(hc, "post_discord_recovery") as recovery, \
         patch.object(hc, "write_status") as write_status, \
         patch.object(hc.sys, "argv", [
             "health_check.py", "--base-url", "https://preview.example"
         ]):
        rc = hc.main()

    assert rc == 1
    alert.assert_not_called()
    recovery.assert_not_called()
    write_status.assert_not_called()


def test_static_headers_and_unmatched_url_are_checked():
    base = "https://preview.example"
    headers = _valid_headers()
    responses = {
        f"{base}/": _Response(headers=headers),
        f"{base}{hc.UNMATCHED_PATH}": _Response(status_code=404, headers=headers),
    }
    with patch.object(hc, "_fetch_page", side_effect=lambda url: (
        responses[url].status_code, responses[url].text, responses[url].headers
    )):
        report = hc.Report()
        hc.check_static_security_headers(report, base)
        hc.check_unmatched_url_404(report, base)
    assert report.ok


def test_recipe_json_ld_and_dimensions_reject_bad_markup():
    valid = _public_html("/recipes/apple-cups", recipe=True)
    hc._check_recipe_json_ld(valid)
    hc._check_intrinsic_image_dimensions(valid)

    try:
        hc._check_intrinsic_image_dimensions(valid.replace('height="768"', ""))
    except AssertionError as exc:
        assert "height" in str(exc)
    else:
        raise AssertionError("missing intrinsic height was not rejected")


def test_csp_must_allow_google_tag_in_script_sources():
    hc._check_csp({"content-security-policy": _valid_headers()["content-security-policy"]})
    try:
        hc._check_csp({
            "content-security-policy": "default-src 'self'; connect-src https://www.googletagmanager.com"
        })
    except AssertionError as exc:
        assert "required directives" in str(exc)
    else:
        raise AssertionError("CSP allowing GTM only in connect-src was accepted")


def _assert_csp_rejected(csp: str, expected_detail: str) -> None:
    try:
        hc._check_csp({"content-security-policy": csp})
    except AssertionError as exc:
        assert expected_detail in str(exc)
    else:
        raise AssertionError("CSP with an unauthorized policy entry was accepted")


def test_csp_rejects_extra_script_source():
    csp = _valid_headers()["content-security-policy"]
    _assert_csp_rejected(
        csp.replace(
            "https://www.googletagmanager.com",
            "https://www.googletagmanager.com https://evil.example",
        ),
        "script-src",
    )


def test_csp_rejects_extra_style_source():
    csp = _valid_headers()["content-security-policy"]
    _assert_csp_rejected(
        csp.replace(
            "https://fonts.googleapis.com",
            "https://fonts.googleapis.com https://evil.example",
        ),
        "style-src",
    )


def test_csp_rejects_extra_connect_source():
    csp = _valid_headers()["content-security-policy"]
    _assert_csp_rejected(
        csp.replace(
            "https://*.googletagmanager.com",
            "https://*.googletagmanager.com https://evil.example",
        ),
        "connect-src",
    )


def test_csp_rejects_extra_directive():
    csp = _valid_headers()["content-security-policy"] + "; media-src 'self'"
    _assert_csp_rejected(csp, "media-src")


def test_preview_catalog_check_does_not_fetch_production_catalog():
    base = "https://preview.example"
    requested = []

    def fetch(url, timeout=15):
        requested.append(url)
        return {"recipes": [{"slug": "preview-cup"}]}

    report = hc.Report()
    with patch.object(hc, "_fetch_json", fetch):
        hc.check_catalog_counts_match(report, baseline=1, base_url=base)

    assert report.ok
    assert requested == [f"{base}/recipes.json"]
    assert hc.CATALOG_BLOB_URL not in requested


def test_static_security_headers_reject_wrong_value():
    base = "https://preview.example"
    headers = _valid_headers()
    headers["x-frame-options"] = "SAMEORIGIN"
    responses = {
        f"{base}/": _Response(headers=headers),
        f"{base}{hc.UNMATCHED_PATH}": _Response(status_code=404, headers=headers),
    }
    with patch.object(hc, "_fetch_page", side_effect=lambda url: (
        responses[url].status_code, responses[url].text, responses[url].headers
    )):
        report = hc.Report()
        hc.check_static_security_headers(report, base)
    assert report.failed
    assert "expected 'DENY'" in report.failed[0][1]
