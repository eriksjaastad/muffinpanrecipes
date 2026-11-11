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
RELEASE_ID = "release-123"


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


def test_deploy_sequence_health_precedes_promotion_and_proof(
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
        deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy()
    )
    monkeypatch.setattr(
        deploy_main, "_production_alias_deployment_id", lambda _host, _link: "old"
    )
    monkeypatch.setattr(
        deploy_main,
        "_stage_production",
        lambda worktree, sha: events.append(("stage", worktree, sha))
        or {"uid": "staged", "url": "https://staged.example"},
    )
    monkeypatch.setattr(
        deploy_main,
        "_health_check",
        lambda worktree, base_url=None: events.append(("health", worktree, base_url)),
    )
    monkeypatch.setattr(
        deploy_main,
        "_promote_staged_production",
        lambda deployment_id: events.append(("promote", deployment_id)),
    )
    monkeypatch.setattr(
        deploy_main,
        "_wait_for_production_identity",
        lambda url, deployment_id, sha: events.append(
            ("identity", url, deployment_id, sha)
        ),
    )

    assert deploy_main.main([]) == 0
    assert events == [
        ("worktree", SHA),
        ("stage", tmp_path, SHA),
        ("health", tmp_path, "https://staged.example"),
        ("promote", "staged"),
        ("identity", deploy_main.PRODUCTION_URL, "staged", SHA),
        ("health", tmp_path, None),
        ("identity", deploy_main.PRODUCTION_URL, "staged", SHA),
    ]
    assert f"production is main: gitSha {SHA}" in capsys.readouterr().out


def test_staged_build_cannot_move_live_alias_before_health(monkeypatch, tmp_path):
    events = []

    @contextmanager
    def fake_worktree(_sha):
        yield tmp_path

    aliases = iter(["old", "unexpected"])
    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_detached_origin_main", fake_worktree)
    monkeypatch.setattr(
        deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy()
    )
    monkeypatch.setattr(
        deploy_main, "_production_alias_deployment_id", lambda _host, _link: next(aliases)
    )
    monkeypatch.setattr(
        deploy_main,
        "_stage_production",
        lambda _worktree, _sha: {"uid": "staged", "url": "https://staged.example"},
    )
    monkeypatch.setattr(
        deploy_main,
        "_health_check",
        lambda *_args, **_kwargs: events.append("health"),
    )
    monkeypatch.setattr(
        deploy_main, "_promote_staged_production", lambda _url: events.append("promote")
    )

    assert deploy_main.main([]) == 1
    assert events == []


@pytest.mark.parametrize(
    ("health_base_url", "expected"),
    [
        (None, [deploy_main.sys.executable, "scripts/health_check.py", "--no-alert"]),
        (
            "https://preview.example",
            [
                deploy_main.sys.executable,
                "scripts/health_check.py",
                "--no-alert",
                "--base-url",
                "https://preview.example",
            ],
        ),
    ],
)
def test_deploy_health_gate_never_alerts_or_writes_monitor_state(
    monkeypatch, tmp_path, health_base_url, expected
):
    seen = {}
    monkeypatch.setattr(
        deploy_main,
        "_run",
        lambda command, *, cwd, timeout: seen.update(
            command=command, cwd=cwd, timeout=timeout
        ),
    )

    deploy_main._health_check(tmp_path, base_url=health_base_url)

    assert seen == {
        "command": expected,
        "cwd": tmp_path,
        "timeout": deploy_main.HEALTH_TIMEOUT_SECONDS,
    }


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


def test_staged_production_build_is_unaliased_and_pins_origin_sha(monkeypatch, tmp_path):
    seen = {}
    responses = iter(
        [
            {"deployments": []},
            {
                "deployments": [
                    {
                        "uid": "new",
                        "url": "staged.example",
                        "target": "production",
                        "state": "READY",
                        "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID},
                    }
                ]
            },
        ]
    )

    def fake_vercel(command):
        seen["command"] = command

    monkeypatch.setattr(deploy_main, "_vercel", fake_vercel)
    monkeypatch.setattr(deploy_main.uuid, "uuid4", lambda: type("U", (), {"hex": RELEASE_ID})())
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: next(responses),
    )

    staged = deploy_main._stage_production(tmp_path, SHA)
    assert staged["uid"] == "new"
    assert staged["url"] == "https://staged.example"
    assert seen["command"] == [
        "deploy",
        str(tmp_path),
        "--prod",
        "--skip-domain",
        "--yes",
        "--meta",
        f"gitSha={SHA}",
        "--meta",
        f"sagaRelease={RELEASE_ID}",
    ]


def test_promotion_targets_the_exact_verified_deployment(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_post",
        lambda path, params: seen.update(path=path, params=params),
    )

    deploy_main._promote_staged_production("dpl_staged")

    assert seen == {
        "path": (
            "/v10/projects/prj_zQgUD5QXAc1OHpgyzyTlbqxvN1Ww/"
            "promote/dpl_staged"
        ),
        "params": {"teamId": deploy_main.EXPECTED_PROJECT_LINK["orgId"]},
    }


def test_rest_deployment_lookups_pin_canonical_team(monkeypatch):
    calls = []
    responses = iter(
        [
            {"deployments": []},
            {
                "deployments": [
                    {
                        "uid": "new",
                        "url": "staged.example",
                        "target": "production",
                        "state": "READY",
                        "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID},
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
    monkeypatch.setattr(deploy_main.uuid, "uuid4", lambda: type("U", (), {"hex": RELEASE_ID})())
    monkeypatch.setattr(deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy())

    deploy_main._stage_production(Path("/tmp/source"), SHA)
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


def test_staged_build_excludes_old_same_sha_and_waits_for_ready(monkeypatch):
    responses = iter(
        [
            {
                "deployments": [
                    {"uid": "old", "url": "old.example", "target": "production", "state": "READY", "meta": {"gitSha": SHA}},
                    {"uid": "new", "url": "new.example", "target": "production", "state": "BUILDING", "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID}},
                ]
            },
            {
                "deployments": [
                    {"uid": "old", "url": "old.example", "target": "production", "state": "READY", "meta": {"gitSha": SHA}},
                    {"uid": "new", "url": "new.example", "target": "production", "state": "READY", "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID}},
                ]
            },
        ]
    )
    sleeps = []
    monkeypatch.setattr(deploy_main, "_vercel_api_json", lambda path, params: next(responses))
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    staged = deploy_main._staged_production_deployment("project", SHA, RELEASE_ID, {"old"})
    assert staged["uid"] == "new"
    assert staged["url"] == "https://new.example"
    assert sleeps == [10]


def test_staged_build_ready_immediately_needs_no_wait(monkeypatch):
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: {
            "deployments": [
                {"uid": "new", "url": "new.example", "target": "production", "state": "READY", "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID}}
            ]
        },
    )
    sleeps = []
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    staged = deploy_main._staged_production_deployment("project", SHA, RELEASE_ID, set())
    assert staged["uid"] == "new"
    assert staged["url"] == "https://new.example"
    assert sleeps == []


def test_staged_build_retry_exhaustion_is_bounded(monkeypatch):
    deployment = {
        "uid": "new",
        "url": "new.example",
        "target": "production",
        "state": "BUILDING",
        "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID},
    }
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda path, params: {"deployments": [deployment]},
    )
    sleeps = []
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    with pytest.raises(deploy_main.DeployError, match="after 3 retries"):
        deploy_main._staged_production_deployment("project", SHA, RELEASE_ID, set())
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


def test_exact_promoted_deployment_proof_waits_for_alias(monkeypatch):
    observed = iter(["old", "staged"])
    sleeps = []
    monkeypatch.setattr(
        deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy()
    )
    monkeypatch.setattr(
        deploy_main,
        "_production_alias_deployment_id",
        lambda _host, _link: next(observed),
    )
    monkeypatch.setattr(deploy_main.time, "sleep", sleeps.append)

    deploy_main._wait_for_production_deployment(
        deploy_main.PRODUCTION_URL, "staged"
    )

    assert sleeps == [10]


def test_deploy_fails_without_production_sha_proof_and_no_success_receipt(
    monkeypatch, tmp_path, capsys
):
    @contextmanager
    def fake_worktree(_sha):
        yield tmp_path

    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_detached_origin_main", fake_worktree)
    monkeypatch.setattr(
        deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy()
    )
    monkeypatch.setattr(
        deploy_main, "_production_alias_deployment_id", lambda _host, _link: "old"
    )
    monkeypatch.setattr(
        deploy_main,
        "_stage_production",
        lambda _worktree, _sha: {"uid": "staged", "url": "https://staged.example"},
    )
    monkeypatch.setattr(deploy_main, "_health_check", lambda _worktree, base_url=None: None)
    monkeypatch.setattr(deploy_main, "_promote_staged_production", lambda _url: None)
    monkeypatch.setattr(
        deploy_main,
        "_wait_for_production_identity",
        lambda _url, _deployment_id, _sha: (_ for _ in ()).throw(
            deploy_main.DeployError("production mismatch")
        ),
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

    def fake_proof(_url, _deployment_id, _sha):
        events.append("identity")

    monkeypatch.setattr(deploy_main, "_fetch_origin_main", lambda: SHA)
    monkeypatch.setattr(deploy_main, "_detached_origin_main", fake_worktree)
    monkeypatch.setattr(
        deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy()
    )
    monkeypatch.setattr(
        deploy_main, "_production_alias_deployment_id", lambda _host, _link: "old"
    )
    monkeypatch.setattr(
        deploy_main,
        "_stage_production",
        lambda _worktree, _sha: {"uid": "staged", "url": "https://staged.example"},
    )
    monkeypatch.setattr(deploy_main, "_promote_staged_production", lambda _url: None)
    monkeypatch.setattr(deploy_main, "_health_check", fake_health)
    monkeypatch.setattr(
        deploy_main,
        "_wait_for_production_identity",
        fake_proof,
    )

    assert deploy_main.main([]) == 1
    assert events == [
        ("health", "https://staged.example"),
        "identity",
        ("health", None),
    ]


def test_staged_discovery_rejects_concurrent_same_sha_release(monkeypatch):
    monkeypatch.setattr(
        deploy_main,
        "_vercel_api_json",
        lambda _path, _params: {
            "deployments": [
                {
                    "uid": "other",
                    "url": "other.example",
                    "target": "production",
                    "state": "READY",
                    "meta": {"gitSha": SHA, "sagaRelease": "other-release"},
                },
                {
                    "uid": "ours",
                    "url": "ours.example",
                    "target": "production",
                    "state": "READY",
                    "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID},
                },
            ]
        },
    )

    staged = deploy_main._staged_production_deployment(
        "project", SHA, RELEASE_ID, set()
    )

    assert staged["uid"] == "ours"


def test_production_identity_rejects_same_sha_replacement(monkeypatch):
    aliases = iter(["staged", "replacement", "replacement", "replacement", "replacement"])
    monkeypatch.setattr(
        deploy_main, "_read_project_link", lambda: deploy_main.EXPECTED_PROJECT_LINK.copy()
    )
    monkeypatch.setattr(
        deploy_main,
        "_production_alias_deployment_id",
        lambda _host, _link: next(aliases),
    )
    monkeypatch.setattr(
        deploy_main,
        "_deployment_list",
        lambda _project_id: [
            {
                "uid": "staged",
                "url": "staged.example",
                "aliases": (),
                "target": "production",
                "state": "READY",
                "gitSha": SHA,
                "releaseId": RELEASE_ID,
            },
            {
                "uid": "replacement",
                "url": "replacement.example",
                "aliases": (),
                "target": "production",
                "state": "READY",
                "gitSha": SHA,
                "releaseId": "other-release",
            },
        ],
    )
    monkeypatch.setattr(deploy_main.time, "sleep", lambda _delay: None)

    with pytest.raises(deploy_main.DeployError, match="production identity"):
        deploy_main._wait_for_production_identity(
            deploy_main.PRODUCTION_URL, "staged", SHA
        )


def test_rest_promotion_is_credential_safe(monkeypatch):
    class Response:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    seen = {}
    monkeypatch.setenv("VERCEL_API_TOKEN", "test-token")

    def fake_urlopen(request, timeout):
        seen.update(request=request, timeout=timeout)
        return Response()

    monkeypatch.setattr(deploy_main, "urlopen", fake_urlopen)

    deploy_main._vercel_api_post(
        "/v10/projects/project/promote/deployment", {"teamId": "team"}
    )

    request = seen["request"]
    assert request.full_url.endswith("?teamId=team")
    assert request.get_method() == "POST"
    assert request.data == b""
    assert request.get_header("Authorization") == "Bearer test-token"
    assert seen["timeout"] == 20


def test_rest_promotion_rejects_http_failure_without_body(monkeypatch):
    monkeypatch.setenv("VERCEL_API_TOKEN", "test-token")
    monkeypatch.setattr(
        deploy_main,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            deploy_main.HTTPError(
                "https://api.vercel.com", 409, "conflict", {}, None
            )
        ),
    )

    with pytest.raises(deploy_main.DeployError, match="HTTP 409") as exc_info:
        deploy_main._vercel_api_post("/promotion", {})

    assert "conflict" not in str(exc_info.value)
    assert "test-token" not in str(exc_info.value)


def test_rest_promotion_rejects_transport_failure(monkeypatch):
    monkeypatch.setenv("VERCEL_API_TOKEN", "test-token")
    monkeypatch.setattr(
        deploy_main,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("secret body")),
    )

    with pytest.raises(deploy_main.DeployError, match="TimeoutError") as exc_info:
        deploy_main._vercel_api_post("/promotion", {})

    assert "secret body" not in str(exc_info.value)
    assert "test-token" not in str(exc_info.value)


def test_rest_promotion_rejects_unexpected_success_status(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setenv("VERCEL_API_TOKEN", "test-token")
    monkeypatch.setattr(deploy_main, "urlopen", lambda *_args, **_kwargs: Response())

    with pytest.raises(deploy_main.DeployError, match="returned HTTP 200"):
        deploy_main._vercel_api_post("/promotion", {})


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
    payload = {"url": "preview.example", "meta": {"gitSha": SHA, "sagaRelease": RELEASE_ID, "secret": "ignored"}}
    assert deploy_main._deployment_projection(payload) == {
        "uid": None,
        "url": "preview.example",
        "aliases": (),
        "target": None,
        "state": None,
        "gitSha": SHA,
        "releaseId": RELEASE_ID,
    }
