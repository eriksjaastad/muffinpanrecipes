# E1 situated-response measurement result

Date: 2026-11-01
Card: #108610799669506048
Frozen scorer commit: `4d0dbc8`

## Verdict

**H1 failed its preregistered holdout gate.** The frozen measurement passed the
room-continuity replication (pair 8), but missed the direct-response replication
(pair 9) and selected an arm instead of abstaining on the stock-language hard
negative (pair 3). Per the preregistration, no rule was changed after unsealing.

The holdout result was 1/3 against Erik. Across the deliberately
disagreement-enriched nine-pair queue, the measurement matched Erik on 5/9
(55.6%), compared with Saga on 4/9 (44.4%) and the stored judge score direction
on 5/9 (55.6%). These are calibration results, not population accuracy estimates.

## Frozen evidence and execution

- Archived source `erik-review-completed.json`: SHA-256
  `5519bfcbcea80058f4467c17dc89dc4cefa7d55125429110c651d080a044c875`
- Archived source `human-result.md`: SHA-256
  `6b94a0a57e8fcec765ef1cbcb9cc1c8bc57c946bd65a828416dd68ac8d42e709`
- Development pairs 1, 2, 4, 5, 6, and 7 were scored before the scorer was
  frozen. Holdout pairs 3, 8, and 9 were unsealed once after commit `4d0dbc8`.
- The analysis was deterministic and offline. No provider, production prompt,
  production model, episode, or deployment was touched.
- The replay CLI now requires both archived source paths, verifies both hashes,
  and records their basenames and hashes in each generated receipt. The source
  archives are intentionally not committed because they contain the full E0
  transcripts; the committed receipts and tests contain no hidden dependency
  on an absolute workstation path.

## Pair comparison

`C/V resets` and `C/V uptake` show the control and variant component values.
The stored judge direction was control for every queue item.

| Pair | Split | Erik | Measurement | Saga | C/V resets | C/V uptake | Result |
|---:|---|---|---|---|---:|---:|---|
| 1 | development | variant | variant | variant | 1/1 | .5000/.7500 | correct |
| 2 | development | variant | variant | variant | 0/0 | .4000/.8000 | correct |
| 4 | development | variant | control | variant | 0/0 | 1.0000/.6667 | false negative |
| 5 | development | control | control | variant | 0/0 | .8571/.7143 | correct negative |
| 6 | development | control | control | control | 0/0 | .8571/.6667 | correct negative |
| 7 | development | variant | control | control | 0/0 | .8333/.7143 | false negative; out-of-scope platform-language preference |
| 3 | holdout | control | variant | variant | 0/0 | .4000/1.0000 | false positive; failed hard-negative abstention |
| 8 | holdout | variant | variant | control | 1/0 | .5000/.6000 | correct; room reset detected |
| 9 | holdout | variant | control | control | 0/0 | 1.0000/.8333 | false negative; direct-response replication missed |

The scorer agreed with the all-control stored judge direction on pairs 4, 5, 6,
7, and 9: 5/9. The earlier 2/9 summary was an arithmetic error.

Each generated pair receipt now also carries the preregistered structured
`evidence_flags`: `room_continuity` (1, 8), `direct_response` (4, 9),
`temporal_plausibility` (5), `operational_plausibility` (6), and
`stock_language` (2, 3, 7; pair 7 additionally has `platform_language`). These
are audit categories only and do not alter the frozen arm comparison.

## Error analysis

- **False positive — pair 3:** lexical repetition made every variant adjacency
  look linked, so the measurement preferred variant even though Erik rejected
  its excessive repeated “ready” language. This is exactly the preregistered
  hard negative: overlap is not evidence of a natural situated response.
- **False negatives — pairs 4 and 9:** Erik preferred the arm with genuine
  speaker response, but raw adjacent-token overlap favored control. Pair 9 is
  the held-out failure that rejects H1; the measure cannot distinguish a
  meaningful reply from a transcript with denser lexical carry-over.
- **Out-of-scope miss — pair 7:** Erik rejected out-of-context Instagram
  language. The situated-response measure correctly has no platform-language
  feature, but its control choice still disagrees with the human label.
- **Operational/temporal categories — pairs 5 and 6:** the measurement happened
  to choose Erik's control arm, but this does not validate operational or
  temporal plausibility measurement. Those dimensions were explicitly outside
  H1 and need their own preregistered evidence if pursued.
- **Room continuity — pair 8:** the explicit reset detector generalized to its
  held-out replication and overrode no contradictory signal; this component is
  the only holdout-supported part of H1.

## Baseline and conclusion

The existing E0 adjacency-link baseline yields the same arm ordering on all
nine pairs. The new reset component exposes useful evidence on pair 8, but does
not improve a pair-level decision in this queue. Do not merge this scorer into
the production judge or tune it against the opened holdout. The durable result
is a negative experiment: explicit room-reset detection is promising, while
lexical adjacent-turn uptake is not a sufficient direct-response measure.
