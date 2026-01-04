# Muffin Pan Recipes — Roadmap

**Last Update:** January 3, 2026
**Status:** 🔮 KICKOFF (Phase 0)

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

## 🚀 Phase 3: Tomorrow's Launch & Iteration [in_progress]
The immediate mission for the next session.

### 📸 Phase 3.1: The Visual Harvest (Automated Photography)
- [ ] **Infrastructure:** Finalize `www` redirect in Vercel (canonicalize to apex).
- [ ] **Prompt Batching:** Run `python3 scripts/generate_image_prompts.py` for all 10 recipes.
- [ ] **Generation Pipeline:** Trigger "Mission Control" (`3D Pose Factory`) using the "Triple-Plate" pattern (3 variants per recipe).
- [ ] **AI Selection:** Use the "Art Director" agent to select winners based on the `IMAGE_STYLE_GUIDE.md`.
- [ ] **Integration:** Automate placement of winners into `src/assets/images/`.

### 🧁 Phase 3.2: Social Presence (Initial)
- [ ] **Favicon:** Generate high-end 🧁 icon for branding.
- [ ] **Pinterest Kickoff:** Build script for vertical Pinterest "Recipe Cards" using AI images.
- [ ] **Skill Acquisition:** Codify "Social Dispatcher" logic in `agent-skills-library`.

## 🏃 Phase 4: Stretch Goals (Coming Soon)
Items to tackle once the visual pipeline is stable.

- [ ] **Static Admin Utility:** Create `scripts/admin_tool.py` for one-click recipe/image management.
- [ ] **Agent Recommendation Loop:** Implement mechanism for AI agents to suggest style guide or schema updates.
- [ ] **The "Idea Basket":** Create `Documents/archives/IDEA_BASKET.md` where agents can dump creative sparks during production.
- [ ] **Content Scaling:** Generate the next batch of 50 recipes.
- [ ] **💡 Content Brainstorming: The "Vessel" Expansion:**
    - [ ] **Oven-less Category:** Frozen Yogurt Bites, smoothie pucks, chilled parfaits.
    - [ ] **Performance/Gym Category:** High-protein "Systematic Prep" (e.g., Egg-white & Oat pods).
    - [ ] **Modular "Base Layers":** Bacon baskets, Tortilla cups, Prosciutto liners.
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

---

## ✅ Completed
- [x] Project Index created (`00_Index_MuffinPanRecipes.md`).
- [x] Initial roadmap drafted.
- [x] Phase 0-2 fully executed.
