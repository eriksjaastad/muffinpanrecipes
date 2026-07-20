# Seat-fit fixtures

These fixtures describe five production model jobs using tracked episode, simulation, prompt, character, image, and policy assets. Gold expectations live under `labels/`; the deterministic hash split reserves remainder 0 for sealed evaluation behind `MODEL_BENCH_UNSEAL=1`. The committed labels are process-isolated, not secret.

## Existing dialogue benchmark decision

The existing dialogue benchmark remains project-owned. `scripts/run_dialogue_benchmarks.py`, `simulate_dialogue_week.py`, `judge_conversation.py`, and the project-specific character/continuity rubric understand the weekly creative system better than a generic runner. The portfolio model-bench should consume this project's `dialogue_generation` fixtures and existing results in `data/simulations/`; it must not duplicate the runner or create a fifth benchmark program.

## Provenance

- dialogue: tracked simulation transcripts plus character biographies
- recipe/copy: tracked episode records and production prompt/validation modules
- vision: committed production image sets and the art-director rubric
- image generation: exact prompts preserved in `data/episodes/2026-W10-vision-test.json`
- editorial judge: tracked full-week simulations and their recorded comparison QA scores

No runtime code, environment value, or production pin changes in this card.
