"""
🧁 Muffin Pan Recipes: Mission Control Handshake Script

This script orchestrates the upload of generation jobs and helper scripts 
to Cloudflare R2 for execution on RunPod.
"""
import os
import sys
import logging
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).parent.parent))
JOBS_FILE = PROJECT_ROOT / "data" / "image_generation_jobs.json"
R2_MUFFIN_PAN_JOBS_PATH = "muffin_pan/jobs/image_generation_jobs.json"
R2_MUFFIN_PAN_DIRECT_HARVEST_PATH = "muffin_pan/scripts/direct_harvest.py"

R2_ACCESS_KEY_ID = os.getenv("MUFFINPANRECIPES_R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("MUFFINPANRECIPES_R2_SECRET_ACCESS_KEY")
R2_ENDPOINT = os.getenv("MUFFINPANRECIPES_R2_ENDPOINT")
R2_BUCKET_NAME = os.getenv("MUFFINPANRECIPES_R2_BUCKET_NAME")


def _require_r2_env() -> None:
    missing: list[str] = []
    if not R2_ACCESS_KEY_ID:
        missing.append("MUFFINPANRECIPES_R2_ACCESS_KEY_ID")
    if not R2_SECRET_ACCESS_KEY:
        missing.append("MUFFINPANRECIPES_R2_SECRET_ACCESS_KEY")
    if not R2_ENDPOINT:
        missing.append("MUFFINPANRECIPES_R2_ENDPOINT")
    if not R2_BUCKET_NAME:
        missing.append("MUFFINPANRECIPES_R2_BUCKET_NAME")
    if missing:
        logger.error("❌ Missing R2 environment variables: %s", ", ".join(missing))
        logger.error("   Run via Doppler with these secrets configured.")
        sys.exit(1)


def _upload_to_r2(local_path: Path, r2_key: str) -> bool:
    try:
        client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            region_name="auto",
        )
        client.upload_file(str(local_path), R2_BUCKET_NAME, r2_key)
        return True
    except (ClientError, BotoCoreError) as exc:
        logger.error("❌ R2 upload failed for %s -> %s: %s", local_path, r2_key, exc)
        return False


def main():
    _require_r2_env()

    logger.info("--- 🧁 Mission Control Handshake: Image Generation Batch ---")

    # 1. Upload the image_generation_jobs.json to R2
    if os.path.exists(JOBS_FILE):
        if _upload_to_r2(JOBS_FILE, R2_MUFFIN_PAN_JOBS_PATH):
            logger.info("✅ Upload complete!")
        else:
            logger.error("❌ Failed to upload jobs file to R2.")
            sys.exit(1)
    else:
        logger.warning(f"⚠️ Jobs file not found at {JOBS_FILE}. Skipping upload.")

    # 2. Upload the direct harvest script to R2
    direct_harvest_script_path = Path(__file__).parent / "direct_harvest.py"
    if direct_harvest_script_path.exists():
        if _upload_to_r2(direct_harvest_script_path, R2_MUFFIN_PAN_DIRECT_HARVEST_PATH):
            logger.info("✅ Direct harvest script uploaded to R2.")
        else:
            logger.error("❌ Failed to upload direct harvest script to R2.")
            sys.exit(1)

    logger.info("\n🚀 MISSION CONTROL HANDSHAKE SUCCESSFUL")
    logger.info("--------------------------------------------------")

if __name__ == "__main__":
    main()
