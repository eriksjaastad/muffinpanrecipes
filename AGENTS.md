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
| Baker | Margaret | Recipe development | Ollama (llama3.2) |
| Creative Director | Steph | Quality gate — approve/reject | DeepSeek-R1 |
| Art Director | Julian | Image generation & selection | GPT-4o |
| Copywriter | Marcus | Titles, descriptions, voice | Claude |
| Site Architect | Devon | HTML/Tailwind, SEO, deploy | Qwen |
| Social Dispatcher | — | Pinterest, Instagram, TikTok | Gemini Flash |
| Screenwriter | — | Captures creative tension | Claude |

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

### Codex (Super Manager)
- Owns planning, prioritization, architecture direction, QA gates, and PR strategy.
- **Must delegate implementation coding to worker coding agents/models.**
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
