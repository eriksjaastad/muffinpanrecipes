# Conversation Lab Protocol

Card #6492. The scientific method for the character conversations: how we
measure whether the weekly dialogue reads like a real team talking to each
other, and how we prove a change to it helped instead of just feeling like it
helped.

This document is written for Erik and for a future Claude with no memory of
this session. If you are that future Claude: read this whole file before you
touch a prompt lever.

## Goal

The six-character (seven with Ria) weekly dialogue should read like a real
creative team talking to each other about a real dish: distinct voices,
turns that respond to what was just said, disagreements that get resolved
instead of reframed away, and a story that never drifts from the named hero
ingredient. When we change a prompt lever to move one of those qualities, we
need evidence the change moved it, not just a feeling that the new week read
better. That is the whole point of this protocol: replace "felt better" with
a measured before/after.

## Instruments

Four things produce evidence, in increasing order of cost:

- **`scripts/review_episode.py`** - the weekly measurement pass. Reads a
published or in-progress episode's dialogue and applies the 6-dimension
conversation rubric (title fidelity, arc resolution, voice
distinctiveness, technical credibility, natural progression,
promise/delivery alignment) deterministically where possible. Zero paid
API calls.
- **`scripts/conversation_lab.py`** - the experiment runner. Three
subcommands: `baseline` (capture a week's current scores with no
generation, free), `ab` (offline blind position-swapped pairwise A/B
between a control and a variant prompt lever, costs Haiku + judge calls),
and `calibrate` (sanity-check the judge itself against known-good and
known-bad transcripts, costs judge calls).
- **`scripts/conversation_metrics.py`** - deterministic, non-LLM metrics
(turn length, echo-of-shared-rules detection, cast coverage against the
expected roster, repeated-phrase detection). These back the parts of the
rubric that do not require judgment, and they are what `review_episode.py`
calls for its zero-cost half of the read.
- **The production judge**, `_JUDGE_SYSTEM_PROMPT` in
`backend/admin/cron_routes.py:267` (called from `_judge_dialogue` at
`backend/admin/cron_routes.py:338`). Since #6861 this is a structured
grader, not a boolean: it scores 8 dimensions 1-5 (the 6 rubric dimensions
plus `turn_taking` and `cast_coverage`, see the JSON schema at
`backend/admin/cron_routes.py:309-311`) and persists them onto the episode
as `judge_scores` / `judge_weakest` / `judge_reason`, keyed by stage
(`backend/admin/cron_routes.py:402-404`). This is the live publish gate -
it fails closed on unparseable output, never defaults to PASS
(`backend/admin/cron_routes.py:325`). Every live week's scores come from
here; the lab's job is to decide what to feed it, not to replace it.

Note: at the time this protocol was written (2026-09-06), `review_episode.py`,
`conversation_lab.py`, and `conversation_metrics.py` did not yet exist in this
branch's `scripts/` directory. This document describes the instruments this
card is building; if you land here before they exist, that is the next
implementation step, not a documentation error.

## Weekly ritual

Monday after 14:45 UTC (15 minutes after the Monday cron fires,
`vercel.json` schedule `30 14 * * 1`), the new week's concept and Monday
dialogue exist. Read it:

1. Run `scripts/review_episode.py` against the new week.
2. Add one row to `EXPERIMENTS.md`'s companion tuning history (or, once the
week is complete, to the seeded table in `EXPERIMENTS.md` if this week is
itself part of a live confirmation).
3. Log anything that reads as a new failure mode, even if it doesn't cost a
rubric point yet - the W36 prompt-leak and Marcus tic were both caught
this way before they were formal experiments.

This is a read, not a gate. Erik steers; nothing here blocks a publish on its
own except the production judge already wired into the cron pipeline.

## Experiment steps

Use this sequence any time a prompt lever is a candidate for change. Do not
skip steps to save time - the whole reason this document exists is that
skipping straight to "ship it, it reads better" is how the same lever gets
re-litigated every few weeks with no record of what was tried.

1. **Measure.** Run `scripts/review_episode.py` (or read `EXPERIMENTS.md`)
to get the current baseline scores for the dimension you think is weak.
2. **Hypothesize ONE lever.** State it as: changing X is predicted to move
dimension(s) Y. Name the dimensions. One lever per experiment - if two
levers move at once, a win or loss cannot be attributed to either.
3. **Offline blind A/B.** Generate N pairs (control prompt vs. variant
prompt) through the *production* call shape (`mode="openai",
prompt_style="scene", ticks_per_day=0`, plus a `recipe_context` anchor -
see `scripts/conversation_lab.py --help` for the exact invocation), then
judge each pair **position-swapped** (variant-first and variant-second)
so the judge's own position bias cancels out, and **blind** (the judge is
not told which transcript is control vs. variant).
4. **Decision rule.** Ship the variant only if it wins on the target
dimension in >= 65% of judged pairs AND does not lose any other dimension
by more than 50% of pairs. N is small (experiments run at N=5 pairs by
default) - this is a signal to act on, not statistical proof. Treat a
borderline result (e.g. 3/5) as inconclusive, not a win.
5. **Ship as its own PR** with a `promptV` version bump recorded in
`EXPERIMENTS.md`'s prompt-versions list. One lever per PR, same as one
lever per experiment.
6. **Confirm live.** The first live week after the prompt version ships is
the confirmation read - log its `judge_scores` against the offline
prediction in `EXPERIMENTS.md`. A lever that wins offline but does not
move the live score the following week is not confirmed; say so.
7. **Grader calibration** runs at kickoff (before the first experiment ever
run under this protocol) and monthly thereafter via
`scripts/conversation_lab.py calibrate`. See "Open hypothesis" below for
why this step exists now, not just as a formality.

## Where the levers live

Verified against the code on 2026-09-06 (line numbers will drift; grep the
symbol name if a citation looks stale):

- `_SHARED_CHARACTER_RULES` - `scripts/simulate_dialogue_week.py:368`. The
universal behavior rules injected into every character's system prompt
(turn length, "talk about the food not the technology", stay in your
lane). Older notes call this `_SHARED_RULES`; the actual symbol in the
current code is `_SHARED_CHARACTER_RULES`. This is where the v2 nudge
(pan-case example removal) and the #6840 turn-taking directive both live.
- `_REACTION_DIRECTIVE` - `scripts/simulate_dialogue_week.py:248`, the
#6840 turn-taking rule itself ("Your first sentence must respond to what
was just said...").
- `build_system_prompt` - `scripts/simulate_dialogue_week.py:484`. Assembles
a character's full system prompt from their persona card, voice guide, and
`_SHARED_CHARACTER_RULES`. Per-character voice tuning happens upstream of
this function, in the voice guides it reads.
- `DAY_STAGE_DIRECTIONS` - `scripts/simulate_dialogue_week.py:134`. The
one-line scene-setting direction for each day of the week (Monday's
"Slack pings stack up...", Wednesday's lighting-test debate, etc). This is
the lever for changing what a day is *about*.
- `CHARACTER_DAY_GOALS` - `scripts/simulate_dialogue_week.py:78`. Per-day,
per-character goals (e.g. Monday's Margaret: "You have strong feelings
about whether this concept respects the craft."). This is the lever for
changing what a specific character is trying to do on a specific day, and
is what the #6832 roster fix (Julian added to Monday, Devon to Tuesday)
had to be made consistent with.
- `_JUDGE_SYSTEM_PROMPT` - `backend/admin/cron_routes.py:267`. The judge's
own instructions and scoring rubric. Changing this changes what gets
measured, not what gets generated - treat it as a separate, rarer kind of
experiment (see "grader calibration" above), never bundle a judge-prompt
change with a character-prompt change in the same experiment.

## Cost budget

Per offline experiment at the default N=5 pairs: roughly 80 Haiku 4.5 turns
(dialogue generation for 5 pairs x 2 variants x ~8 turns/day x however many
days are in scope) plus 10 judge calls (5 pairs x 2 judge orders for the
position swap). Cap any single experiment run at 120 API calls total; if
`conversation_lab.py ab` would exceed that, reduce N or scope to fewer days,
do not silently let it run over.

`--dry-run` on any `conversation_lab.py` subcommand is free (renders the
prompts and call plan without hitting a model). `baseline` is also free - it
only reads already-generated data.

The portfolio-wide default budget is 20 API calls per task
(`~/projects/CLAUDE.md`). An offline conversation experiment is explicitly
over that by design, which is why every experiment run must be logged in
`EXPERIMENTS.md` - the log is what makes each one an explicit, reviewable
exception instead of a quiet budget violation.

## Rules

- One lever at a time. Never stack a prompt change with a judge change, or
two prompt changes, in the same experiment.
- Never regenerate a **published** week's dialogue. A published page is
frozen and its copy references specific dialogue beats
(`reference_dialogue_fast_loop.md`); regenerating breaks that link with no
way to re-sync it. An in-progress (not yet Sunday-published) week is fair
game.
- Anthropic-only for any new LLM call this lab introduces
(`feedback_anthropic_only.md`) - no new providers for dialogue generation
or judging.
- No autonomous prompt rewriting in production. Every prompt-lever change
ships as a human-reviewed PR with a `promptV` bump, per the experiment
steps above. The lab proposes; it does not self-deploy.

## Open hypothesis

Erik, 2026-09-06: the grader may have gone soft - every W30-W36 stage passed
the judge first try, with no FAIL recorded. That is either genuinely strong
dialogue or a judge that is too easy to please. Step 7 (grader calibration)
exists specifically to test this: run `scripts/conversation_lab.py calibrate`
against a small set of transcripts we already know are weak (the W36 BEFORE
transcript in `EXPERIMENTS.md` is a ready-made bad example: prompt leak,
technical error, repeated phrasing) and a set we know are strong, and check
whether the judge actually separates them. If calibration shows the judge
scores the known-bad transcript as well as the known-good one, the judge
prompt itself is the next lever - not the character prompts.
