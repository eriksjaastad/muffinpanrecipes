# Dependency Audit — #5813

Total direct: 15 | KEEP: 13 | REMOVE: 0 | INVESTIGATE: 2 | TRANSITIVE-ONLY: 0

Report-only. No dependency removed in this pass. Evidence-first classification of the 15 runtime direct deps in `[project.dependencies]`. Grep trumps deptry per the card plan.

## Classification Table

| # | Dep | Status | Rationale | Evidence |
|---|-----|--------|-----------|----------|
| 1 | pydantic | KEEP | `BaseModel` used across backend models | `rg '\bpydantic\b' --glob '*.py'` → backend/data/*, backend/memory/*, backend/admin/* |
| 2 | hypothesis | INVESTIGATE | Test-only framework; belongs in dev extras, not runtime. Deptry flagged DEP002. | `rg '\bhypothesis\b' tests/` → tests/test_agent_properties.py, tests/test_agent_behaviors.py, tests/conftest.py |
| 3 | pytest | INVESTIGATE | Test-only framework; belongs in dev extras, not runtime. Deptry flagged DEP002. | `rg '\bpytest\b' tests/` → 60+ hits across tests/ |
| 4 | httpx | KEEP | HTTP client used in auth, discord, newsletter | `rg '\bhttpx\b' --glob '*.py'` → backend/auth/oauth.py, backend/utils/discord.py, backend/newsletter/manager.py |
| 5 | fastapi | KEEP | Core web framework | `rg '\bfastapi\b' --glob '*.py'` → backend/admin/app.py, cron_routes.py, episode_routes.py, auth/middleware.py |
| 6 | uvicorn[standard] | KEEP | ASGI server launched in `backend/admin/app.py` | `rg '\buvicorn\b' --glob '*.py'` → backend/admin/app.py |
| 7 | python-jose[cryptography] | KEEP | JWT signing in auth layer | `rg 'from jose\|import jose' --glob '*.py'` → backend/auth/session.py, backend/auth/oauth.py (`from jose import jwt, JWTError`) |
| 8 | jinja2 | KEEP | Deptry flagged DEP002 but grep shows real use via `fastapi.templating.Jinja2Templates`. FastAPI does NOT declare jinja2 as a dep; it's only imported when user opts into templating. Direct dep entry is correct. Deptry misses this because it sees the import as coming from `fastapi`. | `rg 'Jinja2Templates\|from jinja2' --glob '*.py'` → backend/admin/app.py:16 `from fastapi.templating import Jinja2Templates`, line 82 instantiation |
| 9 | send2trash | KEEP | Used across 8 files for safe deletes (local only; Lambda fallback to unlink) | `rg 'from send2trash' --glob '*.py'` → backend/data/recipe.py, backend/storage.py, backend/utils/atomic.py, backend/publishing/pipeline.py, backend/admin/routes.py, scripts/* |
| 10 | requests | KEEP | HTTP client (blob storage, cleanup scripts, health checks) | `rg '\brequests\b' --glob '*.py'` → backend/storage.py, scripts/health_check.py, scripts/backfill_webp.py, scripts/score_episodes.py, scripts/generate_recipe_page.py |
| 11 | openai | KEEP | OpenAI SDK used in baker + creative director + prompts | `rg '\bopenai\b' --glob '*.py'` → backend/agents/baker.py, backend/agents/creative_director.py, backend/utils/recipe_prompts.py, backend/admin/cron_routes.py, scripts/simulate_dialogue_week.py |
| 12 | anthropic | KEEP | Anthropic SDK used in model router + judge | `rg '\banthropic\b' --glob '*.py'` → backend/utils/model_router.py, scripts/judge_conversation.py, scripts/simulate_dialogue_week.py, tests/test_judge_conversation_parsing.py, scripts/run_compressed_week.py |
| 13 | google-genai | KEEP | Google GenAI SDK for image generation / router | `rg 'google\.genai\|google_genai\|from google import genai' --glob '*.py'` → backend/utils/model_router.py, backend/utils/image_generation.py, tests/test_integration_providers.py |
| 14 | boto3 | KEEP | Used in scripts/trigger_generation.py. Note: deptry also flagged `botocore` as DEP003 (transitive) in same file — that's a code-smell in the SCRIPT, not a dep-list issue. boto3 itself stays. | `rg '\bboto3\b' --glob '*.py'` → scripts/trigger_generation.py |
| 15 | Pillow | KEEP | PIL used for image optimization / webp backfill | `rg '\bPIL\b' --glob '*.py'` → backend/storage.py, backend/agents/art_director.py, scripts/optimize_images.py, scripts/backfill_webp.py |

## Raw deptry output

```
$ uv run deptry .
Scanning 83 files...

pyproject.toml: DEP002 'hypothesis' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'pytest' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'jinja2' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'black' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'deptry' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'ruff' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'mypy' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'playwright' defined as a dependency but not used in the codebase
pyproject.toml: DEP002 'pytest-playwright' defined as a dependency but not used in the codebase
scripts/art_director.py:24:5: DEP001 'router' imported but missing from the dependency definitions
scripts/generate_image_prompts.py:24:5: DEP001 'router' imported but missing from the dependency definitions
scripts/trigger_generation.py:13:1: DEP003 'botocore' imported but it is a transitive dependency
scripts/validate_project.py:23:5: DEP001 'scaffold' imported but missing from the dependency definitions
scripts/validate_project.py:24:5: DEP001 'scaffold' imported but missing from the dependency definitions
scripts/validate_project.py:25:5: DEP001 'scaffold' imported but missing from the dependency definitions
Found 15 dependency issues.

Exit code: 1
```

### Notes on deptry findings NOT directly about runtime deps

- **DEP002 on `black`, `deptry`, `ruff`, `mypy`, `playwright`, `pytest-playwright`**: these are dev extras — correctly placed, deptry's default report doesn't understand them as "used" because they're tooling, not imports. Harmless noise.
- **DEP001 `'router'` and `'scaffold'` in `scripts/`**: these are top-level imports that look like package names but are almost certainly local module imports that failed deptry's resolver. Not in scope for this audit (card is direct deps only). Worth a follow-up card to clean up those scripts.
- **DEP003 `'botocore'` in `scripts/trigger_generation.py`**: `botocore` is a transitive of `boto3`. Code should import from `boto3` or accept the coupling. Script-level cleanup, not a runtime dep issue.

## REMOVE candidates

**None.** Every runtime direct dep has at least one confirmed grep hit in project code. No safe removal candidates emerged.

## INVESTIGATE notes

### `pytest` and `hypothesis` in runtime deps

Both are test-only frameworks. They ARE imported (extensively) in `tests/`, so they pass grep verification — but they belong in `[project.optional-dependencies].dev`, not `[project.dependencies]`. Currently the production Vercel Lambda installs both at runtime for no reason.

**Evidence:**
- `rg '\bpytest\b' tests/` → 60+ hits
- `rg '\bhypothesis\b' tests/` → tests/test_agent_properties.py, tests/test_agent_behaviors.py, tests/conftest.py
- Neither appears in `backend/` or `scripts/` (non-test code)

**Action (follow-up PR, NOT this one):** move both from `[project.dependencies]` to `[project.optional-dependencies].dev`.

### `jinja2` (deptry false-positive)

Deptry flagged `jinja2` as unused (DEP002) because the only import is `from fastapi.templating import Jinja2Templates` — deptry sees that as a fastapi import. But `fastapi.templating.Jinja2Templates` is a thin wrapper that REQUIRES jinja2 at import time, and fastapi does NOT declare jinja2 as a hard dep. Removing jinja2 from our deps would break `backend/admin/app.py` in production. KEEP.

## Proposed `pyproject.toml` diff (NOT applied)

```diff
 dependencies = [
     "pydantic>=2.0.0",
-    "hypothesis>=6.0.0",
-    "pytest>=7.0.0",
     "httpx>=0.27.0",
     "fastapi>=0.115.0",
     "uvicorn[standard]>=0.32.0",
     "python-jose[cryptography]>=3.3.0",
     "jinja2>=3.1.0",
     "send2trash>=1.8.0",
     "requests>=2.28.0",
     "openai>=1.40.0",
     "anthropic>=0.39.0",
     "google-genai>=1.68.0",
     "boto3>=1.35.0,<2.0.0",
     "Pillow>=10.0.0,<11.0.0",
 ]

 [project.optional-dependencies]
 dev = [
     "api-trust-tracker>=0.1.0",
     "black>=23.0.0",
     "deptry>=0.20.0",
+    "hypothesis>=6.0.0",
+    "pytest>=7.0.0",
     "ruff>=0.1.0",
     "mypy>=1.0.0",
     "playwright>=1.40.0",
     "pytest-playwright>=0.4.0",
 ]
```

Net runtime dep count after follow-up: **13** (down from 15). No code removal, no transitive churn — just category correction.

## `requirements.txt` drift note

`requirements.txt` (3838 bytes, modified 2026-04-09) is stale relative to `pyproject.toml`. It is regenerated on release via `uv export --no-dev --no-hashes --no-editable`. This audit does NOT edit it. If pytest/hypothesis are moved to dev in the follow-up, `requirements.txt` should be regenerated at the same time (they will drop out naturally because `--no-dev` is set).

## Follow-up actions

1. **Follow-up PR:** move `pytest` and `hypothesis` from `[project.dependencies]` to `[project.optional-dependencies].dev`. Regenerate `requirements.txt`. Verify Lambda cold-start still works (neither package is imported at production startup, so this should be a no-op for runtime).
2. **Separate cleanup card:** fix `scripts/art_director.py`, `scripts/generate_image_prompts.py`, `scripts/validate_project.py`, `scripts/trigger_generation.py` — dangling `router`/`scaffold`/`botocore` imports caught by deptry. Low priority (scripts, not runtime).
3. **CI integration:** consider adding `uv run deptry .` to CI once the above two are cleaned up, with `--ignore DEP002` on the dev-tool rows or a per-rule config. Not in scope for #5813.
