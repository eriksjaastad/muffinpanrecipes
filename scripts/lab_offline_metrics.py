#!/usr/bin/env python3
"""Offline (zero-API-call) metrics for a saved `conversation_lab.py ab` result.

Card #7791 P3 (docs/conversation-lab/RESEARCH_PLAN.md section 3.2). S1 ("where
are baseline and bundle in absolute terms?") needs the section-4 benchmarks
computed on both arms of every saved `ab` result, per recipe and pooled, at
zero API cost. This script does exactly that by REUSING
`scripts.conversation_metrics.summarize()` - it does not reimplement any
metric that module already computes.

Usage:
    uv run scripts/lab_offline_metrics.py RESULT.json [RESULT2.json ...]
    uv run scripts/lab_offline_metrics.py 'docs/conversation-lab/results/*-ab-*.json'
    uv run scripts/lab_offline_metrics.py RESULT.json --json

Each positional argument is either a literal path or a glob pattern (expanded
with `glob.glob`); a pattern that matches nothing, or a literal path that does
not exist, is a hard error - this script never silently skips a file the
caller named.

For each result file, pairs are grouped by recipe: `ab --testbed` results
carry `scenario_id` per pair and a `scenarios` list naming each one's concept;
single-concept `ab` results are one recipe (the file's own `concept`). An
`ab --sweep` result is refused: it nests pairs per variant around one shared
control, so pooling it here would count that control once per variant. A
result with no pairs is refused too, rather than reported as empty metrics. Within a recipe, `conversation_metrics.summarize()` is computed
per pair per arm, then averaged across that recipe's pairs ("per recipe") and
across every recipe in the file ("pooled") - never by concatenating raw
messages across pairs, which would fabricate a turn-to-turn adjacency link
between the end of one pair's transcript and the start of the next.

Targets are RESEARCH_PLAN.md section 4 (itself DIALS.md section 3, per that
section's own text) - reproduced here as data, not re-derived. A value is
reported against its target range with no invented pass/fail beyond a plain
"within range" / "below" / "above" read where the target is numeric.

Also computes V4 (RESEARCH_PLAN.md section 3.1) - the share of pairs whose two
position-swapped judge orientations disagree on the OVERALL verdict BEFORE
`_combine_orientations` forces disagreement to "tie" - from each pair's saved
`judge_orientations`, when present. This is a judge-instrument diagnostic, not
a `conversation_metrics` metric, so it is computed directly here.
"""

from __future__ import annotations

import argparse
import glob
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from scripts.conversation_metrics import _first_name, summarize
from scripts.simulate_dialogue_week import participants_for_day

# RESEARCH_PLAN.md section 4 ("Absolute benchmarks"), itself DIALS.md section
# 3. Each entry: (summarize() key, target description, optional (lo, hi) for
# a plain within/below/above read - None when the target is qualitative).
TARGET_METRICS: tuple[tuple[str, str, tuple[float, float] | None], ...] = (
    ("dash_clause_rate", "30-50% (production today: 86%)", (0.30, 0.50)),
    ("length_stdev", ">= 10 words (production today: 8.1)", (10.0, float("inf"))),
    ("short_line_rate", "10-15% (production today: 1%)", (0.10, 0.15)),
    ("frame_claim_rate", "<= 10% (production today: 30%)", (0.0, 0.10)),
    ("agree_opener_rate", "8-10% (production today: 21%)", (0.08, 0.10)),
    ("question_rate", "25-30%, with qa_rate up (production today: 20%)", (0.25, 0.30)),
    ("length_mean_words", "10-30, varying by speaker (production today: 24; limits-off: 80-140)", (10.0, 30.0)),
)

# Cast-coverage and cross-week repeated-opener targets are qualitative / not
# computable from a single ab result - reported separately, not folded into
# TARGET_METRICS's numeric range check.
CAST_TARGET_NOTE = "every roster member speaks; nobody above 35% share (production today: Margaret 30%, some roster members silent)"
CROSS_WEEK_NOTE = (
    "not computable from a single ab result - needs multiple weeks/episodes "
    "(production today: 24 of 25 Saturdays share an identical opening 4-gram)"
)


def _iter_result_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for arg in args:
        matches = sorted(glob.glob(arg))
        if matches:
            paths.extend(Path(m) for m in matches)
            continue
        literal = Path(arg)
        if literal.exists():
            paths.append(literal)
            continue
        raise SystemExit(f"lab_offline_metrics: no file matches {arg!r}")
    return paths


def _load_ab_result(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise SystemExit(f"lab_offline_metrics: cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("command") != "ab":
        raise SystemExit(
            f"lab_offline_metrics: {path} is not an `ab` result (command={data.get('command')!r})"
        )
    if data.get("mode") == "sweep":
        raise SystemExit(
            f"lab_offline_metrics: {path} is an `ab --sweep` result (pairs nested per variant around "
            "one shared control) - not supported; pass single-concept or --testbed results"
        )
    if not isinstance(data.get("pairs"), list) or not data["pairs"]:
        raise SystemExit(f"lab_offline_metrics: {path} has no pairs - nothing to measure")
    return data


def _recipe_groups(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{recipe_id: {"concept": str, "pairs": [pair, ...]}} - one entry per
    scenario for a --testbed result, or a single entry keyed
    by the file's own concept for a single-concept ab result."""
    pairs = data.get("pairs") or []
    scenarios = {s["id"]: s for s in (data.get("scenarios") or []) if isinstance(s, dict) and "id" in s}
    groups: dict[str, dict[str, Any]] = {}
    if scenarios:
        for pair in pairs:
            sid = pair.get("scenario_id") or "unknown"
            scenario = scenarios.get(sid, {})
            group = groups.setdefault(sid, {"concept": scenario.get("concept") or data.get("concept"), "pairs": []})
            group["pairs"].append(pair)
    else:
        recipe_id = data.get("concept") or "unknown"
        groups[recipe_id] = {"concept": data.get("concept"), "pairs": list(pairs)}
    return groups


def _speaker_share(messages: list[dict[str, Any]], expected_cast: list[str]) -> dict[str, Any]:
    """Per-arm cast-coverage detail the section-4 target needs beyond what
    `conversation_metrics.cast_coverage` reports: each speaker's SHARE of
    total messages (not just who spoke at all), and which roster members
    never spoke."""
    if not messages:
        return {"max_share": 0.0, "max_speaker": None, "silent_roster_members": sorted({_first_name(c) for c in expected_cast})}
    counts = Counter(_first_name(m.get("character")) for m in messages if m.get("character"))
    total = sum(counts.values())
    expected_norm = sorted({_first_name(c) for c in expected_cast})
    silent = sorted(set(expected_norm) - set(counts))
    if not counts:
        return {"max_share": 0.0, "max_speaker": None, "silent_roster_members": silent}
    max_speaker, max_count = counts.most_common(1)[0]
    return {
        "max_share": round(max_count / total, 4) if total else 0.0,
        "max_speaker": max_speaker,
        "silent_roster_members": silent,
    }


def _arm_metrics(messages: list[dict[str, Any]], expected_cast: list[str], concept: str, day: str | None) -> dict[str, Any]:
    summary = summarize(messages, expected_cast, concept=concept or "", day=day)
    metrics = {key: summary.get(key) for key, _, _ in TARGET_METRICS}
    metrics.update(_speaker_share(messages, expected_cast))
    return metrics


def _average_metrics(per_pair_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean of every numeric field across pairs; silent_roster_members is the
    INTERSECTION (a member missing from every pair, not just one) and
    max_speaker is left out of the average (categorical, reported per-pair
    when helpful, not summarized as a mean)."""
    if not per_pair_metrics:
        return {}
    averaged: dict[str, Any] = {}
    for key, _, _ in TARGET_METRICS:
        values = [m[key] for m in per_pair_metrics if isinstance(m.get(key), (int, float))]
        averaged[key] = round(mean(values), 4) if values else None
    share_values = [m["max_share"] for m in per_pair_metrics if isinstance(m.get("max_share"), (int, float))]
    averaged["max_share"] = round(mean(share_values), 4) if share_values else None
    silent_sets = [set(m.get("silent_roster_members") or []) for m in per_pair_metrics]
    averaged["silent_roster_members_in_every_pair"] = sorted(set.intersection(*silent_sets)) if silent_sets else []
    averaged["pair_count"] = len(per_pair_metrics)
    return averaged


def _orientation_disagreement(pair: dict[str, Any]) -> bool | None:
    """V4: do this pair's two position-swapped orientations disagree on the
    OVERALL verdict, before `_combine_orientations` forces a disagreement to
    "tie"? None when the pair carries no usable judge_orientations (e.g. a
    --dry-run result, or an older result file that predates this field)."""
    orientations = pair.get("judge_orientations")
    if not isinstance(orientations, list) or len(orientations) != 2:
        return None
    overalls = []
    for orientation in orientations:
        result = orientation.get("result") if isinstance(orientation, dict) else None
        if not isinstance(result, dict) or "overall" not in result:
            return None
        overalls.append(result["overall"])
    return overalls[0] != overalls[1]


def compute(paths: list[Path]) -> dict[str, Any]:
    files_report: list[dict[str, Any]] = []
    pooled_control: list[dict[str, Any]] = []
    pooled_variant: list[dict[str, Any]] = []
    pooled_disagreements: list[bool] = []

    for path in paths:
        data = _load_ab_result(path)
        stage = data.get("stage")
        expected_cast = participants_for_day(stage) if stage else []
        groups = _recipe_groups(data)

        recipes_report: dict[str, Any] = {}
        file_control: list[dict[str, Any]] = []
        file_variant: list[dict[str, Any]] = []
        file_disagreements: list[bool] = []

        for recipe_id, group in groups.items():
            concept = group["concept"]
            recipe_control = [
                _arm_metrics(pair.get("control_messages") or [], expected_cast, concept, stage)
                for pair in group["pairs"]
            ]
            recipe_variant = [
                _arm_metrics(pair.get("variant_messages") or [], expected_cast, concept, stage)
                for pair in group["pairs"]
            ]
            disagreements = [d for d in (_orientation_disagreement(p) for p in group["pairs"]) if d is not None]
            recipes_report[recipe_id] = {
                "concept": concept,
                "pair_count": len(group["pairs"]),
                "control": _average_metrics(recipe_control),
                "variant": _average_metrics(recipe_variant),
                "v4_position_disagreement_rate": (
                    round(sum(disagreements) / len(disagreements), 4) if disagreements else None
                ),
                "v4_pairs_with_orientations": len(disagreements),
            }
            file_control.extend(recipe_control)
            file_variant.extend(recipe_variant)
            file_disagreements.extend(disagreements)

        files_report.append({
            "result_file": str(path),
            "stage": stage,
            "recipes": recipes_report,
            "pooled": {
                "control": _average_metrics(file_control),
                "variant": _average_metrics(file_variant),
                "v4_position_disagreement_rate": (
                    round(sum(file_disagreements) / len(file_disagreements), 4) if file_disagreements else None
                ),
                "v4_pairs_with_orientations": len(file_disagreements),
            },
        })
        pooled_control.extend(file_control)
        pooled_variant.extend(file_variant)
        pooled_disagreements.extend(file_disagreements)

    return {
        "files": files_report,
        "pooled_across_files": {
            "control": _average_metrics(pooled_control),
            "variant": _average_metrics(pooled_variant),
            "v4_position_disagreement_rate": (
                round(sum(pooled_disagreements) / len(pooled_disagreements), 4) if pooled_disagreements else None
            ),
            "v4_pairs_with_orientations": len(pooled_disagreements),
        },
        "targets": {
            key: {"description": desc, "range": list(rng) if rng else None}
            for key, desc, rng in TARGET_METRICS
        },
        "cast_target_note": CAST_TARGET_NOTE,
        "cross_week_note": CROSS_WEEK_NOTE,
    }


def _range_note(value: Any, rng: tuple[float, float] | None) -> str:
    if not isinstance(value, (int, float)) or rng is None:
        return ""
    lo, hi = rng
    if value < lo:
        return " (below target)"
    if value > hi:
        return " (above target)"
    return " (within target)"


def _print_arm(label: str, metrics: dict[str, Any]) -> None:
    if not metrics:
        print(f"    {label}: no pairs")
        return
    print(f"    {label} (n={metrics.get('pair_count', 0)} pairs):")
    for key, desc, rng in TARGET_METRICS:
        value = metrics.get(key)
        note = _range_note(value, rng)
        print(f"      {key:<20} {value!r:<10}{note}  target: {desc}")
    print(f"      {'max_speaker_share':<20} {metrics.get('max_share')!r:<10}  target: {CAST_TARGET_NOTE}")
    silent = metrics.get("silent_roster_members_in_every_pair")
    if silent:
        print(f"      silent in every pair: {silent}")


def _print_human(report: dict[str, Any]) -> None:
    for file_report in report["files"]:
        print(f"\n=== {file_report['result_file']} (stage={file_report['stage']}) ===")
        for recipe_id, recipe in file_report["recipes"].items():
            print(f"\n  recipe {recipe_id!r} ({recipe['concept']!r}), {recipe['pair_count']} pairs")
            if recipe["v4_position_disagreement_rate"] is not None:
                print(
                    f"    V4 position disagreement rate: {recipe['v4_position_disagreement_rate']:.2%} "
                    f"over {recipe['v4_pairs_with_orientations']} pairs (threshold: <= 20%)"
                )
            _print_arm("control", recipe["control"])
            _print_arm("variant", recipe["variant"])
        pooled = file_report["pooled"]
        print("\n  -- pooled across recipes in this file --")
        if pooled["v4_position_disagreement_rate"] is not None:
            print(
                f"    V4 position disagreement rate: {pooled['v4_position_disagreement_rate']:.2%} "
                f"over {pooled['v4_pairs_with_orientations']} pairs (threshold: <= 20%)"
            )
        _print_arm("control", pooled["control"])
        _print_arm("variant", pooled["variant"])

    print("\n=== pooled across ALL files ===")
    pooled_all = report["pooled_across_files"]
    if pooled_all["v4_position_disagreement_rate"] is not None:
        print(
            f"V4 position disagreement rate: {pooled_all['v4_position_disagreement_rate']:.2%} "
            f"over {pooled_all['v4_pairs_with_orientations']} pairs (threshold: <= 20%)"
        )
    _print_arm("control", pooled_all["control"])
    _print_arm("variant", pooled_all["variant"])
    print(f"\ncross-week repeated-opener target: {report['cross_week_note']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="lab_offline_metrics",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("result_files", nargs="+", help="ab result JSON path(s) or glob pattern(s)")
    parser.add_argument("--json", action="store_true", help="Print the full report as JSON instead of a human-readable table")
    args = parser.parse_args(argv)

    paths = _iter_result_paths(args.result_files)
    report = compute(paths)

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_human(report)


if __name__ == "__main__":
    main()
