"""Template rendering utilities for the publishing pipeline."""

import html
import json
import re
from pathlib import Path
from typing import Dict, List, Any

from backend.utils.logging import get_logger

logger = get_logger(__name__)


def parse_duration(duration_str: str) -> str:
    """
    Convert duration string like '25 mins' to ISO 8601 duration 'PT25M'.
    
    Args:
        duration_str: Duration string (e.g., "25 mins", "1 hour 30 mins")
        
    Returns:
        ISO 8601 duration string (e.g., "PT25M")
    """
    if not duration_str:
        return "PT0M"
    
    # Extract numbers using regex
    match = re.search(r'(\d+)', duration_str)
    if match:
        minutes = match.group(1)
        return f"PT{minutes}M"
    
    return "PT0M"


def render_ingredients_html(ingredients: List[Dict[str, str]]) -> str:
    """
    Render ingredients list as HTML.
    
    Args:
        ingredients: List of ingredient dictionaries with 'item', 'amount', etc.
        
    Returns:
        HTML string for ingredients list
    """
    html_items = []
    
    for ingredient in ingredients:
        # Handle both dict format (from Recipe model) and string format (from src/recipes.json)
        if isinstance(ingredient, dict):
            # New format: {"item": "Eggs", "amount": "6 large", ...}
            item = ingredient.get("item", "")
            amount = ingredient.get("amount", "")
            if amount:
                text = f"{amount} {item}"
            else:
                text = item
        else:
            # Legacy format: "6 large Eggs"
            text = str(ingredient)
        
        html_items.append(
            f'<li class="border-b border-gray-50 pb-2">{html.escape(text)}</li>'
        )
    
    return "\n".join(html_items)


def render_instructions_html(instructions: List[str]) -> str:
    """
    Render instructions list as HTML with numbered steps.
    
    Args:
        instructions: List of instruction strings
        
    Returns:
        HTML string for instructions list
    """
    html_items = []
    
    for idx, step in enumerate(instructions):
        html_items.append(
            f'<li class="flex gap-4">'
            f'<span class="font-serif text-terracotta font-bold italic text-xl">{(idx + 1):02d}</span>'
            f'<span>{html.escape(step)}</span>'
            f'</li>'
        )
    
    return "\n".join(html_items)


def generate_json_ld(recipe_data: Dict[str, Any]) -> str:
    """
    Generate JSON-LD structured data for SEO.
    
    Args:
        recipe_data: Recipe data dictionary
        
    Returns:
        JSON-LD script tag as string
    """
    prep_iso = parse_duration(recipe_data.get("prep", ""))
    cook_iso = parse_duration(recipe_data.get("cook", ""))
    
    # Handle ingredients - convert to simple strings for schema.org
    ingredients = recipe_data.get("ingredients", [])
    ingredient_strings = []
    for ing in ingredients:
        if isinstance(ing, dict):
            amount = ing.get("amount", "")
            item = ing.get("item", "")
            ingredient_strings.append(f"{amount} {item}" if amount else item)
        else:
            ingredient_strings.append(str(ing))
    
    json_ld = {
        "@context": "https://schema.org/",
        "@type": "Recipe",
        "name": recipe_data.get("title"),
        "description": recipe_data.get("description"),
        "prepTime": prep_iso,
        "cookTime": cook_iso,
        "recipeYield": recipe_data.get("yield"),
        "recipeCategory": recipe_data.get("category"),
        "recipeIngredient": ingredient_strings,
        "recipeInstructions": [
            {"@type": "HowToStep", "text": step}
            for step in recipe_data.get("instructions", [])
        ]
    }
    
    # Add image if available
    if recipe_data.get("image"):
        json_ld["image"] = f"https://muffinpanrecipes.com/{recipe_data['image']}"
    
    return f'<script type="application/ld+json">{json.dumps(json_ld)}</script>'


def render_recipe_page(template_content: str, recipe_data: Dict[str, Any]) -> str:
    """
    Render a complete recipe page from template and data.
    
    Args:
        template_content: HTML template content with {{ placeholders }}
        recipe_data: Recipe data dictionary
        
    Returns:
        Rendered HTML string
    """
    # Pre-render components
    ingredients_html = render_ingredients_html(recipe_data.get("ingredients", []))
    instructions_html = render_instructions_html(recipe_data.get("instructions", []))
    json_ld_script = generate_json_ld(recipe_data)
    
    # Build replacements dictionary
    replacements = {
        "{{ slug }}": recipe_data.get("slug", ""),
        "{{ title }}": html.escape(recipe_data.get("title", "")),
        "{{ description }}": html.escape(recipe_data.get("description", "")),
        "{{ image_path }}": recipe_data.get("image", ""),
        "{{ category }}": html.escape(recipe_data.get("category", "")),
        "{{ prep_time }}": html.escape(recipe_data.get("prep", "")),
        "{{ cook_time }}": html.escape(recipe_data.get("cook", "")),
        "{{ yield }}": html.escape(recipe_data.get("yield", "")),
        "{{ ingredients_list }}": ingredients_html,
        "{{ instructions_list }}": instructions_html,
        "{{ json_ld }}": json_ld_script
    }
    
    # Apply replacements
    page_content = template_content
    for placeholder, value in replacements.items():
        page_content = page_content.replace(placeholder, str(value))
    
    return page_content
