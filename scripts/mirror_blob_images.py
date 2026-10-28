"""Incrementally mirror production Blob images to an append-only offsite store.

The records backup intentionally skips ``images/`` because the image corpus is
large and mostly immutable.  This companion job lists that prefix, downloads
only new or changed objects, stores bodies by SHA-256, and copies a manifest to
an existing rclone remote.  It never writes to or deletes from Vercel Blob and
never prunes the offsite destination.

Run under Doppler for inventory or mirroring::

    doppler run --project muffinpanrecipes --config prd -- \
      uv run python scripts/mirror_blob_images.py inventory
    doppler run --project muffinpanrecipes --config prd -- \
      uv run python scripts/mirror_blob_images.py mirror

Use ``--max-objects`` or ``--max-bytes`` for a bounded first run.  A bounded
run is deliberately marked incomplete; later runs continue from local state.
``restore-sample`` reads one verified body into a caller-chosen local path and
does not touch Blob storage.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.storage import _CloudBackend  # noqa: E402


DEFAULT_STATE_DIR = Path.home() / ".local/state/muffinpanrecipes/image-mirror"
DEFAULT_REMOTE = "gbackup:blob-backups/muffinpanrecipes/images"
MAX_LIST_PAGES = 50
REMOTE_NAME = re.compile(r"[\w.+@](?:[\w.+@ -]*[\w.+@-])?")


class MirrorError(RuntimeError):
    """A token-safe operational error suitable for status output."""


def clock() -> datetime:
    return datetime.now(timezone.utc)


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _remote_parts(remote: str) -> tuple[str, str]:
    name, colon, path = remote.partition(":")
    if not colon or not REMOTE_NAME.fullmatch(name):
        raise MirrorError("Offsite destination must use an existing named rclone remote")
    return name, path


def rclone(*args: str, binary: bool = False) -> str | bytes:
    try:
        result = subprocess.run(
            ["rclone", *args],
            check=True,
            capture_output=True,
            text=not binary,
            timeout=1200,
        )
    except subprocess.CalledProcessError as exc:
        raise MirrorError(f"rclone {args[0]} failed (exit {exc.returncode})") from None
    except subprocess.TimeoutExpired:
        raise MirrorError(f"rclone {args[0]} exceeded 20 minutes") from None
    return result.stdout


def _remote_state(remote: str) -> dict | None:
    """Read the latest remote manifest; missing is valid only before run one."""
    name, remote_path = _remote_parts(remote)
    source = f"{name}:{posixpath.join(remote_path, 'latest.json')}"
    try:
        result = subprocess.run(
            ["rclone", "cat", source],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise MirrorError("rclone cat exceeded 2 minutes") from None
    if result.returncode in {3, 4}:
        return None
    if result.returncode:
        raise MirrorError(f"rclone cat failed (exit {result.returncode})")
    try:
        manifest = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise MirrorError("Remote image-mirror state is not valid JSON") from None
    if manifest.get("version") != 1 or not isinstance(manifest.get("objects"), dict):
        raise MirrorError("Remote image-mirror state has an unsupported shape")
    return {
        "version": 1,
        "remote": remote,
        "last_run_id": manifest.get("run_id"),
        "finished_at": manifest.get("created_at"),
        "source": manifest.get("source"),
        "complete": manifest.get("complete", False),
        "pending_objects": manifest.get("pending_objects"),
        "objects": manifest["objects"],
    }


def list_images(blob: _CloudBackend) -> list[dict]:
    """Return the complete production ``images/`` listing."""
    images: list[dict] = []
    cursor = None
    for _ in range(MAX_LIST_PAGES):
        page = blob._list_page("images/", cursor, "1000")
        images.extend(page["blobs"])
        cursor = page.get("cursor")
        if not page.get("hasMore"):
            if not images:
                raise MirrorError("Blob listing returned no images")
            return images
    raise MirrorError(f"Blob listing did not finish within {MAX_LIST_PAGES} pages")


def inventory(images: list[dict]) -> dict:
    extensions: dict[str, int] = {}
    total = 0
    for image in images:
        pathname = str(image.get("pathname") or "")
        extension = pathname.rsplit(".", 1)[-1].lower() if "." in pathname else "(none)"
        extensions[extension] = extensions.get(extension, 0) + 1
        total += int(image.get("size") or 0)
    return {
        "objects": len(images),
        "bytes": total,
        "extensions": dict(sorted(extensions.items())),
    }


def _empty_state(remote: str) -> dict:
    return {"version": 1, "remote": remote, "objects": {}}


def load_state(path: Path, remote: str) -> dict:
    if not path.exists():
        state = _remote_state(remote) or _empty_state(remote)
    else:
        state = json.loads(path.read_text())
    if state.get("version") != 1 or not isinstance(state.get("objects"), dict):
        raise MirrorError("Local image-mirror state has an unsupported shape")
    if state.get("remote") != remote:
        raise MirrorError("Configured image-mirror destination differs from local state")
    return state


def _unchanged(record: dict, previous: dict | None) -> bool:
    uploaded_at = record.get("uploadedAt")
    return bool(
        previous
        and uploaded_at
        and previous.get("bytes") == int(record.get("size") or 0)
        and previous.get("uploaded_at") == uploaded_at
    )


def select_changes(
    images: list[dict], state: dict, max_objects: int, max_bytes: int
) -> tuple[list[dict], int]:
    changed = [
        image
        for image in images
        if not _unchanged(image, state["objects"].get(image.get("pathname")))
    ]
    # A bounded proof uses the smallest objects first; an unlimited scheduled
    # run still processes every changed object.
    changed.sort(key=lambda image: (int(image.get("size") or 0), str(image.get("pathname") or "")))
    selected: list[dict] = []
    selected_bytes = 0
    for image in changed:
        size = int(image.get("size") or 0)
        if max_objects and len(selected) >= max_objects:
            break
        if max_bytes and selected and selected_bytes + size > max_bytes:
            break
        if max_bytes and not selected and size > max_bytes:
            continue
        selected.append(image)
        selected_bytes += size
    return selected, len(changed) - len(selected)


def download(blob: _CloudBackend, record: dict, directory: Path) -> dict:
    """Stream and hash one image body, returning its durable state entry."""
    temporary = directory / f"download-{uuid4().hex}.partial"
    digest = hashlib.sha256()
    length = 0
    with requests.get(
        record["url"], headers=blob._auth_headers(), timeout=60, stream=True
    ) as response:
        response.raise_for_status()
        with temporary.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    output.write(chunk)
                    digest.update(chunk)
                    length += len(chunk)
    expected = int(record.get("size") or 0)
    if length != expected:
        temporary.replace(directory / f"short-{uuid4().hex}.partial")
        raise MirrorError(f"{record['pathname']}: got {length} bytes, listed {expected}")
    sha256 = digest.hexdigest()
    object_path = directory / "objects" / sha256
    object_path.parent.mkdir(exist_ok=True)
    # Equal SHA-256 means equal content; replacing avoids leaving an extra
    # staged file that rclone would mistake for a mirror object.
    temporary.replace(object_path)
    return {
        "bytes": length,
        "object": f"objects/{sha256}",
        "sha256": sha256,
        "uploaded_at": record.get("uploadedAt"),
    }


def mirror(
    state_dir: Path,
    remote: str,
    max_objects: int = 0,
    max_bytes: int = 0,
) -> dict:
    """Mirror a bounded set of changed images and atomically advance state."""
    name, remote_path = _remote_parts(remote)
    rclone("backend", "features", remote)
    if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
        raise MirrorError("BLOB_READ_WRITE_TOKEN is required; run under Doppler")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise MirrorError("Another image mirror is running") from None

        state_path = state_dir / "state.json"
        state = load_state(state_path, remote)
        blob = _CloudBackend()
        images = list_images(blob)
        selected, remaining = select_changes(images, state, max_objects, max_bytes)
        run_id = clock().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]

        with tempfile.TemporaryDirectory(prefix="muffin-image-mirror-") as temporary:
            stage = Path(temporary)
            updated = dict(state["objects"])
            transferred_bytes = 0
            for record in selected:
                entry = download(blob, record, stage)
                updated[record["pathname"]] = entry
                transferred_bytes += entry["bytes"]

            manifest = {
                "version": 1,
                "remote": remote,
                "run_id": run_id,
                "created_at": clock().isoformat(),
                "source": inventory(images),
                "complete": remaining == 0,
                "pending_objects": remaining,
                "transferred_objects": len(selected),
                "transferred_bytes": transferred_bytes,
                "objects": updated,
            }
            manifests = stage / "manifests"
            manifests.mkdir()
            write_json(manifests / f"{run_id}.json", manifest)
            destination = f"{name}:{posixpath.join(remote_path, '')}"
            rclone("copy", str(stage), destination)
            rclone("check", str(stage), destination, "--one-way")
            # Publish latest only after every body and immutable run manifest
            # verifies.  A failed partial copy can never become recoverable
            # state that claims missing objects are safely offsite.
            latest = stage / "latest.json"
            write_json(latest, manifest)
            remote_latest = f"{name}:{posixpath.join(remote_path, 'latest.json')}"
            rclone("copyto", str(latest), remote_latest)
            if rclone("cat", remote_latest, binary=True) != latest.read_bytes():
                raise MirrorError("Remote latest manifest failed byte verification")

        local_state = {
            "version": 1,
            "remote": remote,
            "last_run_id": run_id,
            "finished_at": clock().isoformat(),
            "source": manifest["source"],
            "complete": manifest["complete"],
            "pending_objects": manifest["pending_objects"],
            "objects": updated,
        }
        write_json(state_path, local_state)
        return {
            "status": "complete",
            "run_id": run_id,
            "source_objects": len(images),
            "source_bytes": manifest["source"]["bytes"],
            "transferred_objects": len(selected),
            "transferred_bytes": transferred_bytes,
            "pending_objects": remaining,
            "mirror_complete": remaining == 0,
            "destination": remote,
        }


def status(state_dir: Path) -> dict:
    state = json.loads((state_dir / "state.json").read_text())
    age = clock() - datetime.fromisoformat(state["finished_at"])
    state["stale"] = not timedelta(0) <= age <= timedelta(days=8)
    state["healthy"] = bool(state.get("complete")) and not state["stale"]
    return state


def restore_sample(state_dir: Path, remote: str, pathname: str, output: Path) -> dict:
    state = load_state(state_dir / "state.json", remote)
    entry = state["objects"].get(pathname)
    if not entry:
        raise MirrorError("Requested pathname is not present in mirrored state")
    name, remote_path = _remote_parts(remote)
    source = f"{name}:{posixpath.join(remote_path, entry['object'])}"
    body = rclone("cat", source, binary=True)
    assert isinstance(body, bytes)
    if len(body) != entry["bytes"] or hashlib.sha256(body).hexdigest() != entry["sha256"]:
        raise MirrorError("Restored sample failed byte-count or SHA-256 verification")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(body)
    return {
        "status": "verified",
        "pathname": pathname,
        "bytes": len(body),
        "sha256": entry["sha256"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("action", choices=["inventory", "mirror", "status", "restore-sample"])
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--max-objects", type=int, default=0)
    parser.add_argument("--max-bytes", type=int, default=0)
    parser.add_argument("--pathname")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.max_objects < 0 or args.max_bytes < 0:
        parser.error("--max-objects and --max-bytes must be non-negative")
    os.umask(0o077)
    try:
        if args.action == "status":
            report = status(args.state_dir)
            ok = report["healthy"]
        elif args.action == "restore-sample":
            if not args.pathname or not args.output:
                raise MirrorError("restore-sample requires --pathname and --output")
            report = restore_sample(args.state_dir, args.remote, args.pathname, args.output)
            ok = True
        else:
            if not os.environ.get("BLOB_READ_WRITE_TOKEN"):
                raise MirrorError("BLOB_READ_WRITE_TOKEN is required; run under Doppler")
            if args.action == "inventory":
                images = list_images(_CloudBackend())
                report = {"status": "complete", **inventory(images)}
            else:
                report = mirror(args.state_dir, args.remote, args.max_objects, args.max_bytes)
            ok = True
    except Exception as exc:  # noqa: BLE001 - emit only token-safe error classes/messages
        report = {
            "status": "failed",
            "error": str(exc) if isinstance(exc, MirrorError) else type(exc).__name__,
        }
        ok = False
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
