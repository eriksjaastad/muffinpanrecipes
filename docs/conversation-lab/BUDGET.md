# Conversation lab budget guard

The calibration, bench, and A/B commands share one ledger and the approved
combined ceiling of $5.00. Create that ledger once with
`AnthropicBudgetGuard(path, budget_usd=5, create=True)`. Later commands must
resume the same path with `create=False` (the default) and the same budget.
Create refuses to replace a file; resume refuses missing, corrupt, stopped, or
budget-mismatched ledgers. Label work with `guard.phase("calibration")`,
`guard.phase("bench")`, or `guard.phase("ab")`.

The tool-only guard wraps Anthropic's `Messages.create` and `count_tokens`
methods, disables SDK retries, and rejects other providers, models, or request
shapes before generation. It accepts only Haiku 4.5 and Opus 4.6 plain-text
requests. Current rates are assumed to be $1/$5 per million input/output tokens
for Haiku and $5/$25 for Opus. Each request explicitly pins `service_tier` to
`standard_only`; Opus 4.6 also pins `inference_geo` to `global`. Haiku 4.5 does
not support the inference geography parameter. This avoids a workspace default
that could select priority service or US-only inference with different pricing.
Before each generation, the guard counts the exact system and user text, then
reserves the larger of UTF-8 text bytes plus 4,096 framing tokens or 125% of the
provider estimate plus 1,024 tokens, along with the entire requested output
limit. Successful validated standard-tier usage settles the reservation at
those rates. Ambiguous failures and unpriced usage retain the reservation and
stop the ledger.

This is an operationally conservative bound for the current request shape and
pricing assumptions, not a mathematical guarantee of provider billing. The
ledger stores phases, call counts, and integer microdollar totals; it never
stores prompts or credentials. A downstream caller that catches a request
error must call `guard.raise_if_stopped()` before continuing.

Pricing and modifier behavior are checked against Anthropic's [pricing docs](https://platform.claude.com/docs/en/about-claude/pricing), [service-tier docs](https://platform.claude.com/docs/en/api/service-tiers), and [data-residency docs](https://platform.claude.com/docs/en/manage-claude/data-residency).

Tests use fake SDK responses only. This guard does not authorize or initiate
paid requests; the experiment remains gated on parent review and the approved
go/no-go.
