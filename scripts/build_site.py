import os
import sys
import json
import logging
import shutil
from pathlib import Path
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    # Get project root path
    project_root = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent.parent))
    logger.info(f"--- 🧁 Muffin Pan Recipes: Site Builder ---")
    logger.info(f"Project root: {project_root}")

    # Define paths
    src_dir = project_root / "src"
    recipes_json_path = src_dir / "recipes.json"
    template_path = src_dir / "templates" / "recipe_page.html"
    recipes_output_dir = src_dir / "recipes"

    # 1. Clean Slate: Clear existing recipes directory
    try:
        if recipes_output_dir.exists():
            shutil.rmtree(recipes_output_dir)
            logger.info(f"Clean Slate: Removed old recipes directory.")
        recipes_output_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"❌ Failed to clear recipes directory: {e}")
        sys.exit(1)

    # 2. Load Data
    try:
        if not recipes_json_path.exists():
            logger.error(f"❌ recipes.json not found at {recipes_json_path}")
            sys.exit(1)
        with open(recipes_json_path, "r") as f:
            data = json.load(f)
            # Handle both list and object structures
            recipes = data.get("recipes", data) if isinstance(data, dict) else data
        logger.info(f"Loaded {len(recipes)} recipes.")
    except Exception as e:
        logger.error(f"❌ Failed to load recipes: {e}")
        sys.exit(1)

    # 3. Load Template
    try:
        if not template_path.exists():
            logger.error(f"❌ Template not found at {template_path}")
            sys.exit(1)
        with open(template_path, "r") as f:
            template_content = f.read()
    except Exception as e:
        logger.error(f"❌ Failed to load template: {e}")
        sys.exit(1)

    # 4. Bake Recipes
    generated_urls = []
    for recipe in recipes:
        slug = recipe.get("slug")
        title = recipe.get("title")
        
        if not slug:
            logger.warning(f"⚠️ Skipping recipe with missing slug: {title}")
            continue

        try:
            recipe_dir = recipes_output_dir / slug
            recipe_dir.mkdir(parents=True, exist_ok=True)

            # Pre-render lists
            ingredients_html = "\n".join([f'<li class="border-b border-gray-50 pb-2">{i}</li>' for i in recipe.get("ingredients", [])])
            instructions_html = "\n".join([
                f'<li class="flex gap-4"><span class="font-serif text-terracotta font-bold italic text-xl">{(idx + 1):02d}</span><span>{step}</span></li>'
                for idx, step in enumerate(recipe.get("instructions", []))
            ])

            # Pre-render JSON-LD
            json_ld = {
                "@context": "https://schema.org/",
                "@type": "Recipe",
                "name": title,
                "description": recipe.get("description"),
                "prepTime": "PT" + recipe.get("prep", "0 mins").replace(' mins', 'M'),
                "cookTime": "PT" + recipe.get("cook", "0 mins").replace(' mins', 'M'),
                "recipeYield": recipe.get("yield"),
                "recipeCategory": recipe.get("category"),
                "recipeIngredient": recipe.get("ingredients"),
                "recipeInstructions": [{"@type": "HowToStep", "text": i} for i in recipe.get("instructions", [])]
            }
            json_ld_script = f'<script type="application/ld+json">{json.dumps(json_ld)}</script>'

            # Inject data
            page_content = template_content
            replacements = {
                "{{ slug }}": slug,
                "{{ title }}": title,
                "{{ description }}": recipe.get("description", ""),
                "{{ image_path }}": recipe.get("image", ""),
                "{{ category }}": recipe.get("category", ""),
                "{{ prep_time }}": recipe.get("prep", ""),
                "{{ cook_time }}": recipe.get("cook", ""),
                "{{ yield }}": recipe.get("yield", ""),
                "{{ ingredients_list }}": ingredients_html,
                "{{ instructions_list }}": instructions_html,
                "{{ json_ld }}": json_ld_script
            }

            for placeholder, value in replacements.items():
                page_content = page_content.replace(placeholder, str(value))

            # Save page
            output_file = recipe_dir / "index.html"
            with open(output_file, "w") as f:
                f.write(page_content)
            
            generated_urls.append(f"recipes/{slug}")
            logger.info(f"✅ Baked: {title}")

        except Exception as e:
            logger.error(f"❌ Failed to bake {title}: {e}")
            sys.exit(1)

    # 5. Generate Sitemap
    try:
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

        for path in generated_urls:
            sitemap_content.extend([
                '  <url>',
                f'    <loc>https://muffinpanrecipes.com/{path}</loc>',
                f'    <lastmod>{today}</lastmod>',
                '    <priority>0.8</priority>',
                '  </url>'
            ])

        sitemap_content.append('</urlset>')
        
        sitemap_path = src_dir / "sitemap.xml"
        with open(sitemap_path, "w") as f:
            f.write("\n".join(sitemap_content))
        logger.info(f"✅ Generated sitemap with {len(generated_urls) + 1} URLs.")

    except Exception as e:
        logger.error(f"❌ Failed to generate sitemap: {e}")
        sys.exit(1)

    logger.info(f"\n✨ SITE BUILD SUCCESSFUL")
    logger.info(f"--------------------------------------------------")

if __name__ == "__main__":
    main()

