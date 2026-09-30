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
    "summary": "<one-line episode_summary() output>",
    "failures": ["<failure detail>", "..."],
    "checked_at": "2026-09-30T18:00:00Z"
  }
  ```
  `status` is only ever `"ok"` or `"degraded"` on disk — a network blip
  never gets written as a fresh verdict over a known one (see below), so a
  reader of this file never has to special-case a third value.

- **Logs**: `~/Library/Logs/muffinpan-pipeline-monitor/{stdout,stderr}.log`.

- **launchd agent**: `~/Library/LaunchAgents/com.eriksjaastad.muffinpan-pipeline-monitor.plist`.

## Alerting

Alerts go through the one door every alert in this repo uses —
`backend/utils/alerts.py::send_alert` — which fans out to Discord and email
(email is the channel Erik actually reads; see that module's docstring).
No new channel was invented for this monitor.

The monitor alerts only on a **transition**:

- `OK`/`unknown` → `DEGRADED`: one `warning` alert, with the failure list.
- `DEGRADED` → `OK`: one `info` recovery alert.
- `DEGRADED` → `DEGRADED` (persistent failure) or `OK` → `OK`: silent — a
  monitor that repeats itself every hour trains you to ignore it.

## Never blocks

Same discipline as `session_pipeline_status.py`, unchanged:

- Always exits 0, including on a total exception (`_safe_main()`'s guard).
- Inherits the 6s-per-request network bound from
  `session_pipeline_status.py`'s `_get_json`.
- A network failure records `"unknown"` and does **not** alert, and does
  **not** overwrite an already-known (`ok`/`degraded`) verdict on disk — a
  flaky connection can't erase history or spam every hour.

## SessionStart hook (not part of this repo)

`~/.claude/hooks/muffinpan-pipeline-status.sh` is the SessionStart hook that
currently fetches production directly every time a session opens here. That
hook should be changed to read the state file above instead of doing its own
network fetch — instant, and consistent with whatever the hourly monitor last
saw. That edit lives in `~/.claude/`, a different repo, and is **not** part
of card #7006's in-repo work.
