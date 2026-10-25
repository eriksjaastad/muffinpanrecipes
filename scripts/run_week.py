#!/usr/bin/env python3
"""Run a test week locally through the production cron handlers (#8617).

Each day is a POST to the same FastAPI app Vercel serves (backend/admin/app.py),
in-process, with ``test=true`` so every write lands under the ``test/`` Blob
prefix. Prompts, models, judge, retries, QA and photos are production's; only
the trigger (this CLI instead of Vercel cron) and the prefix differ. The day
list comes from vercel.json's crons, so a new or renamed cron is picked up here.

Real paid calls; needs the production secrets. Use a week id production will
not reach soon; it lives only under test/:

    doppler run --project muffinpanrecipes --config prd -- \\
        uv run python scripts/run_week.py --episode 2027-W01 --concept "Gruyère Gougères"

It runs inside a throwaway copy of the checkout's tracked files (working-tree
content), deleted afterwards. Production's handlers also write repo-relative
files (agent memories, image jobs, local images, an episode mirror); on Vercel
those land on an ephemeral filesystem, and here they land in the copy, so no
local week can leave state behind that a later run or tool reads.

It stops at the first day that does not return 200 and prints the response:
Sunday returns 202 until a photo is approved in the admin at ?ns=test.
Judge FAILED attempts are counted per day from the handler's own log lines.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
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


def _run_in_sandbox(argv: list[str]) -> int:
    """Re-run this command from a temporary copy of the tracked files; return its exit code."""
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached"],
        capture_output=True, check=True, text=True, timeout=60,
    ).stdout.split("\0")
    with tempfile.TemporaryDirectory(prefix="run_week-") as tmp:
        for rel in filter(None, tracked):
            if (ROOT / rel).is_file():
                (Path(tmp) / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / rel, Path(tmp) / rel)
        env = {**os.environ, "RUN_WEEK_SANDBOX": str(Path(tmp).resolve())}
        return subprocess.run(
            [sys.executable, str(Path(tmp) / "scripts" / "run_week.py"), *argv],
            cwd=tmp, env=env, timeout=4 * 3600,
        ).returncode


def main() -> int:
    days = cron_days()
    parser = argparse.ArgumentParser(description="Run a test week through the production cron handlers.")
    parser.add_argument("--episode", required=True, help="ISO week, e.g. 2026-W44 (stored under test/)")
    parser.add_argument("--concept", help="recipe concept for Monday; omit to let Monday pick")
    parser.add_argument("--days", default=",".join(days), help="comma-separated days (default: all)")
    args = parser.parse_args()
    # A real ISO week, as production uses: Sunday's character memories are
    # keyed by it, and it can carry no path characters out of test/.
    from backend.storage import is_valid_iso_week

    if not is_valid_iso_week(args.episode):
        parser.error(f"--episode must be an ISO week such as 2026-W44, got {args.episode!r}")
    if os.environ.get("RUN_WEEK_SANDBOX") != str(ROOT):
        return _run_in_sandbox(sys.argv[1:])
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

    # A handler of our own would silence Python's last-resort stderr output,
    # so the handlers' warnings and errors get a visible sink too.
    cron_log = logging.getLogger("backend.admin.cron_routes")
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    failures = _JudgeFailures()
    cron_log.addHandler(console)
    cron_log.addHandler(failures)
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {os.environ['CRON_SECRET']}"}
    for day in [d for d in days if d in wanted]:
        body = {"episode_id": args.episode, "test": True, "force": True}
        if day == "monday" and args.concept:
            body["concept"] = args.concept
        failures.count = 0
        resp = client.post(f"/api/cron/{day}", json=body, headers=headers)
        line = f"{day:<10} HTTP {resp.status_code}  judge FAILs before result: {failures.count}"
        if resp.status_code != 200:
            print(f"{line}\n  stopped: {resp.text[:600]}")
            return 1
        with storage.prefix_scope("test/"):
            episode = storage.load_episode(args.episode)
        stage = (episode.get("stages") or {}).get(day) if isinstance(episode, dict) else None
        verdict = stage.get("judge_verdict") if isinstance(stage, dict) else "(stage not readable)"
        print(f"{line}  {str(verdict or '')[:100]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
