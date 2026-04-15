# Plan: Fix Episode Persistence on Vercel (#5039)

## The Problem

Episode JSON files don't persist between Vercel cron invocations. The `_CloudBackend` in `backend/storage.py` is a stub — every method falls through to local filesystem, which is ephemeral on serverless. The weekly pipeline has never completed a full Mon-Sun cycle in production.

## Current State

- `data/` is gitignored (line 10 of `.gitignore`)
- `data/` is also vercelignored (line 2 of `.vercelignore`)
- Episodes live at `data/episodes/*.json` — never deployed, never persisted
- `backend/data/` is whitelisted (character bios, memory.json) — these DO deploy
- `save_image` in `_CloudBackend` already has a working Vercel Blob PUT (lines 221-238) — we can follow that exact pattern for episodes

## The Fix (Two Parts)

### Part 1: Vercel Blob for cron writes (required — unblocks everything)

The `save_image` method already shows the pattern. Do the same for episodes.

**Files to change:** `backend/storage.py` only

1. `save_episode` — PUT JSON to `https://blob.vercel-storage.com/episodes/{episode_id}.json`
2. `load_episode` — GET from same URL, parse JSON
3. `list_episodes` — LIST with prefix `episodes/`

That's it. ~30 lines replacing the TODOs. Follow the exact pattern from `save_image` (lines 221-238).

**Env var:** `BLOB_READ_WRITE_TOKEN` — must be set in Doppler for prd and stg. Check if it already exists:
```bash
doppler secrets get BLOB_READ_WRITE_TOKEN --project muffinpanrecipes --config prd
```
If not, create a Vercel Blob store in the dashboard and add the token.

### Part 2: Commit episodes to git (nice-to-have — historical record)

Episodes are project artifacts. They should be in version control.

**Files to change:**
- `.gitignore` — add `!data/episodes/` whitelist (same pattern as `!backend/data/`)
- `.vercelignore` — add `!data/episodes/` so they deploy (lets filesystem reads work as fallback)

Then periodically: `git add data/episodes/ && git commit -m "data: weekly episodes"`

This gives us belt AND suspenders — Blob for cron writes, git for history.

### Part 3: Loud failure guard

Add to `_CloudBackend.__init__`:
```python
if self._vercel_env and not self._blob_token:
    raise RuntimeError(
        "FATAL: Running on Vercel without BLOB_READ_WRITE_TOKEN. "
        "Episode data WILL NOT persist. Refusing to start."
    )
```

No more silent warnings. If we're on Vercel without the token, the app doesn't start. Period.

**File to change:** `backend/storage.py` — `_CloudBackend.__init__`

## Verification

After deploying:
1. POST to `/api/cron/monday` with cron secret
2. Wait for lambda to die (cold start timeout, ~5 min)
3. POST to `/api/cron/tuesday` with cron secret
4. Check that Tuesday's response includes Monday's concept — proves cross-invocation persistence

## What NOT to do

- Don't add a database
- Don't add Vercel KV
- Don't restructure the storage abstraction
- Don't touch `_FilesystemBackend` — it works fine for local dev
- Don't move episodes out of `data/episodes/` — the path convention is fine

## Order of Operations

1. Check if `BLOB_READ_WRITE_TOKEN` exists in Doppler (or create Vercel Blob store)
2. Implement the 3 episode methods in `_CloudBackend` (Part 1)
3. Add the loud failure guard (Part 3)
4. Deploy and run verification steps
5. Update `.gitignore` and `.vercelignore` (Part 2)
6. Commit existing local episode test files as historical baseline

## Risk Check

- Vercel Blob free tier: 500MB storage, 1M reads/month. We'll use <1MB/year on episodes. No risk.
- If Blob API is down, cron fails loudly (requests.raise_for_status). Better than silent data loss.
- Local dev is completely unaffected — still uses `_FilesystemBackend`.
