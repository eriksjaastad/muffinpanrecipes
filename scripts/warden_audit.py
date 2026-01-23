import pathlib
import logging
import sys
import subprocess
import shutil
from enum import Enum

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

class Severity(Enum):
    P0 = "CRITICAL"  # Block commit - dangerous functions in production
    P1 = "ERROR"     # Block commit - hardcoded paths
    P2 = "WARNING"   # Allow commit - acceptable with context
    P3 = "INFO"      # Allow commit - informational only

def is_tier_1_project(index_path: pathlib.Path) -> bool:
    """
    Determines if the given markdown index file represents a Tier 1 (Full Stack/Code) project.
    """
    if not index_path.exists():
        return False
    
    tech_languages = {'python', 'javascript', 'java', 'c++', 'ruby', 'php', 'typescript', 'rust', 'go'}
    
    try:
        with index_path.open('r') as f:
            content = f.read()
            content_lower = content.lower()
            
        # 1. Explicit tags
        if '#type/code' in content_lower or '#type/project' in content_lower:
            return True
            
        if any(f'tech/{lang}' in content_lower for lang in tech_languages):
            return True

        # 2. Check headers and list items in the first 50 lines
        lines = content.split('\n')
        for line in lines[:50]:
            line_strip = line.strip().lower()
            if not line_strip:
                continue
                
            # If it's a header or a list item
            if line_strip.startswith('#') or line_strip.startswith('- ') or line_strip.startswith('* '):
                if any(lang in line_strip for lang in tech_languages):
                    return True
                    
        return False
        
    except Exception as e:
        logger.error(f"Error reading file {index_path}: {e}")
        return False

def check_dependencies(project_root: pathlib.Path) -> bool:
    """Checks if a Tier 1 project has a dependency manifest."""
    manifests = ['requirements.txt', 'package.json', 'pyproject.toml', 'setup.py']
    for manifest in manifests:
        if (project_root / manifest).exists():
            return True
    return False


def check_doc_ratio(project_root: pathlib.Path) -> tuple:
    """Check documentation to code ratio.

    Returns: (ratio, severity) where severity is None if healthy, P2 if warning, P1 if critical
    """
    code_extensions = ['*.py', '*.js', '*.ts', '*.jsx', '*.tsx', '*.go', '*.rs', '*.java', '*.rb']
    skip_dirs = ['venv', 'node_modules', '.git', '__pycache__', 'archives', '_trash']

    code_lines = 0
    doc_lines = 0

    # Count code lines
    for ext in code_extensions:
        for file_path in project_root.rglob(ext):
            if any(part in file_path.parts for part in skip_dirs):
                continue
            try:
                with file_path.open('r', errors='ignore') as f:
                    code_lines += sum(1 for _ in f)
            except Exception:
                pass

    # Count doc lines
    for file_path in project_root.rglob('*.md'):
        if any(part in file_path.parts for part in skip_dirs):
            continue
        try:
            with file_path.open('r', errors='ignore') as f:
                doc_lines += sum(1 for _ in f)
        except Exception:
            pass

    # Avoid division by zero - if no code, docs are fine
    if code_lines == 0:
        return (0.0, None)

    ratio = doc_lines / code_lines

    if ratio > 5.0:
        return (ratio, Severity.P1)  # Extreme bloat - likely an error
    elif ratio > 0.5:
        return (ratio, Severity.P2)  # Warning - docs > 50% of code
    elif ratio > 0.2:
        return (ratio, Severity.P2)  # Warning - docs 20-50% of code
    else:
        return (ratio, None)  # Healthy

def check_dangerous_functions(project_root: pathlib.Path) -> list:
    """Greps for dangerous file removal functions.
    
    Returns: List of (file_path, pattern, severity) tuples
    """
    dangerous_patterns = [
        'os.remove', 'os.unlink', 'shutil.rmtree',  # Dangerous functions
        '/Us' + 'ers/', '/ho' + 'me/',  # macOS/Linux absolute paths
        'C:\\' + '\\', 'C:/'       # Windows absolute paths
    ]
    found_issues = []
    
    # Simple walk and check to avoid external dependency for basic audit
    for file_path in project_root.rglob('*.py'):
        # Skip certain directories
        if any(part in file_path.parts for part in ['venv', 'node_modules', '.git', '__pycache__']):
            continue
            
        if file_path.name == 'warden_audit.py': # Exclude self
            continue

        # Determine if test file
        is_test_file = 'test' in file_path.parts or file_path.name.startswith('test_')

        try:
            with file_path.open('r') as f:
                content = f.read()
                for pattern in dangerous_patterns:
                    if pattern in content:
                        is_hardcoded_path = pattern in ['/Us' + 'ers/', '/ho' + 'me/', 'C:\\' + '\\', 'C:/']
                        if is_hardcoded_path:
                            severity = Severity.P1
                        elif pattern in ['os.remove', 'os.unlink', 'shutil.rmtree']:
                            severity = Severity.P2 if is_test_file else Severity.P0
                        else:
                            severity = Severity.P3
                        found_issues.append((file_path, pattern, severity))
        except Exception as e:
            logger.warning(f"Could not read file {file_path}: {e}")
            found_issues.append((file_path, f"READ_ERROR: {e}", Severity.P3))
            
    return found_issues

def check_dangerous_functions_fast(project_root: pathlib.Path) -> list:
    """Fast grep-based scanner for pre-commit hooks.

    Uses ripgrep (rg) or grep for sub-second performance.
    Falls back to regular scan if grep not available.
    """
    # Check if ripgrep or grep available
    grep_cmd = 'rg' if shutil.which('rg') else 'grep' if shutil.which('grep') else None

    if grep_cmd is None:
        logger.warning("grep/ripgrep not found, falling back to regular scan")
        return check_dangerous_functions(project_root)

    dangerous_patterns = [
        'os.remove', 'os.unlink', 'shutil.rmtree',
        '/Us' + 'ers/', '/ho' + 'me/',
        'C:\\' + '\\', 'C:/'
    ]
    found_issues = []

    for pattern in dangerous_patterns:
        try:
            if grep_cmd == 'rg':
                # ripgrep: --type py, -l (files with matches)
                cmd = ['rg', '--type', 'py', '-l', pattern, str(project_root)]
            else:
                # grep: -r (recursive), -l (files), --include (Python files)
                cmd = ['grep', '-r', '-l', '--include=*.py', pattern, str(project_root)]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=False)

            if result.returncode == 0:  # Matches found
                for file_path in result.stdout.strip().split('\n'):
                    if file_path:  # Skip empty lines
                        path_obj = pathlib.Path(file_path)
                        if path_obj.name == 'warden_audit.py':
                            continue
                        
                        is_test_file = 'test' in path_obj.parts or path_obj.name.startswith('test_')
                        is_hardcoded_path = pattern in ['/Us' + 'ers/', '/ho' + 'me/', 'C:\\' + '\\', 'C:/']
                        
                        if is_hardcoded_path:
                            severity = Severity.P1
                        elif pattern in ['os.remove', 'os.unlink', 'shutil.rmtree']:
                            severity = Severity.P2 if is_test_file else Severity.P0
                        else:
                            severity = Severity.P3
                            
                        found_issues.append((path_obj, pattern, severity))

        except subprocess.TimeoutExpired:
            logger.warning(f"Fast scan timeout for pattern: {pattern}")
        except Exception as e:
            logger.warning(f"Fast scan error: {e}")

    return found_issues

def run_audit(root_dir: pathlib.Path, use_fast: bool = False) -> bool:
    """Crawls the ecosystem and performs the audit."""
    logger.info(f"Starting Warden Audit in: {root_dir}")
    
    projects_found = 0
    p0_issues = 0  # Critical
    p1_issues = 0  # Error
    p2_issues = 0  # Warning
    
    # Find all project roots by looking for 00_Index_*.md files
    for index_path in root_dir.rglob('00_Index_*.md'):
        # Skip indices in templates or archives
        if any(part in index_path.parts for part in ['templates', 'archives', 'venv', '.git']):
            continue
            
        projects_found += 1
        project_root = index_path.parent
        project_name = project_root.name
        
        is_tier_1 = is_tier_1_project(index_path)
        tier_label = "Tier 1 (Code)" if is_tier_1 else "Tier 2 (Other)"
        
        logger.info(f"Auditing Project: {project_name} [{tier_label}]")
        
        # Tier 1 Dependency Check
        if is_tier_1:
            if not check_dependencies(project_root):
                logger.warning(f"[P2-WARNING] {project_name}: Missing dependency manifest")
                p2_issues += 1

        # Documentation Hygiene Check (All Tiers)
        doc_ratio, doc_severity = check_doc_ratio(project_root)
        if doc_severity == Severity.P1:
            logger.error(f"[P1-ERROR] {project_name}: Doc bloat critical - docs are {doc_ratio:.0%} of codebase (>50%)")
            p1_issues += 1
        elif doc_severity == Severity.P2:
            logger.warning(f"[P2-WARNING] {project_name}: Doc ratio high - docs are {doc_ratio:.0%} of codebase (>20%)")
            p2_issues += 1

        # Safety Check (All Tiers)
        dangerous_usage = check_dangerous_functions_fast(project_root) if use_fast else check_dangerous_functions(project_root)
        for file_path, pattern, severity in dangerous_usage:
            try:
                rel_path = file_path.relative_to(root_dir)
            except ValueError:
                rel_path = file_path
            
            severity_label = f"[{severity.name}-{severity.value}]"
            if severity == Severity.P0:
                logger.error(f"{severity_label} {project_name}: '{pattern}' found in {rel_path}")
                p0_issues += 1
            elif severity == Severity.P1:
                logger.error(f"{severity_label} {project_name}: '{pattern}' found in {rel_path}")
                p1_issues += 1
            elif severity == Severity.P2:
                logger.warning(f"{severity_label} {project_name}: '{pattern}' found in {rel_path}")
                p2_issues += 1
            else:
                logger.info(f"{severity_label} {project_name}: '{pattern}' in {rel_path}")
                    
    logger.info("--- Audit Summary ---")
    logger.info(f"Projects scanned: {projects_found}")
    logger.info(f"P0 (Critical): {p0_issues}")
    logger.info(f"P1 (Error): {p1_issues}")
    logger.info(f"P2 (Warning): {p2_issues}")
    
    # Exit clean only if no P0 or P1 issues
    return (p0_issues == 0 and p1_issues == 0)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Warden Audit Agent - Phase 1")
    parser.add_argument("--root", default=".", help="Root directory to scan (default: .)")
    parser.add_argument("--fast", action="store_true",
                       help="Fast scan mode for pre-commit hooks (<1s target)")
    args = parser.parse_args()
    
    # Standardize to pathlib.Path and relative path if possible
    root_path = pathlib.Path(args.root).resolve()
    try:
        root_path = root_path.relative_to(pathlib.Path.cwd())
    except ValueError:
        pass # Keep absolute if not under CWD, but preference is relative
        
    success = run_audit(root_path, use_fast=args.fast)
    sys.exit(0 if success else 1)
