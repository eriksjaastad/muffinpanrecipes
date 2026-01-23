"""Discord webhook integration for recipe review notifications."""

import os
from typing import Optional, Dict, Any
import httpx

from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Webhook URL - can be overridden via environment variable
DISCORD_WEBHOOK_URL = os.getenv(
    "MUFFINPAN_DISCORD_WEBHOOK",
    "https://discord.com/api/webhooks/1464253198410715310/kVK9q3bQ7td95VoBXxi8C3QDUfGaiK4LRvskeSEr204R62frcaaOaKuW1iscUOqckssF"
)


def notify_recipe_ready(
    recipe_title: str,
    recipe_id: str,
    description_preview: Optional[str] = None,
    ingredient_count: int = 0,
) -> bool:
    """
    Send a Discord notification when a recipe is ready for review.

    Args:
        recipe_title: The recipe title
        recipe_id: Unique recipe ID
        description_preview: First ~200 chars of description
        ingredient_count: Number of ingredients

    Returns:
        True if notification sent successfully
    """
    if not DISCORD_WEBHOOK_URL:
        logger.warning("Discord webhook URL not configured")
        return False

    # Build rich embed
    embed = {
        "title": f"🧁 New Recipe Ready for Review",
        "description": f"**{recipe_title}**",
        "color": 0xF5A623,  # Muffin-ish orange
        "fields": [
            {"name": "Recipe ID", "value": recipe_id, "inline": True},
            {"name": "Ingredients", "value": str(ingredient_count), "inline": True},
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
        else:
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
        "title": f"🧁 Recipe Batch Complete",
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
