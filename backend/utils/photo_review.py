"""Human photo approval for the weekly hero image (#7936).

Wednesday generates the candidate photos and the automated vision review
ranks them; neither is an approval. Erik picks one candidate (or rejects
all) on the authenticated admin review page, and Sunday publishes only an
approved, still-current selection.

Two records, two writers:

* The REQUEST lives on the episode at ``stages.wednesday.photo_review`` and
  only Wednesday writes it: candidates, ``image_set_id`` (fingerprint of the
  set; a Wednesday rerun changes it, which orphans every earlier decision),
  ``requested_at`` and ``notification``.
* The DECISION is a separate append-only record per episode + image set
  (``storage.add_photo_decision``). Only the admin review endpoint writes it.
  A cron's whole-episode save cannot erase it, and every reader lists it
  fresh, so a warm process cannot act on an older decision.

The automated evaluation stays in ``photography_data`` and
``confirmed_winner``; neither record overwrites it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

AWAITING = "awaiting"
APPROVED = "approved"
REJECTED = "rejected"
# A decision record exists but does not validate against the current set.
# It approves nothing.
INVALID = "invalid"

_ACTIONS = {"select": APPROVED, "reject": REJECTED}


class PhotoReviewError(ValueError):
    """A review write that must be refused (bad candidate, stale set, ...)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def new_review(wed: dict) -> dict:
    """A fresh review request for the stage's current image set."""
    generated_at = _now()
    candidates = build_candidates(wed)
    return {
        "image_set_id": image_set_id(candidates, generated_at),
        "generated_at": generated_at,
        "requested_at": generated_at,
        "candidates": candidates,
    }


def current_review(ep: dict) -> dict | None:
    """The review request, or None if it no longer matches the stored images.

    A Wednesday rerun replaces the image set; a request whose fingerprint no
    longer matches the stage's actual candidates is stale and approves nothing.
    """
    wed = (ep.get("stages") or {}).get("wednesday") or {}
    review = wed.get("photo_review") if isinstance(wed, dict) else None
    if not isinstance(review, dict):
        return None
    candidates = build_candidates(wed)
    if not candidates:
        return None
    if image_set_id(candidates, str(review.get("generated_at") or "")) != review.get("image_set_id"):
        return None
    return review


# --- Decisions -------------------------------------------------------------

def make_decision(
    ep: dict,
    *,
    action: str,
    image_set: str,
    path: str | None,
    decided_by: str,
    decided_by_id: str | None = None,
) -> dict:
    """Validate Erik's choice against ``ep`` and return the record to store.

    Decision records live on a public Blob store: ``decided_by`` is a role
    label and ``decided_by_id`` an opaque keyed hash, never an email or
    OAuth subject. Raises PhotoReviewError. Does not touch ``ep`` or storage.
    """
    if ep.get("published_at"):
        raise PhotoReviewError(
            "Episode is already published; its hero is pinned. "
            "Changing it is an editorial override, not a review."
        )
    review = current_review(ep)
    if review is None:
        raise PhotoReviewError("No current photo review for this episode")
    if not image_set or image_set != review.get("image_set_id"):
        raise PhotoReviewError("The photos changed since this page loaded; reload and review again")
    if action not in _ACTIONS:
        raise PhotoReviewError(f"Unknown action {action!r}")

    selected: dict | None = None
    if action == "select":
        match = [c for c in review["candidates"] if c["path"] == path]
        if not match:
            raise PhotoReviewError("Not one of this episode's candidate photos")
        selected = {k: match[0][k] for k in ("index", "path", "url", "variant", "round")}

    return {
        "episode_id": str(ep.get("episode_id") or ""),
        "image_set_id": review["image_set_id"],
        "status": _ACTIONS[action],
        "selected": selected,
        "decided_at": _now(),
        "decided_by": decided_by,
        "decided_by_id": decided_by_id,
    }


def save_decision(store, ep: dict, record: dict) -> dict:
    """Append ``record`` for the episode's current set. Raises on failure."""
    record_id = store.add_photo_decision(str(ep["episode_id"]), record["image_set_id"], record)
    return {**record, "record_id": record_id}


def load_decision(store, ep: dict) -> dict | None:
    """The latest decision for the episode's CURRENT image set, read fresh.

    None when there is no current review or no decision yet. Storage failures
    raise (``PhotoDecisionUnavailable``): an unreadable decision is never
    treated as an approval, and never silently as "awaiting" either.
    """
    review = current_review(ep)
    if review is None:
        return None
    return store.latest_photo_decision(str(ep["episode_id"]), review["image_set_id"])


def review_state(ep: dict, decision: dict | None) -> dict:
    """Pure overlay of a decision on the request.

    status is awaiting | approved | rejected | invalid, or None when there is
    no current review at all. ``selected`` is a current candidate (approved
    only).
    """
    review = current_review(ep)
    if review is None:
        return {"status": None, "selected": None, "record_id": None, "review": None}
    state = {"status": AWAITING, "selected": None, "record_id": None, "review": review,
             "decided_at": None, "decided_by": None}
    if not decision:
        return state
    state.update({
        "record_id": decision.get("record_id"),
        "decided_at": decision.get("decided_at"),
        "decided_by": decision.get("decided_by"),
    })
    status = decision.get("status")
    if decision.get("image_set_id") != review["image_set_id"] or status not in (APPROVED, REJECTED):
        state["status"] = INVALID
        return state
    if status == REJECTED:
        state["status"] = REJECTED
        return state
    selected = decision.get("selected")
    if isinstance(selected, dict):
        for c in review["candidates"]:
            if c["path"] == selected.get("path") and c["url"] == selected.get("url"):
                state.update({"status": APPROVED, "selected": c})
                return state
    state["status"] = INVALID
    return state


def approved_selection(ep: dict, decision: dict | None) -> dict | None:
    """The human-approved candidate for the current image set, else None."""
    state = review_state(ep, decision)
    return state["selected"] if state["status"] == APPROVED else None


def hold_status(ep: dict, decision: dict | None) -> str | None:
    """None when Sunday may publish; otherwise the reason it must wait."""
    state = review_state(ep, decision)
    if state["status"] == APPROVED:
        return None
    if state["status"] == REJECTED:
        return "photos_rejected"
    return "awaiting_photo_approval"


def dialogue_context(ep: dict, decision: dict | None, *, unavailable: bool = False) -> dict[str, Any] | None:
    """Minimal factual review status for later-day dialogue, or None.

    None when the week has no review request (historical and lab runs).
    ``unavailable`` means the decision could not be read: the status is then
    "unknown", which the prompt states as "not confirmed", never approved.
    """
    state = review_state(ep, None if unavailable else decision)
    if state["status"] is None:
        return None
    if unavailable:
        return {"status": "unknown", "selected_variant": None}
    status = state["status"] if state["status"] != INVALID else AWAITING
    selected = state["selected"]
    return {"status": status, "selected_variant": selected.get("variant") if selected else None}
