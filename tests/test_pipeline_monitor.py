"""scripts/pipeline_monitor.py — always-on pipeline monitor (#7006).

Covers the alerting contract: alert on a change TO DEGRADED (from OK or
unknown) OR on the failure signature changing while still degraded, one
recovery notice on a change back to OK, silence on a truly unchanged
DEGRADED, and a network blip that records "unknown" without alerting and
without clobbering a known verdict on disk. Also covers the three
corrections made after Codex review of fb3fb3c:

  A. a delivery `send_alert` reports as failed must be retried on the next
     run, not recorded as delivered (`alerted` vs `status`);
  B. re-alerting on a persistent DEGRADED is keyed on a normalized failure
     signature, not merely on the status staying "degraded";
  C. two overlapping invocations must not both decide "not yet alerted" and
     both send (an exclusive file lock).

Also asserts the job exits 0 on every path, including an unhandled
exception.

Network is never touched: `sps._get_json` and the verdict functions it wraps
are stubbed directly, so these tests exercise pipeline_monitor's own
orchestration/state/alert logic, not session_pipeline_status.py's fetch
internals (covered by its own module) or episode_integrity's rules (covered
by tests/test_episode_integrity.py-equivalent coverage elsewhere).
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

from scripts import pipeline_monitor as pm


def _stub_ok(monkeypatch, *, summary: str = "2026-W40: on track"):
    """Make compute_verdict() report a healthy episode.

    `current_episode_id` is pinned rather than left to resolve from the real
    "now", so the resulting `episode_id` — and any signature computed from it
    — is stable regardless of what day the suite actually runs.
    """
    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: "2026-W40")
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: {"episode_id": "2026-W40"})
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", lambda episode, catalog=None: [])
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: summary)


def _stub_degraded(monkeypatch, *, failures=("monday stage is 'missing'",)):
    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: "2026-W40")
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


class _AlertRecorder(list):
    """A list of delivered-alert dicts that also controls whether the NEXT
    call to the patched send_alert reports success or failure, so a test can
    flip `.deliver = False` mid-run to simulate every channel being down."""

    def __init__(self):
        super().__init__()
        self.deliver = True
        self.attempts = 0  # every call, delivered or not


def _captured_alerts(monkeypatch):
    posts = _AlertRecorder()

    def _fake_send_alert(subject, body, severity="warning", **kw):
        posts.attempts += 1
        if posts.deliver:
            posts.append({"subject": subject, "body": body, "severity": severity, **kw})
        return posts.deliver

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
    assert saved["alerted"]["status"] == "degraded"
    assert saved["alerted"]["signature"] == pm.failure_signature(
        "2026-W40", ["monday stage is 'missing'"]
    )


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


def test_degraded_to_degraded_same_signature_does_not_realert(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_degraded(monkeypatch)
    pm.run(state)
    assert len(posts) == 1

    # Same failure text, run again (and again) — an unchanged incident.
    _stub_degraded(monkeypatch)
    pm.run(state)
    pm.run(state)
    assert len(posts) == 1  # still just the one alert — persistent DEGRADED is silent

    saved = json.loads(state.read_text())
    assert saved["failures"] == ["monday stage is 'missing'"]  # state still updates
    assert saved["alerted"]["status"] == "degraded"


def test_degraded_to_degraded_new_signature_alerts_once(tmp_path, monkeypatch):
    """Correction B: a DIFFERENT failure while still degraded is a new
    incident and must surface exactly one alert, not silence."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_degraded(monkeypatch, failures=("monday stage is 'missing'",))
    pm.run(state)
    assert len(posts) == 1

    _stub_degraded(monkeypatch, failures=("a completely different failure now",))
    pm.run(state)
    assert len(posts) == 2
    assert "DEGRADED" in posts[1]["subject"]

    # Re-running with that SAME new failure must go quiet again.
    pm.run(state)
    assert len(posts) == 2

    saved = json.loads(state.read_text())
    assert saved["failures"] == ["a completely different failure now"]
    assert saved["alerted"]["signature"] == pm.failure_signature(
        "2026-W40", ["a completely different failure now"]
    )


def test_new_episode_week_failing_the_same_way_alerts_once(tmp_path, monkeypatch):
    """Correction B, other half: same failure TEXT but a new episode_id (a new
    ISO week) is still a new incident, not a repeat. compute_verdict's
    episode_id comes from the --episode/current_episode_id argument used to
    build the fetch URL, not from inside the fetched payload, so we vary it
    via pm.run's episode_id parameter."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    monkeypatch.setattr(pm.sps, "_get_json", lambda url: {"episode_id": "irrelevant"})
    monkeypatch.setattr(
        pm.sps,
        "episode_integrity_failures",
        lambda episode, catalog=None: ["monday stage is 'missing'"],
    )
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "degraded")

    pm.run(state, episode_id="2026-W40")
    assert len(posts) == 1

    pm.run(state, episode_id="2026-W41")
    assert len(posts) == 2

    # And running the second week again with the SAME failure goes quiet.
    pm.run(state, episode_id="2026-W41")
    assert len(posts) == 2


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
    assert saved["alerted"] == {"status": "ok", "signature": None, "at": saved["alerted"]["at"]}


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
# Correction A: a failed delivery must be retried, never recorded as sent
# ---------------------------------------------------------------------------


def test_failed_degraded_delivery_is_retried_next_run(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    posts.deliver = False  # every channel is down

    _stub_degraded(monkeypatch)
    rc = pm.run(state)
    assert rc == 0
    assert posts.attempts == 1
    assert len(posts) == 0  # attempted, but nothing was actually delivered

    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"  # observed truth is still recorded
    assert saved["alerted"] == {"status": None, "signature": None, "at": None}  # nothing owed-off

    # Channels recover; the SAME degraded state must be retried, not skipped
    # just because "status" was already degraded last run.
    posts.deliver = True
    rc = pm.run(state)
    assert rc == 0
    assert posts.attempts == 2
    assert len(posts) == 1
    assert "DEGRADED" in posts[0]["subject"]

    saved = json.loads(state.read_text())
    assert saved["alerted"]["status"] == "degraded"

    # And now that it's been confirmed delivered, a third identical run stays quiet.
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1


def test_failed_recovery_delivery_is_retried_next_run(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _stub_degraded(monkeypatch)
    pm.run(state)
    assert len(posts) == 1

    posts.deliver = False
    _stub_ok(monkeypatch)
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1  # recovery attempted but not delivered
    saved = json.loads(state.read_text())
    assert saved["status"] == "ok"
    assert saved["alerted"]["status"] == "degraded"  # still owed: recovery never confirmed

    posts.deliver = True
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 2
    assert "recovered" in posts[1]["subject"].lower()
    saved = json.loads(state.read_text())
    assert saved["alerted"]["status"] == "ok"

    # Steady OK afterward stays silent.
    rc = pm.run(state)
    assert len(posts) == 2


# ---------------------------------------------------------------------------
# Correction B: signature must ignore volatile text (timestamps)
# ---------------------------------------------------------------------------


def test_signature_ignores_embedded_timestamp_using_real_episode_integrity_shape():
    """Uses the ACTUAL episode_integrity_failures() output shape (a stage
    failure embeds a real 'cron window closed at <date> <time> UTC' string —
    see backend/utils/episode_integrity.py) to prove the signature strips it,
    so an otherwise-identical failure never looks "new" from one hourly tick
    to the next just because of that timestamp."""
    from backend.utils.episode_integrity import episode_integrity_failures

    episode = {
        "episode_id": "2026-W40",
        "concept": "Apple Cinnamon Muffin Cups",
        "target_category": "fruit",
        "stages": {
            "monday": {"status": "complete", "target_category": "fruit"},
            "tuesday": {"status": "missing"},
        },
    }
    import datetime as _dt

    failures_a = episode_integrity_failures(
        episode, now=_dt.datetime(2026, 9, 30, 20, 0, tzinfo=_dt.timezone.utc)
    )
    failures_b = episode_integrity_failures(
        # A whole hour later: the SAME stage, so the same deadline text, but
        # this also demonstrates the strip works even if it had changed.
        episode, now=_dt.datetime(2026, 9, 30, 21, 0, tzinfo=_dt.timezone.utc)
    )
    assert failures_a and failures_b  # sanity: the fixture actually produced a failure
    assert any("UTC" in f for f in failures_a)  # sanity: a timestamp is really embedded

    sig_a = pm.failure_signature("2026-W40", failures_a)
    sig_b = pm.failure_signature("2026-W40", failures_b)
    assert sig_a == sig_b


def test_normalize_failure_strips_common_timestamp_shapes():
    assert pm._normalize_failure("closed at 2026-09-30 14:30 UTC") == "closed at <TS> UTC"
    assert pm._normalize_failure("at 2026-09-30T14:30:00Z now") == "at <TS> now"
    assert pm._normalize_failure("no timestamp here") == "no timestamp here"


def test_many_unchanged_signature_runs_across_simulated_hours_stay_silent(tmp_path, monkeypatch):
    """The literal acceptance scenario: the pipeline is degraded and stays
    degraded with the identical failure for many consecutive hourly ticks —
    exactly one alert total, never a repeat."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _stub_degraded(monkeypatch)

    for _ in range(24):  # a full day of hourly ticks
        rc = pm.run(state)
        assert rc == 0

    assert len(posts) == 1


# ---------------------------------------------------------------------------
# Correction C: overlapping invocations must not both alert
# ---------------------------------------------------------------------------


def test_second_concurrent_run_exits_zero_without_alerting_while_lock_held(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    lock_path = state.with_name(state.name + ".lock")
    posts = _captured_alerts(monkeypatch)
    _stub_degraded(monkeypatch)

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = pm.os.open(str(lock_path), pm.os.O_CREAT | pm.os.O_RDWR, 0o644)
    try:
        pm.fcntl.flock(fd, pm.fcntl.LOCK_EX | pm.fcntl.LOCK_NB)  # simulate a run in flight

        rc = pm.run(state)  # a second, overlapping invocation

        assert rc == 0
        assert posts == []  # never even computed a verdict, let alone alerted
        assert not state.exists()  # nothing written either
    finally:
        pm.fcntl.flock(fd, pm.fcntl.LOCK_UN)
        pm.os.close(fd)

    # Lock released -> the next run proceeds normally.
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1


def test_lock_is_released_after_a_run_so_the_next_one_proceeds(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _stub_degraded(monkeypatch)

    pm.run(state)
    pm.run(state)  # would deadlock/hang if the lock weren't released

    assert len(posts) == 1


def test_concurrent_runs_via_threads_alert_exactly_once(tmp_path, monkeypatch):
    """A closer-to-real race: two threads call run() at (as close to) the
    same instant as Python allows. Exactly one of them must win the lock and
    alert; the other must exit 0 having sent nothing."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _stub_degraded(monkeypatch)

    barrier = threading.Barrier(2)
    results = []

    def _worker():
        barrier.wait()
        results.append(pm.run(state))

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert results == [0, 0]
    assert len(posts) == 1  # exactly one delivered alert, never zero, never two


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


def test_compute_verdict_unknown_when_catalog_fetch_fails(monkeypatch):
    """Correction D: a failed catalog GET (_get_json returns None — that
    return value means the fetch itself failed, never a legitimately empty
    catalog, which would be `[]`/`{}`) must not fall through to computing a
    partial verdict with the title-collision check silently missing."""
    called = {"failures": False}

    def _get_json(url):
        if url.endswith("recipes.json"):
            return None
        return {"episode_id": "2026-W40"}

    def _failures(episode, catalog=None):
        called["failures"] = True
        return []

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", _failures)

    verdict = pm.compute_verdict(episode_id="2026-W40")

    assert verdict["status"] == "unknown"
    assert verdict["failures"] == []
    assert called["failures"] is False  # short-circuited, never computed a partial verdict


def test_compute_verdict_malformed_but_present_catalog_still_skips_title_check_only(monkeypatch):
    """The OTHER branch — catalog fetched successfully but in an unexpected
    shape (not None) — is not the flicker case correction D targets, and
    keeps its original behavior: skip only the title-collision check."""
    seen = {}

    def _get_json(url):
        if url.endswith("recipes.json"):
            return "not a list or dict"  # fetched fine, just malformed
        return {"episode_id": "2026-W40"}

    def _failures(episode, catalog=None):
        seen["catalog"] = catalog
        return []

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", _failures)
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "ok")

    verdict = pm.compute_verdict(episode_id="2026-W40")

    assert verdict["status"] == "ok"
    assert seen["catalog"] is None


# ---------------------------------------------------------------------------
# Correction D: a flickering catalog fetch must not manufacture a new
# signature (round 2 Codex review)
# ---------------------------------------------------------------------------


def test_flickering_catalog_fetch_does_not_repeat_alert(tmp_path, monkeypatch):
    """The exact scenario from round 2: a persistent stage failure (A) plus a
    title collision (B, only detectable when the catalog fetch succeeds),
    with the catalog fetch alternating success/failure across three runs.
    Before correction D this produced signatures A+B, A, A+B — three alerts
    for the same two problems. After it: one alert, total."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: "2026-W40")
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "2026-W40: degraded")

    def _failures(episode, catalog=None):
        failures = ["monday stage is 'missing'"]
        if catalog is not None:
            failures.append("recipe title collides with the published catalog: X")
        return failures

    monkeypatch.setattr(pm.sps, "episode_integrity_failures", _failures)

    catalog_up = True

    def _get_json(url):
        if url.endswith("recipes.json"):
            return [{"slug": "x"}] if catalog_up else None
        return {"episode_id": "2026-W40"}

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)

    # Run 1: catalog up -> both failures (A+B) -> the first-ever DEGRADED alert.
    catalog_up = True
    pm.run(state)
    assert len(posts) == 1
    signature_ab = json.loads(state.read_text())["alerted"]["signature"]

    # Run 2: catalog fetch fails -> must be "unknown", not a smaller "A-only"
    # degraded verdict that would look like a new (different) signature.
    catalog_up = False
    pm.run(state)
    assert len(posts) == 1  # still just the one alert
    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"  # untouched from run 1 (unknown never clobbers)
    assert saved["failures"] == [
        "monday stage is 'missing'",
        "recipe title collides with the published catalog: X",
    ]
    assert saved["alerted"]["signature"] == signature_ab

    # Run 3: catalog recovers -> A+B again, same signature as run 1 -> silent.
    catalog_up = True
    pm.run(state)
    assert len(posts) == 1
    assert json.loads(state.read_text())["alerted"]["signature"] == signature_ab


# ---------------------------------------------------------------------------
# Correction E: temp-file cleanup trashes, never permanently deletes
# ---------------------------------------------------------------------------


def test_write_state_trashes_orphaned_temp_file_on_failure(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    trashed = []

    monkeypatch.setattr(pm, "send2trash", lambda path: trashed.append(path))

    real_fdopen = pm.os.fdopen

    def _boom_fdopen(fd, *args, **kwargs):
        f = real_fdopen(fd, *args, **kwargs)
        f.close()
        raise RuntimeError("simulated write failure")

    monkeypatch.setattr(pm.os, "fdopen", _boom_fdopen)

    pm.write_state(state, {"status": "ok"})

    assert not state.exists()
    # send2trash (not os.unlink/os.remove) was handed the orphaned temp file.
    assert len(trashed) == 1
    assert str(trashed[0]).startswith(str(tmp_path))
    assert trashed[0].endswith(".tmp")


def test_write_state_never_calls_os_unlink():
    import inspect

    source = inspect.getsource(pm.write_state)
    assert "os.unlink" not in source
    assert "os.remove" not in source
