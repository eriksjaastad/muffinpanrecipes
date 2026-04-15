# DIRECTION.md - Muffin Pan Recipes

## Goal
An AI-driven content platform disguised as a recipe website. A fictional bakery team (AI characters with distinct personalities) produces one muffin tin recipe per week through a six-day story arc. The recipes are real; the creative drama between characters is the hook.

## Mode
Continual development. The generative loop (Mon-Sun pipeline) is running. Work now focuses on hardening reliability, improving content quality, and tuning the AI creative team rather than shipping new infrastructure.

## North Star
A self-sustaining weekly content engine where AI agents autonomously produce publishable recipes with compelling character-driven narratives -- requiring minimal human intervention beyond taste-level tuning.

## Current Focus
- Pipeline reliability: atomic writes, input validation, dry-run guards, score capping
- Content quality: anti-repetition, title constraints, editorial QA gates, title debate stages
- Model evaluation: Gemini provider, Nano Banana comparison, judge scoring
- CI hardening: Claude auto-review workflow, PR label enforcement
