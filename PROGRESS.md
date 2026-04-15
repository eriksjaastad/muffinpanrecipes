# PROGRESS.md — Session 2026-04-14/15

## TL;DR
9 PRs merged in one session. Groups A + B + C + D of the hygiene sweep plan complete. Prod is healthy, /this-week loads 20× faster, W15 dialogue cleaned, Ria is on the page, and 40+ env vars are documented. Test suite: **212 passed, 11 skipped, 0 failed** on main.

## The Plan
`~/.claude/plans/cryptic-weaving-meadow.md` — multi-group backlog sweep. Groups A/B/C/D executed today. Groups E (social media rollout) and F (gen loop / #5108) deferred to future sessions.

## PRs Merged Today (in order)

| # | Branch | Scope |
|---|---|---|
| [#24](https://github.com/eriksjaastad/muffinpanrecipes/pull/24) | `fix/5911-safety-net` | A1-A4: `storage.prefix_scope()` ctx mgr, STOP_WORDS single source, Discord pytest gate, pyright None-group cleanup |
| [#25](https://github.com/eriksjaastad/muffinpanrecipes/pull/25) | `feat/5918-health-check` | A5: `scripts/health_check.py` — read-only prod monitor that would've caught #5911 in 60s |
| [#26](https://github.com/eriksjaastad/muffinpanrecipes/pull/26) | `perf/5251-image-compression` | C1: WebP sibling generation + hero `<picture>` tag + backfill script |
| [#27](https://github.com/eriksjaastad/muffinpanrecipes/pull/27) | `fix/5919-cot-leak` | B1: `_guard_cot_leak` helper + regex detector + retry-once-then-fail |
| [#28](https://github.com/eriksjaastad/muffinpanrecipes/pull/28) | `feat/5921-ria-template` | C2: Ria Castillo in CHARACTERS dict, team bar, CSS, AVATAR_STYLES (4 coordinated edits) |
| [#29](https://github.com/eriksjaastad/muffinpanrecipes/pull/29) | `fix/image-deterministic-paths` | C1 follow-up: deterministic blob paths (x-add-random-suffix: 0) + random-suffix stripper in `_to_webp_url` |
| [#30](https://github.com/eriksjaastad/muffinpanrecipes/pull/30) | `fix/catalog-webp-urls` | Make `recipes.json` store WebP URLs so index page gets the 20× win |
| [#31](https://github.com/eriksjaastad/muffinpanrecipes/pull/31) | `chore/5815-unused-imports` | D2: 13 unused imports/locals cleaned via ruff |
| [#32](https://github.com/eriksjaastad/muffinpanrecipes/pull/32) | `chore/5824-docs-reorg` | D3+D4: 21 docs moved to `docs/`, `.kiro/` removed, `docs/README.md` index added |
| [#33](https://github.com/eriksjaastad/muffinpanrecipes/pull/33) | `docs/5814-env-vars` | D6: `docs/ENV_VARS.md` — 40+ env vars cataloged, `pt info env_vars_doc` pointer |

## Cards Closed
- **#5816** — CLOSED (verified, no-op). Both test files collect cleanly; fixed 2026-04-09 in commit 7617ed6.
- **#5917, #5915, #5923, #5914** — Group A safety net
- **#5918** — Health check
- **#5251** — Image compression (shipped across 3 PRs)
- **#5919, #5920** — CoT leak (single combined fix)
- **#5921** — Ria page hookup
- **#5815** — Unused imports
- **#5824, #5825** — Docs reorg + .kiro removal
- **#5814** — Env var inventory

## Cards Still Deferred
- **#5813** — Dependency audit. Card claimed "50 dead deps" but direct deps are only 15; the number likely refers to transitive tree. No `deptry` installed. Card annotated with deferral notes. Revisit after installing `deptry` as a dev dep.
- **#5080** — Dialogue prompt tuning (Group B2). Prompt-only changes in `simulate_dialogue_week.py` — anti-agreement, anti-echo, life texture, Margaret sharpening, Sat/Sun thickening, Marcus verbosity. Own branch, own session.

## Production Interventions (not in PRs)
One-off fixes applied directly to prod via Doppler, not commits:

1. **W15 dialogue cleaned**: Steph's monday[1] and tuesday[1] contained leaked Haiku chain-of-thought. Replaced in-place with the clean dialogue portions, saved via `storage.save_episode('2026-W15', ep)` and `regenerate_and_upload(ep)`.
2. **W16 teaser restored**: The W15 regenerate above overwrote `pages/latest.json` with W15 data, regressing `/api/episodes/teaser`. Re-ran `regenerate_and_upload(ep)` on W16 to restore.
3. **WebP backfill (initial + force)**: First run uploaded 33 WebPs at random-suffixed paths (unusable). PR #29 added deterministic headers; re-ran with `--force` to replace. Final state: 33 WebPs at deterministic URLs, all HTTP 200.
4. **recipes.json data rewrite**: In-place rewrite of the 6 cron-generated recipe catalog entries to point `image` at `.webp` URLs. PR #30 makes future cron runs do this automatically.
5. **Historical recipe pages regenerated**: Ran `render_episode_page` + `storage.save_page` for all 6 episodes W10-W15 so existing standalone pages get the `<picture>` tag.

## Key numbers
- **Hero image:** 3.8 MB PNG → 174 KB WebP (95% reduction)
- **Index page weight:** 22 MB → 1.1 MB (20× reduction)
- **Historical WebPs shipped:** 33 images across 6 episodes
- **Test suite:** 193 → 212 passed (+19 new tests across 4 new suites)
- **Board:** 29 → 24 open tasks

## Important Things Future-Me Should Know

### Storage prefix contamination is fixed structurally
`storage.prefix_scope()` is a context manager. Every cron handler wraps its body in `with _test_mode_scope(body):`. Even on exception, the prefix resets. RUNBOOK Incident 1 is closed. See `tests/test_cron_test_mode_isolation.py`.

### The cron_routes.py indentation oddity
The seven cron handlers now use `with _test_mode_scope(body):` with 6-space inner indent (not PEP 8's 4) wrapping an inner `with _run_stage(...):` at 8-space. Deliberate choice to avoid re-indenting ~500 lines during the Group A PR. It parses, it works, it's ugly but intentional. Don't "fix" it unless doing a dedicated reformat PR.

### Vercel Blob random suffixes
Vercel Blob appends a random 26-char hash to every PUT unless you send `x-add-random-suffix: 0` + `x-allow-overwrite: 1`. This caught us twice. All image uploads now use deterministic paths. Episode uploads have used deterministic headers since #5048.

### Historical PNG URLs in episode JSON
Existing episodes still have random-suffixed PNG URLs stored in `image_urls`. `_to_webp_url` in `episode_renderer.py` has a regex (`_VERCEL_RANDOM_SUFFIX_RE`) that strips them before appending `.webp`. This is a permanent shim — do NOT remove until every episode is re-written with deterministic URLs.

### Dialogue CoT leak guard
`scripts/simulate_dialogue_week.py::_guard_cot_leak` runs after every `generate_response` in `generate_turn`. Detects first-line interiority patterns ("What X actually feels:", "How X thinks:"), retries once with a stricter prompt, raises `RuntimeError` on second leak (caught by `_run_stage`). Pattern in `_COT_LEAK_RE`.

### Env var inventory
`docs/ENV_VARS.md` is the single source of truth for every `os.environ.get` / `os.getenv`. When you add a new env var, append a row. Path registered in `pt info -p muffinpanrecipes env_vars_doc`.

### docs/ reorganization
Repo root now has **9 markdown files**: 6 ops-critical (README, CLAUDE, PROGRESS, RUNBOOK, DEPLOYMENT, DECISIONS) + 3 required by `scripts/validate_project.py` (AGENTS, PROJECT_DOD, INTENTIONS). Everything else under `docs/` in subdirs (`research/`, `plans/`, `archive/`). See `docs/README.md`.

### pre_review_scan.sh / validate_project.py
`scripts/validate_project.py muffinpanrecipes` fails with 3 pre-existing errors (missing `00_Index_*.md`, missing `Documents/` dir, `.env.local` secret). These existed before today. I kept AGENTS/PROJECT_DOD/INTENTIONS at root to avoid ADDING new failures but didn't fix the 3 — separate concern.

## Next Session

### First thing to do
1. Read this file.
2. Run `doppler run -- uv run python scripts/health_check.py` to confirm prod.
3. Check `pt tasks -p muffinpanrecipes` for any new cards.

### Biggest open thing
**Group F — the gen loop architecture (#5108).** Erik explicitly flagged this on 2026-04-14 as "the gen loop thing I came to talk about." Architectural keystone, needs fresh context. Start with a `/discover` session on #5108. Prerequisite: **#5120** (numeric judge verdicts, 1-10 per day) — without numeric eval scores, an optimization loop has no signal to chase.

Group F cards:
- #5027 Per-character conversation history
- #5028 Standout moment detection
- #5029 Degrading memory tiers
- #5030 First Monday origin story
- #5471 Character calendar system
- #5120 Numeric judge verdicts (prereq for #5108)
- #5108 Autoresearch dialogue optimization loop ← the main one
- #5102 Stability vs Nano Banana image comparison

### After Group F, still on deck
- Group E: social media rollout (#5466-#5470) — chained, needs product calls from Erik
- #5080 dialogue prompt tuning (prompt-only)
- #5813 dep audit (after installing `deptry`)
- 3 pre-existing `validate_project.py` failures

## Operational Rules
- Manual Vercel deploy per PR: `vercel deploy && vercel deploy --prod`
- Run `scripts/health_check.py` after each prod promote
- `gha` wrapper for all `gh` write ops (never bare `gh`)
- PRs need a label at create time. CI whitelist accepts `docs` but the repo only has `documentation` — use `chore` for docs PRs to avoid the label mismatch
- `trash`, never `rm`
- PR commit format: `type(scope): description (#cardid)`
- No failing tests, no Make.com, Anthropic models only, secrets via Doppler
