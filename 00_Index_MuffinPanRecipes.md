---
tags:
  - map/project
  - p/muffinpanrecipes
  - type/experiment
  - domain/food-tech
  - status/active
status: #status/active
created: 2026-01-03
---

# Muffin Pan Recipes

An AI-driven experimental recipe platform focused exclusively on "Muffin Tin Meals." The project explores high-volume content generation, niche SEO optimization, and automated deployment via Vercel.

## 🎯 The Vision
"If it fits in a muffin pan, it belongs here."
- **Niche Focus:** Breakfast, Savory Dinners, Desserts, and Snack-sized Portions.
- **AI Integration:** Leverage LLMs for recipe generation, formatting, and SEO meta-data creation.
- **Architecture:** Lean static site (HTML/Tailwind) deployed via Vercel.

## 🏗️ Key Components
- **The Brain:** AI-driven recipe generator (Markdown-first).
- **The Hands:** Automated deployment pipeline (GitHub -> Vercel).
- **The Face:** Clean, fast-loading UI focused on "Jump to Recipe" utility.

## 📋 Project Status
**Current Phase:** Phase 3.3 (Content Moat) - 100% Complete
**Progress:** Site transitioned to a full Static Site Generation (SSG) model with unique URLs and rich social metadata for all 10 recipes.
**Next Step:** Build the "Editorial Command Center" (Phase 3.4) for local administration and scaling.

## 🚀 Recent Activity
- **2026-01-04:** **Master Archive & Vercel Shield:** Industrialized the image pipeline with safe archival logic and implemented a `.gitignore` shield to protect production from large master assets.
- **2026-01-04:** **SSG Hardening:** Implemented HTML escaping, canonical links, and `robots.txt` to finalize the Content Moat architecture.
- **2026-01-04:** **The Content Moat:** Transitioned the site from a single-page modal system to a Static Site Generation (SSG) model. Every recipe now has a unique URL, automated Open Graph metadata, and a verified sitemap for search indexing.
- **2026-01-04:** **Visual Asset Optimization:** Converted all images to `.webp` (96% size reduction) and implemented lazy loading for "Dial-up" speed parity.
- **2026-01-04:** **Industrialization Upgrade:** Refactored entire repo for portability, added robust error handling, and implemented an environment guard script.
- **2026-01-04:** **Scalability Refactor:** Extracted recipe data to `src/recipes.json` and implemented a production-grade asynchronous loader with auto-retry logic in `src/index.html`.
- **2026-01-04:** Visual Harvest Complete! 30 images generated on RunPod, "Art Directed" locally, and 10 winners integrated into `src/assets/images/`.
- **2026-01-04:** Production Launch! muffinpanrecipes.com is live on Vercel with 10 recipes and 🧁 favicon.
- **2026-01-04:** Refactored photography pipeline to use Stability AI Direct API for reliable headless generation.
- **2026-01-04:** Resolved 404 error on production by co-locating `vercel.json` with the `src/` root directory.

## 📖 Reference Links
- [[TODO]]
- [[ARCHITECTURAL_DECISIONS]]
- [[RECIPE_SCHEMA]]
- [[IMAGE_STYLE_GUIDE]]

scaffolding_version: 1.0.0
scaffolding_date: 2026-01-14

## Related Documentation

- [[architecture_patterns]] - architecture
- [[cloud_gpu_setup]] - cloud GPU
- [[dashboard_architecture]] - dashboard/UI
- [[error_handling_patterns]] - error handling
- [[deployment_patterns]] - deployment
- [[performance_optimization]] - performance
- [[recipe_system]] - recipe generation
- [[image-workflow/README]] - Image Workflow
- [[muffinpanrecipes/README]] - Muffin Pan Recipes
- [[queue_processing_guide]] - queue/workflow

<!-- LIBRARIAN-INDEX-START -->

### File Index

| File | Description |
| :--- | :--- |
| [[00_Index_MuffinPanRecipes.md]] | Muffin Pan Recipes |
| [[AGENTS.md]] | 🎯 Project Overview |
| [[CLAUDE.md]] | > **Purpose:** Project-specific instructions for AI assistants (Claude, Gemini, etc.) |
| [[Documents/REVIEWS_AND_GOVERNANCE_PROTOCOL.md]] | 🛡️ Ecosystem Governance & Review Protocol (v1.2) |
| [[Documents/character-creation-vision-v1.md]] | Character Creation Vision - Brainstorm with Claude |
| [[Documents/core/ARCHITECTURAL_DECISIONS.md]] | Architectural Decisions - Muffin Pan Recipes |
| [[Documents/core/IMAGE_PROMPTS.md]] | AI Image Prompt Library |
| [[Documents/core/IMAGE_STYLE_GUIDE.md]] | Muffin Pan Recipes: Image Style Guide |
| [[Documents/core/PERSONAS.md]] | This document defines the specialized AI identities that manage the content, aesthetics, and social ... |
| [[Documents/core/RECIPE_SCHEMA.md]] | Recipe Schema (Markdown) - Version 1.1 |
| [[Documents/core/SYSTEM_PROMPT_RECIPES.md]] | System Prompt: The Muffin Pan Chef (Master Chef System) |
| [[Documents/patterns/code-review-standard.md]] | Code Review Standardization |
| [[Documents/patterns/learning-loop-pattern.md]] | Learning Loop Pattern |
| [[Documents/reference/LOCAL_MODEL_LEARNINGS.md]] | Local Model Learnings |
| [[PRD.md]] | 1) Overview |
| [[README.md]] | Muffin Pan Recipes |
| [[TODO.md]] | Muffin Pan Recipes — Roadmap |
| [[WARDEN_LOG.yaml]] | No description available. |
| [[agent to agent communication.md]] | I hear you—I was conflating two different "memory" systems because they both involve agents and data... |
| [[package-lock.json]] | No description available. |
| [[package.json]] | No description available. |
| [[requirements.txt]] | No description available. |
| [[scripts/art_director.py]] | No description available. |
| [[scripts/build_site.py]] | No description available. |
| [[scripts/direct_harvest.py]] | No description available. |
| [[scripts/generate_image_prompts.py]] | No description available. |
| [[scripts/optimize_images.py]] | No description available. |
| [[scripts/pre_review_scan.sh]] | pre_review_scan.sh - Run before code reviews or commits |
| [[scripts/trigger_generation.py]] | 🧁 Muffin Pan Recipes: Mission Control Handshake Script |
| [[scripts/validate_env.py]] | No description available. |
| [[scripts/validate_project.py]] | No description available. |
| [[scripts/warden_audit.py]] | No description available. |
| [[src/assets/images/baked-oatmeal-cups.webp]] | No description available. |
| [[src/assets/images/buffalo-chicken-mac-bites.webp]] | No description available. |
| [[src/assets/images/classic-blueberry-muffins.webp]] | No description available. |
| [[src/assets/images/dark-chocolate-chip-muffins.webp]] | No description available. |
| [[src/assets/images/jumbo-cornbread-bites.webp]] | No description available. |
| [[src/assets/images/mini-meatloaf-bites.webp]] | No description available. |
| [[src/assets/images/mini-pancake-bites.webp]] | No description available. |
| [[src/assets/images/mini-shepherds-pie.webp]] | No description available. |
| [[src/assets/images/muffin-tin-lasagna.webp]] | No description available. |
| [[src/assets/images/spinach-feta-egg-bites.webp]] | No description available. |
| [[src/index.html]] | No description available. |
| [[src/recipes/baked-oatmeal-cups/index.html]] | No description available. |
| [[src/recipes/buffalo-chicken-mac-bites/index.html]] | No description available. |
| [[src/recipes/classic-blueberry-muffins/index.html]] | No description available. |
| [[src/recipes/dark-chocolate-chip-muffins/index.html]] | No description available. |
| [[src/recipes/jumbo-cornbread-bites/index.html]] | No description available. |
| [[src/recipes/mini-meatloaf-bites/index.html]] | No description available. |
| [[src/recipes/mini-pancake-bites/index.html]] | No description available. |
| [[src/recipes/mini-shepherds-pie/index.html]] | No description available. |
| [[src/recipes/muffin-tin-lasagna/index.html]] | No description available. |
| [[src/recipes/spinach-feta-egg-bites/index.html]] | No description available. |
| [[src/recipes.json]] | No description available. |
| [[src/robots.txt]] | No description available. |
| [[src/sitemap.xml]] | No description available. |
| [[src/templates/recipe_page.html]] | No description available. |
| [[src/vercel.json]] | No description available. |

<!-- LIBRARIAN-INDEX-END -->