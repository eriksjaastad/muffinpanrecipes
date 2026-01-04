import os
import json
import shutil
import sys
from pathlib import Path
from PIL import Image

# Set up paths
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent.parent))
IMAGES_DIR = PROJECT_ROOT / "src" / "assets" / "images"
TRASH_DIR = PROJECT_ROOT / "_trash" / "original_pngs"

# Ensure trash directory exists
os.makedirs(TRASH_DIR, exist_ok=True)

total_png_size = 0
total_webp_size = 0
converted_count = 0

print(f"--- 🧁 Muffin Pan Recipes: Image Optimizer ---")
print(f"Scanning: {IMAGES_DIR}")

# Process each PNG file
for image_path in IMAGES_DIR.glob("*.png"):
    try:
        # Track original size
        png_size = os.path.getsize(image_path)
        total_png_size += png_size
        
        # Open and convert to WebP
        img = Image.open(image_path)
        webp_path = image_path.with_suffix(".webp")
        img.save(webp_path, format="WEBP", quality=80)
        
        # Track new size
        webp_size = os.path.getsize(webp_path)
        total_webp_size += webp_size
        
        # Move original PNG to trash
        dest_path = TRASH_DIR / image_path.name
        shutil.move(str(image_path), str(dest_path))
        
        converted_count += 1
        print(f"✅ Converted: {image_path.name} ({(png_size/1024):.1f}KB -> {(webp_size/1024):.1f}KB)")
    except Exception as e:
        print(f"❌ Error processing {image_path.name}: {str(e)}")
        sys.exit(1)

if converted_count == 0:
    print("ℹ️ No .png files found in assets. Checking recipes.json for consistency...")
else:
    # Update recipes.json
    recipes_json = PROJECT_ROOT / "src" / "recipes.json"
    try:
        if recipes_json.exists():
            with open(recipes_json, "r") as f:
                data = json.load(f)
            
            # The structure is {"recipes": [...]} based on previous refactor
            recipes_list = data.get("recipes", [])
            for recipe in recipes_list:
                if "image" in recipe and isinstance(recipe["image"], str):
                    recipe["image"] = recipe["image"].replace(".png", ".webp")
            
            with open(recipes_json, "w") as f:
                json.dump(data, f, indent=2)
            
            print("✅ Successfully updated recipes.json references.")
        else:
            print(f"⚠️ Warning: {recipes_json} not found. Skipping JSON update.")
    except Exception as e:
        print(f"❌ Error updating recipes.json: {str(e)}")
        sys.exit(1)

# Calculate and print summary
if total_png_size > 0:
    space_saved = total_png_size - total_webp_size
    print(f"\n✨ Optimization Complete!")
    print(f"📊 Converted {converted_count} images.")
    print(f"📉 Total size before: {(total_png_size/1024/1024):.2f} MB")
    print(f"📈 Total size after:  {(total_webp_size/1024/1024):.2f} MB")
    print(f"🎉 Space saved:       {(space_saved/1024/1024):.2f} MB ({(space_saved/total_png_size*100):.1f}%)")
else:
    print("\n✨ No optimization needed.")

sys.exit(0)

