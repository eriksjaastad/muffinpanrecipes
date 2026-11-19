# Jev vs Haiku stop-check replay — 2026-11-19

Card: Project Tracker #116958587964780544

## Milestone

Replayed Haiku on the exact final transcript scored by Jev in each of the 14 logged
Wednesday lab pairs from `20260927T163954Z-ab-testbed-open-jev.json`. The replay is
lab-only, retains the incumbent answer, and does not change production behavior or
models. Source and transcript SHA-256 values are stored in the JSON receipt.

## Result

- Official comparable verdicts: **0/14**.
- Haiku strict-parser failures: **14/14**. Every response contained syntactically valid
  JSON wrapped in a Markdown ```json fence, which `_check_haiku` correctly rejects
  under its strict-JSON contract.
- Jev threshold verdicts: 13 done, 1 not done.
- Therefore an official agreement rate cannot be reported yet; treating malformed
  Haiku output as a valid incumbent verdict would hide the actual lab behavior.
- Diagnostic only (removing the fences by hand, without changing the receipt or runtime):
  intended Haiku done verdicts agree with Jev on **13/14 (92.9%)**.

The first attempt used the documented `muffinpanrecipes/dev` credential and failed
before a verdict with OpenRouter HTTP 401 `User not found`. The existing
`muffinpanrecipes/prd` credential authenticated without any credential or production
configuration change. One initial production-config probe exposed the fenced response;
the bounded replay then made 14 calls. Total model responses were 15, below the
repository's 20-call task cap.

## Disagreement judgment

The sole diagnostic disagreement is 2026-W36 run 2:

- Jev: decided 0.86, pushback 0.52 → done.
- Haiku's malformed but readable payload: decided 1, pushback 0 → not done.
- Human judgment: **Jev is correct**. The transcript explicitly selects the side rake
  and contains reasoned pushback: Ria argues for a macro/shatter hook, Julian argues for
  the cross-section's lamination proof, and Ria answers that the cross-section belongs
  second while the side rake should lead.
- Answer-presence class: **answer present in input** (explicit “side rake it is” and
  final “the rake hooks them” resolution).

All 14 final transcripts contain an explicit hero-shot decision, so the current sample
splits as answer-present **14**, answer-absent **0**. No conclusion can be drawn for
answer-absent inputs. As a correctness warning, 2026-W32 run 1 is an agreement where
both models intended “not done,” but the final lines explicitly settle on the
nori-against-rice close-up; agreement alone is not accuracy.

## Durable receipts

- `docs/conversation-lab/results/2026-11-19-jev-haiku-stop-check-replay.json`
- `scripts/replay_stop_check_comparison.py`
- `tests/test_replay_stop_check_comparison.py`

Verification:

- `uv run pytest tests/test_replay_stop_check_comparison.py` — 4 passed.
- `uv run ruff check scripts/replay_stop_check_comparison.py tests/test_replay_stop_check_comparison.py` — passed.
- Replay output — 14 transcripts, 0 comparable, 14 Haiku strict-parser errors.

## Next decision

The measurement is blocked by the incumbent's output-format failure, not by missing
transcripts. A follow-up should choose explicitly between (a) preserving strict JSON
and changing the Haiku request/route until it complies, or (b) accepting fenced JSON
in the lab parser. Either option changes lab behavior and should be reviewed before the
official agreement metric is rerun. No production behavior change is needed because
production uses the regex trigger.
