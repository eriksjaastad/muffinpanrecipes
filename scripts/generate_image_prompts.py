import os
import glob
import re
import json
import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add AI Router to path
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent.parent))
AI_ROUTER_PATH = os.getenv("AI_ROUTER_PATH", str(PROJECT_ROOT.parent / "_tools" / "ai_router"))

if AI_ROUTER_PATH not in sys.path:
    sys.path.append(AI_ROUTER_PATH)

try:
    from router import AIRouter
except ImportError as e:
    import traceback
    traceback.print_exc()
    print(f"Error: Could not import AIRouter from {AI_ROUTER_PATH}: {e}")
    sys.exit(1)

# Configuration
RECIPE_DIR = PROJECT_ROOT / "data" / "recipes"
OUTPUT_JOBS_FILE = PROJECT_ROOT / "data" / "image_generation_jobs.json"
STYLE_GUIDE_PATH = PROJECT_ROOT / "Documents" / "core" / "IMAGE_STYLE_GUIDE.md"

def get_gemini_config():
    """Extract Gemini config from factory settings."""
    # Attempt to find factory settings in home directory
    settings_path = Path.home() / ".factory" / "settings.json"
    if settings_path.exists():
        with open(settings_path, 'r') as f:
            settings = json.load(f)
            for model in settings.get("customModels", []):
                if "gemini" in model.get("model", "").lower():
                    return model.get("apiKey"), model.get("baseUrl"), model.get("model")
    return None, None, None

def extract_recipe_details(filepath):
    """Extract title and key ingredients/description from recipe markdown."""
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Extract title from frontmatter
    title_match = re.search(r'title:\s*"(.*)"', content)
    title = title_match.group(1) if title_match else os.path.basename(filepath).replace('-', ' ').replace('.md', '').title()
    
    # Extract description
    desc_match = re.search(r'description:\s*"(.*)"', content)
    description = desc_match.group(1) if desc_match else ""
    
    # Extract ingredients (rough extraction)
    ingredients_section = re.search(r'## Ingredients(.*?)(?:##|$)', content, re.DOTALL)
    ingredients = ""
    if ingredients_section:
        ingredients = ingredients_section.group(1).strip()
    
    return {
        "id": os.path.basename(filepath).replace('.md', ''),
        "title": title,
        "description": description,
        "ingredients": ingredients
    }

def generate_triple_plate_prompts(router, recipe):
    """Generate 3 variant prompts using AI Router."""
    
    # System prompt for prompt engineering
    system_prompt = "You are an expert food photography prompt engineer. Your goal is to create highly detailed, appetizing image generation prompts for SDXL. Follow the provided Style Guide strictly."
    
    # Style guide context (briefly)
    style_guide_summary = """
    Aesthetic: Clean Kitchen Editorial. 
    Lighting: High-key, natural daylight from side, soft shadows.
    Background: White marble countertop.
    Props: Rustic muffin tin. 
    Camera: 85mm macro lens, f/2.8, shallow depth of field.
    Constraints: No people, no text, no clutter, no dark mode.
    """
    
    user_request = f"""
    Create a 'Triple-Plate' set of 3 SDXL prompts for the recipe: {recipe['title']}.
    Recipe Description: {recipe['description']}
    Ingredients: {recipe['ingredients']}
    
    Style Guide:
    {style_guide_summary}
    
    Required Variants:
    1. Editorial: Close-up, macro focus on the hero texture, high-end lighting.
    2. Action/Steam: Hot, fresh out of the tin, visible rising steam, captured in the moment.
    3. The Spread: Multiple pieces arranged elegantly on the marble surface, showing quantity and variety.
    
    Format your response as a JSON object with keys: 'editorial', 'action_steam', 'the_spread'.
    Only return the JSON.
    """
    
    # Route to local tier (using DeepSeek)
    result = router.chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request}
    ], tier="local")
    
    if result.error:
        logger.warning(f"  ⚠️ Local tier (DeepSeek) failed for {recipe['title']}: {result.error}. Attempting 'cheap' tier fallback.")
        # Fallback to cheap tier (e.g., gpt-4o-mini or similar) if local failed
        result = router.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_request}
        ], tier="cheap")
    
    if result.error:
        logger.error(f"  ❌ All AI tiers failed for {recipe['title']}: {result.error}")
        # Return generic fallback prompts so we don't crash the whole batch
        return {
            "editorial": f"Professional food photography of {recipe['title']} in a muffin tin, white marble, macro texture, 85mm, f/2.8",
            "action_steam": f"Professional food photography of hot {recipe['title']} with rising steam in a muffin tin, white marble, 85mm, f/2.8",
            "the_spread": f"Professional food photography of a spread of {recipe['title']} on a white marble surface, editorial style, 85mm, f/2.8"
        }
    
    try:
        # Extract JSON from response
        json_str = result.text.strip()
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()
            
        prompts = json.loads(json_str)
        return prompts
    except Exception as e:
        print(f"  ❌ Error parsing prompts for {recipe['title']}: {e}")
        return {
            "editorial": f"Professional food photography of {recipe['title']} in a muffin tin, white marble, macro texture, 85mm, f/2.8",
            "action_steam": f"Professional food photography of hot {recipe['title']} with rising steam in a muffin tin, white marble, 85mm, f/2.8",
            "the_spread": f"Professional food photography of a spread of {recipe['title']} on a white marble surface, editorial style, 85mm, f/2.8"
        }

def main():
    print("--- 🧁 Muffin Pan Recipes: Phase 3.1 Visual Harvest ---")
    
    # Use local DeepSeek for high quality local prompts
    print("💡 Using local DeepSeek-R1 (14b) for prompt generation.")
    router = AIRouter(local_model="deepseek-r1:14b", cheap_model="deepseek-r1:14b", expensive_model="deepseek-r1:14b")
    
    recipe_files = glob.glob(os.path.join(RECIPE_DIR, "*.md"))
    
    jobs = []
    
    for rf in sorted(recipe_files):
        recipe = extract_recipe_details(rf)
        logger.info(f"Generating prompts for: {recipe['title']}...")
        
        # Force local for everything to avoid cloud errors
        prompts = generate_triple_plate_prompts(router, recipe)
        
        job = {
            "recipe_id": recipe['id'],
            "recipe_title": recipe['title'],
            "prompts": prompts
        }
        jobs.append(job)
        print(f"  ✅ Done.")
    
    # Save jobs
    with open(OUTPUT_JOBS_FILE, 'w') as f:
        json.dump(jobs, f, indent=2)
    
    print(f"\n✨ Successfully generated prompts for {len(jobs)} recipes.")
    print(f"📁 Jobs saved to: {OUTPUT_JOBS_FILE}")

if __name__ == "__main__":
    main()
