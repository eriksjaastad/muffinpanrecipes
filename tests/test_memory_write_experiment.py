import builtins
import json

from scripts.memory_lab import WEEK_DAYS, build_manifest
from scripts import memory_write_experiment as experiment


CHARACTERS = ["Margaret", "Steph", "Julian", "Marcus", "Devon", "Ria"]


def _episode(path, week, texts):
    stages = {day: {"status": "complete", "dialogue": []} for day in WEEK_DAYS}
    stages["monday"]["dialogue"] = [
        {"character": character, "message": text} for character, text in texts
    ]
    payload = {"episode_id": week, "stages": stages}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _manifest(tmp_path):
    w35 = _episode(
        tmp_path / "2026-W35.json",
        "2026-W35",
        [(character, f"W35 evidence for {character}.") for character in CHARACTERS],
    )
    w36 = _episode(
        tmp_path / "2026-W36.json",
        "2026-W36",
        [(character, f"FUTURE ONLY W36 event for {character}.") for character in CHARACTERS],
    )
    w37 = _episode(
        tmp_path / "2026-W37.json",
        "2026-W37",
        [("Ria", "FUTURE ONLY W37 event.")],
    )
    return build_manifest([w35, w36, w37])


def test_builds_six_same_source_model_budget_prompt_pairs_without_future_leakage(tmp_path):
    manifest = _manifest(tmp_path)

    result = experiment.build_experiment(manifest)

    assert result["episode_id"] == "2026-W35"
    assert result["generation_performed"] is False
    assert result["paid_mode_available"] is False
    assert result["character_count"] == 6
    assert [item["character"] for item in result["prompts"]] == sorted(CHARACTERS)
    for item in result["prompts"]:
        assert item["target_output_tokens"] == 160
        assert item["planned_model"] == "claude-haiku-4-5-20251001"
        assert item["model_called"] is False
        assert set(item["arms"]) == {"A", "B"}
        assert item["arms"]["A"]["user"] == item["arms"]["B"]["user"]
        assert "W35 evidence" in item["arms"]["A"]["user"]
        assert "FUTURE ONLY" not in item["arms"]["A"]["user"]
        assert item["source_ids"] == [
            row["source_id"]
            for row in manifest["characters"][item["character"]]["weekly_memory_slots"][0]["observations"]
        ]
        assert "two sentences" in item["arms"]["A"]["system"]
        assert "third person" in item["arms"]["A"]["system"]
        assert "Inference:" in item["arms"]["B"]["system"]
        assert "no change evidenced" in item["arms"]["B"]["system"]
        assert "Open thread:" in item["arms"]["B"]["system"]
        for source_id in item["source_ids"]:
            assert f"[{source_id}]" in item["arms"]["A"]["user"]


def test_cli_dry_run_does_not_import_or_call_model_apis(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path)), encoding="utf-8")
    output_path = tmp_path / "new" / "prompts.json"
    original_import = builtins.__import__

    def reject_model_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"anthropic", "openai"}:
            raise AssertionError(f"dry run attempted model API import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_model_import)

    assert experiment.main([str(manifest_path), "--output", str(output_path)]) == 0
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["generation_performed"] is False
    assert output["character_count"] == 6
