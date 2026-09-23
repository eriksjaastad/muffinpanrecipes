# Memory writing format experiment: W35 prompt dry run

This card #7545 continuation renders six A/B memory-writing prompt pairs from
the frozen W35 slots in a `scripts/memory_lab.py` manifest. The runner is
offline: it imports no model SDK, generates no memory prose, and writes no
live character data or Blob.

```sh
uv run python scripts/memory_lab.py \
  <W35-episode.json> <W36-episode.json> <W37-episode.json> \
  --output .scratch/w35-w37-manifest.json
uv run python scripts/memory_write_experiment.py \
  .scratch/w35-w37-manifest.json \
  --episode-id 2026-W35 \
  --output .scratch/w35-memory-prompts.json
```

The W35 source evidence is held constant between arms. The runner also loads
`backend/data/agent_personalities.json` by default (`--profiles` can name a
frozen copy) and gives both arms the same bounded persona context per
character: role, one authored internal-contradiction statement, and up to two
relationship excerpts for people who spoke in scenes the character
observed. Personality excerpts are clipped to 180 characters, relationship
excerpts to 100 characters, and the rendered profile context is capped at
1,200 characters. This context is explicitly
marked as identity framing, not evidence about W35 events. Each excerpt has a
profile pointer; the source JSON SHA-256 is recorded in the result and in each
character context. This uses authored profile prose, not numeric personality
dials that have not been shown to bind behavior.

Both arms use the same planned Haiku 4.5 model
(`claude-haiku-4-5-20251001`), an 80–120 provider-output-token target band,
and a 160-token hard maximum. Those are prompt-design fields; the dry run
cannot measure or guarantee generated output length. When generation is
authorized, record exact provider-reported output tokens for the *complete*
response, including A's evidence map and B's citations. Compare arms within
the same band and report token length separately from memory quality. Never
pad an unsupported memory to hit the band.

Arm A is a **recap-style control**, not a claim to reproduce the production
writer exactly: it asks for a two-sentence third-person recap and a separate
evidence map linking each sentence to only its supporting dialogue IDs. Arm B
uses `Observed`, `Inference`, `Stance`, and `Open thread` fields, with IDs on
each supported claim. Both receive the same per-message ID-tagged evidence
block, persona block, source, planned model, and output band. They share the
same no-invention rule.

The in-repository [`BUDGET.md`](BUDGET.md) requires exact provider
`count_tokens` before every paid generation, a shared `AnthropicBudgetGuard`
ledger with a $5 ceiling, and preservation of partial results when stopped.
Paid execution remains unimplemented: this module exposes no paid flag or
model request path. The bounded gap is a reviewed runner that uses the shared
ledger and patched Anthropic client, counts exact request tokens before each
request, sends at most 12 generation calls (six characters times two arms)
with a 160-token maximum, and preserves validated and partial outputs after
guard failures. Until that runner is implemented and separately authorized,
this artifact cannot spend API budget.

The methodology file `prompt-research/TESTING_METHODOLOGY.md` referenced by
`EXPERIMENTS.md` is gitignored and was not present in the available checkout.
The in-repo experiment log records that full raw-history input lost to
curated highlights and forced callbacks sounded unnatural. This experiment
therefore supplies character-scoped observed scenes without requiring a
callback.

## Validation

Synthetic tests verify W35 source scoping, profile context in both arms,
profile provenance and size bounds, equal model/output band, the different
formats and claim-level source linking, no future-week leakage, six prompt
pairs, and a CLI dry run that rejects model SDK imports. Tests need no episode
corpus or API credentials.
