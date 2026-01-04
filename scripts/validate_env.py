import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def validate():
    print("--- 🧁 Muffin Pan Recipes: Environment Guard ---")
    
    missing = []
    
    # 1. STABILITY_API_KEY (Required for direct_harvest.py)
    if not os.getenv("STABILITY_API_KEY"):
        missing.append("STABILITY_API_KEY (Set in .env or shell)")
    
    # 2. PROJECT_ROOT (Required for all scripts)
    project_root = os.getenv("PROJECT_ROOT")
    if not project_root:
        missing.append("PROJECT_ROOT (Required for absolute path resolution)")
    elif not Path(project_root).exists():
        missing.append(f"PROJECT_ROOT (Path does not exist: {project_root})")

    # 3. AI_ROUTER_PATH (Required for generate_image_prompts.py and art_director.py)
    ai_router_path = os.getenv("AI_ROUTER_PATH")
    if not ai_router_path:
        missing.append("AI_ROUTER_PATH (Required for AI Router integration)")
    elif not Path(ai_router_path).exists():
        missing.append(f"AI_ROUTER_PATH (Path does not exist: {ai_router_path})")

    # 4. POSE_FACTORY_SCRIPTS (Required for trigger_generation.py)
    pose_factory_path = os.getenv("POSE_FACTORY_SCRIPTS")
    if not pose_factory_path:
        missing.append("POSE_FACTORY_SCRIPTS (Required for Mission Control integration)")
    elif not Path(pose_factory_path).exists():
        missing.append(f"POSE_FACTORY_SCRIPTS (Path does not exist: {pose_factory_path})")

    if missing:
        print("❌ STATUS: Missing")
        for item in missing:
            print(f"   - {item}")
        sys.exit(1)
    else:
        print("✅ STATUS: Ready")
        print(f"   - PROJECT_ROOT: {os.getenv('PROJECT_ROOT', 'Detected as ' + str(Path(__file__).parent.parent))}")
        sys.exit(0)

if __name__ == "__main__":
    validate()

