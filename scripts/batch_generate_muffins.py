import bpy
import json
import os
import sys
import subprocess

# Configuration
JOBS_FILE = "/workspace/image_generation_jobs.json"
OUTPUT_ROOT = "/workspace/output/muffin_pan"
STEPS = 25
CFG_SCALE = 7.5

def setup_ai_render():
    """Enable and configure AI Render addon."""
    addon_name = "AI-Render"
    if addon_name not in bpy.context.preferences.addons:
        bpy.ops.preferences.addon_enable(module=addon_name)
    
    prefs = bpy.context.preferences.addons[addon_name].preferences
    # Load API key from environment variable
    prefs.dream_studio_api_key = os.getenv("STABILITY_API_KEY", "")
    
    scene = bpy.context.scene
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    
    scene.air_props.is_enabled = True
    scene.air_props.auto_run = True
    scene.air_props.steps = STEPS
    scene.air_props.cfg_scale = CFG_SCALE
    scene.air_props.do_autosave_after_images = True
    
    return True

def generate_variant(recipe_id, variant_name, prompt):
    """Generate a single image variant."""
    scene = bpy.context.scene
    scene.air_props.prompt_text = prompt
    
    output_dir = os.path.join(OUTPUT_ROOT, recipe_id)
    os.makedirs(output_dir, exist_ok=True)
    scene.air_props.autosave_image_path = output_dir
    
    print(f"🎨 Generating {recipe_id} [{variant_name}]...")
    
    # Trigger generation
    from importlib import import_module
    operators_module = import_module('AI-Render.operators')
    operators_module.do_pre_api_setup(scene)
    
    try:
        # CRITICAL: Perform a minimal render to populate 'Render Result'
        bpy.ops.render.render() 
        
        result = operators_module.sd_generate(scene)
        if result:
            print(f"   ✅ {variant_name} complete.")
            return True
        else:
            print(f"   ❌ {variant_name} failed.")
            return False
    except Exception as e:
        print(f"   ❌ Error generating {variant_name}: {e}")
        return False

def main():
    if not os.path.exists(JOBS_FILE):
        print(f"Not found: {JOBS_FILE}")
        return

    # Install requests if missing
    try:
        import requests
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    
    setup_ai_render()
    
    with open(JOBS_FILE, "r") as f:
        jobs = json.load(f)

    for job in jobs:
        recipe_id = job["recipe_id"]
        for variant, prompt in job["prompts"].items():
            generate_variant(recipe_id, variant, prompt)

if __name__ == "__main__":
    main()
