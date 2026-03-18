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

- `backend/` - [AI Creative Team Orchestration](backend/README.md) (Python/FastAPI)
- `src/` - [Static Site Source](src/README.md) (HTML/Tailwind/Recipes)
- `scripts/` - [Automation & Image Pipeline](scripts/README.md) (Python/Shell)
- `tests/` - [Test Suite Documentation](tests/README.md)
- `data/` - Recipe storage, simulation logs, and agent memory
- `_docs_archive/` - Legacy and deep-dive documentation (archived)

## 📋 Status

**Current Phase:** Phase 4 - AI Creative Team Integration
**Last Updated:** 2026-03-06 (Documentation Consolidation)
**Next Steps:** Complete weekly update automation, Admin dashboard testing, Newsletter setup

## 🚀 Recent Activity

- 2026-03-15: fix: strip parenthetical qualifiers from recipe titles in catalog
- 2026-03-15: feat: Sunday cron publishes recipe to main page catalog (#5055)
- 2026-03-14: feat: dialogue improvements, character memory, tests, and agent rules
- 2026-03-11: fix: serve images from main domain + enforce day-of-week on cron stages
- 2026-03-10: chore: fix broken Documents/ references after migration
- 2026-03-10: docs: add notification anti-pattern rules to handoff
- 2026-03-10: docs: add OpenClaw handoff document for project operations
- 2026-03-10: fix: render middot in teaser + add character roles to episode page
- 2026-03-10: fix: cron routes accept GET + progressive episode page rendering [publish]
- 2026-03-07: fix: remove speed insights, update recipe layouts, add feature flag [publish]
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
| [_docs_archive/](_docs_archive/README.md) | 4 | *Auto-generated index. Last updated: 2026-01-24* |
| [backend/](backend/README.md) | 5 | Python-based multi-agent orchestration system for Muffin Pan Recipes. |
| [prompt-research/](prompt-research/) | 13 | No description available. |
| [reviews/](reviews/) | 1 | No description available. |

### Files

| File | Description |
| :--- | :--- |
| [AGENTS.md](AGENTS.md) | <!-- project-scaffolding template appended --> |
| [AI Character Voice Consistency Research.md](AI Character Voice Consistency Research.md) | The operationalization of autonomous personas within generative environments represents a critical i... |
| [ANTI_REPETITION_TEST_RESULTS.md](ANTI_REPETITION_TEST_RESULTS.md) | Added self-awareness anti-repetition to `generate_turn()` in `simulate_dialogue_week.py`. |
| [BOOKEND_TESTING_LOG.md](BOOKEND_TESTING_LOG.md) | Character-driven opening greetings and closing sign-offs for each day's conversation. |
| [CLAUDE.md](CLAUDE.md) | <!-- AUTO-GENERATED from .agentsync/rules/ - Do not edit directly --> |
| [COMPRESSED_TIMELINE_SPEC.md](COMPRESSED_TIMELINE_SPEC.md) | A `--test` flag on `run_full_week.py` that runs the full Mon-Sun cron pipeline against Vercel produc... |
| [CREATIVE_BIBLE.md](CREATIVE_BIBLE.md) | > The show bible for our weekly serialized content. This is a living document. |
| [DECISIONS.md](DECISIONS.md) | > *Documenting WHY we made decisions, not just WHAT we built.* |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Layer |
| [DIALOGUE_IMPLEMENTATION_PLAN.md](DIALOGUE_IMPLEMENTATION_PLAN.md) | Goal: Ship reliable, character-consistent weekly dialogue by Monday. |
| [ERIKS_TODO.md](ERIKS_TODO.md) | This file is intentionally deprecated. |
| [HANDOFF_OPENCLAW.md](HANDOFF_OPENCLAW.md) | > **Last updated:** 2026-03-10 |
| [INTENTIONS.md](INTENTIONS.md) | Outcomes-first intentions and success evidence |
| [MODEL_COMPARISON_REPORT.md](MODEL_COMPARISON_REPORT.md) | **Date:** 2026-03-05 |
| [OPENCLAW_PREFLIGHT.md](OPENCLAW_PREFLIGHT.md) | Use this checklist before running any pipeline or test that depends on secrets. |
| [PLAN_5039_STORAGE_FIX.md](PLAN_5039_STORAGE_FIX.md) | Episode JSON files don't persist between Vercel cron invocations. The `_CloudBackend` in `backend/st... |
| [PLAN_NEWLINE_SANITIZATION.md](PLAN_NEWLINE_SANITIZATION.md) | LLM-generated messages sometimes contain newlines. These messages get injected back into prompts in ... |
| [PRD.md](PRD.md) | > **Note:** This PRD captures intent and constraints. Detailed specifications (EARS requirements, sc... |
| [PROJECT_DOD.md](PROJECT_DOD.md) | - [ ] One complete recipe exists that was generated by Baker (Margaret), written up by Copywriter (M... |
| [README.md](README.md) | Muffin Pan Recipes |
| [REVIEWS_AND_GOVERNANCE_PROTOCOL.md](REVIEWS_AND_GOVERNANCE_PROTOCOL.md) | This file is managed by sync_governance.py and will be OVERWRITTEN on the next sync. |
| [SCENARIOS.md](SCENARIOS.md) | - **Given:** The system is configured with all 7 agent personalities and their model assignments, an... |
| [WARDEN_LOG.yaml](WARDEN_LOG.yaml) | No description available. |
| [_docs_archive/CONTEXT_CEILING_STRATEGY.md](_docs_archive/CONTEXT_CEILING_STRATEGY.md) | Context ceiling refers to the maximum amount of text (measured in tokens) that can be safely sent to... |
| [_docs_archive/README.md](_docs_archive/README.md) | *Auto-generated index. Last updated: 2026-01-24* |
| [_docs_archive/WORKFLOW_DIAGRAM.md](_docs_archive/WORKFLOW_DIAGRAM.md) | This document defines the complete workflow for producing a recipe, from initial idea to published c... |
| [_docs_archive/character-creation-vision-v1.md](_docs_archive/character-creation-vision-v1.md) | **Date:** January 14, 2026 |
| [_docs_archive/core/ARCHITECTURAL_DECISIONS.md](_docs_archive/core/ARCHITECTURAL_DECISIONS.md) | **Status:** Accepted |
| [_docs_archive/core/IMAGE_PROMPTS.md](_docs_archive/core/IMAGE_PROMPTS.md) | This document serves as the "Source of Truth" for maintaining a consistent visual identity across Mu... |
| [_docs_archive/core/IMAGE_STYLE_GUIDE.md](_docs_archive/core/IMAGE_STYLE_GUIDE.md) | This document defines the visual identity for all food photography on MuffinPanRecipes.com. To maint... |
| [_docs_archive/core/PERSONAS.md](_docs_archive/core/PERSONAS.md) | This document defines the specialized AI identities that manage the content, aesthetics, and social ... |
| [_docs_archive/core/RECIPE_SCHEMA.md](_docs_archive/core/RECIPE_SCHEMA.md) | All AI-generated recipes for Muffin Pan Recipes must follow this standard Markdown structure to ensu... |
| [_docs_archive/core/SYSTEM_PROMPT_RECIPES.md](_docs_archive/core/SYSTEM_PROMPT_RECIPES.md) | You are the "Muffin Pan Chef," a specialized culinary AI architect who creates recipes exclusively d... |
| [_docs_archive/patterns/code-review-standard.md](_docs_archive/patterns/code-review-standard.md) | **Status:** Proven Pattern |
| [_docs_archive/patterns/learning-loop-pattern.md](_docs_archive/patterns/learning-loop-pattern.md) | > **Purpose:** Guide for creating reinforcement learning cycles in any project |
| [_docs_archive/reference/CODE_REVIEW_ANTI_PATTERNS.md](_docs_archive/reference/CODE_REVIEW_ANTI_PATTERNS.md) | This database tracks recurring defects found in the project-scaffolding ecosystem. Use this as a ref... |
| [agent to agent communication.md](agent to agent communication.md) | I hear you—I was conflating two different "memory" systems because they both involve agents and data... |
| [backend/README.md](backend/README.md) | Python-based multi-agent orchestration system for Muffin Pan Recipes. |
| [backend/__init__.py](backend/__init__.py) | AI Creative Team System for Muffin Pan Recipes. |
| [backend/admin/__init__.py](backend/admin/__init__.py) | Admin dashboard API and routes. |
| [backend/admin/app.py](backend/admin/app.py) | FastAPI application for the admin dashboard. |
| [backend/admin/cron_routes.py](backend/admin/cron_routes.py) | Vercel Cron API routes for the Muffin Pan Recipes pipeline. |
| [backend/admin/episode_routes.py](backend/admin/episode_routes.py) | Public routes for serving episode pages and teaser data. |
| [backend/admin/routes.py](backend/admin/routes.py) | Admin dashboard routes and endpoints. |
| [backend/admin/static/tailwind.css](backend/admin/static/tailwind.css) | No description available. |
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
| [backend/config.py](backend/config.py) | Environment configuration for Muffin Pan Recipes. |
| [backend/core/README.md](backend/core/README.md) | This directory contains the foundational components for the multi-agent orchestration system. |
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
| [backend/publishing/episode_renderer.py](backend/publishing/episode_renderer.py) | Render episode JSON into a viewable HTML recipe page. |
| [backend/publishing/pipeline.py](backend/publishing/pipeline.py) | Publishing pipeline for transforming approved recipes into live site content. |
| [backend/publishing/templates.py](backend/publishing/templates.py) | Template rendering utilities for the publishing pipeline. |
| [backend/storage.py](backend/storage.py) | Storage abstraction layer for Muffin Pan Recipes. |
| [backend/utils/README.md](backend/utils/README.md) | This directory provides a unified infrastructure for LLM routing, recipe prompting, and operational ... |
| [backend/utils/__init__.py](backend/utils/__init__.py) | Utility functions for the AI Creative Team system. |
| [backend/utils/atomic.py](backend/utils/atomic.py) | Atomic file writing utilities. |
| [backend/utils/discord.py](backend/utils/discord.py) | Discord webhook integration for recipe review notifications. |
| [backend/utils/errors.py](backend/utils/errors.py) | Custom exception classes for the AI Creative Team system. |
| [backend/utils/logging.py](backend/utils/logging.py) | Logging configuration for the AI Creative Team system. |
| [backend/utils/model_router.py](backend/utils/model_router.py) | Unified model router for Ollama + OpenAI-compatible dialogue generation. |
| [backend/utils/publish_schedule.py](backend/utils/publish_schedule.py) | Publish scheduling utilities (DST-aware). |
| [backend/utils/recipe_prompts.py](backend/utils/recipe_prompts.py) | Recipe and description generation via model router. |
| [package-lock.json](package-lock.json) | No description available. |
| [package.json](package.json) | No description available. |
| [prompt-research/TESTING_METHODOLOGY.md](prompt-research/TESTING_METHODOLOGY.md) | --- |
| [prompt-research/__init__.py](prompt-research/__init__.py) | Empty file. |
| [prompt-research/baselines/brown-butter-pecan-tassies/frozen-week-mon-sat.json](prompt-research/baselines/brown-butter-pecan-tassies/frozen-week-mon-sat.json) | No description available. |
| [prompt-research/baselines/jalape-o-corn-dog-bites/frozen-before-monday.json](prompt-research/baselines/jalape-o-corn-dog-bites/frozen-before-monday.json) | No description available. |
| [prompt-research/baselines/jalape-o-corn-dog-bites/frozen-before-thursday.json](prompt-research/baselines/jalape-o-corn-dog-bites/frozen-before-thursday.json) | No description available. |
| [prompt-research/baselines/jalape-o-corn-dog-bites/frozen-before-tuesday.json](prompt-research/baselines/jalape-o-corn-dog-bites/frozen-before-tuesday.json) | No description available. |
| [prompt-research/baselines/jalape-o-corn-dog-bites/frozen-week-mon-sat.json](prompt-research/baselines/jalape-o-corn-dog-bites/frozen-week-mon-sat.json) | No description available. |
| [prompt-research/baselines/mini-shepherd-s-pies/frozen-week-mon-sat.json](prompt-research/baselines/mini-shepherd-s-pies/frozen-week-mon-sat.json) | No description available. |
| [prompt-research/best_compression_prompts.py](prompt-research/best_compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/best_prompts.py](prompt-research/best_prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/compression_program.md](prompt-research/compression_program.md) | You are an autonomous compression researcher optimizing how prior-day conversation context gets summ... |
| [prompt-research/compression_prompts.py](prompt-research/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_results.tsv](prompt-research/compression_results.tsv) | No description available. |
| [prompt-research/compression_runner.py](prompt-research/compression_runner.py) | No description available. |
| [prompt-research/compression_runs/exp-0000/compression_prompts.py](prompt-research/compression_runs/exp-0000/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0000/dialogue.json](prompt-research/compression_runs/exp-0000/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0000/highlights.json](prompt-research/compression_runs/exp-0000/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0000/scores.json](prompt-research/compression_runs/exp-0000/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0001/compression_prompts.py](prompt-research/compression_runs/exp-0001/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0001/dialogue.json](prompt-research/compression_runs/exp-0001/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0001/highlights.json](prompt-research/compression_runs/exp-0001/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0001/scores.json](prompt-research/compression_runs/exp-0001/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0002/compression_prompts.py](prompt-research/compression_runs/exp-0002/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0002/dialogue.json](prompt-research/compression_runs/exp-0002/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0002/highlights.json](prompt-research/compression_runs/exp-0002/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0002/scores.json](prompt-research/compression_runs/exp-0002/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0003/compression_prompts.py](prompt-research/compression_runs/exp-0003/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0003/dialogue.json](prompt-research/compression_runs/exp-0003/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0003/highlights.json](prompt-research/compression_runs/exp-0003/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0003/scores.json](prompt-research/compression_runs/exp-0003/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0004/compression_prompts.py](prompt-research/compression_runs/exp-0004/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0004/dialogue.json](prompt-research/compression_runs/exp-0004/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0004/highlights.json](prompt-research/compression_runs/exp-0004/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0004/scores.json](prompt-research/compression_runs/exp-0004/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0005/compression_prompts.py](prompt-research/compression_runs/exp-0005/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0005/dialogue.json](prompt-research/compression_runs/exp-0005/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0005/highlights.json](prompt-research/compression_runs/exp-0005/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0005/scores.json](prompt-research/compression_runs/exp-0005/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0006/compression_prompts.py](prompt-research/compression_runs/exp-0006/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0006/dialogue.json](prompt-research/compression_runs/exp-0006/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0006/highlights.json](prompt-research/compression_runs/exp-0006/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0006/scores.json](prompt-research/compression_runs/exp-0006/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0007/compression_prompts.py](prompt-research/compression_runs/exp-0007/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0007/dialogue.json](prompt-research/compression_runs/exp-0007/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0007/highlights.json](prompt-research/compression_runs/exp-0007/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0007/scores.json](prompt-research/compression_runs/exp-0007/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0008/compression_prompts.py](prompt-research/compression_runs/exp-0008/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0008/dialogue.json](prompt-research/compression_runs/exp-0008/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0008/highlights.json](prompt-research/compression_runs/exp-0008/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0008/scores.json](prompt-research/compression_runs/exp-0008/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0009/compression_prompts.py](prompt-research/compression_runs/exp-0009/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0009/dialogue.json](prompt-research/compression_runs/exp-0009/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0009/highlights.json](prompt-research/compression_runs/exp-0009/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0009/scores.json](prompt-research/compression_runs/exp-0009/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0010/compression_prompts.py](prompt-research/compression_runs/exp-0010/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0010/dialogue.json](prompt-research/compression_runs/exp-0010/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0010/highlights.json](prompt-research/compression_runs/exp-0010/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0010/scores.json](prompt-research/compression_runs/exp-0010/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0011/compression_prompts.py](prompt-research/compression_runs/exp-0011/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0011/dialogue.json](prompt-research/compression_runs/exp-0011/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0011/highlights.json](prompt-research/compression_runs/exp-0011/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0011/scores.json](prompt-research/compression_runs/exp-0011/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0012/compression_prompts.py](prompt-research/compression_runs/exp-0012/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0012/dialogue.json](prompt-research/compression_runs/exp-0012/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0012/highlights.json](prompt-research/compression_runs/exp-0012/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0012/scores.json](prompt-research/compression_runs/exp-0012/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0013/compression_prompts.py](prompt-research/compression_runs/exp-0013/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0013/dialogue.json](prompt-research/compression_runs/exp-0013/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0013/highlights.json](prompt-research/compression_runs/exp-0013/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0013/scores.json](prompt-research/compression_runs/exp-0013/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0014/compression_prompts.py](prompt-research/compression_runs/exp-0014/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0014/dialogue.json](prompt-research/compression_runs/exp-0014/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0014/highlights.json](prompt-research/compression_runs/exp-0014/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0014/scores.json](prompt-research/compression_runs/exp-0014/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0015/compression_prompts.py](prompt-research/compression_runs/exp-0015/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0015/dialogue.json](prompt-research/compression_runs/exp-0015/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0015/highlights.json](prompt-research/compression_runs/exp-0015/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0015/scores.json](prompt-research/compression_runs/exp-0015/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0016/compression_prompts.py](prompt-research/compression_runs/exp-0016/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0016/dialogue.json](prompt-research/compression_runs/exp-0016/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0016/highlights.json](prompt-research/compression_runs/exp-0016/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0016/scores.json](prompt-research/compression_runs/exp-0016/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0017/compression_prompts.py](prompt-research/compression_runs/exp-0017/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0017/dialogue.json](prompt-research/compression_runs/exp-0017/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0017/highlights.json](prompt-research/compression_runs/exp-0017/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0017/scores.json](prompt-research/compression_runs/exp-0017/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0018/compression_prompts.py](prompt-research/compression_runs/exp-0018/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0018/dialogue.json](prompt-research/compression_runs/exp-0018/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0018/highlights.json](prompt-research/compression_runs/exp-0018/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0018/scores.json](prompt-research/compression_runs/exp-0018/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0019/compression_prompts.py](prompt-research/compression_runs/exp-0019/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0019/dialogue.json](prompt-research/compression_runs/exp-0019/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0019/highlights.json](prompt-research/compression_runs/exp-0019/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0019/scores.json](prompt-research/compression_runs/exp-0019/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0020/compression_prompts.py](prompt-research/compression_runs/exp-0020/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0020/dialogue.json](prompt-research/compression_runs/exp-0020/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0020/highlights.json](prompt-research/compression_runs/exp-0020/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0020/scores.json](prompt-research/compression_runs/exp-0020/scores.json) | No description available. |
| [prompt-research/compression_runs/exp-0021/compression_prompts.py](prompt-research/compression_runs/exp-0021/compression_prompts.py) | Mutable compression templates for autoresearch-style optimization. |
| [prompt-research/compression_runs/exp-0021/dialogue.json](prompt-research/compression_runs/exp-0021/dialogue.json) | No description available. |
| [prompt-research/compression_runs/exp-0021/highlights.json](prompt-research/compression_runs/exp-0021/highlights.json) | No description available. |
| [prompt-research/compression_runs/exp-0021/scores.json](prompt-research/compression_runs/exp-0021/scores.json) | No description available. |
| [prompt-research/evaluator.py](prompt-research/evaluator.py) | Locked evaluator — DO NOT MODIFY during experiments. |
| [prompt-research/program.md](prompt-research/program.md) | You are an autonomous prompt researcher optimizing dialogue quality for a fictional workplace sitcom... |
| [prompt-research/prompts.py](prompt-research/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/results.tsv](prompt-research/results.tsv) | No description available. |
| [prompt-research/runner.py](prompt-research/runner.py) | No description available. |
| [prompt-research/runs/exp-0000/dialogue.json](prompt-research/runs/exp-0000/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0000/prompts.py](prompt-research/runs/exp-0000/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0000/scores.json](prompt-research/runs/exp-0000/scores.json) | No description available. |
| [prompt-research/runs/exp-0001/dialogue.json](prompt-research/runs/exp-0001/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0001/prompts.py](prompt-research/runs/exp-0001/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0001/scores.json](prompt-research/runs/exp-0001/scores.json) | No description available. |
| [prompt-research/runs/exp-0002/dialogue.json](prompt-research/runs/exp-0002/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0002/prompts.py](prompt-research/runs/exp-0002/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0002/scores.json](prompt-research/runs/exp-0002/scores.json) | No description available. |
| [prompt-research/runs/exp-0003/dialogue.json](prompt-research/runs/exp-0003/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0003/prompts.py](prompt-research/runs/exp-0003/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0003/scores.json](prompt-research/runs/exp-0003/scores.json) | No description available. |
| [prompt-research/runs/exp-0004/dialogue.json](prompt-research/runs/exp-0004/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0004/prompts.py](prompt-research/runs/exp-0004/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0004/scores.json](prompt-research/runs/exp-0004/scores.json) | No description available. |
| [prompt-research/runs/exp-0005/dialogue.json](prompt-research/runs/exp-0005/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0005/prompts.py](prompt-research/runs/exp-0005/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0005/scores.json](prompt-research/runs/exp-0005/scores.json) | No description available. |
| [prompt-research/runs/exp-0006/dialogue.json](prompt-research/runs/exp-0006/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0006/prompts.py](prompt-research/runs/exp-0006/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0006/scores.json](prompt-research/runs/exp-0006/scores.json) | No description available. |
| [prompt-research/runs/exp-0007/dialogue.json](prompt-research/runs/exp-0007/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0007/prompts.py](prompt-research/runs/exp-0007/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0007/scores.json](prompt-research/runs/exp-0007/scores.json) | No description available. |
| [prompt-research/runs/exp-0008/dialogue.json](prompt-research/runs/exp-0008/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0008/prompts.py](prompt-research/runs/exp-0008/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0008/scores.json](prompt-research/runs/exp-0008/scores.json) | No description available. |
| [prompt-research/runs/exp-0009/dialogue.json](prompt-research/runs/exp-0009/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0009/prompts.py](prompt-research/runs/exp-0009/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0009/scores.json](prompt-research/runs/exp-0009/scores.json) | No description available. |
| [prompt-research/runs/exp-0010/dialogue.json](prompt-research/runs/exp-0010/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0010/prompts.py](prompt-research/runs/exp-0010/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0010/scores.json](prompt-research/runs/exp-0010/scores.json) | No description available. |
| [prompt-research/runs/exp-0011/dialogue.json](prompt-research/runs/exp-0011/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0011/prompts.py](prompt-research/runs/exp-0011/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0011/scores.json](prompt-research/runs/exp-0011/scores.json) | No description available. |
| [prompt-research/runs/exp-0012/dialogue.json](prompt-research/runs/exp-0012/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0012/prompts.py](prompt-research/runs/exp-0012/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0012/scores.json](prompt-research/runs/exp-0012/scores.json) | No description available. |
| [prompt-research/runs/exp-0013/dialogue.json](prompt-research/runs/exp-0013/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0013/prompts.py](prompt-research/runs/exp-0013/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0013/scores.json](prompt-research/runs/exp-0013/scores.json) | No description available. |
| [prompt-research/runs/exp-0014/dialogue.json](prompt-research/runs/exp-0014/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0014/prompts.py](prompt-research/runs/exp-0014/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0014/scores.json](prompt-research/runs/exp-0014/scores.json) | No description available. |
| [prompt-research/runs/exp-0015/dialogue.json](prompt-research/runs/exp-0015/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0015/prompts.py](prompt-research/runs/exp-0015/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0015/scores.json](prompt-research/runs/exp-0015/scores.json) | No description available. |
| [prompt-research/runs/exp-0016/dialogue.json](prompt-research/runs/exp-0016/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0016/prompts.py](prompt-research/runs/exp-0016/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0016/scores.json](prompt-research/runs/exp-0016/scores.json) | No description available. |
| [prompt-research/runs/exp-0017/dialogue.json](prompt-research/runs/exp-0017/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0017/prompts.py](prompt-research/runs/exp-0017/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0017/scores.json](prompt-research/runs/exp-0017/scores.json) | No description available. |
| [prompt-research/runs/exp-0018/dialogue.json](prompt-research/runs/exp-0018/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0018/prompts.py](prompt-research/runs/exp-0018/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0018/scores.json](prompt-research/runs/exp-0018/scores.json) | No description available. |
| [prompt-research/runs/exp-0019/dialogue.json](prompt-research/runs/exp-0019/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0019/prompts.py](prompt-research/runs/exp-0019/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0019/scores.json](prompt-research/runs/exp-0019/scores.json) | No description available. |
| [prompt-research/runs/exp-0020/dialogue.json](prompt-research/runs/exp-0020/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0020/prompts.py](prompt-research/runs/exp-0020/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0020/scores.json](prompt-research/runs/exp-0020/scores.json) | No description available. |
| [prompt-research/runs/exp-0021/dialogue.json](prompt-research/runs/exp-0021/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0021/prompts.py](prompt-research/runs/exp-0021/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0021/scores.json](prompt-research/runs/exp-0021/scores.json) | No description available. |
| [prompt-research/runs/exp-0022/dialogue.json](prompt-research/runs/exp-0022/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0022/prompts.py](prompt-research/runs/exp-0022/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0022/scores.json](prompt-research/runs/exp-0022/scores.json) | No description available. |
| [prompt-research/runs/exp-0023/dialogue.json](prompt-research/runs/exp-0023/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0023/prompts.py](prompt-research/runs/exp-0023/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0023/scores.json](prompt-research/runs/exp-0023/scores.json) | No description available. |
| [prompt-research/runs/exp-0024/dialogue.json](prompt-research/runs/exp-0024/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0024/prompts.py](prompt-research/runs/exp-0024/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0024/scores.json](prompt-research/runs/exp-0024/scores.json) | No description available. |
| [prompt-research/runs/exp-0025/dialogue.json](prompt-research/runs/exp-0025/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0025/prompts.py](prompt-research/runs/exp-0025/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0025/scores.json](prompt-research/runs/exp-0025/scores.json) | No description available. |
| [prompt-research/runs/exp-0026/dialogue.json](prompt-research/runs/exp-0026/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0026/prompts.py](prompt-research/runs/exp-0026/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0026/scores.json](prompt-research/runs/exp-0026/scores.json) | No description available. |
| [prompt-research/runs/exp-0027/dialogue.json](prompt-research/runs/exp-0027/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0027/prompts.py](prompt-research/runs/exp-0027/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0027/scores.json](prompt-research/runs/exp-0027/scores.json) | No description available. |
| [prompt-research/runs/exp-0028/dialogue.json](prompt-research/runs/exp-0028/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0028/prompts.py](prompt-research/runs/exp-0028/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0028/scores.json](prompt-research/runs/exp-0028/scores.json) | No description available. |
| [prompt-research/runs/exp-0029/dialogue.json](prompt-research/runs/exp-0029/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0029/prompts.py](prompt-research/runs/exp-0029/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0029/scores.json](prompt-research/runs/exp-0029/scores.json) | No description available. |
| [prompt-research/runs/exp-0030/dialogue.json](prompt-research/runs/exp-0030/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0030/prompts.py](prompt-research/runs/exp-0030/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0030/scores.json](prompt-research/runs/exp-0030/scores.json) | No description available. |
| [prompt-research/runs/exp-0031/dialogue.json](prompt-research/runs/exp-0031/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0031/prompts.py](prompt-research/runs/exp-0031/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0031/scores.json](prompt-research/runs/exp-0031/scores.json) | No description available. |
| [prompt-research/runs/exp-0032/dialogue.json](prompt-research/runs/exp-0032/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0032/prompts.py](prompt-research/runs/exp-0032/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0032/scores.json](prompt-research/runs/exp-0032/scores.json) | No description available. |
| [prompt-research/runs/exp-0033/dialogue.json](prompt-research/runs/exp-0033/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0033/prompts.py](prompt-research/runs/exp-0033/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0033/scores.json](prompt-research/runs/exp-0033/scores.json) | No description available. |
| [prompt-research/runs/exp-0034/dialogue.json](prompt-research/runs/exp-0034/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0034/prompts.py](prompt-research/runs/exp-0034/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0034/scores.json](prompt-research/runs/exp-0034/scores.json) | No description available. |
| [prompt-research/runs/exp-0035/dialogue.json](prompt-research/runs/exp-0035/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0035/prompts.py](prompt-research/runs/exp-0035/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0035/scores.json](prompt-research/runs/exp-0035/scores.json) | No description available. |
| [prompt-research/runs/exp-0037/dialogue.json](prompt-research/runs/exp-0037/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0037/prompts.py](prompt-research/runs/exp-0037/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0037/scores.json](prompt-research/runs/exp-0037/scores.json) | No description available. |
| [prompt-research/runs/exp-0038/dialogue.json](prompt-research/runs/exp-0038/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0038/prompts.py](prompt-research/runs/exp-0038/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0038/scores.json](prompt-research/runs/exp-0038/scores.json) | No description available. |
| [prompt-research/runs/exp-0040/dialogue.json](prompt-research/runs/exp-0040/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0040/prompts.py](prompt-research/runs/exp-0040/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0040/scores.json](prompt-research/runs/exp-0040/scores.json) | No description available. |
| [prompt-research/runs/exp-0041/dialogue.json](prompt-research/runs/exp-0041/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0041/prompts.py](prompt-research/runs/exp-0041/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0041/scores.json](prompt-research/runs/exp-0041/scores.json) | No description available. |
| [prompt-research/runs/exp-0042/dialogue.json](prompt-research/runs/exp-0042/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0042/prompts.py](prompt-research/runs/exp-0042/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0042/scores.json](prompt-research/runs/exp-0042/scores.json) | No description available. |
| [prompt-research/runs/exp-0043/dialogue.json](prompt-research/runs/exp-0043/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0043/prompts.py](prompt-research/runs/exp-0043/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0043/scores.json](prompt-research/runs/exp-0043/scores.json) | No description available. |
| [prompt-research/runs/exp-0044/dialogue.json](prompt-research/runs/exp-0044/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0044/prompts.py](prompt-research/runs/exp-0044/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0044/scores.json](prompt-research/runs/exp-0044/scores.json) | No description available. |
| [prompt-research/runs/exp-0045/dialogue.json](prompt-research/runs/exp-0045/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0045/prompts.py](prompt-research/runs/exp-0045/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0045/scores.json](prompt-research/runs/exp-0045/scores.json) | No description available. |
| [prompt-research/runs/exp-0046/dialogue.json](prompt-research/runs/exp-0046/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0046/prompts.py](prompt-research/runs/exp-0046/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0046/scores.json](prompt-research/runs/exp-0046/scores.json) | No description available. |
| [prompt-research/runs/exp-0047/dialogue.json](prompt-research/runs/exp-0047/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0047/prompts.py](prompt-research/runs/exp-0047/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0047/scores.json](prompt-research/runs/exp-0047/scores.json) | No description available. |
| [prompt-research/runs/exp-0048/dialogue.json](prompt-research/runs/exp-0048/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0048/prompts.py](prompt-research/runs/exp-0048/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0048/scores.json](prompt-research/runs/exp-0048/scores.json) | No description available. |
| [prompt-research/runs/exp-0049/dialogue.json](prompt-research/runs/exp-0049/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0049/prompts.py](prompt-research/runs/exp-0049/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0049/scores.json](prompt-research/runs/exp-0049/scores.json) | No description available. |
| [prompt-research/runs/exp-0050/dialogue.json](prompt-research/runs/exp-0050/dialogue.json) | No description available. |
| [prompt-research/runs/exp-0050/prompts.py](prompt-research/runs/exp-0050/prompts.py) | Mutable prompt material for autoresearch-style optimization. |
| [prompt-research/runs/exp-0050/scores.json](prompt-research/runs/exp-0050/scores.json) | No description available. |
| [pyproject.toml](pyproject.toml) | No description available. |
| [requirements.txt](requirements.txt) | No description available. |
| [reviews/admin-backend-code-review.md](reviews/admin-backend-code-review.md) | **Reviewer:** Claude Code |
| [tailwind.config.js](tailwind.config.js) | No description available. |
| [tailwind.css](tailwind.css) | No description available. |
| [uv.lock](uv.lock) | No description available. |
| [vercel-costs.csv](vercel-costs.csv) | No description available. |
| [vercel.json](vercel.json) | No description available. |

<!-- LIBRARIAN-INDEX-END -->
