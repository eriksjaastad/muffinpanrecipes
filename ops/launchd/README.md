# Pipeline monitor launchd agent (#7006)

`scripts/session_pipeline_status.py` only runs when a Claude session opens in
this repo — a Monday failure went undetected until the following Saturday
(#6857) because nobody happened to be looking that week.
`scripts/pipeline_monitor.py` runs the exact same check (imported, not
duplicated) on an hourly launchd schedule instead, so a failure is surfaced
within an hour whether or not anyone opens a session here — the same pattern
`project-tracker`'s dashboard already uses
(`com.eriksjaastad.project-tracker`, `~/Library/LaunchAgents/`).

## Install

```
scripts/install_pipeline_monitor.sh --dry-run   # inspect the rendered plist and commands first
scripts/install_pipeline_monitor.sh             # render, write, and load it
```

This renders `com.eriksjaastad.muffinpan-pipeline-monitor.plist.template`
(placeholders `{{REPO}}`, `{{HOME}}`, `{{UV}}`, `{{DOPPLER}}` — no path in the
template itself is machine-specific) for the current user into
`~/Library/LaunchAgents/com.eriksjaastad.muffinpan-pipeline-monitor.plist`,
then `launchctl bootstrap`s it under `gui/$(id -u)`. The job runs hourly
(`StartInterval` 3600s) plus once at load (`RunAtLoad`), and invokes:

```
doppler run --project muffinpanrecipes --config prd -- uv run python scripts/pipeline_monitor.py
```

`--config prd` matches every other production alerting path in this repo
(`scripts/health_check.py`'s `check_alert_channel`, `RUNBOOK.md`'s recovery
commands) — the monitor needs `RESEND_API_KEY` / `ALERT_EMAIL_TO` /
`MUFFINPAN_DISCORD_WEBHOOK` to actually deliver an alert, and those live in
that Doppler config, not the local `dev` one. If Doppler isn't authenticated
on this machine, the job still runs and still exits 0 — a missing credential
is logged by `backend/utils/alerts.py`, never a crash (see "Never blocks"
below).

## Uninstall

```
scripts/install_pipeline_monitor.sh --uninstall --dry-run   # inspect first
scripts/install_pipeline_monitor.sh --uninstall
```

Unloads the agent (`launchctl bootout`) and moves the installed plist to the
Trash via `trash` — never `rm`, per this repo's hygiene rules.

## Where things live

- **State file** (the verdict the SessionStart hook should read):
  `~/.local/state/muffinpanrecipes/pipeline_status.json`
  (override with `MUFFINPAN_PIPELINE_STATE_FILE` or `--state-file`). JSON:
  ```json
  {
    "status": "ok" | "degraded" | "unknown",
    "episode_id": "2026-W40",
    "summary": "<one-line episode_summary() output>",
    "failures": ["<failure text observed THIS run>", "..."],
    "checks_ran": ["episode"] | ["catalog", "episode"] | [],
    "checked_at": "2026-09-30T18:00:00Z",
    "alerted_failures": {"<failure id>": "<failure text>", "...": "..."},
    "alerted_at": "2026-09-30T18:00:00Z" | null
  }
  ```

  `status` is `"unknown"` **only** the very first time this monitor ever
  runs and the episode fetch fails before any prior state exists on disk —
  every other network hiccup (including every catalog-fetch problem; see
  below) leaves the last known status in place untouched, never
  `"unknown"`. Otherwise `status` is `"degraded"` when this run observed a
  failure OR something from an earlier alert is still outstanding and
  unverified (see `checks_ran` below), and `"ok"` otherwise.

  The monitor tracks failures **per check, individually**, not as one
  whole-pipeline signature. `episode_integrity_failures()` runs several
  checks in one call; all but one (title-collision) only need the episode
  itself (the **"episode" group**, which runs whenever the episode fetch
  succeeds) — the title-collision check additionally needs the separately-
  fetched catalog (the **"catalog" group**, which runs only when that fetch
  returns a usable list or dict). `checks_ran` records which groups actually
  ran THIS cycle; `failures` is only what those checks actually observed.
  A catalog fetch that failed OR came back in an unexpected shape means the
  catalog group simply didn't run — it never blocks the episode group from
  alerting on something new, and it never manufactures a "new" or "cleared"
  failure purely from that flakiness (see "Alerting" below).

  `alerted_failures` is a `{failure id: failure text}` map of every failure
  covered by the last **confirmed** delivery (`send_alert` returned `True`).
  It is deliberately a superset of `failures` when a check didn't run this
  cycle: an id from that check is left exactly as it was (`alerted_failures`
  keeps it, `failures` doesn't currently show it) because it's neither
  confirmed gone nor a repeat worth re-announcing. Comparing `checks_ran`
  against which groups the ids in `alerted_failures` belong to (the id's
  first `\x1f`-separated segment) tells a reader whether something "still
  owed" is currently unverified.

  A lock file sits beside the state file at the same path plus `.lock`
  (e.g. `pipeline_status.json.lock`) — it holds no data, and its only job is
  to serialize concurrent runs (see "Never blocks" below). **Never delete
  it, including after a crash.** Deleting it while a run holds the lock on
  its inode lets a second, concurrent run create and lock a NEW file at the
  same path — the two runs would then hold locks on two different inodes
  and neither would see the other, defeating the whole guard. This is
  never necessary: `fcntl.flock` releases automatically when the holding
  process exits for any reason (normal exit, crash, kill), so a stale
  lock from a dead process is not a real state — the very next invocation
  acquires it immediately. If you ever need to confirm nothing is
  actually holding it, use `lsof <path>.lock` rather than removing the file.

- **Logs**: `~/Library/Logs/muffinpan-pipeline-monitor/{stdout,stderr}.log`.

- **launchd agent**: `~/Library/LaunchAgents/com.eriksjaastad.muffinpan-pipeline-monitor.plist`.

## Alerting

Alerts go through the one door every alert in this repo uses —
`backend/utils/alerts.py::send_alert` — which fans out to Discord and email
(email is the channel Erik actually reads; see that module's docstring).
No new channel was invented for this monitor.

Every observed failure gets a stable id (`<group>\x1f<episode_id>\x1f
<normalized text>`, timestamps stripped). Each run:

- **Any failure id never covered by a confirmed alert before** (a genuinely
  new failure, OR the same failure text on a new episode — a new ISO week
  degrading the same way gets a different id via its episode_id) → one
  `warning` alert listing the new ones (plus the full current list for
  context).
- **Every previously-alerted id clearing at once, with nothing new** → one
  `info` recovery alert.

The monitor stays silent on:

- A previously-alerted id whose check simply **didn't run this cycle**
  (catalog down or malformed) — it's neither cleared (unverified) nor
  re-alerted (already announced once). This is what makes a flickering or
  malformed catalog response inert: a persistent stage failure (A) plus a
  title collision (B), with the catalog fetch failing on the middle of
  three runs, produces exactly ONE alert — not three, and the catalog
  outage never blocks A from being alerted on its own if A is new.
- A failure id that clears while at least one other stays active (a
  **partial** recovery) — the clearing is applied to `alerted_failures`
  silently, no alert, until the LAST one clears too.
- The exact same set of ids repeating, forever — a monitor that repeats
  itself every hour trains you to ignore it.

**Delivery is confirmed, not assumed.** `send_alert`'s boolean return is
checked before `alerted_failures` is updated. If every channel is down (or
credentials are missing):
- a **new-failure** alert that fails to send adds nothing to
  `alerted_failures` — the same ids still look "new" next run and are
  retried (any *unrelated* clearing that happened the same cycle is still
  applied; it's an independent fact, not a claim that was just announced);
- a **recovery** alert that fails to send keeps the **entire** prior
  `alerted_failures` set exactly as it was (undoing even the clearing that
  would have triggered it) — `status` stays `"degraded"` even though
  nothing is currently observed as failing, because the all-clear was never
  actually announced, and the same recovery is retried next run.

## Never blocks

Same discipline as `session_pipeline_status.py`, unchanged, plus one
addition made for concurrency:

- Always exits 0, including on a total exception (`_safe_main()`'s guard).
- Inherits the 6s-per-request network bound from
  `session_pipeline_status.py`'s `_get_json`.
- An episode fetch failure with NO prior state at all writes `"unknown"`
  (the one case it's ever persisted); with a known verdict already on disk
  it changes nothing — no alert, no overwrite, every existing field kept
  exactly as it was. A catalog fetch failure or malformed response never
  produces `"unknown"` at all — it just means the catalog check didn't run
  this cycle (see "Where things live" and "Alerting" above).
- The whole read-decide-alert-write sequence runs under a non-blocking
  exclusive file lock (`fcntl.flock` on the `.lock` file above). If a run
  is already in flight when the next one starts (e.g. a slow run still
  going when the next hourly tick fires), the second run logs that the lock
  is held and exits 0 immediately — it never waits, never double-alerts,
  and never corrupts the state file with a concurrent write. The state
  file itself is replaced atomically via a unique `tempfile.mkstemp` name
  in the same directory, never edited in place.

## SessionStart hook (not part of this repo)

`~/.claude/hooks/muffinpan-pipeline-status.sh` is the SessionStart hook that
currently fetches production directly every time a session opens here. That
hook should be changed to read the state file above instead of doing its own
network fetch — instant, and consistent with whatever the hourly monitor last
saw. That edit lives in `~/.claude/`, a different repo, and is **not** part
of card #7006's in-repo work.
