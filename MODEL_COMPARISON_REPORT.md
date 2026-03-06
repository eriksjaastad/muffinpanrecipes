# Model Comparison Report: GPT-5.1 vs Claude Haiku 4.5
**Date:** 2026-03-05
**Concept:** Jalapeno Corn Dog Bites
**Features:** v3 bookend directives + anti-repetition self-awareness + concept-aware QA scoring

## Head-to-Head Summary

| Metric | GPT-5.1 (n=5) | Haiku 4.5 (n=7) | Winner |
|--------|---------------|-----------------|--------|
| **QA Mean** | 77.2 | 77.9 | Tie |
| **QA Stdev** | 9.1 | **4.0** | **Haiku** |
| **QA Range** | 64-89 | 73-86 | Haiku (tighter) |
| **Prohibited hits** | 0.2/run | 0/run | Haiku |
| **Formal name penalty** | 0.8 | 0.6 | Haiku |
| **Rhythm variation** | **40.6** | 36.6 | GPT-5.1 |
| **Voice pattern bonus** | **7.6** | 5.4 | GPT-5.1 |
| **Conflict bonus** | 4.8 | 4.6 | Tie |
| **Distinctiveness spread** | **23.0** | 17.0 | GPT-5.1 |
| **Participation penalty** | 3.6 | **3.1** | Haiku |

## Key Finding: Haiku Is More Consistent, GPT-5.1 Has Higher Ceiling

**Haiku's stdev is 4.0 vs GPT-5.1's 9.1.** This means:
- Haiku reliably produces 73-86 quality dialogue
- GPT-5.1 can hit 89 but can also drop to 64
- For production (daily cron runs), consistency matters more than peak performance
- A bad day (QA 64) on the live site is worse than a consistently good day (QA 76)

## Where GPT-5.1 Wins
- **Voice distinctiveness:** Characters sound more different from each other (spread 23 vs 17)
- **Rhythm variation:** Message lengths vary more naturally (40.6 vs 36.6)
- **Voice patterns:** Better match to defined character voice guides (7.6 vs 5.4)
- GPT-5.1 at its best (QA 89) is noticeably better than Haiku's best (QA 86)

## Where Haiku Wins
- **Consistency:** Much tighter QA distribution, fewer bad runs
- **Zero prohibited phrases:** Never uses banned language across 7 runs
- **Better participation balance:** Characters share the conversation more evenly
- **No hallucinations in 6 new runs** (the "brown butter" issue from run 1 didn't recur)

## Repetition Analysis
Both models still trigger cross-character phrase penalties (~18-19 points average).

**GPT-5.1 top repeats:** "corn dog bites" (12x), "jalapeno corn dog" (11x) - mostly recipe name
**Haiku top repeats:** "the bite shot" (3x), "the torn edge" (3x) - more specific food/photography terms

Haiku's repetitions are more varied and specific; GPT-5.1 hammers the recipe name harder.

## Cost Comparison
| Model | Input $/M tokens | Output $/M tokens | Est. cost/run (42 msgs) |
|-------|------------------|-------------------|------------------------|
| GPT-5.1 | $1.00 | $3.00 | ~$0.15-0.20 |
| Haiku 4.5 | $0.80 | $4.00 | ~$0.15-0.25 |
| GPT-5-mini | $0.30 | $1.20 | ~$0.05-0.08 |

Cost is roughly equivalent between GPT-5.1 and Haiku for this workload.

## Recommendation

**For production daily runs:** Haiku 4.5 is the better choice.
- Consistency > peak performance for automated daily content
- Zero prohibited phrase risk
- Similar cost

**For special episodes or quality sweeps:** GPT-5.1 for higher voice distinctiveness.

**Not recommended:** GPT-5-mini. Tested extensively (Mar 4) — lower quality, characters sound generic.

## Raw Data

### GPT-5.1 Individual Runs
| Run | QA | Phrase Penalty | Participation Penalty | Voice Bonus |
|-----|-----|---------------|----------------------|-------------|
| 1 | 81 | 20 | 2 | 3 |
| 2 | 75 | 20 | 4 | 3 |
| 3 | 64 | 20 | 4 | 6 |
| 4 (fresh) | 89 | 9 | 4 | 14 |
| 5 (fresh) | 77 | 20 | 4 | 12 |

### Haiku 4.5 Individual Runs
| Run | QA | Phrase Penalty | Participation Penalty | Voice Bonus |
|-----|-----|---------------|----------------------|-------------|
| 1 (earlier) | 78 | 20 | 2 | 6 |
| 2 | 77 | 20 | 4 | 6 |
| 3 | 73 | 20 | 4 | 2 |
| 4 | 86 | 12 | 2 | 8 |
| 5 | 78 | 20 | 2 | 6 |
| 6 | 77 | 20 | 4 | 6 |
| 7 | 76 | 18 | 4 | 4 |

## Historical Context (all simulation data)
| Date | Model | Concept | QA | Notes |
|------|-------|---------|-----|-------|
| Feb 26 | gpt-4o-mini | Various (5 concepts) | 79-97 | 14 msgs only, old QA scorer |
| Mar 3 | gpt-5.1 | Mini Shepherd's Pies | 75-76 | Bookend testing v1/v2 |
| Mar 3 | claude-sonnet-4-6 | Mini Shepherd's Pies | 66 | Lower QA, "Good session" parrot |
| Mar 4 | gpt-5.1 | Brown Butter Pecan Tassies | 76-80 | Bookend v2/v3 testing |
| Mar 4 | gpt-5-mini | Various | 74-77 | Accidentally used wrong model |
| Mar 5 | gpt-5.1 | Jalapeno Corn Dog Bites | 64-89 | Anti-repetition + concept scoring |
| Mar 5 | claude-haiku-4-5 | Jalapeno Corn Dog Bites | 73-86 | 7 runs, consistent |
