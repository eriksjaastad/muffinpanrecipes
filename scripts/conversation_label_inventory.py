#!/usr/bin/env python3
"""Inventory committed primary and derived human dialogue labels offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

VALID_PICKS = frozenset({"control", "variant", "tie"})


def _load(path: Path) -> tuple[bytes, dict[str, Any], list[dict[str, Any]]]:
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: root must be an object")
    pairs = data.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError(f"{path.name}: pairs must be a non-empty list")
    if not all(isinstance(pair, dict) for pair in pairs):
        raise ValueError(f"{path.name}: every pair must be an object")
    return raw, data, pairs


def _pair_id(path: Path, value: Any) -> str:
    if isinstance(value, bool):
        raise ValueError(f"{path.name}: invalid pair ID {value!r}")
    rendered = str(value)
    if not rendered.isdigit() or int(rendered) < 1 or str(int(rendered)) != rendered:
        raise ValueError(f"{path.name}: invalid pair ID {value!r}")
    return rendered


def _validated_pick(path: Path, value: Any) -> str:
    if value not in VALID_PICKS:
        raise ValueError(f"{path.name}: invalid human pick {value!r}")
    return str(value)


def _label(path: Path, pair: dict[str, Any], label_id: str, pick: Any) -> dict[str, str]:
    scenario_id = pair.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        raise ValueError(f"{path.name}: label {label_id} has no scenario_id")
    parts = scenario_id.split(":", 2)
    if len(parts) >= 2:
        day = parts[1].lower()
    else:
        messages = pair.get("control_messages") or pair.get("variant_messages")
        message_day = messages[0].get("day") if isinstance(messages, list) and messages else None
        day = str(message_day or "unknown").lower()
    return {
        "pair_id": _pair_id(path, label_id),
        "scenario_id": scenario_id,
        "day": day,
        "pick": _validated_pick(path, pick),
    }


def inspect_file(path: Path, source_kind: str) -> dict[str, Any]:
    raw, data, pairs = _load(path)
    labels: list[dict[str, str]] = []
    evidence_sha256: str | None = None

    if source_kind == "primary":
        picks = data.get("human_picks")
        if not isinstance(picks, dict) or not picks:
            raise ValueError(f"{path.name}: primary source needs non-empty human_picks")
        for key, pick in sorted(picks.items(), key=lambda item: int(item[0])):
            canonical_id = _pair_id(path, key)
            index = int(canonical_id) - 1
            if index >= len(pairs):
                raise ValueError(f"{path.name}: human pick {key} has no matching pair")
            labels.append(_label(path, pairs[index], canonical_id, pick))
    elif source_kind == "derived":
        sources = data.get("sources")
        evidence = sources.get("evidence") if isinstance(sources, dict) else None
        evidence_sha256 = evidence.get("sha256") if isinstance(evidence, dict) else None
        if (
            not isinstance(evidence_sha256, str)
            or len(evidence_sha256) != 64
            or any(char not in "0123456789abcdef" for char in evidence_sha256)
        ):
            raise ValueError(f"{path.name}: derived source needs an evidence SHA-256")
        for pair in pairs:
            if "human_pick" in pair:
                labels.append(_label(path, pair, _pair_id(path, pair.get("pair")), pair["human_pick"]))
        if not labels:
            raise ValueError(f"{path.name}: derived source has no pair-level human_pick")
    else:
        raise ValueError(f"unsupported source kind: {source_kind}")

    receipt = {
        "source_name": path.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_kind": source_kind,
        "label_count": len(labels),
        "days": dict(sorted(Counter(label["day"] for label in labels).items())),
        "labels": labels,
    }
    if evidence_sha256:
        receipt["evidence_sha256"] = evidence_sha256
    return receipt


def _identity(label: dict[str, str]) -> tuple[str, str, str]:
    return label["pair_id"], label["scenario_id"], label["pick"]


def build_inventory(primary_paths: list[Path], derived_paths: list[Path]) -> dict[str, Any]:
    primary = [inspect_file(path, "primary") for path in sorted(primary_paths)]
    derived = [inspect_file(path, "derived") for path in sorted(derived_paths)]
    files = primary + derived
    hashes = [item["sha256"] for item in files]
    if len(hashes) != len(set(hashes)):
        raise ValueError("the same source content was supplied more than once")

    primary_by_sha = {item["sha256"]: item for item in primary}
    derived_by_evidence: dict[str, Counter[tuple[str, str, str]]] = {}
    for item in derived:
        evidence_sha = item["evidence_sha256"]
        if evidence_sha not in primary_by_sha:
            raise ValueError(f"{item['source_name']}: declared primary evidence is missing")
        derived_by_evidence.setdefault(evidence_sha, Counter()).update(
            _identity(label) for label in item["labels"]
        )
    for evidence_sha, derived_identities in derived_by_evidence.items():
        primary_identities = Counter(
            _identity(label) for label in primary_by_sha[evidence_sha]["labels"]
        )
        missing = derived_identities - primary_identities
        if missing:
            scenarios = ", ".join(sorted(identity[1] for identity in missing))
            raise ValueError(f"derived labels do not match primary evidence: {scenarios}")

    primary_days: Counter[str] = Counter()
    for item in primary:
        primary_days.update(item["days"])
    return {
        "schema_version": 3,
        "summary": {
            "primary_human_labels": sum(item["label_count"] for item in primary),
            "primary_days": dict(sorted(primary_days.items())),
            "derived_label_references": sum(item["label_count"] for item in derived),
            "derived_references_verified": True,
            "derived_files_are_not_new_labels": True,
        },
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", nargs="+", required=True, type=Path)
    parser.add_argument("--derived", nargs="*", default=[], type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    inventory = build_inventory(args.primary, args.derived)
    rendered = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
