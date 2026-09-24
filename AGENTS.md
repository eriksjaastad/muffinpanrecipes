# AGENTS.md - Muffin Pan Recipes

> Project-specific rules and workflow for all agents working on this codebase.

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
- Before attempting interactive login for any third-party CLI, check Doppler for token names only:
  `doppler secrets --project muffinpanrecipes --config dev --only-names | rg -i 'vercel|gh|github|blob|cron|stability|openai|anthropic|google'`.
- Documented project secret names include `BLOB_READ_WRITE_TOKEN`, `CRON_SECRET`, `STABILITY_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `NANOBANANA_API_KEY`, and Discord webhook vars.
- If a needed CLI token such as `VERCEL_TOKEN`, `GH_TOKEN`, or `GITHUB_TOKEN` is missing, tell Erik so he can add it to Doppler. Do not run `vercel login`, `gh auth login`, or provider OAuth first.
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

<!-- BEGIN runtime-doctor:shared:code-review-rules -->
## Code Review Rules

> Shared source: `agent-runtime-config/shared_blocks/code-review-rules.md`, kept
> in sync with its registry-declared authoring surface. Refresh through the
> shared-rule rollout; do not hand-copy rules into individual projects.

These rules apply to independent local code-reviewer sub-agents. This block contains
the essential checks for in-repository review without requiring workstation files.
Additional local detail: [full protocol](https://github.com/eriksjaastad/agent-runtime-config/blob/main/docs/code-review-protocol.md).

### Mechanical checks

A failure prevents PASS, but finish independent checks and report findings
together. Name any check that could not run.

| ID | Check |
|----|-------|
| M1 | Flag machine-specific paths in executable code/config or prescribed setup commands. Illustrative examples and committed evidence are not runtime dependencies. |
| M2 | Flag swallowed unexpected failures. Documented best-effort and expected-absence handling are valid when the contract is preserved. |
| M3 | No real credentials in files. Secrets come from Doppler. Synthetic fixtures and documented placeholders are permitted. |
| M4 | No unresolved placeholders in rendered deliverables or runtime config. Source templates and literal fixtures may contain them. |
| M5 | For changed `.js` under any `static` directory, run from the project root: `npx eslint --no-config-lookup --rule '{"no-redeclare": "error"}' <paths>`. Exit0 passes; skip if none. |

### Judgment and scope

| ID | Check |
|----|-------|
| T1 | Identify the relevant behavior passing tests never exercise. |
| T2 | Assertions such as non-null/type checks alone are insufficient for behavioral claims. |
| E1 | Status contracts must be truthful. JSON deny with exit0 is valid if the caller consumes that protocol. |
| E2 | An operation failure must not silently become a successful empty result. |
| H1 | Subprocesses need timeouts and return-code handling; expected nonzero outcomes must remain usable. |
| H5 | Document foreign-key relationships before a DELETE, including cascade effects. |
| H7 | No unrequested destructive cleanup. |

Trace changed behavior to an authorized requirement. State the scope and check
claimed workflows; a written exclusion does not excuse a defect in behavior the
change promises. Separate unrelated pre-existing concerns from this PR's fixes.
Read propagation sources first, execution-critical code next, then reference docs.

### Evidence and convergence

- Review the whole diff and affected callers. Gather the complete supported
  finding set in one report; group related cases by root cause, most severe first.
- Check both failures and legitimate uses. Use focused synthetic probes where
  they materially validate a claim; do not turn review into an exhaustive audit.
- Separate evidence, inference and unchecked coverage. No supported findings is
  a valid result. Give each finding a concrete failure scenario and file/line.
- Compare base, previous reviewed revision and current head. Distinguish inherited
  misses from fix-induced regressions and verify prior fixes' adjacent effects.
- Test neighbouring legitimate behavior before requesting review. Batch corrections;
  a repeated regression family requires reassessing the approach, not another
  isolated patch. Local preflight also consumes resources and must stay bounded.

### Independent local review

The merge gate is an independent local code-reviewer sub-agent running on the exact
committed HEAD. The reviewer returns PASS or FAIL with the reviewed commit SHA.
A new commit requires fresh review.

Publishing/merging agents follow the complete [PR review and merge policy](https://github.com/eriksjaastad/agent-runtime-config/blob/main/docs/pr-review-policy.md),
also mirrored in `pt info get pr_merge_policy` and `~/projects/Project-workflow.md`.
A local PASS is the required preflight gate. Clearance must identify the current head;
pending, stale, missing or ambiguous evidence is insufficient. An authorized exception
is recorded as an exception, never as PASS.
<!-- END runtime-doctor:shared:code-review-rules -->
