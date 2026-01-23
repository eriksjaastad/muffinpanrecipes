# Proposal: AI Creative Team System for Muffin Pan Recipes

**Proposed By:** Erik + Claude Opus 4.5 (Super Manager)
**Date:** 2026-01-22
**Target Project:** muffinpanrecipes
**Complexity:** major

---

## 1. What We're Doing

Building a multi-agent AI orchestration system where five AI personalities (Baker, Creative Director, Art Director, Editorial Copywriter, Site Architect) collaborate to produce muffin tin recipes. Each agent has a distinct, consistent personality that influences their work style, communication, and creative decisions. The system captures their interactions as entertainment content while producing actual recipes for the website.

## 2. Why It Matters

The website currently has static recipe content. This system transforms recipe production into an ongoing entertainment experience where readers can follow the creative team's workplace dynamics, conflicts, and collaborations. It creates differentiated content that competitors can't replicate - the recipes come with stories.

## 3. Source Files

**Kiro Specifications (primary source of truth):**
- `.kiro/specs/ai-creative-team/requirements.md` - 15 detailed requirements
- `.kiro/specs/ai-creative-team/design.md` - Python architecture with Agent classes, MessageSystem, Pipeline
- `.kiro/specs/ai-creative-team/tasks.md` - 14 implementation tasks with subtasks

**Existing Project Files:**
- `src/index.html` - Current website (DO NOT break existing recipe grid)
- `data/recipes/` - Existing recipe storage (reference for schema)
- `CLAUDE.md` - Project rules and constraints

## 4. Target Output

**New Python Backend:**
- `backend/agents/` - Agent framework and individual agent implementations
- `backend/messaging/` - Message system for inter-agent communication
- `backend/pipeline/` - Recipe production pipeline controller
- `backend/memory/` - Agent memory system for personality development
- `backend/data/` - Data models and storage

**Frontend Updates (minimal):**
- Featured recipe section (above existing grid)
- Newsletter signup form (between featured and grid)

**Tests:**
- `tests/` - Property-based tests (Hypothesis) and unit tests

## 5. Requirements

From Kiro specs, prioritized for MVP (see `.kiro/specs/ai-creative-team/tasks.md` for detailed breakdown):

**Phase 1 - Core Agent Framework:** ✅ COMPLETE
- [x] Agent base class with personality-driven behavior (Req 1.1-1.6)
- [x] PersonalityConfig system with core traits, backstory, quirks (Req 1A)
- [x] All 5 agent implementations with distinct personalities

**Phase 2 - Communication Infrastructure:** ✅ COMPLETE
- [x] Message system with queuing, routing, logging (Req 10.1-10.7)
- [x] Personality-based message styling
- [x] Creative process documentation via message logs (Req 4.1-4.5)

**Phase 3 - Memory & Pipeline:** ✅ COMPLETE
- [x] Agent memory for personality development (Req 3.1-3.6)
- [x] Recipe pipeline controller with stage management (Req 2.1-2.7)
- [x] Creative Director review process (Req 6.1-6.5)

**Phase 4 - Frontend & Integration:** ⚠️ PARTIAL (Core Complete)
- [x] End-to-end recipe production workflow (COMPLETE)
- [x] Data models for Recipe and CreationStory (COMPLETE)
- [x] Integration orchestrator (COMPLETE)
- [ ] Featured recipe section HTML template (Req 12.1-12.7) - Enhancement
- [ ] Newsletter signup form (Req 13) - Enhancement

## 6. Acceptance Criteria

**Agent Framework:** ✅ ALL VERIFIED
- [x] Each agent loads consistent personality across restarts (Property 1)
- [x] All agents have: core traits, backstory, communication style, quirks, triggers (Property 2)
- [x] Baker produces recipes with concept, ingredients, and instructions (Property 4)

**Message System:** ✅ ALL VERIFIED
- [x] Messages delivered only to specified recipient (Property 8)
- [x] All messages logged with sender, recipient, timestamp, content (Property 9)

**Pipeline:** ✅ ALL VERIFIED
- [x] Recipe processes through all stages without skipping (Property 3)
- [x] Creative Director applies consistent quality standards (Property 7)

**Memory:** ✅ ALL VERIFIED
- [x] Each agent has persistent memory file (Property 5)
- [x] Significant experiences recorded with emotional context (Property 6)

**Frontend:** ⚠️ DEFERRED
- [ ] Featured recipe displays without breaking existing grid
- [ ] Newsletter email validation works correctly (Property 11)

**Final Verification:** ✅ PASSED
- [x] All property-based tests pass with 100+ iterations (31 tests passing)
- [x] End-to-end recipe production completes successfully (6 integration tests)

## 7. Constraints

- **Allowed paths:** `backend/`, `src/` (frontend updates only), `tests/`, `data/`
- **Forbidden paths:** `.env`, `vercel.json`, `CLAUDE.md`
- **Deletions allowed:** No (existing code must not be removed)
- **Max diff size:** 500 lines per task (break into subtasks if larger)
- **Testing:** Use pytest + hypothesis for property-based testing
- **Python:** Use uv for dependency management, not pip

**Critical Frontend Constraint:**
The existing recipe grid, styling, mobile responsiveness, and "Jump to Recipe" functionality must remain EXACTLY as implemented. Frontend changes are ADDITIVE only.

## 8. Notes for Implementer

**Agent Personalities (from Req 1A):**

1. **Baker** - 50s traditionalist, 30 years experience, skeptical of trendy ingredients (matcha, activated charcoal), passive-aggressive mutterer

2. **Creative Director** - 28yo woman, first CD role, trust fund background, got job through connections, good intentions but poor communication skills, under pressure not to fail

3. **Art Director** - Pretentious art school grad + failed Instagram influencer. Talks about "visual language of baked goods" and "negative space." Takes 47 shots for crumb structure. Suggests impractical marble backdrops and eucalyptus garnishes.

4. **Editorial Copywriter** - Failed novelist who writes 800-word backstories for blueberry muffins. Must be reined in. Secretly resents that more people read muffin descriptions than his self-published book.

5. **Site Architect** - Fresh college grad who lied on resume, lazy but competent, tries to convince everyone he knows everything about tech while being the only one who actually codes

**Architecture Notes:**
- Python backend (no Node.js)
- Static HTML + Tailwind CSS frontend (existing)
- JSON file storage (no database)
- Vercel deployment (automatic from GitHub)

**Development Order:**
Follow the task order in `.kiro/specs/ai-creative-team/tasks.md`. There are checkpoints at tasks 4, 8, and 14 to verify progress.

**Property-Based Testing:**
Each property test must reference its design document property using the format:
`Feature: ai-creative-team, Property N: [Property Name]`

---

**Erik Approval:** ☐ Approved
**Trigger:** When this file is saved as `_handoff/PROPOSAL_FINAL.md`, the Floor Manager will convert it to a TASK_CONTRACT.json and begin execution.
