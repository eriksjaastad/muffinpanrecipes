"""scripts/run_week.py must run exactly the handlers Vercel's crons run (#8617)."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st
from starlette.routing import Match

import backend.admin.cron_routes as cron_routes
from backend.admin.app import app
from backend.storage import storage
from scripts import run_week

ROOT = Path(__file__).resolve().parent.parent
DAY_PATHS = [c["path"] for c in json.loads((ROOT / "vercel.json").read_text())["crons"]
             if c["path"] != "/api/cron/repair"]


def _endpoint(path: str):
    scope = {"type": "http", "path": path, "method": "POST"}
    return next(r.endpoint for r in app.router.routes if r.matches(scope)[0] is Match.FULL)


def _run(monkeypatch, argv, statuses=None, judge_fails=None, load=None):
    """Run main() with each POST answered from ``statuses`` and logging ``judge_fails`` FAILs."""
    posted = []

    class _Resp:
        def __init__(self, code):
            self.status_code, self.text = code, f"body {code}"

    def post(self, path, json, headers):
        posted.append((path, json))
        day = path.rsplit("/", 1)[-1]
        for _ in range((judge_fails or {}).get(day, 0)):
            logging.getLogger("backend.admin.cron_routes").warning(f"Judge FAILED {day} attempt")
        return _Resp((statuses or {}).get(day, 200))

    monkeypatch.setattr(TestClient, "post", post)
    monkeypatch.setattr(type(storage), "load_episode", lambda self, episode_id: load)
    monkeypatch.setattr(type(storage), "namespaces_episodes", lambda self: True)
    monkeypatch.setenv("CRON_SECRET", "x")
    monkeypatch.setenv("RUN_WEEK_SANDBOX", str(run_week.ROOT))
    monkeypatch.setattr(sys, "argv", ["run_week.py", *argv])
    return run_week.main(), posted


def test_a_local_week_posts_every_vercel_cron_day_to_its_production_handler(monkeypatch):
    code, posted = _run(monkeypatch, ["--episode", "2027-W01", "--concept", "Gougères"])
    assert code == 0
    assert [path for path, _ in posted] == DAY_PATHS
    for path, body in posted:
        assert _endpoint(path) is getattr(cron_routes, f"cron_{path.rsplit('/', 1)[-1]}")
        assert body["test"] is True and body["episode_id"] == "2027-W01"
    assert posted[0][1]["concept"] == "Gougères"
    assert all("concept" not in body for _, body in posted[1:])


def test_a_non_200_day_stops_the_week_and_judge_fails_are_counted_per_day(monkeypatch, capsys):
    code, posted = _run(monkeypatch, ["--episode", "2027-W01"], statuses={"wednesday": 202},
                        judge_fails={"monday": 2})
    out = capsys.readouterr().out
    assert code == 1
    assert [path.rsplit("/", 1)[-1] for path, _ in posted] == ["monday", "tuesday", "wednesday"]
    assert "monday     HTTP 200  judge FAILs before result: 2" in out
    assert "tuesday    HTTP 200  judge FAILs before result: 0" in out
    assert "stopped: body 202" in out


def test_handler_warnings_still_reach_the_console(monkeypatch, capsys):
    def post(self, path, json, headers):
        logging.getLogger("backend.admin.cron_routes").warning("QA scoring failed")
        return type("R", (), {"status_code": 202, "text": ""})()

    monkeypatch.setattr(TestClient, "post", post)
    monkeypatch.setattr(type(storage), "namespaces_episodes", lambda self: True)
    monkeypatch.setenv("CRON_SECRET", "x")
    monkeypatch.setenv("RUN_WEEK_SANDBOX", str(run_week.ROOT))
    monkeypatch.setattr(sys, "argv", ["run_week.py", "--episode", "2027-W01", "--days", "monday"])
    run_week.main()
    assert "QA scoring failed" in capsys.readouterr().err


def test_an_unreadable_episode_after_a_200_still_reports_the_day(monkeypatch, capsys):
    code, _ = _run(monkeypatch, ["--episode", "2027-W01", "--days", "monday"], load="not a dict")
    assert code == 0 and "(stage not readable)" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    ["--episode", "../../episodes/2026-W42"],
    ["--episode", "test-e"],
    ["--episode", "2027-W99"],
    ["--episode", "2027-W01", "--days", " , "],
    ["--episode", "2027-W01", "--days", "funday"],
])
def test_bad_arguments_stop_before_any_call(monkeypatch, argv):
    with pytest.raises(SystemExit):
        _run(monkeypatch, argv)


def test_storage_without_namespaces_stops_before_any_call(monkeypatch):
    monkeypatch.setattr(TestClient, "post", lambda *a, **k: pytest.fail("posted"))
    monkeypatch.setattr(type(storage), "namespaces_episodes", lambda self: False)
    monkeypatch.setenv("RUN_WEEK_SANDBOX", str(run_week.ROOT))
    monkeypatch.setattr(sys, "argv", ["run_week.py", "--episode", "2027-W01"])
    with pytest.raises(SystemExit):
        run_week.main()


DAYS = [p.rsplit("/", 1)[-1] for p in DAY_PATHS]


@settings(max_examples=30)
@given(st.lists(st.sampled_from(DAYS), min_size=1, unique=True))
def test_only_the_selected_days_run_in_schedule_order(days):
    with pytest.MonkeyPatch.context() as monkeypatch:
        code, posted = _run(monkeypatch, ["--episode", "2027-W01", "--days", ",".join(days)])
    assert code == 0
    assert [path.rsplit("/", 1)[-1] for path, _ in posted] == [d for d in DAYS if d in days]


def test_a_week_runs_from_a_throwaway_copy_that_is_deleted_afterwards(monkeypatch):
    """Handlers write repo-relative files; they must land in the copy, not the checkout."""
    seen = {}

    def fake_run(cmd, cwd, env, timeout):
        seen.update(cwd=Path(cwd), script=Path(cmd[1]), sandbox=env["RUN_WEEK_SANDBOX"], argv=cmd[2:])
        assert (Path(cwd) / "vercel.json").read_text() == (ROOT / "vercel.json").read_text()
        return type("Done", (), {"returncode": 3})()

    real_run = run_week.subprocess.run
    monkeypatch.setattr(run_week.subprocess, "run",
                        lambda cmd, **kw: real_run(cmd, **kw) if cmd[0] == "git" else fake_run(cmd, **kw))
    monkeypatch.delenv("RUN_WEEK_SANDBOX", raising=False)
    monkeypatch.setattr(sys, "argv", ["run_week.py", "--episode", "2027-W01", "--days", "monday"])
    assert run_week.main() == 3
    assert seen["cwd"].resolve() != ROOT and seen["script"].is_relative_to(seen["cwd"])
    assert seen["sandbox"] == str(seen["cwd"].resolve())
    assert seen["argv"] == ["--episode", "2027-W01", "--days", "monday"]
    assert not seen["cwd"].exists()
