# Research plan: getting AI characters to hold a believable meeting

Card #7776. Written 2026-09-29. Owner: Erik (direction), floor manager (execution).
Builds on [PROTOCOL.md](PROTOCOL.md) (method and tools), [DIALS.md](DIALS.md) (design
record and metric targets) and [EXPERIMENTS.md](EXPERIMENTS.md) (results log). Where this
plan and PROTOCOL.md differ, this plan governs the studies registered below; PROTOCOL.md
still governs everything else.

## 1. Question

**Primary.** Which generation settings (prompt constraints and conversation structure)
make a simulated team meeting read as a real meeting, instead of a sequence of agreeable
expert monologues?

**Secondary.** Do those settings hold across dialogue models: Claude Haiku 4.5 (production),
DeepSeek v4.1 Flash, and later Gemini and GPT?

Working definition of "a real meeting" (DIALS.md section 1): six specific people; a reader
can name the speaker with names covered; someone is sometimes wrong, tired or short; a
disagreement lands, gets answered with a reason, and resolves because of the reason.

## 2. What we already know (evidence going in)

| Finding | Source | Strength |
|---|---|---|
| A five-change "limits-off" bundle beats production baseline on Monday 13/1/0 (14 pairs) with BOTH Haiku and DeepSeek under the same Opus 4.6 judge | DAY_BY_DAY 09-27, 09-29 | Strong direction, small N, one judge |
| Both models trace the same curve across the week: strong Mon-Tue, about even by Sat-Sun | same | Moderate |
| The judge decides the result: the same DeepSeek Monday config scored 3/7/4 under a DeepSeek judge and 13/1/0 under Opus | 09-28 vs 09-29 | Strong: judge choice is not neutral |
| Limits-off lines run 80-140 words; the judge penalises register but still prefers them overall; a 25-line circular Claude transcript beat its baseline | transcript reads 09-29 | Suggests length bias; unmeasured |
| Numeric word caps shorten lines but remove pushback, in both models | 09-27, 09-28, 09-29 | Moderate |
| Fri-Sun: prior days leave nothing to decide; the stop check ends the day at about 4 lines with 2-3 speakers | 09-28 reads | Moderate |
| Characters cannot see the recipe but the judge can, so they lose technical credibility on invented ratios | W39, 09-27, 09-28 | Moderate |
| Already rejected or ruled out, not to be repeated: shared-rules trim (28.6% win), few-shot food scripts, per-character catchphrases, em-dash ban as a structure fix, few-shot depth sweep (#7206) | EXPERIMENTS.md, PROTOCOL.md 696-709 | - |

## 3. The instrument

The judge is a measuring instrument. It is held fixed for every study in this plan.

- **Judge:** `anthropic/claude-opus-4.6` is replaced by **`anthropic/claude-opus-5.5`** via
  OpenRouter, Anthropic-pinned route. The judge prompt is fixed by its
  `evaluator_prompt_sha256`, which every result file already records. A change to either the
  model or the prompt is a new instrument, and every comparison has to be rerun under it.
- **Pairwise, blind, position-swapped:** as in PROTOCOL.md 99-102. A pair where the two
  orientations disagree counts as a tie.
- **Production is not affected.** The live Sunday gate keeps its own judge. Moving production
  to Opus 5.5 is a separate decision.

### 3.1 Instrument validation (Study S0), run before any other study

A study result means nothing if the instrument has not been checked. Each check has a
threshold fixed now, in advance.

| Check | How | Pass threshold |
|---|---|---|
| V1 Works | 5 smoke pairs; parseable verdicts, no empty output (Opus 5.5 may reason within the 4096 ceiling) | 10/10 orientations valid |
| V2 Separates good from broken | `calibrate` (shuffled-order and rotated-speaker degradations) on 3 transcripts | real preferred >= 0.8 on both |
| V3 Test-retest | re-judge the same 30 pairs a second time | same verdict on >= 80% |
| V4 Position bias | share of orientation disagreements across V3 | <= 20% |
| V5 Length bias | across all S0-S2 pairs, logistic regression of "judge picked A" on the difference in word count, and the human-agreement rate split by "the longer transcript won" vs not | reported; if human agreement on longer-won pairs is below 60%, the judge prompt gets a length-neutrality line (a new instrument) and V1-V6 repeat |
| V6 Human agreement | Erik reads 30 pairs blind with `pairs --pick`, stratified across models and days | agreement >= 70% on non-tie pairs, with Cohen's kappa reported |
| V7 Self-preference | V6 split by dialogue model (a Claude judge may favour Claude text) | agreement gap between models <= 15 points; otherwise add a second-family judge (GPT) on a 30-pair subset for every cross-model claim |

## 4. Outcomes

**Primary outcome.** The judge's overall pairwise verdict, variant vs baseline (current
production settings). Reported as the win rate among decisive pairs, with a 95% Wilson
interval and the tie rate alongside. The significance test is a two-sided sign test on
decisive pairs.

**Guard outcomes.** The ten judge dimensions. A variant fails if it loses any dimension in
more than 50% of pairs (the PROTOCOL.md rule).

**Absolute benchmarks.** Deterministic, from `scripts/conversation_metrics.py`, measured with
`bench`. These are the "is it good yet" numbers, independent of the judge. The targets are
DIALS.md section 3:

| Metric | Production today | Target | Too far when |
|---|---|---|---|
| dash_clause_rate | 86% | 30-50% | technical credibility falls |
| length_stdev (words) | 8.1 | >= 10 | short lines are filler |
| short_line_rate (< 8 words) | 1% | 10-15% | qa_rate falls |
| frame_claim_rate ("that's the story") | 30% | <= 10% | arc_resolution falls |
| agree_opener_rate | 21% | 8-10% | adjacency falls |
| question_rate | 20% | 25-30%, with qa_rate up | questions go unanswered |
| mean words per line | 24 (limits-off: 80-140) | 10-30, varying by speaker | - |
| cast coverage | Margaret 30%; some roster members silent | every roster member speaks; nobody above 35% | - |
| cross-week repeated 4-grams (openers) | 24 of 25 Saturdays identical | 0 | - |

**Human check.** Erik's blind read (V6), repeated for every confirmatory result.

## 5. Design rules (apply to every study)

1. **One change per arm.** Every arm differs from its reference in exactly one registered
   change. Coupled knobs are named as coupled (OPEN_ENDED_MAX_TICKS requires the stop check;
   `simulate_dialogue_week.py:2325`).
2. **Paired design.** Each pair's baseline is regenerated with the same recipe and run index,
   so both arms share prompt inputs apart from the change.
3. **Two stages.**
   - **Screen:** 14 pairs (7 recipes x 2 runs), labelled *exploratory*. Its only job is to
     decide what earns confirmation.
   - **Confirm:** 42 pairs (7 recipes x 6 runs). Only confirmed results can ship.
   - Power (two-sided alpha 0.05, 80% power, decisive pairs): a true win rate of 0.75 needs
     29 pairs, 0.70 needs 47, 0.65 needs 85. At about 25% ties, 42 pairs give about 31
     decisive pairs. The exact sign test's power there is 0.77 at a true win rate of 0.75,
     0.64 at 0.72 and 0.54 at 0.70. So confirmation reliably detects about 0.75 and up.
     Smaller effects are out of reach at this budget, and the report must say so rather than
     claim a null.
4. **Multiple comparisons.** Within a study, Holm-Bonferroni across its arms on the primary
   outcome.
5. **Decision rule for "ships".** Confirmed win rate >= 65% with the Wilson lower bound
   > 50%, no guard dimension lost > 50%, register benchmarks not worse than the reference, and
   Erik's blind read agreeing (V6 standard). Then one live week and the DIALS.md section 7
   dial-back check.
6. **Days.**
   - Monday is the primary test day, because it has no prior-day dependency.
   - Later days are tested two ways:
     - **(a) Per-day tests** use one shared canonical frozen history, so a day's result is
       not confounded by which earlier choices were made.
     - **(b) The end-to-end week** is Erik's chained method (freeze the winner, carry it
       forward).
   - Cross-model comparisons use (a).
7. **Failures.** An arm that dies on a provider error is rerun whole, at most 3 times, and
   the failure is logged. Partial arms are never scored.
8. **Everything is logged.** One EXPERIMENTS.md row per run: code SHA, testbed version,
   variant file sha256, dialogue and judge model ids, served provider, judge prompt sha,
   N, W/T/L, cost, result file. Null and negative results are logged with the same weight.
9. **Pre-registration.** A study's hypotheses, arms, N and decision rule go into the registry
   (section 7) before its first paid call. Any change after that is logged as a deviation
   with its reason.

## 6. Studies

### S0. Instrument validation
Section 3.1. Reference model: Haiku. Estimated cost $4-6 plus about 1 hour of Erik's reading.

### S1. Absolute baseline
`bench` Monday, 10 runs each, on (a) the production baseline and (b) the limits-off bundle.
Produces the benchmark table in section 4 for both, so every later result can be read in
absolute terms, not only as "beat baseline". Estimated cost $4.

### S2. Ablation of the limits-off bundle (Erik, 09-29)
The bundle is locked as the reference arm B. Each of five arms puts back one production
setting:

| Arm | Change put back | Hypothesis (direction) |
|---|---|---|
| A1 | numeric word caps | win rate falls; pushback falls; mean words falls toward target |
| A2 | rewrite guards (repetition, shape, word budget) | win rate falls slightly; dash_clause_rate falls |
| A3 | history depth (production) | win rate falls on later turns; adjacency falls |
| A4 | fixed line count, stop check kept | cast coverage rises; win rate falls on long decision days |
| A5 | stop check off (and fixed line count, since the two are coupled) | agreement loops on non-arguing models |

- Each arm, and B itself, is scored against baseline (current production settings).
- The effect of a change is B's win rate minus that arm's win rate, with an interval for the
  difference.
- Limitation, stated up front: B sits near the ceiling (13/1/0), so this design detects
  changes whose removal *hurts*. It cannot show a change that helps when removed.
  Distinguishing those needs a head-to-head mode (a variant as the control arm), which the lab
  does not have yet. That is a small lab change, carded if S2 needs it.
- Model: Haiku first. It is production, and PROTOCOL.md 703-706 notes prompt sensitivity is
  model-specific.
- Screen: 6 arms x 14 pairs. Confirm the arms that move.
- Estimated cost: screen about $12 on Haiku; confirmation about $6 per arm.

### S3. Register without losing the argument
The open problem: limits-off gives real pushback in speeches, and numeric caps give chat
length with no pushback. Each lever is one arm against the S2 winner, screened then confirmed:

| Arm | Lever | Predicted movement | Guard |
|---|---|---|---|
| R1 | non-numeric length guidance ("most turns a sentence or two; longer only when you are arguing a point") replacing the HARD LIMIT line (DIALS 2a, line 370) | mean words to 10-30, length_stdev up, pushback kept | arc_resolution, qa_rate |
| R2 | turn floor = cast size + 1 on open-ended days | cast coverage up on Fri-Sun | turn_taking |
| R3 | recipe visible to the characters (the W39 gap) | technical_credibility up | title_fidelity |
| R4 | director scene-setting (#7679, RNG plus no-repeat log; Erik 09-27) | Fri-Sun natural_progression up | no-repeat check |

The order is R1, R2, R3, R4. A lever that is not in this table needs its own registry row
before it runs.

### S4. End-to-end week
The best confirmed configuration, run as Erik's chained week (Monday first, freeze, carry
forward) on the fixed protocol. This is the result that decides what ships. Shipping chained
days needs production to carry prior days forward (currently carded; cron_routes has none).

### S5. Model replication
S1, S2 (screen) and S4, repeated per dialogue model, same instrument, same testbed:

| Order | Model | Tier | Status |
|---|---|---|---|
| 1 | anthropic/claude-haiku-4.5 | small/fast (production) | rerun under Opus 5.5 |
| 2 | deepseek/deepseek-v4.1-flash | small/fast | rerun under Opus 5.5 |
| 3 | Gemini (small/fast tier, e.g. google/gemini-3.8-flash; top tier gemini-3.1-pro-preview as a secondary arm) | - | future |
| 4 | GPT (small/fast tier, e.g. openai/gpt-6-luna; larger tier as a secondary arm) | - | future |

- The primary cross-model comparison is like-for-like tier. A larger tier is reported
  separately, because "a bigger model wins" is a different claim.
- Cross-model claims require V7 to pass.

## 7. Study registry (the spreadsheet)

Every study gets its row before its first paid call. Results go into the row and into
EXPERIMENTS.md.

| ID | Question | Model | Arms | N (screen / confirm) | Primary outcome | Decision rule | Status | Result | Cost |
|---|---|---|---|---|---|---|---|---|---|
| S0 | Is Opus 5.5 a valid judge? | Haiku, DeepSeek | V1-V7 | see 3.1 | thresholds in 3.1 | all pass | planned | - | - |
| S1 | Where are baseline and bundle in absolute terms? | Haiku | 2 | 10 runs each | section 4 metrics | descriptive | planned | - | - |
| S2 | Which of the five changes matter? | Haiku | B + A1-A5 | 14 / 42 | win rate vs baseline | section 5.5, Holm | planned | - | - |
| S3 | Can we keep pushback at chat length? | Haiku | R1-R4 | 14 / 42 | win rate vs S2 winner | section 5.5 | planned | - | - |
| S4 | Best config, full week | Haiku | chained | 14 per day | win rate per day | section 5.5 per day | planned | - | - |
| S5-DS | Replication | DeepSeek v4.1 Flash | S1, S2 screen, S4 | as above | as above | as above | planned | - | - |
| S5-GM | Replication | Gemini | as above | as above | as above | as above | future | - | - |
| S5-GPT | Replication | GPT | as above | as above | as above | as above | future | - | - |

Superseded, kept for the record: the 09-27 Claude week and 09-29 DeepSeek rerun (Opus 4.6
judge), and the 09-28 DeepSeek week (DeepSeek judge, not comparable). These are exploratory
evidence that motivated this plan. They are not results under it.

## 8. Threats to validity

| Threat | Mitigation |
|---|---|
| Judge prefers length | V5; length-matched reads in V6; register benchmarks as guards |
| Judge prefers its own model family | V7; a second-family judge subset if V7 fails |
| Small N | two-stage design, Wilson intervals, stated minimum detectable effect; no "no effect" claims from screens |
| 7 recipes are not every recipe | fixed testbed v3 for comparability; one out-of-sample live week before shipping (DIALS section 7) |
| Correlated runs of the same recipe | report per-recipe W/T/L; a result carried by one recipe is flagged |
| Frozen prior days differ between models | per-day tests on a shared canonical history (5.6a) |
| Provider drift or outage (the 09-29 OpenRouter 404 window) | served provider logged; whole-arm rerun policy (5.7) |
| Model version drift | model ids and served provider logged; a changed snapshot is a new model |
| Researcher degrees of freedom | pre-registration (5.9); mechanical freeze rule; transcript reads recorded as reads, never as the decision |

## 9. Budget

| Block | Estimate |
|---|---|
| S0 | $4-6 |
| S1 | about $4 |
| S2 screen + confirm 2-3 arms | about $25 |
| S3 screen + confirm 1-2 arms | about $20 |
| S4 | about $10 |
| S5-DS | about $12 |
| **Program to S5-DS** | **about $75-80** |
| S5-GM, S5-GPT | about $15-40 each, depending on tier |

The OpenRouter key has $32.81 left (09-29). That covers S0-S2. The rest needs a top-up before
S3. `--max-cost` stays a runaway guard per run, not the budget.

## 10. Decisions this plan needs from Erik

1. Approve Opus 5.5 as the fixed lab judge (section 3). Production's gate is unchanged.
2. Approve the two-stage N (14 screen / 42 confirm) and the ship rule in 5.5.
3. The top-up timing for S3 onward.
4. For S5: like-for-like tier as the primary cross-model comparison.
