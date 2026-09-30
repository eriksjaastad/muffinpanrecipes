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
fetch), and alerting on a CHANGE to DEGRADED plus one recovery notice.

Design constraints carried over from session_pipeline_status.py (per #7006,
"KEEP" — this script must not change that check's design):
  1. NEVER block. Always exits 0, including on total failure — a monitor
     must never be the reason a login or a launchd tick goes red.
  2. The 6s-per-request network bound lives in session_pipeline_status.py's
     _get_json and is inherited unchanged.
  3. Silent on network errors: a fetch failure produces "unknown", not an
     alert, and must never overwrite an already-known ("ok"/"degraded")
     verdict on disk — a flaky connection must not erase real history or
     spam every hour.

Alerting goes through backend/utils/alerts.py::send_alert, the one door
every operational alert in this project uses (Discord + email, both on
every severity per #7097) — no new channel is invented here.

Three corrections made after Codex review of fb3fb3c (#7006):

  A. `status` (what was just OBSERVED) and `alerted` (the status + failure
     signature of the last CONFIRMED delivery) are now separate fields.
     `send_alert`'s boolean return is checked; a delivery that fails on every
     channel leaves `alerted` untouched, so the NEXT run still sees "not yet
     alerted for this" and retries — same pattern as #7403's advisory alert
     ("only a confirmed delivery clears what is owed"). Before this, a failed
     delivery was recorded as delivered, and a persistent DEGRADED with no
     working alert channel would never alert again.
  B. Re-alerting on a persistent DEGRADED is keyed on a normalized failure
     SIGNATURE (episode id + sorted, timestamp-stripped failure text), not
     merely on the status staying "degraded". A new, different failure surfaces
     one alert even while already degraded; the same failure repeating stays
     silent.
  C. The read-decide-alert-write section runs under an exclusive, non-blocking
     file lock (`fcntl.flock` on a `.lock` file beside the state file) so two
     overlapping invocations (e.g. a slow run still in flight when the next
     hourly tick fires) cannot both observe "not yet alerted" and both send.
     A run that cannot get the lock logs it and exits 0 immediately, sending
     nothing. `write_state` replaces the file atomically via a unique
     `tempfile.mkstemp` name in the same directory.

Usage:
    uv run python scripts/pipeline_monitor.py
    uv run python scripts/pipeline_monitor.py --episode 2026-W36
    uv run python scripts/pipeline_monitor.py --state-file /tmp/state.json

State file schema (JSON):
    {
      "status": "ok" | "degraded",
      "episode_id": "<ISO week, e.g. 2026-W40>",
      "summary": "<one-line episode_summary() output>",
      "failures": ["<failure detail>", ...],
      "checked_at": "<UTC ISO-8601, e.g. 2026-09-30T18:00:00Z>",
      "alerted": {
        "status": "ok" | "degraded" | null,
        "signature": "<sha256 of episode_id + normalized failures>" | null,
        "at": "<UTC ISO-8601 of the last CONFIRMED delivery>" | null
      }
    }

`status` is what THIS run observed; `alerted` is what has actually been
delivered so far. They can differ — a DEGRADED run whose alert failed to
send leaves `status: "degraded"` but `alerted.status` still at its previous
value, so the next run retries the delivery rather than staying silent.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.alerts import send_alert  # noqa: E402
from scripts import session_pipeline_status as sps  # noqa: E402

LABEL = "muffinpanrecipes pipeline"

# The "nothing has ever been delivered" alerted-state, used both as the
# starting point for a brand-new state file and whenever a previous state
# file has no `alerted` key (older format).
_EMPTY_ALERTED = {"status": None, "signature": None, "at": None}

# Strips date/time text from a failure message before hashing it into a
# signature, so a value that legitimately varies run-to-run (a timestamp) or
# tick-to-tick (an hourly count) can never make an unchanged failure look
# "new" and re-alert every hour. Matches both "YYYY-MM-DD HH:MM[:SS]" and
# full ISO-8601 ("...T...Z") shapes; session_pipeline_status.py's own
# failures use the former (see episode_integrity.stage_deadline formatting).
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


def compute_verdict(episode_id: str | None = None) -> dict:
    """Reuse session_pipeline_status.py's fetch + verdict logic, structured.

    Mirrors sps.main() line for line, but returns a dict instead of printing,
    so this script can persist and diff it. `_get_json`, `current_episode_id`,
    `BLOB_CDN`, `episode_integrity_failures`, and `episode_summary` are all
    the SAME functions session_pipeline_status.py runs — nothing here
    refetches or re-derives a verdict independently.
    """
    eid = episode_id or sps.current_episode_id()
    episode = sps._get_json(f"{sps.BLOB_CDN}/episodes/{eid}.json")

    if not isinstance(episode, dict):
        return {
            "status": "unknown",
            "episode_id": eid,
            "summary": f"could not read {eid} from blob",
            "failures": [],
        }

    raw_catalog = sps._get_json(f"{sps.BLOB_CDN}/pages/recipes.json")
    catalog: list[dict] | None
    if isinstance(raw_catalog, list):
        catalog = raw_catalog
    elif isinstance(raw_catalog, dict):
        catalog = raw_catalog.get("recipes", [])
    else:
        catalog = None  # skips only the title-collision assertion, same as sps.main

    failures = list(sps.episode_integrity_failures(episode, catalog=catalog))
    summary = sps.episode_summary(episode)
    status = "degraded" if failures else "ok"
    return {"status": status, "episode_id": eid, "summary": summary, "failures": failures}


def _normalize_failure(text: str) -> str:
    """Strip date/time text so a signature over failures is stable across
    runs even if a message ever embeds a timestamp or a ticking count."""
    return _TIMESTAMP_RE.sub("<TS>", text)


def failure_signature(episode_id: str, failures: list[str]) -> str:
    """A stable identity for "this specific set of failures, this week".

    Sorted + normalized so failure ORDER and embedded timestamps can't change
    the signature, but a genuinely different failure (or the same failure on
    a new episode_id) does. Used to decide whether a persistent DEGRADED
    status is still the same incident (stay silent) or a new one (alert
    once) — see module docstring, correction B.
    """
    normalized = sorted(_normalize_failure(f) for f in failures)
    raw = "\x1f".join([episode_id, *normalized])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        if tmp_name is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path):
    """Non-blocking exclusive file lock guarding read-decide-alert-write.

    Yields True if the lock was acquired (caller should proceed) or False if
    another invocation currently holds it (caller should do nothing and
    return 0 quietly — see module docstring, correction C). Two overlapping
    launchd ticks — a slow run still in flight when the next hourly one fires
    — must never both decide "not yet alerted" and both send.
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


def _alert_degraded(verdict: dict) -> bool:
    """Send the DEGRADED alert. Returns whether it was actually delivered —
    the caller must not record the alert as sent otherwise (correction A)."""
    failures = verdict.get("failures") or []
    lines = [f"{LABEL}: DEGRADED — {_utc_now_iso()}", "", verdict["summary"], ""]
    lines += [f"- {f}" for f in failures]
    try:
        return bool(
            send_alert(
                subject=f"{LABEL} DEGRADED",
                body="\n".join(lines)[:1900],
                severity="warning",
                fields=[
                    (f"failure {i + 1}", f[:300], False) for i, f in enumerate(failures[:5])
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
    status = verdict["status"]

    if status == "unknown":
        # KEEP: a network blip must never alert, and must never clobber a
        # known verdict already on disk. Only write when there is nothing to
        # clobber (first-ever run), so a reader has something rather than
        # nothing.
        if read_state(state_path) is None:
            write_state(
                state_path,
                {**verdict, "checked_at": _utc_now_iso(), "alerted": dict(_EMPTY_ALERTED)},
            )
        else:
            print(f"{LABEL}: unknown verdict — keeping last known state")
        return 0

    previous = read_state(state_path)
    prev_alerted = (previous or {}).get("alerted") or _EMPTY_ALERTED

    current_signature = (
        failure_signature(verdict["episode_id"], verdict["failures"])
        if status == "degraded"
        else None
    )

    # Default: carry the last CONFIRMED delivery forward unchanged. Only a
    # successful send below replaces it — a failed one leaves this exactly as
    # it was, so the next run's comparison still says "not yet delivered" and
    # retries (correction A).
    new_alerted = dict(prev_alerted)

    if status == "degraded" and (
        prev_alerted.get("status") != "degraded"
        or prev_alerted.get("signature") != current_signature
    ):
        # Covers OK -> DEGRADED, unknown -> DEGRADED, first-ever-run ->
        # DEGRADED, AND a persistent DEGRADED whose failure signature just
        # changed (a new/different failure, or a new week's episode failing
        # the same way — correction B).
        if _alert_degraded(verdict):
            new_alerted = {
                "status": "degraded",
                "signature": current_signature,
                "at": _utc_now_iso(),
            }
        else:
            print(
                f"{LABEL}: DEGRADED alert was not delivered on any channel; "
                "left pending for the next run",
                file=sys.stderr,
            )
    elif status == "ok" and prev_alerted.get("status") == "degraded":
        if _alert_recovered(verdict):
            new_alerted = {"status": "ok", "signature": None, "at": _utc_now_iso()}
        else:
            print(
                f"{LABEL}: recovery alert was not delivered on any channel; "
                "left pending for the next run",
                file=sys.stderr,
            )
    # else: degraded -> degraded with the SAME signature, or ok -> ok: no
    # alert, by design (#7006 — a persistent failure that re-alerts every
    # hour becomes decoration).

    write_state(
        state_path,
        {**verdict, "checked_at": _utc_now_iso(), "alerted": new_alerted},
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
