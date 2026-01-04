# Muffin Pan Recipes — Roadmap

**Last Update:** January 4, 2026
**Status:** 🧁 PHASE 4: THE "VESSEL" EXPANSION (Graduated to Production Ready)

---

## 🔮 Phase 0: Scaffolding & Foundation [completed]
Establish the "Gold Standard" project structure and automated deployment.

- [x] **Infrastructure:** Set up GitHub repository `eriksjaastad/muffinpanrecipes`.
- [x] **Hosting Pivot:** Migrated from Dreamhost to Vercel with domain connected via Namecheap.
- [x] **Deployment Audit:** Verified automated Vercel deployment.
- [x] **Scaffolding:** Created standard directories: `Documents/core/`, `Documents/archives/`, `src/`, `data/`.
- [x] **Architecture:** Drafted `ARCHITECTURAL_DECISIONS.md` and `RECIPE_SCHEMA.md`.
- [x] **Compliance:** Created `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, `.cursorignore`, and `README.md`.

## 🧠 Phase 1: AI Recipe Engine [completed]
Develop the logic for high-volume, niche-specific recipe generation.

- [x] **Prompt Engineering:** Created `Documents/core/SYSTEM_PROMPT_RECIPES.md` for specialized generation.
- [x] **Schema Definition:** Finalized `Documents/core/RECIPE_SCHEMA.md` with Metric/Imperial and mathematical scaling.
- [x] **Initial Harvest:** Generated 10 "Muffin Tin Meals" in `data/recipes/`.

## 🎨 Phase 2: UI/UX & Content [completed]
Build a fast, mobile-first experience for recipe consumers.

- [x] **Design:** Implementation of "No Fluff" UI with editorial aesthetic (Pure White, Serif/Sans pairing).
- [x] **SEO Strategy:** Implemented Schema.org JSON-LD for rich recipe results.
- [x] **Search/Filter:** Added "Filter by Category" functionality.
- [x] **Single Recipe View:** Created high-conversion template with "Gargantuan Jump to Recipe" button.
- [x] **Infrastructure:** Finalize `www` redirect in Vercel (canonicalize to apex).
- [x] **Favicon:** Implement 🧁 cupcake emoji as the initial site favicon for branding.
- [x] **Production Launch:** Deploy the current "No Fluff" frontend to muffinpanrecipes.com with the initial 10 recipes (using placeholder images until the Harvest finishes).

## 📸 Phase 3: The Visual Harvest (Automated Photography) [completed]
High-end AI generation leveraging RunPod (Cloud GPU) and local selection. [PRODUCTION READY]

### 3.1: Cloud Generation (RunPod Tier) [completed]
- [x] **Prompt Batching:** Refactored `generate_image_prompts.py` to use AI Router (DeepSeek-R1). [DONE]
- [x] **Architecture Pivot:** Built `direct_harvest.py` to bypass Blender Buffer errors. [DONE]
- [x] **GPU Execution:** Run `direct_harvest.py` on RunPod to generate the 30 images. [DONE]
- [x] **R2 Sync:** Push finished images from RunPod to Cloudflare R2. [DONE]

### 3.2: Local Processing & Selection [completed]
- [x] **R2 Download:** Use `rclone` to sync the 30 images from R2 to `__temp_harvest/`. [DONE]
- [x] **Orchestration:** Built `trigger_generation.py` and `art_director.py` for pipeline management. [DONE]
- [x] **AI Selection (Art Director):** Run `art_director.py` to pick the single winner for each of the 10 recipes based on `IMAGE_STYLE_GUIDE.md`. [DONE]
- [x] **Integration:** Automate placement of winners into `src/assets/images/` and cleanup temp files. [DONE]
- [x] **Industrialization:** Fixed all V1-V3 audit issues (paths, timeouts, error handling). [DONE]

### 3.3: Static Generation & Social Routing (The Content Moat) [completed]
Refactoring the site from a single-page modal system to a high-authority static engine.

- [x] **Blueprint Creation:** Extract the modal UI from `index.html` into a standalone `src/templates/recipe_page.html` with Jinja2-style placeholders (e.g., `{{ title }}`, `{{ ingredients_list }}`). [DONE]
- [x] **The "Baker" Script:** Build `scripts/build_site.py` to iterate through `recipes.json` and output unique HTML folders for every recipe (e.g., `src/recipes/[slug]/index.html`). [DONE]
- [x] **Metadata Automation:** Ensure the build script generates recipe-specific Open Graph tags (`og:image`, `og:title`, `og:description`) for Pinterest/Social richness. [DONE]
- [x] **Hybrid Handshake:** Update `index.html` grid to use direct links (`<a>` tags) while maintaining the "No Fluff" instant-load feel. [DONE]
- [x] **Sitemap Generation:** Add a task to the build script to generate a `sitemap.xml` for Google Search Console to index all 10+ recipes. [DONE]
- [x] **Vercel Routing:** Implement a `src/vercel.json` rewrite map to support clean URLs (e.g., `/recipes/spinach-bites` → `/recipes/spinach-bites/index.html`). [DONE]
- [x] **SSG Hardening:** Implemented HTML escaping, canonical links, robots.txt, and Article metadata for peak SEO authority. [DONE]
- [x] **Master Archive & Shield:** Optimized `optimize_images.py` with idempotent logic and safe archival (`data/image_archive/`). Implemented `.gitignore` "Vercel Shield" for production safety. [DONE]
- [x] **Done Criteria:**
    - [x] Every recipe has a unique, navigable URL (e.g., `/recipes/dark-chocolate-chip-muffins`).
    - [x] Sharing a recipe link on social media correctly displays the specific recipe image and description.
    - [x] The "Back" button works natively in the browser when navigating between the grid and a recipe.
    - [x] Google Search Console can find the `sitemap.xml` containing all recipe paths.
    - [x] Site is reachable by robots (verified `robots.txt` and `sitemap.xml` accessibility).

### 3.4: Editorial Command Center (Local Admin) [planning]
Building the "Control Tower" for the AI Editorial Board and high-volume scaling.

- [ ] **Admin Interface:** Build a local web UI (FastAPI/Tailwind) to manage the `recipes.json` data layer.
- [ ] **Persona Portal:** A "Live Feed" view to monitor the debates and "Grumpy Reviews" from the Team of 5.
- [ ] **Mission Control Integration:** A dashboard button to trigger the `trigger_generation.py` workflow for new batches.
- [ ] **Staging Viewer:** A local "Draft" mode to preview new recipes with their AI photography before Vercel deployment.

### 3.5: Remote Orchestration (Podrunner) [planning]
Automating the infrastructure lifecycle for high-volume visual harvests.

- [ ] **Remote Lifecycle:** Research and implement remote pod spin-up via Podrunner or RunPod CLI.
- [ ] **Automated Handshake:** Script the deployment of `direct_harvest.py` immediately after pod activation.
- [ ] **Auto-Termination:** Ensure the pod shuts down automatically from the console once R2 sync is verified.

## 🧁 Phase 4: The "Vessel" Expansion (Brainstorming) [planning]
Expanding beyond the oven into modular, systematic food prep.

- [ ] **Oven-less Category:** Frozen Yogurt Bites, smoothie pucks, chilled parfaits.
- [ ] **Performance/Gym Category:** High-protein "Systematic Prep" (e.g., Egg-white & Oat pods).
- [ ] **Modular "Base Layers":** Bacon baskets, Tortilla cups, Prosciutto liners.

## 📱 Phase 5: Social Presence & Social Dispatcher [planning]
Strategy for high-volume Pinterest/Instagram growth.

- [ ] **Pinterest Kickoff:** Build script for vertical Pinterest "Recipe Cards" using AI images.
- [ ] **Skill Acquisition:** Codify "Social Dispatcher" logic in `agent-skills-library`.
- [ ] **Instagram Growth:** Automate Instagram postings via Meta Graph API focusing on "Modular Architecture" visuals.

## 🌟 Phase 5: Long-Term Vision
Future growth and monetization ideas.

- [ ] **Community & Interaction:**
    - [ ] **The "Personality" Responder:** Humor-driven "Muffin Pan Mascot" agent for comment responses.
- [ ] **Merch Store (The Bakery Shop):**
    - [ ] Muffin-themed kitchenware (custom tins, aprons).
    - [ ] "I didn't use the oven" t-shirts.
    - [ ] AI-generated "Zine" of the month.
- [ ] **Advanced Features:**
    - [ ] User-submitted recipe photos (with AI quality check).
    - [ ] Meal planning "Muffin Tin" subscription.

## 🎬 Phase 6: The Moonshot (Video)
High-budget, high-impact motion content.

- [ ] **Short-Form Build Videos:** 15-second TikTok/YouTube Shorts showing the "Docker for Food" build process (Empty Tin -> Base Layer -> Filling -> Finished Product).
- [ ] **Cinematic Steam:** High-res video of sunrise lighting and rising steam.

## 🎭 Phase 7: Editorial Personalities & Artistic Tension [in_progress]
Implementing the "Team of 5" hierarchy to ensure high-end, self-correcting quality.

- [x] **Identity Definition:** Created `Documents/core/PERSONAS.md` defining the 5 roles (Creative Director, Art Director, Copywriter, Site Architect, Social Dispatcher). [DONE]
- [ ] **The "Grumpy Review" Protocol:** Build a prompt/script for the **Creative Director** (DeepSeek-R1) to audit the full "Recipe + Image" package before deployment.
- [x] **The "Art Director" Selection Script:** Finalize `scripts/art_director.py` to compare the 3 variants per recipe and pick the "High-Key" winner. [DONE]
- [ ] **The Social Mascot:** Codify the voice of the **Social Dispatcher** for automated Pinterest/Instagram descriptions.
- [ ] **Multi-Agent Debate:** Implement a "Design Review" where the Site Architect and Art Director must agree on the hero image placement.

---

## ✅ Completed
- [x] Project Index created (`00_Index_MuffinPanRecipes.md`).
- [x] Initial roadmap drafted.
- [x] Phase 0-2 fully executed.
- [x] Production deployment established.
