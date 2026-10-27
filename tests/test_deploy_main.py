"""Deployment must be sourced from fetched origin/main and verified by SHA."""

from __future__ import annotations

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from scripts import deploy_main


SHA = "0123456789abcdef0123456789abcdef01234567"


def test_check_returns_zero_when_production_matches_origin_main(monkeypatch, capsys):
    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_production_git_sha", lambda _url: SHA)

    assert deploy_main.main(["--check"]) == 0
    assert SHA in capsys.readouterr().out


def test_check_returns_one_when_production_does_not_match_origin_main(monkeypatch, capsys):
    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_production_git_sha", lambda _url: "different")

    assert deploy_main.main(["--check"]) == 1
    assert "gitSha mismatch" in capsys.readouterr().err


def test_check_returns_one_when_production_metadata_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_production_git_sha", lambda _url: None)

    assert deploy_main.main(["--check"]) == 1
    assert "found missing" in capsys.readouterr().err


def test_deploy_sequence_health_precedes_production_deploy_and_proof(
    monkeypatch, tmp_path, capsys
):
    events = []

    @contextmanager
    def fake_worktree(sha):
        events.append(("worktree", sha))
        yield tmp_path

    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_detached_origin_main", fake_worktree)
    monkeypatch.setattr(
        deploy_main,
        "_deploy_preview",
        lambda worktree, sha: events.append(("deploy", worktree, sha)) or "https://preview.example",
    )
    monkeypatch.setattr(
        deploy_main,
        "_health_check",
        lambda worktree, base_url=None: events.append(("health", worktree, base_url)),
    )
    monkeypatch.setattr(
        deploy_main,
        "_deploy_production",
        lambda worktree, sha: events.append(("production_deploy", worktree, sha)),
    )
    monkeypatch.setattr(
        deploy_main,
        "_wait_for_production_sha",
        lambda url, sha: events.append(("proof", url, sha)),
    )

    assert deploy_main.main([]) == 0
    assert events == [
        ("worktree", SHA),
        ("deploy", tmp_path, SHA),
        ("health", tmp_path, "https://preview.example"),
        ("production_deploy", tmp_path, SHA),
        ("proof", deploy_main.PRODUCTION_URL, SHA),
        ("health", tmp_path, None),
        ("proof", deploy_main.PRODUCTION_URL, SHA),
    ]
    assert f"production is main: gitSha {SHA}" in capsys.readouterr().out


def test_detached_worktree_is_created_at_fetched_sha(monkeypatch, tmp_path):
    commands = []
    parent = tmp_path / "deploy"
    parent.mkdir()
    monkeypatch.setattr(deploy_main.tempfile, "mkdtemp", lambda prefix: str(parent))
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    monkeypatch.setattr(deploy_main, "_copy_project_link", lambda *args: None)
    monkeypatch.setattr(deploy_main, "_cleanup_temp_worktree", lambda *args: None)

    def fake_git(command, *, cwd=deploy_main.ROOT):
        commands.append((command, cwd))
        if command == ["rev-parse", "HEAD"]:
            return SHA
        if command == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "HEAD"
        if command == ["status", "--porcelain"]:
            return ""
        return ""

    monkeypatch.setattr(deploy_main, "_git", fake_git)

    with deploy_main._detached_origin_main(SHA) as worktree:
        assert worktree == parent / "source"

    assert commands[0] == (
        ["worktree", "add", "--detach", str(parent / "source"), SHA],
        deploy_main.ROOT,
    )
    assert commands[1:] == [
        (["rev-parse", "HEAD"], parent / "source"),
        (["rev-parse", "--abbrev-ref", "HEAD"], parent / "source"),
        (["status", "--porcelain"], parent / "source"),
    ]


def test_preview_deploy_passes_origin_sha_as_gitsha_metadata(monkeypatch, tmp_path):
    seen = {}
    responses = iter(
        [
            {"deployments": []},
            {
                "deployments": [
                    {
                        "uid": "new",
                        "url": "preview.example",
                        "target": "preview",
                        "state": "READY",
                        "meta": {"gitSha": SHA},
                    }
                ]
            },
        ]
    )

    def fake_vercel(command):
        seen["command"] = command

    monkeypatch.setattr(deploy_main, "_vercel", fake_vercel)
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: next(responses),
    )

    assert deploy_main._deploy_preview(tmp_path, SHA) == "https://preview.example"
    assert seen["command"] == [
        "deploy",
        str(tmp_path),
        "--target=preview",
        "--yes",
        "--meta",
        f"gitSha={SHA}",
    ]


def test_production_deploy_uses_same_worktree_and_sha(monkeypatch, tmp_path):
    seen = {}

    monkeypatch.setattr(deploy_main, "_vercel", lambda command: seen.update(command=command))

    deploy_main._deploy_production(tmp_path, SHA)

    assert seen["command"] == [
        "deploy",
        str(tmp_path),
        "--prod",
        "--yes",
        "--meta",
        f"gitSha={SHA}",
    ]


def test_rest_deployment_lookups_pin_canonical_team(monkeypatch):
    calls = []
    responses = iter(
        [
            {"deployments": []},
            {
                "deployments": [
                    {
                        "uid": "new",
                        "url": "preview.example",
                        "state": "READY",
                        "meta": {"gitSha": SHA},
                    }
                ]
            },
            {
                "projectId": deploy_main.EXPECTED_PROJECT_LINK["projectId"],
                "deploymentId": "production",
            },
            {"deployments": [{"uid": "production", "meta": {"gitSha": SHA}}]},
        ]
    )
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: calls.append((path, params)) or next(responses),
    )
    monkeypatch.setattr(deploy_main, "_vercel", lambda _command: None)
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())

    deploy_main._deploy_preview(Path("/tmp/source"), SHA)
    deploy_main._production_git_sha(deploy_main.PRODUCTION_URL)

    assert calls[0][1]["projectId"] == deploy_main.EXPECTED_PROJECT_LINK["projectId"]
    assert calls[0][1]["teamId"] == deploy_main.EXPECTED_PROJECT_LINK["orgId"]
    assert calls[1][1]["projectId"] == deploy_main.EXPECTED_PROJECT_LINK["projectId"]
    assert calls[1][1]["teamId"] == deploy_main.EXPECTED_PROJECT_LINK["orgId"]
    assert calls[2] == (
        "/v4/aliases/muffinpanrecipes.com",
        {"teamId": deploy_main.EXPECTED_PROJECT_LINK["orgId"]},
    )
    assert calls[3][1]["projectId"] == deploy_main.EXPECTED_PROJECT_LINK["projectId"]
    assert calls[3][1]["teamId"] == deploy_main.EXPECTED_PROJECT_LINK["orgId"]


def test_preview_excludes_old_same_sha_and_waits_for_ready(monkeypatch):
    responses = iter(
        [
            {
                "deployments": [
                    {"uid": "old", "url": "old.example", "state": "READY", "meta": {"gitSha": SHA}},
                    {"uid": "new", "url": "new.example", "state": "BUILDING", "meta": {"gitSha": SHA}},
                ]
            },
            {
                "deployments": [
                    {"uid": "old", "url": "old.example", "state": "READY", "meta": {"gitSha": SHA}},
                    {"uid": "new", "url": "new.example", "state": "READY", "meta": {"gitSha": SHA}},
                ]
            },
        ]
    )
    sleeps = []
    monkeypatch.setattr(deploy_main, "_vercel_api_json", lambda path, params: next(responses))
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    assert deploy_main._preview_deployment_url("project", SHA, {"old"}) == "https://new.example"
    assert sleeps == [10]


def test_preview_ready_immediately_needs_no_wait(monkeypatch):
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: {
            "deployments": [
                {"uid": "new", "url": "new.example", "state": "READY", "meta": {"gitSha": SHA}}
            ]
        },
    )
    sleeps = []
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    assert deploy_main._preview_deployment_url("project", SHA, set()) == "https://new.example"
    assert sleeps == []


def test_preview_retry_exhaustion_is_bounded(monkeypatch):
    deployment = {
        "uid": "new",
        "url": "new.example",
        "state": "BUILDING",
        "meta": {"gitSha": SHA},
    }
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: {"deployments": [deployment]},
    )
    sleeps = []
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    with pytest.raises(deploy_main.DeployError, match="after 3 retries"):
        deploy_main._preview_deployment_url("project", SHA, set())
    assert sleeps == [10, 20, 40]


def test_production_sha_proof_waits_for_delayed_convergence(monkeypatch):
    observed = iter([None, "different", SHA])
    sleeps = []
    monkeypatch.setattr(deploy_main, "_production_git_sha", lambda _url: next(observed))
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    deploy_main._wait_for_production_sha(deploy_main.PRODUCTION_URL, SHA)

    assert sleeps == [10, 20]


def test_production_sha_proof_ready_immediately_needs_no_wait(monkeypatch):
    sleeps = []
    monkeypatch.setattr(deploy_main, "_production_git_sha", lambda _url: SHA)
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    deploy_main._wait_for_production_sha(deploy_main.PRODUCTION_URL, SHA)

    assert sleeps == []


def test_production_sha_proof_times_out_after_three_retries(monkeypatch):
    sleeps = []
    monkeypatch.setattr(deploy_main, "_production_git_sha", lambda _url: None)
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    with pytest.raises(deploy_main.DeployError, match="after 3 retries"):
        deploy_main._wait_for_production_sha(deploy_main.PRODUCTION_URL, SHA)

    assert sleeps == [10, 20, 40]


def test_deploy_fails_without_production_sha_proof_and_no_success_receipt(
    monkeypatch, tmp_path, capsys
):
    @contextmanager
    def fake_worktree(_sha):
        yield tmp_path

    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_detached_origin_main", fake_worktree)
    monkeypatch.setattr(deploy_main, "_deploy_preview", lambda _worktree, _sha: "https://preview.example")
    monkeypatch.setattr(deploy_main, "_health_check", lambda _worktree, base_url=None: None)
    monkeypatch.setattr(deploy_main, "_deploy_production", lambda _worktree, _sha: None)
    monkeypatch.setattr(
        deploy_main,
        "_wait_for_production_sha",
        lambda _url, _sha: (_ for _ in ()).throw(deploy_main.DeployError("production mismatch")),
    )

    assert deploy_main.main([]) == 1
    output = capsys.readouterr()
    assert "production is main" not in output.out
    assert "production mismatch" in output.err


def test_unhealthy_new_production_stops_before_final_provenance(monkeypatch, tmp_path):
    events = []

    @contextmanager
    def fake_worktree(_sha):
        yield tmp_path

    def fake_health(_worktree, base_url=None):
        events.append(("health", base_url))
        if base_url is None:
            raise deploy_main.DeployError("new production is unhealthy")

    def fake_proof(_url, _sha):
        events.append("proof")

    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_detached_origin_main", fake_worktree)
    monkeypatch.setattr(deploy_main, "_deploy_preview", lambda _worktree, _sha: "https://preview.example")
    monkeypatch.setattr(deploy_main, "_deploy_production", lambda _worktree, _sha: None)
    monkeypatch.setattr(deploy_main, "_health_check", fake_health)
    monkeypatch.setattr(deploy_main, "_wait_for_production_sha", fake_proof)

    assert deploy_main.main([]) == 1
    assert events == [("health", "https://preview.example"), "proof", ("health", None)]


def test_vercel_commands_use_workspace_safe_wrapper(monkeypatch, tmp_path):
    wrapper = tmp_path / "vercel-safe-command.sh"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(deploy_main, "VERCEL_SAFE_COMMAND", wrapper)
    monkeypatch.setattr(
        deploy_main,
        "_run",
        lambda command, *, cwd, timeout: seen.update(command=command, cwd=cwd, timeout=timeout),
    )

    deploy_main._deploy_production(tmp_path, SHA)

    assert seen == {
        "command": [
            str(wrapper),
            "deploy",
            str(tmp_path),
            "--prod",
            "--yes",
            "--meta",
            f"gitSha={SHA}",
        ],
        "cwd": deploy_main.ROOT,
        "timeout": deploy_main.VERCEL_TIMEOUT_SECONDS,
    }


def test_environment_project_binding_allows_absent_or_matching_and_rejects_conflicts(monkeypatch):
    monkeypatch.delenv("VERCEL_PROJECT_ID", raising=False)
    monkeypatch.delenv("VERCEL_ORG_ID", raising=False)
    deploy_main._validate_environment_binding()

    monkeypatch.setenv("VERCEL_PROJECT_ID", deploy_main.EXPECTED_PROJECT_LINK["projectId"])
    monkeypatch.setenv("VERCEL_ORG_ID", deploy_main.EXPECTED_PROJECT_LINK["orgId"])
    deploy_main._validate_environment_binding()

    monkeypatch.setenv("VERCEL_PROJECT_ID", "other-project")
    with pytest.raises(deploy_main.DeployError, match="conflicts"):
        deploy_main._validate_environment_binding()

    monkeypatch.setenv("VERCEL_PROJECT_ID", deploy_main.EXPECTED_PROJECT_LINK["projectId"])
    monkeypatch.delenv("VERCEL_ORG_ID")
    with pytest.raises(deploy_main.DeployError, match="provided together"):
        deploy_main._validate_environment_binding()


def test_run_reports_operation_timeout(monkeypatch, tmp_path):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(kwargs.get("args", args[0]), kwargs["timeout"])

    monkeypatch.setattr(deploy_main.subprocess, "run", fake_run)
    with pytest.raises(deploy_main.DeployError, match="timed out after 7s"):
        deploy_main._run(["hung-command"], cwd=tmp_path, timeout=7)


def test_project_link_must_exist_and_match_expected_project(tmp_path):
    with pytest.raises(deploy_main.DeployError, match="missing"):
        deploy_main._read_project_link(tmp_path)

    project_path = tmp_path / ".vercel" / "project.json"
    project_path.parent.mkdir()
    project_path.write_text("not json", encoding="utf-8")
    with pytest.raises(deploy_main.DeployError, match="malformed"):
        deploy_main._read_project_link(tmp_path)

    project_path.write_text(json.dumps({"projectId": "another-project"}), encoding="utf-8")
    with pytest.raises(deploy_main.DeployError, match="does not match"):
        deploy_main._read_project_link(tmp_path)


def test_project_link_is_copied_only_after_validation(tmp_path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    deploy_main._copy_project_link(worktree, deploy_main.EXPECTED_PROJECT_LINK)

    project_bytes = (worktree / ".vercel" / "project.json").read_text(encoding="utf-8")
    copied = json.loads(project_bytes)
    assert copied == deploy_main.EXPECTED_PROJECT_LINK
    assert project_bytes == json.dumps(deploy_main.EXPECTED_PROJECT_LINK, sort_keys=True) + "\n"

    with pytest.raises(deploy_main.DeployError, match="unvalidated"):
        deploy_main._copy_project_link(tmp_path / "other", {"projectId": "wrong"})


def test_cleanup_unregisters_then_uses_recoverable_trash(monkeypatch, tmp_path):
    parent = tmp_path / "deploy"
    worktree = parent / "source"
    worktree.mkdir(parents=True)
    payload = worktree / "payload.txt"
    payload.write_text("recoverable", encoding="utf-8")
    calls = []

    def fake_run(command, *, cwd, capture_output, text, check, timeout):
        calls.append(command)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(deploy_main.subprocess, "run", fake_run)
    deploy_main._cleanup_temp_worktree(worktree, parent)

    assert calls == [
        ["trash", str(parent)],
        ["git", "worktree", "prune"],
    ]
    assert payload.read_text(encoding="utf-8") == "recoverable"


@pytest.mark.parametrize(
    "alias_payload",
    [
        {"projectId": "project", "deploymentId": "production"},
        {"projectId": "project", "deployment": {"id": "production"}},
    ],
)
def test_production_sha_uses_matching_production_alias(monkeypatch, alias_payload):
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    alias_payload["projectId"] = deploy_main.EXPECTED_PROJECT_LINK["projectId"]
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: alias_payload
        if path.startswith("/v4/aliases/")
        else {"deployments": [{"uid": "production", "meta": {"gitSha": SHA}}]},
    )

    assert deploy_main._production_git_sha("https://muffinpanrecipes.com") == SHA


def test_production_sha_rejects_alias_for_another_project(monkeypatch):
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda _path, _params: {"projectId": "another-project", "deploymentId": "production"},
    )

    with pytest.raises(deploy_main.DeployError, match="project does not match"):
        deploy_main._production_git_sha("https://muffinpanrecipes.com")


def test_production_sha_rejects_malformed_alias(monkeypatch):
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda _path, _params: {"projectId": deploy_main.EXPECTED_PROJECT_LINK["projectId"]},
    )

    with pytest.raises(deploy_main.DeployError, match="alias response was malformed"):
        deploy_main._production_git_sha("https://muffinpanrecipes.com")


def test_production_sha_returns_none_when_alias_deployment_is_missing(monkeypatch):
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    responses = iter(
        [
            {"projectId": deploy_main.EXPECTED_PROJECT_LINK["projectId"], "deploymentId": "missing"},
            {"deployments": [{"uid": "other", "meta": {"gitSha": SHA}}]},
        ]
    )
    monkeypatch.setattr(deploy_main, "_vercel_api_json", lambda _path, _params: next(responses))

    assert deploy_main._production_git_sha("https://muffinpanrecipes.com") is None


def test_production_sha_returns_none_when_matching_deployment_has_no_gitsha(monkeypatch):
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    responses = iter(
        [
            {"projectId": deploy_main.EXPECTED_PROJECT_LINK["projectId"], "deploymentId": "production"},
            {"deployments": [{"uid": "production", "meta": {}}]},
        ]
    )
    monkeypatch.setattr(deploy_main, "_vercel_api_json", lambda _path, _params: next(responses))

    assert deploy_main._production_git_sha("https://muffinpanrecipes.com") is None


def test_deployment_projection_allowlists_gitsha_metadata():
    payload = {"url": "preview.example", "meta": {"gitSha": SHA, "secret": "ignored"}}
    assert deploy_main._deployment_projection(payload) == {
        "uid": None,
        "url": "preview.example",
        "aliases": (),
        "target": None,
        "state": None,
        "gitSha": SHA,
    }
