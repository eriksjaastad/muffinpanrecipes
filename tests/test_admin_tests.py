"""/admin/tests inventories conversation-lab result files straight from disk (#8608).

Every test builds its own results directory under tmp_path; nothing reads the
repo's real results, storage, or the network.
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
    directory = tmp_path / RESULTS_SUBDIR
    directory.mkdir(parents=True)
    return directory


def test_listing_shows_every_result_newest_first_with_summary_fields(tmp_path: Path) -> None:
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

    response = _client(tmp_path).get("/admin/tests")

    assert response.status_code == 200
    body = response.text
    order = [
        "e1-report.md",
        "20261017T051229Z-ab-testbed-author.json",
        "bench-monday-n2-20260925T165714Z.json",
        "e1-undated.json",
    ]
    assert [body.index(f'href="/admin/tests/{name}"') for name in order] == sorted(
        body.index(f'href="/admin/tests/{name}"') for name in order
    )
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

    response = _client(tmp_path).get("/admin/tests/bench-x.json")

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
    detail = client.get("/admin/tests/broken.json")
    assert detail.status_code == 200 and "{&#34;command&#34;: &#34;ab&#34;, " in detail.text


def test_missing_results_directory_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/admin/tests")

    assert response.status_code == 503
    assert "not present in this deployment" in response.text


def test_detail_rejects_names_outside_the_results_directory(tmp_path: Path) -> None:
    results = _results(tmp_path)
    (tmp_path / "secret.json").write_text('{"token": "outside-the-results-dir"}')
    (results / "link.json").symlink_to(tmp_path / "secret.json")
    client = _client(tmp_path)

    for url in (
        "/admin/tests/..%2Fsecret.json",
        "/admin/tests/link.json",
        "/admin/tests/%2E%2E%2Fsecret.json",
        "/admin/tests/secret.txt",
    ):
        response = client.get(url)
        assert response.status_code == 404, url
        assert "outside-the-results-dir" not in response.text, url
    assert "link.json" not in client.get("/admin/tests").text


def test_pages_require_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (_results(tmp_path) / "a.json").write_text('{"marker": "unauthenticated-read"}')
    monkeypatch.setattr(config, "_vercel_env", "production")  # never bypasses auth on Vercel
    client = _client(tmp_path, authed=False)
    client.app.dependency_overrides[get_current_user] = lambda: None  # no session cookie

    for url in ("/admin/tests", "/admin/tests/a.json"):
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 307, url
        assert "unauthenticated-read" not in response.text, url
