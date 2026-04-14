# PROGRESS.md — Session 2026-04-14/15

## What's Happening
Executing the backlog sweep plan (`~/.claude/plans/cryptic-weaving-meadow.md`).
Branch: `fix/5911-safety-net`. Group A (incident closeout) in progress.

## What Got Done This Session
- **A1 (#5917) — Storage prefix context manager.** Added `storage.prefix_scope()` context manager on both backends in `backend/storage.py`. Replaced `_configure_test_mode(body)` with `_test_mode_scope(body)` context manager in `cron_routes.py`. All 7 cron handlers now wrap their body in `with _test_mode_scope(body):` — prefix reset guaranteed on exception. New tests in `tests/test_cron_test_mode_isolation.py` (8 cases, all pass).
- **A2 (#5915) — STOP_WORDS single source.** Deleted `_STOP_WORDS` from `scripts/pick_concept.py`, now imports `STOP_WORDS` from `backend.utils.title_validator`. Updated sync comment in title_validator.py.
- **A3 (#5923) — Discord pytest gate.** Added `_pytest_gate()` helper + early-return in `notify_recipe_ready`, `notify_pipeline_failure`, `notify_judge_failure`, `notify_batch_complete`. New tests in `tests/test_discord_notification_gating.py` (5 cases, all pass).
- **A4 (#5914) — Pyright None-group cleanup.** Rewrote 4 `re.search().group()` calls in `backend/utils/recipe_prompts.py` (lines 154, 159, 164, 370) to `match = re.search(); if match:` pattern. Pyright diagnostics clear.
- **Test suite:** 193 passed, 11 skipped, 0 failed.

## Restored Today
- **PROGRESS.md** — Was deleted in working tree (never committed). Restored from HEAD. Last meaningful update: 2026-04-09. Hasn't reflected 2026-04-13 / 2026-04-14 work. Root cause: `pre-compact-progress.py` hook only fires on `PreCompact`, and 2026-04-13/14 sessions never hit compaction.

## Next Steps (immediate)
- Commit Group A1-A4 on `fix/5911-safety-net`
- `gh pr create` → review → merge → `vercel deploy && vercel deploy --prod`
- Start A5: `scripts/health_check.py` (#5918)
- Then C1: image compression (#5251)
- Then B1: dialogue CoT leak (#5919/#5920)
- Then C2: Ria template hookup (#5921)

## Cards Still Pending After Group A
- Group B: #5919 + #5920 (CoT leak), #5080 (dialogue tuning)
- Group C: #5251 image compression, #5921 Ria hookup
- Group D: hygiene sweep (#5815, #5816, #5824, #5825, #5813, #5814)
- Group E: social media rollout (#5466, #5467, #5468, #5469, #5470)
- Group F: gen loop / memory architecture (#5027, #5028, #5029, #5030, #5471, #5120, #5108, #5102)

## Don't Forget
- Inner `with _test_mode_scope(body):` blocks in cron_routes.py use **6-space inner indent** (not PEP 8 4-space) because the wrapping was added to avoid re-indenting ~500 lines of existing handler code. Valid Python, parses clean, but visually unusual. Leave alone unless doing a dedicated reformat PR.
- `_pytest_gate()` + `prefix_scope` both rely on env/state discipline — a future session that imports backend.utils.discord outside pytest should not be affected, but if you see Discord alerts stop firing in local dev, check whether PYTEST_CURRENT_TEST is leaking in.
- The plan at `~/.claude/plans/cryptic-weaving-meadow.md` is the source of truth for execution order.
