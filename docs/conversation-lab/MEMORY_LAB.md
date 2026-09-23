# Offline memory experiment scaffold

Card #7545 starts with a no-cost, read-only evidence pass over three completed
weeks. `scripts/memory_lab.py` accepts exactly three episode JSON paths and
prints a JSON manifest (or writes it to `--output`). It has no model client,
does not touch character files or Blob, and does not create memory prose.

```sh
uv run python scripts/memory_lab.py \
  data/episodes/2026-W10.json \
  data/episodes/2026-W11.json \
  data/episodes/2026-W12.json \
  --output .scratch/memory-manifest.json
```

Only `stages[day].dialogue` is included when that stage has
`status: "complete"`. `rejected_dialogues`, rejected or incomplete stages,
and malformed turns are excluded. Each accepted message gets a stable ID from
episode ID, day, turn index, speaker, and message text. Each speaker's
observations contain the accepted dialogue for all scenes in which they
participated, with `self` and `heard` attribution. This gives a future
character-specific memory writer evidence of what colleagues said around
them, without pretending every line was addressed directly to them.

Each character has an empty candidate memory schema with supporting source
IDs, source episode IDs, and a future memory-kind field. The default 80, 160,
and 300 token variants are budget slots, not generated content. The local
word/punctuation token estimate is for relative comparisons only; it is not
the model provider's tokenizer. The library function `render_candidate()`
can render a candidate supplied by a later experiment and flags overflow.

## What to test next

Use the same three-week evidence set to compare memory-writing approaches,
holding the writer prompt and model fixed while varying one choice at a time:

1. What evidence should a character notice: self-only, dialogue addressed to
   them, or all accepted dialogue they witnessed?
2. How should perception differ by character's role and personality while
   each claim remains traceable to source messages?
3. Which length budget (80, 160, 300, then other justified values) carries
   useful continuity without crowding the dialogue prompt?
4. In later weeks, should low-salience memories be omitted from prompt
   selection while impactful memories remain available?

Compare recall accuracy, continuity of opinions and relationships, voice,
and the rendered prompt cost. Treat fading as a selection policy: retain the
underlying source and memory record so changed policies can be evaluated and
mistakes audited. Full raw-history prompting previously performed worse than
curated highlights, and forced callbacks sounded unnatural; memories should
help a character respond when relevant rather than require a callback.

The current manifest is preparation for these experiments, not evidence that
any memory format improves the dialogue. It also only includes characters
who speak in accepted complete scenes; adding the canonical roster and
explicit addressed-to detection should be separate, measured choices.

## Validation

The synthetic tests verify accepted/rejected separation, complete-stage
filtering, character-specific attribution across a shared scene, deterministic
source IDs, empty candidate slots at all three budgets, and overflow
reporting. They require no API credentials or episode data.
