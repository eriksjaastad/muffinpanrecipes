"""Hermetic coverage for the incremental Vercel Blob image mirror."""

import hashlib
import json
import shutil
from unittest.mock import MagicMock, patch

import pytest

from scripts import mirror_blob_images

pytestmark = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone not installed")


def _image(pathname: str, body: bytes, uploaded_at: str = "2026-10-28T00:00:00Z") -> dict:
    return {
        "pathname": pathname,
        "url": f"https://blob.test/{pathname}",
        "size": len(body),
        "uploadedAt": uploaded_at,
        "body": body,
    }


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "fixture-token")
    monkeypatch.setenv("RCLONE_CONFIG_TESTLOCAL_TYPE", "local")
    state_dir = tmp_path / "state"
    remote_root = tmp_path / "remote"
    remote = f"TestLocal:{remote_root}"
    return state_dir, remote, remote_root


def _response(record: dict):
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [record["body"]]
    return response


def _run(environment, images, **limits):
    state_dir, remote, _ = environment
    by_url = {image["url"]: image for image in images}
    with (
        patch.object(mirror_blob_images, "list_images", return_value=images),
        patch.object(
            mirror_blob_images.requests,
            "get",
            side_effect=lambda url, **_kwargs: _response(by_url[url]),
        ) as get,
    ):
        report = mirror_blob_images.mirror(state_dir, remote, **limits)
    return report, get


def test_bounded_runs_continue_without_redownloading_previous_objects(environment):
    images = [
        _image("images/large.png", b"large"),
        _image("images/small.png", b"x"),
    ]
    first, first_get = _run(environment, images, max_objects=1)
    assert first["transferred_objects"] == 1
    assert first["pending_objects"] == 1
    assert first["mirror_complete"] is False
    assert first_get.call_args.args[0].endswith("small.png")

    second, second_get = _run(environment, images, max_objects=1)
    assert second["transferred_objects"] == 1
    assert second["pending_objects"] == 0
    assert second["mirror_complete"] is True
    assert second_get.call_args.args[0].endswith("large.png")

    third, third_get = _run(environment, images, max_objects=1)
    assert third["transferred_objects"] == 0
    assert third["pending_objects"] == 0
    third_get.assert_not_called()


def test_changed_object_creates_a_new_immutable_body_and_updates_manifest(environment):
    original = _image("images/hero.png", b"old")
    _run(environment, [original])
    changed = _image("images/hero.png", b"new", "2026-10-29T00:00:00Z")
    report, get = _run(environment, [changed])
    assert report["transferred_objects"] == 1
    assert get.call_count == 1

    state_dir, _, remote_root = environment
    state = json.loads((state_dir / "state.json").read_text())
    entry = state["objects"]["images/hero.png"]
    assert entry["sha256"] == hashlib.sha256(b"new").hexdigest()
    assert (remote_root / entry["object"]).read_bytes() == b"new"
    assert (remote_root / f"objects/{hashlib.sha256(b'old').hexdigest()}").read_bytes() == b"old"


def test_restore_sample_verifies_the_offsite_body_without_blob_access(environment, tmp_path):
    image = _image("images/hero.webp", b"verified-image")
    _run(environment, [image])
    state_dir, remote, _ = environment
    restored = tmp_path / "restore" / "hero.webp"

    report = mirror_blob_images.restore_sample(state_dir, remote, image["pathname"], restored)

    assert report["status"] == "verified"
    assert restored.read_bytes() == image["body"]


def test_remote_manifest_recovers_lost_local_state_without_redownload(environment):
    image = _image("images/hero.webp", b"already-offsite")
    _run(environment, [image])
    state_dir, _, _ = environment
    (state_dir / "state.json").replace(state_dir / "lost-state.json")

    report, get = _run(environment, [image])

    assert report["transferred_objects"] == 0
    assert report["mirror_complete"] is True
    get.assert_not_called()


def test_separate_state_directory_continues_from_remote_progress(environment, tmp_path):
    images = [_image("images/a.webp", b"a"), _image("images/b.webp", b"bb")]
    _run(environment, images, max_objects=1)
    _, remote, remote_root = environment
    second_environment = (tmp_path / "other-state", remote, remote_root)

    report, get = _run(second_environment, images)

    assert report["transferred_objects"] == 1
    assert report["mirror_complete"] is True
    assert get.call_args.args[0].endswith("b.webp")


def test_failed_attempt_makes_status_unhealthy_until_recovery(environment):
    image = _image("images/hero.webp", b"body")
    _run(environment, [image])
    state_dir, remote, _ = environment
    assert mirror_blob_images.status(state_dir)["healthy"] is True

    with patch.object(
        mirror_blob_images,
        "list_images",
        side_effect=mirror_blob_images.MirrorError("listing failed"),
    ):
        with pytest.raises(mirror_blob_images.MirrorError, match="listing failed"):
            mirror_blob_images.mirror(state_dir, remote)
    failed = mirror_blob_images.status(state_dir)
    assert failed["healthy"] is False
    assert failed["last_attempt"]["status"] == "failed"

    _run(environment, [image])
    recovered = mirror_blob_images.status(state_dir)
    assert recovered["healthy"] is True
    assert recovered["last_attempt"]["status"] == "complete"


def test_remote_failure_does_not_advance_local_state(environment):
    state_dir, remote, _ = environment
    image = _image("images/hero.jpg", b"body")
    with (
        patch.object(mirror_blob_images, "list_images", return_value=[image]),
        patch.object(mirror_blob_images.requests, "get", return_value=_response(image)),
        patch.object(
            mirror_blob_images,
            "rclone",
            side_effect=["ok", mirror_blob_images.MirrorError("copy failed")],
        ),
    ):
        with pytest.raises(mirror_blob_images.MirrorError, match="copy failed"):
            mirror_blob_images.mirror(state_dir, remote)
    assert not (state_dir / "state.json").exists()


def test_short_blob_body_does_not_advance_local_state(environment):
    state_dir, remote, _ = environment
    image = _image("images/short.jpg", b"x")
    image["size"] = 2
    with (
        patch.object(mirror_blob_images, "list_images", return_value=[image]),
        patch.object(mirror_blob_images.requests, "get", return_value=_response(image)),
    ):
        with pytest.raises(mirror_blob_images.MirrorError, match="got 1 bytes, listed 2"):
            mirror_blob_images.mirror(state_dir, remote)
    assert not (state_dir / "state.json").exists()


def test_inventory_reports_only_aggregate_non_secret_fields():
    images = [_image("images/a.PNG", b"a"), _image("images/b.webp", b"bb")]
    assert mirror_blob_images.inventory(images) == {
        "objects": 2,
        "bytes": 3,
        "extensions": {"png": 1, "webp": 1},
    }
