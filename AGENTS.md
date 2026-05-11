# AGENTS.md - Muffin Pan Recipes

> Project-specific rules and workflow for all agents working on this codebase.

---

## Git Workflow (ENFORCED — not a suggestion)

### Branch Protection
- **main is protected.** Direct pushes to main are blocked at the GitHub level.
- **All work must be on a feature branch.** Create a branch, do your work, open a PR.
- **PRs require 1 approving review** before merge. The Judge (Claude Code) reviews against acceptance criteria.
- **Never force-push to main.** Force pushes are blocked.

### Branch Naming
- Use descriptive branch names: `feat/first-recipe-pipeline`, `fix/discord-notification-link`
- Keep branches focused — one feature or fix per branch

### Commit Messages
- Follow conventional commits: `feat:`, `fix:`, `docs:`, `chore:`
- Include task ID if applicable: `feat: add baker agent (#1234)`

---

## Governance Rules

### 1. Trash, Don't Delete
- **NEVER** use `rm`, `unlink`, or `shred`. Permanent deletion is forbidden.
- Use `trash <file>` (CLI) or `send2trash` (Python).

### 2. No Hardcoded Paths
- NO absolute paths (e.g., `/Users/erik/...`).
- Use relative paths or environment variables.

### 3. Secrets Management (Doppler)
- **All secrets are managed via Doppler.** No `.env` files in this project.
- **Run commands with:** `doppler run -- <command>` (e.g., `doppler run -- python main.py`)
- Access secrets in code with `os.getenv("SECRET_NAME")` — Doppler injects them at runtime.
- NEVER hard-code API keys or credentials.
- NEVER read, log, echo, or store secret values in any file, message, or output.
- If a credential is missing, tell Erik — he manages Doppler directly.
- Before any env-dependent pipeline/test run, execute the preflight checklist in `OPENCLAW_PREFLIGHT.md`.

### 4. No Hook Bypass
- NEVER use `--no-verify` with git commit or push.
- Fix the issue, don't bypass the hook.

---

## Project Context

### What This Is
An AI-driven content platform disguised as a recipe website. The recipes are real, but the actual product is **AI personalities working as a creative team**. See `PRD.md` for full details.

### The Creative Team (7 Agents)

| Role | Name | Function | Model |
|------|------|----------|-------|
| Baker | Margaret | Recipe development | Low-cost subagent |
| Creative Director | Steph | Quality gate — approve/reject | Low-cost subagent |
| Art Director | Julian | Image generation & selection | Stability AI (images) + subagent (dialogue) |
| Copywriter | Marcus | Titles, descriptions, voice | Low-cost subagent |
| Site Architect | Devon | HTML/Tailwind, SEO, deploy | Low-cost subagent |
| Social Dispatcher | — | Pinterest, Instagram, TikTok | Low-cost subagent |
| Screenwriter | — | Captures creative tension | Low-cost subagent |

> **"Low-cost subagent"** = Claude Haiku or GPT-mini on laptop, Ollama (Qwen/DeepSeek) on Mac Mini. Run `hostname` to check.

### 7-Stage Pipeline

```
1. Recipe Development (Baker/Margaret)
2. Photography (Art Director/Julian)
3. Copywriting (Copywriter/Marcus)
4. Creative Review (Creative Director/Steph) — can reject, max 3 revision cycles
5. Human Review (Erik) — notified via Discord with review link
6. Deployment (Site Architect/Devon) — static HTML to Vercel
7. Social Distribution — DEFERRED (not in scope for first milestone)
```

### Current Milestone
**First E2E Recipe:** Get one recipe through the full pipeline from generation to published on muffinpanrecipes.com. See `PROJECT_DOD.md` for the complete Definition of Done.

---

## File & Data Rules

- Recipe JSON is persisted to `data/recipes/` immediately upon creation — never exists only in memory
- Recipe states: `pending → approved → published` (or `→ rejected`)
- Rejected recipes are archived with notes, never deleted
- Each agent has a persistent memory file — consult before each task, update after
- NEVER modify `.env` or `venv/`
- NEVER install dependencies globally — use project-local venv or uv
- ALWAYS update `EXTERNAL_RESOURCES.yaml` when adding external services

---

## Hierarchy

### Erik (The Conductor)
- Human-in-the-loop. Final approval on all architecture and direction.
- Reviews recipes via admin dashboard (notified through Discord).

### Claude Code (Super Manager + Judge)
- Writes project specs, DOD, and scenarios.
- Reviews PRs against acceptance criteria before merge.
- Does NOT write implementation code for this project.

### Codex (Super Manager on Mac Mini)
- Owns planning, prioritization, architecture direction, QA gates, and PR strategy.
- **Must delegate implementation coding to low-cost subagents.**
- Reviews worker output, requests revisions, then prepares final PR.

### Other Agents (Workers)
- Implement features, fix bugs, write tests.
- Always work on branches, always submit PRs.
- Follow the DOD outcomes as your north star.

### Mandatory Pre-PR Sync
- Before opening/updating any PR, run: `./scripts/pre_pr_sync.sh`
- This ensures work is rebased on latest `origin/main` and prevents stale PR drift.

---

## Related Documents

- [PRD.md](PRD.md) — Full product requirements
- [PROJECT_DOD.md](PROJECT_DOD.md) — Definition of Done for current milestone
- [README.md](README.md) — Project overview and setup
- [CLAUDE.md](CLAUDE.md) — Claude Code-specific instructions

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
