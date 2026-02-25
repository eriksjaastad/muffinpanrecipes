#!/usr/bin/env python3
"""
Validate project structure and requirements.

Usage:
    ./scripts/validate_project.py [project_name]      # Check specific project
    ./scripts/validate_project.py --all               # Check all projects
    ./scripts/validate_project.py --missing           # List projects without indexes

This script enforces:
- Critical Rule #0: Every project must have an index file
- Index file naming: 00_Index_[ProjectName].md
- Index file location: project root
- Index file structure: Valid YAML frontmatter + required sections
"""

import sys
import os
from pathlib import Path
from typing import List, Tuple
import re
from scaffold.utils import safe_slug
from scaffold.alerts import send_discord_alert
from scaffold.constants import PROTECTED_PROJECTS

# Configuration
PROJECTS_ROOT_ENV = os.getenv("PROJECTS_ROOT")
if not PROJECTS_ROOT_ENV:
    # Fallback to standard layout: parent of scaffolding root
    PROJECTS_ROOT = Path(__file__).parent.parent.parent.resolve()
else:
    PROJECTS_ROOT = Path(PROJECTS_ROOT_ENV).resolve()

REQUIRED_INDEX_PATTERN = r"00_Index_.+\.md"
SKIP_DIRS = PROTECTED_PROJECTS

# Mandatory files and directories
MANDATORY_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursorignore",
    "TODO.md",
    "README.md",
    ".gitignore"
]
MANDATORY_DIRS = [
    "Documents"
]

# YAML frontmatter requirements
REQUIRED_TAGS = ["map/project", "p/"]  # p/ is a prefix that must exist
# Regex patterns for required sections (allows for emojis and slight variations)
REQUIRED_SECTION_PATTERNS = [
    (re.compile(r"^#\s+", re.MULTILINE), "H1 Title"),
    (re.compile(r"^##\s+.*(Key Components|Project Overview)", re.MULTILINE | re.IGNORECASE), "## Key Components"),
    (re.compile(r"^##\s+.*Status", re.MULTILINE | re.IGNORECASE), "## Status"),
]


class ValidationError(Exception):
    """Raised when project fails validation."""
    pass


def find_projects(root: Path) -> List[Path]:
    """Find all project directories (top-level folders)."""
    projects = []
    for item in root.iterdir():
        if item.is_dir() and not item.name.startswith((".", "_")):
            # Skip explicit directories
            if item.name in SKIP_DIRS:
                continue
            projects.append(item)
    return sorted(projects)


def has_index_file(project_path: Path) -> Tuple[bool, Path | None]:
    """Check if project has index file matching pattern."""
    for file in project_path.glob("00_Index_*.md"):
        return True, file
    return False, None


def validate_index_content(index_path: Path) -> List[str]:
    """Validate index file content. Returns list of errors."""
    errors = []
    content = index_path.read_text()
    
    # Check for YAML frontmatter
    if not content.startswith("---"):
        errors.append("Missing YAML frontmatter (must start with '---')")
        return errors  # Can't continue without frontmatter
    
    # Extract frontmatter
    try:
        parts = content.split("---", 2)
        if len(parts) < 3:
            errors.append("Invalid YAML frontmatter (must have closing '---')")
            return errors
        
        frontmatter = parts[1]
        body = parts[2]
    except Exception as e:
        errors.append(f"Failed to parse frontmatter: {e}")
        return errors
    
    # Check required tags
    for required_tag in REQUIRED_TAGS:
        if required_tag not in frontmatter:
            errors.append(f"Missing required tag: {required_tag}")
    
    # Check required sections
    for pattern, section_name in REQUIRED_SECTION_PATTERNS:
        if not pattern.search(body):
            errors.append(f"Missing required section: {section_name}")
    
    # Check for 3-sentence summary (heuristic: body should have substance before first ##)
    if "## Key Components" in body:
        summary_section = body.split("## Key Components")[0]
        # Should have H1 title and some content
        if summary_section.count("\n") < 5:
            errors.append("Summary section appears too short (need 3-sentence description)")
    
    return errors


def validate_dna_integrity(project_path: Path) -> List[str]:
    """Scan project for absolute paths and secrets. Returns list of errors."""
    errors = []
    
    # Patterns to catch absolute paths (using character class to avoid self-detection)
    path_pattern = re.compile(r"/[U]sers/[a-zA-Z0-9._-]+")
    # Patterns to catch common secrets (sk-, AIza, etc.)
    secret_pattern = re.compile(r"(sk-[a-zA-Z0-9]{32,}|AIza[a-zA-Z0-9_-]{35})")
    
    # Files to exclude from scan
    exclude_dirs = {
        ".git", "venv", ".venv", "__pycache__", "node_modules", "data",
        "library", ".mypy_cache", ".pytest_cache", ".ruff_cache",
        "htmlcov", ".tox", ".nox", ".cache", "logs", "recovered", "cursor_history",
        "entries", "insights"
    }
    
    for root, dirs, files in os.walk(project_path):
        # Filter directories in-place
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]
        
        for file in files:
            # Skip binary files, known safe files, generated files, and env files
            if file.endswith((".png", ".jpg", ".jpeg", ".pyc", ".db", ".zip", ".tar.gz", ".bak", ".xml", ".log", ".pdf", ".json", ".csv")) or \
               file in {".env", ".env.example", "full_repo_context.txt", "billing.error.log", "repomix-output.xml", "pandoc"}:
                continue
                
            file_path = Path(root) / file
            try:
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                
                # Check for absolute paths
                if path_pattern.search(content):
                    # Skip common intentional paths if any (e.g. journal protocol uses absolute paths)
                    journal_path_str = str(PROJECTS_ROOT / "ai-journal" / "entries")
                    if journal_path_str in content:
                        continue
                    # Skip AGENTS.md absolute paths (they are ecosystem-wide)
                    if file == "AGENTS.md":
                        continue
                    errors.append(f"DNA Defect: Absolute path found in {file_path.relative_to(project_path)}")
                
                # Check for secrets
                if secret_pattern.search(content):
                    errors.append(f"Security Defect: Potential secret found in {file_path.relative_to(project_path)}")
                    
            except Exception as e:
                # We log but don't fail the whole scan for one unreadable file
                errors.append(f"Scan Defect: Could not read file {file_path.relative_to(project_path)}: {e}")
                
    return errors


def validate_project(project_path: Path, verbose: bool = True) -> bool:
    """
    Validate a single project against the Master Compliance Checklist.
    
    Returns:
        True if valid, False otherwise
    """
    project_name = project_path.name
    errors = []
    
    # 1. Check for index file
    has_index, index_path = has_index_file(project_path)
    if not has_index:
        errors.append("Missing index file (00_Index_*.md)")
    else:
        # Validate index content
        index_errors = validate_index_content(index_path)
        errors.extend(index_errors)
    
    # 2. Check for mandatory files
    for filename in MANDATORY_FILES:
        if not (project_path / filename).exists():
            # Special case: check for README.md in Documents/ if not in root
            if filename == "README.md" and (project_path / "Documents" / "README.md").exists():
                continue
            errors.append(f"Missing mandatory file: {filename}")
            
    # 3. Check for mandatory directories
    for dirname in MANDATORY_DIRS:
        if not (project_path / dirname).is_dir():
            errors.append(f"Missing mandatory directory: {dirname}")
            
    # 4. DNA Integrity Scan (Automated Gate 0)
    dna_errors = validate_dna_integrity(project_path)
    errors.extend(dna_errors)
    
    # 5. Dangerous Command Scan (Automated Gate 1)
    # Check for banned functions like rm, shutil.rmtree, os.remove
    dangerous_patterns = [
        (r"\brm\s+", "rm command found - use 'trash <file>' instead"),
        (r"shutil\.rmtree\s*\(", "shutil.rmtree() found - use send2trash"),
        (r"os\.remove\s*\(", "os.remove() found - use send2trash"),
        (r"os\.unlink\s*\(", "os.unlink() found - use send2trash"),
    ]
    
    # Files to skip for safety scan
    safety_skip_files = {"validate_project.py", "warden_audit.py"}
    
    # 6. Placeholder Scan (Automated Gate 2)
    # Check for unfilled template placeholders: {{VAR}}
    placeholder_patterns = [
        (re.compile(r"\{\{[A-Z0-9_]+\}\}"), "Unfilled double-brace placeholder"),
    ]
    
    # Intentional placeholders that are allowed to remain (e.g. in documentation or examples)
    ALLOWED_PLACEHOLDERS = {
        "{{RECIPE_ID}}",
        "{{BACKGROUND}}",
        "{{PRIMARY}}",
        "{{SECONDARY}}",
        "{{ACCENT}}",
        "{{PLACEHOLDER}}"
    }
    
    # Files/directories to skip for placeholder scan
    placeholder_skip_files = {
        "SILENT_FAILURES_AUDIT.md",
        "TODO_FORMAT_STANDARD.md",
        "REVIEWS_AND_GOVERNANCE_PROTOCOL.md",
        "validate_project.py",
        "cli.py"
    }
    placeholder_skip_dirs = {"templates", "_handoff", "prompts"}
    
    for root, dirs, files in os.walk(project_path):
        # Filter directories in-place
        dirs[:] = [d for d in dirs if d not in {"venv", ".venv", "__pycache__", "node_modules", ".git"}]
        
        rel_root = Path(root).relative_to(project_path)
        is_in_skip_dir = any(part in placeholder_skip_dirs for part in rel_root.parts)
        
        for file in files:
            # Check placeholders in Markdown, Python, and Shell scripts
            if not file.endswith((".md", ".py", ".sh", ".js", ".ts")):
                continue
            
            # Skip index files for placeholder check (they pull from other files)
            if file.startswith("00_Index_") and file.endswith(".md"):
                is_placeholder_skip_file = True
            else:
                is_placeholder_skip_file = file in placeholder_skip_files
                
            file_path = Path(root) / file
            rel_file_path = file_path.relative_to(project_path)
            
            try:
                content = file_path.read_text(encoding='utf-8', errors='ignore')
                
                # Check for dangerous patterns (skip if in skip list)
                if file not in safety_skip_files:
                    for pattern, reason in dangerous_patterns:
                        if re.search(pattern, content):
                            errors.append(f"Safety Defect: {reason} in {rel_file_path}")
                
                # Check for unfilled placeholders (skip if in skip list)
                if not is_in_skip_dir and not is_placeholder_skip_file:
                    lines = content.splitlines()
                    for i, line in enumerate(lines):
                        for pattern, reason in placeholder_patterns:
                            match = pattern.search(line)
                            if match:
                                placeholder = match.group(0)
                                if placeholder in ALLOWED_PLACEHOLDERS:
                                    continue
                                
                                # Special case: ignore some common single-brace patterns that aren't placeholders
                                # e.g. f-strings in python or shell variables if they look like placeholders
                                if file.endswith(".py") and ("f\"" in line or "f'" in line):
                                    continue
                                
                                errors.append(f"Placeholder Defect: {reason} found in {rel_file_path}:{i+1} - {match.group(0)}")
            except Exception:
                pass

    if errors:
        if verbose:
            status_icon = "⚠️ " if has_index else "❌ "
            print(f"{status_icon} {project_name}")
            for error in errors:
                print(f"   - {error}")
        
        # Send Discord alert for validation failure
        msg = f"❌ **Project Validation Failed** for: `{project_name}`\n"
        msg += "\n".join(f"- {e}" for e in errors[:10])
        if len(errors) > 10:
            msg += f"\n... and {len(errors) - 10} more errors."
        send_discord_alert(msg)
        
        return False
    
    # All good!
    if verbose:
        print(f"✅ {project_name} (Fully Compliant)")
    return True


def main() -> None:
    """Main validation logic."""
    if len(sys.argv) < 2 or sys.argv[1] in ["--help", "-h"]:
        print("Usage:")
        print("  ./scripts/validate_project.py [project_name]  # Check specific project")
        print("  ./scripts/validate_project.py --all           # Check all projects")
        print("  ./scripts/validate_project.py --missing       # List missing indexes")
        sys.exit(0 if len(sys.argv) > 1 else 1)
    
    arg = sys.argv[1]
    
    if arg == "--all":
        # Validate all projects
        print("Validating all projects...\n")
        projects = find_projects(PROJECTS_ROOT)
        
        valid_count = 0
        invalid_count = 0
        
        for project in projects:
            is_valid = validate_project(project, verbose=True)
            if is_valid:
                valid_count += 1
            else:
                invalid_count += 1
            print()  # Blank line between projects
        
        # Summary
        total = valid_count + invalid_count
        print(f"{'='*60}")
        print(f"Summary: {valid_count}/{total} projects valid ({invalid_count} need attention)")
        
        if invalid_count > 0:
            print(f"\n⚠️  {invalid_count} projects need index files or fixes")
            print("Run with --missing to see which projects need indexes")
            sys.exit(1)
        else:
            print("\n✅ All projects have valid index files!")
            sys.exit(0)
    
    elif arg == "--missing":
        # List projects without indexes
        print("Projects missing index files:\n")
        projects = find_projects(PROJECTS_ROOT)
        
        missing = []
        for project in projects:
            has_index, _ = has_index_file(project)
            if not has_index:
                missing.append(project.name)
        
        if missing:
            for name in missing:
                print(f"  - {name}")
            
            print(f"\n{len(missing)} projects need index files")
            print("\nTo create indexes:")
            print("  ./scripts/reindex_projects.py --missing")
        else:
            print("✅ All projects have index files!")
        
        sys.exit(len(missing))  # Exit code = number of missing
    
    else:
        # Validate specific project
        # First try the raw name
        project_path = (PROJECTS_ROOT / arg).resolve()
        if not project_path.exists() or not project_path.is_dir():
            # Fallback to slugged name
            project_name = safe_slug(arg)
            project_path = (PROJECTS_ROOT / project_name).resolve()
        
        # Security: Ensure path stays within PROJECTS_ROOT
        if not project_path.is_relative_to(PROJECTS_ROOT):
            print(f"❌ Security Alert: Path traversal detected for {arg}")
            sys.exit(1)
            
        if not project_path.exists():
            print(f"❌ Project not found: {arg}")
            print(f"   Expected: {project_path}")
            sys.exit(1)
        
        if not project_path.is_dir():
            print(f"❌ Not a directory: {arg}")
            sys.exit(1)
        
        print(f"Validating: {project_path.name}\n")
        is_valid = validate_project(project_path, verbose=True)
        
        if not is_valid:
            print(f"\n❌ Validation failed for {project_path.name}")
            sys.exit(1)
        else:
            print(f"\n✅ {project_path.name} is valid!")
            sys.exit(0)


if __name__ == "__main__":
    main()

