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

## Image Generation Pipeline
This project uses a 4-step automated photography pipeline to generate and select high-end visuals.

1. **`scripts/generate_image_prompts.py`**: Analyzes recipes and generates 3 high-key SDXL prompts per recipe using AI Router (Local DeepSeek-R1 or Cloud).
2. **`scripts/trigger_generation.py`**: Handshakes with Mission Control to upload jobs and scripts to Cloudflare R2.
3. **`scripts/direct_harvest.py` (RunPod)**: Executed on a remote GPU pod to generate the images directly via Stability AI API, bypassing Blender buffer issues.
4. **`scripts/art_director.py` (Local)**: The Art Director agent reviews the generated variants from `__temp_harvest/` and moves the winner to `src/assets/images/`.

## Documentation
See the `Documents/` directory for detailed documentation:
- [Architectural Decisions](Documents/core/ARCHITECTURAL_DECISIONS.md)
- [Recipe Schema](Documents/core/RECIPE_SCHEMA.md)

## Development Resources
- [[muffinpanrecipes/WARDEN_LOG.yaml|WARDEN_LOG.yaml]]
- [[muffinpanrecipes/package-lock.json|package-lock.json]]
- [[muffinpanrecipes/scripts/warden_audit.py|warden_audit.py]]

## Status
- **Current Phase:** Phase 1: AI Recipe Engine
- **Status:** #status/active


<!-- project-scaffolding template appended -->

# [PROJECT_NAME]

[Brief 2-3 sentence description of the project.]

## Quick Start

### Installation
```bash
# [Add installation steps here]
```

### Usage
```bash
# [Add usage steps here]
```

## Documentation
See the `Documents/` directory for detailed documentation:
- [Architecture Overview](Documents/ARCHITECTURE_OVERVIEW.md)
- [Operations Guide](Documents/OPERATIONS_GUIDE.md)

## Status
- **Current Phase:** [Phase Name]
- **Status:** #status/active

