"""Vercel deployment-hook handoff for static publishing.

Sunday writes the authoritative episode and catalog records to Blob.  The
public reader routes serve the artifacts produced by the next Vercel build, so
the handoff must be explicit and must never expose the hook URL in logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

import requests

from backend.utils.logging import get_logger

logger = get_logger(__name__)

DEPLOY_HOOK_ENV_VAR = "VERCEL_DEPLOY_HOOK_URL"
_REQUEST_TIMEOUT_SECONDS = 10


class DeployHookError(RuntimeError):
    """Base error for a failed or unavailable production handoff."""


class DeployHookConfigurationError(DeployHookError):
    """Raised when production has no configured Vercel deploy hook."""


@dataclass(frozen=True)
class DeployHookResult:
    """Outcome of a deploy-hook handoff attempt."""

    triggered: bool
    skipped: bool = False


def trigger_vercel_deploy_hook(
    *,
    test_mode: bool = False,
    environment: str | None = None,
    post: Callable[..., Any] = requests.post,
) -> DeployHookResult:
    """Request a Vercel deployment for the current Blob source state.

    Test-mode invocations are always skipped, even if a URL is present, so a
    compressed week can never deploy its isolated ``test/`` Blob prefix.
    Missing configuration and request failures are warnings outside
    production. Production raises a safe, URL-free error so cron cannot report
    a successful publish while static reader routes still serve the old build.
    """
    vercel_environment = (
        environment
        if environment is not None
        else os.environ.get("VERCEL_ENV", "")
    ).strip()
    is_production = vercel_environment.lower() == "production"

    if test_mode:
        logger.warning(
            "Skipping Vercel static deployment handoff for test-mode publish "
            "(environment=%s)",
            vercel_environment or "local",
        )
        return DeployHookResult(triggered=False, skipped=True)

    hook_url = os.environ.get(DEPLOY_HOOK_ENV_VAR, "").strip()
    if not hook_url:
        message = (
            f"{DEPLOY_HOOK_ENV_VAR} is not configured; static deployment handoff "
            "cannot be requested"
        )
        if is_production:
            raise DeployHookConfigurationError(
                f"Production publish blocked: {message}. Configure it in Doppler."
            )
        logger.warning(
            "%s (environment=%s); continuing outside production",
            message,
            vercel_environment or "local",
        )
        return DeployHookResult(triggered=False, skipped=True)

    try:
        response = post(hook_url, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except Exception as exc:
        # Do not include ``exc``: requests exceptions can include the secret
        # URL in their string representation.
        if is_production:
            raise DeployHookError(
                "Production publish blocked: Vercel deploy-hook request failed"
            ) from exc
        logger.warning(
            "Vercel deploy-hook request failed outside production "
            "(environment=%s; error_type=%s); continuing",
            vercel_environment or "local",
            type(exc).__name__,
        )
        return DeployHookResult(triggered=False)

    logger.info(
        "Vercel static deployment requested after Sunday publish (environment=%s)",
        vercel_environment or "local",
    )
    return DeployHookResult(triggered=True)
