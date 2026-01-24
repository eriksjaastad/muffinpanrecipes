# Validation Fixes for muffinpanrecipes

**Created:** 2026-01-23
**Priority:** HIGH
**Context:** `uv run scripts/validate_project.py muffinpanrecipes` is failing

---

## Issues to Fix

### 1. Missing Index Sections

**File:** `00_Index_MuffinPanRecipes.md`

Add these required sections if missing:

```markdown
## Key Components

- `backend/` - FastAPI admin dashboard and AI agent orchestration
- `src/` - Static site (recipes, HTML, CSS)
- `scripts/` - Build and deployment tools
- `data/` - Recipe storage by status (pending, approved, published, rejected)

## Status

**Current Phase:** Infrastructure implementation
**Last Updated:** 2026-01-23
**Next Steps:** Complete admin dashboard testing, newsletter setup
```

---

### 2. Placeholder in TODO.md - FALSE POSITIVE

**File:** `TODO.md`
**Line:** 86
**Pattern:** `{RECIPE_ID}`

**Actual content:**
```markdown
- [x] **Save recipes to JSON** - Persist to `data/recipes/pending/{RECIPE_ID}.json`
```

**This is NOT garbage.** It's a path template in documentation describing the file naming convention. This is intentional.

**Action:** The validator is being too aggressive. This should be excluded OR the validator needs smarter detection that ignores placeholders inside code blocks and path examples.

**For now:** Leave it alone. This is a validator false positive, not a project defect.

---

### 3. Safety Defects - shutil.rmtree() as Fallback

The code TRIES to use `send2trash` but falls back to `shutil.rmtree()` if the import fails.

**File 1:** `scripts/build_site.py` (lines 101-105)
```python
try:
    send2trash(str(pipeline.recipes_output_dir))
    logger.info("Moved old recipes directory to trash")
except ImportError:
    shutil.rmtree(self.recipes_output_dir)  # <-- DANGEROUS FALLBACK
    logger.info("Deleted old recipes directory")
```

**File 2:** `backend/publishing/pipeline.py` (lines 420-424)
Same pattern - tries send2trash, falls back to shutil.rmtree.

**The Problem:** If `send2trash` isn't installed, the code silently becomes dangerous.

**The Fix:** Make `send2trash` a hard requirement. Remove the fallback entirely:

```python
from send2trash import send2trash  # No try/except - fail if not installed

# Later in code:
send2trash(str(self.recipes_output_dir))
logger.info("Moved old recipes directory to trash")
```

**Also:** Ensure `send2trash` is in `pyproject.toml` or `requirements.txt` as a required dependency, not optional.

---

## Acceptance Criteria

- [ ] `00_Index_MuffinPanRecipes.md` has `## Key Components` and `## Status` sections
- [ ] `send2trash` is a hard dependency (in pyproject.toml/requirements.txt)
- [ ] No `shutil.rmtree()` fallback in `scripts/build_site.py`
- [ ] No `shutil.rmtree()` fallback in `backend/publishing/pipeline.py`
- [ ] `uv run scripts/validate_project.py muffinpanrecipes` passes

**Note on tests:** Test files may legitimately use `shutil.rmtree()` for cleanup. The validator should (and does) treat test files differently.

**Note on `{RECIPE_ID}`:** This is a false positive. If the validator keeps flagging it, we need to fix the validator, not the TODO.md.

---

## Verification

After fixes, run from project-scaffolding:

```bash
cd /path/to/project-scaffolding
uv run scripts/validate_project.py muffinpanrecipes
```

Or if you have `$PROJECTS_ROOT` set:

```bash
uv run $PROJECTS_ROOT/project-scaffolding/scripts/validate_project.py muffinpanrecipes
```

Expected output: `✅ muffinpanrecipes (Fully Compliant)`

---

## Why This Matters

The fallback pattern `try: send2trash() except ImportError: shutil.rmtree()` is sneaky. It looks safe but becomes dangerous if the dependency isn't installed. Better to fail loudly than delete silently.
