# AI Creative Team - Utilities (`backend/utils/`)

This directory provides a unified infrastructure for LLM routing, recipe prompting, and operational safeguards.

## 📡 `model_router.py` - Unified LLM Orchestration

Every LLM call in the project (OpenAI, Anthropic, or future providers) must go through `generate_response()`.

### 🛡️ Safety & Policy
- **Fail-Closed Allowlisting**: Models must be explicitly allowed in `DEFAULT_OPENAI_ALLOWLIST` or `DEFAULT_ANTHROPIC_ALLOWLIST`.
- **Hard Blocks**: Explicit blocks on expensive or high-end models (e.g., `o1-pro`, `claude-opus-4-6`) to prevent accidental cost spikes.
- **Judge Model Policy**: A separate `JUDGE_ALLOWLIST` is used for post-generation quality review, permitting higher-tier models (like Opus) only for evaluation.

### 💰 Cost Tracking
- All calls are logged with token counts and estimated USD costs.
- Use `get_cost_summary()` to retrieve a per-model breakdown of usage and spending.

## 🥧 `recipe_prompts.py` - Culinary Logic

Implements the **"Muffin Pan Chef"** system prompt directives:
- **No-Fluff Policy**: Eliminates conversational preamble.
- **Dual-Measurement Standard**: Enforces `Metric (Imperial)` formatting.
- **Mathematical Scaling**: Ensures recipes fit exactly into 12 standard, 6 jumbo, or 24 mini cups.

## 🛠️ Operational Tools

- **`atomic.py`**: Safe file writing to prevent data corruption.
- **`discord.py`**: Webhook integration for real-time notifications (e.g., "Margaret has finished the recipe").
- **`publish_schedule.py`**: DST-aware scheduling for automated recipe releases.
- **`errors.py`**: Custom exceptions for agent and pipeline failures.
