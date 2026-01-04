# Muffin Pan Recipes

An AI-driven experimental recipe platform focused exclusively on "Muffin Tin Meals." This project explores high-volume content generation, niche SEO optimization, and automated deployment to Vercel.

## Quick Start

### Development
1. Clone the repository.
2. Open `src/index.html` in your browser to view the prototype.

### Deployment
1. The project is configured for Vercel via `vercel.json`.
2. Push to `main` to trigger an automatic deployment.

## Mission Control (Cloud GPU)
This project leverages the central **SSH Agent** for high-end image generation on RunPod.

### Photography Pipeline
1.  **Prompts:** Generated locally using `scripts/generate_image_prompts.py`.
2.  **Generation:** Triggered via the central SSH Agent on your GPU pod.
3.  **Sync:** Results are synced to Cloudflare R2 and then downloaded locally for selection.

To start the photography pipeline, ensure the SSH Agent is running:
```bash
cd /Users/eriksjaastad/projects/_tools/ssh_agent
./start_agent.sh
```

## Documentation
See the `Documents/` directory for detailed documentation:
- [Architectural Decisions](Documents/core/ARCHITECTURAL_DECISIONS.md)
- [Recipe Schema](Documents/core/RECIPE_SCHEMA.md)

## Status
- **Current Phase:** Phase 1: AI Recipe Engine
- **Status:** #status/active

