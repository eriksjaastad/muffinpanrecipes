#!/usr/bin/env python3
"""Run the frozen W35 memory prompt artifact behind the conversation budget guard.

The default mode only validates and reports the artifact. Paid execution requires
an explicit flag, a pinned artifact SHA-256, and fresh or bound resume files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.conversation_budget import AnthropicBudgetGuard

MODEL = "claude-haiku-4-5-20251001"
EXPERIMENT = "memory_write_policy_bundle_ab_dry_run"
HARD_MAX_TOKENS = 220
BUDGET_USD = 5
SOURCE_ID_RE = re.compile(r"msg_[0-9a-f]{20}")


class ExperimentError(ValueError):
    pass


def _read_artifact(path: Path, expected_sha256: str) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or digest != expected_sha256:
        raise ExperimentError("prompt artifact SHA-256 does not match --artifact-sha256")
    try:
        artifact = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError("prompt artifact is not valid UTF-8 JSON") from exc
    _validate_artifact(artifact)
    return artifact, digest


def _validate_artifact(artifact: Any) -> None:
    if not isinstance(artifact, dict):
        raise ExperimentError("prompt artifact must be an object")
    required = {
        "mode": "offline_prompt_render_only",
        "generation_performed": False,
        "paid_mode_available": False,
        "experiment": EXPERIMENT,
        "episode_id": "2026-W35",
        "planned_model": MODEL,
        "hard_max_response_tokens_per_prompt": HARD_MAX_TOKENS,
        "character_count": 6,
    }
    for key, expected in required.items():
        if artifact.get(key) != expected:
            raise ExperimentError(f"prompt artifact has invalid {key}")
    prompts = artifact.get("prompts")
    if not isinstance(prompts, list) or len(prompts) != 6:
        raise ExperimentError("prompt artifact must contain exactly six character records")
    seen: set[str] = set()
    for record in prompts:
        if not isinstance(record, dict) or not isinstance(record.get("character"), str):
            raise ExperimentError("prompt artifact contains a malformed character record")
        name = record["character"]
        if name in seen or record.get("episode_id") != "2026-W35":
            raise ExperimentError("prompt artifact contains a duplicate or non-W35 record")
        seen.add(name)
        if record.get("planned_model") != MODEL or record.get("hard_max_response_tokens") != HARD_MAX_TOKENS:
            raise ExperimentError(f"{name}: model or response cap differs from the approved plan")
        if not isinstance(record.get("source_ids"), list) or not all(
            isinstance(source_id, str) and SOURCE_ID_RE.fullmatch(source_id)
            for source_id in record["source_ids"]
        ):
            raise ExperimentError(f"{name}: source_ids must be a list of stable message IDs")
        arms = record.get("arms")
        if not isinstance(arms, dict) or set(arms) != {"A", "B"}:
            raise ExperimentError(f"{name}: exactly A and B prompts are required")
        if not isinstance(arms["A"], dict) or not isinstance(arms["B"], dict):
            raise ExperimentError(f"{name}: malformed prompt arms")
        for arm in ("A", "B"):
            prompt = arms[arm]
            if set(prompt) != {"system", "user"} or not all(isinstance(prompt[k], str) for k in prompt):
                raise ExperimentError(f"{name}/{arm}: prompt must contain system and user text")
            expected_label = "Policy A — recap bundle" if arm == "A" else "Policy B — source-linked perspective-card bundle"
            if expected_label not in prompt["system"] or "80–120 provider tokens" not in prompt["system"]:
                raise ExperimentError(f"{name}/{arm}: prompt policy or shared length target is missing")
        if arms["A"]["user"] != arms["B"]["user"]:
            raise ExperimentError(f"{name}: A/B source and persona inputs differ")
        if not arms["A"]["user"].startswith(f"Character: {name}\nEpisode: 2026-W35\n"):
            raise ExperimentError(f"{name}: prompt is not bound to its W35 character")
        input_source_ids = set(SOURCE_ID_RE.findall(arms["A"]["user"]))
        if input_source_ids != set(record["source_ids"]):
            raise ExperimentError(f"{name}: source_ids do not match the frozen prompt evidence")


def _atomic_json(path: Path, value: dict[str, Any], *, create: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if create:
        if path.exists():
            raise ExperimentError(f"refusing to replace existing checkpoint: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        # Keep the temporary evidence if replacement failed; do not unlink it.
        raise


def _extract_prose(
    arm: str, raw: str, allowed_source_ids: set[str] | None = None
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    allowed_source_ids = allowed_source_ids or set()
    citations = set(SOURCE_ID_RE.findall(raw))
    if not citations:
        return None, "response contains no source IDs", None
    if not citations <= allowed_source_ids:
        return None, "response cites source IDs outside this character's evidence", None
    if arm == "A":
        marker = re.search(r"(?im)^\s*Evidence map\s*:\s*", raw)
        if not marker:
            return None, "missing evidence map", None
        prose = raw[: marker.start()].strip()
        sentences = re.split(r"(?<=[.!?])\s+", prose)
        if len(sentences) != 2 or not all(sentence.strip() for sentence in sentences):
            return None, "recap is not exactly two sentences", None
        evidence = raw[marker.end():]
        if len(SOURCE_ID_RE.findall(evidence)) < 2:
            return None, "evidence map lacks source IDs", None
        return SOURCE_ID_RE.sub("", prose).strip(), None, {"format": "two_sentence_recap"}
    labels = ("Observed", "Inference", "Stance", "Open thread")
    matches: list[tuple[int, int, str]] = []
    for label in labels:
        found = list(re.finditer(rf"(?im)^\s*{re.escape(label)}\s*:\s*", raw))
        if len(found) != 1:
            return None, f"missing or duplicate {label} field", None
        matches.append((found[0].start(), found[0].end(), label))
    matches.sort()
    contents = {
        label: raw[end:matches[index + 1][0] if index + 1 < len(matches) else len(raw)].strip()
        for index, (_, end, label) in enumerate(matches)
    }
    observed = contents["Observed"]
    if not SOURCE_ID_RE.search(observed):
        return None, "Observed field lacks a source ID", None
    inference = contents["Inference"].lower()
    if inference not in {"none supported", "none supported."} and not SOURCE_ID_RE.search(contents["Inference"]):
        return None, "Inference field lacks a source ID", None
    stance = contents["Stance"].lower()
    if not stance.startswith("no change evidenced") and not SOURCE_ID_RE.search(contents["Stance"]):
        return None, "changed Stance field lacks a source ID", None
    thread = contents["Open thread"].lower()
    if thread not in {"none", "none."} and not SOURCE_ID_RE.search(contents["Open thread"]):
        return None, "Open thread lacks a source ID", None
    # Keep only field value prose. Structure and citations remain separately
    # recoverable from the preserved raw response and explicit structure field.
    normalized = {
        label: re.sub(r"\s*\[\s*msg_[0-9a-f]{20}\s*\]", "", contents[label]).strip()
        for label in labels
    }
    normalized = {label: SOURCE_ID_RE.sub("", value).strip() for label, value in normalized.items()}
    prose = "\n".join(normalized[label] for label in labels)
    return prose.strip(), None, {"format": "perspective_card", "fields": normalized}


def _usage_fields(response: Any) -> dict[str, Any]:
    usage = response.usage
    data = usage.model_dump(exclude_none=True) if hasattr(usage, "model_dump") else dict(usage)
    return {key: data[key] for key in ("input_tokens", "output_tokens", "service_tier", "inference_geo") if key in data}


def _response_text(response: Any) -> str:
    blocks = getattr(response, "content", None)
    if not isinstance(blocks, list):
        raise ExperimentError("provider response has no content list")
    texts = [block.text for block in blocks if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)]
    if not texts:
        raise ExperimentError("provider response has no text block")
    return "".join(texts)


def _base_state(artifact: dict[str, Any], digest: str, ledger_path: Path) -> dict[str, Any]:
    calls = [
        {"call_id": f"{item['character']}:{arm}", "character": item["character"], "arm": arm}
        for item in artifact["prompts"] for arm in ("A", "B")
    ]
    return {
        "schema_version": 1,
        "experiment": "memory_write_policy_bundle_ab_paid_run",
        "episode_id": "2026-W35",
        "prompt_artifact_sha256": digest,
        "ledger_path": ledger_path.name,
        "ledger_path_binding_sha256": hashlib.sha256(str(ledger_path.resolve()).encode()).hexdigest(),
        "ledger_created_at": None,
        "model": MODEL,
        "max_tokens": HARD_MAX_TOKENS,
        "temperature": 0.4,
        "calls": calls,
        "next_call_index": 0,
        "in_flight_call_id": None,
        "responses": [],
        "status": "ready",
    }


def _validate_state(state: Any, digest: str, ledger_path: Path) -> dict[str, Any]:
    if not isinstance(state, dict) or state.get("schema_version") != 1:
        raise ExperimentError("checkpoint schema is malformed")
    if state.get("prompt_artifact_sha256") != digest:
        raise ExperimentError("checkpoint is bound to a different prompt artifact")
    if (
        state.get("ledger_path") != ledger_path.name
        or state.get("ledger_path_binding_sha256") != hashlib.sha256(str(ledger_path.resolve()).encode()).hexdigest()
    ):
        raise ExperimentError("checkpoint is bound to a different ledger path")
    if state.get("model") != MODEL or state.get("max_tokens") != HARD_MAX_TOKENS:
        raise ExperimentError("checkpoint model or response cap mismatch")
    calls, responses = state.get("calls"), state.get("responses")
    if not isinstance(calls, list) or len(calls) != 12 or not isinstance(responses, list):
        raise ExperimentError("checkpoint call/response records are malformed")
    # Call schedule is reconstructed from the checkpoint's six names, requiring
    # every character to have exactly one A and one B in adjacent positions.
    names = [calls[index].get("character") for index in range(0, 12, 2) if isinstance(calls[index], dict)]
    if len(names) != 6 or len(set(names)) != 6:
        raise ExperimentError("checkpoint character call schedule is malformed")
    expected_calls = [
        {"call_id": f"{name}:{arm}", "character": name, "arm": arm}
        for name in names for arm in ("A", "B")
    ]
    if calls != expected_calls:
        raise ExperimentError("checkpoint call schedule is malformed")
    if state.get("in_flight_call_id") is not None:
        raise ExperimentError("checkpoint has an ambiguous in-flight request; refusing to repeat it")
    if len(responses) > 12 or state.get("next_call_index") != len(responses):
        raise ExperimentError("checkpoint progress is inconsistent")
    for index, response in enumerate(responses):
        if not isinstance(response, dict) or response.get("call_id") != calls[index].get("call_id"):
            raise ExperimentError("checkpoint contains an invalid completed response")
        if (
            response.get("character") != calls[index]["character"]
            or response.get("arm") != calls[index]["arm"]
            or not isinstance(response.get("raw_response_text"), str)
            or not response["raw_response_text"]
            or not isinstance(response.get("usage"), dict)
            or response.get("prose_parse_status") not in {"parsed", "unscored"}
        ):
            raise ExperimentError("checkpoint completed response is incomplete")
    return state


def execute(artifact: dict[str, Any], digest: str, ledger_path: Path, checkpoint_path: Path, *, resume: bool = False) -> dict[str, Any]:
    if ledger_path.resolve() == checkpoint_path.resolve():
        raise ExperimentError("ledger and checkpoint paths must differ")
    if resume:
        if not ledger_path.is_file() or not checkpoint_path.is_file():
            raise ExperimentError("resume requires both the original ledger and checkpoint")
        try:
            state = _validate_state(json.loads(checkpoint_path.read_text(encoding="utf-8")), digest, ledger_path)
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExperimentError("cannot read bound checkpoint or ledger") from exc
        if ledger.get("status") != "active" or ledger.get("budget_microusd") != 5_000_000:
            raise ExperimentError("resume ledger is stopped or has a different budget")
        if ledger.get("created_at") != state.get("ledger_created_at"):
            raise ExperimentError("checkpoint is bound to a different ledger instance")
        if len(ledger.get("calls", [])) != len(state["responses"]):
            raise ExperimentError("ledger generation history does not match saved responses")
        if any(
            not isinstance(call, dict)
            or call.get("model") != MODEL
            or call.get("phase") != "ab"
            or call.get("status") != "settled"
            for call in ledger.get("calls", [])
        ):
            raise ExperimentError("resume ledger contains a non-settled or foreign generation call")
    else:
        if ledger_path.exists() or checkpoint_path.exists():
            raise ExperimentError("fresh run requires unused ledger and checkpoint paths")
        state = _base_state(artifact, digest, ledger_path)
        _atomic_json(checkpoint_path, state, create=True)

    import anthropic
    with AnthropicBudgetGuard(ledger_path, budget_usd=BUDGET_USD, create=not resume) as guard:
        client = anthropic.Anthropic()
        if not resume:
            state["ledger_created_at"] = json.loads(ledger_path.read_text(encoding="utf-8"))["created_at"]
            _atomic_json(checkpoint_path, state)
        with guard.phase("ab"):
            for index in range(state["next_call_index"], len(state["calls"])):
                call = state["calls"][index]
                character = call["character"]
                arm = call["arm"]
                record = next(item for item in artifact["prompts"] if item["character"] == character)
                prompt = record["arms"][arm]
                state["status"] = "running"
                state["in_flight_call_id"] = call["call_id"]
                _atomic_json(checkpoint_path, state)
                try:
                    response = client.messages.create(
                        model=MODEL,
                        max_tokens=HARD_MAX_TOKENS,
                        temperature=0.4,
                        system=prompt["system"],
                        messages=[{"role": "user", "content": prompt["user"]}],
                    )
                    raw_text = _response_text(response)
                    usage = _usage_fields(response)
                    parsed, parse_error, structure = _extract_prose(
                        arm, raw_text, set(record["source_ids"])
                    )
                    item: dict[str, Any] = {
                        "call_id": call["call_id"],
                        "character": character,
                        "arm": arm,
                        "raw_response_text": raw_text,
                        "usage": usage,
                        "stop_reason": getattr(response, "stop_reason", None),
                        "request_id": getattr(response, "_request_id", None),
                        "actual_cost_microusd": usage["input_tokens"] + 5 * usage["output_tokens"],
                        "prose_parse_status": "unscored" if parse_error else "parsed",
                        "prose_parse_error": parse_error,
                    }
                    state["responses"].append(item)
                    state["next_call_index"] = index + 1
                    state["in_flight_call_id"] = None
                    state["status"] = "complete" if state["next_call_index"] == 12 else "partial"
                    # First durable write after a response preserves the full
                    # raw text even if a later length-measurement call fails.
                    _atomic_json(checkpoint_path, state)
                    if parsed is not None:
                        item["memory_prose"] = parsed
                        item["memory_structure"] = structure
                        empty = client.messages.count_tokens(model=MODEL, messages=[{"role": "user", "content": ""}]).input_tokens
                        raw_count = client.messages.count_tokens(model=MODEL, messages=[{"role": "user", "content": raw_text}]).input_tokens
                        prose_count = client.messages.count_tokens(model=MODEL, messages=[{"role": "user", "content": parsed}]).input_tokens
                        item["normalized_text_lengths"] = {
                            "method": "Anthropic count_tokens input count minus same empty-user-message framing baseline; descriptive text length only, not output usage",
                            "response_text_tokens": max(0, raw_count - empty),
                            "memory_prose_tokens": max(0, prose_count - empty),
                        }
                    _atomic_json(checkpoint_path, state)
                except Exception as exc:
                    state["status"] = "stopped"
                    state["stop_reason"] = type(exc).__name__
                    _atomic_json(checkpoint_path, state)
                    guard.raise_if_stopped()
                    raise
            state["status"] = "complete"
            state["guard_summary"] = guard.summary()
            _atomic_json(checkpoint_path, state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--artifact-sha256", required=True)
    parser.add_argument("--execute", action="store_true", help="perform guarded paid calls; omitted means validation only")
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--output", type=Path, help="atomic checkpoint/results JSON")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        artifact, digest = _read_artifact(args.artifact, args.artifact_sha256)
        if not args.execute:
            if args.resume or args.ledger or args.output:
                raise ExperimentError("ledger/output/resume options require --execute")
            sys.stdout.write(json.dumps({"status": "validated_offline_only", "artifact_sha256": digest, "request_count": 12, "paid_call_count": 0}, indent=2) + "\n")
            return 0
        if args.ledger is None or args.output is None:
            raise ExperimentError("--execute requires --ledger and --output")
        state = execute(artifact, digest, args.ledger, args.output, resume=args.resume)
        sys.stdout.write(json.dumps({"status": state["status"], "completed_calls": len(state["responses"]), "checkpoint": str(args.output)}, indent=2) + "\n")
        return 0 if state["status"] == "complete" else 2
    except (OSError, json.JSONDecodeError, ExperimentError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
