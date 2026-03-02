# Antigravity Rules for muffinpanrecipes

<!-- AUTO-GENERATED from .agentsync/rules/ - Do not edit directly -->
<!-- Run: uv run $TOOLS_ROOT/agentsync/sync_rules.py muffinpanrecipes -->

# AGENTS.md - Source of Truth for AI Agents

## 🎯 Project Overview
Muffin Pan Recipes is a high-volume, AI-driven recipe site focused on "Muffin Tin Meals." It leverages LLMs for content generation and is hosted on Vercel.

## 🛠 Tech Stack
- **Frontend:** Static HTML, Tailwind CSS (via CDN for prototype).
- **Deployment:** Vercel (Automatic GitHub Integration).
- **Hosting:** Vercel.
- **AI Strategy:** Single-agent execution (Flash) with browser-based verification. See `.agent/workflows/code-task.md` for the full workflow. Note: Ollama/Mac Mini is currently offline/unavailable for this project.

## 📋 Definition of Done (DoD)
- [ ] Recipes follow the `Documents/core/RECIPE_SCHEMA.md`.
- [ ] Technical changes are documented in `Documents/core/ARCHITECTURAL_DECISIONS.md`.
- [ ] `00_Index_MuffinPanRecipes.md` is updated with recent activity.
- [ ] Deployment to Vercel is verified with 0 manual steps.

## 🚀 Execution Commands
- **Local Dev:** Open `src/index.html` directly in the browser.
- **Production:** `vercel.json` maps root traffic to the `src/` directory.

## ⚠️ Critical Constraints
- **Static Only:** No server-side logic or database for the initial phase.
- **Mobile-First:** All UI changes must be tested for mobile responsiveness.
- **No Fluff:** Priority is the "Jump to Recipe" experience.

## 📖 Reference Links
- `00_Index_*.md`
- [Recipe Schema](Documents/core/RECIPE_SCHEMA.md)
- [Architectural Decisions](Documents/core/ARCHITECTURAL_DECISIONS.md)
