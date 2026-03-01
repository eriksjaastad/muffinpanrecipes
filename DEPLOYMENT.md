# Deployment Guide — Muffin Pan Recipes

## Architecture

| Layer | Local Dev | Vercel Production |
|-------|-----------|-------------------|
| Pipeline trigger | `crontab` → `run_compressed_week.py` | Vercel Cron → `/api/cron/{stage}` |
| Secrets | `doppler run --` | Vercel Env Vars (auto-synced from Doppler) |
| Storage | Local filesystem (`data/`) | Vercel Blob (stub, falls back to filesystem) |
| Dialogue model | `openai/gpt-5-mini` (or `DIALOGUE_MODEL_OVERRIDE`) | `openai/gpt-5-mini` |
| Auth bypass | `LOCAL_DEV=true` | Never (always OAuth) |

---

## Required Secrets

All secrets live in **Doppler** (`project: muffinpanrecipes`). They auto-sync to
Vercel via the Doppler → Vercel integration (set up once).

| Secret | Purpose | Doppler config |
|--------|---------|----------------|
| `STABILITY_API_KEY` | Stability AI image generation | `prd`, `stg` |
| `OPENAI_API_KEY` | `gpt-5-mini` dialogue | `prd`, `stg` |
| `GOOGLE_CLIENT_ID` | OAuth login | `prd`, `stg` |
| `GOOGLE_CLIENT_SECRET` | OAuth login | `prd`, `stg` |
| `GOOGLE_AUTHORIZED_EMAILS` | Admin allow-list (comma-sep) | `prd`, `stg` |
| `MUFFINPAN_DISCORD_WEBHOOK` | Pipeline Discord notifications | `prd`, `stg` |
| `CRON_SECRET` | Vercel cron auth token | `prd`, `stg` |

To add a new secret:
```bash
doppler secrets set MY_SECRET  # adds to current config (prd by default)
# Doppler → Vercel integration syncs it within seconds
```

---

## Setup: New Developer

### 1. Install tools
```bash
brew install doppler
npm install -g vercel
```

### 2. Authenticate
```bash
doppler login
doppler setup   # in repo root — selects muffinpanrecipes project
vercel link     # link to Vercel project
```

### 3. Run locally
```bash
LOCAL_DEV=true PYTHONPATH=. doppler run -- .venv/bin/uvicorn \
  backend.admin.app:create_admin_app --factory --reload --port 8000
```

---

## Vercel Cron Schedule

Defined in `vercel.json` at repo root. All times UTC.

| Stage | Route | Schedule (UTC) | Local time (PT) |
|-------|-------|----------------|-----------------|
| Monday — Brainstorm | `/api/cron/monday` | `30 14 * * 1` | 7:30 AM Mon |
| Tuesday — Recipe Dev | `/api/cron/tuesday` | `30 14 * * 2` | 7:30 AM Tue |
| Wednesday — Photography | `/api/cron/wednesday` | `30 14 * * 3` | 7:30 AM Wed |
| Thursday — Copywriting | `/api/cron/thursday` | `30 14 * * 4` | 7:30 AM Thu |
| Friday — Final Review | `/api/cron/friday` | `30 14 * * 5` | 7:30 AM Fri |
| Saturday — Deployment | `/api/cron/saturday` | `30 14 * * 6` | 7:30 AM Sat |
| Sunday — Publish | `/api/cron/sunday` | `0 0 * * 1` | 5:00 PM Sun |

### Manual trigger (dev)
```bash
# Trigger a stage manually (local dev — no CRON_SECRET needed)
curl -X POST http://localhost:8000/api/cron/monday \
  -H "Content-Type: application/json" \
  -d '{"episode_id": "2026-W09", "concept": "Mini Tiramisu Cups"}'

# Trigger on production (requires CRON_SECRET)
curl -X POST https://muffinpanrecipes.com/api/cron/monday \
  -H "Authorization: Bearer $CRON_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"episode_id": "2026-W09", "concept": "Mini Tiramisu Cups"}'
```

---

## Local Dev Cron (Mac Mini)

For testing the full pipeline locally with compressed timing:

```bash
# Install test cron (all 7 stages, 8 min apart, every day at noon)
bash scripts/cron_stage_schedules.sh install-test

# Check status
bash scripts/cron_stage_schedules.sh status

# View live log
tail -f logs/cron_compressed.log

# Remove cron
bash scripts/cron_stage_schedules.sh remove
```

---

## Doppler → Vercel Sync

Doppler is the single source of truth. The integration syncs automatically.

- Dashboard: [dashboard.doppler.com](https://dashboard.doppler.com)
- Project: `muffinpanrecipes`
- Configs: `prd` → Vercel Production, `stg` → Vercel Preview
- Integration: Settings → Integrations → Vercel

> **Never set Vercel env vars manually** — they'll be overwritten by Doppler sync.

---

## Environment Detection (`backend/config.py`)

```python
from backend.config import config

config.is_local_dev     # True if LOCAL_DEV=true
config.is_vercel        # True if VERCEL_ENV is set
config.environment      # "local" | "development" | "preview" | "production"
config.storage_backend  # "filesystem" | "cloud"
config.dialogue_model   # "openai/gpt-5-mini" (override: DIALOGUE_MODEL_OVERRIDE)
config.auth_bypass      # True in local dev — skips OAuth
```

---

## Storage (`backend/storage.py`)

```python
from backend.storage import storage

storage.load_episode("2026-W09")
storage.save_episode("2026-W09", data)
storage.list_episodes()
storage.save_image("data/images/abc/shot.png", bytes)
storage.get_image_url("data/images/abc/shot.png")
```

In `LOCAL_DEV`, data writes to `data/episodes/`, `data/images/` etc.
On Vercel, Vercel Blob will be wired in once `BLOB_READ_WRITE_TOKEN` is set.
