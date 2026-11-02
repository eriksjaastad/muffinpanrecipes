# E1 situated-response SHARP review

Date: 2026-11-01
Card: #108610799669506048
Reviewed implementation: scorer frozen at `4d0dbc8`; holdout report at `e3427ad`

## Phase 2 — Dogfood

The scorer was run offline against the frozen E0 evidence for both the
development and holdout splits. Each rendered result matched its committed JSON
byte for byte. The full managed-environment suite passed with 3,230 tests, one
skip, and 57 existing Starlette deprecation warnings. Focused Ruff passed.

The first unconfigured full-suite attempt stopped during collection because
`GOOGLE_CLIENT_ID` was absent. Re-running through the repository's explicit
Doppler project/config supplied the managed test environment and passed. No
provider or model call was made.

## Phase 3 — Adversarial network

- **Skeptic:** The disagreement-enriched nine-pair queue cannot establish
  population accuracy. The report says so and preserves the failed H1.
- **Breaker:** Empty transcript arms return a defined zero rate; exact component
  ties abstain; evidence hash drift fails closed. Hermetic tests exercise the
  reset, answer-cue, alias, lexicographic, and tie paths.
- **Security auditor:** Input is local frozen evidence, output is deterministic
  JSON, and the scorer has no network, credential, shell, or production path.
- **Architect:** The experiment reuses the existing adjacency metric and remains
  outside the production judge. Its only purpose is the card's offline
  calibration slice.
- **User proxy:** The CLI replays from the Mini when given the archived evidence
  path. Both committed receipts reproduced exactly; production prompts and
  models remain untouched.

## Phase 4 — Remediation

- **CRITICAL:** none.
- **MAJOR:** none.
- **MINOR:** `git diff --check` found Markdown hard-break whitespace in the
  preregistration header; replaced it with explicit blank lines. Repository-wide
  Ruff still reports 17 inherited findings outside this branch's changed files;
  focused Ruff on both changed Python files passes.
- **Independent-review remediation:** corrected the stored-judge aggregate from
  2/9 to the table-consistent 5/9; made both frozen source hashes executable
  provenance checks; added structured evidence-category flags without changing
  the frozen comparison; and added an end-to-end hermetic test covering source
  hashes, the sealed split, receipt metadata, and flags.

## Verdict

DONE. The negative experiment is reproducible, hermetic, and honest about its
failed holdout gate. It is ready for independent exact-head review after the
documentation-only remediation commit.
