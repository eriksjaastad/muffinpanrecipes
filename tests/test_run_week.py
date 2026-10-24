"""scripts/run_week.py must run exactly the handlers Vercel's crons run (#8617)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.routing import Match

import backend.admin.cron_routes as cron_routes
from backend.admin.app import app
from backend.storage import storage
from scripts import run_week

ROOT = Path(__file__).resolve().parent.parent


def _endpoint(path: str):
    scope = {"type": "http", "path": path, "method": "POST"}
    return next(r.endpoint for r in app.router.routes if r.matches(scope)[0] is Match.FULL)


def test_a_local_week_posts_every_vercel_cron_day_to_its_production_handler(monkeypatch):
    crons = json.loads((ROOT / "vercel.json").read_text())["crons"]
    day_paths = [c["path"] for c in crons if c["path"] != "/api/cron/repair"]
    posted: list[tuple[str, dict]] = []

    class _Resp:
        status_code, text = 200, ""

    monkeypatch.setattr(TestClient, "post", lambda self, path, json, headers: posted.append((path, json)) or _Resp())
    monkeypatch.setattr(storage, "load_episode", lambda episode_id: None)
    monkeypatch.setenv("CRON_SECRET", "x")
    monkeypatch.setattr(sys, "argv", ["run_week.py", "--episode", "test-e", "--concept", "Gougères"])

    assert run_week.main() == 0
    assert [path for path, _ in posted] == day_paths
    for path, body in posted:
        assert _endpoint(path) is getattr(cron_routes, f"cron_{path.rsplit('/', 1)[-1]}")
        assert body["test"] is True and body["episode_id"] == "test-e"
    assert posted[0][1]["concept"] == "Gougères"
    assert all("concept" not in body for _, body in posted[1:])
