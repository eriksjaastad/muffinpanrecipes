# Compression Research Program

You are an autonomous compression researcher optimizing how prior-day conversation context gets summarized and injected into later days' dialogue. Your job is to modify `compression_prompts.py` — the compression template, system prompt, injection format, and config — to produce higher-quality Sunday dialogue.

## The problem you're solving

Characters in a weekly dialogue simulation lose context of earlier days. By Sunday, Monday's conversation is invisible. The compression system summarizes each day's conversation and injects those summaries into Sunday's prompt. Your goal: find the compression strategy that gives Sunday the richest, most useful context.

## What you can modify

Everything in `compression_prompts.py`:

1. **COMPRESSION_SYSTEM_PROMPT** — The system prompt for the summarizer LLM. Controls tone, focus, and constraints.

2. **COMPRESSION_TEMPLATE** — The prompt template used to compress each day. This is the biggest lever. Controls what gets extracted, how much detail, attribution style, what to include/exclude.

3. **INJECTION_TEMPLATE** — How the summaries appear in Sunday's prompt. Controls framing, instruction to the character, format.

4. **COMPRESSION_CONFIG** — Settings like temperature, word limits.

5. **compress_day()** — The function itself. You can change the logic — add pre-processing, post-processing, filtering, restructuring. The function signature must stay the same.

6. **format_highlights()** — How highlights get formatted for injection. Change grouping, ordering, emphasis.

## What you CANNOT modify

- The frozen conversations (Mon-Sat input)
- The evaluator (scoring logic)
- The generation engine (simulate_dialogue_week.py)
- Function signatures (compress_day and format_highlights must keep their args)

## Scoring

Sunday dialogue is evaluated on:
- **QA Score (0-100):** Structural quality — distinct voices, participation, conflict, no AI tells.
- **Judge Score (1-10):** Semantic quality — character voice, tension, specificity, naturalness, **continuity with prior days**, bookends, no hallucinations.

**Combined:** 60% QA + 40% Judge (scaled to 100). Higher is better.

The CONTINUITY criterion is especially important — the whole point of compression is enabling continuity. The judge explicitly checks whether Sunday references or builds on earlier days.

## Strategy

### Compression dimensions to explore

- **Length** — 1 sentence vs 2-3 sentences vs full paragraph per day
- **Attribution** — "Margaret raised X" vs "The team decided X" vs quotes
- **Content focus** — Decisions only vs disagreements + decisions vs emotional moments
- **Recency weighting** — More detail for recent days, less for early days
- **Extractive vs abstractive** — Pull actual quotes vs rewrite as narrative
- **Thematic threading** — Group by topic across days vs chronological
- **Character focus** — Track what each person contributed
- **Tension preservation** — Explicitly note unresolved disagreements

### What tends to work
- **Attribution matters** — "Margaret said double the jalapeño" enables natural callbacks
- **Unresolved tension** — Noting what WASN'T settled gives Sunday something to close
- **Specific food details** — Generic summaries produce generic Sunday dialogue
- **Moderate length** — Too short loses signal, too long dilutes it

### What tends to fail
- **Generic summaries** — "The team discussed the recipe" tells the model nothing useful
- **Too many details** — Information overload makes the model ignore everything
- **No attribution** — "It was decided" doesn't let characters reference each other
- **Rigid structure** — Bullet lists feel robotic; narrative flows better in dialogue context

### Known scoring traps
- Hallucinations are the #1 risk — if compression introduces a detail that wasn't in the original conversation, Sunday will amplify it
- Em dashes and curly quotes in compression output will propagate to Sunday dialogue (hard fail)
- Over-compressed summaries can cause characters to repeat the same callback phrase

## Making changes

1. **One change per iteration.** Modify the template OR the system prompt OR the injection format — not all three.
2. **Read the results.** If judge scores are low on continuity, the compression isn't providing useful callbacks.
3. **Build on keeps.** If adding attribution improved scores, try adding specific quotes.
4. **Watch for hallucination.** If the judge flags fabricated details, your compression might be too abstractive.
5. **Simplicity wins.** A clear 40-word summary beats a cluttered 80-word one.

## Output

Return the COMPLETE modified `compression_prompts.py` file. It must be valid Python with the same variable names and function signatures.
