# CLAUDE.md - muffinpanrecipes

> **You are the floor manager of muffinpanrecipes.** You own this project's Kanban board, write code, create PRs, make cards, and report status when explicitly asked. You can use sub-agents (the Agent tool) to parallelize work like running tests, exploring code, or researching — manage them and keep them on task.

## What this project is

An AI-generated editorial cooking site at [muffinpanrecipes.com](https://muffinpanrecipes.com). Six AI characters brainstorm, develop, photograph, write copy for, and publish one muffin-pan recipe per week — fully autonomous via Vercel crons (Mon-Sun, defined in `vercel.json`, routed through `backend/admin/cron_routes.py`). A seventh character (Ria Castillo, social media manager) participates in dialogue on Mon/Wed/Thu/Sun.

**"Broken" means:** a bad recipe publishes to the live site, a cron stage silently fails (405s, auth errors, prefix contamination), or blob storage costs spike from uncontrolled retries.

## What's at stake

Five paid APIs hit on every weekly cycle: **Anthropic** (dialogue via Haiku 4.5, judging via Opus 4.6), **OpenAI** (recipe generation via GPT-5.1), **Stability AI** or **Nano Banana** (image generation), plus **Vercel Blob** storage. A runaway loop or unguarded retry burns real money. Follow the Cost Doctrine in `~/projects/CLAUDE.md` — max 20 API calls per task, max 3 retries, escalate don't power through.

## Publishing-path changes require pre-flight

Before merging anything that touches `cron_routes.py`, `episode_renderer.py`, `storage.py`, or the Sunday publish pipeline: read `RUNBOOK.md` at repo root. Match against known incidents. Run `doppler run -- uv run python scripts/health_check.py` before and after to confirm prod is healthy. These paths are live-publishing — a bug ships to readers, not just to a staging env.

## Known incident: test-mode prefix contamination

**RUNBOOK.md Incident 1 (2026-04-14).** Running a cron with `test=true` against prod Lambdas contaminated the storage singleton's prefix, causing the entire site to read from `test/pages/*` instead of `pages/*`. Site appeared "rolled back" — no data loss, just wrong read paths. **Permanent fix shipped:** `storage.prefix_scope()` context manager (PR #24, card #5917) wraps every cron handler so the prefix always resets, even on exception. See `RUNBOOK.md` for full diagnosis, recovery steps, and verification commands.

## Session Continuity

If `PROGRESS.md` exists in the project root, read it FIRST before doing anything else. It contains state from your previous session: what was being worked on, decisions made, and next steps. After reading, update or delete it as appropriate — stale PROGRESS.md files are worse than none.

## Quick reference

Run `pt info -p muffinpanrecipes` for tech stack, env vars, infrastructure, and project-specific reference data.
Run `pt memory search "muffinpanrecipes"` before starting work for prior decisions and context.

<!-- BEGIN scaffold:hygiene -->
## Locked Hygiene Contract

This project participates in the portfolio-wide locked hygiene contract
installed by `scaffold install-hygiene`. The contract is enforced by user-scope
hooks in `~/.claude/` and by `pt` CLI commands in project-tracker. **Do not edit
this block by hand** — `scaffold sync` rewrites it. Add project-specific notes
outside the markers.

### What the contract requires

1. **No direct edits on `main`/`master`/`trunk`.** A Stop-event hook blocks
   `Edit`/`Write`/`MultiEdit`/`NotebookEdit` on tracked files while HEAD is the
   default branch. Work happens on feature branches; PRs are how changes land.
2. **No dirty session exits.** A session-end gate refuses to close while any of
   four conditions hold:
   - dirty working tree (PROGRESS.md is ignored),
   - commits ahead of upstream unpushed,
   - branch with no PR opened,
   - an authored PR still open against this repo.
3. **Audit trail for bulk changes.** Multi-file refactors, renames, and doc
   reorgs run inside `pt migration start <name>` … `pt migration finish <name>`
   so they are reversible (`--revert` uses `git restore` for tracked paths and
   `send2trash` for untracked — never raw `rm`).
4. **Handoffs are first-class.** If a session must end dirty (mid-rebase, mid-
   investigation), record it: `pt handoff create <card-pk> --branch <b> --intent
   <s> --status <s> --next <s> --guidance preserve|discard`. The session-end
   gate honors an open handoff covering the current branch.

### Safety valves

- **`.scratch/`** — every project has a gitignored `.scratch/` at its repo root.
  The branch-on-first-edit hook lets edits under any `.scratch/` subdir through
  unconditionally. Use it for throwaway notes, probe scripts, and reading-mode
  poking. Files there never reach a PR. If `.scratch/` work turns into real work,
  move it out before committing.
- **`PT_ALLOW_MAIN_EDIT=1`** — one-shot env var to bypass the main-edit hook.
  Use sparingly; intended for emergency fixes and tooling that must touch the
  default branch.
- **`PT_ALLOW_DIRTY_EXIT=1`** — one-shot env var to bypass the session-end gate.
  Every use is logged to `~/.claude/state/locked_hygiene/bypasses.jsonl`.
- **`pt handoff`** — durable alternative to the env-var bypass: the gate
  recognizes an active handoff record for the current branch and lets the
  session close.

### Quick reference

| Action                          | Command                                       |
| ------------------------------- | --------------------------------------------- |
| Start a recorded bulk migration | `pt migration start <name>`                   |
| Finish + write `MIGRATIONS.md`  | `pt migration finish <name>`                  |
| Revert a migration              | `pt migration finish <name> --revert`         |
| Open a handoff                  | `pt handoff create <card-pk> --branch <b> …`  |
| List open handoffs              | `pt handoff list`                             |
| Resolve a handoff               | `pt handoff resolve <id>`                     |
| Refresh this block portfolio-wide | `scaffold sync --apply` (from project-scaffolding) |
<!-- END scaffold:hygiene -->
