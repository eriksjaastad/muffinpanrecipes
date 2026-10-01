# Migration Manifests


Append-only log of `pt migration` sessions. Each section records the paths touched between `start` and `finish` for a named bulk operation.


## 7441-testbed-v3 — 2026-09-22T23:09:14Z

- started_at:  `2026-09-22T22:55:30Z`
- finished_at: `2026-09-22T23:09:14Z`
- baseline_head: `604ecb7fe01d557f81c1d92426e91ef9be2548bd`
- action: `manifest-only`

### New paths (introduced during session)
- `[dirty]` `docs/conversation-lab/PROTOCOL.md`
- `[dirty]` `scripts/conversation_lab.py`
- `[dirty]` `tests/test_testbed_panel.py`
- `[untracked]` `docs/conversation-lab/testbed-v3.json`

### Modified paths (status changed during session)
- _(none)_

---

## 7441-zero-name-framing-correction — 2026-09-22T23:34:03Z

- started_at:  `2026-09-22T23:32:27Z`
- finished_at: `2026-09-22T23:34:03Z`
- baseline_head: `8de2013be05974c66cbe4a2faaca66373a5146dd`
- action: `manifest-only`

### New paths (introduced during session)
- _(none)_

### Modified paths (status changed during session)
- _(none)_

---

## 7441-ingredient-name-reconstruction — 2026-09-22T23:55:39Z

- started_at:  `2026-09-22T23:50:01Z`
- finished_at: `2026-09-22T23:55:39Z`
- baseline_head: `44ba1224c0a7976637c75656ea256239427a2f6b`
- action: `manifest-only`

### New paths (introduced during session)
- `[dirty]` `backend/admin/cron_routes.py`
- `[dirty]` `docs/conversation-lab/testbed-v3.json`
- `[dirty]` `tests/test_recipe_anchor.py`

### Modified paths (status changed during session)
- _(none)_

---

## pr119-bench-fingerprint-split — 2026-09-23T00:25:22Z

- started_at:  `2026-09-23T00:21:32Z`
- finished_at: `2026-09-23T00:25:22Z`
- baseline_head: `f5855e8a11bb43205994c8732a2fd63c3efcaa5e`
- action: `manifest-only`

### New paths (introduced during session)
- `[dirty]` `docs/conversation-lab/PROTOCOL.md`

### Modified paths (status changed during session)
- _(none)_

---

## 7323-anthropic-budget-guard — 2026-09-23T00:40:27Z

- started_at:  `2026-09-23T00:25:01Z`
- finished_at: `2026-09-23T00:40:27Z`
- baseline_head: `edee0764fca2eb8640754c9deadccf9588d9233b`
- action: `manifest-only`
- WARNING: HEAD drifted from baseline edee0764fca2 to f0e97e8ade67; revert will restore against current HEAD

### New paths (introduced during session)
- `[dirty]` `scripts/conversation_lab.py`
- `[untracked]` `docs/conversation-lab/BUDGET.md`
- `[untracked]` `scripts/conversation_budget.py`
- `[untracked]` `tests/test_conversation_budget.py`

### Modified paths (status changed during session)
- _(none)_

---

## sf-sweep-muffinpanrecipes-20261001 — 2026-10-01T05:46:01Z

- started_at:  `2026-10-01T05:00:27Z`
- finished_at: `2026-10-01T05:46:01Z`
- baseline_head: `0a16fab0fd2264a7afb9533ad9407c9f10c7de65`
- action: `committed`

### New paths (introduced during session)
- `[dirty]` `backend/admin/cron_routes.py`
- `[dirty]` `backend/admin/episode_routes.py`
- `[dirty]` `backend/admin/routes.py`
- `[dirty]` `backend/agents/art_director.py`
- `[dirty]` `backend/auth/oauth.py`
- `[dirty]` `backend/auth/session.py`
- `[dirty]` `backend/config.py`
- `[dirty]` `backend/memory/agent_memory.py`
- `[dirty]` `backend/messaging/message_system.py`
- `[dirty]` `backend/orchestrator.py`
- `[dirty]` `backend/publishing/episode_renderer.py`
- `[dirty]` `backend/publishing/static_renderer.py`
- `[dirty]` `backend/storage.py`
- `[dirty]` `backend/utils/alerts.py`
- `[dirty]` `backend/utils/atomic.py`
- `[dirty]` `backend/utils/model_router.py`
- `[dirty]` `backend/utils/recipe_sanity.py`
- `[dirty]` `backend/utils/title_validator.py`
- `[dirty]` `scripts/audit_ingredient_overlap.py`
- `[dirty]` `scripts/compare_image_providers.py`
- `[dirty]` `scripts/conversation_lab.py`
- `[dirty]` `scripts/direct_harvest.py`
- `[dirty]` `scripts/fix_catalog.py`
- `[dirty]` `scripts/generate_openai_benchmark_report.py`
- `[dirty]` `scripts/generate_recipe_page.py`
- `[dirty]` `scripts/health_check.py`
- `[dirty]` `scripts/pick_concept.py`
- `[dirty]` `scripts/pin_published_heroes.py`
- `[dirty]` `scripts/run_compressed_week.py`
- `[dirty]` `scripts/run_full_week.py`
- `[dirty]` `scripts/run_pipeline_stage.py`
- `[dirty]` `scripts/score_episodes.py`
- `[dirty]` `scripts/seo_crawl_diff.py`
- `[dirty]` `scripts/session_pipeline_status.py`
- `[dirty]` `scripts/simulate_dialogue_week.py`
- `[dirty]` `scripts/trigger_generation.py`
- `[dirty]` `tests/test_agent_behaviors.py`
- `[dirty]` `tests/test_auth.py`
- `[dirty]` `tests/test_character_memory_durable.py`
- `[dirty]` `tests/test_compare_image_providers.py`
- `[dirty]` `tests/test_creative_dialogue.py`
- `[dirty]` `tests/test_health_check_recovery.py`
- `[dirty]` `tests/test_integration.py`
- `[dirty]` `tests/test_integration_providers.py`
- `[dirty]` `tests/test_judge_scoring.py`
- `[dirty]` `tests/test_pick_concept.py`
- `[dirty]` `tests/test_recipe_anchor.py`
- `[dirty]` `tests/test_seo.py`
- `[untracked]` `tests/test_admin_episode_run.py`
- `[untracked]` `tests/test_run_compressed_week_cost.py`
- `[untracked]` `tests/test_run_pipeline_stage_dialogue.py`
- `[untracked]` `tests/test_session_pipeline_status.py`

### Modified paths (status changed during session)
- _(none)_

---

