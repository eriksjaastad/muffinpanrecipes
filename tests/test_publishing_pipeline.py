"""Tests for the publishing pipeline."""

import json
import pytest
from pathlib import Path
from datetime import datetime

from backend.data.recipe import Recipe, RecipeStatus
from backend.publishing.pipeline import PublishingPipeline
from backend.publishing.templates import (
    parse_duration,
    render_ingredients_html,
    render_instructions_html,
    generate_json_ld
)


class TestTemplateUtils:
    """Test template rendering utilities."""
    
    def test_parse_duration(self):
        """Test duration parsing to ISO 8601."""
        assert parse_duration("25 mins") == "PT25M"
        assert parse_duration("10 minutes") == "PT10M"
        assert parse_duration("") == "PT0M"
        assert parse_duration(None) == "PT0M"
    
    def test_render_ingredients_html_dict_format(self):
        """Test rendering ingredients in dict format."""
        ingredients = [
            {"item": "Eggs", "amount": "6 large"},
            {"item": "Milk", "amount": "1 cup"}
        ]
        html = render_ingredients_html(ingredients)
        assert "6 large Eggs" in html
        assert "1 cup Milk" in html
        assert '<li class="border-b border-gray-50 pb-2">' in html
    
    def test_render_ingredients_html_string_format(self):
        """Test rendering ingredients in legacy string format."""
        ingredients = ["6 large Eggs", "1 cup Milk"]
        html = render_ingredients_html(ingredients)
        assert "6 large Eggs" in html
        assert "1 cup Milk" in html
    
    def test_render_instructions_html(self):
        """Test rendering instructions with numbered steps."""
        instructions = ["Preheat oven", "Mix ingredients", "Bake"]
        html = render_instructions_html(instructions)
        assert "01</span>" in html  # First step number
        assert "02</span>" in html  # Second step number
        assert "03</span>" in html  # Third step number
        assert "Preheat oven" in html
        assert "Mix ingredients" in html
        assert "Bake" in html
    
    def test_generate_json_ld(self):
        """Test JSON-LD generation."""
        recipe_data = {
            "title": "Test Recipe",
            "description": "A test recipe",
            "prep": "10 mins",
            "cook": "20 mins",
            "yield": "12 portions",
            "category": "savory",
            "ingredients": ["2 Eggs", "1 cup Flour"],
            "instructions": ["Mix", "Bake"]
        }
        json_ld = generate_json_ld(recipe_data)
        
        assert '<script type="application/ld+json">' in json_ld
        assert '"@type": "Recipe"' in json_ld
        assert "Test Recipe" in json_ld
        assert "PT10M" in json_ld  # Prep time
        assert "PT20M" in json_ld  # Cook time


class TestPublishingPipeline:
    """Test publishing pipeline functionality."""
    
    @pytest.fixture
    def temp_project(self, tmp_path):
        """Create a temporary project structure for testing."""
        # Create directory structure
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        
        templates_dir = src_dir / "templates"
        templates_dir.mkdir()
        
        recipes_dir = src_dir / "recipes"
        recipes_dir.mkdir()
        
        data_dir = tmp_path / "data" / "recipes"
        data_dir.mkdir(parents=True)
        
        # Create subdirectories for recipe status
        for status in RecipeStatus:
            (data_dir / status.value).mkdir()
        
        # Create minimal template
        template_content = """<!DOCTYPE html>
<html>
<head><title>{{ title }}</title></head>
<body>
<h1>{{ title }}</h1>
<p>{{ description }}</p>
{{ json_ld }}
</body>
</html>"""
        template_path = templates_dir / "recipe_page.html"
        template_path.write_text(template_content)
        
        # Create empty recipes.json
        recipes_json = src_dir / "recipes.json"
        recipes_json.write_text('{"recipes": []}')
        
        return tmp_path
    
    @pytest.fixture
    def sample_recipe(self, temp_project):
        """Create a sample recipe for testing."""
        recipe = Recipe(
            recipe_id="test_recipe_001",
            title="Test Muffin Bites",
            concept="A test recipe for unit testing",
            description="Delicious test muffins for automated testing",
            ingredients=[
                {"item": "Eggs", "amount": "2 large"},
                {"item": "Flour", "amount": "1 cup"}
            ],
            instructions=[
                "Preheat oven to 350°F",
                "Mix ingredients",
                "Bake for 20 minutes"
            ],
            servings=12,
            prep_time_minutes=10,
            cook_time_minutes=20,
            slug="test-muffin-bites",
            category="savory",
            status=RecipeStatus.APPROVED
        )
        
        # Save to approved directory
        data_dir = temp_project / "data" / "recipes"
        recipe.save_to_file(data_dir, use_status_dir=True)
        
        return recipe
    
    def test_pipeline_initialization(self, temp_project):
        """Test pipeline initialization."""
        pipeline = PublishingPipeline(
            project_root=temp_project,
            auto_commit=False,
            auto_push=False
        )
        
        assert pipeline.project_root == temp_project
        assert pipeline.auto_commit is False
        assert pipeline.auto_push is False
        assert pipeline.src_dir.exists()
        assert pipeline.template_path.exists()
    
    def test_load_template(self, temp_project):
        """Test template loading."""
        pipeline = PublishingPipeline(
            project_root=temp_project,
            auto_commit=False
        )
        
        template = pipeline._load_template()
        assert "{{ title }}" in template
        assert "{{ description }}" in template
    
    def test_recipe_to_web_format(self, temp_project, sample_recipe):
        """Test converting Recipe model to web format."""
        pipeline = PublishingPipeline(
            project_root=temp_project,
            auto_commit=False
        )
        
        web_data = pipeline._recipe_to_web_format(sample_recipe)
        
        assert web_data["slug"] == "test-muffin-bites"
        assert web_data["title"] == "Test Muffin Bites"
        assert web_data["description"] == "Delicious test muffins for automated testing"
        assert web_data["prep"] == "10 mins"
        assert web_data["cook"] == "20 mins"
        assert web_data["yield"] == "12 portions"
        assert "2 large Eggs" in web_data["ingredients"]
        assert "1 cup Flour" in web_data["ingredients"]
    
    def test_publish_recipe(self, temp_project, sample_recipe):
        """Test full recipe publishing flow."""
        pipeline = PublishingPipeline(
            project_root=temp_project,
            auto_commit=False,  # Don't try to git commit in tests
            auto_push=False
        )
        
        # Publish the recipe
        success = pipeline.publish_recipe(
            sample_recipe.recipe_id,
            send_notification=False  # Don't send Discord notifications in tests
        )
        
        assert success is True
        
        # Verify HTML file was created
        html_file = temp_project / "src" / "recipes" / "test-muffin-bites" / "index.html"
        assert html_file.exists()
        
        html_content = html_file.read_text()
        assert "Test Muffin Bites" in html_content
        assert "Delicious test muffins" in html_content
        
        # Verify recipes.json was updated
        recipes_json = temp_project / "src" / "recipes.json"
        with open(recipes_json) as f:
            data = json.load(f)
        
        assert len(data["recipes"]) == 1
        assert data["recipes"][0]["slug"] == "test-muffin-bites"
        
        # Verify sitemap was generated
        sitemap = temp_project / "src" / "sitemap.xml"
        assert sitemap.exists()
        
        sitemap_content = sitemap.read_text()
        assert "test-muffin-bites" in sitemap_content
        
        # Verify recipe status was updated to PUBLISHED
        published_file = temp_project / "data" / "recipes" / "published" / f"{sample_recipe.recipe_id}.json"
        assert published_file.exists()
        
        published_recipe = Recipe.load_from_file(published_file)
        assert published_recipe.status == RecipeStatus.PUBLISHED
        assert published_recipe.published_at is not None
    
    def test_publish_recipe_not_approved(self, temp_project):
        """Test that publishing fails for non-approved recipes."""
        # Create a pending recipe
        recipe = Recipe(
            recipe_id="pending_recipe",
            title="Pending Recipe",
            concept="Not ready yet",
            description="Still pending",
            ingredients=[{"item": "egg", "amount": "1"}],
            instructions=["Wait"],
            servings=12,
            prep_time_minutes=5,
            cook_time_minutes=10,
            slug="pending-recipe",
            status=RecipeStatus.PENDING
        )

        
        data_dir = temp_project / "data" / "recipes"
        recipe.save_to_file(data_dir, use_status_dir=True)
        
        pipeline = PublishingPipeline(
            project_root=temp_project,
            auto_commit=False
        )
        
        # Try to publish - should fail
        success = pipeline.publish_recipe(recipe.recipe_id, send_notification=False)
        assert success is False
    
    def test_update_existing_recipe(self, temp_project, sample_recipe):
        """Test updating an already published recipe."""
        pipeline = PublishingPipeline(
            project_root=temp_project,
            auto_commit=False
        )
        
        # First publish
        pipeline.publish_recipe(sample_recipe.recipe_id, send_notification=False)
        
        # Modify the recipe and move back to approved
        sample_recipe.description = "Updated description"
        sample_recipe.status = RecipeStatus.APPROVED
        data_dir = temp_project / "data" / "recipes"
        sample_recipe.save_to_file(data_dir, use_status_dir=True)
        
        # Publish again
        pipeline.publish_recipe(sample_recipe.recipe_id, send_notification=False)
        
        # Verify updated content
        html_file = temp_project / "src" / "recipes" / "test-muffin-bites" / "index.html"
        html_content = html_file.read_text()
        assert "Updated description" in html_content
        
        # Verify only one entry in recipes.json
        recipes_json = temp_project / "src" / "recipes.json"
        with open(recipes_json) as f:
            data = json.load(f)
        assert len(data["recipes"]) == 1
