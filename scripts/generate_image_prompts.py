import os
import glob
import re

STYLE_GUIDE_PROMPT = "Professional food photography of {title} served in a rustic muffin tin. Bright, high-key lighting, natural daylight coming from the side. Soft shadows on a white marble countertop. Focus on the texture of the {texture}. Shot on 85mm macro lens, f/2.8. Editorial cookbook style, clean, minimalist, highly appetizing. No text, no people."

def extract_recipe_info(filepath):
    with open(filepath, 'r') as f:
        content = f.read()
        
    # Extract title
    title_match = re.search(r'title:\s*"(.*)"', content)
    title = title_match.group(1) if title_match else os.path.basename(filepath).replace('-', ' ').replace('.md', '').title()
    
    # Simple logic to guess "texture" focus based on title/content
    texture = "golden-brown crust"
    if "lasagna" in title.lower():
        texture = "melted mozzarella and crispy wonton edges"
    elif "egg" in title.lower() or "quiche" in title.lower():
        texture = "velvety set egg and crumbled feta"
    elif "blueberry" in title.lower():
        texture = "bursting blueberries and sugar-dusted dome"
    elif "oatmeal" in title.lower():
        texture = "toasted oats and fresh berries"
    elif "meatloaf" in title.lower():
        texture = "savory glazed crust"
    
    return title, texture

def main():
    recipe_files = glob.glob('data/recipes/*.md')
    print(f"--- Muffin Pan Recipe Image Prompt Generator ---")
    print(f"Found {len(recipe_files)} recipes.\n")
    
    for rf in sorted(recipe_files):
        title, texture = extract_recipe_info(rf)
        prompt = STYLE_GUIDE_PROMPT.format(title=title, texture=texture)
        print(f"RECIPE: {title}")
        print(f"PROMPT: {prompt}")
        print("-" * 20)

if __name__ == "__main__":
    main()

