"""Environment configuration for Muffin Pan Recipes.

Single source of truth for environment detection AND model selection.
Replace all raw os.environ.get() checks with imports from this module.

Usage:
    from backend.config import config

    if config.is_local_dev:
        ...  # filesystem, no OAuth
    if config.is_vercel:
        ...  # cloud storage, production models

Model Configuration:
    All model defaults live here. There are three ways to set them,
    in order of precedence (highest first):

    1. API call body (per-request override):
       curl -X POST http://localhost:8000/api/cron/monday \\
         -H "Authorization: Bearer $CRON_SECRET" \\
         -H "Content-Type: application/json" \\
         -d '{"concept": "Mini Shepherd Pies", "model": "openai/gpt-5.1"}'

    2. Doppler (per-environment default):
       doppler secrets set DIALOGUE_MODEL "openai/gpt-5.1" --project muffinpanrecipes --config dev
       doppler secrets set RECIPE_MODEL "openai/gpt-5.1" --project muffinpanrecipes --config dev

    3. Hardcoded fallback in this file: "openai/gpt-5-mini"

    For the CLI script, use --model or --character-models:
       PYTHONPATH=. uv run scripts/simulate_dialogue_week.py \\
         --concept "Mini Shepherd Pies" --model openai/gpt-5.1
       PYTHONPATH=. uv run scripts/simulate_dialogue_week.py \\
         --concept "Mini Shepherd Pies" \\
         --character-models '{"Margaret Chen":"openai/gpt-5.1","default":"openai/gpt-5-mini"}'

    Available models (must be in model_router allowlists):
      OpenAI:    openai/gpt-5-mini, openai/gpt-5-nano, openai/gpt-5.1
      Anthropic: anthropic/claude-haiku-4-5-20251001, anthropic/claude-sonnet-4-6
"""

from __future__ import annotations

import os


class _Config:
    """Immutable environment configuration. Instantiated once at import time."""

    def __init__(self) -> None:
        # Vercel injects VERCEL_ENV automatically in all deployed environments.
        self._vercel_env: str | None = os.environ.get("VERCEL_ENV")

        # LOCAL_DEV=true is set manually when running locally.
        self._local_dev: bool = os.environ.get("LOCAL_DEV", "").lower() == "true"

    # ------------------------------------------------------------------
    # Core flags
    # ------------------------------------------------------------------

    @property
    def is_local_dev(self) -> bool:
        """True when LOCAL_DEV=true — local machine, filesystem storage."""
        return self._local_dev

    @property
    def is_vercel(self) -> bool:
        """True when running on Vercel (any tier: production, preview, development)."""
        return self._vercel_env is not None

    # ------------------------------------------------------------------
    # Derived attributes
    # ------------------------------------------------------------------

    @property
    def environment(self) -> str:
        """Human-readable environment string.

        Returns one of: "local", "development", "preview", "production".
        """
        if self._local_dev:
            return "local"
        if self._vercel_env:
            return self._vercel_env  # Vercel sets "production" | "preview" | "development"
        # Running without either flag — treat as local for safety.
        return "local"

    @property
    def storage_backend(self) -> str:
        """Which storage backend to use.

        Returns "filesystem" for local dev, "cloud" on Vercel.
        Override via STORAGE_BACKEND env var if needed.
        """
        override = os.environ.get("STORAGE_BACKEND", "").strip()
        if override:
            return override
        return "filesystem" if self.is_local_dev else "cloud"

    @property
    def dialogue_model(self) -> str:
        """Default model for dialogue generation.

        Override via DIALOGUE_MODEL env var or Doppler.
        Used by: simulate_dialogue_week.py, cron_routes.py dialogue stages.

        Available models (must be in model_router allowlists):
          OpenAI:    openai/gpt-5-mini, openai/gpt-5-nano, openai/gpt-5.1
          Anthropic: anthropic/claude-haiku-4-5-20251001, anthropic/claude-sonnet-4-6

        Set via Doppler:  doppler secrets set DIALOGUE_MODEL "openai/gpt-5.1"
        Set via CLI:      DIALOGUE_MODEL=openai/gpt-5.1 uv run scripts/simulate_dialogue_week.py ...
        Set via API:      pass --model flag or character_models JSON to simulate_dialogue_week
        """
        override = os.environ.get("DIALOGUE_MODEL", "").strip()
        if not override:
            raise RuntimeError(
                "DIALOGUE_MODEL is not set. Set it via Doppler or environment variable. "
                "No silent fallback — you must explicitly choose a model."
            )
        return override

    @property
    def recipe_model(self) -> str:
        """Default model for recipe/copywriting generation.

        Override via RECIPE_MODEL env var or Doppler.
        Used by: recipe_prompts.py (recipe generation, description generation).

        Same model options as dialogue_model.
        Set via Doppler:  doppler secrets set RECIPE_MODEL "openai/gpt-5.1"
        """
        override = os.environ.get("RECIPE_MODEL", "").strip()
        if not override:
            raise RuntimeError(
                "RECIPE_MODEL is not set. Set it via Doppler or environment variable. "
                "No silent fallback — you must explicitly choose a model."
            )
        return override

    @property
    def judge_model(self) -> str:
        """Model for post-generation quality review (judge tier).

        Expensive models allowed here — this is judgment, not generation.
        Override via JUDGE_MODEL env var or Doppler.

        Allowed models (JUDGE_ALLOWLIST in model_router):
          Anthropic: claude-opus-4-6, claude-sonnet-4-6
          OpenAI:    gpt-5.1, gpt-5.2

        Set via Doppler:  doppler secrets set JUDGE_MODEL "anthropic/claude-opus-4-6"
        """
        override = os.environ.get("JUDGE_MODEL", "").strip()
        if not override:
            return "anthropic/claude-sonnet-4-6"  # sensible default — cheaper than Opus
        return override

    @property
    def auth_bypass(self) -> bool:
        """True when OAuth should be bypassed (local dev only).

        Explicitly blocked on Vercel — prevents LOCAL_DEV=true leaking to production.
        """
        if self._vercel_env:  # We're on Vercel — never bypass auth
            return False
        return self.is_local_dev

    def __repr__(self) -> str:
        return (
            f"Config(environment={self.environment!r}, "
            f"storage={self.storage_backend!r}, "
            f"dialogue_model={self.dialogue_model!r}, "
            f"recipe_model={self.recipe_model!r}, "
            f"auth_bypass={self.auth_bypass})"
        )


# Singleton — import this everywhere.
config = _Config()
