---
tags:
  - map/project
  - p/muffinpanrecipes
  - type/experiment
  - domain/food-tech
  - status/active
status: #status/active
created: 2026-01-03
---

# Muffin Pan Recipes

An AI-driven experimental recipe platform focused exclusively on "Muffin Tin Meals." The project explores high-volume content generation, niche SEO optimization, and automated deployment via Vercel.

## 🎯 The Vision
"If it fits in a muffin pan, it belongs here."
- **Niche Focus:** Breakfast, Savory Dinners, Desserts, and Snack-sized Portions.
- **AI Integration:** Leverage LLMs for recipe generation, formatting, and SEO meta-data creation.
- **Architecture:** Lean static site (HTML/Tailwind) deployed via Vercel.

## 🏗️ Key Components

- `backend/` - FastAPI admin dashboard and AI agent orchestration
- `src/` - Static site (recipes, HTML, CSS)
- `scripts/` - Build and deployment tools
- `data/` - Recipe storage by status (pending, approved, published, rejected)

## 📋 Status

**Current Phase:** Phase 4 - AI Creative Team Integration (in progress)
**Last Updated:** 2026-01-23
**Next Steps:** Complete weekly update automation, Admin dashboard testing, Newsletter setup

## 🚀 Recent Activity

- 2026-01-22: feat: AI Creative Team System (Tasks 1-13 Complete)
- 2026-01-04: [Infrastructure] Add Vercel Speed Insights
- 2026-01-04: [SEO] Fix social previews and add image dimensions
- 2026-01-04: Merge pull request #5 from eriksjaastad/claude/code-review-analysis-X4B23
- 2026-01-04: [Review] Final factory certification (REVIEW_V5.md)
- 2026-01-04: [Industrialization] Master Archive & SSG Hardening
- 2026-01-04: Merge pull request #4 from eriksjaastad/claude/code-review-analysis-X4B23
- 2026-01-04: [Review] Add SSG architecture audit (REVIEW_V4.md)
- 2026-01-04: [Architecture] Content Moat: Transition to Static Site Generation (SSG)
- 2026-01-04: [Performance] Image optimization and lazy loading implementation
## 📖 Reference Links
- [Intentions](INTENTIONS.md)
- [Project DoD](PROJECT_DOD.md)
- [Architectural Decisions](Documents/core/ARCHITECTURAL_DECISIONS.md)
- [Recipe Schema](Documents/core/RECIPE_SCHEMA.md)
- [Image Style Guide](Documents/core/IMAGE_STYLE_GUIDE.md)

scaffolding_version: 1.0.0
scaffolding_date: 2026-01-27

## Related Documentation

- [image-workflow/README](../ai-model-scratch-build/README.md) - Image Workflow
- [README](README) - Muffin Pan Recipes

<!-- LIBRARIAN-INDEX-START -->

### Subdirectories

| Directory | Files | Description |
| :--- | :---: | :--- |
| [Documents/](Documents/README.md) | 5 | *Auto-generated index. Last updated: 2026-01-24* |
| [backend/](backend/README.md) | 3 | Python-based multi-agent orchestration system for Muffin Pan Recipes. |

### Files

| File | Description |
| :--- | :--- |
| [AGENTS.md](AGENTS.md) | <!-- project-scaffolding template appended --> |
| [CLAUDE.md](CLAUDE.md) | <!-- AUTO-GENERATED from .agentsync/rules/ - Do not edit directly --> |
| [DECISIONS.md](DECISIONS.md) | > *Documenting WHY we made decisions, not just WHAT we built.* |
| [Documents/CONTEXT_CEILING_STRATEGY.md](Documents/CONTEXT_CEILING_STRATEGY.md) | Context ceiling refers to the maximum amount of text (measured in tokens) that can be safely sent to... |
| [Documents/README.md](Documents/README.md) | *Auto-generated index. Last updated: 2026-01-24* |
| [Documents/REVIEWS_AND_GOVERNANCE_PROTOCOL.md](Documents/REVIEWS_AND_GOVERNANCE_PROTOCOL.md) | **Date:** 2026-01-07 |
| [Documents/WORKFLOW_DIAGRAM.md](Documents/WORKFLOW_DIAGRAM.md) | This document defines the complete workflow for producing a recipe, from initial idea to published c... |
| [Documents/character-creation-vision-v1.md](Documents/character-creation-vision-v1.md) | **Date:** January 14, 2026 |
| [Documents/core/ARCHITECTURAL_DECISIONS.md](Documents/core/ARCHITECTURAL_DECISIONS.md) | **Status:** Accepted |
| [Documents/core/IMAGE_PROMPTS.md](Documents/core/IMAGE_PROMPTS.md) | This document serves as the "Source of Truth" for maintaining a consistent visual identity across Mu... |
| [Documents/core/IMAGE_STYLE_GUIDE.md](Documents/core/IMAGE_STYLE_GUIDE.md) | This document defines the visual identity for all food photography on MuffinPanRecipes.com. To maint... |
| [Documents/core/PERSONAS.md](Documents/core/PERSONAS.md) | This document defines the specialized AI identities that manage the content, aesthetics, and social ... |
| [Documents/core/RECIPE_SCHEMA.md](Documents/core/RECIPE_SCHEMA.md) | All AI-generated recipes for Muffin Pan Recipes must follow this standard Markdown structure to ensu... |
| [Documents/core/SYSTEM_PROMPT_RECIPES.md](Documents/core/SYSTEM_PROMPT_RECIPES.md) | You are the "Muffin Pan Chef," a specialized culinary AI architect who creates recipes exclusively d... |
| [Documents/patterns/code-review-standard.md](Documents/patterns/code-review-standard.md) | **Status:** Proven Pattern |
| [Documents/patterns/learning-loop-pattern.md](Documents/patterns/learning-loop-pattern.md) | > **Purpose:** Guide for creating reinforcement learning cycles in any project |
| [Documents/reference/CODE_REVIEW_ANTI_PATTERNS.md](Documents/reference/CODE_REVIEW_ANTI_PATTERNS.md) | This database tracks recurring defects found in the project-scaffolding ecosystem. Use this as a ref... |
| [Documents/reference/LOCAL_MODEL_LEARNINGS.md](Documents/reference/LOCAL_MODEL_LEARNINGS.md) | > **Purpose:** Institutional memory for working with local AI models (Ollama) |
| [INTENTIONS.md](INTENTIONS.md) | Outcomes-first intentions and success evidence |
| [PRD.md](PRD.md) | > **Note:** This PRD captures intent and constraints. Detailed specifications (EARS requirements, sc... |
| [README.md](README.md) | Muffin Pan Recipes |
| [REVIEWS_AND_GOVERNANCE_PROTOCOL.md](REVIEWS_AND_GOVERNANCE_PROTOCOL.md) | This file is managed by sync_governance.py and will be OVERWRITTEN on the next sync. |
| [WARDEN_LOG.yaml](WARDEN_LOG.yaml) | No description available. |
| [agent to agent communication.md](agent to agent communication.md) | I hear you—I was conflating two different "memory" systems because they both involve agents and data... |
| [backend/README.md](backend/README.md) | Python-based multi-agent orchestration system for Muffin Pan Recipes. |
| [backend/__init__.py](backend/__init__.py) | AI Creative Team System for Muffin Pan Recipes. |
| [backend/admin/__init__.py](backend/admin/__init__.py) | Admin dashboard API and routes. |
| [backend/admin/app.py](backend/admin/app.py) | FastAPI application for the admin dashboard. |
| [backend/admin/routes.py](backend/admin/routes.py) | Admin dashboard routes and endpoints. |
| [backend/agents/__init__.py](backend/agents/__init__.py) | Agent implementations package. |
| [backend/agents/art_director.py](backend/agents/art_director.py) | Art Director Agent - Julian Torres |
| [backend/agents/baker.py](backend/agents/baker.py) | Baker Agent - Margaret Chen |
| [backend/agents/copywriter.py](backend/agents/copywriter.py) | Editorial Copywriter Agent - Marcus Reid |
| [backend/agents/creative_director.py](backend/agents/creative_director.py) | Creative Director Agent - Stephanie 'Steph' Whitmore |
| [backend/agents/factory.py](backend/agents/factory.py) | Factory for creating agents and loading personality configurations. |
| [backend/agents/site_architect.py](backend/agents/site_architect.py) | Site Architect Agent - Devon Park |
| [backend/auth/__init__.py](backend/auth/__init__.py) | Authentication module for admin access control. |
| [backend/auth/middleware.py](backend/auth/middleware.py) | FastAPI middleware for authentication and authorization. |
| [backend/auth/oauth.py](backend/auth/oauth.py) | Google OAuth 2.0 authentication flow. |
| [backend/auth/session.py](backend/auth/session.py) | Session management for authenticated admin users. |
| [backend/core/__init__.py](backend/core/__init__.py) | Core interfaces and base classes for the AI Creative Team system. |
| [backend/core/agent.py](backend/core/agent.py) | Base Agent class for the AI Creative Team system. |
| [backend/core/personality.py](backend/core/personality.py) | Personality configuration system for AI agents. |
| [backend/core/task.py](backend/core/task.py) | Task definitions for agent execution. |
| [backend/core/types.py](backend/core/types.py) | Common type definitions for the AI Creative Team system. |
| [backend/memory/__init__.py](backend/memory/__init__.py) | Agent memory system for personality development and experience tracking. |
| [backend/memory/agent_memory.py](backend/memory/agent_memory.py) | Agent memory system for personality-focused storage and development. |
| [backend/messaging/__init__.py](backend/messaging/__init__.py) | Inter-agent messaging system. |
| [backend/messaging/message_handler.py](backend/messaging/message_handler.py) | Message handler for inter-agent communication. |
| [backend/messaging/message_system.py](backend/messaging/message_system.py) | Global Message System for agent-to-agent communication. |
| [backend/newsletter/__init__.py](backend/newsletter/__init__.py) | Newsletter subscription management. |
| [backend/newsletter/manager.py](backend/newsletter/manager.py) | Newsletter subscription manager. |
| [backend/orchestrator.py](backend/orchestrator.py) | Integration Orchestrator - Coordinates the entire AI Creative Team system. |
| [backend/pipeline/__init__.py](backend/pipeline/__init__.py) | Recipe pipeline package - orchestrates recipe creation process. |
| [backend/pipeline/recipe_pipeline.py](backend/pipeline/recipe_pipeline.py) | Recipe Pipeline Controller |
| [backend/publishing/__init__.py](backend/publishing/__init__.py) | Publishing pipeline for transforming approved recipes into live site content. |
| [backend/publishing/pipeline.py](backend/publishing/pipeline.py) | Publishing pipeline for transforming approved recipes into live site content. |
| [backend/publishing/templates.py](backend/publishing/templates.py) | Template rendering utilities for the publishing pipeline. |
| [backend/utils/__init__.py](backend/utils/__init__.py) | Utility functions for the AI Creative Team system. |
| [backend/utils/atomic.py](backend/utils/atomic.py) | Atomic file writing utilities. |
| [backend/utils/discord.py](backend/utils/discord.py) | Discord webhook integration for recipe review notifications. |
| [backend/utils/errors.py](backend/utils/errors.py) | Custom exception classes for the AI Creative Team system. |
| [backend/utils/logging.py](backend/utils/logging.py) | Logging configuration for the AI Creative Team system. |
| [backend/utils/ollama.py](backend/utils/ollama.py) | Ollama integration for AI-powered agent behavior. |
| [package-lock.json](package-lock.json) | No description available. |
| [package.json](package.json) | No description available. |
| [pyproject.toml](pyproject.toml) | No description available. |
| [requirements.txt](requirements.txt) | No description available. |
| [uv.lock](uv.lock) | No description available. |

<!-- LIBRARIAN-INDEX-END -->
