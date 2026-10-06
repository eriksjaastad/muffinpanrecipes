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
`emotional_expressiveness` — do not bind. Nothing acts on them:
`build_system_prompt` touches `communication_style` only to pull `signature_phrases`.
Section 4 is the per-character fix, already specified. That work is card #6966 and has
never been started.

**Do not conclude from that the characters are thinly drawn.** `build_system_prompt`
(scripts/simulate_dialogue_week.py) pastes eight per-character blocks, among them a voice
guide and few-shot example messages; 2(c) lists them and measures how much character
material reaches the prompt against the shared rules. The voices blur anyway, which is why
"add more character material" is a hypothesis rather than an obvious fix; few-shot depth
has already been swept and ruled out (#7206).

Read DIALS.md's measurements in DIALS.md and do not restate them here.

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

