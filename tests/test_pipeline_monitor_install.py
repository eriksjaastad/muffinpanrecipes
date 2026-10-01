"""scripts/install_pipeline_monitor.sh (#7006).

Exercises --dry-run, plus --uninstall against stub launchctl/trash binaries
and a temporary HOME: never the real launchctl, never the real
~/Library/LaunchAgents, never the real `trash`. Asserts the template carries
no machine-specific path (M1) and that --dry-run's rendered output has every
placeholder substituted.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "install_pipeline_monitor.sh"
TEMPLATE = (
    REPO_ROOT
    / "ops"
    / "launchd"
    / "com.eriksjaastad.muffinpan-pipeline-monitor.plist.template"
)

_HARDCODED_USER_PATH = re.compile(r"/Users/[^/{}\s]+")


def test_template_has_no_hardcoded_user_path():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert not _HARDCODED_USER_PATH.search(text), (
        "template must use {{HOME}}/{{REPO}} placeholders, not a literal /Users/<name> path"
    )


def test_template_declares_hourly_start_interval_and_run_at_load():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "<key>StartInterval</key>" in text
    assert "<integer>3600</integer>" in text
    assert "<key>RunAtLoad</key>" in text


def test_dry_run_install_renders_no_leftover_placeholders():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "{{" not in result.stdout
    assert "}}" not in result.stdout
    assert "<key>Label</key>" in result.stdout
    assert "com.eriksjaastad.muffinpan-pipeline-monitor" in result.stdout


def test_dry_run_install_does_not_call_launchctl_or_write_files(tmp_path, monkeypatch):
    # A fake `launchctl` ahead of the real one on PATH would get invoked if
    # --dry-run ever stopped being dry; its mere existence + a call log lets
    # us prove it never ran.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "launchctl_calls.log"
    fake_launchctl = fake_bin / "launchctl"
    fake_launchctl.write_text(
        f"#!/usr/bin/env bash\necho \"$@\" >> '{call_log}'\nexit 0\n"
    )
    fake_launchctl.chmod(0o755)

    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not call_log.exists(), "launchctl must not be invoked under --dry-run"


def test_dry_run_uninstall_does_not_call_trash_or_launchctl(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "calls.log"
    for name in ("launchctl", "trash"):
        fake = fake_bin / name
        fake.write_text(f"#!/usr/bin/env bash\necho \"{name} $@\" >> '{call_log}'\nexit 0\n")
        fake.chmod(0o755)

    env = {"PATH": f"{fake_bin}:/usr/bin:/bin", "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", str(SCRIPT), "--uninstall", "--dry-run"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert not call_log.exists(), "uninstall --dry-run must not touch launchctl or trash"
    assert "bootout" in result.stdout
    assert "trash" in result.stdout


def test_script_never_shells_out_to_rm():
    # Comment lines are allowed to mention `rm` in prose (explaining the
    # policy); only an actual command invocation is disallowed.
    code_lines = [
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    ]
    assert not any(re.search(r"\brm\b", line) for line in code_lines), (
        "install script must use `trash`, never `rm`"
    )


def _fake_bin(tmp_path: Path, bootout_rc: int, print_rc: int = 0) -> tuple[Path, Path]:
    """A PATH dir with stub `launchctl` and `trash` that log their calls.
    `print_rc` 0 means "the service is loaded"."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    (bin_dir / "launchctl").write_text(
        "#!/bin/bash\n"
        f'echo "launchctl $*" >> "{log}"\n'
        'if [[ "$1" == "bootout" ]]; then exit ' + str(bootout_rc) + "; fi\n"
        'if [[ "$1" == "print" ]]; then exit ' + str(print_rc) + "; fi\n"
        "exit 0\n"
    )
    (bin_dir / "trash").write_text(f'#!/bin/bash\necho "trash $*" >> "{log}"\n')
    for name in ("launchctl", "trash"):
        (bin_dir / name).chmod(0o755)
    return bin_dir, log


def _run_uninstall(tmp_path: Path, bootout_rc: int, print_rc: int = 0):
    import os

    home = tmp_path / "home"
    dest = home / "Library" / "LaunchAgents" / "com.eriksjaastad.muffinpan-pipeline-monitor.plist"
    dest.parent.mkdir(parents=True)
    dest.write_text("<plist/>")
    bin_dir, log = _fake_bin(tmp_path, bootout_rc, print_rc)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(SCRIPT), "--uninstall"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    calls = log.read_text() if log.exists() else ""
    return result, dest, calls


def test_uninstall_stops_and_keeps_the_plist_when_bootout_fails(tmp_path):
    """Gate review on b7065b2: `bootout || true` let uninstall trash the plist
    and report success while the hourly job stayed loaded."""
    result, dest, calls = _run_uninstall(tmp_path, bootout_rc=5)
    assert result.returncode == 1
    assert "Uninstall FAILED" in result.stderr
    assert dest.exists()
    assert "trash" not in calls


def test_uninstall_trashes_the_plist_after_a_successful_bootout(tmp_path):
    result, dest, calls = _run_uninstall(tmp_path, bootout_rc=0)
    assert result.returncode == 0, result.stderr
    assert "launchctl bootout" in calls
    assert f"trash {dest}" in calls


def test_uninstall_attempts_the_unload_even_when_the_query_fails(tmp_path):
    """Gate review on a010221: a failing `launchctl print` used to skip the
    bootout entirely, then trash the plist while the job stayed loaded."""
    result, dest, calls = _run_uninstall(tmp_path, bootout_rc=0, print_rc=1)
    assert result.returncode == 0, result.stderr
    assert "launchctl bootout gui/" in calls
    assert f"trash {dest}" in calls


def test_uninstall_proceeds_when_nothing_was_loaded(tmp_path):
    """bootout fails because there is no such service, and the follow-up
    query agrees: nothing to unload, so the plist is trashed."""
    result, dest, calls = _run_uninstall(tmp_path, bootout_rc=3, print_rc=113)
    assert result.returncode == 0, result.stderr
    assert f"trash {dest}" in calls


def test_uninstall_refuses_when_neither_bootout_nor_the_query_confirms(tmp_path):
    """Gate review on 904d5d4: bootout failed and the query failed for an
    operational reason (not launchctl's "no such service", exit 113)."""
    result, dest, calls = _run_uninstall(tmp_path, bootout_rc=5, print_rc=1)
    assert result.returncode == 1
    assert "not confirmed unloaded" in result.stderr
    assert dest.exists()
    assert "trash" not in calls


def _run_install_without_doppler_or_uv(tmp_path: Path, *args: str):
    import os

    home = tmp_path / "home"
    home.mkdir()
    bin_dir, log = _fake_bin(tmp_path, bootout_rc=0, print_rc=113)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"}
    result = subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=30,
    )
    return result, home, (log.read_text() if log.exists() else "")


def test_install_refuses_when_doppler_or_uv_is_missing(tmp_path):
    """Gate review on 904d5d4: a bare command name in the plist installs a
    job launchd can never run, while the script said "Installed"."""
    result, home, calls = _run_install_without_doppler_or_uv(tmp_path)
    assert result.returncode == 1
    assert "Install FAILED" in result.stderr
    assert not (home / "Library" / "LaunchAgents").exists()
    assert "bootstrap" not in calls


def test_dry_run_still_renders_and_warns_when_a_binary_is_missing(tmp_path):
    result, _home, calls = _run_install_without_doppler_or_uv(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "a real install would refuse" in result.stderr
    assert calls == ""
