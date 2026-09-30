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

Usage:
    uv run python scripts/pipeline_monitor.py
    uv run python scripts/pipeline_monitor.py --episode 2026-W36
    uv run python scripts/pipeline_monitor.py --state-file /tmp/state.json

State file schema (JSON):
    {
      "status": "ok" | "degraded",
      "summary": "<one-line episode_summary() output>",
      "failures": ["<failure detail>", ...],
      "checked_at": "<UTC ISO-8601, e.g. 2026-09-30T18:00:00Z>"
    }
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.utils.alerts import send_alert  # noqa: E402
from scripts import session_pipeline_status as sps  # noqa: E402

LABEL = "muffinpanrecipes pipeline"

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
    return {"status": status, "summary": summary, "failures": failures}


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
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception as e:
        print(f"{LABEL}: state write failed ({type(e).__name__}: {e})", file=sys.stderr)


def _alert_degraded(verdict: dict) -> None:
    failures = verdict.get("failures") or []
    lines = [f"{LABEL}: DEGRADED — {_utc_now_iso()}", "", verdict["summary"], ""]
    lines += [f"- {f}" for f in failures]
    try:
        send_alert(
            subject=f"{LABEL} DEGRADED",
            body="\n".join(lines)[:1900],
            severity="warning",
            fields=[(f"failure {i + 1}", f[:300], False) for i, f in enumerate(failures[:5])],
        )
    except Exception as e:
        # send_alert already swallows per-backend failures; this is a last
        # resort so a totally unexpected error here still can't block main().
        print(f"{LABEL}: alert (degraded) failed ({type(e).__name__}: {e})", file=sys.stderr)


def _alert_recovered(verdict: dict) -> None:
    try:
        send_alert(
            subject=f"{LABEL} recovered",
            body=(f"{LABEL}: back to OK — {_utc_now_iso()}\n{verdict['summary']}")[:1900],
            severity="info",
        )
    except Exception as e:
        print(f"{LABEL}: alert (recovery) failed ({type(e).__name__}: {e})", file=sys.stderr)


def run(state_path: Path, episode_id: str | None = None) -> int:
    verdict = compute_verdict(episode_id)
    status = verdict["status"]

    if status == "unknown":
        # KEEP: a network blip must never alert, and must never clobber a
        # known verdict already on disk. Only write when there is nothing to
        # clobber (first-ever run), so a reader has something rather than
        # nothing.
        if read_state(state_path) is None:
            write_state(state_path, {**verdict, "checked_at": _utc_now_iso()})
        else:
            print(f"{LABEL}: unknown verdict — keeping last known state")
        return 0

    previous = read_state(state_path)
    previous_status = previous.get("status") if previous else None

    if status == "degraded" and previous_status != "degraded":
        # Covers OK -> DEGRADED, unknown -> DEGRADED, and first-ever-run ->
        # DEGRADED (previous_status is None in the last two cases).
        _alert_degraded(verdict)
    elif status == "ok" and previous_status == "degraded":
        _alert_recovered(verdict)
    # degraded -> degraded and ok -> ok: no alert, by design (#7006 — a
    # persistent failure that re-alerts every hour becomes decoration).

    write_state(state_path, {**verdict, "checked_at": _utc_now_iso()})
    return 0


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
