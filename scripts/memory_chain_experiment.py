#!/usr/bin/env python3
"""Create an offline plan for a three-week character-memory chain experiment.

Execution is deliberately restricted to injected fake adapters. The CLI only
writes a plan; it never invokes the dialogue simulator or a provider.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


EXPERIMENT_ID = "memory-chain-2026-w40-w42-v1"
WEEK_PLAN = (
    {"week": "2026-W40", "concept": "Crispy chickpea and feta muffin cups", "seed": 40140},
    {"week": "2026-W41", "concept": "Apple cheddar breakfast strata cups", "seed": 40141},
    {"week": "2026-W42", "concept": "Roasted tomato polenta muffin bites", "seed": 40142},
)
ARMS = ("control_no_persistent_memory", "weekly_character_memory")
CHARACTER_ROSTER = (
    "Margaret Chen",
    "Stephanie 'Steph' Whitmore",
    "Julian Torres",
    "Marcus Reid",
    "Devon Park",
    "Ria Castillo",
)


def _send_to_trash(path: Path) -> None:
    """Move an experiment root to the OS trash so it remains recoverable."""
    from send2trash import send2trash

    send2trash(str(path))


def _validate_assumptions(assumptions: dict[str, Any]) -> None:
    required = {
        "budget_usd": 0,
        "provider_calls_allowed": False,
        "provenance": "every generated turn retains week, day, speaker, and stable source_id",
        "isolation": "each arm uses a temporary character-memory root that is sent to OS trash after the run",
        "side_effects": "no cron, publish, Blob, or production character writes",
    }
    for key, expected in required.items():
        if assumptions.get(key) != expected:
            raise ValueError(f"experiment assumption {key!r} must be {expected!r}")


def build_plan(assumptions: dict[str, Any] | None = None) -> dict[str, Any]:
    if assumptions is None:
        assumptions = {
            "budget_usd": 0,
            "provider_calls_allowed": False,
            "provenance": "every generated turn retains week, day, speaker, and stable source_id",
            "isolation": "each arm uses a temporary character-memory root that is sent to OS trash after the run",
            "side_effects": "no cron, publish, Blob, or production character writes",
        }
    _validate_assumptions(assumptions)
    return {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "status": "plan_only",
        "execution_performed": False,
        "provider_calls": 0,
        "budget_usd": 0,
        "character_roster": list(CHARACTER_ROSTER),
        "weekly_memory_slots_per_arm": len(CHARACTER_ROSTER) * len(WEEK_PLAN),
        "assumptions": assumptions,
        "weeks": [
            {
                **week,
                "arms": [
                    {
                        "name": arm,
                        "memory_input": "empty" if arm == ARMS[0] else "prior weekly memory for this character only",
                        "memory_output": "none" if arm == ARMS[0] else "one provenance-linked character memory per week",
                    }
                    for arm in ARMS
                ],
            }
            for week in WEEK_PLAN
        ],
        "source_id_policy": "derive stable IDs from experiment, arm, ISO week, day, turn index, speaker, and exact generated text; retain IDs through each weekly memory",
        "memory_retention_policy": "persist one character slot each week in the harness-owned treatment store; pass read-back records to the next treatment week; carry prior memory references through weeks with no observed dialogue without inventing an event; send roots to OS trash after the run",
        "measurement": [
            "voice distinctiveness by named character",
            "grounding against the cited source turns",
            "whether a prior memory changes later-week dialogue",
            "prompt and memory token counts recorded per character and week",
        ],
        "limitations": [
            "This artifact does not run full-week simulations or model calls.",
            "Seeds are fixed for reproducibility but only control random choices when an offline adapter honors them.",
            "A future execution requires an explicitly reviewed test adapter and a separate cost decision before any paid calls.",
        ],
    }


def write_plan(output: Path, assumptions: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = build_plan(assumptions)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return plan


def stable_source_id(arm: str, week: str, day: str, index: int, speaker: str, text: str) -> str:
    if arm not in ARMS or not all(isinstance(value, str) and value for value in (week, day, speaker, text)):
        raise ValueError("source provenance requires a known arm, week, day, and speaker")
    if day not in {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}:
        raise ValueError(f"unknown dialogue day {day!r}")
    if not isinstance(index, int) or index < 0:
        raise ValueError("turn index must be a non-negative integer")
    basis = "\0".join((EXPERIMENT_ID, arm, week, day, str(index), speaker, text))
    return "turn_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class FakeAdapters:
    """Injected callbacks; `kind=fake` is a test convention, not a sandbox."""

    kind: str
    provider_calls_allowed: bool
    simulate_week: Callable[[dict[str, Any], str, Path, dict[str, list[dict[str, Any]]]], dict[str, Any]]
    write_memory: Callable[[str, dict[str, Any]], dict[str, Any]]


def _character_store_key(character: str) -> str:
    return "".join(char.lower() for char in character if char.isalnum())


def _persist_memory_record(store_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = store_root / "characters" / _character_store_key(record["character"]) / f"{record['week']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"memory slot already exists for {record['character']} in {record['week']}")
    path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    persisted = json.loads(path.read_text(encoding="utf-8"))
    if persisted != record:
        raise ValueError(f"persisted memory did not round-trip for {record['character']} in {record['week']}")
    return persisted


def _load_prior_memory_records(
    store_root: Path, prior_weeks: list[str]
) -> dict[str, list[dict[str, Any]]]:
    records_by_character = {character: [] for character in CHARACTER_ROSTER}
    for character in CHARACTER_ROSTER:
        for week in prior_weeks:
            path = store_root / "characters" / _character_store_key(character) / f"{week}.json"
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot read persisted memory for {character} in {week}: {exc}") from exc
            if not isinstance(record, dict) or record.get("character") != character or record.get("week") != week:
                raise ValueError(f"persisted memory identity mismatch for {character} in {week}")
            if not isinstance(record.get("memory_id"), str) or not record["memory_id"]:
                raise ValueError(f"persisted memory ID missing for {character} in {week}")
            if not isinstance(record.get("status"), str) or "text" not in record:
                raise ValueError(f"persisted memory content/status missing for {character} in {week}")
            records_by_character[character].append(record)
    return records_by_character


def execute_fake_chain(
    plan: dict[str, Any],
    adapters: FakeAdapters,
    simulator_state: Any,
) -> dict[str, Any]:
    """Exercise orchestration with fake adapters under isolated temp roots.

    No real simulation adapter exists by design. The `fake` marker is a caller
    convention, not a sandbox. Since arbitrary callbacks cannot be proven
    offline here, result metadata reports provider-call status as unverified.
    """
    _validate_assumptions(plan.get("assumptions", {}))
    if plan.get("schema_version") != 1:
        raise ValueError("plan schema_version must be 1")
    if plan.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("plan experiment_id does not match the fixed experiment")
    if plan.get("status") != "plan_only":
        raise ValueError("input must be an unexecuted plan-only artifact")
    if plan.get("execution_performed") is not False or plan.get("provider_calls") != 0:
        raise ValueError("input plan must be unexecuted with zero provider calls")
    if plan.get("budget_usd") != 0:
        raise ValueError("plan budget must remain zero")
    if plan.get("weeks") != build_plan()["weeks"]:
        raise ValueError("plan weeks, concepts, seeds, and arm definitions must match the frozen scenarios")
    if plan.get("character_roster") != list(CHARACTER_ROSTER):
        raise ValueError("plan must contain the fixed character roster")
    if any([arm.get("name") for arm in row.get("arms", [])] != list(ARMS) for row in plan["weeks"]):
        raise ValueError("each week must contain the fixed control and memory arms")
    if adapters.kind != "fake" or adapters.provider_calls_allowed is not False:
        raise ValueError("only explicitly marked zero-provider fake adapters are supported")
    if not hasattr(simulator_state, "CHARACTERS_DIR") or not hasattr(simulator_state, "_system_prompt_cache") or not hasattr(simulator_state, "DAY_STAGE_DIRECTIONS"):
        raise ValueError("simulator state lacks required isolation and restoration surfaces")

    output: dict[str, Any] = {
        "experiment_id": plan["experiment_id"],
        "arms": {},
        "prompt_injection_verified": False,
        "memory_delivery": "read_back_records_passed_to_injected_callback; model_prompt_visibility_unverified",
    }
    original_characters_dir = simulator_state.CHARACTERS_DIR
    original_cache = dict(simulator_state._system_prompt_cache)
    original_directions = dict(simulator_state.DAY_STAGE_DIRECTIONS)

    def new_isolated_root(prefix: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix=prefix))
        isolated_roots.append(root)
        return root

    def run_arm(arm: str, memory_store_root: Path | None = None) -> dict[str, Any]:
        rows = []
        seen_memory_ids: set[str] = set()
        for week_index, week in enumerate(plan["weeks"]):
            # Every simulated week gets a fresh character root. Only the
            # treatment arm has a distinct harness-owned memory store.
            root = new_isolated_root(f"mpr-memory-{arm}-{week['week']}-")
            simulator_state.CHARACTERS_DIR = root
            simulator_state._system_prompt_cache.clear()
            prior_weeks = [row["week"] for row in plan["weeks"][:week_index]] if arm == ARMS[1] else []
            prior_memory_records = (
                _load_prior_memory_records(memory_store_root, prior_weeks)
                if memory_store_root is not None else {character: [] for character in CHARACTER_ROSTER}
            )
            prior_memory_ids = {
                character: [records[-1]["memory_id"]]
                for character, records in prior_memory_records.items()
                if records
            }
            prior_memory_records_snapshot = copy.deepcopy(prior_memory_records)
            prior_memory_records_for_callback = copy.deepcopy(prior_memory_records)
            week_directions = dict(simulator_state.DAY_STAGE_DIRECTIONS)
            try:
                result = adapters.simulate_week(week, arm, root, prior_memory_records_for_callback)
            finally:
                simulator_state.DAY_STAGE_DIRECTIONS.clear()
                simulator_state.DAY_STAGE_DIRECTIONS.update(week_directions)
            turns = result.get("turns")
            if not isinstance(turns, list):
                raise ValueError(f"{week['week']}: fake simulation must return a turns list")
            unknown_speakers = sorted({
                turn.get("speaker") for turn in turns
                if isinstance(turn, dict)
                and isinstance(turn.get("speaker"), str)
                and turn.get("speaker") not in CHARACTER_ROSTER
            })
            if unknown_speakers:
                raise ValueError(f"{week['week']}: speakers outside the fixed roster: {unknown_speakers}")
            normalized_turns = []
            for index, turn in enumerate(turns):
                if not isinstance(turn, dict) or not all(isinstance(turn.get(key), str) and turn[key] for key in ("day", "speaker", "text")):
                    raise ValueError(f"{week['week']}: malformed simulated turn at index {index}")
                normalized_turns.append({**turn, "week": week["week"], "source_id": stable_source_id(arm, week["week"], turn["day"], index, turn["speaker"], turn["text"])})
            source_ids = [turn["source_id"] for turn in normalized_turns]
            if len(source_ids) != len(set(source_ids)):
                raise ValueError(f"{week['week']}: duplicate source IDs")
            memory_records = []
            if arm == ARMS[1]:
                for speaker in CHARACTER_ROSTER:
                    attended_days = {turn["day"] for turn in normalized_turns if turn["speaker"] == speaker}
                    observations = [turn for turn in normalized_turns if turn["day"] in attended_days]
                    observed_turns_snapshot = copy.deepcopy(observations)
                    observed_source_ids_snapshot = tuple(turn["source_id"] for turn in observed_turns_snapshot)
                    slot_status = "observed" if observations else "no_new_evidence"
                    expected_prior_ids = list(prior_memory_ids.get(speaker, []))
                    record = adapters.write_memory(speaker, {
                        "week": week["week"],
                        "status": slot_status,
                        "source_ids": list(observed_source_ids_snapshot),
                        "turns": copy.deepcopy(observed_turns_snapshot),
                        "prior_memory_ids": copy.deepcopy(expected_prior_ids),
                    })
                    if not isinstance(record, dict):
                        raise ValueError(f"{week['week']}: memory writer must return a structured memory record")
                    memory_id = record.get("memory_id")
                    if not isinstance(memory_id, str) or not memory_id:
                        raise ValueError(f"{week['week']}: memory record requires a stable memory_id")
                    if memory_id in seen_memory_ids:
                        raise ValueError(f"{week['week']}: duplicate memory_id {memory_id!r} makes memory references ambiguous")
                    seen_memory_ids.add(memory_id)
                    if record.get("status") != slot_status:
                        raise ValueError(f"{week['week']}: memory status must be {slot_status!r} for {speaker}")
                    raw_cited_source_ids = record.get("source_ids")
                    if not isinstance(raw_cited_source_ids, list) or any(not isinstance(source_id, str) for source_id in raw_cited_source_ids):
                        raise ValueError(f"{week['week']}: memory source_ids must be a list of strings")
                    cited_source_ids = copy.deepcopy(raw_cited_source_ids)
                    if len(cited_source_ids) != len(set(cited_source_ids)):
                        raise ValueError(f"{week['week']}: memory source_ids must not contain duplicates")
                    if slot_status == "observed" and (
                        not cited_source_ids or not set(cited_source_ids).issubset(observed_source_ids_snapshot)
                    ):
                        raise ValueError(f"{week['week']}: observed memory source IDs must be a non-empty subset of the character's observed turns")
                    if slot_status == "no_new_evidence" and cited_source_ids:
                        raise ValueError(f"{week['week']}: no-evidence memory source IDs must be empty")
                    if record.get("prior_memory_ids") != expected_prior_ids:
                        raise ValueError(f"{week['week']}: memory record must retain the expected prior memory IDs")
                    memory_text = record.get("text")
                    if slot_status == "observed" and (not isinstance(memory_text, str) or not memory_text.strip()):
                        raise ValueError(f"{week['week']}: observed memory requires non-empty text")
                    if slot_status == "no_new_evidence" and memory_text is not None:
                        raise ValueError(f"{week['week']}: no-evidence memory text must be null")
                    memory_record = {
                        "memory_id": memory_id,
                        "text": memory_text,
                        "status": slot_status,
                        "source_ids": cited_source_ids,
                        "prior_memory_ids": expected_prior_ids,
                        "character": speaker,
                        "week": week["week"],
                        "observed_source_ids": list(observed_source_ids_snapshot),
                    }
                    if memory_store_root is None:
                        raise ValueError("treatment memory store is required to persist generated records")
                    persisted_record = _persist_memory_record(memory_store_root, memory_record)
                    memory_records.append(persisted_record)
            rows.append({
                "week": week["week"],
                "source_ids": source_ids,
                "turns": normalized_turns,
                "prior_memory_ids": prior_memory_ids,
                "prior_memory_records_provided_to_callback": prior_memory_records_snapshot,
                "memory_records": memory_records,
            })
        return {"weeks": rows}

    isolated_roots: list[Path] = []
    try:
        control = run_arm(ARMS[0])
        treatment_memory_store = new_isolated_root("mpr-memory-treatment-store-")
        treatment = run_arm(ARMS[1], treatment_memory_store)
        output["arms"] = {ARMS[0]: control, ARMS[1]: treatment}
        output["execution_performed"] = True
        output["provider_calls"] = "unverified_in_injected_callbacks"
        output["provider_calls_verified"] = False
        return output
    finally:
        primary_error = sys.exc_info()[1]
        simulator_state.CHARACTERS_DIR = original_characters_dir
        simulator_state._system_prompt_cache.clear()
        simulator_state._system_prompt_cache.update(original_cache)
        simulator_state.DAY_STAGE_DIRECTIONS.clear()
        simulator_state.DAY_STAGE_DIRECTIONS.update(original_directions)
        cleanup_errors = []
        for root in isolated_roots:
            try:
                _send_to_trash(root)
            except Exception as exc:
                cleanup_errors.append(exc)
        if cleanup_errors:
            errors = ([primary_error] if primary_error is not None else []) + cleanup_errors
            raise BaseExceptionGroup("memory experiment failed during simulation and/or root cleanup", errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write plan JSON here; default is stdout")
    args = parser.parse_args(argv)
    try:
        plan = build_plan()
        rendered = json.dumps(plan, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
