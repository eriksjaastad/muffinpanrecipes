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

**Note (2026-09-30, #7715).** The 2026-09-27 through 2026-09-29 rows below
are exploratory day-by-day lab results, not a shipping decision. Most are
scored under the Opus 4.6 judge; the 2026-09-28 DeepSeek-judged week is not
comparable to the rest (different judge, same instrument gap covered in
`project_day_by_day_weeks` memory) and should not be read alongside the
Opus-judged rows. `RESEARCH_PLAN.md` (fixed judge, pre-registered registry,
sign-test ship rule) supersedes these rows as evidence for any ship/hold
call going forward.

| Date | Experiment ID | Lever (one) | Target dimension(s) | N | Wins/Ties/Losses on target | Other dimensions lost | Decision | Shipped PR | Live confirmation week + scores |
|------|----------------|--------------|----------------------|---|------------------------------|------------------------|-----------|-------------|-----------------------------------|
| 2026-09-25T17:16:30.839007+00:00 | 20260925T165757Z-ab-testbed-rules-trim | _SHARED_CHARACTER_RULES | voice_distinctiveness | 21 | 6/11/4 | arc_resolution, technical_credibility, natural_progression, promise_delivery, turn_taking, emotional_range, register_naturalness | REJECT: 29% target wins, below 65% | not shipped | n/a |
| 2026-09-27T16:01:10.912388+00:00 | 20260927T155139Z-ab-testbed-winddown-off | WINDDOWN_TRIGGER | natural_progression | 14 | 1/12/1 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, turn_taking, emotional_range, register_naturalness | signal: HOLD (7% on natural_progression) | TBD | TBD |
| 2026-09-27T16:57:22.392794+00:00 | 20260927T163954Z-ab-testbed-open-jev | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+STOP_CHECK+WINDDOWN_TRIGGER | natural_progression | 14 | 10/3/1 | arc_resolution, voice_distinctiveness, turn_taking, emotional_range, register_naturalness | signal: SHIP (71% on natural_progression) | TBD | TBD |
| 2026-09-27T17:04:10.147702+00:00 | 20260927T164237Z-ab-sweep-length-tue-len09 | TICKS_RANGE | natural_progression | 14 | 4/10/0 | voice_distinctiveness, technical_credibility, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-27T17:04:10.147702+00:00 | 20260927T164237Z-ab-sweep-length-tue-len12 | HISTORY_DEPTH+TICKS_RANGE | natural_progression | 14 | 8/5/1 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (57% on natural_progression) | TBD | TBD |
| 2026-09-27T17:22:37.103196+00:00 | 20260927T170440Z-ab-sweep-length-sat-len09 | TICKS_RANGE | natural_progression | 14 | 7/3/4 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (50% on natural_progression) | TBD | TBD |
| 2026-09-27T17:22:37.103196+00:00 | 20260927T170440Z-ab-sweep-length-sat-len12 | TICKS_RANGE | natural_progression | 14 | 5/8/1 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (36% on natural_progression) | TBD | TBD |
| 2026-09-27T17:27:06.830155+00:00 | 20260927T170439Z-ab-sweep-length-thu-len09 | TICKS_RANGE | natural_progression | 14 | 4/9/1 | arc_resolution, voice_distinctiveness, turn_taking, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-27T17:27:06.830155+00:00 | 20260927T170439Z-ab-sweep-length-thu-len12 | HISTORY_DEPTH+TICKS_RANGE | natural_progression | 14 | 7/4/3 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (50% on natural_progression) | TBD | TBD |
| 2026-09-27T17:49:29.172878+00:00 | 20260927T174929Z-ab-testbed-monday-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 0/14/0 | none | DRY RUN - no signal | TBD | TBD |
| 2026-09-27T18:09:44.300214+00:00 | 20260927T174943Z-ab-testbed-monday-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 13/1/0 | technical_credibility, promise_delivery, register_naturalness | signal: SHIP (93% on natural_progression) | TBD | TBD |
| 2026-09-27T18:10:21.858251+00:00 | 20260927T181021Z-ab-testbed-monday-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 0/14/0 | none | DRY RUN - no signal | TBD | TBD |
| 2026-09-27T18:28:08.566517+00:00 | 20260927T181026Z-ab-testbed-monday-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/5/0 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-27T18:28:44.654440+00:00 | 20260927T182844Z-ab-testbed-monday-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 0/14/0 | none | DRY RUN - no signal | TBD | TBD |
| 2026-09-27T18:47:34.945922+00:00 | 20260927T182845Z-ab-testbed-monday-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 6/3/5 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (43% on natural_progression) | TBD | TBD |
| 2026-09-27T18:48:12.620963+00:00 | 20260927T184812Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 0/14/0 | none | DRY RUN - no signal | TBD | TBD |
| 2026-09-27T19:10:13.615702+00:00 | 20260927T184821Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 11/1/2 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (79% on natural_progression) | TBD | TBD |
| 2026-09-27T19:29:38.077501+00:00 | 20260927T191045Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 11/3/0 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, register_naturalness | signal: HOLD (79% on natural_progression) | TBD | TBD |
| 2026-09-27T19:36:17.948433+00:00 | 20260927T193010Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 6/1/0 | turn_taking, cast_coverage, register_naturalness | signal: HOLD (86% on natural_progression) | TBD | TBD |
| 2026-09-27T19:42:29.583720+00:00 | 20260927T193619Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 3/3/1 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (43% on natural_progression) | TBD | TBD |
| 2026-09-27T19:52:19.122969+00:00 | 20260927T194336Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 4/3/0 | technical_credibility, cast_coverage, register_naturalness | signal: HOLD (57% on natural_progression) | TBD | TBD |
| 2026-09-27T20:00:25.245768+00:00 | 20260927T195220Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 5/1/1 | arc_resolution, voice_distinctiveness, technical_credibility, turn_taking, emotional_range, register_naturalness | signal: SHIP (71% on natural_progression) | TBD | TBD |
| 2026-09-27T20:07:19.520537+00:00 | 20260927T200046Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 3/4/0 | voice_distinctiveness, register_naturalness | signal: HOLD (43% on natural_progression) | TBD | TBD |
| 2026-09-27T20:18:47.108689+00:00 | 20260927T201205Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 4/2/1 | voice_distinctiveness, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (57% on natural_progression) | TBD | TBD |
| 2026-09-27T20:22:56.347815+00:00 | 20260927T201907Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 3/3/1 | voice_distinctiveness, technical_credibility, turn_taking, register_naturalness | signal: HOLD (43% on natural_progression) | TBD | TBD |
| 2026-09-27T20:26:26.315543+00:00 | 20260927T202257Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 1/4/2 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, turn_taking, emotional_range, register_naturalness | signal: HOLD (14% on natural_progression) | TBD | TBD |
| 2026-09-27T20:33:08.421318+00:00 | 20260927T202656Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 1/6/0 | voice_distinctiveness, turn_taking, cast_coverage, register_naturalness | signal: HOLD (14% on natural_progression) | TBD | TBD |
| 2026-09-27T20:37:36.192701+00:00 | 20260927T203309Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 0/6/1 | voice_distinctiveness, turn_taking, emotional_range, register_naturalness | signal: HOLD (0% on natural_progression) | TBD | TBD |
| 2026-09-28T17:12:32.395589+00:00 | 20260928T161006Z-ab-testbed-monday-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/1/4 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-28T17:20:14.053948+00:00 | 20260928T161006Z-ab-testbed-monday-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/4/1 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-28T18:26:39.342260+00:00 | 20260928T172047Z-ab-testbed-monday-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 3/5/6 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (21% on natural_progression) | TBD | TBD |
| 2026-09-28T21:33:49.220790+00:00 | 20260928T182735Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/2/3 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-28T22:31:23.585777+00:00 | 20260928T213803Z-ab-testbed-week-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/3/2 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-28T22:38:05.203294+00:00 | 20260928T213803Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 10/4/0 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, register_naturalness | signal: HOLD (71% on natural_progression) | TBD | TBD |
| 2026-09-28T23:11:51.178542+00:00 | 20260928T223837Z-ab-testbed-week-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 5/1/8 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (36% on natural_progression) | TBD | TBD |
| 2026-09-28T23:12:37.277686+00:00 | 20260928T223837Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/0/5 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-28T23:19:03.455292+00:00 | 20260928T223837Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 11/3/0 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, register_naturalness | signal: SHIP (79% on natural_progression) | TBD | TBD |
| 2026-09-29T00:02:24.064742+00:00 | 20260928T231933Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/2/3 | title_fidelity, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-29T00:07:01.715552+00:00 | 20260928T231935Z-ab-testbed-week-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/2/3 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-29T00:10:22.632797+00:00 | 20260928T231933Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 11/2/1 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, register_naturalness | signal: HOLD (79% on natural_progression) | TBD | TBD |
| 2026-09-29T00:51:40.368690+00:00 | 20260929T001102Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 2/4/8 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (14% on natural_progression) | TBD | TBD |
| 2026-09-29T01:01:07.341853+00:00 | 20260929T001102Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 6/5/3 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (43% on natural_progression) | TBD | TBD |
| 2026-09-29T01:01:28.693310+00:00 | 20260929T001102Z-ab-testbed-week-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 4/3/7 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-29T01:44:34.012046+00:00 | 20260929T010210Z-ab-testbed-week-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 1/4/9 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (7% on natural_progression) | TBD | TBD |
| 2026-09-29T01:45:10.255541+00:00 | 20260929T010210Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 4/3/7 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-29T02:01:47.532891+00:00 | 20260929T010210Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 4/7/3 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-29T02:51:23.409672+00:00 | 20260929T020221Z-ab-testbed-week-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 2/1/11 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (14% on natural_progression) | TBD | TBD |
| 2026-09-29T02:57:44.120921+00:00 | 20260929T020222Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 3/2/9 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (21% on natural_progression) | TBD | TBD |
| 2026-09-29T03:10:38.744598+00:00 | 20260929T020222Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 4/4/6 | arc_resolution, voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-29T14:04:59.845432+00:00 | 20260929T134408Z-ab-testbed-monday-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 4/9/1 | voice_distinctiveness, technical_credibility, promise_delivery, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-29T14:05:20.329183+00:00 | 20260929T134408Z-ab-testbed-monday-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 13/1/0 | turn_taking, cast_coverage, register_naturalness | signal: SHIP (93% on natural_progression) | TBD | TBD |
| 2026-09-29T14:09:53.810504+00:00 | 20260929T134408Z-ab-testbed-monday-lo-caps-rep | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 9/4/1 | arc_resolution, voice_distinctiveness, technical_credibility, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (64% on natural_progression) | TBD | TBD |
| 2026-09-29T15:00:25.230628+00:00 | 20260929T143700Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 12/2/0 | emotional_range, register_naturalness | signal: SHIP (86% on natural_progression) | TBD | TBD |
| 2026-09-29T15:08:37.104403+00:00 | 20260929T143701Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 14 | 13/1/0 | technical_credibility, promise_delivery, turn_taking, register_naturalness | signal: SHIP (93% on natural_progression) | TBD | TBD |
| 2026-09-29T15:15:30.377927+00:00 | 20260929T150852Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 0/4/3 | title_fidelity, arc_resolution, voice_distinctiveness, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (0% on natural_progression) | TBD | TBD |
| 2026-09-29T15:18:32.844710+00:00 | 20260929T150853Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 7/0/0 | register_naturalness | signal: SHIP (100% on natural_progression) | TBD | TBD |
| 2026-09-29T15:28:26.862816+00:00 | 20260929T151846Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 4/3/0 | cast_coverage, emotional_range | signal: HOLD (57% on natural_progression) | TBD | TBD |
| 2026-09-29T15:36:30.253476+00:00 | 20260929T151846Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 7/0/0 | none | signal: SHIP (100% on natural_progression) | TBD | TBD |
| 2026-09-29T15:46:40.344519+00:00 | 20260929T153646Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 0/6/1 | voice_distinctiveness, turn_taking, cast_coverage, emotional_range, register_naturalness | signal: HOLD (0% on natural_progression) | TBD | TBD |
| 2026-09-29T15:50:22.089632+00:00 | 20260929T153646Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 5/2/0 | cast_coverage, register_naturalness | signal: SHIP (71% on natural_progression) | TBD | TBD |
| 2026-09-29T15:56:54.173967+00:00 | 20260929T155035Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 2/1/4 | arc_resolution, voice_distinctiveness, turn_taking, emotional_range, register_naturalness | signal: HOLD (29% on natural_progression) | TBD | TBD |
| 2026-09-29T16:00:08.636070+00:00 | 20260929T155035Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 3/4/0 | technical_credibility, turn_taking, register_naturalness | signal: HOLD (43% on natural_progression) | TBD | TBD |
| 2026-09-29T16:09:20.504242+00:00 | 20260929T160023Z-ab-testbed-week-lo-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 1/6/0 | voice_distinctiveness, turn_taking, cast_coverage | signal: HOLD (14% on natural_progression) | TBD | TBD |
| 2026-09-29T16:11:43.588453+00:00 | 20260929T160023Z-ab-testbed-week-limits-off | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | natural_progression | 7 | 3/1/3 | voice_distinctiveness, turn_taking, cast_coverage, register_naturalness | signal: HOLD (43% on natural_progression) | TBD | TBD |
| 2026-10-01T16:38:40.590314+00:00 | 20261001T151937Z-ab-sweep-claude-o55-s2-sweep-A1-word-caps | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER | turn_taking | 14 | 8/5/1 | title_fidelity, arc_resolution, voice_distinctiveness, technical_credibility, natural_progression, promise_delivery, cast_coverage, emotional_range, register_naturalness | signal: HOLD (57% on turn_taking) | TBD | TBD |
| 2026-10-01T16:38:40.590314+00:00 | 20261001T151937Z-ab-sweep-claude-o55-s2-sweep-A2-rewrite-guards | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | turn_taking | 14 | 5/6/3 | arc_resolution, voice_distinctiveness, technical_credibility, natural_progression, promise_delivery, cast_coverage, register_naturalness | signal: HOLD (36% on turn_taking) | TBD | TBD |
| 2026-10-01T16:38:40.590314+00:00 | 20261001T151937Z-ab-sweep-claude-o55-s2-sweep-A4-fixed-count | HISTORY_DEPTH+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | turn_taking | 14 | 5/7/2 | voice_distinctiveness, technical_credibility, natural_progression, promise_delivery, emotional_range, register_naturalness | signal: HOLD (36% on turn_taking) | TBD | TBD |
| 2026-10-01T16:38:40.590314+00:00 | 20261001T151937Z-ab-sweep-claude-o55-s2-sweep-A5-stop-check-off | HISTORY_DEPTH+REWRITE_GUARDS+WORD_CAPS | turn_taking | 14 | 6/3/5 | arc_resolution, voice_distinctiveness, technical_credibility, natural_progression, promise_delivery, emotional_range, register_naturalness | signal: HOLD (43% on turn_taking) | TBD | TBD |
| 2026-10-01T16:38:40.590314+00:00 | 20261001T151937Z-ab-sweep-claude-o55-s2-sweep-B-bundle | HISTORY_DEPTH+OPEN_ENDED_MAX_TICKS+REWRITE_GUARDS+STOP_CHECK+WINDDOWN_TRIGGER+WORD_CAPS | turn_taking | 14 | 10/2/2 | title_fidelity, voice_distinctiveness, technical_credibility, natural_progression, promise_delivery, cast_coverage, register_naturalness | signal: HOLD (71% on turn_taking) | TBD | TBD |

### 2026-09-25 — Reference calibration, bench, and first v3 A/B

The [reference-panel calibration](results/20260925T165113Z-calibrate-reference-panel-v0.json)
used the versioned `pairwise-v2-candidate-versions-character-rules` judge. All 24
position-swapped responses were valid and all 12 pairs completed. Identical
transcripts received unanimous ties in all three repeats. The original beat
inconsistently reassigned speaker labels on `voice_distinctiveness` in all
three repeats, meeting the preregistered 80% operational check. Consistent
name permutation and the W38 pushback edit were diagnostic cases; they have
no human directional quality label. This check supports using the judge for
an exploratory comparison; it does not establish agreement with a blind human
reader or validate the production publish gate.

The [two-run Monday bench](results/bench-monday-n2-20260925T165714Z.json)
scored 2/2 runs and passed 0/2. Arc resolution, natural progression, and
turn-taking were the weakest judge dimensions in both runs. Two runs describe
this baseline; they are too few to establish a population pass rate.

The [approved single-lever A/B](results/20260925T165757Z-ab-testbed-rules-trim.json)
used the unchanged v3 panel and approved `_SHARED_CHARACTER_RULES` trim:
seven Monday scenarios with three pairs each. All 21 pairs completed.
Voice distinctiveness favored the variant in 6, tied in 11, and favored
control in 4. Its 28.57% target win rate misses the preregistered 65%
threshold. No production prompt change follows from this result; the blind
human read required before shipping has not been done.

The [shared budget ledger](results/20260925T171630Z-experiment-budget-ledger.json)
records 692 settled generation attempts and $2.961497 total actual spend:
$0.482345 calibration (including the preserved September 23 run), $0.105245
bench, and $2.373907 A/B. It has zero reserved or uncertain spend and
$2.038503 remaining under the approved $5 combined ceiling. The router's
per-command cost estimates differ from this authoritative ledger. These runs
used source commit `007d5a3`; the current evaluator includes the speaker
attribution metric merged through PR #126. The A/B and calibration both used
judge prompt SHA-256 `c6570e4d93e3470eff97f779e4998b4aec0f8c04e6499f46c402c0af50d67329`.

### 2026-09-23 — W25 Thursday judge calibration (not an A/B result)

At source commit `c08a73950ee7a976f3b4885ec7e0f4348b62ed11`, the calibration
command tested the existing W25 Thursday transcript for **Harissa Chickpea
Feta Cups** against two deterministic degradations, three runs each, with
both position orders judged (12 paid judge requests total). The judge was
Anthropic Claude Opus 4.6. This was a calibration of the current judge on a
W25 transcript; it was **not** the approved v3 A/B, and it does not measure
the future v3 panel's experiment outcomes.

| Degradation | Completed / requested | Combined results | Tool verdict |
|---|---:|---|---|
| `shuffled_order` | 3 / 3 | real preferred 3; ties 0; degraded preferred 0 | GRADER OK (100% real preference) |
| `rotated_speakers` | 3 / 3 | real preferred 0; ties 3; degraded preferred 0 | GRADER SUSPECT (0% real preference) |

The shared guard ledger records **$0.214525 actual** for calibration, with
zero reserved and zero uncertain spend, against its $5 ceiling. The original
tool result is
[`20260923T015903Z-calibrate-2026-W25-thursday.json`](results/20260923T015903Z-calibrate-2026-W25-thursday.json).
The copied ledger and preservation snapshot are
[`20260923T015903Z-calibrate-budget-ledger.json`](results/20260923T015903Z-calibrate-budget-ledger.json)
and
[`20260923T015903Z-calibrate-2026-W25-thursday.inputs.json`](results/20260923T015903Z-calibrate-2026-W25-thursday.inputs.json).
The snapshot records the source episode SHA-256
`555401c9ecfb2e2a5641eef9ffcfcf55f9bbe8a0bbb9b2abbb450e4e0e0cb1d6` and
original tool-result SHA-256
`17f5b68c7271f7763d9dd4f073ac9d725c481b694e54c066754b9d547f087195`, along
with the source transcript, seed 1–3 degraded variants, expected cast,
recipe context/facts, judge system prompt and model ID, and the 12 rendered
orientation prompts. Those judge inputs were reconstructed offline after the
run from the matching episode hash and frozen source commit; they are not
captured API request logs and contain no raw API answers.

Interpret this narrowly. The combined records preserve only the combined
verdicts; raw answers from the two judge orientations were not recorded, so
the three rotated-speaker ties cannot be attributed to either position order.
A combined tie could mean both orientations tied or that their preferences
disagreed; the saved record does not distinguish those cases.
At the source commit, `PAIRWISE_JUDGE_SYSTEM_PROMPT` and
`_build_pairwise_prompt` provide names, roster, recipe facts, and transcripts,
but no character voice guides or identity expectations. The rotation itself
changes turn labels by one position and is identical for seeds 1–3; that
construction does not establish that the resulting dialogue has poor voice
distinctness. Therefore **GRADER SUSPECT is the tool's flag for this known-
degradation test, not proof that the judge is wrong or that the characters
are indistinct**. Separately, the parser can default omitted overall or
per-dimension judge fields to ties before combining orientations. That is a
possible interpretation caveat for this output, not a claim that any fields
were omitted here. The protocol's known-bad calibration guidance still calls
for diagnosing judge prompts when ties appear; this record does not claim
that diagnosis is complete.

The `rules-trim` A/B remains unrun and pending Erik's direction; no
production prompt changed. Tuesday remains held. The Experiments table above
stays empty until an actual A/B tool run produces a row; this calibration
must not be represented as a completed lever test. No additional paid calls
were made while recording this entry.

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

---

## 2026-09-17 — Haiku 4.5 vs gpt-6-astra (high) on panel v2

First run on the refreshed current-anchor panel (#7201). **Structure metrics only
— no judge scoring, no blind human read. This is not a quality verdict.**

4 scenarios x 4 Wednesday turns, plus a Tuesday spread test.

| metric | Haiku 4.5 | gpt-6-astra high | corpus baseline |
|---|---:|---:|---:|
| dash-clause rate | 0.81 | **0.00** | 0.86 |
| within word budget | 0.81 | **1.00** | — |
| mean words | 15.9 | 17.8 | 24.1 |
| lines under 8 words | 0.00 | 0.00 | 0.01 |

**Read the Haiku column carefully — it is not the old baseline.** Haiku here is
running *with* the PR #114 guards, and they work: mean length fell 24.1 → 15.9
and 81% of lines land in budget. But **dash rate barely moved, 0.86 → 0.81.**
The guards fixed length and did not fix shape. That is the cleanest evidence yet
that these are two separate problems, and that the shape tic survives a
post-generation rate limit.

**Review correction (2026-09-17):** these measurements predate the fixes for
dash classification and the shape-window override in PR #117. The observed
numbers remain historical results, but the claim above that the guards do not
fix shape is unsupported: the shape guard was not reliably invoked. A fresh
controlled run with the corrected wiring is required to assess its effect.

### The spread test (Tuesday: Devon 12, Margaret 15, Steph 25, Marcus 35)

Wednesday's cast only spans budgets 15–25, so Astra's low length-variance there
looked like flattening. On the wide cast it is the reverse:

| | Margaret /15 | Steph /25 | Marcus /35 | Devon /12 | stdev |
|---|---|---|---|---|---:|
| Haiku | 17 (over) | 13 | 22 | 10 | 4.50 |
| **Astra** | **15** | **22** | **29** | **10** | **7.18** |

Astra tracks each character's individual budget; Haiku compresses everyone toward
the middle. Astra's 7.18 is close to the corpus's 8.1 — and the corpus got there
by being uniformly *long*, whereas this is uniform *differentiation*.

### Cost

$1.04 (Astra) vs $0.054 (Haiku) for 16 turns + retries — roughly 20x, and about
**$146/yr vs $16–32/yr** at production volume. Erik's 2026-09-16 call was that
cost is not a factor at this volume.

### What would make this a verdict

Judge scoring on the panel, repeated runs, position-swapped blind judging, and
Erik's `pairs --show` read. All of that exists already and none of it was run
here. Attribution is also unavailable by construction: swapping the model changes
everything at once.

## Folded-in research logs (#8144)

These entries were `docs/research/*.md` and `docs/conversation-lab/{SPEAKER_ATTRIBUTION,MEMORY_*}.md` until #8144 folded them here so prior results sit in one findable place. Numbers are copied from the source logs unchanged. Every entry ends with the `git show` command that prints the original, pinned to commit `eb90b0f`. The mechanics of the memory scripts (inputs, flags, guards) moved into those scripts' module docstrings; only the experiment design and results are here.

### 2026-03-03 to 2026-03-04 — Dialogue bookends: opener/closer directives v1 to v3

What was tested: character-driven opening greetings and closing sign-offs for each day's conversation, with no extra messages (only better direction of the first and last turns that already exist). Code: `_DAY_OPENER_CONTEXT`, `_DAY_CLOSER_CONTEXT`, `_CHARACTER_EXAMPLE_MESSAGES`, and `is_last_turn` in `generate_turn()` of `scripts/simulate_dialogue_week.py`. No card number in the source. Setup: full-week runs; Tests 1-4 one run each, Test 5 three runs.

- Test 1, GPT-5.1, v1 soft directives ("Start with a brief, natural greeting or arrival moment..."; "Wrap up naturally..."), Mini Shepherd's Pies: openers ~60% (4 clear, 1 borderline, 2 missed), closers ~15% (1 clear, 1 borderline, 5 missed), QA 75. Models summarized decisions and forgot to say goodbye; openers failed where voice dominated (Margaret, Steph). Decision: tighten the language before switching models.
- Test 2, GPT-5.1, v2 explicit structure ("STRUCTURE: Your FIRST sentence must be a greeting...", "Your FINAL sentence must be a goodbye", with concrete examples such as "logging off"), same concept: openers ~70% (5 clear, 1 borderline, 1 missed), closers ~70% (5 of 7), QA 76. Devon's "efficient" voice resisted the greeting; Margaret closed 5 of 7 days.
- Test 3, claude-sonnet-4-6, v2, same concept: openers ~70%, closers ~70%, QA 66 (10 below GPT-5.1's 76). Marcus's closers echoed the directive examples (the source table: Mon "Good brainstorm today, heading out.", Tue "Good session, see you tomorrow.", Thu "Good session, heading out."; its prose calls this repetition): the examples over-anchored. Friday Devon's closer was just "both" (possible degenerate output; not investigated).
- Test 4, GPT-5.1, v2, Brown Butter Pecan Tassies (variance check): openers ~60% (4 clear, 1 borderline, 2 missed), closers ~57% (4 of 7), QA 76. The source concludes Test 2's 70/70 "was partially lucky"; characters with efficient or anxious voices (Margaret, Devon, Steph) resisted most.
- Test 5, GPT-5.1, v3 few-shot anchoring, Tassies, 3 runs (21 day-runs): openers 20/21 = 95%, closers 18/21 = 86%, QA 78-80 on the two valid runs (Run 1 had qa=0 / `real_inference=False`, a probable template fallback, and is called unreliable). v3 = 3 example messages per character with the LAST always a greeting/arrival ("Last Example Weight"), motivation-based opener ("You just arrived. Your first words should reflect that arrival in YOUR voice."), and character-filtered closer ("You're leaving. End with a departure in YOUR voice.") with the prescriptive sign-off examples removed.

| Metric | v1 (soft) | v2 (explicit) | v2 (diff concept) | v3 (few-shot) |
|---|---|---|---|---|
| Openers | ~60% | ~70% | ~60% | **95%** |
| Closers | ~15% | ~70% | ~57% | **86%** |
| QA Score | 75 | 76 | 76 | 78-80 |
| Sample size | n=14 | n=14 | n=14 | n=42 |

Caveats the source states: Test 5 misses were Run 1 Tuesday opener (Margaret, "New plan for today:"), Run 1 Mon/Tue closers (Marcus, pure summaries) and Run 2 Monday closer (Steph, conditional plan); Marcus's remaining failure is that his voice guide ("always one sentence too many") makes his last sentence another thought, not a goodbye. Bookend compliance did not move QA in v1 to v2 (75 to 76). The v3 ideas came from a Gemini Deep Research essay (its "Last Example Weight", "Internal Motivation" and "Character-filtered sign-offs" sections), described as "confirmed effective" by Test 5.

Recommendations / open items in the source: Test 6 (Gemini comparison, pending API key) and Test 7 (Marcus-specific closer example, "if we want to push past 86%") were both unchecked. The source does not say whether v3 shipped; the anti-repetition log below ran with v3 directives.

Full log: `git show eb90b0f:docs/research/BOOKEND_TESTING_LOG.md`

### 2026-03-05 — Self-awareness anti-repetition and concept-aware phrase scoring (#5031)

What changed: `generate_turn()` in `simulate_dialogue_week.py` now shows each character what they already said that day ("You already said today: ... Do NOT repeat these phrases, ideas, or sentence structures. Say something new."). Setup: concept Jalapeno Corn Dog Bites, v3 bookend directives, 3 full-week GPT-5.1 runs plus 1 Claude Haiku 4.5 run.

| Run | QA | Prohibited | Cross-char penalty | Notes |
|---|---|---|---|---|
| GPT-5.1 #1 | 81 | 0 | 20 | Strong bookends, good voice variety |
| GPT-5.1 #2 | 75 | 0 | 20 | Marcus "logging off" closer works |
| GPT-5.1 #3 | 64 | 1 | 20 | one prohibited phrase, 2 formal-name uses |
| GPT-5.1 avg | **73** | | | |
| Haiku #1 | 78 | 0 | 20 | voice-pattern bonus 6 vs 2, rhythm 35 vs 20, conflict bonus 5 vs 2 |

Against the pre-anti-repetition v3 runs (BOOKEND Test 5): QA 78-80 without, 73 with (GPT-5.1 avg), 78 Haiku; openers 95% / ~95% (visual check) / ~85%; closers 86% / ~90% (visual check) / ~70%.

Findings and the QA scorer fix:
- The 20-point cross-character phrase penalty was a false positive in every run: characters repeat the recipe name. Fix: `_cross_char_phrase_penalty()` now excludes the concept name, Unicode-normalized (jalapeno / jalapeño).
- Re-scored with the fix: GPT-5.1 #1 81 to 92 (penalty 9, was 20); #2 stays 75 and #3 stays 64 (penalty 20, "genuinely repetitive"); Haiku #1 stays 78 (penalty 20, after accent normalization). Fresh runs with both fixes: GPT-5.1 89 and 77 ("some genuine cross-char repetition remains").
- Anti-repetition "is working but hard to A/B quantitatively": it stops the "Love it Marcus" broken-record problem, but QA was slightly lower (73 vs 78-80). The source offers two explanations, less varied voice-pattern matching or run variance (n=3 is small), and tests neither.
- One Haiku hallucination: Margaret said "brown butter" on Sunday (wrong recipe). Margaret occasionally skips greetings (voice-guide conflict). GPT-5.1 QA spread 64-81 over three runs is wide.

Limits of the setup: n=3 GPT-5.1 and n=1 Haiku, one concept; the ~95% and ~90% opener/closer figures for GPT-5.1 are labelled visual checks in the source. Open items from the source (unchecked): more Haiku runs (n=3) for confidence, consider Haiku for cheaper daily runs, investigate remaining cross-character repeats ("just make sure", "state fair vibes").

Full log: `git show eb90b0f:docs/research/ANTI_REPETITION_TEST_RESULTS.md`

### 2026-03-05 — GPT-5.1 vs Claude Haiku 4.5, generation model head-to-head

Setup: concept Jalapeno Corn Dog Bites; v3 bookend directives + anti-repetition + concept-aware QA scoring; GPT-5.1 n=5, Haiku 4.5 n=7. The 2026-09-23 voice-distinctiveness report (below) states that this generation comparison really happened and that the March 14 model swap concerned compression; that corrects the banner at the top of this file, which says the generation model was never compared.

| Metric | GPT-5.1 (n=5) | Haiku 4.5 (n=7) | Source's winner |
|---|---|---|---|
| QA mean | 77.2 | 77.9 | Tie |
| QA stdev | 9.1 | **4.0** | Haiku |
| QA range | 64-89 | 73-86 | Haiku (tighter) |
| Prohibited hits | 0.2/run | 0/run | Haiku |
| Formal name penalty | 0.8 | 0.6 | Haiku |
| Rhythm variation | **40.6** | 36.6 | GPT-5.1 |
| Voice pattern bonus | **7.6** | 5.4 | GPT-5.1 |
| Conflict bonus | 4.8 | 4.6 | Tie |
| Distinctiveness spread | **23.0** | 17.0 | GPT-5.1 |
| Participation penalty | 3.6 | **3.1** | Haiku |

Per-run QA: GPT-5.1 81, 75, 64, 89 (fresh), 77 (fresh); Haiku 78, 77, 73, 86, 78, 77, 76. Both models still trigger cross-character phrase penalties (~18-19 points average). GPT-5.1 top repeats: "corn dog bites" (12x), "jalapeno corn dog" (11x), mostly the recipe name. Haiku top repeats: "the bite shot" (3x), "the torn edge" (3x). No Haiku hallucinations in the 6 new runs (the "brown butter" slip from run 1 did not recur). Cost per 42-message run: GPT-5.1 ~$0.15-0.20 ($1.00 in / $3.00 out per M tokens), Haiku 4.5 ~$0.15-0.25 ($0.80 / $4.00), GPT-5-mini ~$0.05-0.08 ($0.30 / $1.20); the source calls GPT-5.1 and Haiku roughly equivalent in cost.

Verdict in the source: Haiku is more consistent, GPT-5.1 has the higher ceiling (best 89 vs 86) and more distinct voices. Recommendation: Haiku 4.5 for production daily runs ("consistency > peak performance"; zero prohibited-phrase risk; similar cost); GPT-5.1 for special episodes or quality sweeps; GPT-5-mini not recommended (tested Mar 4, characters sound generic). The source does not say whether it shipped; the banner at the top of this file records Haiku 4.5 as the standing dialogue model.

Coverage gaps the source states: Gemini blocked (no API key; needed #5034, add a Google/Gemini provider to `model_router`); Claude Sonnet only one run (QA 66, Mar 3), needs 5+; GPT-5-mini not in the head-to-head (Mar 4 run used a different scorer and was accidental). Earlier runs are not comparable (older scorer, other message counts): Feb 26 gpt-4o-mini 79-97 (14 messages, 5 concepts); Mar 3 gpt-5.1 Mini Shepherd's Pies 75-76; Mar 3 claude-sonnet-4-6 66; Mar 4 gpt-5.1 Brown Butter Pecan Tassies 76-80; Mar 4 gpt-5-mini 74-77.

Full log: `git show eb90b0f:docs/research/MODEL_COMPARISON_REPORT.md`

### 2026-09-22 — Speaker attribution baseline (leave-one-out content-word classifier)

What was measured: whether word choice alone identifies the speaker in published transcripts. The method (per-week leave-one-out multinomial Naive Bayes on content words, candidate and exclusion rules) is documented in the `speaker_attribution` docstring in `scripts/conversation_metrics.py`. Run: `collect_corpus()` (`scripts/conversation_heatmap.py`) with `include_unpublished=False` and no week filter, against the prepared local corpus `.scratch/attribution-corpus/` (canonical local episode files plus refreshed CDN copies for W37, W38, W39).

Headline: weighted weekly accuracy **26.85%** against weighted chance **17.16%**, over 26 published weeks. Equal-week macro accuracy **26.85%** against **17.18%** macro chance. 999 dialogue lines, 998 scored (99.9% coverage; W24 scored 39 of 40, 97.5%). Included weeks: W11, W13-W21, W23-W38 (26 weeks). Excluded: W12 and W39 as unpublished; W10 present but no dialogue; W22 absent. Candidates per week: 5 (W11, W13, W14, W24) or 6 (all others). Per-week accuracy ranges from 17.24% (W14, chance 20.00%) to 44.74% (W27, chance 16.67%); highest are W27 44.74%, W28 43.59%, W32 39.02%, W29 36.11%, W24 35.90%. Three weeks fall below chance: W11 17.95% (20.00%), W13 17.50% (20.00%), W14 17.24% (20.00%). Latest three weeks: W36 23.26%, W37 19.51%, W38 31.71%. The full 26-row per-week table (lines, scored/coverage, accuracy, chance, candidates) is in `results/speaker-attribution-baseline-20260922.json`, values rounded to four decimals. Weighted summaries weight each week by scored lines; macro summaries weight weeks equally; both summarize per-week models, not one model trained across all weeks.

What this does not prove (source): a small same-week lexical prediction task, 29-44 lines per week, with differing candidate counts per week. A classifier can exploit recurring role language. The score says nothing directly about personality quality, voice consistency across weeks, or whether a reader would recognize a character without names. Treat the weekly values as a baseline for controlled experiments, not a quality grade. See the voice-distinctiveness entry below for what the metric does and does not capture.

Full log: `git show eb90b0f:docs/conversation-lab/SPEAKER_ATTRIBUTION.md`

### 2026-09-23 — Voice distinctiveness: evidence and next measurements (#7522)

Research only, nothing spent; #7522 owns it, #7469 owns the lexical attribution metric (PR #126), #7521 records the future Haiku 4.5 / Opus 5.5 / large-DeepSeek comparison. It "does not establish a dialogue improvement or authorize a production change."

Three separate questions, each needing its own evidence: (1) can a reader tell speakers apart within a scene (repeated speech, adequate coverage, blind reader judgments; job nouns or message lengths alone do not establish it); (2) does each speaker match the intended character (explicit references; within-scene separability alone does not establish it); (3) is the conversation worth reading (coherence, motivated disagreement, resolution, factual credibility, human preference; neither attribution accuracy nor rubric compliance alone). A consistent permutation of speaker names preserves anonymous separability yet can violate identity, so a test expecting scores to fall just because names changed tests the wrong property.

Local evidence at source `2b426d40ca16ab0c603f023cb029afda2fc902ae`:
- The pairwise judge merges separability and identity in one `voice_distinctiveness` description, but `_build_pairwise_prompt` supplies names, roster, recipe facts and transcripts, not character voice guides.
- The 2026-09-23 W25 Thursday calibration (entry in the Experiments section above) used five turns, four speakers, only Marcus twice. `_rotate_speakers` shifts the sequence of turn labels, not a consistent mapping; all three seeds produce the same transformation, so three trials are not three different degraded conversations. The original result has three combined ties, and the code discarded completed pairs' orientation records, so a combined tie could be two explicit ties or order disagreement, and missing verdict fields can default to ties. The archived data cannot tell. Do not retroactively assign raw answers or claim the judge failed to see a proven degradation. Original artifacts stay unchanged.
- The attribution baseline's 26.85% vs 17.16% is a lexical signal, not a voice-quality grade; role vocabulary can supply it. Offline probe of PR #126 source `e8af4ae928bb95b884ef2ef84b6e78e6717f1428`:

| Constructed probe | Accuracy | Reading |
|---|---:|---|
| Three groups, deliberately different vocabularies | 100% | detects lexical separation |
| Same messages, consistent anonymous renaming | 100% | does not establish identity fidelity |
| Two speakers, identical sentence templates, different job nouns | 100% | topic alone can give a perfect score |
| Same content-word bags, different word order | 50%, equal to chance | unigrams do not measure syntax |
| Actual five-line W25 Thursday | unavailable; 2/5 lines scored | too little repeated-speaker evidence |

Inputs, outputs and source hashes: `results/20260923-voice-attribution-audit.json` (rotation section keeps the exact original/rotated transcripts and shows identical outputs for seeds 1, 2, 3). To reproduce, load `speaker_attribution` from the recorded PR #126 source and call it on each scenario's `messages` (the W25 scenario uses `source_turns`).
- `build_system_prompt` already supplies substantial character material (biography, contradictions, relationships, voice guide, examples, memories or first-week fallback, signature phrases, triggers); the four numeric traits are not rendered. The DIALS.md 2(c) denominator/coverage issue is #7430; its ratio is not used as evidence here. Wednesday's opener gets a predetermined photography winner and a later wind-down line says the decision is made; the photography path floors the scene at seven turns without a reshoot, ten with one (plausible limits on disagreement and resolution, not proof that longer dialogue or hidden information would help; see #7161, #7352; keep the authorization gate on automatic injected events).

Research that fits (all "does not prove" for our case): InCharacter (Wang et al., ACL 2024) separates behavioral fidelity from surface recognizability, but its scales are not validated for this cast; CharacterEval (Tu et al., ACL 2024) says ground scores in reviewed human examples, not import its Chinese-language leaderboard; Topic Confusion Task (Altakrori et al., Findings of EMNLP 2021) says validate with same-topic characters and topic-shift controls; Shi et al. 2024 (arXiv 2406.07791) says keep both orientations and report order disagreement, since two orders with different winners are uncertain evidence even if our rule calls them a tie; PersonaWeaver (Qraitem et al., 2026 preprint) is a lead for #7161, vary how a character responds for a concrete reason instead of adding biography or catchphrases; SOTOPIA (Zhou et al., arXiv 2310.11667 and EMNLP 2024) shows omniscient simulation overstates performance, but recipe facts are needed for credibility, so it is not evidence that withholding them would help.

Measurement plan before another quality claim: (1) make judge records inspectable (complete verdict fields, exact inputs, raw answers, arm mappings, both orientations, order disagreement reported separately; keep the conservative decision rule); (2) build a small reference set of real scenes and labelled synthetic controls (identical-pair ties, consistent renaming, inconsistent label mixing, same-topic different styles, different-topic same styles, weak/strong discourse), with expected outcomes treated as hypotheses; (3) Erik reviews character references and ambiguous examples, separate refinement and held-out cases, report valid-response coverage, order consistency, repeatability, agreement per property, thresholds chosen before spending; (4) freeze panel, prompts, model IDs, evaluator and rubric (a changed evaluator is a new scoring version; re-score a common saved set; the v3 panel and production eight-dimension scores stay the baseline); (5) only then resume one authorized prompt experiment, keeping #7472 (rules ablation), #7161 (behavior hypothesis), #6966 (unbound numeric traits) and #7521 (model tests) separate, under the shared $5 ledger.

Historical correction: the March 5 generation comparison happened (five GPT-5.1 and seven Haiku 4.5 runs, entry above). The March 14 model comparison concerned compression. The September 17 high-reasoning Astra probe measured structure without a quality verdict. No isolated reasoning-effort experiment was found in the records read. The lab's empty completed-A/B table does not mean models or prompts were never tested. Model facts as of that date: Opus 5.5 released 2026-09-22; DeepSeek's API docs list `deepseek-v4-pro` and `deepseek-flash`; #7521 must identify the large-model deployment Erik uses before a test. Model and reasoning-effort comparisons are separate experiments with one fixed evaluator and explicit budgets before paid calls.

Full log: `git show eb90b0f:docs/research/VOICE_DISTINCTIVENESS_2026-09-23.md`

### 2026-09-23 — Character memory experiments (#7545): evidence pass, prompt dry run, paid pilot, chain plan

Four stages, all scaffolding; no paid call and no memory-vs-no-memory result existed at the time of the source docs (2026-09-23/24). Mechanics live in the module docstrings of `scripts/memory_lab.py`, `memory_write_experiment.py` and `memory_chain_experiment.py`; the paid runner's are still in [`MEMORY_PAID_EXPERIMENT.md`](MEMORY_PAID_EXPERIMENT.md).

- Stage 1, evidence pass (`memory_lab.py`): offline manifest over three completed weeks (the source suggests copies of W35, W36, W37). It is "preparation for these experiments, not evidence that any memory format improves the dialogue." Next-test questions, to be run holding the writer prompt and model fixed and varying one choice at a time: (1) what a character should notice (self-only, dialogue addressed to them, or all accepted dialogue they witnessed); (2) how perception should differ by role and personality while each claim stays traceable to source messages; (3) which length budget (80, 160, 300, then other justified values) carries continuity without crowding the dialogue prompt; (4) in later weeks, whether low-salience memories should be omitted from prompt selection while impactful ones stay available. Compare recall accuracy, continuity of opinions and relationships, voice, and rendered prompt cost. Treat fading as a selection policy and keep the underlying source and memory record. Prior finding the source cites: full raw-history prompting performed worse than curated highlights, and forced callbacks sounded unnatural, so memories should help when relevant rather than require a callback. Adding the canonical roster and explicit addressed-to detection are separate measured choices.
- Stage 2, W35 prompt dry run (`memory_write_experiment.py`): six A/B prompt pairs (one per character), model planned `claude-haiku-4-5-20251001`, 80-120 token memory-prose target, 220-token response cap. The two arms are bundled memory-writing policies: A = two-sentence third-person recap plus an evidence map; B = source-linked perspective card (Observed / Inference / Stance / Open thread). They differ in structure, perspective, content requirements and citation obligations together, so any gain belongs to the bundle and "cannot identify which individual mechanism caused it"; it is not a format-only test. Length handling in a future paid run: keep `usage.output_tokens` as the billable count; measure prose separately with the same extraction and `count_tokens` method in both arms minus the empty-message baseline (call it normalized prose length); compare quality only at matched prose lengths and report unmatched outputs separately; never pad an unsupported memory to hit the band. The W35 prompt artifact used for sizing has SHA-256 `1919bcd838743d4c53678672fa53848ec66e84040f51c21393db1251a36a5e52`.
- Stage 3, guarded paid pilot (`memory_paid_experiment.py`): 12 requests (6 characters x 2 arms), not run. Sizing from the source for the actual W35 source: all twelve prompt pairs total 92,356 UTF-8 bytes and 18,666 local regex-estimated tokens (largest pair 10,820 bytes / 2,205 estimated tokens); applying the guard's reservation formula to those local estimates (the UTF-8 byte floor is larger for all twelve) gives 141,508 input-reservation tokens plus 2,640 output-reservation tokens, or $0.154708 at the guard's recorded Haiku rates. This is a reference projection, "not a conservative upper bound or a provider-token measurement"; exact `count_tokens` could reserve more. Maximum exposure is the $5.00 ledger ceiling. Predeclared scoring: show A/B in randomized order per character with names retained; hard gate first for unsupported factual claims or citations that do not support them; then score whether interpretation is grounded and character-specific and whether the memory could help future dialogue without forcing a callback (no supported stance change or open thread is acceptable). Pairwise preference only when both normalized prose lengths are 80-120 tokens and differ by at most 15 (`matched_prose_lengths()` in the script). Advance one bundle to a separate length test only if there are no source-grounding hard defects and at least 5 of 6 scorable matched-length pairs prefer it for perspective/usefulness; fewer than 5 scorable pairs, any grounding defect, or no bundle meeting the threshold means "inconclusive". Even 5 of 6 is exploratory because all six pairs share one episode. The pilot does not test memory length, fading or downstream dialogue effects. Paid execution awaited explicit authorization; any spend needs the exact artifact hash and a separately reviewed cost projection.
- Stage 4, three-week chain (`memory_chain_experiment.py`), planned, not run: fixed weeks, concepts and seeds (constants in the script), paired arms `control_no_persistent_memory` (nothing carried) and `weekly_character_memory` (one short source-linked memory per character, written after each week and visible only to that character's next week). Measures: voice distinctiveness by named character, grounding against cited source turns, whether a prior memory changes later-week dialogue, prompt and memory token counts per character and week. Sequencing in the source: the memory-length comparison should follow the memory-format experiment, holding the selected format fixed while varying the rendered memory budget; the chain then tests whether short character-specific memories change later dialogue without inventing unsupported events or perspectives. It stops before a full-week simulation adapter; a test adapter must be separately reviewed to replace every provider call and route every write, and a separate cost decision is needed before any paid call.

Full logs: `git show eb90b0f:docs/conversation-lab/MEMORY_LAB.md`, `git show eb90b0f:docs/conversation-lab/MEMORY_WRITE_EXPERIMENT.md`, `git show eb90b0f:docs/conversation-lab/MEMORY_CHAIN_EXPERIMENT.md`

## Benchmarks

Single-arm characterization runs (`conversation_lab.py bench`). A row here is a baseline another run gets compared against, not a decision.

| Date | Label | Stage | N | Pass rate (scored/ran) | Most frequent weakest | Result file |
|------|-------|-------|---|----------------------|-----------------------|-------------|
| 2026-09-25 | monday-n2 | monday | 2 | 0% (2/2) | arc_resolution | bench-monday-n2-20260925T165714Z.json |
