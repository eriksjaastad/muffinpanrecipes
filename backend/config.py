"""Environment configuration for Muffin Pan Recipes.

Single source of truth for environment detection. Replace all raw
os.environ.get("LOCAL_DEV") checks with imports from this module.

Usage:
    from backend.config import config

    if config.is_local_dev:
        ...  # filesystem, no OAuth
    if config.is_vercel:
        ...  # cloud storage, production models
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

        Override via DIALOGUE_MODEL env var for benchmarking different providers.
        """
        override = os.environ.get("DIALOGUE_MODEL", "").strip()
        if override:
            return override
        return "openai/gpt-5-mini"

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
            f"model={self.dialogue_model!r}, "
            f"auth_bypass={self.auth_bypass})"
        )


# Singleton — import this everywhere.
config = _Config()
