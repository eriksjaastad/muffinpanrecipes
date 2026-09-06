# Muffin Pan Recipes - Automation & Scripts (`scripts/`)

This directory contains Python and Shell scripts for automating the recipe generation, image photography, and site building processes.

## 📡 Image Generation Pipeline (SSH Agent + RunPod)

The most complex part of the system is the 4-step automated photography pipeline:

1.  **`generate_image_prompts.py`**: Analyzes recipes and generates 3 high-key SDXL prompts per recipe using AI Router (Local DeepSeek-R1 or Cloud).
2.  **`trigger_generation.py`**: Uploads jobs and helper scripts to Cloudflare R2 (native boto3 uploader).
3.  **`direct_harvest.py`**: Executed on remote GPU pods (RunPod) to generate images directly via Stability AI API.
4.  **`art_director.py`**: The Art Director agent reviews the generated variants from `__temp_harvest/` and moves the winner to `src/assets/images/recipes/`.
5.  **`compare_image_providers.py`**: Generates side-by-side Stability AI vs Nano Banana comparisons.

## 🏗️ Build & Site Management

- **`optimize_images.py`**: Handles image compression and formatting for the web.

## 🎭 Simulation & Dialogue

- **`simulate_dialogue_week.py`**: Runs a full week of character-driven dialogue between the AI Creative Team.
- **`grade_simulations.py`**: Uses an LLM to grade the quality and character consistency of generated dialogues.
- **`judge_conversation.py`**: Provides real-time feedback on agent interactions.

## 🧪 Conversation Lab

See `docs/conversation-lab/PROTOCOL.md` for the full method (card #6492).

- **`review_episode.py`**: weekly measurement pass over a week's dialogue against the 6-dimension conversation rubric - zero paid API calls. This is the weekly read; it does not import `conversation_metrics.py`.
- **`conversation_lab.py`**: experiment runner with `baseline` / `ab` / `calibrate` / `pairs` subcommands for offline blind position-swapped A/B testing of a single prompt lever; a targeted `ab --sweep DIR` (see `docs/conversation-lab/PROTOCOL.md`'s "Weekly sweep" section, and `ab --help` for whether it has landed yet) would run a batch of variant files against one shared control on the test bed instead of paying for a fresh control per variant.
- **`conversation_metrics.py`**: deterministic (non-LLM) dialogue metrics - turn length, shared-rules echo detection, cast coverage, and the structure-tell rates (dash-clause, frame-claim, agree-opener, pitch-vocabulary) - used by `conversation_lab.py`'s `baseline` command, not by `review_episode.py`. `brand_term_rate` (the muffin-pan mechanism-justification rate named in PROTOCOL.md's "Areas") is not yet one of them.
- **`conversation_heatmap.py`**: cross-corpus phrase/structure heat map across every published week, writing the matrix to `docs/conversation-lab/results/` - see `--help` for its flags and `docs/conversation-lab/EXPERIMENTS.md`'s "Heat map baseline v0" section for the 2026-09-06 manual read done ahead of the script's existence.

`conversation_lab.py ab` and `conversation_lab.py calibrate` call `DIALOGUE_MODEL` / `JUDGE_MODEL` and need `doppler run -- `. `--dry-run` and `baseline` are free and need no Doppler wrapper.

## 🛠️ Utilities

- `validate_env.py`: Ensures all required secrets (Doppler) and environment variables are present.
