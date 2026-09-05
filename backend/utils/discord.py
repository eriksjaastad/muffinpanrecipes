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


def notify_pipeline_failure(
    recipe_id: str,
    concept: str,
    stage: str,
    error_message: str,
) -> bool:
    """Send a loud failure alert so pipeline issues are never silent."""
    return send_alert(
        subject="🚨 Pipeline Failure",
        body=f"Recipe pipeline stopped for **{concept}**",
        severity="critical",
        fields=[
            ("Recipe ID", recipe_id, True),
            ("Failed Stage", stage or "unknown", True),
            ("Error", error_message[:900] or "unknown error", False),
        ],
    )


def notify_judge_failure(
    concept: str,
    stage: str,
    verdict: str,
    episode_id: str,
    attempts: int,
) -> bool:
    """Alert when the judge fails a day's dialogue after all retries.

    This means the conversation had quality issues that couldn't be fixed
    by regenerating. Episode is paused — needs manual review.
    """
    return send_alert(
        subject="Judge Failed — Episode Paused",
        body=f"**{concept}** — {stage.title()} dialogue failed quality review",
        # Warning, not critical: the pipeline stopped on purpose at a quality
        # gate. Nothing crashed and nothing bad shipped.
        severity="warning",
        fields=[
            ("Episode", episode_id, True),
            ("Day", stage.title(), True),
            ("Attempts", str(attempts), True),
            ("Last Verdict", verdict[:900], False),
        ],
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
