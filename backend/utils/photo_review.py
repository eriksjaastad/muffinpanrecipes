"""Human photo approval for the weekly hero image (#7936).

Wednesday generates candidate photos and the automated vision review ranks
them; neither is an approval. Erik picks one candidate (or rejects all) on
the authenticated admin review page, and Sunday publishes only an approved
selection of the CURRENT image set.

One authoritative record: the photo-control log
-----------------------------------------------
``storage.read_photo_control`` / ``create_photo_control_version`` keep, per
episode, an append-only log of versions. Every version carries the whole
state, so the request, the decision and Sunday's publication claim cannot
disagree::

    awaiting --select--> approved --Sunday claim--> claimed --> publishing --> published
        |  ^                |  ^                       |
        |  +----reject------+  +--release (pre-publish failure, operator)
        +--reject--> rejected

    Wednesday (new image set): awaiting/approved/rejected --> awaiting

A transition creates version N+1 from version N with create-if-absent, so
two writers that both read N cannot both win (compare-and-swap). Once a
version is ``claimed`` choices are frozen (409) and Wednesday will not
replace the set. ``claimed`` can go back to ``approved`` (a pre-publication
failure, or the operator); a run that still holds the old claim then loses
its ``mark_publishing`` compare-and-swap and publishes nothing.

``publishing`` and ``published`` never return to a decidable state, however
much time passes. The ``publishing`` version carries the immutable
publication checkpoint: the exact episode body the publishing save writes.
A later Sunday run completes that SAME publication from the checkpoint with
no paid work, whether the publishing save failed, its outcome is unknown,
or a stale whole-episode save later removed the published fields.

The request carries a snapshot of Wednesday's photo evaluation
(``wednesday_snapshot``), so the admin page, Sunday's hold record and the
published episode all show the evaluation of the authoritative set, never a
stale mirror's (``apply_request_mirror``).

The episode keeps a display MIRROR of the request
(``stages.wednesday.photo_review``). A stale whole-episode save can
resurrect an old mirror, so nothing decides from it once a control log
exists. Before the first control version (weeks that predate this, or a
Wednesday whose control write failed) the request is derived from a
freshness-verified episode, and the first decision creates version 1 with
create-if-absent, so a bootstrap can never replace an existing control.

The automated evaluation stays in ``photography_data`` and
``confirmed_winner``; nothing here overwrites it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from backend.storage import (
    PHOTO_CONTROL_MAX_VERSION,
    PhotoControlConflict,
    PhotoControlUnavailable,
)

AWAITING = "awaiting"
APPROVED = "approved"
REJECTED = "rejected"
CLAIMED = "claimed"
PUBLISHING = "publishing"
PUBLISHED = "published"

DECIDABLE = (AWAITING, APPROVED, REJECTED)
# Publication has begun (or finished): choices and image sets are frozen.
FROZEN = (CLAIMED, PUBLISHING, PUBLISHED)
STATES = DECIDABLE + FROZEN

SCHEMA = 1
_ACTIONS = {"select": APPROVED, "reject": REJECTED}

# A Wednesday replacement or a decision retries a lost compare-and-swap
# against the new latest version this many times, re-validating each time.
_CAS_ATTEMPTS = 3


class PhotoReviewError(ValueError):
    """A review write that must be refused (bad candidate, stale set, ...)."""


class PublicationUnderway(RuntimeError):
    """The photo choice is frozen: Sunday has claimed or published it."""


class ReviewConflict(RuntimeError):
    """The control changed while this write was being made; reload."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Requests (what is up for review) ----------------------------------------

def build_candidates(wed: dict) -> list[dict]:
    """Every uploaded variant from the stored rounds, in round order.

    A variant is a candidate only when its canonical path is in the stage's
    ``image_paths`` with a non-empty uploaded URL at the same index: the review
    page must show the actual public image, never a local path.
    """
    paths = wed.get("image_paths") or []
    urls = wed.get("image_urls") or []
    url_for = {p: u for p, u in zip(paths, urls) if p and u}
    photo = wed.get("photography_data")
    rounds = photo.get("rounds", []) if isinstance(photo, dict) else []

    candidates: list[dict] = []
    seen: set[str] = set()
    for rnd in rounds:
        if not isinstance(rnd, dict):
            continue
        for v in rnd.get("variants", []) or []:
            if not isinstance(v, dict):
                continue
            path = str(v.get("path") or "")
            url = url_for.get(path, "")
            if not path or not url or path in seen:
                continue
            seen.add(path)
            candidates.append({
                "index": len(candidates) + 1,
                "path": path,
                "url": url,
                "variant": str(v.get("variant") or ""),
                "round": rnd.get("round"),
            })
    return candidates


def image_set_id(candidates: list[dict], generated_at: str) -> str:
    payload = json.dumps(
        [[c["path"], c["url"]] for c in candidates] + [generated_at],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _wednesday_snapshot(wed: dict) -> dict:
    """The automated evaluation of THIS image set, frozen into the request.

    ``photography_data`` holds the rounds ``build_candidates`` reads and the
    scores/defects the review page shows; ``confirmed_winner`` is the team
    pick. Stored with the request so a stale episode mirror can never pair
    one set's evaluation with another set's pixels.
    """
    photo = wed.get("photography_data")
    winner = wed.get("confirmed_winner")
    return {
        "photography_data": copy.deepcopy(photo) if isinstance(photo, dict) else None,
        "confirmed_winner": copy.deepcopy(winner) if isinstance(winner, dict) else {},
    }


def new_review(wed: dict) -> dict:
    """A fresh review request for the stage's current image set."""
    generated_at = _now()
    candidates = build_candidates(wed)
    return {
        "image_set_id": image_set_id(candidates, generated_at),
        "generated_at": generated_at,
        "requested_at": generated_at,
        "candidates": candidates,
        "image_paths": list(wed.get("image_paths") or []),
        "image_urls": list(wed.get("image_urls") or []),
        "wednesday_snapshot": _wednesday_snapshot(wed),
    }


def legacy_review(wed: dict) -> dict | None:
    """The implied request for a Wednesday that completed before #7936.

    Derived, not stored: the same candidates as ``build_candidates`` and the
    stage's own ``completed_at`` as fingerprint timestamp, so every reader
    computes the same ``image_set_id``. No "photos ready" notification was
    sent for it, and none is claimed. Generates nothing.
    """
    completed_at = wed.get("completed_at")
    if wed.get("status") != "complete" or not isinstance(completed_at, str) or not completed_at:
        return None
    candidates = build_candidates(wed)
    if not candidates:
        return None
    return {
        "image_set_id": image_set_id(candidates, completed_at),
        "generated_at": completed_at,
        "requested_at": None,
        "candidates": candidates,
        "image_paths": list(wed.get("image_paths") or []),
        "image_urls": list(wed.get("image_urls") or []),
        "wednesday_snapshot": _wednesday_snapshot(wed),
        "legacy": True,
    }


def episode_request(ep: dict) -> dict | None:
    """The request implied by the episode itself; bootstrap input ONLY.

    The mirror Wednesday wrote, if it still matches the stage's actual
    candidates, else (no mirror key at all) the derived legacy request. An
    explicit but malformed or stale mirror is authoritative-as-nothing: it
    never falls back to the derived one. Never consulted once a control
    version exists.
    """
    wed = (ep.get("stages") or {}).get("wednesday") or {}
    if not isinstance(wed, dict):
        return None
    if "photo_review" not in wed:
        return legacy_review(wed)
    review = wed.get("photo_review")
    if not isinstance(review, dict):
        return None
    candidates = build_candidates(wed)
    if not candidates:
        return None
    if image_set_id(candidates, str(review.get("generated_at") or "")) != review.get("image_set_id"):
        return None
    return {
        "image_set_id": review["image_set_id"],
        "generated_at": review.get("generated_at"),
        "requested_at": review.get("requested_at"),
        "candidates": candidates,
        "image_paths": list(wed.get("image_paths") or []),
        "image_urls": list(wed.get("image_urls") or []),
        "wednesday_snapshot": _wednesday_snapshot(wed),
    }


def request_snapshot(request: dict | None) -> dict | None:
    """The request's frozen Wednesday evaluation, or None if it has none."""
    snap = (request or {}).get("wednesday_snapshot")
    return snap if isinstance(snap, dict) and isinstance(snap.get("photography_data"), dict) else None


def apply_request_mirror(ep: dict, request: dict) -> bool:
    """Make ``ep``'s Wednesday photo fields show ``request`` (in place).

    Writes the image paths/URLs, the mirror, and, when the request carries
    one, its evaluation snapshot, so ``episode_request(ep)`` afterwards
    yields this request's ``image_set_id``. Returns whether the mirror now
    agrees with the request; False means the request has no snapshot and
    the episode's own evaluation belongs to a different set.
    """
    wed = ep.setdefault("stages", {}).setdefault("wednesday", {})
    snap = request_snapshot(request)
    if snap is not None:
        wed["photography_data"] = copy.deepcopy(snap["photography_data"])
        wed["confirmed_winner"] = copy.deepcopy(snap.get("confirmed_winner") or {})
    if request.get("image_paths") and request.get("image_urls"):
        for target in (wed, ep):
            target["image_paths"] = list(request["image_paths"])
            target["image_urls"] = list(request["image_urls"])
    prior = wed.get("photo_review") if isinstance(wed.get("photo_review"), dict) else {}
    mirror = {k: request.get(k) for k in ("image_set_id", "generated_at", "requested_at")}
    if prior.get("image_set_id") == request.get("image_set_id") and "notification" in prior:
        mirror["notification"] = prior["notification"]
    wed["photo_review"] = mirror
    derived = episode_request(ep)
    return bool(derived) and derived.get("image_set_id") == request.get("image_set_id")


# --- The control view -----------------------------------------------------------

@dataclass
class ControlView:
    """What a reader acts on. ``version`` 0 = derived, nothing stored yet."""

    episode_id: str
    state: Optional[str]
    request: Optional[dict]
    decision: Optional[dict]
    claim: Optional[dict]
    version: int
    raw: Optional[dict]

    @property
    def image_set_id(self) -> Optional[str]:
        return (self.request or {}).get("image_set_id")

    @property
    def selected(self) -> Optional[dict]:
        """The approved candidate, when the decision approves one of this request's.

        The decision itself must be an approval of THIS set: a malformed
        version whose state says approved but whose decision is a reject,
        lacks a status, or names another set approves nothing.
        """
        if self.state not in (APPROVED,) + FROZEN or not self.decision or not self.request:
            return None
        if self.decision.get("status") != APPROVED or not self.image_set_id \
                or self.decision.get("image_set_id") != self.image_set_id:
            return None
        sel = self.decision.get("selected")
        if not isinstance(sel, dict):
            return None
        for c in self.request.get("candidates") or []:
            if c.get("path") == sel.get("path") and c.get("url") == sel.get("url"):
                return c
        return None

    @property
    def frozen(self) -> bool:
        return self.state in FROZEN

    @property
    def legacy(self) -> bool:
        return bool((self.request or {}).get("legacy"))

    @property
    def publication(self) -> Optional[dict]:
        """The immutable publication checkpoint (publishing/published), or None."""
        pub = (self.raw or {}).get("publication")
        if self.state in (PUBLISHING, PUBLISHED) and isinstance(pub, dict) and pub.get("published_at"):
            return pub
        return None


def _valid_request(request: object) -> bool:
    return (
        isinstance(request, dict)
        and isinstance(request.get("image_set_id"), str)
        and bool(request["image_set_id"])
        and isinstance(request.get("candidates"), list)
        and bool(request["candidates"])
    )


def _view_from_control(episode_id: str, ctrl: dict) -> ControlView:
    state = ctrl.get("state")
    request = ctrl.get("request")
    if state not in STATES or not _valid_request(request):
        raise PhotoControlUnavailable(f"photo control {episode_id} v{ctrl.get('version')} is malformed")
    return ControlView(
        episode_id=episode_id,
        state=state,
        request=request,
        decision=ctrl.get("decision") if isinstance(ctrl.get("decision"), dict) else None,
        claim=ctrl.get("claim") if isinstance(ctrl.get("claim"), dict) else None,
        version=int(ctrl["version"]),
        raw=ctrl,
    )


def _derived_view(episode_id: str, ep: dict | None) -> ControlView:
    request = episode_request(ep) if ep else None
    return ControlView(
        episode_id=episode_id,
        state=AWAITING if request else None,
        request=request,
        decision=None,
        claim=None,
        version=0,
        raw=None,
    )


def read_view(store, episode_id: str, ep: dict | None) -> ControlView:
    """The authoritative view: the latest control version, else derived from ``ep``.

    Raises PhotoControlUnavailable when the control cannot be read. ``ep`` is
    only used when no control exists; callers that may write on the derived
    view must pass a freshness-verified episode.
    """
    ctrl = store.read_photo_control(episode_id)
    if ctrl is None:
        return _derived_view(episode_id, ep)
    return _view_from_control(episode_id, ctrl)


def _append(store, view: ControlView, *, state: str, request: dict | None, decision: dict | None,
            claim: dict | None, event: str, **extra) -> ControlView:
    """Create version view.version+1. Raises PhotoControlConflict / Unavailable.

    An Unavailable write is resolved by re-reading: if our version landed
    (same write_id) it succeeded, otherwise the failure propagates.
    """
    version = view.version + 1
    if version > PHOTO_CONTROL_MAX_VERSION:
        raise PhotoControlUnavailable("photo control log is full")
    body = {
        "schema": SCHEMA,
        "episode_id": view.episode_id,
        "version": version,
        "write_id": secrets.token_hex(8),
        "state": state,
        "request": request,
        "decision": decision,
        "claim": claim,
        "event": event,
        "at": _now(),
        **extra,
    }
    try:
        store.create_photo_control_version(view.episode_id, version, body)
    except PhotoControlUnavailable:
        latest = store.read_photo_control(view.episode_id)
        if not (latest and latest.get("version") == version and latest.get("write_id") == body["write_id"]):
            raise
    return _view_from_control(view.episode_id, body)


# --- Transitions -----------------------------------------------------------------

def register_request(store, episode_id: str, request: dict) -> ControlView:
    """Wednesday: make ``request`` the current set, voiding any earlier decision.

    Raises PublicationUnderway when Sunday has claimed or published the
    week; PhotoControlUnavailable on storage failure.
    """
    if not _valid_request(request):
        raise PhotoReviewError("No uploaded candidate photos to review")
    for _ in range(_CAS_ATTEMPTS):
        ctrl = store.read_photo_control(episode_id)
        view = _derived_view(episode_id, None) if ctrl is None else _view_from_control(episode_id, ctrl)
        if view.frozen:
            raise PublicationUnderway(f"photo control is {view.state}; the image set is frozen")
        try:
            return _append(store, view, state=AWAITING, request=request, decision=None, claim=None,
                           event="wednesday: new image set")
        except PhotoControlConflict:
            continue
    raise ReviewConflict("photo control kept changing while Wednesday registered its photos")


def ensure_not_frozen(store, episode_id: str) -> ControlView | None:
    """Wednesday, before paid generation: refuse a claimed/published week."""
    ctrl = store.read_photo_control(episode_id)
    if ctrl is None:
        return None
    view = _view_from_control(episode_id, ctrl)
    if view.frozen:
        raise PublicationUnderway(f"photo control is {view.state}; the image set is frozen")
    return view


def record_decision(
    store,
    episode_id: str,
    verified_ep: dict | None,
    *,
    action: str,
    image_set: str,
    path: str | None,
    decided_by: str,
    decided_by_id: str | None = None,
) -> ControlView:
    """Validate Erik's choice against the CURRENT control and append it.

    ``verified_ep`` must be a freshness-verified read of the episode; it
    is used for the published check and, before any control exists, to
    derive the request. Decision records live on a public Blob store:
    ``decided_by`` is a role label and ``decided_by_id`` an opaque keyed hash.

    Raises PhotoReviewError (bad input, published), PublicationUnderway
    (frozen), ReviewConflict (lost a race; reload) or PhotoControlUnavailable.
    """
    if action not in _ACTIONS:
        raise PhotoReviewError(f"Unknown action {action!r}")
    view = read_view(store, episode_id, verified_ep)
    if view.frozen:
        raise PublicationUnderway("Publishing is underway; the photo choice is frozen.")
    if verified_ep and verified_ep.get("published_at"):
        raise PhotoReviewError(
            "Episode is already published; its hero is pinned. "
            "Changing it is an editorial override, not a review."
        )
    if view.request is None:
        raise PhotoReviewError("No current photo review for this episode")
    if not image_set or image_set != view.image_set_id:
        raise PhotoReviewError("The photos changed since this page loaded; reload and review again")

    selected: dict | None = None
    if action == "select":
        match = [c for c in view.request["candidates"] if c.get("path") == path]
        if not match:
            raise PhotoReviewError("Not one of this episode's candidate photos")
        selected = {k: match[0].get(k) for k in ("index", "path", "url", "variant", "round")}
    decision = {
        "status": _ACTIONS[action],
        "image_set_id": view.image_set_id,
        "selected": selected,
        "decided_at": _now(),
        "decided_by": decided_by,
        "decided_by_id": decided_by_id,
    }
    try:
        return _append(store, view, state=_ACTIONS[action], request=view.request, decision=decision,
                       claim=None, event=f"review: {_ACTIONS[action]}")
    except PhotoControlConflict as exc:
        latest = store.read_photo_control(episode_id)
        if latest and latest.get("state") in FROZEN:
            raise PublicationUnderway("Publishing is underway; the photo choice is frozen.") from exc
        raise ReviewConflict("The photo review changed while saving; reload and choose again") from exc


class PhotoHold(Exception):
    """Sunday may not publish: the reason is a hold status."""

    def __init__(self, reason: str, view: ControlView):
        super().__init__(reason)
        self.reason = reason
        self.view = view


def hold_reason(view: ControlView) -> str | None:
    """None when the view is an approval Sunday may claim, else the hold reason."""
    if view.state == APPROVED and view.selected is not None:
        return None
    if view.state == REJECTED:
        return "photos_rejected"
    return "awaiting_photo_approval"


def claim_for_publication(store, episode_id: str, verified_ep: dict) -> ControlView:
    """Sunday, BEFORE any paid work: take the exclusive publication claim.

    Raises PhotoHold (not approved: no claim taken), PublicationUnderway
    (another run holds or finished it) or PhotoControlUnavailable. Exactly
    one concurrent Sunday can win the create of the claimed version.
    """
    for _ in range(_CAS_ATTEMPTS):
        view = read_view(store, episode_id, verified_ep)
        if view.frozen:
            raise PublicationUnderway(f"photo control is already {view.state}")
        reason = hold_reason(view)
        if reason:
            raise PhotoHold(reason, view)
        claim = {"claim_id": secrets.token_hex(8), "claimed_at": _now(), "from_version": view.version}
        try:
            return _append(store, view, state=CLAIMED, request=view.request, decision=view.decision,
                           claim=claim, event="sunday: publication claimed")
        except PhotoControlConflict:
            # Someone wrote first. Re-read and re-check from scratch: a
            # rival Sunday's claim is PublicationUnderway, a new decision is
            # judged on its own merits.
            continue
    raise ReviewConflict("photo control kept changing while Sunday tried to claim it")


def mark_publishing(store, claimed: ControlView, publication: dict) -> ControlView:
    """Immediately before the save that publishes. Raises if the claim was lost.

    ``publication`` is the exact episode body the publishing save will
    write; it becomes the immutable checkpoint every later run completes
    from. PhotoControlConflict here means the claim was released (operator
    or a pre-publication failure) or superseded: the caller must not publish.
    """
    if claimed.state != CLAIMED:
        raise PhotoReviewError(f"cannot publish from a {claimed.state} control")
    if not isinstance(publication, dict) or not publication.get("published_at"):
        raise PhotoReviewError("the publication checkpoint must be a published episode")
    return _append(store, claimed, state=PUBLISHING, request=claimed.request, decision=claimed.decision,
                   claim=claimed.claim, event="sunday: publishing",
                   publication=copy.deepcopy(publication))


def mark_published(store, publishing: ControlView, published_at: str) -> ControlView:
    return _append(store, publishing, state=PUBLISHED, request=publishing.request,
                   decision=publishing.decision, claim=publishing.claim,
                   event="sunday: published", published_at=published_at,
                   publication=publishing.publication)


def release_claim(store, claimed: ControlView, reason: str) -> ControlView:
    """Return a claimed approval to ``approved`` after a PRE-publication failure.

    Only valid while nothing has been published: the caller must not have
    reached ``mark_publishing``. A lost race raises and leaves the visible
    claimed state for the operator.
    """
    if claimed.state != CLAIMED:
        raise PhotoReviewError(f"cannot release a {claimed.state} control")
    return _append(store, claimed, state=APPROVED, request=claimed.request, decision=claimed.decision,
                   claim=None, event=f"sunday: claim released ({reason[:120]})")


def claim_age_seconds(view: ControlView, now: datetime | None = None) -> float | None:
    """Seconds since the view's version was written; informational only."""
    at = (view.raw or {}).get("at")
    try:
        written = datetime.fromisoformat(str(at))
    except ValueError:  # governance: allow-silent SF002: an unparseable timestamp has no age to show; nothing decides on it
        return None
    return ((now or datetime.now(timezone.utc)) - written).total_seconds()


def operator_release(store, episode_id: str, verified_ep: dict) -> ControlView:
    """RUNBOOK recovery for a ``claimed`` control whose Sunday run stopped.

    Only ``claimed`` is released, and age is not a condition: the release
    is a compare-and-swap, so a run that still holds the claim loses its
    ``mark_publishing`` and publishes nothing. ``publishing``/``published``
    are never released, however old: their checkpoint is completed by
    running Sunday again.
    """
    view = read_view(store, episode_id, verified_ep)
    if view.state in (PUBLISHING, PUBLISHED):
        raise PhotoReviewError(
            f"control is {view.state}; it is never released. Run Sunday again: it completes "
            "this same publication from its checkpoint with no paid work."
        )
    if view.state != CLAIMED:
        raise PhotoReviewError(f"control is {view.state}; nothing to release")
    if verified_ep.get("published_at"):
        raise PhotoReviewError("episode is published; investigate by hand before releasing")
    return _append(store, view, state=APPROVED, request=view.request, decision=view.decision,
                   claim=None, event="operator: released claimed control")


def reconcile_published(store, episode_id: str, ep: dict) -> ControlView | None:
    """When the episode proves a claim published, record it on the control.

    Only acts if ``ep`` is published with ``photo_approval.claim_id`` equal
    to the control's claim and the control is still claimed/publishing.
    Returns the new view, or None when nothing needed doing.
    """
    ctrl = store.read_photo_control(episode_id)
    if ctrl is None:
        return None
    view = _view_from_control(episode_id, ctrl)
    claim_id = ((ep.get("photo_approval") or {}) if isinstance(ep.get("photo_approval"), dict) else {}).get("claim_id")
    if (
        view.state in (CLAIMED, PUBLISHING)
        and ep.get("published_at")
        and claim_id
        and claim_id == (view.claim or {}).get("claim_id")
    ):
        return _append(store, view, state=PUBLISHED, request=view.request, decision=view.decision,
                       claim=view.claim, event="reconciled: episode shows this claim published",
                       published_at=ep.get("published_at"), publication=view.publication)
    return None


# --- Read-side helpers ------------------------------------------------------------

def dialogue_context(view: ControlView | None, *, unavailable: bool = False) -> dict[str, Any] | None:
    """Minimal factual review status for later-day dialogue, or None.

    None when the week has no review request (historical and lab runs).
    ``unavailable`` means the control could not be read: the status is then
    "unknown", which the prompt states as "not confirmed", never approved.
    """
    if unavailable:
        return {"status": "unknown", "selected_variant": None}
    if view is None or view.request is None:
        return None
    selected = view.selected
    if selected is not None:
        return {"status": APPROVED, "selected_variant": selected.get("variant")}
    status = REJECTED if view.state == REJECTED else AWAITING
    return {"status": status, "selected_variant": None}


def protected_image_paths(view: ControlView | None, ep: dict | None = None) -> list[str]:
    """Image paths cleanup must never trash: approved/pinned/legacy-confirmed."""
    paths: list[str] = []
    if view is not None and view.selected:
        paths.append(view.selected["path"])
    if isinstance(ep, dict):
        approval = ep.get("photo_approval")
        if isinstance(approval, dict) and approval.get("path"):
            paths.append(str(approval["path"]))
        wed = (ep.get("stages") or {}).get("wednesday") or {}
        winner = wed.get("confirmed_winner") if isinstance(wed, dict) else None
        if isinstance(winner, dict):
            for key in ("path", "featured_image"):
                if winner.get(key):
                    paths.append(str(winner[key]))
    return list(dict.fromkeys(paths))
