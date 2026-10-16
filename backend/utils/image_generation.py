"""Image generation through OpenRouter (Gemini image models, "Nano Banana")."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import requests

from backend.utils import model_router

try:
    from api_cost_tracker import log_call
except ImportError:
    log_call = lambda *a, **kw: None


def _extract_image_bytes(payload: dict[str, Any]) -> bytes:
    """The first generated image in an OpenRouter chat-completions response.

    OpenRouter returns images as ``choices[0].message.images[].image_url.url``
    data URLs (``data:image/png;base64,...``).
    """
    for choice in payload.get("choices") or []:
        for image in (choice.get("message") or {}).get("images") or []:
            url = (image.get("image_url") or {}).get("url") or ""
            _, sep, data = url.partition("base64,")
            if sep and data:
                return base64.b64decode(data)
    raise RuntimeError("No inline image data found in Nano Banana response")


def generate_nano_banana_image(
    prompt: str,
    api_key: str,
    *,
    model: str = "gemini-3.1-flash-image",
    aspect_ratio: str = "1:1",
    image_size: str | None = None,
    temperature: float = 0.4,
    timeout_s: float | None = None,
) -> bytes:
    """Generate a single image with a Gemini image model through OpenRouter.

    ``api_key`` is OPENROUTER_API_KEY. ``model`` is Google's id; it is mapped
    by model_router.OPENROUTER_MODEL_IDS (an unmapped id raises) and pinned to
    Google AI Studio with no fallbacks. ``image_size`` is Gemini's "1K"/"2K"/"4K".
    ``timeout_s`` bounds the whole call, as art_director's shoot deadline
    needs; with None a stalled call runs until the host kills it. Raises
    RuntimeError on a non-200 response, a response with no image, or a call
    that outlasts ``timeout_s``.
    """
    openrouter_model = model_router.openrouter_model_id("google", model)
    image_config: dict[str, Any] = {"aspect_ratio": aspect_ratio}
    if image_size:
        image_config["image_size"] = image_size

    deadline = time.monotonic() + timeout_s if timeout_s else None
    with requests.post(
        f"{model_router.OPENROUTER_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": openrouter_model,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
            "image_config": image_config,
            "temperature": temperature,
            "provider": model_router.openrouter_provider_route(openrouter_model),
            "usage": {"include": True},
        },
        timeout=timeout_s,
        stream=True,
    ) as response:
        # requests' timeout only limits each wait for bytes, so a server that
        # trickles bytes (keepalive whitespace) could hold the call open past
        # the shoot deadline; checking the clock per chunk bounds the total.
        chunks = []
        for chunk in response.iter_content(chunk_size=65536):
            if deadline is not None and time.monotonic() > deadline:
                raise RuntimeError(f"OpenRouter image call exceeded {timeout_s}s")
            chunks.append(chunk)
    body = b"".join(chunks)
    log_call("openrouter", service="image", project="muffinpanrecipes", caller="image_generation.nano_banana")
    if response.status_code != 200:
        raise RuntimeError(f"OpenRouter image error {response.status_code}: {body[:200].decode(errors='replace')}")

    payload = json.loads(body)
    model_router.record_external_cost(
        "openrouter", openrouter_model, (payload.get("usage") or {}).get("cost"),
    )
    return _extract_image_bytes(payload)
