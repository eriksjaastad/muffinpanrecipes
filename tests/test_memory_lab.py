import hashlib
import json

import pytest

from scripts.memory_lab import WEEK_DAYS, build_manifest, main, render_candidate


def _episode(path, episode_id, monday, *, rejected=None, tuesday_stage=None):
    stages = {day: {"status": "complete", "dialogue": []} for day in WEEK_DAYS}
    stages["monday"] = {"status": "complete", "dialogue": monday}
    if tuesday_stage:
        stages["tuesday"] = tuesday_stage
    payload = {"episode_id": episode_id, "stages": stages}
    if rejected is not None:
        payload["rejected_dialogues"] = rejected
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manifest_uses_accepted_complete_dialogue_and_keeps_all_perspectives(tmp_path):
    episodes = [
        _episode(tmp_path / "one.json", "2026-W10", [
            {"character": "Ria", "message": "The label needs more contrast."},
            {"character": "Marcus", "message": "I can simplify the title."},
        ], rejected=[{"character": "Ria", "message": "Rejected line must not appear."}]),
        _episode(tmp_path / "two.json", "2026-W11", [
            {"character": "Ria", "message": "That title reads clearly now."},
        ]),
        _episode(tmp_path / "three.json", "2026-W12", [
            {"character": "Marcus", "message": "Ria's contrast note changed the layout."},
            {"character": "Ria", "message": "The new layout carries that note forward."},
        ]),
    ]

    manifest = build_manifest(episodes)

    ria = manifest["characters"]["Ria"]
    assert manifest["episode_count"] == 3
    assert len(ria["weekly_memory_slots"]) == 3
    first_week, second_week, third_week = ria["weekly_memory_slots"]
    assert manifest["episodes"][0]["sha256"] == hashlib.sha256(episodes[0].read_bytes()).hexdigest()
    assert manifest["episodes"][0]["sha256"] == hashlib.sha256(episodes[0].read_bytes()).hexdigest()
    assert [row["episode_id"] for row in first_week["observations"]] == ["2026-W10", "2026-W10"]
    assert [row["perspective"] for row in first_week["observations"]] == ["self", "heard"]
    assert all("Rejected line" not in row["message"]
               for slot in ria["weekly_memory_slots"] for row in slot["observations"])
    assert first_week["observations"][1]["character"] == "Marcus"
    assert first_week["observations"][1]["source_id"].startswith("msg_")
    assert first_week["observations"][1]["turn_index"] == 1
    assert first_week["candidate_memory_schema"]["text"] is None
    assert [v["budget_tokens"] for v in first_week["budget_variants"]] == [80, 160, 300]
    assert all(v["estimated_tokens"] == 0 and not v["overflow"] for v in first_week["budget_variants"])
    assert first_week["prior_slot_ids_available_after_creation"] == []
    assert second_week["prior_slot_ids_available_after_creation"] == [first_week["slot_id"]]
    assert third_week["prior_slot_ids_available_after_creation"] == [first_week["slot_id"], second_week["slot_id"]]
    marcus_weeks = manifest["characters"]["Marcus"]["weekly_memory_slots"]
    assert marcus_weeks[1]["episode_id"] == "2026-W11"
    assert marcus_weeks[1]["observations"] == []
    assert marcus_weeks[1]["prior_slot_ids_available_after_creation"] == [marcus_weeks[0]["slot_id"]]
    assert manifest["generation_performed"] is False


def test_source_ids_are_stable_and_budget_rendering_flags_overflow(tmp_path):
    paths = [
        _episode(tmp_path / f"{week}.json", week, [{"character": "Ria", "message": "Same evidence."}])
        for week in ("2026-W20", "2026-W21", "2026-W22")
    ]
    first = build_manifest(paths)
    second = build_manifest(paths)
    first_id = first["characters"]["Ria"]["weekly_memory_slots"][0]["observations"][0]["source_id"]
    second_id = second["characters"]["Ria"]["weekly_memory_slots"][0]["observations"][0]["source_id"]
    assert first_id == second_id

    rendered = render_candidate({"text": "word " * 81, "source_ids": [first_id]}, budget=80)
    assert rendered["estimated_tokens"] == 81
    assert rendered["overflow"] is True


def test_character_observes_only_days_they_attended(tmp_path):
    episodes = [
        _episode(tmp_path / f"{week}.json", week, [
            {"character": "Ria", "message": "I attended Monday."},
            {"character": "Marcus", "message": "I spoke Monday too."},
        ], tuesday_stage={"status": "complete", "dialogue": [
            {"character": "Marcus", "message": "This Tuesday exchange is unseen by Ria."},
        ]})
        for week in ("2026-W30", "2026-W31", "2026-W32")
    ]

    manifest = build_manifest(episodes)

    ria_first = manifest["characters"]["Ria"]["weekly_memory_slots"][0]
    marcus_first = manifest["characters"]["Marcus"]["weekly_memory_slots"][0]
    assert {row["day"] for row in ria_first["observations"]} == {"monday"}
    assert "This Tuesday" not in " ".join(row["message"] for row in ria_first["observations"])
    assert "I spoke Monday too." in " ".join(row["message"] for row in ria_first["observations"])
    assert {row["day"] for row in marcus_first["observations"]} == {"monday", "tuesday"}
    assert "This Tuesday" in " ".join(row["message"] for row in marcus_first["observations"])


def test_incomplete_week_requires_explicit_partial_flag(tmp_path):
    paths = []
    for week in ("2026-W40", "2026-W41", "2026-W42"):
        path = _episode(tmp_path / f"{week}.json", week, [{"character": "Ria", "message": "Monday."}])
        if week == "2026-W40":
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["stages"].pop("sunday")
            path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)

    with pytest.raises(ValueError, match="incomplete week"):
        build_manifest(paths)
    manifest = build_manifest(paths, allow_partial=True)
    assert manifest["partial_input"] is True
    assert manifest["episodes"][0]["missing_days"] == ["sunday"]


def test_cli_rejects_partial_input_by_default(tmp_path, capsys):
    paths = []
    for week in ("2026-W50", "2026-W51", "2026-W52"):
        path = _episode(tmp_path / f"{week}.json", week, [{"character": "Ria", "message": "Monday."}])
        if week == "2026-W50":
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["stages"].pop("sunday")
            path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(str(path))

    with pytest.raises(SystemExit) as exc:
        main(paths)

    assert exc.value.code == 2
    assert "incomplete week" in capsys.readouterr().err


def test_manifest_rejects_out_of_order_iso_weeks(tmp_path):
    paths = [
        _episode(tmp_path / f"{week}.json", week, [{"character": "Ria", "message": "Monday."}])
        for week in ("2026-W32", "2026-W31", "2026-W33")
    ]

    with pytest.raises(ValueError, match="chronological ISO week order"):
        build_manifest(paths)


def test_cli_creates_output_parent_directory(tmp_path):
    paths = [
        _episode(tmp_path / f"{week}.json", week, [{"character": "Ria", "message": "Monday."}])
        for week in ("2026-W20", "2026-W21", "2026-W22")
    ]
    output = tmp_path / "new" / "nested" / "manifest.json"

    main([*(str(path) for path in paths), "--output", str(output)])

    assert json.loads(output.read_text(encoding="utf-8"))["episode_count"] == 3
