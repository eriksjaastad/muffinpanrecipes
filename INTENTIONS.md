# INTENTIONS.md - Outcomes First

This document defines desired outcomes, guardrails, and proof points.
It is intentionally **not** a step-by-step TODO list.

## Current Intentions

### 1) Ship one complete E2E recipe
**Outcome:** One recipe moves end-to-end through generation → review → publish on muffinpanrecipes.com.

**Proof:**
- Public URL is live
- Recipe JSON exists in `data/recipes/published/`
- Publish commit is visible in git history

### 2) Make collaboration safe and low-stress
**Outcome:** Codex works proactively without risking `main` or requiring constant supervision.

**Proof:**
- Branch-only workflow for Codex changes
- PR review before merge
- Clear change summaries for each branch

### 3) Shared project visibility across machines
**Outcome:** Erik and Codex can see the same priorities/status from anywhere.

**Proof:**
- Hosted Kanban chosen and active
- At least one workflow link from board cards to PRs/commits

## Guardrails

- No secrets in repo
- No destructive deletes
- No direct pushes to `main` from Codex
- Keep docs concise, current, and non-duplicative

## Working Mode

- Erik defines intentions and constraints
- Codex proposes/implements solutions to satisfy those intentions
- Progress reports focus on outcomes and evidence, not busywork
