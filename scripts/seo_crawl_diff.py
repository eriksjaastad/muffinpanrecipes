"""Diff two Screaming Frog ``Internal:All`` CSV exports and report what moved.

The weekly SEO check (see ``SEO_RUNBOOK.md``) crawls the live site headless and
compares the export against the previous run. This script is that comparison.

Both exports must come from the same crawl configuration or the diff is
meaningless. On the free Screaming Frog tier the configuration is locked to
defaults, which makes that automatic — but a licence would unlock it, so the
rule is written down rather than assumed.

Usage::

    uv run python scripts/seo_crawl_diff.py <before.csv> <after.csv>

Exits 1 when a regression is found (a status-code change, an indexable page
that stopped being indexable, or a changed canonical), so the weekly run can
be wired to a check that actually fails.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

HTML_CONTENT_TYPE = "text/html"

# A page reachable from only one other page has effectively no internal link
# equity. The Ahrefs baseline audit flagged this as the site's top structural
# finding, so the weekly diff tracks it as a first-class number.
MIN_UNIQUE_INLINKS = 2

# Below this, a page is thin enough that it competes for nothing. The ten seed
# recipes sit at 92-101 words (card #6389).
MIN_WORD_COUNT = 200

Rows = dict[str, dict[str, str]]


def load_export(path: Path) -> Rows:
    """Read a Screaming Frog CSV export, keyed by URL.

    Screaming Frog writes a UTF-8 BOM; ``utf-8-sig`` strips it so the first
    column header is ``Address`` and not ``﻿Address``.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "Address" not in reader.fieldnames:
            raise ValueError(
                f"{path} does not look like a Screaming Frog Internal:All export "
                "(no 'Address' column)"
            )
        return {row["Address"]: row for row in reader}


def html_pages(rows: Rows) -> Rows:
    """Keep only HTML documents — images and JSON are not SEO surfaces."""
    return {
        url: row
        for url, row in rows.items()
        if HTML_CONTENT_TYPE in (row.get("Content Type") or "")
    }


def as_int(value: str | None) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def find_regressions(before: Rows, after: Rows) -> list[str]:
    """Report only changes that are unambiguously worse or need explaining.

    New pages are not regressions — a weekly recipe publish adds one every
    Sunday by design.
    """
    regressions: list[str] = []
    for url in sorted(set(before) & set(after)):
        was, now = before[url], after[url]

        if was.get("Status Code") != now.get("Status Code"):
            regressions.append(
                f"{url}\n    status {was.get('Status Code')} -> {now.get('Status Code')}"
            )

        if was.get("Indexability") != now.get("Indexability"):
            reason = now.get("Indexability Status") or "no reason given"
            regressions.append(
                f"{url}\n    indexability {was.get('Indexability')} -> "
                f"{now.get('Indexability')} ({reason})"
            )

        was_canonical = (was.get("Canonical Link Element 1") or "").strip()
        now_canonical = (now.get("Canonical Link Element 1") or "").strip()
        if was_canonical != now_canonical:
            regressions.append(
                f"{url}\n    canonical {was_canonical or '(none)'} -> "
                f"{now_canonical or '(none)'}"
            )

    return regressions


def thin_inlinks(rows: Rows) -> list[tuple[str, int]]:
    """Indexable HTML pages reachable from fewer than MIN_UNIQUE_INLINKS pages."""
    return sorted(
        (url, as_int(row.get("Unique Inlinks")))
        for url, row in rows.items()
        if row.get("Indexability") == "Indexable"
        and as_int(row.get("Unique Inlinks")) < MIN_UNIQUE_INLINKS
    )


def on_page_gaps(rows: Rows) -> list[tuple[str, list[str]]]:
    """Indexable HTML pages missing a title, a meta description, or substance."""
    gaps: list[tuple[str, list[str]]] = []
    for url, row in sorted(rows.items()):
        if row.get("Indexability") != "Indexable":
            continue
        problems: list[str] = []
        if not (row.get("Title 1") or "").strip():
            problems.append("no title")
        if not (row.get("Meta Description 1") or "").strip():
            problems.append("no meta description")
        word_count = as_int(row.get("Word Count"))
        if word_count < MIN_WORD_COUNT:
            problems.append(f"thin: {word_count} words")
        if problems:
            gaps.append((url, problems))
    return gaps


def _print_count_shift(before: Rows, after: Rows, field: str) -> None:
    was, now = Counter(r.get(field, "") for r in before.values()), Counter(
        r.get(field, "") for r in after.values()
    )
    for key in sorted(set(was) | set(now)):
        delta = now[key] - was[key]
        suffix = f"  ({delta:+d})" if delta else ""
        print(f"  {key or '(blank)':<28} {was[key]:>4} -> {now[key]:>4}{suffix}")


def report(before_path: Path, after_path: Path) -> int:
    before, after = load_export(before_path), load_export(after_path)
    before_html, after_html = html_pages(before), html_pages(after)

    print(f"# SEO crawl diff\n\nbefore: {before_path}\nafter:  {after_path}")

    print("\n## Crawl size")
    print(f"  all URLs    {len(before):>4} -> {len(after):>4}  ({len(after) - len(before):+d})")
    print(
        f"  HTML pages  {len(before_html):>4} -> {len(after_html):>4}  "
        f"({len(after_html) - len(before_html):+d})"
    )

    print("\n## Status codes (all URLs)")
    _print_count_shift(before, after, "Status Code")

    print("\n## Indexability (HTML pages)")
    _print_count_shift(before_html, after_html, "Indexability")

    added, removed = sorted(set(after) - set(before)), sorted(set(before) - set(after))
    print(f"\n## URLs added ({len(added)})")
    print("\n".join(f"  + {url}" for url in added) or "  none")
    print(f"\n## URLs removed ({len(removed)})")
    print("\n".join(f"  - {url}" for url in removed) or "  none")

    regressions = find_regressions(before_html, after_html)
    print(f"\n## Regressions ({len(regressions)})")
    print("\n".join(f"  {line}" for line in regressions) or "  none")

    print("\n## Thin internal linking (indexable pages under "
          f"{MIN_UNIQUE_INLINKS} unique inlinks)")
    was_thin, now_thin = thin_inlinks(before_html), thin_inlinks(after_html)
    print(f"  {len(was_thin)} -> {len(now_thin)}  ({len(now_thin) - len(was_thin):+d})")
    for url, count in now_thin:
        print(f"      {url}  ({count} inlinks)")

    gaps = on_page_gaps(after_html)
    print(f"\n## On-page gaps on indexable pages ({len(gaps)})")
    for url, problems in gaps:
        print(f"  {url}\n      {', '.join(problems)}")

    return 1 if regressions else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Diff two Screaming Frog Internal:All CSV exports."
    )
    parser.add_argument("before", type=Path, help="earlier Internal:All CSV export")
    parser.add_argument("after", type=Path, help="later Internal:All CSV export")
    args = parser.parse_args(argv)
    return report(args.before, args.after)


if __name__ == "__main__":
    sys.exit(main())
