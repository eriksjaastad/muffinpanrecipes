# PROGRESS.md — Session 2026-04-15/16

## TL;DR
3 PRs merged. Investigated and killed the gen loop (#5108) after discovering production dialogue quality is actually healthy — the HARD FAIL scores that motivated the loop were an artifact of a judge prompt written in-session, not real regression. Shipped numeric judge verdicts (#5120), dependency audit (#5813, zero dead deps — Card Factory scanner was wrong), and fleshed out CLAUDE.md (#5970). Closed 6 cards total. Test suite: **219 passed, 11 skipped, 0 failed** on main.

## PRs Merged

| # | Branch | Scope |
|---|---|---|
| [#35](https://github.com/eriksjaastad/muffinpanrecipes/pull/35) | `feat/5120-numeric-judge-verdicts` | Per-day numeric quality (1-10) + QA (0-100) + rationale in judge output, schema v2 with backward-compat loader |
| [#36](https://github.com/eriksjaastad/muffinpanrecipes/pull/36) | `chore/5813-dep-audit-report` | Added deptry to dev deps, audit report in `docs/research/DEPENDENCY_AUDIT_5813.md`. 13 KEEP, 0 REMOVE, 2 INVESTIGATE (pytest/hypothesis belong in dev not runtime) |
| [#37](https://github.com/eriksjaastad/muffinpanrecipes/pull/37) | `chore/5970-claude-md-audit` | CLAUDE.md expanded from 11 lines to full project context (purpose, stakes, publishing gate, Incident 1 summary). RUNBOOK.md "permanent fix" updated from pending to shipped. |

## Cards Closed
- **#5120** — Done. Numeric per-day judge verdicts with schema v2.
- **#5080** — Done. Dialogue prompt tuning — discovered it already shipped in commit `311c729` on 2026-03-14 but card was never moved.
- **#5813** — Done. Dep audit: zero dead deps. Card Factory scanner was counting transitive tree (78 packages) as "dead." Bug reported to project-tracker floor manager.
- **#5970** — Done. CLAUDE.md audit fix + RUNBOOK update.
- **#5108** — Cancelled. Gen loop not needed: production judge scores W15 PASS, code QA avg ~79/100, editorial QA PASS, human read confirms dialogue is healthy. The HARD FAIL verdicts on W14 were produced by a new judge prompt written in this session, not real regression.
- **#5102** — Cancelled. Image gen is working well, no need for comparison.

## Key Decisions

### Gen loop is dead — production quality is healthy
The entire #5108 arc (shadow-mode gen loop, weekly variant experiments) was cancelled after discovering:
- W15 live judge verdict: **PASS** with warm praise
- W15 programmatic QA: avg **~79/100** across all 7 days
- W15 editorial QA: **PASS**, "polished, well-written, publish as-is"
- W15 Monday dialogue reads fine to a human reader
- The W14 HARD FAIL scores (Thursday 2/10, Saturday 2/10) were produced by re-running the new #5120 judge prompt against an old episode — not real regression

The gen loop was a solution looking for a problem. If `weekly_rollup.avg_quality_score` trends down over 4+ real weeks with no prompt changes, that's the signal to revisit. Until then, it's parked.

### Card Factory scanner produces garbage dep cards
`scripts/card-factory-scan.py` in project-tracker counts the transitive dependency tree (78 packages) against direct imports and calls the delta "dead." For muffinpanrecipes, all 15 direct deps are alive. The jinja2 false positive (used via `fastapi.templating.Jinja2Templates`) would have broken prod if blindly removed. Bug reported to project-tracker floor manager.

### #5080 was a zombie card
Dialogue prompt tuning (anti-agreement, anti-echo, Margaret sharpening, Sat/Sun thickening, Marcus verbosity) all shipped in commit `311c729` on 2026-03-14 but the card was never moved from To Do. Discovered by reading the actual code instead of trusting the card status.

### PR flow: mark card Done on merge, not Review
Erik confirmed: when a PR merges, the card goes straight to Done. The `FORBIDDEN: ./pt tasks done (Conductor only)` line in card prompts is for sub-agent workers, not for the floor manager running the /pr skill. Don't stop at Review.

## Board State (11 open)

**Social media cluster (5 cards):** #5466 (Ria schedule integration — may be stale), #5467 (Instagram), #5468 (Facebook), #5469 (Video), #5470 (TikTok)

**Character development (1 card):** #5471 (Character calendar system — birthdays/vacations)

**Group F orphans (4 cards, all March, no priority):** #5027-#5030 (memory experiments). These were orbiting the cancelled gen loop. Erik hasn't decided whether to cancel them yet.

**CI label fix:** Created `docs` label in the repo. The CI workflow checks for `docs` but the repo only had `documentation`. Fixed during PR #37.

## Important Things Future-Me Should Know

### The judge prompt calibration trap
The #5120 judge prompt includes explicit scale guidance: "A SOFT FAIL should land 50-75, a HARD FAIL below 50, a PASS 75+." When Opus reads this and decides "HARD FAIL," it dutifully picks a number below 50. Running this prompt against old episodes produces harsh-looking scores that reflect the prompt's calibration, not a real quality change. Do NOT use post-hoc judge scores on old episodes as evidence of quality regression. Compare like-for-like: same prompt version, same time period.

### pytest and hypothesis are in runtime deps
`pyproject.toml` has `pytest` and `hypothesis` under `[project.dependencies]` instead of `[project.optional-dependencies].dev`. Flagged INVESTIGATE in the dep audit report (`docs/research/DEPENDENCY_AUDIT_5813.md`). Follow-up PR needed to move them. Low risk but wrong.

### Vercel manual deploy still required
Auto-deploy is OFF. Must use `vercel deploy && vercel deploy --prod` for each PR. No code changes in this session required a Vercel deploy (all backend/docs changes, no frontend-facing changes).

## Next Session

### First thing to do
1. Read this file.
2. Run `doppler run -- uv run python scripts/health_check.py` to confirm prod.
3. Check `pt tasks -p muffinpanrecipes` for board state.

### Biggest open thing
**Social media rollout** — Erik explicitly said this is what he needs to get to. Start by verifying whether #5466 (Ria schedule integration) is already done like #5080 was — Ria already participates in Mon/Wed/Thu/Sun rotations in `simulate_dialogue_week.py`. If so, close it and unblock #5467 (Instagram) and #5468 (Facebook).

### Cleanup candidates
- #5027-#5030: four Group F orphans from March. Memory experiments with no user-facing outcome. Erik hasn't decided yet — ask at session start.
- `pytest`/`hypothesis` runtime→dev move: small follow-up from the dep audit.

## Operational Rules
- Manual Vercel deploy per PR: `vercel deploy && vercel deploy --prod`
- Run `scripts/health_check.py` after each prod promote
- `gha` wrapper for all `gh` write ops (never bare `gh`)
- PR label: use `docs` (not `documentation`) for doc PRs — CI checks for `docs`
- `trash`, never `rm`
- PR commit format: `type(scope): description (#cardid)`
- No failing tests, no Make.com, Anthropic models only, secrets via Doppler
- **PR merged = card Done.** Don't stop at Review.
