#!/usr/bin/env python3
"""Run a test week locally through the production cron handlers (#8617).

Each day is a POST to the same FastAPI app Vercel serves (backend/admin/app.py),
in-process, with ``test=true`` so every write lands under the ``test/`` Blob
prefix. Prompts, models, judge, retries, QA and photos are production's; only
the trigger (this CLI instead of Vercel cron) and the prefix differ. The day
list comes from vercel.json's crons, so a new or renamed cron is picked up here.

Real paid calls; needs the production secrets:

    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/run_week.py --episode test-gougeres --concept "Gruyère Gougères"

It stops at the first day that does not return 200 and prints the response:
Sunday returns 202 until a photo is approved in the admin at ?ns=test.
Judge FAILED attempts are counted per day from the handler's own log lines.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def cron_days() -> list[str]:
    """Day stages in the order vercel.json schedules them; repair is not a day."""
    crons = json.loads((ROOT / "vercel.json").read_text())["crons"]
    return [c["path"].rsplit("/", 1)[-1] for c in crons if c["path"] != "/api/cron/repair"]


class _JudgeFailures(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage().startswith("Judge FAILED"):
            self.count += 1


def main() -> int:
    days = cron_days()
    parser = argparse.ArgumentParser(description="Run a test week through the production cron handlers.")
    parser.add_argument("--episode", required=True, help="test episode id, starting test- (stored under test/)")
    parser.add_argument("--concept", help="recipe concept for Monday; omit to let Monday pick")
    parser.add_argument("--days", default=",".join(days), help="comma-separated days (default: all)")
    args = parser.parse_args()
    # Blob writes go under test/, but the backend also mirrors every episode to
    # data/episodes/<id>.json unprefixed, so the id itself must never name a
    # real week. Storage's own id rule then bounds it (photo control, Wednesday).
    from backend import storage as storage_module

    try:
        storage_module._check_photo_control_args(args.episode)
    except ValueError:
        parser.error(f"--episode must be 1-64 letters, digits, - or _, got {args.episode!r}")
    if not args.episode.startswith("test-"):
        parser.error(f"--episode must start with 'test-', got {args.episode!r}")
    wanted = {d.strip() for d in args.days.split(",") if d.strip()}
    if not wanted:
        parser.error("--days selects no day")
    if unknown := sorted(wanted - set(days)):
        parser.error(f"unknown day(s) {unknown}; choose from {days}")

    from fastapi.testclient import TestClient

    from backend.admin.app import app
    from backend.storage import storage

    # The filesystem fallback ignores the test/ prefix, so without Blob this
    # would write ordinary local episodes, not an isolated test week.
    if not storage.namespaces_episodes():
        parser.error("storage cannot keep test/ separate; run under doppler --config prd")

    failures = _JudgeFailures()
    logging.getLogger("backend.admin.cron_routes").addHandler(failures)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {os.environ['CRON_SECRET']}"}
    for day in [d for d in days if d in wanted]:
        body = {"episode_id": args.episode, "test": True, "force": True}
        if day == "monday" and args.concept:
            body["concept"] = args.concept
        failures.count = 0
        resp = client.post(f"/api/cron/{day}", json=body, headers=headers)
        with storage.prefix_scope("test/"):
            stage = (storage.load_episode(args.episode) or {}).get("stages", {}).get(day, {})
        verdict = str(stage.get("judge_verdict") or "")[:100]
        print(f"{day:<10} HTTP {resp.status_code}  judge FAILs before result: {failures.count}  {verdict}")
        if resp.status_code != 200:
            print(f"  stopped: {resp.text[:600]}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
