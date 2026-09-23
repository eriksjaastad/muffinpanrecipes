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
MAX_RESPONSE_TOKENS = 220
MEMORY_PROSE_TOKEN_BAND = (80, 120)
EXPECTED_CHARACTER_COUNT = 6
MAX_PROFILE_CONTEXT_CHARS = 1200
DEFAULT_PROFILES = Path(__file__).resolve().parents[1] / "backend" / "data" / "agent_personalities.json"

COMMON_SYSTEM = """Write one weekly memory for {character}, based on the accepted dialogue evidence and stable persona context supplied by the user. The same evidence is provided to both memory policies. Persona context is identity framing, not event evidence: do not report profile history as something that happened this week or assert that a relationship changed unless dialogue supports it. Do not invent events, facts, quotes, motives, feelings, relationships, or unresolved issues. Every factual event or interpretation must be traceable to the supplied dialogue source IDs. Aim for 80–120 provider tokens of memory prose, excluding citation and evidence-map overhead, when the evidence supports that length. If not, write less rather than repeat or invent. Keep the complete response, including labels and citations, under the 220-token hard cap."""

FORMAT_A = """Policy A — recap bundle. Write exactly two sentences in third person, past tense. Focus on supported interpersonal meaning rather than technical specifications. Do not include source IDs in the prose. After the two-sentence memory, add a separate Evidence map with one entry per sentence and only the source ID(s) that support that sentence. Do not add a sentence that lacks direct evidence."""

FORMAT_B = """Policy B — source-linked perspective-card bundle. Write a compact memory in this exact labeled structure: Observed: one concrete event, citing source ID(s). Inference: the character's interpretation, explicitly marked as an inference and citing source ID(s), or 'none supported'. Stance: a supported change in stance, citing source ID(s), or 'no change evidenced' when this week's evidence does not establish one. Open thread: an explicitly unresolved issue supported by source ID(s), or 'none'. Do not claim a prior stance, feeling, or unresolved issue that the supplied evidence does not establish."""

FORMAT_A_SYSTEM = "You write concise character summaries. The memory itself is exactly 2 sentences."


def _profile_context(
    character: str,
    profile: dict[str, Any],
    observed_profiles: list[dict[str, Any]],
    profile_sha256: str,
) -> dict[str, Any]:
    contradictions = profile.get("internal_contradictions", [])
    if not isinstance(contradictions, list):
        contradictions = []
    personality_framing = []
    for index, text in enumerate(contradictions[:1]):
        if isinstance(text, str) and text.strip():
            excerpt = text.strip()
            if len(excerpt) > 180:
                excerpt = excerpt[:177].rsplit(" ", 1)[0] + "..."
            personality_framing.append({
                "text": excerpt,
                "profile_pointer": f"internal_contradictions[{index}]",
            })
    if not personality_framing:
        backstory = profile.get("backstory", "")
        if isinstance(backstory, str) and backstory.strip():
            excerpt = backstory.strip().split(". ", 1)[0].rstrip(".")
            personality_framing.append({
                "text": excerpt[:220],
                "profile_pointer": "backstory[0]",
            })

    relationship_map = profile.get("relationships", {})
    if not isinstance(relationship_map, dict):
        relationship_map = {}
    observed_roles = {
        record["role"]: record for record in observed_profiles
        if record.get("name") != character and isinstance(record.get("role"), str)
    }
    relationships = []
    for role, counterpart in observed_roles.items():
        text = relationship_map.get(role)
        if not isinstance(text, str) or not text.strip():
            continue
        excerpt = text.strip().split(". ", 1)[0].rstrip(".")
        if len(excerpt) > 100:
            excerpt = excerpt[:97].rsplit(" ", 1)[0] + "..."
        relationships.append({
            "counterpart": counterpart["name"],
            "counterpart_role": role,
            "profile_pointer": f"relationships.{role}",
            "framing_excerpt": excerpt,
        })
    context = {
        "name": character,
        "role": profile.get("role"),
        "authored_personality_framing": personality_framing,
        "relationship_framing": relationships[:2],
        "profile_source_sha256": profile_sha256,
        "profile_pointer": f"agent_personalities.json[{profile.get('role')}]",
    }
    rendered_size = len(json.dumps(context, ensure_ascii=False, sort_keys=True))
    if rendered_size > MAX_PROFILE_CONTEXT_CHARS:
        raise ValueError(f"{character}: profile context exceeds {MAX_PROFILE_CONTEXT_CHARS} characters")
    return context


def _render_profile(context: dict[str, Any]) -> str:
    lines = [
        "Stable authored persona context (identity framing only; NOT evidence about this week's events):",
        f"Role: {context['role']}",
    ]
    lines.extend(
        f"Personality framing: {item['text']} [profile: {item['profile_pointer']}]"
        for item in context["authored_personality_framing"]
    )
    lines.extend(
        f"Relationship framing with {item['counterpart']} ({item['counterpart_role']}): "
        f"{item['framing_excerpt']} [profile: {item['profile_pointer']}]"
        for item in context["relationship_framing"]
    )
    return "\n".join(lines)


def _render_user(
    character: str, week: str, rows: list[dict[str, Any]], profile_context: dict[str, Any]
) -> str:
    lines = [
        f"Character: {character}",
        f"Episode: {week}",
        _render_profile(profile_context),
        "Accepted dialogue you observed this week (source IDs are stable provenance):",
    ]
    if not rows:
        lines.append("[No accepted dialogue evidence was recorded for this character this week.]")
    for row in rows:
        lines.append(
            f"[{row['source_id']}] {row['day']} — {row['character']}: {row['message']}"
        )
    return "\n".join(lines)


def _prompt_pair(
    character: str, episode_id: str, rows: list[dict[str, Any]], profile_context: dict[str, Any]
) -> dict[str, Any]:
    user = _render_user(character, episode_id, rows, profile_context)
    common_system = COMMON_SYSTEM.format(character=character)
    return {
        "A": {"system": f"{common_system}\n\n{FORMAT_A_SYSTEM}\n\n{FORMAT_A}", "user": user},
        "B": {"system": f"{common_system}\n\n{FORMAT_B}", "user": user},
    }


def load_profiles(path: Path = DEFAULT_PROFILES) -> tuple[list[dict[str, Any]], str]:
    try:
        raw = path.read_bytes()
        profiles = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read persona profiles {path}: {exc}") from exc
    if not isinstance(profiles, list):
        raise ValueError(f"{path}: persona profiles must be a JSON list")
    return profiles, hashlib.sha256(raw).hexdigest()


def build_experiment(
    manifest: dict[str, Any],
    profiles: list[dict[str, Any]],
    profile_sha256: str,
    episode_id: str = TARGET_EPISODE,
) -> dict[str, Any]:
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
    if not isinstance(profiles, list):
        raise ValueError("persona profiles must be a list")
    profiles_by_name = {
        profile["name"]: profile for profile in profiles
        if isinstance(profile, dict) and isinstance(profile.get("name"), str)
    }
    if len(profiles_by_name) != len(profiles):
        raise ValueError("persona profiles contain missing or duplicate names")
    missing_profiles = sorted(set(characters) - set(profiles_by_name))
    if missing_profiles:
        raise ValueError(f"persona profiles missing characters: {', '.join(missing_profiles)}")

    speakers_by_character: dict[str, set[str]] = {}
    for character, character_data in characters.items():
        slots = character_data.get("weekly_memory_slots") if isinstance(character_data, dict) else None
        if not isinstance(slots, list):
            raise ValueError(f"{character}: missing weekly memory slots")
        slot_matches = [slot for slot in slots if isinstance(slot, dict) and slot.get("episode_id") == episode_id]
        if len(slot_matches) != 1 or not isinstance(slot_matches[0].get("observations"), list):
            raise ValueError(f"{character}: expected one valid slot for {episode_id}")
        speakers_by_character[character] = {
            row.get("character") for row in slot_matches[0]["observations"]
            if isinstance(row, dict) and isinstance(row.get("character"), str)
        }

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
        other_profiles = [
            profiles_by_name[speaker]
            for speaker in sorted(speakers_by_character[character])
            if speaker in profiles_by_name and speaker != character
        ]
        profile_context = _profile_context(
            character, profiles_by_name[character], other_profiles, profile_sha256
        )
        profile_context_chars = len(_render_profile(profile_context))
        prompts.append({
            "character": character,
            "episode_id": episode_id,
            "profile_context": profile_context,
            "profile_context_chars": profile_context_chars,
            "source_ids": source_ids,
            "observation_count": len(rows),
            "target_memory_prose_token_band": list(MEMORY_PROSE_TOKEN_BAND),
            "hard_max_response_tokens": MAX_RESPONSE_TOKENS,
            "planned_model": PLANNED_MODEL,
            "model_called": False,
            "arms": _prompt_pair(character, episode_id, rows, profile_context),
        })
    if len(prompts) != EXPECTED_CHARACTER_COUNT:
        raise ValueError(f"expected {EXPECTED_CHARACTER_COUNT} characters for {episode_id}; found {len(prompts)}")

    source_manifest_hash = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "experiment": "memory_write_policy_bundle_ab_dry_run",
        "mode": "offline_prompt_render_only",
        "generation_performed": False,
        "paid_mode_available": False,
        "episode_id": episode_id,
        "source_manifest_sha256": source_manifest_hash,
        "persona_profile_sha256": profile_sha256,
        "source_episode_sha256": source_episode.get("sha256"),
        "planned_model": PLANNED_MODEL,
        "target_memory_prose_token_band": list(MEMORY_PROSE_TOKEN_BAND),
        "hard_max_response_tokens_per_prompt": MAX_RESPONSE_TOKENS,
        "length_evaluation": "When paid generation is authorized, provider response usage output_tokens is the billing count for the full response. Measure prose separately from citation/evidence metadata with one consistent extraction and count method for both arms; if using count_tokens, subtract the same empty-message framing baseline and label that measure as normalized prose length, not billable output usage. Compare memory quality only for outputs with matched actual prose lengths; report unmatched outputs separately.",
        "character_count": len(prompts),
        "arm_design": {
            "comparison_type": "bundled memory-writing policy comparison",
            "A": "recap bundle: two-sentence third-person recap plus a per-sentence evidence map",
            "B": "source-linked perspective-card bundle: observed event, marked interpretation, evidence-based stance, cited open thread only when real",
            "shared_inputs": ["episode evidence", "profile context", "planned model", "memory prose target band", "hard response cap"],
            "attribution_limit": "Structure, perspective, content requirements, and citation obligations differ together. Any observed gain belongs to the bundled policy; this comparison cannot identify an individual mechanism.",
        },
        "prompts": prompts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="offline manifest emitted by scripts/memory_lab.py")
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES, help="persona profile JSON to freeze into prompts")
    parser.add_argument("--episode-id", default=TARGET_EPISODE, help=f"frozen experiment week (default: {TARGET_EPISODE})")
    parser.add_argument("--output", type=Path, help="write rendered prompt record here (default: stdout)")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        profiles, profile_sha256 = load_profiles(args.profiles)
        result = build_experiment(manifest, profiles, profile_sha256, args.episode_id)
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
