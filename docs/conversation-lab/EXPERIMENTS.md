# Conversation Lab - Experiment Log

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
