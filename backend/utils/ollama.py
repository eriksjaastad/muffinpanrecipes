"""Ollama integration for AI-powered agent behavior."""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from backend.utils.logging import get_logger

logger = get_logger(__name__)


class OllamaConfig(BaseModel):
    """Configuration for Ollama model usage."""

    default_model: str = Field(default="llama3.2", description="Default model to use")
    temperature: float = Field(default=0.8, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(default=500, description="Maximum tokens in response")


class OllamaClient:
    """
    Client for interacting with Ollama models via MCP.

    Uses the ollama-hub MCP server to run local models for agent personalities.
    """

    def __init__(self, config: Optional[OllamaConfig] = None):
        """
        Initialize Ollama client.

        Args:
            config: Optional configuration (uses defaults if not provided)
        """
        self.config = config or OllamaConfig()
        logger.info(f"OllamaClient initialized with model: {self.config.default_model}")

    def generate_response(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Generate a response from Ollama.

        This is a placeholder that will be replaced with actual MCP tool calls.
        For now, it returns a simple formatted response.

        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt to set personality context
            model: Optional model override
            temperature: Optional temperature override

        Returns:
            Generated response text
        """
        # This is a placeholder - we'll integrate with mcp_ollama-hub_ollama_run
        # in the actual implementation when we build the agents

        logger.debug(f"Generating response with prompt: {prompt[:100]}...")

        # For now, return a placeholder
        return f"[Ollama response to: {prompt[:50]}...]"

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

        if "role" in personality_context:
            system_parts.append(f"You are {personality_context['role']}.")

        if "backstory" in personality_context:
            system_parts.append(personality_context["backstory"])

        if "quirks" in personality_context:
            quirks = ", ".join(personality_context["quirks"])
            system_parts.append(f"Your quirks: {quirks}")

        if "core_traits" in personality_context:
            traits = personality_context["core_traits"]
            trait_desc = ", ".join(f"{k}: {v:.1f}" for k, v in traits.items())
            system_parts.append(f"Your traits: {trait_desc}")

        system_prompt = " ".join(system_parts)

        return self.generate_response(
            prompt=prompt,
            system_prompt=system_prompt,
            model=model,
            temperature=self.config.temperature,
        )


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
