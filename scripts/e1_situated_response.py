#!/usr/bin/env python3
"""Score the preregistered E1 situated-response measurement offline.

The default development split keeps the three holdout pairs sealed until the
implementation and tests are committed. This script makes no provider calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from scripts.conversation_metrics import STOPWORDS, adjacency_continuity

EVIDENCE_SHA256 = "5519bfcbcea80058f4467c17dc89dc4cefa7d55125429110c651d080a044c875"
HUMAN_RESULT_SHA256 = "6b94a0a57e8fcec765ef1cbcb9cc1c8bc57c946bd65a828416dd68ac8d42e709"
DEVELOPMENT_PAIRS = (1, 2, 4, 5, 6, 7)
HOLDOUT_PAIRS = (3, 8, 9)
EVIDENCE_FLAGS = {
    1: ("room_continuity",),
    2: ("stock_language",),
    3: ("stock_language",),
    4: ("direct_response",),
    5: ("temporal_plausibility",),
    6: ("operational_plausibility",),
    7: ("stock_language", "platform_language"),
    8: ("room_continuity",),
    9: ("direct_response",),
}

ROOM_RESET_PATTERNS = (
    re.compile(r"\b(?:everybody|everyone)\s+(?:is\s+)?here\b", re.IGNORECASE),
    re.compile(r"\ball\s+(?:of\s+us\s+)?here\b", re.IGNORECASE),
)
ANSWER_CUES = frozenset(
    {
        "absolutely",
        "both",
        "correct",
        "definitely",
        "no",
        "nope",
        "right",
        "sure",
        "yes",
        "yeah",
        "yep",
    }
)
TOKEN_ALIASES = {
    "baked": "bake",
    "baking": "bake",
    "breaks": "break",
    "breaking": "break",
    "cropped": "crop",
    "cropping": "crop",
    "filled": "fill",
    "filling": "fill",
    "photos": "shot",
    "pictures": "shot",
    "shoot": "shot",
    "shooting": "shot",
    "shots": "shot",
}
_WORD_RE = re.compile(r"[a-z0-9']+")


def content_terms(text: str) -> set[str]:
    """Return frozen normalized content terms for adjacent-turn matching."""
    words = _WORD_RE.findall(text.lower())
    return {
        TOKEN_ALIASES.get(word, word)
        for word in words
        if word not in STOPWORDS and len(word) > 1
    }


def _room_reset_hits(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for turn, message in enumerate(messages, start=1):
        text = str(message.get("message") or "")
        phrases = [match.group(0) for pattern in ROOM_RESET_PATTERNS if (match := pattern.search(text))]
        if phrases:
            hits.append({"turn": turn, "phrases": phrases})
    return hits


def score_arm(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Score one transcript and retain inspectable evidence for every link."""
    links = []
    for index in range(1, len(messages)):
        previous = str(messages[index - 1].get("message") or "")
        current = str(messages[index].get("message") or "")
        shared_terms = sorted(content_terms(previous) & content_terms(current))
        current_words = _WORD_RE.findall(current.lower())
        answer_cue = current_words[0] if previous.rstrip().endswith("?") and current_words else None
        if answer_cue not in ANSWER_CUES:
            answer_cue = None
        links.append(
            {
                "turn": index + 1,
                "linked": bool(shared_terms or answer_cue),
                "shared_terms": shared_terms,
                "answer_cue": answer_cue,
            }
        )

    linked_count = sum(bool(link["linked"]) for link in links)
    room_reset_hits = _room_reset_hits(messages)
    return {
        "room_continuity_reset_count": len(room_reset_hits),
        "room_continuity_reset_hits": room_reset_hits,
        "adjacent_turn_uptake_rate": round(linked_count / len(links), 4) if links else 0.0,
        "adjacent_turn_linked_count": linked_count,
        "adjacent_turn_count": len(links),
        "adjacent_turn_evidence": links,
        "baseline_adjacency_share_with_link": adjacency_continuity(messages)["share_with_link"],
    }


def compare_arms(control: dict[str, Any], variant: dict[str, Any]) -> str:
    """Apply the frozen lexicographic comparison and return control/variant/tie."""
    control_resets = control["room_continuity_reset_count"]
    variant_resets = variant["room_continuity_reset_count"]
    if control_resets != variant_resets:
        return "control" if control_resets < variant_resets else "variant"
    control_uptake = control["adjacent_turn_uptake_rate"]
    variant_uptake = variant["adjacent_turn_uptake_rate"]
    if control_uptake == variant_uptake:
        return "tie"
    return "control" if control_uptake > variant_uptake else "variant"


def _verified_sha256(path: Path, expected_hash: str, label: str) -> str:
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"{label} hash mismatch: expected {expected_hash}, got {actual_hash}")
    return actual_hash


def score_evidence(path: Path, human_result_path: Path, split: str) -> dict[str, Any]:
    raw = path.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != EVIDENCE_SHA256:
        raise ValueError(f"evidence hash mismatch: expected {EVIDENCE_SHA256}, got {actual_hash}")
    human_result_hash = _verified_sha256(
        human_result_path, HUMAN_RESULT_SHA256, "human result"
    )
    evidence = json.loads(raw)
    wanted = DEVELOPMENT_PAIRS if split == "development" else HOLDOUT_PAIRS
    if split == "all":
        wanted = DEVELOPMENT_PAIRS + HOLDOUT_PAIRS
    human_picks = evidence["human_picks"]
    results = []
    for pair in evidence["pairs"]:
        pair_number = int(pair["run_index"])
        if pair_number not in wanted:
            continue
        control = score_arm(pair["control_messages"])
        variant = score_arm(pair["variant_messages"])
        measurement_pick = compare_arms(control, variant)
        human_pick = human_picks[str(pair_number)]
        results.append(
            {
                "pair": pair_number,
                "scenario_id": pair["scenario_id"],
                "human_pick": human_pick,
                "measurement_pick": measurement_pick,
                "agrees_with_human": measurement_pick == human_pick,
                "evidence_flags": list(EVIDENCE_FLAGS[pair_number]),
                "control": control,
                "variant": variant,
            }
        )
    return {
        "study": "E1 situated-response measurement",
        "split": split,
        "sources": {
            "evidence": {"name": path.name, "sha256": actual_hash},
            "human_result": {
                "name": human_result_path.name,
                "sha256": human_result_hash,
            },
        },
        "pairs": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path, help="Frozen erik-review-completed.json")
    parser.add_argument(
        "--human-result",
        type=Path,
        required=True,
        help="Frozen human-result.md used to verify label provenance",
    )
    parser.add_argument(
        "--split", choices=("development", "holdout", "all"), default="development"
    )
    parser.add_argument("--output", type=Path, help="Optional JSON result path")
    args = parser.parse_args()
    result = score_evidence(args.evidence, args.human_result, args.split)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
