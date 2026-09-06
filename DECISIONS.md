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
