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

