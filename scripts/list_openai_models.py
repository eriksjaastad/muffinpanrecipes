#!/usr/bin/env python3
"""List accessible OpenAI models (chat-capable filtered + raw)."""

from __future__ import annotations

import json
import os

from backend.utils.model_router import ensure_openai_model_allowed


def is_chat_like(model_id: str) -> bool:
    low = model_id.lower()
    blocked = ["embed", "whisper", "tts", "transcribe", "moderation", "image", "dall", "realtime", "audio"]
    if any(b in low for b in blocked):
        return False
    return low.startswith(("gpt", "o1", "o3", "o4"))


def main() -> None:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    from openai import OpenAI

    client = OpenAI(api_key=key)
    models = sorted([m.id for m in client.models.list().data])
    chat_models = [m for m in models if is_chat_like(m)]

    allowlisted = []
    blocked = []
    for m in chat_models:
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
