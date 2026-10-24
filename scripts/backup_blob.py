"""Snapshot this project's Vercel Blob records, then copy them offsite (#8427).

The site has no database: its state is the Vercel Blob store (episodes,
pages, photo control, scores, character memory). Each run downloads every
blob outside SKIPPED_PREFIXES into a new run directory, then copies that
directory to the named rclone remote and checks it there. Images are 97% of
the bytes and change rarely, so a separate incremental mirror covers them;
test/ is compressed-timeline scratch.

Run directory layout: blobs/00000, blobs/00001, ... hold the bodies in
listing order, and manifest.json maps each file to its blob pathname, size,
sha256 and uploadedAt. Numbered names mean no blob pathname can collide with
another or with the manifest. To restore one, look up its pathname in the
manifest.

Run under Doppler with BLOB_READ_WRITE_TOKEN:
    doppler run --config prd -- uv run python scripts/backup_blob.py create
    uv run python scripts/backup_blob.py status   # exit 1 if stale (>26h) or failed

Never syncs, prunes or writes to the Blob store. A verified copy is not a
restore drill.
"""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.storage import _CloudBackend  # noqa: E402

DEFAULT_BACKUPS = Path.home() / ".local/state/muffinpanrecipes/backups"
DEFAULT_REMOTE = "gbackup:db-backups/muffinpanrecipes"
SKIPPED_PREFIXES = ("images/", "test/")
MAX_LIST_PAGES = 50


class BackupError(RuntimeError):
    """Safe operational message; never includes the token or rclone stderr."""


def clock() -> datetime:
    return datetime.now(timezone.utc)


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".partial")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def rclone(*args: str) -> str:
    try:
        return subprocess.run(["rclone", *args], check=True, capture_output=True, text=True,
                              timeout=1200).stdout
    except subprocess.CalledProcessError as exc:
        raise BackupError(f"rclone {args[0]} failed (exit {exc.returncode})") from None
    except subprocess.TimeoutExpired:
        raise BackupError(f"rclone {args[0]} exceeded 20 minutes") from None


def list_records(blob: _CloudBackend) -> list[dict]:
    """Every listed blob outside SKIPPED_PREFIXES, across all LIST pages."""
    records, cursor = [], None
    for _ in range(MAX_LIST_PAGES):
        page = blob._list_page("", cursor, "1000")
        records += [b for b in page["blobs"] if not b["pathname"].startswith(SKIPPED_PREFIXES)]
        cursor = page.get("cursor")
        if not page.get("hasMore"):
            return records
    raise BackupError(f"Blob listing did not finish within {MAX_LIST_PAGES} pages")


def download(blob: _CloudBackend, record: dict, path: Path) -> dict:
    """Write one blob's body to ``path``; return its manifest entry."""
    response = requests.get(record["url"], headers=blob._auth_headers(), timeout=60)
    response.raise_for_status()
    body = response.content
    if len(body) != record["size"]:
        raise BackupError(f"{record['pathname']}: got {len(body)} bytes, listed {record['size']}")
    path.write_bytes(body)
    return {"file": f"blobs/{path.name}", "pathname": record["pathname"], "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(), "uploaded_at": record.get("uploadedAt")}


def snapshot(directory: Path, remote: str) -> dict:
    """Download, manifest, copy and verify one run; raise on any failure."""
    # rclone reads a destination whose prefix is not a configured remote as a
    # local path or an on-the-fly backend (./gbackup:x, /x:y, :local:x), so let
    # rclone resolve it and require the remote it names. An unknown remote
    # fails here as an rclone error.
    name, colon, _ = remote.partition(":")
    resolved = json.loads(rclone("backend", "features", remote))["Name"]
    if not colon or resolved != name.split(",")[0]:
        raise BackupError("Offsite destination must use an existing named rclone remote")
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise BackupError("BLOB_READ_WRITE_TOKEN is required; run under Doppler")
    blob = _CloudBackend()
    records = list_records(blob)
    if not records:
        raise BackupError("Blob listing returned no records")
    (directory / "blobs").mkdir()
    blobs = [download(blob, record, directory / "blobs" / f"{index:05d}")
             for index, record in enumerate(records)]
    write_json(directory / "manifest.json", {"run_id": directory.name, "blobs": blobs})
    destination = remote.rstrip("/") + "/" + directory.name
    rclone("copy", str(directory), destination)
    rclone("check", str(directory), destination, "--one-way")
    return {"blobs": len(blobs), "bytes": sum(b["bytes"] for b in blobs), "destination": destination}


def create_backup(root: Path, remote: str) -> dict:
    """Run one snapshot under a lock and record its outcome in latest.json."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError("Another backup is running") from None
        run_id = clock().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        directory = root / run_id
        directory.mkdir(mode=0o700)
        report: dict[str, object] = {"run_id": run_id, "started_at": clock().isoformat()}
        try:
            report.update(status="complete", **snapshot(directory, remote))
        except Exception as exc:  # noqa: BLE001 - recorded in latest.json; main exits nonzero
            report.update(status="failed",
                          error=str(exc) if isinstance(exc, BackupError) else type(exc).__name__)
        report["finished_at"] = clock().isoformat()
        write_json(root / "latest.json", report)
        return report


def backup_status(root: Path) -> dict:
    report = json.loads((root / "latest.json").read_text())
    age = clock() - datetime.fromisoformat(report["finished_at"])
    report["stale"] = not timedelta(0) <= age <= timedelta(hours=26)
    report["healthy"] = report["status"] == "complete" and not report["stale"]
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["create", "status"])
    parser.add_argument("--backup-dir", type=Path, default=DEFAULT_BACKUPS)
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="named rclone remote and path")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "status":
            report = backup_status(args.backup_dir)
            ok = report["healthy"]
        else:
            report = create_backup(args.backup_dir, args.remote)
            ok = report["status"] == "complete"
    except Exception as exc:  # noqa: BLE001 - nonzero exit with token-safe diagnostics
        report = {"status": "failed", "error": str(exc) if isinstance(exc, BackupError) else type(exc).__name__}
        ok = False
    print(json.dumps(report, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
