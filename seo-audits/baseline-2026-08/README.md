# SEO baseline — August 2026

**These runs were taken BEFORE the current round of SEO changes.** They are a baseline, not a
result. Nothing here should be read as an outcome of any change.

| File | Tool | Date |
|---|---|---|
| `screaming-frog-internal_all.csv` | Screaming Frog SEO Spider, full internal crawl | 22 Aug 2026 |
| [`docs/assets/seo/ahrefs-site-audit-2026-08.pdf`](../../docs/assets/seo/ahrefs-site-audit-2026-08.pdf) | Ahrefs Site Audit overview | 24 Aug 2026 |

The Ahrefs PDF lives under `docs/assets/` rather than beside the CSV. The reason is
external to this repo: the user-scope `git-artifact-guard` pre-commit hook in
`~/.claude/hooks/` only accepts tracked binaries under approved asset paths, so nothing
in this repository enforces it — don't go looking. It is the right shape anyway, since a
rendered PDF is not something a diff can use. The machine-readable CSV stays here.

## The point of them

Re-run both against the same site in roughly four months and diff against these files to see
what the changes actually did. It may show movement sooner; four months is the outside estimate.
Keep the crawl configuration identical between runs or the comparison is meaningless.

**Note for whoever re-runs this:** the two tools disagreed at baseline — Screaming Frog found 109
URLs on 22 Aug, Ahrefs Site Audit found 206 on 24 Aug. Two days apart, so it may be growth, or
it may be crawl scope/configuration. Worth resolving before treating either as the reference
count.

Moved here 2026-08-30 from `job-search/research/muffinpanrecipes/`. The SEO work belongs to this
project; job-search only records that the tools were used.

## How to re-run this

`SEO_RUNBOOK.md` at the repo root now owns the procedure. Do not reconstruct the crawl by
hand:

```bash
./scripts/seo_weekly_crawl.sh --against baseline
```

That script pins the crawl configuration, so successive runs stay comparable. If you ever
change a flag in it, say so here — runs from before and after the change cannot be
compared.

**First re-run: 2026-09-04.** 109 -> 118 URLs, no regressions, and pages with fewer than
two internal inlinks went 5 -> 1. The 118 makes plain growth the weaker explanation for
the Screaming Frog / Ahrefs disagreement noted above; resolve crawl scope at the December
quarterly.
