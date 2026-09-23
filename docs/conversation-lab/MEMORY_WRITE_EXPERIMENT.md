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
(`claude-haiku-4-5-20251001`) and the same 80–120 provider-token target band
for **memory prose**, excluding citation and evidence-map overhead. The
complete response has a 220-token hard maximum. Those are prompt instructions;
the dry run cannot measure or guarantee generated prose length. When
generation is authorized, retain provider-reported `usage.output_tokens` as
the billable full-response count, including structure and citations. Measure
prose length separately from citation/evidence metadata with the same
extraction and token-count method in both arms. If using provider
`count_tokens`, pass the extracted prose through the same message wrapper and
subtract the same empty-message framing baseline; label this normalized prose
length, not billable output usage. Compare memory quality only for outputs
with matched actual prose lengths, and report unmatched outputs separately.
Never pad an unsupported memory to hit the band.

The comparison is between two **bundled memory-writing policies**. Arm A is
the recap bundle: a two-sentence third-person recap plus a separate evidence
map linking each sentence only to the dialogue IDs that support it. Arm B is
the source-linked perspective-card bundle: `Observed`, `Inference`, `Stance`,
and `Open thread` fields with citations for supported claims. Both receive
the same per-message ID-tagged evidence block, persona block, model, prose
target band, and full-response cap. They differ together in structure,
perspective, content requirements, and citation obligations. Any observed
gain applies to the bundled policy; this experiment cannot identify which
individual mechanism caused it. It does not test only a format change.

The in-repository [`BUDGET.md`](BUDGET.md) requires exact provider
`count_tokens` before every paid generation, a shared `AnthropicBudgetGuard`
ledger with a $5 ceiling, and preservation of partial results when stopped.
This prompt renderer remains offline-only and exposes no paid flag or model
request path. The separately implemented
[`memory_paid_experiment.py`](../../scripts/memory_paid_experiment.py) runner
requires an explicit `--execute`, validates the frozen artifact SHA, uses the
shared $5 ledger, counts exact request tokens before generation, and caps the
run at 12 calls (six characters times two arms) with a 220-token response cap.
Its result and resume behavior are described in
[`MEMORY_PAID_EXPERIMENT.md`](MEMORY_PAID_EXPERIMENT.md). No paid calls have
been run; the W35 prompt artifact used for offline sizing remains SHA-256
`1919bcd838743d4c53678672fa53848ec66e84040f51c21393db1251a36a5e52`. Paid
execution remains pending explicit authorization, and this artifact alone
cannot spend API budget.

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
