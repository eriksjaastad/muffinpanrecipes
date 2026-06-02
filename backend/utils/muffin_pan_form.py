"""Deterministic muffin-pan form checks.

The brand promise is not "food baked near a muffin tin"; the pan must shape
the food into self-contained cups, bites, nests, or mini loaves that hold
together after removal.
"""

from __future__ import annotations

import re
from typing import Any

LOOSE_OR_INCIDENTAL_PATTERNS = (
    r"\bloose fillings?\b",
    r"\bloose mixture\b",
    r"\bspoon(?:ed)?\s+(?:the\s+)?(?:mixture|filling|topping|salad)\b",
    r"\bserve(?:d)?\s+(?:directly\s+)?(?:from|in)\s+the\s+(?:muffin\s+)?(?:tin|pan)\b",
    r"\btin\s+is\s+(?:just\s+)?(?:a\s+)?vessel\b",
    r"\bmuffin\s+(?:tin|pan)\s+is\s+incidental\b",
    r"\bportion control\b",
)

SHAPED_PORTION_PATTERNS = (
    r"\bself-contained\b",
    r"\bhold(?:s|ing)?\s+(?:their\s+|its\s+)?shape\b",
    r"\bholds?\s+together\b",
    r"\bcups?\b",
    r"\bbites?\b",
    r"\bnests?\b",
    r"\btassies?\b",
    r"\bmini\s+loaves\b",
    r"\bfrittatas?\b",
    r"\bmeatloaves\b",
)

BIND_OR_RELEASE_PATTERNS = (
    r"\bbind(?:s|er|ing)?\b",
    r"\bset(?:s|ting)?\b",
    r"\bfirm(?:s|ed)?\b",
    r"\bcohesive\b",
    r"\bpack(?:ed)?\s+firmly\b",
    r"\bpress(?:ed)?\b",
    r"\brest\s+\d+\s+minutes?\b",
    r"\brelease\b",
    r"\bloosen\b",
    r"\bunmold(?:ed|ing)?\b",
    r"\blift\s+(?:them\s+)?out\b",
    r"\bpop\s+(?:them\s+)?out\b",
    r"\bfall\s+apart\b",
    r"\bstructural\b",
)


def _flatten_recipe_text(recipe: dict[str, Any]) -> str:
    parts: list[str] = [
        str(recipe.get("title", "")),
        str(recipe.get("description", "")),
        str(recipe.get("chef_notes", "")),
    ]
    for ingredient in recipe.get("ingredients", []) or []:
        if isinstance(ingredient, dict):
            parts.append(
                " ".join(
                    str(ingredient.get(key, ""))
                    for key in ("amount", "item", "notes")
                )
            )
        else:
            parts.append(str(ingredient))
    for step in recipe.get("instructions", []) or []:
        parts.append(str(step))
    return re.sub(r"\s+", " ", " ".join(parts).lower()).strip()


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def check_muffin_pan_form(recipe: dict[str, Any] | None) -> str | None:
    """Return a failure reason if the recipe does not take muffin-pan form."""
    if not isinstance(recipe, dict) or not recipe:
        return "recipe data is missing"

    text = _flatten_recipe_text(recipe)
    if _matches_any(text, LOOSE_OR_INCIDENTAL_PATTERNS):
        return (
            "muffin tin use appears incidental or loose; recipe must bind into "
            "self-contained portions"
        )

    if not _matches_any(text, SHAPED_PORTION_PATTERNS):
        return (
            "recipe does not describe a self-contained muffin-pan cup, bite, "
            "nest, or mini loaf"
        )

    if not _matches_any(text, BIND_OR_RELEASE_PATTERNS):
        return (
            "recipe does not explain how the portions set, bind, release, or "
            "hold shape after removal from the pan"
        )

    return None
