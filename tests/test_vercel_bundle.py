"""Every scripts/ module backend/ imports must survive .vercelignore (#6855).

`.vercelignore` excludes `scripts/*` wholesale and re-includes named files.
A `from scripts.X import ...` inside backend/ therefore works locally, works
in every test, and raises ModuleNotFoundError only inside the Lambda — the
one place nobody is watching.

That is exactly how `scripts/pick_concept.py` went missing: concept selection
raised on every production Monday from 2026-W30 to 2026-W35 and the handler's
`except Exception` turned it into a placeholder concept with the catalog-aware
duplicate check switched off.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERCELIGNORE = ROOT / ".vercelignore"
BACKEND = ROOT / "backend"

_IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+scripts\.([A-Za-z_][A-Za-z0-9_]*)", re.M)


def _runtime_script_modules() -> dict[str, list[str]]:
    """Map scripts/<module>.py -> the backend files that import it."""
    found: dict[str, list[str]] = {}
    for path in BACKEND.rglob("*.py"):
        for module in _IMPORT_RE.findall(path.read_text(encoding="utf-8")):
            found.setdefault(module, []).append(str(path.relative_to(ROOT)))
    return found


def _reincluded_scripts() -> set[str]:
    lines = VERCELIGNORE.read_text(encoding="utf-8").splitlines()
    return {
        line.strip()[len("!scripts/"):]
        for line in lines
        if line.strip().startswith("!scripts/")
    }


def test_backend_still_imports_from_scripts() -> None:
    """Guard the guard: if this ever empties out, the test below is vacuous."""
    assert _runtime_script_modules(), (
        "no `from scripts.X import` found under backend/ — either the imports "
        "moved (good, delete this file) or the detection regex broke"
    )


def test_every_runtime_script_is_reincluded_in_vercelignore() -> None:
    reincluded = _reincluded_scripts()
    missing = {
        module: importers
        for module, importers in _runtime_script_modules().items()
        if f"{module}.py" not in reincluded
    }
    assert not missing, (
        "these scripts/ modules are imported by backend code but excluded "
        "from the Vercel bundle, so the import will raise in production "
        "only:\n"
        + "\n".join(
            f"  scripts/{m}.py  (imported by {', '.join(f)}) "
            f"-> add `!scripts/{m}.py` to .vercelignore"
            for m, f in sorted(missing.items())
        )
    )


def test_reincluded_scripts_actually_exist() -> None:
    """A re-include pointing at a deleted file is a silent no-op."""
    missing = [
        name for name in _reincluded_scripts() if not (ROOT / "scripts" / name).exists()
    ]
    assert not missing, f"stale .vercelignore re-includes: {sorted(missing)}"


def test_pick_concept_is_importable_the_way_the_lambda_imports_it() -> None:
    """The exact import line in cron_routes._pick_weekly_concept."""
    from scripts.pick_concept import pick_concept, pick_target_category  # noqa: F401


# --------------------------------------------------------------------------
# The inverse invariant (#6868).
#
# The tests above stop a needed file being EXCLUDED. These stop junk being
# INCLUDED. Both failures are invisible locally: `.vercelignore` governs what
# the CLI uploads, and nothing in the repo or the test suite reflects it.
#
# Measured 2026-09-05 before the fix: the upload set was 613 MB and the
# function bundle reached 277.52 MB against a 225 MB cap. `.scratch/` alone
# contributed 320 MB. That directory also matters for a non-size reason — the
# locked hygiene contract promises files there "never reach a PR", which is a
# statement about git and says nothing about a third-party upload.
# --------------------------------------------------------------------------

DEV_ONLY_DIRECTORIES = (
    ".scratch",
    ".venv",
    "venv",
    ".vercel",
    "node_modules",
    ".hypothesis",
    ".pytest_cache",
    ".ruff_cache",
)


def _ignored_directories() -> set[str]:
    """Directory patterns .vercelignore excludes, normalised to a bare name."""
    ignored: set[str] = set()
    for raw in VERCELIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        ignored.add(line.rstrip("/").lstrip("/"))
    return ignored


def test_local_dev_artifacts_are_excluded_from_the_upload() -> None:
    """None of these belong in a Lambda, and one of them is a privacy problem."""
    ignored = _ignored_directories()
    missing = [name for name in DEV_ONLY_DIRECTORIES if name not in ignored]

    assert not missing, (
        "these local-only directories are NOT excluded by .vercelignore and "
        f"would be uploaded on every deploy: {missing}. "
        "Being in .gitignore does not exclude them — .vercelignore is a "
        "separate list."
    )


def test_scratch_is_excluded_because_the_hygiene_contract_does_not_cover_uploads() -> None:
    """Called out on its own because the reason is not size.

    `.scratch/` is the sanctioned dump for probe scripts, API responses and
    working copies of config. The hygiene contract keeps it out of git; only
    this line keeps it out of a deploy.
    """
    assert ".scratch" in _ignored_directories()
