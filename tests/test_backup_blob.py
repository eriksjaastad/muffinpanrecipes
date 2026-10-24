"""scripts/backup_blob.py: Vercel Blob records snapshot copied offsite (#8427).

The Blob API is faked; rclone is real, pointed at a local-directory remote
defined by environment variables, so no test touches the network.
"""
import fcntl
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from hypothesis import given, settings, strategies as st

from scripts import backup_blob

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


BODIES: dict[str, bytes] = {}


def _blob(pathname: str, body: bytes) -> dict:
    """A listed blob whose fake download returns ``body``."""
    url = f"https://blob.test/{len(BODIES)}"
    BODIES[url] = body
    return {"pathname": pathname, "url": url, "size": len(body), "uploadedAt": "2026-10-23T00:00:00Z"}
# Names that collided under the old pathname-derived layout (Codex FAIL #1).
COLLIDING = [_blob("manifest.json", b"m"), _blob("manifest.partial", b"p"),
             _blob("pages//r.json", b"double"), _blob("pages/r.json", b"single")]
PAGES = [
    {"blobs": [_blob("episodes/2026-W43.json", b"{}{}"), _blob("images/hero.png", b"png")] + COLLIDING[:2],
     "hasMore": True, "cursor": "c1"},
    {"blobs": COLLIDING[2:] + [_blob("test/episodes/x.json", b"t")], "hasMore": False},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "test-token")
    monkeypatch.setenv("RCLONE_CONFIG_TESTLOCAL_TYPE", "local")
    # rclone accepts an env-defined remote in any case; listremotes shows only lowercase.
    return tmp_path / "backups", f"TestLocal:{tmp_path / 'remote'}"


def _get(url, **_kw):
    return MagicMock(content=BODIES[url], raise_for_status=lambda: None)


def _create(root, remote, pages=PAGES, get=_get):
    listed = iter(pages)
    with patch.object(backup_blob._CloudBackend, "_list_page", lambda *_a: next(listed)), \
         patch.object(backup_blob.requests, "get", get):
        return backup_blob.main(["create", "--backup-dir", str(root), "--remote", remote])


def _latest(root) -> dict:
    return json.loads((root / "latest.json").read_text())


def _status(root) -> int:
    return backup_blob.main(["status", "--backup-dir", str(root)])


def test_a_run_copies_every_record_once_by_manifest_and_skips_images_and_test(env):
    root, remote = env
    assert _create(root, remote) == 0
    report = _latest(root)
    assert report["status"] == "complete" and report["blobs"] == 5

    copied = Path(report["destination"].removeprefix("TestLocal:"))
    manifest = json.loads((copied / "manifest.json").read_text())
    by_name = {b["pathname"]: (copied / b["file"]).read_bytes() for b in manifest["blobs"]}
    assert by_name == {"episodes/2026-W43.json": b"{}{}", "manifest.json": b"m",
                       "manifest.partial": b"p", "pages//r.json": b"double", "pages/r.json": b"single"}
    assert all(hashlib.sha256((copied / b["file"]).read_bytes()).hexdigest() == b["sha256"]
               for b in manifest["blobs"])
    assert _status(root) == 0


def test_a_short_download_fails_and_copies_nothing_offsite(env):
    root, remote = env

    def short(url, **_kw):
        return MagicMock(content=BODIES[url][:-1], raise_for_status=lambda: None)

    assert _create(root, remote, get=short) == 1
    assert "got 3 bytes, listed 4" in _latest(root)["error"]
    assert not Path(remote.removeprefix("TestLocal:")).exists()


def test_a_failed_offsite_copy_fails_the_run(env, tmp_path):
    root, _ = env
    (tmp_path / "a-file").write_text("")
    assert _create(root, f"testlocal:{tmp_path / 'a-file' / 'remote'}") == 1
    assert _latest(root)["error"] == "rclone copy failed (exit 1)"
    assert _status(root) == 1


def test_a_failed_offsite_check_after_a_good_copy_fails_the_run(env):
    root, remote = env
    real_run = subprocess.run

    def run(args, **kwargs):
        return real_run(["false"] if args[1] == "check" else args, **kwargs)

    with patch.object(backup_blob.subprocess, "run", run):
        assert _create(root, remote) == 1
    assert _latest(root)["error"] == "rclone check failed (exit 1)"
    assert _status(root) == 1


@pytest.mark.parametrize("remote, token, error", [
    ("local", "test-token", "Offsite destination must use an existing named rclone remote"),
    ("no-such-remote:records", "test-token", "rclone backend failed (exit 1)"),
    ("./testlocal:records", "test-token", "Offsite destination must use an existing named rclone remote"),
    (":local:records", "test-token", "Offsite destination must use an existing named rclone remote"),
    (None, "", "BLOB_READ_WRITE_TOKEN is required; run under Doppler"),
], ids=["no-colon", "unknown-remote", "local-path-with-colon", "on-the-fly-backend", "no-token"])
def test_a_setup_failure_after_a_success_is_recorded_and_status_fails(env, monkeypatch, remote, token, error):
    root, good_remote = env
    assert _create(root, good_remote) == 0
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", token)

    assert _create(root, remote or good_remote) == 1
    assert _latest(root)["error"] == error
    assert _status(root) == 1


@pytest.mark.parametrize("pages, error", [
    ([{"blobs": [_blob("images/only.png", b"x")], "hasMore": False}], "Blob listing returned no records"),
    ([{"blobs": [], "hasMore": True, "cursor": "same"}] * 50, "Blob listing did not finish within 50 pages"),
], ids=["nothing-to-back-up", "listing-never-ends"])
def test_an_unusable_listing_fails_the_run(env, pages, error):
    root, remote = env
    assert _create(root, remote, pages=pages) == 1
    assert _latest(root)["error"] == error


def test_a_listing_http_error_fails_the_run_without_leaking_detail(env):
    root, remote = env

    def broken(*_a):
        raise requests.HTTPError("403 Forbidden for url with token=secret")

    with patch.object(backup_blob._CloudBackend, "_list_page", broken):
        assert backup_blob.main(["create", "--backup-dir", str(root), "--remote", remote]) == 1
    assert _latest(root)["error"] == "HTTPError"


def test_an_overlapping_run_exits_nonzero_and_leaves_latest_alone(env):
    root, remote = env
    assert _create(root, remote) == 0
    before = (root / "latest.json").read_text()

    with (root / ".lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert _create(root, remote) == 1
    assert (root / "latest.json").read_text() == before


def test_status_fails_once_the_last_success_is_over_26_hours_old(env):
    root, remote = env
    assert _create(root, remote) == 0

    later = backup_blob.clock() + timedelta(hours=27)
    with patch.object(backup_blob, "clock", return_value=later):
        assert _status(root) == 1


@settings(max_examples=50, deadline=None)
@given(st.lists(st.text(min_size=1).filter(lambda p: not p.startswith(backup_blob.SKIPPED_PREFIXES)),
                min_size=1, max_size=8))
def test_any_listed_pathnames_all_survive_with_their_own_bytes(pathnames):
    """Duplicates, '..', '/', NUL and 'manifest.json' included: no body overwrites another."""
    records = [{"pathname": p, "url": str(i), "size": len(str(i))} for i, p in enumerate(pathnames)]
    with tempfile.TemporaryDirectory() as tmp, \
         patch.object(backup_blob, "rclone", return_value='{"Name": "testlocal"}'), \
         patch.object(backup_blob, "list_records", return_value=records), \
         patch.object(backup_blob.requests, "get",
                      side_effect=lambda url, **_kw: MagicMock(content=url.encode(), raise_for_status=lambda: None)), \
         patch.dict("os.environ", {"BLOB_READ_WRITE_TOKEN": "t"}):
        directory = Path(tmp) / "run"
        directory.mkdir()
        backup_blob.snapshot(directory, "testlocal:unused")
        manifest = json.loads((directory / "manifest.json").read_text())
        assert [b["pathname"] for b in manifest["blobs"]] == pathnames
        assert [(directory / b["file"]).read_bytes() for b in manifest["blobs"]] == [str(i).encode() for i in range(len(pathnames))]
        assert sorted(p.name for p in directory.iterdir()) == ["blobs", "manifest.json"]
