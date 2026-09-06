"""Tests for scripts/fix_w36_category_cuisine.py — #6877.

Only the pure transform functions are tested — no network, no storage, no
blob writes. The script's I/O (fetching the catalog, calling storage,
regenerate_and_upload) is exercised only under --apply, which this repo's
test suite never runs (see the script's own refuse-unless-title-matches
guard for why it is safe to leave untested here).
"""

import copy

from scripts.fix_w36_category_cuisine import fix_catalog_entry, fix_episode


def test_fix_catalog_entry_changes_only_category_and_cuisine() -> None:
    entry = {
        "slug": "caramelized-custard-tart-cups",
        "title": "Caramelized Custard Tart Cups",
        "episode_id": "2026-W36",
        "recipe_id": "rid-123",
        "category": "Savory",
        "cuisine": "",
        "image": "assets/images/x.webp",
        "description": "A pastel de nata riff.",
    }
    original = copy.deepcopy(entry)

    fixed = fix_catalog_entry(entry)

    # Input is not mutated.
    assert entry == original

    # Exactly these two fields changed.
    assert fixed["category"] == "Sweet"
    assert fixed["cuisine"] == "Portuguese"

    # Nothing else changed.
    untouched = {k: v for k, v in fixed.items() if k not in ("category", "cuisine")}
    expected_untouched = {k: v for k, v in original.items() if k not in ("category", "cuisine")}
    assert untouched == expected_untouched
    assert set(fixed.keys()) == set(original.keys())


def test_fix_episode_changes_only_category_cuisine_and_target_category() -> None:
    ep = {
        "episode_id": "2026-W36",
        "concept": "Portuguese custard tarts baked in a muffin pan",
        "target_category": None,
        "stages": {
            "monday": {
                "status": "complete",
                "target_category": None,
                "recipe_data": {
                    "title": "Caramelized Custard Tart Cups",
                    "category": "savory",
                    "cuisine": None,
                    "ingredients": ["Egg yolks", "Sugar", "Puff pastry"],
                },
            },
            "tuesday": {"status": "complete"},
        },
    }
    original = copy.deepcopy(ep)

    fixed = fix_episode(ep)

    # Input is not mutated.
    assert ep == original

    # The four target fields changed.
    assert fixed["target_category"] == "Sweet"
    assert fixed["stages"]["monday"]["target_category"] == "Sweet"
    assert fixed["stages"]["monday"]["recipe_data"]["category"] == "sweet"
    assert fixed["stages"]["monday"]["recipe_data"]["cuisine"] == "Portuguese"

    # Everything else — every other recipe_data field, every other stage —
    # is untouched.
    fixed_rd = dict(fixed["stages"]["monday"]["recipe_data"])
    original_rd = dict(original["stages"]["monday"]["recipe_data"])
    for key in ("category", "cuisine"):
        del fixed_rd[key]
        del original_rd[key]
    assert fixed_rd == original_rd

    assert fixed["stages"]["tuesday"] == original["stages"]["tuesday"]
    assert fixed["concept"] == original["concept"]
    assert fixed["episode_id"] == original["episode_id"]
    assert set(fixed["stages"]["monday"].keys()) == set(original["stages"]["monday"].keys())
    assert set(fixed.keys()) == set(original.keys())
