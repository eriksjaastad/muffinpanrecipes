
# Muffin Pan Recipes

An AI-driven experimental recipe platform focused exclusively on "Muffin Tin Meals." This project explores high-volume content generation, niche SEO optimization, and automated deployment to Vercel.

> **The Vision:** "If it fits in a muffin pan, it belongs here."
> **Core Tenets:** Encapsulation, Structural Layering, Modular Scalability, and Medium-Agnosticism (Oven, Fridge, Freezer).

## 🏗️ Architectural Decisions (ADR Summary)

- **AD 001: Pre-rendered pages** - Recipe pages are rendered when a week publishes and served as static, mobile-first HTML (vanilla CSS). Episode data lives in Vercel Blob.
- **AD 002: Manual Vercel deploys** - Deploy a preview, health-check it, then promote it; see [DEPLOYMENT.md](DEPLOYMENT.md).
- **AD 003: "No-Fluff" UI** - Prioritizes "Jump to Recipe" and core content; eliminates clutter common in food blogs.
- **AD 004: Vercel Root Directory** - `src/` is the web root to keep scripts and raw data private.

## 🚀 Quick Start

### Runtime Bootstrap (uv + Python 3.12)

```bash
# One-time on macOS
brew install uv python@3.12

# From project root
uv venv --python 3.12 --clear .venv
uv sync --python 3.12

# Verify
uv run pytest tests/test_discord_review_link.py tests/test_creative_dialogue.py -q
```

If `uv` isn't available or fails in your environment, you can run tests with a local venv:

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python -m pytest tests/test_discord_review_link.py tests/test_creative_dialogue.py -q
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

## 📡 Image Generation
Wednesday's cron shoots the week's photos with Gemini (`backend/agents/art_director.py`), each
prompt naming one real muffin pan from `backend/utils/pan_library.py`. Erik approves a photo on
the admin photo review before Sunday publishes. The older RunPod / R2 scripts
(`generate_image_prompts.py`, `trigger_generation.py`, `direct_harvest.py`) are not part of the
weekly pipeline.

## 💰 Vercel Cost Management

Build Minutes are the dominant cost driver (~95% of usage charges at $0.126/min).

Each `vercel deploy` and each `vercel promote` is a build, and both count against the
5-deploys-per-day cap in [DEPLOYMENT.md](DEPLOYMENT.md).

**Disabled:** Speed Insights (was $0.65/period, not needed).

## 📋 Status
- **Status:** #status/active. Live, publishing one recipe a week through the Mon-Sun Vercel crons.

## 🖥️ Admin Simulation Viewer (MVP)
Route: `/admin/simulations` - View character-driven dialogue transcripts and recipe generation runs.

## Code Review

No GitHub Actions run on this repo. Every PR gets an independent local review on its exact
head (a Claude code-reviewer, then Codex) before it merges; the procedure is the portfolio PR
policy (`pt info get pr_merge_policy`).
