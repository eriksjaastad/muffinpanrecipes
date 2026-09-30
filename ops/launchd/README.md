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
    "status": "ok" | "degraded",
    "episode_id": "2026-W40",
    "summary": "<one-line episode_summary() output>",
    "failures": ["<failure detail>", "..."],
    "checked_at": "2026-09-30T18:00:00Z",
    "alerted": {
      "status": "ok" | "degraded" | null,
      "signature": "<sha256 of episode_id + normalized failures>" | null,
      "at": "2026-09-30T18:00:00Z" | null
    }
  }
  ```
  `status` is only ever `"ok"` or `"degraded"` on disk — a network blip
  never gets written as a fresh verdict over a known one (see below), so a
  reader of this file never has to special-case a third value.

  `status` and `alerted` are deliberately separate. `status` is what THIS
  run observed; `alerted` is the status + failure signature of the last
  **confirmed** delivery (`send_alert` returned `True`). They can disagree —
  a DEGRADED run whose alert failed on every channel writes
  `status: "degraded"` but leaves `alerted` at its previous value, so the
  next run sees "still owed" and retries the delivery instead of treating
  the failure as already announced. A reader that only wants "what does the
  monitor currently believe about the pipeline" should read `status`; a
  reader that wants "has the current problem actually been announced yet"
  should compare `status`/failure-signature against `alerted`.

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

The monitor alerts on:

- `OK`/`unknown` → `DEGRADED` (including the very first run ever): one
  `warning` alert, with the failure list.
- A **new failure signature** while still `DEGRADED` — a different failure,
  or the same failure text on a new episode (a new ISO week degrading the
  same way): one `warning` alert. The signature is a hash of the episode id
  plus the sorted failure text with any embedded timestamp stripped first,
  so an identical failure never looks "new" just because an hour passed.
- `DEGRADED` → `OK`: one `info` recovery alert.

The monitor stays silent on:

- `DEGRADED` → `DEGRADED` with the **same** failure signature (persistent,
  unchanged failure) — a monitor that repeats itself every hour trains you
  to ignore it.
- `OK` → `OK`.

**Delivery is confirmed, not assumed.** `send_alert`'s boolean return is
checked before `alerted` is updated. If every channel is down (or
credentials are missing), the alert attempt is logged to stderr and
`alerted` is left exactly as it was — the next run (an hour later, or
sooner via a manual invocation) sees the same "not yet delivered" state and
tries again, rather than the failure silently being recorded as announced.

## Never blocks

Same discipline as `session_pipeline_status.py`, unchanged, plus one
addition made for concurrency:

- Always exits 0, including on a total exception (`_safe_main()`'s guard).
- Inherits the 6s-per-request network bound from
  `session_pipeline_status.py`'s `_get_json`.
- A network failure records `"unknown"` and does **not** alert, and does
  **not** overwrite an already-known (`ok`/`degraded`) verdict on disk — a
  flaky connection can't erase history or spam every hour.
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
