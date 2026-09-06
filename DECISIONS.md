## Project-Specific Decisions (Post-Scaffold)

### 2026-02-25: Branch-only execution for Codex changes

**Context:** We are establishing trust and safe collaboration while Codex starts making active changes.

**Decision:** All Codex changes must be developed on dedicated feature branches and reviewed before merge.

**Reasoning:** Keeps `main` stable while enabling proactive work. Makes diffs reviewable and reversible.

**Alternatives considered:** Direct pushes to `main` were rejected due to risk and low auditability.

### 2026-02-25: Replace task-list framing with outcomes framing

**Context:** `TODO` style docs were creating implementation bias and stress rather than clarity.

**Decision:** Shift planning language toward outcomes/intentions. Keep work aligned to `PROJECT_DOD.md` and an intentions document rather than a step-by-step TODO list.

**Reasoning:** Encourages solution design by the agent while preserving clear human intent and success criteria.

**Alternatives considered:** Keeping a traditional TODO board as the primary planning artifact was rejected.

### 2026-02-25: Move toward hosted Kanban for shared visibility

**Context:** Work is happening across multiple machines and users.

**Decision:** Use a hosted Kanban model (likely GitHub Projects) for shared real-time task visibility.

**Reasoning:** Single source of truth across devices, fewer sync issues, easier PR/task traceability.

**Alternatives considered:** Local-only Kanban was rejected for multi-device collaboration.

### 2026-09-05: Concept selection is self-contained, deterministic, and filters instead of nudging (#6858, absorbs #6391)

**Context:** On 2026-09-05 the picker asked for `Sweet` and returned "Saucy Pork Chops With Coconut Crisp" — three savory mains, all scored 2.5, all from one scraped site. `target_category` was a +2 bonus, not a filter; muffin-pan fit was down-weighted, not required; and every candidate came from scraping five third-party recipe sites, the dependency whose failure set up the W36 duplicate (RUNBOOK INCIDENT 4). The category picker was weighted-random, so two runs minutes apart chose different categories.

**Decision:**
- `pick_target_category()` is deterministic: the thinnest category wins, ties go to the one published least recently. Balancing is an input, not a suggestion.
- Candidates come from an LLM brainstorm (model: `CONCEPT_MODEL` if set, else `DIALOGUE_MODEL`) fed a gap brief built entirely from the published catalog: target category, unused cuisines, overused words, every existing title. Scraped sites are optional inspiration inside that prompt and can all fail with no effect. A curated per-category pool is the fallback when the brainstorm fails.
- Category, muffin-pan form (title must end in a pan-form noun), off-brand shape, novelty, overused words, and dish-noun collision are **hard filters**. A pick outside the target category is impossible; if nothing survives the picker raises and Monday fails closed.
- The catalog is read through `backend/utils/catalog.load_published_catalog()`, which retries and then raises. No fallback to `src/recipes.json` (ten seeds) anywhere on Monday's path.

**Reasoning:** Everything needed to choose well already lives in this repo. The third-party dependency added failure modes and subtracted quality. Deterministic selection makes Monday re-runs reproducible and testable. Hard filters are cheap here because the picker has a dozen candidates to discard — unlike the title gate, where a rejection fails the week.

**Alternatives considered:** Keeping the scrape as the candidate source with a category filter bolted on (rejected: the scraped pool contained zero Sweet candidates on 09-05, so filtering alone would have produced an empty pick every Sweet week). Weighted-random category selection with steeper weights (rejected: still nondeterministic, still lets the overweight category win).

### 2026-09-05: Same-dish duplicates are caught by ingredient overlap on Monday, not by tightening the title gate (#6854)

**Context:** W36 "Greek Spanakopita Cups" cleared `check_title_conflict()` against the live "Spanakopita Phyllo Cups" — same dish, one shared distinctive word. The obvious fix, tightening the title gate, is how INCIDENT 3 happened (PR #51 relaxed it after an over-strict validator failed Monday and a week ran headless).

**Decision:** Add `backend/utils/recipe_overlap.check_ingredient_overlap()` as a second Monday gate on the baker's `recipe_data`. Metric: ingredient overlap **coefficient** (matched items ÷ smaller ingredient count), item-level with subset matching, staples kept. `MIN_ITEMS = 10` on both sides. `DUPLICATE_THRESHOLD = 0.80`. On a hit the baker retries once with the offending recipe named; a second hit fails Monday closed. The title gate's threshold is untouched. `scripts/audit_ingredient_overlap.py` prints the live distribution and scores one episode (`--episode 2026-W37`) for the Saturday pre-flight.

**Reasoning (calibration, live catalog of 35 on 2026-09-05):** Jaccard was tried first and discarded — the *known* W36 duplicate scored 34%, under any sane line; a detector that cannot flag the duplicate you already know about proves nothing. Under the coefficient, known duplicates score 0.81 (Roasted Veggie Egg Cups vs Roasted Veggie Frittata Cups), 0.88 (Roasted Veggie Egg Cups vs Make-Ahead Veggie & Sausage Egg Cups), and 0.81–0.88 for a W36 reconstruction. The recent, internationally varied weeks (W23–W36) top out at 0.70. There is no clean gap: the early egg-cup cluster is a continuum of near-duplicates. A publish-order retrospective at 0.80 would have flagged 3 of 25 cron weeks — each inspected by hand and each a potato-or-veg egg cup with sausage, cheddar, parmesan and peppers that an editor would also have sent back. Ingredient space cannot be exhausted the way the title namespace can, so this gate does not carry INCIDENT 3's failure mode; a genuinely different dish always passes. The 10-item minimum excludes exactly the ten thin seed recipes (#6389) and stops a 6-item recipe from reading as 0.83 against a 20-item one.

**Alternatives considered:** Weighting the title's dish noun (rejected: "last significant word" is a noisy proxy — it names `egg`, `brown`, `sunrise` and `feta` as dish nouns in the live catalog — and it still tightens the axis that caused INCIDENT 3). IDF-weighted overlap and staple-dropping (both tried; thinner margins on the same pairs). Re-calibrate with the audit script if the threshold ever fires on a recipe that is plainly a different dish.

### 2026-09-05: Reader pages go static as committed artifacts, hybrid with the Lambda (#6684, #6688)

**Context:** PR #88 pointed five reader routes at files a build step was supposed to generate; Vercel ignores `buildCommand` whenever `vercel.json` has a top-level `builds` array, so nothing was generated and every route 404'd (PR #90 reverted). Erik on 2026-09-05: retry the static build, multiple prod rebuilds authorized.

**Decision:** Published recipe pages (`src/recipes/<slug>/index.html`, catalog entries and the ten seeds) are **committed static artifacts** produced by `scripts/build_site.py` and served filesystem-first, with the existing Lambda handler as **fallback** for any page published to Blob after the last deploy. `/this-week`, `/recipes`, `/recipes.json` and `/sitemap.xml` stay on the Lambda unconditionally so a Sunday publish is visible the same day with zero operator action. The homepage is baked at build time from the catalog (crawlers see real content, #6821) and keeps its client-side refresh from `/recipes.json` as progressive enhancement. The builder never writes Blob, which removes the shared preview/prod Blob hazard (RUNBOOK INCIDENT 1) from the static path structurally. The `builds` array stays; no zero-config migration.

**Reasoning:** The immutability gate (`ExistingPageMutationError`) and the incremental/full-rebuild split already assume committed output as the baseline, so committing artifacts is what the shipped machinery was built for. A deploy-time build would re-render every page on every deploy, which is exactly what #6686 forbids. Filesystem-first-with-fallback means forgetting the weekly build step costs nothing: the Lambda serves the page as it does today. Same-day freshness for the listing pages matters more than removing the Lambda from those four routes.

**Alternatives considered:** Deploy-time build via `@vercel/static-build` (rejected: every deploy becomes a full rebuild; unproven in this project's build container). Full static including `/this-week` (rejected: stale between manual deploys, and re-enabling any automated deploy reopens the closed auto-deploy decision). Git growth from committed HTML is roughly one 30 KB file per week, accepted.

**Operator ritual:** weekly `uv run python scripts/build_site.py` (incremental) → commit `src/**` → preview deploy → `health_check.py --base-url <preview>` → promote. `--full-rebuild` only when a renderer change is meant to reach every page.

### 2026-09-05: Alerts email through the existing Resend setup, routed by severity (#6860)

**Context:** Erik does not read Discord; he does read the daily email from `alerts@send.synthinsightlabs.com`. That domain already has a verified Resend configuration (SPF, MX, DKIM) and `RESEND_API_KEY` already exists in the synth-insight-labs and auxesis Doppler projects. PR #94 routed every alert through one `send_alert()` so adding a channel is one file.

**Decision:** Reuse Resend and the existing sender. `send_alert` fans out to Discord and email in parallel; only `error`/`critical` email, `warning`/`info` stay Discord-only. A missing `RESEND_API_KEY` or `ALERT_EMAIL_TO` is loud (ERROR log plus a one-time Discord notice) but does not take the site down. Erik copies the key into the muffinpanrecipes Doppler project.

**Reasoning:** No new provider, no new DNS, no new account. Severity routing is the whole point: if everything emails, the channel dies the way Discord did.

### 2026-09-05: The dialogue judge scores dimensions and fails closed (#6861, #6832, #6840)

**Decision:** The judge returns structured JSON with eight 1–5 scores (the six rubric dimensions plus turn-taking and cast coverage), a verdict, the weakest dimensions and a one-line reason; scores persist on the stage as `judge_scores`. Unparseable judge output is a FAIL after one retry, never a default PASS. The roster bug behind W36's photographer-less photography discussion is fixed with the minimal lever: Julian joins Monday, Devon joins Tuesday, matching what `CHARACTER_DAY_GOALS` already assumed. Exactly one turn-taking prompt nudge lands (permission for short replies and answering the question first), logged in the tuning record. Flat dialogue was diagnosed as prompt-level: the per-turn reaction directive already exists, so no architectural rewrite.

### 2026-09-05: Cloud image-variant cleanup is an honest no-op (#6712)

**Decision:** `_CloudBackend.cleanup_image_variants` returns `[]` with a docstring: round-1 variants are live behind-the-scenes gallery content, not discards, so there is nothing to clean in Blob. It previously delegated to a filesystem trash on a directory that does not exist on Vercel and silently claimed success. Not wired to `delete_by_prefix` on purpose.

### 2026-09-05: A published week's hero is pinned data, and the Lambda fallback matches the rewritten path (#6688, #6685, #6684)

**Context:** The first live full rebuild changed the hero on 20 of 25 published pages. The renderer's "prefer the art director's confirmed winner" rule post-dates those pages, and the static builder carried its own copy of the rule; several winners point at a top-level copy that was never uploaded. Erik pinned published heroes on 2026-08-22, but the pin lived only in intent. Separately, the first preview showed that after a `check: true` miss Vercel keeps routing against the rewritten path, so a Lambda fallback keyed on the original path never matched and a week published after the last deploy would have hit the static 404.

**Decision:** `hero_image_url` on the episode is authoritative in both the renderer and the builder. Sunday writes it before the page renders; `scripts/pin_published_heroes.py` backfilled the 25 live weeks from the exact image each production page shows (0 of 25 differ after rebuild). The fallback route matches `/src/recipes/<slug>/index.html` and the app answers that path like `/recipes/<slug>`; the proof is that a missing slug returns the Lambda's own "Recipe not found", not the static 404.

**Reasoning:** A pin that depends on a rule is not a pin. Two copies of one rule drift. Routing semantics that are not documented must be proven on a preview before promotion, which is why the deploy ritual keeps the preview step.
