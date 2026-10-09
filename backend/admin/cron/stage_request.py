"""The cron routes' request model, body parsing and day-of-week guard (#8148).

Moved verbatim from backend/admin/cron_routes.py. cron_routes imports these back; its
handlers call `_parse_body` and `_verify_day_of_week` through cron_routes' namespace, so
tests that patch them there still reach every handler. `_test_mode_scope` stays in
cron_routes on purpose: it must use the same `storage` binding as the handlers.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, Request, status
from pydantic import BaseModel

from backend.utils.catalog import VALID_CATEGORIES, normalize_category


class StageRequest(BaseModel):
    episode_id: Optional[str] = None   # defaults to current ISO week
    concept: Optional[str] = None      # defaults to stored or generic
    # Breakfast | Savory | Sweet | Party. Only meaningful alongside an explicit
    # concept: it names the shelf the operator chose the dish for (#6858).
    target_category: Optional[str] = None
    model: Optional[str] = None        # override dialogue model (e.g. "openai/gpt-5.1")
    test: bool = False                 # test mode: saves to test/ prefix in blob
    force: bool = False                # skip day-of-week check (manual catch-ups only)
    # Narrative problem handed to every character for this stage (#7352). The
    # parameter has been plumbed end to end since W15 but nothing could supply
    # one, so every production prompt has carried "Injected event: none" and
    # the back half of the week had nothing left to decide. Operator-supplied
    # only: the cron sends no body, so unattended runs still inject nothing.
    # Automatic per-day events are a separate, gated question (#6967).
    injected_event: Optional[str] = None


async def _parse_body(request: Request) -> StageRequest:
    """Parse JSON body from POST, return defaults for GET (Vercel cron sends GET)."""
    if request.method != "POST":
        return StageRequest()

    # A body-less POST is a legitimate "run this stage with defaults" call and
    # keeps working. A body that IS present but malformed used to be swallowed
    # into the same defaults (#6856) — so a typo'd episode_id or a misspelled
    # "concept" key silently ran auto-pick against the current week instead.
    # That is now a 400.
    raw = await request.body()
    if not raw.strip():
        return StageRequest()
    try:
        body = StageRequest(**json.loads(raw))
        if body.target_category is not None and normalize_category(body.target_category) is None:
            raise ValueError(
                f"target_category must be one of {', '.join(VALID_CATEGORIES)}, "
                f"got {body.target_category!r}"
            )
        return body
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed cron request body: {type(e).__name__}: {e}",
        ) from e


_DAY_TO_WEEKDAY = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _verify_day_of_week(stage: str, body: StageRequest) -> None:
    """Reject requests that fire on the wrong day of the week.

    Vercel crons are scheduled per-day, but the handlers don't inherently
    know what day it is. This guard prevents accidental firings (e.g. curl
    testing that triggers real API calls on the wrong day).

    Bypass with force=True in POST body for manual catch-ups.
    Test mode also bypasses (compressed week simulations).
    """
    if body.force or body.test:
        return
    expected = _DAY_TO_WEEKDAY.get(stage)
    if expected is None:
        return
    actual = datetime.now(timezone.utc).weekday()
    if actual != expected:
        actual_name = list(_DAY_TO_WEEKDAY.keys())[actual]
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Stage '{stage}' can only run on {stage.title()} (today is {actual_name.title()}). Use force=true to override.",
        )
