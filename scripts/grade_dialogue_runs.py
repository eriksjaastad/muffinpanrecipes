#!/usr/bin/env python3
"""Grade simulation transcripts using a high-end OpenAI judge model."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

from backend.utils.model_router import generate_response


def build_prompt(payload: dict) -> tuple[str, str]:
    system = (
        "You are a strict dialogue quality judge for a serialized character drama. "
        "Return ONLY valid JSON."
    )
    user = {
        "task": "Grade this conversation for character distinctiveness and quality.",
        "rubric": {
            "overall_score": "0-100",
            "name_strip_test": "0-100: can we infer speaker identity without names",
            "character_scores": "score each character 0-100 with reasons",
            "pairwise_scores": "score chemistry for key pairs: Margaret-Steph, Julian-Marcus, Margaret-Julian, Steph-Marcus, Devon-Team",
            "genericity_penalty": "0-100 where higher means too generic/AI-like",
            "notes": "best lines, worst lines, concrete fixes"
        },
        "conversation": payload.get("messages", []),
        "name_strip": payload.get("name_strip_transcript", []),
    }
    return system, json.dumps(user, ensure_ascii=False)


def grade_file(path: Path, judge_model: str) -> dict:
    data = json.loads(path.read_text())
    system, prompt = build_prompt(data)
    raw = generate_response(prompt=prompt, system_prompt=system, model=judge_model, temperature=0.1)

    # best effort parse
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    graded = json.loads(raw)

    return {
        "file": str(path),
        "judge_model": judge_model,
        "grade": graded,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pattern", default="data/simulations/sim-*.json")
    parser.add_argument("--judge-model", default="openai/gpt-4o")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    files = [Path(p) for p in sorted(glob.glob(args.pattern)) if not p.endswith("-comparison.json")][: args.limit]
    out = []

    for f in files:
        try:
            out.append(grade_file(f, args.judge_model))
            print(f"graded: {f}")
        except Exception as e:
            print(f"failed: {f} :: {e}")

    report = {
        "judge_model": args.judge_model,
        "count": len(out),
        "results": out,
    }

    report_path = Path("data/simulations") / "grading-report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"saved: {report_path}")


if __name__ == "__main__":
    main()
