"""The committed admin CSS is exactly what the build makes from the templates (#8604).

backend/admin/static/tailwind.css was built once on 2026-03-02 and never
again, so colours added to templates later had no rule and buttons rendered
white on light grey. Tailwind decides which classes the templates use, so the
guard reruns the repo's own `build:css` script and compares the output.
"""
import json
import shlex
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_committed_css_matches_a_fresh_build(tmp_path):
    tailwind = ROOT / "node_modules/.bin/tailwindcss"
    assert tailwind.exists(), "run `npm ci`: this test rebuilds the admin CSS with the pinned Tailwind"
    argv = shlex.split(json.loads((ROOT / "package.json").read_text())["scripts"]["build:css"])
    out = argv.index("-o") + 1
    committed, argv[out] = ROOT / argv[out], str(tmp_path / "tailwind.css")
    argv[0] = str(tailwind)
    build = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert build.returncode == 0, build.stderr
    assert (tmp_path / "tailwind.css").read_bytes() == committed.read_bytes(), (
        "run `npm run build:css`: the committed admin CSS is stale for the templates")
