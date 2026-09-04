"""The weekly crawl wrapper, driven with a stub crawler.

Two real bugs shipped into this script before it had any test: `set -e` killed
it silently when `find` hit a missing directory, and a same-day re-run diffed
the crawl against itself and reported serene zero change. Both are covered
here.

The stub stands in for Screaming Frog via SEO_SPIDER_BIN, so these tests never
touch the network or the real app.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "seo_weekly_crawl.sh"

COLUMNS = (
    '"Address","Content Type","Status Code","Indexability","Indexability Status",'
    '"Title 1","Meta Description 1","Canonical Link Element 1","Word Count","Unique Inlinks"'
)


def csv_for(urls: list[str]) -> str:
    rows = [
        f'"{url}","text/html; charset=utf-8","200","Indexable","","A Title",'
        f'"A description.","{url}","800","5"'
        for url in urls
    ]
    return "\n".join([COLUMNS, *rows]) + "\n"


@pytest.fixture
def stub_spider(tmp_path: Path):
    """A fake crawler that writes whatever CSV the test asked for."""

    def build(urls: list[str], exit_code: int = 0) -> Path:
        payload = tmp_path / "payload.csv"
        payload.write_text(csv_for(urls), encoding="utf-8-sig")
        stub = tmp_path / "stub_spider.sh"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "# Args arrive as --crawl <url> --headless --output-folder <dir> ...\n"
            "out=\"\"\n"
            'while [[ $# -gt 0 ]]; do\n'
            '  if [[ "$1" == "--output-folder" ]]; then out="$2"; shift; fi\n'
            "  shift\n"
            "done\n"
            f'echo "stub crawl finished"\n'
            f'if [[ {exit_code} -eq 0 ]]; then cp "{payload}" "$out/internal_all.csv"; fi\n'
            f"exit {exit_code}\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        return stub

    return build


@pytest.fixture
def audit_dir(tmp_path: Path) -> Path:
    """A seo-audits/ layout with a baseline, isolated from the real repo."""
    baseline_dir = tmp_path / "audits" / "baseline-2026-08"
    baseline_dir.mkdir(parents=True)
    (baseline_dir / "screaming-frog-internal_all.csv").write_text(
        csv_for(["https://x.test/", "https://x.test/recipes/a"]), encoding="utf-8-sig"
    )
    return tmp_path / "audits"


def run_script(audit_dir: Path, spider: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "SEO_AUDIT_DIR": str(audit_dir),
        "SEO_SPIDER_BIN": str(spider),
        "SEO_SITE": "https://x.test",
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")
class TestWeeklyCrawl:
    def test_the_first_ever_run_falls_back_to_the_baseline(
        self, audit_dir: Path, stub_spider
    ) -> None:
        """seo-audits/weekly does not exist yet; `set -e` used to kill this."""
        spider = stub_spider(["https://x.test/", "https://x.test/recipes/a"])

        result = run_script(audit_dir, spider)

        assert result.returncode == 0, result.stderr
        assert "No previous weekly crawl found" in result.stdout
        # The temp baseline lives outside the repo, so display_path renders the
        # bare filename rather than a repo-relative one.
        assert "before: screaming-frog-internal_all.csv" in result.stdout

    def test_a_same_day_rerun_does_not_compare_the_crawl_to_itself(
        self, audit_dir: Path, stub_spider
    ) -> None:
        """The selector must skip the directory this run is about to overwrite."""
        spider = stub_spider(["https://x.test/", "https://x.test/recipes/a"])

        run_script(audit_dir, spider)
        second = run_script(audit_dir, spider)

        assert second.returncode == 0, second.stderr
        # Comparing against itself would print the same path on both lines.
        before_line = next(l for l in second.stdout.splitlines() if l.startswith("before:"))
        after_line = next(l for l in second.stdout.splitlines() if l.startswith("after:"))
        assert before_line.split(":", 1)[1].strip() != after_line.split(":", 1)[1].strip()

    def test_a_vanished_page_fails_the_run(self, audit_dir: Path, stub_spider) -> None:
        """The end-to-end version of the regression the diff script catches."""
        spider = stub_spider(["https://x.test/"])  # /recipes/a is in the baseline, not here

        result = run_script(audit_dir, spider)

        assert result.returncode != 0
        assert "absent from the crawl" in result.stdout

    def test_a_crawler_that_exits_nonzero_is_diagnosed_not_just_aborted(
        self, audit_dir: Path, stub_spider
    ) -> None:
        spider = stub_spider([], exit_code=3)

        result = run_script(audit_dir, spider)

        assert result.returncode != 0
        assert "spider exited non-zero" in result.stderr

    def test_a_missing_crawler_says_where_it_looked(self, audit_dir: Path, tmp_path: Path) -> None:
        result = run_script(audit_dir, tmp_path / "nope")

        assert result.returncode != 0
        assert "Screaming Frog not found" in result.stderr
