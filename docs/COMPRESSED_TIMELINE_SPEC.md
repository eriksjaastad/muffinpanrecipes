# Compressed Timeline Test Spec

## What This Is

A `--test` flag on `run_full_week.py` that runs the full Mon-Sun cron pipeline against Vercel production but stores all artifacts in an isolated test directory in Vercel Blob. Easy to run, easy to inspect, easy to clean up.

## Two Modes

### Test Mode (`--test`)
```bash
doppler run --config prd -- uv run python scripts/run_full_week.py --test
```
- Fires all 7 cron stages sequentially (compressed timeline)
- Episode JSON saved to: `test/episodes/{id}.json`
- Images saved to: `test/images/{recipe_id}/...`
- Auto-generates episode ID like `test-YYYYMMDD-HHMMSS`
- Everything under `test/` prefix in Vercel Blob
- Cleanup: `--cleanup` flag deletes ALL blobs under `test/`

### Production Mode (no flag)
```bash
doppler run --config prd -- uv run python scripts/run_full_week.py
```
- Episode JSON saved to: `episodes/{iso-week}.json`
- Images saved to: `images/{recipe_id}/...`
- Episode ID = current ISO week (e.g. `2026-W11`)
- Sunday attaches episode to published recipe
- Recipe gets its own page, hero image on front page

## What Changes

### 1. `run_full_week.py` (the script)
- `--test` flag: sets `test=true` in payload to cron endpoints
- `--cleanup` flag: deletes all `test/*` blobs and exits
- In test mode, auto-generates episode ID with timestamp
- After run completes, prints viewable URL to episode data

### 2. Cron route `StageRequest` model
- Add optional `test: bool = False` field
- When `test=true`, storage calls use `test/` prefix for all paths

### 3. `backend/storage.py`
- `save_episode()` and `load_episode()` accept optional `prefix` param
- Default prefix: `""` (production)
- Test prefix: `"test/"` — so path becomes `test/episodes/{id}.json`

### 4. Wednesday image upload
- Image blob paths get same prefix treatment
- Test: `test/images/{recipe_id}/round_1/macro_closeup.png`
- Production: `images/{recipe_id}/round_1/macro_closeup.png`

## Cleanup

```bash
# Delete all test artifacts from Vercel Blob
doppler run --config prd -- uv run python scripts/run_full_week.py --cleanup
```

Lists everything under `test/` prefix, confirms count, deletes all.

## What This Does NOT Change

- Cron schedules in `vercel.json` (those are for real weekly runs)
- The actual stage logic (brainstorm, recipe dev, photography, etc.)
- Production episode/image storage paths
- Any existing test-e2e-001 through test-e2e-008 blobs (those are legacy)

## Success Criteria

1. `--test` runs all 7 stages, all pass
2. Episode JSON is at `test/episodes/{id}.json` in blob
3. Wednesday images are at `test/images/{recipe_id}/...` in blob
4. `--cleanup` removes all test artifacts
5. Running WITHOUT `--test` saves to production paths (no `test/` prefix)
