# Guarded W35 memory-writing pilot

This is the guarded execution layer for the frozen, offline W35 artifact made
by [`scripts/memory_write_experiment.py`](../../scripts/memory_write_experiment.py), following the prompt
design in [`MEMORY_WRITE_EXPERIMENT.md`](MEMORY_WRITE_EXPERIMENT.md). Its normal mode only verifies the
artifact and prints a 12-request plan. It does not import or instantiate the
Anthropic client. Paid execution requires `--execute`, the exact artifact
SHA-256, a new ledger path, and a new checkpoint path. No calls have been run
for this pilot.

The run accepts only the six-character `2026-W35` artifact, the exact planned
Haiku 4.5 model, and the 220-token hard response cap. It uses one unique $5
ledger through `AnthropicBudgetGuard` in the `ab` phase, which counts provider
input tokens and reserves the full response cap before each generation. SDK
retries are disabled. Every response is checkpointed with its complete raw
text, usage, stop reason, request ID when available, and a cost computed from
the validated Haiku rates. A failed or ambiguous paid call stays marked
in-flight and is never retried. Resume requires both files, their artifact and
ledger binding to match, and a checkpoint with no in-flight call; only valid
saved responses are skipped. Writes to application data and live Blob are not
part of this runner.

For successfully parsed responses, the runner extracts memory prose separately
from citation/evidence-map text. It records prose length using Anthropic
`count_tokens` on the extracted text minus the same empty-user-message framing
baseline. This is a normalized descriptive text-length measure, not billed
output usage. `usage.output_tokens` is retained as the authoritative response
usage for billing. A parse failure is preserved as `unscored`; the runner does
not estimate a prose length from malformed structure. Parse failure does not
erase or retry a completed response.

The result/checkpoint JSON fields are:

- Run identity: `schema_version`, `experiment`, `episode_id`,
  `prompt_artifact_sha256`, ledger basename `ledger_path`, opaque
  `ledger_path_binding_sha256`, `ledger_created_at`, `model`, `max_tokens`, and
  `temperature`. The checkpoint does not store an absolute machine path.
- Progress: ordered `calls` (`call_id`, `character`, `arm`),
  `next_call_index`, `in_flight_call_id`, `status`, and optional `stop_reason`.
- Each `responses` entry: `call_id`, `character`, `arm`, `raw_response_text`,
  `usage` (`input_tokens`, `output_tokens`, `service_tier`, and
  `inference_geo` when supplied), `stop_reason`, `request_id`,
  `actual_cost_microusd`, `prose_parse_status`, and `prose_parse_error`.
  Successfully parsed responses also have `memory_prose` and
  `memory_structure` (format plus parsed fields when applicable), and
  `normalized_text_lengths` (`method`, `response_text_tokens`,
  `memory_prose_tokens`). B-arm prose length excludes field labels and citation
  IDs; citation references are checked against that character's source IDs.
  On complete runs, `guard_summary` records the guard's aggregate accounting.

## Predeclared pilot scoring

The two arms are bundled writing policies: A is a recap plus evidence map; B is
a source-linked perspective card. Structure, perspective, content obligations,
and citation requirements differ together, so a result cannot identify which
single feature caused a difference. This one-week, six-character pilot does not
test memory length, fading, or downstream dialogue effects.

For human review, present A/B in a randomized order independently for each
character while retaining character names so the reviewer can judge
character-specific grounding. Before comparing prose, apply a hard gate for
unsupported factual claims or citations that do not support their claims. Then
score whether interpretation is grounded and character-specific, and whether
the memory could help future dialogue without forcing a callback. A lack of a
supported stance change or open thread is acceptable. Compare pairwise
preference only when actual memory-prose lengths are matched; unmatched lengths
and parse failures are unscored. Advance one bundle to a separate length test
only if there are no source-grounding hard defects and at least 5 of 6
scorable, matched-length character pairs prefer it for perspective/usefulness.
With fewer than 5 scorable pairs, any grounding defect, or no bundle meeting
that threshold, label the result inconclusive. Even a 5-of-6 preference is
exploratory because all six pairs share the same W35 episode.

## Offline validation and execution interface

Validate without a paid request:

```sh
uv run python scripts/memory_paid_experiment.py \
  .scratch/w35-memory-prompts.json \
  --artifact-sha256 <sha256-from-frozen-artifact>
```

An authorized future execution would additionally need fresh unique paths and
the explicit flag:

```sh
uv run python scripts/memory_paid_experiment.py \
  .scratch/w35-memory-prompts.json \
  --artifact-sha256 <sha256-from-frozen-artifact> \
  --execute --ledger .scratch/w35-memory-ledger.json \
  --output .scratch/w35-memory-results.json
```

That command is documentation only and has not been run. Any later spend
decision must use the exact artifact hash and a separately reviewed cost
projection. The budget guard remains the live reservation authority because
provider tokenization is not available in offline dry-run estimates.

For the actual W35 source rendered in this workspace, all twelve system/user
prompt pairs total 92,356 UTF-8 bytes and 18,666 local regex-estimated tokens
(largest pair: 10,820 bytes / 2,205 estimated tokens). Applying the guard's
reservation formula to those local regex estimates, the UTF-8 byte floor is
larger for all twelve requests: 141,508 input-reservation tokens plus 2,640
output-reservation tokens, or $0.154708 at the guard's recorded Haiku rates.
This is a reference projection, not a conservative upper bound or a
provider-token measurement: exact `count_tokens` results could produce a
larger reservation. The conservative maximum exposure is the guard's $5.00
ledger ceiling; the live guard decides whether each request fits. The artifact
used for that estimate had SHA-256
`1919bcd838743d4c53678672fa53848ec66e84040f51c21393db1251a36a5e52`.
