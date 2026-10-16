"""Model router: every paid text and vision call goes through OpenRouter.

Callers name models with provider-prefixed ids (``anthropic/claude-haiku-4-5-20251001``,
``openai/gpt-5.1``, ``google/...``, or an OpenRouter id as ``openrouter/<vendor>/<model>``).
Each call first passes that provider's allowlist/blocklist, then OPENROUTER_MODEL_IDS
turns the id into OpenRouter's name for the same model, and the request goes to
OpenRouter pinned to the model's own vendor with no fallbacks. One OpenRouter bill
shows all spend (#8343). An id with no OPENROUTER_MODEL_IDS entry raises: a new model
is a deliberate table edit, never a guess.

Image generation lives in backend.utils.image_generation and uses the same table and
route.
"""

from __future__ import annotations

import base64
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from backend.utils.logging import get_logger

try:
    from api_cost_tracker import track as _central_track
except ImportError:
    def _central_track(resp, *a, **kw):
        return resp

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# OpenAI model policy (fail-closed)
# OWNER RULE (do not relax without explicit Erik approval):
# ---------------------------------------------------------------------------
# Reasoning effort for OpenAI reasoning models (gpt-6-astra and friends).
# Valid: low | medium | high | xhigh | max. Overridable per deployment so the
# conversation lab can sweep it without a code change.
#
# Why this is a knob and not a constant: OpenAI's own published figures put cost
# per task at $0.46 at low effort and $1.67 at max - a 3.6x spread - while the
# intelligence score moves only 57 -> 61. For a character writing one sentence in
# a group chat, the top of that range is very unlikely to pay for itself. Measure
# before raising it.
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "high").strip().lower()
_VALID_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max"}

# OpenRouter ids of models that take a reasoning.effort parameter and reject
# `temperature`.
OPENAI_REASONING_MODELS = {"openai/gpt-6-astra"}


def _reasoning_kwargs(model: str) -> dict:
    """reasoning.effort kwargs for an OpenRouter reasoning model id, or {} for a plain one."""
    if model.lower().strip() not in OPENAI_REASONING_MODELS:
        return {}
    effort = OPENAI_REASONING_EFFORT
    if effort not in _VALID_REASONING_EFFORTS:
        raise RuntimeError(
            f"OPENAI_REASONING_EFFORT={effort!r} is not valid. "
            f"Use one of: {', '.join(sorted(_VALID_REASONING_EFFORTS))}"
        )
    return {"reasoning": {"effort": effort}}


DEFAULT_OPENAI_ALLOWLIST = {
    "gpt-5-mini",
    "gpt-5-nano",
    "gpt-5.1",
    # Dialogue-generation candidate (Erik, 2026-09-16). Reasoning model - see
    # OPENAI_REASONING_EFFORT below and note it rejects `temperature`.
    "gpt-6-astra",
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
# OpenRouter model policy (fail-closed)
# ---------------------------------------------------------------------------
# Callers that name an OpenRouter id directly (``openrouter/<vendor>/<model>``,
# the conversation lab) are checked against these; production's
# provider-prefixed ids are checked against their own provider's list above.
DEFAULT_OPENROUTER_ALLOWLIST = {
    "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4-6",
}

HARD_BLOCKED_OPENROUTER_MODELS: set[str] = set()

OPENROUTER_JUDGE_ALLOWLIST = {
    "anthropic/claude-opus-4.6",
    "anthropic/claude-sonnet-4-6",
}

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Provider-prefixed id a caller passes -> OpenRouter's id for the same model.
# Every target was checked against https://openrouter.ai/api/v1/models on
# 2026-10-16. Unlisted ids fail closed in openrouter_model_id().
OPENROUTER_MODEL_IDS = {
    "anthropic/claude-haiku-4-5-20251001": "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "anthropic/claude-opus-4-6": "anthropic/claude-opus-4.6",
    "openai/gpt-5-mini": "openai/gpt-5-mini",
    "openai/gpt-5-nano": "openai/gpt-5-nano",
    "openai/gpt-5.1": "openai/gpt-5.1",
    "openai/gpt-5.2": "openai/gpt-5.2",
    "openai/gpt-6-astra": "openai/gpt-6-astra",
    "google/gemini-3.1-pro-preview": "google/gemini-3.1-pro-preview",
    "google/gemini-3.1-flash-lite-preview": "google/gemini-3.1-flash-lite-preview",
    "google/gemini-3-flash-preview": "google/gemini-3-flash-preview",
    # Image output (backend.utils.image_generation, the Wednesday photo stage).
    "google/gemini-3.1-flash-image": "google/gemini-3.1-flash-image",
}


def openrouter_model_id(provider: str, model: str) -> str:
    """OpenRouter's id for ``provider``/``model``; an ``openrouter`` id passes through.

    Raises for any id missing from OPENROUTER_MODEL_IDS.
    """
    if provider == "openrouter":
        return model
    native = f"{provider}/{model}"
    try:
        return OPENROUTER_MODEL_IDS[native]
    except KeyError:
        raise RuntimeError(
            f"No OpenRouter id mapped for {native!r}; add it to OPENROUTER_MODEL_IDS"
        ) from None


# An Anthropic model is served by Anthropic's own servers, never by
# Vertex/Bedrock. Erik's account guardrail (verified 2026-09-27) means the
# served model is exactly the one production uses. Kept as a literal alias for
# the anthropic vendor (equal to openrouter_provider_route("anthropic/...")) -
# some callers/tests still import this constant directly.
OPENROUTER_PROVIDER_ROUTE = {"order": ["anthropic"], "allow_fallbacks": False}

# OpenRouter provider slug for a vendor whose slug differs from its model-id
# prefix. Google's own API, the one GOOGLE_API_KEY used to reach, is
# "google-ai-studio"; Vertex is a different provider.
_OPENROUTER_PROVIDER_SLUGS = {"google": "google-ai-studio"}


def openrouter_provider_route(model_id: str) -> dict[str, Any]:
    """Provider-routing hint for an OpenRouter dotted model id (e.g.
    ``anthropic/claude-haiku-4.5`` or ``deepseek/deepseek-v4.1-flash``),
    derived from its vendor prefix - the part before the first ``/`` - rather
    than a single hardcoded global. For any anthropic id this returns exactly
    OPENROUTER_PROVIDER_ROUTE. Every route still pins to one vendor and never
    falls back (#7714 - swappable lab models needed a route per model, not
    one route for the whole process).
    """
    if "/" not in model_id:
        raise RuntimeError(f"Cannot derive an OpenRouter provider route from model id: {model_id!r}")
    vendor = model_id.split("/", 1)[0].strip().lower()
    if not vendor:
        raise RuntimeError(f"Cannot derive an OpenRouter provider route from model id: {model_id!r}")
    return {"order": [_OPENROUTER_PROVIDER_SLUGS.get(vendor, vendor)], "allow_fallbacks": False}


# ---------------------------------------------------------------------------
# Lab-only OpenRouter allowlist registration (#7714)
#
# scripts/conversation_lab.py reads scripts/lab_models.json and calls
# allow_openrouter_models() to register whichever model ids that file names -
# this module must NEVER read that file itself: backend/ ships in the Vercel
# Lambda bundle and scripts/lab_models.json does not (see .vercelignore), so a
# file read here would 404 in production. Registration is additive only -
# it never removes an id from DEFAULT_OPENROUTER_ALLOWLIST/OPENROUTER_JUDGE_ALLOWLIST.
# ---------------------------------------------------------------------------
_EXTRA_OPENROUTER_DIALOGUE_MODELS: set[str] = set()
_EXTRA_OPENROUTER_JUDGE_MODELS: set[str] = set()


def allow_openrouter_models(*, dialogue: str, judge: str) -> None:
    """Register one lab model set's OpenRouter ids as allowed for dialogue
    generation and for the judge, in addition to whatever is already allowed.
    Safe to call more than once (e.g. once per set in lab_models.json)."""
    _EXTRA_OPENROUTER_DIALOGUE_MODELS.add(dialogue)
    _EXTRA_OPENROUTER_JUDGE_MODELS.add(judge)

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
# Cost tracking
# ---------------------------------------------------------------------------
_COST_LOG: list[dict] = []


def _record_cost(
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    actual_cost: Optional[float] = None,
    served_provider: Optional[str] = None,
) -> None:
    """Log one paid call. ``actual_cost`` is the billed USD (OpenRouter's
    ``usage.cost``), absent when the provider reported none."""
    entry = {
        "provider": provider,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "timestamp": time.time(),
    }
    if actual_cost is not None:
        entry["actual_cost"] = actual_cost
    if served_provider is not None:
        entry["served_provider"] = served_provider
    _COST_LOG.append(entry)


def get_cost_summary() -> dict:
    """Per-model breakdown and totals of the billed cost.

    ``unpriced_calls`` counts calls whose provider reported no cost; their
    spend is missing from ``cost``/``total_cost``, not zero.
    """
    by_model: dict[str, dict] = {}
    for entry in _COST_LOG:
        key = f"{entry['provider']}/{entry['model']}"
        if key not in by_model:
            by_model[key] = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "unpriced_calls": 0}
        by_model[key]["calls"] += 1
        by_model[key]["tokens_in"] += entry["tokens_in"]
        by_model[key]["tokens_out"] += entry["tokens_out"]
        if "actual_cost" in entry:
            by_model[key]["cost"] += entry["actual_cost"]
        else:
            by_model[key]["unpriced_calls"] += 1

    return {
        "by_model": by_model,
        "total_cost": sum(m["cost"] for m in by_model.values()),
        "unpriced_calls": sum(m["unpriced_calls"] for m in by_model.values()),
        "total_calls": sum(m["calls"] for m in by_model.values()),
        "total_tokens_in": sum(m["tokens_in"] for m in by_model.values()),
        "total_tokens_out": sum(m["tokens_out"] for m in by_model.values()),
    }


def cost_text(cost: float, unpriced_calls: int) -> str:
    """A billed total for printing that never passes off unknown spend as
    zero: calls with no billed cost (see get_cost_summary) are named, not summed."""
    return f"${cost:.4f}" + (f" + {unpriced_calls} unpriced call(s)" if unpriced_calls else "")


def reset_cost_log() -> None:
    """Clear the cost log (call between benchmark runs)."""
    _COST_LOG.clear()


_SENT_REQUESTS = 0


def sent_request_count() -> int:
    """Text/vision requests sent to OpenRouter by this process so far.

    The cost log only gains an entry when a response comes back; this counts
    every request at the moment it goes out, so a caller capping paid calls
    (the conversation lab) also counts one that failed with no response.
    """
    return _SENT_REQUESTS


def get_cost_entries() -> list[dict]:
    """Return a shallow copy of the raw cost log.

    Callers that need per-call fields beyond ``get_cost_summary`` (for
    example OpenRouter's ``usage.cost`` and the served provider) total them
    from here instead of re-deriving them from token counts.
    """
    return [dict(entry) for entry in _COST_LOG]


def record_external_cost(provider: str, model: str, cost: Any) -> None:
    """Record a cost-log entry for a paid unit that never goes through
    generate_response()/generate_judge_response() - backend.utils.stop_check's
    Jev HTTP attempts (#7714) and backend.utils.image_generation's images.

    Stored as ``actual_cost`` with zero tokens, exactly like an OpenRouter
    call's real ``usage.cost`` - so it counts toward
    ``get_cost_summary()['total_calls']`` (a lab caller's before/after
    total_calls delta, e.g. scripts/conversation_lab.py's
    ``_run_arm_and_count``, naturally includes it) and toward
    ``get_cost_entries()``'s actual-cost total the same way a router call
    would. A cost that is not a real price is recorded as absent (see
    `_billed_cost`).
    """
    _record_cost(provider, model, 0, 0, actual_cost=_billed_cost(cost))


# ---------------------------------------------------------------------------
# Lab-only pre-call guard hook (#7714)
#
# A caller (scripts/conversation_lab.py) may install a callback here that
# fires immediately before every generate_response()/generate_judge_response()
# call - and, via backend.utils.stop_check's own hook, before every Jev HTTP
# attempt - so it can enforce --max-calls/--max-cost INSIDE a running arm
# instead of only at coarser boundaries. With no hook installed (the
# production default, and every test that never calls set_pre_call_hook),
# this is a complete no-op and behavior is byte-identical to before #7714.
# ---------------------------------------------------------------------------
_PRE_CALL_HOOK: Optional[Any] = None


def set_pre_call_hook(hook: Optional[Any]) -> Optional[Any]:
    """Install (or clear, with None) the pre-call hook; returns the
    previously-installed hook so a caller can restore it (see
    scripts/conversation_lab.py's ``_installed_budget_guard``)."""
    global _PRE_CALL_HOOK
    previous = _PRE_CALL_HOOK
    _PRE_CALL_HOOK = hook
    return previous


def _fire_pre_call_hook(provider: str, model: str, prompt_bytes: int) -> None:
    """`provider`/`model`/`prompt_bytes` describe the call about to be
    made (#7714 finding 2) - a caller-installed hook can use them to
    reserve that call's worst-case cost (e.g. an OpenRouter reasoning
    model's output ceiling) instead of only checking the running total.
    A no-op when unset, same as before this signature existed."""
    if _PRE_CALL_HOOK is not None:
        _PRE_CALL_HOOK(provider, model, prompt_bytes)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
SUPPORTED_PROVIDERS = {"openai", "anthropic", "google", "openrouter"}


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
    # governance: allow-silent SF003: optional override; empty selects DEFAULT_OPENAI_ALLOWLIST, never an open allowlist
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
    # governance: allow-silent SF003: optional override; empty selects DEFAULT_ANTHROPIC_ALLOWLIST, never an open allowlist
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
    # governance: allow-silent SF003: optional override; empty selects DEFAULT_GOOGLE_ALLOWLIST, never an open allowlist
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


def _allowed_openrouter_models() -> set[str]:
    # governance: allow-silent SF003: optional override; empty selects DEFAULT_OPENROUTER_ALLOWLIST, never an open allowlist
    raw = os.getenv("OPENROUTER_MODEL_ALLOWLIST", "").strip()
    base = {m.strip() for m in raw.split(",") if m.strip()} if raw else set(DEFAULT_OPENROUTER_ALLOWLIST)
    # Union, not override - a lab model set registered via allow_openrouter_models()
    # must stay allowed even when OPENROUTER_MODEL_ALLOWLIST is set for something else.
    return base | _EXTRA_OPENROUTER_DIALOGUE_MODELS


def ensure_openrouter_model_allowed(model: str) -> None:
    low = model.lower().strip()
    if low in HARD_BLOCKED_OPENROUTER_MODELS:
        raise RuntimeError(f"OpenRouter model blocked by policy: {model}")
    allowed = _allowed_openrouter_models()
    if low not in allowed:
        raise RuntimeError(
            f"OpenRouter model not allowlisted: {model}. Allowed: {', '.join(sorted(allowed))}"
        )


_OPENROUTER_ANTHROPIC_MAX_TOKENS = 4096
_OPENROUTER_REASONING_MAX_TOKENS = 32768


def openrouter_max_tokens(model_id: str) -> int:
    """Output ceiling for one OpenRouter call.

    Anthropic ids keep 4096, the ceiling production used when it called
    Anthropic directly. Other vendors' models (OpenAI's GPT-5 family, the lab's
    DeepSeek set) reason before answering and count that reasoning against
    max_tokens: at 4096 the DeepSeek judge used every token thinking and
    returned nothing (12288 tokens over three calls, smoke run 2026-09-28).
    32768 leaves room to reason and still answer; OpenRouter bills tokens
    actually generated, not the ceiling.
    """
    vendor = model_id.split("/", 1)[0].strip().lower()
    if vendor == "anthropic":
        return _OPENROUTER_ANTHROPIC_MAX_TOKENS
    return _OPENROUTER_REASONING_MAX_TOKENS


# deepseek-v4.1-flash occasionally ends a turn with finish_reason "stop" and
# no content after reasoning (3 in ~3000 calls, 2026-09-28); each one used to
# end the whole lab run. Bounded: first try plus two retries.
_OPENROUTER_EMPTY_STOP_MAX_ATTEMPTS = 3


def _generate_openrouter(
    prompt: str,
    system_prompt: Optional[str],
    model: str,
    temperature: float,
    images: Optional[list[bytes]] = None,
) -> str:
    """Generate through OpenRouter's OpenAI-compatible chat-completions API.

    ``model`` is the OpenRouter dotted id (e.g. ``anthropic/claude-haiku-4.5``).
    Every request is pinned to the model's own vendor via
    openrouter_provider_route() and asks OpenRouter to include ``usage.cost``
    so callers can total the exact USD cost of each call. ``images`` (raw PNG
    bytes) go in the user message as base64 data URLs.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    try:
        from openai import OpenAI
    except Exception as e:
        raise RuntimeError("openai package is not installed") from e

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if images:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"},
            })
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})

    prompt_bytes = _prompt_bytes(prompt, system_prompt)
    finish_reason: Any = None
    completion_tokens: Any = None
    for attempt in range(_OPENROUTER_EMPTY_STOP_MAX_ATTEMPTS):
        if attempt:
            # A retry is another paid call: it passes the caller's guard too.
            _fire_pre_call_hook("openrouter", model, prompt_bytes)
            logger.warning("openrouter %s empty content with finish_reason=stop; retry %d", model, attempt)
        text, finish_reason, completion_tokens = _openrouter_attempt(client, model, messages, temperature)
        if text:
            return text
        if finish_reason != "stop":
            break
    # Empty content is never a successful result. finish_reason "length" (a
    # reasoning model spent the whole ceiling thinking) is not retried - the
    # same ceiling would fail the same way.
    raise RuntimeError(
        f"OpenRouter {model} returned empty content "
        f"(finish_reason={finish_reason!r}, completion_tokens={completion_tokens})"
    )


def _openrouter_request_kwargs(model: str, messages: list, temperature: float) -> dict[str, Any]:
    """The exact kwargs one OpenRouter attempt passes to chat.completions.create.
    judge_request_settings() derives its description from this same
    function, so the recorded judge instrument is the request itself.

    A reasoning model gets ``reasoning.effort`` and no ``temperature``, which
    it rejects."""
    extra_body: dict[str, Any] = {
        "provider": openrouter_provider_route(model),
        "usage": {"include": True},
    }
    kwargs: dict[str, Any] = {"model": model, "messages": messages}
    reasoning = _reasoning_kwargs(model)
    if reasoning:
        extra_body.update(reasoning)
    else:
        kwargs["temperature"] = temperature
    kwargs["max_tokens"] = openrouter_max_tokens(model)
    kwargs["extra_body"] = extra_body
    return kwargs


def _billed_cost(raw_cost: Any) -> Optional[float]:
    """OpenRouter's ``usage.cost`` as a float, or None when it is not a real price.

    A missing, NaN, infinite or negative cost is recorded as absent so the
    lab's cost total treats the entry as unmetered and fails closed, instead
    of poisoning the running total.
    """
    if isinstance(raw_cost, bool) or not isinstance(raw_cost, (int, float)):
        return None
    if not math.isfinite(raw_cost) or raw_cost < 0:
        return None
    return float(raw_cost)


def _openrouter_attempt(client: Any, model: str, messages: list, temperature: float) -> tuple[str, Any, Any]:
    """One OpenRouter request; records its cost. Returns (text, finish_reason, completion_tokens)."""
    global _SENT_REQUESTS
    _SENT_REQUESTS += 1  # counted before sending: a request can be billed with no response back
    response = client.chat.completions.create(**_openrouter_request_kwargs(model, messages, temperature))

    usage = getattr(response, "usage", None)
    served_provider = getattr(response, "provider", None)
    if not isinstance(served_provider, str) or not served_provider:
        served_provider = None
    _record_cost(
        "openrouter", model,
        getattr(usage, "prompt_tokens", 0) if usage else 0,
        getattr(usage, "completion_tokens", 0) if usage else 0,
        actual_cost=_billed_cost(getattr(usage, "cost", None) if usage else None),
        served_provider=served_provider,
    )
    _central_track(response, "openrouter", project="muffinpanrecipes", caller="model_router.openrouter")

    choice = response.choices[0]
    return (
        (choice.message.content or "").strip(),
        getattr(choice, "finish_reason", None),
        getattr(usage, "completion_tokens", None) if usage else None,
    )


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------
_ALLOWLIST_CHECKS = {
    "openai": ensure_openai_model_allowed,
    "anthropic": ensure_anthropic_model_allowed,
    "google": ensure_google_model_allowed,
    "openrouter": ensure_openrouter_model_allowed,
}


def _prompt_bytes(prompt: str, system_prompt: Optional[str]) -> int:
    return len(prompt.encode("utf-8")) + len((system_prompt or "").encode("utf-8"))


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
        model: Provider-prefixed model name, checked against that
               provider's allowlist and sent through OpenRouter.
        temperature: Sampling temperature.

    Returns:
        Generated text.
    """
    routed = parse_model(model)
    logger.debug(f"Vision router provider={routed.provider} model={routed.model} images={len(images)}")
    _ALLOWLIST_CHECKS[routed.provider](routed.model)
    return _generate_openrouter(
        prompt=prompt, system_prompt=system_prompt,
        model=openrouter_model_id(routed.provider, routed.model),
        temperature=temperature, images=images,
    )


def generate_response(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "openai/gpt-5-mini",
    temperature: float = 0.8,
) -> str:
    """Generate a text response from a cloud LLM, through OpenRouter.

    Args:
        prompt: User prompt text.
        system_prompt: Optional system/persona prompt.
        model: Provider-prefixed model name (e.g. "openai/gpt-5-mini",
               "anthropic/claude-haiku-4-5-20251001"), checked against that
               provider's allowlist, then mapped by OPENROUTER_MODEL_IDS.
        temperature: Sampling temperature (0.0–2.0).

    Returns:
        Generated text.
    """
    routed = parse_model(model)
    logger.debug(f"Model router provider={routed.provider} model={routed.model}")
    _ALLOWLIST_CHECKS[routed.provider](routed.model)
    openrouter_model = openrouter_model_id(routed.provider, routed.model)
    _fire_pre_call_hook("openrouter", openrouter_model, _prompt_bytes(prompt, system_prompt))
    return _generate_openrouter(
        prompt=prompt, system_prompt=system_prompt,
        model=openrouter_model, temperature=temperature,
    )


def _judge_openrouter_model(model: str) -> str:
    """The OpenRouter id for a judge-tier ``model``, after the judge allowlist check."""
    routed = parse_model(model)
    if routed.provider == "openrouter":
        allowed_judge_models = OPENROUTER_JUDGE_ALLOWLIST | _EXTRA_OPENROUTER_JUDGE_MODELS
        if routed.model not in allowed_judge_models:
            raise RuntimeError(
                f"OpenRouter model not in judge allowlist: {routed.model}. "
                f"Allowed: {', '.join(sorted(allowed_judge_models))}"
            )
    elif routed.model not in JUDGE_ALLOWLIST:
        raise RuntimeError(
            f"Model not in judge allowlist: {routed.model}. "
            f"Allowed: {', '.join(sorted(JUDGE_ALLOWLIST))}"
        )
    return openrouter_model_id(routed.provider, routed.model)


def generate_judge_response(
    prompt: str,
    system_prompt: Optional[str] = None,
    model: str = "anthropic/claude-opus-4-6",
    temperature: float = 0.3,
) -> str:
    """Generate a response using a judge-tier model, through OpenRouter.

    Bypasses dialogue allowlists — only checks JUDGE_ALLOWLIST.
    Used for post-generation quality review, not content generation.
    Lower temperature by default for more consistent evaluation.
    """
    openrouter_model = _judge_openrouter_model(model)
    logger.info(f"Judge router model={model} openrouter={openrouter_model}")
    _fire_pre_call_hook("openrouter", openrouter_model, _prompt_bytes(prompt, system_prompt))
    return _generate_openrouter(
        prompt=prompt, system_prompt=system_prompt,
        model=openrouter_model, temperature=temperature,
    )


def judge_request_settings(model: str, temperature: float) -> dict[str, Any]:
    """Everything except the prompt text that `generate_judge_response`
    sends for `model` at `temperature`.

    Built by the SAME kwargs builder the send path calls, minus the message
    content, so it cannot drift from the request (#7791). The conversation
    lab saves it with every judge verdict and `rejudge` compares it whole.
    Describes the request only; the allowlist check stays with the send.
    """
    routed = parse_model(model)
    kwargs = _openrouter_request_kwargs(openrouter_model_id(routed.provider, routed.model), [], temperature)
    kwargs.pop("messages")
    return {
        "provider": "openrouter",
        **kwargs,
        "endpoint": "chat.completions",
        "empty_stop_max_attempts": _OPENROUTER_EMPTY_STOP_MAX_ATTEMPTS,
        "request_shape": "fixed",
    }
