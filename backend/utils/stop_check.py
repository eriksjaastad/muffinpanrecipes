"""Stop-check scoring for dialogue scenes (#7680, #7681, #7629).

A scene's wind-down trigger can be switched from the production regex to a
model-based stop check. Two providers:

- ``jev``: POST to OpenRouter's decisions endpoint
  (``https://openrouter.ai/api/alpha/decisions``) with the
  ``typesafe/jev-1.13`` model. The response carries the probabilities in
  ``answers.<question>.noul`` and a usage cost.
- ``haiku``: the same Haiku model the dialogue uses, through
  ``backend.utils.model_router.generate_response``, asked for strict JSON.

Both providers fail loudly: a missing key, non-200 response, timeout, or
malformed payload raises :class:`StopCheckError`. There is deliberately no
fallback to the production regex or to a default value.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import httpx

from backend.utils.model_router import generate_response, record_external_cost

logger = logging.getLogger(__name__)

# Seam so tests can skip the real backoff wait.
_sleep = time.sleep

# The Haiku model the dialogue itself uses. Provider-prefixed so it can go
# straight into backend.utils.model_router.generate_response.
HAIKU_MODEL = "anthropic/claude-haiku-4-5-20251001"

_JEV_URL = "https://openrouter.ai/api/alpha/decisions"
_JEV_REQUEST_MODEL = "typesafe/jev-1.13"
_TIMEOUT_SECONDS = 30.0
# 1 try + 2 retries, inside the portfolio's 3-attempt cap.
_JEV_MAX_ATTEMPTS = 3
_JEV_RETRY_BACKOFF_SECONDS = 2.0

# A successful jev call's real usage.cost, observed in a live logged response
# (tests/test_lab_stop_knobs.py's fixture): $0.000013272. An attempt that
# fails before returning a usable usage payload (network error, timeout, a
# 5xx that gets retried, or a 200 with no usage object) reports no real cost
# at all - counting it as $0 would let a string of retried failures spend
# real HTTP/compute time for free against --max-cost (#7714 finding 2), so it
# is charged a documented, deliberately generous ~10x that one observed real
# per-call cost instead.
_JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD = 0.00015

# ---------------------------------------------------------------------------
# Lab-only pre-attempt guard hook (#7714)
#
# A caller (scripts/conversation_lab.py) may install a callback here that
# fires immediately before every Jev HTTP attempt - including retries - so
# it can enforce --max-calls/--max-cost INSIDE a running stop check instead
# of only after it returns. With no hook installed (the production default),
# this is a no-op and behavior is byte-identical to before #7714.
# ---------------------------------------------------------------------------
_PRE_ATTEMPT_HOOK: Optional[Callable[[], None]] = None


def set_pre_attempt_hook(hook: Optional[Callable[[], None]]) -> Optional[Callable[[], None]]:
    """Install (or clear, with None) the pre-attempt hook; returns the
    previously-installed hook so a caller can restore it."""
    global _PRE_ATTEMPT_HOOK
    previous = _PRE_ATTEMPT_HOOK
    _PRE_ATTEMPT_HOOK = hook
    return previous


def _fire_pre_attempt_hook() -> None:
    if _PRE_ATTEMPT_HOOK is not None:
        _PRE_ATTEMPT_HOOK()


# Every Jev HTTP attempt - success or failure, first try or retry - appended
# here for lab-side accounting/debugging. Unlike simulate_dialogue_week.py's
# STOP_CHECK_LOG (one entry per successful check_scene_done call), this
# records EVERY attempt _check_jev makes, since a failed/retried attempt
# still costs an HTTP round trip and, per _JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD
# above, real (estimated) money.
JEV_ATTEMPT_LOG: list[dict[str, Any]] = []


def _record_jev_attempt(*, ok: bool, cost_usd: float | None, detail: str | None) -> None:
    """Log one Jev HTTP attempt and feed its cost into model_router's cost
    log via record_external_cost, so it counts toward
    get_cost_summary()['total_calls'] and the lab's own cost total
    (see scripts/conversation_lab.py's _lab_cost_total) exactly like a
    router call would (#7714 finding 2)."""
    charged_cost = cost_usd if cost_usd is not None else _JEV_FAILED_ATTEMPT_COST_ESTIMATE_USD
    JEV_ATTEMPT_LOG.append({
        "ok": ok,
        "cost_usd": cost_usd,
        "charged_cost_usd": charged_cost,
        "detail": detail,
        "timestamp": time.time(),
    })
    record_external_cost("jev", _JEV_REQUEST_MODEL, charged_cost)


_DECIDED_QUESTION = "Has the team actually reached a final decision on: {objective}?"
_PUSHBACK_QUESTION = "Before agreeing, did at least one person disagree or push back with a reason?"


class StopCheckError(RuntimeError):
    """The stop check could not produce a usable decision."""


@dataclass
class StopCheckResult:
    """One stop-check verdict.

    ``decided`` and ``pushback`` are probabilities in [0, 1]. ``model`` is
    the exact model snapshot string the provider returned (for ``haiku`` it
    is the model identifier the request was sent to, since
    ``generate_response`` returns only text). ``cost_usd`` is ``None`` when
    the provider reports no per-call cost.
    """

    decided: float
    pushback: float
    provider: str
    model: str
    cost_usd: float | None


def _questions(objective: str) -> dict[str, dict[str, str]]:
    return {
        "decided": {
            "type": "noul",
            "instructions": _DECIDED_QUESTION.format(objective=objective),
        },
        "pushback": {
            "type": "noul",
            "instructions": _PUSHBACK_QUESTION,
        },
    }


def _probability(value: Any, where: str) -> float:
    """Parse a strict 0-1 probability, rejecting bools and out-of-range values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StopCheckError(f"{where} is not a number: {value!r}")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise StopCheckError(f"{where} is outside 0-1: {number!r}")
    return number


def _check_jev(state: str, objective: str) -> StopCheckResult:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise StopCheckError("OPENROUTER_API_KEY is not set")

    body = {
        "model": _JEV_REQUEST_MODEL,
        "state": state,
        "questions": _questions(objective),
    }
    # Jev is an alpha endpoint. On 2026-09-27 one HTTP 520 at a single tick
    # aborted a whole four-arm sweep, so server-side errors and timeouts get a
    # bounded, logged retry. Client errors (4xx: bad key, no credits, bad
    # request) are not retried - repeating them cannot help.
    response = None
    last_error = ""
    for attempt in range(_JEV_MAX_ATTEMPTS):
        _fire_pre_attempt_hook()
        if attempt:
            logger.warning("jev stop check retry %d/%d after %s", attempt, _JEV_MAX_ATTEMPTS - 1, last_error)
            _sleep(_JEV_RETRY_BACKOFF_SECONDS * attempt)
        try:
            response = httpx.post(
                _JEV_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=_TIMEOUT_SECONDS,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            _record_jev_attempt(ok=False, cost_usd=None, detail=last_error)
            response = None
            continue
        except httpx.HTTPError as exc:
            _record_jev_attempt(ok=False, cost_usd=None, detail=str(exc))
            raise StopCheckError(f"jev request failed: {exc}") from exc
        if response.status_code >= 500:
            last_error = f"HTTP {response.status_code}"
            _record_jev_attempt(ok=False, cost_usd=None, detail=last_error)
            continue
        break

    if response is None:
        raise StopCheckError(f"jev request failed after {_JEV_MAX_ATTEMPTS} attempts: {last_error}")
    if response.status_code != 200:
        # A >=500 response was already recorded inside the retry loop above
        # (on the `continue` that leads here after the last attempt) - only
        # a non-5xx failure (a 4xx that broke the loop immediately, without
        # ever taking that branch) needs recording here.
        if response.status_code < 500:
            _record_jev_attempt(ok=False, cost_usd=None, detail=f"HTTP {response.status_code}")
        raise StopCheckError(f"jev returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        _record_jev_attempt(ok=False, cost_usd=None, detail="malformed JSON")
        raise StopCheckError("jev returned malformed JSON") from exc

    # The HTTP round trip that produced this payload already happened and
    # already cost money regardless of whether the payload shape below turns
    # out to be usable - record it now, once, using whatever real usage.cost
    # it carries (or the conservative estimate when it carries none), before
    # any of the shape checks below can raise.
    attempt_cost_usd: float | None = None
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if isinstance(usage, dict):
        raw_attempt_cost = usage.get("cost")
        if isinstance(raw_attempt_cost, (int, float)) and not isinstance(raw_attempt_cost, bool):
            attempt_cost_usd = float(raw_attempt_cost)
    _record_jev_attempt(ok=True, cost_usd=attempt_cost_usd, detail=None)

    if not isinstance(payload, dict):
        raise StopCheckError("jev response is not a JSON object")

    model = payload.get("model")
    if not isinstance(model, str) or not model:
        raise StopCheckError("jev response is missing the model snapshot")

    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise StopCheckError("jev response is missing the answers object")

    decided = _probability(
        answers.get("decided", {}).get("noul") if isinstance(answers.get("decided"), dict) else None,
        "jev response decided.noul",
    )
    pushback = _probability(
        answers.get("pushback", {}).get("noul") if isinstance(answers.get("pushback"), dict) else None,
        "jev response pushback.noul",
    )

    return StopCheckResult(
        decided=decided,
        pushback=pushback,
        provider="jev",
        model=model,
        cost_usd=attempt_cost_usd,
    )


def _check_haiku(state: str, objective: str, haiku_model: str = HAIKU_MODEL) -> StopCheckResult:
    prompt = (
        f"Read this meeting transcript and answer two questions with STRICT JSON only.\n\n"
        f"1. Has the team actually reached a final decision on: {objective}?\n"
        f"2. Before agreeing, did at least one person disagree or push back with a reason?\n\n"
        'Respond with exactly: {"decided": <number 0-1>, "pushback": <number 0-1>}\n\n'
        f"Transcript:\n{state}"
    )
    try:
        raw = generate_response(
            prompt=prompt,
            system_prompt="You answer dialogue-scene questions with strict JSON only.",
            model=haiku_model,
            temperature=0.0,
        ).strip()
    except Exception as exc:
        raise StopCheckError(f"haiku stop check failed: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StopCheckError(
            f"haiku stop check returned malformed JSON: {raw[:120]!r}"
        ) from exc

    if not isinstance(payload, dict):
        raise StopCheckError("haiku stop check JSON is not an object")

    decided = _probability(payload.get("decided"), "haiku stop check decided")
    pushback = _probability(payload.get("pushback"), "haiku stop check pushback")

    return StopCheckResult(
        decided=decided,
        pushback=pushback,
        provider="haiku",
        model=haiku_model,
        cost_usd=None,
    )


def check_scene_done(
    lines: list[str],
    provider: str,
    *,
    day: str,
    objective: str,
    haiku_model: str = HAIKU_MODEL,
) -> StopCheckResult:
    """Score whether the scene is done for ``day``'s ``objective``.

    ``day`` is part of the call contract so every stop-check site names the
    scene it is scoring (it is also recorded into STOP_CHECK_LOG by the
    caller); the providers themselves score the joined transcript.

    ``haiku_model`` lets the lab's ``haiku`` provider follow the same
    dialogue model string as the run that spawned the scene (for example
    ``openrouter/anthropic/claude-haiku-4.5`` under --provider openrouter).
    Production callers omit it and keep the direct-Anthropic default.
    """
    if not isinstance(lines, list):
        raise StopCheckError(f"lines must be a list of strings, got {type(lines).__name__}")
    state = "\n".join(lines)
    if provider == "jev":
        return _check_jev(state, objective)
    if provider == "haiku":
        return _check_haiku(state, objective, haiku_model=haiku_model)
    raise StopCheckError(f"unknown stop check provider: {provider!r}")
