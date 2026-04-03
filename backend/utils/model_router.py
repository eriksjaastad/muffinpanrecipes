"""Unified model router for OpenAI + Anthropic + Google (+ future providers).

Every LLM call in the project goes through generate_response(). To add a new
provider (e.g. Gemini), just:
  1. Add a _generate_<provider>() function
  2. Add it to the PROVIDERS dict
  3. Add an allowlist/blocklist
  4. Add cost-per-M-token entries
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Optional

from backend.utils.logging import get_logger

# Centralized cost tracker (silent no-op if unavailable)
_tracker_path = os.environ.get("COST_TRACKER_PATH", os.path.expanduser("~/projects/synth-insight-labs/api-cost-tracker"))
sys.path.insert(0, _tracker_path)
try:
    from ai_cost_tracker import track as _central_track
except ImportError:
    _central_track = lambda resp, *a, **kw: resp

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# OpenAI model policy (fail-closed)
# OWNER RULE (do not relax without explicit Erik approval):
# ---------------------------------------------------------------------------
DEFAULT_OPENAI_ALLOWLIST = {
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.1",
}

HARD_BLOCKED_OPENAI_MODELS = {
    "gpt-5",
    "gpt-5.2",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "gpt-5.1-codex-max",
    "gpt-5-pro",
    "gpt-5.2-pro",
    "gpt-5.2-pro-2025-12-11",
    "gpt-5-pro-2025-10-06",
    "o1-pro",
    "o1-pro-2025-03-19",
}

# ---------------------------------------------------------------------------
# Anthropic model policy (fail-closed)
# OWNER RULE: Only cheap models allowed. No Opus without explicit approval.
# ---------------------------------------------------------------------------
DEFAULT_ANTHROPIC_ALLOWLIST = {
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
}

HARD_BLOCKED_ANTHROPIC_MODELS = {
    "claude-opus-4-6",
    "claude-opus-4-20250514",
}

# ---------------------------------------------------------------------------
# Google model policy (fail-closed)
# ---------------------------------------------------------------------------
DEFAULT_GOOGLE_ALLOWLIST = {
    # Text models (preview IDs per Gemini API pricing docs)
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview",
}

HARD_BLOCKED_GOOGLE_MODELS: set[str] = set()

# ---------------------------------------------------------------------------
# Judge model policy (separate from dialogue — expensive models allowed)
# Used for post-generation quality review, not content generation.
# ---------------------------------------------------------------------------
JUDGE_ALLOWLIST = {
    # Anthropic
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    # OpenAI
    "gpt-5.1",
    "gpt-5.2",
    # Google
    "gemini-3.1-pro-preview",
}

# ---------------------------------------------------------------------------
# Approximate cost per million tokens (USD) for cost tracking.
# Conservative estimates — update as pricing changes.
# ---------------------------------------------------------------------------
_COST_PER_M_TOKENS: dict[str, tuple[float, float]] = {
    # (input_cost_per_M, output_cost_per_M)
    # OpenAI — text
    "gpt-5-mini": (0.30, 1.20),
    "gpt-5-nano": (0.10, 0.40),
    "gpt-5.1": (1.00, 3.00),
    # OpenAI — vision (image tokens counted as input)
    "gpt-5-mini:vision": (0.30, 1.20),
    # Anthropic — text
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    # Anthropic — vision
    "claude-haiku-4-5-20251001:vision": (0.80, 4.00),
    # Google — text (Gemini API pricing)
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-3.1-flash-lite-preview": (0.25, 1.50),
    "gemini-3-flash-preview": (0.50, 3.00),
    # Google — image generation (image output priced separately; use output cost for images)
    "gemini-3.1-flash-image-preview": (0.50, 60.00),
}

# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------
_COST_LOG: list[dict] = []


def _record_cost(
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    costs = _COST_PER_M_TOKENS.get(model, (0.0, 0.0))
    estimated = (tokens_in * costs[0] + tokens_out * costs[1]) / 1_000_000
    _COST_LOG.append({
        "provider": provider,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "estimated_cost": estimated,
        "timestamp": time.time(),
    })


def get_cost_summary() -> dict:
    """Return per-model breakdown and total estimated cost."""
    by_model: dict[str, dict] = {}
    for entry in _COST_LOG:
        key = f"{entry['provider']}/{entry['model']}"
        if key not in by_model:
            by_model[key] = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "estimated_cost": 0.0}
        by_model[key]["calls"] += 1
        by_model[key]["tokens_in"] += entry["tokens_in"]
        by_model[key]["tokens_out"] += entry["tokens_out"]
        by_model[key]["estimated_cost"] += entry["estimated_cost"]

    total_cost = sum(m["estimated_cost"] for m in by_model.values())
    total_calls = sum(m["calls"] for m in by_model.values())
    total_tokens_in = sum(m["tokens_in"] for m in by_model.values())
    total_tokens_out = sum(m["tokens_out"] for m in by_model.values())

    return {
        "by_model": by_model,
        "total_cost": total_cost,
        "total_calls": total_calls,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
    }


def reset_cost_log() -> None:
    """Clear the cost log (call between benchmark runs)."""
    _COST_LOG.clear()


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
SUPPORTED_PROVIDERS = {"openai", "anthropic", "google"}  # extend when adding Gemini etc.


@dataclass
class RoutedModel:
    provider: str
    model: str


def parse_model(model: str) -> RoutedModel:
    if "/" in model:
        provider, model_name = model.split("/", 1)
        provider = provider.strip().lower()
        if provider in SUPPORTED_PROVIDERS:
            return RoutedModel(provider=provider, model=model_name.strip())
        raise RuntimeError(
            f"Unknown provider '{provider}'. Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
        )
    # No bare model names anymore — must specify provider
    raise RuntimeError(
        f"Model must include provider prefix (e.g. 'openai/{model}'). "
        f"Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
    )


# ---------------------------------------------------------------------------
# Allowlist helpers
# ---------------------------------------------------------------------------
def _allowed_openai_models() -> set[str]:
    raw = os.getenv("OPENAI_MODEL_ALLOWLIST", "").strip()
    if not raw:
        return set(DEFAULT_OPENAI_ALLOWLIST)
    return {m.strip() for m in raw.split(",") if m.strip()}


def ensure_openai_model_allowed(model: str) -> None:
    low = model.lower().strip()
    if low in HARD_BLOCKED_OPENAI_MODELS:
        raise RuntimeError(f"OpenAI model blocked by policy: {model}")
    allowed = _allowed_openai_models()
    if low not in allowed:
        raise RuntimeError(
            f"OpenAI model not allowlisted: {model}. Allowed: {', '.join(sorted(allowed))}"
        )


def _allowed_anthropic_models() -> set[str]:
    raw = os.getenv("ANTHROPIC_MODEL_ALLOWLIST", "").strip()
    if not raw:
        return set(DEFAULT_ANTHROPIC_ALLOWLIST)
    return {m.strip() for m in raw.split(",") if m.strip()}


def ensure_anthropic_model_allowed(model: str) -> None:
    low = model.lower().strip()
    if low in HARD_BLOCKED_ANTHROPIC_MODELS:
        raise RuntimeError(f"Anthropic model blocked by policy: {model}")
    allowed = _allowed_anthropic_models()
    if low not in allowed:
        raise RuntimeError(
            f"Anthropic model not allowlisted: {model}. Allowed: {', '.join(sorted(allowed))}"
        )


def _allowed_google_models() -> set[str]:
    raw = os.getenv("GOOGLE_MODEL_ALLOWLIST", "").strip()
    if not raw:
        return set(DEFAULT_GOOGLE_ALLOWLIST)
    return {m.strip() for m in raw.split(",") if m.strip()}


def ensure_google_model_allowed(model: str) -> None:
    low = model.lower().strip()
    if low in HARD_BLOCKED_GOOGLE_MODELS:
        raise RuntimeError(f"Google model blocked by policy: {model}")
    allowed = _allowed_google_models()
    if low not in allowed:
        raise RuntimeError(
            f"Google model not allowlisted: {model}. Allowed: {', '.join(sorted(allowed))}"
        )


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------
def _generate_openai(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    try:
        from openai import OpenAI, BadRequestError, NotFoundError
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e

    client = OpenAI(api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    input_items = []
    if system_prompt:
        input_items.append({"role": "system", "content": [{"type": "input_text", "text": system_prompt}]})
    input_items.append({"role": "user", "content": [{"type": "input_text", "text": prompt}]})

    def _from_responses() -> str:
        try:
            r = client.responses.create(model=model, input=input_items, temperature=temperature)
        except BadRequestError as e2:
            if "temperature" in str(e2).lower():
                r = client.responses.create(model=model, input=input_items)
            else:
                raise

        usage = getattr(r, "usage", None)
        _record_cost(
            "openai", model,
            getattr(usage, "input_tokens", 0) if usage else 0,
            getattr(usage, "output_tokens", 0) if usage else 0,
        )
        _central_track(r, "openai", project="muffinpanrecipes", caller="model_router.openai_responses")

        text = (getattr(r, "output_text", "") or "").strip()
        if text:
            return text

        parts = []
        for item in getattr(r, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                piece = getattr(content, "text", None)
                if piece:
                    parts.append(piece)
        return "\n".join(parts).strip()

    # First attempt: chat completions with temperature.
    try:
        response = client.chat.completions.create(
            model=model, messages=messages, temperature=temperature,
        )
        usage = response.usage
        _record_cost(
            "openai", model,
            getattr(usage, "prompt_tokens", 0) if usage else 0,
            getattr(usage, "completion_tokens", 0) if usage else 0,
        )
        _central_track(response, "openai", project="muffinpanrecipes", caller="model_router.openai")
        return (response.choices[0].message.content or "").strip()
    except BadRequestError as e:
        msg = str(e)
        low = msg.lower()
        if "temperature" in low and ("unsupported" in low or "default (1)" in low):
            response = client.chat.completions.create(model=model, messages=messages)
            usage = response.usage
            _record_cost(
                "openai", model,
                getattr(usage, "prompt_tokens", 0) if usage else 0,
                getattr(usage, "completion_tokens", 0) if usage else 0,
            )
            _central_track(response, "openai", project="muffinpanrecipes", caller="model_router.openai")
            return (response.choices[0].message.content or "").strip()
        if "v1/responses" in low or "responses api" in low:
            return _from_responses()
        raise
    except NotFoundError as e:
        msg = str(e).lower()
        if "v1/responses" in msg or "responses api" in msg:
            return _from_responses()
        raise


def _generate_anthropic(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    try:
        import anthropic
    except Exception as e:
        raise RuntimeError("anthropic package is not installed") from e

    client = anthropic.Anthropic(api_key=api_key)

    kwargs: dict = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(**kwargs)

    usage = getattr(response, "usage", None)
    _record_cost(
        "anthropic", model,
        getattr(usage, "input_tokens", 0) if usage else 0,
        getattr(usage, "output_tokens", 0) if usage else 0,
    )
    _central_track(response, "anthropic", project="muffinpanrecipes", caller="model_router.anthropic")

    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


def _generate_google(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
) -> str:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is not set")

    try:
        from google import genai
        from google.genai import types
    except Exception as e:
        raise RuntimeError("google-genai package is not installed") from e

    config = types.GenerateContentConfig(temperature=temperature)
    if system_prompt:
        config.system_instruction = system_prompt

    with genai.Client(api_key=api_key) as client:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )

    usage = getattr(response, "usage_metadata", None)
    tokens_in = 0
    tokens_out = 0
    if usage:
        tokens_in = getattr(usage, "prompt_token_count", 0) or getattr(usage, "input_tokens", 0) or 0
        tokens_out = (
            getattr(usage, "candidates_token_count", 0)
            or getattr(usage, "output_tokens", 0)
            or 0
        )
    _record_cost("google", model, tokens_in, tokens_out)
    _central_track(response, "google", project="muffinpanrecipes", caller="model_router.google")

    text = (getattr(response, "text", None) or "").strip()
    if text:
        return text

    return str(response).strip()


# ---------------------------------------------------------------------------
# Vision provider implementations
# ---------------------------------------------------------------------------
def _generate_vision_openai(
    prompt: str,
    images: list[bytes],
    system_prompt: Optional[str],
    model: str,
    temperature: float,
) -> str:
    import base64

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e

    client = OpenAI(api_key=api_key)

    # Build content array: text prompt + image_url blocks
    content: list[dict] = [{"type": "text", "text": prompt}]
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
        })

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})

    response = client.chat.completions.create(
        model=model, messages=messages, temperature=temperature,
    )
    usage = response.usage
    _record_cost(
        "openai", f"{model}:vision",
        getattr(usage, "prompt_tokens", 0) if usage else 0,
        getattr(usage, "completion_tokens", 0) if usage else 0,
    )
    _central_track(response, "openai", project="muffinpanrecipes", caller="model_router.vision_openai")
    return (response.choices[0].message.content or "").strip()


def _generate_vision_anthropic(
    prompt: str,
    images: list[bytes],
    system_prompt: Optional[str],
    model: str,
    temperature: float,
) -> str:
    import base64

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    try:
        import anthropic
    except Exception as e:
        raise RuntimeError("anthropic package is not installed") from e

    client = anthropic.Anthropic(api_key=api_key)

    # Build content array: image blocks + text
    content: list[dict] = []
    for img_bytes in images:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    content.append({"type": "text", "text": prompt})

    kwargs: dict = {
        "model": model,
        "max_tokens": 4096,
        "temperature": temperature,
        "messages": [{"role": "user", "content": content}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt

    response = client.messages.create(**kwargs)
    usage = getattr(response, "usage", None)
    _record_cost(
        "anthropic", f"{model}:vision",
        getattr(usage, "input_tokens", 0) if usage else 0,
        getattr(usage, "output_tokens", 0) if usage else 0,
    )
    _central_track(response, "anthropic", project="muffinpanrecipes", caller="model_router.vision_anthropic")
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------
def generate_vision_response(
    prompt: str,
    images: list[bytes],
    system_prompt: Optional[str] = None,
    model: str = "openai/gpt-5-mini",
    temperature: float = 0.3,
) -> str:
    """Generate a text response from a vision-capable LLM given images.

    Args:
        prompt: User prompt text describing what to evaluate.
        images: List of raw PNG bytes to include in the request.
        system_prompt: Optional system/persona prompt.
        model: Provider-prefixed model name.
        temperature: Sampling temperature.

    Returns:
        Generated text.
    """
    routed = parse_model(model)
    logger.debug(f"Vision router provider={routed.provider} model={routed.model} images={len(images)}")

    if routed.provider == "openai":
        ensure_openai_model_allowed(routed.model)
        return _generate_vision_openai(
            prompt=prompt, images=images, system_prompt=system_prompt,
            model=routed.model, temperature=temperature,
        )

    if routed.provider == "anthropic":
        ensure_anthropic_model_allowed(routed.model)
        return _generate_vision_anthropic(
            prompt=prompt, images=images, system_prompt=system_prompt,
            model=routed.model, temperature=temperature,
        )

    raise RuntimeError(f"Unsupported provider for vision: {routed.provider}")


def generate_response(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "openai/gpt-5-mini",
    temperature: float = 0.8,
) -> str:
    """Generate a text response from a cloud LLM.

    Args:
        prompt: User prompt text.
        system_prompt: Optional system/persona prompt.
        model: Provider-prefixed model name (e.g. "openai/gpt-5-mini",
               "anthropic/claude-haiku-4-5-20251001").
        temperature: Sampling temperature (0.0–2.0).

    Returns:
        Generated text.
    """
    routed = parse_model(model)
    logger.debug(f"Model router provider={routed.provider} model={routed.model}")

    if routed.provider == "openai":
        ensure_openai_model_allowed(routed.model)
        return _generate_openai(
            prompt=prompt, system_prompt=system_prompt,
            model=routed.model, temperature=temperature,
        )

    if routed.provider == "anthropic":
        ensure_anthropic_model_allowed(routed.model)
        return _generate_anthropic(
            prompt=prompt, system_prompt=system_prompt,
            model=routed.model, temperature=temperature,
        )

    if routed.provider == "google":
        ensure_google_model_allowed(routed.model)
        return _generate_google(
            prompt=prompt, system_prompt=system_prompt,
            model=routed.model, temperature=temperature,
        )

    raise RuntimeError(f"Unsupported provider: {routed.provider}")


def generate_judge_response(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "anthropic/claude-opus-4-6",
    temperature: float = 0.3,
) -> str:
    """Generate a response using a judge-tier model.

    Bypasses dialogue allowlists — only checks JUDGE_ALLOWLIST.
    Used for post-generation quality review, not content generation.
    Lower temperature by default for more consistent evaluation.
    """
    routed = parse_model(model)
    if routed.model not in JUDGE_ALLOWLIST:
        raise RuntimeError(
            f"Model not in judge allowlist: {routed.model}. "
            f"Allowed: {', '.join(sorted(JUDGE_ALLOWLIST))}"
        )
    logger.info(f"Judge router provider={routed.provider} model={routed.model}")

    if routed.provider == "openai":
        return _generate_openai(
            prompt=prompt, system_prompt=system_prompt,
            model=routed.model, temperature=temperature,
        )
    if routed.provider == "anthropic":
        return _generate_anthropic(
            prompt=prompt, system_prompt=system_prompt,
            model=routed.model, temperature=temperature,
        )
    raise RuntimeError(f"Unsupported provider for judge: {routed.provider}")
