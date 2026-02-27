#!/usr/bin/env python3
"""Orchestrate OpenAI dialogue benchmarks and write machine-readable summaries.

Phases:
1) Enumerate accessible OpenAI chat-like models.
2) Ensure >=2 monday-stage quick-pass attempts per model (using existing runs + new runs).
3) Run deeper full-week passes for a curated stable subset + Ollama baseline.
4) Run character-model assignment experiments (>=3 variants).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "scripts" / "simulate_dialogue_week.py"
LIST = ROOT / "scripts" / "list_openai_models.py"
OUT_DIR = ROOT / "data" / "simulations"
ASSIGNMENTS_PATH = ROOT / "data" / "dialogue_model_assignments.json"

MONDAY_CONCEPT = "Jalapeño Corn Dog Bites"
FULL_WEEK_CONCEPTS = [
    "Jalapeño Corn Dog Bites",
    "Brown Butter Pecan Tassies",
    "Korean BBQ Meatball Cups",
    "Lemon Ricotta Breakfast Muffins",
    "Mini Shepherd's Pies",
]


def run_cmd(cmd: list[str], timeout: int = 25) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
        ok = p.returncode == 0
        out = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
        return ok, out.strip()
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + ("\n" + (e.stderr or "") if e.stderr else "")
        return False, f"timeout_after_{timeout}s\n{out.strip()}"


def list_openai_models() -> list[str]:
    ok, out = run_cmd([".venv/bin/python", str(LIST)])
    if not ok:
        raise RuntimeError(f"failed to list models: {out}")
    marker = "=== json ==="
    payload = out.split(marker, 1)[1].strip()
    data = json.loads(payload)
    discovered = [f"openai/{m}" for m in data["allowlisted_chat_models"]]

    preferred = [
        "openai/gpt-5-mini",
        "openai/gpt-5-nano",
        "openai/gpt-5.1",
    ]

    picked: list[str] = []
    seen: set[str] = set()
    for model in preferred + discovered:
        if model in discovered and model not in seen:
            picked.append(model)
            seen.add(model)
        if len(picked) >= 10:
            break
    return picked


def load_existing_monday_attempts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in OUT_DIR.glob("sim-*-monday.json"):
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        model = data.get("default_model")
        if isinstance(model, str):
            counts[model] = counts.get(model, 0) + 1
    return counts


def quick_attempt(model: str, run_tag: str) -> tuple[bool, str]:
    cmd = [
        ".venv/bin/python",
        str(SIM),
        "--concept",
        MONDAY_CONCEPT,
        "--model",
        model,
        "--runs",
        "1",
        "--stage",
        "monday",
        "--ticks-per-day",
        "1",
        "--mode",
        "ollama",
        "--event",
        f"compatibility_probe:{run_tag}",
    ]
    return run_cmd(cmd)


def run_full_week(model: str, concept: str, prompt_style: str = "scene") -> tuple[bool, str]:
    cmd = [
        ".venv/bin/python",
        str(SIM),
        "--concept",
        concept,
        "--model",
        model,
        "--runs",
        "1",
        "--ticks-per-day",
        "3",
        "--mode",
        "ollama",
        "--prompt-style",
        prompt_style,
    ]
    return run_cmd(cmd, timeout=240)


def assignment_variants() -> dict[str, dict[str, str]]:
    base = json.loads(ASSIGNMENTS_PATH.read_text())

    v1 = dict(base)
    v1.update(
        {
            "default": "openai/gpt-4o-mini",
            "Devon Park": "ollama/qwen3:32b",
        }
    )

    v2 = dict(base)
    v2.update(
        {
            "default": "openai/gpt-4o-mini",
            "Margaret Chen": "openai/gpt-4o",
            "Stephanie 'Steph' Whitmore": "openai/gpt-4o-mini",
            "Julian Torres": "openai/gpt-4o",
            "Marcus Reid": "openai/gpt-4o-mini",
            "Devon Park": "ollama/qwen3:32b",
        }
    )

    v3 = dict(base)
    v3.update(
        {
            "default": "ollama/qwen3:32b",
            "Margaret Chen": "openai/gpt-4o",
            "Stephanie 'Steph' Whitmore": "openai/gpt-4o-mini",
            "Julian Torres": "ollama/qwen3:32b",
            "Marcus Reid": "openai/gpt-4o-mini",
            "Devon Park": "ollama/qwen3:32b",
        }
    )

    return {"variant_balanced": v1, "variant_4o_leads": v2, "variant_local_heavy": v3}


def run_assignment_variant(name: str, mapping: dict[str, str]) -> tuple[bool, str]:
    cmd = [
        ".venv/bin/python",
        str(SIM),
        "--concept",
        "Mini Shepherd's Pies",
        "--runs",
        "1",
        "--ticks-per-day",
        "3",
        "--mode",
        "ollama",
        "--character-models",
        json.dumps(mapping),
    ]
    return run_cmd(cmd, timeout=240)


def reason_from_output(output: str) -> str:
    text = (output or "").strip()
    if not text:
        return "unknown error"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    tail = lines[-1] if lines else text
    if "model_not_found" in text or "does not exist" in text:
        return "model_not_found"
    if "v1/responses" in text and "NotFoundError" in text:
        return "requires_responses_api_unhandled"
    if "unsupported" in text and "temperature" in text.lower():
        return "temperature_unsupported"
    if "429" in text or "rate limit" in text.lower():
        return "rate_limited"
    return tail[:240]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-week-models", default="ollama/qwen3:32b,openai/gpt-5-mini,openai/gpt-5-nano,openai/gpt-5.1")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    models = list_openai_models()
    existing = load_existing_monday_attempts()

    compatibility: dict[str, dict[str, Any]] = {}
    for model in models:
        compatibility[model] = {
            "existing_pass_runs": existing.get(model, 0),
            "new_attempts": [],
        }

    for model in models:
        needed = max(0, 2 - existing.get(model, 0))
        for i in range(needed):
            ok, out = quick_attempt(model, f"{i+1}")
            compatibility[model]["new_attempts"].append(
                {
                    "ok": ok,
                    "reason": "pass" if ok else reason_from_output(out),
                }
            )

    for model, rec in compatibility.items():
        passes = rec["existing_pass_runs"] + sum(1 for a in rec["new_attempts"] if a["ok"])
        fails = sum(1 for a in rec["new_attempts"] if not a["ok"])
        rec["pass_runs_total"] = passes
        rec["fail_runs_new"] = fails
        rec["status"] = "stable" if passes >= 2 else ("partial" if passes > 0 else "incompatible")

    full_week_models = [m.strip() for m in args.full_week_models.split(",") if m.strip()]
    deep_runs: list[dict[str, Any]] = []
    for model in full_week_models:
        for concept in FULL_WEEK_CONCEPTS:
            ok, out = run_full_week(model, concept, prompt_style="scene")
            deep_runs.append(
                {
                    "model": model,
                    "concept": concept,
                    "prompt_style": "scene",
                    "ok": ok,
                    "reason": "pass" if ok else reason_from_output(out),
                }
            )

    # Comparison context only: one full-prompt run for a representative concept.
    for model in full_week_models:
        ok, out = run_full_week(model, MONDAY_CONCEPT, prompt_style="full")
        deep_runs.append(
            {
                "model": model,
                "concept": MONDAY_CONCEPT,
                "prompt_style": "full",
                "ok": ok,
                "reason": "pass" if ok else reason_from_output(out),
            }
        )

    variants = assignment_variants()
    variant_results: list[dict[str, Any]] = []
    for name, mapping in variants.items():
        ok, out = run_assignment_variant(name, mapping)
        variant_results.append(
            {
                "variant": name,
                "ok": ok,
                "reason": "pass" if ok else reason_from_output(out),
                "mapping": mapping,
            }
        )

    summary = {
        "generated_at": datetime.now().isoformat(),
        "openai_models_total": len(models),
        "compatibility": compatibility,
        "deep_runs": deep_runs,
        "assignment_variants": variant_results,
    }
    out_path = OUT_DIR / "openai_benchmark_summary.json"
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"saved: {out_path}")


if __name__ == "__main__":
    main()
