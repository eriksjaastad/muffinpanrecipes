# Proposal: Infrastructure & Admin System for Muffin Pan Recipes

**Proposed By:** Erik + Claude Opus 4.5 (Super Manager)
**Date:** 2026-01-23
**Target Project:** muffinpanrecipes
**Complexity:** major

---

## EXECUTION MODEL

> **NOT USING AGENT HUB.** Antigravity should use its own agents for implementation.
> We want this done quickly compared to Agent Hub's speed.
> Break work into phases but execute efficiently.

---

## 1. What We're Doing

Building the infrastructure layer that makes the AI Creative Team's output usable: recipe storage, publishing pipeline, admin dashboard, and authentication. The creative agents already work (31 tests passing) - now we need the plumbing to get recipes from "generated" to "live on site."

**Current State:**
- AI agents generate recipes with LLM integration (Margaret/Baker, Marcus/Copywriter)
- Discord notifications work (recipe ready alerts)
- Recipe storage with status tracking implemented today (pending → approved → published)
- Static site exists with 10 recipes, Vercel deployment works via git push

**What's Missing:**
- Publishing pipeline (Recipe JSON → HTML → Vercel)
- Admin dashboard (review/approve/publish UI)
- Authentication (Google OAuth)
- Newsletter system

---

## 2. Erik's Tasks (Do These First)

**IMPORTANT:** Complete these setup tasks before implementation begins. Batch them together.

### E1: Google OAuth Credentials
- [ ] Create Google Cloud project (or use existing)
- [ ] Enable Google OAuth 2.0 API
- [ ] Create OAuth client credentials (Web application type)
- [ ] Set authorized redirect URI: `http://localhost:8000/auth/callback` (dev)
- [ ] Save Client ID and Client Secret to `.env` file:
  ```
  GOOGLE_CLIENT_ID=your-client-id
  GOOGLE_CLIENT_SECRET=your-client-secret
  GOOGLE_AUTHORIZED_EMAILS=erik@youremail.com
  ```

### E2: Newsletter Service Setup
- [ ] Choose email service (Buttondown, ConvertKit, Resend, or custom SMTP)
- [ ] Create account and get API credentials
- [ ] Add to `.env`:
  ```
  NEWSLETTER_SERVICE=buttondown  # or resend, etc.
  NEWSLETTER_API_KEY=your-api-key
  ```

### E3: Verify Discord Webhook
- [ ] Confirm `DISCORD_WEBHOOK_URL` is in `.env` (already done earlier today)

**Note:** Implementation can proceed with Phases 1-2 while Erik completes these tasks. Phase 3+ requires E1, Phase 4 requires E2.

---

## 3. Source Files

**Kiro Specifications (updated today):**
- `.kiro/specs/ai-creative-team/requirements.md` - Requirements 16-22 cover infrastructure
- `.kiro/specs/ai-creative-team/design.md` - Infrastructure component designs
- `.kiro/specs/ai-creative-team/tasks.md` - Tasks 15-22 cover infrastructure

**Existing Code to Build On:**
- `scripts/build_site.py` - Static site generator (incorporate into PublishingPipeline)
- `backend/orchestrator.py` - Recipe production orchestrator
- `backend/data/recipe.py` - Recipe model with status tracking (completed today)
- `backend/utils/discord.py` - Discord webhook notifications

**PRD Reference:**
- `PRD.md` Section 10.5 - Infrastructure requirements
- `PRD.md` Section 3.5 - Security/Auth constraints

---

## 4. Phases

### Phase 1: Publishing Pipeline (Priority)

**Goal:** Approved recipes automatically become live on the site.

**Task 17 from Kiro - Modified:**

Create `backend/publishing/pipeline.py` - a `PublishingPipeline` class that:

1. **Incorporates `scripts/build_site.py` logic:**
   - Load recipe JSON
   - Render HTML from template (`src/templates/recipe_page.html`)
   - Generate JSON-LD structured data
   - Update `src/recipes.json` with new recipe
   - Regenerate `src/sitemap.xml`

2. **New functionality:**
   - `publish_recipe(recipe_id)` - Publish single approved recipe
   - `publish_all_approved()` - Batch publish all approved recipes
   - Update recipe status to `published` after successful generation
   - Git commit + push (triggers Vercel deploy automatically)

3. **Integration:**
   - Connect to `Recipe.transition_status()` from today's A1 work
   - Add Discord notification on successful publish

**Files to create/modify:**
- `backend/publishing/__init__.py` (new)
- `backend/publishing/pipeline.py` (new - main class)
- `backend/publishing/templates.py` (new - template rendering)
- `scripts/build_site.py` → keep as CLI entry point, calls PublishingPipeline

**Acceptance Criteria:**
- [ ] `PublishingPipeline.publish_recipe(recipe_id)` generates HTML and updates status
- [ ] Recipe appears in `src/recipes/{slug}/index.html`
- [ ] `src/recipes.json` updated with new entry
- [ ] `src/sitemap.xml` regenerated
- [ ] Git commit created with recipe info
- [ ] Discord notification sent on publish

---

### Phase 2: Authentication System

**Goal:** Secure admin access with Google OAuth.

**Task 16 from Kiro:**

**Depends on:** Erik's Task E1 (Google OAuth credentials)

Create `backend/auth/`:

1. **`oauth.py`** - Google OAuth 2.0 flow:
   - `get_authorization_url()` - Redirect to Google login
   - `handle_callback(code)` - Exchange code for tokens
   - `verify_token(token)` - Validate Google ID token
   - Check email against whitelist (`GOOGLE_AUTHORIZED_EMAILS`)

2. **`session.py`** - Session management:
   - Create secure session on successful auth
   - Session expiry (24 hours)
   - Session validation middleware

3. **`middleware.py`** - FastAPI dependency:
   - `require_auth` decorator for protected routes
   - Return 401 if not authenticated

**Files to create:**
- `backend/auth/__init__.py`
- `backend/auth/oauth.py`
- `backend/auth/session.py`
- `backend/auth/middleware.py`

**Acceptance Criteria:**
- [ ] Unauthenticated requests to `/admin/*` redirect to Google login
- [ ] Only whitelisted emails can access admin
- [ ] Sessions persist across requests
- [ ] Sessions expire after 24 hours

---

### Phase 3: Admin Dashboard

**Goal:** Web UI to review, approve, and publish recipes.

**Task 16.3 + Task 21 from Kiro:**

**Depends on:** Phase 2 (Authentication)

Create `backend/admin/`:

1. **`app.py`** - FastAPI application:
   ```
   GET  /admin/                    → Dashboard home
   GET  /admin/recipes             → List all recipes by status
   GET  /admin/recipes/{id}        → Recipe detail with preview
   POST /admin/recipes/{id}/approve → Move pending → approved
   POST /admin/recipes/{id}/reject  → Move to rejected with notes
   POST /admin/recipes/{id}/publish → Trigger PublishingPipeline
   GET  /admin/agents              → View agent status/mood
   POST /admin/generate            → Trigger new recipe generation
   ```

2. **`templates/`** - Jinja2 HTML templates:
   - `dashboard.html` - Overview with stats
   - `recipe_list.html` - Table of recipes by status
   - `recipe_detail.html` - Full preview with approve/reject buttons
   - `agents.html` - Agent status panel

3. **Integration:**
   - Use `Recipe.transition_status()` for state changes
   - Use `PublishingPipeline` for publishing
   - Send Discord notifications on actions

**Files to create:**
- `backend/admin/__init__.py`
- `backend/admin/app.py`
- `backend/admin/routes.py`
- `backend/admin/templates/*.html`

**Acceptance Criteria:**
- [ ] Dashboard shows recipe counts by status
- [ ] Can view full recipe preview before approval
- [ ] Approve button moves recipe to approved status
- [ ] Reject button moves recipe to rejected with notes field
- [ ] Publish button triggers pipeline and shows result
- [ ] All actions require authentication

---

### Phase 4: Newsletter System

**Goal:** Capture email signups for future newsletters.

**Task 18 from Kiro:**

**Depends on:** Erik's Task E2 (Newsletter service setup)

1. **`backend/newsletter/manager.py`:**
   - `subscribe(email)` - Validate and store subscription
   - `unsubscribe(token)` - Remove subscription
   - `list_subscribers()` - Admin view of subscribers
   - Integration with chosen service (Buttondown/Resend/etc.)

2. **Frontend form** (add to `src/index.html`):
   - Email input between featured recipe and grid
   - Client-side validation
   - Submit to `/api/newsletter/subscribe`
   - Success/error feedback

**Files to create:**
- `backend/newsletter/__init__.py`
- `backend/newsletter/manager.py`
- Update `src/index.html` with signup form

**Acceptance Criteria:**
- [ ] Email validation rejects invalid formats
- [ ] Duplicate emails handled gracefully
- [ ] Subscriptions stored (JSON file or service)
- [ ] Admin can view subscriber list

---

### Phase 5: Lower Priority / Future Enhancements

**Do these after Phases 1-4 are complete:**

#### 5A: Enhanced Discord Notifications
- Error alerts when pipeline fails
- Weekly summary of recipe activity
- (NOT daily summaries - only 1 recipe/week)

#### 5B: Backup System Improvements
- Currently using rclone for backups
- Add automated verification
- Add retention policy management
- Lower priority since rclone handles basics

#### 5C: Conversation Pipeline Notifications (Future)
- When character conversations are captured throughout the week
- Notify Erik before publishing any conversation content
- This depends on conversation capture system being built (not in current scope)

---

## 5. Recipe State Management - COMPLETE

**Task 15 from Kiro - Already Implemented Today:**

- [x] `RecipeStatus` enum: pending, approved, published, rejected
- [x] `Recipe.status` field with default=pending
- [x] `Recipe.transition_status()` method moves files between directories
- [x] `Recipe.list_by_status()` to query recipes by state
- [x] Directory structure: `data/recipes/{pending,approved,published,rejected}/`
- [x] Orchestrator saves new recipes to `pending/`

**Verification only needed** - Antigravity should run tests to confirm:
```bash
uv run pytest tests/test_integration.py -v
```

---

## 6. Constraints

- **Allowed paths:** `backend/`, `src/` (additive only), `scripts/`, `tests/`
- **Forbidden paths:** `.env`, `vercel.json`, `CLAUDE.md`
- **Deletions allowed:** Only for refactoring (e.g., moving build_site.py logic)
- **Testing:** Use pytest for new functionality
- **Python:** Use `uv run` for all Python execution

**Critical:**
- Existing recipe grid must NOT be broken
- Vercel deployment continues to work via git push
- Discord webhook URL is already configured

---

## 7. Traceability

| PRD Requirement | Kiro Task | Implementation |
|-----------------|-----------|----------------|
| 10.5 Recipe Storage | 15 | COMPLETE (A1) |
| 10.5 Publishing Pipeline | 17 | Phase 1 |
| 10 Authentication | 16 | Phase 2 |
| 10.5 Admin API | 16.3, 21 | Phase 3 |
| 10.5 Newsletter | 18 | Phase 4 |
| 10.5 Notifications | 19 | Phase 5A |
| 10.5 Backup | 20 | Phase 5B (rclone exists) |

---

## 8. Notes for Implementer

**PublishingPipeline Design:**

The existing `scripts/build_site.py` does these things:
1. Loads ALL recipes from `src/recipes.json`
2. Clears and rebuilds entire `src/recipes/` directory
3. Generates HTML from template with placeholder replacement
4. Creates JSON-LD structured data
5. Generates sitemap

The new `PublishingPipeline` class should:
1. Be able to publish a SINGLE recipe (incremental)
2. Also support full rebuild (for consistency)
3. Update `src/recipes.json` incrementally (add new recipe, don't rewrite all)
4. Handle the Recipe model from `backend/data/recipe.py`
5. Keep `scripts/build_site.py` as a CLI wrapper that calls the class

**Authentication Flow:**
```
User visits /admin → Not authenticated → Redirect to Google
Google authenticates → Callback to /auth/callback
Check email in whitelist → Create session → Redirect to /admin
Session cookie sent on subsequent requests
```

**Recipe Status Flow:**
```
Baker generates → pending/
Erik approves via dashboard → approved/
Publish button clicked → PublishingPipeline runs → published/
(or Erik rejects → rejected/)
```

---

**Erik Approval:** ☐ Approved
**Execution:** Antigravity with own agents (NOT Agent Hub)
