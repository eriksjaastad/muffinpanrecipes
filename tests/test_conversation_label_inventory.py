from __future__ import annotations

import json

import pytest

from scripts.conversation_label_inventory import build_inventory, inspect_file


def _write_json(tmp_path, name: str, payload: dict):
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    return path


def test_inventory_counts_primary_labels_by_day_and_hashes_source(tmp_path):
    source = _write_json(
        tmp_path,
        "primary.json",
        {
            "stage": "monday",
            "human_picks": {"1": "variant", "2": "control"},
            "pairs": [{"run_index": 1}, {"run_index": 2}],
        },
    )

    result = build_inventory([source])

    assert result["summary"] == {
        "primary_human_labels": 2,
        "primary_days": {"monday": 2},
        "derived_label_references": 0,
        "derived_files_are_not_new_labels": True,
    }
    assert len(result["files"][0]["sha256"]) == 64


def test_pair_level_e1_receipt_is_derived_and_not_double_counted(tmp_path):
    source = _write_json(
        tmp_path,
        "derived.json",
        {
            "pairs": [
                {"pair": 1, "scenario_id": "2026-W40:sunday:3", "human_pick": "variant"},
                {"pair": 2, "scenario_id": "2026-W42:wednesday:2", "human_pick": "control"},
            ]
        },
    )

    result = build_inventory([source])

    assert result["summary"]["primary_human_labels"] == 0
    assert result["summary"]["derived_label_references"] == 2
    assert result["files"][0]["source_kind"] == "derived"
    assert result["files"][0]["days"] == {"sunday": 1, "wednesday": 1}


def test_missing_pair_for_top_level_pick_fails_closed(tmp_path):
    source = _write_json(
        tmp_path,
        "broken.json",
        {"stage": "monday", "human_picks": {"2": "variant"}, "pairs": [{}]},
    )

    with pytest.raises(ValueError, match="has no matching pair"):
        inspect_file(source)
