"""Card #6702 — seed-recipe calories are transcribed, not fabricated.

The 10 launch seed recipes (src/seed_recipes.json) predate the `calories`
field entirely. This backfills each entry's `recipe_data.calories` straight
from the matching data/recipes/<slug>.md frontmatter, on the condition that
the frontmatter's per-portion unit (per muffin / per bite) actually equals
per-serving — i.e. recipe_data.servings agrees with the frontmatter `yield`
count. Each test below re-derives the expected number from the frontmatter
file itself, so a future edit to seed_recipes.json that drifts from its
source of truth (or that changes servings without updating calories) fails
loudly instead of silently shipping a wrong "calories" claim to readers and
to Google's Recipe rich-result JSON-LD.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.publishing.episode_renderer import render_seed_recipe_page

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_RECIPES_PATH = REPO_ROOT / "src" / "seed_recipes.json"
RECIPES_DIR = REPO_ROOT / "data" / "recipes"


def _seed_recipes() -> dict:
    return json.loads(SEED_RECIPES_PATH.read_text(encoding="utf-8"))


def _frontmatter_calories(slug: str) -> int:
    """Parse the integer out of a `calories: "N kcal per <unit>"` line."""
    text = (RECIPES_DIR / f"{slug}.md").read_text(encoding="utf-8")
    match = re.search(r'^calories:\s*"(\d+)\s*kcal', text, re.MULTILINE)
    assert match, f"{slug}.md has no parseable 'calories' frontmatter line"
    return int(match.group(1))


def _frontmatter_yield_count(slug: str) -> int:
    """Parse the leading integer out of a `yield: "N ... muffins"` line."""
    text = (RECIPES_DIR / f"{slug}.md").read_text(encoding="utf-8")
    match = re.search(r'^yield:\s*"(\d+)', text, re.MULTILINE)
    assert match, f"{slug}.md has no parseable 'yield' frontmatter line"
    return int(match.group(1))


def _extract_json_ld(html: str) -> dict:
    start = html.index('application/ld+json">') + len('application/ld+json">')
    end = html.index("</script>", start)
    return json.loads(html[start:end])


SLUGS = sorted(_seed_recipes().keys())


@pytest.mark.parametrize("slug", SLUGS)
def test_calories_present_and_positive(slug) -> None:
    rd = _seed_recipes()[slug]["recipe_data"]
    calories = rd.get("calories")
    assert isinstance(calories, int) and calories > 0, slug


@pytest.mark.parametrize("slug", SLUGS)
def test_calories_match_frontmatter_transcription(slug) -> None:
    """Pins the transcription to its source — a drifted number fails here."""
    rd = _seed_recipes()[slug]["recipe_data"]
    assert rd["calories"] == _frontmatter_calories(slug), slug


@pytest.mark.parametrize("slug", SLUGS)
def test_servings_matches_frontmatter_yield(slug) -> None:
    """Per-portion == per-serving only holds when servings agrees with yield;
    this is the precondition the card requires before trusting the figure."""
    rd = _seed_recipes()[slug]["recipe_data"]
    assert rd["servings"] == _frontmatter_yield_count(slug), slug


@pytest.mark.parametrize("slug", SLUGS)
def test_rendered_page_shows_matching_nutrition(slug) -> None:
    rec = _seed_recipes()[slug]
    html_text = render_seed_recipe_page(rec["recipe_data"], rec.get("image", ""), slug)
    ld = _extract_json_ld(html_text)
    calories = rec["recipe_data"]["calories"]

    assert ld["nutrition"] == {
        "@type": "NutritionInformation",
        "calories": f"{calories} calories",
    }
    assert f"{calories} per serving" in html_text
