"""Replay Haiku against Jev-scored lab transcripts without changing the lab."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.utils.stop_check import StopCheckError, check_scene_done
from scripts import simulate_dialogue_week as simulate_module

MAX_REPLAY_CALLS = 20


class ReplayError(RuntimeError):
    """The source result cannot support a bounded same-input replay."""


def _done(
    verdict: dict[str, Any],
    *,
    decided_threshold: float,
    pushback_threshold: float,
) -> bool:
    return (
        verdict["decided"] >= decided_threshold
        and verdict["pushback"] >= pushback_threshold
    )


def _scored_transcript(
    messages: list[dict[str, Any]],
    *,
    final_tick: int,
) -> tuple[list[str], str]:
    scored_count = final_tick + 1
    if final_tick < 0 or scored_count > len(messages):
        raise ReplayError(
            f"final Jev tick {final_tick} cannot index {len(messages)} variant messages"
        )
    lines = []
    models = set()
    for message in messages[:scored_count]:
        character = message.get("character")
        text_value = message.get("message")
        model = message.get("model")
        if (
            not isinstance(character, str)
            or not character
            or not isinstance(text_value, str)
            or not isinstance(model, str)
            or not model
        ):
            raise ReplayError(
                "every scored message needs non-empty character, message, and model strings"
            )
        lines.append(f"{character.split()[0]}: {text_value}")
        models.add(model)
    if len(models) != 1:
        raise ReplayError(f"scored transcript has multiple dialogue models: {sorted(models)}")
    return lines, models.pop()


def replay(
    source: dict[str, Any],
    *,
    max_samples: int,
    max_errors: int,
    decided_threshold: float,
    pushback_threshold: float,
) -> dict[str, Any]:
    """Run Haiku on final Jev-scored states, stopping after repeated errors."""
    if not 1 <= max_samples <= MAX_REPLAY_CALLS:
        raise ReplayError(f"max_samples must be between 1 and {MAX_REPLAY_CALLS}")
    if not 1 <= max_errors <= max_samples:
        raise ReplayError("max_errors must be between 1 and max_samples")
    for name, value in (
        ("decided_threshold", decided_threshold),
        ("pushback_threshold", pushback_threshold),
    ):
        if isinstance(value, bool) or not 0 <= value <= 1:
            raise ReplayError(f"{name} must be between 0 and 1")
    if source.get("prior_days") is not None:
        raise ReplayError(
            "source result used seeded prior days, which are not embedded in each pair"
        )
    source_stage = source.get("stage")
    if source_stage not in simulate_module.DAY_MEETING_GOAL:
        raise ReplayError(f"source result has unsupported stage: {source_stage!r}")
    pairs = source.get("pairs")
    if not isinstance(pairs, list):
        raise ReplayError("source result has no pairs list")

    samples = []
    error_count = 0
    for pair in pairs:
        jev_log = pair.get("variant_stop_check_log")
        messages = pair.get("variant_messages")
        if not isinstance(jev_log, list) or not jev_log:
            continue
        if not isinstance(messages, list):
            raise ReplayError("a Jev-scored pair has no variant_messages list")
        final_jev = jev_log[-1]
        if final_jev.get("provider") != "jev":
            raise ReplayError("the final variant stop-check verdict is not from Jev")
        final_tick = final_jev.get("tick")
        if isinstance(final_tick, bool) or not isinstance(final_tick, int):
            raise ReplayError("the final Jev verdict has no integer tick")

        lines, haiku_model = _scored_transcript(messages, final_tick=final_tick)
        day = final_jev.get("day")
        if day != source_stage:
            raise ReplayError(
                f"Jev verdict day {day!r} does not match source stage {source_stage!r}"
            )
        goal = simulate_module.DAY_MEETING_GOAL.get(day)
        if goal is None:
            raise ReplayError(f"unsupported replay day: {day!r}")
        objective = goal["objective"]

        haiku_verdict = None
        haiku_error = None
        try:
            haiku = check_scene_done(
                lines,
                "haiku",
                day=day,
                objective=objective,
                haiku_model=haiku_model,
            )
        except StopCheckError as exc:
            haiku_error = str(exc)
            error_count += 1
        else:
            haiku_verdict = {
                "decided": haiku.decided,
                "pushback": haiku.pushback,
                "done": _done(
                    {"decided": haiku.decided, "pushback": haiku.pushback},
                    decided_threshold=decided_threshold,
                    pushback_threshold=pushback_threshold,
                ),
                "model": haiku.model,
            }
        jev = {
            "decided": final_jev["decided"],
            "pushback": final_jev["pushback"],
            "done": _done(
                final_jev,
                decided_threshold=decided_threshold,
                pushback_threshold=pushback_threshold,
            ),
            "model": final_jev["model"],
        }
        samples.append(
            {
                "scenario_id": pair.get("scenario_id"),
                "run_index": pair.get("run_index"),
                "day": day,
                "objective": objective,
                "source_message_count": len(messages),
                "scored_tick": final_tick,
                "scored_message_count": len(lines),
                "transcript_sha256": hashlib.sha256("\n".join(lines).encode()).hexdigest(),
                "transcript": lines,
                "jev": jev,
                "haiku": haiku_verdict,
                "haiku_error": haiku_error,
                "agrees_on_done": (
                    jev["done"] == haiku_verdict["done"] if haiku_verdict else None
                ),
            }
        )
        if len(samples) == max_samples or error_count == max_errors:
            break

    if not samples:
        raise ReplayError("source result has no Jev-scored pairs")
    comparable = [sample for sample in samples if sample["agrees_on_done"] is not None]
    agreements = sum(sample["agrees_on_done"] for sample in comparable)
    return {
        "method": (
            "messages through each final Jev tick replayed once through the same "
            "Haiku dialogue model"
        ),
        "incumbent_answer_retained": True,
        "production_behavior_changed": False,
        "source_prior_days_seeded": False,
        "source_stage": source_stage,
        "thresholds": {
            "decided": decided_threshold,
            "pushback": pushback_threshold,
            "require_pushback": True,
        },
        "requested_sample_cap": max_samples,
        "systematic_error_stop": max_errors,
        "sample_count": len(samples),
        "comparable_count": len(comparable),
        "haiku_error_count": len(samples) - len(comparable),
        "agreement_count": agreements,
        "agreement_rate": agreements / len(comparable) if comparable else None,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="historical conversation-lab A/B result")
    parser.add_argument("output", type=Path, help="standalone JSON comparison receipt")
    parser.add_argument("--max-samples", type=int, default=MAX_REPLAY_CALLS)
    parser.add_argument("--max-errors", type=int, default=3)
    parser.add_argument("--decided-threshold", type=float, required=True)
    parser.add_argument("--pushback-threshold", type=float, required=True)
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    result = replay(
        json.loads(source_bytes),
        max_samples=args.max_samples,
        max_errors=args.max_errors,
        decided_threshold=args.decided_threshold,
        pushback_threshold=args.pushback_threshold,
    )
    result["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"compared {result['sample_count']} transcripts: "
        f"{result['comparable_count']} comparable, "
        f"{result['agreement_count']} done agreements, "
        f"{result['haiku_error_count']} Haiku errors"
    )


if __name__ == "__main__":
    main()
