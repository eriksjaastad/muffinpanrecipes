# AGENTS.md - Muffin Pan Recipes

> Project-specific rules and workflow for all agents working on this codebase.

---

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

**A dialogue change ships with a measured pass rate.** Before merging any change
to a day's conversation, run `scripts/conversation_lab.py bench --from-episode <week>
--stage <day>` for that day on the six most recent weeks, once on main and once on the
branch. Put both judge pass rates in the PR body, and don't ship a change that doesn't
raise the rate. This covers changes to what the generator is asked or how the day is
shaped (prompt, dials, turn counts, judge); a deterministic guard that discards a
malformed reply and keeps the existing draft does not need a bench. A day that fails in
production gets the same bench before anyone theorizes about it.

**Apply a fix to every day built the same way.** #8285 gave Tuesday more turns
after 5–6 turns proved too few for four speakers to argue and settle anything.
Wednesday kept the same shape and failed W42 for the same reason (#8293).

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
An AI-driven content platform disguised as a recipe website. The recipes are real, but the actual product is **AI personalities working as a creative team**. See `docs/PRD.md` for full details.

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
**First E2E Recipe:** Get one recipe through the full pipeline from generation to published on muffinpanrecipes.com.

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

- [docs/PRD.md](docs/PRD.md) — Full product requirements
- [README.md](README.md) — Project overview and setup


<!-- BEGIN runtime-doctor:shared:code-review-rules -->
## Code Review Rules

> Shared source: `agent-runtime-config/shared_blocks/code-review-rules.md`, kept
> in sync with its registry-declared authoring surface. Refresh through the
> shared-rule rollout; do not hand-copy rules into individual projects.

These rules apply to an independent local Codex review process.
Claude implements authorized coding cards; Codex reviews the exact committed
HEAD. This block contains the essential
checks for in-repository review without requiring workstation files. Additional
local detail: [full protocol](https://github.com/eriksjaastad/agent-runtime-config/blob/main/docs/code-review-protocol.md).

### Better code

**Main standard:** Google's code review guide, "What to look for":
https://google.github.io/eng-practices/review/reviewer/looking-for.html
- Design: the pieces fit together, and the change belongs here and now.
- Functionality: it does what was meant, is good for its users, and has no bugs you can see by reading.
- Complexity: readable quickly; nothing built for a future that hasn't arrived.
- Tests: right kind of tests, correct, and they fail when the code breaks.
- Naming: names say clearly what a thing is or does.
- Comments: explain why, not what; if the code needs a "what", simplify the code.
- Style / Consistency: follow the style guide; otherwise match the code around it.
- Documentation: update docs when how people build, run or use the code changes.
- Every line, Context: read every line, and check the change makes the whole system healthier.
Python style is PEP 8, enforced by ruff.

**Before you code:** State the problem in one sentence and the goal: how we'll know it's solved.
Is the change needed? What's the smallest, simplest version that works? What could break? Build that
and test what could break. When a test, a review or a new fact contradicts your problem statement,
restate the problem before you patch again.

**Our rules on top** (agent-specific; a finding names the one it breaks, e.g. `Breaks: 6 Tested`):
0. **One purpose.** One purpose per PR. Bundle the cards that serve that one purpose; split unrelated work.
   Refactors and moves go in their own PR.
1. **Reuse.** Use what exists: the standard library, the standard tool, or the rule the code already
   has. Never add a second copy of a rule. No repeated reads or calls inside loops.
2. **Nothing extra.** No mechanism nobody asked for: undo files, retries, caches, fallbacks, options.
3. **Split only to simplify.** Say in one sentence what the function does; split only where that
   sentence needs "and". Never split to hit a size or count. Line, argument and file counts are
   signals, not rules.
4. **Honest.** No swallowed errors, no "success" after a failure, no secrets in output or logs.
5. **Docs in the code.** Docstrings, `--help`, comments for why. No separate doc that can drift.
6. **Tested, no more.** Test only the new paths this change adds, one test each, not one per
   variation. Where existing tests already cover a path, point to them instead. Each new test must
   fail when its bug is put back: check that once. A test that can't fail for a real reason is cost.
7. **Smaller.** Delete before adding.

**Before you commit:**
- Read your diff against the guide and this list. If any point is bent, fix the code; don't just
  note it. Keep a bend only with a reason a reviewer would accept, stated in the PR body.
- If test lines added are more than about 2x the app lines changed, cut back to one test per new path.
- If the card's checklist conflicts with this section, this section wins. Note the conflict in the PR body.

**Reviewers:** mark each finding blocking (it makes the code worse) or nit. Mechanical checks (M1–M5)
always block. For judgment findings, approve once the change clearly makes the code better; list the
rest as nits.

### Mechanical checks

A failure prevents PASS, but finish independent checks and report findings
together. Name any check that could not run.

| ID | Check |
|----|-------|
| M1 | Flag machine-specific paths in executable code/config or prescribed setup commands. Illustrative examples and committed evidence are not runtime dependencies. |
| M2 | Flag swallowed unexpected failures. Documented best-effort and expected-absence handling are valid when the contract is preserved. |
| M3 | No real credentials in files. Secrets come from Doppler. Synthetic fixtures and documented placeholders are permitted. |
| M4 | No unresolved placeholders in rendered deliverables or runtime config. Source templates and literal fixtures may contain them. |
| M5 | Changed `.js` files beneath any `static` directory must have no redeclarations. The manager's review launcher checks them on the exact HEAD and supplies the result to the independent reviewer. A missing or failed check prevents PASS; no matching files skips M5. The check's command and evidence format are owned by the review launcher and full protocol, not by this portable rule. |

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

Run a separate local Codex reviewer process on the exact committed HEAD.
The reviewer must not be the implementing agent or process.
Record the reviewed SHA, verdict and findings in the PR or task notes. Address
findings, test the affected behavior and request a fresh review for each new
commit. Repeated findings call for reassessing the approach and tests.

### Verdict and publication

Independent review verdicts end PASS or FAIL with the exact reviewed commit SHA;
a new commit requires fresh review. Review itself needs no workstation-tool access.

Publishing/merging agents follow the complete [PR review and merge policy](https://github.com/eriksjaastad/agent-runtime-config/blob/main/docs/pr-review-policy.md),
also mirrored in `pt info get pr_merge_policy` and `~/projects/Project-workflow.md`.
Independent local code-reviewer clearance must identify the current head and
clear findings; pending, stale, missing or ambiguous evidence is insufficient.
If that policy is unavailable, stop publication/merging, not review. An authorized
exception is recorded as an exception, never as PASS.
<!-- END runtime-doctor:shared:code-review-rules -->

<!-- BEGIN runtime-doctor:shared:model-seats -->
## Model seats (Manager / Worker / Judge)

Concrete bindings live in **`~/projects/MODEL_SEATS.md`** (update when vendors bounce). Full policy: **`~/projects/ORCHESTRATOR_CHEAP_CODER_RULES.md`**.

- **Manager** (Codex / Claude / Grok Bot): plan, brief, verify, review, and handle PRs.
- **Worker** (Claude Code): implement authorized code in an isolated task branch or worktree and run focused tests.
- **Judge** (separate local Codex CLI process): review the exact committed HEAD under the ChatGPT login.

### Floor-manager default on the MacBook

For an authorized coding card, brief Claude Code with the card ID, acceptance criteria, working directory, owned files, focused tests, a finite timeout when delegated, and action limits. Keep implementation in an isolated task branch or worktree. A Claude manager may implement directly. When Erik directs Codex manager implementation, or Claude is unavailable or unsuitable, record the reason on the card.

Inspect the coder's diff, tests, and handoff before accepting its work. Invoke a separate local Codex reviewer process on the exact committed HEAD and record its full-SHA PASS or FAIL verdict. The implementing process cannot review its own work. A missing, stale, timed-out, or inconclusive review blocks publication; repeat review after a new commit. The floor manager owns integration, the PR, CI, and merge. The coder's response alone does not complete a card.

Do not use DeepSeek as the coding Worker on the MacBook or Mini. The linked cutover cards own launcher and credential retirement; preserve their dependency gates. Current binding and rollback: `~/projects/MODEL_SEATS.md`.
<!-- END runtime-doctor:shared:model-seats -->
