# Jev vs Haiku stop-check benchmark — full preserved population

Card: Project Tracker #116958587964780544

PR: #264

Run date: 2026-11-21

## Question

On the same final Wednesday lab transcript states previously scored by Jev, how
often does Haiku produce the same thresholded `done` decision, and which reading
is better when they disagree?

This is a lab measurement only. It does not change the production stop rule,
model, prompt, or conversation pipeline.

## Population and authorization

The preserved source contains 14 Jev-scored pairs and hashes to
`23752f3e95c943837b6a4780031663afeffc54c1bbddd23d7aa333ef4b5550c0`.
Three exact-input Haiku verdicts had already been recovered and audited. Erik
explicitly authorized 11 additional model calls on 2026-11-21 at 23:00 ET. The
three existing scenario/run identities were removed before the paid run, leaving
exactly 11 unique missing pairs. The replay made 11 attempts, received 11 valid
verdicts, and made no transport retries.

The historical objective was `Choose the hero shot`. A scene counted as done at
`decided >= 0.7` and `pushback >= 0.5`.

## Result

- Comparable samples: **14/14**.
- Done/not-done agreements: **10/14 (71.4%)**.
- Disagreements: **4/14 (28.6%)**.
- Jev: **13 done, 1 not done**.
- Haiku: **9 done, 5 not done**.
- Answer present in the scored transcript: **14**.
- Answer absent: **0**.

The preserved population therefore supports only the answer-present stratum. It
does not establish performance when the answer is absent.

## Disagreement review

Every disagreement was judged against the scored transcript rather than treating
agreement as accuracy.

- **2026-W36 run 2 — Jev preferred.** The transcript resolves the hero ordering
  after reasoned alternatives involving the macro shatter detail and
  cross-section. Haiku missed the pushback.
- **2026-W33 run 1 — Jev preferred.** The transcript explicitly resolves to the
  cheese pull first and the cross-section second after arguing proof versus
  scroll-stop value. Haiku missed the decision.
- **2026-W37 run 1 — Jev preferred.** The transcript resolves to a pulled-back
  cheese-stretch frame that includes the crisp shell after explicit framing
  pushback. Haiku missed the decision.
- **2026-W38 run 2 — Jev preferred.** The transcript explicitly resolves to shot
  two at full frame after disagreement over a tighter crop. Haiku missed the
  decision.

Human review preferred Jev in **4/4** disagreements. Three failures were false
`not done` decisions despite explicit final choices; the remaining failure missed
reasoned pushback.

## Decision

Retain the incumbent production answer. A 71.4% agreement rate is not strong
enough to treat Haiku as an interchangeable stop checker, and the disagreement
review favors Jev in every observed conflict. Production remains unchanged.

## Receipts

- Full result: `2026-11-21-jev-haiku-stop-check-full.json`
- Prior three-sample recovery: `2026-11-19-jev-haiku-stop-check-replay.json`
- Original source: `2026-11-21-jev-haiku-source-14.json` (SHA-256
  `23752f3e95c943837b6a4780031663afeffc54c1bbddd23d7aa333ef4b5550c0`)
- Missing-population source: `2026-11-21-jev-haiku-missing-11-source.json`
  (SHA-256
  `5947e363034be743f7958d2137ebd0f8348f5bac9acb67120aecd4ce9a23944d`)
- Additional-call receipt: `2026-11-21-jev-haiku-missing-11-replay.json`
  (SHA-256
  `06f7b4b6f91697445fc3e7cbf2fec633939b22f967baf08417fcd1f04397f7f2`)
- Full combined result: `2026-11-21-jev-haiku-stop-check-full.json`

All four files are committed beside this report. The focused provenance test
reconstructs the 14-sample population from them in a clean checkout.

Task-level call accounting is 18 prior model responses plus the 11 explicitly
authorized attempts, for 29 responses total. Only 14 exact-input samples belong
to the final comparison; the earlier invalidated and parser-probe responses are
retained solely in cost accounting.
