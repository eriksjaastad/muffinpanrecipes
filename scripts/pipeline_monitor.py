#!/usr/bin/env python3
"""Always-on pipeline monitor for muffinpanrecipes (#7006).

scripts/session_pipeline_status.py only runs when a Claude session opens in
this repo — a Monday failure went undetected until Saturday (#6857) because
nobody happened to be looking. This script runs the SAME check on a schedule
(via launchd, see ops/launchd/) so the pipeline is checked anytime the
machine is on, not just when someone opens a session here.

It deliberately does NOT reimplement the fetch or the verdict logic — both
come straight from session_pipeline_status.py, imported as a module. This
file only adds: persisting the verdict to a small JSON state file (so the
SessionStart hook can read it instantly instead of doing its own network
fetch), and alerting when the set of active failures changes.

Design constraints carried over from session_pipeline_status.py (per #7006,
"KEEP" — this script must not change that check's design):
  1. NEVER block. Always exits 0, including on total failure — a monitor
     must never be the reason a login or a launchd tick goes red.
  2. The 6s-per-request network bound lives in session_pipeline_status.py's
     _get_json and is inherited unchanged.
  3. Silent on network errors: an unreadable episode produces "unknown", not
     an alert, and must never overwrite an already-known verdict on disk —
     a flaky connection must not erase real history or spam every hour.

Alerting goes through backend/utils/alerts.py::send_alert, the one door
every operational alert in this project uses (Discord + email, both on
every severity per #7097) — no new channel is invented here.

PER-CHECK FAILURE TRACKING (current design, round 3)
-----------------------------------------------------
episode_integrity_failures() runs several checks in one call; exactly one of
them (title-collision) needs the separately-fetched catalog and is skipped
when `catalog=None` — see that function's own docstring. Everything else
only needs the episode itself. `compute_verdict()` calls it TWICE — once
with `catalog=None` (the "episode" group: always runs whenever the episode
fetch succeeds) and, only if the catalog fetch itself produced a usable list
or dict, once more with the real catalog (adds the "catalog" group, i.e. the
title-collision check). A catalog fetch that failed OR returned something
malformed is treated identically: the catalog GROUP simply did not run this
cycle — it never has any bearing on whether the EPISODE group's checks ran,
and it never makes the whole verdict "unknown" (round 2 tried exactly that
and got it wrong: it hid a genuinely new episode-group failure for as long
as the catalog was down, and if that failure then recovered before the
catalog came back, it was never alerted at all).

Every observed failure gets a stable id: `<group><SEP><episode_id><SEP>
<normalized failure text>` (timestamps stripped so an identical failure
never looks "new" just because an hour passed, while the same failure text
on a new week's episode genuinely does look new via its episode_id). The
group prefix is what makes CLEARING correct without re-deriving provenance
later: given only a stored id, `_failure_id_group()` reads off which check
produced it, so we always know whether THIS run's `checks_ran` is even in a
position to confirm that failure is gone.

State keeps `alerted_failures`, a `{id: text}` map of every failure id
covered by the last CONFIRMED delivered alert (never a merely-attempted
one — see "confirmed delivery" below, inherited from round 1). Each run:
  - `new = observed_ids - alerted_ids`: failures never before announced.
    Non-empty -> send one alert listing them (plus the full current list for
    context). Only on a CONFIRMED delivery are they added to
    `alerted_failures`; a failed delivery adds nothing, so the SAME ids
    still look "new" next run and get retried.
  - `cleared = {id in alerted_ids : id's group is in this run's checks_ran
    AND id is not in observed_ids}`. A check that did NOT run this cycle
    (e.g. catalog down) leaves ITS alerted ids untouched either way — they
    are neither cleared (we didn't verify they're gone) nor re-alerted
    (we already told Erik about them once). This makes a flickering catalog
    fetch inert: A+B, catalog-down (episode-only group still confirms A is
    unchanged, B is simply unverified this run), A+B again -> one alert,
    never three, and never a "new" catalog-shaped failure the third time
    just because the fetch briefly failed. Clearing itself is unconditional
    (no alert is sent to say "X is fixed" individually) — only a FULL
    recovery (see below) is announced.
  - If nothing is new and clearing empties `alerted_failures` completely
    (and it was non-empty before): one recovery alert. Confirmed delivery
    clears it for real; a failed delivery keeps the full prior set so the
    same recovery is retried next run, exactly like round 1's advisory-alert
    pattern (#7403 — "only a confirmed delivery clears what is owed").
  - Otherwise (no new ids, and either nothing cleared or a PARTIAL clear
    that still leaves something outstanding): silent, `alerted_failures` is
    just updated to reflect whatever cleared.

`status` in the state file is `"unknown"` ONLY when the episode fetch itself
failed (no check ran at all — nothing to compare, nothing to clear, nothing
to alert); `"degraded"` when this run observed a failure OR something is
still alerted-and-unverified (an outstanding id from a check that didn't run
this cycle); `"ok"` otherwise. "ok" is deliberately also the answer when not
every check ran but nothing is or was ever wrong — e.g. a catalog fetch that
timed out on a week with no other problems is not something to be anxious
about.

DESIGN HISTORY
---------------
Round 1 (fb3fb3c) shipped OK/DEGRADED-status-transition alerting with a
whole-set failure signature. Codex found three bugs, all still true today
and unchanged by round 3: (A) a failed `send_alert` call must not be
recorded as delivered — `alerted_*` is only ever updated by a CONFIRMED
delivery; (B) re-alerting on a persistent DEGRADED must be keyed on WHICH
failures are present, not merely that the status stayed "degraded"; (C) the
whole read-decide-alert-write section runs under a non-blocking `fcntl.flock`
so two overlapping invocations can't both decide "not yet alerted" and both
send, and `write_state` replaces the file atomically via a unique
`tempfile.mkstemp` name.

Round 2 (bb4b313) replaced the whole-set signature with a hash, but treated
ANY catalog problem (fetch failure or malformed shape) as making the WHOLE
verdict "unknown" — simple, but wrong: it hid a genuinely new episode-group
failure for as long as the catalog was down, and a malformed (but present)
catalog response still flapped the signature exactly like round 1's bug. It
also said the `.lock` file was safe to delete (wrong: see
ops/launchd/README.md) and used `os.unlink` for orphaned-temp-file cleanup
instead of `send2trash` (AGENTS.md forbids permanent deletion).

Round 3 (this version) replaces the single whole-set signature with the
per-check, per-failure-id model described above, and fixes the lock-file
guidance and `send2trash` usage from round 2's own review.

Usage:
    uv run python scripts/pipeline_monitor.py
    uv run python scripts/pipeline_monitor.py --episode 2026-W36
    uv run python scripts/pipeline_monitor.py --state-file /tmp/state.json

State file schema (JSON):
    {
      "status": "ok" | "degraded" | "unknown",
      "episode_id": "<ISO week, e.g. 2026-W40>",
      "summary": "<one-line episode_summary() output>",
      "failures": ["<failure text observed THIS run>", ...],
      "checks_ran": ["episode"] | ["catalog", "episode"] | [],
      "checked_at": "<UTC ISO-8601, e.g. 2026-09-30T18:00:00Z>",
      "alerted_failures": {"<failure id>": "<failure text>", ...},
      "alerted_at": "<UTC ISO-8601 of the last CONFIRMED delivery>" | null
    }

`"unknown"` appears on disk ONLY the very first time this monitor ever runs
and the episode fetch fails before any prior state exists — every other
network hiccup leaves the last known status in place untouched (constraint
3). `failures` is what THIS run actually observed (from `checks_ran`);
`alerted_failures` is the superset that includes ids from a check that
didn't run this cycle and so couldn't be confirmed cleared — comparing the
two tells a reader whether something "still owed" is currently unverified.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from send2trash import send2trash  # noqa: E402

from backend.utils.alerts import send_alert  # noqa: E402
from scripts import session_pipeline_status as sps  # noqa: E402

LABEL = "muffinpanrecipes pipeline"

# Separator used inside a failure id (<group><SEP><episode_id><SEP><text>).
# \x1f (ASCII unit separator) rather than something printable, so it can
# never collide with a character that legitimately appears in an episode id
# or a failure message.
_ID_SEP = "\x1f"

# Strips date/time text from a failure message before folding it into an id,
# so a value that legitimately varies run-to-run (a timestamp) or tick-to-
# tick (an hourly count) can never make an unchanged failure look "new" and
# re-alert every hour. Matches both "YYYY-MM-DD HH:MM[:SS]" and full
# ISO-8601 ("...T...Z") shapes; session_pipeline_status.py's own failures use
# the former (see episode_integrity.stage_deadline formatting).
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?Z?")

# Resolved at call time (not a module-level constant) so MUFFINPAN_PIPELINE_STATE_FILE
# actually takes effect and tests can point it at a temp path, same pattern as
# scripts/health_check.py's DEFAULT_STATE_FILE / _state_file().
DEFAULT_STATE_FILE = str(
    Path.home() / ".local" / "state" / "muffinpanrecipes" / "pipeline_status.json"
)


def _default_state_file() -> Path:
    return Path(os.environ.get("MUFFINPAN_PIPELINE_STATE_FILE", DEFAULT_STATE_FILE))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_failure(text: str) -> str:
    """Strip date/time text so an id built from a failure is stable across
    runs even if a message ever embeds a timestamp or a ticking count."""
    return _TIMESTAMP_RE.sub("<TS>", text)


def _failure_id(group: str, episode_id: str, text: str) -> str:
    """A stable identity for "this specific failure, this week, from this
    check". The group prefix lets `_failure_id_group` recover which check
    produced an id we only have from a PREVIOUS run's state file, which is
    what makes correct clearing possible without re-deriving anything."""
    return _ID_SEP.join((group, episode_id, _normalize_failure(text)))


def _failure_id_group(failure_id: str) -> str:
    return failure_id.split(_ID_SEP, 1)[0]


def compute_verdict(episode_id: str | None = None) -> dict:
    """Reuse session_pipeline_status.py's fetch + verdict logic, structured
    per-check so the caller can tell which checks ran this time.

    Returns either:
      {"kind": "unknown", "episode_id": eid, "summary": "..."}
        — the episode fetch itself failed; no check ran at all.
      {"kind": "observed", "episode_id": eid, "summary": "...",
       "checks_ran": ("episode",) | ("episode", "catalog"),
       "observed_failures": {"<failure id>": "<failure text>", ...}}
        — the episode fetch succeeded; the catalog-dependent check
          (title-collision) additionally ran iff the catalog fetch produced
          a usable list or dict. `_get_json`, `current_episode_id`,
          `BLOB_CDN`, `episode_integrity_failures`, and `episode_summary`
          are the SAME functions session_pipeline_status.py runs — nothing
          here refetches or re-derives a verdict independently.
    """
    eid = episode_id or sps.current_episode_id()
    episode = sps._get_json(f"{sps.BLOB_CDN}/episodes/{eid}.json")

    if not isinstance(episode, dict):
        return {
            "kind": "unknown",
            "episode_id": eid,
            "summary": f"could not read {eid} from blob",
        }

    # One `now` for both calls below, so the two runs of
    # episode_integrity_failures (with and without the catalog) can never
    # disagree about which stages are "due" purely because a few
    # milliseconds of wall-clock time passed between them.
    now = datetime.now(timezone.utc)
    episode_only = list(sps.episode_integrity_failures(episode, catalog=None, now=now))

    raw_catalog = sps._get_json(f"{sps.BLOB_CDN}/pages/recipes.json")
    catalog: list[dict] | None
    if isinstance(raw_catalog, list):
        catalog = raw_catalog
    elif isinstance(raw_catalog, dict):
        catalog = raw_catalog.get("recipes", [])
    else:
        # Covers BOTH a failed fetch (raw_catalog is None) and a fetched-but-
        # malformed response alike: either way the catalog-dependent check
        # simply does not run this cycle. This used to be two different
        # code paths (round 2 special-cased None into a whole-verdict
        # "unknown"); per-check tracking makes them the same thing.
        catalog = None

    if catalog is not None:
        full = list(sps.episode_integrity_failures(episode, catalog=catalog, now=now))
        checks_ran: tuple[str, ...] = ("episode", "catalog")
    else:
        full = episode_only
        checks_ran = ("episode",)

    summary = sps.episode_summary(episode)

    observed: dict[str, str] = {
        _failure_id("episode", eid, text): text for text in episode_only
    }
    if "catalog" in checks_ran:
        for text in full:
            if text not in episode_only:
                observed[_failure_id("catalog", eid, text)] = text

    return {
        "kind": "observed",
        "episode_id": eid,
        "summary": summary,
        "checks_ran": checks_ran,
        "observed_failures": observed,
    }


def read_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as e:
        # A corrupt or unreadable state file is treated as "no prior state"
        # rather than crashing the job — see module docstring, constraint 1.
        print(f"{LABEL}: state read failed ({type(e).__name__}: {e})", file=sys.stderr)
        return None


def write_state(path: Path, state: dict) -> None:
    """Atomic replace via a unique temp file in the same directory.

    `tempfile.mkstemp` (not a fixed `.tmp` suffix) so two processes that
    somehow both reach this function never write through the same temp
    path — the exclusive lock in `run()` is the real race guard, this is
    belt-and-suspenders so a leftover temp file from a killed process can
    never collide with a fresh write.
    """
    tmp_name: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(state, indent=2) + "\n")
        os.replace(tmp_name, path)
        tmp_name = None
    except Exception as e:
        print(f"{LABEL}: state write failed ({type(e).__name__}: {e})", file=sys.stderr)
    finally:
        # The repo forbids permanent deletion (AGENTS.md) even for an
        # orphaned temp file — trash it instead, same as
        # scripts/conversation_lab.py's _write_pairs_report_atomic. A trash
        # failure is logged, not raised: constraint 1 wins even here, a
        # failed cleanup after an already-failed write must not be the
        # reason this monitor stops.
        if tmp_name is not None and os.path.exists(tmp_name):
            try:
                send2trash(tmp_name)
            except Exception as trash_exc:
                print(
                    f"{LABEL}: could not trash orphaned temp file {tmp_name} "
                    f"({type(trash_exc).__name__}: {trash_exc})",
                    file=sys.stderr,
                )


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path):
    """Non-blocking exclusive file lock guarding read-decide-alert-write.

    Yields True if the lock was acquired (caller should proceed) or False if
    another invocation currently holds it (caller should do nothing and
    return 0 quietly). Two overlapping launchd ticks — a slow run still in
    flight when the next hourly one fires — must never both decide "not yet
    alerted" and both send.

    Never delete this lock file (see ops/launchd/README.md): flock locks the
    open file description tied to a specific inode, so deleting the file out
    from under a holder and letting a new run create a fresh one at the same
    path produces two independently-lockable inodes — the guard silently
    stops working. It is never necessary: flock releases automatically when
    the holding process exits for ANY reason, so a stale lock from a dead
    process simply isn't a real state.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _alert_new_failures(verdict: dict, new_texts: list[str], all_texts: list[str]) -> bool:
    """Announce failures never before alerted. Returns whether it was
    actually delivered — the caller must not record them as sent otherwise
    (round 1 correction A, unchanged)."""
    still_open = [t for t in all_texts if t not in new_texts]
    lines = [f"{LABEL}: DEGRADED — {_utc_now_iso()}", "", verdict["summary"], "", "New:"]
    lines += [f"- {t}" for t in new_texts]
    if still_open:
        lines += ["", "Still open:"]
        lines += [f"- {t}" for t in still_open]
    try:
        return bool(
            send_alert(
                subject=f"{LABEL} DEGRADED",
                body="\n".join(lines)[:1900],
                severity="warning",
                fields=[
                    (f"failure {i + 1}", t[:300], False) for i, t in enumerate(new_texts[:5])
                ],
            )
        )
    except Exception as e:
        # send_alert already swallows per-backend failures; this is a last
        # resort so a totally unexpected error here still can't block main().
        print(f"{LABEL}: alert (degraded) failed ({type(e).__name__}: {e})", file=sys.stderr)
        return False


def _alert_recovered(verdict: dict) -> bool:
    """Send the recovery alert. Returns whether it was actually delivered."""
    try:
        return bool(
            send_alert(
                subject=f"{LABEL} recovered",
                body=(f"{LABEL}: back to OK — {_utc_now_iso()}\n{verdict['summary']}")[:1900],
                severity="info",
            )
        )
    except Exception as e:
        print(f"{LABEL}: alert (recovery) failed ({type(e).__name__}: {e})", file=sys.stderr)
        return False


def _run_locked(state_path: Path, episode_id: str | None) -> int:
    """The actual read-decide-alert-write body, run under `run()`'s lock."""
    verdict = compute_verdict(episode_id)

    if verdict["kind"] == "unknown":
        # KEEP: no check ran at all, so there's nothing to compare, clear,
        # or alert on. Only write when there is nothing to clobber
        # (first-ever run — this is the ONE case "unknown" is ever
        # persisted), so a reader has something rather than nothing.
        if read_state(state_path) is None:
            write_state(
                state_path,
                {
                    "status": "unknown",
                    "episode_id": verdict["episode_id"],
                    "summary": verdict["summary"],
                    "failures": [],
                    "checks_ran": [],
                    "checked_at": _utc_now_iso(),
                    "alerted_failures": {},
                    "alerted_at": None,
                },
            )
        else:
            print(f"{LABEL}: unknown verdict — keeping last known state")
        return 0

    checks_ran = set(verdict["checks_ran"])
    observed: dict[str, str] = verdict["observed_failures"]
    observed_ids = set(observed)

    previous = read_state(state_path) or {}
    prev_alerted: dict[str, str] = previous.get("alerted_failures") or {}
    prev_alerted_ids = set(prev_alerted)

    # A previously-alerted id is cleared only when the check that produces
    # it ran THIS cycle and no longer reports it. An id whose check did NOT
    # run this cycle (e.g. catalog down) is left exactly as it was — neither
    # cleared (unverified) nor re-alerted (already announced once).
    cleared_ids = {
        fid
        for fid in prev_alerted_ids
        if _failure_id_group(fid) in checks_ran and fid not in observed_ids
    }
    after_clear_ids = prev_alerted_ids - cleared_ids
    new_ids = observed_ids - prev_alerted_ids

    committed_alerted: dict[str, str] = {i: prev_alerted[i] for i in after_clear_ids}
    alerted_at = previous.get("alerted_at")

    if new_ids:
        new_texts = [observed[i] for i in sorted(new_ids)]
        all_texts = [observed[i] for i in sorted(observed_ids)]
        if _alert_new_failures(verdict, new_texts, all_texts):
            committed_alerted.update({i: observed[i] for i in new_ids})
            alerted_at = _utc_now_iso()
        else:
            # Clearing (unrelated old failures confirmed gone) still applies
            # regardless — only the NEW ids stay unconfirmed so they're
            # retried next run (round 1 correction A, generalized to a set).
            print(
                f"{LABEL}: DEGRADED alert (new failures) was not delivered on any "
                "channel; left pending for the next run",
                file=sys.stderr,
            )
    elif not after_clear_ids and prev_alerted_ids:
        # Nothing new, and everything previously alerted just cleared: a
        # full recovery.
        if _alert_recovered(verdict):
            committed_alerted = {}
            alerted_at = _utc_now_iso()
        else:
            # Keep the ENTIRE prior set (undo the clearing too) so the same
            # recovery is retried next run rather than silently applied.
            committed_alerted = dict(prev_alerted)
            print(
                f"{LABEL}: recovery alert was not delivered on any channel; "
                "left pending for the next run",
                file=sys.stderr,
            )
    # else: no new ids and either nothing cleared, or a PARTIAL clear that
    # still leaves something outstanding — silent, `committed_alerted`
    # already reflects whatever cleared (set above).

    status = "degraded" if (observed_ids or committed_alerted) else "ok"

    write_state(
        state_path,
        {
            "status": status,
            "episode_id": verdict["episode_id"],
            "summary": verdict["summary"],
            "failures": [observed[i] for i in sorted(observed_ids)],
            "checks_ran": sorted(checks_ran),
            "checked_at": _utc_now_iso(),
            "alerted_failures": committed_alerted,
            "alerted_at": alerted_at,
        },
    )
    return 0


def run(state_path: Path, episode_id: str | None = None) -> int:
    lock_path = state_path.with_name(state_path.name + ".lock")
    with _exclusive_lock(lock_path) as acquired:
        if not acquired:
            print(
                f"{LABEL}: another run holds the lock ({lock_path}); skipping this tick",
                file=sys.stderr,
            )
            return 0
        return _run_locked(state_path, episode_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--episode",
        help="ISO week to inspect, e.g. 2026-W36 (default: the current week)",
    )
    parser.add_argument(
        "--state-file",
        help=(
            "Path to the JSON state file (default: $MUFFINPAN_PIPELINE_STATE_FILE "
            f"or {DEFAULT_STATE_FILE})"
        ),
    )
    args = parser.parse_args()

    state_path = Path(args.state_file) if args.state_file else _default_state_file()
    return run(state_path, args.episode)


def _safe_main() -> int:
    """`main()` wrapped in the last-resort guard, factored out so it's
    directly testable (rather than only reachable via `__main__`).

    Constraint 1 wins over everything else: this monitor must never be the
    reason a launchd tick (or anything else) fails, no matter what breaks
    inside `main()`.
    """
    try:
        return main()
    except Exception as exc:  # pragma: no cover - exercised via tests directly
        print(f"{LABEL}: unknown ({type(exc).__name__}: {exc})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(_safe_main())
