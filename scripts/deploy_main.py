#!/usr/bin/env python3
"""Deploy the fetched ``origin/main`` commit through preview to production.

The command never deploys the caller's checkout. It fetches ``origin/main``,
creates a clean detached worktree at that commit, copies the validated source
project link into the ignored worktree, deploys that directory through the
workspace Vercel-safe wrapper with ``gitSha`` metadata, runs the preview health
check, promotes the preview, runs the production health check, and proves the
production alias resolves to the fetched SHA.

Vercel mutations use ``~/.openclaw/workspace/ops/vercel-safe-command.sh`` so
raw CLI output cannot expose an operations token. Deployment URL discovery and
production SHA verification use allowlisted Vercel REST fields instead.

Commands (run with Vercel/Doppler credentials already injected)::

    doppler run -- uv run python scripts/deploy_main.py
    doppler run -- uv run python scripts/deploy_main.py --check

``--check`` only compares production's Vercel ``gitSha`` metadata with the
freshly fetched ``origin/main`` SHA. It exits non-zero when metadata is absent
or different. The source checkout may be dirty; only the temporary detached
worktree is eligible for deployment.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_URL = "https://muffinpanrecipes.com"
GIT_SHA_META_KEY = "gitSha"
VERCEL_API_BASE = "https://api.vercel.com"
VERCEL_SAFE_COMMAND = Path.home() / ".openclaw/workspace/ops/vercel-safe-command.sh"
MAX_PROVIDER_RETRIES = 3
EXPECTED_PROJECT_LINK = {
    "projectId": "prj_zQgUD5QXAc1OHpgyzyTlbqxvN1Ww",
    "orgId": "team_PJUqNMkyhhctGVPOgpjU8F3L",
    "projectName": "muffinpanrecipes",
}


class DeployError(RuntimeError):
    """A deploy prerequisite or command failed."""


def _run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one command and raise with its captured, non-secret output."""
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = (result.stderr or result.stdout or "command failed").strip()
        raise DeployError(f"{' '.join(command)}: {detail}")
    return result


def _git(command: list[str], *, cwd: Path = ROOT) -> str:
    return _run(["git", *command], cwd=cwd).stdout.strip()


def _vercel(command: list[str]) -> None:
    """Run a Vercel mutation through the credential-safe workspace wrapper."""
    if not VERCEL_SAFE_COMMAND.is_file():
        raise DeployError(f"Vercel safe command wrapper is missing: {VERCEL_SAFE_COMMAND}")
    _run([str(VERCEL_SAFE_COMMAND), *command], cwd=ROOT)


def _vercel_api_json(path: str, params: dict[str, str]) -> dict[str, Any]:
    """Read a Vercel REST response without exposing its credential or payload."""
    token = os.environ.get("VERCEL_API_TOKEN")
    if not token:
        raise DeployError("VERCEL_API_TOKEN is required for Vercel REST verification")
    url = f"{VERCEL_API_BASE}{path}?{urlencode(params)}"
    request = Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise DeployError(f"Vercel REST request failed with HTTP {exc.code}") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise DeployError(f"Vercel REST request failed: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise DeployError("Vercel REST response was not an object")
    return payload


def _read_project_link(root: Path = ROOT) -> dict[str, str]:
    """Return the exact, expected Vercel project binding from ``root``."""
    project_path = root / ".vercel" / "project.json"
    if project_path.is_symlink() or not project_path.is_file():
        raise DeployError(f"validated Vercel project link is missing: {project_path}")
    try:
        payload = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeployError(f"Vercel project link is malformed: {project_path}") from exc
    if not isinstance(payload, dict) or any(
        payload.get(key) != value for key, value in EXPECTED_PROJECT_LINK.items()
    ):
        raise DeployError("Vercel project link does not match muffinpanrecipes")
    return EXPECTED_PROJECT_LINK.copy()


def _copy_project_link(
    worktree: Path, link: dict[str, str]
) -> None:
    """Write only the validated ignored project link into a fresh worktree."""
    if link != EXPECTED_PROJECT_LINK:
        raise DeployError("refusing to copy an unvalidated Vercel project link")
    target_dir = worktree / ".vercel"
    if target_dir.exists() or target_dir.is_symlink():
        raise DeployError("deploy worktree already contains a Vercel project link")
    target_dir.mkdir(mode=0o700)
    (target_dir / "project.json").write_text(
        json.dumps(link, sort_keys=True) + "\n", encoding="utf-8"
    )


def _fetch_origin_main() -> str:
    _git(["fetch", "origin", "main"])
    sha = _git(["rev-parse", "origin/main"])
    if not sha:
        raise DeployError("origin/main resolved to an empty SHA")
    return sha


def _assert_clean_detached(worktree: Path, expected_sha: str) -> None:
    actual_sha = _git(["rev-parse", "HEAD"], cwd=worktree)
    if actual_sha != expected_sha:
        raise DeployError(f"detached worktree is at {actual_sha}, expected {expected_sha}")
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree)
    if branch != "HEAD":
        raise DeployError(f"deploy worktree is not detached (branch: {branch})")
    if _git(["status", "--porcelain"], cwd=worktree):
        raise DeployError("deploy worktree is not clean")


@contextmanager
def _detached_origin_main(sha: str) -> Iterator[Path]:
    """Yield a clean temporary worktree at ``sha`` and always unregister it."""
    project_link = _read_project_link()
    parent = Path(tempfile.mkdtemp(prefix="muffinpan-deploy-"))
    worktree = parent / "source"
    try:
        _git(["worktree", "add", "--detach", str(worktree), sha])
        _assert_clean_detached(worktree, sha)
        _copy_project_link(worktree, project_link)
        yield worktree
    finally:
        _cleanup_temp_worktree(worktree, parent)


def _cleanup_temp_worktree(worktree: Path, parent: Path) -> None:
    """Unregister the worktree and recoverably trash its temporary parent."""
    if worktree.exists():
        try:
            result = subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            print("WARNING: could not unregister temporary deploy worktree", file=sys.stderr)
        else:
            if result.returncode:
                print("WARNING: could not unregister temporary deploy worktree", file=sys.stderr)
    if parent.exists():
        try:
            result = subprocess.run(
                ["trash", str(parent)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            print("WARNING: could not trash temporary deploy directory", file=sys.stderr)
        else:
            if result.returncode:
                print("WARNING: could not trash temporary deploy directory", file=sys.stderr)


def _absolute_url(value: str) -> str:
    value = value.strip()
    return value if value.startswith(("http://", "https://")) else f"https://{value}"


def _deployment_projection(value: Any) -> dict[str, Any] | None:
    """Keep only URL, aliases, target, and gitSha from a deployment object."""
    if not isinstance(value, dict):
        return None
    metadata = value.get("meta")
    git_sha = metadata.get(GIT_SHA_META_KEY) if isinstance(metadata, dict) else None
    aliases = value.get("alias")
    if isinstance(aliases, str):
        aliases = [aliases]
    if not isinstance(aliases, list):
        aliases = []
    return {
        "uid": value.get("uid") if isinstance(value.get("uid"), str) else None,
        "url": value.get("url") if isinstance(value.get("url"), str) else None,
        "aliases": tuple(item for item in aliases if isinstance(item, str)),
        "target": value.get("target") if isinstance(value.get("target"), str) else None,
        "state": value.get("state") if isinstance(value.get("state"), str) else None,
        "gitSha": git_sha if isinstance(git_sha, str) else None,
    }


def _preview_deployment_list(project_id: str) -> list[dict[str, Any]]:
    payload = _vercel_api_json(
        "/v6/deployments", {"projectId": project_id, "limit": "100"}
    )
    deployments = payload.get("deployments")
    if not isinstance(deployments, list):
        raise DeployError("Vercel deployment list was malformed")
    return [projection for item in deployments if (projection := _deployment_projection(item))]


def _preview_deployment_url(project_id: str, sha: str, before_ids: set[str]) -> str:
    """Wait for a new READY preview with the requested SHA."""
    last_state = "not visible"
    for attempt in range(MAX_PROVIDER_RETRIES + 1):
        if attempt:
            time.sleep(2 ** (attempt - 1))
        for deployment in _preview_deployment_list(project_id):
            deployment_id = deployment["uid"]
            if (
                deployment_id
                and deployment_id not in before_ids
                and deployment["gitSha"] == sha
                and deployment["target"] != "production"
            ):
                last_state = deployment["state"] or "unknown"
                if deployment["state"] == "READY" and deployment["url"]:
                    return _absolute_url(deployment["url"])
    raise DeployError(
        f"new Vercel preview for gitSha was not READY after {MAX_PROVIDER_RETRIES} retries "
        f"(last state: {last_state})"
    )


def _deploy_preview(worktree: Path, sha: str) -> str:
    before_ids = {
        deployment["uid"]
        for deployment in _preview_deployment_list(EXPECTED_PROJECT_LINK["projectId"])
        if deployment["uid"]
    }
    _vercel(
        [
            "deploy",
            str(worktree),
            "--target=preview",
            "--yes",
            "--meta",
            f"{GIT_SHA_META_KEY}={sha}",
        ]
    )
    return _preview_deployment_url(EXPECTED_PROJECT_LINK["projectId"], sha, before_ids)


def _health_check(worktree: Path, *, base_url: str | None = None) -> None:
    command = [sys.executable, "scripts/health_check.py"]
    if base_url:
        command.extend(["--base-url", base_url])
    _run(command, cwd=worktree)


def _promote(preview_url: str) -> None:
    _vercel(["promote", preview_url, "--yes"])


def _host(value: str) -> str | None:
    parsed = urlparse(_absolute_url(value))
    return parsed.hostname


def _production_git_sha(production_url: str) -> str | None:
    project_link = _read_project_link()
    production_host = _host(production_url)
    if not production_host:
        raise DeployError("production URL has no hostname")
    payload = _vercel_api_json(
        "/v6/deployments",
        {"projectId": project_link["projectId"], "target": "production", "limit": "100"},
    )
    deployments = payload.get("deployments")
    if not isinstance(deployments, list):
        raise DeployError("Vercel deployment list was malformed")
    for item in deployments:
        projection = _deployment_projection(item)
        if not projection:
            continue
        hosts = {_host(alias) for alias in projection["aliases"]}
        if production_host in hosts or production_host == _host(projection["url"] or ""):
            return projection["gitSha"]
    return None


def _wait_for_production_sha(production_url: str, expected_sha: str) -> None:
    """Wait for production's alias to resolve to the fetched SHA."""
    observed = None
    for attempt in range(MAX_PROVIDER_RETRIES + 1):
        if attempt:
            time.sleep(2 ** (attempt - 1))
        observed = _production_git_sha(production_url)
        if observed == expected_sha:
            return
    raise DeployError(
        f"production did not converge to gitSha {expected_sha} after "
        f"{MAX_PROVIDER_RETRIES} retries (found {observed or 'missing'})"
    )


def _check(production_url: str) -> int:
    expected = _fetch_origin_main()
    observed = _production_git_sha(production_url)
    if observed != expected:
        shown = observed or "missing"
        print(f"Production gitSha mismatch: expected {expected}, found {shown}", file=sys.stderr)
        return 1
    print(f"Production matches origin/main at gitSha {expected}")
    return 0


def _deploy() -> int:
    sha = _fetch_origin_main()
    with _detached_origin_main(sha) as worktree:
        preview_url = _deploy_preview(worktree, sha)
        print(f"Preview deployed from origin/main {sha}: {preview_url}")
        _health_check(worktree, base_url=preview_url)
        print("Preview health check passed")
        _promote(preview_url)
        print("Preview promoted to production")
        _health_check(worktree)
        print("Production health check passed")
        _wait_for_production_sha(PRODUCTION_URL, sha)
        print(f"production is main: gitSha {sha}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare production gitSha metadata with freshly fetched origin/main; do not deploy.",
    )
    parser.add_argument(
        "--production-url",
        default=PRODUCTION_URL,
        help=f"Production deployment URL to inspect (default: {PRODUCTION_URL}).",
    )
    args = parser.parse_args(argv)
    try:
        return _check(args.production_url) if args.check else _deploy()
    except DeployError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
