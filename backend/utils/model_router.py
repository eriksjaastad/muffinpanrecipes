"""Unified model router for Ollama + OpenAI-compatible dialogue generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

# Fail-closed OpenAI model policy: only cheap models unless explicitly allowlisted.
DEFAULT_OPENAI_ALLOWLIST = {
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5-nano",
    "gpt-5-mini",
}

HARD_BLOCKED_MODELS = {
    "gpt-5-pro",
    "gpt-5.2-pro",
    "gpt-5.2-pro-2025-12-11",
    "gpt-5-pro-2025-10-06",
    "o1-pro",
    "o1-pro-2025-03-19",
}

from backend.utils.logging import get_logger
from backend.utils.ollama import get_ollama_client

logger = get_logger(__name__)


@dataclass
class RoutedModel:
    provider: str
    model: str


def parse_model(model: str) -> RoutedModel:
    if "/" in model:
        provider, model_name = model.split("/", 1)
        provider = provider.strip().lower()
        if provider in {"ollama", "openai"}:
            return RoutedModel(provider=provider, model=model_name.strip())
    # Backward compatibility: bare model names route to Ollama
    return RoutedModel(provider="ollama", model=model)


def _allowed_openai_models() -> set[str]:
    raw = os.getenv("OPENAI_MODEL_ALLOWLIST", "").strip()
    if not raw:
        return set(DEFAULT_OPENAI_ALLOWLIST)
    return {m.strip() for m in raw.split(",") if m.strip()}


def ensure_openai_model_allowed(model: str) -> None:
    low = model.lower().strip()
    if low in HARD_BLOCKED_MODELS:
        raise RuntimeError(f"OpenAI model blocked by policy: {model}")

    allowed = _allowed_openai_models()
    if low not in allowed:
        raise RuntimeError(
            "OpenAI model not allowlisted: "
            f"{model}. Allowed: {', '.join(sorted(allowed))}"
        )


def _generate_ollama(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
) -> str:
    client = get_ollama_client()
    return client.generate_response(
        prompt=prompt,
        system_prompt=system_prompt,
        model=model,
        temperature=temperature,
    )


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

        text = (getattr(r, "output_text", "") or "").strip()
        if text:
            return text

        # Fallback for SDK variants that don't populate output_text.
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
            model=model,
            messages=messages,
            temperature=temperature,
        )
        return (response.choices[0].message.content or "").strip()
    except BadRequestError as e:
        msg = str(e)
        low = msg.lower()
        # Some models disallow custom temperature; retry without it.
        if "temperature" in low and ("unsupported" in low or "default (1)" in low):
            response = client.chat.completions.create(
                model=model,
                messages=messages,
            )
            return (response.choices[0].message.content or "").strip()

        # Some models only support v1/responses.
        if "v1/responses" in low or "responses api" in low:
            return _from_responses()
        raise
    except NotFoundError as e:
        msg = str(e).lower()
        # Some newer models only support v1/responses.
        if "v1/responses" in msg or "responses api" in msg:
            return _from_responses()
        raise


def generate_response(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "ollama/qwen3:32b",
    temperature: float = 0.8,
) -> str:
    routed = parse_model(model)

    logger.debug(f"Model router provider={routed.provider} model={routed.model}")

    if routed.provider == "ollama":
        return _generate_ollama(
            prompt=prompt,
            system_prompt=system_prompt,
            model=routed.model,
            temperature=temperature,
        )

    if routed.provider == "openai":
        ensure_openai_model_allowed(routed.model)
        return _generate_openai(
            prompt=prompt,
            system_prompt=system_prompt,
            model=routed.model,
            temperature=temperature,
        )

    raise RuntimeError(f"Unsupported model provider: {routed.provider}")
