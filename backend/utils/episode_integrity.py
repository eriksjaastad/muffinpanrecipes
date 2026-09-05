"""Assertions about a weekly episode that no other check would catch (#6857).

The site can look perfectly healthy while the pipeline that produces it is
degraded. W36 (2026-08-31) is the reference case: every stage reported
`complete`, the judge PASSed all six days, `/this-week` rendered a real recipe
with a real title, and health_check's seven checks all passed — while the
concept was the placeholder, the catalog-aware novelty scorer had never run,
and the recipe was a duplicate of one already published. Nothing anywhere said
"degraded". The only trace was a concept string buried in the episode JSON.

This module is the pure logic: no network, no storage, no FastAPI. Both
`scripts/health_check.py` (the Discord-alerting monitor) and
`scripts/session_pipeline_status.py` (the SessionStart surface) feed it an
episode dict and render whatever comes back.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

# The concept a week carries before anything has picked one. A stored
# placeholder means scripts/pick_concept.py never ran, and with it the
# catalog-aware novelty scoring that is the pipeline's strongest duplicate
# defense. cron_routes imports this so there is exactly one copy.
PLACEHOLDER_CONCEPT = "Weekly Muffin Pan Recipe"

DAY_ORDER = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]

# UTC (hour, minute) each stage is scheduled at, mirroring vercel.json crons.
# Keep in sync with the "crons" block there.
CRON_SCHEDULE_UTC: dict[str, tuple[int, int]] = {
    "monday": (14, 30),
    "tuesday": (14, 30),
    "wednesday": (14, 30),
    "thursday": (14, 30),
    "friday": (14, 30),
    "saturday": (14, 30),
    "sunday": (0, 0),
}

# How long after its scheduled time a stage may still be in flight before we
# call it missing. Wednesday is the slow one (~3-4 min of image generation);
# 45 minutes covers a retry and a cold start without crying wolf. This check
# alerts Erik's Discord under `-c prd`, so a false positive is expensive: it
# trains him to ignore the channel, which is worse than having no check.
STAGE_GRACE_MINUTES = 45

_EPISODE_ID_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def parse_episode_id(episode_id: str) -> tuple[int, int]:
    """Return (iso_year, iso_week) for an episode id like '2026-W36'."""
    match = _EPISODE_ID_RE.match(str(episode_id).strip())
    if not match:
        raise ValueError(f"not an ISO week episode id: {episode_id!r}")
    return int(match.group(1)), int(match.group(2))


def current_episode_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def stage_deadline(episode_id: str, day: str) -> datetime:
    """UTC datetime by which `day`'s cron should have fired for that week."""
    iso_year, iso_week = parse_episode_id(episode_id)
    scheduled = date.fromisocalendar(iso_year, iso_week, DAY_ORDER.index(day) + 1)
    hour, minute = CRON_SCHEDULE_UTC[day]
    return datetime(
        scheduled.year, scheduled.month, scheduled.day, hour, minute,
        tzinfo=timezone.utc,
    )


def stages_due(
    episode_id: str,
    now: datetime | None = None,
    grace_minutes: int = STAGE_GRACE_MINUTES,
) -> list[str]:
    """Days whose cron window has fully passed for this episode."""
    now = now or datetime.now(timezone.utc)
    grace = timedelta(minutes=grace_minutes)
    return [d for d in DAY_ORDER if now >= stage_deadline(episode_id, d) + grace]


def _recipe_title(episode: dict) -> str:
    monday = episode.get("stages", {}).get("monday", {})
    return str((monday.get("recipe_data") or {}).get("title") or "").strip()


def _catalog_titles_excluding_self(episode: dict, catalog: list[dict]) -> list[str]:
    """Published titles minus this episode's own entry.

    Once a week publishes, its recipe IS in the catalog, so a naive conflict
    check would flag every published week against itself. Entries carry
    `episode_id` since PR #45; for older ones, fall back to dropping a single
    exact-title match.

    The title fallback applies ONLY to an already-published episode. An
    unpublished week has no catalog entry to be confused with its own, so
    excluding by title there would silently delete the exact-duplicate rule —
    the strictest one, and the one most worth keeping before publish.
    """
    episode_id = str(episode.get("episode_id") or "")
    own_title = _recipe_title(episode).lower()
    allow_title_fallback = bool(episode.get("published_at"))
    titles: list[str] = []
    dropped_by_title = False
    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        if episode_id and entry.get("episode_id") == episode_id:
            continue
        if (
            allow_title_fallback
            and not entry.get("episode_id")
            and not dropped_by_title
            and title.lower() == own_title
        ):
            dropped_by_title = True
            continue
        titles.append(title.lower())
    return titles


def episode_integrity_failures(
    episode: dict | None,
    *,
    catalog: list[dict] | None = None,
    now: datetime | None = None,
    grace_minutes: int = STAGE_GRACE_MINUTES,
) -> list[str]:
    """Return one string per integrity failure. Empty list means healthy.

    `catalog` is the published `pages/recipes.json` "recipes" list; pass None
    to skip the title-collision check (it is the only network-dependent one).

    Deliberately NOT checked: whether each stage's `completed_at` falls inside
    its cron window. Manual recovery re-runs are the documented RUNBOOK fix
    for several incidents and legitimately produce out-of-window timestamps —
    W36 itself was regenerated Mon-Sat by hand on a Saturday. Alerting on that
    would fire on every recovery, which is how a monitor gets ignored.
    """
    if not episode:
        return []  # caller decides whether a missing episode is a problem

    now = now or datetime.now(timezone.utc)
    episode_id = str(episode.get("episode_id") or "")
    stages = episode.get("stages", {}) or {}
    monday = stages.get("monday", {}) or {}
    failures: list[str] = []

    # 1. The placeholder concept must never persist. This one line would have
    #    caught W36 on the Monday it happened.
    concept = str(episode.get("concept") or "").strip()
    if concept == PLACEHOLDER_CONCEPT:
        failures.append(
            f"concept is the placeholder {PLACEHOLDER_CONCEPT!r} — "
            f"scripts/pick_concept.py never ran, so this week has NO "
            f"catalog-aware duplicate avoidance (card #6855)"
        )
    elif not concept:
        failures.append("episode has no concept at all")

    # 2. target_category is written only on the concept picker's success path,
    #    so None on a completed Monday is the fingerprint of the old
    #    fail-open branch even when the concept string looks fine.
    if monday.get("status") == "complete":
        target_category = episode.get("target_category") or monday.get("target_category")
        if not target_category:
            failures.append(
                "monday completed with no target_category — the category "
                "picker did not run, so category balancing is off"
            )

    # 3. Every stage whose cron window has passed must be complete.
    if episode_id:
        for day in stages_due(episode_id, now=now, grace_minutes=grace_minutes):
            stage_status = (stages.get(day) or {}).get("status")
            if stage_status == "complete":
                continue
            detail = (stages.get(day) or {}).get("error") or ""
            failures.append(
                f"{day} stage is {stage_status or 'missing'!r} but its cron "
                f"window closed at "
                f"{stage_deadline(episode_id, day):%Y-%m-%d %H:%M} UTC"
                + (f" — {detail[:200]}" if detail else "")
            )

    # 4. The recipe title must not collide with what is already published.
    if catalog is not None:
        title = _recipe_title(episode)
        if title:
            from backend.utils.title_validator import check_title_conflict

            conflict = check_title_conflict(
                title, _catalog_titles_excluding_self(episode, catalog)
            )
            if conflict:
                failures.append(
                    f"recipe title {title!r} collides with the published "
                    f"catalog: {conflict}"
                )

    return failures


def episode_summary(episode: dict | None) -> str:
    """One-line description of an episode for a status banner."""
    if not episode:
        return "no episode"
    episode_id = episode.get("episode_id") or "?"
    title = _recipe_title(episode) or "(no recipe yet)"
    complete = sum(
        1
        for day in DAY_ORDER
        if (episode.get("stages", {}).get(day) or {}).get("status") == "complete"
    )
    published = " published" if episode.get("published_at") else ""
    return f'{episode_id} "{title}", {complete}/7 stages complete{published}'
