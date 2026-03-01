#!/usr/bin/env python3
"""Run compressed one-week dialogue simulations with model comparisons.

Examples:
  PYTHONPATH=. .venv/bin/python scripts/simulate_dialogue_week.py \
    --concept "Jalapeño Corn Dog Bites" --runs 3 --models "ollama/qwen3:32b,openai/gpt-5-mini"

  PYTHONPATH=. .venv/bin/python scripts/simulate_dialogue_week.py \
    --concept "Mini Shepherd's Pies" --stage friday --event "ingredient shortage: cheddar"

  PYTHONPATH=. .venv/bin/python scripts/simulate_dialogue_week.py \
    --concept "Brown Butter Pecan Tassies" \
    --character-models '{"Margaret Chen":"openai/gpt-5.1","default":"ollama/qwen3:32b"}'
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
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

DAY_STAGE_DIRECTIONS = {
    "monday": "Slack pings stack up as the team races to lock the concept.",
    "tuesday": "Test trays cool on the rack while ratio notes keep changing.",
    "wednesday": "Julian drops fresh lighting tests and everyone debates the hero shot.",
    "thursday": "Draft copy is open in shared docs with comments arriving in bursts.",
    "friday": "Final review is tense as approvals hinge on tiny fixes.",
    "saturday": "Deployment prep is mostly quiet until one staging snag interrupts flow.",
    "sunday": "Publish window is close and everyone is watching the clock.",
}

PROHIBITED = [
    "as an ai",
    "i can't",
    "i cannot",
    "i don't have access",
    "let me know if",
    "happy to help",
    "great question",
    "\u2014",  # em dash — hard AI tell
    "\u2013",  # en dash
    "\u2019",  # right single quote / curly apostrophe (e.g. ’s, let’s)
    "\u201c",  # left curly double quote
    "\u201d",  # right curly double quote
]

# Variable message counts by day (min, max). Sampled fresh each run.
TICKS_RANGE: dict[str, tuple[int, int]] = {
    "monday":    (6, 10),  # heated concept debate
    "tuesday":   (4, 6),   # focused recipe dev
    "wednesday": (4, 6),   # photography + image refs (may have fewer because messages are longer)
    "thursday":  (3, 5),   # copywriting
    "friday":    (5, 8),   # approval discussion
    "saturday":  (2, 3),   # quiet deploy
    "sunday":    (2, 3),   # short celebratory wrap
}

PROMPT_ECHO_PATTERNS = [
    "day:",
    "scene goal:",
    "deadline pressure:",
    "write this character's next message",
    "injected event",
]


@dataclass
class Message:
    day: str
    stage: str
    character: str
    message: str
    timestamp: str
    model: str
    attachments: list[str] = field(default_factory=list)  # image paths for Wednesday photography messages


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


def deadline_for_day(day: str) -> str:
    if day == "thursday":
        return "3:00 PM local"
    if day == "saturday":
        return "12:00 PM local"
    return "5:00 PM local"


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
    prompt_style: str,
    day_turn: int,
) -> str:
    if mode == "template":
        sig = persona["communication_style"].get("signature_phrases", ["Right."])
        pick = random.choice(sig)
        event_bit = f" Also: {event}." if event else ""
        return f"{pick} {day.title()} is {stage}; deadline is {deadline}. For {concept}, lock one decision now.{event_bit}"[:220]

    history = "\n".join(recent_lines[-8:]) if recent_lines else "(no prior messages)"
    event_line = f"Injected event: {event}" if event else "Injected event: none"

    if prompt_style == "scene":
        if day_turn == 1:
            scene_sentence = (
                f"{DAY_STAGE_DIRECTIONS[day]} {DAY_PROMPT[day]}"
            )
            deadline_sentence = f"Deadline today is {deadline}."
            prompt = (
                f"Episode concept: {concept}\n"
                f"Day: {day.title()} ({stage})\n"
                f"Scene context: {scene_sentence}\n"
                f"Time pressure: {deadline_sentence}\n"
                f"{event_line}\n"
                f"Recent chat:\n{history}\n\n"
                "What do you say next?"
            )
        else:
            prompt = f"Recent chat:\n{history}\n\nWhat do you say next?"
    else:
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


def is_prompt_echo(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in PROMPT_ECHO_PATTERNS)


def token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.lower()))


def pairwise_overlap_penalty(messages: list[Message]) -> tuple[float, dict[str, float]]:
    by_char: dict[str, list[str]] = {}
    for m in messages:
        by_char.setdefault(m.character, []).append(m.message)

    joined = {c: " ".join(lines) for c, lines in by_char.items()}
    chars = sorted(joined.keys())
    if len(chars) < 2:
        return 0.0, {}

    overlaps: dict[str, float] = {}
    penalty = 0.0
    for i, c1 in enumerate(chars):
        s1 = token_set(joined[c1])
        if not s1:
            continue
        for c2 in chars[i + 1 :]:
            s2 = token_set(joined[c2])
            if not s2:
                continue
            jacc = len(s1 & s2) / max(1, len(s1 | s2))
            key = f"{c1} <> {c2}"
            overlaps[key] = round(jacc, 3)
            if jacc > 0.62:
                penalty += (jacc - 0.62) * 45
    return round(penalty, 2), overlaps


def _cross_char_phrase_penalty(messages: list[Message]) -> tuple[float, list[str]]:
    """Penalise phrases that appear verbatim in >1 character's messages."""
    by_char: dict[str, list[str]] = {}
    for m in messages:
        by_char.setdefault(m.character, []).append(m.message.lower())

    # Build 3-word n-grams per character
    def ngrams(text: str, n: int = 3) -> set[str]:
        words = re.findall(r"[a-z']+", text)
        return {" ".join(words[i:i+n]) for i in range(len(words) - n + 1)}

    char_ngrams: dict[str, set[str]] = {c: set() for c in by_char}
    for c, msgs in by_char.items():
        for msg in msgs:
            char_ngrams[c] |= ngrams(msg)

    repeated: list[str] = []
    chars = list(char_ngrams)
    for i, c1 in enumerate(chars):
        for c2 in chars[i+1:]:
            shared = char_ngrams[c1] & char_ngrams[c2]
            # Only flag phrases longer than stop-word noise
            meaningful = [p for p in shared if not all(w in {"the","a","an","and","or","in","on","is","it","to","of","at","we","i"} for w in p.split())]
            repeated.extend(meaningful[:3])  # cap output

    penalty = min(20.0, len(repeated) * 3.0)
    return round(penalty, 2), repeated[:10]


def _participation_balance(messages: list[Message], total: int) -> tuple[float, dict[str, float]]:
    """Penalise severely underrepresented characters (<10% share) and warn on >40%."""
    by_char: dict[str, int] = {}
    for m in messages:
        by_char[m.character] = by_char.get(m.character, 0) + 1

    fractions: dict[str, float] = {c: round(cnt / max(total, 1), 3) for c, cnt in by_char.items()}
    penalty = 0.0
    for c, frac in fractions.items():
        if by_char[c] < 2:  # severely underrepresented
            penalty += 5.0
        elif frac < 0.10:
            penalty += 2.0
        elif frac > 0.40:
            penalty += 2.0  # one character monopolising
    return round(penalty, 2), fractions


def _conflict_bonus(messages: list[Message]) -> int:
    """Award bonus points if characters push back or disagree."""
    conflict_markers = ["but", "i disagree", "no,", "that won't", "not sure", "wait,", "hold on", "actually,", "i don't think", "problem is", "issue is", "we need to", "we can't"]
    hits = sum(1 for m in messages if any(p in m.message.lower() for p in conflict_markers))
    return min(5, hits)  # up to +5


def _stage_coherence_penalty(messages: list[Message]) -> float:
    """Penalise if a later day's content is too lexically similar to an earlier day (topic stagnation)."""
    by_day: dict[str, list[str]] = {}
    for m in messages:
        by_day.setdefault(m.day, []).append(m.message.lower())

    days = list(by_day.keys())
    if len(days) < 2:
        return 0.0

    day_tokens: dict[str, set[str]] = {d: token_set(" ".join(msgs)) for d, msgs in by_day.items()}

    penalty = 0.0
    for i in range(1, len(days)):
        prev = day_tokens[days[i-1]]
        curr = day_tokens[days[i]]
        if not prev or not curr:
            continue
        overlap = len(prev & curr) / max(1, len(prev | curr))
        if overlap > 0.60:
            penalty += (overlap - 0.60) * 30  # proportional penalty

    return round(min(penalty, 15.0), 2)


def _formal_name_penalty(messages: list[Message]) -> int:
    """Penalise messages using full character names in overly formal constructions."""
    # Patterns like "Thanks, Margaret" / "I agree with Julian" / "As Marcus mentioned"
    formal_pattern = re.compile(
        r"\b(thanks|thank you|i agree with|as \w+ mentioned|great idea,|good point,)\b",
        re.IGNORECASE,
    )
    hits = sum(1 for m in messages if formal_pattern.search(m.message))
    return hits


def score_quality(messages: list[Message], personas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """
    Scoring categories (base = 72):
    1.  Prohibited phrases: -14 per hit
    2.  Em dash/en dash/curly quotes (\u2014\u2013\u2019\u201C\u201D): hard fail → 0
    3.  Prompt echo: hard fail → 0
    4.  Signature phrase usage: +up to 10
    5.  Rhythm/length variation: +up to 8
    6.  Distinctiveness spread: +up to 8
    7.  Min-content failures (<4 words): -8 per hit
    8.  Cross-character lexical overlap (Jaccard): penalty via pairwise_overlap_penalty()
    9.  Cross-character phrase repetition: -3 per shared 3-gram, max -20
    10. Participation balance: -2/-5 per under/over-represented character
    11. Conflict/disagreement bonus: +up to 5
    12. Stage coherence (topic stagnation): up to -15
    13. Formal name usage: -1 per hit
    """
    lowered = [m.message.lower() for m in messages]
    prohibited_hits = sum(sum(1 for p in PROHIBITED if p in msg) for msg in lowered)

    # Hard fail on em dash, en dash, or curly quotes — all strong AI formatting tells
    TYPOGRAPHIC_TELLS = ("\u2014", "\u2013", "\u2019", "\u201c", "\u201d")
    em_dash_hits = sum(1 for msg in [m.message for m in messages] if any(t in msg for t in TYPOGRAPHIC_TELLS))
    if em_dash_hits > 0:
        return {
            "score": 0,
            "prohibited_hits": prohibited_hits,
            "em_dash_hits": em_dash_hits,
            "prompt_echo_hits": 0,
            "min_content_failures": 0,
            "cross_character_overlap_penalty": 0.0,
            "pairwise_lexical_overlap": {},
            "rhythm_variation": 0,
            "signature_hits": {},
            "avg_length_by_character": {},
            "distinctiveness_spread": 0.0,
            "hard_fail_reason": "typographic_tell_detected",  # em dash, en dash, or curly quotes
        }

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

    prompt_echo_hits = sum(1 for msg in lowered if is_prompt_echo(msg))
    min_content_failures = sum(1 for l in lengths if l < 4)
    overlap_penalty, overlaps = pairwise_overlap_penalty(messages)

    # New scoring rules (#4970)
    phrase_penalty, repeated_phrases = _cross_char_phrase_penalty(messages)
    balance_penalty, participation = _participation_balance(messages, len(messages))
    conflict_bonus = _conflict_bonus(messages)
    coherence_penalty = _stage_coherence_penalty(messages)
    formal_penalty = _formal_name_penalty(messages)

    # Hard fail if prompt echo detected
    if prompt_echo_hits > 0:
        score = 0
    else:
        score = 72
        score -= prohibited_hits * 14
        score += min(10, int(signature_rate * 18))
        score += min(8, int(rhythm_variation / 2))
        score += min(8, int(distinctiveness_spread))
        score -= min_content_failures * 8
        score -= int(overlap_penalty)
        score -= int(phrase_penalty)       # cross-char phrase repetition
        score -= int(balance_penalty)      # participation imbalance
        score += conflict_bonus            # disagreement/tension
        score -= int(coherence_penalty)    # topic stagnation
        score -= formal_penalty            # "Thanks, Margaret" -1 each
        score = max(0, min(100, score))

    return {
        "score": score,
        "prohibited_hits": prohibited_hits,
        "prompt_echo_hits": prompt_echo_hits,
        "min_content_failures": min_content_failures,
        "cross_character_overlap_penalty": overlap_penalty,
        "pairwise_lexical_overlap": overlaps,
        "rhythm_variation": rhythm_variation,
        "signature_hits": per_character_signature_hits,
        "avg_length_by_character": avg_len_by_character,
        "distinctiveness_spread": round(distinctiveness_spread, 2),
        "cross_char_phrase_penalty": phrase_penalty,
        "repeated_phrases_sample": repeated_phrases,
        "participation_balance": participation,
        "participation_penalty": balance_penalty,
        "conflict_bonus": conflict_bonus,
        "stage_coherence_penalty": coherence_penalty,
        "formal_name_penalty": formal_penalty,
    }


def verify_real_inference(messages: list[Message], mode: str) -> dict[str, Any]:
    lowered = [m.message.lower() for m in messages]
    template_fingerprints = sum(
        1
        for msg in lowered
        if "deadline is" in msg or "lock one decision now" in msg
    )
    echo_hits = sum(1 for msg in lowered if is_prompt_echo(msg))
    unique_lines = len(set(m.message.strip() for m in messages))
    is_real = mode != "template" and echo_hits == 0 and template_fingerprints == 0 and unique_lines >= max(1, len(messages) // 2)
    return {
        "mode": mode,
        "real_inference": is_real,
        "template_fingerprints": template_fingerprints,
        "prompt_echo_hits": echo_hits,
        "unique_lines": unique_lines,
    }


def _distribute_images_wednesday(
    messages: list[Message],
    image_paths: list[str],
    day: str,
) -> None:
    """Inject image attachments into Wednesday messages in a mood-driven way.

    Three delivery patterns, chosen randomly each run:
    - 'dump'    : All 3 in Julian's first Wednesday message (grumpy/efficient)
    - 'scatter' : One image per message, with dialogue between each
    - 'two_one' : First 2 together, third later after some back-and-forth
    """
    if not image_paths or day != "wednesday":
        return

    wednesday_msgs = [m for m in messages if m.day == "wednesday"]
    if not wednesday_msgs:
        return

    julian_msgs = [m for m in wednesday_msgs if "Julian" in m.character]
    if not julian_msgs:
        julian_msgs = wednesday_msgs  # fallback: any wednesday speaker

    style = random.choice(["dump", "scatter", "two_one"])

    if style == "dump" or len(julian_msgs) == 1:
        # All 3 images in one message — Julian is in no mood to narrate
        julian_msgs[0].attachments = image_paths[:3]

    elif style == "scatter":
        # One image per Julian message, discussion happens in between
        for i, img in enumerate(image_paths[:3]):
            if i < len(julian_msgs):
                julian_msgs[i].attachments = [img]

    else:  # two_one
        # First 2 together ("here's what I've got so far"), third after debate
        julian_msgs[0].attachments = image_paths[:2]
        if len(julian_msgs) > 1:
            julian_msgs[-1].attachments = image_paths[2:3]


def run_simulation(
    concept: str,
    default_model: str,
    run_index: int,
    stage_only: str | None,
    injected_event: str | None,
    ticks_per_day: int,
    mode: str,
    prompt_style: str,
    character_models: dict[str, str] | None,
    image_paths: list[str] | None = None,  # 3 image paths from photography stage
) -> dict[str, Any]:
    personas = load_personas()
    start = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    messages: list[Message] = []
    recent_lines: list[str] = []

    days = [stage_only] if stage_only else DAY_ORDER
    for day_i, day in enumerate(days):
        stage = DAY_STAGE[day]
        names = participants_for_day(day)

        # Variable message count — sample fresh each day/run
        if ticks_per_day > 0:
            # Caller passed explicit count (e.g. pipeline stage calling with ticks_per_day=4)
            day_ticks = ticks_per_day
        else:
            lo, hi = TICKS_RANGE.get(day, (4, 6))
            day_ticks = random.randint(lo, hi)

        for tick in range(day_ticks):
            speaker = names[tick % len(names)]
            persona = personas[speaker]
            ts = start + timedelta(days=day_i, minutes=tick * (480 // max(1, day_ticks)))
            deadline = deadline_for_day(day)
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
                prompt_style=prompt_style,
                day_turn=tick + 1,
            )
            recent_lines.append(f"{speaker.split()[0]}: {line}")
            messages.append(Message(day=day, stage=stage, character=speaker, message=line, timestamp=ts.isoformat(), model=model))

        # After all Wednesday messages are generated, distribute image attachments
        if day == "wednesday" and image_paths:
            _distribute_images_wednesday(messages, image_paths, day)

    by_character: dict[str, int] = {}
    by_model: dict[str, int] = {}
    for m in messages:
        by_character[m.character] = by_character.get(m.character, 0) + 1
        by_model[m.model] = by_model.get(m.model, 0) + 1

    qa = score_quality(messages, personas)
    inference_check = verify_real_inference(messages, mode)
    return {
        "run": run_index,
        "concept": concept,
        "default_model": default_model,
        "prompt_style": prompt_style,
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
        "inference_check": inference_check,
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
    parser.add_argument("--prompt-style", choices=["scene", "full"], default="full")
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
                prompt_style=args.prompt_style,
                character_models=character_models,
            )
            suffix = f"{args.stage}" if args.stage else "full-week"
            safe_model = model.replace("/", "_").replace(":", "-")
            out_path = OUT_DIR / f"sim-{stamp}-{concept_slug}-{safe_model}-{args.prompt_style}-run{i}-{suffix}.json"
            out_path.write_text(json.dumps(result, indent=2))
            all_results.append(
                {
                    "model": model,
                    "run": i,
                    "path": str(out_path),
                    "qa": result["qa"]["score"],
                    "real_inference": result["inference_check"]["real_inference"],
                }
            )
            print(f"saved: {out_path}")
            print(
                f"messages: {result['metrics']['message_count']} | cast: {result['metrics']['unique_characters']} "
                f"| qa={result['qa']['score']} | real_inference={result['inference_check']['real_inference']}"
            )

    comparison = {
        "generated_at": datetime.now().isoformat(),
        "concept": args.concept,
        "mode": args.mode,
        "prompt_style": args.prompt_style,
        "models": models,
        "results": all_results,
    }
    comparison_path = OUT_DIR / f"sim-{stamp}-comparison.json"
    comparison_path.write_text(json.dumps(comparison, indent=2))
    print(f"comparison: {comparison_path}")


if __name__ == "__main__":
    main()
