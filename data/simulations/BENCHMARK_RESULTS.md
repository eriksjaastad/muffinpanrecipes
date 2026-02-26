# Dialogue Prompt Style A/B Results

Date: 2026-02-26
Script: `scripts/simulate_dialogue_week.py`
Model: `openai/gpt-4o-mini`
Config: `--runs 1 --ticks-per-day 2 --mode ollama`

## Test Matrix (5 concepts × 2 prompt styles)

| Concept | Full QA | Scene QA | Delta (Scene-Full) |
|---|---:|---:|---:|
| Jalapeño Corn Dog Bites | 90 | 97 | +7 |
| Mini Shepherd's Pies | 94 | 95 | +1 |
| Lemon Ricotta Breakfast Muffins | 94 | 96 | +2 |
| Korean BBQ Meatball Cups | 79 | 91 | +12 |
| Brown Butter Pecan Tassies | 93 | 92 | -1 |

## Aggregate

- Full average QA: **90.0**
- Scene average QA: **94.2**
- Net lift from scene style: **+4.2**
- Real inference check: **10/10 true**

## Qualitative read

- `scene` style reduced prompt echo risk by removing structured labels on turn 2+.
- Turn-1 scene setup + explicit deadline gave stronger situational grounding without forcing template-like outputs.
- One concept regressed slightly (`Brown Butter Pecan Tassies`, -1), but overall gains were consistent.

## Recommendation

Adopt `--prompt-style scene` as the default for dialogue simulations and keep `full` for backward-compat and diagnostics.

## Output artifacts

### Full style
- `data/simulations/sim-20260226-125103-jalape-o-corn-dog-bites-openai_gpt-4o-mini-full-run1-full-week.json`
- `data/simulations/sim-20260226-125123-mini-shepherd-s-pies-openai_gpt-4o-mini-full-run1-full-week.json`
- `data/simulations/sim-20260226-125147-lemon-ricotta-breakfast-muffins-openai_gpt-4o-mini-full-run1-full-week.json`
- `data/simulations/sim-20260226-125208-korean-bbq-meatball-cups-openai_gpt-4o-mini-full-run1-full-week.json`
- `data/simulations/sim-20260226-125230-brown-butter-pecan-tassies-openai_gpt-4o-mini-full-run1-full-week.json`

### Scene style
- `data/simulations/sim-20260226-125251-jalape-o-corn-dog-bites-openai_gpt-4o-mini-scene-run1-full-week.json`
- `data/simulations/sim-20260226-125309-mini-shepherd-s-pies-openai_gpt-4o-mini-scene-run1-full-week.json`
- `data/simulations/sim-20260226-125328-lemon-ricotta-breakfast-muffins-openai_gpt-4o-mini-scene-run1-full-week.json`
- `data/simulations/sim-20260226-125346-korean-bbq-meatball-cups-openai_gpt-4o-mini-scene-run1-full-week.json`
- `data/simulations/sim-20260226-125403-brown-butter-pecan-tassies-openai_gpt-4o-mini-scene-run1-full-week.json`
