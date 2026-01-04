import os
import requests
import json
import base64
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

# Configuration
# Default to /workspace for RunPod, or local project root for local runs
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace" if os.path.exists("/workspace") else Path(__file__).parent.parent))
JOBS_FILE = os.getenv("JOBS_FILE", str(WORKSPACE_ROOT / "image_generation_jobs.json"))
OUTPUT_ROOT = os.getenv("OUTPUT_ROOT", str(WORKSPACE_ROOT / "output" / "muffin_pan"))
API_KEY = os.getenv("STABILITY_API_KEY")
if not API_KEY:
    sys.exit("❌ Error: STABILITY_API_KEY not set in .env")

ENGINE_ID = "stable-diffusion-xl-1024-v1-0" 

def generate_image(recipe_id, variant_name, prompt):
    output_dir = Path(OUTPUT_ROOT) / recipe_id
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{variant_name}.png"

    # SMART SKIPPING: Don't pay for the same image twice
    if file_path.exists():
        logger.info(f"Skipping: {recipe_id} [{variant_name}] (Already exists)")
        return True

    logger.info(f"Direct Harvest: {recipe_id} [{variant_name}]...")
    
    url = f"https://api.stability.ai/v1/generation/{ENGINE_ID}/text-to-image"
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }
    
    body = {
        "text_prompts": [{"text": prompt}],
        "cfg_scale": 7,
        "height": 1024,
        "width": 1024,
        "samples": 1,
        "steps": 30,
    }

    try:
        response = requests.post(url, headers=headers, json=body, timeout=60)
    except requests.exceptions.Timeout:
        logger.error(f"❌ Timeout error generating {recipe_id} [{variant_name}] after 60 seconds.")
        return False
    except Exception as e:
        logger.error(f"❌ Request error for {recipe_id} [{variant_name}]: {e}")
        return False

    if response.status_code != 200:
        logger.error(f"❌ Failed: {response.text}")
        return False

    data = response.json()
    
    for i, image in enumerate(data["artifacts"]):
        with open(file_path, "wb") as f:
            f.write(base64.b64decode(image["base64"]))
        logger.info(f"✅ Saved to {file_path}")
    
    return True

def main():
    if not os.path.exists(JOBS_FILE):
        print(f"Not found: {JOBS_FILE}")
        return

    with open(JOBS_FILE, "r") as f:
        try:
            jobs = json.load(f)
        except Exception as e:
            f.seek(0)
            content = f.read()
            jobs = json.loads(content)

    for job in jobs:
        recipe_id = job["recipe_id"]
        for variant, prompt in job["prompts"].items():
            if not generate_image(recipe_id, variant, prompt):
                logger.error(f"❌ Batch aborted due to failure in {recipe_id} [{variant}]")
                sys.exit(1)

if __name__ == "__main__":
    main()
