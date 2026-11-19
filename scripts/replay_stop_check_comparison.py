"""Replay Haiku against Jev-scored lab transcripts without changing the lab."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.utils.stop_check import StopCheckError, check_scene_done

DECIDED_THRESHOLD = 0.7
PUSHBACK_THRESHOLD = 0.5
MAX_REPLAY_CALLS = 20


class ReplayError(RuntimeError):
    """The source result cannot support a bounded same-input replay."""


def _done(verdict: dict[str, Any]) -> bool:
    return verdict["decided"] >= DECIDED_THRESHOLD and verdict["pushback"] >= PUSHBACK_THRESHOLD


def _transcript(messages: list[dict[str, Any]]) -> list[str]:
    lines = []
    for message in messages:
        character = message.get("character")
        text_value = message.get("message")
        if not isinstance(character, str) or not character or not isinstance(text_value, str):
            raise ReplayError("every variant message needs non-empty character and message strings")
        lines.append(f"{character.split()[0]}: {text_value}")
    if not lines:
        raise ReplayError("a replay transcript cannot be empty")
    return lines


def replay(source: dict[str, Any], *, max_samples: int) -> dict[str, Any]:
    """Run Haiku once on each final Jev-scored transcript in source."""
    if not 1 <= max_samples <= MAX_REPLAY_CALLS:
        raise ReplayError(f"max_samples must be between 1 and {MAX_REPLAY_CALLS}")
    pairs = source.get("pairs")
    if not isinstance(pairs, list):
        raise ReplayError("source result has no pairs list")

    samples = []
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

        lines = _transcript(messages)
        day = final_jev.get("day")
        if day != "wednesday":
            raise ReplayError(f"unsupported replay day: {day!r}")
        objective = "Choose the hero shot"
        haiku_verdict = None
        haiku_error = None
        try:
            haiku = check_scene_done(lines, "haiku", day=day, objective=objective)
        except StopCheckError as exc:
            haiku_error = str(exc)
        else:
            haiku_verdict = {
                "decided": haiku.decided,
                "pushback": haiku.pushback,
                "done": _done({"decided": haiku.decided, "pushback": haiku.pushback}),
                "model": haiku.model,
            }
        jev = {
            "decided": final_jev["decided"],
            "pushback": final_jev["pushback"],
            "done": _done(final_jev),
            "model": final_jev["model"],
        }
        samples.append(
            {
                "scenario_id": pair.get("scenario_id"),
                "run_index": pair.get("run_index"),
                "day": day,
                "objective": objective,
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
        if len(samples) == max_samples:
            break

    if not samples:
        raise ReplayError("source result has no Jev-scored pairs")
    comparable = [sample for sample in samples if sample["agrees_on_done"] is not None]
    agreements = sum(sample["agrees_on_done"] for sample in comparable)
    return {
        "method": "final Jev-scored transcript from each logged pair replayed once through Haiku",
        "incumbent_answer_retained": True,
        "production_behavior_changed": False,
        "thresholds": {
            "decided": DECIDED_THRESHOLD,
            "pushback": PUSHBACK_THRESHOLD,
            "require_pushback": True,
        },
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
    args = parser.parse_args()
    source_bytes = args.source.read_bytes()
    result = replay(json.loads(source_bytes), max_samples=args.max_samples)
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
