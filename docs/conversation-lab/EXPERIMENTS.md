# Conversation Lab - Experiment Log

> ## READ THE PRIOR ART BEFORE DESIGNING AN ARM
>
> **`prompt-research/TESTING_METHODOLOGY.md`** (546 lines, 2026-03-13/14) is the
> other half of this project's experimental record. It is **gitignored**, so
> nothing linked to it, and it has now been missed twice.
>
> It holds 261 autonomous experiments **plus four hand-run comparisons that exist
> only in its prose** - they are not rows in `results.tsv`, so grepping the TSVs
> finds nothing. Already answered there; do not re-run:
>
> | experiment | date | result |
> |---|---|---|
> | Compression model swap, Sonnet vs Haiku | 2026-03-14 | **Haiku wins** on quality *and* cost (96.0 vs 95.0; $0.003 vs $0.03). "The bottleneck is not compression quality." |
> | Progressive (rolling) compression | 2026-03-14 | did not win |
> | Cross-concept validation | 2026-03-14 | passed - the template is not overfit to one recipe |
> | XML-structured injection | 2026-03-14 | **plain text wins**; Haiku treats XML tags as formatting overhead at ~60 words/day |
>
> Standing model config (its section 11): dialogue **Haiku 4.5**, judge
> **Sonnet 4.6**, compression **Haiku 4.5**. Note what that means - the
> *compression* model has been compared, the *generation* model never has (#7203).
>
> **Both times prior work was missed here, the search was against the
> machine-generated TSVs instead of the human-written writeup.** Read the prose.


Card #6492. This file is the versioned, in-repo record of the manual
conversation self-learning loop. It replaces
`~/.claude/projects/-Users-eriksjaastad-projects-muffinpanrecipes/memory/project_conversation_tuning_log.md`
as the canonical history - that memory file is machine-local and not
reviewable in a PR; this one is. See `PROTOCOL.md` in this directory for the
method these entries follow.

## Weekly rubric history

One row per week read at a check-in (see PROTOCOL.md's "Weekly ritual").
Columns:

- **Week** - episode id and dish name.
- **Tit** - Title fidelity: does the dish stay about its named hero
ingredient?
- **Arc** - Arc resolution: do raised problems actually get resolved, not
reframed away?
- **Voice** - Voice distinctiveness: are the characters separable blind?
- **Tech** - Technical credibility: would a real cook believe the
food-science?
- **Prog** - Natural progression: builds vs. repeats/agrees-in-circles?
- **P/D** - Promise/delivery alignment: does the dialogue promise what the
recipe or images will not deliver?

Each score is 1-5. A `*` on a score means the read was partial (not all
days existed yet at read time). **promptV** is the character-prompt version
in effect for that read (see "Prompt versions" below) - this is what makes a
score comparable across weeks.

| Week | Tit | Arc | Voice | Tech | Prog | P/D | promptV | Notes |
|------|-----|-----|-------|------|------|-----|---------|-------|
| W32 (Miso Ginger Donburi Cups) | 2 | 2* | 4 | 3 | 4 | 3 | v1 | Baseline. Salmon vanished into a rice essay (title fidelity). Tuesday's "rice sticking" problem got reframed as a story beat, never solved (arc). Voices strong. Baked sushi rice is culinarily shaky (tech). *Arc partial - only Mon/Tue existed at read time. |
| W36 (Greek Spanakopita Cups) - BEFORE | 4 | 3* | 2 | 2 | 2 | 3 | v1 | Prompt leak: Margaret's first line recited the `_SHARED_RULES` example sentence verbatim. "Crispy when it cools" is wrong for phyllo. Marcus took two near-identical turns. "X is actually the story" appeared 3x. |
| W36 (Greek Spanakopita Cups) - AFTER | 4 | 4* | 3 | 4 | 4 | 4 | v2 | Regenerated Mon+Tue via the production call path; Opus judge PASS both days. Real disagreement was reached and resolved. Still only 4 of 7 characters present; Marcus's tic persists. |
| W38 Monday, 3 runs same day (2026-09-14) | 5 | 4* | **3** | 4 | 5 | 4 | v3 | Scores are the judge's own, from the run that PASSED. *Mon only. See the three-run note below - Voice scored exactly 3 in all three, on three different character pairs. |

### W38 Monday — three runs, one day, and the number that would not move

2026-09-14 produced an accidental controlled experiment. Monday's cron failed
the judge, and two re-fires followed. `force=true` re-picks the concept, so all
three runs used a **different dish**, the same prompt version, and the same
cast of five.

| Run | Concept | Verdict | Voice | Other weak dims | Pair the judge named |
|---|---|---|---|---|---|
| 1 (14:30 UTC cron) | Cinnamon Apple Streusel Cakes | FAIL x3 | **3** | prog 3, turn 3 | Marcus / Julian |
| 2 (re-fire) | Beet Horseradish Cream Cups | FAIL x3 | **3** | tech 3 | Margaret / Steph |
| 3 (re-fire, PASS) | Cardamom Cinnamon Spiral Bites | PASS | **3** | - | Marcus / Steph |

**`voice_distinctiveness` scored exactly 3 in all three runs, across three
different character pairs, on three unrelated dishes.** Every other dimension
moved with the concept: run 2's `technical_credibility` 3 is the incoherent
dish (a Sweet-shelf beet-and-horseradish dessert built with sugar and vanilla -
that is card #7154, a concept-picker and recipe-gate problem, not a dialogue
one). Run 3 scored 5/4/**3**/4/5/4 and passed cleanly.

The reading: **concept quality drives the pass/fail, and voice is a separate
standing ceiling underneath it.** A good dish is not enough to lift Voice past
3, and a bad dish does not push it below 3. Run 3's own PASS verdict says it
plainly - "Marcus and Steph blur slightly in register and Margaret is less
blunt than usual after her opening lines" - on a conversation that is otherwise
genuinely good: a real technical tension (cardamom is volatile, the pan's whole
argument is enclosure) resolved into a plan change (shoot it broken open while
warm). QA 85, zero prompt-echo hits, zero cross-character overlap penalty.

**Next candidate nudge (one lever, do not stack):** character-voice separation.
This is the third consecutive Monday where the judge names a blurred *pair*
rather than a weak individual, which points at the shared prompt flattening
everyone toward one register rather than at any one bio. Card **#6966**
(personality dials - make the existing numeric traits bind, per-day sampled
state) is the designed lever and should be tried before hand-editing bios.

Do not treat this as three independent samples of the same thing: runs 1 and 2
were judged on dialogues that **no longer exist**. `_save_stage_failure` blind-
overwrote the stage both times and destroyed all six. That is fixed as of
#7100 (same day) - rejected attempts now persist at
`episode["rejected_dialogues"][stage]` and `review_episode.py` renders them, so
the next failure is readable instead of inferable from one sentence of verdict.

Note: `_SHARED_RULES`, named in the W36 BEFORE row above and in v1/v2
below, is the pre-2026-09-05 name of the code symbol now called
`_SHARED_CHARACTER_RULES` (`scripts/simulate_dialogue_week.py:368`). The
rename happened as part of the #6832/#6840 work that produced v3; these
entries predate it and are left as originally logged.

## Prompt versions

- **v1** - as of 2026-08-06. Baseline. The `_SHARED_RULES` example included a
literal pan-case demonstration sentence that character prompts would
sometimes recite verbatim instead of using as a template (see W36 BEFORE).
- **v2** - 2026-09-02. Removed the literal pan-case example sentence from
`_SHARED_RULES` and replaced it with constraints instead of a worked
example (name a mechanism specific to THIS dish; never a bare conclusion;
never open a stage with it).
- **v3** - 2026-09-05 (#6832 roster fix: Julian added to Monday's cast,
Devon added to Tuesday's, matching what `CHARACTER_DAY_GOALS` already
assumed; #6840 turn-taking rule, verbatim: "Your first sentence must
respond to what was just said. Don't change the subject. A turn can be
one sentence, a single question, or a direct answer to the last speaker -
that is a complete turn, not a shortfall. If the last message asked you
something, answer it before you add anything new." plus prompt-echo
detection extended to any 6+-word run shared with the shared rules text;
#6861 structured 8-dimension judge). First live week: **W37,
Monday 2026-09-07.**

## Experiments

Empty except this header - `scripts/conversation_lab.py` appends a row here
each time an offline A/B experiment completes (see PROTOCOL.md's
"Experiment steps"). Do not hand-edit rows into this table; let the tool
write them so the log and the tool never drift apart.

| Date | Experiment ID | Lever (one) | Target dimension(s) | N | Wins/Ties/Losses on target | Other dimensions lost | Decision | Shipped PR | Live confirmation week + scores |
|------|----------------|--------------|----------------------|---|------------------------------|------------------------|-----------|-------------|-----------------------------------|

## Heat map baseline v0 (2026-09-06)

A one-off read across the full corpus (W11-W36, 917 dialogue lines) to size
the "Areas" named in `PROTOCOL.md` before picking a lever - not an A/B
experiment, so it does not use the table above. See `PROTOCOL.md`'s "Areas"
and "Corpus baseline 2026-09-06" sections for what each number means and
which lever it points at.

| Metric | Corpus (W11-W36) | W36 alone |
|---|---|---|
| Dash-clause sentences | 86% | 91% |
| Frame claims | 30% | 33% |
| Agree-openers | 21% | 26% |
| Lines under 8 words | 1% | 2% |
| Mean turn length | 24.1 words (stdev 8.1) | 24.5 words (stdev 7.8) |
| Questions | 20% | 16% |

Top cross-week phrases (3-word runs, by number of distinct weeks they
appear in out of 25):

| Phrase | Weeks |
|---|---|
| "now should be live" | 23 |
| "should be live" | 22 |
| "we need to" | 21 |
| "in a few" | 19 |
| "stops the scroll" | 16 |
| "the muffin pan" | 15 |
| "the cross section" | 14 |
| "we're good to" | 14 |
| "staging the muffin" | 13 |
| "the three quarter" | 13 |
| "people need to" | 13 |
| "the whole point" | 12 |
| "what if we" | 12 |

The em-dash ban (house style, this card) moved the model from an em dash to
a plain hyphen but did not move any of the numbers above - the glyph was
never the problem.

The full matrix behind this table (every week, every metric) now comes from
`scripts/conversation_heatmap.py` (run its `--help` for the exact flags)
and lands under `docs/conversation-lab/results/`. This section stays as
the one-off manual read that motivated building it.

---

## 2026-09-15 — W38 Tuesday failure: two prompt defects, shipped unmeasured

Logged here because this is the canonical record for dial changes, and these
went to production **without** a lab sweep. That was deliberate: W38's Tuesday
stage had already failed the Judge three times and the cron window had closed,
so something had to change. (The week was not in fact dead — Sunday gates on
Wednesday, not Tuesday — so the urgency was real but smaller than stated here
originally.) **These are hypotheses with a plausible mechanism, not defects with
a known cause**; an earlier draft called them "mechanical defects with a known
cause" and that is retracted. See "What this does not prove" below.

**The judge's verdict, verbatim:** *"Devon sounds too much like Marcus/Steph
with verbose explanations rather than his characteristic efficiency, and the
conversation is largely everyone agreeing."*

**These are hypotheses, not established causes.** An earlier draft of this entry
said "both halves turned out to be mechanical." That overstated the evidence and
is retracted — see "What this does not prove".

### #7184 — hypothesis: the shared word limit suppressed the per-character budgets (REFUTED as stated)

`_CHARACTER_VOICE_GUIDES` sets a per-character maximum: Devon 12 words,
Margaret 15, Julian 20, Ria 20, Steph 25, Marcus 35. `_SHARED_CHARACTER_RULES`
then opened with `HARD LIMIT: 1-2 sentences max. If you wrote more than 25
words, rewrite shorter.` — later in the prompt, and labelled harder.

**The "it was the binding ceiling" story is refuted.** Word counts from the
three rejected W38 Tuesday attempts, gathered independently:

| attempt | Margaret (max 15) | Devon (12) | Marcus (35) | Steph (25) |
|---|---|---|---|---|
| rejected 1 | 18 | 11, 11 | 22 | 22 |
| rejected 2 | 23, 29 | 17 | 26, 37 | 33 |
| rejected 3 | 18, 26 | 23, 21 | **50** | 27 |
| accepted (post-batch) | 23, **60** | 26 | 46, 36 | 22 |

Marcus produced **50 words while the 25-word shared cap was still in place**, so
that cap was not mechanically binding. Devon's 12 and the shared 25 are also
logically compatible — nothing in the pair requires him to lengthen. Prompt
salience could still drive convergence, but **source ordering alone cannot
establish it**, and the counts above are evidence against the simple version.

Note the accepted run is not cleaner than the rejected ones on length: Margaret's
60 is the worst number in the table. Whatever improved, it was not this.

### #7160 — the scene's premise fell out of the context window

`history_depth` was `8 if day_turn == 1 else 4` for mon-thu. Monday runs up to
10 turns, so with four prior lines retained the opening message first drops out
on **turn 6** (an earlier draft said turn 5 — corrected). From there the question
that opened the scene was not in the prompt. That it therefore went unanswered is
an inference, not a demonstrated cause. Now 16/12 early, 20/16
late, with the opening floor above the largest `TICKS_RANGE` upper bound by
construction.

**Cost of the wider window — CORRECTED 2026-09-16 after an independent audit.**
The arithmetic was right and the workload assumption was wrong, so the published
number was wrong. `_generate_dialogue` runs each stage separately and its history
**starts empty**, so the opening-window expansion adds nothing and Fri–Sun
already fit inside the old eight-line window. A five-turn Tuesday gains no
history at all; a six-turn Tuesday gains exactly one prior line, on its last
turn. Across ordinary weekly turn ranges the change adds roughly **4–20 total
prior-line presentations per successful week** (~141–705 tokens), not 8 lines on
every turn; a ten-turn reshoot Wednesday reaches ~16–32 lines. Retries and
rewrites add more. Still cheap — but the earlier "~12,100 tokens/week, $0.63/yr"
figure assumed a per-turn cost that production does not incur. Note also that
chars/4 is a sizing heuristic; `model_router` already records real
`usage.input_tokens`, and that is what a future measurement should use.

### #7159 — three measured March winners that never shipped

From `prompt-research/results.tsv` (2026-03-13, 261 experiments): *"push back"*
(94.0), *"voice first, content second"* (78.4), *"name the ingredient or
technique"* (73.4). Two other KEEP lines from that run — *"conflict is
natural"* and *"never address someone by name"* — were already in production.
The winners that had shipped were the mechanical/formatting ones; the
behavioral ones had not.

### What this does not prove — read before citing any of it

**Nothing here is attributed.** The re-fire changed the shared cap, the history
depth, three behavioral rules and the recipe anchor *simultaneously*, then drew
one stochastic generation. Accepted voice score 4 against earlier 2/3 is a real
observation and not an attribution. The rejected attempts also differ
substantially from one another, so run-to-run variance alone is not ruled out.

A failed future sweep would not prove the remaining cause is the voice guides,
either. That inference was asserted in an earlier draft and is withdrawn.

**The sweep design that would actually settle it:** compare shared-cap
present/absent while holding history, behavioral rules, recipe anchor, model,
memory snapshots and scenarios fixed. Test history independently. Use repeated
generations, blinded position-swapped judging, per-character length
distributions and violation rates, malformed-response counts, and recipe-fidelity
checks. Reuse W38 as a regression scenario alongside the panel. A 2x2 can probe
cap/history interaction only if the isolated results warrant it.

**Testbed caveat:** the frozen panel still carries the old `Key ingredients:`
anchors. Version a current-anchor panel before claiming a production result, or
the sweep is silently testing a different context format than production uses.

**On enforcement:** there is no post-generation word-count check in
`generate_turn`. The voice guides already say MAXIMUM with a number and the
replacement shared text already forbids exceeding it, so "add the words HARD
LIMIT" is another untested hypothesis. A bounded validate-and-rewrite step would
enforce the stated contract directly — evaluate the quality cost of actually
holding characters to very short budgets before assuming it is free. #7161 (every turn requests the same speech act), #6966 (personality
dials) and #7157 (Monday produces the title) remain unshipped and sweep-gated.

---

## 2026-09-16 — the baseline that explains "it sounds mechanical"

Erik, reading W38 Wednesday: *"it does not sound like natural language."* He is
right, and it is measurable. Across **all 1,053 stored dialogue lines**:

| sentence shape | lines | share |
|---|---:|---:|
| `<claim> - <elaboration>` | 911 | **86.5%** |
| plain declarative | 123 | 11.7% |
| terse | 11 | 1.0% |
| question | 8 | 0.8% |

In W38 specifically it was **21 of 21 lines** — monday, tuesday and wednesday,
five characters, 100%.

**Every vocabulary guard passed all of it.** `_is_repetitive_candidate` is a
Jaccard over tokens and `_shared_trigram_with_recent` is word trigrams; both are
blind to syntax. The words differed every time. The shape never did.

This is the near-miss recorded further up this file finally landing: banning em
dashes *"moved the model from an em dash to a plain hyphen but did not move any
of the numbers - the glyph was never the problem."* `sanitize_typographic_tells`
rewrites em dashes to `" - "`, so house style **converted** the tic instead of
removing it. (That quoted sentence is itself a dash clause.)

### The per-character table is the interesting part

| character | dash rate | mean words | stated MAXIMUM |
|---|---:|---:|---:|
| Marcus | 100.0% | 31.5 | 35 |
| Julian | 100.0% | 27.9 | 20 |
| Ria | 98.5% | 27.9 | 20 |
| Steph | 95.0% | 23.1 | 25 |
| Margaret | 77.6% | 21.2 | 15 |
| **Devon** | **40.0%** | **13.2** | **12** |

**Devon is the least broken on both axes and has the shortest budget.** The two
most over-budget characters, Julian and Ria, are also the two most shape-locked.

That suggests a hypothesis worth testing rather than assuming: **a tight word
budget may fix the shape problem as a side effect**, because `<claim> -
<elaboration>` does not fit in twelve words. If true, enforcing budgets is one
lever that moves two metrics, and no separate shape rule is needed.

### What shipped, and what was deliberately NOT shipped

Shipped: two post-generation guards feeding the existing single bounded rewrite -
same API cost, more signal. `_shape_is_saturated` is a **rate limit, not a ban**
(a dash clause is a legitimate way to talk; the defect is everyone using it every
time). `_over_word_budget` uses `WORD_BUDGET_TOLERANCE = 1.3` rather than a hard
line, because holding a speaker to exactly twelve words risks the stilted output
an independent review warned about.

**Deliberately not shipped: a prompt rule about sentence variety.** Adding a rule
and a guard in the same change would make the result unattributable - which is
exactly the mistake made on 2026-09-15. If the dash rate drops, it was the guard.

`SHAPE_WINDOW`, `SHAPE_MAX_IN_WINDOW` and `WORD_BUDGET_TOLERANCE` are all lab
levers, so the sweep can ask "does enforcing the budget hurt the writing?" with
`WORD_BUDGET_TOLERANCE = 1.0` as the strict arm.

### Numbers to beat

Corpus dash rate **86.5%**. W38 **100%**. Per-character means above. Re-run the
measurement after a week of generated dialogue and compare; that script is four
lines against `storage.list_episodes()` and `_sentence_shape`.
