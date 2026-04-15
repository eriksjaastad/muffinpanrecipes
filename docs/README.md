# docs/

Planning, research, and historical documentation for muffinpanrecipes. Moved here 2026-04-14 (#5824) to declutter the repo root.

## What lives at repo root (NOT here)

- `README.md` — repo entry point
- `CLAUDE.md` — floor-manager operating instructions
- `RUNBOOK.md` — incident-response runbook (check this first when prod looks broken)
- `PROGRESS.md` — live session continuity (auto-written before context compaction)
- `DEPLOYMENT.md` — deploy instructions
- `DECISIONS.md` — architectural decision log
- `AGENTS.md`, `PROJECT_DOD.md`, `INTENTIONS.md` — required by `scripts/validate_project.py`

## What lives in `docs/`

### Top-level
- `PRD.md` — product requirements
- `CREATIVE_BIBLE.md` — character bios, voice guides, content rules
- `DIRECTION.md` — current creative direction
- `GEN-LOOP-BLOCKERS.md` — open issues for the generation loop work
- `COMPRESSED_TIMELINE_SPEC.md` — compressed-week test harness spec
- `DIALOGUE_IMPLEMENTATION_PLAN.md` — dialogue generation architecture
- `REVIEWS_AND_GOVERNANCE_PROTOCOL.md` — review process
- `OPENCLAW_PREFLIGHT.md`, `HANDOFF_OPENCLAW.md` — Openclaw runbooks
- `SCENARIOS.md` — (gitignored) local scenario notes

### `research/`
One-off investigations, testing logs, and comparison reports.
- `MODEL_COMPARISON_REPORT.md`
- `ANTI_REPETITION_TEST_RESULTS.md`
- `BOOKEND_TESTING_LOG.md`
- `ai-character-voice-consistency-research.md`
- `agent-to-agent-communication.md`

### `plans/`
Historical implementation plans (kept for audit trail; not currently executing).
- `PLAN_5039_STORAGE_FIX.md`
- `PLAN_KANBAN_PROMPTS_PHASE1.md` through `PHASE3.md`
- `PLAN_NEWLINE_SANITIZATION.md`

### `archive/`
Explicitly retired docs.
- `ERIKS_TODO.md` — replaced by Kanban + `INTENTIONS.md`

## Reference doc
- `ENV_VARS.md` — inventory of every environment variable the codebase reads (forthcoming, #5814)
