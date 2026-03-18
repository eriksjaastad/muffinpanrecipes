PLAN_KANBAN_PROMPTS_PHASE1

Scope
Phase 1 focuses on model/eval/integration tickets and one cross-project idea.

Plan For Writing Prompts And Done Criteria
1. Confirm task intent and scope for each ticket against current milestone.
2. Draft a structured prompt with Overview, Context, Execution, Done Criteria.
3. Flag external dependencies that require Doppler or new services.
4. Mark any out-of-scope items as worthless for this project.
5. Update tracker prompts after review.

Ticket #5034 Draft Prompt
Overview
Add Google/Gemini as a supported provider in the model router so we can run Gemini experiments and bookend tests.

Context
GEMINI_API_KEY exists in Doppler. The router currently supports other providers only. This enables Test 6 in BOOKEND_TESTING_LOG.md.

Execution
1. Add a Google provider implementation in `backend/llm/model_router.py` using the `google-genai` SDK.
2. Include `"google"` in the supported providers list.
3. Add Gemini allowlist entries and cost tracking entries to match existing provider patterns.
4. Add dependency via project-local tooling and update lock files as needed.
5. Add or update tests to cover provider selection and failure handling.
6. Run the preflight checklist in `OPENCLAW_PREFLIGHT.md` before any env-dependent run.

Done Criteria
- [ ] Google provider generates a basic completion end-to-end with a sample prompt in a dev run.
- [ ] Costs and allowlists include Gemini entries consistent with other providers.
- [ ] Dependency added with lock file updates.
- [ ] Tests pass for provider routing.
- [ ] `EXTERNAL_RESOURCES.yaml` updated with Google GenAI usage.

Ticket #5037 Draft Prompt
Overview
Switch DIALOGUE_MODEL in Doppler for prod and staging to the selected Haiku model.

Context
Model comparison showed Haiku has better consistency and voice variation. This is a configuration-only change.

Execution
1. Run the preflight checklist in `OPENCLAW_PREFLIGHT.md`.
2. Update Doppler secrets for `prd` and `stg` to set `DIALOGUE_MODEL=anthropic/claude-haiku-4-5-20251001`.
3. Verify settings via Doppler CLI readback and a small non-destructive dialogue generation in each environment.
4. Record the change in `DECISIONS.md` with rationale and date.

Done Criteria
- [ ] Doppler `prd` and `stg` configs show the new `DIALOGUE_MODEL`.
- [ ] A smoke dialogue generation succeeds in both environments.
- [ ] Decision recorded in `DECISIONS.md`.

Ticket #5102 Draft Prompt
Overview
Compare Stability AI (current) vs Nano Banana for food photography quality and cost.

Context
We use SDXL via Stability AI. Nano Banana is a potential alternative with similar cost but reportedly better prompt interpretation.

Execution
1. Obtain Nano Banana API key and add to Doppler.
2. Add a minimal, separate client for Nano Banana without changing the main pipeline.
3. Select 5 prompts from `data/image_generation_jobs.json`.
4. Generate images in both systems with identical prompts and settings.
5. Build a side-by-side comparison grid and document costs, time, and quality notes.
6. Update `EXTERNAL_RESOURCES.yaml` with Nano Banana usage.

Done Criteria
- [ ] 5 side-by-side comparisons saved to a dated folder under `data/`.
- [ ] Cost and timing notes recorded in a short markdown report.
- [ ] Clear recommendation: switch, keep Stability, or dual-track.

Ticket #5120 Draft Prompt
Overview
Switch daily judge verdicts to numeric scores while preserving weekly rollups.

Context
Current daily verdicts use PASS/SOFT FAIL/HARD FAIL only. Numeric scores are needed for trend analysis and future optimization loops.

Execution
1. Update the judge system prompt to request day-level numeric scores: quality 1-10 and QA 0-100.
2. Update judgment JSON schema to include per-day numeric scores.
3. Update `score_quality()` to accept a day filter for individual-day scoring.
4. Backfill or version the schema to avoid breaking existing tooling.
5. Add tests for the new scoring fields and parsing.

Done Criteria
- [ ] New daily numeric scores appear in judgment JSON for a sample run.
- [ ] Weekly rollup still computed correctly.
- [ ] `score_quality()` can compute per-day scores without errors.
- [ ] Tests cover parsing and scoring updates.

Ticket #5121 Assessment
Worthless for this project.
This ticket targets Claude skills optimization, which belongs in the Codex skills or meta tooling repos, not muffinpanrecipes. Recommend moving the card to the appropriate project tracker or closing as out of scope.
