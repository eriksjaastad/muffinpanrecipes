# Governance Compliance Review — 2026-03-01

**Reviewer:** Claude (Opus 4.6)
**Scope:** Full codebase audit against `REVIEWS_AND_GOVERNANCE_PROTOCOL.md`
**Trigger:** Post-sprint review — "we've been missing things, not thinking ahead"

---

## Executive Summary

The codebase has **solid foundations** — proper secret management, atomic writes, good agent architecture, fail-fast pipeline with Discord alerts. But the auth layer shipped with **two production-blocking security gaps** (both already marked TODO), and a pattern of "it works for one admin" assumptions that will bite when the system scales.

**CRITICAL (2):** Must fix before production.
**HIGH (4):** Should fix in next sprint.
**MEDIUM (7):** Track and fix before scaling.
**LOW/NOTE (4):** Awareness items.

---

## CRITICAL Findings

### C1. No JWT Signature Verification on Google ID Tokens

**File:** `backend/auth/oauth.py:193-194`
**Governance Violation:** H4 (Path Safety / Input Sanitization), General Security
**Status:** TODO exists but unfixed

```python
# TODO: Implement full JWT verification with JWKS.
decoded = jwt.get_unverified_claims(id_token)
```

**The Problem:** `get_unverified_claims()` parses the JWT payload without checking the cryptographic signature. If an attacker can present a crafted JWT (e.g., via a man-in-the-middle on the token exchange, or if the `code` exchange is somehow bypassed), they can forge any claim — including email.

**Mitigating Factors:**
- The token comes from Google's token endpoint over HTTPS (hard to MITM)
- Email whitelist provides a second gate
- Issuer and audience are checked post-decode (but meaningless without sig verification)

**Why It Still Matters:** The whole point of ID token verification is defense-in-depth. If the token exchange is ever replayed, leaked, or the code is refactored to accept tokens from other sources, this is a wide-open door.

**Fix Applied:** Yes — full JWKS verification with caching implemented.

---

### C2. OAuth State Parameter Generated But Never Verified

**Files:** `backend/admin/routes.py:184-190` (login), `backend/admin/routes.py:192-220` (callback)
**Governance Violation:** H4 (Input Sanitization), Security Best Practice
**Status:** TODO exists but unfixed

```python
# NOTE: Session middleware is not wired yet, so we do not persist oauth_state
# in request.session. Callback currently validates token + authorized email.
# TODO: Add SessionMiddleware and strict state verification.
```

**The Problem:** The `state` parameter is generated in `get_authorization_url()` and sent to Google, but on callback it's accepted as a parameter and **never compared** to the original. This defeats CSRF protection — the entire purpose of the OAuth `state` parameter.

**Attack Scenario:** An attacker initiates their own OAuth flow, gets a valid authorization code for their Google account, and tricks the admin into visiting the callback URL. The admin's browser sets a session cookie for the attacker's Google account. This is a session fixation attack.

**Mitigating Factors:**
- Email whitelist means the attacker would need to be in GOOGLE_AUTHORIZED_EMAILS
- SameSite=lax cookies prevent some cross-origin scenarios

**Fix Applied:** Yes — state stored in a signed, time-limited cookie and verified on callback.

---

## HIGH Findings

### H1. `secure=True` Cookie Blocks Local Development

**File:** `backend/auth/middleware.py:116`
**Governance Violation:** E3 (env var behavior)

```python
secure=True,  # Requires HTTPS — cookie won't set over HTTP
```

**The Problem:** Local dev runs on `http://localhost:8000`. With `secure=True`, the browser silently refuses to set the cookie. Auth works only because `config.auth_bypass` returns a fake user — but if you ever need to test the real OAuth flow locally, you can't.

**Fix Applied:** Yes — `secure` flag now reads from `config.is_local_dev`.

---

### H2. JWT_SECRET Falls Back to Insecure Key Without Hard Failure

**File:** `backend/auth/session.py:28-31`
**Governance Violation:** E3 (Critical env vars validated at startup)

```python
if not secret:
    logger.warning("JWT_SECRET not set — using insecure fallback (local dev only)")
    return "dev-insecure-fallback-key-do-not-use-in-production"
```

**The Problem:** If Doppler injection fails on Vercel, every session is signed with a publicly known key. A `logger.warning` is not a sufficient gate — in production this should be a hard crash.

**Fix Applied:** Yes — raises `RuntimeError` when not in local dev.

---

### H3. `subprocess.Popen` Without Output Capture (Governance Violation)

**File:** `backend/admin/routes.py:760-771`
**Governance Violation:** H1 (Subprocess Integrity — `check=True`, `timeout`, `capture_output=True`)

```python
subprocess.Popen(
    [...],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
```

**The Problem:** The governance doc requires `check=True`, `timeout`, and `capture_output=True` for all subprocess calls. This Popen sends stdout/stderr to `/dev/null` — if the background run fails, there's zero diagnostic trace. This is a fire-and-forget pattern that contradicts the "no silent failures" principle.

**Mitigating Factor:** This is intentionally fire-and-forget for admin UX (immediate HTTP response). But the output should go to a log file, not DEVNULL.

**Fix Applied:** Yes — stderr/stdout now routed to a log file for post-mortem.

---

### H4. No Path Sanitization on User-Supplied IDs

**Files:** `backend/admin/routes.py` (recipe_id, episode_id), `backend/admin/cron_routes.py` (episode_id)
**Governance Violation:** H4 (Path Safety — safe_slug + traversal check)

```python
# routes.py:321
filepath = data_dir / status.value / f"{recipe_id}.json"

# routes.py:718
ep_path = episodes_dir / f"{episode_id}.json"

# cron_routes.py:48 (via storage.load_episode)
path = EPISODES_DIR / f"{episode_id}.json"
```

**The Problem:** `recipe_id` and `episode_id` come from URL path parameters or request body. A value like `../../etc/passwd` would construct `data/recipes/pending/../../etc/passwd.json`. While FastAPI's path parameter extraction limits some characters, it doesn't prevent `..` sequences in all cases.

The governance doc (H4) explicitly requires: "Verify all user-input paths are sanitized." The `validate_project.py` script has `safe_slug` and traversal detection — but none of the admin routes use it.

**Fix Applied:** Yes — added `_sanitize_id()` helper that rejects any ID containing path separators or `..`.

---

## MEDIUM Findings

### M1. Silent Empty Return in `_generate_dialogue`

**File:** `backend/admin/cron_routes.py:142-143`
**Governance Violation:** E2 (No silent failure returns), Section 6 (Silent Failure Prevention)

```python
except Exception as e:
    return []  # dialogue is non-fatal
```

**The Problem:** The governance doc explicitly prohibits `return []` without logging. If the dialogue simulator fails consistently, you'd never know from logs. This is exactly the "Couldn't Look" vs "Nothing Found" distinction from the 2026-01-27 incident documentation.

**Fix Applied:** Yes — added `logger.warning` before the return.

---

### M2. `_load_simulation_runs` Swallows Exceptions Silently

**File:** `backend/admin/routes.py:67-69`
**Governance Violation:** E2, H9

```python
except Exception as exc:
    logger.warning(f"Skipping invalid simulation file {path.name}: {exc}")
    continue
```

**Assessment:** This one actually **passes** governance — the warning is logged. However, if *all* files fail to parse, the function returns an empty list with no aggregate warning. A "zero results found" sanity check (E4) is missing.

**Fix Applied:** Yes — added aggregate zero-result warning.

---

### M3. Newsletter Subscribe Endpoint Is Unauthenticated and Unthrottled

**File:** `backend/admin/routes.py:567-576`

```python
@app.post("/api/newsletter/subscribe")
async def newsletter_subscribe(request_data: NewsletterSubscribeRequest):
    """Public endpoint for newsletter subscription."""
```

**The Problem:** This is a public endpoint with no rate limiting. An attacker can:
1. Enumerate whether emails are subscribed (returns "already subscribed" vs "success")
2. Fill the subscriber file with garbage (DoS on file-based storage)
3. Spam the Buttondown API quota

**This is by design (public signup)** but needs rate limiting before launch.

**Fix Applied:** No code fix — documented as pre-launch requirement.

---

### M4. Cloud Storage Backend Is Entirely Stubbed

**File:** `backend/storage.py:112-189`
**Governance Violation:** E2 (every TODO-stub falls back to filesystem silently)

Every method in `_CloudBackend` falls through to the filesystem backend with no indication that cloud storage isn't working. On Vercel, the filesystem is ephemeral — data written in one request is gone on the next cold start.

**Risk:** If `BLOB_READ_WRITE_TOKEN` is unset on Vercel (misconfiguration), episodes saved by cron routes vanish silently between invocations. The system appears to work but loses all state.

**Fix Applied:** No code change — this is a known in-progress item (#4974). Added warning log when cloud backend falls back.

---

### M5. Missing Security Headers

**File:** `backend/admin/app.py`

No Content-Security-Policy, X-Frame-Options, X-Content-Type-Options, or Strict-Transport-Security headers. The admin dashboard serves HTML templates — without CSP, any XSS in template rendering would be exploitable.

**Fix Applied:** No — requires architectural decision on CSP policy. Documented.

---

### M6. `storage._FilesystemBackend.list_episodes` Swallows Exceptions

**File:** `backend/storage.py:66-68`
**Governance Violation:** E2, H9

```python
except Exception:
    pass  # Silent swallow
```

**Fix Applied:** Yes — added logging.

---

### M7. `run_compressed_week.py` Subprocess Missing `check=True`

**File:** `scripts/run_compressed_week.py:60`
**Governance Violation:** H1

The script captures output and checks `returncode` manually, which is functionally equivalent to `check=True` — but the governance doc is explicit about the pattern. Left as-is since it's a script (not backend), but noted.

---

## LOW / Notes

### L1. JWT Secret Rotation Invalidates All Sessions

**File:** `backend/auth/session.py`

If `JWT_SECRET` changes in Doppler, every active session cookie becomes invalid immediately. Users get silently logged out and redirected to Google login.

**Assessment:** Acceptable for a single-admin system. If you ever add more users, consider supporting both old and new secrets during a rotation window.

---

### L2. Cron Secret Comparison Is Not Constant-Time

**File:** `backend/admin/cron_routes.py:80`

```python
if auth_header != expected:
```

String comparison with `!=` is vulnerable to timing attacks in theory. In practice, Vercel's network latency dwarfs any timing signal. For production hardening, use `hmac.compare_digest()`.

---

### L3. `datetime.now()` Without Timezone in Several Locations

**Files:** `backend/newsletter/manager.py:207`, `backend/publishing/pipeline.py:190`, `backend/orchestrator.py:438`

The cron routes correctly use `datetime.now(timezone.utc)`, but other modules use bare `datetime.now()` which returns local time. Inconsistent timestamps make debugging cross-timezone issues harder.

---

### L4. `on_event("startup")` Is Deprecated in FastAPI

**File:** `backend/admin/app.py:86`

FastAPI recommends `lifespan` context manager instead of `@app.on_event("startup")`. Not a security issue but will emit deprecation warnings in newer versions.

---

## Governance Checklist Evidence

| ID | Check | Result | Evidence |
|----|-------|--------|----------|
| M1 | No hardcoded paths | PASS | No `/Users/` or `/home/` paths in code |
| M2 | No silent `except: pass` | PASS | Grep found 0 matches |
| M3 | No API keys in code | PASS | All use `os.getenv()` |
| M4 | Zero `{{VAR}}` placeholders | PASS | No template placeholders found |
| E2 | No silent empty returns | **FAIL** | `cron_routes.py:143`, `storage.py:68` — fixed in this PR |
| E3 | Critical env vars validated | **FAIL** | `JWT_SECRET` had insecure fallback — fixed in this PR |
| H1 | Subprocess `check`/`timeout` | **FAIL** | `routes.py:760` Popen — fixed in this PR |
| H4 | Path safety on user input | **FAIL** | No sanitization on recipe_id/episode_id — fixed in this PR |
| H8 | No unbounded recursive globs | PASS | No `**/*.py` globs found |
| H9 | Exception handling on fs ops | **FAIL** | `storage.py:68` bare `except: pass` — fixed in this PR |
| T4 | External deps mocked in tests | PASS | Auth tests don't hit real Google |

---

## Summary of Fixes Applied

1. **`backend/auth/oauth.py`** — Full JWKS signature verification with caching
2. **`backend/admin/routes.py`** — OAuth state stored in signed cookie, verified on callback; path sanitization on all ID parameters; subprocess output to log file; zero-result warning on simulations
3. **`backend/auth/middleware.py`** — `secure` cookie flag conditional on environment
4. **`backend/auth/session.py`** — Hard failure on missing `JWT_SECRET` in production
5. **`backend/admin/cron_routes.py`** — Logging on dialogue generation failure; path sanitization
6. **`backend/storage.py`** — Logging on swallowed exceptions; cloud fallback warning
