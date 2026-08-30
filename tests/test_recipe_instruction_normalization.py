"""Markdown-shaped recipe instruction parsing and the W34 repair guard."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from backend.utils.recipe_prompts import (
    _parse_recipe_response,
    normalize_recipe_instructions,
)
from scripts import fix_encoding


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1. Cut chicken", "Cut chicken"),
        ("2) Stir the sauce", "Stir the sauce"),
        ("- Cut chicken", "Cut chicken"),
        ("* Cut chicken", "Cut chicken"),
        ("• Cut chicken", "Cut chicken"),
        ("**Cut chicken carefully.**", "Cut chicken carefully."),
        ("Mix **very gently** into the batter.", "Mix **very gently** into the batter."),
    ],
)
def test_normalizer_handles_markers_and_preserves_inner_emphasis(raw, expected):
    assert normalize_recipe_instructions([raw]) == [expected]


@pytest.mark.parametrize(
    "header",
    [
        "**Marinate the chicken:**",
        "Prepare the muffin pan:",
        "__Finish and serve:__",
    ],
)
def test_normalizer_drops_section_headers(header):
    assert normalize_recipe_instructions([header, "- Keep the cups warm."]) == [
        "Keep the cups warm."
    ]


def test_parser_normalizes_markdown_instruction_lines():
    response = """TITLE: Test Cups
DESCRIPTION: A test recipe.
INGREDIENTS:
- 1 cup flour
INSTRUCTIONS:
**Prepare the pan:**
- Grease each muffin well.
1. Add the batter.
* Bake until golden.
CHEF_NOTES:
Serve warm.
"""

    parsed = _parse_recipe_response(response, "Test Cups")

    assert parsed["instructions"] == [
        "Grease each muffin well.",
        "Add the batter.",
        "Bake until golden.",
    ]


W34_INSTRUCTIONS = [
    "**Marinate the chicken:**",
    "- Cut chicken thighs into small 1/2-inch pieces so they’ll nest tightly in the cups.",
    "- In a medium bowl, whisk together 1/2 cup yogurt, lemon juice, garam masala, cumin, smoked paprika, sweet paprika, coriander, turmeric, cayenne, 3/4 tsp salt, ginger, and garlic until smooth.",
    "- Add chicken pieces, stir to coat thoroughly, cover, and refrigerate at least 30 minutes (up to 4 hours).",
    "**Make quick pickled onions:**",
    "- In a small bowl, combine apple cider vinegar, water, sugar, and 1/4 tsp salt. Stir until sugar dissolves.",
    "- Add sliced red onion, press down to submerge, and let sit at room temperature while you continue. Stir occasionally.",
    "**Prepare the naan dough:**",
    "- In a medium bowl, whisk flour, baking powder, 1/2 tsp salt, and 1/2 tsp sugar.",
    "- In a small bowl, whisk 1/4 cup yogurt, milk, and neutral oil until smooth.",
    "- Pour wet ingredients into dry and stir with a fork until a shaggy dough forms.",
    "- Turn onto a lightly floured surface and knead gently for 1 to 2 minutes until smooth and soft, adding a light dusting of flour only if very sticky.",
    "- Form into a ball, cover with plastic wrap or an inverted bowl, and let rest at room temperature for 15 minutes. This relaxes the gluten so the dough presses cleanly into the round wells.",
    "**Prep the muffin pan and oven:**",
    "- Position a rack in the center of the oven and preheat to 400°F.",
    "- Generously grease a standard 12-cup muffin tin with butter or nonstick spray, making sure to coat the round bottom and sides of each well so the naan cups release cleanly.",
    "**Shape the naan cups:**",
    "- After resting, divide the dough into 12 equal pieces (about 1 heaping tablespoon each). I weigh them when I’m being fussy, but eyeballing is fine here.",
    "- Roll each piece into a ball, then flatten into a 3 to 3 1/2-inch round with your fingers or a small rolling pin.",
    "- Press each round firmly into a muffin well, working it up the sides so you form a neat cup with a thin, even layer of dough all around. The dough should hug the round sides and flare slightly at the top edge.",
    "- Brush the inside of each dough cup lightly with melted butter (reserve remaining butter for later).",
    "**Pre-cook the chicken filling:**",
    "- Line a baking sheet with foil for easy cleanup. Using tongs, lift chicken pieces from the marinade, letting excess drip off, and spread in a single layer on the sheet. Discard leftover marinade.",
    "- Bake at 400°F for 8 to 10 minutes, just until the chicken is mostly cooked through and starting to get a few browned edges. It will finish cooking inside the naan cups.",
    "- Remove from oven and reduce oven temperature to 375°F.",
    "**Fill and bake the naan cups:**",
    "- Spoon the par-cooked chicken pieces evenly into the naan-lined wells, packing them gently so each cup is nicely mounded but not overflowing (about 2 heaping tablespoons per cup).",
    "- Brush the exposed edges of the naan cups with the remaining melted butter for good color.",
    "- Bake at 375°F for 13 to 16 minutes, until the naan edges are golden brown and crisp, the bottoms are set, and the chicken registers at least 165°F. You should see the dough clearly holding the round, molded cup shape.",
    "**Make the yogurt drizzle:**",
    "- While the cups bake, in a small bowl whisk together 1/2 cup yogurt, lemon juice, cilantro, and 1/4 tsp salt until smooth. If you like a thinner drizzle, whisk in 1 to 2 tsp water.",
    "**Cool and release the cups:**",
    "- Remove muffin pan from the oven and let the cups rest in the pan for 5 minutes so the naan firms up and pulls slightly from the sides.",
    "- Run a thin knife or offset spatula carefully around the edge of each cup to loosen.",
    "- Lift each Tandoori Chicken Naan Cup straight up from the round well and transfer to a wire rack or serving plate. They should stand on their own as round, individual cups with crisp, flared edges.",
    "**Finish and serve:**",
    "- Drain the pickled onions.",
    "- Top each warm naan cup with a small spoonful of yogurt drizzle, a few strands of pickled onion, and a pinch of fresh cilantro.",
    "- Drizzle lightly with olive oil if desired and serve warm or at room temperature.",
]


def test_w34_repair_removes_only_the_ten_headers():
    repaired = normalize_recipe_instructions(W34_INSTRUCTIONS)

    assert len(W34_INSTRUCTIONS) == 39
    assert len(repaired) == 29
    assert all(not step.startswith(("-", "*", "•")) for step in repaired)
    assert all(not step.endswith(":") for step in repaired)
    assert all("**" not in step for step in repaired)


def _published_episode(episode_id: str) -> dict:
    return {
        "episode_id": episode_id,
        "stages": {
            "monday": {
                "recipe_data": {
                    "title": "Tandoori Chicken Naan Cups",
                    "instructions": list(W34_INSTRUCTIONS),
                }
            },
            "sunday": {"status": "complete"},
        },
    }


def test_fix_episode_repairs_and_saves_w34_before_rendering():
    episode = _published_episode("2026-W34")
    with (
        patch.object(fix_encoding.storage, "load_episode", return_value=episode),
        patch.object(fix_encoding.storage, "save_episode") as save_episode,
        patch.object(fix_encoding.storage, "save_page"),
        patch.object(fix_encoding, "render_episode_page", return_value="<html></html>"),
    ):
        assert fix_encoding.fix_episode("2026-W34") is True

    save_episode.assert_called_once_with("2026-W34", episode)
    assert episode["stages"]["monday"]["recipe_data"]["instructions"] == normalize_recipe_instructions(W34_INSTRUCTIONS)


def test_fix_episode_never_repairs_another_week():
    episode = _published_episode("2026-W33")
    with (
        patch.object(fix_encoding.storage, "load_episode", return_value=episode),
        patch.object(fix_encoding.storage, "save_episode") as save_episode,
        patch.object(fix_encoding.storage, "save_page"),
        patch.object(fix_encoding, "render_episode_page", return_value="<html></html>"),
    ):
        assert fix_encoding.fix_episode("2026-W33") is True

    save_episode.assert_not_called()
    assert episode["stages"]["monday"]["recipe_data"]["instructions"] == W34_INSTRUCTIONS
