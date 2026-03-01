#!/usr/bin/env python3
import os
import json
import sys
import shutil
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
STAGING_DIR = PROJECT_ROOT / "__temp_harvest"
FINAL_IMAGE_DIR = PROJECT_ROOT / "src" / "assets" / "images"
TRASH_DIR = PROJECT_ROOT / "_trash"
STYLE_GUIDE_PATH = PROJECT_ROOT / "Documents" / "core" / "IMAGE_STYLE_GUIDE.md"
INDEX_HTML_PATH = PROJECT_ROOT / "src" / "index.html"

def pick_winner_metadata(recipe_id, recipe_title, variant_metadata):
    """
    Uses AI Router to pick the best variant based on the STYLE GUIDE 
    by comparing the generated descriptions/metadata.
    """
    router = AIRouter(expensive_model="gpt-5.1")
    
    with open(STYLE_GUIDE_PATH, 'r') as f:
        style_guide = f.read()

    system_prompt = f"""
    You are the 'Art Director' for Muffin Pan Recipes. 
    Your job is to select the BEST image from 3 generated variants based on the Style Guide.
    
    Style Guide:
    {style_guide}
    """

    user_request = f"""
    Recipe: {recipe_title}
    
    Variant Descriptions (based on generated prompts):
    1. Editorial: {variant_metadata['editorial']}
    2. Action/Steam: {variant_metadata['action_steam']}
    3. The Spread: {variant_metadata['the_spread']}
    
    Which variant best represents the 'Clean Kitchen Editorial' look defined in the Style Guide?
    Consider lighting, composition, and "appetizing texture."
    
    Respond with ONLY the name of the winning variant: 'editorial', 'action_steam', or 'the_spread'.
    """

    result = router.chat([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_request}
    ], tier="expensive")

    winner = result.text.strip().lower()
    if winner not in ['editorial', 'action_steam', 'the_spread']:
        # Fallback to editorial as it's the most high-end
        winner = 'editorial'
    
    return winner

def main():
    logger.info("--- 🧁 Muffin Pan Recipes: The Art Director Agent ---")
    
    # Create directories if they don't exist
    FINAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)

    # Load the prompts we used
    JOBS_FILE = PROJECT_ROOT / "data" / "image_generation_jobs.json"
    if not os.path.exists(JOBS_FILE):
        print(f"Error: Jobs file not found at {JOBS_FILE}")
        return

    with open(JOBS_FILE, 'r') as f:
        jobs = json.load(f)

    for job in jobs:
        recipe_id = job['recipe_id']
        recipe_title = job['recipe_title']
        
        print(f"Reviewing images for: {recipe_title}...")
        
        # 1. AI Decision (based on Style Guide)
        winner_variant = pick_winner_metadata(recipe_id, recipe_title, job['prompts'])
        print(f"  🏆 Winner: {winner_variant}")
        
        # 2. File Operations
        recipe_output_dir = STAGING_DIR / recipe_id
        if recipe_output_dir.exists():
            for img_file in recipe_output_dir.glob("*"):
                variant_name = img_file.stem
                ext = img_file.suffix.lower().replace('.', '')
                
                if variant_name == winner_variant:
                    # Move to final
                    dest = FINAL_IMAGE_DIR / f"{recipe_id}.{ext}"
                    try:
                        shutil.move(str(img_file), str(dest))
                        logger.info(f"  ✅ Integrated: {dest.name}")
                    except Exception as e:
                        logger.error(f"  ❌ CRITICAL ERROR: Could not move {img_file.name} to {dest}: {e}")
                        sys.exit(1)
                else:
                    # Move to trash
                    # Clean up the filename to avoid "File name too long" errors
                    clean_name = f"{recipe_id}_{variant_name}.{ext}"
                    trash_dest = TRASH_DIR / clean_name
                    try:
                        shutil.move(str(img_file), str(trash_dest))
                    except Exception as e:
                        logger.error(f"  ❌ CRITICAL ERROR: Could not move {img_file.name} to trash: {e}")
                        sys.exit(1)
        else:
            logger.warning(f"  ⚠️ No images found in {recipe_output_dir}. Skipping movement.")

    logger.info("\n✨ Art Director processing complete.")

if __name__ == "__main__":
    main()
