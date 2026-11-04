from __future__ import annotations

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


def test_inventory_counts_primary_and_verifies_derived_reference(tmp_path):
    primary = _write_json(tmp_path, "primary.json", _primary_payload())
    derived = _write_json(
        tmp_path,
        "derived.json",
        {
            "pairs": [
                {"pair": 1, "scenario_id": "2026-W40:sunday:3", "human_pick": "variant"}
            ]
        },
    )

    result = build_inventory([primary], [derived])

    assert result["summary"] == {
        "primary_human_labels": 1,
        "primary_days": {"sunday": 1},
        "derived_label_references": 1,
        "derived_references_verified": True,
        "derived_files_are_not_new_labels": True,
    }
    assert result["files"][0]["source_name"] == "primary.json"
    assert len(result["files"][0]["sha256"]) == 64


def test_derived_label_must_match_primary_scenario_and_pick(tmp_path):
    primary = _write_json(tmp_path, "primary.json", _primary_payload())
    derived = _write_json(
        tmp_path,
        "derived.json",
        {
            "pairs": [
                {"pair": 1, "scenario_id": "2026-W40:sunday:3", "human_pick": "control"}
            ]
        },
    )

    with pytest.raises(ValueError, match="do not match primary evidence"):
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
