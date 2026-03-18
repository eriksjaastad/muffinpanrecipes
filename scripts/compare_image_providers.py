#!/usr/bin/env python3
"""Compare Stability AI vs Nano Banana image generation side-by-side."""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.utils.image_generation import generate_nano_banana_image, generate_stability_image


@dataclass
class PromptEntry:
    recipe_id: str
    variant: str
    prompt: str


def load_jobs(jobs_file: Path) -> list[dict[str, Any]]:
    with jobs_file.open() as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("jobs", [])
    if isinstance(data, list):
        return data
    raise ValueError("Unexpected jobs file format")


def flatten_prompts(jobs: list[dict[str, Any]]) -> list[PromptEntry]:
    entries: list[PromptEntry] = []
    for job in jobs:
        recipe_id = str(job.get("recipe_id") or job.get("recipe_title") or "unknown")
        prompts = job.get("prompts") or {}
        for variant, prompt in prompts.items():
            entries.append(PromptEntry(recipe_id=recipe_id, variant=str(variant), prompt=str(prompt)))
    entries.sort(key=lambda e: (e.recipe_id, e.variant))
    return entries


def select_prompts(entries: list[PromptEntry], count: int, seed: int | None = None) -> list[PromptEntry]:
    if count <= 0:
        return []
    if seed is not None:
        rng = random.Random(seed)
        return rng.sample(entries, min(count, len(entries)))
    return entries[: min(count, len(entries))]


def _cost_from_env(var_name: str) -> float | None:
    raw = os.getenv(var_name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


NANOBANANA_IMAGE_TOKENS = {
    "gemini-2.5-flash-image": 1290,
    "gemini-3.1-flash-image-preview": 1120,
    "gemini-3-pro-image-preview": 1120,
}


def estimate_nano_banana_cost(model: str) -> float | None:
    override = _cost_from_env("NANOBANANA_COST_PER_IMAGE")
    if override is not None:
        return override

    tokens = NANOBANANA_IMAGE_TOKENS.get(model)
    if tokens is None:
        return None

    # Gemini 3.1 Flash Image pricing: output images $60 / 1M tokens
    output_cost_per_m = 60.0
    return round(tokens * output_cost_per_m / 1_000_000, 6)


def estimate_stability_cost() -> float | None:
    return _cost_from_env("STABILITY_COST_PER_IMAGE")


def build_comparison_html(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "<!doctype html>",
        "<html><head><meta charset='utf-8'>",
        "<title>Image Provider Comparison</title>",
        "<style>",
        "body{font-family:Arial,sans-serif;margin:24px;color:#111}",
        "table{border-collapse:collapse;width:100%}",
        "th,td{border:1px solid #ddd;padding:12px;vertical-align:top}",
        "th{background:#f5f5f5;text-align:left}",
        "img{max-width:320px;border-radius:8px;display:block}",
        ".meta{font-size:12px;color:#444;margin-top:6px}",
        "</style></head><body>",
        "<h1>Stability vs Nano Banana</h1>",
        "<table>",
        "<tr><th>Prompt</th><th>Stability</th><th>Nano Banana</th></tr>",
    ]
    for row in rows:
        lines.append("<tr>")
        lines.append(f"<td><strong>{row['recipe_id']}</strong><br>{row['prompt']}</td>")
        lines.append(
            f"<td><img src='{row['stability_path']}' alt='Stability'><div class='meta'>"
            f"{row.get('stability_time_s','?')}s</div></td>"
        )
        lines.append(
            f"<td><img src='{row['nano_path']}' alt='Nano Banana'><div class='meta'>"
            f"{row.get('nano_time_s','?')}s</div></td>"
        )
        lines.append("</tr>")
    lines.extend(["</table>", "</body></html>"])
    out_path.write_text("\n".join(lines))


def run_compare(args: argparse.Namespace) -> Path:
    jobs = load_jobs(Path(args.jobs_file))
    entries = select_prompts(flatten_prompts(jobs), args.count, seed=args.seed)
    if not entries:
        raise RuntimeError("No prompts found in jobs file")

    out_dir = Path(args.out_dir)
    stability_dir = out_dir / "stability"
    nano_dir = out_dir / "nano_banana"
    stability_dir.mkdir(parents=True, exist_ok=True)
    nano_dir.mkdir(parents=True, exist_ok=True)

    stability_key = os.getenv("STABILITY_API_KEY")
    nano_key = os.getenv("NANOBANANA_API_KEY") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    if not args.skip_stability and not stability_key and not args.dry_run:
        raise RuntimeError("STABILITY_API_KEY not set")
    if not args.skip_nano and not nano_key and not args.dry_run:
        raise RuntimeError("NANOBANANA_API_KEY/GEMINI_API_KEY not set")

    rows: list[dict[str, Any]] = []
    for entry in entries:
        stability_path = f"stability/{entry.recipe_id}-{entry.variant}.png"
        nano_path = f"nano_banana/{entry.recipe_id}-{entry.variant}.png"

        stability_time = None
        nano_time = None

        if not args.skip_stability and not args.dry_run:
            start = time.monotonic()
            img_bytes = generate_stability_image(
                entry.prompt,
                stability_key,
                engine_id=args.stability_engine,
            )
            stability_time = round(time.monotonic() - start, 2)
            (stability_dir / f"{entry.recipe_id}-{entry.variant}.png").write_bytes(img_bytes)

        if not args.skip_nano and not args.dry_run:
            start = time.monotonic()
            img_bytes = generate_nano_banana_image(
                entry.prompt,
                nano_key,
                model=args.nano_model,
                aspect_ratio=args.nano_aspect_ratio,
                image_size=args.nano_image_size,
            )
            nano_time = round(time.monotonic() - start, 2)
            (nano_dir / f"{entry.recipe_id}-{entry.variant}.png").write_bytes(img_bytes)

        rows.append({
            "recipe_id": entry.recipe_id,
            "variant": entry.variant,
            "prompt": entry.prompt,
            "stability_path": stability_path,
            "nano_path": nano_path,
            "stability_time_s": stability_time,
            "nano_time_s": nano_time,
            "stability_cost": estimate_stability_cost(),
            "nano_cost": estimate_nano_banana_cost(args.nano_model),
        })

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(rows),
        "stability_engine": args.stability_engine,
        "nano_model": args.nano_model,
        "rows": rows,
    }

    report_path = out_dir / "comparison_report.json"
    report_path.write_text(json.dumps(report, indent=2))

    html_path = out_dir / "comparison_grid.html"
    build_comparison_html(rows, html_path)
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Stability AI vs Nano Banana images.")
    parser.add_argument("--jobs-file", default="data/image_generation_jobs.json")
    parser.add_argument("--out-dir", default="data/image_comparisons/latest")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--stability-engine", default=os.getenv("STABILITY_ENGINE_ID", "stable-diffusion-xl-1024-v1-0"))
    parser.add_argument("--nano-model", default=os.getenv("NANOBANANA_MODEL", "gemini-2.5-flash-image"))
    parser.add_argument("--nano-aspect-ratio", default=os.getenv("NANOBANANA_ASPECT_RATIO", "1:1"))
    parser.add_argument("--nano-image-size", default=os.getenv("NANOBANANA_IMAGE_SIZE", ""))
    parser.add_argument("--skip-stability", action="store_true")
    parser.add_argument("--skip-nano", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.nano_image_size:
        args.nano_image_size = None

    out_dir = run_compare(args)
    print(f"✅ Comparison written to: {out_dir}")


if __name__ == "__main__":
    main()
