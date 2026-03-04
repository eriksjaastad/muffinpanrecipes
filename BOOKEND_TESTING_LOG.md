# Dialogue Bookends — Testing Log

## Feature
Character-driven opening greetings and closing sign-offs for each day's conversation.
No extra messages — just better-directing the first and last turns that already exist.

## Files Modified
- `scripts/simulate_dialogue_week.py`
  - `_DAY_OPENER_CONTEXT` dict — day-specific arrival texture
  - `_DAY_CLOSER_CONTEXT` dict — day-specific wrap-up texture
  - `generate_turn()` — `is_last_turn` param, opener/closer directives injected into prompts
  - `run_simulation()` — passes `is_last_turn=(tick == day_ticks - 1)`

---

## Test 1: GPT-5.1, v1 directives (soft language)
**Date:** 2026-03-03
**Model:** openai/gpt-5.1
**Concept:** Mini Shepherd's Pies
**File:** `sim-20260303-180039-mini-shepherd-s-pies-openai_gpt-5.1-full-run1-full-week.json`
**QA Score:** 75 | **Messages:** 42

### Opener directive (v1)
> "Start with a brief, natural greeting or arrival moment - walking in, logging on, checking in.
> Then introduce the day's topic. Don't jump straight into business without any human warmth."

### Closer directive (v1)
> "Wrap up naturally - confirm what was decided, say goodbye, or sign off for the day.
> Keep it brief and in character. Don't introduce new topics or ask questions."

### Results — Openers
| Day | Character | Greeting? | Notes |
|-----|-----------|-----------|-------|
| Mon | Margaret | YES | "Morning. Who decided 'mini' anything..." |
| Tue | Margaret | NO | Jumped straight to "Let's just lock the white 6 oz ramekins" |
| Wed | Julian | BORDERLINE | "Just got to set" — more stage direction than greeting |
| Thu | Marcus | YES | "Morning, all - laptop open, caffeine engaged" |
| Fri | Margaret | YES | "Morning. If we want sign-off by 5:00 pm..." |
| Sat | Devon | YES | "Morning. I pushed the staging build..." |
| Sun | Steph | NO | Launched straight into a confirmation question |

**Opener hit rate: ~60% (4 clear, 1 borderline, 2 missed)**

### Results — Closers
| Day | Character | Sign-off? | Notes |
|-----|-----------|-----------|-------|
| Mon | Marcus | NO | Long ramble, no goodbye |
| Tue | Marcus | NO | Summarized decisions, no sign-off |
| Wed | Julian | BORDERLINE | "so I have what I need from this scene for today" |
| Thu | Steph | NO | Summary, no goodbye |
| Fri | Julian | NO | Summary, no goodbye |
| Sat | Margaret | NO | Plan statement, no sign-off |
| Sun | Margaret | YES | "I am going home." — perfect |

**Closer hit rate: ~15% (1 clear, 1 borderline, 5 missed)**

### Analysis
- Model CAN do it (Mon opener, Sun closer prove capability)
- Openers fail when character voice dominates (Margaret skips greetings, Steph overthinks)
- Closers fail because arc resolution prompt outweighs the sign-off directive — models summarize decisions but forget to say goodbye
- Soft language ("start with a brief greeting", "wrap up naturally") gives the model too much room to interpret away

### Decision
Tighten directive language before switching models. Prompt engineering > model upgrade when the model has proven capability.

---

## Test 2: GPT-5.1, v2 directives (explicit structure)
**Date:** 2026-03-03
**Model:** openai/gpt-5.1
**Concept:** Mini Shepherd's Pies
**File:** (pending — running now)

### Opener directive (v2)
> "STRUCTURE: Your FIRST sentence must be a greeting or arrival moment -
> 'Morning', 'Hey all', 'Just got in', etc. Even one word counts.
> Your SECOND sentence introduces or pitches the day's topic.
> Do NOT skip the greeting and jump straight into work."

### Closer directive (v2)
> "STRUCTURE: Briefly confirm what was decided (one sentence max),
> then END with a sign-off — 'heading out', 'see you tomorrow', 'night',
> 'done for today', 'logging off', etc. Your FINAL sentence must be a goodbye or departure.
> Keep it short. Don't introduce new topics or ask questions."

### Changes from v1
- Added "STRUCTURE:" prefix — signals this is a format requirement, not a suggestion
- Specified sentence positions explicitly (FIRST sentence = greeting, SECOND = topic)
- Gave concrete examples for both openers and closers
- "Do NOT skip" / "must be" — harder language
- Closer separates the two jobs: summarize (1 sentence) THEN sign off (final sentence)

### Results
**File:** `sim-20260303-184415-mini-shepherd-s-pies-openai_gpt-5.1-full-run1-full-week.json`
**QA Score:** 76 | **Messages:** 42

### Results — Openers
| Day | Character | Greeting? | Notes |
|-----|-----------|-----------|-------|
| Mon | Margaret | YES | "Morning. So, mini shepherd's pies, apparently..." |
| Tue | Margaret | YES | "Morning. We have eight hours to turn nana pie..." |
| Wed | Julian | YES | "Morning, emerging from the prop closet..." |
| Thu | Marcus | BORDERLINE | "Question for you, Steph:" — no greeting, but conversational address |
| Fri | Margaret | YES | "Morning. Before anyone says 'weekend'..." |
| Sat | Devon | NO | Jumped straight into task plan, no greeting |
| Sun | Steph | YES | "Hey team, for Mini Shepherds can you do..." |

**Opener hit rate: ~70% (5 clear, 1 borderline, 1 missed)**

### Results — Closers
| Day | Character | Sign-off? | Notes |
|-----|-----------|-----------|-------|
| Mon | Steph | YES | "...so I am going to duck out now." |
| Tue | Margaret | NO | Locked decisions but no sign-off |
| Wed | Margaret | YES | "...all locked and worth the dental bill. Logging off." |
| Thu | Margaret | YES | "Copy is in... heading out." |
| Fri | Marcus | NO | Gave Devon instructions, no goodbye |
| Sat | Margaret | YES | "Staging locked... logging off." |
| Sun | Margaret | YES | "...no fires, for once - logging off." |

**Closer hit rate: ~70% (5 clear, 0 borderline, 2 missed)**

### Analysis — v1 vs v2 comparison
| Metric | v1 (soft) | v2 (explicit) | Delta |
|--------|-----------|---------------|-------|
| Opener hit rate | ~60% | ~70% | +10% |
| Closer hit rate | ~15% | ~70% | +55% |
| QA Score | 75 | 76 | +1 (neutral) |

- **Closers massively improved.** "Logging off" / "heading out" / "duck out now" — the concrete examples in the directive gave the model a pattern to follow.
- **Margaret dominates closers** — she's the closer 5/7 days. Makes sense with her voice ("short, clipped").
- **Openers improved but not 100%.** Devon (Sat) still skipped the greeting — his voice guide says "efficient and understated" which fights the greeting directive.
- **Thursday opener (Marcus)** addressed Steph directly but no "morning" — borderline.
- **Tuesday closer still missed** — Margaret locked decisions but didn't sign off. The arc resolution instinct still wins sometimes.
- **No QA score regression** — bookends don't hurt quality scoring.

### Remaining issues
1. Devon's voice ("1 sentence max, efficient") actively resists greetings
2. Marcus sometimes addresses people instead of greeting (conversational but not a hello)
3. ~30% miss rate on both ends — acceptable? Or push for higher?

### Decision
v2 directives are a significant improvement. Test next with Claude Sonnet to compare model instruction-following before deciding if v3 tightening is needed.

---

---

## Test 3: Claude Sonnet, v2 directives
**Date:** 2026-03-03
**Model:** anthropic/claude-sonnet-4-6
**Concept:** Mini Shepherd's Pies
**File:** `sim-20260303-184932-mini-shepherd-s-pies-anthropic_claude-sonnet-4-6-full-run1-full-week.json`
**QA Score:** 66 | **Messages:** 42

### Results — Openers
| Day | Character | Greeting? | Notes |
|-----|-----------|-----------|-------|
| Mon | Margaret | NO | "Shepherd's pie in a muffin tin. Someone tell me why..." — no greeting |
| Tue | Margaret | YES | "Morning. Lamb or beef, that's the first decision..." |
| Wed | Julian | YES | "Alright, I'm on set, lighting's actually cooperating..." |
| Thu | Marcus | YES | "Morning, opening my laptop and the headline question..." |
| Fri | Margaret | BORDERLINE | "Friday." — day name as greeting, then jumped to done |
| Sat | Devon | YES | "saturday. staging's up, doing final checks..." |
| Sun | Steph | YES | "Good morning everyone, okay it's publish day..." |

**Opener hit rate: ~70% (5 clear, 1 borderline, 1 missed)**

### Results — Closers
| Day | Character | Sign-off? | Notes |
|-----|-----------|-----------|-------|
| Mon | Marcus | YES | "Good brainstorm today, heading out." |
| Tue | Marcus | YES | "Good session, see you tomorrow." |
| Wed | Steph | NO | Praised the work but no sign-off |
| Thu | Marcus | YES | "Good session, heading out." — submitted at 2:47 pm detail is great |
| Fri | Devon | NO | Just "both" — truncated/weird response |
| Sat | Margaret | YES | "Done for today." |
| Sun | Margaret | YES | "Good pies. Night." — perfect Margaret closer |

**Closer hit rate: ~70% (5 clear, 0 borderline, 2 missed)**

### Analysis — Cross-model comparison (all v2 directives)
| Metric | GPT-5.1 (v1) | GPT-5.1 (v2) | Sonnet (v2) |
|--------|------------|------------|-------------|
| Opener hit rate | ~60% | ~70% | ~70% |
| Closer hit rate | ~15% | ~70% | ~70% |
| QA Score | 75 | 76 | 66 |
| Messages | 42 | 42 | 42 |

**Sonnet observations:**
- Bookend compliance matches GPT-5.1 v2 (~70/70) — the directive language matters more than the model
- **QA score dropped 10 points** (76 → 66). Sonnet's dialogue may have quality/voice issues separate from bookends
- **Friday closer is broken** — Devon said just "both" which is either a truncation or degenerate response
- Sonnet's character voice feels slightly different — Marcus says "Good session" pattern repeatedly (Mon, Tue, Thu closers)
- "Good pies. Night." is peak Margaret — Sonnet nails her voice in closers
- **Repetition risk:** Marcus used "Good session, heading out" twice and "Good session, see you tomorrow" once — the examples in the directive may be creating a template

### Concerns
1. **QA score gap:** Sonnet 66 vs GPT-5.1 76. Need to understand what's penalizing Sonnet before drawing conclusions
2. **Closer formula:** "Good [noun], [sign-off]" appearing too often — directive examples may be over-anchoring
3. **Friday Devon "both"** — possible degenerate output, needs investigation

---

---

## Test 4: GPT-5.1, v2 directives, different concept (variance check)
**Date:** 2026-03-03
**Model:** openai/gpt-5.1
**Concept:** Brown Butter Pecan Tassies
**File:** `sim-20260304-003819-brown-butter-pecan-tassies-openai_gpt-5.1-full-run1-full-week.json`
**QA Score:** 76 | **Messages:** 42

### Results — Openers
| Day | Character | Greeting? | Notes |
|-----|-----------|-----------|-------|
| Mon | Margaret | YES | "Morning. We locking Brown Butter Pecan Tassies today..." |
| Tue | Margaret | YES | "Morning. Starting tassie dough ratios now..." |
| Wed | Julian | YES | "Heading in now with props and surfaces..." |
| Thu | Marcus | BORDERLINE | "Quick check-in before I start typing..." — conversational but no greeting |
| Fri | Margaret | YES | "Morning. Let's get this tray wrapped..." |
| Sat | Devon | NO | "On it: I am loading the recipe page..." — straight to task |
| Sun | Steph | NO | "Quick status sweep..." — no greeting |

**Opener hit rate: ~60% (4 clear, 1 borderline, 2 missed)**

### Results — Closers
| Day | Character | Sign-off? | Notes |
|-----|-----------|-----------|-------|
| Mon | Marcus | NO | Summary, no goodbye |
| Tue | Steph | YES | "before I disappear for a bit" |
| Wed | Margaret | NO | Gave directions, no sign-off |
| Thu | Steph | YES | "step away for tonight" |
| Fri | Marcus | YES | "Before I disappear..." |
| Sat | Margaret | NO | Summary, no departure |
| Sun | Marcus | YES | "logging off" |

**Closer hit rate: ~57% (4 clear, 0 borderline, 3 missed)**

### Analysis — concept variance
| Metric | Shepherd's Pies (Test 2) | Pecan Tassies (Test 4) | Delta |
|--------|--------------------------|------------------------|-------|
| Opener hit rate | ~70% | ~60% | -10% |
| Closer hit rate | ~70% | ~57% | -13% |
| QA Score | 76 | 76 | 0 |

- **Results are worse with a different concept.** The 70/70 from Test 2 was partially lucky.
- **Same problem characters:** Devon (Sat opener) and Steph (Sun opener) skip greetings. Margaret skips sign-offs.
- **Marcus closers are inconsistent** — nails it when he does it ("logging off", "Before I disappear") but sometimes just summarizes.
- **QA score stable at 76** — bookend compliance doesn't affect quality scoring.
- **Pattern:** Characters with "efficient" or "anxious" voices resist the directive more. Margaret and Devon skip greetings. Margaret skips sign-offs. The voice guides actively fight the bookend structure.

### Emerging conclusion
v2 directives land ~60-70% depending on concept/run variance. To push higher, we likely need:
1. Per-character bookend hints that work WITH the voice guide instead of against it
2. Fewer prescriptive examples in the closer (reduce "Good session" parrot risk)
3. Possibly a harder constraint: "Your message MUST start with one of: Morning, Hey, Hi, Alright, Ok"

---

---

## Test 5: GPT-5.1, v3 few-shot anchoring (3 runs)
**Date:** 2026-03-03
**Model:** openai/gpt-5.1
**Concept:** Brown Butter Pecan Tassies (same as Test 4 for comparison)
**Files:**
- `sim-20260304-033129-...-run1-full-week.json` (qa=0 — template fallback, Run 1 unreliable)
- `sim-20260304-033129-...-run2-full-week.json` (qa=78)
- `sim-20260304-033129-...-run3-full-week.json` (qa=80)

### v3 Changes (from Gemini Deep Research findings)

**1. Few-shot character anchoring** — Added `_CHARACTER_EXAMPLE_MESSAGES` dict with 3 example
messages per character in `build_system_prompt()`. LAST example is always a greeting/arrival
moment, exploiting "Last Example Weight" — models weight the final example highest.

**2. Motivation-based opener** — Replaced structural directive:
- v2: "Your FIRST sentence must be a greeting or arrival moment"
- v3: "You just arrived. Your first words should reflect that arrival in YOUR voice."

**3. Character-filtered closer** — Removed prescriptive sign-off examples:
- v2: "END with a sign-off — 'heading out', 'see you tomorrow', 'night'..."
- v3: "You're leaving. End with a departure in YOUR voice."

### Results — Aggregate (21 day-runs across 3 full weeks)

**Openers: 20/21 = 95%**
Only miss: Run 1 Tuesday Margaret ("New plan for today:" — no arrival moment)

**Closers: 18/21 = 86%**
Misses: Run 1 Mon/Tue (Marcus x2 — pure summaries), Run 2 Mon (Steph — conditional plan)

### Cross-version comparison

| Metric | v1 (soft) | v2 (explicit) | v2 (diff concept) | v3 (few-shot) |
|--------|-----------|---------------|-------------------|---------------|
| Openers | ~60% | ~70% | ~60% | **95%** |
| Closers | ~15% | ~70% | ~57% | **86%** |
| QA Score | 75 | 76 | 76 | 78-80 |
| Sample size | n=14 | n=14 | n=14 | **n=42** |

### Analysis

- **Openers jumped from ~65% to 95%.** Few-shot examples are doing the heavy lifting. The model
  sees Margaret's example "Morning. Whose idea was the glaze - because it's wrong." and understands
  that THIS is what a Margaret greeting looks like. No more conflict with her voice guide.

- **Closers jumped from ~64% to 86%.** Removing prescriptive examples ("logging off", "heading out")
  and replacing with "in YOUR voice" eliminated the Marcus "Good session" parrot problem. Characters
  now sign off naturally: Margaret says "my brain is closed," Steph says "go lie quietly in a dark
  room," Julian says "shutting this laptop."

- **QA scores improved slightly** (76 → 78-80). The few-shot examples may be anchoring voice
  quality generally, not just bookends.

- **Remaining failure mode:** Marcus on Monday/Tuesday closers — he summarizes decisions eloquently
  but forgets to actually leave. His voice guide says "always one sentence too many" and that last
  sentence is another thought, not a goodbye. Could address with a Marcus-specific closer example.

- **Run 1 had qa=0 / real_inference=False** — possible template mode fallback. Runs 2-3 are solid.

### Key Research Insights Applied
- "Last Example Weight" (Gemini research Section 2) — confirmed effective
- "Internal Motivation" over structural commands (Section 3) — confirmed effective
- "Character-filtered sign-offs" (Section 6) — confirmed effective
- Few-shot > zero-shot for character adherence — confirmed at scale

---

## Planned Tests
- [x] Test 1: GPT-5.1 v1 (soft) — 60%/15%
- [x] Test 2: GPT-5.1 v2 (explicit) — 70%/70%
- [x] Test 3: Sonnet 4.6 v2 — 70%/70%, QA 66
- [x] Test 4: GPT-5.1 v2 different concept — 60%/57%
- [x] Test 5: GPT-5.1 v3 few-shot x3 runs — **95%/86%**, QA 78-80
- [ ] Test 6: Gemini comparison (pending API key)
- [ ] Test 7: Marcus-specific closer example (if we want to push past 86%)
