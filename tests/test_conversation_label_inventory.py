from __future__ import annotations

import hashlib
import json

import pytest

from scripts.conversation_label_inventory import build_inventory, inspect_file


def _write_json(tmp_path, name: str, payload: dict):
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def _primary_payload(pick: str = "variant") -> dict:
    return {
        "human_picks": {"1": pick},
        "pairs": [{"scenario_id": "2026-W40:sunday:3"}],
    }


def _derived_payload(evidence_sha: str, *, pair=1, pick="variant") -> dict:
    return {
        "sources": {"evidence": {"sha256": evidence_sha}},
        "pairs": [
            {
                "pair": pair,
                "scenario_id": "2026-W40:sunday:3",
                "human_pick": pick,
            }
        ],
    }


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inventory_counts_primary_and_verifies_exact_derived_provenance(tmp_path):
    primary = _write_json(tmp_path, "primary.json", _primary_payload())
    derived = _write_json(tmp_path, "derived.json", _derived_payload(_sha(primary)))

    result = build_inventory([primary], [derived])

    assert result["summary"] == {
        "primary_human_labels": 1,
        "primary_days": {"sunday": 1},
        "derived_label_references": 1,
        "derived_references_verified": True,
        "derived_files_are_not_new_labels": True,
    }
    assert result["files"][0]["source_name"] == "primary.json"
    assert result["files"][1]["evidence_sha256"] == _sha(primary)


@pytest.mark.parametrize(
    "evidence_sha, pair, pick, message",
    [
        ("0" * 64, 1, "variant", "declared primary evidence is missing"),
        (None, 999, "variant", "do not match primary evidence"),
        (None, 1, "control", "do not match primary evidence"),
    ],
)
def test_derived_provenance_must_match_source_pair_scenario_and_pick(
    tmp_path, evidence_sha, pair, pick, message
):
    primary = _write_json(tmp_path, "primary.json", _primary_payload())
    derived = _write_json(
        tmp_path,
        "derived.json",
        _derived_payload(evidence_sha or _sha(primary), pair=pair, pick=pick),
    )

    with pytest.raises(ValueError, match=message):
        build_inventory([primary], [derived])


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"pairs": "broken", "human_picks": {"1": "variant"}}, "pairs must be"),
        ({"pairs": [{}], "human_picks": {"1": "banana"}}, "no scenario_id"),
        (
            {"pairs": [{"scenario_id": "2026-W40:sunday:3"}], "human_picks": {"1": "banana"}},
            "invalid human pick",
        ),
        (
            {
                "pairs": [{"scenario_id": "2026-W40:sunday:3"}],
                "human_picks": {"1": "variant", "01": "variant"},
            },
            "invalid pair ID",
        ),
    ],
)
def test_malformed_primary_fails_closed(tmp_path, payload, message):
    source = _write_json(tmp_path, "broken.json", payload)

    with pytest.raises(ValueError, match=message):
        inspect_file(source, "primary")


def test_duplicate_source_content_fails_closed(tmp_path):
    first = _write_json(tmp_path, "first.json", _primary_payload())
    second = _write_json(tmp_path, "second.json", _primary_payload())

    with pytest.raises(ValueError, match="same source content"):
        build_inventory([first, second], [])
