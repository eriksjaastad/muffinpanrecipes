# E1 preregistration: situated-response measurement

Registered: 2026-11-01 before E1 implementation  
Card: #108610799669506048  
Production prompts/models: frozen

## Frozen evidence

This experiment uses only the nine Erik-labelled E0 pairs in:

- `erik-review-completed.json` SHA-256 `5519bfcbcea80058f4467c17dc89dc4cefa7d55125429110c651d080a044c875`
- `human-result.md` SHA-256 `6b94a0a57e8fcec765ef1cbcb9cc1c8bc57c946bd65a828416dd68ac8d42e709`

The nine labels contain repeated evidence for room continuity (review items 1 and 8), immediate turn response (4 and 9), operational/temporal plausibility (5 and 6), and stock/model language (2, 3, and 7). The queue is disagreement-enriched and cannot estimate population accuracy.

## One measurement hypothesis

**H1:** A hermetic **situated-response measurement** that combines explicit room-continuity resets with immediate adjacent-turn uptake will rank Erik's preferred arm on the two held-out human-supported situated-conversation pairs more reliably than the existing E0 adjacency-link metric, without falsely claiming that the held-out stock-language pair is a situated-response win.

This is one measurement hypothesis. Operational/temporal plausibility and stock-language evidence will be reported as error categories, not folded into H1 or used to tune its result.

## Measurement contract

The implementation may use only transcript speaker labels and message text. It must be deterministic, offline, and free of model/provider calls.

For each arm it will report two inspectable components:

1. **Room-continuity reset count:** attendance/reset phrases such as “everyone here,” “all here,” or equivalent whole-room roll calls in a scene represented as one shared multi-speaker conversation.
2. **Adjacent-turn uptake rate:** among turns after the opener, the fraction that either answer an immediately preceding question or repeat/paraphrase a concrete content term from the immediately preceding turn, after fixed normalization and stop-word removal.

The arm comparison is lexicographic: fewer room-continuity resets wins first; when tied, higher adjacent-turn uptake wins. An exact component tie is an abstention. Phrase lists, normalization, stop words, and question-answer cues must be committed in code and frozen before the holdout is scored. No coefficient or threshold may be tuned on holdout outcomes.

The existing E0 `adjacency_share_with_link` is the baseline. H1 is not a claim that lexical overlap proves natural dialogue; false links and missed paraphrases must be reported.

## Development and holdout split

The split is frozen before implementation.

Development pairs:

- 1 — `2026-W40:sunday:3` — room continuity
- 4 — `2026-W42:wednesday:3` — direct response
- 2 — `2026-W41:sunday:1` — punctuation/stock-language error audit
- 5 — `2026-W43:friday:3` — temporal plausibility error audit
- 6 — `2026-W43:friday:2` — operational plausibility error audit
- 7 — `2026-W43:monday:1` — out-of-context platform-language error audit

Holdout pairs:

- 8 — `2026-W40:sunday:1` — room-continuity replication
- 9 — `2026-W42:wednesday:2` — direct-response replication
- 3 — `2026-W41:sunday:3` — hard negative: repeated “ready” language, not a situated-response label

The split deliberately holds out one replication of each positive situated-response family and one negative family. It is not random and may not be described as an unbiased accuracy sample.

## Frozen outcomes

Primary success requires all three:

1. the situated-response measurement ranks Erik's preferred arm on holdout pair 8;
2. it ranks Erik's preferred arm on holdout pair 9;
3. it abstains on holdout pair 3 rather than claiming a situated-response explanation for a stock-language preference.

Secondary reporting, not a pass gate:

- development and holdout confusion tables against Erik;
- comparison with Saga's choice and stored judge direction;
- every false positive and false negative with component evidence;
- separate evidence flags for operational/temporal plausibility and stock language, with no claim that H1 measures them;
- the existing adjacency baseline on the same split.

## Stop and mutation rules

- Do not inspect holdout measurement output until implementation and hermetic tests are frozen in a commit.
- Do not change a phrase list, cue, stop word, normalization rule, tie rule, or split after holdout scoring.
- If H1 fails, report the failure; do not patch against the three holdout records.
- No production prompt, judge, model, episode, provider, or deployment change is authorized by this experiment.
- No paid API or OAuth model call is allowed.
