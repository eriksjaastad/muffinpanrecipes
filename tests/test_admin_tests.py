"""/admin/tests inventories the canonical test-result sources straight from disk (#8608).

Every test builds its own project root under tmp_path; nothing reads the repo's
real results, storage, or the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from backend.admin.routes import create_routes
from backend.admin.test_results import RESULTS_SUBDIR
from backend.config import config
from backend.auth.middleware import get_current_user, require_auth

TEMPLATES = Path(__file__).resolve().parents[1] / "backend" / "admin" / "templates"


def _client(project_root: Path, *, authed: bool = True) -> TestClient:
    app = FastAPI()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES))
    app.state.project_root = project_root
    create_routes(app)
    if authed:
        app.dependency_overrides[require_auth] = lambda: {"email": "admin@example.com"}
    return TestClient(app)


def _results(tmp_path: Path) -> Path:
    """A project root with every required source present; returns the results dir."""
    directory = tmp_path / RESULTS_SUBDIR
    directory.mkdir(parents=True)
    (tmp_path / "docs" / "conversation-lab" / "EXPERIMENTS.md").write_text("# Experiments\n")
    (tmp_path / "data" / "simulations").mkdir(parents=True)
    return directory


def test_listing_shows_every_source_newest_first_with_summary_fields(tmp_path: Path) -> None:
    results = _results(tmp_path)
    (results / "bench-monday-n2-20260925T165714Z.json").write_text(
        json.dumps(
            {
                "command": "bench",
                "label": "monday-n2",
                "stage": "monday",
                "generated_at": "2026-09-25T16:57:14+00:00",
                "aggregate": {"pass_rate": 0.5, "pass_count": 1, "judged_runs": 2},
                "cost_summary": {"total_cost": 0.0915},
            }
        )
    )
    (results / "20261017T051229Z-ab-testbed-author.json").write_text(
        json.dumps(
            {
                "command": "ab",
                "variant_name": "author",
                "target_dimension": "turn_taking",
                "target_win_rate": 0.4286,
                "completed_pairs": 14,
                "total_cost_usd": 1.623,
                "meets_decision_rule": False,
            }
        )
    )
    (results / "e1-report.md").write_text(
        "# E1 result\n\nDate: 2026-11-01\n\n## Verdict\n\n**H1 failed** its gate.\n\nMore.\n"
    )
    (results / "e1-undated.json").write_text(json.dumps({"study": "E1", "pairs": []}))
    (results / ".gitkeep").write_text("")
    (results / "20261017T050300Z-ab-aborted.json").write_text(
        json.dumps(
            {
                "command": "ab",
                "aborted": True,
                "target_win_rate": 0.0,
                "completed_pairs": 0,
            }
        )
    )
    (tmp_path / "data" / "simulations" / "sim-20260226-run.json").write_text(
        json.dumps({"label": "simulated week"})
    )

    response = _client(tmp_path).get("/admin/tests")

    assert response.status_code == 200
    body = response.text
    order = [
        "e1-report.md",
        "20261017T051229Z-ab-testbed-author.json",
        "bench-monday-n2-20260925T165714Z.json",
        "e1-undated.json",
    ]
    assert [body.index(f'href="/admin/tests/results/{name}"') for name in order] == sorted(
        body.index(f'href="/admin/tests/results/{name}"') for name in order
    )
    assert 'href="/admin/tests/simulations/sim-20260226-run.json"' in body
    assert 'href="/admin/tests/experiments/EXPERIMENTS.md"' in body
    assert ".gitkeep" not in body
    assert "pass 50% (1/2)" in body and "$0.09" in body and "(generated_at)" in body
    assert "target win 43% of 14 pairs on turn_taking" in body and "$1.62" in body
    assert "fails decision rule" in body and "(filename)" in body
    assert "**H1 failed** its gate." in body and "(Date: line)" in body
    assert "undated" in body
    assert "aborted" in body and "target win 0%" not in body
    assert 'href="/admin/tests"' in body  # nav link


def test_detail_serves_the_source_artifact(tmp_path: Path) -> None:
    results = _results(tmp_path)
    source = {"command": "bench", "runs": [{"transcript": "Margaret: <b>batter</b>"}]}
    (results / "bench-x.json").write_text(json.dumps(source))

    response = _client(tmp_path).get("/admin/tests/results/bench-x.json")

    assert response.status_code == 200
    assert "Margaret: &lt;b&gt;batter&lt;/b&gt;" in response.text


def test_malformed_files_get_an_error_row_and_stay_inspectable(tmp_path: Path) -> None:
    results = _results(tmp_path)
    (results / "broken.json").write_text('{"command": "ab", ')
    (results / "list-top.json").write_text("[1, 2]")
    (results / "non-finite.json").write_text('{"aggregate": {"pass_rate": NaN}}')
    (results / "oversized.json").write_text('{"total_cost_usd": 1' + "0" * 400 + "}")
    (results / "nested-non-finite.json").write_text(
        '{"runs": [{"metrics": {"unused": Infinity}}]}'
    )
    (results / "over-parser-limit.json").write_text('{"unused": 1' + "0" * 5_000 + "}")
    (results / "wrong-types.json").write_text(
        json.dumps({"aggregate": [], "target_win_rate": "n/a", "cost_summary": "x"})
    )
    (results / "good.json").write_text(json.dumps({"label": "fine"}))

    client = _client(tmp_path)
    body = client.get("/admin/tests").text

    assert "6 could not be read or parsed" in body
    assert "invalid JSON" in body and "unexpected JSON top level: list" in body
    assert "non-finite number NaN" in body
    assert "non-finite number Infinity" in body
    assert "numeric value is too large" in body
    assert "wrong-types.json" in body and "fine" in body
    detail = client.get("/admin/tests/results/broken.json")
    assert detail.status_code == 200 and "{&#34;command&#34;: &#34;ab&#34;, " in detail.text


def test_missing_required_source_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/admin/tests")

    assert response.status_code == 503
    assert "Required sources are missing from this deployment" in response.text
    assert "docs/conversation-lab/results: not present" in response.text


def test_absent_optional_tsv_is_shown_as_not_present_and_never_created(tmp_path: Path) -> None:
    _results(tmp_path)

    response = _client(tmp_path).get("/admin/tests")

    assert response.status_code == 200
    assert "<code>prompt-research/results.tsv</code>: not present" in response.text
    assert "nothing was created" in response.text
    assert not (tmp_path / "prompt-research").exists()


def test_tsv_is_summarized_and_a_ragged_row_is_named(tmp_path: Path) -> None:
    _results(tmp_path)
    tsv = tmp_path / "prompt-research" / "results.tsv"
    tsv.parent.mkdir()
    tsv.write_text("variant\tpass_rate\tnotes\nbase\t0.5\tok\nnext\t0.7\tbetter\n")
    client = _client(tmp_path)

    body = client.get("/admin/tests").text
    assert "base" in body and "next" in body
    assert "0.5" in body and "better" in body
    assert client.get("/admin/tests/prompt-research/results.tsv").status_code == 200

    tsv.write_text("variant\tpass_rate\tnotes\nbase\t0.5\n")
    assert "invalid TSV: line 2 has 2 fields; header has 3" in client.get("/admin/tests").text


def test_experiment_ledger_is_one_source_backed_row_per_run(tmp_path: Path) -> None:
    _results(tmp_path)
    ledger = tmp_path / "docs" / "conversation-lab" / "EXPERIMENTS.md"
    ledger.write_text(
        "# Log\n\n## Experiments\n\n"
        "| Date | Experiment ID | Lever (one) | Target dimension(s) | N | "
        "Wins/Ties/Losses on target | Other dimensions lost | Decision | Shipped PR | "
        "Live confirmation week + scores |\n"
        "|---|---|---|---|---|---|---|---|---|---|\n"
        "| 2026-10-01 | run-a | SHORTER_PROMPT | voice | 14 | 9/3/2 | none | "
        "signal: HOLD | TBD | TBD |\n"
        "| 2026-10-02 | run-b | MORE_TURNS | progression | 7 | 7/0/0 | none | "
        "signal: SHIP | #99 | W40 |\n"
        "\n### Calibration notes\n\n"
        "| Degradation | Completed | Verdict |\n"
        "|---|---:|---|\n"
        "| rotated speakers | 3/3 | suspect |\n"
    )
    body = _client(tmp_path).get("/admin/tests").text
    assert "run-a" in body and "run-b" in body
    assert "SHORTER_PROMPT" in body and "MORE_TURNS" in body
    assert "9/3/2" in body and "7/0/0" in body
    assert "signal: HOLD" in body and "signal: SHIP" in body
    assert body.count('href="/admin/tests/experiments/EXPERIMENTS.md"') == 2
    assert "ledger line" not in body


def test_tsv_rows_map_common_run_fields_and_keep_one_detail_source(tmp_path: Path) -> None:
    _results(tmp_path)
    tsv = tmp_path / "prompt-research" / "results.tsv"
    tsv.parent.mkdir()
    tsv.write_text(
        "run_id\tdate\twhat_changed\tpass_rate\tcost_usd\tverdict\n"
        "trial-1\t2026-03-14\tcompression model\t0.96\t0.003\tSHIP\n"
    )
    body = _client(tmp_path).get("/admin/tests").text
    assert "trial-1" in body and "compression model" in body
    assert "2026-03-14" in body and "0.96" in body and "$0.003" in body and "SHIP" in body
    assert 'href="/admin/tests/prompt-research/results.tsv"' in body


def test_detail_serves_only_names_listed_in_their_source(tmp_path: Path) -> None:
    results = _results(tmp_path)
    (tmp_path / "secret.json").write_text('{"token": "outside-the-results-dir"}')
    (results / "link.json").symlink_to(tmp_path / "secret.json")
    (results / "x.tsv").write_text("a\tb\n")
    client = _client(tmp_path)

    for url in (
        "/admin/tests/results/..%2Fsecret.json",
        "/admin/tests/results/link.json",
        "/admin/tests/results/%2E%2E%2Fsecret.json",
        "/admin/tests/results/secret.txt",
        "/admin/tests/results/x.tsv",
        "/admin/tests/results/missing.json",
        "/admin/tests/unlisted/secret.json",
        "/admin/tests/experiments/other.md",
        "/admin/tests/secret.json",
    ):
        response = client.get(url)
        assert response.status_code == 404, url
        assert "outside-the-results-dir" not in response.text, url
    assert "link.json" not in client.get("/admin/tests").text
    assert client.get("/admin/tests/experiments/EXPERIMENTS.md").status_code == 200


def test_case_twins_are_marked_ambiguous_and_neither_is_served(tmp_path: Path) -> None:
    results = _results(tmp_path)
    (results / "Bench.json").write_text('{"label": "upper"}')
    (results / "bench.json").write_text('{"label": "lower"}')
    if len(list(results.iterdir())) != 2:
        pytest.skip("filesystem is case-insensitive; two case-twins cannot exist")
    client = _client(tmp_path)

    body = client.get("/admin/tests").text

    assert body.count("ambiguous: another file differs only by case") == 2
    assert client.get("/admin/tests/results/Bench.json").status_code == 404
    assert client.get("/admin/tests/results/bench.json").status_code == 404


def test_symlinked_source_is_refused_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.json").write_text('{"token": "outside-the-results-dir"}')
    _results(tmp_path)
    simulations = tmp_path / "data" / "simulations"
    simulations.rmdir()
    simulations.symlink_to(outside, target_is_directory=True)

    response = _client(tmp_path).get("/admin/tests")

    assert response.status_code == 503
    assert "data/simulations: is a symlink; refused" in response.text
    assert "outside-the-results-dir" not in response.text


def test_pages_require_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (_results(tmp_path) / "a.json").write_text('{"marker": "unauthenticated-read"}')
    monkeypatch.setattr(config, "_vercel_env", "production")  # never bypasses auth on Vercel
    client = _client(tmp_path, authed=False)
    client.app.dependency_overrides[get_current_user] = lambda: None  # no session cookie

    for url in ("/admin/tests", "/admin/tests/results/a.json"):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 307, url
        assert "unauthenticated-read" not in response.text, url
