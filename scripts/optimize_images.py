import os
import json
import shutil
import sys
import html
import re
import argparse
from pathlib import Path
from PIL import Image
from datetime import datetime

# Set up paths
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent.parent))
IMAGES_DIR = PROJECT_ROOT / "src" / "assets" / "images"
ARCHIVE_DIR = PROJECT_ROOT / "data" / "image_archive"

# Ensure archive directory exists
os.makedirs(ARCHIVE_DIR, exist_ok=True)

def safe_move_to_archive(src_path, dry_run=False):
    """Moves a file to the archive, appending a timestamp if it already exists."""
    dest_path = ARCHIVE_DIR / src_path.name
    if dest_path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        new_name = f"{src_path.stem}_{timestamp}{src_path.suffix}"
        dest_path = ARCHIVE_DIR / new_name
    
    if not dry_run:
        shutil.move(str(src_path), str(dest_path))
    return dest_path

def main():
    parser = argparse.ArgumentParser(
        description="Optimize images for the Muffin Pan Recipes site"
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be optimized without making changes"
    )
    
    args = parser.parse_args()
    
    total_png_size = 0
    total_webp_size = 0
    converted_count = 0
    skipped_count = 0

    print(f"--- 🧁 Muffin Pan Recipes: Image Optimizer ---")
    print(f"Scanning: {IMAGES_DIR}")
    
    if args.dry_run:
        print("🏜️ DRY-RUN MODE: No files will be modified")

    # Process each PNG file
    for image_path in IMAGES_DIR.glob("*.png"):
        try:
            webp_path = image_path.with_suffix(".webp")
            
            # IDEMPOTENCY: Skip if WebP already exists
            if webp_path.exists():
                print(f"ℹ️ Skipped: {image_path.name} (Already Optimized)")
                # Move the leftover PNG to archive anyway to keep production clean
                if not args.dry_run:
                    safe_move_to_archive(image_path, dry_run=args.dry_run)
                else:
                    print(f"[DRY-RUN] Would archive: {image_path.name}")
                skipped_count += 1
                continue

            # Track original size
            png_size = os.path.getsize(image_path)
            total_png_size += png_size
            
            # Open and convert to WebP
            img = Image.open(image_path)
            
            if args.dry_run:
                print(f"[DRY-RUN] Would convert: {image_path.name} (estimated ~{(png_size/1024):.1f}KB)")
                webp_size = int(png_size * 0.65)  # Estimate
                total_webp_size += webp_size
            else:
                img.save(webp_path, format="WEBP", quality=80)
                
                # Track new size
                webp_size = os.path.getsize(webp_path)
                total_webp_size += webp_size
                
                # Move original PNG to archive safely
                archived_path = safe_move_to_archive(image_path, dry_run=False)
                
                print(f"✅ Converted: {image_path.name} ({(png_size/1024):.1f}KB -> {(webp_size/1024):.1f}KB)")
                print(f"📦 Archived to: {archived_path.name}")
            
            converted_count += 1
            
        except Exception as e:
            print(f"❌ Error processing {image_path.name}: {str(e)}")
            sys.exit(1)

    if converted_count > 0 or skipped_count > 0:
        # Update recipes.json for consistency
        recipes_json = PROJECT_ROOT / "src" / "recipes.json"
        try:
            if recipes_json.exists():
                with open(recipes_json, "r") as f:
                    data = json.load(f)
                
                recipes_list = data.get("recipes", [])
                for recipe in recipes_list:
                    if "image" in recipe and isinstance(recipe["image"], str):
                        recipe["image"] = recipe["image"].replace(".png", ".webp")
                
                if not args.dry_run:
                    with open(recipes_json, "w") as f:
                        json.dump(data, f, indent=2)
                    print("✅ Successfully updated recipes.json references.")
                else:
                    print("[DRY-RUN] Would update recipes.json with WebP references")
        except Exception as e:
            print(f"❌ Error updating recipes.json: {str(e)}")
            sys.exit(1)

    # Calculate and print summary
    if converted_count > 0:
        space_saved = total_png_size - total_webp_size
        print(f"\n✨ Optimization {'Simulation' if args.dry_run else 'Complete'}!")
        print(f"📊 Would convert {converted_count} images." if args.dry_run else f"📊 Converted {converted_count} images.")
        print(f"📉 Total size reduction: {(space_saved/1024/1024):.2f} MB ({(space_saved/total_png_size*100):.1f}%)")
    elif skipped_count > 0:
        print(f"\n✨ {'Cleanup simulation' if args.dry_run else 'Cleanup complete'}: {skipped_count} original PNGs {'would be ' if args.dry_run else ''}moved to archive.")
    else:
        print("\n✨ No optimization needed (Production is clean).")

    sys.exit(0)

if __name__ == "__main__":
    main()
