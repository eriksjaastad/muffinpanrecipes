"""Notification formatting for recipe-pipeline events.

This module used to own the Discord webhook — four near-identical copies of
"build an embed, httpx.post it, check for 204". It no longer talks to any
channel. Each function here decides only WHAT an event says; `send_alert` in
backend/utils/alerts.py decides WHERE it goes.

That split exists because Erik does not read Discord ("I've noticed all of the
Discord notifications, but I just don't look. Emails do show up."), so email is
landing as a second channel. With the transport behind `send_alert`, that is a
one-file change instead of an edit at every call site.

The public function names and signatures are unchanged — `orchestrator.py`,
`cron_routes.py`, `art_director.py`, and `run_pipeline_stage.py` all import
them by name.
"""

import os
from typing import Optional

from backend.utils.alerts import _pytest_gate, send_alert  # noqa: F401
from backend.utils.logging import get_logger

logger = get_logger(__name__)

ADMIN_BASE_URL = os.getenv("MUFFINPAN_ADMIN_BASE_URL", "http://localhost:8000").rstrip("/")


def build_recipe_review_url(recipe_id: str, base_url: Optional[str] = None) -> str:
    """Build a direct admin review URL for a specific recipe."""
    origin = (base_url or ADMIN_BASE_URL).rstrip("/")
    return f"{origin}/admin/recipes/{recipe_id}"


def notify_recipe_ready(
    recipe_title: str,
    recipe_id: str,
    description_preview: Optional[str] = None,
    ingredient_count: int = 0,
    review_url: Optional[str] = None,
) -> bool:
    """Send a notification when a recipe is ready for review.

    Args:
        recipe_title: The recipe title
        recipe_id: Unique recipe ID
        description_preview: First ~200 chars of description
        ingredient_count: Number of ingredients
        review_url: Optional direct admin review URL. If omitted, one is built.

    Returns:
        True if the alert was delivered to at least one channel.
    """
    recipe_review_url = review_url or build_recipe_review_url(recipe_id)

    fields = [
        ("Recipe ID", recipe_id, True),
        ("Ingredients", str(ingredient_count), True),
        ("Review Link", recipe_review_url, False),
    ]
    if description_preview:
        preview = (
            description_preview[:200] + "..."
            if len(description_preview) > 200
            else description_preview
        )
        fields.append(("Description Preview", preview, False))

    return send_alert(
        subject="🧁 New Recipe Ready for Review",
        body=(
            f"**{recipe_title}**\n"
            f"🔎 **Review now:** [Open admin review page]({recipe_review_url})"
        ),
        severity="info",
        fields=fields,
        url=recipe_review_url,
    )


def _short(value: object, default: str = "unknown") -> str:
    """One identifier for a short alert line, whatever type the caller had.

    Judge metadata is model output and stage names come from many callers, so
    nothing here may assume a string: formatting must never raise out of an
    alert path (#7394).
    """
    text = " ".join(str(value).split()) if value is not None else ""
    return text[:60] or default


def _day(stage: object) -> str:
    return _short(stage).title()


# Pipeline alerts are a short status and its identifiers only (#7930): Erik
# wants "Episode paused / 2026-W40 · Friday", not a verdict, score list or
# error dump. Full evidence stays in the episode, the state file and the logs.


def notify_pipeline_failure(
    recipe_id: str,
    concept: str,
    stage: str,
    error_message: str,
) -> bool:
    """Send a loud failure alert so pipeline issues are never silent."""
    logger.warning(
        "Pipeline failure alert: recipe=%s stage=%s concept=%s error=%s",
        recipe_id, stage, concept, error_message,
    )
    return send_alert(
        subject="Pipeline failed",
        body=f"{_short(stage)} · {_short(recipe_id)}",
        severity="critical",
    )


def notify_judge_failure(
    concept: str,
    stage: str,
    verdict: str,
    episode_id: str,
    attempts: int,
) -> bool:
    """Alert when the judge fails a day's dialogue after all retries.

    The episode is paused. The alert says only that, with the week and day;
    the verdict is recorded on the episode, not in the alert.
    """
    return send_alert(
        subject="Episode paused",
        body=f"{_short(episode_id)} · {_day(stage)}",
        # Warning, not critical: the pipeline stopped on purpose at a quality
        # gate. Nothing crashed and nothing bad shipped.
        severity="warning",
    )


def notify_judge_advisory(
    concept: str,
    stage: str,
    verdict: str,
    episode_id: str,
    attempts: int,
    scores: dict | None = None,
    weakest: list[str] | None = None,
) -> bool:
    """Alert when a day's dialogue failed the judge but shipped anyway.

    The counterpart to notify_judge_failure: on the publish stage the judge is
    advisory, so an exhausted retry loop ships the best-scoring attempt rather
    than withholding the recipe from readers (#7394). The week is NOT paused,
    so this alert exists to make sure a weak conversation is still seen.

    `verdict`, `scores` and `weakest` are accepted for compatibility and
    deliberately not shown: they are model output recorded on the episode,
    and an alert that raised while formatting them would escape into
    _run_stage and fail the publish.
    """
    return send_alert(
        subject="Published · Dialogue below bar",
        body=f"{_short(episode_id)} · {_day(stage)}",
        severity="warning",
    )


def notify_batch_complete(
    recipe_count: int,
    recipe_titles: list[str],
) -> bool:
    """Send notification when a batch of recipes is complete.

    Args:
        recipe_count: Number of recipes in batch
        recipe_titles: List of recipe titles

    Returns:
        True if the alert was delivered to at least one channel.
    """
    titles_preview = "\n".join(f"• {t}" for t in recipe_titles[:5])
    if len(recipe_titles) > 5:
        titles_preview += f"\n... and {len(recipe_titles) - 5} more"

    return send_alert(
        subject="🧁 Recipe Batch Complete",
        body=f"**{recipe_count} recipes** ready for review",
        severity="info",
        fields=[("Recipes", titles_preview, False)],
    )


def build_episode_review_url(episode_id: str, base_url: Optional[str] = None, namespace: str = "") -> str:
    """Authenticated admin page where Erik approves the week's photos.

    ``namespace`` "test" points at the test-prefix week (#7936); the page
    reads and writes that namespace only.
    """
    origin = (base_url or ADMIN_BASE_URL).rstrip("/")
    query = "?ns=test" if namespace == "test" else ""
    return f"{origin}/admin/episodes/{episode_id}{query}#photo-review"


def notify_photos_ready(episode_id: str, candidate_count: int, namespace: str = "") -> bool:
    """Wednesday: the candidate photos are up and Sunday waits for a pick (#7936)."""
    url = build_episode_review_url(episode_id, namespace=namespace)
    label = f"{_short(episode_id)} (test)" if namespace == "test" else _short(episode_id)
    return send_alert(
        subject="Photos ready",
        body=f"{label} · {candidate_count} photos · pick one or reject all\n{url}",
        severity="info",
        url=url,
    )


def notify_publish_held(episode_id: str, reason: str, namespace: str = "") -> bool:
    """Sunday held the publish for photo approval. Sent once per hold."""
    label = {
        "photos_rejected": "photos rejected",
    }.get(reason, "awaiting photo approval")
    url = build_episode_review_url(episode_id, namespace=namespace)
    name = f"{_short(episode_id)} (test)" if namespace == "test" else _short(episode_id)
    return send_alert(
        subject="Publish held",
        body=f"{name} · {label}\n{url}",
        severity="warning",
        url=url,
    )
