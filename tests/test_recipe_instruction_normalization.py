"""Markdown-shaped recipe instruction parsing and the W34 repair guard."""

from __future__ import annotations

import sys
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


# fix_episode reads the serving slug from the live catalog and refuses to guess
# one, so every test that renders a page has to supply the matching row.
_CATALOG = {"recipes": [{"title": "Tandoori Chicken Naan Cups", "slug": "tandoori-chicken-naan-cups"}]}


def test_fix_episode_refuses_when_the_catalog_has_no_row():
    """A page whose serving slug we cannot confirm must be left alone (#7106).

    Re-deriving it from the title would write an orphan page at a new path and
    leave the real URL serving stale HTML.
    """
    episode = _published_episode("2026-W33")
    with (
        patch.object(fix_encoding.storage, "load_episode", return_value=episode),
        patch.object(fix_encoding.storage, "save_page") as save_page,
        patch.object(fix_encoding, "render_episode_page", return_value="<html></html>"),
    ):
        assert fix_encoding.fix_episode("2026-W33", catalog={"recipes": []}) is False

    save_page.assert_not_called()


def test_fix_episode_uses_the_catalog_slug_not_a_re_derived_one():
    """W37's live URL predates the #7106 slugify fix; a re-render must not move it."""
    episode = _published_episode("2026-W33")
    episode["stages"]["monday"]["recipe_data"]["title"] = "Brazilian Pao de Queijo Bites"
    legacy = {"recipes": [{"title": "Brazilian Pao de Queijo Bites", "slug": "brazilian-p-o-de-queijo-bites"}]}
    with (
        patch.object(fix_encoding.storage, "load_episode", return_value=episode),
        patch.object(fix_encoding.storage, "save_page") as save_page,
        patch.object(fix_encoding, "render_episode_page", return_value="<html></html>") as render,
    ):
        assert fix_encoding.fix_episode("2026-W33", catalog=legacy) is True

    written = [c.args[0] for c in save_page.call_args_list]
    assert "pages/recipes/brazilian-p-o-de-queijo-bites/index.html" in written
    assert not any("brazilian-pao-de-queijo-bites" in w for w in written)
    # and the canonical inside the HTML is pinned to that same serving slug
    assert render.call_args.kwargs["canonical_slug"] == "brazilian-p-o-de-queijo-bites"


def test_fix_episode_repairs_and_saves_w34_before_rendering():
    episode = _published_episode("2026-W34")
    with (
        patch.object(fix_encoding.storage, "load_episode", return_value=episode),
        patch.object(fix_encoding.storage, "save_episode") as save_episode,
        patch.object(fix_encoding.storage, "save_page"),
        patch.object(fix_encoding, "render_episode_page", return_value="<html></html>"),
    ):
        assert fix_encoding.fix_episode("2026-W34", catalog=_CATALOG) is True

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
        assert fix_encoding.fix_episode("2026-W33", catalog=_CATALOG) is True

    save_episode.assert_not_called()
    assert episode["stages"]["monday"]["recipe_data"]["instructions"] == W34_INSTRUCTIONS


def test_fix_encoding_main_aborts_when_the_catalog_is_unreachable():
    """Without the catalog there are no confirmed serving slugs, so write nothing.

    The alternative - falling back to _slugify - is what #7106 made unsafe.
    """
    argv = ["fix_encoding.py", "--all", "--full-rebuild"]
    with (
        patch.object(sys, "argv", argv),
        patch.object(
            fix_encoding,
            "load_published_catalog",
            side_effect=fix_encoding.CatalogUnavailableError("blob unreachable"),
        ),
        patch.object(fix_encoding.storage, "save_page") as save_page,
    ):
        assert fix_encoding.main() == 1

    save_page.assert_not_called()


def test_fix_episode_refuses_before_writing_the_w34_repair():
    """Refusing after a production write is not refusing (Codex audit).

    The W34 instruction repair calls save_episode(). It used to run BEFORE the
    slug was resolved, so an episode whose slug could not be confirmed got its
    recipe data mutated in production and then reported SKIP, leaving the
    episode and its published page out of sync.
    """
    episode = _published_episode("2026-W34")
    with (
        patch.object(fix_encoding.storage, "load_episode", return_value=episode),
        patch.object(fix_encoding.storage, "save_episode") as save_episode,
        patch.object(fix_encoding.storage, "save_page") as save_page,
        patch.object(fix_encoding, "render_episode_page", return_value="<html></html>"),
    ):
        assert fix_encoding.fix_episode("2026-W34", catalog={"recipes": []}) is False

    save_episode.assert_not_called()
    save_page.assert_not_called()


def test_catalog_slug_prefers_episode_id_over_title():
    """Rows written since 2026-09 carry episode_id; that match is exact."""
    catalog = {
        "recipes": [
            {"title": "Some Other Title", "slug": "the-right-slug", "episode_id": "2026-W37"},
            {"title": "Tandoori Chicken Naan Cups", "slug": "the-wrong-slug"},
        ]
    }
    got = fix_encoding.catalog_slug_for_title(
        "Tandoori Chicken Naan Cups", catalog, episode_id="2026-W37"
    )
    assert got == "the-right-slug"


def test_catalog_slug_refuses_an_ambiguous_title():
    """Two rows sharing a cleaned title must refuse, not silently take the first."""
    catalog = {
        "recipes": [
            {"title": "Twin Cups", "slug": "twin-cups-a"},
            {"title": "Twin Cups", "slug": "twin-cups-b"},
        ]
    }
    assert fix_encoding.catalog_slug_for_title("Twin Cups", catalog) is None


def test_catalog_slug_still_matches_a_legacy_row_by_title():
    catalog = {"recipes": [{"title": "Legacy Cups", "slug": "legacy-cups"}]}
    assert fix_encoding.catalog_slug_for_title("Legacy Cups", catalog) == "legacy-cups"
