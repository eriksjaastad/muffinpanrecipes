# Three-week character-memory chain: dry-run plan

This is the next experiment scaffold for card #7545. It fixes three week labels,
concepts, and seeds, and defines paired arms:

| Arm | Memory passed into the next week |
| --- | --- |
| `control_no_persistent_memory` | None |
| `weekly_character_memory` | One short, source-linked memory for each character, written after each week and available only to that character's next week |

The plan artifact is created with:

```sh
doppler run -- python scripts/memory_chain_experiment.py --output memory-chain-plan.json
```

The command only writes JSON. It does not import or invoke the weekly simulator,
call a model, contact a cron route, publish content, access Blob, or write to
`backend/data/characters`. The artifact declares a zero-dollar budget and zero
provider calls. It refuses plan assumptions that omit provenance, isolation, or
the no-side-effects boundary.

The module also has an orchestration helper used only with explicitly marked
fake adapters in unit tests. Every arm and week receives a fresh temporary
simulator character root, so a write from one control week cannot leak into the
next. The treatment uses a separate harness-owned memory store: the helper
validates each returned memory record, persists and reads it back, then passes
those actual records to the next treatment callback. It clears the simulator
prompt cache before every week and restores the original character root, prompt
cache, and scene directions even if a fake week raises. Stable source IDs
include arm, ISO week, day, turn number, speaker, and exact text.
Each week writes one slot for every character. An absent character receives a
`no_new_evidence` slot with no source IDs and a reference to the prior memory,
so continuity carries forward without inventing an event. Weekly memory records
retain their source IDs. The helper sends both temporary roots to the operating
system trash after the fake chain finishes and returns no paths to those moved
roots. The returned artifact retains the complete generated turns and each
memory record's ID, text or no-evidence status, source IDs, and prior-memory
IDs, so provenance remains inspectable after cleanup. A memory may cite only
the unique source-ID subset that supports it; the full observed-ID set is
retained separately, and a citation outside that set fails validation.

The callback helper's `kind="fake"` marker is a test convention, not a security
boundary. It reports provider calls as unverified because arbitrary Python
callbacks cannot be proven offline. Its output field
`prior_memory_records_provided_to_callback` shows which records reached the
callback; `prompt_injection_verified` remains false because this does not prove
a future simulator placed them in its model prompt. That prompt path must be
checked by a separately reviewed adapter. The CLI plan artifact itself performs
zero calls, and the unit tests supply local fake callbacks.

This deliberately stops before a full-week simulation adapter. The production
simulator can call providers, writes memory at the end of a week, mutates shared
scene directions, and has other pipeline integrations. A test-environment
adapter must be separately reviewed to replace every provider call and route
every write before running three full weeks. No paid generation or full-week
simulation has been performed by this scaffold.

The plan records prompt and memory token counts as measures for a later run. The
memory-length comparison should follow the memory-format experiment, holding
the selected format fixed while varying the rendered memory budget. The
three-week chain then tests whether short, character-specific memories change
later dialogue without inventing unsupported events or perspectives.
