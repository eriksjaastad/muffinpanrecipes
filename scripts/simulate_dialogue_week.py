#!/usr/bin/env python3
"""Run compressed one-week dialogue simulations with model comparisons.

Examples:
  PYTHONPATH=. .venv/bin/python scripts/simulate_dialogue_week.py \
    --concept "Jalapeño Corn Dog Bites" --runs 3 --models "ollama/qwen3:32b,openai/gpt-4o-mini"

  PYTHONPATH=. .venv/bin/python scripts/simulate_dialogue_week.py \
    --concept "Mini Shepherd's Pies" --stage friday --event "ingredient shortage: cheddar"

  PYTHONPATH=. .venv/bin/python scripts/simulate_dialogue_week.py \
    --concept "Brown Butter Pecan Tassies" \
    --character-models '{"Margaret Chen":"openai/gpt-4o","default":"ollama/qwen3:32b"}'
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any

from backend.utils.model_router import generate_response

ROOT = Path(__file__).resolve().parents[1]
PERSONAS_PATH = ROOT / "backend" / "data" / "agent_personalities.json"
OUT_DIR = ROOT / "data" / "simulations"

DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_STAGE = {
    "monday": "brainstorm",
    "tuesday": "recipe_development",
    "wednesday": "photography",
    "thursday": "copywriting",
    "friday": "final_review",
    "saturday": "deployment",
    "sunday": "publish",
}
DAY_PROMPT = {
    "monday": "Concept lock by 5pm. Spark, disagreement, and a decision.",
    "tuesday": "Recipe draft by 5pm. Ratios, testing, and pressure.",
    "wednesday": "Final shots by 5pm. Styling and visual arguments.",
    "thursday": "Copy submitted by 3pm. Headline/tone disagreement.",
    "friday": "Review by 5pm. Team-wide tension and verdict.",
    "saturday": "Staging by noon. Quiet execution + small snag.",
    "sunday": "Publish at 5pm sharp. Final short messages only.",
}

PROHIBITED = [
    "as an ai",
    "i can't",
    "i cannot",
    "i don't have access",
    "let me know if",
    "happy to help",
    "great question",
]


@dataclass
class Message:
    day: str
    stage: str
    character: str
    message: str
    timestamp: str
    model: str


def load_personas() -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in json.loads(PERSONAS_PATH.read_text())}


def participants_for_day(day: str) -> list[str]:
    if day == "monday":
        return ["Margaret Chen", "Marcus Reid", "Stephanie 'Steph' Whitmore"]
    if day == "tuesday":
        return ["Margaret Chen", "Stephanie 'Steph' Whitmore", "Marcus Reid"]
    if day == "wednesday":
        return ["Julian Torres", "Stephanie 'Steph' Whitmore", "Margaret Chen"]
    if day == "thursday":
        return ["Marcus Reid", "Stephanie 'Steph' Whitmore", "Margaret Chen"]
    if day == "friday":
        return ["Margaret Chen", "Stephanie 'Steph' Whitmore", "Julian Torres", "Marcus Reid", "Devon Park"]
    if day == "saturday":
        return ["Devon Park", "Margaret Chen"]
    return ["Stephanie 'Steph' Whitmore", "Margaret Chen", "Marcus Reid"]


def build_system_prompt(persona: dict[str, Any]) -> str:
    comm = persona["communication_style"]
    return (
        f"You are {persona['name']} ({persona['role']}). Stay strictly in character. "
        "Write ONE group chat message only (max 28 words). "
        "No narration, no markdown, no role labels.\n\n"
        f"Backstory: {persona['backstory']}\n"
        f"Traits: {persona['core_traits']}\n"
        f"Signature phrases: {comm.get('signature_phrases', [])}\n"
        f"Behavioral quirks: {persona.get('behavioral_quirks', [])}\n"
        f"Triggers: {persona.get('triggers', [])}\n"
        f"Internal contradictions: {persona.get('internal_contradictions', [])}\n"
        f"Relationships: {persona.get('relationships', {})}"
    )


def choose_model(character: str, default_model: str, mapping: dict[str, str] | None) -> str:
    if not mapping:
        return default_model
    return mapping.get(character, mapping.get("default", default_model))


def generate_turn(
    persona: dict[str, Any],
    concept: str,
    day: str,
    stage: str,
    deadline: str,
    recent_lines: list[str],
    event: str | None,
    model: str,
    mode: str,
) -> str:
    if mode == "template":
        sig = persona["communication_style"].get("signature_phrases", ["Right."])
        pick = random.choice(sig)
        event_bit = f" Also: {event}." if event else ""
        return f"{pick} {day.title()} is {stage}; deadline is {deadline}. For {concept}, lock one decision now.{event_bit}"[:220]

    history = "\n".join(recent_lines[-8:]) if recent_lines else "(no prior messages)"
    event_line = f"Injected event: {event}" if event else "Injected event: none"

    prompt = (
        f"Episode concept: {concept}\n"
        f"Day: {day.title()} ({stage})\n"
        f"Deadline pressure: {deadline}\n"
        f"Scene goal: {DAY_PROMPT[day]}\n"
        f"{event_line}\n"
        f"Recent chat:\n{history}\n\n"
        "Write this character's next message. Keep it natural and specific."
    )
    msg = generate_response(
        prompt=prompt,
        system_prompt=build_system_prompt(persona),
        model=model,
        temperature=0.8,
    ).strip()
    return " ".join(msg.split())


def score_quality(messages: list[Message], personas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lowered = [m.message.lower() for m in messages]
    prohibited_hits = sum(sum(1 for p in PROHIBITED if p in msg) for msg in lowered)

    lengths = [len(re.findall(r"\w+", m.message)) for m in messages] or [0]
    rhythm_variation = (max(lengths) - min(lengths)) if lengths else 0

    per_character_signature_hits: dict[str, int] = {}
    for m in messages:
        sigs = [s.lower() for s in personas[m.character]["communication_style"].get("signature_phrases", [])]
        per_character_signature_hits[m.character] = per_character_signature_hits.get(m.character, 0) + sum(
            1 for s in sigs if s and s in m.message.lower()
        )

    signature_total = sum(per_character_signature_hits.values())
    signature_rate = signature_total / max(len(messages), 1)

    avg_len_by_character: dict[str, float] = {}
    for char in set(m.character for m in messages):
        c_lengths = [len(re.findall(r"\w+", m.message)) for m in messages if m.character == char]
        avg_len_by_character[char] = round(mean(c_lengths), 2) if c_lengths else 0.0

    distinctiveness_spread = 0.0
    if avg_len_by_character:
        vals = list(avg_len_by_character.values())
        distinctiveness_spread = max(vals) - min(vals)

    score = 100
    score -= prohibited_hits * 12
    score += min(12, int(signature_rate * 20))
    score += min(10, int(rhythm_variation / 2))
    score += min(10, int(distinctiveness_spread))
    score = max(0, min(100, score))

    return {
        "score": score,
        "prohibited_hits": prohibited_hits,
        "rhythm_variation": rhythm_variation,
        "signature_hits": per_character_signature_hits,
        "avg_length_by_character": avg_len_by_character,
        "distinctiveness_spread": round(distinctiveness_spread, 2),
    }


def run_simulation(
    concept: str,
    default_model: str,
    run_index: int,
    stage_only: str | None,
    injected_event: str | None,
    ticks_per_day: int,
    mode: str,
    character_models: dict[str, str] | None,
) -> dict[str, Any]:
    personas = load_personas()
    start = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    messages: list[Message] = []
    recent_lines: list[str] = []

    days = [stage_only] if stage_only else DAY_ORDER
    for day_i, day in enumerate(days):
        stage = DAY_STAGE[day]
        names = participants_for_day(day)

        for tick in range(ticks_per_day):
            speaker = names[tick % len(names)]
            persona = personas[speaker]
            ts = start + timedelta(days=day_i, minutes=tick * (480 // max(1, ticks_per_day)))
            deadline = "5:00 PM local" if day not in ("thursday", "saturday") else ("3:00 PM local" if day == "thursday" else "12:00 PM local")
            model = choose_model(speaker, default_model, character_models)

            line = generate_turn(
                persona=persona,
                concept=concept,
                day=day,
                stage=stage,
                deadline=deadline,
                recent_lines=recent_lines,
                event=injected_event,
                model=model,
                mode=mode,
            )
            recent_lines.append(f"{speaker.split()[0]}: {line}")
            messages.append(Message(day=day, stage=stage, character=speaker, message=line, timestamp=ts.isoformat(), model=model))

    by_character: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for m in messages:
        by_character[m.character] = by_character.get(m.character, 0) + 1
        by_model[m.model] = by_model.get(m.model, 0) + 1

    qa = score_quality(messages, personas)
    return {
        "run": run_index,
        "concept": concept,
        "default_model": default_model,
        "character_models": character_models or {},
        "generated_at": datetime.now().isoformat(),
        "messages": [m.__dict__ for m in messages],
        "name_strip_transcript": [m.message for m in messages],
        "metrics": {
            "message_count": len(messages),
            "unique_characters": len(by_character),
            "balance": by_character,
            "model_usage": by_model,
        },
        "qa": qa,
    }


def parse_models(raw: str) -> list[str]:
    return [m.strip() for m in raw.split(",") if m.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Compressed weekly dialogue simulator")
    parser.add_argument("--concept", required=True)
    parser.add_argument("--model", default="ollama/qwen3:32b", help="Single default model")
    parser.add_argument("--models", default=None, help="Comma-separated models for comparison")
    parser.add_argument("--character-models", default=None, help="JSON map of character=>model, with optional default")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--stage", choices=DAY_ORDER, default=None)
    parser.add_argument("--event", default=None)
    parser.add_argument("--ticks-per-day", type=int, default=6)
    parser.add_argument("--mode", choices=["ollama", "template"], default="ollama")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    models = parse_models(args.models) if args.models else [args.model]
    character_models = json.loads(args.character_models) if args.character_models else None

    all_results: list[dict[str, Any]] = []
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    concept_slug = re.sub(r"[^a-z0-9]+", "-", args.concept.lower()).strip("-")[:48]

    for model in models:
        for i in range(1, args.runs + 1):
            result = run_simulation(
                concept=args.concept,
                default_model=model,
                run_index=i,
                stage_only=args.stage,
                injected_event=args.event,
                ticks_per_day=args.ticks_per_day,
                mode=args.mode,
                character_models=character_models,
            )
            suffix = f"{args.stage}" if args.stage else "full-week"
            safe_model = model.replace("/", "_").replace(":", "-")
            out_path = OUT_DIR / f"sim-{stamp}-{concept_slug}-{safe_model}-run{i}-{suffix}.json"
            out_path.write_text(json.dumps(result, indent=2))
            all_results.append({"model": model, "run": i, "path": str(out_path), "qa": result["qa"]["score"]})
            print(f"saved: {out_path}")
            print(f"messages: {result['metrics']['message_count']} | cast: {result['metrics']['unique_characters']} | qa={result['qa']['score']}")

    comparison = {
        "generated_at": datetime.now().isoformat(),
        "concept": args.concept,
        "mode": args.mode,
        "models": models,
        "results": all_results,
    }
    comparison_path = OUT_DIR / f"sim-{stamp}-comparison.json"
    comparison_path.write_text(json.dumps(comparison, indent=2))
    print(f"comparison: {comparison_path}")


if __name__ == "__main__":
    main()
