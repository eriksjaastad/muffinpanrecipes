# Plan: Newline Sanitization for Message Text (#5031 review finding)

## Problem

LLM-generated messages sometimes contain newlines. These messages get injected back into prompts in multiple places without sanitization, breaking prompt formatting. This is a recurring issue — the same class of bug keeps appearing in different callsites.

## Root Cause

There is no single point where message text is cleaned before storage. The `generate_turn()` function in `simulate_dialogue_week.py` does run several sanitizers on the output (lines 794-800):

```python
msg = sanitize_typographic_tells(msg)
msg = _normalize_time_notation(msg, deadline)
msg = _canonicalize_flour_ricotta_ratios(msg)
msg = re.sub(r"\bat\s+\d{2}:\d{2}\b", "", msg)
msg = re.sub(r"\b[012]\d:\d{2}\b", "", msg)
return " ".join(msg.split())
```

**Line 800 (`" ".join(msg.split())`) already collapses all whitespace including newlines into single spaces.** This means messages coming OUT of `generate_turn()` should already be single-line.

However, this only protects the `simulate_dialogue_week.py` path. The cron pipeline in `cron_routes.py` reads messages back from stored episode JSON, and those messages could have been written by:
- An older version of the code that didn't have the whitespace collapse
- The `generate_response()` call directly (bypassing `generate_turn()`)
- Manual episode edits

So we have two options:

### Option A: Trust the source, sanitize on read (defense in depth)
### Option B: Sanitize at the single write point and trust all downstream reads

## Recommendation: Option A — Sanitize on read at each injection point

Even though line 800 should prevent newlines in new messages, we can't trust stored data. The fix should be at every point where message text enters a prompt.

> COMMENT (Codex): I agree with defense-in-depth, but I want us to confirm we found **all** prompt injection points that read stored messages. If we miss one, this fix looks complete but isn’t. Please double-check other prompt builders that read episode JSON (admin routes, judge helpers, export/report scripts).

> COMMENT (Codex): Claude confirmed the only other reader is `backend/admin/routes.py:885` (admin UI render, not a prompt path). So the 5 injection points listed here are complete; route 885 can be left as-is.

## Exact Changes

### File: `scripts/simulate_dialogue_week.py`

**Line 652 — Anti-repetition self-awareness block:**
```python
# BEFORE:
quoted = "\n".join(f"  - \"{m[:120]}\"" for m in prior_own_messages[-4:])

# AFTER:
quoted = "\n".join(f"  - \"{' '.join(m.split())[:120]}\"" for m in prior_own_messages[-4:])
```

**Line 1532 — Per-character memory generation (simulate script version):**
```python
# BEFORE:
by_char.setdefault(m.character, []).append(f"[{m.day}] {m.message}")

# AFTER:
by_char.setdefault(m.character, []).append(f"[{m.day}] {' '.join(m.message.split())}")
```

### File: `backend/admin/cron_routes.py`

**Lines 221 and 228 — Judge prompt building:**
```python
# BEFORE (line 221):
lines.append(f"{name}: {m.get('message', '')}")

# AFTER:
lines.append(f"{name}: {' '.join((m.get('message') or '').split())}")

# BEFORE (line 228):
today_lines.append(f"{name}: {m.get('message', '')}")

# AFTER:
today_lines.append(f"{name}: {' '.join((m.get('message') or '').split())}")
```

> COMMENT (Codex): Potential bug if `m.get("message")` is present but `None`. The current code would render `"None"` but not crash; the proposed `.split()` would raise. Safer: `{' '.join((m.get('message') or '').split())}`.

**Line 354 — Per-character memory generation (cron version):**
```python
# BEFORE:
by_char.setdefault(char, []).append(f"[{m.get('day', '?')}] {m.get('message', '')}")

# AFTER:
by_char.setdefault(char, []).append(f"[{m.get('day', '?')}] {' '.join((m.get('message') or '').split())}")
```

> COMMENT (Codex): Same `None` risk as above. Use `m.get('message') or ''` to avoid a crash.

## Why not a shared helper function?

The pattern `' '.join(text.split())` is a Python idiom — it's 23 characters, universally understood, and does exactly one thing (collapse all whitespace to single spaces). Wrapping it in a `_flatten()` function adds indirection without adding clarity. The idiom IS the function.

> COMMENT (Codex): I’m fine with the idiom locally, but if we repeat it across multiple files, a helper can reduce subtle inconsistencies (`None` handling, future sanitizers) and makes tests easier. If we keep the one-liner, let’s be strict about the `(text or '')` guard everywhere.

If we find ourselves needing more complex sanitization (strip quotes, truncate, normalize unicode) at these sites, then a helper makes sense. But right now the problem is literally just newlines, and the one-liner is the right fix.

## What about the Message dataclass?

There's a `Message` namedtuple/dataclass used in the simulation (line 1681):
```python
messages.append(Message(day=day, stage=stage, character=speaker, message=line, ...))
```

The `line` value comes from `generate_turn()` which already runs `" ".join(msg.split())` at line 800. So Messages created by the simulation are already clean. The risk is only when reading messages back from stored JSON (episode files), which is exactly the callsites listed above.

> COMMENT (Codex): This is true for simulation, but do any other code paths write `Message` objects without `generate_turn()` (test fixtures, admin edits, imports)? If yes, include them in the “sanitize on read” checklist.

## What this does NOT fix

- Messages stored in existing episode JSON files may still contain newlines. This plan sanitizes on read, so old data is handled.
- The `generate_response()` function itself (in `model_router.py`) does not strip newlines from LLM output. It shouldn't — some callers (like the judge) want multi-line responses. The sanitization belongs at the callsite, not in the model router.

## Verification

After applying changes:
1. Create a test episode JSON with a message containing `\n` characters
2. Run the judge on it — confirm the judge prompt is well-formed
3. Run a simulation — confirm anti-repetition block doesn't break formatting
4. Check that `generate_turn()` still returns single-line output (line 800 unchanged)

> COMMENT (Codex): Add one negative test for `message: null` in stored JSON to ensure the new `.split()` calls don’t crash.

## Files changed

- `scripts/simulate_dialogue_week.py` — 2 lines
- `backend/admin/cron_routes.py` — 3 lines

Total: 5 one-line changes. No new functions, no new files, no new dependencies.
