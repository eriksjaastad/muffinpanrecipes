"""Catalog publish deduplication for Sunday re-fire protection."""

from __future__ import annotations

import json
from unittest.mock import patch

from backend.publishing import episode_renderer


def _episode(
    *,
    title: str = "Herbed Sausage Sunrise Cups",
    episode_id: str = "2026-W20",
    recipe_id: str = "e9f30301",
    image_url: str = "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/e9f30301.png",
) -> dict:
    return {
        "episode_id": episode_id,
        "recipe_id": recipe_id,
        "image_urls": [image_url],
        "stages": {
            "monday": {
                "recipe_data": {
                    "title": title,
                    "category": "breakfast",
                    "description": "Savory breakfast cups with herbs.",
                    "prep_time": 15,
                    "cook_time": 20,
                    "servings": 12,
                    "ingredients": [
                        {"amount": "6", "item": "eggs"},
                        {"amount": "4 oz", "item": "sausage"},
                    ],
                    "instructions": [
                        "Whisk the eggs.",
                        "Fold in sausage and bake.",
                    ],
                }
            }
        },
    }


def test_publish_recipe_to_catalog_skips_existing_slug():
    catalog = {"recipes": [{"slug": "herbed-sausage-sunrise-cups"}]}

    with patch.object(episode_renderer.storage, "load_page", return_value=json.dumps(catalog)), \
         patch.object(episode_renderer.storage, "save_page") as save_page:
        result = episode_renderer.publish_recipe_to_catalog(_episode())

    assert result is None
    save_page.assert_not_called()


def test_publish_recipe_to_catalog_skips_new_slug_with_existing_image():
    catalog = {
        "recipes": [
            {
                "slug": "herbed-sausage-sunrise-cups",
                "image": "/blob-images/e9f30301.webp",
                "ingredients": ["6 eggs", "4 oz sausage"],
                "instructions": ["Whisk the eggs.", "Fold in sausage and bake."],
            }
        ]
    }

    with patch.object(episode_renderer.storage, "load_page", return_value=json.dumps(catalog)), \
         patch.object(episode_renderer.storage, "save_page") as save_page:
        result = episode_renderer.publish_recipe_to_catalog(
            _episode(title="Cheddar Turkey Breakfast Rounds")
        )

    assert result is None
    save_page.assert_not_called()


def test_publish_recipe_to_catalog_skips_new_slug_with_existing_body():
    catalog = {
        "recipes": [
            {
                "slug": "different-title",
                "image": "/blob-images/different-image.webp",
                "description": "Savory breakfast cups with herbs.",
                "ingredients": ["6 eggs", "4 oz sausage"],
                "instructions": ["Whisk the eggs.", "Fold in sausage and bake."],
            }
        ]
    }

    with patch.object(episode_renderer.storage, "load_page", return_value=json.dumps(catalog)), \
         patch.object(episode_renderer.storage, "save_page") as save_page:
        result = episode_renderer.publish_recipe_to_catalog(
            _episode(title="Cheddar Turkey Breakfast Rounds", image_url="/blob-images/other.png")
        )

    assert result is None
    save_page.assert_not_called()


def test_publish_recipe_to_catalog_prepends_distinct_recipe_with_identity_metadata():
    catalog = {"recipes": []}
    saved: dict[str, str] = {}

    def fake_save_page(pathname: str, content: str) -> str:
        saved["pathname"] = pathname
        saved["content"] = content
        return "/pages/recipes.json"

    with patch.object(episode_renderer.storage, "load_page", return_value=json.dumps(catalog)), \
         patch.object(episode_renderer.storage, "save_page", side_effect=fake_save_page):
        result = episode_renderer.publish_recipe_to_catalog(_episode())

    assert result == "/pages/recipes.json"
    assert saved["pathname"] == "pages/recipes.json"
    recipes = json.loads(saved["content"])["recipes"]
    assert recipes[0]["slug"] == "herbed-sausage-sunrise-cups"
    assert recipes[0]["episode_id"] == "2026-W20"
    assert recipes[0]["recipe_id"] == "e9f30301"



def _written_row(save_page) -> dict:
    """Return the single catalog row publish_recipe_to_catalog just wrote."""
    payload = json.loads(save_page.call_args.args[1])
    return payload["recipes"][-1]


def test_catalog_row_image_follows_the_pinned_hero() -> None:
    """#6965: the card image is the hero that actually won, not image_urls[0].

    image_urls[0] is always round_1/macro_closeup, so before this every card showed
    the macro shot even for weeks whose winner was overhead_flatlay.
    """
    episode = _episode(title="Pinned Hero Cups")
    episode["image_urls"] = [
        "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/e9f30301/round_1/macro_closeup.png"
    ]
    episode["hero_image_url"] = (
        "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/e9f30301/round_1/overhead_flatlay.png"
    )

    with patch.object(episode_renderer.storage, "load_page", return_value=json.dumps({"recipes": []})), \
         patch.object(episode_renderer.storage, "save_page") as save_page:
        episode_renderer.publish_recipe_to_catalog(episode)

    assert _written_row(save_page)["image"] == "/blob-images/e9f30301/round_1/overhead_flatlay.webp"


def test_catalog_row_falls_back_to_image_urls_without_a_pinned_hero() -> None:
    """Forward-only: the 25 pre-2026-09-05 rows have no hero_image_url and must not move."""
    episode = _episode(title="Legacy Cups")
    episode["image_urls"] = [
        "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/images/e9f30301/round_1/macro_closeup.png"
    ]
    assert "hero_image_url" not in episode

    with patch.object(episode_renderer.storage, "load_page", return_value=json.dumps({"recipes": []})), \
         patch.object(episode_renderer.storage, "save_page") as save_page:
        episode_renderer.publish_recipe_to_catalog(episode)

    assert _written_row(save_page)["image"] == "/blob-images/e9f30301/round_1/macro_closeup.webp"
