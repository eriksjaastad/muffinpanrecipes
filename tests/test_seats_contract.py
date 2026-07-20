from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SEATS = {
    "dialogue_generation",
    "recipe_copy_generation",
    "vision_art_direction",
    "image_generation",
    "editorial_judge",
}


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_all_seats_have_real_dev_and_sealed_artifacts() -> None:
    document = yaml.safe_load((ROOT / "seats.yaml").read_text())
    assert {seat["id"] for seat in document["seats"]} == EXPECTED_SEATS

    for seat in document["seats"]:
        contract = seat["eval_fixtures"]
        fixtures = _rows(ROOT / contract["path"])
        labels = _rows(ROOT / contract["sealed_labels"]["path"])
        ids = {row["fixture_id"] for row in fixtures}
        assert len(fixtures) >= 3
        assert ids == {row["fixture_id"] for row in labels}
        remainders = {
            hashlib.sha256(value.encode()).digest()[0] % contract["split"]["modulo"]
            for value in ids
        }
        assert 0 in remainders
        assert remainders - {0}
        for row in fixtures:
            assert all((ROOT / ref).is_file() for ref in row["source_refs"])

        output = seat["output_contract"]
        if output["type"] == "json":
            assert json.loads((ROOT / output["schema_ref"]).read_text())


def test_dialogue_benchmark_remains_project_owned() -> None:
    decision = (ROOT / "benchmarks/seats/README.md").read_text()
    assert "remains project-owned" in decision
    assert "must not duplicate the runner" in decision
    assert (ROOT / "scripts/run_dialogue_benchmarks.py").is_file()
    assert (ROOT / "data/simulations/BENCHMARK_RESULTS.md").is_file()
