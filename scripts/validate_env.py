import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def validate():
    print("--- 🧁 Muffin Pan Recipes: Environment Guard ---")
    
    missing = []
    
    # 1. STABILITY_API_KEY
    if not os.getenv("STABILITY_API_KEY"):
        missing.append("STABILITY_API_KEY (Set in .env or shell)")
    
    # 2. PROJECT_ROOT
    project_root = os.getenv("PROJECT_ROOT")
    if not project_root:
        missing.append("PROJECT_ROOT (Optional but recommended for portability)")
    elif not Path(project_root).exists():
        missing.append(f"PROJECT_ROOT (Path does not exist: {project_root})")

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

