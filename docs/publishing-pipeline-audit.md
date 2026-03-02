# Publishing Pipeline Audit — Protected Paths

**Date:** 2026-03-02  
**Auditor:** Antigravity (Floor Manager)

## Protected Paths

| Path | Status |
|------|--------|
| `src/recipes/` | ✅ Protected |
| `src/recipes.json` | ✅ Protected |

## Audit: Who writes to `src/recipes/` and `src/recipes.json`?

```
$ grep -rn "src/recipes" backend/ --include="*.py" | grep -v "__pycache__"
backend/publishing/pipeline.py:6:  Convert to web format (src/recipes.json compatible)
backend/publishing/pipeline.py:96:  Load the src/recipes.json index file.
backend/publishing/pipeline.py:96:  """Save the recipes index back to src/recipes.json using atomic write.
backend/publishing/pipeline.py:143: Save recipe HTML to src/recipes/{slug}/index.html using atomic write.
backend/publishing/pipeline.py:163: Add or update a recipe in src/recipes.json.
backend/publishing/templates.py:48: Handle both dict format (from Recipe model) and string format (from src/recipes.json)
```

**Result:** Only `backend/publishing/pipeline.py` and `backend/publishing/templates.py` reference these paths. No other backend module writes to `src/recipes/`. ✅

## Enforcement Mechanisms

1. **`scripts/hooks/pre-commit`** — Blocks any `git commit` that stages files matching `^src/recipes/` or `^src/recipes\.json$` unless the commit message contains `[publish]` or starts with `publish:`.

2. **`scripts/hooks/commit-msg`** — Secondary check on the commit message for the same marker. Complements pre-commit since `--no-verify` on the latter still triggers commit-msg.

3. **Policy:** `--no-verify` is **FORBIDDEN**. No escape hatch is documented in the hook output.

## Install Hooks

After cloning, run:
```bash
bash scripts/install-hooks.sh
```

This copies both hooks from `scripts/hooks/` into `.git/hooks/` with executable permissions.
