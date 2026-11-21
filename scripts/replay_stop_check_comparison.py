"""Replay Haiku against Jev-scored lab transcripts without changing the lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.utils import model_router
from backend.utils.atomic import atomic_write
from backend.utils.stop_check import HAIKU_MODEL, StopCheckError, check_scene_done
from scripts import simulate_dialogue_week as simulate_module

MAX_REPLAY_CALLS = 20
# Total wall-clock seconds for one replayed Haiku request. Passing it makes the
# router disable SDK transport retries (max_retries=0), so each admitted attempt
# is exactly one HTTP request and the MAX_REPLAY_CALLS cap counts real requests.
REPLAY_HAIKU_TIMEOUT_S = 60.0


def _openrouter_id(model: str) -> str | None:
    """Repository-normalized OpenRouter id for a provider-prefixed model, or None
    when the router has no such id (unknown provider, unmapped or local model)."""
    try:
        routed = model_router.parse_model(model)
        return model_router.openrouter_model_id(routed.provider, routed.model)
    except RuntimeError:
        return None


# The only Haiku the replay will call: the production dialogue model's id after
# the router's own normalization. Equal ids such as
# anthropic/claude-haiku-4-5-20251001 and openrouter/anthropic/claude-haiku-4.5
# both normalize to this; any other model is refused before a paid call.
SUPPORTED_HAIKU_ID = _openrouter_id(HAIKU_MODEL)


class ReplayError(RuntimeError):
    """The source result cannot support a bounded same-input replay."""


def _require_objective(objective: object) -> None:
    """The historical objective the source was scored against; replay never
    substitutes the current config's objective for it."""
    if not isinstance(objective, str) or not objective.strip():
        raise ReplayError("objective must be a non-blank string")


class ReplayBudgetAbort(BaseException):
    """Refuses a model_router attempt past MAX_REPLAY_CALLS.

    Derives from BaseException, like LabBudgetAbort, so stop_check's
    `except Exception` cannot wrap it into a StopCheckError and count it as a
    Haiku error.
    """


@contextmanager
def _replay_call_budget(limit: int) -> Iterator[dict[str, int]]:
    """Count every model_router attempt (including empty-stop retries) for the
    replay scope. The refused attempt is never sent. A previously installed
    pre-call hook is chained first and restored on exit."""
    state = {"admitted": 0}

    def guard(provider: str, model: str, prompt_bytes: int) -> None:
        if state["admitted"] >= limit:
            raise ReplayBudgetAbort(f"replay would exceed {limit} model calls")
        if previous is not None:
            previous(provider, model, prompt_bytes)
        state["admitted"] += 1

    previous = model_router.set_pre_call_hook(guard)
    try:
        yield state
    finally:
        model_router.set_pre_call_hook(previous)


def _done(
    verdict: dict[str, Any],
    *,
    decided_threshold: float,
    pushback_threshold: float,
    require_pushback: bool,
) -> bool:
    # Mirrors simulate_dialogue_week's production rule for STOP_CHECK.
    return verdict["decided"] >= decided_threshold and (
        not require_pushback or verdict["pushback"] >= pushback_threshold
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
        if not isinstance(message, dict):
            raise ReplayError("every scored message must be a JSON object")
        character = message.get("character")
        text_value = message.get("message")
        model = message.get("model")
        if (
            not isinstance(character, str)
            or not character.strip()
            or not isinstance(text_value, str)
            or not text_value.strip()
            or not isinstance(model, str)
            or not model.strip()
        ):
            raise ReplayError(
                "every scored message needs non-empty character, message, and model strings"
            )
        lines.append(f"{character.split()[0]}: {text_value}")
        models.add(model)
    if len(models) != 1:
        raise ReplayError(f"scored transcript has multiple dialogue models: {sorted(models)}")
    model = models.pop()
    if _openrouter_id(model) != SUPPORTED_HAIKU_ID:
        raise ReplayError(f"scored transcript dialogue model is not the supported Haiku: {model!r}")
    return lines, model


def replay(
    source: dict[str, Any],
    *,
    objective: str,
    max_samples: int,
    max_errors: int,
    decided_threshold: float,
    pushback_threshold: float,
    require_pushback: bool,
) -> dict[str, Any]:
    """Run Haiku on final Jev-scored states, stopping after repeated errors.

    Every candidate is checked against `objective`, the objective the source
    was scored under. At most MAX_REPLAY_CALLS model_router attempts are sent.
    If the budget runs out, the in-flight sample is dropped and the receipt is
    marked `call_budget_exhausted`."""
    _require_objective(objective)
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
    if not isinstance(require_pushback, bool):
        raise ReplayError("require_pushback must be a bool")
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

    candidates = []
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ReplayError("every source pair must be an object")
        jev_log = pair.get("variant_stop_check_log")
        messages = pair.get("variant_messages")
        if not isinstance(jev_log, list):
            raise ReplayError("variant_stop_check_log must be a list of verdicts")
        if not jev_log:
            continue
        if not isinstance(messages, list):
            raise ReplayError("a Jev-scored pair has no variant_messages list")
        final_jev = jev_log[-1]
        if not isinstance(final_jev, dict):
            raise ReplayError("the final stop-check verdict must be an object")
        if final_jev.get("provider") != "jev":
            raise ReplayError("the final variant stop-check verdict is not from Jev")
        final_tick = final_jev.get("tick")
        if isinstance(final_tick, bool) or not isinstance(final_tick, int):
            raise ReplayError("the final Jev verdict has no integer tick")
        for field in ("decided", "pushback"):
            value = final_jev.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= value <= 1
            ):
                raise ReplayError(f"the final Jev {field} must be a number between 0 and 1")
        model = final_jev.get("model")
        if not isinstance(model, str) or not model.strip():
            raise ReplayError("the final Jev model must be a non-empty string")
        lines, haiku_model = _scored_transcript(messages, final_tick=final_tick)
        day = final_jev.get("day")
        if day != source_stage:
            raise ReplayError(
                f"Jev verdict day {day!r} does not match source stage {source_stage!r}"
            )
        candidates.append(
            {
                "pair": pair,
                "messages": messages,
                "final_jev": final_jev,
                "final_tick": final_tick,
                "lines": lines,
                "haiku_model": haiku_model,
                "day": day,
                "objective": objective,
            }
        )
    if not candidates:
        raise ReplayError("source result has no Jev-scored pairs")

    samples = []
    error_count = 0
    call_budget_exhausted = False
    with _replay_call_budget(MAX_REPLAY_CALLS) as budget:
        for candidate in candidates[:max_samples]:
            pair = candidate["pair"]
            messages = candidate["messages"]
            final_jev = candidate["final_jev"]
            final_tick = candidate["final_tick"]
            lines = candidate["lines"]
            haiku_model = candidate["haiku_model"]
            day = candidate["day"]
            objective = candidate["objective"]

            haiku_verdict = None
            haiku_error = None
            try:
                haiku = check_scene_done(
                    lines,
                    "haiku",
                    day=day,
                    objective=objective,
                    haiku_model=haiku_model,
                    timeout_s=REPLAY_HAIKU_TIMEOUT_S,
                )
            except StopCheckError as exc:
                haiku_error = str(exc)
                error_count += 1
            except ReplayBudgetAbort:
                # The refused attempt was never sent; drop this sample rather than
                # record it as a Haiku error or a disagreement.
                call_budget_exhausted = True
                break
            else:
                haiku_verdict = {
                    "decided": haiku.decided,
                    "pushback": haiku.pushback,
                    "done": _done(
                        {"decided": haiku.decided, "pushback": haiku.pushback},
                        decided_threshold=decided_threshold,
                        pushback_threshold=pushback_threshold,
                        require_pushback=require_pushback,
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
                    require_pushback=require_pushback,
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
            if error_count == max_errors:
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
        "objective": objective,
        "thresholds": {
            "decided": decided_threshold,
            "pushback": pushback_threshold,
            "require_pushback": require_pushback,
        },
        "requested_sample_cap": max_samples,
        "systematic_error_stop": max_errors,
        "call_budget_exhausted": call_budget_exhausted,
        "model_call_attempts": budget["admitted"],
        "sample_count": len(samples),
        "comparable_count": len(comparable),
        "haiku_error_count": len(samples) - len(comparable),
        "agreement_count": agreements,
        "agreement_rate": agreements / len(comparable) if comparable else None,
        "samples": samples,
    }


def _reserve_output(path: Path) -> None:
    """Exclusively create the receipt path with a small marker. The marker stays
    as evidence if the run fails; a successful run replaces it."""
    if not path.parent.is_dir():
        raise ReplayError(f"output directory does not exist: {path.parent}")
    try:
        with open(path, "x", encoding="utf-8") as handle:
            handle.write(json.dumps({"status": "reserved", "pid": os.getpid()}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ReplayError(f"output already exists and will not be overwritten: {path}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="historical conversation-lab A/B result")
    parser.add_argument("output", type=Path, help="standalone JSON comparison receipt")
    parser.add_argument(
        "--objective",
        required=True,
        help="the objective the source was scored under (not the current config's)",
    )
    parser.add_argument("--max-samples", type=int, default=MAX_REPLAY_CALLS)
    parser.add_argument("--max-errors", type=int, default=3)
    parser.add_argument("--decided-threshold", type=float, required=True)
    parser.add_argument("--pushback-threshold", type=float, required=True)
    parser.add_argument(
        "--require-pushback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="require pushback >= --pushback-threshold to call a scene done (default: on)",
    )
    args = parser.parse_args()
    _require_objective(args.objective)
    _reserve_output(args.output)
    source_bytes = args.source.read_bytes()
    result = replay(
        json.loads(source_bytes),
        objective=args.objective,
        max_samples=args.max_samples,
        max_errors=args.max_errors,
        decided_threshold=args.decided_threshold,
        pushback_threshold=args.pushback_threshold,
        require_pushback=args.require_pushback,
    )
    result["source_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    atomic_write(args.output, json.dumps(result, indent=2) + "\n")
    print(
        f"compared {result['sample_count']} transcripts: "
        f"{result['comparable_count']} comparable, "
        f"{result['agreement_count']} done agreements, "
        f"{result['haiku_error_count']} Haiku errors"
    )


if __name__ == "__main__":
    main()
