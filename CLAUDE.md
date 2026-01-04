# CLAUDE.md - AI Collaboration Instructions

> **Purpose:** Project-specific instructions for AI assistants (Claude, Gemini, etc.)
> **Audience:** AI collaborators and human developers

---

## 📚 Required Reading Before Writing Code

**You MUST read these files:**

1. **README.md** - Project overview and quick start
2. **This file (CLAUDE.md)** - Coding standards and safety rules
3. **Documents/core/RECIPE_SCHEMA.md** - How recipes must be structured
4. **Documents/core/ARCHITECTURAL_DECISIONS.md** - Core architecture rules

---

## Project Summary

**What this project does:**
Automates the generation and deployment of a niche recipe site dedicated to meals made in muffin pans.

**Current status:**
Phase 1 complete. Infrastructure for automated deployment is ready on Vercel. 10 initial recipes harvested.

**Key constraints:**
- Static site architecture only.
- 0-manual-step deployment via Vercel.
- Mobile-first "No-Fluff" UI.

---

## Project Structure

```
muffinpanrecipes/
├── src/                      # Source code (HTML/CSS)
├── data/                     # Recipe data and assets
├── Documents/                # Project documentation
│   ├── core/                 # Architecture, schema, and decisions
│   └── archives/             # Historical records
├── README.md                 # High-level overview
├── AGENTS.md                 # AI source of truth
├── CLAUDE.md                 # This file
└── TODO.md                   # Task tracking
```

---

## Coding Standards

### UI/UX
- Use Tailwind CSS for rapid prototyping.
- Prioritize the "Jump to Recipe" button above the fold.
- Ensure all components are accessible and mobile-responsive.

---

## Safety Rules

### 🟡 Be Careful With These:
1. **`Documents/core/RECIPE_SCHEMA.md`** - Changing this impacts all future AI generation.
2. **`src/index.html`** - Core UI; ensure mobile layout isn't broken.

### ✅ Safe to Modify:
1. **`Documents/core/ARCHITECTURAL_DECISIONS.md`** - Log new decisions here.
2. **`TODO.md`** - Keep tasks updated.

---

## Git Workflow

### Commit Message Format
```
[Component] Brief description

- Change 1
- Change 2
```

---

## Working with This AI

### Communication Preferences
- **Be direct** - Tell me what you need.
- **Focus on the Niche** - Always remember the "Muffin Pan Constraint."
- **Check Schema** - Before generating recipes, verify they match the schema.

---

*This file follows the [project-scaffolding](https://github.com/eriksjaastad/project-scaffolding) pattern.*

