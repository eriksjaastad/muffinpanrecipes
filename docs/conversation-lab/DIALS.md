# Conversation Dials

Card #6492. Branch feat/6492-conversation-lab. Written 2026-09-06 from the W11-W36 corpus (917 lines, 24 weeks; W12 unpublished, W22 absent) and the generation code; revised the same day after Erik's review. Method: [PROTOCOL.md](PROTOCOL.md). Results: [EXPERIMENTS.md](EXPERIMENTS.md).

## 1. What we are trying to make

Six specific people talking about a real recipe. A reader can name the speaker with the names covered. Someone is sometimes wrong, tired, or short. A disagreement lands, gets answered with a reason, and resolves because of the reason. Nobody recites the brand; when the pan matters, a character notices it because of something in front of them.

### What good sounded like

W25 Thu:

> Marcus: Morning, all. Got the harissa chickpea situation drafted and I'm thinking the title should be "Five-Minute Breakfast in a Muffin Pan" - something that promises speed but doesn't lie about the actual work involved, which I think matters here.
> Ria: Five minutes is a lie though - the dicing alone takes longer, and people hate that. What about "Crispy Chickpea Cups, Five Ingredients"? That's honest and it stops the scroll.
> Marcus: You're right about the dicing - I was leaning into the promise rather than the reality, which is exactly the trap you called out. What if we went "Harissa Chickpea Feta Cups" and let the copy do the work of showing it's actually doable, not pretending it's faster than it is?

W36 Mon:

> Ria: That's the hook right there - crispy edges, custardy center, twelve perfect portions you can grab with one hand.
> Margaret: That's not the hook - the hook is that the pan's walls give you structural support while the custard sets, so you get clean edges without a mold. The grab-and-go part is just a bonus.
> Ria: Fair - the structural thing is actually what makes it work visually.

W30 Sat:

> Margaret: Good, but did you taste the pickle brine before it went in - that ratio looked thin when I checked it yesterday.
> Devon: Checked it this morning - ratio's solid, you were just looking at it before the sugar dissolved.
> Margaret: Fair enough - sugar takes time.

The 09-02 spanakopita regen (never shipped; session transcript 6487b243):

> Margaret: Coffee's cold. Who decided spanakopita cups this week?
> Ria: That was me - heard it tested well in focus groups but let me actually see what we're working with before we commit.
> Margaret: Focus groups don't know phyllo. You need lamination or the whole thing falls apart - literally and texturally.

One shape in all four: a specific objection, a direct answer with a concrete reason, acceptance because of the reason. The spanakopita run relapsed on line four ("Margaret's right - ... That's the actual story").

### What robotic sounds like

Claim, dash, mechanism (W36 Mon, Marcus): "The shells need low, steady heat to set properly - that's where the pan's geometry actually matters, because the walls trap warmth and give you an even bake that a loose tart shell never gets. Copy should lead with that."

Pronouncement frame (W19 Mon, Steph): "The beauty of sheet pan eggs is watching them actually puff - that's the story, not the technique itself."

Agree-opener plus forced brand (W16 Mon, Marcus): "Margaret's right - the crispness is earned, not performed, and that's actually the story worth telling." W32 Mon, Margaret: "That is exactly why this wants to be a muffin pan."

## 2. Why it sounds robotic

### (a) Our own rules produce the tells

Line numbers are scripts/simulate_dialogue_week.py unless another file is named.

The dash. Line 383: "NEVER use em dashes or en dashes." The model writes them anyway and sanitize_typographic_tells (1025-1043) rewrites each into " - ", the exact string DASH_CLAUSE_RE counts. Part of the 86% dash-clause rate is our own sanitizer; the fix is asking for two sentences, not a different glyph.

The cap between the caps. Line 370: "HARD LIMIT: 1-2 sentences max. If you wrote more than 25 words, rewrite shorter." It sits between the per-character voice caps (283-365: Margaret 15 words, Devon 12, Ria 20, Marcus 35) and the model, compressing everyone toward 25.

The agree-opener. Line 377: "acknowledge it before pivoting." _REACTION_DIRECTIVE (249): "Your first sentence must respond to what was just said." Line 381 bans names. Inside 25 words the cheapest legal move is "Fair - <pivot>".

The mechanism sentence. The pan block (385-390): "Name the mechanism: what the pan's walls, depth, or heat do to THIS batter, dough, or filling ... It has to follow from something a character just observed." Observation-dash-mechanism, demanded of everyone every turn.

The pronouncement. CHARACTER_DAY_GOALS hands Marcus "the story angle" (83) and Ria "hooks" (84) every Monday; the arc text adds "Marcus sees a literary angle in it" (1506).

Boilerplate is structural. _DAY_LEADS (1587-1595) fixes Devon as Saturday's opener and Julian as Wednesday's; arrival text is identical every week; Devon's few-shot line (435) is "Pushed the fix. Should be live in two minutes."; Wednesday's opener gets identical variant descriptions (1688-1692) and "After real debate, they land on {winner_variant}" (1723). Result: 24 of 25 Saturday openers and 25 of 25 Wednesday Julian openers are the same; 12 of 24 Saturdays open with the exact line "Staging the muffin pan recipe now, should be live in a few"; 24 of 24 Wednesdays open "Just ..." with macro, overhead, three-quarter.

No history. Production (cron_routes.py 229-242) calls run_simulation without initial_recent_lines, so every day opens from "(no prior messages)" (722).

Frozen memory. Every memory.json under backend/data/characters is stamped 2026-W11. Ria's is 38 bytes, so build_system_prompt (528) tells her "THIS IS YOUR FIRST WEEK ON THE JOB" every week.

Hidden second call. Any shared 3-gram with the last 6 lines triggers a rewrite (662-686) whose prompt keeps only "different structure and new specific detail" (905) and drops goal, reaction directive and recipe anchor. Frequency unmeasured.

### (b) Uniform register

Everyone is the same 24-word expert. Per-line sd is 4.5 to 7.4 for every character; nobody varies inside their own voice. Only Devon is ever short.

| Character | lines | mean words | sd | <8 words | dash | pronouncement | agree-open | question | brand |
|---|---|---|---|---|---|---|---|---|---|
| Margaret | 279 | 21.0 | 6.2 | 0% | 77% | 20% | 4% | 9% | 14% |
| Marcus | 165 | 31.4 | 7.1 | 0% | 100% | 45% | 16% | 13% | 15% |
| Steph | 197 | 23.1 | 5.6 | 1% | 95% | 26% | 21% | 54% | 10% |
| Julian | 80 | 27.9 | 6.1 | 0% | 100% | 35% | 15% | 10% | 5% |
| Devon | 80 | 13.0 | 4.5 | 10% | 38% | 2% | 25% | 0% | 12% |
| Ria | 116 | 28.1 | 7.4 | 0% | 99% | 51% | 26% | 18% | 12% |

### (c) Backstory is pasted in; nothing per character binds

backend/data/agent_personalities.json carries numeric traits per character (verbosity, directness, formality, emotional_expressiveness). build_system_prompt (484-557) reads none. It pastes bio.md and the relationship text, then ~650 tokens of universal rules that outweigh the character material 1.5-2.5x. Margaret exceeds her voice cap on 83% of lines, Julian 92%, Ria 90%. 36 signature phrases produce one hit in 917 lines (Julian, "negative space", W16 Fri). Designed directness is inverted: Ria (0.85) opens agreeing 26%, Margaret (0.8) 4%. Nothing turns backstory into a constraint the model can obey or a number we can check.

### (d) The judge passes it

W30-W36 Monday verdicts all begin "PASS - Characters are distinct and". Heuristic QA scored 82-92 on the weeks Erik calls robotic. The judge prompt (cron_routes.py 288-303) scores title fidelity, technical credibility, voice, turn-taking, arc, cast; nothing scores sentence shape, length variance, question rate or register. Judge and generator disagree about Steph: cron_routes 274 "NOT a nervous intern", simulate 310 "terrified of Margaret". Structured judge scores (#6861) first go live W37.

### Corpus readings

| Reading | W11-W36 | W36 | Mondays (all weeks) |
|---|---|---|---|
| lines | 917 | 43 | 208 |
| mean words / sd | 24.1 / 8.1 | 24.5 / 7.8 | - |
| dash-clause | 86% | 91% | 91% |
| pronouncement frame | 30% | 33% (Mon 67%) | - |
| agree-opener | 21% | 21% (Mon 44%) | - |
| question | 20% | 16% | 25% |
| under 8 words | 1% | 2% | - |
| brand term | 13% | 19% (Mon 44%) | 31% |

Brand by day: Monday 31%, Tuesday 5%, Wednesday 3%, Thursday 8%, Friday 6%, Saturday 9%, Sunday 11%. Even across characters (10-15%) because the pan block is identical for everyone; concentrated on Monday because Monday's goal is "why this dish".

## 3. Conversation dials

Metrics are functions in scripts/conversation_metrics.py; judge dims are from cron_routes.py _JUDGE_SYSTEM_PROMPT. "Too far" is the guard that says dial back.

| Dial | Controls | Reading today | Metric | Lever text (file:symbol:line) | Direction we think | Too far when |
|---|---|---|---|---|---|---|
| Structure: dash clause | two-clause claim-dash-reason sentences | 86% | dash_clause_rate | simulate: sanitize_typographic_tells 1025-1043; _SHARED_CHARACTER_RULES 370, 383 | down, target 30-50% | adjacency_continuity falls, or technical_credibility falls (reasons got dropped, not reshaped) |
| Structure: length variance | spread of line lengths | sd 8.1; 1% under 8 words | length_stdev, short_line_rate | simulate: 370 global cap; _voice_pattern_score 1208; min_content_failures 1391 | sd up to 10+, short lines 10-15% | short lines are filler ("Yeah." "Fair.") and qa_rate or turn_taking falls |
| Pronouncement frames | "that's the story / the hook" | 30% (Ria 51%, Marcus 45%) | frame_claim_rate | simulate: CHARACTER_DAY_GOALS 83, 84, 95, 99; _build_dynamic_arc 1506; json signature_phrases 138, 199, 318-322 | down to 10% | arc_resolution falls (nobody ever states a conclusion) |
| Agree-openers | lines that start by agreeing | 21% | agree_opener_rate, opener_diversity | simulate: 377, _REACTION_DIRECTIVE 249, role_chain 816, name ban 381 | down to 8-10% | adjacency_continuity or qa_rate falls (people stop responding to each other) |
| Questions and disagreement | asks, refusals with reasons | 20% question; outright "no" almost only Margaret | question_rate, question_answer_rate | simulate: closer 874, 250, Steph guide 298, _conflict_bonus 1144-1148 | questions 25-30% with qa_rate up; refusals spread beyond Margaret | question_answer_rate falls (questions asked, never answered); arc_resolution falls |
| Brand reinforcement (Monday) | pan mentions | 13% overall, 31% Monday | none dedicated (heatmap brand area; add brand_term_rate) | simulate: THE MUFFIN PAN IS THE POINT 383-393; DAY_MEETING_GOAL monday 48-51 | Monday down to one earned line, 5-10% | title_fidelity or technical_credibility falls; the pan vanishes from a recipe where it matters |
| Mechanism justification | reason-after-claim sentences | inside the 86% dash figure | dash_clause_rate (proxy) | simulate: 385-390 | fewer, not zero; keep in Margaret and Devon, drop the universal demand | technical_credibility falls |
| Boilerplate: Saturday deploy line | "should be live in a few" | 23 of 25 weeks | repeated_phrases (cross-week via heatmap) | simulate: _DAY_LEADS 1594; Devon example 435; DAY_STAGE_DIRECTIONS 141; _DAY_OPENER_CONTEXT 193 | 0 cross-week 4-grams | Saturday loses its completion_signal and the closer never fires |
| Boilerplate: photography stock | "macro, overhead, three-quarter, one broken open" | 24 of 24 Wednesdays | repeated_phrases, pitch_vocab_rate | simulate: variant descriptions 1688-1692; 1717-1724; _DAY_LEADS 1591 | opener varies by week; winner not pre-announced | Wednesday no longer names which shot won (arc_resolution) |
| Pitch vocabulary | hook, scroll, performs | "stops the scroll" 16 of 25 weeks; Ria "scroll" 38% of lines | pitch_vocab_rate | simulate: Ria guide 345, goals 84, 99, example 440; Steph goal 95 | down by half; replace with numbers and format specifics | Ria stops sounding like social at all (voice_distinctiveness) |
| Cast coverage | who speaks, how often | Margaret 30% of lines; Steph named once in 720 lines | cast_coverage | simulate: participants_for_day 271-289; TICKS_RANGE 222-230; 40% cap 1651 | Margaret under 25%; every roster member speaks | ticks rise past budget; turn_taking falls |
| Turn-taking context depth | what a non-opener sees | 4 lines mid-day; no cross-day history in prod (cron_routes 229-242) | adjacency_continuity | simulate: 718-723, 807-838 | day scene to every turn; prior-day summary to Monday-after | prompt tokens up with no continuity gain; CoT leak (RUNBOOK #5919) |

## 4. Personality dials

Backstory says who a character is, not how long a line is or how often to ask. Those are numbers, and agent_personalities.json already carries them (verbosity, directness, emotional_expressiveness, formality) while build_system_prompt reads none (section 2c). "Dials" means making those existing numbers bind and adding a daily state, not inventing a schema.

### Range, not setting; state, not schedule

Erik, 2026-09-06: "We don't want to lock a character into a day-in, day-out Devon-is-terse scenario. That gets old instantly. People have good days and bad days, don't sleep the night before, or have something else going on ... Those things change every day ... We also don't want it so that on Tuesdays Devon's pissed off and on Friday he's in a good mood."

A character is three layers:

- **Core** (stable): what they care about, how they argue, who they clash with.
- **Range** (stable): a floor and ceiling per dial. The character never leaves it.
- **State** (per day): sampled fresh each day from a small distribution - slept badly, distracted, fired up, patient, short-fused, in a good mood - and used to move that day's settings within the range. Written into the episode JSON (for example `dialogue.state["devon-park"] = "short-fused"`) so the judge and review_episode.py can score the day against it.

State is random per day, never keyed to the weekday, never carried as a fixed trait. Every character can have a bad day, Steph and Margaret included. The reader never sees the label; the generator renders it as one register note ("You slept badly; shorter and less patient than usual"), never a stage direction the model can quote. The lab checks the ranges; the model gets positive register notes. Section 2b figures are not repeated below.

**Margaret Chen.** Core: technique first; refuses when the ratio is wrong; clashes with Marcus on copy, Ria on hooks. Observed: "ratio" in 27% of lines, zero signature hits, a confident authority rather than a muttering one. Range: mean 9-16 words (patient toward 16, short-fused toward 9); short lines 15-30%; agree-open 0-6%; questions 5-15%; one sarcasm most days, none on a patient one. Never: "ratio" twice in a day, "honest", "actually".

**Marcus Reid.** Core: literary over-writer who resents "keep it simple"; clashes with Margaret and Ria on length. Observed: "You're right" opens 10% of lines; folds every Thursday in one turn. Range: mean 22-35 (fired up toward 35, distracted toward 22); pronouncement 5-15%; agree-open 0-10%; defends his copy once before folding most days, folds fast on a slept-badly day. Never: "narrative", "the real story".

**Steph Whitmore.** Core: hedges, apologizes, wants Margaret's approval, envies Julian, still has to make the call. Observed: names others in 25% of lines, addressed once in 720; a competent moderator nobody pushes back on. Range: questions 35-60%; decision tokens 10-25% of lines (fired up toward 25); agree-open 0-12%; mean 14-24 words; trails off on a slept-badly day, decides cleanly on a good one. Never: "nailed", "you're both right", "that's exactly". Someone questions her call weekly (cast lever). Her design is open (section 8).

**Julian Torres.** Core: pretentious, defensive conceptualist; clashes with Ria on crops, Margaret on what a photo is for. Observed: same three-shot menu 24 of 24 Wednesdays, 1 signature hit; a deferential vendor with a menu. Range: mean 16-26; pushes back on a photo note before conceding 0-2 times a day (short-fused toward 2); aesthetic jargon 0-2 words per line; "macro/overhead/three-quarter" one line per Wednesday; Wednesday opener 0 cross-week 4-grams.

**Devon Park.** Core: shrugging automator who deflects with jargon; second baker on Saturdays; clashes with Ria (alliance), Margaret (tasting). Observed: 10% short is the one dial that matches; deploy line 12 of 24 Saturdays; right length, wrong content. Range: mean 9-18 words (in a good mood toward 18, short-fused toward 9); questions 15-40%; agree-open 0-8%; tech content 30-50% of lines; short lines 10-35%. Never: "ratio", tasting verbs.

**Ria Castillo.** Core: blunt TikTok native who argues with numbers; clashes with Julian on crops, Margaret on hooks. Observed: "scroll" in 38% of lines, a number in 6 of 116, dangling "pm" in 7; a 28-word marketing panelist. Range: mean 10-20 words; pronouncement 5-15%; agree-open 0-10%; a number or format spec in 25-45% of lines (fired up toward 45); "scroll" and "hook" at most one line a day; lowercase pending section 8.

### What has to change for these to be real

The lab's variant mechanism overrides four module constants (conversation_lab.py ALLOWED_VARIANT_ATTRS, 171-176), none per character, so a personality-dial experiment is not runnable as written. Making dials bind touches about ten files: agent_personalities.json (ranges, state distribution), simulate_dialogue_week.py (render core + range + state; drop the 370 cap; seeded daily state written to the episode; cache keyed on dial version; _voice_pattern_score and _select_next_speaker read the range), conversation_metrics.py (actual-vs-range, split by state), conversation_lab.py (persona override; state pinned or random per arm), conversation_heatmap.py and review_episode.py (range overlay, state column), cron_routes.py (judge rules from the same JSON, state visible to the judge), backend/core/personality.py (additive fields), .vercelignore (a backend import of conversation_metrics needs a "!" line or fails only in the Lambda, RUNBOOK Incident 4), and the six test files. Its own card, not #6492.

## 5. The sitcom layer (B-plots), and why not yet

CREATIVE_BIBLE.md designed it: A-plot/B-plot per episode (161, 244), story_arc.b_plot in the episode schema (340-345), "B-plots can span multiple weeks" (377), evolving relationships, running jokes, a varying tension day. In code: injected_event plumbing end to end (cron_routes 1771 to simulate 724) with one consumer, a hardcoded W15 paragraph; every production prompt since carries "Injected event: none". Not in code: story_arc, a_plot, b_plot (zero hits in *.py), a running-joke store, a varying tension day (DAY_ARC is fixed per weekday), evolving memory (frozen at W11). Card #5471 (character calendar) cancelled 2026-09-06; #5032 and #5033 in April.

Erik: "I definitely don't want to go into the sitcom yet because we have not figured out writing at all." A B-plot in the current voice adds more 24-word claim-dash-mechanism lines about a birthday instead of a custard, and the lab could not tell which layer moved the read.

Gate: Structure, Frames and Brand dials inside target for two consecutive live weeks, and PROTOCOL.md's grader calibration passing. Then one offline A/B on the test bed: a single event through the existing injected_event parameter (no calendar, no new writer), blind-read against the same week without it. Only if it wins does one live week get one event. Calendar and multi-week threads stay parked until then.

## 6. Where the effort goes now

Erik's stated priority is the heat map plus offline experiments: ideally ten single-lever experiments a week on the test bed, each compared as a before/after heat map for variety and engagement, then winners shipped one at a time to live weeks. The weekly sweep in PROTOCOL.md is the cadence; `conversation_lab.py ab --sweep` is the command. Nothing in sections 4 or 5 starts before that loop is running.

## 7. How to turn a dial without breaking something

PROTOCOL.md owns the method. One dial per experiment: offline blind A/B on the test bed, both arms judged and read blind, PROTOCOL.md decision rule, then one live week as confirmation, logged in EXPERIMENTS.md with before and after numbers.

Dial-back rule: every shipped lever is re-measured two weeks later against the section 3 table. If the target moved but a guard dimension fell (technical_credibility, title_fidelity, arc_resolution, qa_rate, adjacency_continuity), revert it in a PR and log the revert with the numbers that triggered it. A revert is a result. Never two dials in one week; never a new one inside a previous one's two-week window.

## 8. Open questions for Erik

1. Steph's designed weakness (freezing, hedging) reads as a flaw on a real team, and the judge and generator already disagree about her. Stay anxious, or become the one who is occasionally short and blunt, with the judge's "NOT a nervous intern" made canonical?
2. How much brand per Monday: zero, one earned line, or "only when the pan changes the outcome"? Section 3 assumes one earned line.
3. Which two characters should disagree most often? Today's rosters make Julian vs Ria (crops) and Devon vs Ria (alliance) nearly impossible; changing rosters is a cast lever with a cost in ticks.
4. How much randomness in daily state feels human rather than erratic - one notable state per character per week, or every day?
5. Margaret speaks 30% of lines and is the authority everyone defers to. Keep her as the hub, or cap her and let someone else be right sometimes?
6. Is Ria allowed to write in lowercase on the live site, or does house style forbid it?
