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

import pytest

from scripts import pipeline_monitor as pm


@pytest.fixture(autouse=True)
def _no_absence_probe(monkeypatch):
    """No test touches the network: the 404 probe defaults to "not
    confirmed absent". Tests of the missing-episode path override it."""
    monkeypatch.setattr(pm, "_episode_confirmed_absent", lambda eid: False)


def _install_pipeline(
    monkeypatch,
    *,
    episode_id: str = "2026-W40",
    episode_ok: bool = True,
    episode_only_failures=(),
    catalog_only_failures=(),
    catalog_state: str = "up",
    summary: str | None = None,
):
    """Stub session_pipeline_status's fetch + verdict primitives so
    compute_verdict() sees a controlled scenario.

    `catalog_state`:
      - "up": the catalog fetch returns a usable list of recipe dicts (the
        catalog-dependent check runs).
      - "down": the catalog fetch fails (_get_json returns None — the
        catalog-dependent check does not run this cycle).
      - "malformed": the catalog fetch SUCCEEDS but returns an unexpected
        top-level shape (a bare string) — the check does not run.
      - "error_dict": a dict with no "recipes" key at all, e.g.
        {"error": "temporary failure"} — round 4 finding 1: this used to
        satisfy isinstance(..., dict), and `.get("recipes", [])` silently
        coerced it to [], treating the catalog group as "ran, found nothing."
      - "recipes_not_list": {"recipes": "invalid"} — "recipes" present but
        not a list.
      - "list_of_non_dicts": a top-level list whose items aren't dicts.
      - "dict_missing_recipes": a dict with unrelated keys and no "recipes".
      - "entry_missing_title" / "entry_blank_title" / "entry_non_string_title"
        / "recipes_entry_missing_title": dict entries without a usable title
        (round 7) — the title check skips them, so it would "run" against too
        few titles.
      All four malformed-but-PRESENT variants (as opposed to "down", a fetch
      failure) must behave identically to "down": the catalog group did not
      run this cycle.

    `episode_integrity_failures` is stubbed to return `episode_only_failures`
    unconditionally, plus `catalog_only_failures` ONLY when called with a
    non-None `catalog` — mirroring the real function's contract (title-
    collision is the one check gated on `catalog is not None`).
    """
    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: episode_id)

    catalog_payloads = {
        "up": [{"slug": "x", "title": "Something"}],
        "down": None,
        "malformed": "not-a-list-or-a-dict",
        "error_dict": {"error": "temporary failure"},
        "recipes_not_list": {"recipes": "invalid"},
        "list_of_non_dicts": ["a", "b", "c"],
        "dict_missing_recipes": {"foo": "bar"},
        "entry_missing_title": [{}],
        "entry_blank_title": [{"slug": "x", "title": "   "}],
        "entry_non_string_title": [{"slug": "x", "title": 5}],
        "recipes_entry_missing_title": {"recipes": [{"title": "ok"}, {"slug": "x"}]},
    }

    def _get_json(url):
        if url.endswith("recipes.json"):
            return catalog_payloads[catalog_state]
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

    monkeypatch.setattr(pm, "send_alert_confirming_email", _fake_send_alert)
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
    assert posts[0]["subject"] == "Pipeline degraded"
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
    assert posts[0]["subject"] == "Pipeline degraded"


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
    assert posts[1]["subject"] == "Pipeline degraded"
    assert posts[1]["body"] == "2026-W40 · 2 issues"
    assert set(json.loads(state.read_text())["alerted_failures"].values()) == {"failure A", "failure B"}

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
    # W40's id is never verified gone (W40 is no longer checked), so it is
    # held as retired until the final recovery alert can name it (round 12).
    assert len(saved["alerted_failures"]) == 2
    assert saved["pending_failures"] == {}

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
    assert posts[0]["body"] == "2026-W40 · 1 issue"
    assert list(json.loads(state.read_text())["alerted_failures"].values()) == ["new stage failure"]
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
# Round 4, finding 1: a catalog fetch that "succeeds" but returns the wrong
# SHAPE (as opposed to failing or being an unmistakably wrong top-level
# type) must be treated exactly like a fetch failure, not like a usable
# empty catalog.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "catalog_state",
    [
        "error_dict", "recipes_not_list", "list_of_non_dicts", "dict_missing_recipes",
        "entry_missing_title", "entry_blank_title", "entry_non_string_title",
        "recipes_entry_missing_title",
    ],
)
def test_malformed_but_present_catalog_shape_does_not_repeat_alert(
    tmp_path, monkeypatch, catalog_state
):
    """The literal round-4 finding-1 scenario for each malformed shape: a
    persistent stage failure (A) plus a title collision (B), with the
    catalog fetch returning garbage — but not None, and satisfying the old
    loose isinstance check — on the middle run. Before this fix, each of
    these coerced to a usable-but-empty catalog via `.get("recipes", [])`
    or an all-lenient isinstance(..., list) check, so the catalog group
    "ran," found no title collision (title_validator skips non-dict
    entries), and B looked cleared -> a false recovery -> then re-alerted
    once the real catalog returned. After the fix: one alert, total, and no
    recovery in between."""
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
        catalog_state=catalog_state,
    )
    pm.run(state)
    assert len(posts) == 1  # no re-alert
    assert not any("recovered" in p["subject"].lower() for p in posts)  # no false recovery
    saved = json.loads(state.read_text())
    assert saved["checks_ran"] == ["episode"]  # the catalog group did NOT run
    assert saved["alerted_failures"] == alerted_after_run1  # untouched, not cleared

    _install_pipeline(
        monkeypatch,
        episode_only_failures=["stage A"],
        catalog_only_failures=["title collision B"],
        catalog_state="up",
    )
    pm.run(state)
    assert len(posts) == 1  # still silent once the real catalog returns


def test_valid_catalog_accepts_lists_and_recipes_dicts():
    assert pm._valid_catalog([{"title": "a"}]) == [{"title": "a"}]
    assert pm._valid_catalog([]) == []
    assert pm._valid_catalog({"recipes": [{"title": "a"}]}) == [{"title": "a"}]
    assert pm._valid_catalog({"recipes": []}) == []


def test_valid_catalog_rejects_every_malformed_shape():
    assert pm._valid_catalog(None) is None
    assert pm._valid_catalog("not a list or dict") is None
    assert pm._valid_catalog(12345) is None
    assert pm._valid_catalog({"error": "temporary failure"}) is None
    assert pm._valid_catalog({"recipes": "invalid"}) is None
    assert pm._valid_catalog(["a", "b"]) is None
    assert pm._valid_catalog({"foo": "bar"}) is None
    assert pm._valid_catalog([{"title": "ok"}, "bad"]) is None  # partial malformation
    # Round 7: an entry without a usable title is skipped by the title check,
    # so a catalog containing one cannot count as the check having run.
    assert pm._valid_catalog([{}]) is None
    assert pm._valid_catalog([{"title": "ok"}, {"slug": "no-title"}]) is None
    assert pm._valid_catalog([{"title": ""}]) is None
    assert pm._valid_catalog([{"title": "  "}]) is None
    assert pm._valid_catalog([{"title": None}]) is None
    assert pm._valid_catalog({"recipes": [{"title": 7}]}) is None


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
# Round 4, finding 2: an unusable state file (non-object JSON, or a stale
# pre-round-3 schema) must never crash the monitor and must never be
# silently misread as "nothing has ever been alerted" — it's logged, moved
# aside with send2trash, and the run starts fresh.
# ---------------------------------------------------------------------------


def test_non_object_json_state_no_longer_raises_and_starts_fresh(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps([1, 2, 3]))  # valid JSON, but not an object
    monkeypatch.setattr(pm, "send2trash", lambda path: None)  # don't touch the real Trash
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])

    rc = pm.run(state)
    assert rc == 0  # previously: AttributeError from `[1, 2, 3].get(...)`, every single run
    # A fresh start alerting once for a currently-failing pipeline is the
    # accepted cost (documented in ops/launchd/README.md) — not a bug.
    assert len(posts) == 1
    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"
    assert list(saved["alerted_failures"].values()) == ["monday stage is 'missing'"]


def test_old_pre_round3_schema_state_is_discarded_and_starts_fresh(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    old_schema = {
        "status": "degraded",
        "episode_id": "2026-W40",
        "summary": "2026-W40: degraded",
        "failures": ["monday stage is 'missing'"],
        "checked_at": "2026-09-30T00:00:00Z",
        "alerted": {  # round 1/2's key — not "alerted_failures"
            "status": "degraded",
            "signature": "abc123",
            "at": "2026-09-30T00:00:00Z",
        },
    }
    state.write_text(json.dumps(old_schema))
    monkeypatch.setattr(pm, "send2trash", lambda path: None)
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])

    rc = pm.run(state)
    assert rc == 0
    # Treated as no prior state at all: the unchanged failure alerts once
    # (a one-time cost) rather than silently trusting the old shape forever
    # (it would otherwise never look "new" again under the OLD key, and
    # never get cleared either since nothing ever populates "alerted").
    assert len(posts) == 1
    saved = json.loads(state.read_text())
    assert "alerted_failures" in saved
    assert list(saved["alerted_failures"].values()) == ["monday stage is 'missing'"]


def test_valid_current_schema_state_is_trusted_not_discarded(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["monday stage is 'missing'"])
    pm.run(state)
    assert len(posts) == 1

    # A second run with the IDENTICAL failure must stay silent — proving a
    # valid current-schema file is read and trusted, not discarded and
    # treated as a fresh start (which would re-alert).
    pm.run(state)
    assert len(posts) == 1


def test_unusable_state_file_is_trashed_never_deleted(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("not json at all {{{")
    trashed = []
    monkeypatch.setattr(pm, "send2trash", lambda path: trashed.append(path))
    _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=[])

    rc = pm.run(state)
    assert rc == 0
    assert trashed == [str(state)]


def test_is_current_schema_directly():
    assert pm._is_current_schema(
        {"status": "ok", "alerted_failures": {}, "checks_ran": []}
    )
    assert not pm._is_current_schema([1, 2, 3])
    assert not pm._is_current_schema("just a string")
    assert not pm._is_current_schema(None)
    assert not pm._is_current_schema({"status": "degraded", "alerted": {"status": "degraded"}})
    assert not pm._is_current_schema({"alerted_failures": "not a dict", "checks_ran": []})
    assert not pm._is_current_schema({"alerted_failures": {}, "checks_ran": "not a list"})


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

    with pytest.raises(RuntimeError, match="simulated write failure"):
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
            return [{"slug": "a", "title": "A"}]
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
    assert seen["catalog"] == [{"slug": "a", "title": "A"}]


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



@pytest.mark.parametrize("state", [
    {"alerted_failures": {"bad-id": "old failure"}, "checks_ran": []},
    {"alerted_failures": {"nope\x1f2026-W40\x1ftext": "t"}, "checks_ran": ["episode"]},
    {"alerted_failures": {"episode\x1f\x1ftext": "t"}, "checks_ran": ["episode"]},
    {"alerted_failures": {"episode\x1f2026-W40\x1ftext": 5}, "checks_ran": ["episode"]},
    {"alerted_failures": {}, "checks_ran": ["mystery"]},
])
def test_state_with_unclearable_or_unknown_entries_is_not_current_schema(state):
    """Codex round 5: an alerted id whose group no check runs could never
    clear, pinning the monitor to degraded with no recovery."""
    assert pm._is_current_schema(state) is False


def test_valid_failure_ids_are_current_schema():
    state = {
        "alerted_failures": {
            "episode\x1f2026-W40\x1ftuesday failed": "tuesday failed",
            "catalog\x1f2026-W40\x1ftitle collides": "title collides",
        },
        "checks_ran": ["catalog", "episode"],
    }
    assert pm._is_current_schema(state) is True



def test_failure_text_containing_the_separator_round_trips_through_validation():
    """Codex round 6: a real failure whose text contains the separator was
    saved, then discarded as invalid on the next run and alerted again."""
    text = "stage error: bad\x1fbyte in title"
    fid = pm._failure_id("episode", "2026-W40", text)
    state = {"alerted_failures": {fid: text}, "checks_ran": ["episode"]}
    assert pm._is_current_schema(state) is True
    assert pm._failure_id_group(fid) == "episode"


@pytest.mark.parametrize("checks_ran", [[{}], [["episode"]], [None], [3]])
def test_non_string_checks_ran_is_discarded_not_raised(checks_ran):
    state = {"alerted_failures": {}, "checks_ran": checks_ran}
    assert pm._is_current_schema(state) is False


@pytest.mark.parametrize("failure_id, text", [
    # Empty or blank text for an otherwise well-formed id.
    ("episode\x1f2026-W40\x1ftuesday failed", ""),
    ("episode\x1f2026-W40\x1f", ""),
    ("episode\x1f2026-W40\x1ftuesday failed", "   "),
    # Text that does not produce the id's text segment.
    ("episode\x1f2026-W40\x1ftuesday failed", "wednesday failed"),
    # An extra separator inside the text segment.
    ("episode\x1f2026-W40\x1ftuesday\x1ffailed", "tuesday\x1ffailed"),
    # Un-normalized timestamp in the id.
    ("episode\x1f2026-W40\x1fdue 2026-09-29 14:30 UTC", "due 2026-09-29 14:30 UTC"),
])
def test_stored_id_must_match_its_own_text(failure_id, text):
    """Codex round 7: {<current failure id>: ""} validated, so the next run
    saw a real failure as already alerted and sent nothing."""
    state = {"alerted_failures": {failure_id: text}, "checks_ran": ["episode"]}
    assert pm._is_current_schema(state) is False


def test_mismatched_state_entry_cannot_suppress_a_real_alert(tmp_path, monkeypatch):
    """End to end: a state file that maps the current failure's id to an
    empty text is set aside, and the failure alerts."""
    state = tmp_path / "pipeline_status.json"
    fid = pm._failure_id("episode", "2026-W40", "stage A")
    state.write_text(json.dumps({
        "status": "degraded",
        "alerted_failures": {fid: ""},
        "checks_ran": ["episode"],
    }))
    trashed = []
    monkeypatch.setattr(pm, "send2trash", lambda path: trashed.append(path))
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)
    assert trashed == [str(state)]
    assert len(posts) == 1
    assert json.loads(state.read_text())["alerted_failures"] == {fid: "stage A"}


# ---------------------------------------------------------------------------
# Round 8: a state file that exists but can be neither read nor moved aside
# must never be treated as "no prior state" — that re-sent alerts and then
# overwrote the history the monitor could not see.
# ---------------------------------------------------------------------------


def _unreadable(state, monkeypatch):
    real_read_text = pm.Path.read_text

    def _read_text(self, *args, **kwargs):
        if self == state:
            raise PermissionError(13, "Permission denied", str(self))
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pm.Path, "read_text", _read_text)


def _prior_state_bytes(state):
    fid = pm._failure_id("episode", "2026-W40", "stage A")
    state.write_text(json.dumps({
        "status": "degraded",
        "alerted_failures": {fid: "stage A"},
        "checks_ran": ["episode"],
        "alerted_at": "2026-09-30T00:00:00Z",
    }))
    return state.read_bytes()


def test_unreadable_state_defers_the_transition(tmp_path, monkeypatch, capsys):
    state = tmp_path / "pipeline_status.json"
    before = _prior_state_bytes(state)
    _unreadable(state, monkeypatch)
    trashed = []
    monkeypatch.setattr(pm, "send2trash", lambda path: trashed.append(path))
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A", "stage B"])

    assert pm.run(state) == 0
    assert posts.attempts == 0          # no duplicate alert
    assert trashed == []                # not moved
    assert state.read_bytes() == before  # not overwritten
    assert "state left untouched" in capsys.readouterr().err


def test_unreadable_state_on_unknown_verdict_is_not_overwritten(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    before = _prior_state_bytes(state)
    _unreadable(state, monkeypatch)
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_ok=False)

    assert pm.run(state) == 0
    assert posts.attempts == 0
    assert state.read_bytes() == before


def test_unusable_state_that_cannot_be_trashed_defers_the_transition(tmp_path, monkeypatch, capsys):
    state = tmp_path / "pipeline_status.json"
    state.write_text("{not json")
    before = state.read_bytes()

    def _trash_fails(path):
        raise OSError("trash unavailable")

    monkeypatch.setattr(pm, "send2trash", _trash_fails)
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])

    assert pm.run(state) == 0
    assert posts.attempts == 0
    assert state.read_bytes() == before
    assert "could not move unusable state file" in capsys.readouterr().err


def test_unusable_state_moved_aside_still_starts_fresh(tmp_path, monkeypatch):
    """The legitimate path is unchanged: once the bad file is moved aside,
    the run proceeds like a first run and alerts once."""
    state = tmp_path / "pipeline_status.json"
    state.write_text("{not json")
    trashed = []

    def _trash(path):
        trashed.append(path)
        pm.Path(path).rename(tmp_path / "trashed.json")

    monkeypatch.setattr(pm, "send2trash", _trash)
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])

    assert pm.run(state) == 0
    assert trashed == [str(state)]
    assert len(posts) == 1
    assert list(json.loads(state.read_text())["alerted_failures"].values()) == ["stage A"]


# ---------------------------------------------------------------------------
# Round 9 asked that every failure recorded as alerted was really in the
# delivered alert. #7930 made the alert a short count with no failure text,
# so the count covers the whole group: one delivered summary records every
# failure it counts, and an undelivered one records none.
# ---------------------------------------------------------------------------


def _many_long_failures(n=9, width=600):
    return [f"{chr(65 + i)} stage failed — " + chr(65 + i) * width for i in range(n)]


def test_one_delivered_summary_records_every_failure_it_counts(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    failures = _many_long_failures()
    _install_pipeline(monkeypatch, episode_only_failures=failures)

    pm.run(state)
    assert posts == [
        {"subject": "Pipeline degraded", "body": "2026-W40 · 9 issues", "severity": "warning"}
    ]
    saved = json.loads(state.read_text())
    assert set(saved["alerted_failures"].values()) == set(failures)
    assert saved["pending_failures"] == {}
    # The detail stays in the state file, not the alert.
    assert set(saved["failures"]) == set(failures)

    pm.run(state)
    assert len(posts) == 1  # acknowledged as a group, not re-sent


def test_summary_records_nothing_when_delivery_fails(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    posts.deliver = False
    _install_pipeline(monkeypatch, episode_only_failures=_many_long_failures())
    pm.run(state)
    assert posts.attempts == 1
    saved = json.loads(state.read_text())
    assert saved["alerted_failures"] == {}
    assert len(saved["pending_failures"]) == 9

    # Retried next run, still as one summary.
    posts.deliver = True
    pm.run(state)
    assert posts.attempts == 2
    assert len(json.loads(state.read_text())["alerted_failures"]) == 9


def test_summary_counts_new_and_still_open_failures(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A", "stage B", "stage C"])
    pm.run(state)
    assert [p["body"] for p in posts] == ["2026-W40 · 1 issue", "2026-W40 · 3 issues"]


# ---------------------------------------------------------------------------
# Round 10: A cleared on the same run B appeared, B's alert failed (or was
# deferred), then B cleared too, and no recovery alert ever went out for A.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("b_alert", ["undelivered", "delivered"])
def test_recovery_is_announced_even_when_the_new_failure_alert_failed(tmp_path, monkeypatch, b_alert):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)
    assert len(posts) == 1

    # A resolves as B appears; B's alert may fail.
    posts.deliver = b_alert == "delivered"
    _install_pipeline(monkeypatch, episode_only_failures=["stage B"])
    pm.run(state)
    saved = json.loads(state.read_text())
    if b_alert == "undelivered":
        # B is owed, so A is held rather than cleared.
        assert "stage A" in saved["alerted_failures"].values()
        assert list(saved["pending_failures"].values()) == ["stage B"]
    else:
        # B was told; A clears silently as a partial recovery, and A,
        # verified gone, is not counted as open.
        assert posts[-1]["body"] == "2026-W40 · 1 issue"
        assert list(saved["alerted_failures"].values()) == ["stage B"]

    # B resolves before the next run: a full recovery must be announced.
    posts.deliver = True
    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    assert any("recovered" in p["subject"].lower() for p in posts)
    assert json.loads(state.read_text())["alerted_failures"] == {}


def test_resolved_failure_clears_once_the_new_group_is_delivered(tmp_path, monkeypatch):
    """Round 9 deferred failures that did not fit the body, which held A.
    The #7930 summary fits every failure, so A clears silently once the new
    group is delivered, and the final clear still brings the recovery."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)

    _install_pipeline(monkeypatch, episode_only_failures=_many_long_failures())
    pm.run(state)
    saved = json.loads(state.read_text())
    assert set(saved["alerted_failures"].values()) == set(_many_long_failures())
    assert saved["pending_failures"] == {}

    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    assert any("recovered" in p["subject"].lower() for p in posts)



# ---------------------------------------------------------------------------
# Round 11 redesign: alerted (told) + pending (owed) sets.
# ---------------------------------------------------------------------------


def test_catalog_outage_cannot_fake_a_recovery_while_a_failure_is_owed(tmp_path, monkeypatch):
    """Codex round 11: A announced; A resolves as catalog failure B appears
    but B's alert fails; then the catalog is down. B is unverified, so it
    must stay owed, A must stay held, and no "recovered" goes out."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)

    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)

    posts.deliver = False
    _install_pipeline(monkeypatch, catalog_only_failures=["title collision B"])
    pm.run(state)

    posts.deliver = True
    _install_pipeline(monkeypatch, catalog_only_failures=["title collision B"], catalog_state="down")
    pm.run(state)
    assert not any("recovered" in p["subject"].lower() for p in posts)
    saved = json.loads(state.read_text())
    assert saved["status"] == "degraded"
    assert "stage A" in saved["alerted_failures"].values()
    assert "title collision B" in saved["pending_failures"].values()

    # Catalog back with B still present: B is finally told, A clears.
    _install_pipeline(monkeypatch, catalog_only_failures=["title collision B"])
    pm.run(state)
    assert posts[-1]["subject"] == "Pipeline degraded"
    saved = json.loads(state.read_text())
    assert list(saved["alerted_failures"].values()) == ["title collision B"]
    assert saved["pending_failures"] == {}


def test_an_owed_failure_that_resolves_is_dropped_without_any_alert(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    posts.deliver = False
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)
    assert list(json.loads(state.read_text())["pending_failures"].values()) == ["stage A"]

    posts.deliver = True
    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    assert len(posts) == 0  # never told, so no recovery owed either
    saved = json.loads(state.read_text())
    assert saved["status"] == "ok"
    assert saved["pending_failures"] == {} and saved["alerted_failures"] == {}


@pytest.mark.parametrize("pending", [
    "not a dict",
    {"episode\x1f2026-W40\x1fstage A": ""},
    {"nope\x1f2026-W40\x1fstage A": "stage A"},
])
def test_malformed_pending_failures_is_not_current_schema(pending):
    state = {"alerted_failures": {}, "pending_failures": pending, "checks_ran": ["episode"]}
    assert pm._is_current_schema(state) is False


def test_a_failure_cannot_be_both_alerted_and_pending():
    fid = pm._failure_id("episode", "2026-W40", "stage A")
    state = {
        "alerted_failures": {fid: "stage A"},
        "pending_failures": {fid: "stage A"},
        "checks_ran": ["episode"],
    }
    assert pm._is_current_schema(state) is False


def test_state_without_pending_failures_is_still_current_schema():
    fid = pm._failure_id("episode", "2026-W40", "stage A")
    assert pm._is_current_schema({"alerted_failures": {fid: "stage A"}, "checks_ran": ["episode"]})


# ---------------------------------------------------------------------------
# Round 12: a failure belongs to its own week. A healthy check of a LATER
# week never verifies it gone; it is "retired" (no longer checked), and is
# reported as such, never as resolved.
# ---------------------------------------------------------------------------


def test_rollover_never_reports_an_unchecked_week_as_resolved(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=["stage A"])
    pm.run(state)
    assert len(posts) == 1

    _install_pipeline(monkeypatch, episode_id="2026-W41", episode_only_failures=[])
    pm.run(state)
    assert len(posts) == 2
    assert posts[-1] == {
        "subject": "Previous week closed",
        "body": "2026-W40 closed · 1 issue unverified",
        "severity": "info",
    }
    assert "recovered" not in posts[-1]["subject"].lower()
    assert json.loads(state.read_text())["alerted_failures"] == {}


def test_closed_week_notice_is_retried_until_delivered(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=["stage A"])
    pm.run(state)

    posts.deliver = False
    _install_pipeline(monkeypatch, episode_id="2026-W41", episode_only_failures=[])
    pm.run(state)
    assert "stage A" in json.loads(state.read_text())["alerted_failures"].values()

    posts.deliver = True
    pm.run(state)
    assert posts[-1]["subject"] == "Previous week closed"
    assert json.loads(state.read_text())["alerted_failures"] == {}


def test_rollover_still_tells_a_failure_whose_alert_never_went_out(tmp_path, monkeypatch):
    """Round 12: W40's failure was pending (alert undelivered); a healthy W41
    check used to drop it silently. It is now told under its own closed
    week, never as present degradation and never as recovered."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    posts.deliver = False
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=["stage A"])
    pm.run(state)
    assert list(json.loads(state.read_text())["pending_failures"].values()) == ["stage A"]

    posts.deliver = True
    _install_pipeline(monkeypatch, episode_id="2026-W41", episode_only_failures=[])
    pm.run(state)
    # Only delivered alerts are recorded, so posts[0] is the first one out.
    assert posts[0] == {
        "subject": "Previous week closed",
        "body": "2026-W40 closed · 1 issue unverified",
        "severity": "warning",
    }
    assert posts[-1]["subject"] == "Previous week closed"
    assert not any(p["subject"] == "Pipeline degraded" for p in posts)
    assert not any("recovered" in p["subject"].lower() for p in posts)
    saved = json.loads(state.read_text())
    assert saved["pending_failures"] == {} and saved["alerted_failures"] == {}


def test_a_manual_run_of_an_older_week_leaves_current_failures_alone(tmp_path, monkeypatch):
    """`--episode 2026-W36` against the live state file must neither resolve
    nor retire the current week's failures."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=["stage A"])
    pm.run(state)

    _install_pipeline(monkeypatch, episode_id="2026-W36", episode_only_failures=[])
    pm.run(state)
    assert len(posts) == 1  # no recovery claimed
    assert "stage A" in json.loads(state.read_text())["alerted_failures"].values()


def test_retired_failure_is_held_while_the_current_week_is_still_failing(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=["stage A"])
    pm.run(state)
    _install_pipeline(monkeypatch, episode_id="2026-W41", episode_only_failures=["stage B"])
    pm.run(state)
    _install_pipeline(monkeypatch, episode_id="2026-W41", episode_only_failures=[])
    pm.run(state)
    assert posts[-2]["body"] == "2026-W41 · 1 issue"  # W40 was already told
    # W41 was verified clean; W40 only closed, and is said to be.
    assert posts[-1] == {
        "subject": "Pipeline recovered · Previous week closed",
        "body": "2026-W41 recovered; 2026-W40 closed · 1 issue unverified",
        "severity": "info",
    }


# ---------------------------------------------------------------------------
# #7930 manager review: the summary stays a few words however many weeks of
# undelivered failures pile up. Closed weeks fold into ONE count marked
# closed and unverified, and are never called present degradation or repair.
# ---------------------------------------------------------------------------


def _backlog_state(path, weeks, per_week=2, width=200):
    """A state file owing `per_week` long failures for each of `weeks`."""
    pending = {}
    for week in weeks:
        for i in range(per_week):
            text = f"{week} stage {i} failed — " + "x" * width
            pending[pm._failure_id("episode", week, text)] = text
    path.write_text(
        json.dumps(
            {
                "status": "degraded",
                "episode_id": weeks[-1],
                "summary": "",
                "failures": [],
                "checks_ran": ["episode"],
                "checked_at": "2026-10-01T00:00:00Z",
                "alerted_failures": {},
                "pending_failures": pending,
                "alerted_at": None,
            }
        )
    )
    return set(pending)


def _backlog_weeks(n):
    weeks = [f"{year}-W{week:02d}" for year in range(2021, 2027) for week in range(1, 53)]
    return [w for w in weeks if w < "2026-W40"][-n:]


def _words(post):
    return [w for w in f"{post['subject']} {post['body']}".split() if w not in ("·", ";")]


def _every_attempt(monkeypatch, deliver):
    attempts = []

    def _send(subject, body, severity="warning", **kw):
        attempts.append({"subject": subject, "body": body, "severity": severity})
        return deliver[0]

    monkeypatch.setattr(pm, "send_alert_confirming_email", _send)
    return attempts


def test_many_week_backlog_is_one_short_summary_that_covers_every_id(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    weeks = _backlog_weeks(250)
    owed = _backlog_state(state, weeks)
    # One entry per week (the old body) would have run far past 4000 chars.
    assert len("; ".join(f"{w} · 2 issues unverified" for w in weeks)) > 4000
    current = ["stage A", "stage B", "stage C"]
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=current)
    current_ids = {pm._failure_id("episode", "2026-W40", t) for t in current}

    deliver = [False]
    attempts = _every_attempt(monkeypatch, deliver)
    pm.run(state)
    expected = {
        "subject": "Pipeline degraded",
        "body": "2026-W40 · 3 issues; 250 earlier weeks closed · 500 issues unverified",
        "severity": "warning",
    }
    assert attempts == [expected]
    saved = json.loads(state.read_text())
    assert saved["alerted_failures"] == {}  # an undelivered summary records none
    assert set(saved["pending_failures"]) == owed | current_ids

    deliver[0] = True
    pm.run(state)
    assert attempts[1:] == [expected]
    assert len(_words(expected)) <= 20
    saved = json.loads(state.read_text())
    assert set(saved["alerted_failures"]) == owed | current_ids  # every counted id
    assert saved["pending_failures"] == {}


def test_closed_only_backlog_is_earlier_weeks_closed_not_degraded(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    _backlog_state(state, _backlog_weeks(250))
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=[])
    posts = _captured_alerts(monkeypatch)

    pm.run(state)
    assert posts[0] == {
        "subject": "Earlier weeks closed",
        "body": "250 earlier weeks closed · 500 issues unverified",
        "severity": "warning",
    }
    assert not any(p["subject"] in ("Pipeline degraded", "Pipeline recovered") for p in posts)
    assert all(len(_words(p)) <= 20 for p in posts)
    # Once told, nothing current is open, so the same run closes them out.
    assert [p["subject"] for p in posts] == ["Earlier weeks closed"] * 2
    assert posts[1]["severity"] == "info"
    saved = json.loads(state.read_text())
    assert saved["pending_failures"] == {} and saved["alerted_failures"] == {}


def test_single_closed_pending_week_is_previous_week_closed(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    _backlog_state(state, ["2026-W39"], per_week=2)
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=[])
    posts = _captured_alerts(monkeypatch)

    pm.run(state)
    assert posts[0] == {
        "subject": "Previous week closed",
        "body": "2026-W39 closed · 2 issues unverified",
        "severity": "warning",
    }


def test_closed_pending_week_beside_a_current_failure_is_labelled_apart(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    _backlog_state(state, ["2026-W39"], per_week=2)
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=["stage A"])
    posts = _captured_alerts(monkeypatch)

    pm.run(state)
    assert posts == [
        {
            "subject": "Pipeline degraded",
            "body": "2026-W40 · 1 issue; 2026-W39 closed · 2 issues unverified",
            "severity": "warning",
        }
    ]


def test_mixed_recovery_over_many_closed_weeks_stays_short(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    weeks = _backlog_weeks(250)
    _backlog_state(state, weeks)
    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=["stage A"])
    posts = _captured_alerts(monkeypatch)
    pm.run(state)

    _install_pipeline(monkeypatch, episode_id="2026-W40", episode_only_failures=[])
    pm.run(state)
    assert posts[-1] == {
        "subject": "Pipeline recovered · Earlier weeks closed",
        "body": "2026-W40 recovered; 250 earlier weeks closed · 500 issues unverified",
        "severity": "info",
    }
    assert len(_words(posts[-1])) <= 20
    assert json.loads(state.read_text())["alerted_failures"] == {}


# ---------------------------------------------------------------------------
# Codex cycle 1 on #7930: the checked week's count must include failures
# still open but unverified (their check did not run), whether already
# alerted or still owed. They are counted, marked unverified, and NOT
# acknowledged by the alert that counts them.
# ---------------------------------------------------------------------------

_COLLISION = "title collision C"


def _owe_closed_week(state, week="2026-W39", n=2):
    """Add `n` owed failures from an earlier week to an existing state file."""
    doc = json.loads(state.read_text())
    texts = [f"{week} stage {i} failed" for i in range(n)]
    owed = {pm._failure_id("episode", week, t): t for t in texts}
    doc["pending_failures"].update(owed)
    state.write_text(json.dumps(doc))
    return set(owed)


def _collision_id(week="2026-W40"):
    return pm._failure_id("catalog", week, _COLLISION)


@pytest.mark.parametrize("collision", ["alerted", "pending"])
def test_unverified_current_failure_is_counted_beside_a_new_one(tmp_path, monkeypatch, collision):
    """Reviewer case 1: a catalog collision, then a catalog outage and a new
    stage failure. Two failures are open, not one."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    posts.deliver = collision == "alerted"
    _install_pipeline(monkeypatch, catalog_only_failures=[_COLLISION])
    pm.run(state)

    posts.deliver = True
    _install_pipeline(
        monkeypatch, episode_only_failures=["stage S"], catalog_only_failures=[_COLLISION],
        catalog_state="down",
    )
    pm.run(state)
    assert posts[-1] == {
        "subject": "Pipeline degraded",
        "body": "2026-W40 · 2 issues (1 unverified)",
        "severity": "warning",
    }
    saved = json.loads(state.read_text())
    assert "stage S" in saved["alerted_failures"].values()
    # Counted, not acknowledged: an owed collision stays owed until its own
    # check re-confirms it; an alerted one stays alerted.
    bucket = "alerted_failures" if collision == "alerted" else "pending_failures"
    assert _collision_id() in saved[bucket]

    _install_pipeline(monkeypatch, episode_only_failures=["stage S"], catalog_only_failures=[_COLLISION])
    pm.run(state)
    if collision == "pending":
        assert posts[-1]["body"] == "2026-W40 · 2 issues"  # now re-confirmed and told
    else:
        assert len(posts) == 2  # nothing new
    saved = json.loads(state.read_text())
    assert _collision_id() in saved["alerted_failures"] and saved["pending_failures"] == {}


def test_unverified_current_failure_keeps_degraded_beside_a_closed_week(tmp_path, monkeypatch):
    """Reviewer case 2: only a closed week's ids are new, but the checked
    week still has an unverified failure, so the status is degraded, not
    "Previous week closed", and no recovery is claimed."""
    state = tmp_path / "pipeline_status.json"
    deliver = [True]
    attempts = _every_attempt(monkeypatch, deliver)
    _install_pipeline(monkeypatch, catalog_only_failures=[_COLLISION])
    pm.run(state)
    owed = _owe_closed_week(state)

    expected = {
        "subject": "Pipeline degraded",
        "body": "2026-W40 · 1 issue unverified; 2026-W39 closed · 2 issues unverified",
        "severity": "warning",
    }
    _install_pipeline(monkeypatch, catalog_only_failures=[_COLLISION], catalog_state="down")
    deliver[0] = False
    pm.run(state)
    assert attempts[-1] == expected
    saved = json.loads(state.read_text())
    assert set(saved["pending_failures"]) == owed  # undelivered: nothing acknowledged
    assert list(saved["alerted_failures"]) == [_collision_id()]

    deliver[0] = True
    pm.run(state)
    assert attempts[-1] == expected  # retried, and nothing else sent
    assert len(attempts) == 3
    saved = json.loads(state.read_text())
    assert saved["pending_failures"] == {}
    assert set(saved["alerted_failures"]) == owed | {_collision_id()}
    assert saved["status"] == "degraded"


def test_resolved_current_failure_is_not_counted_and_closed_week_stays_closed(tmp_path, monkeypatch):
    """Neighbour: a current failure verified gone is not open, so a closed
    week owed on the same run is "Previous week closed", then the final
    clear is a recovery that keeps the closed week apart."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)
    _owe_closed_week(state, n=1)

    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    assert posts[1:] == [
        {"subject": "Previous week closed", "body": "2026-W39 closed · 1 issue unverified",
         "severity": "warning"},
        {"subject": "Pipeline recovered · Previous week closed",
         "body": "2026-W40 recovered; 2026-W39 closed · 1 issue unverified", "severity": "info"},
    ]
    saved = json.loads(state.read_text())
    assert saved["alerted_failures"] == {} and saved["pending_failures"] == {}


@pytest.mark.parametrize("older_week_failures,expected", [
    ([], {"subject": "Previous week closed", "body": "2026-W35 closed · 1 issue unverified"}),
    (["stage M"], {"subject": "Pipeline degraded",
                   "body": "2026-W36 · 1 issue; 2026-W35 closed · 1 issue unverified"}),
])
def test_manual_older_week_check_neither_counts_nor_closes_later_weeks(
    tmp_path, monkeypatch, older_week_failures, expected
):
    """`--episode 2026-W36` with W40 failures (one alerted, one owed with its
    check down) and a W35 one owed: W40 is a LATER week, so it is neither
    this week's count nor an earlier closed week, and stays as it was."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)
    posts.deliver = False
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"], catalog_only_failures=[_COLLISION])
    pm.run(state)
    posts.deliver = True
    owed = _owe_closed_week(state, week="2026-W35", n=1)

    _install_pipeline(monkeypatch, episode_id="2026-W36", episode_only_failures=older_week_failures)
    pm.run(state)
    assert posts[1] == {**expected, "severity": "warning"}
    assert len(posts) == 2  # W40 is still open, so no recovery of any kind
    saved = json.loads(state.read_text())
    assert pm._failure_id("episode", "2026-W40", "stage A") in saved["alerted_failures"]
    assert owed <= set(saved["alerted_failures"])
    assert list(saved["pending_failures"]) == [_collision_id()]


# ---------------------------------------------------------------------------
# Gate review on b7065b2: a state write that fails after a delivered alert
# made the next run send it again. The state is now proven writable before
# any alert goes out.
# ---------------------------------------------------------------------------


def test_unwritable_state_sends_no_alert(tmp_path, monkeypatch, capsys):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])

    def _cannot_write(path, doc):
        raise OSError("read-only file system")

    monkeypatch.setattr(pm, "write_state", _cannot_write)
    assert pm.run(state) == 0
    assert posts.attempts == 0
    assert "state not writable" in capsys.readouterr().err


def test_state_is_written_before_the_alert_is_sent(tmp_path, monkeypatch):
    """The pre-alert write records the failure as owed, so even a crash
    between sending and the final write leaves a truthful state."""
    state = tmp_path / "pipeline_status.json"
    seen_at_send = {}
    posts = _captured_alerts(monkeypatch)
    real_send = pm.send_alert_confirming_email

    def _send(**kwargs):
        seen_at_send.update(json.loads(state.read_text()))
        return real_send(**kwargs)

    monkeypatch.setattr(pm, "send_alert_confirming_email", _send)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    pm.run(state)

    assert list(seen_at_send["pending_failures"].values()) == ["stage A"]
    assert seen_at_send["alerted_failures"] == {}
    assert len(posts) == 1
    final = json.loads(state.read_text())
    assert list(final["alerted_failures"].values()) == ["stage A"]
    assert final["pending_failures"] == {}


def _write_failing_on(calls_to_fail: set[int], monkeypatch):
    real_write = pm.write_state
    count = {"n": 0}

    def _write(path, doc):
        count["n"] += 1
        if count["n"] in calls_to_fail:
            raise OSError("disk hiccup")
        return real_write(path, doc)

    monkeypatch.setattr(pm, "write_state", _write)


def test_a_failed_final_write_is_retried_once(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    _write_failing_on({2}, monkeypatch)  # 1 = pre-alert, 2 = final, 3 = retry
    pm.run(state)
    assert len(posts) == 1
    assert list(json.loads(state.read_text())["alerted_failures"].values()) == ["stage A"]


def test_an_unrecordable_delivery_is_resent_at_least_once(tmp_path, monkeypatch, capsys):
    """The documented trade-off: if the outcome cannot be recorded, the
    failure stays owed and is sent again; it is never silently dropped."""
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _install_pipeline(monkeypatch, episode_only_failures=["stage A"])
    _write_failing_on({2, 3}, monkeypatch)
    pm.run(state)
    assert "at-least-once delivery" in capsys.readouterr().err
    assert list(json.loads(state.read_text())["pending_failures"].values()) == ["stage A"]


# ---------------------------------------------------------------------------
# --self-test: run by the installer through the job's exact launchd command.
# ---------------------------------------------------------------------------


def test_self_test_passes_with_email_configured_and_a_writable_state_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm, "email_channel_status", lambda: {"configured": True, "missing": []})
    assert pm.self_test(tmp_path / "state" / "pipeline_status.json") == 0
    assert "self-test passed" in capsys.readouterr().out


def test_self_test_fails_without_email_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        pm, "email_channel_status", lambda: {"configured": False, "missing": ["RESEND_API_KEY"]}
    )
    assert pm.self_test(tmp_path / "pipeline_status.json") == 1
    assert "RESEND_API_KEY" in capsys.readouterr().err


def test_self_test_fails_when_the_state_dir_is_not_writable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm, "email_channel_status", lambda: {"configured": True, "missing": []})
    monkeypatch.setattr(pm.os, "access", lambda *_a, **_k: False)
    assert pm.self_test(tmp_path / "pipeline_status.json") == 1
    assert "not writable" in capsys.readouterr().err


def test_self_test_sends_nothing_and_writes_no_state(tmp_path, monkeypatch):
    posts = _captured_alerts(monkeypatch)
    monkeypatch.setattr(pm, "email_channel_status", lambda: {"configured": True, "missing": []})
    state = tmp_path / "pipeline_status.json"
    pm.self_test(state)
    assert posts.attempts == 0
    assert not state.exists()


def test_self_test_fails_when_an_existing_state_file_is_unreadable(tmp_path, monkeypatch, capsys):
    # Every scheduled run would defer behind StateUnavailable; the install
    # must not report success over it.
    monkeypatch.setattr(pm, "email_channel_status", lambda: {"configured": True, "missing": []})
    state = tmp_path / "pipeline_status.json"
    state.write_text("{}")
    state.chmod(0o000)
    try:
        assert pm.self_test(state) == 1
    finally:
        state.chmod(0o644)
    assert "state file" in capsys.readouterr().err


def test_self_test_fails_when_an_existing_lock_file_cannot_be_opened(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pm, "email_channel_status", lambda: {"configured": True, "missing": []})
    state = tmp_path / "pipeline_status.json"
    lock = tmp_path / "pipeline_status.json.lock"
    lock.write_text("")
    lock.chmod(0o444)
    try:
        assert pm.self_test(state) == 1
    finally:
        lock.chmod(0o644)
    assert "lock file" in capsys.readouterr().err


def test_self_test_passes_with_a_readable_state_file_and_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "email_channel_status", lambda: {"configured": True, "missing": []})
    state = tmp_path / "pipeline_status.json"
    state.write_text("{}")
    (tmp_path / "pipeline_status.json.lock").write_text("")
    assert pm.self_test(state) == 0


# ---------------------------------------------------------------------------
# A Monday that never created the episode record (the #6857 shape)
# ---------------------------------------------------------------------------

_MONDAY_DUE = pm.datetime(2026, 9, 28, 16, 0, tzinfo=pm.timezone.utc)  # W40 Monday, past 14:30+45m
_MONDAY_NOT_DUE = pm.datetime(2026, 9, 28, 14, 0, tzinfo=pm.timezone.utc)


class _FrozenDatetime(pm.datetime):
    frozen = _MONDAY_DUE

    @classmethod
    def now(cls, tz=None):
        return cls.frozen


def _freeze(monkeypatch, when):
    _FrozenDatetime.frozen = when
    monkeypatch.setattr(pm, "datetime", _FrozenDatetime)


def test_missing_episode_after_mondays_window_is_a_failure(monkeypatch):
    _freeze(monkeypatch, _MONDAY_DUE)
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: None)
    monkeypatch.setattr(pm, "_episode_confirmed_absent", lambda eid: True)
    verdict = pm.compute_verdict(episode_id="2026-W40")
    assert verdict["kind"] == "observed"
    assert list(verdict["observed_failures"].values()) == [
        "no episode record for 2026-W40: Monday's cron never created it"
    ]


def test_missing_episode_before_mondays_window_stays_unknown(monkeypatch):
    _freeze(monkeypatch, _MONDAY_NOT_DUE)
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: None)
    probed = []
    monkeypatch.setattr(pm, "_episode_confirmed_absent", lambda eid: probed.append(eid) or True)
    assert pm.compute_verdict(episode_id="2026-W40")["kind"] == "unknown"
    assert probed == []


def test_unconfirmed_absence_after_mondays_window_stays_unknown(monkeypatch):
    # A timeout or 5xx is a read problem, not a missing week.
    _freeze(monkeypatch, _MONDAY_DUE)
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: None)
    assert pm.compute_verdict(episode_id="2026-W40")["kind"] == "unknown"


def test_missing_episode_alerts_then_recovers_when_the_record_appears(tmp_path, monkeypatch):
    state = tmp_path / "pipeline_status.json"
    posts = _captured_alerts(monkeypatch)
    _freeze(monkeypatch, _MONDAY_DUE)
    monkeypatch.setattr(pm.sps, "current_episode_id", lambda: "2026-W40")
    monkeypatch.setattr(pm.sps, "_get_json", lambda url: None)
    monkeypatch.setattr(pm, "_episode_confirmed_absent", lambda eid: True)

    pm.run(state)
    pm.run(state)  # still missing: no repeat
    assert len(posts) == 1
    assert posts[0]["subject"] == "Pipeline degraded"
    assert posts[0]["body"] == "2026-W40 · 1 issue"
    assert any("never created it" in f for f in json.loads(state.read_text())["failures"])

    _install_pipeline(monkeypatch, episode_only_failures=[])
    pm.run(state)
    assert len(posts) == 2
    assert "recovered" in posts[1]["subject"].lower()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (pm.urllib.error.HTTPError("u", 404, "Not Found", {}, None), True),
        (pm.urllib.error.HTTPError("u", 503, "Unavailable", {}, None), False),
        (pm.urllib.error.URLError("timed out"), False),
        (TimeoutError("slow"), False),
    ],
    ids=["404", "503", "url-error", "timeout"],
)
def test_absence_probe_is_true_only_for_a_404(monkeypatch, error, expected):
    monkeypatch.undo()  # drop the autouse stub; exercise the real probe

    def _urlopen(req, timeout):
        assert timeout == pm.sps.TIMEOUT_SECONDS
        raise error

    monkeypatch.setattr(pm.urllib.request, "urlopen", _urlopen)
    assert pm._episode_confirmed_absent("2026-W40") is expected


def test_absence_probe_is_false_when_the_episode_is_served(monkeypatch):
    monkeypatch.undo()

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(pm.urllib.request, "urlopen", lambda req, timeout: _Resp())
    assert pm._episode_confirmed_absent("2026-W40") is False
