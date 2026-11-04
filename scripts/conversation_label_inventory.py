#!/usr/bin/env python3
"""Inventory committed human dialogue labels without provider calls.

The calibration work has both primary result files and derived E1 scoring
receipts. This tool reports each label-bearing file separately and marks E1
receipts as derived, preventing their labels from being counted a second time
as new human judgments.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _day_from_pair(pair: dict[str, Any], fallback: str | None) -> str:
    scenario_id = pair.get("scenario_id")
    if isinstance(scenario_id, str) and ":" in scenario_id:
        return scenario_id.split(":", 2)[1].lower()
    for arm in ("control_messages", "variant_messages"):
        messages = pair.get(arm)
        if isinstance(messages, list) and messages:
            day = messages[0].get("day")
            if isinstance(day, str):
                return day.lower()
    return (fallback or "unknown").lower()


def inspect_file(path: Path) -> dict[str, Any] | None:
    raw = path.read_bytes()
    data = json.loads(raw)
    pairs = data.get("pairs")
    if not isinstance(pairs, list):
        return None

    top_level_picks = data.get("human_picks")
    pair_level = [pair for pair in pairs if isinstance(pair, dict) and pair.get("human_pick")]
    labels: list[dict[str, str]] = []
    source_kind = "primary"

    if isinstance(top_level_picks, dict):
        for key, pick in sorted(top_level_picks.items(), key=lambda item: int(item[0])):
            index = int(key) - 1
            if index < 0 or index >= len(pairs):
                raise ValueError(f"{path}: human pick {key} has no matching pair")
            labels.append(
                {
                    "id": str(key),
                    "day": _day_from_pair(pairs[index], data.get("stage")),
                    "pick": str(pick),
                }
            )
    elif pair_level:
        source_kind = "derived"
        for pair in pair_level:
            labels.append(
                {
                    "id": str(pair.get("pair")),
                    "day": _day_from_pair(pair, data.get("stage")),
                    "pick": str(pair["human_pick"]),
                }
            )
    else:
        return None

    return {
        "path": path.as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_kind": source_kind,
        "label_count": len(labels),
        "days": dict(sorted(Counter(label["day"] for label in labels).items())),
        "labels": labels,
    }


def build_inventory(paths: list[Path]) -> dict[str, Any]:
    files = [receipt for path in sorted(paths) if (receipt := inspect_file(path))]
    primary = [item for item in files if item["source_kind"] == "primary"]
    derived = [item for item in files if item["source_kind"] == "derived"]
    primary_days: Counter[str] = Counter()
    for item in primary:
        primary_days.update(item["days"])
    return {
        "schema_version": 1,
        "summary": {
            "primary_human_labels": sum(item["label_count"] for item in primary),
            "primary_days": dict(sorted(primary_days.items())),
            "derived_label_references": sum(item["label_count"] for item in derived),
            "derived_files_are_not_new_labels": True,
        },
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    inventory = build_inventory(args.paths)
    rendered = json.dumps(inventory, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
