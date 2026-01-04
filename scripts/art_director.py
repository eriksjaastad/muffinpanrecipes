#!/usr/bin/env python3
import os
import json
import sys
import shutil
from pathlib import Path

# Add AI Router to path
AI_ROUTER_PATH = "/Users/eriksjaastad/projects/_tools/ai_router"
if AI_ROUTER_PATH not in sys.path:
    sys.path.append(AI_ROUTER_PATH)

try:
    from router import AIRouter
except ImportError:
    print(f"Error: Could not import AIRouter from {AI_ROUTER_PATH}")
    sys.exit(1)

# Configuration
STAGING_DIR = Path("/Users/eriksjaastad/projects/muffinpanrecipes/data/output")
FINAL_IMAGE_DIR = Path("/Users/eriksjaastad/projects/muffinpanrecipes/src/assets/images")
TRASH_DIR = Path("/Users/eriksjaastad/projects/muffinpanrecipes/_trash")
STYLE_GUIDE_PATH = Path("/Users/eriksjaastad/projects/muffinpanrecipes/Documents/core/IMAGE_STYLE_GUIDE.md")
INDEX_HTML_PATH = Path("/Users/eriksjaastad/projects/muffinpanrecipes/src/index.html")

def pick_winner_metadata(recipe_id, recipe_title, variant_metadata):
    """
    Uses AI Router to pick the best variant based on the STYLE GUIDE 
    by comparing the generated descriptions/metadata.
    
    (Note: In a full vision setup, we would pass the actual image base64)
    """
    router = AIRouter(expensive_model="gpt-4o")
    
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

def update_index_html(recipe_id, winner_ext):
    """Updates the hardcoded image path in index.html for the given recipe."""
    with open(INDEX_HTML_PATH, 'r') as f:
        content = f.read()
    
    # Update the image path in the recipes array
    # Pattern looks for the slug and then the image line
    pattern = rf'(slug:\s*"{recipe_id}".*?image:\s*")([^"]*)(")'
    new_image_path = f"assets/images/{recipe_id}.{winner_ext}"
    
    new_content = re.sub(pattern, rf'\1{new_image_path}\3', content, flags=re.DOTALL)
    
    with open(INDEX_HTML_PATH, 'w') as f:
        f.write(new_content)

def main():
    print("--- 🧁 Muffin Pan Recipes: The Art Director Agent ---")
    
    # Create directories if they don't exist
    FINAL_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)

    # Load the prompts we used
    JOBS_FILE = "/Users/eriksjaastad/projects/muffinpanrecipes/data/image_generation_jobs.json"
    with open(JOBS_FILE, 'r') as f:
        jobs = json.load(f)

    for job in jobs:
        recipe_id = job['recipe_id']
        recipe_title = job['recipe_title']
        
        print(f"Reviewing images for: {recipe_title}...")
        
        # 1. AI Decision (based on Style Guide)
        winner_variant = pick_winner_metadata(recipe_id, recipe_title, job['prompts'])
        print(f"  🏆 Winner: {winner_variant}")
        
        # 2. File Operations (Simulated: In real use, files would be in data/output/{recipe_id}/)
        recipe_output_dir = STAGING_DIR / recipe_id
        if recipe_output_dir.exists():
            for img_file in recipe_output_dir.glob("*"):
                variant_name = img_file.stem
                ext = img_file.suffix.lower().replace('.', '')
                
                if variant_name == winner_variant:
                    # Move to final
                    dest = FINAL_IMAGE_DIR / f"{recipe_id}.{ext}"
                    shutil.move(str(img_file), str(dest))
                    print(f"  ✅ Integrated: {dest.name}")
                    # update_index_html(recipe_id, ext)
                else:
                    # Move to trash
                    trash_dest = TRASH_DIR / f"{recipe_id}_{variant_name}.{ext}"
                    shutil.move(str(img_file), str(trash_dest))
        else:
            print(f"  ⚠️ No images found in {recipe_output_dir}. Skipping movement.")

    print("\n✨ Art Director processing complete.")

if __name__ == "__main__":
    import re
    main()

