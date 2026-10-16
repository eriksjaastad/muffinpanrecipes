"""Image generation through OpenRouter (Gemini image models, "Nano Banana")."""

from __future__ import annotations

import base64
import threading
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
    # Say what came back instead: an upstream error object or the model's text.
    choices = payload.get("choices") or [{}]
    said = payload.get("error") or (choices[0].get("message") or {}).get("content")
    raise RuntimeError(f"No inline image data found in Nano Banana response: {str(said)[:200]!r}")


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

    outcome: dict[str, Any] = {}

    def post() -> None:
        try:
            response = requests.post(
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
            )
            log_call("openrouter", service="image", project="muffinpanrecipes", caller="image_generation.nano_banana")
            if response.status_code != 200:
                raise RuntimeError(f"OpenRouter image error {response.status_code}: {response.text[:200]}")
            payload = response.json()
            # Recorded here, not by the caller, so an image that arrives after
            # the caller gave up is still in the cost log: it was billed.
            model_router.record_external_cost(
                "openrouter", openrouter_model, (payload.get("usage") or {}).get("cost"),
            )
            outcome["payload"] = payload
        except BaseException as exc:  # handed to the caller below
            outcome["error"] = exc

    # requests' timeout only limits each wait for bytes (connect, headers,
    # each read), so a slow or trickling response could run past the shoot
    # deadline. Waiting on the request from outside bounds the whole call,
    # whatever stage it stalls in. A daemon thread never holds the process
    # open; its own requests timeout ends it.
    worker = threading.Thread(target=post, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        raise RuntimeError(f"OpenRouter image call exceeded {timeout_s}s")
    if "error" in outcome:
        raise outcome["error"]
    return _extract_image_bytes(outcome["payload"])
