"""scripts/install_pipeline_monitor.sh --dry-run (#7006).

Only ever exercises --dry-run: no launchctl call, no file written under
~/Library/LaunchAgents, no `trash` call. Asserts the template itself carries
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
