import os
import sys
from pathlib import Path

# Ensure the Mission Control script is in the Python path
# This path is relative to the projects/ directory where all related repos live.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "3D Pose Factory" / "shared" / "scripts"))
try:
    from mission_control import MissionControl  # type: ignore
except ImportError:
    print("❌ Error: Could not find 'mission_control.py'.")
    print(f"Looked in: {Path(__file__).parent.parent.parent / '3D Pose Factory' / 'shared' / 'scripts'}")
    sys.exit(1)

JOBS_FILE = Path(__file__).parent.parent / "data" / "image_generation_jobs.json"
R2_MUFFIN_PAN_JOBS_PATH = "muffin_pan/jobs/image_generation_jobs.json"
R2_MUFFIN_PAN_SCRIPTS_PATH = "muffin_pan/scripts/batch_generate_muffins.py"
R2_MUFFIN_PAN_DIRECT_HARVEST_PATH = "muffin_pan/scripts/direct_harvest.py"


def main():
    mc = MissionControl()

    print("--- 🧁 Mission Control Handshake: Image Generation Batch ---")

    # 1. Upload the image_generation_jobs.json to R2
    if os.path.exists(JOBS_FILE):
        if mc.upload_to_r2(JOBS_FILE, R2_MUFFIN_PAN_JOBS_PATH, show_progress=True):
            print("✅ Upload complete!")
        else:
            print("❌ Failed to upload jobs file to R2.")
            sys.exit(1)
    else:
        print(f"⚠️ Jobs file not found at {JOBS_FILE}. Skipping upload.")

    # 2. Upload the batch generation script to R2
    batch_script_path = Path(__file__).parent / "batch_generate_muffins.py"
    if batch_script_path.exists():
        if mc.upload_to_r2(batch_script_path, R2_MUFFIN_PAN_SCRIPTS_PATH, show_progress=False):
            print("✅ Batch generation script uploaded to R2.")
        else:
            print("❌ Failed to upload batch generation script to R2.")
            sys.exit(1)
    
    # 3. Upload the direct harvest script to R2
    direct_harvest_script_path = Path(__file__).parent / "direct_harvest.py"
    if direct_harvest_script_path.exists():
        if mc.upload_to_r2(direct_harvest_script_path, R2_MUFFIN_PAN_DIRECT_HARVEST_PATH, show_progress=False):
            print("✅ Direct harvest script uploaded to R2.")
        else:
            print("❌ Failed to upload direct harvest script to R2.")
            sys.exit(1)

    print("\n🚀 MISSION CONTROL HANDSHAKE SUCCESSFUL")
    print("--------------------------------------------------")

if __name__ == "__main__":
    main()

