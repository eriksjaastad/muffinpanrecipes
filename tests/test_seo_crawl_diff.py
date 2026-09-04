"""The weekly SEO crawl diff: what counts as a regression, and what does not.

The weekly run (SEO_RUNBOOK.md) exists to catch the Sunday publish breaking an
existing page. It has to stay quiet about the one page the publish is supposed
to add every week, or nobody will read it by week four.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from scripts.seo_crawl_diff import (
    find_regressions,
    html_pages,
    load_export,
    on_page_gaps,
    report,
    thin_inlinks,
)

COLUMNS = [
    "Address",
    "Content Type",
    "Status Code",
    "Indexability",
    "Indexability Status",
    "Title 1",
    "Meta Description 1",
    "Canonical Link Element 1",
    "Word Count",
    "Unique Inlinks",
]


def page(address: str, **overrides: str) -> dict[str, str]:
    row = {
        "Address": address,
        "Content Type": "text/html; charset=utf-8",
        "Status Code": "200",
        "Indexability": "Indexable",
        "Indexability Status": "",
        "Title 1": "A Muffin Pan Recipe",
        "Meta Description 1": "A description that exists.",
        "Canonical Link Element 1": address,
        "Word Count": "800",
        "Unique Inlinks": "5",
    }
    row.update(overrides)
    return row


def write_export(path: Path, rows: list[dict[str, str]]) -> Path:
    # Screaming Frog writes a UTF-8 BOM; reproduce it so the reader is tested
    # against the real file shape.
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def keyed(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["Address"]: row for row in rows}


class TestLoadExport:
    def test_strips_the_utf8_bom_from_the_address_column(self, tmp_path: Path) -> None:
        path = write_export(tmp_path / "internal_all.csv", [page("https://x.test/")])

        loaded = load_export(path)

        assert list(loaded) == ["https://x.test/"]

    def test_rejects_a_csv_that_is_not_a_frog_export(self, tmp_path: Path) -> None:
        path = tmp_path / "wrong.csv"
        path.write_text("Keyword,Volume\nmuffin pan,1000\n", encoding="utf-8")

        with pytest.raises(ValueError, match="Address"):
            load_export(path)


class TestHtmlPages:
    def test_drops_images_and_json(self) -> None:
        rows = keyed(
            [
                page("https://x.test/recipes/a"),
                page("https://x.test/hero.png", **{"Content Type": "image/png"}),
                page("https://x.test/recipes.json", **{"Content Type": "application/json"}),
            ]
        )

        assert list(html_pages(rows)) == ["https://x.test/recipes/a"]


class TestFindRegressions:
    def test_a_new_page_is_not_a_regression(self) -> None:
        """Sunday adds a recipe every week. That is the system working."""
        before = keyed([page("https://x.test/recipes/a")])
        after = keyed([page("https://x.test/recipes/a"), page("https://x.test/recipes/b")])

        assert find_regressions(before, after) == []

    def test_a_page_that_starts_404ing_is_reported(self) -> None:
        before = keyed([page("https://x.test/recipes/a")])
        after = keyed([page("https://x.test/recipes/a", **{"Status Code": "404"})])

        (regression,) = find_regressions(before, after)

        assert "status 200 -> 404" in regression

    def test_a_page_that_stops_being_indexable_is_reported_with_the_reason(self) -> None:
        before = keyed([page("https://x.test/recipes/a")])
        after = keyed(
            [
                page(
                    "https://x.test/recipes/a",
                    **{
                        "Indexability": "Non-Indexable",
                        "Indexability Status": "Noindex",
                    },
                )
            ]
        )

        (regression,) = find_regressions(before, after)

        assert "Indexable -> Non-Indexable" in regression
        assert "Noindex" in regression

    def test_a_moved_canonical_is_reported(self) -> None:
        """The 2026-06 audit turned on a canonical bug twice; watch the field."""
        before = keyed([page("https://x.test/recipes/a")])
        after = keyed(
            [page("https://x.test/recipes/a", **{"Canonical Link Element 1": "https://x.test/"})]
        )

        (regression,) = find_regressions(before, after)

        assert "canonical https://x.test/recipes/a -> https://x.test/" in regression

    def test_a_canonical_that_only_gained_whitespace_is_not_a_regression(self) -> None:
        before = keyed([page("https://x.test/recipes/a")])
        after = keyed(
            [
                page(
                    "https://x.test/recipes/a",
                    **{"Canonical Link Element 1": " https://x.test/recipes/a "},
                )
            ]
        )

        assert find_regressions(before, after) == []

    def test_an_unchanged_crawl_reports_nothing(self) -> None:
        rows = keyed([page("https://x.test/recipes/a"), page("https://x.test/recipes/b")])

        assert find_regressions(rows, rows) == []


class TestThinInlinks:
    def test_flags_a_page_reachable_from_only_one_other_page(self) -> None:
        rows = keyed(
            [
                page("https://x.test/recipes", **{"Unique Inlinks": "1"}),
                page("https://x.test/recipes/a", **{"Unique Inlinks": "4"}),
            ]
        )

        assert thin_inlinks(rows) == [("https://x.test/recipes", 1)]

    def test_ignores_non_indexable_pages(self) -> None:
        rows = keyed(
            [
                page(
                    "https://x.test/draft",
                    **{"Unique Inlinks": "0", "Indexability": "Non-Indexable"},
                )
            ]
        )

        assert thin_inlinks(rows) == []

    def test_a_blank_inlink_count_reads_as_zero_not_a_crash(self) -> None:
        rows = keyed([page("https://x.test/orphan", **{"Unique Inlinks": ""})])

        assert thin_inlinks(rows) == [("https://x.test/orphan", 0)]


class TestOnPageGaps:
    def test_flags_the_thin_seed_recipes(self) -> None:
        """The ten seed recipes crawl at 92-101 words (card #6389)."""
        rows = keyed([page("https://x.test/recipes/seed", **{"Word Count": "94"})])

        assert on_page_gaps(rows) == [("https://x.test/recipes/seed", ["thin: 94 words"])]

    def test_flags_a_missing_title_and_description_together(self) -> None:
        rows = keyed(
            [page("https://x.test/bare", **{"Title 1": "", "Meta Description 1": "   "})]
        )

        (_, problems) = on_page_gaps(rows)[0]

        assert problems == ["no title", "no meta description"]

    def test_a_complete_page_is_not_flagged(self) -> None:
        assert on_page_gaps(keyed([page("https://x.test/recipes/a")])) == []


class TestReportExitCode:
    def test_exits_nonzero_when_a_page_regressed(self, tmp_path: Path) -> None:
        before = write_export(tmp_path / "before.csv", [page("https://x.test/a")])
        after = write_export(
            tmp_path / "after.csv", [page("https://x.test/a", **{"Status Code": "500"})]
        )

        assert report(before, after) == 1

    def test_exits_zero_when_only_new_pages_appeared(self, tmp_path: Path) -> None:
        before = write_export(tmp_path / "before.csv", [page("https://x.test/a")])
        after = write_export(
            tmp_path / "after.csv", [page("https://x.test/a"), page("https://x.test/b")]
        )

        assert report(before, after) == 0
