# Dialogue calibration label coverage

Date: 2026-11-04
Card: #8275 / task 103773194834571264

## Reproducible inventory

Run from a clean repository checkout:

```bash
uv run python scripts/conversation_label_inventory.py \
  --primary \
  docs/conversation-lab/results/e0-human-labels-2026-10-27.json \
  docs/conversation-lab/results/20261017T051229Z-ab-testbed-claude-o55-author.json \
  --derived \
  docs/conversation-lab/results/e1-situated-response-development.json \
  docs/conversation-lab/results/e1-situated-response-holdout.json
```

The committed receipt hashes every input. It counts 23 primary human labels:
15 Monday, 2 Wednesday, 2 Friday, and 4 Sunday. The tool verifies that all nine
E1 development and holdout rows match primary E0 `(scenario_id, pick)`
identities, then reports but does not count those derived rows again.

## Acceptance check

- **At least 30 labels:** not met; 23 are preserved, leaving a minimum gap of 7.
- **At least 3 chained days:** not met. The 14 author/control labels are Monday
  only. The nine E0 labels cover four day names but are independently sampled
  scenes from several weeks, not day-chained choices on a frozen prior winner.
- **Held-out dialogue-judge agreement before and after tuning:** not met. E0's
  structured fields record the all-control stored judge at 3/9 against Erik.
  E1's 1/3 holdout is a failed deterministic situated-response measurement
  experiment, not a revised dialogue judge.
- **Deterministic metrics beside judge scores:** met for the nine E0 labels by
  the E1 receipts and report.

The next acceptance-producing input is at least seven additional blind human
choices in a day-chained queue that reaches at least three days. An unattended
agent cannot manufacture those labels. Production prompts and models remain
frozen, and no provider call is justified until that human queue is available.
