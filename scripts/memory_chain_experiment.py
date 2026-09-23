#!/usr/bin/env python3
"""Create an offline plan for a three-week character-memory chain experiment.

Execution is deliberately restricted to injected fake adapters. The CLI only
writes a plan; it never invokes the dialogue simulator or a provider.
"""

from __future__ import annotations

import argparse
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
        "memory_retention_policy": "write one character slot each week; carry prior memory references forward through weeks with no observed dialogue, without inventing an event; send roots to OS trash after the run",
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
    simulate_week: Callable[[dict[str, Any], str, Path], dict[str, Any]]
    write_memory: Callable[[str, dict[str, Any], Path], str]


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
    if plan.get("execution_performed") is not False or plan.get("provider_calls") != 0:
        raise ValueError("input plan must be unexecuted with zero provider calls")
    if [row.get("week") for row in plan.get("weeks", [])] != [row["week"] for row in WEEK_PLAN]:
        raise ValueError("plan must contain the fixed three chronological weeks")
    if plan.get("character_roster") != list(CHARACTER_ROSTER):
        raise ValueError("plan must contain the fixed character roster")
    if any([arm.get("name") for arm in row.get("arms", [])] != list(ARMS) for row in plan["weeks"]):
        raise ValueError("each week must contain the fixed control and memory arms")
    if adapters.kind != "fake" or adapters.provider_calls_allowed is not False:
        raise ValueError("only explicitly marked zero-provider fake adapters are supported")
    if not hasattr(simulator_state, "CHARACTERS_DIR") or not hasattr(simulator_state, "_system_prompt_cache") or not hasattr(simulator_state, "DAY_STAGE_DIRECTIONS"):
        raise ValueError("simulator state lacks required isolation and restoration surfaces")

    output: dict[str, Any] = {"experiment_id": plan["experiment_id"], "arms": {}}
    original_characters_dir = simulator_state.CHARACTERS_DIR
    original_cache = dict(simulator_state._system_prompt_cache)
    original_directions = dict(simulator_state.DAY_STAGE_DIRECTIONS)

    def run_arm(root: Path, arm: str) -> dict[str, Any]:
        simulator_state.CHARACTERS_DIR = root
        simulator_state._system_prompt_cache.clear()
        rows = []
        previous_memory_ids: dict[str, list[str]] = {}
        for week in plan["weeks"]:
            simulator_state._system_prompt_cache.clear()
            week_directions = dict(simulator_state.DAY_STAGE_DIRECTIONS)
            try:
                result = adapters.simulate_week(week, arm, root)
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
            new_memory_ids: dict[str, list[str]] = {}
            if arm == ARMS[1]:
                for speaker in CHARACTER_ROSTER:
                    attended_days = {turn["day"] for turn in normalized_turns if turn["speaker"] == speaker}
                    observations = [turn for turn in normalized_turns if turn["day"] in attended_days]
                    observed_source_ids = [turn["source_id"] for turn in observations]
                    slot_status = "observed" if observations else "no_new_evidence"
                    memory_id = adapters.write_memory(speaker, {
                        "week": week["week"],
                        "status": slot_status,
                        "source_ids": observed_source_ids,
                        "turns": observations,
                        "prior_memory_ids": list(previous_memory_ids.get(speaker, [])),
                    }, root)
                    if not isinstance(memory_id, str) or not memory_id:
                        raise ValueError(f"{week['week']}: memory writer must return a stable memory ID")
                    new_memory_ids[speaker] = [memory_id]
            rows.append({"week": week["week"], "source_ids": source_ids, "turns": normalized_turns, "prior_memory_ids": dict(previous_memory_ids)})
            previous_memory_ids = new_memory_ids
        return {"weeks": rows}

    isolated_roots: list[Path] = []
    try:
        control_root = Path(tempfile.mkdtemp(prefix="mpr-memory-control-"))
        isolated_roots.append(control_root)
        control = run_arm(control_root, ARMS[0])
        treatment_root = Path(tempfile.mkdtemp(prefix="mpr-memory-treatment-"))
        isolated_roots.append(treatment_root)
        treatment = run_arm(treatment_root, ARMS[1])
        output["arms"] = {ARMS[0]: control, ARMS[1]: treatment}
        output["execution_performed"] = True
        output["provider_calls"] = "unverified_in_injected_callbacks"
        output["provider_calls_verified"] = False
        return output
    finally:
        simulator_state.CHARACTERS_DIR = original_characters_dir
        simulator_state._system_prompt_cache.clear()
        simulator_state._system_prompt_cache.update(original_cache)
        simulator_state.DAY_STAGE_DIRECTIONS.clear()
        simulator_state.DAY_STAGE_DIRECTIONS.update(original_directions)
        for root in isolated_roots:
            _send_to_trash(root)


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
