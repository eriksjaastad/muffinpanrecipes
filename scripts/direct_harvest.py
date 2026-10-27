"""Legacy image harvest through OpenRouter's Gemini image model.

Run with ``doppler run -- uv run python scripts/direct_harvest.py`` so
``OPENROUTER_API_KEY`` is injected. The optional path overrides are
``WORKSPACE_ROOT`` (default: ``/workspace`` when present, otherwise the repo),
``JOBS_FILE`` (default: ``$WORKSPACE_ROOT/image_generation_jobs.json``), and
``OUTPUT_ROOT`` (default: ``$WORKSPACE_ROOT/output/muffin_pan``).
"""

from io import BytesIO
import json
import logging
import os
import sys
from pathlib import Path

from PIL import Image

from backend.utils.image_generation import generate_nano_banana_image

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
# Default to /workspace for RunPod, or local project root for local runs
WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", "/workspace" if os.path.exists("/workspace") else Path(__file__).parent.parent))
JOBS_FILE = os.getenv("JOBS_FILE", str(WORKSPACE_ROOT / "image_generation_jobs.json"))
OUTPUT_ROOT = os.getenv("OUTPUT_ROOT", str(WORKSPACE_ROOT / "output" / "muffin_pan"))
IMAGE_MODEL = "gemini-3.1-flash-image"


def _openrouter_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set (use `doppler run -- ...`)")
    return key


def _as_png(image_bytes: bytes) -> bytes:
    """Decode provider output and return actual PNG bytes for the .png path."""
    with Image.open(BytesIO(image_bytes)) as image:
        output = BytesIO()
        image.convert("RGB").save(output, format="PNG")
    return output.getvalue()


def generate_image(recipe_id, variant_name, prompt, api_key=None):
    output_dir = Path(OUTPUT_ROOT) / recipe_id
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{variant_name}.png"

    # SMART SKIPPING: Don't pay for the same image twice
    if file_path.exists():
        logger.info(f"Skipping: {recipe_id} [{variant_name}] (Already exists)")
        return True

    logger.info(f"Direct Harvest: {recipe_id} [{variant_name}]...")

    try:
        image_bytes = generate_nano_banana_image(
            prompt,
            api_key or _openrouter_api_key(),
            model=IMAGE_MODEL,
            aspect_ratio="1:1",
            image_size="1K",
            timeout_s=60,
        )
    except Exception as e:  # governance: allow-silent SF002: False is the failure signal; main() aborts the batch with exit 1 on any False
        logger.error(f"❌ OpenRouter image error for {recipe_id} [{variant_name}]: {e}")
        return False

    try:
        png_bytes = _as_png(image_bytes)
    except Exception as exc:  # noqa: BLE001 - a malformed provider image aborts this job
        logger.error(f"❌ Invalid image returned for {recipe_id} [{variant_name}]: {exc}")
        return False

    file_path.write_bytes(png_bytes)
    logger.info(f"✅ Saved to {file_path}")
    return True

def main():
    try:
        _openrouter_api_key()
    except RuntimeError as exc:
        sys.exit(f"❌ Error: {exc}")

    if not os.path.exists(JOBS_FILE):
        print(f"Not found: {JOBS_FILE}")
        return

    with open(JOBS_FILE, "r") as f:
        try:
            jobs = json.load(f)
        except Exception:
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
