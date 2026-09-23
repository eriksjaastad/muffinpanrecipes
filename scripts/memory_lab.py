#!/usr/bin/env python3
"""Build an offline, provenance-preserving manifest for memory experiments.

This script reads completed stage dialogue only. It never calls a model and
never writes character data or remote storage.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


DEFAULT_BUDGETS = (80, 160, 300)
WEEK_DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
ISO_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def estimate_tokens(text: str) -> int:
    """Use a stable, local approximation; record tokenizer limitations in reports."""
    return len(TOKEN_RE.findall(text))


def render_candidate(candidate: dict[str, Any], budget: int) -> dict[str, Any]:
    """Render a candidate slot and report whether its text exceeds a budget."""
    text = candidate.get("text")
    if text is not None and not isinstance(text, str):
        raise ValueError("candidate text must be a string or null")
    text = text or ""
    tokens = estimate_tokens(text)
    return {
        "budget_tokens": budget,
        "text": text,
        "estimated_tokens": tokens,
        "overflow": tokens > budget,
    }


def _source_id(episode_id: str, day: str, turn_index: int, character: str, message: str) -> str:
    basis = "\0".join((episode_id, day, str(turn_index), character, message))
    return "msg_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def _episode_id(episode: dict[str, Any], path: Path) -> str:
    value = episode.get("episode_id") or episode.get("id") or path.stem
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{path}: episode_id must be a non-empty string")
    return value.strip()


def _iso_week_key(episode_id: str) -> tuple[int, int]:
    match = ISO_WEEK_RE.fullmatch(episode_id)
    if not match:
        raise ValueError(f"episode_id {episode_id!r} must use ISO week format YYYY-Www")
    year, week = map(int, match.groups())
    try:
        monday = dt.date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError(f"invalid ISO week episode_id {episode_id!r}") from exc
    return (monday.year, monday.timetuple().tm_yday)


def collect_episode(path: Path, *, allow_partial: bool = False) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    try:
        raw_bytes = path.read_bytes()
        episode = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read episode {path}: {exc}") from exc
    if not isinstance(episode, dict):
        raise ValueError(f"{path}: episode JSON must be an object")
    episode_id = _episode_id(episode, path)
    stages = episode.get("stages", {})
    if not isinstance(stages, dict):
        raise ValueError(f"{path}: stages must be an object")

    complete_days = [day for day in WEEK_DAYS if isinstance(stages.get(day), dict) and stages[day].get("status") == "complete"]
    missing_days = [day for day in WEEK_DAYS if day not in complete_days]
    if missing_days and not allow_partial:
        raise ValueError(f"{path}: incomplete week; missing complete stages: {', '.join(missing_days)} (use --allow-partial to label it)")

    accepted: list[dict[str, Any]] = []
    for day in WEEK_DAYS:
        stage = stages.get(day)
        if not isinstance(stage, dict) or stage.get("status") != "complete":
            continue
        if "dialogue" not in stage:
            raise ValueError(f"{path}: {day}.dialogue is missing from a complete stage")
        dialogue = stage["dialogue"]
        if not isinstance(dialogue, list):
            raise ValueError(f"{path}: {day}.dialogue must be a list")
        for turn_index, turn in enumerate(dialogue):
            if not isinstance(turn, dict):
                raise ValueError(f"{path}: {day}.dialogue[{turn_index}] must be an object")
            character, message = turn.get("character"), turn.get("message")
            if not isinstance(character, str) or not character.strip() or not isinstance(message, str):
                raise ValueError(f"{path}: {day}.dialogue[{turn_index}] requires non-empty character and string message")
            character, message = character.strip(), message.strip()
            if not message:
                raise ValueError(f"{path}: {day}.dialogue[{turn_index}].message must not be empty")
            record = {
                "source_id": _source_id(episode_id, str(day), turn_index, character, message),
                "episode_id": episode_id,
                "day": str(day),
                "turn_index": turn_index,
                "character": character,
                "message": message,
                "estimated_tokens": estimate_tokens(message),
            }
            accepted.append(record)
    # Participation is tracked by day. A character receives the group turns
    # from days they spoke, never dialogue from days they did not attend.
    participants = {row["character"] for row in accepted}
    observations: dict[str, list[dict[str, Any]]] = {}
    for character in participants:
        attended_days = {row["day"] for row in accepted if row["character"] == character}
        observations[character] = [
            {**row, "perspective": "self" if row["character"] == character else "heard"}
            for row in accepted if row["day"] in attended_days
        ]
    return {
        "episode_id": episode_id,
        "source_path": str(path),
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "accepted_messages": accepted,
        "missing_days": missing_days,
    }, observations


def build_manifest(
    paths: list[Path], budgets: tuple[int, ...] = DEFAULT_BUDGETS, *, allow_partial: bool = False
) -> dict[str, Any]:
    episodes: list[dict[str, Any]] = []
    per_episode: list[tuple[str, dict[str, list[dict[str, Any]]]]] = []
    week_order: list[tuple[str, tuple[int, int]]] = []
    for path in paths:
        summary, observations = collect_episode(path, allow_partial=allow_partial)
        if any(episode["episode_id"] == summary["episode_id"] for episode in episodes):
            raise ValueError(f"duplicate episode_id {summary['episode_id']!r}; provide three distinct weeks")
        week_order.append((summary["episode_id"], _iso_week_key(summary["episode_id"])))
        episodes.append({key: value for key, value in summary.items() if key != "accepted_messages"} | {
            "accepted_message_count": len(summary["accepted_messages"]),
            "source_ids": [row["source_id"] for row in summary["accepted_messages"]],
        })
        per_episode.append((summary["episode_id"], observations))

    if [key for _, key in week_order] != sorted(key for _, key in week_order):
        raise ValueError("episode paths must be in chronological ISO week order")

    all_characters = sorted({character for _, observations in per_episode for character in observations})
    characters: dict[str, Any] = {}
    for character in all_characters:
        weekly_slots = []
        prior_slot_ids: list[str] = []
        for episode_id, observations in per_episode:
            slot_id = "mem_" + hashlib.sha256(f"{episode_id}\0{character}".encode("utf-8")).hexdigest()[:20]
            rows = observations.get(character, [])
            candidate_schema = {"text": None, "source_ids": [], "created_from_episode_ids": [], "memory_kind": None}
            weekly_slots.append({
                "slot_id": slot_id,
                "episode_id": episode_id,
                "observations": rows,
                "candidate_memory_schema": candidate_schema,
                "budget_variants": [render_candidate(candidate_schema, budget) for budget in budgets],
                "prior_slot_ids_available_after_creation": list(prior_slot_ids),
            })
            prior_slot_ids.append(slot_id)
        characters[character] = {
            "weekly_memory_slots": weekly_slots,
        }
    return {
        "schema_version": 1,
        "mode": "offline_dry_run",
        "generation_performed": False,
        "token_estimator": "regex word/punctuation approximation; not provider tokenizer",
        "token_cap_enforced": False,
        "provider_token_count_required_before_paid_generation": True,
        "partial_input": any(bool(episode["missing_days"]) for episode in episodes),
        "memory_policy_note": "Fading is a future prompt-selection policy; source evidence is retained.",
        "episode_count": len(episodes),
        "episodes": episodes,
        "memory_output_schema": {
            "text": "short character-specific memory text or null before generation",
            "source_ids": "source message IDs supporting the memory",
            "created_from_episode_ids": "episode IDs used to create it",
            "memory_kind": "episodic, relationship, preference, or open_thread (future experiment field)",
        },
        "characters": characters,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("episodes", nargs=3, type=Path, help="three episode JSON files")
    parser.add_argument("--budgets", nargs="+", type=int, default=list(DEFAULT_BUDGETS), help="candidate memory token budgets")
    parser.add_argument("--allow-partial", action="store_true", help="accept incomplete weeks and label them in the manifest")
    parser.add_argument("--output", type=Path, help="write manifest here (default: stdout)")
    args = parser.parse_args(argv)
    if any(budget <= 0 for budget in args.budgets):
        parser.error("budgets must be positive")
    try:
        manifest = build_manifest(args.episodes, tuple(args.budgets), allow_partial=args.allow_partial)
        rendered = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
