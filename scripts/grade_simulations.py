#!/usr/bin/env python3
"""Grade dialogue simulations with an OpenAI judge model and export benchmark markdown."""

from __future__ import annotations

import argparse
import glob
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.utils.model_router import generate_response

ROOT = Path(__file__).resolve().parents[1]
PERSONAS_PATH = ROOT / "backend" / "data" / "agent_personalities.json"
SIM_DIR = ROOT / "data" / "simulations"


def load_personality_cards() -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in json.loads(PERSONAS_PATH.read_text())}


def compact_cards(cards: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for name, p in cards.items():
        out[name] = {
            "role": p.get("role"),
            "backstory": p.get("backstory"),
            "quirks": p.get("behavioral_quirks", []),
            "traits": p.get("core_traits", {}),
            "signature_phrases": p.get("communication_style", {}).get("signature_phrases", []),
            "contradictions": p.get("internal_contradictions", []),
        }
    return out


def build_prompt(payload: dict[str, Any], cards: dict[str, dict[str, Any]]) -> tuple[str, str]:
    system = (
        "You are a strict dialogue benchmark judge. Evaluate quality and character fidelity. "
        "Return ONLY minified JSON with required keys."
    )
    user = {
        "task": "Score this simulation using the personality cards.",
        "required_output": {
            "overall": "0-100",
            "name_strip_test": "0-100",
            "character_fidelity": "0-100",
            "tension_and_chemistry": "0-100",
            "specificity": "0-100",
            "verdict": "one short sentence",
            "weak_characters": "list of names",
            "best_pairings": "list of pairing strings",
            "notes": "bullet-like short strings"
        },
        "personality_cards": compact_cards(cards),
        "simulation_meta": {
            "concept": payload.get("concept"),
            "default_model": payload.get("default_model"),
            "character_models": payload.get("character_models", {}),
            "inference_check": payload.get("inference_check", {}),
            "qa": payload.get("qa", {}),
        },
        "messages": payload.get("messages", []),
        "name_strip": payload.get("name_strip_transcript", []),
    }
    return system, json.dumps(user, ensure_ascii=False)


def parse_json(raw: str) -> dict[str, Any]:
    s = raw.strip()
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        s = s[i : j + 1]
    return json.loads(s)


def grade_file(path: Path, judge_model: str, cards: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    system, prompt = build_prompt(payload, cards)
    raw = generate_response(prompt=prompt, system_prompt=system, model=judge_model, temperature=0.1)
    grade = parse_json(raw)
    return {
        "file": str(path),
        "concept": payload.get("concept"),
        "default_model": payload.get("default_model"),
        "character_models": payload.get("character_models", {}),
        "inference_check": payload.get("inference_check", {}),
        "qa_score": payload.get("qa", {}).get("score"),
        "judge_model": judge_model,
        "grade": grade,
    }


def model_bucket(rec: dict[str, Any]) -> str:
    cm = rec.get("character_models") or {}
    if cm:
        return "character-mix"
    return rec.get("default_model", "unknown")


def write_benchmark_markdown(results: list[dict[str, Any]], out_path: Path) -> None:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        grouped[model_bucket(r)].append(r)

    ranking = []
    for model, recs in grouped.items():
        judge_avg = sum(float(x["grade"].get("overall", 0)) for x in recs) / max(1, len(recs))
        qa_avg = sum(float(x.get("qa_score") or 0) for x in recs) / max(1, len(recs))
        combined = round(judge_avg * 0.8 + qa_avg * 0.2, 2)
        ranking.append((combined, model, len(recs), round(judge_avg, 2), round(qa_avg, 2)))
    ranking.sort(reverse=True)

    pairing_counts: dict[str, int] = defaultdict(int)
    weak_counts: dict[str, int] = defaultdict(int)
    for r in results:
        g = r.get("grade", {})
        for p in g.get("best_pairings", []) or []:
            pairing_counts[str(p)] += 1
        for w in g.get("weak_characters", []) or []:
            weak_counts[str(w)] += 1

    lines = [
        "# Dialogue Benchmark Results",
        "",
        f"Generated: {datetime.now().isoformat()}",
        "",
        "## Ranked Models / Configs",
    ]
    for score, model, n, javg, qavg in ranking:
        lines.append(f"- **{model}**: combined={score} (judge={javg}, qa={qavg}, runs={n})")

    lines.append("")
    lines.append("## Best Pairings")
    if pairing_counts:
        for pair, c in sorted(pairing_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]:
            lines.append(f"- {pair}: mentioned {c} runs")
    else:
        lines.append("- None surfaced")

    lines.append("")
    lines.append("## Weak Characters")
    if weak_counts:
        for name, c in sorted(weak_counts.items(), key=lambda kv: kv[1], reverse=True):
            lines.append(f"- {name}: flagged {c} runs")
    else:
        lines.append("- None flagged")

    lines.append("")
    lines.append("## Recommended Config")
    if ranking:
        top = ranking[0][1]
        lines.append(f"- Primary: **{top}**")
        lines.append("- Guardrails: enforce prompt-echo hard fail + min-content + cross-character overlap penalties.")
        lines.append("- Judge pass: keep `openai/gpt-5.1` for final weekly grading.")
    else:
        lines.append("- No successful graded runs.")

    out_path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="data/simulations/sim-*.json")
    parser.add_argument("--judge-model", default="openai/gpt-5.1")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--report", default="data/simulations/grading-report.json")
    parser.add_argument("--markdown", default="data/simulations/BENCHMARK_RESULTS.md")
    args = parser.parse_args()

    files = [Path(p) for p in sorted(glob.glob(args.pattern)) if not p.endswith("-comparison.json")][: args.limit]
    cards = load_personality_cards()
    results: list[dict[str, Any]] = []

    for f in files:
        try:
            results.append(grade_file(f, args.judge_model, cards))
            print(f"graded: {f}")
        except Exception as e:
            print(f"failed: {f} :: {e}")

    report = {
        "generated_at": datetime.now().isoformat(),
        "judge_model": args.judge_model,
        "count": len(results),
        "results": results,
    }

    report_path = ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"saved: {report_path}")

    md_path = ROOT / args.markdown
    md_path.parent.mkdir(parents=True, exist_ok=True)
    write_benchmark_markdown(results, md_path)
    print(f"saved: {md_path}")


if __name__ == "__main__":
    main()
