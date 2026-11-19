# Jev vs Haiku stop-check replay pilot — 2026-11-19

Card: Project Tracker #116958587964780544

## Milestone

Built and tested a bounded lab-only replay that reconstructs the exact transcript state
Jev scored: messages are truncated through the final logged tick (`tick + 1`), the
objective comes from the repository's day configuration, and Haiku uses the dialogue
model recorded on those messages. This historical result has no `prior_days` provenance
(the lab writes that field whenever seeded earlier-day context is used), so there are no
unembedded seed lines. The harness now refuses any result that does have seeded prior
days. The lab's `_run_arm` fixes `character_models=None`, so the one model recorded on
the scored messages is the same `default_model` passed to the stop check. The replay
retains the incumbent answer and changes no production behavior or model.

An initial 14-call run was invalidated during SHARP because it included post-check
closing messages. Its output was overwritten and none of its agreement observations are
used below. The corrected pilot stopped after three repeated Haiku format errors.

## Corrected result

- Exact-input samples attempted: **3**.
- Official comparable verdicts: **0/3**.
- Haiku strict-parser failures: **3/3**.
- Each response contained JSON wrapped in a Markdown ```json fence. The incumbent
  `_check_haiku` correctly rejects that under its strict-JSON contract.
- Jev threshold verdicts on those states: 2 done, 1 not done.
- Official agreement rate: **not measurable**. Text visible inside a rejected response
  is error evidence, not a valid incumbent verdict.

The three replay inputs and their SHA-256 values are preserved in the JSON receipt.
The original historical result remains an ignored MacBook-local lab artifact; its full
file SHA-256 is recorded, but the repository does not claim the source file itself is
reproducible from Git.

## Human review and answer-presence split

All three exact states contain an explicit hero-shot decision, so this pilot is:

- Answer present in input: **3**
- Answer absent from input: **0**

There are no valid model disagreements to judge because Haiku returned no valid
verdict. As a qualitative check only, W36 run 2 contains explicit reasoned pushback:
Ria argues for the macro/shatter hook, Julian argues for cross-section proof, and Ria
answers that the cross-section belongs second while the side rake should lead. The
rejected Haiku response text contained `pushback: 0`, but it is not counted as a
verdict or agreement observation.

W32 run 1 also shows why future agreement must be judged rather than treated as
accuracy: Jev is just below the decided threshold at 0.67 even though the final state
explicitly settles on a nori-against-rice close-up.

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
- Corrected replay: 3 exact-tick transcripts, 0 comparable, 3 strict-parser errors.

## Blocker and next step

The acceptance metric is blocked by incumbent output-format failure, not by missing
transcripts. Completing the 14-run agreement study requires a reviewed lab-behavior
decision: either make the Haiku route comply with strict JSON or explicitly accept
fenced JSON in the lab parser. No production behavior change is needed because
production uses the regex trigger.
