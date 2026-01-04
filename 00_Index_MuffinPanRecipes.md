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
- [[Documents/core/ARCHITECTURAL_DECISIONS]]
- [[Documents/core/RECIPE_SCHEMA]]
- [[Documents/core/IMAGE_STYLE_GUIDE]]
