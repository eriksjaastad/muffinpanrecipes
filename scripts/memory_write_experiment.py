#!/usr/bin/env python3
"""Render offline memory-writing prompts from a frozen memory-lab manifest.

This is a prompt-design artifact generator only. It never invokes a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


TARGET_EPISODE = "2026-W35"
PLANNED_MODEL = "claude-haiku-4-5-20251001"
OUTPUT_TOKEN_TARGET = 160
EXPECTED_CHARACTER_COUNT = 6

COMMON_SYSTEM = """Write one weekly memory for {character}, based only on the accepted dialogue evidence supplied by the user. The same evidence is provided to both memory formats. Do not invent events, facts, quotes, motives, feelings, relationships, or unresolved issues. Distinguish observable evidence from interpretation. Keep the complete memory under 160 output tokens. Cite evidence using its exact source ID wherever the format asks for support."""

FORMAT_A = """Format A — existing recap form. Write exactly two sentences in third person, past tense, under 40 words total. Focus on relationships and emotions rather than technical specifications, and include one specific interpersonal moment when the evidence supports one. Use plain hyphens and straight quotes. Do not include source IDs in the prose; the experiment record retains them separately."""

FORMAT_B = """Format B — personal, source-linked form. Write a compact memory in this exact labeled structure: Observed: one concrete event, citing source ID(s). Inference: the character's interpretation, explicitly marked as an inference and citing source ID(s), or 'none supported'. Stance: a supported change in stance, or 'no change evidenced' when this week's evidence does not establish one. Open thread: an explicitly unresolved issue supported by source ID(s), or 'none'. Do not claim a prior stance, feeling, or unresolved issue that the supplied evidence does not establish."""

FORMAT_A_SYSTEM = "You write concise character summaries. Exactly 2 sentences, under 40 words."


def _render_user(character: str, week: str, rows: list[dict[str, Any]]) -> str:
    lines = [
        f"Character: {character}",
        f"Episode: {week}",
        "Accepted dialogue you observed this week (source IDs are stable provenance):",
    ]
    if not rows:
        lines.append("[No accepted dialogue evidence was recorded for this character this week.]")
    for row in rows:
        lines.append(
            f"[{row['source_id']}] {row['day']} — {row['character']}: {row['message']}"
        )
    return "\n".join(lines)


def _prompt_pair(character: str, episode_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    user = _render_user(character, episode_id, rows)
    common_system = COMMON_SYSTEM.format(character=character)
    return {
        "A": {"system": f"{common_system}\n\n{FORMAT_A_SYSTEM}\n\n{FORMAT_A}", "user": user},
        "B": {"system": f"{common_system}\n\n{FORMAT_B}", "user": user},
    }


def build_experiment(manifest: dict[str, Any], episode_id: str = TARGET_EPISODE) -> dict[str, Any]:
    if manifest.get("mode") != "offline_dry_run" or manifest.get("generation_performed") is not False:
        raise ValueError("input must be a no-generation memory-lab manifest")
    episodes = manifest.get("episodes")
    characters = manifest.get("characters")
    if not isinstance(episodes, list) or not isinstance(characters, dict):
        raise ValueError("manifest is missing episodes or characters")
    matching = [episode for episode in episodes if isinstance(episode, dict) and episode.get("episode_id") == episode_id]
    if len(matching) != 1:
        raise ValueError(f"manifest must contain exactly one episode {episode_id}")
    source_episode = matching[0]
    prompts: list[dict[str, Any]] = []
    for character, character_data in sorted(characters.items()):
        slots = character_data.get("weekly_memory_slots") if isinstance(character_data, dict) else None
        if not isinstance(slots, list):
            raise ValueError(f"{character}: missing weekly memory slots")
        slot_matches = [slot for slot in slots if isinstance(slot, dict) and slot.get("episode_id") == episode_id]
        if len(slot_matches) != 1:
            raise ValueError(f"{character}: expected exactly one slot for {episode_id}")
        observations = slot_matches[0].get("observations")
        if not isinstance(observations, list):
            raise ValueError(f"{character}: observations for {episode_id} must be a list")
        rows = []
        for row in observations:
            if not isinstance(row, dict) or not isinstance(row.get("source_id"), str):
                raise ValueError(f"{character}: malformed source observation for {episode_id}")
            if row.get("episode_id") != episode_id:
                raise ValueError(f"{character}: future or foreign episode leaked into {episode_id} observations")
            rows.append(row)
        source_ids = list(dict.fromkeys(row["source_id"] for row in rows))
        prompts.append({
            "character": character,
            "episode_id": episode_id,
            "source_ids": source_ids,
            "observation_count": len(rows),
            "target_output_tokens": OUTPUT_TOKEN_TARGET,
            "planned_model": PLANNED_MODEL,
            "model_called": False,
            "arms": _prompt_pair(character, episode_id, rows),
        })
    if len(prompts) != EXPECTED_CHARACTER_COUNT:
        raise ValueError(f"expected {EXPECTED_CHARACTER_COUNT} characters for {episode_id}; found {len(prompts)}")

    source_manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "experiment": "memory_write_format_ab_dry_run",
        "mode": "offline_prompt_render_only",
        "generation_performed": False,
        "paid_mode_available": False,
        "episode_id": episode_id,
        "source_manifest_sha256": source_manifest_hash,
        "source_episode_sha256": source_episode.get("sha256"),
        "planned_model": PLANNED_MODEL,
        "target_output_tokens_per_prompt": OUTPUT_TOKEN_TARGET,
        "character_count": len(prompts),
        "arm_design": {
            "A": "two-sentence third-person recap",
            "B": "source-linked personal perspective with marked inference, evidence-based stance, and real open thread only",
            "controlled_fields": ["episode evidence", "planned model", "output token target"],
        },
        "prompts": prompts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="offline manifest emitted by scripts/memory_lab.py")
    parser.add_argument("--episode-id", default=TARGET_EPISODE, help=f"frozen experiment week (default: {TARGET_EPISODE})")
    parser.add_argument("--output", type=Path, help="write rendered prompt record here (default: stdout)")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = build_experiment(manifest, args.episode_id)
        rendered = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
