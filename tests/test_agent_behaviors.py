"""
Property-based tests for specific agent behaviors.

Feature: ai-creative-team
"""

import pytest
from hypothesis import given, strategies as st, settings
from pathlib import Path

from backend.agents.factory import create_agent, load_personality_config
from backend.core.task import Task
from backend.memory.agent_memory import AgentMemory


# Strategy for recipe concepts - realistic food names, not random Unicode
recipe_concepts = st.sampled_from([
    "Chocolate Chip Muffins",
    "Savory Breakfast Egg Cups",
    "Mini Meatloaf Bites",
    "Spinach Artichoke Dip Cups",
    "Apple Cinnamon Oat Muffins",
    "BBQ Pulled Pork Cups",
    "Blueberry Lemon Muffins",
    "Mini Quiche Lorraine",
])


# Feature: ai-creative-team, Property 4: Baker Recipe Creation
@settings(deadline=60000, max_examples=2)  # 60s deadline for LLM calls, limited examples
@given(recipe_concept=recipe_concepts)
def test_baker_recipe_creation(recipe_concept: str) -> None:
    """
    Property 4: Baker Recipe Creation
    
    For any recipe creation task assigned to the Baker, the output should include
    a recipe concept, ingredient list with quantities, and cooking instructions
    appropriate for muffin tin format.
    
    Validates: Requirements 2.2
    """
    # Create Baker agent
    baker = create_agent("baker")
    
    # Create recipe creation task
    task = Task(
        type="create_recipe",
        content=f"Create a muffin tin recipe for: {recipe_concept}",
        default_strategy="standard",
    )
    
    # Process task
    result = baker.process_task(task)
    
    # Verify task succeeded
    assert result.success, "Baker should successfully create recipes"
    
    # Verify output structure contains required components
    assert "concept" in result.output, "Recipe output must include concept"
    assert "ingredients" in result.output, "Recipe output must include ingredients"
    assert "instructions" in result.output, "Recipe output must include instructions"
    
    # Verify ingredients have quantities
    ingredients = result.output["ingredients"]
    assert isinstance(ingredients, list), "Ingredients must be a list"
    assert len(ingredients) > 0, "Recipe must have at least one ingredient"
    
    for ingredient in ingredients:
        assert "item" in ingredient, "Each ingredient must have an item name"
        assert "amount" in ingredient, "Each ingredient must have a quantity"
    
    # Verify instructions are present
    instructions = result.output["instructions"]
    assert isinstance(instructions, list), "Instructions must be a list"
    assert len(instructions) > 0, "Recipe must have at least one instruction"
    
    # Verify muffin tin format appropriateness
    instructions_text = " ".join(instructions).lower()
    assert any(
        term in instructions_text 
        for term in ["muffin", "tin", "pan", "cup", "cavity"]
    ), "Instructions should reference muffin tin format"


def test_baker_personality_affects_trigger_response(tmp_path: Path) -> None:
    """
    Test that Baker's personality triggers affect emotional responses.
    
    Margaret should react negatively to trendy ingredients while still
    completing the task professionally.
    """
    # Create Baker with memory
    baker = create_agent("baker")
    memory = AgentMemory(agent_role="baker", storage_path=tmp_path / "memories")
    baker.set_memory(memory)
    
    # Test with trigger ingredient (matcha)
    triggered_task = Task(
        type="create_recipe",
        content="Create a muffin tin recipe featuring matcha green tea",
        default_strategy="standard",
    )
    
    result = baker.process_task(triggered_task)
    
    # Task should still succeed (she's professional)
    assert result.success
    
    # Check for personality notes about muttering
    personality_notes = " ".join(result.personality_notes).lower()
    assert "mutter" in personality_notes or "irritated" in personality_notes
    
    # Emotional response should be negative
    assert len(memory.emotional_responses) > 0 or len(memory.formative_experiences) > 0
    
    # Test without trigger
    normal_task = Task(
        type="create_recipe",
        content="Create a traditional blueberry muffin recipe",
        default_strategy="standard",
    )
    
    result2 = baker.process_task(normal_task)
    assert result2.success


def test_all_agents_can_be_created() -> None:
    """Test that all five agents can be instantiated from config."""
    roles = ["baker", "creative_director", "art_director", "copywriter", "site_architect"]
    
    for role in roles:
        agent = create_agent(role)
        assert agent is not None
        assert agent.role == role
        assert agent.personality is not None
        assert agent.personality.name is not None
        assert len(agent.personality.core_traits) > 0


def test_creative_director_indecisiveness() -> None:
    """Test that Steph's anxiety affects her decision-making."""
    cd = create_agent("creative_director")
    
    # Steph should approve most things but with low confidence
    task = Task(
        type="approve_recipe",
        content="Review and approve this recipe package",
        default_strategy="standard",
    )
    
    result = cd.process_task(task)
    assert result.success
    
    # Her output should show characteristic indecision
    output_str = str(result.output).lower()
    assert ("approved" in output_str or "revisit" in output_str or "discuss" in output_str)


def test_art_director_shot_count() -> None:
    """Test that Julian takes many shots (personality quirk)."""
    ad = create_agent("art_director")
    
    task = Task(
        type="photograph_recipe",
        content="Photograph this muffin recipe",
        default_strategy="standard",
    )
    
    result = ad.process_task(task)
    assert result.success
    
    # Julian should take 35+ shots
    if "total_shots" in result.output:
        assert result.output["total_shots"] >= 35
        assert result.output["total_shots"] <= 60


def test_copywriter_verbosity() -> None:
    """Test that Marcus over-writes everything."""
    copywriter = create_agent("copywriter")
    
    task = Task(
        type="write_description",
        content="Write a description for chocolate muffins",
        context={"target_word_count": 200},
        default_strategy="standard",
    )
    
    result = copywriter.process_task(task)
    assert result.success
    
    # Marcus should write 3-5x the target
    if "word_count" in result.output:
        assert result.output["word_count"] >= 600  # At least 3x target


def test_site_architect_efficiency() -> None:
    """Test that Devon prefers automated solutions."""
    sa = create_agent("site_architect")
    
    task = Task(
        type="deploy_recipe",
        content="Deploy this recipe to the website",
        default_strategy="standard",
    )
    
    result = sa.process_task(task)
    assert result.success
    
    # Devon should use automation
    output_str = str(result.output).lower()
    assert "automat" in output_str or "pipeline" in output_str
