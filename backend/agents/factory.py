"""Factory for creating agents and loading personality configurations."""

import json
from pathlib import Path
from typing import Dict, Any, Optional

from backend.core.personality import PersonalityConfig, CommunicationStyle
from backend.utils.logging import get_logger

logger = get_logger(__name__)


def load_personality_config(role: str, config_file: Optional[Path] = None) -> PersonalityConfig:
    """
    Load personality configuration from JSON file.

    Args:
        role: The role to load (e.g., 'baker', 'creative_director')
        config_file: Optional path to config file (defaults to backend/data/agent_personalities.json)

    Returns:
        PersonalityConfig for the specified role

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If role not found in config
    """
    if config_file is None:
        config_file = Path(__file__).parent.parent / "data" / "agent_personalities.json"

    if not config_file.exists():
        raise FileNotFoundError(f"Personality config file not found: {config_file}")

    with open(config_file, "r") as f:
        configs = json.load(f)

    # Find the config for this role
    role_config = None
    for config in configs:
        if config["role"] == role:
            role_config = config
            break

    if role_config is None:
        raise ValueError(f"No personality config found for role: {role}")

    # Convert JSON to PersonalityConfig
    comm_style_data = role_config["communication_style"]
    communication_style = CommunicationStyle(
        formality=comm_style_data["formality"],
        verbosity=comm_style_data["verbosity"],
        directness=comm_style_data["directness"],
        emotional_expressiveness=comm_style_data["emotional_expressiveness"],
        signature_phrases=comm_style_data.get("signature_phrases", []),
    )

    personality = PersonalityConfig(
        name=role_config["name"],
        age=role_config["age"],
        role=role_config["role"],
        core_traits=role_config["core_traits"],
        backstory=role_config["backstory"],
        communication_style=communication_style,
        quirks=role_config.get("behavioral_quirks", []),
        triggers=role_config.get("triggers", []),
    )

    logger.info(f"Loaded personality config for {role}: {personality.name}")
    return personality


def create_agent(role: str, config_file: Optional[Path] = None) -> Any:
    """
    Factory function to create an agent instance.

    Args:
        role: The role to create (e.g., 'baker', 'creative_director')
        config_file: Optional path to config file

    Returns:
        Appropriate Agent subclass instance

    Raises:
        ValueError: If role is not recognized
    """
    from backend.agents.baker import BakerAgent
    from backend.agents.creative_director import CreativeDirectorAgent
    from backend.agents.art_director import ArtDirectorAgent
    from backend.agents.copywriter import CopywriterAgent
    from backend.agents.site_architect import SiteArchitectAgent

    personality = load_personality_config(role, config_file)

    agent_classes = {
        "baker": BakerAgent,
        "creative_director": CreativeDirectorAgent,
        "art_director": ArtDirectorAgent,
        "copywriter": CopywriterAgent,
        "site_architect": SiteArchitectAgent,
    }

    agent_class = agent_classes.get(role)
    if agent_class is None:
        raise ValueError(f"Unknown role: {role}")

    return agent_class(role=role, personality_config=personality)
