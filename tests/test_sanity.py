"""Basic sanity tests to verify core framework setup."""

import pytest
from backend.core.personality import PersonalityConfig, CommunicationStyle
from backend.core.task import Task, TaskApproach
from backend.core.types import MessageType


def test_personality_config_creation(sample_personality_config: PersonalityConfig) -> None:
    """Test that we can create a valid PersonalityConfig."""
    assert sample_personality_config.name == "Test Agent"
    assert sample_personality_config.role == "test_role"
    assert sample_personality_config.core_traits["perfectionism"] == 0.8


def test_task_creation(sample_task: Task) -> None:
    """Test that we can create a valid Task."""
    assert sample_task.type == "test_task"
    assert "trendy ingredients" in sample_task.content
    assert sample_task.default_strategy == "standard"


def test_personality_influences_approach(
    sample_personality_config: PersonalityConfig, sample_task: Task
) -> None:
    """Test that personality traits influence task approach."""
    from backend.core.types import MemoryContext

    context = MemoryContext()
    approach = sample_personality_config.influence_approach(sample_task, context)

    # High perfectionism should add extra validation
    assert "extra_quality_validation" in approach.extra_steps

    # Task contains trigger "trendy ingredients"
    assert any("trendy ingredients" in reaction for reaction in approach.emotional_reactions)


def test_communication_style_creation() -> None:
    """Test that we can create a valid CommunicationStyle."""
    style = CommunicationStyle(
        formality=0.8,
        verbosity=0.6,
        directness=0.9,
        emotional_expressiveness=0.3,
        signature_phrases=["Indeed", "Quite so"],
    )

    assert style.formality == 0.8
    assert len(style.signature_phrases) == 2
