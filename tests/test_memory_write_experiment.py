import builtins
import json

from scripts.memory_lab import WEEK_DAYS, build_manifest
from scripts import memory_write_experiment as experiment


CHARACTERS = ["Margaret", "Steph", "Julian", "Marcus", "Devon", "Ria"]
ROLES = {
    "Margaret": "baker",
    "Steph": "creative_director",
    "Julian": "art_director",
    "Marcus": "copywriter",
    "Devon": "site_architect",
    "Ria": "social_media_manager",
}


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


def _profiles(tmp_path):
    records = []
    for index, character in enumerate(CHARACTERS):
        role = ROLES[character]
        records.append({
            "name": character,
            "role": role,
            "internal_contradictions": [f"Authored personality frame {index}.", "A stable second perspective."],
            "relationships": {other_role: f"Trusts the {other_role} for specific work." for other_role in ROLES.values()},
        })
    path = tmp_path / "agent_personalities.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    loaded, digest = experiment.load_profiles(path)
    return loaded, digest


def test_builds_six_same_source_model_budget_prompt_pairs_without_future_leakage(tmp_path):
    manifest = _manifest(tmp_path)
    profiles, profile_hash = _profiles(tmp_path)

    result = experiment.build_experiment(manifest, profiles, profile_hash)

    assert result["episode_id"] == "2026-W35"
    assert result["generation_performed"] is False
    assert result["paid_mode_available"] is False
    assert result["character_count"] == 6
    assert result["target_output_token_band"] == [80, 120]
    assert result["hard_max_output_tokens_per_prompt"] == 160
    assert result["persona_profile_sha256"] == profile_hash
    assert [item["character"] for item in result["prompts"]] == sorted(CHARACTERS)
    for item in result["prompts"]:
        assert item["target_output_token_band"] == [80, 120]
        assert item["hard_max_output_tokens"] == 160
        assert item["planned_model"] == "claude-haiku-4-5-20251001"
        assert item["model_called"] is False
        assert set(item["arms"]) == {"A", "B"}
        assert item["arms"]["A"]["user"] == item["arms"]["B"]["user"]
        assert "W35 evidence" in item["arms"]["A"]["user"]
        assert "FUTURE ONLY" not in item["arms"]["A"]["user"]
        assert "Stable authored persona context" in item["arms"]["A"]["user"]
        assert f"Role: {ROLES[item['character']]}" in item["arms"]["A"]["user"]
        assert f"Authored personality frame {CHARACTERS.index(item['character'])}" in item["arms"]["A"]["user"]
        assert item["profile_context"]["profile_source_sha256"] == profile_hash
        assert item["profile_context_chars"] <= experiment.MAX_PROFILE_CONTEXT_CHARS
        assert 1 <= len(item["profile_context"]["relationship_framing"]) <= 2
        assert item["profile_context"]["relationship_framing"][0]["profile_pointer"].startswith("relationships.")
        assert item["source_ids"] == [
            row["source_id"]
            for row in manifest["characters"][item["character"]]["weekly_memory_slots"][0]["observations"]
        ]
        assert "two sentences" in item["arms"]["A"]["system"]
        assert "third person" in item["arms"]["A"]["system"]
        assert "Evidence map" in item["arms"]["A"]["system"]
        assert "40 words" not in item["arms"]["A"]["system"]
        assert "80–120 provider output tokens for the complete response" in item["arms"]["A"]["system"]
        assert "only the source ID(s) that support that sentence" in item["arms"]["A"]["system"]
        assert "Inference:" in item["arms"]["B"]["system"]
        assert "citing source ID(s)" in item["arms"]["B"]["system"]
        assert "no change evidenced" in item["arms"]["B"]["system"]
        assert "Open thread:" in item["arms"]["B"]["system"]
        for source_id in item["source_ids"]:
            assert f"[{source_id}]" in item["arms"]["A"]["user"]


def test_cli_dry_run_does_not_import_or_call_model_apis(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(tmp_path)), encoding="utf-8")
    profiles, _ = _profiles(tmp_path)
    profiles_path = tmp_path / "agent_personalities.json"
    profiles_path.write_text(json.dumps(profiles), encoding="utf-8")
    output_path = tmp_path / "new" / "prompts.json"
    original_import = builtins.__import__

    def reject_model_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in {"anthropic", "openai"}:
            raise AssertionError(f"dry run attempted model API import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_model_import)

    assert experiment.main([
        str(manifest_path), "--profiles", str(profiles_path), "--output", str(output_path)
    ]) == 0
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["generation_performed"] is False
    assert output["character_count"] == 6
