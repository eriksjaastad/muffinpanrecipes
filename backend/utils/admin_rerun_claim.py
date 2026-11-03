"""Per-episode claim that serialises admin stage re-runs across processes (#8604).

A re-run dispatches a paid cron handler, so two requests must never both run,
whichever stage they name or whichever Vercel instance serves them. The claim
is an append-only log in storage (``storage.read_admin_rerun_control`` /
``create_admin_rerun_control_version``) with the photo control's rules: each
version is created if absent and never overwritten or deleted, a writer that
read version N creates N+1, and the loser of a race gets a conflict.

* The latest version is ``claimed`` (a run owns the episode) or ``released``.
* Anything unreadable, unexpected, or of unknown write outcome refuses the run.
* Release is a new ``released`` version made after the handler returns or raises.
  If its outcome is unknown the log still says ``claimed`` and the episode stays
  blocked; nothing expires or is force-released here.
* Bodies hold the stage, state, outcome and timestamps only: the log is the audit
  trail and carries no secrets, request data or user identity.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

from backend.storage import PhotoControlConflict, PhotoControlUnavailable

logger = logging.getLogger(__name__)

SCHEMA = "admin_rerun/v1"


class RerunBusy(RuntimeError):
    """Another run owns this episode, or its release is unconfirmed."""


class RerunClaimUnavailable(RuntimeError):
    """The claim could not be read or written authoritatively; nothing may run."""


def _body(episode_id: str, version: int, state: str, stage: str, **extra) -> dict:
    return {
        "schema": SCHEMA,
        "episode_id": episode_id,
        "version": version,
        "write_id": secrets.token_hex(8),
        "state": state,
        "stage": stage,
        "at": datetime.now(timezone.utc).isoformat(),
        **extra,
    }


def _create(store, body: dict) -> None:
    """Create ``body``'s version. An unknown outcome is resolved by re-reading it."""
    episode_id = body["episode_id"]
    try:
        store.create_admin_rerun_control_version(episode_id, body["version"], body)
    except PhotoControlUnavailable:
        latest = store.read_admin_rerun_control(episode_id)
        if not (latest and latest.get("version") == body["version"] and latest.get("write_id") == body["write_id"]):
            raise


def _acquire(store, episode_id: str, stage: str) -> dict:
    try:
        with store.prefix_scope(""):
            latest = store.read_admin_rerun_control(episode_id)
            if latest is not None and latest.get("state") == "claimed":
                raise RerunBusy(f"{latest.get('stage')} owns the re-run claim for {episode_id}")
            if latest is not None and latest.get("state") != "released":
                raise RerunClaimUnavailable(f"re-run claim for {episode_id} has an unexpected state")
            claim = _body(episode_id, (latest["version"] if latest else 0) + 1, "claimed", stage)
            _create(store, claim)
    except PhotoControlConflict as exc:
        raise RerunBusy(f"another run claimed {episode_id} first") from exc
    except PhotoControlUnavailable as exc:
        raise RerunClaimUnavailable(f"re-run claim for {episode_id} is unavailable: {exc}") from exc
    return claim


def _release(store, claim: dict, outcome: str) -> None:
    """Never raises: the handler's result or error must reach the caller either way."""
    release = _body(claim["episode_id"], claim["version"] + 1, "released", claim["stage"], outcome=outcome)
    try:
        with store.prefix_scope(""):
            _create(store, release)
    except (PhotoControlConflict, PhotoControlUnavailable) as exc:
        logger.error(
            f"Re-run claim for {claim['episode_id']} v{claim['version']} was not released and stays "
            f"blocked: {type(exc).__name__}: {exc}"
        )


@contextmanager
def claim(store, episode_id: str, stage: str):
    """Own ``episode_id`` for one run of ``stage``; RerunBusy / RerunClaimUnavailable before the body runs."""
    held = _acquire(store, episode_id, stage)
    outcome = "returned"
    try:
        yield
    except BaseException as exc:
        outcome = f"raised {type(exc).__name__}"
        raise
    finally:
        _release(store, held, outcome)
