#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM_DIR = ROOT / "data" / "simulations"
SUMMARY = SIM_DIR / "openai_benchmark_summary.json"
REPORT = SIM_DIR / "OPENAI_BENCHMARK_REPORT.md"


def load_json(path: Path):
    return json.loads(path.read_text())


def latest_sim_files(limit: int = 250) -> list[Path]:
    files = [p for p in SIM_DIR.glob("sim-*.json") if not p.name.endswith("-comparison.json")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def pick_best_worst(sim_payloads: list[dict]):
    scored = [p for p in sim_payloads if isinstance(p.get("qa", {}).get("score"), int)]
    if not scored:
        return None, None
    best = max(scored, key=lambda p: p["qa"]["score"])
    worst = min(scored, key=lambda p: p["qa"]["score"])
    return best, worst


def excerpt(payload: dict, count: int = 4) -> str:
    msgs = payload.get("messages", [])[:count]
    return "\n".join(f"- **{m['character']}** ({m.get('model','n/a')}): {m['message']}" for m in msgs)


def weak_characters(sim_payloads: list[dict]):
    stats = {}
    for p in sim_payloads:
        avg = p.get("qa", {}).get("avg_length_by_character", {})
        sig = p.get("qa", {}).get("signature_hits", {})
        for c, ln in avg.items():
            s = stats.setdefault(c, {"runs": 0, "len_sum": 0.0, "sig_sum": 0})
            s["runs"] += 1
            s["len_sum"] += float(ln)
            s["sig_sum"] += int(sig.get(c, 0))

    rows = []
    for c, s in stats.items():
        runs = max(1, s["runs"])
        rows.append((c, s["len_sum"] / runs, s["sig_sum"] / runs))
    rows.sort(key=lambda x: (x[2], x[1]))
    return rows[:3]


def main():
    summary = load_json(SUMMARY)
    sims = []
    for p in latest_sim_files():
        try:
            sims.append(load_json(p))
        except Exception:
            pass

    best, worst = pick_best_worst(sims)
    weak = weak_characters(sims)

    comp = summary["compatibility"]
    total = len(comp)
    stable = sum(1 for v in comp.values() if v["status"] == "stable")
    partial = sum(1 for v in comp.values() if v["status"] == "partial")
    incompatible = sum(1 for v in comp.values() if v["status"] == "incompatible")

    lines = []
    lines.append("# OpenAI Dialogue Benchmark Report\n")
    lines.append(f"Generated: {summary['generated_at']}\n")
    lines.append("## Model compatibility matrix")
    lines.append(f"- Total chat-like models enumerated: **{total}**")
    lines.append(f"- Stable (>=2 monday passes): **{stable}**")
    lines.append(f"- Partial (1 pass): **{partial}**")
    lines.append(f"- Incompatible (0 pass): **{incompatible}**\n")
    lines.append("- Sample incompatible/partial reasons:")
    for model, rec in list(comp.items())[:]:
        if rec["status"] != "stable":
            rs = [a["reason"] for a in rec.get("new_attempts", []) if not a.get("ok")]
            reason = rs[0] if rs else "insufficient pass count"
            lines.append(f"  - `{model}` → {rec['status']} ({reason})")
    lines.append("")

    lines.append("## Best dialogue excerpt")
    if best:
        lines.append(f"- QA score: **{best['qa']['score']}** | concept: **{best.get('concept','n/a')}** | model: **{best.get('default_model','mixed')}**")
        lines.append(excerpt(best))
    else:
        lines.append("- No graded simulation payloads found.")
    lines.append("")

    lines.append("## Worst dialogue excerpt")
    if worst:
        lines.append(f"- QA score: **{worst['qa']['score']}** | concept: **{worst.get('concept','n/a')}** | model: **{worst.get('default_model','mixed')}**")
        lines.append(excerpt(worst))
    else:
        lines.append("- No graded simulation payloads found.")
    lines.append("")

    lines.append("## Weak characters (lowest signature/voice retention)")
    for c, avg_len, sig in weak:
        lines.append(f"- **{c}**: avg_len={avg_len:.1f}, avg_signature_hits={sig:.2f}")
    lines.append("")

    lines.append("## Prompt/settings recommendations")
    lines.append("- Keep monday quick-pass at `--ticks-per-day 2` for compatibility sweeps; use `3` for full-week quality checks.")
    lines.append("- For reasoning-heavy families (`o1/o3/o4`, `gpt-5*`), avoid forcing temperature when unsupported (router fallback already handles this for many models).")
    lines.append("- Continue mixed casting: strong results when Steph/Julian/Marcus use 4o/5-tier models while Devon stays on Ollama baseline.")
    lines.append("")

    lines.append("## Assignment variant experiments")
    for v in summary.get("assignment_variants", []):
        lines.append(f"- **{v['variant']}**: {'PASS' if v['ok'] else 'FAIL'} ({v['reason']})")
    lines.append("")

    lines.append("## Next actions")
    lines.append("- Prune deprecated OpenAI snapshot model IDs from default production candidate list.")
    lines.append("- Add an auto-rerun lane for models marked partial/incompatible once per week.")
    lines.append("- Add judge-model grading pass over deep runs and track trendline in CI artifact.")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"saved: {REPORT}")


if __name__ == "__main__":
    main()
