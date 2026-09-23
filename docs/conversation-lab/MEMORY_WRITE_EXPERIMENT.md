# Memory writing format experiment: W35 prompt dry run

This card #7545 continuation renders six pairs of memory-writing prompts from
a frozen W35 slot in a `scripts/memory_lab.py` manifest. It is an offline
design artifact: no model client is imported by the runner, no memory text is
generated, and no live character file or Blob is written.

```sh
uv run python scripts/memory_lab.py \
  <W35-episode.json> <W36-episode.json> <W37-episode.json> \
  --output .scratch/w35-w37-manifest.json
uv run python scripts/memory_write_experiment.py \
  .scratch/w35-w37-manifest.json \
  --episode-id 2026-W35 \
  --output .scratch/w35-memory-prompts.json
```

The output has exactly six character records, each with A and B prompts. Both
arms receive identical W35 attended-scene evidence, retain all supporting
message source IDs in the output record, plan the same Haiku 4.5 model
(`claude-haiku-4-5-20251001`), and set the same 160-token output target. The
model and target are design fields only; the runner never calls the provider.
The W35 episode hash and a canonical hash of the source manifest freeze the
prompt source. The builder rejects evidence rows whose episode ID differs
from W35, so W36/W37 dialogue cannot enter these prompts.

Arm A preserves the existing two-sentence, third-person, past-tense weekly
recap shape. Arm B uses labeled `Observed`, `Inference`, `Stance`, and `Open
thread` fields. It requires interpretations to be marked as inference,
stance changes to be supported by evidence, and unresolved threads to be
real and cited; unsupported inference or open threads must be reported as
none. Both arms share the same no-invention rule and exact source block.

The in-repository [`BUDGET.md`](BUDGET.md) requires exact provider
`count_tokens` before each paid generation, a shared `AnthropicBudgetGuard`
ledger with a $5 ceiling, and preservation of partial results when stopped.
Paid execution is intentionally unimplemented here: this module exposes no
paid flag or model request path. The bounded gap is a reviewed execution
runner that loads the approved shared ledger, calls the guard's patched
Anthropic client with the fixed model and 160 output tokens, stops after at
most 12 generation requests (six characters times two arms), and writes
validated responses plus partial results after guard failures. Until that
runner is implemented and separately authorized, this artifact cannot spend
API budget.

The methodology file `prompt-research/TESTING_METHODOLOGY.md` referenced by
`EXPERIMENTS.md` is gitignored and was not present in the available checkout.
The in-repo conversation experiment log records that full raw-history input
lost to curated highlights and forced callbacks sounded unnatural. This
experiment therefore supplies compact, character-scoped evidence and does
not instruct a future writer to force a callback.

## Validation

Synthetic tests verify W35 source scoping, identical input/model/target across
arms, the difference between formats, no future-week leakage, six prompt
pairs, and a CLI dry run that rejects attempts to import model SDKs. Tests
need no episode corpus or API credentials.
