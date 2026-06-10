"""Regression tests for muffin-pan form quality gates."""

import asyncio
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

from backend.admin import cron_routes
from backend.utils.muffin_pan_form import check_muffin_pan_form


def _recipe(title: str, description: str, instructions: list[str], chef_notes: str = "") -> dict:
    return {
        "title": title,
        "description": description,
        "ingredients": [{"item": "eggs", "amount": "2"}, {"item": "cheese", "amount": "1 cup"}],
        "instructions": instructions,
        "chef_notes": chef_notes,
    }


def test_rejects_mini_caprese_bruschetta_bites_loose_form():
    recipe = _recipe(
        "Mini Caprese Bruschetta Bites",
        "Tomatoes, mozzarella, and basil are spooned into muffin cups for easy serving.",
        [
            "Toast baguette rounds.",
            "Spoon the tomato salad into the muffin tin wells.",
            "Serve directly from the muffin tin while the filling is still loose.",
        ],
    )

    assert check_muffin_pan_form(recipe) is not None


def test_rejects_smoky_sweet_potato_frittatas_without_shape_release():
    recipe = _recipe(
        "Smoky Sweet Potato Frittatas",
        "A smoky sweet potato breakfast baked in a muffin tin.",
        [
            "Roast sweet potato cubes.",
            "Divide the vegetables among muffin cups.",
            "Bake until warm and garnish with herbs.",
        ],
    )

    reason = check_muffin_pan_form(recipe)
    assert reason is not None
    assert "hold shape" in reason or "set" in reason


def test_rejects_herbed_sausage_sunrise_cups_incidental_tin():
    recipe = _recipe(
        "Herbed Sausage Sunrise Cups",
        "A sausage and egg breakfast where the muffin tin is incidental portion control.",
        [
            "Brown sausage with herbs.",
            "Divide the sausage and eggs among the muffin cups.",
            "Bake until hot and serve in the pan.",
        ],
    )

    assert check_muffin_pan_form(recipe) is not None


def test_accepts_spinach_feta_egg_bites():
    recipe = _recipe(
        "Spinach Feta Egg Bites",
        "Tender egg bites shaped by the muffin pan.",
        [
            "Whisk eggs, cream, salt, and pepper until smooth.",
            "Distribute spinach and feta across muffin cups.",
            "Bake until the centers are set.",
            "Rest 5 minutes, then release each bite with a thin spatula.",
        ],
    )

    assert check_muffin_pan_form(recipe) is None


def test_accepts_mini_meatloaf_bites():
    recipe = _recipe(
        "Mini Meatloaf Bites",
        "Mini meatloaves with caramelized edges from the muffin pan.",
        [
            "Mix beef, breadcrumbs, egg, milk, and seasonings.",
            "Scoop the meat mixture into muffin cups and smooth the tops.",
            "Bake until cooked through.",
            "Rest 5 minutes in the pan so the bites do not fall apart.",
        ],
    )

    assert check_muffin_pan_form(recipe) is None


def test_accepts_buffalo_chicken_mac_bites():
    recipe = _recipe(
        "Buffalo Chicken Mac Bites",
        "Mac and cheese bites with crisp edges and a creamy center.",
        [
            "Combine macaroni, cheese, chicken, buffalo sauce, and beaten egg.",
            "Pack the mixture firmly into mini muffin cups.",
            "Bake until golden and crisp at the edges.",
            "Let them cool until set so the bites hold their shape.",
        ],
    )

    assert check_muffin_pan_form(recipe) is None


def test_editorial_qa_rejects_bad_form_before_llm_judge():
    episode = {
        "stages": {
            "monday": {
                "recipe_data": _recipe(
                    "Mini Caprese Bruschetta Bites",
                    "A loose tomato salad spooned into muffin cups.",
                    ["Spoon the salad into cups.", "Serve directly from the pan."],
                )
            }
        }
    }

    with patch.object(cron_routes, "generate_judge_response") as judge:
        passed, report = cron_routes._editorial_qa_review(episode)

    assert passed is False
    assert "MUFFIN PAN FORM" in report
    judge.assert_not_called()


def test_cron_monday_retries_baker_when_form_gate_fails():
    bad_recipe = _recipe(
        "Mini Caprese Bruschetta Bites",
        "A loose tomato salad spooned into muffin cups.",
        ["Spoon the salad into cups.", "Serve directly from the pan."],
    )
    good_recipe = _recipe(
        "Spinach Feta Egg Bites",
        "Tender egg bites shaped by the muffin pan.",
        [
            "Whisk eggs until smooth.",
            "Bake until the centers are set.",
            "Rest 5 minutes, then release each bite with a thin spatula.",
        ],
    )
    baker_concepts: list[str] = []

    class FakePipeline:
        def start_recipe(self, *_args, **_kwargs):
            return None

    class FakeOrchestrator:
        def __init__(self, *_args, **_kwargs):
            self.pipeline = FakePipeline()

        def _execute_stage_baker(self, _recipe_id, concept, **_kwargs):
            baker_concepts.append(concept)
            return bad_recipe if len(baker_concepts) == 1 else good_recipe

    request = SimpleNamespace(url=SimpleNamespace(path="/api/cron/monday"))
    episode = {
        "episode_id": "2026-W99",
        "concept": "Caprese brunch cups",
        "stages": {},
        "events": [],
        "recipe_id": None,
    }

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(
             cron_routes,
             "_parse_body",
             new=AsyncMock(return_value=cron_routes.StageRequest(
                 episode_id="2026-W99",
                 concept="Caprese brunch cups",
                 force=True,
             )),
         ), \
         patch.object(cron_routes, "_test_mode_scope", return_value=nullcontext()), \
         patch.object(cron_routes, "_load_or_create_episode", return_value=episode), \
         patch.object(cron_routes, "_get_orchestrator", return_value=FakeOrchestrator), \
         patch("backend.utils.title_validator.load_catalog_titles", return_value=[]), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Margaret", "message": "These hold together."}],
             "PASS",
         )), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "regenerate_and_upload"):
        result = asyncio.run(cron_routes.cron_monday(request))

    assert len(baker_concepts) == 2
    assert "CRITICAL MUFFIN-PAN FORM" in baker_concepts[1]
    assert result["recipe_title"] == "Spinach Feta Egg Bites"
    assert episode["stages"]["monday"]["recipe_data"] == good_recipe
    save_episode.assert_called_once_with("2026-W99", episode)


# ---------------------------------------------------------------------------
# Off-brand title shapes — W24 "Cheddar Broccoli Egg Squares" rendered literal
# sheet-pan squares because the title named a shape a muffin pan cannot make.
# ---------------------------------------------------------------------------

def _valid_body_recipe(title: str) -> dict:
    return _recipe(
        title,
        "Tender portions shaped by the muffin pan.",
        [
            "Whisk eggs, cream, salt, and pepper until smooth.",
            "Distribute filling across muffin cups.",
            "Bake until the centers are set.",
            "Rest 5 minutes, then release each bite with a thin spatula.",
        ],
    )


def test_rejects_squares_title_even_with_valid_body():
    reason = check_muffin_pan_form(_valid_body_recipe("Cheddar Broccoli Egg Squares"))
    assert reason is not None
    assert "shape a muffin pan cannot make" in reason


def test_rejects_bars_and_slices_titles():
    assert check_muffin_pan_form(_valid_body_recipe("Lemon Crumble Bars")) is not None
    assert check_muffin_pan_form(_valid_body_recipe("Pepperoni Pizza Slices")) is not None


def test_rejects_singular_bar_title():
    """Singular 'Bar' is deliberately caught — 'Candy Bar Bites' should be renamed."""
    assert check_muffin_pan_form(_valid_body_recipe("Candy Bar Bites")) is not None


def test_accepts_cups_title_with_valid_body():
    assert check_muffin_pan_form(_valid_body_recipe("Cheddar Broccoli Egg Cups")) is None
