"""
🧁 Muffin Pan Recipes: Mission Control Handshake Script

This script orchestrates the upload of generation jobs and helper scripts 
to Cloudflare R2 for execution on RunPod.

DEPENDENCIES:
- MissionControl class from the '3D Pose Factory' project.
  Ensure '3D Pose Factory' is a sibling directory or set POSE_FACTORY_SCRIPTS.
"""
import os
import sys
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add Mission Control to path
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent.parent))
POSE_FACTORY_SCRIPTS = os.getenv("POSE_FACTORY_SCRIPTS", str(PROJECT_ROOT.parent / "3D Pose Factory" / "shared" / "scripts"))

if POSE_FACTORY_SCRIPTS not in sys.path:
    sys.path.insert(0, POSE_FACTORY_SCRIPTS)

try:
    from mission_control import MissionControl  # type: ignore
except ImportError:
    print("❌ Error: Could not find 'mission_control.py'.")
    print(f"Looked in: {POSE_FACTORY_SCRIPTS}")
    sys.exit(1)

JOBS_FILE = PROJECT_ROOT / "data" / "image_generation_jobs.json"
R2_MUFFIN_PAN_JOBS_PATH = "muffin_pan/jobs/image_generation_jobs.json"
R2_MUFFIN_PAN_SCRIPTS_PATH = "muffin_pan/scripts/batch_generate_muffins.py"
R2_MUFFIN_PAN_DIRECT_HARVEST_PATH = "muffin_pan/scripts/direct_harvest.py"


def main():
    mc = MissionControl()

    logger.info("--- 🧁 Mission Control Handshake: Image Generation Batch ---")

    # 1. Upload the image_generation_jobs.json to R2
    if os.path.exists(JOBS_FILE):
        if mc.upload_to_r2(JOBS_FILE, R2_MUFFIN_PAN_JOBS_PATH, show_progress=True):
            logger.info("✅ Upload complete!")
        else:
            logger.error("❌ Failed to upload jobs file to R2.")
            sys.exit(1)
    else:
        logger.warning(f"⚠️ Jobs file not found at {JOBS_FILE}. Skipping upload.")

    # 2. Upload the batch generation script to R2
    batch_script_path = Path(__file__).parent / "batch_generate_muffins.py"
    if batch_script_path.exists():
        if mc.upload_to_r2(batch_script_path, R2_MUFFIN_PAN_SCRIPTS_PATH, show_progress=False):
            logger.info("✅ Batch generation script uploaded to R2.")
        else:
            logger.error("❌ Failed to upload batch generation script to R2.")
            sys.exit(1)
    
    # 3. Upload the direct harvest script to R2
    direct_harvest_script_path = Path(__file__).parent / "direct_harvest.py"
    if direct_harvest_script_path.exists():
        if mc.upload_to_r2(direct_harvest_script_path, R2_MUFFIN_PAN_DIRECT_HARVEST_PATH, show_progress=False):
            logger.info("✅ Direct harvest script uploaded to R2.")
        else:
            logger.error("❌ Failed to upload direct harvest script to R2.")
            sys.exit(1)

    logger.info("\n🚀 MISSION CONTROL HANDSHAKE SUCCESSFUL")
    logger.info("--------------------------------------------------")

if __name__ == "__main__":
    main()

