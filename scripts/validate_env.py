import os
import sys
from pathlib import Path

def validate():
    print("--- 🧁 Muffin Pan Recipes: Environment Guard ---")
    
    missing = []
    warnings = []
    
    # 1. GOOGLE_API_KEY (Required for the Wednesday photography stage, #8068)
    if not os.getenv("GOOGLE_API_KEY"):
        missing.append("GOOGLE_API_KEY (Set in Doppler; run with `doppler run -- ...`)")

    # 1b. STABILITY_API_KEY (Optional; only scripts/direct_harvest.py uses it)
    if not os.getenv("STABILITY_API_KEY"):
        warnings.append("STABILITY_API_KEY not set (needed only for scripts/direct_harvest.py)")
    
    # 2. PROJECT_ROOT (Optional; scripts default to repo root when unset)
    project_root = os.getenv("PROJECT_ROOT")
    if not project_root:
        warnings.append("PROJECT_ROOT not set (using script-local default path)")
    elif not Path(project_root).exists():
        warnings.append(f"PROJECT_ROOT path does not exist: {project_root}")

    # 3. AI_ROUTER_PATH (Optional unless using scripts/generate_image_prompts.py)
    ai_router_path = os.getenv("AI_ROUTER_PATH")
    if not ai_router_path:
        warnings.append("AI_ROUTER_PATH not set (needed only for scripts/generate_image_prompts.py)")
    elif not Path(ai_router_path).exists():
        warnings.append(f"AI_ROUTER_PATH does not exist: {ai_router_path}")

    if missing:
        print("❌ STATUS: Missing")
        for item in missing:
            print(f"   - {item}")
        for item in warnings:
            print(f"   - WARN: {item}")
        print("   - Hint: this project is Doppler-managed; run commands via `doppler run -- <command>`")
        sys.exit(1)
    else:
        print("✅ STATUS: Ready")
        print(f"   - PROJECT_ROOT: {os.getenv('PROJECT_ROOT', 'Detected as ' + str(Path(__file__).parent.parent))}")
        for item in warnings:
            print(f"   - WARN: {item}")
        sys.exit(0)

if __name__ == "__main__":
    validate()
