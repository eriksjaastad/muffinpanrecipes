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

Alerting goes through backend/utils/alerts.py, the one door every
operational alert in this project uses (Discord + email, both on every
severity per #7097) — no new channel is invented here. It calls
`send_alert_confirming_email`, which attempts both channels like
`send_alert` but reports delivery only when EMAIL accepted: the human reads
email, so a Discord-only success must not mark a failure as told (round 15).

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

Round 3 replaced the single whole-set signature with the per-check,
per-failure-id model described above, and fixed the lock-file guidance and
`send2trash` usage from round 2's own review.

Round 4 (this version) found two edge cases the per-check model itself
didn't cover:
  F. The catalog-shape check was too permissive: `isinstance(raw_catalog,
     dict)` plus `raw_catalog.get("recipes", [])` treats `{"error": "..."}`,
     `{"recipes": "invalid"}`, and a dict missing `"recipes"` entirely as a
     usable (if empty) catalog — same for `isinstance(raw_catalog, list)`
     with a list of non-dict items. `_catalog_titles_excluding_self` then
     iterates to zero titles for all of these (it skips non-dict entries),
     so the catalog group "ran" and "found no collision" purely because the
     payload was garbage: a real alerted title-collision failure would
     silently clear (a false recovery), then re-alert once the real catalog
     came back. `_valid_catalog()` now validates the actual shape the
     title-collision check reads; anything that doesn't match means the
     catalog group did not run this cycle, exactly like a fetch failure.
  G. `read_state` trusted ANY dict it parsed. A pre-round-3 state file
     (`{"alerted": {...}}` instead of `{"alerted_failures": {...}}`) read as
     "nothing has ever been alerted," making an unchanged failure look brand
     new and alert once for no real reason; a non-object JSON value (an
     array, a bare string or number) made `.get()` raise on every run —
     `_safe_main`'s guard kept the exit code at 0, but the monitor could
     never proceed past that exception, so it was permanently stuck doing
     nothing while LOOKING like it succeeded. `read_state` now validates the
     loaded value against the current schema; anything else is logged to
     stderr and moved aside with `send2trash` (never deleted), and the run
     proceeds as if there were no prior state at all (only once the move
     succeeds; see `StateUnavailable` otherwise). This monitor has never
     been installed, so there is no real history to protect — a fresh start
     may alert once for a currently-failing pipeline, which is an accepted,
     one-time cost (see ops/launchd/README.md), not a bug.

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

A state file that doesn't match this schema (an old pre-round-3 shape, or
non-JSON-object garbage) is treated as if it didn't exist: logged to stderr,
moved aside via `send2trash`, and the run proceeds as a fresh start (round
4) — see `_is_current_schema`/`_discard_unusable_state`.
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

from backend.utils.alerts import email_channel_status, send_alert_confirming_email  # noqa: E402
from scripts import session_pipeline_status as sps  # noqa: E402

LABEL = "muffinpanrecipes pipeline"

# Separator used inside a failure id (<group><SEP><episode_id><SEP><text>).
# \x1f (ASCII unit separator) rather than something printable, so it can
# never collide with a character that legitimately appears in an episode id
# or a failure message.
_ID_SEP = "\x1f"
# The check groups a verdict can run; every stored failure id belongs to one.
_CHECK_GROUPS = frozenset({"episode", "catalog"})

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
    # The id separator can never appear inside the text segment, so a stored
    # id always splits into exactly (group, episode_id, text) (round 6).
    return _TIMESTAMP_RE.sub("<TS>", text).replace(_ID_SEP, " ")


def _failure_id(group: str, episode_id: str, text: str) -> str:
    """A stable identity for "this specific failure, this week, from this
    check". The group prefix lets `_failure_id_group` recover which check
    produced an id we only have from a PREVIOUS run's state file, which is
    what makes correct clearing possible without re-deriving anything."""
    return _ID_SEP.join((group, episode_id, _normalize_failure(text)))


def _failure_id_group(failure_id: str) -> str:
    return failure_id.split(_ID_SEP, 1)[0]


def _failure_id_episode(failure_id: str) -> str:
    return failure_id.split(_ID_SEP, 2)[1]


def _retired_label(failure_id: str, text: str) -> str:
    """How a failure from a closed week is shown: never as resolved."""
    return f"[{_failure_id_episode(failure_id)}: week closed, no longer checked] {text}"


def _valid_catalog(raw_catalog: object) -> list[dict] | None:
    """Validate the catalog payload against exactly what the title-collision
    check reads (`_catalog_titles_excluding_self` in
    backend/utils/episode_integrity.py): a JSON list of dict entries with a
    `title` field, or a dict wrapping such a list under `"recipes"`.

    Returns the concrete list (possibly empty — a genuinely empty published
    catalog is a legitimate, if degenerate, state the title check can run
    against) when the shape checks out, or `None` when it doesn't — `None`
    means the catalog GROUP did not run this cycle, exactly like a fetch
    failure (see `compute_verdict`).

    Round 4 found the previous `isinstance(raw_catalog, list/dict)` check
    too permissive: `{"error": "temporary failure"}` and `{"recipes":
    "invalid"}` both satisfy `isinstance(raw_catalog, dict)`, and
    `raw_catalog.get("recipes", [])` silently coerces either into `[]`. A
    list of non-dict items passes `isinstance(raw_catalog, list)` the same
    way. In every one of those cases, `_catalog_titles_excluding_self`
    iterates to zero titles (`if not isinstance(entry, dict): continue`
    skips every entry), so the title check "ran" and "found no collision"
    purely because the payload was garbage — clearing a real alerted title-
    collision failure and then re-alerting once the real catalog returned.
    Validating the actual shape here means a malformed-but-present response
    is treated exactly like a fetch failure: the catalog group simply did
    not run.
    """
    if isinstance(raw_catalog, list):
        candidate = raw_catalog
    elif isinstance(raw_catalog, dict):
        candidate = raw_catalog.get("recipes")
    else:
        return None
    if not isinstance(candidate, list):
        return None
    # Every entry must carry a usable title. The title check skips an entry
    # without one, so `[{}]` would otherwise "run" the check against zero
    # titles and falsely clear an alerted collision (round 7).
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("title"), str)
        and item["title"].strip()
        for item in candidate
    ):
        return None
    return candidate


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
          a usable catalog (`_valid_catalog`). `_get_json`, `current_episode_id`,
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
    # Covers a failed fetch (raw_catalog is None) AND a fetched-but-malformed
    # response (wrong shape, or present but not actually a list of recipe
    # dicts — see `_valid_catalog`, round 4) identically: either way the
    # catalog-dependent check simply does not run this cycle.
    catalog = _valid_catalog(raw_catalog)

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


def _is_current_schema(state: object) -> bool:
    """True iff `state` matches the CURRENT (round 3+) schema closely enough
    to trust its `alerted_failures`/`checks_ran` fields.

    Deliberately simple: this monitor has never been installed for real
    (#7006), so there is no real migration to support — only "don't crash on
    a non-object JSON value" and "don't silently misread a pre-round-3
    `{"alerted": {...}}` shape as if `alerted_failures` were merely empty."
    Both a bare non-dict value and an old-schema dict lack a dict
    `alerted_failures` and a list `checks_ran`, so this one check catches
    both (round 4).
    """
    if not (
        isinstance(state, dict)
        and isinstance(state.get("alerted_failures"), dict)
        and isinstance(state.get("checks_ran"), list)
    ):
        return False
    # Every stored failure id must be one this monitor can produce, and so
    # one a future run can clear: "<group>\x1f<episode_id>\x1f<text>" with a
    # known group. An id with an unknown group could never clear, pinning
    # the monitor to degraded with no recovery (round 5).
    # The id must also be the one its own stored text produces: a non-empty
    # text whose normalized form is exactly the id's text segment. Otherwise
    # an entry like {<current failure id>: ""} would validate, and the next
    # run would treat a real failure as already alerted (round 7).
    # `pending_failures` (round 11) gets the same checks, must not overlap
    # `alerted_failures` (a failure is either told or owed, never both), and
    # may be absent in a file written before it existed.
    pending = state.get("pending_failures", {})
    if not isinstance(pending, dict) or set(pending) & set(state["alerted_failures"]):
        return False
    for failure_map in (state["alerted_failures"], pending):
        for failure_id, text in failure_map.items():
            parts = failure_id.split(_ID_SEP, 2) if isinstance(failure_id, str) else []
            if (
                len(parts) != 3
                or parts[0] not in _CHECK_GROUPS
                or not parts[1]
                or not isinstance(text, str)
                or not text.strip()
                or parts[2] != _normalize_failure(text)
            ):
                return False
    return all(isinstance(group, str) and group in _CHECK_GROUPS for group in state["checks_ran"])


class StateUnavailable(Exception):
    """The state file exists but can be neither read nor moved aside.

    Treating that as "no prior state" would re-send every current alert and
    then overwrite the file with a replacement, losing the history that was
    in it (round 8). The caller skips the whole state transition instead:
    no alert, no write, the file left exactly where it is.
    """


def _discard_unusable_state(path: Path, reason: str) -> None:
    """Log and move an unusable state file aside — never delete it outright
    (AGENTS.md). Called for both invalid JSON and a recognized-but-wrong
    schema, so a garbage or stale file is only ever warned about once: the
    next run sees no file at all and proceeds like any other first-ever run.

    Raises `StateUnavailable` when the move fails: the file is still there,
    so this run must not start fresh on top of it.
    """
    print(
        f"{LABEL}: state file {path} is unusable ({reason}) — moving it aside",
        file=sys.stderr,
    )
    try:
        send2trash(str(path))
    except Exception as trash_exc:
        raise StateUnavailable(
            f"could not move unusable state file {path} aside "
            f"({type(trash_exc).__name__}: {trash_exc})"
        ) from trash_exc


def read_state(path: Path) -> dict | None:
    """The trusted prior state, or None when there is none (no file, or an
    unusable one that was successfully moved aside).

    Raises `StateUnavailable` when the file exists but can be neither read
    nor moved aside (round 8).
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except Exception as e:
        # An unreadable (not merely unparsable) state file — a permissions
        # error, for instance — is NOT "no prior state": it may hold alert
        # history this run cannot see. It is not trashed either (this monitor
        # may simply lack permission to touch it).
        raise StateUnavailable(
            f"state read failed ({type(e).__name__}: {e})"
        ) from e

    try:
        state = json.loads(raw_text)
    except Exception as e:
        _discard_unusable_state(path, f"invalid JSON: {type(e).__name__}: {e}")
        return None

    if _is_current_schema(state):
        return state

    # Either a non-object JSON value (an array, a string, a bare number —
    # `previous.get(...)` on any of those raises, which used to mean this
    # monitor ran forever without ever successfully alerting or recovering,
    # "stuck" behind an exit-0 exception every single run — constraint 1
    # forbids that even though _safe_main's guard technically kept the exit
    # code at 0) or a pre-round-3 `{"alerted": {...}}` schema (which would
    # otherwise be silently read as "alerted_failures is empty," making an
    # UNCHANGED failure look brand new and re-alert once for no real reason).
    _discard_unusable_state(path, "not a recognized schema (pre-round-3 or non-object)")
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
        # Raised, not swallowed: a caller must know its state did not land
        # (gate review on b7065b2). The temp file is still cleaned up below.
        print(f"{LABEL}: state write failed ({type(e).__name__}: {e})", file=sys.stderr)
        raise
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


def _write_state_logged(path: Path, state: dict) -> bool:
    """`write_state` for a write whose failure must not stop the run (it is
    already reported on stderr by write_state). Returns whether it landed."""
    try:
        write_state(path, state)
        return True
    except Exception:
        print(f"{LABEL}: continuing without a saved state this run", file=sys.stderr)
        return False


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


# Largest alert body that every backend delivers whole: alerts.py cuts the
# Discord description at 4000 characters. The body is built to fit this
# budget, never cut after the fact (round 9).
_ALERT_BODY_BUDGET = 3800
# One failure longer than this is shortened so a single huge message can't
# starve every other failure of room. Its id still records it as alerted.
_ALERT_TEXT_MAX = 1500


def _compose_degraded_body(summary: str, new_texts: list[str], still_open: list[str]) -> tuple[str, int]:
    """The DEGRADED alert body and how many of `new_texts` (a leading run,
    in order) it contains in full or as a marked excerpt.

    Only those are recorded as alerted. A new failure that doesn't fit is
    left out whole, stays unrecorded, and leads the next run's alert (round
    9: a cut-off body used to record failures nobody was ever shown).
    """
    def _item(text: str) -> str:
        if len(text) > _ALERT_TEXT_MAX:
            text = text[:_ALERT_TEXT_MAX] + " … [shortened]"
        return f"- {text}"

    head = [f"{LABEL}: DEGRADED — {_utc_now_iso()}", "", summary[:500], "", "New:"]
    # Room kept free for the "more to follow" line, so adding it can never
    # push the body over budget.
    more_line_room = 80
    body = "\n".join(head)
    included = 0
    for text in new_texts:
        line = _item(text)
        if len(body) + 1 + len(line) > _ALERT_BODY_BUDGET - more_line_room:
            break
        body += "\n" + line
        included += 1
    deferred = len(new_texts) - included
    if deferred:
        body += f"\n({deferred} more new failure(s) follow in the next hourly alert)"
    if still_open:
        # Already alerted before, so leaving some out loses nothing.
        section = "\n\nStill open:"
        if len(body) + len(section) <= _ALERT_BODY_BUDGET:
            body += section
            for text in still_open:
                line = "\n" + _item(text)
                if len(body) + len(line) > _ALERT_BODY_BUDGET:
                    break
                body += line
    return body, included


def _alert_new_failures(verdict: dict, new_texts: list[str], all_texts: list[str]) -> int:
    """Announce failures never before alerted. Returns how many of
    `new_texts` (a leading run, in order) were in a DELIVERED alert — 0 when
    delivery failed. The caller records only those as sent (round 1
    correction A; round 9 for the count)."""
    still_open = [t for t in all_texts if t not in new_texts]
    body, included = _compose_degraded_body(verdict["summary"], new_texts, still_open)
    try:
        delivered = bool(
            send_alert_confirming_email(subject=f"{LABEL} DEGRADED", body=body, severity="warning")
        )
    except Exception as e:
        # The sender already swallows per-backend failures; this is a last
        # resort so a totally unexpected error here still can't block main().
        print(f"{LABEL}: alert (degraded) failed ({type(e).__name__}: {e})", file=sys.stderr)
        return 0
    return included if delivered else 0


def _alert_recovered(verdict: dict, retired_texts: list[str] | None = None) -> bool:
    """Send the recovery alert. Returns whether it was actually delivered.

    `retired_texts` are earlier-week failures that were announced but can
    never be rechecked; they are listed as such, not claimed resolved."""
    body = f"{LABEL}: back to OK — {_utc_now_iso()}\n{verdict['summary'][:500]}"
    if retired_texts:
        body += "\n\nNo longer checked (earlier week closed; NOT verified resolved):"
        for text in retired_texts:
            line = f"\n- {text[:_ALERT_TEXT_MAX]}"
            if len(body) + len(line) > _ALERT_BODY_BUDGET - 60:
                body += f"\n- ...and {len(retired_texts)} in total"
                break
            body += line
    try:
        return bool(
            send_alert_confirming_email(
                subject=f"{LABEL} recovered",
                body=body,
                severity="info",
            )
        )
    except Exception as e:
        print(f"{LABEL}: alert (recovery) failed ({type(e).__name__}: {e})", file=sys.stderr)
        return False


def _run_locked(state_path: Path, episode_id: str | None) -> int:
    """The read-decide-alert-write body, run under `run()`'s lock.

    An existing state file that can be neither read nor moved aside defers
    the whole transition: no alert, no write, the file left in place, and
    the reason on stderr (the launchd log) every run until it is fixed.
    """
    try:
        return _transition(state_path, episode_id)
    except StateUnavailable as exc:
        print(
            f"{LABEL}: {exc} — no alert sent and state left untouched this run",
            file=sys.stderr,
        )
        return 0


def _transition(state_path: Path, episode_id: str | None) -> int:
    verdict = compute_verdict(episode_id)

    if verdict["kind"] == "unknown":
        # KEEP: no check ran at all, so there's nothing to compare, clear,
        # or alert on. Only write when there is nothing to clobber
        # (first-ever run — this is the ONE case "unknown" is ever
        # persisted), so a reader has something rather than nothing.
        if read_state(state_path) is None:
            _write_state_logged(
                state_path,
                {
                    "status": "unknown",
                    "episode_id": verdict["episode_id"],
                    "summary": verdict["summary"],
                    "failures": [],
                    "checks_ran": [],
                    "checked_at": _utc_now_iso(),
                    "alerted_failures": {},
                    "pending_failures": {},
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
    prev_pending: dict[str, str] = previous.get("pending_failures") or {}

    checked_episode = verdict["episode_id"]

    def _resolved(fid: str) -> bool:
        # Verified gone: this run checked the failure's own episode, its own
        # check ran, and it was not reported. An id whose check did not run
        # (e.g. catalog down) is unverified and stays exactly where it is
        # (round 3); so is an id for an episode this run did not check
        # (round 12).
        return (
            _failure_id_episode(fid) == checked_episode
            and _failure_id_group(fid) in checks_ran
            and fid not in observed_ids
        )

    def _retired(fid: str) -> bool:
        # A failure from an EARLIER week than the one checked: that week has
        # closed and this monitor will never check it again, so it can never
        # be verified gone. It is reported as "no longer checked", never as
        # resolved (round 12). ISO week ids ("YYYY-Www") sort chronologically.
        # A LATER week (a manual --episode run of an older week) is neither
        # resolved nor retired; it is simply left alone.
        return _failure_id_episode(fid) < checked_episode

    # Two sets (round 11 redesign, after rounds 9-11 each found a way for a
    # failure to slip between "seen" and "told"):
    #   alerted — failures a DELIVERED alert has told the reader are open.
    #   pending — failures seen but not yet in a delivered alert (delivery
    #             failed, or they did not fit the body budget).
    # A pending failure that resolves is dropped quietly: nobody was told
    # about it. An alerted failure only clears while nothing is pending, so a
    # recovery can never be announced while an untold failure might still be
    # open, and a resolution is never dropped while something is still owed.
    pending: dict[str, str] = {
        fid: observed.get(fid, text) for fid, text in prev_pending.items() if not _resolved(fid)
    }
    for fid in observed_ids - set(prev_alerted):
        pending[fid] = observed[fid]
    alerted: dict[str, str] = dict(prev_alerted)
    alerted_at = previous.get("alerted_at")

    def _state_doc() -> dict:
        return {
            "status": "degraded" if (observed_ids or alerted or pending) else "ok",
            "episode_id": verdict["episode_id"],
            "summary": verdict["summary"],
            "failures": [observed[i] for i in sorted(observed_ids)],
            "checks_ran": sorted(checks_ran),
            "checked_at": _utc_now_iso(),
            "alerted_failures": dict(alerted),
            "pending_failures": dict(pending),
            "alerted_at": alerted_at,
        }

    # Prove the state can be written BEFORE any alert goes out, by writing
    # what is true right now: nothing new delivered, newly seen failures
    # owed. If that fails, an alert sent now could never be recorded and
    # would repeat every hour, so the run defers like an unreadable state
    # file (gate review on b7065b2).
    try:
        write_state(state_path, _state_doc())
    except Exception as exc:
        raise StateUnavailable(f"state not writable ({type(exc).__name__}: {exc})") from None

    # Alert pending failures this run re-confirmed (an unverified one waits
    # for its check to run again), plus retired ones, which can never be
    # re-confirmed and would otherwise never be told at all.
    to_send = sorted(fid for fid in pending if fid in observed_ids or _retired(fid))
    if to_send:
        new_texts = [
            observed[i] if i in observed_ids else _retired_label(i, pending[i]) for i in to_send
        ]
        all_texts = [observed[i] for i in sorted(observed_ids)]
        shown = _alert_new_failures(verdict, new_texts, all_texts)
        if shown:
            for fid in to_send[:shown]:
                alerted[fid] = pending.pop(fid)
            alerted_at = _utc_now_iso()
        else:
            print(
                f"{LABEL}: DEGRADED alert (new failures) was not delivered on any "
                "channel; left pending for the next run",
                file=sys.stderr,
            )

    if not pending:
        resolved_alerted = {fid for fid in alerted if _resolved(fid)}
        retired_alerted = {fid for fid in alerted if _retired(fid)}
        closable = resolved_alerted | retired_alerted
        if closable and not (set(alerted) - closable):
            # Everything that was announced is now verified gone or belongs
            # to a closed week: a full recovery, which names any retired
            # failures as no longer checked rather than resolved. If its
            # alert isn't delivered, keep the whole set so the same recovery
            # is retried next run.
            retired_texts = [_retired_label(fid, alerted[fid]) for fid in sorted(retired_alerted)]
            if _alert_recovered(verdict, retired_texts):
                alerted = {}
                alerted_at = _utc_now_iso()
            else:
                print(
                    f"{LABEL}: recovery alert was not delivered on any channel; "
                    "left pending for the next run",
                    file=sys.stderr,
                )
        else:
            # A partial recovery (something announced is still open) clears
            # verified-gone failures silently; retired ones are held so the
            # final recovery alert can name them. The LAST one to clear
            # brings the recovery alert.
            for fid in resolved_alerted:
                del alerted[fid]

    # Delivery is AT LEAST ONCE, by choice. An email send and a file write
    # cannot be made atomic, so one of them has to win when the disk fails
    # in between. Recording a failure as alerted BEFORE sending would turn
    # that rare failure into a MISSED alert; recording it after (here) turns
    # it into a repeated one. For an alerting monitor a duplicate is the
    # safe failure. The final write follows a write that succeeded moments
    # ago and is retried once; if both attempts fail, the pre-alert state
    # (failures owed) stays on disk, the next run re-sends, and stderr says
    # exactly that.
    if not _write_state_logged(state_path, _state_doc()):
        if not _write_state_logged(state_path, _state_doc()):
            print(
                f"{LABEL}: this run's outcome could not be recorded; anything it "
                "delivered will be sent again next run (at-least-once delivery)",
                file=sys.stderr,
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


def self_test(state_path: Path) -> int:
    """`--self-test`: can THIS command, in THIS environment, do its job?

    Run by the installer through the job's exact launchd command before it
    changes anything. Reaching this function at all proves the interpreter
    and every import resolve; it then checks the two things a run needs that
    imports don't prove: the email channel is configured (Doppler supplied
    the credentials) and the state directory is writable. No network call,
    no alert, no state written. Unlike the scheduled path, it exits nonzero
    on failure: its whole purpose is to report one.
    """
    problems = []
    email = email_channel_status()
    if not email["configured"]:
        problems.append(f"email channel not configured (missing: {', '.join(email['missing'])})")
    state_dir = state_path.parent
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        problems.append(f"cannot create state directory {state_dir} ({type(exc).__name__}: {exc})")
    else:
        if not os.access(state_dir, os.W_OK):
            problems.append(f"state directory {state_dir} is not writable")
    for problem in problems:
        print(f"{LABEL}: self-test FAILED: {problem}", file=sys.stderr)
    if problems:
        return 1
    print(f"{LABEL}: self-test passed")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test(_default_state_file()))
    sys.exit(_safe_main())
