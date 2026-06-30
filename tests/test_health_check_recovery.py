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


def _run(monkeypatch, tmp_path, *, healthy: bool):
    """Run main() with the 3 network checks stubbed to pass/fail, capturing
    Discord posts. Returns the list of posted message strings."""
    monkeypatch.setenv("MUFFINPAN_HEALTH_STATE_FILE", str(tmp_path / "status"))
    monkeypatch.setenv("MUFFINPAN_DISCORD_WEBHOOK", "https://discord.test/webhook")

    def _catalog(report, baseline):
        report.passed.append("catalog_counts_match_baseline") if healthy \
            else report.failed.append(("catalog_counts_match_baseline", "drift"))

    def _teaser(report):
        report.passed.append("teaser_is_current_iso_week")

    def _this_week(report):
        report.passed.append("this_week_renders") if healthy \
            else report.failed.append(("this_week_renders", "thin page"))

    posts: list[str] = []
    with patch.object(hc, "check_catalog_counts_match", _catalog), \
         patch.object(hc, "check_teaser_current_week", _teaser), \
         patch.object(hc, "check_this_week_page", _this_week), \
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
            raise Exception("404 not found")
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
