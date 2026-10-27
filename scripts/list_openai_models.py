#!/usr/bin/env python3
"""List OpenAI models available through OpenRouter.

Requires ``OPENROUTER_API_KEY`` (normally injected with ``doppler run``).
``OPENROUTER_MODEL_ALLOWLIST`` optionally narrows the models accepted by the
existing router policy; this script has no filesystem or path overrides.
"""

from __future__ import annotations

import json
import os

from backend.utils.model_router import OPENROUTER_BASE_URL, ensure_openai_model_allowed


def _native_openai_id(model_id: str) -> str | None:
    """Return the native model id for an OpenRouter OpenAI catalog entry."""
    provider, separator, native_id = model_id.partition("/")
    if separator and provider.lower() != "openai":
        return None
    return native_id if separator else model_id


def is_chat_like(model_id: str) -> bool:
    native_id = _native_openai_id(model_id)
    if native_id is None:
        return False
    low = native_id.lower()
    blocked = ["embed", "whisper", "tts", "transcribe", "moderation", "image", "dall", "realtime", "audio"]
    if any(b in low for b in blocked):
        return False
    return low.startswith(("gpt", "o1", "o3", "o4"))


def main() -> None:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    from openai import OpenAI

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key)
    models = sorted([m.id for m in client.models.list().data])
    chat_models = [m for m in models if is_chat_like(m)]

    allowlisted = []
    blocked = []
    for openrouter_id in chat_models:
        m = _native_openai_id(openrouter_id)
        assert m is not None
        try:
            ensure_openai_model_allowed(m)
            allowlisted.append(m)
        except Exception:
            blocked.append(m)

    print("=== allowlisted_chat_models ===")
    for m in allowlisted:
        print(m)

    print("\n=== json ===")
    print(json.dumps({"allowlisted_chat_models": allowlisted, "blocked_chat_models": blocked, "all_models": models}, indent=2))


if __name__ == "__main__":
    main()
