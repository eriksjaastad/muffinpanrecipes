
# Muffin Pan Recipes

An AI-driven experimental recipe platform focused exclusively on "Muffin Tin Meals." This project explores high-volume content generation, niche SEO optimization, and automated deployment to Vercel.

> **The Vision:** "If it fits in a muffin pan, it belongs here."
> **Core Tenets:** Encapsulation, Structural Layering, Modular Scalability, and Medium-Agnosticism (Oven, Fridge, Freezer).

## 🏗️ Architectural Decisions (ADR Summary)

- **AD 001: Static Site Architecture** - High-speed, mobile-first HTML/Tailwind. AI-generated recipes are stored as Markdown/JSON and rendered.
- **AD 002: Vercel Deployment** - Native GitHub integration for 0-manual-step deployment on push to `main`.
- **AD 003: "No-Fluff" UI** - Prioritizes "Jump to Recipe" and core content; eliminates clutter common in food blogs.
- **AD 004: Vercel Root Directory** - `src/` is the web root to keep scripts and raw data private.

## 🚀 Quick Start

### Runtime Bootstrap (uv + Python 3.12)

```bash
# One-time on macOS
brew install uv python@3.12

# From project root
uv venv --python /opt/homebrew/bin/python3.12 --clear .venv
uv sync --python /opt/homebrew/bin/python3.12

# Verify
uv run pytest tests/test_discord_review_link.py tests/test_creative_dialogue.py -q
```

### Development
1. Clone the repository.
2. Run the bootstrap steps above.
3. Open `src/index.html` in your browser to view the prototype.

### Secrets Runtime (Doppler)

This project expects runtime secrets from Doppler (not `.env` files).

```bash
# Example: run admin app with injected secrets
cd <repo-root>
doppler run -- uv run python -m backend.admin.app
```

## 🛠️ Project Structure

- `backend/` - [AI Creative Team Orchestration](backend/README.md) (Python/FastAPI)
- `src/` - [Static Site Source](src/README.md) (HTML/Tailwind/Recipes)
- `scripts/` - [Automation & Image Pipeline](scripts/README.md) (Python/Shell)
- `data/` - Recipe storage and simulation logs
- `Documents/` - Legacy and deep-dive documentation (deprecated in favor of READMEs)

## 📡 Image Generation Pipeline
Leverages a central **SSH Agent** for high-end image generation on RunPod.

1. **Prompt Gen:** `scripts/generate_image_prompts.py` (SDXL prompts via DeepSeek-R1).
2. **Trigger:** `scripts/trigger_generation.py` (Uploads to Cloudflare R2).
3. **Harvest:** `scripts/direct_harvest.py` (Remote GPU pods generate images via Stability AI).
4. **Curation:** `scripts/art_director.py` (Agent selects the winner and moves to `src/assets/images/`).

## 💰 Vercel Cost Management

Build Minutes are the dominant cost driver (~95% of usage charges at $0.126/min).

**Key rule: Batch your pushes.** Every push to `main` triggers a full build. On heavy dev days (20+ commits pushed individually), build costs can hit $3+. Batching into fewer pushes cuts costs proportionally.

| Scenario | Pushes/Day | Est. Daily Cost |
|----------|-----------|----------------|
| Heavy dev (push per commit) | 15-25 | $2-3+ |
| Normal dev (batched) | 3-5 | $0.40-0.65 |
| Steady state (cron only) | 0-1 | $0-0.13 |

**Disabled:** Speed Insights (was $0.65/period, not needed).

## 📋 Status
- **Current Phase:** Phase 4: AI Creative Team Integration
- **Status:** #status/active

## 🖥️ Admin Simulation Viewer (MVP)
Route: `/admin/simulations` - View character-driven dialogue transcripts and recipe generation runs.
