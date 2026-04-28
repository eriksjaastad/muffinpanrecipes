# Environment Variables

Single source of truth for every environment variable the muffinpanrecipes codebase reads. Generated 2026-04-14 from a grep over `os.environ.get` / `os.getenv` calls across `backend/` and `scripts/` (#5814).

**Where they come from**
- **Production:** Vercel env + Doppler (`muffinpanrecipes` project, `prd` config)
- **Staging:** Doppler `stg` config
- **Local dev:** Doppler `dev` config (`doppler run --project muffinpanrecipes --config dev -- …`) — dev is local-only, does NOT need Blob / Discord / admin URLs

**Conventions**
- `R` (Required): app crashes / misbehaves badly without it
- `O` (Optional): has a default, safe to omit
- `S` (Secret): do not commit; store in Doppler
- `T` (Test-only): only read during pytest / test harness runs

---

## API secrets (Doppler)

| Var | Flags | Read at | Purpose |
|---|---|---|---|
| `OPENAI_API_KEY` | R, S | `backend/utils/model_router.py:267,454`, `scripts/list_openai_models.py:21` | OpenAI API auth for GPT-5.1 recipe generation + vision eval |
| `ANTHROPIC_API_KEY` | R, S | `backend/utils/model_router.py:358,501` | Anthropic API auth for Haiku 4.5 dialogue + Opus 4.6 judge |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | O, S | `backend/utils/model_router.py:401`, `scripts/compare_image_providers.py:137` | Google API auth. Either works; `GOOGLE_API_KEY` is the canonical name, `GEMINI_API_KEY` is accepted for compat |
| `NANOBANANA_API_KEY` | O, S | `scripts/compare_image_providers.py:137`, `tests/test_integration_providers.py:23` | Nano Banana image-gen auth (same upstream as Google) |
| `STABILITY_API_KEY` | R, S | `scripts/validate_env.py:12`, `scripts/direct_harvest.py:21`, `backend/agents/art_director.py:334` | Stability AI auth for image generation. Required for photography stage |
| `BLOB_READ_WRITE_TOKEN` | R, S | `backend/storage.py:200`, `scripts/backfill_webp.py:102`, `scripts/fix_catalog.py:56`, `scripts/run_full_week.py:317`, `scripts/score_episodes.py:59`, `scripts/generate_recipe_page.py:527` | Vercel Blob auth for episode + image persistence. `_CloudBackend` crashes on start if missing on Vercel |
| `CRON_SECRET` | R, S | `scripts/run_full_week.py:316`, `backend/admin/cron_routes.py` (via header check) | Vercel cron endpoint shared secret |
| `JWT_SECRET` | R, S | `backend/auth/session.py:32`, `tests/test_auth.py:109` | Admin UI session token signing |
| `MUFFINPAN_DISCORD_WEBHOOK` | O, S | `backend/utils/discord.py:12`, `scripts/health_check.py:129` | Discord failure alerts + health-check pings. `_pytest_gate()` suppresses during tests |
| `NEWSLETTER_API_KEY` | O, S | `backend/newsletter/manager.py:53` | Newsletter service auth (currently unused — `NEWSLETTER_SERVICE=file`) |

### R2 (unused — scripts/trigger_generation.py only)
| Var | Flags | Read at | Purpose |
|---|---|---|---|
| `MUFFINPANRECIPES_R2_ACCESS_KEY_ID` | O, S | `scripts/trigger_generation.py:27` | Cloudflare R2 access key (legacy path, not on critical path) |
| `MUFFINPANRECIPES_R2_SECRET_ACCESS_KEY` | O, S | `scripts/trigger_generation.py:28` | R2 secret |
| `MUFFINPANRECIPES_R2_ENDPOINT` | O, S | `scripts/trigger_generation.py:29` | R2 endpoint URL |
| `MUFFINPANRECIPES_R2_BUCKET_NAME` | O, S | `scripts/trigger_generation.py:30` | R2 bucket name |

---

## Model configuration

| Var | Flags | Read at | Default | Purpose |
|---|---|---|---|---|
| `DIALOGUE_MODEL` | O | `backend/config.py:119`, `scripts/run_pipeline_stage.py:39` | `openai/gpt-5-mini` (legacy) → overridden to `anthropic/claude-haiku-4-5` in Doppler prd | Dialogue generation model |
| `RECIPE_MODEL` | O | `backend/config.py:137`, `backend/agents/baker.py:201,253`, `creative_director.py:84,136,170`, `copywriter.py:164,224` | `openai/gpt-5-mini` | Recipe content model (baker + creative director + copywriter) |
| `JUDGE_MODEL` | O | `backend/config.py:158` | `anthropic/claude-opus-4-6` (Doppler) | Dialogue QA judge |
| `VISION_EVAL_MODEL` | O | `backend/agents/art_director.py:28` | `openai/gpt-5-mini` | Image round quality evaluator |
| `OPENAI_MODEL_ALLOWLIST` | O | `backend/utils/model_router.py:205` | unset → permissive | Comma-separated whitelist of OpenAI models the router may use |
| `ANTHROPIC_MODEL_ALLOWLIST` | O | `backend/utils/model_router.py:223` | unset → permissive | Same for Anthropic |
| `GOOGLE_MODEL_ALLOWLIST` | O | `backend/utils/model_router.py:241` | unset → permissive | Same for Google |
| `STABILITY_ENGINE_ID` | O | `scripts/compare_image_providers.py:208` | `stable-diffusion-xl-1024-v1-0` | Stability engine selection |
| `NANOBANANA_MODEL` | O | `scripts/compare_image_providers.py:209`, `tests/test_integration_providers.py:27` | `gemini-2.5-flash-image` | Nano Banana model name |
| `NANOBANANA_ASPECT_RATIO` | O | `scripts/compare_image_providers.py:210` | `1:1` | Aspect ratio hint |
| `NANOBANANA_IMAGE_SIZE` | O | `scripts/compare_image_providers.py:211` | unset | Image size hint |

---

## Runtime / deployment

| Var | Flags | Read at | Default | Purpose |
|---|---|---|---|---|
| `VERCEL_ENV` | O (auto-set) | `backend/config.py:54`, `backend/storage.py:208`, `backend/admin/cron_routes.py:90`, `backend/agents/art_director.py:123` | unset | Vercel auto-sets to `production`/`preview`/`development`. Code uses it to branch cloud-vs-filesystem behavior |
| `LOCAL_DEV` | O | `backend/config.py:57` | `""` | Force filesystem backend. Set `true` for local dev |
| `STORAGE_BACKEND` | O | `backend/config.py:97` | auto-detect | Manual override: `filesystem` or `cloud` |
| `ENABLE_BEHIND_THE_SCENES` | O | `backend/publishing/episode_renderer.py:287`, `scripts/generate_recipe_page.py:253` | `"true"` | Hide the dialogue/BTS section on recipe pages when `false` |
| `CI` | O, auto-set | `scripts/health_check.py:165` | unset | Truthy in GitHub Actions; strictens health_check failure mode |
| `PYTEST_CURRENT_TEST` | T, auto-set | `backend/utils/discord.py:24` | unset outside pytest | pytest sets this per-test; `_pytest_gate()` in Discord helpers keys off it |
| `RUN_LIVE_PROVIDER_TESTS` | T | `tests/test_creative_dialogue.py:10`, `test_agent_behaviors.py:18`, `test_integration.py:17`, `test_integration_providers.py:7` | `""` → skip | Gate for 11 tests that hit live LLM providers. Set `true` + real Doppler secrets to run them |

---

## Paths

| Var | Flags | Read at | Default | Purpose |
|---|---|---|---|---|
| `PROJECT_ROOT` | O | `backend/publishing/pipeline.py:51`, `scripts/optimize_images.py:11`, `generate_image_prompts.py:17`, `art_director.py:17`, `trigger_generation.py:22`, `validate_env.py:16` | derived from `__file__` | Repo root path override |
| `WORKSPACE_ROOT` | O | `scripts/direct_harvest.py:18` | `/workspace` if exists else repo | Legacy harvest workspace |
| `AI_ROUTER_PATH` | O | `scripts/generate_image_prompts.py:18`, `scripts/art_director.py:18`, `scripts/validate_env.py:23` | `../_tools/ai_router` | Path to the AI router CLI used by a couple of legacy scripts |
| `OUTPUT_ROOT` | O | `scripts/direct_harvest.py:20` | `$WORKSPACE_ROOT/output/muffin_pan` | Legacy harvest output dir |
| `JOBS_FILE` | O | `scripts/direct_harvest.py:19` | `$WORKSPACE_ROOT/image_generation_jobs.json` | Legacy harvest job queue |

---

## Publishing schedule

| Var | Flags | Read at | Default | Purpose |
|---|---|---|---|---|
| `MUFFINPAN_PUBLISH_TIMEZONE` | O | `backend/utils/publish_schedule.py:9` | `America/Los_Angeles` | Weekly publish cutoff timezone |
| `MUFFINPAN_PUBLISH_WEEKDAY` | O | `backend/utils/publish_schedule.py:10` | `6` (Sun) | Day of week for publish, 0=Mon..6=Sun |
| `MUFFINPAN_PUBLISH_HOUR` | O | `backend/utils/publish_schedule.py:11` | `17` (5pm) | Publish hour |
| `MUFFINPAN_PUBLISH_MINUTE` | O | `backend/utils/publish_schedule.py:12` | `0` | Publish minute |
| `MUFFINPAN_ADMIN_BASE_URL` | O | `backend/utils/discord.py:13` | `http://localhost:8000` | Base URL used to build admin review links in Discord notifications |

---

## Auth

| Var | Flags | Read at | Default | Purpose |
|---|---|---|---|---|
| `GOOGLE_REDIRECT_URI` | O | `backend/auth/oauth.py:69` | passed arg | OAuth callback URL |
| `GOOGLE_AUTHORIZED_EMAILS` | O | `backend/auth/oauth.py:75` | unset → deny all | Comma-separated allowlist of admin emails |
| `NEWSLETTER_SERVICE` | O | `backend/newsletter/manager.py:52` | `file` | Newsletter backend: `file` / `mailchimp` / etc |

---

## Maintenance

When a new `os.environ.get(...)` or `os.getenv(...)` is added to the codebase, append a row here. Verification query:

```bash
grep -rnE "os\.(environ\.get|getenv)\(['\"]([A-Z_][A-Z0-9_]*)['\"]" backend/ scripts/ tests/ \
  | grep -oE "['\"]([A-Z_][A-Z0-9_]*)['\"]" | sort -u
```

The path to this file is recorded in `pt info -p muffinpanrecipes env_vars_doc` for cross-session discovery.
