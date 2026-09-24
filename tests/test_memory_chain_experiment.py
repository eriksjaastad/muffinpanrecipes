import copy
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

    def simulate_week(week, arm, memory_root, prior_memories):
        if arm == ARMS[0]:
            assert list(memory_root.iterdir()) == []
            (memory_root / "unwanted-memory.json").write_text("control-local-only", encoding="utf-8")
        elif week["week"] == "2026-W40":
            assert list(memory_root.iterdir()) == []
            assert all(not records for records in prior_memories.values())
        elif week["week"] == "2026-W41":
            assert list(memory_root.iterdir()) == []
            assert all(len(prior_memories[character]) == 1 for character in CHARACTER_ROSTER)
            margaret_prior = prior_memories["Margaret Chen"]
            assert len(margaret_prior) == 1
            assert margaret_prior[0]["week"] == "2026-W40"
            assert margaret_prior[0]["text"] == "Synthetic fake memory, not dialogue content."
            assert margaret_prior[0]["memory_id"] == "mem_2026-W40_margaretchen"
            margaret_prior[0]["text"] = "Mutated by callback after delivery."
            margaret_prior[0]["memory_id"] = "mutated-memory-id"
        elif week["week"] == "2026-W42":
            assert list(memory_root.iterdir()) == []
            assert all(len(prior_memories[character]) == 1 for character in CHARACTER_ROSTER)
            ria_prior = prior_memories["Ria Castillo"]
            assert [record["week"] for record in ria_prior] == ["2026-W41"]
            assert ria_prior[0]["status"] == "no_new_evidence"
            assert ria_prior[0]["text"] is None
            assert ria_prior[0]["prior_memory_ids"] == ["mem_2026-W40_riacastillo"]
            margaret_prior = prior_memories["Margaret Chen"]
            assert margaret_prior[0]["memory_id"] == "mem_2026-W41_margaretchen"
            assert margaret_prior[0]["text"] == "Synthetic fake memory, not dialogue content."
        observed.append({
            "week": week["week"],
            "seed": week["seed"],
            "arm": arm,
            "cache_at_start": dict(STATE._system_prompt_cache),
            "root": memory_root,
            "prior_memories": prior_memories,
        })
        assert STATE._system_prompt_cache == {}
        STATE._system_prompt_cache["added during week"] = week["week"]
        STATE.DAY_STAGE_DIRECTIONS["monday"] = "mutated in fake simulator"
        if fail_week == week["week"] and arm == ARMS[1]:
            raise RuntimeError("synthetic simulation failure")
        if arm == ARMS[1]:
            observed[-1]["prior_memory_ids"] = {
                character: [record["memory_id"] for record in records]
                for character, records in prior_memories.items()
            }
        turns = [
            {"day": "monday", "speaker": "Margaret Chen", "text": f"Week {week['week']} note."},
            {"day": "monday", "speaker": "Ria Castillo", "text": "I see the framing differently."},
        ]
        if week["week"] == "2026-W41":
            turns = turns[:1]
        return {"turns": turns}

    def write_memory(speaker, payload):
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
        return record

    return FakeAdapters("fake", False, simulate_week, write_memory), observed


def test_plan_has_fixed_three_week_control_and_memory_arms():
    plan = build_plan()

    assert [row["week"] for row in plan["weeks"]] == [row["week"] for row in WEEK_PLAN]
    assert [row["seed"] for row in plan["weeks"]] == [40140, 40141, 40142]
    assert plan["character_roster"] == list(CHARACTER_ROSTER)
    assert plan["memory_slots_by_arm"] == {ARMS[0]: 0, ARMS[1]: 18}
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
    assert result["prompt_injection_verified"] is False
    assert "model_prompt_visibility_unverified" in result["memory_delivery"]
    assert set(result["arms"]) == set(ARMS)
    assert [row["week"] for row in result["arms"][ARMS[0]]["weeks"]] == [row["week"] for row in WEEK_PLAN]
    assert [row["week"] for row in result["arms"][ARMS[1]]["weeks"]] == [row["week"] for row in WEEK_PLAN]
    assert all(not row["prior_memory_ids"] for row in result["arms"][ARMS[0]]["weeks"])
    treatment_weeks = result["arms"][ARMS[1]]["weeks"]
    assert treatment_weeks[0]["prior_memory_ids"] == {}
    assert treatment_weeks[0]["prior_memory_records_provided_to_callback"] == {
        character: [] for character in CHARACTER_ROSTER
    }
    w40_margaret_record = next(
        record for record in treatment_weeks[0]["memory_records"]
        if record["character"] == "Margaret Chen"
    )
    assert treatment_weeks[1]["prior_memory_records_provided_to_callback"]["Margaret Chen"] == [w40_margaret_record]
    assert treatment_weeks[1]["prior_memory_records_provided_to_callback"]["Margaret Chen"][0]["memory_id"] == "mem_2026-W40_margaretchen"
    assert treatment_weeks[1]["prior_memory_records_provided_to_callback"]["Margaret Chen"][0]["text"] == "Synthetic fake memory, not dialogue content."
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
    assert len(trashed) == 7
    assert len(list(trash_root.iterdir())) == 7
    treatment_trash = next(path for path in trashed if path.name.startswith("mpr-memory-treatment-store-"))
    assert len(list(treatment_trash.rglob("*.json"))) == len(CHARACTER_ROSTER) * len(WEEK_PLAN)
    assert all(path.exists() for path in trashed)
    treatment_roots = {str(row["root"]) for row in observed if row["arm"] == ARMS[1]}
    control_roots = {str(row["root"]) for row in observed if row["arm"] == ARMS[0]}
    assert len(treatment_roots) == len(control_roots) == 3
    assert treatment_roots.isdisjoint(control_roots)
    assert len({observation["root"] for observation in observed}) == 6
    control_trash = [path for path in trashed if path.name.startswith("mpr-memory-control_no_persistent_memory-")]
    assert len(control_trash) == 3
    assert all((path / "unwanted-memory.json").read_text(encoding="utf-8") == "control-local-only"
               for path in control_trash)
    for character in CHARACTER_ROSTER:
        char_key = "".join(char.lower() for char in character if char.isalnum())
        records = [
            json.loads((treatment_trash / "characters" / char_key / f"{week['week']}.json").read_text(encoding="utf-8"))
            for week in WEEK_PLAN
        ]
        assert len(records) == 3
    ria_w41 = json.loads((treatment_trash / "characters" / "riacastillo" / "2026-W41.json").read_text(encoding="utf-8"))
    assert ria_w41["status"] == "no_new_evidence"
    assert ria_w41["source_ids"] == []
    assert ria_w41["prior_memory_ids"] == ["mem_2026-W40_riacastillo"]
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
    assert len(list(trash_root.iterdir())) == 6


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

    def unknown_speaker(week, arm, root, prior_memories):
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

    def invalid_writer(speaker, payload):
        record = adapters.write_memory(speaker, payload)
        if speaker == "Margaret Chen" and payload["status"] == "observed":
            record["source_ids"] = ["turn_not_in_observed_week"]
        return record

    invalid_adapters = FakeAdapters("fake", False, adapters.simulate_week, invalid_writer)
    with pytest.raises(ValueError, match="non-empty subset"):
        execute_fake_chain(build_plan(), invalid_adapters, STATE)


def test_memory_writer_can_mutate_its_copy_without_corrupting_validated_provenance(tmp_path, monkeypatch):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    adapters, _ = _fake_adapters()
    trash_root = tmp_path / "trash"
    trash_root.mkdir()
    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", lambda path: path.rename(trash_root / path.name))

    def mutating_but_valid_writer(speaker, payload):
        valid_record = copy.deepcopy(adapters.write_memory(speaker, payload))
        payload["source_ids"].append("fabricated-source-id")
        if payload["turns"]:
            payload["turns"][0]["text"] = "fabricated dialogue"
        return valid_record

    mutating_adapters = FakeAdapters("fake", False, adapters.simulate_week, mutating_but_valid_writer)
    result = execute_fake_chain(build_plan(), mutating_adapters, STATE)

    first_week = result["arms"][ARMS[1]]["weeks"][0]
    assert all("fabricated-source-id" not in row["source_ids"] for row in result["arms"][ARMS[1]]["weeks"])
    assert first_week["turns"][0]["text"] == "Week 2026-W40 note."
    margaret_record = next(record for record in first_week["memory_records"] if record["character"] == "Margaret Chen")
    assert "fabricated-source-id" not in margaret_record["source_ids"]
    assert set(margaret_record["source_ids"]).issubset(margaret_record["observed_source_ids"])


def test_memory_writer_cannot_validate_fabricated_source_id_by_mutating_payload(tmp_path, monkeypatch):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    adapters, _ = _fake_adapters()
    trash_root = tmp_path / "trash"
    trash_root.mkdir()
    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", lambda path: path.rename(trash_root / path.name))

    def malicious_writer(speaker, payload):
        payload["source_ids"].append("fabricated-source-id")
        payload["turns"][0]["text"] = "fabricated dialogue"
        return {
            "memory_id": f"memory-{speaker}",
            "status": payload["status"],
            "text": "claims based on fabricated turn",
            "source_ids": ["fabricated-source-id"],
            "prior_memory_ids": payload["prior_memory_ids"],
        }

    malicious_adapters = FakeAdapters("fake", False, adapters.simulate_week, malicious_writer)
    with pytest.raises(ValueError, match="non-empty subset"):
        execute_fake_chain(build_plan(), malicious_adapters, STATE)


def test_execution_rejects_duplicate_memory_ids_across_characters(tmp_path, monkeypatch):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    adapters, _ = _fake_adapters()
    trash_root = tmp_path / "trash"
    trash_root.mkdir()
    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", lambda path: path.rename(trash_root / path.name))

    def duplicate_writer(speaker, payload):
        record = adapters.write_memory(speaker, payload)
        record["memory_id"] = "duplicate-id"
        return record

    duplicate_adapters = FakeAdapters("fake", False, adapters.simulate_week, duplicate_writer)
    with pytest.raises(ValueError, match="duplicate memory_id"):
        execute_fake_chain(build_plan(), duplicate_adapters, STATE)


def test_cleanup_attempts_every_root_and_reports_primary_and_cleanup_errors(tmp_path, monkeypatch):
    global STATE
    STATE = FakeSimulatorState(tmp_path / "production-characters")
    adapters, _ = _fake_adapters(fail_week="2026-W41")
    preserved_roots = tmp_path / "preserved-roots"
    preserved_roots.mkdir()
    cleanup_calls = []

    def fail_first_cleanup(path):
        destination = preserved_roots / path.name
        path.rename(destination)
        cleanup_calls.append(destination)
        if len(cleanup_calls) == 1:
            raise OSError("simulated first trash failure")

    monkeypatch.setattr("scripts.memory_chain_experiment._send_to_trash", fail_first_cleanup)

    with pytest.raises(ExceptionGroup) as exc_info:
        execute_fake_chain(build_plan(), adapters, STATE)

    assert len(cleanup_calls) == 6
    assert len(list(preserved_roots.iterdir())) == 6
    exception_messages = [str(error) for error in exc_info.value.exceptions]
    assert any("synthetic simulation failure" in message for message in exception_messages)
    assert any("simulated first trash failure" in message for message in exception_messages)


def test_stable_source_ids_include_arm_and_week():
    common = ("monday", 0, "Ria", "The crop needs more space.")

    control_id = stable_source_id(ARMS[0], "2026-W40", *common)
    same_control_id = stable_source_id(ARMS[0], "2026-W40", *common)
    treatment_id = stable_source_id(ARMS[1], "2026-W40", *common)
    next_week_id = stable_source_id(ARMS[1], "2026-W41", *common)

    assert control_id == same_control_id
    assert control_id != treatment_id
    assert treatment_id != next_week_id
