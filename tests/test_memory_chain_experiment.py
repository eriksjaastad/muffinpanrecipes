import json

import pytest

from scripts.memory_chain_experiment import (
    ARMS,
    CHARACTER_ROSTER,
    WEEK_PLAN,
    FakeAdapters,
    build_plan,
    execute_fake_chain,
    main,
    stable_source_id,
    write_plan,
)


class FakeSimulatorState:
    def __init__(self, characters_dir):
        self.CHARACTERS_DIR = characters_dir
        self._system_prompt_cache = {"existing": "cached prompt"}
        self.DAY_STAGE_DIRECTIONS = {"monday": "original Monday", "tuesday": "original Tuesday"}


def _fake_adapters(*, fail_week=None, observed=None):
    observed = observed if observed is not None else []

    def simulate_week(week, arm, memory_root):
        if arm == ARMS[0]:
            assert list(memory_root.iterdir()) == []
        observed.append({
            "week": week["week"],
            "seed": week["seed"],
            "arm": arm,
            "cache_at_start": dict(STATE._system_prompt_cache),
            "root": memory_root,
        })
        assert STATE._system_prompt_cache == {}
        STATE._system_prompt_cache["added during week"] = week["week"]
        STATE.DAY_STAGE_DIRECTIONS["monday"] = "mutated in fake simulator"
        if fail_week == week["week"] and arm == ARMS[1]:
            raise RuntimeError("synthetic simulation failure")
        if arm == ARMS[1]:
            prior = sorted(memory_root.glob("*/memory.json"))
            assert len(prior) == len(CHARACTER_ROSTER) if week["week"] != "2026-W40" else not prior
            observed[-1]["prior_memory_files"] = [path.parent.name for path in prior]
        turns = [
            {"day": "monday", "speaker": "Margaret Chen", "text": f"Week {week['week']} note."},
            {"day": "monday", "speaker": "Ria Castillo", "text": "I see the framing differently."},
        ]
        if week["week"] == "2026-W41":
            turns = turns[:1]
        return {"turns": turns}

    def write_memory(speaker, payload, root):
        path = root / speaker / "memory.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        prior = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        normalized = "".join(character.lower() for character in speaker if character.isalnum())
        cited_ids = payload["source_ids"]
        if speaker == "Margaret Chen" and payload["status"] == "observed":
            cited_ids = cited_ids[:1]
        record = {
            "memory_id": f"mem_{payload['week']}_{normalized}",
            "week": payload["week"],
            "status": payload["status"],
            "text": "Synthetic fake memory, not dialogue content." if payload["status"] == "observed" else None,
            "source_ids": cited_ids,
            "prior_memory_ids": payload["prior_memory_ids"],
        }
        prior.append(record)
        path.write_text(json.dumps(prior), encoding="utf-8")
        return record

    return FakeAdapters("fake", False, simulate_week, write_memory), observed


def test_plan_has_fixed_three_week_control_and_memory_arms():
    plan = build_plan()

    assert [row["week"] for row in plan["weeks"]] == [row["week"] for row in WEEK_PLAN]
    assert [row["seed"] for row in plan["weeks"]] == [40140, 40141, 40142]
    assert plan["character_roster"] == list(CHARACTER_ROSTER)
    assert plan["weekly_memory_slots_per_arm"] == 18
    assert all([arm["name"] for arm in row["arms"]] == list(ARMS) for row in plan["weeks"])
    assert plan["execution_performed"] is False
    assert plan["provider_calls"] == 0
    assert plan["budget_usd"] == 0


@pytest.mark.parametrize("key,value", [
    ("budget_usd", 5),
    ("provider_calls_allowed", True),
    ("provenance", "missing"),
    ("isolation", "production directory"),
    ("side_effects", "cron enabled"),
])
def test_plan_rejects_missing_or_unsafe_assumptions(key, value):
    assumptions = build_plan()["assumptions"]
    assumptions[key] = value

    with pytest.raises(ValueError, match="experiment assumption"):
        build_plan(assumptions)


def test_plan_rejects_empty_assumptions_instead_of_filling_defaults():
    with pytest.raises(ValueError, match="experiment assumption"):
        build_plan({})


def test_write_plan_is_an_artifact_only(tmp_path):
    output = tmp_path / "out" / "plan.json"

    plan = write_plan(output)

    assert json.loads(output.read_text(encoding="utf-8")) == plan
    assert plan["status"] == "plan_only"
    assert plan["execution_performed"] is False


def test_cli_writes_default_plan_successfully(tmp_path):
    output = tmp_path / "plan.json"

    assert main(["--output", str(output)]) == 0
    plan = json.loads(output.read_text(encoding="utf-8"))
    assert plan["assumptions"]["isolation"] == "each arm uses a temporary character-memory root that is sent to OS trash after the run"
    assert plan["provider_calls"] == 0


def test_fake_chain_keeps_arms_isolated_and_links_week_memories(tmp_path, monkeypatch):
    global STATE
    production_root = tmp_path / "production-characters"
    production_root.mkdir()
    sentinel = production_root / "sentinel.json"
    sentinel.write_text("preserve", encoding="utf-8")
    STATE = FakeSimulatorState(production_root)
    original_cache = dict(STATE._system_prompt_cache)
    original_directions = dict(STATE.DAY_STAGE_DIRECTIONS)
    adapters, observed = _fake_adapters()
    trash_root = tmp_path / "trash"
    trash_root.mkdir()
    trashed = []

    def preserve_in_test_trash(path):
        destination = trash_root / path.name
        path.rename(destination)
        trashed.append(destination)

    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", preserve_in_test_trash)

    result = execute_fake_chain(build_plan(), adapters, STATE)

    assert result["provider_calls"] == "unverified_in_injected_callbacks"
    assert result["provider_calls_verified"] is False
    assert set(result["arms"]) == set(ARMS)
    assert [row["week"] for row in result["arms"][ARMS[0]]["weeks"]] == [row["week"] for row in WEEK_PLAN]
    assert [row["week"] for row in result["arms"][ARMS[1]]["weeks"]] == [row["week"] for row in WEEK_PLAN]
    assert all(not row["prior_memory_ids"] for row in result["arms"][ARMS[0]]["weeks"])
    treatment_weeks = result["arms"][ARMS[1]]["weeks"]
    assert treatment_weeks[0]["prior_memory_ids"] == {}
    assert treatment_weeks[1]["prior_memory_ids"] == {
        character: [f"mem_2026-W40_{''.join(c.lower() for c in character if c.isalnum())}"]
        for character in CHARACTER_ROSTER
    }
    assert treatment_weeks[2]["prior_memory_ids"] == {
        character: [f"mem_2026-W41_{''.join(c.lower() for c in character if c.isalnum())}"]
        for character in CHARACTER_ROSTER
    }
    assert len(treatment_weeks[0]["memory_records"]) == len(CHARACTER_ROSTER)
    margaret_record = next(record for record in treatment_weeks[0]["memory_records"] if record["character"] == "Margaret Chen")
    assert margaret_record["text"] == "Synthetic fake memory, not dialogue content."
    assert margaret_record["source_ids"] == [
        treatment_weeks[0]["turns"][0]["source_id"]
    ]
    assert margaret_record["observed_source_ids"] == [
        turn["source_id"] for turn in treatment_weeks[0]["turns"]
    ]
    assert set(margaret_record["source_ids"]).issubset(margaret_record["observed_source_ids"])
    for arm in ARMS:
        for week in result["arms"][arm]["weeks"]:
            assert all(turn["week"] == week["week"] for turn in week["turns"])
            assert all(turn["source_id"].startswith("turn_") for turn in week["turns"])
            assert len(week["source_ids"]) == len(set(week["source_ids"]))
    assert all(observation["cache_at_start"] == {} for observation in observed)
    assert STATE.CHARACTERS_DIR == production_root
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    assert sorted(path.name for path in production_root.iterdir()) == ["sentinel.json"]
    assert STATE._system_prompt_cache == original_cache
    assert STATE.DAY_STAGE_DIRECTIONS == original_directions
    assert all("memory_root" not in arm for arm in result["arms"].values())
    assert len(trashed) == 2
    assert len(list(trash_root.iterdir())) == 2
    treatment_trash = next(path for path in trashed if path.name.startswith("mpr-memory-treatment-"))
    assert len(list(treatment_trash.glob("*/memory.json"))) == len(CHARACTER_ROSTER)
    assert all(path.exists() for path in trashed)
    treatment_roots = {str(row["root"]) for row in observed if row["arm"] == ARMS[1]}
    control_roots = {str(row["root"]) for row in observed if row["arm"] == ARMS[0]}
    assert len(treatment_roots) == len(control_roots) == 1
    assert treatment_roots.isdisjoint(control_roots)
    for character in CHARACTER_ROSTER:
        records = json.loads((treatment_trash / character / "memory.json").read_text(encoding="utf-8"))
        assert len(records) == 3
    ria_records = json.loads((treatment_trash / "Ria Castillo" / "memory.json").read_text(encoding="utf-8"))
    assert ria_records[1]["status"] == "no_new_evidence"
    assert ria_records[1]["source_ids"] == []
    assert ria_records[1]["prior_memory_ids"] == ["mem_2026-W40_riacastillo"]
    assert treatment_weeks[2]["prior_memory_ids"]["Ria Castillo"] == ["mem_2026-W41_riacastillo"]
    retained_ria_slot = next(record for record in treatment_weeks[1]["memory_records"] if record["character"] == "Ria Castillo")
    assert retained_ria_slot["text"] is None
    assert retained_ria_slot["status"] == "no_new_evidence"


def test_globals_restore_after_fake_simulation_exception(tmp_path, monkeypatch):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    original_dir = STATE.CHARACTERS_DIR
    original_cache = dict(STATE._system_prompt_cache)
    original_directions = dict(STATE.DAY_STAGE_DIRECTIONS)
    adapters, _ = _fake_adapters(fail_week="2026-W41")
    trash_root = tmp_path / "trash"
    trash_root.mkdir()
    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", lambda path: path.rename(trash_root / path.name))

    with pytest.raises(RuntimeError, match="synthetic simulation failure"):
        execute_fake_chain(build_plan(), adapters, STATE)

    assert STATE.CHARACTERS_DIR == original_dir
    assert STATE._system_prompt_cache == original_cache
    assert STATE.DAY_STAGE_DIRECTIONS == original_directions
    assert len(list(trash_root.iterdir())) == 2


def test_execution_refuses_non_fake_or_provider_enabled_adapters(tmp_path):
    global STATE
    STATE = FakeSimulatorState(tmp_path)
    adapters, _ = _fake_adapters()
    paid_adapter = FakeAdapters("provider", True, adapters.simulate_week, adapters.write_memory)

    with pytest.raises(ValueError, match="only explicitly marked zero-provider fake"):
        execute_fake_chain(build_plan(), paid_adapter, STATE)


@pytest.mark.parametrize("tamper,expected_error", [
    ("experiment_id", "experiment_id"),
    ("schema_version", "schema_version"),
    ("concept", "frozen scenarios"),
    ("seed", "frozen scenarios"),
    ("arm", "frozen scenarios"),
])
def test_execution_rejects_tampered_fixed_scenario_before_callbacks(tmp_path, tamper, expected_error):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    callback_calls = []

    def simulate_week(*args):
        callback_calls.append("simulate")
        return {"turns": []}

    def write_memory(*args):
        callback_calls.append("memory")
        return {}

    adapters = FakeAdapters("fake", False, simulate_week, write_memory)
    plan = build_plan()
    if tamper == "experiment_id":
        plan["experiment_id"] = "memory-chain-altered"
    elif tamper == "schema_version":
        plan["schema_version"] = 2
    elif tamper == "concept":
        plan["weeks"][0]["concept"] = "Changed concept"
    elif tamper == "seed":
        plan["weeks"][0]["seed"] += 1
    elif tamper == "arm":
        plan["weeks"][0]["arms"][1]["name"] = "changed_arm"

    with pytest.raises(ValueError, match=expected_error):
        execute_fake_chain(plan, adapters, STATE)
    assert callback_calls == []


def test_execution_rejects_speakers_outside_fixed_character_roster(tmp_path, monkeypatch):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    adapters, _ = _fake_adapters()

    def unknown_speaker(week, arm, root):
        return {"turns": [{"day": "monday", "speaker": "Unknown Guest", "text": "Hello."}]}

    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", lambda path: path.rename(tmp_path / path.name))
    adapters = FakeAdapters("fake", False, unknown_speaker, adapters.write_memory)

    with pytest.raises(ValueError, match="speakers outside the fixed roster"):
        execute_fake_chain(build_plan(), adapters, STATE)


def test_execution_rejects_memory_citations_outside_observed_source_set(tmp_path, monkeypatch):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    adapters, _ = _fake_adapters()
    trash_root = tmp_path / "trash"
    trash_root.mkdir()
    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", lambda path: path.rename(trash_root / path.name))

    def invalid_writer(speaker, payload, root):
        record = adapters.write_memory(speaker, payload, root)
        if speaker == "Margaret Chen" and payload["status"] == "observed":
            record["source_ids"] = ["turn_not_in_observed_week"]
        return record

    invalid_adapters = FakeAdapters("fake", False, adapters.simulate_week, invalid_writer)
    with pytest.raises(ValueError, match="non-empty subset"):
        execute_fake_chain(build_plan(), invalid_adapters, STATE)


def test_stable_source_ids_include_arm_and_week():
    common = ("monday", 0, "Ria", "The crop needs more space.")

    control_id = stable_source_id(ARMS[0], "2026-W40", *common)
    same_control_id = stable_source_id(ARMS[0], "2026-W40", *common)
    treatment_id = stable_source_id(ARMS[1], "2026-W40", *common)
    next_week_id = stable_source_id(ARMS[1], "2026-W41", *common)

    assert control_id == same_control_id
    assert control_id != treatment_id
    assert treatment_id != next_week_id
