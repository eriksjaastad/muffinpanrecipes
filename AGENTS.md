# AGENTS.md - Source of Truth for AI Agents

## 🎯 Project Overview
Muffin Pan Recipes is a high-volume, AI-driven recipe site focused on "Muffin Tin Meals." It leverages LLMs for content generation and GitHub Actions for automated deployment to Dreamhost.

## 🛠 Tech Stack
- **Frontend:** Static HTML, Tailwind CSS (via CDN for prototype).
- **Deployment:** GitHub Actions (SSH + Rsync).
- **Hosting:** Dreamhost.
- **AI Strategy:** Gemini 3 Flash for complex logic/prompts; Local models for routine tasks.

## 📋 Definition of Done (DoD)
- [ ] Recipes follow the `Documents/core/RECIPE_SCHEMA.md`.
- [ ] Technical changes are documented in `Documents/core/ARCHITECTURAL_DECISIONS.md`.
- [ ] `00_Index_MuffinPanRecipes.md` is updated with recent activity.
- [ ] Deployment to Dreamhost is verified with 0 manual steps.

## 🚀 Execution Commands
- **Local Dev:** Open `src/index.html` directly in the browser.
- **Test Deployment:** Push to `main` branch to trigger GitHub Actions.

## ⚠️ Critical Constraints
- **Static Only:** No server-side logic or database for the initial phase.
- **Mobile-First:** All UI changes must be tested for mobile responsiveness.
- **No Fluff:** Priority is the "Jump to Recipe" experience.

## 📖 Reference Links
- [[00_Index_MuffinPanRecipes]]
- [[Documents/core/RECIPE_SCHEMA]]
- [[Documents/core/ARCHITECTURAL_DECISIONS]]

