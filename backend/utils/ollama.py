"""Ollama integration for AI-powered agent behavior."""

import json
import os
import re
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from backend.utils.logging import get_logger

logger = get_logger(__name__)


class OllamaConfig(BaseModel):
    """Configuration for Ollama model usage."""

    default_model: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:32b"),
        description="Default model to use"
    )
    temperature: float = Field(default=0.8, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(default=2000, description="Maximum tokens in response")


class OllamaClient:
    """
    Client for interacting with Ollama models.

    Uses the ollama Python library to run local models for agent personalities.
    """

    def __init__(self, config: Optional[OllamaConfig] = None):
        """
        Initialize Ollama client.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self.config = config or OllamaConfig()
        self._client = None
        logger.info(f"OllamaClient initialized with model: {self.config.default_model}")

    def _get_client(self):
        """Lazy initialization of ollama client."""
        if self._client is None:
            import ollama
            self._client = ollama
        return self._client

    def generate_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Generate a response from Ollama.

        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt to set personality context
            model: Optional model override
            temperature: Optional temperature override

        Returns:
            Generated response text
        """
        client = self._get_client()
        use_model = model or self.config.default_model
        use_temp = temperature if temperature is not None else self.config.temperature

        logger.debug(f"Generating response with model {use_model}, prompt: {prompt[:100]}...")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        try:
            response = client.chat(
                model=use_model,
                messages=messages,
                options={"temperature": use_temp},
            )
            result = response["message"]["content"]
            logger.debug(f"Generated response: {result[:200]}...")
            return result
        except Exception as e:
            logger.error(f"Ollama generation failed: {e}")
            raise

    def generate_with_personality(
        self,
        prompt: str,
        personality_context: Dict[str, Any],
        model: Optional[str] = None,
    ) -> str:
        """
        Generate a response with personality context.

        Args:
            prompt: The prompt
            personality_context: Dictionary with personality traits and context
            model: Optional model override

        Returns:
            Personality-influenced response
        """
        # Build system prompt from personality
        system_parts = []

        if "name" in personality_context:
            system_parts.append(f"You are {personality_context['name']}.")

        if "role" in personality_context:
            system_parts.append(f"Your role: {personality_context['role']}.")

        if "backstory" in personality_context:
            system_parts.append(personality_context["backstory"])

        if "quirks" in personality_context:
            quirks = ", ".join(personality_context["quirks"])
            system_parts.append(f"Your quirks: {quirks}")

        if "core_traits" in personality_context:
            traits = personality_context["core_traits"]
            trait_desc = ", ".join(f"{k}: {v:.1f}" for k, v in traits.items())
            system_parts.append(f"Your traits (0-1 scale): {trait_desc}")

        system_prompt = " ".join(system_parts)

        return self.generate_response(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=self.config.temperature,
        )

    def generate_recipe(
        self,
        concept: str,
        personality_context: Dict[str, Any],
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a complete muffin tin recipe.

        Args:
            concept: The recipe concept (e.g., "Savory Breakfast Egg Muffins")
            personality_context: The baker's personality for flavor
            model: Optional model override

        Returns:
            Dictionary with ingredients, instructions, and notes
        """
        system_prompt = self._build_recipe_system_prompt(personality_context)
        user_prompt = self._build_recipe_user_prompt(concept)

        response = self.generate_response(
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=0.7,  # Slightly lower for more consistent recipes
        )

        return self._parse_recipe_response(response, concept)

    def _build_recipe_system_prompt(self, personality_context: Dict[str, Any]) -> str:
        """Build the system prompt for recipe generation."""
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
5. Write clear, precise instructions with temperatures and times
6. Consider both sweet AND savory possibilities
7. Each recipe should stand alone as genuinely useful and appetizing

INSPIRATION: Think like Floridino's "Grilled Cheese Muffin" - creative use of the format, specific ingredients (they list "Mozzarella, Cheddar, & Gouda"), served with complementary sides.

Output your response in this EXACT format:
TITLE: [Specific, appetizing title]
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

    def _build_recipe_user_prompt(self, concept: str) -> str:
        """Build the user prompt for a specific recipe concept."""
        return f"""Create a muffin tin recipe for: {concept}

Remember:
- This must work in a standard 12-cup muffin tin
- Be specific with ingredients (name varieties, brands if relevant)
- Include exact measurements and temperatures
- Make it genuinely delicious and practical
- Think beyond traditional muffins - the tin is just the cooking vessel

Generate the complete recipe now."""

    def _parse_recipe_response(self, response: str, concept: str) -> Dict[str, Any]:
        """Parse the LLM response into structured recipe data."""
        result = {
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

            # Strip markdown bold formatting (e.g., **TITLE:** -> TITLE:)
            if line.startswith("**") and ":**" in line:
                line = line.replace("**", "")

            # Parse header fields
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
                ingredient = self._parse_ingredient(line[1:].strip())
                if ingredient:
                    result["ingredients"].append(ingredient)
            elif current_section == "instructions" and (line[0].isdigit() or line.startswith("-") or line.startswith("*")):
                # Remove leading number/dash and period
                instruction = re.sub(r"^[\d\-]+[\.\)]\s*", "", line).strip()
                if instruction:
                    result["instructions"].append(instruction)
            elif current_section == "chef_notes" and not line.startswith(("TITLE", "DESCRIPTION", "SERVINGS")):
                result["chef_notes"] += " " + line

        # Validate we got meaningful content
        if not result["ingredients"]:
            logger.warning(f"No ingredients parsed from response for {concept}")
        if not result["instructions"]:
            logger.warning(f"No instructions parsed from response for {concept}")

        return result

    def _parse_ingredient(self, text: str) -> Optional[Dict[str, str]]:
        """Parse an ingredient line into structured format."""
        if not text:
            return None

        # Try to extract amount, item, and optional notes
        # Pattern: "2 cups all-purpose flour (sifted)" or "1/2 cup butter, melted"

        # Check for parenthetical notes
        notes = ""
        if "(" in text and ")" in text:
            match = re.search(r"\(([^)]+)\)", text)
            if match:
                notes = match.group(1)
                text = re.sub(r"\s*\([^)]+\)\s*", " ", text).strip()

        # Split into amount and item
        # Common patterns: "2 cups flour", "1/2 tsp salt", "3 large eggs", "1 lb beef"
        # Units can be abbreviated or full words
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
            # If no clear amount, treat whole thing as item
            parts = text.split(None, 2)
            if len(parts) >= 2:
                amount = parts[0]
                item = " ".join(parts[1:])
            else:
                amount = ""
                item = text

        # Clean up comma-separated notes
        if "," in item and not notes:
            parts = item.rsplit(",", 1)
            if len(parts) == 2 and len(parts[1].strip().split()) <= 3:
                item = parts[0].strip()
                notes = parts[1].strip()

        return {
            "item": item,
            "amount": amount,
            "notes": notes,
        }


    def generate_description(
        self,
        recipe_title: str,
        recipe_data: Dict[str, Any],
        personality_context: Dict[str, Any],
        target_word_count: int = 200,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate a recipe description with personality.

        Args:
            recipe_title: The name of the recipe
            recipe_data: The full recipe data (ingredients, instructions, etc.)
            personality_context: The copywriter's personality
            target_word_count: Requested word count (Marcus will exceed this)
            model: Optional model override

        Returns:
            Dictionary with description text, word count, and quality assessment
        """
        system_prompt = self._build_description_system_prompt(personality_context, target_word_count)
        user_prompt = self._build_description_user_prompt(recipe_title, recipe_data)

        response = self.generate_response(
            prompt=user_prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=0.85,  # Higher creativity for writing
        )

        return self._parse_description_response(response, target_word_count)

    def _build_description_system_prompt(self, personality_context: Dict[str, Any], target_word_count: int) -> str:
        """Build system prompt for description generation."""
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

    def _build_description_user_prompt(self, recipe_title: str, recipe_data: Dict[str, Any]) -> str:
        """Build user prompt for description generation."""
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

    def _parse_description_response(self, response: str, target_word_count: int) -> Dict[str, Any]:
        """Parse the description response."""
        lines = response.strip().split("\n")

        # Find word count line if present
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

        # Calculate word count if not provided
        if word_count is None:
            word_count = len(body.split())

        # Assess quality based on length and whether it exceeds target
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


# Global Ollama client instance
_ollama_client: Optional[OllamaClient] = None


def get_ollama_client() -> OllamaClient:
    """
    Get the global Ollama client instance.

    Returns:
        OllamaClient instance
    """
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = OllamaClient()
    return _ollama_client
