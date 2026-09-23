# Speaker attribution baseline

The leave-one-out content-word classifier reached a weighted weekly accuracy
of **26.85%**, against a weighted chance baseline of **17.16%**, over 26
published weeks. The equal-week macro accuracy was **26.85%**, against **17.18%**
macro chance. The run covered 999 dialogue lines; 998 were scored (99.9%
coverage). This indicates some lexical signal for identifying speakers in
these transcripts. It does not establish that the characters have strong,
consistent, or appealing personalities.

## Method

For each message, the evaluator trains a multinomial Naive Bayes classifier on
the other messages in that week. It uses `_content_words` for stopword-filtered
features, recovers token counts from `_words`, applies uniform speaker priors,
and smooths each speaker's word likelihood toward the pooled training unigram
distribution. Each fold builds its vocabulary and probabilities from training
messages only. Words found only in the held-out message are ignored; a message
with no remaining known words is excluded. Scores tied within `1e-12` receive
fractional expected credit.

Names use `_first_name` normalization. A speaker needs at least two messages in
the week's transcript to enter the fixed candidate set, so one message can be
held out while another remains for training. The chance baseline is `1 / N`
for that week's candidate count. A speaker who appears only once is excluded
from candidates, and that line does not count toward coverage. Aggregate
accuracy and chance are available only when at least two lines were scored and
every candidate speaker has at least one scored line; otherwise both are
`null`, so experiment comparisons omit an incomplete metric. Coverage,
exclusions, candidate names, and per-character recall remain diagnostic; a
recall is `null` when no lines for that speaker were scored.

## Corpus and results

The run used `collect_corpus()` with `include_unpublished=False` and no week
filter, against the prepared local corpus at
`.scratch/attribution-corpus/`. It contains canonical local episode files and
refreshed CDN copies for W37, W38, and W39. The collector included 26 weeks and
999 lines. It listed W12 and W39 among excluded unpublished weeks. W10 was
present but contained no dialogue, so the collector did not include it; W22
was absent. W37 contributed 41 lines. The artifact records the exact included
week list, candidate set, recall, and counts for each week.

| Week | Lines | Scored / coverage | Accuracy | Chance | Candidates |
|---|---:|---:|---:|---:|---:|
| W11 | 39 | 39 / 100% | 17.95% | 20.00% | 5 |
| W13 | 40 | 40 / 100% | 17.50% | 20.00% | 5 |
| W14 | 29 | 29 / 100% | 17.24% | 20.00% | 5 |
| W15 | 37 | 37 / 100% | 21.62% | 16.67% | 6 |
| W16 | 40 | 40 / 100% | 17.50% | 16.67% | 6 |
| W17 | 44 | 44 / 100% | 20.45% | 16.67% | 6 |
| W18 | 31 | 31 / 100% | 32.26% | 16.67% | 6 |
| W19 | 41 | 41 / 100% | 26.83% | 16.67% | 6 |
| W20 | 41 | 41 / 100% | 24.39% | 16.67% | 6 |
| W21 | 39 | 39 / 100% | 23.08% | 16.67% | 6 |
| W23 | 37 | 37 / 100% | 21.62% | 16.67% | 6 |
| W24 | 40 | 39 / 97.5% | 35.90% | 20.00% | 5 |
| W25 | 40 | 40 / 100% | 27.50% | 16.67% | 6 |
| W26 | 33 | 33 / 100% | 24.24% | 16.67% | 6 |
| W27 | 38 | 38 / 100% | 44.74% | 16.67% | 6 |
| W28 | 39 | 39 / 100% | 43.59% | 16.67% | 6 |
| W29 | 36 | 36 / 100% | 36.11% | 16.67% | 6 |
| W30 | 36 | 36 / 100% | 19.44% | 16.67% | 6 |
| W31 | 36 | 36 / 100% | 27.78% | 16.67% | 6 |
| W32 | 41 | 41 / 100% | 39.02% | 16.67% | 6 |
| W33 | 38 | 38 / 100% | 21.05% | 16.67% | 6 |
| W34 | 37 | 37 / 100% | 35.14% | 16.67% | 6 |
| W35 | 42 | 42 / 100% | 28.57% | 16.67% | 6 |
| W36 | 43 | 43 / 100% | 23.26% | 16.67% | 6 |
| W37 | 41 | 41 / 100% | 19.51% | 16.67% | 6 |
| W38 | 41 | 41 / 100% | 31.71% | 16.67% | 6 |

Weighted summaries weight each weekly result by its scored lines. Macro
summaries average weeks equally. These summarize the per-week models; they are
not a single leave-one-out model trained across all weeks. Values are rounded
to four decimals in the machine-readable artifact,
[`speaker-attribution-baseline-20260922.json`](results/speaker-attribution-baseline-20260922.json).

## Limits

This is a small, same-week lexical prediction task. It only measures whether
word choice helps distinguish the speakers in these transcripts. A classifier
can exploit recurring role language, and the available sample is only 29–44
lines per week. Different weeks also have different candidate counts. The
score says nothing directly about personality quality, voice consistency
across weeks, or whether a reader would recognize a character without names.
Treat the weekly values as a baseline for controlled experiments, not as a
quality grade.
