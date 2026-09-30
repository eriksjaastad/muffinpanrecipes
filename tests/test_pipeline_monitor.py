"""scripts/pipeline_monitor.py — always-on pipeline monitor (#7006).

Covers the round-3 per-check, per-failure-id alerting model (see the module
docstring for the full design): a failure is identified by
`<group>\\x1f<episode_id>\\x1f<normalized text>`, where `group` is
"episode" (always runs once the episode itself is fetched) or "catalog"
(only runs when the separately-fetched catalog is usable). Each run:
  - a NEW id (never before confirmed-alerted) triggers one alert;
  - an id whose check did NOT run this cycle is left exactly as it was
    (neither cleared nor re-alerted) — this is what makes a flickering or
    malformed catalog response inert instead of a repeat-alert generator;
  - a full recovery (every alerted id cleared, nothing new) triggers one
    recovery alert;
  - every alert only updates persisted state on a CONFIRMED delivery
    (`send_alert` returning True) — a failed delivery is retried next run.

Also covers: `"unknown"` appears on disk ONLY on a first-ever run whose
episode fetch failed (constraint 3, otherwise a network hiccup never
clobbers a known verdict); the lock guarding concurrent runs; write_state's
mkstemp + send2trash cleanup; and that the job exits 0 on every path,
including an unhandled exception.

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


def _install_pipeline(
    monkeypatch,
    *,
    episode_id: str = "2026-W40",
    episode_ok: bool = True,
    episode_only_failures=(),
    catalog_only_failures=(),
    catalog_state: str = "up",  # "up" | "down" | "malformed"
    summary: str | None = None,
):
    """Stub session_pipeline_status's fetch + verdict primitives so
    compute_verdict() sees a controlled scenario.

    `catalog_state`:
      - "up": the catalog fetch returns a usable list (the catalog-dependent
        check runs).
      - "down": the catalog fetch fails (_get_json returns None — the
        catalog-dependent check does not run this cycle).
      - "malformed": the catalog fetch SUCCEEDS but returns an unexpected
        shape (also: the catalog-dependent check does not run this cycle).
        Round 2's fix only handled "down"; round 3 must handle both the
        same way (finding 2).

    `episode_integrity_failures` is stubbed to return `episode_only_failures`
    unconditionally, plus `catalog_only_failures` ONLY when called with a
    non-None `catalog` — mirroring the real function's contract (title-
    collision is the one check gated on `catalog is not None`).
    """
    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: episode_id)

    def _get_json(url):
        if url.endswith("recipes.json"):
            if catalog_state == "up":
                return [{"slug": "x"}]
            if catalog_state == "malformed":
                return "not-a-list-or-a-dict"
            return None  # "down"
        if not episode_ok:
            return None
        return {"episode_id": episode_id}

    def _failures(episode, catalog=None, now=None):
        failures = list(episode_only_failures)
        if catalog is not None:
            failures = failures + list(catalog_only_failures)
        return failures

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", _failures)
    monkeypatch.setattr(
        pm.sps, "episode_summary", lambda episode: summary or f"{episode_id}: status"
    )


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
# Basic transitions
# ---------------------------------------------------------------------------


def test_ok_to_degraded_alerts_once(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=[])
    rc1 = pm.run(state)
    assert rc1 == 0
    assert posts == []
    assert json.loads(state.read_text())["status"] == "ok"

    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])
    rc2 = pm.run(state)
    assert rc2 == 0
    assert len(posts) == 1
    assert "DEGRADED" in posts[0]["subject"]
    assert posts[0]["severity"] == "warning"

    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"
    assert saved["failures"] == ["monday stage is 'missing'"]
    assert list(saved["alerted_failures"].values()) == ["monday stage is 'missing'"]
    assert "checked_at" in saved
    assert saved["alerted_at"] is not None


def test_first_run_degraded_alerts_immediately(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])

    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1
    assert "DEGRADED" in posts[0]["subject"]


def test_persistent_identical_failure_stays_silent_across_24_runs(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])

    for _ in range(24):  # a full day of hourly ticks
        rc = pm.run(state)
        assert rc == 0

    assert len(posts) == 1


def test_a_second_distinct_failure_alerts_once_while_first_persists(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["failure A"])
    pm.run(state)
    assert len(posts) == 1

    _install_pipeline(monkeypatch, episode_only_failures=["failure A", "failure B"])
    pm.run(state)
    assert len(posts) == 2
    assert "DEGRADED" in posts[1]["subject"]
    assert "failure B" in posts[1]["body"]

    # Steady with both still present -> silent.
    pm.run(state)
    assert len(posts) == 2

    saved = json.loads(state.read_text())
    assert sorted(saved["failures"]) == ["failure A", "failure B"]


def test_partial_recovery_without_new_failures_stays_silent(tmp_path, monkeypatch):
    """One of two active failures clearing, with nothing new, is not a FULL
    recovery — no alert, but the cleared id must actually drop out of
    alerted_failures so a later full recovery can still fire."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["failure A", "failure B"])
    pm.run(state)
    assert len(posts) == 1

    _install_pipeline(monkeypatch, episode_only_failures=["failure B"])  # A recovered
    pm.run(state)
    assert len(posts) == 1  # no alert for a partial clear
    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"
    assert list(saved["alerted_failures"].values()) == ["failure B"]

    # Now the remaining failure clears too -> FULL recovery -> one alert.
    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    assert len(posts) == 2
    assert "recovered" in posts[1]["subject"].lower()
    saved = json.loads(state.read_text())
    assert saved["status"] == "ok"
    assert saved["alerted_failures"] == {}


def test_degraded_to_ok_sends_one_recovery(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])
    pm.run(state)
    assert len(posts) == 1

    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    assert len(posts) == 2
    assert "recovered" in posts[1]["subject"].lower()
    assert posts[1]["severity"] == "info"

    saved = json.loads(state.read_text())
    assert saved["status"] == "ok"
    assert saved["alerted_failures"] == {}


def test_ok_to_ok_stays_silent(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    pm.run(state)
    assert posts == []


def test_new_episode_week_failing_the_same_way_alerts_once(tmp_path, monkeypatch):
    """Same failure TEXT but a new episode_id (a new ISO week) is still a new
    incident — the id embeds episode_id — and the old week's alerted id
    quietly clears since the episode check re-ran and no longer reports it."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(
        monkeypatch, episode_id="2026-W40", episode_only_failures=["monday stage is 'missing'"]
    )
    pm.run(state)
    assert len(posts) == 1

    _install_pipeline(
        monkeypatch, episode_id="2026-W41", episode_only_failures=["monday stage is 'missing'"]
    )
    pm.run(state)
    assert len(posts) == 2

    saved = json.loads(state.read_text())
    assert saved["episode_id"] == "2026-W41"
    assert len(saved["alerted_failures"]) == 1  # the old W40 id rolled off

    # Running the new week again with the SAME failure goes quiet.
    pm.run(state)
    assert len(posts) == 2


# ---------------------------------------------------------------------------
# Findings 1 & 2 (round 3): a catalog outage or malformed response must not
# manufacture a new/removed failure and must not block an unrelated,
# genuinely new episode-group failure from alerting.
# ---------------------------------------------------------------------------


def test_flickering_catalog_down_does_not_repeat_alert(tmp_path, monkeypatch):
    """The literal round-3 finding-1 scenario: a persistent stage failure (A,
    episode group) plus a title collision (B, catalog group), with the
    catalog fetch failing on the middle run. Exactly one alert, total."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(
        monkeypatch,
        episode_only_failures=["stage A"],
        catalog_only_failures=["title collision B"],
        catalog_state="up",
    )
    pm.run(state)
    assert len(posts) == 1
    alerted_after_run1 = json.loads(state.read_text())["alerted_failures"]
    assert len(alerted_after_run1) == 2

    _install_pipeline(
        monkeypatch,
        episode_only_failures=["stage A"],
        catalog_only_failures=["title collision B"],
        catalog_state="down",
    )
    pm.run(state)
    assert len(posts) == 1  # no re-alert
    saved = json.loads(state.read_text())
    assert saved["checks_ran"] == ["episode"]
    assert saved["failures"] == ["stage A"]  # only what THIS run's checks could see
    assert saved["alerted_failures"] == alerted_after_run1  # untouched, not cleared

    _install_pipeline(
        monkeypatch,
        episode_only_failures=["stage A"],
        catalog_only_failures=["title collision B"],
        catalog_state="up",
    )
    pm.run(state)
    assert len(posts) == 1  # still silent
    assert json.loads(state.read_text())["alerted_failures"] == alerted_after_run1


def test_flickering_catalog_malformed_does_not_repeat_alert(tmp_path, monkeypatch):
    """Finding 2: a catalog response that is present but the wrong SHAPE
    (not a fetch failure) must behave identically to catalog_state='down'."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(
        monkeypatch,
        episode_only_failures=["stage A"],
        catalog_only_failures=["title collision B"],
        catalog_state="up",
    )
    pm.run(state)
    assert len(posts) == 1

    _install_pipeline(
        monkeypatch,
        episode_only_failures=["stage A"],
        catalog_only_failures=["title collision B"],
        catalog_state="malformed",
    )
    pm.run(state)
    assert len(posts) == 1
    saved = json.loads(state.read_text())
    assert saved["checks_ran"] == ["episode"]

    _install_pipeline(
        monkeypatch,
        episode_only_failures=["stage A"],
        catalog_only_failures=["title collision B"],
        catalog_state="up",
    )
    pm.run(state)
    assert len(posts) == 1


def test_new_stage_failure_while_catalog_down_alerts_immediately(tmp_path, monkeypatch):
    """Finding 1's core complaint: round 2's "whole verdict unknown" hid a
    genuinely new episode-group failure for as long as the catalog was down.
    The episode group must alert regardless of catalog health."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=[], catalog_state="up")
    pm.run(state)
    assert posts == []

    _install_pipeline(monkeypatch, episode_only_failures=["new stage failure"], catalog_state="down")
    pm.run(state)
    assert len(posts) == 1
    assert "new stage failure" in posts[0]["body"]
    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"
    assert saved["checks_ran"] == ["episode"]


def test_stage_failure_then_recovers_while_catalog_stays_down(tmp_path, monkeypatch):
    """Continuation of the above: the stage recovers while the catalog is
    STILL down — the episode check re-ran and confirmed it's gone, so a full
    recovery must fire even though catalog health is unknown throughout."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["new stage failure"], catalog_state="down")
    pm.run(state)
    assert len(posts) == 1

    _install_pipeline(monkeypatch, episode_only_failures=[], catalog_state="down")
    pm.run(state)
    assert len(posts) == 2
    assert "recovered" in posts[1]["subject"].lower()
    saved = json.loads(state.read_text())
    assert saved["status"] == "ok"
    assert saved["alerted_failures"] == {}
    assert saved["checks_ran"] == ["episode"]  # catalog was down the whole time


# ---------------------------------------------------------------------------
# Confirmed delivery only (round 1 correction A, generalized to sets)
# ---------------------------------------------------------------------------


def test_failed_new_failure_delivery_is_retried_next_run(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    posts.deliver = False

    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])
    rc = pm.run(state)
    assert rc == 0
    assert posts.attempts == 1
    assert len(posts) == 0

    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"  # observed truth still recorded
    assert saved["alerted_failures"] == {}  # nothing confirmed delivered

    posts.deliver = True
    rc = pm.run(state)
    assert rc == 0
    assert posts.attempts == 2
    assert len(posts) == 1

    saved = json.loads(state.read_text())
    assert list(saved["alerted_failures"].values()) == ["monday stage is 'missing'"]

    rc = pm.run(state)
    assert len(posts) == 1  # confirmed now, stays quiet


def test_failed_recovery_delivery_is_retried_and_keeps_status_degraded(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])
    pm.run(state)
    assert len(posts) == 1

    posts.deliver = False
    _install_pipeline(monkeypatch, episode_only_failures=[])
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1  # recovery attempted, not delivered
    saved = json.loads(state.read_text())
    # The recovery was never confirmed, so the FULL prior set is kept "owed"
    # — status stays degraded even though nothing is currently observed as
    # failing, because the all-clear was never actually announced.
    assert list(saved["alerted_failures"].values()) == ["monday stage is 'missing'"]
    assert saved["status"] == "degraded"
    assert saved["failures"] == []  # what was actually observed this run

    posts.deliver = True
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 2
    assert "recovered" in posts[1]["subject"].lower()
    saved = json.loads(state.read_text())
    assert saved["alerted_failures"] == {}
    assert saved["status"] == "ok"

    rc = pm.run(state)
    assert len(posts) == 2  # steady OK afterward


# ---------------------------------------------------------------------------
# "unknown" only ever appears on a first-ever run (constraint 3 + finding 3)
# ---------------------------------------------------------------------------


def test_episode_fetch_failure_on_first_run_writes_unknown(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_ok=False)

    rc = pm.run(state)
    assert rc == 0
    assert posts == []
    saved = json.loads(state.read_text())
    assert saved["status"] == "unknown"
    assert saved["failures"] == []
    assert saved["checks_ran"] == []
    assert saved["alerted_failures"] == {}
    assert saved["alerted_at"] is None


def test_episode_fetch_failure_does_not_clobber_known_ok_verdict(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    before = json.loads(state.read_text())

    _install_pipeline(monkeypatch, episode_ok=False)
    rc = pm.run(state)
    assert rc == 0
    assert posts == []

    after = json.loads(state.read_text())
    assert after == before  # untouched — a blip must not erase the last known verdict


def test_episode_fetch_failure_does_not_clobber_known_degraded_verdict(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])
    pm.run(state)
    before = json.loads(state.read_text())
    assert len(posts) == 1

    _install_pipeline(monkeypatch, episode_ok=False)
    rc = pm.run(state)
    assert rc == 0
    assert len(posts) == 1  # no re-alert, no phantom recovery

    after = json.loads(state.read_text())
    assert after == before


# ---------------------------------------------------------------------------
# Correction C (round 1): overlapping invocations must not both alert
# ---------------------------------------------------------------------------


def test_second_concurrent_run_exits_zero_without_alerting_while_lock_held(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    lock_path = state.with_name(state.name + ".lock")
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])

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

    rc = pm.run(state)  # lock released -> proceeds normally
    assert rc == 0
    assert len(posts) == 1


def test_lock_is_released_after_a_run_so_the_next_one_proceeds(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])

    pm.run(state)
    pm.run(state)  # would deadlock/hang if the lock weren't released

    assert len(posts) == 1


def test_concurrent_runs_via_threads_alert_exactly_once(tmp_path, monkeypatch):
    """A closer-to-real race: two threads call run() at (as close to) the
    same instant as Python allows. Exactly one delivered alert regardless of
    interleaving, since the decision logic is idempotent even if both
    threads eventually reach _run_locked sequentially."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])

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
    assert len(posts) == 1


# ---------------------------------------------------------------------------
# Correction E (round 2): temp-file cleanup trashes, never permanently deletes
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
    assert len(trashed) == 1
    assert str(trashed[0]).startswith(str(tmp_path))
    assert trashed[0].endswith(".tmp")


def test_write_state_never_calls_os_unlink():
    import inspect

    source = inspect.getsource(pm.write_state)
    assert "os.unlink" not in source
    assert "os.remove" not in source


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


# ---------------------------------------------------------------------------
# compute_verdict reuses session_pipeline_status, doesn't reimplement it
# ---------------------------------------------------------------------------


def test_compute_verdict_unknown_when_episode_fetch_fails(monkeypatch):
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: None)
    verdict = pm.compute_verdict(episode_id="2026-W40")
    assert verdict["kind"] == "unknown"
    assert verdict["episode_id"] == "2026-W40"


def test_compute_verdict_uses_sps_current_episode_id(monkeypatch):
    calls = []

    def _get_json(url):
        calls.append(url)
        return None

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: "2026-W99")
    pm.compute_verdict()
    assert calls == [f"{pm.sps.BLOB_CDN}/episodes/2026-W99.json"]


def test_compute_verdict_marks_catalog_group_when_catalog_usable(monkeypatch):
    def _get_json(url):
        if url.endswith("recipes.json"):
            return [{"slug": "a"}]
        return {"episode_id": "2026-W40"}

    seen = {}

    def _failures(episode, catalog=None, now=None):
        seen["catalog"] = catalog
        return []

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", _failures)
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "ok")

    verdict = pm.compute_verdict(episode_id="2026-W40")
    assert verdict["kind"] == "observed"
    assert set(verdict["checks_ran"]) == {"episode", "catalog"}
    assert seen["catalog"] == [{"slug": "a"}]


def test_compute_verdict_omits_catalog_group_when_fetch_fails(monkeypatch):
    called_with_catalog = []

    def _get_json(url):
        if url.endswith("recipes.json"):
            return None
        return {"episode_id": "2026-W40"}

    def _failures(episode, catalog=None, now=None):
        called_with_catalog.append(catalog)
        return []

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(pm.sps, "episode_integrity_failures", _failures)
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "ok")

    verdict = pm.compute_verdict(episode_id="2026-W40")
    assert verdict["kind"] == "observed"
    assert verdict["checks_ran"] == ("episode",)
    assert called_with_catalog == [None]  # episode_integrity_failures called exactly ONCE


def test_compute_verdict_omits_catalog_group_when_malformed(monkeypatch):
    def _get_json(url):
        if url.endswith("recipes.json"):
            return 12345  # fetched fine, just the wrong shape
        return {"episode_id": "2026-W40"}

    monkeypatch.setattr(pm.sps, "_get_json", _get_json)
    monkeypatch.setattr(
        pm.sps, "episode_integrity_failures", lambda episode, catalog=None, now=None: []
    )
    monkeypatch.setattr(pm.sps, "episode_summary", lambda episode: "ok")

    verdict = pm.compute_verdict(episode_id="2026-W40")
    assert verdict["checks_ran"] == ("episode",)


def test_failure_ids_embed_group_and_episode_and_ignore_timestamps():
    id_a = pm._failure_id("episode", "2026-W40", "closed at 2026-09-30 14:30 UTC")
    id_b = pm._failure_id("episode", "2026-W40", "closed at 2026-09-30 21:00 UTC")
    assert id_a == id_b  # timestamp-only difference collapses to the same id
    assert pm._failure_id_group(id_a) == "episode"

    id_other_episode = pm._failure_id("episode", "2026-W41", "closed at 2026-09-30 14:30 UTC")
    assert id_other_episode != id_a  # different week -> different id

    id_catalog = pm._failure_id("catalog", "2026-W40", "closed at 2026-09-30 14:30 UTC")
    assert id_catalog != id_a  # different group -> different id
    assert pm._failure_id_group(id_catalog) == "catalog"


def test_failure_id_stable_using_real_episode_integrity_shape():
    """Uses the ACTUAL episode_integrity_failures() output shape (a stage
    failure embeds a real 'cron window closed at <date> <time> UTC' string —
    see backend/utils/episode_integrity.py) to prove ids built from it are
    stable across an hour passing, so a persistent failure's id never
    "changes" just because time moved on."""
    import datetime as _dt

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
    failures_a = episode_integrity_failures(
        episode, now=_dt.datetime(2026, 9, 30, 20, 0, tzinfo=_dt.timezone.utc)
    )
    failures_b = episode_integrity_failures(
        episode, now=_dt.datetime(2026, 9, 30, 21, 0, tzinfo=_dt.timezone.utc)
    )
    assert failures_a and failures_b  # sanity: the fixture really produces a failure
    assert any("UTC" in f for f in failures_a)  # sanity: a timestamp is really embedded

    ids_a = {pm._failure_id("episode", "2026-W40", f) for f in failures_a}
    ids_b = {pm._failure_id("episode", "2026-W40", f) for f in failures_b}
    assert ids_a == ids_b
