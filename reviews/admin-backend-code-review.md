# Code Review: Admin Backend (master..HEAD, 8 commits)

**Reviewer:** Claude Code
**Date:** 2026-03-02
**Scope:** `backend/admin/` — routes, app factory, cron pipeline, templates, plus cross-cutting files `storage.py`, `recipe.py`, `config.py`, `vercel.json`

---

## How to read this document

Every finding is tagged:

| Tag | Meaning |
|-----|---------|
| **BUG** | Incorrect behavior at runtime |
| **SECURITY** | Could be exploited by an attacker |
| **QUALITY** | Not wrong, but fragile / confusing / duplicated |

Severity column: **CRITICAL > HIGH > MEDIUM > LOW**

---

## 1. `backend/storage.py`

### 1.1 [SECURITY / CRITICAL] Path traversal in `save_image`, `get_image_url`, `image_exists`

**Lines 101-116**

```python
def save_image(self, relative_path: str, image_bytes: bytes) -> str:
    dest = ROOT / relative_path          # <-- no validation
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(image_bytes)
```

`relative_path` is joined directly to `ROOT` with zero sanitization. A caller passing `../../etc/crontab` writes outside the project tree. `image_exists` and `get_image_url` have the same issue.

**Recommendation:** Resolve the final path and assert it is still under `ROOT`:

```python
def _safe_path(self, relative_path: str) -> Path:
    dest = (ROOT / relative_path).resolve()
    if not dest.is_relative_to(ROOT.resolve()):
        raise ValueError(f"Path traversal blocked: {relative_path}")
    return dest
```

Use `_safe_path()` at the top of `save_image`, `get_image_url`, and `image_exists`.

---

## 2. `backend/admin/routes.py`

### 2.1 [SECURITY / HIGH] Unsanitized `recipe_id` from episode JSON in delete handler

**Lines 901-910**

```python
recipe_id = data.get("recipe_id")            # read from untrusted JSON
images_base = app.state.project_root / "src" / "assets" / "images"
if recipe_id:
    image_dir = images_base / recipe_id       # <-- unsanitized
    featured = images_base / f"{recipe_id}.png"
```

`episode_id` is sanitized via `_sanitize_id` on line 886, but `recipe_id` is read from the JSON body and used raw to build filesystem paths. A corrupted episode file with `recipe_id: "../../"` would target the project root for trash.

**Recommendation:** Add `recipe_id = _sanitize_id(recipe_id, "recipe_id")` before building paths, or at minimum validate with `_SAFE_ID_RE.match(recipe_id)`.

---

### 2.2 [BUG / HIGH] Hardcoded `base_url` for self-calls + potential deadlock

**Line 951**

```python
base_url = "http://127.0.0.1:8000"
```

`admin_episode_run` fires HTTP requests back to itself through a hardcoded loopback. Two problems:

1. **Deployment failure.** On Vercel (or any non-localhost deployment), the server is not at `127.0.0.1:8000`.
2. **Deadlock risk.** On single-worker uvicorn in local dev, the server is busy handling the `/run` request and cannot accept the self-call.

**Recommendation:** Either derive `base_url` from `request.base_url`, or (better) call the cron stage functions directly in-process instead of round-tripping through HTTP.

---

### 2.3 [BUG / MEDIUM] Rate-limit dict grows unbounded (memory leak)

**Lines 39, 673-684**

```python
_SUBSCRIBE_RATE_LIMITS: dict[str, list[float]] = {}
```

Old IP timestamps are filtered inside the list, but dictionary keys are never evicted. Every unique IP that ever subscribes remains as a key forever. On Vercel serverless this is mitigated by cold starts; on a persistent `uvicorn` process it's a slow leak.

**Recommendation:** After filtering timestamps, delete the key if the list is empty:

```python
timestamps = [ts for ts in timestamps if now - ts < 60]
if not timestamps:
    _SUBSCRIBE_RATE_LIMITS.pop(client_ip, None)
    # ... still check rate limit ...
```

Or add a periodic sweep that drops keys with empty lists.

---

### 2.4 [BUG / MEDIUM] Image URL changed — may 404 in local dev

**Line 460**

```python
image_url = f"/assets/images/{featured}"
```

The static files mount is at `/static` (app.py line 74 mounts `src/` at that prefix). The old URL was `/static/assets/images/{featured}`. The new URL `/assets/images/{featured}` will 404 unless there's a Vercel rewrite or reverse proxy mapping it.

**Recommendation:** Verify this path resolves in local dev. If it doesn't, either add a route alias or revert to `/static/assets/images/{featured}`.

---

### 2.5 [BUG / LOW] OAuth state verification breaks if state contains `|`

**Line 74**

```python
parts = cookie_value.split("|")
if len(parts) != 3:
    return False
stored_state, ts_str, sig = parts
```

The cookie format is `state|timestamp|signature`. If the OAuth library's state string contains a literal `|`, `split("|")` produces >3 parts and verification always fails (returns False).

**Recommendation:** Use `cookie_value.split("|", 2)` to limit to 3 parts, keeping any `|` characters inside the state portion. (Or validate at generation time that state is URL-safe alphanumeric.)

---

### 2.6 [SECURITY / LOW] No email validation on newsletter subscribe

**Lines 662-664**

```python
class NewsletterSubscribeRequest(BaseModel):
    email: str
```

Accepts any string — empty strings, `<script>` tags, 10MB payloads. Rate limiting mitigates volume but not malformed input.

**Recommendation:** Use `pydantic.EmailStr` (requires `email-validator` package) or add a regex validator and max-length constraint.

---

### 2.7 [QUALITY / LOW] `_load_episodes` shadows top-level `datetime` import

**Line 732**

```python
from datetime import datetime, timezone, timedelta
```

Module already imports `from datetime import datetime` at line 23. This local re-import inside `_load_episodes` works but is confusing — future editors may rely on the module-level import without realizing this function redefines the local name.

**Recommendation:** Add `timezone, timedelta` to the module-level import and remove the local one.

---

### 2.8 [QUALITY / LOW] Inconsistent error handling: JSON vs HTML recipe detail

**Lines 414-421 vs 440-443**

The JSON endpoint `get_recipe_detail` wraps `Recipe.load_from_file` in try/except and logs errors, returning a structured error. The HTML endpoint `view_recipe_detail` does not — a corrupted recipe JSON causes an unhandled 500 with a generic error page.

**Recommendation:** Add the same try/except pattern to `view_recipe_detail`, returning a user-friendly error template.

---

### 2.9 [QUALITY / LOW] `_load_episodes` silently swallows all exceptions

**Lines 768-769**

```python
except Exception as exc:
    logger.warning(f"Skipping invalid episode file {path.name}: {exc}")
```

Malformed episode files silently disappear from the admin list with no UI indication. An admin won't know something is wrong.

**Recommendation:** Either surface skipped files in the template (e.g. a "N files skipped" warning), or narrow the except to `json.JSONDecodeError` / `KeyError`.

---

## 3. `backend/admin/app.py`

### 3.1 [SECURITY / MEDIUM] CSP `unsafe-inline` weakens XSS protection

**Lines 90-91**

```python
"script-src 'self' 'unsafe-inline' cdn.tailwindcss.com",
"style-src 'self' 'unsafe-inline' cdn.tailwindcss.com fonts.googleapis.com",
```

`'unsafe-inline'` for scripts effectively defeats CSP's XSS protection. The templates rely on inline `<script>` blocks and `onclick` handlers, making this currently necessary — but it should be a target for removal.

**Recommendation:** Move inline scripts to external `.js` files, convert `onclick` attributes to `addEventListener`, and switch to nonce-based CSP. At minimum, add these missing directives:

```
object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
```

---

### 3.2 [QUALITY / LOW] `on_event("startup")` is deprecated

**Line 111**

```python
@app.on_event("startup")
async def startup_event():
```

Deprecated since FastAPI 0.93+ in favor of lifespan context managers.

**Recommendation:**

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    logger.info("Admin dashboard starting...")
    yield

app = FastAPI(lifespan=lifespan, ...)
```

---

### 3.3 [QUALITY / LOW] Module-level `create_admin_app()` call triggers side effects at import time

**Line 126 (referenced as line 124+ in file)**

```python
app = create_admin_app()
```

Importing this module — even for type checking or tests — creates a full FastAPI app, initializes OAuth, JWT, middleware, and mounts static files. Can cause import-time crashes if env vars are missing.

**Recommendation:** Guard behind `if __name__` or use a lazy pattern for test environments.

---

## 4. `backend/admin/cron_routes.py`

### 4.1 [SECURITY / MEDIUM] Cron secret comparison is not constant-time

**Line 81**

```python
if auth_header != expected:
```

Standard string `!=` is vulnerable to timing attacks. An attacker could theoretically time requests to deduce the `CRON_SECRET` character by character.

**Recommendation:** Replace with:

```python
import hmac
if not hmac.compare_digest(auth_header, expected):
```

---

### 4.2 [BUG / MEDIUM] Sunday cron publishes without verifying prior stages

**Lines 435-464**

`cron_sunday` sets `published_at` and `published: True` without checking whether Monday through Saturday actually succeeded. A failed or incomplete pipeline could be marked as "published."

**Recommendation:** Before publishing, verify that at minimum the critical stages (monday/brainstorm, wednesday/photography) have `status: "complete"`:

```python
required = ["monday", "wednesday"]
for day in required:
    if ep.get("stages", {}).get(day, {}).get("status") != "complete":
        raise HTTPException(400, f"Cannot publish: {day} stage incomplete")
```

---

### 4.3 [BUG / LOW] `_generate_dialogue` swallows all exceptions

**Lines 143-145**

```python
except Exception as e:
    logger.warning(f"Dialogue generation failed for stage={stage}: {e}")
    return []
```

The docstring says "non-fatal on failure" which is intentional, but catching bare `Exception` hides unexpected failures like `MemoryError` or filesystem issues that shouldn't be silent.

**Recommendation:** Narrow to expected errors (e.g. `RuntimeError`, `ValueError`, `KeyError`) or at least log at `error` level with `exc_info=True` for unexpected types.

---

### 4.4 [SECURITY / LOW] LOCAL_DEV mode bypasses cron auth entirely

**Lines 69-70**

```python
if config.is_local_dev:
    return  # bypass in local dev
```

Intentional for development, but if `LOCAL_DEV=true` is accidentally set in production, all 7 cron endpoints become completely unauthenticated. There's no secondary guard.

**Recommendation:** Add a warning log when bypassing, and/or check `VERCEL_ENV` as a secondary signal that we're actually in production:

```python
if config.is_local_dev:
    if os.environ.get("VERCEL_ENV"):
        raise HTTPException(500, "LOCAL_DEV=true in Vercel environment — refusing to bypass auth")
    return
```

---

### 4.5 [QUALITY / LOW] New orchestrator instance on every request — `active_recipes` always empty

**Lines 191, 267, 319, etc.**

Each cron stage calls `_get_orchestrator()` which creates a new `RecipeOrchestrator`. The code then checks `orchestrator.pipeline.active_recipes` — but since the instance is fresh, it's always empty, and `start_recipe()` is always called. This is wasteful and the `active_recipes` guard is dead code.

**Recommendation:** Either persist the orchestrator across requests (e.g. in `app.state`) or remove the `active_recipes` check since it can never be true.

---

### 4.6 [QUALITY / LOW] Repeated boilerplate across all 7 stage handlers

Each `cron_*` function repeats ~20 identical lines: verify secret, resolve episode_id, load/create episode, try/except with save-on-error. Copy-paste across 7 functions increases the risk of drift.

**Recommendation:** Extract a decorator or shared helper:

```python
async def _run_stage(request, body, stage_name, stage_fn):
    _verify_cron_secret(request)
    episode_id = body.episode_id or _current_episode_id()
    ep = _load_or_create_episode(episode_id, body.concept or DEFAULT_CONCEPT)
    concept = body.concept or ep.get("concept") or DEFAULT_CONCEPT
    try:
        result = stage_fn(ep, concept)
        storage.save_episode(episode_id, ep)
        return _stage_response(stage_name, episode_id, concept, result)
    except Exception as e:
        ep.setdefault("stages", {})[stage_name] = {"status": "failed", "error": str(e)}
        storage.save_episode(episode_id, ep)
        raise HTTPException(500, detail=str(e))
```

---

## 5. `backend/admin/templates/episode_detail.html`

### 5.1 [BUG / HIGH] Lightbox opens wrong image path

**Lines 155 vs 158**

```html
<img src="/{{ img_path | replace('src/', '') }}"
     ...
     onclick="openLightbox('{{ img_path }}')"
```

The `<img src>` strips the `src/` prefix (producing e.g. `/assets/images/foo.png`), but `openLightbox()` receives the raw `img_path` (e.g. `src/assets/images/foo.png`). The lightbox `<img>` will try to load the wrong URL and show a broken image.

**Recommendation:** Use the same transformation in the onclick:

```html
onclick="openLightbox('/{{ img_path | replace('src/', '') }}')"
```

---

### 5.2 [QUALITY / LOW] Dead code: `msg.attachments` block never executes

**Lines 230-245**

```html
{% if msg.attachments %}
<div class="mt-2 grid ...">
    {% for att in msg.attachments %}
    ...
```

`_build_episode_detail` in `routes.py` (lines 791-799) never sets an `attachments` key on dialogue line dicts. This entire block is unreachable.

**Recommendation:** Either populate `msg.attachments` from the dialogue data, or remove the dead template block.

---

### 5.3 [QUALITY / LOW] Confusing HTML formatting around Jinja `set` tag

**Lines 152-153**

```html
{% set variant_label = img_variants[loop.index0] if loop.index0 < img_variants|length
    else 'variant ' ~ loop.index %} <div
    class="aspect-square ...">
```

The `%}` closing the Jinja tag runs directly into the `<div` opening tag on the same line. The HTML is *technically correct* but very hard to read and maintain — looks like the `<div` is part of the Jinja expression.

**Recommendation:** Put the `<div` on its own line.

---

### 5.4 [SECURITY / LOW] Inline `onclick` handlers with template variables

**Lines 43, 48, 158**

```html
onclick="runCompressedWeek('{{ episode.episode_id }}')"
onclick="deleteEpisode('{{ episode.episode_id }}')"
onclick="openLightbox('{{ img_path }}')"
```

These inject server-side values into JavaScript string literals inside HTML attributes. Currently safe because `episode_id` is sanitized by `_sanitize_id` server-side and `img_path` comes from trusted storage — but the pattern is fragile. Any template that reuses this pattern without upstream sanitization would be an XSS vector.

**Recommendation:** Use `data-*` attributes and `addEventListener` instead of inline handlers. This also enables removing `'unsafe-inline'` from the CSP.

---

## 6. `backend/admin/templates/episodes.html`

### 6.1 [QUALITY / LOW] Stage keys and labels hardcoded in template

**Lines 73-74**

```html
{% set stage_keys = ['monday','tuesday',...] %}
{% set stage_labels = {'monday':'Mon','tuesday':'Tue',...} %}
```

These duplicate `STAGE_ORDER` and `STAGE_LABELS` from `routes.py`. If stages change, two locations need updating.

**Recommendation:** Pass `stage_keys` and `stage_labels` from the route handler as template context variables.

---

### 6.2 [QUALITY / LOW] No pagination

The episode list renders all episodes. For a long-running site, this page will get progressively slower.

**Recommendation:** Add pagination or limit to most recent N episodes with a "load more" mechanism.

---

## 7. `backend/data/recipe.py`

### 7.1 [BUG / MEDIUM] Uses `unlink()` instead of `send2trash`

**Line 159**

```python
old_filepath.unlink()
```

CLAUDE.md and project governance rules state: *"NEVER use `rm` for file deletion — use `trash` command instead."* `Path.unlink()` is permanent deletion, the Python equivalent of `rm`.

**Recommendation:**

```python
from send2trash import send2trash
send2trash(str(old_filepath))
```

---

### 7.2 [QUALITY / LOW] No explicit `recipe_id` path validation at model level

**Lines 88-115 (`save_to_file`)**

`recipe_id` is used directly in filenames (`f"{self.recipe_id}.json"`) without validation. The route layer sanitizes via `_sanitize_id`, but the model itself has no guardrail. If called from a context other than the admin routes (e.g. cron pipeline, tests), a malicious `recipe_id` could write outside the intended directory.

**Recommendation:** Add a Pydantic validator on `recipe_id`:

```python
@validator("recipe_id")
def validate_recipe_id(cls, v):
    if not re.match(r"^[a-zA-Z0-9_\-]+$", v):
        raise ValueError(f"Invalid recipe_id: {v}")
    return v
```

---

## 8. `vercel.json`

### 8.1 [BUG / MEDIUM] Sunday cron fires on Monday

**Line 97**

```json
{"path": "/api/cron/sunday", "schedule": "0 0 * * 1"}
```

The cron expression `0 0 * * 1` is **Monday at midnight UTC**, not Sunday. All other stages correctly use day-of-week numbers (1=Mon through 6=Sat). Sunday should be `0` (or `7`):

```json
{"path": "/api/cron/sunday", "schedule": "0 0 * * 0"}
```

---

## 9. `backend/config.py`

### 9.1 [SECURITY / MEDIUM] `auth_bypass` has no production guardrail

**Lines 87-89**

```python
@property
def auth_bypass(self) -> bool:
    return self.is_local_dev
```

If `LOCAL_DEV=true` is set in a production environment (accident, misconfiguration, CI leak), OAuth is completely bypassed. There's no secondary check.

**Recommendation:** Cross-check with `VERCEL_ENV`:

```python
@property
def auth_bypass(self) -> bool:
    if self._vercel_env:  # we're on Vercel — never bypass
        return False
    return self.is_local_dev
```

---

## Summary Table — All Findings

| # | Severity | Type | File | Lines | One-liner |
|---|----------|------|------|-------|-----------|
| 1.1 | **CRITICAL** | SECURITY | `storage.py` | 101-116 | Path traversal in `save_image` / `image_exists` / `get_image_url` |
| 2.1 | **HIGH** | SECURITY | `routes.py` | 901-910 | Unsanitized `recipe_id` from JSON in episode delete |
| 2.2 | **HIGH** | BUG | `routes.py` | 951 | Hardcoded `127.0.0.1:8000` + deadlock risk in `admin_episode_run` |
| 5.1 | **HIGH** | BUG | `episode_detail.html` | 155-158 | Lightbox loads wrong image path |
| 2.3 | **MEDIUM** | BUG | `routes.py` | 39, 673-684 | Rate-limit dict grows unbounded (memory leak) |
| 2.4 | **MEDIUM** | BUG | `routes.py` | 460 | Image URL `/assets/images/` may 404 in local dev |
| 3.1 | **MEDIUM** | SECURITY | `app.py` | 90-91 | CSP `'unsafe-inline'` weakens XSS protection |
| 4.1 | **MEDIUM** | SECURITY | `cron_routes.py` | 81 | Cron secret comparison not constant-time |
| 4.2 | **MEDIUM** | BUG | `cron_routes.py` | 435-464 | Sunday publish doesn't verify prior stages |
| 7.1 | **MEDIUM** | BUG | `recipe.py` | 159 | `unlink()` instead of `send2trash` (violates governance) |
| 8.1 | **MEDIUM** | BUG | `vercel.json` | 97 | Sunday cron fires on Monday (`* * 1` not `* * 0`) |
| 9.1 | **MEDIUM** | SECURITY | `config.py` | 87-89 | `auth_bypass` has no production guardrail |
| 2.5 | **LOW** | BUG | `routes.py` | 74 | Pipe in OAuth state breaks verification |
| 2.6 | **LOW** | SECURITY | `routes.py` | 662-664 | No email validation on newsletter subscribe |
| 2.7 | **LOW** | QUALITY | `routes.py` | 732 | Shadows module-level `datetime` import |
| 2.8 | **LOW** | QUALITY | `routes.py` | 414-443 | Inconsistent error handling: JSON vs HTML detail |
| 2.9 | **LOW** | QUALITY | `routes.py` | 768-769 | `_load_episodes` silently swallows all exceptions |
| 3.2 | **LOW** | QUALITY | `app.py` | 111 | Deprecated `on_event("startup")` |
| 3.3 | **LOW** | QUALITY | `app.py` | 126 | Import-time side effects from module-level app creation |
| 4.3 | **LOW** | BUG | `cron_routes.py` | 143-145 | `_generate_dialogue` catches bare `Exception` |
| 4.4 | **LOW** | SECURITY | `cron_routes.py` | 69-70 | LOCAL_DEV bypasses cron auth with no secondary check |
| 4.5 | **LOW** | QUALITY | `cron_routes.py` | 191+ | `active_recipes` check is dead code (fresh instance every request) |
| 4.6 | **LOW** | QUALITY | `cron_routes.py` | all stages | Repeated ~20-line boilerplate across 7 handlers |
| 5.2 | **LOW** | QUALITY | `episode_detail.html` | 230-245 | Dead code: `msg.attachments` never populated |
| 5.3 | **LOW** | QUALITY | `episode_detail.html` | 152-153 | Confusing HTML/Jinja formatting |
| 5.4 | **LOW** | SECURITY | `episode_detail.html` | 43, 48, 158 | Inline `onclick` handlers — fragile XSS pattern |
| 6.1 | **LOW** | QUALITY | `episodes.html` | 73-74 | Stage keys/labels duplicated from backend |
| 6.2 | **LOW** | QUALITY | `episodes.html` | — | No pagination for episode list |
| 7.2 | **LOW** | QUALITY | `recipe.py` | 88-115 | No `recipe_id` validation at model level |

**Total: 29 findings** — 1 critical, 3 high, 8 medium, 17 low
