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
- **`scripts/conversation_lab.py`** - the experiment runner. Four
subcommands: `baseline` (capture a week's current scores with no
generation, free), `ab` (offline blind position-swapped pairwise A/B
between a control and a variant prompt lever, costs Haiku + judge calls),
`calibrate` (sanity-check the judge itself against known-good and
known-bad transcripts, costs judge calls), and `pairs` (a free, offline
blind human read of an already-completed `ab` result - see "Blind human
read" below). `baseline` calls `scripts.conversation_metrics.summarize()`
per day for its deterministic numbers. Run `--help` on the script and on
each subcommand for the exact flags; they may still be changing.
- **`scripts/conversation_metrics.py`** - deterministic, non-LLM metrics
(turn length, echo-of-shared-rules detection, cast coverage against the
expected roster, repeated-phrase detection). This is the experiment side's
library, imported by `conversation_lab.py` - **not** by `review_episode.py`,
which is a separate, simpler weekly read: it imports only
`backend.utils.episode_integrity` (title and persisted-`judge_scores`
extraction) and `scripts.simulate_dialogue_week` (`DAY_ORDER`,
`participants_for_day`, for cast-coverage checks). The two scripts do not
call each other; `review_episode.py` is the weekly read, `conversation_lab.py`
and `conversation_metrics.py` are the experiment side.
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
by more than 50% of pairs. `ab --runs` is required with no default - N is
chosen explicitly per experiment. Test-bed mode (`ab --testbed`,
see "Test bed" below) is the one exception: it defaults to 3 pairs per
scenario. Whatever N is, keep it small and treat the result as a signal to
act on, not statistical proof - a borderline result (e.g. 3/5 at N=5) is
inconclusive, not a win.
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

## Areas

Named heat-map regions (Erik, 2026-09-06): patterns that recur across many
different weeks' dialogue no matter which dish is in the prompt, found by
grepping the corpus for phrases and shapes that repeat across weeks rather
than by reading one week closely. Each names the metric(s) that track it
(all shipped in `scripts/conversation_metrics.py` and
`scripts/conversation_heatmap.py`) and the prompt lever most likely to move
it.

- **Structure** - claim-dash-mechanism sentences ("X sets the tone - the
sear locks in the crust"), a uniform turn length around 24 words, almost no
short lines. Metric: `dash_clause_rate` and `length_stdev`, alongside the
existing `length_stats` mean (all shipped in `conversation_metrics.py`).
Lever: `_SHARED_CHARACTER_RULES` / `_REACTION_DIRECTIVE`
(`scripts/simulate_dialogue_week.py:368` / `:248`).
- **Frames** - "X is the story / the hook / the key", "that's what / where
...". Metric: `frame_claim_rate` (shipped). Lever:
`_SHARED_CHARACTER_RULES` / `_REACTION_DIRECTIVE`.
- **Agree-openers** - a turn opening with agreement before anything else:
"Fair", "Yeah", "Marcus is right". Metric: `agree_opener_rate` (shipped).
Lever: `_SHARED_CHARACTER_RULES` / `_REACTION_DIRECTIVE`.
- **Boilerplate** - specific lines recited near-verbatim across many weeks
regardless of the dish: the Saturday "recipe now should be live in a few"
line appeared in 23 of 25 weeks; the photography "cross section / three
quarter / staging the muffin pan" cluster appeared in 13-14 weeks. Metric:
cross-week phrase recitation, now `conversation_heatmap.py`'s hot-phrase
detector (an n-gram appearing in at least `--min-weeks` distinct weeks,
classified into the `Boilerplate` AREA) - distinct from `repeated_phrases`'
within-episode n-gram detector, which stays in `conversation_metrics.py`.
Lever: `DAY_STAGE_DIRECTIONS` (`scripts/simulate_dialogue_week.py:134`) -
Saturday's one-line direction is what the model keeps reciting.
- **Pitch vocabulary** - Ria's own recurring phrases: "stops the scroll" in
16 of 25 weeks, "people need to see" close behind. Metric: `pitch_vocab_rate`
(shipped in `conversation_metrics.py`) plus the same cross-week hot-phrase
detector, classified into the `Pitch vocabulary` AREA. Lever:
`CHARACTER_DAY_GOALS` (`scripts/simulate_dialogue_week.py:78`) - Ria's
per-day goal text is what teaches her the phrase.
- **Brand reinforcement** - the pan-case mechanism justification the shared
rules force reads as marketing copy dropped into a group chat, even though
the literal noun is rare: 13% of lines corpus-wide carry a brand/mechanism
term, 16% in W36, and 32% of Monday's lines against only 7-12% on every
other day - spread evenly across the cast (every character sits at
11-15%), with just ~3 literal "muffin pan" / "muffin tin" mentions in an
average week. The forced feel comes from the mechanism-justification
language the Monday pan-first rules demand, not from the noun itself.
Metric: `brand_term_rate` in `conversation_metrics.py` (`BRAND_TERM_PATTERNS`,
`AREA_METRICS["Brand reinforcement"]`); `conversation_heatmap.py` reports it as
an area and in the structure-rates-by-day table. Lever: the Monday-specific rules in
`scripts/simulate_dialogue_week.py` - `CHARACTER_DAY_GOALS`'s `monday`
entries (`scripts/simulate_dialogue_week.py:79-86`), `DAY_STAGE_DIRECTIONS`'s
`monday` entry (`scripts/simulate_dialogue_week.py:135`), and the pan-first
/ mechanism clauses inside `_SHARED_CHARACTER_RULES`'s "THE MUFFIN PAN IS
THE POINT" block (`scripts/simulate_dialogue_week.py:386-400`).

The dash-clause, frame-claim, and agree-opener rates above shipped as real
functions in `conversation_metrics.py` (`dash_clause_rate`,
`frame_claim_rate`, `agree_opener_rate`), alongside `short_line_rate`,
`question_rate`, `length_stdev`, `opener_diversity`, and `pitch_vocab_rate`
- all eight are what `conversation_lab.py baseline` reports per day via
`conversation_metrics.summarize()`. The cross-corpus read of these same
rates plus recurring phrases, each hot phrase and each dialogue line
classified into an AREA (`Frames`, `Agree-openers`, `Boilerplate`, `Brand
reinforcement`, `Pitch vocabulary`, `Other` - see `conversation_heatmap.py --help`
for the exact flags), now runs as `scripts/conversation_heatmap.py` over every
published week at once, with a structure-rates-by-day table that shows the
Monday concentration of brand terms. The numbers in "Corpus baseline" below
were first computed by hand on 2026-09-06 and then reproduced by the shipped
scripts (dash-clause 0.76-0.98 per week, Monday brand_term_rate ~0.33).

## Corpus baseline 2026-09-06

One-off read across the full corpus (W11-W36, 917 dialogue lines) to size
the areas above before picking a lever. W36 is broken out separately
because it is also the Test bed's sweet scenario below.

| Metric | Corpus (W11-W36) | W36 alone |
|---|---|---|
| Dash-clause sentences | 86% | 91% |
| Frame claims | 30% | 33% |
| Agree-openers | 21% | 26% |
| Lines under 8 words | 1% | 2% |
| Mean turn length | 24.1 words (stdev 8.1) | 24.5 words (stdev 7.8) |
| Questions | 20% | 16% |

The em-dash ban (house style, this card) moved the model from an em dash to
a plain hyphen but did not move any of the numbers above - the glyph was
never the problem. Structure is a sentence-shape habit, not a punctuation
habit.

## Test bed

Shipped: five frozen scenarios, one per named dish, stored in
`docs/conversation-lab/testbed.json` so that every `ab` run compares across
the same five weeks instead of whichever week happens to be current.

- **W36 - Caramelized Custard Tart Cups** - sweet.
- **W32 - Miso Ginger Donburi Cups** - savory. This is the baseline week
(see the Weekly rubric history table in `EXPERIMENTS.md`).
- **W27 - Paneer Saag Egg Bites** - breakfast.
- **W33 - Kimchi Cheddar Rice Cups** - party.
- **W11 - Make-Ahead Veggie & Sausage Egg Cups** - the oldest week with
dialogue, for range across the corpus's age.

`ab --testbed` runs every scenario in the panel (bare flag defaults to the
file above; pass a path to use another one) instead of a single `--concept`
- it forbids `--concept`, `--recipe-context`, and `--from-episode`, since
each scenario supplies its own. `--runs` still sets pairs per scenario
(default 3 in testbed mode, so 15 pairs across all five by default) and the
report breaks results down per scenario plus an aggregate across all of
them, so a lever's win rate compares month to month on the same fixed set
instead of a fresh sample each time that happens to include whatever week
is current. See `ab --help` for the exact flags.

## Lab judge dimensions

Shipped: the lab's pairwise judge - shared by `ab` and `calibrate`, both of
which judge two transcripts head to head - scores the production eight
(`title_fidelity`, `arc_resolution`, `voice_distinctiveness`,
`technical_credibility`, `natural_progression`, `promise_delivery`,
`turn_taking`, `cast_coverage` - `_JUDGE_SYSTEM_PROMPT`,
`backend/admin/cron_routes.py:267`) plus two lab-only dimensions; both are
valid `--target` choices on `ab` (see `ab --help`):

- **emotional_range** - does the day move through more than one register of
feeling (frustration, delight, doubt), or does every line sit at the same
even keel?
- **register_naturalness** - do characters shift register mid-conversation
the way people actually do (joke, then get serious), or does the whole day
read at one flattened formality level?

The production judge (`_JUDGE_SYSTEM_PROMPT`) stays at eight dimensions
until one of these two proves useful in the lab - this is a separate, rarer
kind of experiment from a character-prompt change (see "Where the levers
live" above), and no lab-only dimension reaches production without its own
review.

## Blind human read

Shipped: `conversation_lab.py pairs --from <result> --show` prints a
completed `ab` result's pairs with the control/variant labels stripped,
order randomised per pair (seeded from the pair's position in the file, so
a re-run of `--show` on the same result reproduces the same labeling).
`--pick 'POSITION:A|B|tie,...'` records which transcript Erik preferred in
each pair back into the result file as `human_picks` and reports his
agreement rate with the judge's own overall verdict on the same pairs,
mapped back through the stored A/B order. See `pairs --help` for the exact
flags.

Erik is the reference grader here - not a second opinion to reconcile with
the judge, the standard the judge is being checked against. This costs
nothing (it reads an already-completed `ab` result; no new API calls) and
is how "the judge might have gone soft" (see "Open hypothesis" below) gets
checked without waiting for a live-week PASS streak to look suspicious.

## Cost budget

`ab --runs` has no default - pick N per experiment. At N=5 pairs (a common
choice, not a default): roughly 80 Haiku 4.5 turns (dialogue generation for
5 pairs x 2 variants x ~8 turns/day x however many days are in scope) plus
10 judge calls (5 pairs x 2 judge orders for the position swap). Cap any
single experiment run at 120 API calls total; if `conversation_lab.py ab`
would exceed that, reduce N or scope to fewer days, do not silently let it
run over.

`--dry-run` on `ab` and `calibrate` is free (renders the prompts and call
plan without hitting a model). `baseline` has no `--dry-run` flag and needs
none - it is already free, since it only reads already-generated data and
never calls a model.

The portfolio-wide default budget is 20 API calls per task
(`~/projects/CLAUDE.md`). An offline conversation experiment is explicitly
over that by design, which is why every experiment run must be logged in
`EXPERIMENTS.md` - the log is what makes each one an explicit, reviewable
exception instead of a quiet budget violation. `conversation_lab.py ab`
does this automatically: every completed run (single-concept or
`--testbed`) appends a row to the Experiments table via `--experiments-log`
(default `docs/conversation-lab/EXPERIMENTS.md`; pass `--no-log` to skip).
`calibrate` does not append a row - it reports a GRADER OK / not-OK verdict
per degradation instead of a lever result.

## Budget

Erik's standing cap, set 2026-09-06: **$5 per experiment**, shipped as
`--max-cost`, defaulting to `5.00` on both `conversation_lab.py ab` and
`calibrate`, alongside the existing `--max-calls 120` cap above. Every run
logs calls and cost via `model_router.get_cost_summary()`, checked before
every paid unit; the run aborts with a partial result once the running
total has reached the cap. See `ab --help` / `calibrate --help` for the
exact flag.

The price-table gap flagged 2026-09-06 has since closed: `_COST_PER_M_TOKENS`
in `backend/utils/model_router.py` now prices `claude-opus-4-6` - the judge
model (`backend/config.py:156`) - at $5 input / $25 output per million
tokens, alongside `claude-haiku-4-5-20251001` and `claude-sonnet-4-6`, so a
judge call an experiment makes is counted at its real cost instead of the
`(0.0, 0.0)` fallback `_record_cost` uses for an unpriced model.
`cost_summary` in an `ab` or `calibrate` report, and the `--max-cost` cap
above, therefore reflect Haiku plus judge spend together, not Haiku spend
alone. `backend/utils/model_router.py` is outside this slice's ownership
(docs-only); this section just records that the gap closed.

## Lever catalog

Twelve candidate levers, one per experiment - never stack two (see "Rules"
below). Each names its target area (see "Areas" above) and the movement it
predicts.

1. **Sentence-length variation rule** - target: Structure. Predicted:
short-line rate up, length stdev up.
2. **At most one dash per line / no dash clause** - target: Structure.
Predicted: dash-clause rate down.
3. **Never open by agreeing** - target: Agree-openers. Predicted:
agree-opener rate down.
4. **Ban pronouncement frames**, possibly via the `PROHIBITED` list
(`scripts/simulate_dialogue_week.py:207`) - target: Frames. Predicted:
frame-claim rate down.
5. **Per-character REGISTER notes** (terse / hedging / needling / deadpan)
instead of catchphrases - target: voice up, pitch vocabulary down.
6. **Remove** the "name a mechanism specific to this dish" clause (added in
v2, see "Prompt versions" in `EXPERIMENTS.md`) to test whether our own rule
is what induces the claim-dash-mechanism sentence - target: Structure.
7. **Rewrite the Saturday `DAY_STAGE_DIRECTIONS` entry** so the deploy line
is not recited verbatim - target: Boilerplate.
8. **Stylistic exemplars from a non-food domain**, with the echo detector
(`is_prompt_echo`, `scripts/simulate_dialogue_week.py:1006`) extended to
cover them - target: Structure / Frames. Guarded: the W36 prompt leak came
from a food example, so a non-food exemplar needs the same leak-detection
before it ships, not less.
9. **Give each turn the full day's scene** instead of the last 4-8 lines -
target: turn_taking.
10. **An explicit "say something short or ask a question at least once per
day" per-character goal** - target: short-line rate, question rate.
11. **Soften or remove the Monday pan-first / mechanism-per-line rule** -
target: Brand reinforcement and Structure. The "THE MUFFIN PAN IS THE
POINT" block in `_SHARED_CHARACTER_RULES`
(`scripts/simulate_dialogue_week.py:386-400`) and Monday's
`CHARACTER_DAY_GOALS` / `DAY_STAGE_DIRECTIONS` entries are what push
`brand_term_rate` to 32% on Mondays against 7-12% other days (see "Areas"
above). Predicted: `brand_term_rate` down, dash-clause rate down (the
mechanism-justification clause is itself a claim-dash-mechanism sentence
generator). Guard: `title_fidelity` and `technical_credibility` must not
fall - the pan-case rule also does real work (it is the only place the
prompt asks a character to justify the dish belonging in a muffin pan at
all), so if either guard dimension drops, the rule was earning its keep and
the change should be dialed back, not shipped.
12. **Give the Wednesday dialogue a real vision description of the three
generated shots** instead of the image metadata (filename, prompt text) it
gets today, so the photography critique is about the actual image - one
vision call per Wednesday, Anthropic vision model, per the Anthropic-only
rule (`feedback_anthropic_only.md`). Target: technical_credibility and
voice_distinctiveness (Julian's critique should differ shot to shot instead
of reciting the same "cross section / three quarter" boilerplate - see
"Areas" above). Guard: cost (one extra vision call per week is small, but
must be logged and priced the same way judge calls are, see "Budget"
above) and the Anthropic-only rule - no other vision provider, even if
cheaper.

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

## Advice considered and rejected (2026-09-06, from a Gemini suggestion Erik shared)

- **Per-character catchphrases** - rejected. We already have them; "stops
the scroll" in 16 of 25 weeks (see "Areas" above) is the disease this card
is trying to cure, not a technique to add more of.
- **Few-shot food script examples** - rejected. The W36 verbatim prompt
leak (see "Prompt versions" v1 in `EXPERIMENTS.md`) came from exactly one
worked example in `_SHARED_CHARACTER_RULES`; a few-shot script is the same
failure mode at larger scale.
- **Testing levers on a different model than production** - rejected.
Prompt sensitivity is model-specific; a lever validated on a different
model tells us nothing about how it will behave on
`claude-haiku-4-5-20251001`, the actual production dialogue model.
- **Porting to open-weight models** - rejected. Contradicts the
Anthropic-only decision (`feedback_anthropic_only.md`); dialogue already
costs roughly 10 cents a week, so there is no cost problem to solve by
switching providers.

Adopted from the same suggestion: the fixed test bed (see "Test bed"
above), emotional range as a judge dimension (see "Lab judge dimensions"
above), and sentence-length / negative structural constraints as levers
(catalog items 1 and 2 above).

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

## Weekly sweep

Erik's target, 2026-09-06: ten offline single-lever experiments a week.
Running ten independent `ab --testbed` calls would pay for the control side
of the comparison ten times over; `ab --sweep DIR` runs every
variant file in `DIR` against ONE shared control on the test bed, so the
control is generated and judged once and each variant is judged only
against that shared baseline. Report: a ranking table (judge wins on the
target dimension per variant, most winning first) plus a per-area heat-map
delta for each variant - did dash-clause sentences, frame claims,
agree-openers, and brand mentions fall; did boilerplate recitation fall;
did short-line rate and question rate rise (see "Areas" above for what each
of these already measures).

Rough cost: about $0.70 per variant plus roughly $0.30 for the one shared
control, so a ten-variant week runs about $7-8 total against the $5 cap in
"Budget" above being a per-run limit, not a per-week one - `--max-cost` on
each `ab --sweep` invocation still caps that single sweep. Winners ship one
at a time to live weeks, never a batch, so a live-week regression can be
attributed to exactly one lever (see "Rules" below).

## Personality dials principle

A character has a stable core - what they care about, how they argue, the
things that make them "them" - plus a per-day STATE sampled fresh each day:
slept badly, distracted, in a good mood. The STATE shifts line length,
patience, and pushback for that one day only. It is never a fixed setting
("Devon is terse") and never a schedule ("Devon is angry on Tuesdays") -
both of those are exactly the kind of catchphrase-shaped determinism this
card already rejected once (see "Advice considered and rejected" above).
Erik, 2026-09-06: "People have good days and bad days ... those things
change every day." See `docs/conversation-lab/DIALS.md` (being drafted
separately) for the per-character detail - core traits, STATE ranges, and
how STATE gets sampled.

## Sitcom / B-plot layer

Not now. Erik, 2026-09-06: "we have not figured out writing at all" - this
layer is a future direction, not a lever to pick up next. The design
already exists in `docs/CREATIVE_BIBLE.md` ("The Sitcom Formula" section:
the recipe is the A-plot, a character moment - Julian having an off day,
Marcus trying a new writing style - is the B-plot, and a B-plot does not
need to resolve; it can carry across episodes), and the plumbing to inject
one already exists (`injected_event` is threaded through
`backend/admin/cron_routes.py` and `scripts/simulate_dialogue_week.py`,
consumed by `simulate_dialogue_week.py`'s prompt assembly). Gate before
picking this up: the Structure, Frames, and Brand reinforcement areas
inside target for two consecutive live weeks, and grader calibration
passing (see "Open hypothesis" above) - then ONE offline A/B of a single
injected event on the test bed, before any live use.
