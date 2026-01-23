"""
Publishing pipeline for transforming approved recipes into live site content.

This module handles the full publishing workflow:
1. Load approved recipe from Recipe model
2. Convert to web format (src/recipes.json compatible)
3. Generate HTML page from template
4. Update site index and sitemap
5. Git commit and push to trigger Vercel deployment
6. Update recipe status to PUBLISHED
"""

import os
import json
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from backend.data.recipe import Recipe, RecipeStatus
from backend.publishing.templates import render_recipe_page, generate_json_ld
from backend.utils.logging import get_logger
from backend.utils.discord import notify_recipe_ready

logger = get_logger(__name__)


class PublishingPipeline:
    """
    Main publishing pipeline for converting approved recipes to live site content.
    
    Supports both single-recipe incremental publishing and full site rebuilds.
    """
    
    def __init__(
        self,
        project_root: Optional[Path] = None,
        auto_commit: bool = True,
        auto_push: bool = True
    ):
        """
        Initialize the publishing pipeline.
        
        Args:
            project_root: Project root directory (defaults to PROJECT_ROOT env var)
            auto_commit: Whether to automatically git commit after publishing
            auto_push: Whether to automatically git push after committing
        """
        self.project_root = Path(project_root) if project_root else Path(
            os.getenv("PROJECT_ROOT", Path(__file__).parent.parent.parent)
        )
        self.auto_commit = auto_commit
        self.auto_push = auto_push
        
        # Define key paths
        self.src_dir = self.project_root / "src"
        self.recipes_json_path = self.src_dir / "recipes.json"
        self.template_path = self.src_dir / "templates" / "recipe_page.html"
        self.recipes_output_dir = self.src_dir / "recipes"
        self.sitemap_path = self.src_dir / "sitemap.xml"
        self.data_dir = self.project_root / "data" / "recipes"
        
        logger.info(f"PublishingPipeline initialized: {self.project_root}")
    
    def _load_template(self) -> str:
        """Load the recipe page template."""
        if not self.template_path.exists():
            raise FileNotFoundError(f"Template not found: {self.template_path}")
        
        with open(self.template_path, "r") as f:
            return f.read()
    
    def _load_recipes_index(self) -> Dict[str, Any]:
        """
        Load the src/recipes.json index file.
        
        Returns:
            Dictionary with 'recipes' key containing list of recipe data
        """
        if not self.recipes_json_path.exists():
            logger.warning(f"recipes.json not found, creating new one")
            return {"recipes": []}
        
        with open(self.recipes_json_path, "r") as f:
            data = json.load(f)
            # Handle both list and object structures
            if isinstance(data, dict) and "recipes" in data:
                return data
            elif isinstance(data, list):
                return {"recipes": data}
            else:
                return {"recipes": []}
    
    def _save_recipes_index(self, data: Dict[str, Any]) -> None:
        """Save the recipes index back to src/recipes.json."""
        with open(self.recipes_json_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Updated recipes index: {len(data['recipes'])} recipes")
    
    def _recipe_to_web_format(self, recipe: Recipe) -> Dict[str, Any]:
        """
        Convert Recipe model to web format (src/recipes.json compatible).
        
        Args:
            recipe: Recipe model instance
            
        Returns:
            Dictionary compatible with src/recipes.json format
        """
        # Convert ingredients to simple string format for web display
        ingredients_list = []
        for ing in recipe.ingredients:
            if isinstance(ing, dict):
                amount = ing.get("amount", "")
                item = ing.get("item", "")
                ingredients_list.append(f"{amount} {item}" if amount else item)
            else:
                ingredients_list.append(str(ing))
        
        return {
            "slug": recipe.slug,
            "title": recipe.title,
            "category": recipe.category,
            "image": recipe.featured_photo or (recipe.photos[0] if recipe.photos else ""),
            "description": recipe.description,
            "prep": f"{recipe.prep_time_minutes} mins",
            "cook": f"{recipe.cook_time_minutes} mins",
            "yield": f"{recipe.servings} portions",
            "ingredients": ingredients_list,
            "instructions": recipe.instructions
        }
    
    def _generate_recipe_html(
        self,
        template_content: str,
        recipe_data: Dict[str, Any]
    ) -> str:
        """Generate HTML for a single recipe."""
        return render_recipe_page(template_content, recipe_data)
    
    def _save_recipe_page(self, slug: str, html_content: str) -> Path:
        """
        Save recipe HTML to src/recipes/{slug}/index.html.
        
        Args:
            slug: Recipe slug
            html_content: Rendered HTML content
            
        Returns:
            Path to saved file
        """
        recipe_dir = self.recipes_output_dir / slug
        recipe_dir.mkdir(parents=True, exist_ok=True)
        
        output_file = recipe_dir / "index.html"
        with open(output_file, "w") as f:
            f.write(html_content)
        
        logger.info(f"Saved recipe page: {output_file}")
        return output_file
    
    def _update_recipes_index(self, recipe_data: Dict[str, Any]) -> None:
        """
        Add or update a recipe in src/recipes.json.
        
        Args:
            recipe_data: Recipe data in web format
        """
        index = self._load_recipes_index()
        
        # Check if recipe already exists (update) or is new (append)
        existing_idx = None
        for idx, r in enumerate(index["recipes"]):
            if r.get("slug") == recipe_data["slug"]:
                existing_idx = idx
                break
        
        if existing_idx is not None:
            index["recipes"][existing_idx] = recipe_data
            logger.info(f"Updated existing recipe in index: {recipe_data['slug']}")
        else:
            index["recipes"].append(recipe_data)
            logger.info(f"Added new recipe to index: {recipe_data['slug']}")
        
        self._save_recipes_index(index)
    
    def _generate_sitemap(self) -> None:
        """Generate sitemap.xml with all published recipes."""
        index = self._load_recipes_index()
        
        today = datetime.now().strftime("%Y-%m-%d")
        sitemap_content = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
            '  <url>',
            '    <loc>https://muffinpanrecipes.com/</loc>',
            f'    <lastmod>{today}</lastmod>',
            '    <priority>1.0</priority>',
            '  </url>'
        ]
        
        for recipe in index["recipes"]:
            slug = recipe.get("slug")
            if slug:
                sitemap_content.extend([
                    '  <url>',
                    f'    <loc>https://muffinpanrecipes.com/recipes/{slug}</loc>',
                    f'    <lastmod>{today}</lastmod>',
                    '    <priority>0.8</priority>',
                    '  </url>'
                ])
        
        sitemap_content.append('</urlset>')
        
        with open(self.sitemap_path, "w") as f:
            f.write("\n".join(sitemap_content))
        
        logger.info(f"Generated sitemap with {len(index['recipes']) + 1} URLs")
    
    def _git_commit_and_push(self, recipe_title: str) -> bool:
        """
        Create git commit and optionally push.
        
        Args:
            recipe_title: Recipe title for commit message
            
        Returns:
            True if successful
        """
        if not self.auto_commit:
            logger.info("Auto-commit disabled, skipping git operations")
            return True
        
        try:
            # Stage all changes in src/
            subprocess.run(
                ["git", "add", "src/"],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )
            
            # Commit
            commit_msg = f"Publish recipe: {recipe_title}"
            subprocess.run(
                ["git", "commit", "-m", commit_msg],
                cwd=self.project_root,
                check=True,
                capture_output=True
            )
            logger.info(f"Git commit created: {commit_msg}")
            
            # Push if enabled
            if self.auto_push:
                subprocess.run(
                    ["git", "push"],
                    cwd=self.project_root,
                    check=True,
                    capture_output=True
                )
                logger.info("Git push successful - Vercel deployment triggered")
            
            return True
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Git operation failed: {e.stderr.decode() if e.stderr else str(e)}")
            return False
    
    def publish_recipe(self, recipe_id: str, send_notification: bool = True) -> bool:
        """
        Publish a single approved recipe to the live site.
        
        This is the main entry point for incremental publishing.
        
        Args:
            recipe_id: Recipe ID to publish
            send_notification: Whether to send Discord notification
            
        Returns:
            True if publishing successful
        """
        logger.info(f"🚀 Starting publish for recipe: {recipe_id}")
        
        try:
            # 1. Load the recipe
            recipe = self._load_recipe(recipe_id)
            
            # Verify status is approved
            if recipe.status != RecipeStatus.APPROVED:
                logger.error(f"Recipe {recipe_id} is not approved (status: {recipe.status.value})")
                return False
            
            # 2. Load template
            template_content = self._load_template()
            
            # 3. Convert recipe to web format
            recipe_data = self._recipe_to_web_format(recipe)
            
            # 4. Generate HTML page
            html_content = self._generate_recipe_html(template_content, recipe_data)
            
            # 5. Save HTML page
            self._save_recipe_page(recipe.slug, html_content)
            
            # 6. Update recipes index
            self._update_recipes_index(recipe_data)
            
            # 7. Regenerate sitemap
            self._generate_sitemap()
            
            # 8. Update recipe status to PUBLISHED
            recipe.transition_status(
                RecipeStatus.PUBLISHED,
                self.data_dir,
                notes="Published to live site"
            )
            
            # 9. Git commit and push
            if not self._git_commit_and_push(recipe.title):
                logger.warning("Git operations failed, but recipe was published locally")
            
            # 10. Send notification
            if send_notification:
                notify_recipe_ready(
                    recipe_title=recipe.title,
                    recipe_id=recipe.recipe_id,
                    description_preview=recipe.description,
                    ingredient_count=len(recipe.ingredients)
                )
            
            logger.info(f"✅ Successfully published: {recipe.title}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to publish recipe {recipe_id}: {e}", exc_info=True)
            return False
    
    def publish_all_approved(self, send_notifications: bool = True) -> Dict[str, Any]:
        """
        Publish all approved recipes in batch.
        
        Args:
            send_notifications: Whether to send Discord notifications
            
        Returns:
            Dictionary with success/failure counts and details
        """
        logger.info("🚀 Starting batch publish of all approved recipes")
        
        # Load all approved recipes
        approved_recipes = Recipe.list_by_status(self.data_dir, RecipeStatus.APPROVED)
        
        if not approved_recipes:
            logger.info("No approved recipes to publish")
            return {"success": 0, "failed": 0, "total": 0, "recipes": []}
        
        results = {
            "success": 0,
            "failed": 0,
            "total": len(approved_recipes),
            "recipes": []
        }
        
        for recipe in approved_recipes:
            success = self.publish_recipe(
                recipe.recipe_id,
                send_notification=send_notifications
            )
            
            if success:
                results["success"] += 1
                results["recipes"].append({
                    "recipe_id": recipe.recipe_id,
                    "title": recipe.title,
                    "status": "published"
                })
            else:
                results["failed"] += 1
                results["recipes"].append({
                    "recipe_id": recipe.recipe_id,
                    "title": recipe.title,
                    "status": "failed"
                })
        
        logger.info(
            f"✅ Batch publish complete: {results['success']} published, "
            f"{results['failed']} failed out of {results['total']} total"
        )
        
        return results
    
    def rebuild_site(self) -> bool:
        """
        Full site rebuild - regenerate all published recipes.
        
        This loads all recipes with PUBLISHED status and regenerates their pages.
        Useful for template changes or fixing consistency issues.
        
        Returns:
            True if successful
        """
        logger.info("🔨 Starting full site rebuild")
        
        try:
            # Load template
            template_content = self._load_template()
            
            # Load all published recipes
            published_recipes = Recipe.list_by_status(self.data_dir, RecipeStatus.PUBLISHED)
            
            if not published_recipes:
                logger.warning("No published recipes found")
                return True
            
            # Clear and rebuild recipes directory
            if self.recipes_output_dir.exists():
                import shutil
                try:
                    from send2trash import send2trash
                    send2trash(str(self.recipes_output_dir))
                    logger.info("Moved old recipes directory to trash")
                except ImportError:
                    shutil.rmtree(self.recipes_output_dir)
                    logger.info("Deleted old recipes directory")
            
            self.recipes_output_dir.mkdir(parents=True, exist_ok=True)
            
            # Rebuild index
            index = {"recipes": []}
            
            # Process each recipe
            for recipe in published_recipes:
                recipe_data = self._recipe_to_web_format(recipe)
                html_content = self._generate_recipe_html(template_content, recipe_data)
                self._save_recipe_page(recipe.slug, html_content)
                index["recipes"].append(recipe_data)
            
            # Save index
            self._save_recipes_index(index)
            
            # Regenerate sitemap
            self._generate_sitemap()
            
            logger.info(f"✅ Site rebuild complete: {len(published_recipes)} recipes")
            return True
            
        except Exception as e:
            logger.error(f"❌ Site rebuild failed: {e}", exc_info=True)
            return False
    
    def _load_recipe(self, recipe_id: str) -> Recipe:
        """
        Load a recipe by ID, checking all status directories.
        
        Args:
            recipe_id: Recipe ID to load
            
        Returns:
            Recipe instance
            
        Raises:
            FileNotFoundError if recipe not found
        """
        # Try each status directory
        for status in RecipeStatus:
            filepath = self.data_dir / status.value / f"{recipe_id}.json"
            if filepath.exists():
                return Recipe.load_from_file(filepath)
        
        raise FileNotFoundError(f"Recipe not found: {recipe_id}")
