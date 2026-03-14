"""Tests for send2trash failure fallback in recipe.transition_status (#5047).

Verifies that transition_status correctly falls back to unlink() when
send2trash is unavailable, and uses send2trash when it is.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.data.recipe import Recipe, RecipeStatus


def _make_recipe(**overrides) -> Recipe:
    """Create a minimal valid Recipe for testing."""
    defaults = dict(
        recipe_id="test-recipe-001",
        title="Test Recipe",
        concept="Test concept",
        description="A test recipe.",
        ingredients=[{"item": "flour", "amount": "1 cup"}],
        instructions=["Mix well.", "Bake at 350F."],
        prep_time_minutes=10,
        cook_time_minutes=20,
        slug="test-recipe",
        status=RecipeStatus.PENDING,
    )
    defaults.update(overrides)
    return Recipe(**defaults)


class TestTransitionStatusTrashFallback:
    def test_uses_send2trash_when_available(self, tmp_path: Path):
        """When send2trash is importable, transition_status should use it."""
        recipe = _make_recipe()

        # Create the old file in pending/
        old_dir = tmp_path / "pending"
        old_dir.mkdir()
        old_file = old_dir / f"{recipe.recipe_id}.json"
        old_file.write_text("{}")

        mock_trash = MagicMock()
        with patch("backend.data.recipe.send2trash", mock_trash):
            recipe.transition_status(RecipeStatus.APPROVED, tmp_path)

        mock_trash.assert_called_once_with(str(old_file))

    def test_falls_back_to_unlink_when_no_send2trash(self, tmp_path: Path):
        """When send2trash is None, transition_status should use unlink()."""
        recipe = _make_recipe()

        # Create the old file in pending/
        old_dir = tmp_path / "pending"
        old_dir.mkdir()
        old_file = old_dir / f"{recipe.recipe_id}.json"
        old_file.write_text("{}")

        with patch("backend.data.recipe.send2trash", None):
            recipe.transition_status(RecipeStatus.APPROVED, tmp_path)

        # Old file should be gone (unlinked)
        assert not old_file.exists()
        # New file should exist in approved/
        new_file = tmp_path / "approved" / f"{recipe.recipe_id}.json"
        assert new_file.exists()

    def test_status_updated_after_transition(self, tmp_path: Path):
        recipe = _make_recipe()

        # Create old file
        old_dir = tmp_path / "pending"
        old_dir.mkdir()
        (old_dir / f"{recipe.recipe_id}.json").write_text("{}")

        with patch("backend.data.recipe.send2trash", None):
            recipe.transition_status(RecipeStatus.APPROVED, tmp_path)

        assert recipe.status == RecipeStatus.APPROVED

    def test_published_at_set_on_publish(self, tmp_path: Path):
        recipe = _make_recipe(status=RecipeStatus.APPROVED)

        old_dir = tmp_path / "approved"
        old_dir.mkdir()
        (old_dir / f"{recipe.recipe_id}.json").write_text("{}")

        assert recipe.published_at is None
        with patch("backend.data.recipe.send2trash", None):
            recipe.transition_status(RecipeStatus.PUBLISHED, tmp_path)

        assert recipe.published_at is not None
        assert recipe.status == RecipeStatus.PUBLISHED

    def test_review_notes_saved(self, tmp_path: Path):
        recipe = _make_recipe()

        old_dir = tmp_path / "pending"
        old_dir.mkdir()
        (old_dir / f"{recipe.recipe_id}.json").write_text("{}")

        with patch("backend.data.recipe.send2trash", None):
            recipe.transition_status(
                RecipeStatus.REJECTED, tmp_path, notes="Needs more garlic"
            )

        assert recipe.review_notes == "Needs more garlic"
        assert recipe.status == RecipeStatus.REJECTED

    def test_no_old_file_no_crash(self, tmp_path: Path):
        """If the old file doesn't exist, transition should still work."""
        recipe = _make_recipe()

        with patch("backend.data.recipe.send2trash", None):
            new_path = recipe.transition_status(RecipeStatus.APPROVED, tmp_path)

        assert new_path.exists()
        assert recipe.status == RecipeStatus.APPROVED

    def test_returns_new_filepath(self, tmp_path: Path):
        recipe = _make_recipe()

        old_dir = tmp_path / "pending"
        old_dir.mkdir()
        (old_dir / f"{recipe.recipe_id}.json").write_text("{}")

        with patch("backend.data.recipe.send2trash", None):
            result = recipe.transition_status(RecipeStatus.APPROVED, tmp_path)

        assert result == tmp_path / "approved" / f"{recipe.recipe_id}.json"
