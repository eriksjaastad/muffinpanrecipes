PLAN_KANBAN_PROMPTS_PHASE2

Scope
Phase 2 covers dialogue, memory, and narrative experiments.

Plan For Writing Prompts And Done Criteria
1. Clarify whether each experiment should be gated behind a feature flag.
2. Draft prompts with explicit scope, data persistence rules, and safety checks.
3. Note cross-dependencies among memory features to avoid double work.
4. Update tracker prompts after review.

Ticket #5030 Draft Prompt
Overview
Add a one-time "pilot episode" mode where characters meet for the first time and establish baseline relationships.

Context
Current simulations assume prior relationships. A pilot run should create foundational memory used for all future weeks.

Execution
1. Add a pilot mode flag to the dialogue pipeline or simulation runner.
2. In pilot mode, disable historical memory injection and shared history references.
3. Generate a full Monday conversation using the pilot mode.
4. Save the pilot output in a stable location and seed future memory with it.
5. Add documentation for how and when to run pilot mode again.

Done Criteria
- [ ] Pilot mode run produces a complete Monday episode.
- [ ] Output is persisted and used as baseline memory for subsequent weeks.
- [ ] Re-running pilot mode is possible without overwriting prior history unless explicitly requested.

Ticket #5032 Draft Prompt
Overview
Run a "life event" experiment to test whether private context changes behavior without explicit mention.

Context
We want to test organic behavioral shifts and whether other characters notice.

Execution
1. Choose one character and define a minor private life event.
2. Inject the event into that character's private context only.
3. Run a full week simulation without instructing the character to mention the event.
4. Analyze output for behavioral changes and whether others notice.
5. Document findings and decide whether to keep, revise, or drop the mechanism.

Done Criteria
- [ ] One full week run completed with the private event injected.
- [ ] Findings documented with concrete dialogue excerpts and conclusions.
- [ ] Decision recorded on whether to keep the mechanism.

Ticket #5033 Draft Prompt
Overview
Add birthdays to character bios to enable in-season moments and interactions.

Context
Birthdays provide organic hooks for dialogue and tension.

Execution
1. Choose month/day birthdays for each character consistent with their backstory.
2. Add birthdays to character bio files and any summary profile files.
3. Optionally add zodiac traits as context, without making them deterministic.
4. Update any memory/profile loading to surface birthday info during relevant weeks.

Done Criteria
- [ ] Each character has a birthday recorded in bio content.
- [ ] Optional zodiac traits added without breaking voice consistency.
- [ ] Birthday data is available to the dialogue system for in-season references.

Ticket #5027 Draft Prompt
Overview
Feed each character their own prior-week messages instead of the full group transcript.

Context
Per-character memory should reflect lived experience and presence in a scene.

Execution
1. Update memory assembly to capture each character's prior messages per day.
2. Inject only the character's own history when generating their next-day responses.
3. Ensure characters who were absent on a given day do not get that day's transcript.
4. Add tests or a verification script that shows memory differences per character.

Done Criteria
- [ ] Each character receives only their own prior messages.
- [ ] Absences correctly omit transcripts for those characters.
- [ ] A verification artifact shows per-character memory differences.

Ticket #5028 Draft Prompt
Overview
Detect and persist standout "charged moments" after each day.

Context
Highlight memories should persist longer than routine conversation for continuity and tension.

Execution
1. Add a post-day extraction step to identify charged moments.
2. Store highlights in a distinct memory tier with longer retention.
3. Ensure highlights can be referenced in subsequent days and weeks.
4. Add logging to inspect which moments were captured.

Done Criteria
- [ ] Each simulated day outputs a list of charged moments.
- [ ] Highlights are persisted in memory and carried into future prompts.
- [ ] Logged or saved inspection output confirms capture quality.

Ticket #5029 Draft Prompt
Overview
Implement a 3-tier memory decay system: full excerpts for week 1, condensed moments for week 2, emotional residue for week 3+.

Context
Human memory fades with time; this should make dialogue feel more realistic.

Execution
1. Define the memory tiers and retention rules.
2. Add a memory compaction step that converts older transcripts into condensed summaries.
3. Add a residue representation for week 3+ that stores feelings without details.
4. Ensure the memory loader chooses the correct tier per timeframe.

Done Criteria
- [ ] Week 1 uses full excerpts, week 2 uses condensed moments, week 3+ uses residue.
- [ ] Memory compaction runs automatically without manual intervention.
- [ ] Sample run shows tiered memory selection working as designed.

Ticket #5108 Draft Prompt
Overview
Prototype an autoresearch-style loop for dialogue quality using iterative prompt tweaks and judge scoring.

Context
We already have voice guides, cheap generators, and a judge model. This should automate improvement.

Execution
1. Define a scoring rubric tied to the judge outputs.
2. Create a loop that tweaks voice guides, generates dialogue, and scores it.
3. Keep a leaderboard of prompt variants and their scores.
4. Add a stop condition and manual review gate.

Done Criteria
- [ ] Loop runs for a defined number of iterations without manual intervention.
- [ ] Scoring and variant leaderboard are recorded.
- [ ] Manual review gate prevents automatic promotion to production.
