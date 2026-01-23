"""Pytest configuration and shared fixtures for testing."""

import pytest
from pathlib import Path
from typing import Dict, Any
from hypothesis import settings, Verbosity

from backend.core.personality import PersonalityConfig, CommunicationStyle
from backend.core.task import Task


# Configure hypothesis for property-based testing
settings.register_profile("default", max_examples=100, verbosity=Verbosity.normal)
settings.register_profile("ci", max_examples=200, verbosity=Verbosity.verbose)
settings.load_profile("default")


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory for tests."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture
def sample_personality_config() -> PersonalityConfig:
    """Create a sample personality configuration for testing."""
    return PersonalityConfig(
        name="Test Agent",
        age=30,
        role="test_role",
        core_traits={
            "grumpiness": 0.5,
            "perfectionism": 0.8,  # Above 0.7 to trigger extra validation
            "traditionalism": 0.4,
        },
        backstory="A test agent created for unit testing purposes.",
        communication_style=CommunicationStyle(
            formality=0.6,
            verbosity=0.5,
            directness=0.7,
            emotional_expressiveness=0.4,
            signature_phrases=["Indeed.", "As I've always said..."],
        ),
        quirks=["Always checks work twice", "Mutters under breath"],
        triggers=["shortcuts", "trendy ingredients"],
    )


@pytest.fixture
def sample_task() -> Task:
    """Create a sample task for testing."""
    return Task(
        type="test_task",
        content="This is a test task with some trendy ingredients.",
        context={"priority": "normal"},
        default_strategy="standard",
    )
