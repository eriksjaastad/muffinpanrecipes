# Jev vs Haiku stop-check replay pilot — 2026-11-19

Card: Project Tracker #116958587964780544

## Milestone

Built and tested a bounded lab-only replay that reconstructs the exact transcript state
Jev scored: messages are truncated through the final logged tick (`tick + 1`), the
objective is the historical value the operator passes with `--objective`, not the
current day configuration, and Haiku uses the dialogue model recorded on those messages.
This historical result has no `prior_days` provenance (the lab writes that field whenever
seeded earlier-day context is used), so there are no
unembedded seed lines. The harness now refuses any result that does have seeded prior
days. The lab's `_run_arm` fixes `character_models=None`, so the one model recorded on
the scored messages is the same `default_model` passed to the stop check. The replay
retains the incumbent answer and changes no production behavior or model.

An initial 14-call run was invalidated during SHARP because it included post-check
closing messages. Its output was overwritten and none of its agreement observations are
used below. The corrected pilot stopped after three repeated Haiku format errors.

## Offline-recovered result

The parser now accepts exactly one complete Markdown fence containing one JSON object while preserving strict schema and probability validation. The prior receipt retained each rejected response verbatim, so all three verdicts were recovered offline with **zero additional model calls or spend**.

- Exact-input samples: **3**.
- Comparable verdicts: **3/3**.
- Done agreements: **2/3 (66.7%)**.
- Disagreements: **1/3 (33.3%)**.
- Jev threshold verdicts: 2 done, 1 not done.
- Haiku threshold verdicts: 1 done, 2 not done.
- Answer present in input: **3**; answer absent: **0**.

The JSON receipt preserves both the original strict-parser error and the recovered typed verdict for every sample. Recovery accepts only the bounded wrapper now covered by regression tests; prose, trailing text, nested fences, malformed JSON, missing fields, and invalid probabilities still fail closed.

## Human review and answer-presence split

All three exact states contain the answer in the transcript. The sole disagreement is W36 run 2: Jev says done (`decided=0.86`, `pushback=0.52`) while Haiku says not done (`decided=1`, `pushback=0`). The transcript contains explicit reasoned pushback after the initial side-rake choice: Ria argues for the macro/shatter hook, Julian argues that the cross-section matters, and Ria resolves the ordering by retaining side rake as the hook and cross-section as the second image. Against that transcript, Jev is correct that both a decision and reasoned pushback are present; Haiku missed the pushback.

The two agreements are W36 run 1 (both done) and W32 run 1 (both not done at the configured 0.7/0.5 threshold). W32 also illustrates why agreement is not accuracy: the final line settles on a nori-against-rice close-up even though both threshold verdicts are not done.

## Call and credential receipt

- The documented `muffinpanrecipes/dev` credential was present but OpenRouter rejected
  it before a model response with HTTP 401 `User not found`.
- The existing `muffinpanrecipes/prd` credential authenticated without any credential
  or production configuration change.
- Before SHARP, one format probe plus the invalidated 14-call run produced 15 model
  responses. The corrected pilot produced 3 more. Total model responses for this task:
  **18**, below the repository's 20-call cap.
- The corrected harness now stops after a configurable repeated-error limit; this pilot
  used three.

## Durable receipts

- `docs/conversation-lab/results/2026-11-19-jev-haiku-stop-check-replay.json`
- `scripts/replay_stop_check_comparison.py`
- `tests/test_replay_stop_check_comparison.py`

Verification:

- `uv run pytest tests/test_replay_stop_check_comparison.py tests/test_lab_stop_knobs.py`
- `uv run ruff check scripts/replay_stop_check_comparison.py tests/test_replay_stop_check_comparison.py`
- Offline recovery: 3 exact-tick transcripts, 3 comparable, 2 agreements, 1 judged disagreement, 0 additional model calls.
- Parser regression suite: accepts one complete `json` or bare fence; rejects prose, trailing content, and nested fences.

## Remaining scope

This milestone makes the preserved pilot measurable and resolves its parser blocker. The result is a small answer-present-only sample, not the full 14-run population requested by the card. No further paid calls were made: the task already consumed 18 of its 20-call cap before this milestone. Expanding the sample requires a separately authorized call budget or additional already-logged responses; production remains on the regex trigger and is unchanged.
