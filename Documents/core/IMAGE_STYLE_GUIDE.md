# Muffin Pan Recipes: Image Style Guide

Visual identity for all food photography on MuffinPanRecipes.com.

> **Source of truth for generation is code, not this file.** `Documents/` is in
> `.vercelignore`, so this doc never reaches the image Lambda. The live prompt is
> `ArtDirector._STYLE_CLAUSE` + `_MUFFIN_FORM_CLAUSE` in
> `backend/agents/art_director.py`. Keep this doc and that constant in sync; edit
> the constant to change what actually ships.

## The Aesthetic: "Warm Rustic" (approved 2026-07)

Replaces the old "Clean Kitchen Editorial" white-marble/high-key look, which read
as the default AI-food-photo aesthetic with no personality. The house look is now
warm, cozy, and appetizing — moody but inviting, artisanal, full of character.

## Lighting
- Warm golden-hour light, soft and directional.
- Rich warm amber tones; soft, deep shadows (never flat, never clinical).
- Gentle steam where it fits the dish.
- NOT flat high-key white studio lighting.

## Composition & Surface
- Reclaimed-wood and slate surfaces; rustic terracotta linen.
- Minimal props (a linen napkin, a vintage fork, scattered herb sprigs).
- Every portion reads as muffin-tin-made: round, flared, ridged muffin-cup shape.
- **No stacking:** portions sit flat, level, and separate — never piled, leaning,
  or on top of one another. Whole and intact (one deliberate cross-section allowed
  in the hero shot only).

## Shot Variety (hero rotates across recipes — do not let every hero be the same)
- `macro_closeup` — 100mm f/2.0, one item filling frame, 15-degree low angle.
- `overhead_flatlay` — 90-degree overhead, full tin, portions seated in the cups.
- `hero_threequarter` — 85mm f/2.8, 2–3 items on a rustic board (out of the pan),
  one broken open. **This is the pulled-back / outside-the-pan look.**

## Negative Constraints
- No people, hands, text, or watermarks.
- No stacked or piled food.
- No flat high-key white studio lighting.
