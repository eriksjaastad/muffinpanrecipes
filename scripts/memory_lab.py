#!/usr/bin/env python3
"""Build an offline, provenance-preserving manifest for memory experiments.

This script reads completed stage dialogue only. It never calls a model and
never writes character data or remote storage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


DEFAULT_BUDGETS = (80, 160, 300)
TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


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


def collect_episode(path: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    try:
        episode = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read episode {path}: {exc}") from exc
    if not isinstance(episode, dict):
        raise ValueError(f"{path}: episode JSON must be an object")
    episode_id = _episode_id(episode, path)
    stages = episode.get("stages", {})
    if not isinstance(stages, dict):
        raise ValueError(f"{path}: stages must be an object")

    accepted: list[dict[str, Any]] = []
    observations: dict[str, list[dict[str, Any]]] = {}
    for day, stage in stages.items():
        if not isinstance(stage, dict) or stage.get("status") != "complete":
            continue
        dialogue = stage.get("dialogue", [])
        if not isinstance(dialogue, list):
            raise ValueError(f"{path}: {day}.dialogue must be a list")
        for turn_index, turn in enumerate(dialogue):
            if not isinstance(turn, dict):
                continue
            character, message = turn.get("character"), turn.get("message")
            if not isinstance(character, str) or not character.strip() or not isinstance(message, str):
                continue
            character, message = character.strip(), message.strip()
            if not message:
                continue
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
    # Each participant gets the whole accepted scene evidence. Attribution is
    # explicit so later memory-writing experiments can model perception.
    for character in sorted({row["character"] for row in accepted}):
        observations[character] = [
            {**row, "perspective": "self" if row["character"] == character else "heard"}
            for row in accepted
        ]
    return {"episode_id": episode_id, "source_path": str(path), "accepted_messages": accepted}, observations


def build_manifest(paths: list[Path], budgets: tuple[int, ...] = DEFAULT_BUDGETS) -> dict[str, Any]:
    episodes: list[dict[str, Any]] = []
    by_character: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        summary, observations = collect_episode(path)
        episodes.append({key: value for key, value in summary.items() if key != "accepted_messages"} | {
            "accepted_message_count": len(summary["accepted_messages"]),
            "source_ids": [row["source_id"] for row in summary["accepted_messages"]],
        })
        for character, rows in observations.items():
            by_character.setdefault(character, []).extend(rows)

    characters: dict[str, Any] = {}
    for character, rows in sorted(by_character.items()):
        source_ids = [row["source_id"] for row in rows]
        candidate_schema = {
            "text": None,
            "source_ids": [],
            "created_from_episode_ids": [],
            "memory_kind": None,
        }
        characters[character] = {
            "observations": rows,
            "candidate_memory_schema": candidate_schema,
            "budget_variants": [render_candidate(candidate_schema, budget) for budget in budgets],
            "available_source_ids": source_ids,
        }
    return {
        "schema_version": 1,
        "mode": "offline_dry_run",
        "generation_performed": False,
        "token_estimator": "regex word/punctuation approximation; not provider tokenizer",
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
    parser.add_argument("--output", type=Path, help="write manifest here (default: stdout)")
    args = parser.parse_args(argv)
    if any(budget <= 0 for budget in args.budgets):
        parser.error("budgets must be positive")
    try:
        manifest = build_manifest(args.episodes, tuple(args.budgets))
        rendered = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
