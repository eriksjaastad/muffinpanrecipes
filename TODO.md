# Muffin Pan Recipes — Roadmap

**Last Update:** January 23, 2026
**Status:** 🧁 PHASE 4: THE "VESSEL" EXPANSION (Graduated to Production Ready)

---

Erik's notes 1/4/2026: My goal is to make muffinpanrecipes super entertaining and have the AI personalities like that's the real content but we're doing it through a recipe website.
I think on a weekly or monthly basis, me and another AI look at whatever control parameters we have for our worker agents that are the creative director and the photographer in those, and we try and see if we can do anything to help them progress. Like, is our format for memories working? Can we inspire them by having them study something? So, coming up with a continual learning where we try and improve the environment for them to learn in.

The meta-learning layer is where it gets interesting. You're describing three tiers:

Worker agents (creative director, photographer, etc.) — produce content
You + an AI partner — review and tune the worker agents periodically
The environment itself — memory formats, learning materials, control parameters

Building a curriculum. "Is our memory format helping them retain useful patterns? Should we have the photographer study food photography from a specific era? Does the creative director need exposure to different design philosophies?"

If the format works, you've essentially built a reality show where the cast improves over time and the audience watches it happen through the lens of muffin pan recipes. That's weird and original enough to stand out.

---

## 🚀 CURRENT SESSION: AI Creative Team (January 23, 2026)

**Status:** LLM integration complete! Margaret (baker) and Marcus (copywriter) now generate real content. Discord notifications working. **BUT** recipes have nowhere to go yet.

### ✅ COMPLETED THIS SESSION
- [x] **Proof of Life** - Recipes generate with real ingredients and instructions
- [x] **LLM Integration (Margaret)** - Baker generates unique muffin tin recipes via Ollama
- [x] **LLM Integration (Marcus)** - Copywriter writes 2000+ char literary descriptions
- [x] **Discord Notifications** - Webhook sends recipe alerts when ready for review
- [x] **Test Suite Updated** - 31 tests passing with realistic recipe inputs
- [x] **Recipe Storage (A1)** - Recipes persist to JSON with status tracking (pending→approved→published)

---

## 📊 HONEST INVENTORY: What We Have vs Don't Have

### ✅ What EXISTS
| Component | Status | Notes |
|-----------|--------|-------|
| Static site | ✅ Live | 10 recipes on muffinpanrecipes.com |
| Homepage + grid | ✅ Works | Category filtering, mobile-responsive |
| Recipe pages | ✅ Works | 10 static HTML pages (Jan 4 content) |
| Vercel deployment | ✅ Live | Auto-deploy from GitHub |
| LLM recipe generation | ✅ Works | Margaret generates real recipes |
| LLM descriptions | ✅ Works | Marcus writes 2000+ char literary descriptions |
| Discord notifications | ✅ Works | Alerts when recipes are generated |
| Recipe storage | ✅ Works | JSON files in data/recipes/{pending,approved,published}/ |
| Test suite | ✅ Passing | 31 tests |

### ❌ What DOESN'T EXIST
| Gap | Impact | Priority |
|-----|--------|----------|
| **Recipe → HTML pipeline** | Can't publish new recipes to site | 🔴 Critical |
| **Admin dashboard** | No UI to manage anything | 🔴 Critical |
| **Google OAuth** | No authentication | 🟡 High |
| **Review URLs** | Can't preview before publish | 🟡 High |
| **Recipe of the week** | No featured content | 🟡 High |
| **Newsletter signup** | Can't capture audience | 🟡 High |
| **Email address/system** | No email for muffinpanrecipes.com | 🟡 High |
| **Story/BTS pages** | AI personalities have no public presence | 🔵 Medium |
| **Image generation** | Using placeholders (Phase 3 done but not connected) | 🔵 Medium |

### 📈 Pipeline Stage Status

| Stage | Agent | Status | Notes |
|-------|-------|--------|-------|
| 1. Recipe Development | Margaret (Baker) | ✅ Working | LLM generates real recipes |
| 2. Photography | Julian (Art Director) | ⚠️ Placeholder | No image gen connected |
| 3. Copywriting | Marcus (Copywriter) | ✅ Working | LLM writes 2000+ char descriptions |
| 4. Creative Review | Steph (Creative Director) | ⚠️ Placeholder | Always approves (no real review) |
| 5. Human Review | Erik | ❌ Missing | No admin dashboard |
| 6. Deployment | Devon (Site Architect) | ⚠️ Disconnected | build_site.py exists, not in pipeline |
| 7. Social Distribution | TBD | ❌ Missing | Not started |
| Revision Loop | — | ❌ Missing | No implementation |
| Screenwriter | — | ⚠️ Partial | Moments captured, no agent |

**See full workflow:** [Documents/WORKFLOW_DIAGRAM.md](Documents/WORKFLOW_DIAGRAM.md)

---

## 🔴 PHASE A: Make Generated Recipes Usable (CURRENT PRIORITY)

### A1: Recipe Storage ✅ COMPLETE
- [x] **Save recipes to JSON** - Persist to `data/recipes/pending/{recipe_id}.json`
- [x] **Recipe status tracking** - RecipeStatus enum (pending → approved → published → rejected)
- [x] **Status transitions** - `recipe.transition_status()` moves files between directories
- [x] **Include story data** - CreationStory saved alongside recipe

### A2: Recipe Publishing Pipeline → **LIGHT PATH**
> Existing `build_site.py` + <3 files = Light Path per Project-workflow.md

- [ ] **Recipe → HTML generator** - Extend `build_site.py` to handle new recipes
- [ ] **Image placeholder handling** - Use placeholder until real image exists
- [ ] **Deployment trigger** - Push approved recipes to Vercel
- [ ] **Status update** - Mark recipe as `published` after deploy

### A3 + A4: Admin Dashboard + Auth → **NEEDS KIRO SPECS**
> Substantial new work (FastAPI app, multiple routes, auth) = Full Workflow

**Action Required:** Create new Kiro spec for admin-dashboard feature before implementation.
- PRD Section 10.5 has intent (API endpoints, auth requirements)
- Kiro will generate: EARS requirements, design, tasks

Planned scope:
- [ ] **Google OAuth** - Login with Google account
- [ ] **FastAPI app** - `/admin` routes with protected access
- [ ] **Recipe list view** - See all pending/approved/published
- [ ] **Recipe detail/preview** - Full preview before approval
- [ ] **Approve/Reject/Publish** - Move recipes through pipeline

---

## 🟡 PHASE B: Frontend Enhancements

### B1: Homepage Updates
- [ ] **Recipe of the week section** - Featured content above grid
- [ ] **Newsletter signup form** - Email capture UI

### B2: Email System
- [ ] **Set up email address** - hello@muffinpanrecipes.com or similar
- [ ] **Newsletter backend** - Store signups, send emails

### B3: Story/Behind-the-Scenes
- [ ] **Story page template** - Show creative process
- [ ] **Story preview on recipe pages** - Link to full story

---

## 🔵 PHASE C: Configuration & Management

- [ ] Agent configuration viewer/editor
- [ ] Pipeline monitoring dashboard
- [ ] Agent performance metrics

---

### Reference
- Kiro specs: `.kiro/specs/ai-creative-team/`
- Proposal: `_handoff/PROPOSAL_FINAL.md`
- Backend: `backend/` (orchestrator.py is the main entry point)

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

- [ ] **Tech Debt (Flat Root):** Move contents of `Documents/core/` to `Documents/` root and delete the core directory.
High-budget, high-impact motion content.

- [ ] **Short-Form Build Videos:** 15-second TikTok/YouTube Shorts showing the "Docker for Food" build process (Empty Tin -> Base Layer -> Filling -> Finished Product).
- [ ] **Cinematic Steam:** High-res video of sunrise lighting and rising steam.

## 🎭 Phase 7: Editorial Personalities & Artistic Tension [in_progress]
Implementing the "Team of 5" hierarchy to ensure high-end, self-correcting quality.

- [x] **Identity Definition:** Created `Documents/core/PERSONAS.md` defining the 5 roles (Creative Director, Art Director, Copywriter, Site Architect, Social Dispatcher). [DONE]
- [ ] **The "Screenwriter" Implementation:** Draft the logic for the Screenwriter to capture "Creative Tension" logs between roles.
- [ ] **The "Grumpy Review" Protocol:** Build a prompt/script for the **Creative Director** (DeepSeek-R1) to audit the full "Recipe + Image" package before deployment.
- [x] **The "Art Director" Selection Script:** Finalize `scripts/art_director.py` to compare the 3 variants per recipe and pick the "High-Key" winner. [DONE]
- [ ] **The Social Mascot:** Codify the voice of the **Social Dispatcher** for automated Pinterest/Instagram descriptions.
- [ ] **Multi-Agent Debate:** Implement a "Design Review" where the Site Architect and Art Director must agree on the hero image placement.

## 🎭 Phase 8: Editorial Cadence & Artistic Tension (The Weekly Rhythm)

- [ ] **Dialogue Feed:** Implement a "Behind the Scenes" section on recipe pages surfacing the Screenwriter's dialogue logs.
- [ ] **Editorial Calendar:** Codify the "Sunday 8:00 AM" launch cycle for new recipes.
- [ ] **Persona Commentary:** Update the recipe schema to include "Editorial Notes" from the Creative Director or Art Director.
    - [ ] **Creative Director (DeepSeek-R1):** Sophisticated critique of the recipe's architectural integrity.
    - [ ] **Art Director (GPT-4o):** Discerning notes on the "Triple-Plate" photography choices.
- [ ] **Video Pilot (Wednesday Cycle):** Initial planning for the "Cinematic Build" video release.
    - [ ] **The Video Director:** Define a new persona focused on rhythm, steam, and lighting.
    - [ ] **Director's Cut:** Include a "Director's Statement" with each video describing the artistic intent.
- [ ] **Ingredient & Step Audit:** Refactor the generator to break the "4 ingredients / 6 steps" pattern to ensure authentic complexity for diverse recipes.

## 🧪 Content Brainstorming: The "Vessel" Expansion
- [ ] **The "Docker for Food" Vision:** Further develop the concept of the muffin tin as a "Vessel" for modular, systematic food prep.

---

## ✅ Completed
- [x] Project Index created (`00_Index_MuffinPanRecipes.md`).
- [x] Initial roadmap drafted.
- [x] Phase 0-2 fully executed.
- [x] Production deployment established.


<!-- project-scaffolding template appended -->

# {{PROJECT_NAME}} - TODO

**Last Updated:** {{DATE}}  
**Project Status:** {{STATUS}} (In Progress/Active/Development/Paused/Stalled/Complete)  
**Current Phase:** {{PHASE}} (Foundation/MVP/Production/etc.)

---

## 📍 Current State

### What's Working ✅
<!-- List what's operational and tested -->
- **Feature 1:** Brief description of what works
- **Feature 2:** Another working component
- **Automation:** Any scheduled jobs or automated processes

### What's Missing ❌
<!-- Honest assessment of gaps -->
- **Feature X:** Not implemented yet
- **Integration Y:** Needs setup
- **Documentation Z:** Incomplete

### Blockers & Dependencies
<!-- What's stopping progress? -->
- ⛔ **Blocker:** Clear description of what blocks progress
- 🔗 **Dependency:** External service, API key, or approval needed
- ⏳ **Waiting:** What you're waiting for

---

## ✅ Completed Tasks

### Phase {{PHASE_NUMBER}}: {{PHASE_NAME}} ({{DATE_RANGE}})
- [x] Task description with clear outcome
- [x] Another completed task
- [x] Task that was finished

### Phase {{PREVIOUS_PHASE}}: {{PHASE_NAME}} ({{DATE_RANGE}})
- [x] Historical completed task
- [x] Another past milestone

---

## 📋 Pending Tasks

### Phase 0: Industrial Hardening (Gate 0)
- [ ] **Dependency Pinning:** Replace `>=` with `~=` or `==` in `requirements.txt`.
- [ ] **DNA Check:** Verify zero machine-specific absolute paths remain in codebase.
- [ ] **Error Audit:** Replace `except: pass` with explicit logging.
- [ ] **Subprocess Audit:** Ensure all CLI calls have `check=True` and `timeout`.

### 🔴 CRITICAL - Must Do First
<!-- High-priority, blocking other work -->

#### Task Group 1: {{TASK_GROUP_NAME}}
- [ ] Specific actionable task
- [ ] Another task with clear success criteria
- [ ] Task that depends on previous tasks

#### Task Group 2: {{TASK_GROUP_NAME}}
- [ ] Task description
  - [ ] Sub-task (if needed)
  - [ ] Another sub-task

---

### 🟡 HIGH PRIORITY - Important
<!-- Important but not blocking -->

#### Task Group 3: {{TASK_GROUP_NAME}}
- [ ] High-value task
- [ ] Another important task

---

### 🔵 MEDIUM PRIORITY - Nice to Have
<!-- Useful but can wait -->

#### Task Group 4: {{TASK_GROUP_NAME}}
- [ ] Enhancement or improvement
- [ ] Optional feature

---

### 🟢 LOW PRIORITY - Future
<!-- Backlog items, not urgent -->

#### Task Group 5: {{TASK_GROUP_NAME}}
- [ ] Long-term idea
- [ ] Nice-to-have feature

---

## 🎯 Success Criteria

### {{PHASE}} Complete When:
- [ ] Clear, measurable criterion
- [ ] Another specific goal
- [ ] Outcome that defines "done"

### Project Complete When:
- [ ] Final outcome achieved
- [ ] All core features working
- [ ] Documentation complete

---

## 📊 Notes

### AI Agents in Use
<!-- Which AI is helping with what? NEW SECTION -->
- **{{AI_NAME}} ({{MODEL}}):** Role description (e.g., "Implementation", "Code Review", "Architecture")
- **{{AI_NAME}}:** Another AI agent and its role

### Cron Jobs / Automation
<!-- Scheduled tasks for this project -->
- **Schedule:** `{{CRON_EXPRESSION}}` (e.g., "0 14 * * *" = daily 2 PM)
- **Command:** `{{COMMAND}}`
- **Purpose:** What it does
- **Status:** Active/Inactive

### External Services Used
<!-- From project-scaffolding/EXTERNAL_RESOURCES.md -->
- **{{SERVICE_NAME}}:** Purpose, cost
- **{{SERVICE_NAME}}:** Another service

### Cost Estimates
<!-- If applicable -->
- **Development:** Estimated time or cost
- **Monthly:** Recurring costs (API, hosting, etc.)
- **One-time:** Setup or infrastructure costs

### Time Estimates
<!-- Rough guidance -->
- **{{PHASE}}:** X-Y hours
- **Total project:** X-Y hours/weeks
- **Next milestone:** X hours

### Related Projects & Documentation
<!-- Links to other relevant projects or docs -->
- **{{PROJECT_NAME}}:** How it relates
- **{{DOC_PATH}}:** Important reference document

### Technical Stack
<!-- Key technologies -->
- **Language:** Python 3.11+ / JavaScript / etc.
- **Framework:** FastAPI / React / etc.
- **Database:** SQLite / PostgreSQL / etc.
- **Deployment:** Railway / Local / etc.

### Key Decisions Made
<!-- Important choices for future reference -->
1. **Decision:** Rationale and date
2. **Decision:** Another key choice

### Open Questions
<!-- Unresolved items needing discussion -->
- ❓ Question that needs answering
- ❓ Choice that needs to be made

---

## 🔄 Change Log (Optional)

### {{DATE}} - {{PHASE_NAME}}
- Major milestone or significant change
- Another important update

### {{PREVIOUS_DATE}} - {{PREVIOUS_PHASE}}
- Historical change
- Past update

---

<!-- 
=============================================================================
GUIDANCE FOR AI SESSIONS:
=============================================================================

This TODO is designed to be both HUMAN and AI readable.

When updating this file:
1. Always update "Last Updated" date at the top
2. Move completed tasks from Pending → Completed (keep the checkbox [x])
3. Add dates to completed phases
4. Update "Current State" section as project evolves
5. Keep Blockers section honest and current
6. Mark tasks as [x] when done, don't delete them (shows progress)
7. Update Success Criteria as understanding improves
8. Keep Notes section current (costs, time, related projects)

When reading this file at session start:
1. Read "Current State" first (understand where things are)
2. Check "Blockers & Dependencies" (know what's stopping progress)
3. Review "Pending Tasks" (understand what's next)
4. Check "Success Criteria" (know what "done" looks like)
5. Scan "Notes" for context (costs, related projects, decisions)

Priority Emojis:
- 🔴 CRITICAL: Must do first, blocking other work
- 🟡 HIGH: Important but not blocking
- 🔵 MEDIUM: Nice to have, can wait
- 🟢 LOW: Backlog, future consideration

Task Status:
- [ ] Not started
- [x] Completed (never delete, shows progress!)

Formatting:
- Use clear hierarchy (Phase → Task Group → Task → Sub-task)
- Keep task descriptions actionable ("Create X", not "X needs creating")
- Include enough context for a new AI session to understand

Meta-Philosophy:
- This is a living document
- Honest assessment > optimistic projection
- Show progress (keep completed tasks)
- Context for future you/AI (notes, decisions, questions)

=============================================================================
-->

---

*Template Version: 1.0*  
*Last Modified: December 30, 2025*  
*Source: ./templates/TODO.md.template*


<!-- project-scaffolding template appended -->

## Related Documentation

- [[CODE_REVIEW_ANTI_PATTERNS]] - code review
- [[DOPPLER_SECRETS_MANAGEMENT]] - secrets management
- [[LOCAL_MODEL_LEARNINGS]] - local AI
- [[PROJECT_KICKOFF_GUIDE]] - project setup
- [[PROJECT_STRUCTURE_STANDARDS]] - project structure
- [[architecture_patterns]] - architecture
- [[automation_patterns]] - automation
- [[cloud_gpu_setup]] - cloud GPU
- [[cost_management]] - cost management
- [[dashboard_architecture]] - dashboard/UI
- [[database_setup]] - database
- [[error_handling_patterns]] - error handling
- [[prompt_engineering_guide]] - prompt engineering
- [[queue_processing_guide]] - queue/workflow
- [[adult_business_compliance]] - adult industry
- [[ai_model_comparison]] - AI models
- [[deployment_patterns]] - deployment
- [[orchestration_patterns]] - orchestration
- [[performance_optimization]] - performance
- [[project_planning]] - planning/roadmap
- [[recipe_system]] - recipe generation
- [[research_methodology]] - research
- [[security_patterns]] - security
- [[video_analysis_tools]] - video analysis
- [[agent-skills-library/README]] - Agent Skills
- [[analyze-youtube-videos/README]] - YouTube Analyzer
- [[muffinpanrecipes/README]] - Muffin Pan Recipes
- [[project-scaffolding/README]] - Project Scaffolding
