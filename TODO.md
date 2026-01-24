# Muffin Pan Recipes — Roadmap

**Last Update:** January 23, 2026
**Status:** Infrastructure Complete - Needs Erik's OAuth Setup

---

## 🔴 BLOCKING: Erik's Setup Tasks

These are blocking the admin dashboard from going live:

- [ ] **E1: Google OAuth** - Add to `.env`:
  ```
  GOOGLE_CLIENT_ID=your-client-id
  GOOGLE_CLIENT_SECRET=your-client-secret
  GOOGLE_AUTHORIZED_EMAILS=erik@youremail.com
  ```
- [ ] **E2: Newsletter API** - Add to `.env`:
  ```
  NEWSLETTER_SERVICE=buttondown
  NEWSLETTER_API_KEY=your-api-key
  ```
- [x] **E3: Discord Webhook** - Already complete ✅

**Once E1 is done, run:** `uv run uvicorn backend.admin.app:create_admin_app --factory --reload --port 8000`

---

## 📋 OPEN TASKS

### Email System
- [ ] **Set up email address** - hello@muffinpanrecipes.com or similar

### Story/Behind-the-Scenes
- [ ] **Story page template** - Show creative process
- [ ] **Story preview on recipe pages** - Link to full story

### Editorial Command Center (remaining)
- [ ] **Persona Portal** - Live feed to monitor debates and "Grumpy Reviews" from the Team of 5
- [ ] **Mission Control Integration** - Dashboard button to trigger `trigger_generation.py` for new batches

### Image Generation Pipeline
- [ ] Connect new recipe generation to image pipeline (currently manual)

---

## 🟡 NEXT PRIORITIES

### Phase 7: Editorial Personalities (in progress)
- [ ] **Screenwriter Implementation** - Capture "Creative Tension" logs between roles
- [ ] **Grumpy Review Protocol** - Creative Director audits full "Recipe + Image" package
- [ ] **Social Mascot** - Codify voice of Social Dispatcher for Pinterest/Instagram
- [ ] **Multi-Agent Debate** - Site Architect and Art Director agree on hero image

### Phase 8: Editorial Cadence (The Weekly Rhythm)
- [ ] **Dialogue Feed** - "Behind the Scenes" section on recipe pages
- [ ] **Editorial Calendar** - Codify "Sunday 8:00 AM" launch cycle
- [ ] **Persona Commentary** - Editorial notes from Creative Director/Art Director in recipe schema

---

## 🔵 FUTURE PHASES

### Remote Orchestration (Podrunner)
- [ ] Remote pod spin-up via Podrunner or RunPod CLI
- [ ] Auto-deploy `direct_harvest.py` after pod activation
- [ ] Auto-terminate pod after R2 sync verified

### The "Vessel" Expansion
- [ ] Oven-less Category (Frozen Yogurt Bites, smoothie pucks)
- [ ] Performance/Gym Category (High-protein prep)
- [ ] Modular Base Layers (Bacon baskets, Tortilla cups)

### Social Presence
- [ ] Pinterest vertical "Recipe Cards"
- [ ] Instagram automation via Meta Graph API
- [ ] "Social Dispatcher" skill in agent-skills-library

### Long-Term Vision
- [ ] The "Personality" Responder (Muffin Pan Mascot)
- [ ] Merch Store (The Bakery Shop)
- [ ] User-submitted recipe photos
- [ ] Meal planning subscription

### Video (Moonshot)
- [ ] Short-form TikTok/YouTube Shorts
- [ ] Cinematic steam videos

---

## 📈 Pipeline Status

| Stage | Agent | Status |
|-------|-------|--------|
| 1. Recipe Development | Margaret (Baker) | ✅ Working |
| 2. Photography | Julian (Art Director) | ⚠️ Manual |
| 3. Copywriting | Marcus (Copywriter) | ✅ Working |
| 4. Creative Review | Steph (Creative Director) | ⚠️ Placeholder |
| 5. Human Review | Erik | ✅ Ready (needs OAuth) |
| 6. Deployment | Devon (Site Architect) | ✅ Connected |
| 7. Social Distribution | TBD | ❌ Not started |

---

## 📚 Reference

```bash
# Start admin dashboard
uv run uvicorn backend.admin.app:create_admin_app --factory --reload --port 8000

# Run tests
uv run pytest tests/ -v
```

- Backend: `backend/` - admin/, auth/, newsletter/, publishing/, agents/
- Kiro specs: `.kiro/specs/ai-creative-team/`

---

## ✅ COMPLETED

<details>
<summary>Phase A: Infrastructure (Jan 23, 2026) - Click to expand</summary>

*Implemented by Antigravity in ~15 minutes*

### A1: Recipe Storage
- [x] Save recipes to JSON - `data/recipes/pending/{RECIPE_ID}.json`
- [x] Recipe status tracking - RecipeStatus enum
- [x] Status transitions - moves files between directories
- [x] Include story data

### A2: Publishing Pipeline
- [x] Recipe → HTML generator - `backend/publishing/pipeline.py` (468 lines)
- [x] Template rendering with JSON-LD for SEO
- [x] Git commit + push triggers Vercel automatically
- [x] Sitemap regeneration on publish

### A3: Admin Dashboard
- [x] FastAPI app with `/admin` routes
- [x] Recipe list, detail, preview
- [x] Approve/Reject/Publish buttons
- [x] Tailwind UI

### A4: Auth System
- [x] Google OAuth 2.0 - `backend/auth/oauth.py`
- [x] Session management with 24-hour expiry
- [x] Email whitelist
- [x] FastAPI middleware

### A5: Newsletter
- [x] Newsletter backend - `backend/newsletter/manager.py`
- [x] Buttondown integration
- [x] Email validation
- [x] Subscribe endpoint

</details>

<details>
<summary>Phase B1: Homepage Updates - Click to expand</summary>

- [x] Recipe of the week section
- [x] Newsletter signup form (backend at `POST /api/newsletter/subscribe`)

</details>

<details>
<summary>Phases 0-3: Foundation & Visual Harvest - Click to expand</summary>

### Phase 0: Scaffolding
- [x] GitHub repo, Vercel deployment, standard directories
- [x] AGENTS.md, CLAUDE.md, .cursorrules

### Phase 1: AI Recipe Engine
- [x] System prompt for specialized generation
- [x] Recipe schema with Metric/Imperial
- [x] Initial 10 recipes generated

### Phase 2: UI/UX
- [x] "No Fluff" UI with editorial aesthetic
- [x] Schema.org JSON-LD for SEO
- [x] Category filtering, mobile-responsive
- [x] "Jump to Recipe" button

### Phase 3: Visual Harvest
- [x] Cloud generation on RunPod
- [x] Art Director selection script
- [x] 10 recipes with real images
- [x] SSG with sitemap, robots.txt, canonical links

</details>

---

## 💭 Erik's Vision (Jan 4, 2026)

> My goal is to make muffinpanrecipes super entertaining and have the AI personalities like that's the real content but we're doing it through a recipe website.

Three tiers:
1. **Worker agents** (creative director, photographer, etc.) — produce content
2. **Erik + AI partner** — review and tune the worker agents periodically
3. **The environment itself** — memory formats, learning materials, control parameters

> If the format works, you've essentially built a reality show where the cast improves over time and the audience watches it happen through the lens of muffin pan recipes. That's weird and original enough to stand out.
