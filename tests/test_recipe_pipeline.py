"""
Property-based tests for Recipe Pipeline Controller.

Feature: ai-creative-team
"""

import pytest
from hypothesis import given, strategies as st

from backend.pipeline.recipe_pipeline import RecipePipeline, PipelineStage, RecipeContext
from backend.agents.factory import create_agent


# Strategies
recipe_concepts = st.text(min_size=10, max_size=100)


# Feature: ai-creative-team, Property 3: Pipeline Stage Completeness
@given(recipe_concept=recipe_concepts)
def test_pipeline_stage_completeness(recipe_concept: str) -> None:
    """
    Property 3: Pipeline Stage Completeness
    
    For any recipe that enters the pipeline, it should:
    1. Pass through all required stages in order
    2. Have a defined owner for each stage
    3. Generate appropriate tasks for each stage
    4. Track all stage transitions
    
    Validates: Requirements 2.1
    """
    pipeline = RecipePipeline()
    
    # Start recipe
    recipe_id = f"test_{recipe_concept[:20]}"
    context = pipeline.start_recipe(recipe_id, recipe_concept)
    
    # Verify initial state
    assert context.recipe_id == recipe_id
    assert context.concept == recipe_concept
    assert context.current_stage == PipelineStage.IDEATION
    
    # Track all stages visited
    stages_visited = [context.current_stage]
    
    # Advance through pipeline
    expected_stages = [
        PipelineStage.IDEATION,
        PipelineStage.RECIPE_DEVELOPMENT,
        PipelineStage.PHOTOGRAPHY,
        PipelineStage.COPYWRITING,
        PipelineStage.CREATIVE_REVIEW,
        PipelineStage.FINAL_APPROVAL,
        PipelineStage.DEPLOYMENT,
        PipelineStage.COMPLETE,
    ]
    
    for i, expected_stage in enumerate(expected_stages[:-1]):  # Don't advance from COMPLETE
        # Verify current stage
        assert context.current_stage == expected_stage, \
            f"Should be at {expected_stage.value} (stage {i})"
        
        # Verify stage has an owner (except IDEATION which may not)
        if expected_stage != PipelineStage.IDEATION:
            owner = pipeline.get_current_owner(recipe_id)
            assert owner is not None, f"Stage {expected_stage.value} should have an owner"
        
        # Verify task can be created for stages that need them
        task = pipeline.create_task_for_stage(recipe_id)
        if expected_stage in [
            PipelineStage.RECIPE_DEVELOPMENT,
            PipelineStage.PHOTOGRAPHY,
            PipelineStage.COPYWRITING,
            PipelineStage.CREATIVE_REVIEW,
            PipelineStage.DEPLOYMENT
        ]:
            assert task is not None, f"Should create task for {expected_stage.value}"
            assert task.type is not None
            assert task.content is not None
        
        # Advance to next stage
        next_stage = pipeline.advance_stage(recipe_id)
        stages_visited.append(next_stage)
    
    # Verify all expected stages were visited
    assert stages_visited == expected_stages, \
        "Recipe should pass through all pipeline stages in order"
    
    # Verify recipe is now complete
    assert len(pipeline.completed_recipes) == 1
    assert pipeline.completed_recipes[0].recipe_id == recipe_id
    assert recipe_id not in pipeline.active_recipes


# Feature: ai-creative-team, Property 7: Creative Director Review Consistency
def test_creative_director_review_consistency() -> None:
    """
    Property 7: Creative Director Review Consistency
    
    The Creative Director's review process should:
    1. Occur at the CREATIVE_REVIEW stage
    2. Be able to approve or request revisions
    3. Provide feedback regardless of decision
    4. Route recipes back to appropriate stages for revision
    
    Validates: Requirements 6.1, 6.2
    """
    pipeline = RecipePipeline()
    cd = create_agent("creative_director")
    
    # Create a recipe
    recipe_id = "test_review"
    context = pipeline.start_recipe(recipe_id, "Test Recipe for Review")
    
    # Advance to CREATIVE_REVIEW stage
    while context.current_stage != PipelineStage.CREATIVE_REVIEW:
        pipeline.advance_stage(recipe_id)
        context = pipeline.active_recipes[recipe_id]
    
    # Verify CD is the owner
    owner = pipeline.get_current_owner(recipe_id)
    assert owner == "creative_director", \
        "Creative Director should own CREATIVE_REVIEW stage"
    
    # Test approval path
    context.add_review(
        reviewer="creative_director",
        approved=True,
        feedback="Looks great! Ready to go."
    )
    
    assert len(context.reviews) == 1
    assert context.reviews[0]["reviewer"] == "creative_director"
    assert context.reviews[0]["approved"] is True
    assert len(context.reviews[0]["feedback"]) > 0
    
    # Advance after approval
    next_stage = pipeline.advance_stage(recipe_id)
    assert next_stage == PipelineStage.FINAL_APPROVAL
    
    # Test revision path with a different recipe
    recipe_id2 = "test_revisions"
    context2 = pipeline.start_recipe(recipe_id2, "Recipe Needing Revisions")
    
    # Advance to review
    while context2.current_stage != PipelineStage.CREATIVE_REVIEW:
        pipeline.advance_stage(recipe_id2)
        context2 = pipeline.active_recipes[recipe_id2]
    
    # Request revisions
    context2.add_review(
        reviewer="creative_director",
        approved=False,
        feedback="The photographs need better lighting."
    )
    
    stage_sent_back_to = pipeline.request_revisions(
        recipe_id2,
        PipelineStage.PHOTOGRAPHY,
        "Need better lighting and composition",
        "creative_director"
    )
    
    assert stage_sent_back_to == PipelineStage.PHOTOGRAPHY
    assert context2.current_stage == PipelineStage.PHOTOGRAPHY
    assert context2.revision_count == 1
    assert len(context2.revision_requests) == 1
    assert context2.revision_requests[0]["stage"] == "photography"


def test_pipeline_revision_tracking() -> None:
    """Test that revisions are properly tracked through the pipeline."""
    pipeline = RecipePipeline()
    
    recipe_id = "revision_test"
    context = pipeline.start_recipe(recipe_id, "Recipe with Multiple Revisions")
    
    # Advance to COPYWRITING
    while context.current_stage != PipelineStage.COPYWRITING:
        pipeline.advance_stage(recipe_id)
        context = pipeline.active_recipes[recipe_id]
    
    # Request revision
    pipeline.request_revisions(
        recipe_id,
        PipelineStage.RECIPE_DEVELOPMENT,
        "Recipe needs less salt",
        "creative_director"
    )
    
    assert context.revision_count == 1
    assert context.current_stage == PipelineStage.RECIPE_DEVELOPMENT
    
    # Redo work and advance again
    pipeline.advance_stage(recipe_id)  # Back to PHOTOGRAPHY
    pipeline.advance_stage(recipe_id)  # Back to COPYWRITING
    pipeline.advance_stage(recipe_id)  # To CREATIVE_REVIEW
    
    # Another revision
    pipeline.request_revisions(
        recipe_id,
        PipelineStage.PHOTOGRAPHY,
        "Photos are too dark",
        "creative_director"
    )
    
    assert context.revision_count == 2
    assert len(context.revision_requests) == 2


def test_pipeline_statistics() -> None:
    """Test that pipeline statistics are accurately tracked."""
    pipeline = RecipePipeline()
    
    # Start multiple recipes at different stages
    r1 = pipeline.start_recipe("recipe1", "Concept 1")
    r2 = pipeline.start_recipe("recipe2", "Concept 2")
    r3 = pipeline.start_recipe("recipe3", "Concept 3")
    
    # Advance them to different stages
    pipeline.advance_stage("recipe1")  # To RECIPE_DEVELOPMENT
    
    pipeline.advance_stage("recipe2")  # To RECIPE_DEVELOPMENT
    pipeline.advance_stage("recipe2")  # To PHOTOGRAPHY
    
    pipeline.advance_stage("recipe3")  # To RECIPE_DEVELOPMENT
    pipeline.advance_stage("recipe3")  # To PHOTOGRAPHY
    pipeline.advance_stage("recipe3")  # To COPYWRITING
    
    stats = pipeline.get_statistics()
    
    assert stats["active_recipes"] == 3
    assert stats["completed_recipes"] == 0
    assert stats["by_stage"]["recipe_development"] == 1
    assert stats["by_stage"]["photography"] == 1
    assert stats["by_stage"]["copywriting"] == 1


def test_pipeline_work_product_storage() -> None:
    """Test that work products are stored correctly."""
    pipeline = RecipePipeline()
    
    recipe_id = "work_product_test"
    context = pipeline.start_recipe(recipe_id, "Test Recipe")
    
    # Advance to RECIPE_DEVELOPMENT
    pipeline.advance_stage(recipe_id)
    
    # Submit recipe work product
    recipe_data = {
        "name": "Test Muffins",
        "ingredients": ["flour", "eggs", "milk"],
        "instructions": ["Mix", "Bake"]
    }
    pipeline.advance_stage(recipe_id, work_product=recipe_data)
    
    context = pipeline.active_recipes[recipe_id]
    assert context.recipe_data == recipe_data
    
    # Advance to PHOTOGRAPHY
    assert context.current_stage == PipelineStage.PHOTOGRAPHY
    
    # Submit photo work product
    photos = {"photos": ["photo1.jpg", "photo2.jpg", "photo3.jpg"]}
    pipeline.advance_stage(recipe_id, work_product=photos)
    
    context = pipeline.active_recipes[recipe_id]
    assert context.photos == photos["photos"]
