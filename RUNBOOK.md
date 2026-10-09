# RUNBOOK — Read this when the site looks broken

> **You are not the first version of Claude to see this.** Prior sessions have hit the same incidents. This is the runbook. Match the symptom, run the recipe, verify. Don't improvise.

---

## Marcus's intro: Thursday failed, or Sunday says "Marcus's intro is missing" (#7853)

The published description is Marcus's intro, written on Thursday after he
"tastes" the dish. Monday's one-line DESCRIPTION is kept as the internal
`recipe_data.pitch`. It feeds dialogue, the judge and the muffin-pan form gate,
and is never published. Until Thursday, the in-progress page shows "Marcus adds
his intro on Thursday after tasting the new recipe."

- **Thursday writes it:** one model call with `config.recipe_model`, plus one
  retry when the intro breaks a rule (30–50 words in two sentences; it must not
  open with any word that opened one of the last 10 published descriptions).
  It stores the text in `recipe_data.description` and in
  `stages.thursday.copy_text` (`kind: intro`, with the banned openers).
- **There is no fallback text.** A failed call, a second rule break, or an
  unreadable live catalog (needed for the opener ban) fails Thursday with a
  pipeline alert.
- **Sunday refuses a week with no intro.** It returns 400 "Cannot publish:
  Marcus's intro is missing" before any paid work. A week whose Monday ran
  before #7853 has Monday's text as its description and publishes as before.

**Recovery:** re-fire Thursday, then Sunday. Thursday re-runs its dialogue too
(paid, about the same as one normal Thursday).

```bash
WEEK=2026-W41
doppler run --project muffinpanrecipes --config prd -- sh -lc \
  "curl -s -m 280 -X POST https://muffinpanrecipes.com/api/cron/thursday \
    -H \"Authorization: Bearer \$CRON_SECRET\" -H 'Content-Type: application/json' \
    -d '{\"episode_id\":\"$WEEK\",\"force\":true}'"
# then the same call to /api/cron/sunday
```

---

## Photo approval: Sunday says "awaiting_photo_approval", "photos_rejected" or "publication_underway" (#7936)

Expected, not a failure. Sunday publishes only a photo Erik approved from the
CURRENT Wednesday image set, and runs no dialogue, QA or publish until then.
The monitor shows the week as "not published: awaiting photo approval", not
as a missing Sunday.

**One authority: the photo-control log.** `photo_control/<week>/vNNNNNN.json`
in Blob (`test/photo_control/...` for test weeks; `data/photo_control/production/` locally).
Every version holds the whole state: the current image-set request, Erik's
decision and Sunday's publication claim. A change creates the next version with
overwrite refused, so two writers can never both win. Nothing is deleted; the
versions are the audit trail. The episode's `stages.wednesday.photo_review` is a
display mirror only. A stale cron save that puts an older set back on the
episode cannot change what is approved or published. The request also stores
Wednesday's evaluation of its own set (team pick, defects, automated status),
so the page, Sunday's hold record and the published episode always show the
evaluation of the photos actually under review; a request without one shows
"automated review ... not available" rather than another set's.

States: `awaiting` → `approved`/`rejected` (Erik) → `claimed` (Sunday, before
any paid step) → `publishing` (just before the save that publishes) →
`published`. From `claimed` on, choices are frozen: the page says "Publishing is
underway. Choices are frozen." and Wednesday refuses to replace the photos (409).

- Review: the "Photos ready" alert links to `/admin/episodes/<week>#photo-review`
  (`?ns=test` for test weeks). Pick one photo or "None usable". Neither action
  generates images.
- Test weeks are cloud-only. Their photos live under `test/images/`, which the
  public `/blob-images/` rewrite does not reach, so the page loads them through
  the authenticated `/admin/episodes/<week>/photos/<image_set_id>/<n>/image?ns=test`
  route (same origin, URL taken from the control by index, not cached). A URL
  naming a replaced image set gets a 409, so a tab left open on an old set
  shows broken images rather than the new set's photos: reload the page. In the
  test namespace the stage gallery below shows only the current set's photos
  and labels any others "not in the current review set". Local storage does not
  separate test episodes from production, so locally `?ns=test` is refused
  (400) and a local `test=true` Wednesday/Sunday stops with a 503 before any
  paid step.
- Each Wednesday run stores its photos under a new generation directory
  (`images/<recipe>/<generation>/round_N/`), so a rerun never overwrites the
  URLs of an earlier set and the page shows exactly the pixels being approved.
- Approved before Sunday's scheduled run: Sunday publishes it.
- Approved after Sunday's scheduled run (the page and the save message say so,
  based on the week's schedule): nothing reruns on its own. To publish, re-fire
  Sunday by hand (paid: Sunday dialogue + editorial QA), from the repo:
  `doppler run -- sh -c 'curl -sS -X POST -H "Authorization: Bearer $CRON_SECRET" -H "Content-Type: application/json" -d "{\"episode_id\": \"<week>\", \"force\": true}" https://muffinpanrecipes.com/api/cron/sunday'`.
  `force` only skips the weekday check; the approval is still required.
- `publication_underway` (409): another Sunday run holds the claim. Nothing
  was spent. Wait for it, or see "Stuck claim" below.
- "Storage is still updating" (admin 503) or a Sunday 503 "could not read a
  current copy": the episode body the CDN served did not carry the stored
  version's ETag (Blob caches overwritten files for up to ~60s). Nothing was
  approved, spent or saved. Retry in a minute.
- Rejected: nothing publishes. New photos mean a paid Wednesday rerun, which
  starts a fresh review: `.../api/cron/wednesday` with the same curl. The old
  admin `images/rerun` endpoint is retired (410).
- Weeks whose Wednesday completed before this deploy: no migration and no
  paid recovery. The page derives the request from the photos already
  uploaded; the first decision creates control version 1 (never replacing an
  existing control). No "Photos ready" alert was sent for them.
- Automated retries: only the original image criteria can trigger the one paid
  reshoot. A physical-defect finding, an incomplete or non-finite score, or an
  unavailable vision model only marks the set for human review.
- Decision records are public Blob files: "site editor" plus an opaque keyed
  hash of the account, never an email or OAuth subject.
- Published weeks keep their pinned hero; changing one is an editorial override.
- The admin viewer shows Blob episodes. Its "Run Compressed Week" button
  exists only for local filesystem data, in the production view, for a week
  that is not published (`published_at` OR a complete Sunday, the same
  legacy-aware predicate the static builder uses, so a historical week is
  never demoted); the endpoint refuses (409) on cloud storage, in any
  `?ns=`, and for a published week. The simulation keeps a Wednesday's photos
  and review mirror and never touches the photo control; a claimed/publishing
  week refuses it. Its Sunday is stored with status `simulated`, not
  `complete`: the site builder, renderer, backfill and fix_encoding all read a
  `complete` Sunday as published, so a stub Sunday must never look like one.
- **Delete Episode is retired (410).** It trashed a local episode file and
  its whole image directory after checking `published_at` only, which could
  run (or race) while the photo control was claimed/publishing and left the
  next Sunday restoring a checkpoint whose images were in the trash. Local
  cleanup is a manual operator step: first
  `doppler run -- uv run python scripts/photo_control.py show <week>` and
  confirm the control is `awaiting`/`approved`/`rejected` (never
  `claimed`/`publishing`/`published`) and the episode is not published, then
  move `data/episodes/<week>.json` and `src/assets/images/<recipe>/` with
  `trash` (recoverable), never `rm`. `scripts/cleanup_image_backlog.py`
  remains the protected sweep for orphaned variant directories.
- The review section renders from the photo-control log whether or not the
  episode's Wednesday stage shows the set (a registration whose episode save
  failed, a stale save that dropped Wednesday). The Wednesday card keeps its
  real status; the section says when the episode record does not show the
  set. Decisions always target the control's current set.
- Sunday does NOT trash image variants any more. The automatic cleanup ran
  on the provisional copy before `mark_publishing`; when that step failed the
  approval was handed back with the other candidates already in the trash.
  Every candidate stays until an explicit sweep:
  `scripts/cleanup_image_backlog.py` (dry run by default) never trashes a
  directory holding an approved, pinned, published or legacy confirmed photo.
- `publish_hold` on the episode is cleared (one save) the moment Sunday
  claims an approval, before any paid step; if that save fails the claim is
  handed back and nothing is spent. A failure after the claim (editorial QA,
  dialogue, a lost claim) is therefore recorded as a failed Sunday, and the
  monitor reports it instead of "awaiting photo approval". A later true hold
  (rejection, new photos) replaces an earlier failed Sunday stage; the QA
  record stays in `editorial_qa` and the events.
- A Wednesday that completed but left NO reviewable photo set (no uploaded
  candidates with usable URLs) is not "awaiting approval": nobody can approve
  anything. Sunday stops before any paid step with a failed Sunday stage
  ("No reviewable photos …", 400), one pipeline-failure alert and no
  "Photos ready"/"Publish held" email. Fix: re-fire Wednesday (paid). A claim
  that loses three compare-and-swap rounds is reported as a conflict (409,
  alerted, attempt recorded on any earlier hold), never as "another Sunday
  holds the claim".
- A hold is quiet only while the LAST Sunday attempt confirmed it. Every
  Sunday that finds an earlier hold writes `publish_hold.last_attempt`:
  `held` when it confirmed the hold, `failed` (with the detail) when it could
  not read or claim the photo control before deciding. The monitor reports a
  failed attempt as "sunday attempt after the photo hold failed — …" and no
  longer shows the week as waiting; the next confirmed hold makes it quiet
  again. If storage is so broken that even that record cannot be saved, the
  alert email says so explicitly ("could NOT be recorded"), and the monitor
  will keep showing the earlier hold until a Sunday run succeeds: the alert is
  the record in that case.
  The same applies when Sunday cannot even read a current copy of the episode:
  nothing can be saved safely, so the monitor shows the last persisted snapshot
  and the alert says so. Display surfaces (episode page "Published" field, list,
  monitor summary) label a legacy complete-Sunday week as published without
  inventing a timestamp.
- A claimed/publishing/published control version is checked as a whole at
  the read boundary: a real approved candidate of the current set, a valid
  claim, and (once publishing) a checkpoint that is this claim's own
  publication (same episode, hero pinned to the approved URL, `photo_approval`
  naming this claim, set and candidate). An inconsistent frozen version reads
  as unavailable everywhere (admin 503, Sunday 503 before any paid step,
  operator script refuses); nothing restores, releases or re-decides it.
  The claim must tie to its version (a `claimed` version is exactly
  `claim.from_version + 1`; publishing/published come after it) and the
  checkpoint's `photo_approval.control_version` must be that claimed version,
  with a complete, published Sunday stage, exactly as `_published_copy`
  writes it. Every new version is validated BEFORE it is written, so an
  inconsistent body never reaches the log. Inspect the version files by hand.
- The monitor reports the most recent truth: a recorded Sunday stage (failed
  or complete) outranks an older `publish_hold.last_attempt`, and a published
  week never reports one.
- A control version whose request is unusable (a candidate with no path or
  URL, a bad index, a duplicate, a malformed `image_set_id`) reads as
  unavailable: the page is a 503, decisions are refused, and Sunday stops
  with an alerted 503 before any paid step. It is never treated as a legacy
  or empty review. Inspect the version files by hand.

### Stuck or lost publication (`claimed`, `publishing`, `published`)

`claimed` → can be released. A Sunday that fails BEFORE publishing releases
its own claim. If that release failed (alert: "could not release its photo
claim"), release it by hand. Age is not a condition: the release is a
compare-and-swap, so a Sunday run that still holds the claim loses its
`claimed` → `publishing` step and publishes nothing (its paid dialogue is
wasted, never published). After a release, re-fire Sunday as above.

`publishing` / `published` → never released or re-decided, however much time
has passed. The `publishing` version carries an immutable checkpoint: the
exact episode the publishing save writes. Re-fire Sunday (the curl above). If
the episode does not show that publication (the publishing save failed, its
outcome was unknown, or a stale whole-episode save from a slow daily run or a
released Sunday later removed `published_at`/hero/`photo_approval`), Sunday
writes the checkpoint back, marks the control `published`, and redoes the
source handoff, advisory alert and IndexNow. No dialogue, QA, image or other
paid call. Response: `completed_from_checkpoint: true`.

Until that re-fire, a stale-save regression is real: the episode reads as
unpublished and the monitor reports the missing Sunday. Nothing repairs it on
its own.

Idempotency: a slow original run that resumes writes the same checkpoint body
and loses its `published` mark (logged). Either run may write the static source
handoff; it rewrites the same pages and catalog entry, and a regressed
`static_deploy` state (back to `pending`) is finished by the next Sunday's
already-published path. A checkpoint restored after the original run had
completed carries `announce_pending`/`indexnow_pending` again, so the advisory
alert and IndexNow ping can be sent once more.

```
doppler run -- uv run python scripts/photo_control.py show <week>          # add --test for test weeks
doppler run -- uv run python scripts/photo_control.py reconcile <week>     # dry run
doppler run -- uv run python scripts/photo_control.py reconcile <week> --execute
```

`reconcile` reads the episode with the ETag freshness check, then either
records `published` (the episode is published by THIS claim), or releases a
`claimed` control back to `approved` (episode not published), or refuses. It
refuses to release `publishing`/`published` and says to re-fire Sunday. An
already-published episode whose control still says `publishing` is also
reconciled automatically by the next Sunday run's already-published path.

## INCIDENT 5 — "GA4 shows no traffic from recipe pages" (CSP blocked the tag on Lambda routes; the static homepage masked it)

### Symptom

- GA4 Realtime shows visits to `/` but nothing for `/recipes/*` or `/this-week`.
- The homepage (served statically by Vercel, no CSP) tracks fine, so the
  install looks correct at a glance and reads exactly like an ad blocker.
- Browser console on a recipe page shows `gtag.js` blocked by
  Content-Security-Policy (`script-src` directive violation).

### Root cause

`backend/admin/app.py`'s security middleware sets a CSP header on **every**
Lambda route, not just the admin dashboard it was written for — and every
reader page (`/recipes/*`, `/this-week`) is served by that same Lambda
(`backend/admin/episode_routes.py`). The CSP had no `googletagmanager.com` /
`google-analytics.com` entries, so the tag and every beacon it tried to send
were silently dropped by the browser — no server error, no log line, nothing
`health_check.py` (at the time) checked for.

### How to verify

```bash
curl -sI https://muffinpanrecipes.com/recipes/<any-slug> | grep -i content-security-policy
# googletagmanager.com and google-analytics.com absent from script-src/connect-src = this incident
```

### Recovery

None needed once the fix ships — deploy carries the corrected CSP to every
Lambda route immediately; no retroactive data can be recovered for the
window traffic went untracked.

### Verify recovery

```bash
doppler run --project muffinpanrecipes --config prd -- \
  uv run python scripts/health_check.py --no-alert
# Expect: security headers check passes (asserts the CSP directive, below)
```

### How to avoid retriggering

- Any change to the CSP in `backend/admin/app.py` must be checked against
  **every** route class the app serves (admin AND public reader pages), not
  just whichever page prompted the edit — this middleware has no per-route
  carve-out.
- `scripts/health_check.py::_check_csp` now asserts
  `REQUIRED_CSP_DIRECTIVES` on live pages, so a regression here fails
  health checks instead of waiting on someone to notice GA4 go quiet.

### Permanent fix (shipped)

`git show 6030c61` (2026-08-15): CSP `script-src` allows
`https://www.googletagmanager.com`; `connect-src`/`img-src` allow the
`google-analytics.com` endpoints the tag posts to. Nothing else widened.
`tests/test_seo.py::test_csp_allows_ga4` and `scripts/health_check.py`'s
`_check_csp` both assert the directive now.

### First occurrence

**2026-08-15** — GA4 Realtime showed homepage traffic only; reproduced in
incognito, which ruled out an ad blocker. Fixed same day.

---

## INCIDENT 4 — "We published a recipe we already have" (the SILENT twin of Incident 3)

> **Read INCIDENT 3 first, then this.** They share the placeholder concept string
> and share nothing else. Incident 3 is loud: Monday *fails*, the placeholder
> reaches the homepage, `_require_monday_recipe` blocks the week. Incident 4 is
> silent: Monday *succeeds*, the page looks perfect, and the duplicate ships.
> Every fix shipped for Incident 3 misses this one.

### Symptom

- A published recipe is the same dish as one already in the catalog, under a
  different name (W36: "Greek Spanakopita Cups" vs the existing "Spanakopita
  Phyllo Cups" — same phyllo, spinach, feta, parmesan, dill, nutmeg, eggs).
- **Everything looks fine.** All stages `complete` with empty errors, the judge
  PASSed all six days, `/this-week` renders a real title, `health_check.py`
  passes.
- The only trace is inside the episode JSON: `concept` is the literal
  `"Weekly Muffin Pan Recipe"` and `stages.monday.target_category` is `null`.

**Panic reaction to avoid:** tightening `check_title_conflict`. That is the
*third* and weakest defense, deliberately relaxed by PR #51, and tightening it
re-triggers INCIDENT 3 (an over-strict validator failed Monday and the week ran
headless). See card #6854 — the dish-noun weighting is the only safe direction.

### Root cause — the concept picker was not in the Vercel bundle

`.vercelignore` excludes `scripts/*` and re-includes named files. `pick_concept.py`
was never re-included, so this line in `cron_routes.cron_monday`

```python
from scripts.pick_concept import pick_concept, pick_target_category
```

raised `ModuleNotFoundError` **in the Lambda and only in the Lambda** — never
locally, never in tests. The surrounding `except Exception` downgraded it to a
`logger.warning` and continued with the placeholder concept.

`pick_concept()` is where duplicate avoidance lives: `_load_recent_concepts()`
reads the entire published catalog from blob and scores candidates for novelty
against it. When the import failed, that whole system was skipped and the baker
invented a title with zero catalog awareness.

**This was not intermittent.** Every production week from 2026-W30 to 2026-W35
stored `concept: "Weekly Muffin Pan Recipe"`, `target_category: null`. The
picker had never once run in production.

A second bug made it unrecoverable by re-running Monday:

```python
concept = body.concept or ep.get("concept") or concept   # the stored placeholder wins
```

The stored placeholder out-ranked a freshly picked concept, so re-firing Monday
on an existing episode could never re-pick.

### How to verify this is the incident

```bash
cd "$HOME/projects/muffinpanrecipes"
uv run python scripts/session_pipeline_status.py
```

`DEGRADED` plus a line naming the placeholder concept or a null `target_category`
is this incident. For an arbitrary week:

```bash
WEEK=2026-W36 uv run python -c 'import json,os,urllib.request as u; ep=json.load(u.urlopen(f"https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/episodes/{os.environ[\"WEEK\"]}.json")); print("concept:", repr(ep.get("concept"))); print("target_category:", repr(ep.get("stages",{}).get("monday",{}).get("target_category")))'
```

`concept: 'Weekly Muffin Pan Recipe'` with `target_category: None` is the
fingerprint. Either one alone is enough — `target_category` is written only on
the picker's success path, so a null survives even after someone repairs the
concept string by hand.

### Recovery

The week has a real recipe; the problem is that nothing checked it against the
catalog. So: check it by hand, and regenerate only if it is genuinely a duplicate.

```bash
# 1. Does this week's title actually collide?
WEEK=2026-W36 uv run python -c 'import json,os,urllib.request as u; from backend.utils.title_validator import check_title_conflict, load_catalog_titles; ep=json.load(u.urlopen(f"https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/episodes/{os.environ[\"WEEK\"]}.json")); t=ep["stages"]["monday"]["recipe_data"]["title"]; print(t, "->", check_title_conflict(t, [x for x in load_catalog_titles() if x != t.lower()]))'
```

`check_title_conflict` needs two shared distinctive words, so **read the
ingredient list yourself** — a same-dish duplicate under a different name will
clear the gate (that is exactly what W36 did).

If it is a duplicate, re-fire Monday with an explicit concept, then the rest of
the week in order. Pick a category and cuisine the catalog is thin on.

```bash
WEEK=2026-W36
doppler run --project muffinpanrecipes --config prd -- sh -lc \
  "curl -s -m 280 -X POST https://muffinpanrecipes.com/api/cron/monday \
    -H \"Authorization: Bearer \$CRON_SECRET\" -H 'Content-Type: application/json' \
    -d '{\"episode_id\":\"$WEEK\",\"force\":true,\"concept\":\"Portuguese Custard Tarts baked in a muffin pan\",\"target_category\":\"Sweet\"}'"
# then tuesday..saturday, each depending on the prior stage's output
```

Pass `target_category` (Breakfast | Savory | Sweet | Party) with the concept: it
is the shelf you chose the dish for, and Monday now stamps it on the recipe.
Leave it out and the week is shelved by the baker's own classification — which
is how W36 ended up filed under Savory (#6877).

### Verify recovery

```bash
uv run python scripts/session_pipeline_status.py
# Expect: "muffinpanrecipes pipeline: OK — <week> "<title>", N/7 stages complete"

doppler run --project muffinpanrecipes --config prd -- \
  uv run python scripts/health_check.py --no-alert
# Expect: episode_integrity ✓
```

### How to avoid retriggering

- **Never add a `from scripts.X import` to `backend/` without a matching
  `!scripts/X.py` line in `.vercelignore`.** `tests/test_vercel_bundle.py`
  enforces this now; it is the only thing standing between a local-only import
  and a production-only failure.
- Do not restore a fallback to `PLACEHOLDER_CONCEPT`. A week with no concept has
  no duplicate avoidance and must stop at Monday.
- Do not restore a fallback to `src/recipes.json` on Monday's path. It is the ten
  launch seeds and nothing else; reading it instead of the live catalog turns
  every catalog-aware gate into a ten-recipe check that still reports success.
  Monday reads through `backend/utils/catalog.load_published_catalog()`, which
  retries and then raises (#6854, #6858).
- **Saturday pre-flight, ingredient-level:** the title gate cannot see a
  same-dish duplicate under a different name (that is exactly what W36 was).
  Score the week's recipe against the catalog by ingredients:

  ```bash
  uv run python scripts/audit_ingredient_overlap.py --episode $(date +%G-W%V)
  ```

  Exit 1 with a named match means the Monday gate (#6854) should have caught it
  — investigate before Sunday rather than after.
- Read the `muffinpanrecipes pipeline:` line at session start. Its whole job is
  to make this visible on day one instead of day five. The check lives here
  (`scripts/session_pipeline_status.py`); the SessionStart hook that runs it is
  machine-local at `~/.claude/hooks/muffinpan-pipeline-status.sh`, per the
  portfolio rule that all hooks live at user scope. If you never see that line,
  the hook is not installed on this machine — run the script by hand.

### Permanent fix (shipped)

- `.vercelignore` re-includes `scripts/pick_concept.py`, with
  `tests/test_vercel_bundle.py` asserting the invariant for every `scripts/`
  module `backend/` imports.
- `cron_routes._pick_weekly_concept()` retries and then raises
  `ConceptSelectionError`. Monday fails closed, writes a failed-stage record and
  Discord-alerts. `PLACEHOLDER_CONCEPT` is never persisted.
- `cron_routes._resolve_monday_concept()` treats a stored placeholder as "no
  concept", so a re-run can re-pick; `force=true` re-picks unconditionally.
- `backend/utils/episode_integrity.py` + `health_check.py --expect-episode` +
  `scripts/session_pipeline_status.py` detect the shape from outside.

- Monday runs a second, ingredient-level duplicate gate
  (`backend/utils/recipe_overlap.check_ingredient_overlap`, #6854): overlap
  coefficient ≥ 0.80 against any published recipe with ≥ 10 ingredients retries
  the baker once with the matching recipe named, then fails closed. This is the
  gate that would have caught W36. Calibration and threshold rationale live in
  `DECISIONS.md` (2026-09-05) and the module docstring.
- The concept picker itself is now self-contained and filters by category and
  muffin-pan form instead of nudging (#6858, `DECISIONS.md` 2026-09-05).

Regression coverage: `tests/test_concept_selection_fail_closed.py`,
`tests/test_episode_integrity.py`, `tests/test_vercel_bundle.py`,
`tests/test_recipe_overlap.py`, `tests/test_pick_concept.py`,
`tests/test_catalog_loader.py`.

### First occurrence

**2026-08-31** — W36 picked "Greek Spanakopita Cups" against the existing
"Spanakopita Phyllo Cups". Undetected for five days; found 2026-09-05 by a
Saturday pre-flight that opened the episode JSON by hand, hours before the
autonomous Sunday publish. No reader ever saw the duplicate. Blob evidence
showed W30–W35 carried the same fingerprint, so the picker had been dead since
at least 2026-07-20. Cards #6855, #6856, #6857.

---

## INCIDENT 3 — "The homepage shows 'Weekly Muffin Pan Recipe' as the title"

> **If you grepped your way here on the string "Weekly Muffin Pan Recipe", read
> INCIDENT 4 too.** The same placeholder has two failure modes. Here it reaches
> the *homepage* because Monday failed. In Incident 4 it sits in the episode's
> `concept` field while Monday reports `complete` and the homepage looks
> perfect — and it ships a duplicate recipe.

### Symptom

- `/this-week` and the homepage hero show the literal placeholder title **"Weekly Muffin Pan Recipe"** instead of a real recipe name.
- The week's page is thin / has no recipe card, but later stages (photos, dialogue) still ran.
- In the episode JSON, `stages.monday.status == "failed"` while `tuesday`/`wednesday` are `complete`.

**Panic reaction to avoid:** re-running the whole week, or assuming the renderer broke. The renderer is fine — Monday never produced a recipe, so everything downstream ran against the generic concept.

### Root Cause — Monday failed, the week ran headless

Monday's baker stage hard-failed (commonly the title validator rejecting every candidate, or a baker error), so the episode had no `recipe_data`. Before the stage gate shipped, Tue–Sun cron stages ran anyway against the placeholder concept `"Weekly Muffin Pan Recipe"` — spending dialogue + image-gen budget on a recipe that doesn't exist and rendering placeholder content to the live site.

### How to verify this is the incident

```bash
curl -s "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/episodes/$(date +%G-W%V).json" \
  | python3 -c "import sys,json; ep=json.load(sys.stdin); [print(d, st.get('status'), '|', st.get('error','')[:120]) for d,st in ep['stages'].items()]"
```

If `monday` shows `failed` with an error (e.g. a title-validator rejection) and later days show `complete`, this is it.

### Recovery

The permanent fixes are shipped (see below), so a failed Monday now **blocks** Tue–Sun (409 + Discord alert) instead of running headless. To recover the current week, re-fire the stages in order once the Monday cause is resolved — pass an explicit `concept` to bypass auto-pick if the validator was the cause:

```bash
WEEK=$(date +%G-W%V)
for day in monday tuesday wednesday; do
  doppler run --project muffinpanrecipes --config prd -- sh -lc \
    "curl -s -m 280 -X POST https://muffinpanrecipes.com/api/cron/$day \
      -H \"Authorization: Bearer \$CRON_SECRET\" -H 'Content-Type: application/json' \
      -d '{\"episode_id\":\"$WEEK\",\"force\":true}'" | python3 -m json.tool | grep -E 'status|recipe_title'
done
```

If the week needs to run all the way to publish, extend the loop to include `thursday friday saturday sunday` (each depends on the prior stage's output existing).

### Verify recovery

```bash
curl -s "https://muffinpanrecipes.com/this-week?cb=$(date +%s)" | grep -o '<h1[^>]*>[^<]*</h1>' | head -1
# Expect a real recipe title, NOT "Weekly Muffin Pan Recipe"
doppler run --project muffinpanrecipes --config prd -- uv run python scripts/health_check.py   # this_week_renders should pass
```

### How to avoid retriggering

- The `_require_monday_recipe` gate (cron_routes.py) now returns 409 + Discord alert on Tue–Sun when Monday has no completed recipe — a failed Monday halts the week's spend at the gate.
- The title validator was relaxed (a single shared word no longer rejects a title) so Monday stops failing on plausible titles.

### Permanent fix (shipped)

- **PR #51** — relaxed `check_title_conflict` (exact/phrase/≥2-shared-words/all-recycled, not any-single-word) + `_require_monday_recipe` stage gate.
- Title-shape gate, pan-first generation, and recipe-page unification (PRs #52–#57) further harden the Monday path.

### First occurrence

**2026-06-08** — W24 Monday failed on the over-strict title validator; discovered 2026-06-10 when the homepage showed the placeholder title. No data loss; recovered by re-firing Mon→Wed after the validator fix deployed.

---

## INCIDENT 2 — "Sunday published twice and changed the recipe"

### Symptom

User reports:
- Sunday publish ran more than once for the same ISO week
- The live recipe or episode data changed between Sunday invocations
- A second invocation may show a different auto-fixed recipe/title even though the week had already published
- In the live catalog (`pages/recipes.json`), two recipe entries appear for the same week with the same `image` field but different `slug` and `title`. Both standalone pages exist under `pages/recipes/{slug}/index.html`. The dish, ingredients, and description are identical between them.
- `/api/cron/sunday` now returns `already_published=true` for that week

**Panic reaction to avoid:** re-running Sunday with `force=true` and expecting it to repair or replace the recipe. The current code intentionally treats `published_at` as a sticky idempotency guard.

### Root Cause — At-least-once cron delivery plus non-idempotent publish work

Vercel cron delivery is at-least-once. On 2026-05-17 (W20), Sunday could be invoked more than once. Before PR #45, the Sunday path did not stop immediately once an episode was already published, and `_auto_fix_recipe` is LLM-backed/nondeterministic. A duplicate invocation could therefore run editorial QA and auto-fix again, producing a changed recipe. Catalog dedup was slug-only, so a changed title/slug could also evade the old protection.

PR #45 fixed the production behavior:

- `cron_sunday` checks `ep["published_at"]` before generating dialogue, running editorial QA, saving episode data, rendering pages, or updating the catalog.
- If `published_at` exists, Sunday returns `already_published=true` and never regenerates dialogue, re-runs editorial QA, or re-publishes, even when the request body has `force=true`. It does two bounded catch-up steps, both no-ops once complete:
  - finishes a static source handoff left `pending` or `failed` in the sources phase (writes the reader pages and catalog, and alerts if that fails);
  - sends an advisory "published below the judge bar" alert that is still owed (`judge_advisory.sunday.announce_pending` is true and the handoff reached `source_ready`), then records `announced_at` (#7403). Records published before 2026-09-30 never carry `announce_pending` and are never re-announced;
  - It does NOT touch a "kitchen took the week off" note on the successor week (#7630, see "Late/recovered Sunday publish and next week's homepage note" below).
- Catalog publish dedup is no longer slug-only.

### How to verify this is the incident you're looking at

First confirm the episode is already published in Vercel Blob. This reads the episode through the same storage API the app uses and prints booleans/status only.

```bash
cd "$HOME/projects/muffinpanrecipes"
WEEK=2026-W20 doppler run --project muffinpanrecipes --config prd -- \
  uv run python -c 'import os; from backend.storage import storage; ep = storage.load_episode(os.environ["WEEK"]) or {}; print("episode:", os.environ["WEEK"]); print("published_at_present:", bool(ep.get("published_at"))); print("sunday_status:", ep.get("stages", {}).get("sunday", {}).get("status")); print("events_tail:", ep.get("events", [])[-5:]); print("static_deploy.status:", ep.get("static_deploy", {}).get("status")); print("judge_advisory.sunday.announce_pending:", ep.get("judge_advisory", {}).get("sunday", {}).get("announce_pending"))'
```

Expected:

```text
published_at_present: True
sunday_status: complete
```

Then verify the sticky idempotency guard locally:

```bash
uv run pytest tests/test_sunday_publish_idempotency.py -q
```

Expected: both tests pass, including `test_cron_sunday_returns_without_side_effects_when_already_published`.

If you must confirm the live endpoint, do it only after the Blob check shows `published_at_present: True`. This POST is not read-only: if the episode's handoff is unfinished or an advisory alert is still owed, it completes them (see the bullets above). Check `static_deploy.status` and `judge_advisory.sunday.announce_pending` in the Blob read first if you need a side-effect-free check:

```bash
WEEK=2026-W20 doppler run --project muffinpanrecipes --config prd -- \
  sh -lc 'curl -s -X POST "https://muffinpanrecipes.com/api/cron/sunday" \
    -H "Authorization: Bearer $CRON_SECRET" \
    -H "Content-Type: application/json" \
    -d "{\"episode_id\":\"$WEEK\",\"force\":true}" | uv run python -m json.tool'
```

Expected response includes:

```json
{
  "published": true,
  "already_published": true
}
```

If `already_published` is absent, stop. You are not looking at the fixed behavior and should not keep firing cron requests.

### Recovery

If the duplicate publish already happened, do not re-run cron as a first move. Capture the current state, compare it to the intended recipe/catalog entry, and decide which artifact should remain live.

```bash
cd "$HOME/projects/muffinpanrecipes"
WEEK=2026-W20 doppler run --project muffinpanrecipes --config prd -- \
  uv run python -c 'import os; from backend.storage import storage; ep = storage.load_episode(os.environ["WEEK"]) or {}; recipe = ep.get("stages", {}).get("monday", {}).get("recipe_data", {}); print("episode:", os.environ["WEEK"]); print("title:", recipe.get("title")); print("published_at:", ep.get("published_at")); print("recipe_slug:", ep.get("recipe_slug") or ep.get("slug"))'
```

If the live catalog/page needs repair, make a focused fix PR or use the existing catalog/episode storage APIs under Doppler. Do not edit secret values, do not delete Blob data, and do not use raw destructive cleanup.

If the first publish was correct and only the duplicate needs removal, remove the duplicate catalog entry and duplicate Blob page, then verify recovery below. If the second publish is the intended survivor, update the catalog/page intentionally and still verify that only one entry remains.

**Deliberate override — re-run an already-published Sunday**

The only supported override is to manually clear `published_at` from `episodes/{week}.json` in Vercel Blob, then invoke Sunday. `force=true` alone is not an override.

Use this only with explicit operator intent:

```bash
cd "$HOME/projects/muffinpanrecipes"
WEEK=2026-W20 doppler run --project muffinpanrecipes --config prd -- \
  uv run python -c 'import os; from datetime import datetime, timezone; from backend.storage import storage; week = os.environ["WEEK"]; ep = storage.load_episode(week); assert ep, f"episode not found: {week}"; old = ep.pop("published_at", None); ep.setdefault("events", []).append(f"operator: cleared published_at for deliberate Sunday rerun at {datetime.now(timezone.utc).isoformat()}"); storage.save_episode(week, ep); print("cleared_published_at:", bool(old))'
```

Before re-firing Sunday, read the episode back and confirm the Blob write round-tripped. This catches stale read-modify-write loops before another publish attempt:

```bash
WEEK=2026-W20 doppler run --project muffinpanrecipes --config prd -- \
  uv run python -c 'import os; from backend.storage import storage; week = os.environ["WEEK"]; ep = storage.load_episode(week) or {}; present = bool(ep.get("published_at")); print("published_at_present_after_clear:", present); assert not present'
```

Then re-run Sunday once:

```bash
# force=true bypasses the day-of-week guard so Sunday can run on a non-Sunday.
WEEK=2026-W20 doppler run --project muffinpanrecipes --config prd -- \
  sh -lc 'curl -s -X POST "https://muffinpanrecipes.com/api/cron/sunday" \
    -H "Authorization: Bearer $CRON_SECRET" \
    -H "Content-Type: application/json" \
    -d "{\"episode_id\":\"$WEEK\",\"force\":true}" | uv run python -m json.tool'
```

After the rerun, verify `published_at_present: True` with the first Blob check above.

**Late/recovered Sunday publish and next week's homepage note**

Suppose the week you just force-republished (call it W) had already missed its own Sunday window, and the next week's Monday cron had already run. Finding W unpublished, that Monday stamped the next episode with `week_off_note: {"missed_week": "W", ...}`, and the homepage shows "The kitchen took the week off".

**The recovery publish does NOT clear that note** (#7630, Erik's decision on 2026-10-05). An automatic clear could erase a recipe-less next week's own valid note.

The note drops at the next week's next stage write. Every stage write re-checks it (`episode_renderer._week_off_note_still_true`) with a verified Blob read and removes it once W is published. Expect the homepage to keep the note until the next daily cron, typically within a day. A failed or stale read keeps the note, which is logged as `week_off_note recheck skipped for <W>`.

Do not re-fire a completed stage to clear the note sooner. That repeats paid work and can replace the stage's content (a re-fired Wednesday reshoots the photos). Wait for the next scheduled stage.

A related rule from the same decision: a week held for photo approval (or failing after Wednesday) shows no note on Sunday. If it never publishes, the next Monday notes it.

### Verify recovery

Set the surviving slug and any known duplicate slug first:

```bash
export BLOB_CDN="https://gtczmjysc51nh8fq.public.blob.vercel-storage.com"
export SURVIVING_SLUG="herbed-sausage-sunrise-cups"
export DUP_SLUG="old-duplicate-slug"
```

Confirm `pages/recipes.json` has exactly one entry for the surviving slug and zero entries for the duplicate slug:

```bash
curl -s "$BLOB_CDN/pages/recipes.json?cb=$(date +%s)" \
  | uv run python -c 'import json, os, sys; data = json.load(sys.stdin); recipes = data if isinstance(data, list) else data.get("recipes", []); surviving = [r for r in recipes if r.get("slug") == os.environ["SURVIVING_SLUG"]]; duplicate = [r for r in recipes if r.get("slug") == os.environ["DUP_SLUG"]]; print("surviving_slug_count:", len(surviving)); print("duplicate_slug_count:", len(duplicate)); assert len(surviving) == 1; assert len(duplicate) == 0'
```

Confirm the duplicate Blob page is gone:

```bash
curl -s -o /dev/null -w "duplicate page HTTP %{http_code}\n" \
  "$BLOB_CDN/pages/recipes/$DUP_SLUG/index.html?cb=$(date +%s)"
# Expect: duplicate page HTTP 404
```

Confirm the surviving Blob page exists:

```bash
curl -s -o /dev/null -w "surviving page HTTP %{http_code} %{size_download}b\n" \
  "$BLOB_CDN/pages/recipes/$SURVIVING_SLUG/index.html?cb=$(date +%s)"
# Expect: HTTP 200 and a non-trivial body size
```

Confirm the live homepage hero loads the surviving recipe:

```bash
curl -s "https://muffinpanrecipes.com/?cb=$(date +%s)" \
  | uv run python -c 'import os, sys; body = sys.stdin.read(); slug = os.environ["SURVIVING_SLUG"]; print("homepage_contains_surviving_slug:", slug in body); assert slug in body'
```

### How to avoid retriggering this

- Treat `published_at` as the source of truth for Sunday idempotency.
- Do not assume `force=true` means "publish again"; it only bypasses day-of-week validation.
- Do not add Sunday pre-publish work before the `published_at` guard.
- Keep `tests/test_sunday_publish_idempotency.py` passing whenever Sunday publish code changes.

### Permanent fix (shipped)

PR #45, commit `564d3f8` (merged 2026-05-19), shipped two permanent defenses:

- `backend/admin/cron_routes.py:1290`: `cron_sunday` early-returns when `published_at` is set, before dialogue generation, editorial QA, page rendering, catalog updates, or episode writes. This sticky guard does not honor `force=true`; `force=true` only bypasses the day-of-week guard.
- `backend/publishing/episode_renderer.py`: `publish_recipe_to_catalog` dedupes on `slug`, `recipe_id`, `episode_id`, normalized image key, and normalized recipe body, so same-image-different-slug duplicates are skipped.

Regression coverage:

- `tests/test_sunday_publish_idempotency.py`
- `tests/test_catalog_publish_dedup.py`

### First occurrence

**2026-05-17** — W20 double-publish. Root cause: Vercel cron at-least-once delivery combined with nondeterministic `_auto_fix_recipe` and slug-only catalog dedup. Permanent fix shipped in PR #45 (`fix/pipeline-idempotency-hygiene`).

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
- `scripts/run_full_week.py --test` does **not** use a local server. It fires the cron routes at `--base-url` (production by default) with `test=true`, writing under the `test/` prefix and making real paid calls. Point `--base-url` at a preview deploy.

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
