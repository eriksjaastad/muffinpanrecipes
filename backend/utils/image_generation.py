"""Image generation helpers for provider comparisons (Stability + Nano Banana)."""

from __future__ import annotations

import base64
from typing import Any

import requests

try:
    from ai_cost_tracker import log_call
except ImportError:
    log_call = lambda *a, **kw: None


def generate_stability_image(
    prompt: str,
    api_key: str,
    *,
    engine_id: str = "stable-diffusion-xl-1024-v1-0",
    width: int = 1024,
    height: int = 1024,
    steps: int = 30,
    cfg_scale: int = 7,
    timeout: int = 60,
) -> bytes:
    """Generate a single image via Stability API and return raw PNG bytes."""
    url = f"https://api.stability.ai/v1/generation/{engine_id}/text-to-image"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "text_prompts": [{"text": prompt}],
        "cfg_scale": cfg_scale,
        "height": height,
        "width": width,
        "samples": 1,
        "steps": steps,
    }

    response = requests.post(url, headers=headers, json=body, timeout=timeout)
    log_call("stability", service="image", project="muffinpanrecipes", caller="image_generation.stability")
    if response.status_code != 200:
        raise RuntimeError(f"Stability API error {response.status_code}: {response.text[:200]}")

    data = response.json()
    artifacts = data.get("artifacts") or []
    if not artifacts:
        raise RuntimeError("Stability API returned no artifacts")

    b64 = artifacts[0].get("base64") or artifacts[0].get("b64_json")
    if not b64:
        raise RuntimeError("Stability API response missing base64 image")

    return base64.b64decode(b64)


def _extract_inline_image_bytes(response: Any) -> bytes:
    """Extract inline image bytes from a google-genai response."""
    parts = getattr(response, "parts", None) or []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline is None:
            continue
        data = getattr(inline, "data", None)
        if data:
            if isinstance(data, bytes):
                return data
            return base64.b64decode(data)

    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        cand_parts = getattr(content, "parts", None) or []
        for part in cand_parts:
            inline = getattr(part, "inline_data", None)
            if inline is None:
                continue
            data = getattr(inline, "data", None)
            if data:
                if isinstance(data, bytes):
                    return data
                return base64.b64decode(data)

    raise RuntimeError("No inline image data found in Nano Banana response")


def generate_nano_banana_image(
    prompt: str,
    api_key: str,
    *,
    model: str = "gemini-2.5-flash-image",
    aspect_ratio: str = "1:1",
    image_size: str | None = None,
    temperature: float = 0.4,
) -> bytes:
    """Generate a single image via Gemini image models (Nano Banana)."""
    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        raise RuntimeError("google-genai package is not installed") from e

    image_config_kwargs: dict[str, Any] = {"aspect_ratio": aspect_ratio}
    if image_size:
        image_config_kwargs["image_size"] = image_size

    config = types.GenerateContentConfig(
        temperature=temperature,
        response_modalities=["IMAGE"],
        image_config=types.ImageConfig(**image_config_kwargs),
    )

    with genai.Client(api_key=api_key) as client:
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=config,
        )

    log_call("google", service="imagen", project="muffinpanrecipes", caller="image_generation.nano_banana")
    return _extract_inline_image_bytes(response)
