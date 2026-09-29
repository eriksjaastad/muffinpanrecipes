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

### 3.1 Instrument validation (Study S0)

A study result means nothing if the instrument has not been checked. Each check has a
threshold fixed now, in advance. S0 has two parts.

**S0a, the gate. Nothing else runs until it passes.** It includes two pilot screens: the
limits-off bundle vs baseline, 14 pairs each on Haiku and on DeepSeek, both judged by Opus
5.5. Those 28 pairs are the data for V4, V6 and V7, and the Haiku screen doubles as S2's
screen of the reference arm B.

| Check | How | Pass threshold |
|---|---|---|
| V1 Works | 5 smoke pairs; parseable verdicts, no empty output (Opus 5.5 may reason within the 4096 ceiling) | 10/10 orientations valid |
| V2 Separates good from broken | `calibrate` (shuffled-order and rotated-speaker degradations) on 3 transcripts, judged by Opus 5.5 (needs P1) | real preferred >= 0.8 on both |
| V3 Test-retest | re-judge the 28 pilot pairs a second time from the saved transcripts (needs P2) | same verdict on >= 80% |
| V4 Position bias | share of pilot pairs whose two orientations disagree, computed from the saved `judge_orientations` | <= 20% |
| V6 Human agreement | Erik reads the 28 pilot pairs blind with `pairs --pick` | agreement >= 70% on non-tie pairs, with Cohen's kappa reported |
| V7 Self-preference | V6 split by dialogue model (a Claude judge may favour Claude text) | agreement gap between models <= 15 points; otherwise a second-family judge (GPT) is added on a 30-pair subset for every cross-model claim |

**S0b, running checks. Re-evaluated before any confirmatory claim.**

| Check | How | Threshold |
|---|---|---|
| V5 Length bias | across all judged pairs so far, logistic regression of "judge picked A" on the difference in word count; plus human agreement split by "the longer transcript won" vs not, from every blind-read batch | if human agreement on longer-won pairs is below 60%, the judge prompt gets a length-neutrality line (a new instrument), S0a repeats, and results judged under the old prompt are marked superseded |
| V6 (ongoing) | Erik blind-reads a 10-pair sample of every confirmation | agreement >= 70% |

### 3.2 Prerequisites (lab work before S0a)

The lab as it stands cannot run all of this. The following is the minimum, kept as one PR:

| ID | What | Why | Size |
|---|---|---|---|
| P0 | `scripts/lab_models.json` sets pairing each dialogue model with the Opus 5.5 judge | the instrument | data only |
| P1 | `calibrate --models` | V2 with the Opus 5.5 judge; `calibrate` has no model-set option today | small |
| P2 | a re-judge command: re-run the judge on a saved `ab` result without regenerating | V3 test-retest | small |
| P3 | offline metrics script: `conversation_metrics` on both arms of every saved `ab` result, per recipe | S1 absolute benchmarks at zero API cost | analysis script |
| P4 | a variant key that appends the scenario's `judge_recipe_facts` to the speakers' recipe context | R3; the variant mechanism changes simulator attributes, not scenario inputs | small |
| P5 | head-to-head mode (a variant as the control arm) | required before S3, whose arms are compared against the S2 winner, not production; also answers S2's second question (see S2, limitation) | medium; not needed for S0-S2 |

## 4. Outcomes

**Primary outcome.** The judge's overall pairwise verdict, variant vs baseline (current
production settings). Reported as the win rate among decisive pairs, with a 95% Wilson
interval and the tie rate alongside. The significance test is a two-sided sign test on
decisive pairs.

**Guard outcomes.** The ten judge dimensions. A variant fails if it loses any dimension in
more than 50% of pairs (the PROTOCOL.md rule).

**Absolute benchmarks.** Deterministic, from `scripts/conversation_metrics.py`, computed
offline on both arms of every saved `ab` result (P3). These are the "is it good yet" numbers,
independent of the judge. The targets are DIALS.md section 3:

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
   - Power (exact two-sided sign test, alpha 0.05, 80% power, counted in decisive pairs): a
     true win rate of 0.75 needs 30 decisive pairs, 0.70 needs 49, 0.65 needs 90. At about
     25% ties, 42 pairs give about 31 decisive pairs. Power there is 0.77 at a true win rate
     of 0.75, 0.64 at 0.72 and 0.54 at 0.70. So confirmation reliably detects about 0.75 and
     up. Smaller effects are out of reach at this budget, and the report must say so rather
     than claim a null.
   - These figures assume independent pairs. They are optimistic, because six runs of the
     same recipe share inputs (rule 4).
4. **Inference.**
   - **What a claim covers:** the seven fixed testbed-v3 recipes, treated as fixed effects.
     Given a recipe, each run is an independent generation, so pairs are conditionally
     independent and a pair-level test is valid for the claim "this change wins on this
     testbed". A statistical claim about recipes outside the testbed is *not* made. The
     recipe-level test below would need all 7 recipes in favour (the smallest achievable
     two-sided p with 7 clusters is 0.016). Generalization is addressed by the out-of-sample
     live week (rule 5) and by growing the testbed.
   - **Primary test:** exact two-sided sign test on decisive pairs, Holm-Bonferroni-adjusted
     across the arms of the study.
   - **Estimate:** win rate with a 95% Wilson interval, reported alongside and never used as
     the test.
   - **Heterogeneity checks, required to ship:**
     - the variant must be favoured in at least 5 of 7 recipes, so a result carried by one or
       two recipes does not ship;
     - a cluster bootstrap over recipes (10,000 draws) 95% interval is reported, as a
       descriptive measure of how much the result depends on which recipes are in the panel;
     - the design effect is reported.
5. **Decision rule for "ships".** All of the following:
   - a confirmation with a Holm-adjusted sign-test p < 0.05 and a win rate >= 65%;
   - the rule 4 clustering checks pass;
   - no guard dimension lost > 50%;
   - register benchmarks not worse than the reference;
   - Erik's blind read agreeing (V6 standard).

   Then one live week and the DIALS.md section 7 dial-back check. (A Wilson lower bound above
   50% is not enough on its own: 21 of 31 decisive pairs clears it at 50.1%, but the sign
   test gives p = 0.071.)
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
Section 3.1. S0a runs after the prerequisites (3.2); S0b runs alongside every later study.
Estimated cost: S0a $5-7, including the two pilot screens, plus about 1.5 hours of Erik's
reading.

### S1. Absolute baseline
The section 4 benchmarks computed by P3 on both arms of every `ab` result, starting with the
S0a pilots (baseline and bundle, Haiku and DeepSeek). Every later result is read in absolute
terms, not only as "beat baseline". No API cost. `bench` is not used: it takes no variant or
testbed and scores with the production judge, not the fixed lab instrument.

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
- Screen: A1-A5 at 14 pairs each. B's screen is the S0a Haiku pilot. Confirm B plus the arms
  that move.
- Estimated cost: screen about $12 on Haiku; confirmation about $6 per arm.

### S3. Register without losing the argument
The open problem: limits-off gives real pushback in speeches, and numeric caps give chat
length with no pushback. Each lever is one arm compared head-to-head against the S2 winner
(needs P5), screened then confirmed:

| Arm | Lever | Predicted movement | Guard |
|---|---|---|---|
| R1 | non-numeric length guidance ("most turns a sentence or two; longer only when you are arguing a point") replacing the HARD LIMIT line (DIALS 2a, line 370) | mean words to 10-30, length_stdev up, pushback kept | arc_resolution, qa_rate |
| R2 | turn floor = cast size + 1 on open-ended days | cast coverage up on Fri-Sun | turn_taking |
| R3 | the speakers also see the scenario's `judge_recipe_facts`, the amounts and details the judge scores against. Testbed v3 already gives them the ingredient names and boundaries (PROTOCOL.md 247), so this is the rest of the W39 gap. Baseline arm unchanged; needs P4. | technical_credibility up | title_fidelity |
| R4 | director scene-setting (#7679, RNG plus no-repeat log; Erik 09-27) | Fri-Sun natural_progression up | no-repeat check |

The order is R1, R2, R3, R4. A lever that is not in this table needs its own registry row
before it runs.

### S4. End-to-end week
The best confirmed configuration, run as Erik's chained week (Monday first, freeze, carry
forward) on the fixed protocol.
- Each day is screened at 14 pairs. This is an end-to-end screen: it finds the days where the
  configuration helps, hurts or does nothing.
- A day only ships a non-baseline configuration after its own 42-pair confirmation under rule
  5 (5.5). Days that don't confirm keep baseline.
- Shipping chained days needs production to carry prior days forward (currently carded;
  cron_routes has none).

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
| P | Lab prerequisites P0-P4 (before S0a); P5 (before S3) | - | - | - | - | tests pass, independent review | planned | - | - |
| S0a | Is Opus 5.5 a valid judge? (gate) | Haiku, DeepSeek | V1-V4, V6, V7 + two 14-pair pilots | 28 pilot pairs | thresholds in 3.1 | all pass | planned | - | - |
| S0b | Is the judge biased toward length? (running) | all | V5, ongoing V6 | every judged pair | 3.1 | 3.1 | planned | - | - |
| S1 | Where are baseline and bundle in absolute terms? | all | both arms of every ab result | no API calls | section 4 metrics | descriptive | planned | - | $0 |
| S2 | Which of the five changes matter? | Haiku | B + A1-A5 | 14 / 42 | win rate vs baseline | section 5.5, Holm | planned | - | - |
| S3 | Can we keep pushback at chat length? | Haiku | R1-R4, head-to-head (P5) | 14 / 42 | win rate vs S2 winner | section 5.5 | planned | - | - |
| S4 | Best config, full week | Haiku | chained | 14 per day / 42 per day that changes | win rate per day | screen; 5.5 per shipped day | planned | - | - |
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
| Small N | two-stage design, exact sign test, stated minimum detectable effect; no "no effect" claims from screens |
| 7 recipes are not every recipe | fixed testbed v3 for comparability; one out-of-sample live week before shipping (DIALS section 7) |
| Correlated runs of the same recipe | claims scoped to the fixed 7-recipe testbed, where runs are conditionally independent (5.4); 5-of-7 recipe rule required to ship; cluster bootstrap and design effect reported; per-recipe W/T/L reported |
| Frozen prior days differ between models | per-day tests on a shared canonical history (5.6a) |
| Provider drift or outage (the 09-29 OpenRouter 404 window) | served provider logged; whole-arm rerun policy (5.7) |
| Model version drift | model ids and served provider logged; a changed snapshot is a new model |
| Researcher degrees of freedom | pre-registration (5.9); mechanical freeze rule; transcript reads recorded as reads, never as the decision |

## 9. Budget

**Unit costs,** measured on 09-27 and 09-29 with the Opus 4.6 judge and scaled to Opus 5.5
($4/$20 per M vs $5/$25):
- a Haiku pair costs about $0.16 (both arms plus two judge orientations);
- a DeepSeek pair costs about $0.07.

A head-to-head pair costs the same as a normal pair.

| Block | Arithmetic | Estimate |
|---|---|---|
| Prerequisites P0-P5 | engineering time | no API cost |
| S0a | Haiku pilot 14 x $0.16 + DeepSeek pilot 14 x $0.07 + smoke, calibrate and re-judge of 28 pairs (judge only) | about $6 |
| S1 | offline | $0 |
| S2 | screen A1-A5: 70 x $0.16 = $11; confirm B + 2-3 arms: 126-168 x $0.16 = $20-27 | $31-38 |
| S3 | screen R1-R4: 56 x $0.16 = $9; confirm 1-2 arms: 42-84 x $0.16 = $7-13 | $16-22 |
| S4 | screen 7 days x 14 = 98 x $0.16 = $16; confirm each changed day 42 x $0.16 = $6.70 (0-7 days; planning assumption 4) | $16-63, plan $43 |
| S5-DS | S2 screen 84 x $0.07 = $6; S4 screen 98 x $0.07 = $7; 2-4 confirmations x 42 x $0.07 = $6-12 | $19-25 |
| **Program to S5-DS** | | **$88-154; plan about $120** |
| S5-GM, S5-GPT | same blocks as S5-DS at each model's price | estimated once their tier is chosen |

The OpenRouter key has $32.81 left (09-29). That covers S0a and the S2 screen, with room for
one S2 confirmation. A top-up is needed before the rest of S2. `--max-cost` stays a runaway
guard per run, not the budget.

## 10. Decisions this plan needs from Erik

1. Approve Opus 5.5 as the fixed lab judge (section 3). Production's gate is unchanged.
   This includes the prerequisite lab work (3.2): one small PR before S0a can run.
2. Approve the two-stage N (14 screen / 42 confirm) and the ship rule in 5.5.
3. The top-up timing for S3 onward.
4. For S5: like-for-like tier as the primary cross-model comparison.
