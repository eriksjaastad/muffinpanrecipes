#!/usr/bin/env bash
#
# Weekly SEO crawl: crawl the live site headless, export Internal:All, and diff
# against the most recent previous crawl. See SEO_RUNBOOK.md.
#
# The whole point of this script is that the crawl configuration is identical
# every week. Do not add flags to a one-off invocation — change them here, and
# note in seo-audits/README.md that runs before and after the change are not
# comparable.
#
# Usage:
#   ./scripts/seo_weekly_crawl.sh                      # crawl + diff vs previous
#   ./scripts/seo_weekly_crawl.sh --against baseline   # diff vs the Aug 2026 baseline
#
set -euo pipefail

SITE="${SEO_SITE:-https://muffinpanrecipes.com}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# SEO_SPIDER_BIN and SEO_AUDIT_DIR exist so tests/test_seo_weekly_crawl.py can
# drive this script with a stub crawler writing into a temp directory. Two real
# bugs were found here by hand before there was any way to test it. Leave the
# defaults alone in normal use.
SPIDER="${SEO_SPIDER_BIN:-/Applications/Screaming Frog SEO Spider.app/Contents/MacOS/ScreamingFrogSEOSpiderLauncher}"
AUDIT_DIR="${SEO_AUDIT_DIR:-$REPO_ROOT/seo-audits}"
BASELINE="$AUDIT_DIR/baseline-2026-08/screaming-frog-internal_all.csv"
STAMP="$(date +%Y-%m-%d)"
OUT_DIR="$AUDIT_DIR/weekly/$STAMP"

COMPARE_TO=""
if [[ "${1:-}" == "--against" && "${2:-}" == "baseline" ]]; then
  COMPARE_TO="$BASELINE"
fi

if [[ ! -x "$SPIDER" ]]; then
  echo "Screaming Frog not found at:" >&2
  echo "  $SPIDER" >&2
  echo "Install it, or run the crawl in the UI and drop internal_all.csv into $OUT_DIR." >&2
  exit 1
fi

# Pick the newest existing weekly crawl to diff against, before this run creates
# a new directory that would otherwise select itself.
if [[ -z "$COMPARE_TO" ]]; then
  # Two things matter here.
  # `-not -path "$OUT_DIR/*"`: re-running on a date that already has a crawl
  # would otherwise select that crawl — which --overwrite is about to replace —
  # and diff the run against itself, reporting a serene zero change.
  # `|| true`: on the very first run seo-audits/weekly does not exist, find
  # exits 1, and `set -e` would kill the script at the assignment.
  COMPARE_TO="$(find "$AUDIT_DIR/weekly" -name internal_all.csv -not -path "$OUT_DIR/*" \
    2>/dev/null | sort | tail -1 || true)"
fi
if [[ -z "$COMPARE_TO" ]]; then
  echo "No previous weekly crawl found; diffing against the August 2026 baseline."
  COMPARE_TO="$BASELINE"
fi

mkdir -p "$OUT_DIR"

echo "Crawling $SITE (headless)..."
# Free-tier Screaming Frog caps at 500 URLs and cannot --save-crawl, so the CSV
# export is the durable artifact. That is fine: a CSV diffs in git, a .seospider
# file does not.
# `|| crawl_failed` rather than bare invocation: under `set -e` a crashing
# Spider would abort here before the diagnostic below ever ran, leaving the
# operator to find crawl.log on their own.
crawl_failed() {
  echo "Crawl failed ($1). Last lines of $OUT_DIR/crawl.log:" >&2
  tail -20 "$OUT_DIR/crawl.log" >&2
  exit 1
}

"$SPIDER" \
  --crawl "$SITE" \
  --headless \
  --output-folder "$OUT_DIR" \
  --export-format csv \
  --overwrite \
  --export-tabs "Internal:All" \
  > "$OUT_DIR/crawl.log" 2>&1 || crawl_failed "spider exited non-zero"

if [[ ! -f "$OUT_DIR/internal_all.csv" ]]; then
  crawl_failed "spider exited 0 but wrote no export"
fi

CRAWLED="$(grep -c . "$OUT_DIR/internal_all.csv" || true)"
echo "Exported $OUT_DIR/internal_all.csv ($((CRAWLED - 1)) rows)"

# The free tier silently truncates at 500 URLs rather than failing, which would
# read as "pages disappeared" in the diff. Say so loudly — and write it into
# diff.txt, because that file is what gets committed and read back weeks later,
# when a terminal warning is long gone.
TRUNCATED=""
if (( CRAWLED - 1 >= 500 )); then
  TRUNCATED="TRUNCATED: hit the 500-URL free-tier cap. This crawl is incomplete and
every 'URLs removed' or regression line below may be an artifact of the cap
rather than a real change. A Screaming Frog licence is now required."
  echo "WARNING: $TRUNCATED" >&2
fi

echo
{
  if [[ -n "$TRUNCATED" ]]; then
    echo "!! $TRUNCATED"
    echo
  fi
  "$HOME/.local/bin/uv" run --no-project python "$REPO_ROOT/scripts/seo_crawl_diff.py" \
    "$COMPARE_TO" "$OUT_DIR/internal_all.csv"
} | tee "$OUT_DIR/diff.txt"
