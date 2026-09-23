import json

from scripts.memory_lab import build_manifest, render_candidate


def _episode(path, episode_id, monday, *, rejected=None, extra_stage=None):
    stages = {"monday": {"status": "complete", "dialogue": monday}}
    if extra_stage:
        stages["tuesday"] = extra_stage
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
        ], rejected=[{"character": "Ria", "message": "Rejected line must not appear."}],
            extra_stage={"status": "rejected", "dialogue": [{"character": "Ria", "message": "Incomplete stage."}]}),
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
    assert [row["episode_id"] for row in ria["observations"]] == [
        "2026-W10", "2026-W10", "2026-W11", "2026-W12", "2026-W12"
    ]
    assert [row["perspective"] for row in ria["observations"]] == ["self", "heard", "self", "heard", "self"]
    assert all("Rejected line" not in row["message"] and "Incomplete stage" not in row["message"]
               for row in ria["observations"])
    assert ria["observations"][1]["character"] == "Marcus"
    assert ria["observations"][1]["source_id"].startswith("msg_")
    assert ria["observations"][1]["turn_index"] == 1
    assert ria["candidate_memory_schema"]["text"] is None
    assert [v["budget_tokens"] for v in ria["budget_variants"]] == [80, 160, 300]
    assert all(v["estimated_tokens"] == 0 and not v["overflow"] for v in ria["budget_variants"])
    assert manifest["generation_performed"] is False


def test_source_ids_are_stable_and_budget_rendering_flags_overflow(tmp_path):
    paths = [
        _episode(tmp_path / f"{week}.json", week, [{"character": "Ria", "message": "Same evidence."}])
        for week in ("2026-W20", "2026-W21", "2026-W22")
    ]
    first = build_manifest(paths)
    second = build_manifest(paths)
    first_id = first["characters"]["Ria"]["observations"][0]["source_id"]
    second_id = second["characters"]["Ria"]["observations"][0]["source_id"]
    assert first_id == second_id

    rendered = render_candidate({"text": "word " * 81, "source_ids": [first_id]}, budget=80)
    assert rendered["estimated_tokens"] == 81
    assert rendered["overflow"] is True
