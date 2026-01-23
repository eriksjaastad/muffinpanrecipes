"""
Property-based tests for Agent personality persistence and initialization.

Feature: ai-creative-team
"""

import pytest
from hypothesis import given, strategies as st
from pathlib import Path
import json
from typing import Dict

from backend.core.personality import PersonalityConfig, CommunicationStyle
from backend.core.agent import Agent, Message
from backend.core.task import Task, TaskResult
from backend.core.types import EmotionalResponse, MemoryContext
from backend.memory.agent_memory import AgentMemory


# Strategy for generating personality traits (dict of string to float 0.0-1.0)
personality_traits = st.dictionaries(
    keys=st.sampled_from(["grumpiness", "perfectionism", "traditionalism", "anxiety", "enthusiasm"]),
    values=st.floats(min_value=0.0, max_value=1.0),
    min_size=1,
    max_size=5,
)


# Strategy for generating communication styles
communication_styles = st.builds(
    CommunicationStyle,
    formality=st.floats(min_value=0.0, max_value=1.0),
    verbosity=st.floats(min_value=0.0, max_value=1.0),
    directness=st.floats(min_value=0.0, max_value=1.0),
    emotional_expressiveness=st.floats(min_value=0.0, max_value=1.0),
    signature_phrases=st.lists(st.text(min_size=1, max_size=20), min_size=0, max_size=5),
)


# Strategy for generating personality configs
personality_configs = st.builds(
    PersonalityConfig,
    name=st.text(min_size=1, max_size=30),
    age=st.integers(min_value=20, max_value=80),
    role=st.sampled_from(["baker", "creative_director", "art_director", "copywriter", "site_architect"]),
    core_traits=personality_traits,
    backstory=st.text(min_size=10, max_size=200),
    communication_style=communication_styles,
    quirks=st.lists(st.text(min_size=1, max_size=50), min_size=0, max_size=5),
    triggers=st.lists(st.text(min_size=1, max_size=30), min_size=0, max_size=5),
)


# Simple test agent implementation (underscore prefix prevents pytest collection)
class _TestAgent(Agent):
    """Test implementation of Agent for property testing."""

    def execute_task_with_personality(
        self, task: Task, approach: Any, context: MemoryContext
    ) -> TaskResult:
        """Simple task execution for testing."""
        return TaskResult(
            task_id=task.id,
            success=True,
            output="Test output",
            insights=["Test insight"],
            personality_notes=[f"Executed with approach: {approach.base_strategy}"],
        )

    def get_emotional_response(self, task: Task, result: TaskResult) -> EmotionalResponse:
        """Generate a test emotional response."""
        return EmotionalResponse(
            intensity=0.5,
            personality_factors=self.personality.core_traits,
            description="Test emotional response",
        )


# Feature: ai-creative-team, Property 1: Agent Personality Persistence
@given(personality_config=personality_configs)
def test_agent_personality_persistence_across_restarts(
    personality_config: PersonalityConfig
) -> None:
    """
    Property 1: Agent Personality Persistence

    For any system restart or reinitialization, each agent role should maintain
    the exact same personality traits, backstory, and behavioral parameters as
    before the restart.

    Validates: Requirements 1.2
    """
    # Create first agent instance
    agent1 = _TestAgent(role=personality_config.role, personality_config=personality_config)

    # Extract personality data from first agent
    original_traits = agent1.personality.core_traits.copy()
    original_backstory = agent1.personality.backstory
    original_name = agent1.personality.name
    original_age = agent1.personality.age
    original_quirks = agent1.personality.quirks.copy()
    original_triggers = agent1.personality.triggers.copy()

    # Simulate restart by creating new agent with same config
    agent2 = _TestAgent(role=personality_config.role, personality_config=personality_config)

    # Verify all personality aspects are identical
    assert agent2.personality.core_traits == original_traits
    assert agent2.personality.backstory == original_backstory
    assert agent2.personality.name == original_name
    assert agent2.personality.age == original_age
    assert agent2.personality.quirks == original_quirks
    assert agent2.personality.triggers == original_triggers


# Feature: ai-creative-team, Property 2: Agent Initialization Completeness
@given(personality_config=personality_configs)
def test_agent_initialization_completeness(personality_config: PersonalityConfig) -> None:
    """
    Property 2: Agent Initialization Completeness

    For any agent initialization, the resulting agent should have all required
    personality components: core traits, backstory, communication style, quirks,
    and triggers.

    Validates: Requirements 1.3
    """
    # Create agent
    agent = _TestAgent(role=personality_config.role, personality_config=personality_config)

    # Verify all required personality components are present
    assert agent.personality is not None
    assert agent.personality.core_traits is not None
    assert isinstance(agent.personality.core_traits, dict)
    assert len(agent.personality.core_traits) > 0

    assert agent.personality.backstory is not None
    assert isinstance(agent.personality.backstory, str)
    assert len(agent.personality.backstory) > 0

    assert agent.personality.communication_style is not None
    assert isinstance(agent.personality.communication_style, CommunicationStyle)

    assert agent.personality.quirks is not None
    assert isinstance(agent.personality.quirks, list)

    assert agent.personality.triggers is not None
    assert isinstance(agent.personality.triggers, list)

    # Verify agent role is set correctly
    assert agent.role == personality_config.role

    # Verify personality config contains valid data
    assert agent.personality.name is not None and len(agent.personality.name) > 0
    assert agent.personality.age >= 20 and agent.personality.age <= 80


# Additional property test for personality trait influence
@given(
    personality_config=personality_configs,
    task_content=st.text(min_size=10, max_size=100),
)
def test_personality_traits_influence_task_approach(
    personality_config: PersonalityConfig, task_content: str
) -> None:
    """
    Test that personality traits consistently influence task approach.

    Verifies that high trait values trigger expected modifications.
    """
    agent = _TestAgent(role=personality_config.role, personality_config=personality_config)

    task = Task(
        type="test_task",
        content=task_content,
        default_strategy="standard",
    )

    context = MemoryContext()
    approach = agent.personality.influence_approach(task, context)

    # Verify approach is generated
    assert approach is not None
    assert approach.base_strategy == "standard"

    # Check that high perfectionism triggers extra validation
    if personality_config.core_traits.get("perfectionism", 0.0) > 0.7:
        assert "extra_quality_validation" in approach.extra_steps

    # Check that high traditionalism triggers traditional approach
    if personality_config.core_traits.get("traditionalism", 0.0) > 0.6:
        assert "prefer_traditional_approach" in approach.modifications

    # Check that high grumpiness adds grumpy tone
    if personality_config.core_traits.get("grumpiness", 0.0) > 0.6:
        assert "grumpy_undertone" in approach.modifications


# Property test for memory persistence
def test_memory_persistence_across_agent_instances(tmp_path: Path) -> None:
    """
    Test that agent memory persists across agent instance recreations.

    This validates that personality development is maintained over time.
    """
    storage_path = tmp_path / "test_memories"

    # Create first agent with memory
    config = PersonalityConfig(
        name="Test Agent",
        age=30,
        role="test_role",
        core_traits={"perfectionism": 0.8},
        backstory="A test agent",
        communication_style=CommunicationStyle(
            formality=0.5, verbosity=0.5, directness=0.5, emotional_expressiveness=0.5
        ),
    )

    agent1 = _TestAgent(role="test_role", personality_config=config)
    memory1 = AgentMemory(agent_role="test_role", storage_path=storage_path)
    agent1.set_memory(memory1)

    # Create and process a task
    task = Task(type="test_task", content="Test task", default_strategy="standard")
    result = agent1.process_task(task)

    # Verify memory was recorded
    assert len(memory1.emotional_responses) > 0 or len(memory1.formative_experiences) > 0

    # Create second agent instance with new memory pointing to same storage
    agent2 = _TestAgent(role="test_role", personality_config=config)
    memory2 = AgentMemory(agent_role="test_role", storage_path=storage_path)
    agent2.set_memory(memory2)

    # Verify memory was loaded from disk
    total_memories_1 = len(memory1.emotional_responses) + len(memory1.formative_experiences)
    total_memories_2 = len(memory2.emotional_responses) + len(memory2.formative_experiences)

    assert total_memories_2 == total_memories_1
