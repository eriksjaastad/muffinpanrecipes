# Muffin Pan Recipes - Automation & Scripts (`scripts/`)

This directory contains Python and Shell scripts for automating the recipe generation, image photography, and site building processes.

## 📡 Image Generation Pipeline (SSH Agent + RunPod)

The most complex part of the system is the 4-step automated photography pipeline:

1.  **`generate_image_prompts.py`**: Analyzes recipes and generates 3 high-key SDXL prompts per recipe using AI Router (Local DeepSeek-R1 or Cloud).
2.  **`trigger_generation.py`**: Uploads jobs and helper scripts to Cloudflare R2 (native boto3 uploader).
3.  **`direct_harvest.py`**: Executed on remote GPU pods (RunPod) to generate images directly via Stability AI API.
4.  **`art_director.py`**: The Art Director agent reviews the generated variants from `__temp_harvest/` and moves the winner to `src/assets/images/recipes/`.

## 🏗️ Build & Site Management

- **`build_site.py`**: Compiles Markdown recipes and templates into the final static site in `src/`.
- **`optimize_images.py`**: Handles image compression and formatting for the web.
- **`warden_audit.py`**: Runs project-wide health checks and validates repository state.

## 🎭 Simulation & Dialogue

- **`simulate_dialogue_week.py`**: Runs a full week of character-driven dialogue between the AI Creative Team.
- **`grade_simulations.py`**: Uses an LLM to grade the quality and character consistency of generated dialogues.
- **`judge_conversation.py`**: Provides real-time feedback on agent interactions.

## 🛠️ Utilities & Hooks

- `hooks/`: Git hooks for pre-commit/pre-push validation.
- `install-hooks.sh`: Script to set up project git hooks.
- `validate_env.py`: Ensures all required secrets (Doppler) and environment variables are present.
