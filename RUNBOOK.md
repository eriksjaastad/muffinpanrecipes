# RUNBOOK — Read this when the site looks broken

> **You are not the first version of Claude to see this.** Prior sessions have hit the same incidents. This is the runbook. Match the symptom, run the recipe, verify. Don't improvise.

---

## INCIDENT 1 — "The website got rolled back and all the conversations are gone"

### Symptom

User reports:
- Home page looks reverted to "the old version"
- Recipe pages for cron-generated recipes (W10–W15 and later) return 404 "Recipe not found"
- `/api/episodes/teaser` shows a weird or test episode (e.g. `test-*`, "Market Veggie Egg Nests", etc.)
- `/recipes.json` returns only the 10 original seed recipes
- `/this-week` loads but the episode content area is empty
- Nothing broken in the Vercel dashboard — deploys are green, no errors in logs
- Blob data looks fine if you check directly

**Panic reaction to avoid:** assuming blob was wiped, restoring from backup, re-running the full week of crons, or rolling back deploys further. The data is fine. The Lambda is reading from the wrong paths.

### Root Cause — Test Mode Prefix Contamination

**The bug:** `backend/storage.py::_CloudBackend` has a module-level singleton with a mutable `self.prefix` attribute. `_configure_test_mode()` in `backend/admin/cron_routes.py` sets `storage.set_prefix("test/" if body.test else "")` at the start of every cron handler.

When a cron is fired with `test=true` (e.g. a smoke test), the Lambda's storage singleton flips to `prefix="test/"`. That Lambda stays warm. Any subsequent request to that Lambda — including non-cron routes like `/api/episodes/teaser`, `/recipes.json`, `/recipes/{slug}` — inherits that prefix and starts reading from `test/pages/*` instead of `pages/*`.

Since `test/pages/*` is nearly empty (it only contains whatever the test run wrote), every read returns None and the code falls through to the static 10-seed recipes.json. The result: the site looks like it rolled back to pre-cron state, even though the real data is perfectly intact at `pages/*` in blob.

Reset only happens when a non-test cron fires (`_configure_test_mode({"test": false})` → `set_prefix("")`). Until then, the Lambda stays contaminated.

### How to verify this is the incident you're looking at

Run these four checks. If the answers match, this is it:

```bash
# 1. Public blob catalog has 16+ recipes (real data intact)
curl -s "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/pages/recipes.json" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('blob catalog:', len(d.get('recipes',[])))"
# Expect: blob catalog: 16+  ← data is fine

# 2. Public blob latest.json has the correct current-week episode
curl -s "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/pages/latest.json" | python3 -m json.tool
# Expect: correct episode_id and title for current week

# 3. Live /recipes.json endpoint returns only 10 (static fallback) — WRONG
curl -s "https://muffinpanrecipes.com/recipes.json?cb=$(date +%s)" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('live endpoint:', len(d.get('recipes',[])))"
# Expect: live endpoint: 10  ← the bug

# 4. Live teaser shows stale/test data — WRONG
curl -s "https://muffinpanrecipes.com/api/episodes/teaser?cb=$(date +%s)"
# Expect: the WRONG episode (test_* id, or an old week)
```

If steps 1+2 return real data but 3+4 return the broken view, **you have this incident.** Proceed.

If step 1 or 2 return empty/wrong data, **STOP.** Something else is wrong — the blob really is missing data. This runbook won't help. Investigate before touching anything.

### Recovery — Force Lambda Cold Start

A fresh Vercel deploy forces Lambda cold starts across all warm instances. A cold Lambda boots with `storage.prefix = ""` (the `__init__` default), which immediately puts every read back onto the correct `pages/*` path.

```bash
cd ~/projects/muffinpanrecipes

# Single command — no code changes needed, empty redeploy is fine
vercel deploy --prod
```

Build takes ~35 seconds. When it prints `Production: <url>` and `Aliased: muffinpanrecipes.com`, the new Lambdas are live and all warm instances are invalidated.

### Verify recovery (run these all, in order)

```bash
# Should return 16
curl -s "https://muffinpanrecipes.com/recipes.json?cb=$(date +%s)" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('count:', len(d.get('recipes',[])))"

# Should return the current real week's episode (Mini Caprese / Ria / W16, or whatever is current)
curl -s "https://muffinpanrecipes.com/api/episodes/teaser?cb=$(date +%s)"

# All cron-generated recipe pages should return 200 with 30KB+ HTML
for slug in smoky-cheddar-breakfast-bites roasted-veggie-frittata-cups \
            mini-lemon-meringue-cups roasted-chicken-potato-cups \
            make-ahead-veggie-sausage-egg-cups; do
  curl -s -o /dev/null -w "$slug: %{http_code} %{size_download}b\n" \
    "https://muffinpanrecipes.com/recipes/$slug"
done
# Expect: all 200, 30000+ bytes each
```

If all three check groups pass, recovery is complete. Update MEMORY.md with the date and move on. Do **not** re-fire any crons to "make sure things got written" — the data was never lost in the first place.

### How to avoid retriggering this

**Do not run `test=true` smoke tests against production Lambdas.** Specifically:

- Don't `POST /api/cron/monday` (or any cron route) with `{"test": true, ...}` against `muffinpanrecipes.com` or any `*.vercel.app` production deployment.
- If you need to smoke-test a cron route in production, use a real episode ID with `force=true` and accept that it writes real state. Cost doctrine applies — max 3 retries, count your API calls.
- Local testing: use `scripts/run_full_week.py --test` which talks to a local dev server, not prod.

### Permanent fix (shipped)

`storage.prefix_scope()` context manager shipped in PR #24 (card #5917, 2026-04-14). Every cron handler wraps its body in `with _test_mode_scope(body):` so the prefix always resets, even on exception. The shared mutable state in `_CloudBackend` is no longer exposed to handler-level leaks. See `backend/storage.py::prefix_scope` and `backend/admin/cron_routes.py`.

### First occurrence

**2026-04-14** — Session fixing #5911 (title uniqueness). Smoke test `POST /api/cron/monday {"test": true, "concept": "Roasted Veggie Egg Cups"}` contaminated a warm Lambda. User observed symptoms ~15 minutes later and reported "we rolled back the entire website." No data loss. Recovery: single empty `vercel deploy --prod` call restored normal operation in ~35 seconds.

---

## Adding a new incident to this runbook

When you hit a production issue that took non-trivial diagnosis:

1. Add a new `## INCIDENT N — "quoted user-facing symptom"` section at the top of the incidents list (most recent first is fine, or chronological — pick one and keep it).
2. Required subsections: **Symptom**, **Root Cause**, **How to verify this is the incident**, **Recovery**, **Verify recovery**, **How to avoid retriggering**, **First occurrence** (date + session context).
3. Include actual commands with expected output. "It should work" is not a runbook — show what success looks like.
4. If there's a permanent fix pending, link the card.
5. Commit with `docs(runbook): add incident N — <symptom>`.

This file is at repo root on purpose. `docs/` is where things go to be forgotten. Keep it visible.
