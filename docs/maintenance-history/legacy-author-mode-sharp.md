# Legacy author-mode retirement SHARP review

Date: 2026-11-02
Card: #104790093907259392
Reviewed implementation: `12dc9244fcbfb5deaf46ca69aedbd2656fea6092`
Base: `61d86a4ffeb04156249a6bb55ec7c7d2134ea8bb`

## Phase 2 — Dogfood

The managed-environment suite passed with 2,957 tests, one audited skip, and
57 existing Starlette deprecation warnings. The first unconfigured suite run
stopped during collection because `GOOGLE_CLIENT_ID` was absent; the documented
Doppler environment supplied test configuration and the complete suite passed.
No provider or model call was made.

Focused Ruff passed across every changed Python file. Repository-wide Ruff
still reports ten inherited findings in unchanged files. `git diff --check`
passed.

The branch removes 7,407 lines and adds 738, a net reduction of 6,669 lines.
An acceptance scan found zero remaining repository hits for all ten exact
card-named retired identifiers.

## Phase 3 — Adversarial network

- **Skeptic:** A green hermetic suite does not satisfy the card's separate
  seven-day cron-entry-point run. That acceptance gate remains open and must be
  recorded before deploy.
- **Breaker:** The full suite covers authored scene generation, cron dialogue
  routing, sanitization, durable memories, judge filtering, lab comparisons,
  and OpenRouter request contracts after the legacy modules and fixtures are
  removed.
- **Security auditor:** This milestone made no provider calls, did not read or
  print secret values, and did not touch production state.
- **Architect:** Production now has one authored-dialogue path. The change
  deletes the per-speaker director, stop checker, ignored controls, dead admin
  surface, and tests that asserted the retired architecture instead of adding
  a compatibility layer.
- **User proxy:** Personas, biographies, memories, highlights, retry notes,
  Wednesday images, and the Sunday quality gate remain represented in the
  passing suite. Historical experiment provenance remains in canonical docs
  while executable retired controls are gone.

## Phase 4 — Remediation

- **CRITICAL:** none found.
- **MAJOR:** The required local seven-day cron-entry-point proof is not yet
  complete. Do not deploy until it passes and the rollback command is recorded
  on the authoritative card.
- **MINOR:** Repository-wide Ruff has ten inherited findings outside this
  branch's changed files. Focused Ruff is green.

## Verdict

PASS FOR REVIEW. The bounded retirement is substantially smaller, the named
legacy controls have zero scoped hits, and the full hermetic suite passes. The
seven-day local run remains a deployment gate, not a reason to weaken this
review milestone.
