# Offline memory experiment scaffold

Card #7545 starts with a no-cost, read-only evidence pass over three completed
weeks. `scripts/memory_lab.py` accepts exactly three episode JSON paths and
prints a JSON manifest (or writes it to `--output`). It has no model client,
does not touch character files or Blob, and does not create memory prose.

```sh
uv run python scripts/memory_lab.py \
  <week-1-episode.json> \
  <week-2-episode.json> \
  <week-3-episode.json> \
  --output .scratch/memory-manifest.json
```

Replace the three placeholders with the actual completed episode paths (for
example, copies of W35, W36, and W37 placed under `.scratch/`). Do not assume
that every numbered week exists in the episode corpus. Episode IDs must use
`YYYY-Www` ISO-week form, and the paths must be in chronological order so a
prior slot cannot point to a later week. The output parent directory is
created when needed.

Only `stages[day].dialogue` is included when that stage has
`status: "complete"`. `rejected_dialogues` and rejected or incomplete stages
are excluded. A malformed turn in an accepted stage fails the run with its
episode, day, and turn index so evidence cannot disappear silently; empty
dialogue lists remain valid, while a complete stage missing the `dialogue`
key fails with its episode and day. By default, each episode must have all
seven Monday-through-Sunday stages marked complete. `--allow-partial` opts
into partial input and records missing days plus a top-level partial-input
flag in the manifest. Each accepted message gets a stable ID from episode ID,
day, turn index, speaker, and message text. A character observes only the
days on which they speak; on those days, their observations include the full
accepted group dialogue with `self` and `heard` attribution. This captures
what colleagues said around them without assigning unseen dialogue or
pretending every line was addressed directly to them. The manifest stores a
SHA-256 hash of each raw episode file to identify the exact source bytes used.

Each character has one empty candidate memory slot for each of the three
episodes. Each slot contains that week's observations, an empty schema with
supporting source IDs and a future memory-kind field, and default 80, 160,
and 300 token budget variants. Later slots list earlier slot IDs as possible
prior-memory inputs, preserving the week-by-week creation order. Those prior
slots become available only after they have actually been created; the
manifest contains no generated or presumed memories. Characters seen in any
input week have a slot for every week, including an empty-evidence slot when
they were absent. Supply episode paths in chronological order so prior-slot
links represent earlier weeks.

The local word/punctuation estimate supports rough comparisons only. It does
not enforce a provider token cap. Any paid generation experiment must count
with the exact provider tokenizer before making the request. The library
function `render_candidate()` can render supplied candidate text and flags
overflow against the local estimate.

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
any memory format improves the dialogue. It includes characters who speak in
accepted scenes; adding the canonical roster and explicit addressed-to
detection should be separate, measured choices.

## Validation

The synthetic tests verify accepted/rejected separation, complete-week
validation, day-scoped attendance, weekly slots and prior-slot ordering,
character-specific attribution across shared scenes, deterministic source
IDs, empty candidate slots at all three budgets, and overflow reporting. They
require no API credentials or episode data.
