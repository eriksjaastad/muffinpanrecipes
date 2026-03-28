"""Tests for self-healing pipeline functions.

Covers: _enforce_title_rules, _score_dialogue_qa, _auto_fix_recipe.
"""

import json
from unittest.mock import patch

import pytest

from backend.utils.recipe_prompts import _enforce_title_rules


# ---------------------------------------------------------------------------
# _enforce_title_rules
# ---------------------------------------------------------------------------


class TestEnforceTitleRules:
    def test_passthrough_clean_title(self):
        assert _enforce_title_rules("Lemon Meringue Cups") == "Lemon Meringue Cups"

    def test_strips_parenthetical(self):
        result = _enforce_title_rules("Egg Cups (Weekly Muffin Pan Breakfast)")
        assert "(" not in result
        assert ")" not in result
        assert result == "Egg Cups"

    def test_strips_subtitle_after_colon(self):
        assert _enforce_title_rules("Dinner Cups: A Muffin Twist") == "Dinner Cups"

    def test_strips_subtitle_after_em_dash(self):
        assert _enforce_title_rules("Chicken Bites \u2014 Crispy Edition") == "Chicken Bites"

    def test_strips_subtitle_after_en_dash(self):
        assert _enforce_title_rules("Veggie Bites \u2013 Garden Fresh") == "Veggie Bites"

    def test_strips_subtitle_after_ellipsis(self):
        # Ellipsis stripped first, then "Sunday" removed by day-of-week rule
        assert _enforce_title_rules("Sunday Dinner\u2026In A Muffin Tin") == "Dinner"

    def test_removes_day_of_week(self):
        assert _enforce_title_rules("Sunday Roasted Chicken Cups") == "Roasted Chicken Cups"

    def test_removes_day_case_insensitive(self):
        assert _enforce_title_rules("MONDAY Morning Muffins") == "Morning Muffins"

    def test_truncates_to_six_words(self):
        result = _enforce_title_rules("One Two Three Four Five Six Seven Eight")
        assert len(result.split()) == 6
        assert result == "One Two Three Four Five Six"

    def test_combined_parenthetical_and_long(self):
        result = _enforce_title_rules(
            "Sunday Sheet-Pan Dinner (Family Style) In A Muffin Tin"
        )
        # Strips Sunday, strips parenthetical, truncates
        words = result.split()
        assert len(words) <= 6
        assert "Sunday" not in result
        assert "(" not in result

    def test_empty_string(self):
        assert _enforce_title_rules("") == ""

    def test_cleans_double_spaces(self):
        # After removing a day name, double spaces should collapse
        result = _enforce_title_rules("Great Monday Cups")
        assert "  " not in result


# ---------------------------------------------------------------------------
# _score_dialogue_qa
# ---------------------------------------------------------------------------


class TestScoreDialogueQa:
    def test_returns_score_dict_on_valid_input(self):
        from backend.admin.cron_routes import _score_dialogue_qa

        dialogue = [
            {
                "character": "Margaret Chen",
                "message": "I think we should try a savory approach with rosemary.",
                "timestamp": "09:00",
                "model": "test",
            },
            {
                "character": "Marcus Reid",
                "message": "That pairs well with the goat cheese I picked up.",
                "timestamp": "09:05",
                "model": "test",
            },
            {
                "character": "Stephanie 'Steph' Whitmore",
                "message": "Our readers love anything with fresh herbs right now.",
                "timestamp": "09:10",
                "model": "test",
            },
        ]
        result = _score_dialogue_qa(dialogue, stage="monday", concept="rosemary cups")
        assert "score" in result
        assert isinstance(result["score"], (int, float))
        assert "details" in result

    def test_returns_empty_dict_on_empty_dialogue(self):
        from backend.admin.cron_routes import _score_dialogue_qa

        result = _score_dialogue_qa([], stage="monday", concept="test")
        # Either returns a score dict for empty input or empty dict on failure
        # Both are acceptable — the function is non-fatal
        assert isinstance(result, dict)

    def test_non_fatal_on_bad_input(self):
        from backend.admin.cron_routes import _score_dialogue_qa

        # Malformed dialogue entries shouldn't crash
        result = _score_dialogue_qa(
            [{"bad": "data"}], stage="monday", concept="test"
        )
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# _auto_fix_recipe
# ---------------------------------------------------------------------------


class TestAutoFixRecipe:
    def _make_episode(self, recipe_data: dict) -> dict:
        return {
            "stages": {
                "monday": {
                    "recipe_data": recipe_data,
                }
            }
        }

    def test_returns_false_when_no_recipe(self):
        from backend.admin.cron_routes import _auto_fix_recipe

        episode = {"stages": {"monday": {}}}
        assert _auto_fix_recipe(episode, "title too long") is False

    def test_returns_false_on_empty_stages(self):
        from backend.admin.cron_routes import _auto_fix_recipe

        assert _auto_fix_recipe({}, "qa report") is False

    @patch("backend.admin.cron_routes.config")
    @patch("backend.admin.cron_routes.generate_response")
    def test_applies_fix_from_llm(self, mock_generate, mock_config):
        from backend.admin.cron_routes import _auto_fix_recipe
        mock_config.recipe_model = "test-model"

        fixed_recipe = {
            "title": "Rosemary Goat Cheese Cups",
            "description": "Savory herb cups",
            "servings": 12,
            "prep_time": 15,
            "cook_time": 20,
            "difficulty": "medium",
            "category": "savory",
            "ingredients": [{"item": "goat cheese", "amount": "4 oz", "notes": ""}],
            "instructions": ["Mix goat cheese with herbs.", "Fill muffin cups."],
            "chef_notes": "Best served warm.",
        }
        mock_generate.return_value = f"```json\n{json.dumps(fixed_recipe)}\n```"

        episode = self._make_episode({
            "title": "Sunday Rosemary Goat Cheese Cups (A Weekly Special)",
            "description": "old desc",
            "ingredients": [{"item": "cheese", "amount": "4 oz", "notes": ""}],
            "instructions": ["Step 1"],
        })

        result = _auto_fix_recipe(episode, "Title too long, has parenthetical")
        assert result is True
        new_recipe = episode["stages"]["monday"]["recipe_data"]
        assert new_recipe["title"] == "Rosemary Goat Cheese Cups"
        assert len(new_recipe["ingredients"]) == 1

    @patch("backend.admin.cron_routes.config")
    @patch("backend.admin.cron_routes.generate_response")
    def test_enforces_title_rules_on_fix(self, mock_generate, mock_config):
        from backend.admin.cron_routes import _auto_fix_recipe
        mock_config.recipe_model = "test-model"

        # LLM returns a title that still breaks rules
        fixed_recipe = {
            "title": "Sunday Mega Deluxe Rosemary Goat Cheese Cups (Special Edition)",
            "description": "desc",
            "ingredients": [{"item": "cheese", "amount": "4 oz", "notes": ""}],
            "instructions": ["Step 1"],
        }
        mock_generate.return_value = json.dumps(fixed_recipe)

        episode = self._make_episode({
            "title": "Bad Title",
            "ingredients": [{"item": "x", "amount": "1", "notes": ""}],
            "instructions": ["y"],
        })

        result = _auto_fix_recipe(episode, "Title violations")
        assert result is True
        title = episode["stages"]["monday"]["recipe_data"]["title"]
        assert "Sunday" not in title
        assert "(" not in title
        assert len(title.split()) <= 6

    @patch("backend.admin.cron_routes.config")
    @patch("backend.admin.cron_routes.generate_response")
    def test_returns_false_on_incomplete_fix(self, mock_generate, mock_config):
        from backend.admin.cron_routes import _auto_fix_recipe
        mock_config.recipe_model = "test-model"

        # LLM returns recipe missing required fields
        mock_generate.return_value = json.dumps({"title": "Cups", "ingredients": []})

        episode = self._make_episode({
            "title": "Test",
            "ingredients": [{"item": "x", "amount": "1", "notes": ""}],
            "instructions": ["y"],
        })

        result = _auto_fix_recipe(episode, "Some issues")
        assert result is False

    @patch("backend.admin.cron_routes.config")
    @patch("backend.admin.cron_routes.generate_response")
    def test_returns_false_on_invalid_json(self, mock_generate, mock_config):
        from backend.admin.cron_routes import _auto_fix_recipe
        mock_config.recipe_model = "test-model"

        mock_generate.return_value = "I can't fix this recipe because..."

        episode = self._make_episode({
            "title": "Test",
            "ingredients": [{"item": "x", "amount": "1", "notes": ""}],
            "instructions": ["y"],
        })

        result = _auto_fix_recipe(episode, "Some issues")
        assert result is False
