"""scripts/pipeline_monitor.py — always-on pipeline monitor (#7006).

Covers the transition-only alerting contract: alert on a change TO DEGRADED
(from OK or unknown), one recovery notice on a change back to OK, silence on
a persistent DEGRADED, and a network blip that records "unknown" without
alerting and without clobbering a known verdict on disk. Also asserts the
job exits 0 on every path, including an unhandled exception.

Network is never touched: `sps._get_json` and the verdict functions it wraps
are stubbed directly, so these tests exercise pipeline_monitor's own
orchestration/state/alert logic, not session_pipeline_status.py's fetch
internals (covered by its own module) or episode_integrity's rules (covered
by tests/test_episode_integrity.py-equivalent coverage elsewhere).
"""
from __future__ import annotations

import json
from unittest.mock import patch

from scripts import pipeline_monitor as pm


def _stub_ok(monkeypatch, *, summary: str = "2026-W40: on track"):
    """Make compute_verdict() report a healthy episode."""
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: {"episode_id": "2026-W40"})
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", lambda episode, catalog=None: [])
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: summary)


def _stub_degraded(monkeypatch, *, failures=("monday stage is 'missing'",)):
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: {"episode_id": "2026-W40"})
    monkeypatch.setattr(
        pm.sps, "episode_integrity_failures", lambda episode, catalog=None: list(failures)
    )
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "2026-W40: degraded")


def _stub_network_failure(monkeypatch):
    """Simulate the fetch itself failing — sps._get_json already swallows
    network exceptions and returns None; compute_verdict must turn that into
    'unknown'."""
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: None)


def _captured_alerts(monkeypatch):
    posts: list[dict] = []

    def _fake_send_alert(subject, body, severity="warning", **kw):
        posts.append({"subject": subject, "body": body, "severity": severity, **kw})
        return True

    monkeypatch.setattr(pm, "send_alert", _fake_send_alert)
    return posts


# ---------------------------------------------------------------------------
# Transition alerting
# ---------------------------------------------------------------------------


def test_ok_to_degraded_alerts_once(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_ok(monkeypatch)
    rc1 = pm.run(state)
    assert rc1 == 0
    assert posts == []  # first-ever OK run: nothing was degraded before

    _stub_degraded(monkeypatch)
    rc2 = pm.run(state)
    assert rc2 == 0
    assert len(posts) == 1
    assert "DEGRADED" in posts[0]["subject"]
    assert posts[0]["severity"] == "warning"

    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"
    assert saved["failures"] == ["monday stage is 'missing'"]
    assert "checked_at" in saved


def test_first_run_degraded_alerts_immediately(tmp_path, monkeypatch):
    # No prior state at all -> "from unknown" per the card's own wording, and
    # the failure needs to be caught the very first time it's seen.
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _stub_degraded(monkeypatch)

    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1
    assert "DEGRADED" in posts[0]["subject"]


def test_degraded_to_degraded_does_not_realert(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_degraded(monkeypatch)
    pm.run(state)
    assert len(posts) == 1

    _stub_degraded(monkeypatch, failures=("a different failure now",))
    pm.run(state)
    assert len(posts) == 1  # still just the one alert — persistent DEGRADED is silent

    saved = json.loads(state.read_text())
    assert saved["failures"] == ["a different failure now"]  # state still updates


def test_degraded_to_ok_sends_one_recovery(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_degraded(monkeypatch)
    pm.run(state)
    assert len(posts) == 1

    _stub_ok(monkeypatch)
    pm.run(state)
    assert len(posts) == 2
    assert "recovered" in posts[1]["subject"].lower()
    assert posts[1]["severity"] == "info"

    saved = json.loads(state.read_text())
    assert saved["status"] == "ok"


def test_ok_to_ok_stays_silent(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_ok(monkeypatch)
    pm.run(state)
    pm.run(state)
    assert posts == []


# ---------------------------------------------------------------------------
# Network failure / "unknown" handling
# ---------------------------------------------------------------------------


def test_network_failure_records_unknown_without_alerting(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _stub_network_failure(monkeypatch)

    rc = pm.run(state)
    assert rc == 0
    assert posts == []
    saved = json.loads(state.read_text())
    assert saved["status"] == "unknown"


def test_network_failure_does_not_clobber_known_ok_verdict(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_ok(monkeypatch, summary="2026-W40: on track")
    pm.run(state)
    before = json.loads(state.read_text())

    _stub_network_failure(monkeypatch)
    rc = pm.run(state)
    assert rc == 0
    assert posts == []

    after = json.loads(state.read_text())
    assert after == before  # untouched — a blip must not erase the last known verdict


def test_network_failure_does_not_clobber_known_degraded_verdict(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_degraded(monkeypatch)
    pm.run(state)
    before = json.loads(state.read_text())
    assert len(posts) == 1

    _stub_network_failure(monkeypatch)
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1  # no re-alert, no phantom recovery

    after = json.loads(state.read_text())
    assert after == before


# ---------------------------------------------------------------------------
# Never blocks
# ---------------------------------------------------------------------------


def test_unhandled_exception_in_main_still_exits_zero(tmp_path, monkeypatch):
    """`_safe_main()` is the exact function `__main__` calls — an unhandled
    exception anywhere inside `main()`/`run()`/`compute_verdict()` must not
    produce a nonzero exit or propagate (card constraint: never block)."""
    state = tmp_path / "pipeline_status.json"
    monkeypatch.setattr(pm.sys, "argv", ["pipeline_monitor.py", "--state-file", str(state)])

    with patch.object(pm, "run", side_effect=RuntimeError("boom")):
        assert pm._safe_main() == 0


def test_exception_inside_compute_verdict_still_exits_zero(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    monkeypatch.setattr(pm.sys, "argv", ["pipeline_monitor.py", "--state-file", str(state)])

    def _boom(url):
        raise RuntimeError("everything is on fire")

    monkeypatch.setattr(pm.sps, "_get_json", _boom)
    assert pm._safe_main() == 0


def test_first_run_ok_writes_unknown_never_persisted_as_status(tmp_path, monkeypatch):
    # Sanity: "unknown" is never the value written for a resolved verdict —
    # only ok/degraded are persisted once a real fetch has succeeded.
    state = tmp_path / "pipeline_status.json"
    _captured_alerts(monkeypatch)
    _stub_ok(monkeypatch)
    pm.run(state)
    saved = json.loads(state.read_text())
    assert saved["status"] in ("ok", "degraded")


# ---------------------------------------------------------------------------
# compute_verdict reuses session_pipeline_status, doesn't reimplement it
# ---------------------------------------------------------------------------


def test_compute_verdict_unknown_when_episode_unreadable(monkeypatch):
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: None)
    verdict = pm.compute_verdict()
    assert verdict["status"] == "unknown"
    assert verdict["failures"] == []


def test_compute_verdict_uses_sps_current_episode_id(monkeypatch):
    calls = []

    def _get_json(url):
        calls.append(url)
        return None

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: "2026-W99")
    pm.compute_verdict()
    assert calls == [f"{pm.sps.BLOB_CDN}/episodes/2026-W99.json"]


def test_compute_verdict_passes_catalog_list_through(monkeypatch):
    seen = {}

    def _get_json(url):
        if url.endswith("recipes.json"):
            return [{"slug": "a"}]
        return {"episode_id": "2026-W40"}

    def _failures(episode, catalog=None):
        seen["catalog"] = catalog
        return []

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", _failures)
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "ok")
    pm.compute_verdict()
    assert seen["catalog"] == [{"slug": "a"}]
