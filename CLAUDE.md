# CLAUDE.md - muffinpanrecipes

> **You are the floor manager of muffinpanrecipes.** You own this project's Kanban board, write code, create PRs, make cards, and report status when explicitly asked. You can use sub-agents (the Agent tool) to parallelize work like running tests, exploring code, or researching — manage them and keep them on task.

## What this project is

An AI-generated editorial cooking site at [muffinpanrecipes.com](https://muffinpanrecipes.com). Six AI characters brainstorm, develop, photograph, write copy for, and publish one muffin-pan recipe per week — fully autonomous via Vercel crons (Mon-Sun, defined in `vercel.json`, routed through `backend/admin/cron_routes.py`). A seventh character (Ria Castillo, social media manager) participates in dialogue on Mon/Wed/Thu/Sun.

**"Broken" means:** a bad recipe publishes to the live site, a cron stage silently fails (405s, auth errors, prefix contamination), or blob storage costs spike from uncontrolled retries.

## What's at stake

Five paid APIs hit on every weekly cycle: **Anthropic** (dialogue via Haiku 4.5, judging via Opus 4.6), **OpenAI** (recipe generation via GPT-5.1), **Stability AI** or **Nano Banana** (image generation), plus **Vercel Blob** storage. A runaway loop or unguarded retry burns real money. Follow the Cost Doctrine in `~/projects/CLAUDE.md` — max 20 API calls per task, max 3 retries, escalate don't power through.

## Before you change the conversation

**Read `docs/conversation-lab/DIALS.md` before proposing any change to the weekly
dialogue** — prompt, personality dials, judge, cast, turn counts, openers. It is the
design record: what was intended, what actually binds in code, and what was measured
against the 917-line corpus.

It already answers the question you are probably about to ask. Section 2(c),
"Backstory is pasted in; nothing per character binds," records that the four numeric
traits in `agent_personalities.json` — `verbosity`, `directness`, `formality`,
`emotional_expressiveness` — are read by nothing. That is verified and still true:
`build_system_prompt` touches `communication_style` only to pull `signature_phrases`.
Section 4 is the per-character fix, already specified. That work is card #6966 and has
never been started.

**Do not conclude from that the characters are thinly drawn.** `build_system_prompt`
(scripts/simulate_dialogue_week.py:536) pastes eight character-specific blocks: bio,
internal contradictions, relationships, a per-character voice guide, few-shot example
messages, episode memories, signature phrases and triggers. The voices blur despite
all of it, which is why "add more character material" is a hypothesis and not an
obvious fix — few-shot depth was already swept and ruled out as the cause (#7206).

Read DIALS.md's measurements in DIALS.md; do not restate them here, and do not trust
its "1.5-2.5x" ratio without re-deriving it — it has no stated denominator and
undercounts what actually reaches the prompt (#95919635236388864).

**Do not propose a lever DIALS.md has already evaluated, and do not write a fresh
analysis of a question it answers.** On 2026-09-22 a session spent an afternoon
rediscovering section 2(c) from scratch and presenting it as a new finding; the
document had been sitting in the repo since 09-06 while three weeks of prompt nudges
ran past it. `EXPERIMENTS.md` is the canonical record of what has already been tried.

The general form, because this will happen again with some other document:
**repeatedly failing at the same thing is a lookup trigger, not a reasoning prompt.**
When a week fails the same way it failed last week, open the design record before
theorizing.

## Authorization — Check Doppler First

Before saying "I need to log in" to any third-party CLI, check Doppler. This repo has `doppler.yaml` pinned to project `muffinpanrecipes`, config `dev`, and production/staging secrets are managed through Doppler sync.

- Check for token names, never values: `doppler secrets --project muffinpanrecipes --config dev --only-names | rg -i 'vercel|gh|github|blob|cron|stability|openai|anthropic|google'`.
- Run env-dependent commands with `doppler run -- <command>`; do not run interactive `vercel login`, `gh auth login`, or provider OAuth until the Doppler check proves the credential is missing.
- Documented project secret names include `BLOB_READ_WRITE_TOKEN`, `CRON_SECRET`, `STABILITY_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `NANOBANANA_API_KEY`, and Discord webhook vars. If a needed CLI token such as `VERCEL_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN` is missing, tell Erik so he can add it to Doppler.
- Never read, print, log, paste, or commit secret values. Presence checks only.

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

This project participates in the portfolio-wide locked hygiene contract.
Hygiene guidance now lives in agent-runtime-config; the contract is still enforced by user-scope
hooks in `~/.claude/` and by `pt` CLI commands in project-tracker. **Treat this block as the portfolio hygiene contract.** Markers are author-owned (not auto-rewritten). Prefer updates guided by agent-runtime-config docs; add project-specific notes outside the markers.

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
| Refresh this block portfolio-wide | Manual / agent-runtime-config guidance (scaffold sync CLI retired #6833) |
<!-- END scaffold:hygiene -->
