"""Unified model router for Ollama + OpenAI-compatible dialogue generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

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
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e

    client = OpenAI(api_key=api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return (response.choices[0].message.content or "").strip()


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
        return _generate_openai(
            prompt=prompt,
            system_prompt=system_prompt,
            model=routed.model,
            temperature=temperature,
        )

    raise RuntimeError(f"Unsupported model provider: {routed.provider}")
