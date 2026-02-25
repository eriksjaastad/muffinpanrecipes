"""Discord webhook integration for recipe review notifications."""

import os
from typing import Optional
import httpx

from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Webhook URL must come from environment (no hardcoded defaults)
DISCORD_WEBHOOK_URL = os.getenv("MUFFINPAN_DISCORD_WEBHOOK")
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
    """
    Send a Discord notification when a recipe is ready for review.

    Args:
        recipe_title: The recipe title
        recipe_id: Unique recipe ID
        description_preview: First ~200 chars of description
        ingredient_count: Number of ingredients
        review_url: Optional direct admin review URL. If omitted, one is built.

    Returns:
        True if notification sent successfully
    """
    if not DISCORD_WEBHOOK_URL:
        logger.warning("Discord webhook URL not configured")
        return False

    recipe_review_url = review_url or build_recipe_review_url(recipe_id)

    # Build rich embed
    embed = {
        "title": "🧁 New Recipe Ready for Review",
        "description": (
            f"**{recipe_title}**\n"
            f"🔎 **Review now:** [Open admin review page]({recipe_review_url})"
        ),
        "url": recipe_review_url,
        "color": 0xF5A623,  # Muffin-ish orange
        "fields": [
            {"name": "Recipe ID", "value": recipe_id, "inline": True},
            {"name": "Ingredients", "value": str(ingredient_count), "inline": True},
            {"name": "Review Link", "value": recipe_review_url, "inline": False},
        ],
    }

    if description_preview:
        preview = description_preview[:200] + "..." if len(description_preview) > 200 else description_preview
        embed["fields"].append({
            "name": "Description Preview",
            "value": preview,
            "inline": False,
        })

    payload = {
        "embeds": [embed],
    }

    try:
        response = httpx.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10.0)
        if response.status_code == 204:
            logger.info(f"Discord notification sent for recipe: {recipe_title}")
            return True
        logger.error(f"Discord webhook failed: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        logger.error(f"Discord notification error: {e}")
        return False


def notify_batch_complete(
    recipe_count: int,
    recipe_titles: list[str],
) -> bool:
    """
    Send notification when a batch of recipes is complete.

    Args:
        recipe_count: Number of recipes in batch
        recipe_titles: List of recipe titles

    Returns:
        True if notification sent successfully
    """
    if not DISCORD_WEBHOOK_URL:
        return False

    titles_preview = "\n".join(f"• {t}" for t in recipe_titles[:5])
    if len(recipe_titles) > 5:
        titles_preview += f"\n... and {len(recipe_titles) - 5} more"

    embed = {
        "title": "🧁 Recipe Batch Complete",
        "description": f"**{recipe_count} recipes** ready for review",
        "color": 0x27AE60,  # Green for complete
        "fields": [
            {"name": "Recipes", "value": titles_preview, "inline": False},
        ],
    }

    payload = {"embeds": [embed]}

    try:
        response = httpx.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10.0)
        return response.status_code == 204
    except Exception as e:
        logger.error(f"Discord batch notification error: {e}")
        return False
