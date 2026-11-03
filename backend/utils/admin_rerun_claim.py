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
  blocked; acquisition never expires or steals a claim.
* A hard process crash (no ``finally`` runs) leaves ``claimed`` forever, so an
  operator can call ``reconcile`` for the exact episode. It appends a ``released``
  version only when the latest version is ``claimed`` and its recorded ``at`` is
  older than ``RECONCILE_MIN_AGE``, which must stay well above Vercel's longest
  function execution (``maxDuration`` 300s in vercel.json) so a live run is never
  released. Same CAS as every other version.
* Bodies hold the stage, state, outcome and timestamps only: the log is the audit
  trail and carries no secrets, request data or user identity.
"""

from __future__ import annotations

import logging
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from backend.storage import (
    PHOTO_CONTROL_MAX_VERSION,
    PhotoControlConflict,
    PhotoControlUnavailable,
)

logger = logging.getLogger(__name__)

SCHEMA = "admin_rerun/v1"

# A live run cannot outlast the Vercel function limit (vercel.json maxDuration 300s);
# one hour stays far above any platform ceiling before an operator may release.
RECONCILE_MIN_AGE = timedelta(hours=1)
RECONCILED_OUTCOME = "operator reconciliation: claim older than the function limit, run presumed crashed"


class RerunBusy(RuntimeError):
    """Another run owns this episode, or its release is unconfirmed."""


class RerunClaimUnavailable(RuntimeError):
    """The claim could not be read or written authoritatively; nothing may run."""


class RerunNotReconcilable(RuntimeError):
    """Reconcile refused: the latest state is not a claim old enough to release."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _body(episode_id: str, version: int, state: str, stage: str, at: datetime | None = None, **extra) -> dict:
    return {
        "schema": SCHEMA,
        "episode_id": episode_id,
        "version": version,
        "write_id": secrets.token_hex(8),
        "state": state,
        "stage": stage,
        "at": (at or _utcnow()).isoformat(),
        **extra,
    }


def _validated_latest(body: object, episode_id: str) -> dict | None:
    """Return a trustworthy control body; refuse malformed audit state."""
    if body is None:
        return None
    from backend.utils.episode_integrity import DAY_ORDER

    if (
        not isinstance(body, dict)
        or body.get("schema") != SCHEMA
        or body.get("episode_id") != episode_id
        or isinstance(body.get("version"), bool)
        or not isinstance(body.get("version"), int)
        or not 1 <= body["version"] <= PHOTO_CONTROL_MAX_VERSION
        or body.get("state") not in {"claimed", "released"}
        or body.get("stage") not in DAY_ORDER
        or not isinstance(body.get("write_id"), str)
        or not body["write_id"]
    ):
        raise RerunClaimUnavailable(f"re-run claim for {episode_id} has an unexpected body")
    try:
        recorded_at = datetime.fromisoformat(body["at"])
        if recorded_at.tzinfo is None:
            raise ValueError("timestamp has no timezone")
    except (KeyError, TypeError, ValueError) as exc:
        raise RerunClaimUnavailable(f"re-run claim for {episode_id} has no usable timestamp") from exc
    return body


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
            latest = _validated_latest(store.read_admin_rerun_control(episode_id), episode_id)
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


def reconcile(store, episode_id: str, now=None) -> dict:
    """Release a claim left by a crashed run; return the appended ``released`` body.

    Only a ``claimed`` latest version older than ``RECONCILE_MIN_AGE`` is released.
    ``now`` is an injectable clock (default: UTC now). RerunNotReconcilable for a
    recent claim, nothing to release, or a lost race; RerunClaimUnavailable when
    the log is unreadable, unexpected, or the write outcome is unknown. Nothing is
    written unless the release is created.
    """
    current = (now or _utcnow)()
    try:
        with store.prefix_scope(""):
            latest = _validated_latest(store.read_admin_rerun_control(episode_id), episode_id)
            if latest is None or latest.get("state") == "released":
                raise RerunNotReconcilable(f"re-run claim for {episode_id} is not claimed; nothing to reconcile")
            if latest.get("state") != "claimed" or not isinstance(latest.get("version"), int):
                raise RerunClaimUnavailable(f"re-run claim for {episode_id} has an unexpected state")
            try:
                claimed_at = datetime.fromisoformat(latest["at"])
                age = current - claimed_at
            except (KeyError, TypeError, ValueError) as exc:
                raise RerunClaimUnavailable(f"re-run claim for {episode_id} has no usable timestamp") from exc
            if age < RECONCILE_MIN_AGE:
                raise RerunNotReconcilable(
                    f"{latest.get('stage')} claimed {episode_id} too recently to be presumed crashed; "
                    f"reconcile is allowed once the claim is older than {RECONCILE_MIN_AGE}"
                )
            release = _body(
                episode_id, latest["version"] + 1, "released", latest.get("stage"), at=current,
                outcome=RECONCILED_OUTCOME, reconciled_version=latest["version"],
            )
            _create(store, release)
    except PhotoControlConflict as exc:
        raise RerunNotReconcilable(f"the re-run claim for {episode_id} changed first; nothing was written") from exc
    except PhotoControlUnavailable as exc:
        raise RerunClaimUnavailable(f"re-run claim for {episode_id} is unavailable: {exc}") from exc
    logger.warning(f"Re-run claim for {episode_id} v{latest['version']} reconciled by an operator")
    return release


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
