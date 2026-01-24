"""
Site builder CLI - thin wrapper around PublishingPipeline.

This script maintains backward compatibility while delegating
all logic to the new backend.publishing.pipeline module.
"""

import sys
import argparse
from pathlib import Path

from backend.publishing.pipeline import PublishingPipeline
from backend.utils.logging import get_logger

logger = get_logger(__name__)


def main():
    """Main CLI entry point for site building."""
    parser = argparse.ArgumentParser(
        description="Build the Muffin Pan Recipes site"
    )
    parser.add_argument(
        "--no-commit",
        action="store_true",
        help="Skip git commit after building"
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Skip git push after building (implies --no-commit for push only)"
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Full site rebuild from published recipes (default: use recipes.json)"
    )
    
    args = parser.parse_args()
    
    logger.info("--- 🧁 Muffin Pan Recipes: Site Builder ---")
    
    # Initialize pipeline
    pipeline = PublishingPipeline(
        auto_commit=not args.no_commit,
        auto_push=not args.no_push
    )
    
    try:
        if args.rebuild:
            # Full rebuild from PUBLISHED recipes in data/
            logger.info("Mode: Full site rebuild from published recipes")
            success = pipeline.rebuild_site()
        else:
            # Legacy mode: rebuild from src/recipes.json
            # This is the default to maintain backward compatibility
            logger.info("Mode: Legacy build from src/recipes.json")
            success = _legacy_build(pipeline)
        
        if success:
            logger.info("✨ SITE BUILD SUCCESSFUL")
            logger.info("--------------------------------------------------")
            sys.exit(0)
        else:
            logger.error("❌ SITE BUILD FAILED")
            sys.exit(1)
            
    except Exception as e:
        logger.error(f"❌ Build error: {e}", exc_info=True)
        sys.exit(1)


def _legacy_build(pipeline: PublishingPipeline) -> bool:
    """
    Legacy build mode that uses src/recipes.json as source.
    
    This maintains backward compatibility with the old build_site.py behavior.
    In the future, this will be replaced with publishing from approved recipes.
    """
    import json
    
    # Load recipes.json directly
    if not pipeline.recipes_json_path.exists():
        logger.error(f"recipes.json not found at {pipeline.recipes_json_path}")
        return False
    
    with open(pipeline.recipes_json_path, "r") as f:
        data = json.load(f)
        recipes = data.get("recipes", data) if isinstance(data, dict) else data
    
    logger.info(f"Loaded {len(recipes)} recipes from recipes.json")
    
    # Load template
    template_content = pipeline._load_template()
    
    # Clear recipes directory
    if pipeline.recipes_output_dir.exists():
        from send2trash import send2trash
        send2trash(str(pipeline.recipes_output_dir))
        logger.info("Moved old recipes directory to trash")
    
    pipeline.recipes_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate pages for each recipe
    for recipe in recipes:
        slug = recipe.get("slug")
        title = recipe.get("title")
        
        if not slug:
            logger.warning(f"Skipping recipe with missing slug: {title}")
            continue
        
        html_content = pipeline._generate_recipe_html(template_content, recipe)
        pipeline._save_recipe_page(slug, html_content)
        logger.info(f"✅ Baked: {title}")
    
    # Regenerate sitemap (recipes.json is already up to date)
    pipeline._generate_sitemap()
    
    return True


if __name__ == "__main__":
    main()
