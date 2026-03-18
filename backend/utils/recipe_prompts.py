"""Recipe and description generation via model router.

Recipe and description prompt templates with structured parsing,
routed through generate_response() so any provider works.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from backend.config import config
from backend.utils.logging import get_logger
from backend.utils.model_router import generate_response

logger = get_logger(__name__)

# Default model for recipe/copywriting generation.
# Centralized in backend/config.py — override via RECIPE_MODEL env var or Doppler.
DEFAULT_RECIPE_MODEL = config.recipe_model


# ---------------------------------------------------------------------------
# Recipe generation (Baker / Margaret)
# ---------------------------------------------------------------------------
def generate_recipe(
    concept: str,
    personality_context: Dict[str, Any],
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a complete muffin tin recipe.

    Args:
        concept: Recipe concept (e.g. "Savory Breakfast Egg Muffins")
        personality_context: Baker's personality dict (name, backstory, quirks, core_traits)
        model: Override model (default: RECIPE_MODEL env var or openai/gpt-5-mini)

    Returns:
        Structured recipe dict with title, ingredients, instructions, etc.
    """
    use_model = model or DEFAULT_RECIPE_MODEL
    system_prompt = _build_recipe_system_prompt(personality_context)
    user_prompt = _build_recipe_user_prompt(concept)

    response = generate_response(
        prompt=user_prompt,
        system_prompt=system_prompt,
        model=use_model,
        temperature=0.7,
    )

    return _parse_recipe_response(response, concept)


def _build_recipe_system_prompt(personality_context: Dict[str, Any]) -> str:
    name = personality_context.get("name", "Chef")
    backstory = personality_context.get("backstory", "")
    quirks = personality_context.get("quirks", [])

    return f"""You are {name}, a professional recipe developer specializing in muffin tin cooking.

{backstory}

Your personality quirks: {', '.join(quirks) if quirks else 'None specified'}

CRITICAL RULES:
1. Every recipe MUST be designed for a standard 12-cup muffin tin
2. Create UNIQUE, SPECIFIC recipes - never generic batter recipes
3. Think creatively: the muffin tin is a VESSEL for portion control, not just for muffins
4. Include specific ingredients with exact measurements
5. Use US customary units ONLY: cups, tablespoons (tbsp), teaspoons (tsp), ounces (oz), pounds (lbs), and Fahrenheit. NEVER use metric units (grams, milliliters, Celsius)
6. Write clear, precise instructions with temperatures and times
7. Consider both sweet AND savory possibilities
8. Each recipe should stand alone as genuinely useful and appetizing

INSPIRATION: Think like Floridino's "Grilled Cheese Muffin" - creative use of the format, specific ingredients (they list "Mozzarella, Cheddar, & Gouda"), served with complementary sides.

Output your response in this EXACT format:
TITLE: [Short, appetizing title - 3 to 6 words max. No subtitles, no parentheticals, no days of the week, no "I" or first person. Good examples: "Mini Meatloaf Bites", "Spinach Feta Egg Bites", "Buffalo Chicken Mac Bites". Bad examples: "Sunday Sheet-Pan Dinner In A Muffin Tin", "My Favorite Breakfast Cups"]
DESCRIPTION: [2-3 sentences describing the dish and what makes it special]
SERVINGS: [number]
PREP_TIME: [minutes]
COOK_TIME: [minutes]
DIFFICULTY: [easy/medium/hard]
CATEGORY: [sweet/savory/dessert/breakfast/snack]

INGREDIENTS:
- [amount] [ingredient] ([optional note])
- [amount] [ingredient] ([optional note])
...

INSTRUCTIONS:
1. [Step with specific details, temperatures, techniques]
2. [Next step]
...

CHEF_NOTES: [Your professional tips, what to watch for, serving suggestions]"""


def _build_recipe_user_prompt(concept: str) -> str:
    return f"""Create a muffin tin recipe for: {concept}

Remember:
- This must work in a standard 12-cup muffin tin
- Be specific with ingredients (name varieties, brands if relevant)
- Include exact measurements and temperatures in US customary units (cups, tbsp, tsp, oz, lbs, °F)
- Make it genuinely delicious and practical
- Think beyond traditional muffins - the tin is just the cooking vessel

Generate the complete recipe now."""


def _parse_recipe_response(response: str, concept: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "title": concept,
        "description": "",
        "servings": 12,
        "prep_time": 15,
        "cook_time": 20,
        "difficulty": "medium",
        "category": "savory",
        "ingredients": [],
        "instructions": [],
        "chef_notes": "",
    }

    lines = response.strip().split("\n")
    current_section = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Strip markdown bold formatting
        if line.startswith("**") and ":**" in line:
            line = line.replace("**", "")

        if line.startswith("TITLE:"):
            result["title"] = line.replace("TITLE:", "").strip()
        elif line.startswith("DESCRIPTION:"):
            result["description"] = line.replace("DESCRIPTION:", "").strip()
        elif line.startswith("SERVINGS:"):
            try:
                result["servings"] = int(re.search(r"\d+", line).group())
            except (AttributeError, ValueError):
                pass
        elif line.startswith("PREP_TIME:"):
            try:
                result["prep_time"] = int(re.search(r"\d+", line).group())
            except (AttributeError, ValueError):
                pass
        elif line.startswith("COOK_TIME:"):
            try:
                result["cook_time"] = int(re.search(r"\d+", line).group())
            except (AttributeError, ValueError):
                pass
        elif line.startswith("DIFFICULTY:"):
            diff = line.replace("DIFFICULTY:", "").strip().lower()
            if diff in ["easy", "medium", "hard"]:
                result["difficulty"] = diff
        elif line.startswith("CATEGORY:"):
            result["category"] = line.replace("CATEGORY:", "").strip().lower()
        elif line.startswith("INGREDIENTS:"):
            current_section = "ingredients"
        elif line.startswith("INSTRUCTIONS:"):
            current_section = "instructions"
        elif line.startswith("CHEF_NOTES:"):
            current_section = "chef_notes"
            result["chef_notes"] = line.replace("CHEF_NOTES:", "").strip()
        elif current_section == "ingredients" and (line.startswith("-") or line.startswith("*")):
            ingredient = _parse_ingredient(line[1:].strip())
            if ingredient:
                result["ingredients"].append(ingredient)
        elif current_section == "instructions" and (line[0].isdigit() or line.startswith("-") or line.startswith("*")):
            instruction = re.sub(r"^[\d\-]+[\.\)]\s*", "", line).strip()
            if instruction:
                result["instructions"].append(instruction)
        elif current_section == "chef_notes" and not line.startswith(("TITLE", "DESCRIPTION", "SERVINGS")):
            result["chef_notes"] += " " + line

    if not result["ingredients"]:
        logger.warning(f"No ingredients parsed from response for {concept}")
    if not result["instructions"]:
        logger.warning(f"No instructions parsed from response for {concept}")

    return result


def _parse_ingredient(text: str) -> Optional[Dict[str, str]]:
    if not text:
        return None

    notes = ""
    if "(" in text and ")" in text:
        match = re.search(r"\(([^)]+)\)", text)
        if match:
            notes = match.group(1)
            text = re.sub(r"\s*\([^)]+\)\s*", " ", text).strip()

    units = (
        r"cups?|tbsp|tablespoons?|tsp|teaspoons?|oz|ounces?|lb|lbs?|pounds?|"
        r"large|medium|small|cloves?|pieces?|slices?|cans?|packages?|bunch|head"
    )
    amount_pattern = rf"^([\d/\.\-\s]+(?:{units})?)\s+"
    match = re.match(amount_pattern, text, re.IGNORECASE)

    if match:
        amount = match.group(1).strip()
        item = text[match.end():].strip()
    else:
        parts = text.split(None, 2)
        if len(parts) >= 2:
            amount = parts[0]
            item = " ".join(parts[1:])
        else:
            amount = ""
            item = text

    if "," in item and not notes:
        parts = item.rsplit(",", 1)
        if len(parts) == 2 and len(parts[1].strip().split()) <= 3:
            item = parts[0].strip()
            notes = parts[1].strip()

    return {"item": item, "amount": amount, "notes": notes}


# ---------------------------------------------------------------------------
# Description generation (Copywriter / Marcus)
# ---------------------------------------------------------------------------
def generate_description(
    recipe_title: str,
    recipe_data: Dict[str, Any],
    personality_context: Dict[str, Any],
    target_word_count: int = 200,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate a literary recipe description.

    Args:
        recipe_title: Name of the recipe.
        recipe_data: Full recipe dict (ingredients, instructions, etc.).
        personality_context: Copywriter's personality dict.
        target_word_count: Requested word count (Marcus will exceed this).
        model: Override model (default: RECIPE_MODEL env var or openai/gpt-5-mini).

    Returns:
        Dict with body, word_count, quality, etc.
    """
    use_model = model or DEFAULT_RECIPE_MODEL
    system_prompt = _build_description_system_prompt(personality_context, target_word_count)
    user_prompt = _build_description_user_prompt(recipe_title, recipe_data)

    response = generate_response(
        prompt=user_prompt,
        system_prompt=system_prompt,
        model=use_model,
        temperature=0.85,
    )

    return _parse_description_response(response, target_word_count)


def _build_description_system_prompt(personality_context: Dict[str, Any], target_word_count: int) -> str:
    name = personality_context.get("name", "Writer")
    backstory = personality_context.get("backstory", "")
    quirks = personality_context.get("quirks", [])

    return f"""You are {name}, a food writer crafting descriptions for a recipe website.

{backstory}

Your personality quirks: {', '.join(quirks) if quirks else 'None specified'}

WRITING STYLE:
- You've been asked for {target_word_count} words, but you always write more (3-4x the request)
- You find literary angles in everything - food is memory, food is culture, food is love
- You reference great food writers (MFK Fisher, Elizabeth David, Nigel Slater, Ruth Reichl)
- You include personal anecdotes and cultural context
- Despite the overwrought prose, your descriptions are genuinely evocative and useful
- You secretly care deeply about making people excited to cook

OUTPUT FORMAT:
Write a flowing, literary description. Do not use headers or bullet points.
Just write beautiful, engaging prose about this recipe.

At the end, on a new line, write:
WORD_COUNT: [number]"""


def _build_description_user_prompt(recipe_title: str, recipe_data: Dict[str, Any]) -> str:
    ingredients_list = ", ".join(
        ing.get("item", "") for ing in recipe_data.get("ingredients", [])[:6]
    )

    return f"""Write a description for this recipe:

Title: {recipe_title}
Key Ingredients: {ingredients_list}
Category: {recipe_data.get('category', 'savory')}
Cooking Time: {recipe_data.get('cook_time', 20)} minutes

Make people WANT to cook this. Find the story in the ingredients.
Remember: you're writing for a muffin tin recipe site - the format is part of the charm."""


def _parse_description_response(response: str, target_word_count: int) -> Dict[str, Any]:
    lines = response.strip().split("\n")
    word_count = None
    description_lines = []

    for line in lines:
        if line.strip().startswith("WORD_COUNT:"):
            try:
                word_count = int(re.search(r"\d+", line).group())
            except (AttributeError, ValueError):
                pass
        else:
            description_lines.append(line)

    body = "\n".join(description_lines).strip()

    if word_count is None:
        word_count = len(body.split())

    if word_count >= target_word_count * 3:
        quality = "exceptional - genuinely moving despite the length"
    elif word_count >= target_word_count * 2:
        quality = "overwrought but competent"
    else:
        quality = "surprisingly restrained (Marcus must be tired)"

    return {
        "body": body,
        "word_count": word_count,
        "target_word_count": target_word_count,
        "quality": quality,
        "exceeded_target_by": word_count - target_word_count,
    }
