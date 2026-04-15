# Anti-Repetition Test Results (2026-03-05)

## What Changed
Added self-awareness anti-repetition to `generate_turn()` in `simulate_dialogue_week.py`.
Before generating a turn, each character now sees what they already said today:
```
You already said today:
  - "Morning. Who decided Jalapeno Corn Dog Bites were a thing..."
  - "Fun is fine. But if the batter steams instead of shatters, I walk."
Do NOT repeat these phrases, ideas, or sentence structures. Say something new.
```

Card: #5031

## Test Setup
- Concept: Jalapeno Corn Dog Bites
- 3 full-week runs on GPT-5.1, 1 full-week run on Claude Haiku 4.5
- All with v3 bookend directives (few-shot anchoring)

## Results

### GPT-5.1 (3 runs with anti-repetition)
| Run | QA | Prohibited | Cross-char penalty | Notes |
|-----|-----|-----------|-------------------|-------|
| 1 | 81 | 0 | 20 | Strong bookends, good voice variety |
| 2 | 75 | 0 | 20 | Solid, Marcus "logging off" closer works |
| 3 | 64 | 1 | 20 | One prohibited phrase, 2 formal name uses |
| **Avg** | **73** | | | |

### Claude Haiku 4.5 (1 run with anti-repetition)
| Run | QA | Prohibited | Cross-char penalty | Notes |
|-----|-----|-----------|-------------------|-------|
| 1 | 78 | 0 | 20 | Higher voice pattern bonus (6 vs 2), better rhythm variation |

### Comparison to pre-anti-repetition (from BOOKEND_TESTING_LOG.md Test 5)
| Metric | v3 without anti-rep (avg) | v3 WITH anti-rep GPT-5.1 (avg) | Haiku |
|--------|--------------------------|--------------------------------|-------|
| QA | 78-80 | 73 | 78 |
| Openers | 95% | ~95% (visual check) | ~85% |
| Closers | 86% | ~90% (visual check) | ~70% |

## Key Findings

### 1. Cross-character phrase penalty is a false positive
The 20-point penalty appears in EVERY run because characters repeat the recipe name
("jalapeno corn dog", "corn dog bites"). This is expected and correct behavior.
**Recommendation: Exclude recipe name tokens from cross-char phrase detection.**

### 2. Haiku is surprisingly competitive
- Higher voice pattern bonus (6 vs 2) - characters sound more distinct
- Better rhythm variation (35 vs 20) - message lengths vary more naturally
- More conflict bonus (5 vs 2) - characters actually disagree
- One hallucination: Margaret said "brown butter" on Sunday (wrong recipe)

### 3. Anti-repetition is working but hard to A/B quantitatively
The self-awareness block prevents the "Love it Marcus" broken record problem,
but QA scores are slightly lower. This may be because:
- More varied language = slightly less voice-pattern matching
- Or just run-to-run variance (n=3 is small)

### 4. Bookends remain strong
Both models maintain high opener/closer rates with v3 directives.
Margaret occasionally skips greetings (voice guide conflict), but closers are solid.

## Issues Found
1. **QA scorer false positive:** Recipe name counted as cross-character repetition (20 pts)
2. **Haiku hallucination:** "Brown butter" mentioned in corn dog bites episode
3. **GPT-5.1 run variance:** QA 64-81 spread across 3 runs is wide

## QA Scorer Fix: Concept-Aware Phrase Detection
Fixed `_cross_char_phrase_penalty()` to exclude recipe concept name from cross-character
repetition detection. Unicode-normalized to handle accented characters (jalapeno/jalapeño).

### Re-scored with fix:
| Run | Old QA | New QA | Phrase Penalty |
|-----|--------|--------|---------------|
| GPT-5.1 Run 1 | 81 | 92 | 9 (was 20) |
| GPT-5.1 Run 2 | 75 | 75 | 20 (genuinely repetitive) |
| GPT-5.1 Run 3 | 64 | 64 | 20 (genuinely repetitive) |
| Haiku Run 1 | 78 | 78 | 20 (accent normalization fixed) |

### Fresh runs with BOTH fixes (anti-rep + concept-aware scoring):
| Run | QA | Notes |
|-----|-----|-------|
| GPT-5.1 Fresh Run 1 | 89 | Concept filtering working |
| GPT-5.1 Fresh Run 2 | 77 | Some genuine cross-char repetition remains |

## Next Steps
- [ ] Run more Haiku comparison runs (n=3) for statistical confidence
- [ ] Consider Haiku for cheaper daily runs if quality holds
- [ ] Investigate remaining cross-character repetition ("just make sure", "state fair vibes")
