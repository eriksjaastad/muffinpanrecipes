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

--from-episode copies a production week's stages that come before the first
requested day into the test episode, so a later day can be replayed against a
real week (Sunday still needs a photo approved in the admin at ?ns=test).
Judge FAILED attempts are counted from the handler's own log lines.
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
    parser.add_argument("--episode", required=True, help="test episode id (stored under test/)")
    parser.add_argument("--concept", help="recipe concept for Monday; omit to let Monday pick")
    parser.add_argument("--days", default=",".join(days), help="comma-separated days (default: all)")
    parser.add_argument("--from-episode", help="production episode whose earlier stages seed the test episode")
    args = parser.parse_args()
    wanted = [d.strip() for d in args.days.split(",") if d.strip()]
    if unknown := sorted(set(wanted) - set(days)):
        parser.error(f"unknown day(s) {unknown}; choose from {days}")

    from fastapi.testclient import TestClient

    from backend.admin.app import app
    from backend.storage import storage

    if args.from_episode:
        source = storage.load_episode(args.from_episode)
        if source is None:
            parser.error(f"production episode {args.from_episode!r} not found")
        keep = days[: days.index(wanted[0])]
        source["stages"] = {d: s for d, s in source.get("stages", {}).items() if d in keep}
        source["episode_id"] = args.episode
        with storage.prefix_scope("test/"):
            storage.save_episode(args.episode, source)

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
