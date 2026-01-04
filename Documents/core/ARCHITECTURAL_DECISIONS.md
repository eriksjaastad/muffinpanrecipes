# Architectural Decisions - Muffin Pan Recipes

## AD 001: Static Site Architecture
**Status:** Accepted
**Date:** 2026-01-03
**Context:** We need a high-speed, mobile-first experience that is easy to deploy and maintain on Dreamhost.
**Decision:** We will use a static site approach (HTML/Tailwind) for the initial prototype. AI-generated recipes will be stored as Markdown files and rendered into HTML.
**Consequences:** 
- Extremely fast load times.
- No database management required initially.
- SEO friendly out of the box.

## AD 002: Vercel Deployment via GitHub Integration
**Status:** Accepted
**Date:** 2026-01-03
**Context:** Automated deployment is required to maintain the "Clean Slate" philosophy of rapid iteration.
**Decision:** Use Vercel's native GitHub integration to deploy the project.
**Consequences:** 
- 0-manual-step deployment on every push to `main`.
- Automatic SSL and Global CDN.
- Replaced original Dreamhost SSH/Rsync plan for better speed and developer experience.

## AD 003: "No-Fluff" UI Design
**Status:** Accepted
**Date:** 2026-01-03
**Context:** Recipe sites are notoriously cluttered with ads and long preambles.
**Decision:** The UI will prioritize the "Jump to Recipe" button and put the core ingredients and instructions front and center.
**Consequences:** 
- Higher user satisfaction.
- Better mobile experience.
- Differentiator from traditional food blogs.

## AD 004: Vercel Root Directory Configuration
**Status:** Accepted
**Date:** 2026-01-04
**Context:** To keep the repository clean and separate source code from scripts/data, the `src/` directory is designated as the Vercel Root Directory.
**Decision:** 
- Set Vercel "Root Directory" to `src/` in project settings.
- Move `vercel.json` into the `src/` directory.
- Use path rewrites relative to the `src/` root.
**Consequences:**
- Scripts and raw recipe data are not exposed to the public web.
- Vercel-specific configuration is co-located with the source code.
- Prevents 404 errors caused by incorrect path mapping when the repo root is used as the web root.

---

# Vision & Philosophy: The Vessel & Modular Architecture

## The "Docker Container of Food"
The Muffin Tin is more than a baking tool; it is a **rigid, predictable, modular vessel**. Unlike the "Stick" (kebabs, corn dogs) which represents vertical mobility, the Muffin Pan represents **encapsulation**.

## Core Tenets
1.  **Encapsulation:** Every meal is a self-contained unit. 
2.  **Structural Layering:** The vessel allows for "Vessels within Vessels" (e.g., a bacon strip acting as a basket for an egg).
3.  **Modular Scalability:** If it fits in one cup, it fits in 12. This is "Systematic Meal Prep."
4.  **The Oven-less Frontier:** The vessel is medium-agnostic. It works for the Oven (Baking), the Freezer (Yogurt pucks), and the Fridge (Jello parfaits).

