#!/usr/bin/env python3
"""Run compressed one-week dialogue simulations with model comparisons.

Examples:
  PYTHONPATH=. uv run scripts/simulate_dialogue_week.py \
    --concept "Jalapeño Corn Dog Bites" --runs 3 --models "openai/gpt-5-mini,anthropic/claude-haiku-4-5-20251001"

  PYTHONPATH=. uv run scripts/simulate_dialogue_week.py \
    --concept "Mini Shepherd's Pies" --stage friday --event "ingredient shortage: cheddar"

  PYTHONPATH=. uv run scripts/simulate_dialogue_week.py \
    --concept "Brown Butter Pecan Tassies" \
    --character-models '{"Margaret Chen":"openai/gpt-5.1","default":"openai/gpt-5-mini"}'
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from backend.config import config
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
    "friday": "Final review is tense as approvals hinge on tiny fixes. Devon Park (site architect) joins to confirm deployment readiness.",
    "saturday": "Devon Park (site architect) leads deployment prep - mostly quiet execution until a staging snag interrupts flow.",
    "sunday": "Publish window is close and everyone is watching the clock.",
}

DAY_ARC = {
    "monday": (
        "SETUP: Someone floats the concept - raw, half-formed, maybe divisive. "
        "TENSION: Another character challenges it or suggests something that pulls in a different direction. They don't agree easily. "
        "RESOLUTION: The team locks a direction under deadline pressure. Not everyone is happy about it."
    ),
    "tuesday": (
        "SETUP: A specific technical problem surfaces - ratios, technique, or a substitution that seems wrong. "
        "TENSION: Margaret is skeptical or frustrated. Someone pushes back on her. The outcome isn't obvious. "
        "RESOLUTION: A decision is made and the recipe is confirmed, even if reluctantly."
    ),
    "wednesday": (
        "SETUP: Julian has a strong visual opinion. He shares it like it's obvious. "
        "TENSION: Someone disagrees with his aesthetic call. Creative egos clash. "
        "RESOLUTION: The hero shot is chosen. Julian may or may not get his way."
    ),
    "thursday": (
        "SETUP: Marcus shares copy. It's too long, too literary, or too something. "
        "TENSION: Margaret edits it harshly. Steph tries to mediate without offending anyone. "
        "RESOLUTION: The copy is approved, probably shorter than Marcus wanted."
    ),
    "friday": (
        "SETUP: Final review. Something isn't quite right and someone says so. "
        "TENSION: Time pressure makes the stakes real. A decision has to be made NOW. "
        "RESOLUTION: Approved or sent back with specific fixes. No vague feedback."
    ),
    "saturday": (
        "SETUP: Devon is handling deployment. It's mostly quiet. "
        "TENSION: One small technical snag interrupts the calm. Devon fixes it without drama. "
        "RESOLUTION: Staged. Brief confirmation. Everyone moves on."
    ),
    "sunday": (
        "SETUP: Publish window is here. "
        "TENSION: Last-second nerves or one final check. "
        "RESOLUTION: Published. A moment of warmth or exhausted relief."
    ),
}

_DAY_OPENER_CONTEXT = {
    "monday": "Start of a new week. You're arriving fresh (or not). Open the group chat or walk into the kitchen.",
    "tuesday": "Day two. Picking up from yesterday's concept lock. Check in before diving into recipe work.",
    "wednesday": "Photo day. Arriving at the set or dropping into the chat with visual work to share.",
    "thursday": "Copy day. Opening your laptop or checking messages with writing on your mind.",
    "friday": "End of the working week. Final review energy. Whatever's on your mind about wrapping this up.",
    "saturday": "Weekend deployment. Quieter energy. Brief check-in before getting to work.",
    "sunday": "Publish day. The anticipation is real. A quick word before the final push.",
}

_DAY_CLOSER_CONTEXT = {
    "monday": "Concept is locked (or close enough). Confirm what was decided and sign off for the day.",
    "tuesday": "Recipe draft is done. Close out the work session.",
    "wednesday": "Hero shot is picked. Sign off - maybe a last comment about the images.",
    "thursday": "Copy is submitted. Wrap up the writing discussion.",
    "friday": "Review verdict is in. Close the week's creative work.",
    "saturday": "Staging is done. Brief sign-off.",
    "sunday": "It's published. A moment of relief, warmth, or exhaustion. Say goodnight.",
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
    "saturday":  (3, 5),   # enough for Devon's snag scene
    "sunday":    (3, 4),   # brief nervousness + publish + warmth
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


_CHARACTER_VOICE_GUIDES: dict[str, str] = {
    "Margaret Chen": (
        "Margaret speaks in short, clipped sentences. Fragments. Verdicts, not speeches. "
        "She mutters. She states facts like they're obvious. Dry humor slips out sideways. "
        "1-3 sentences max. Average message: 8-15 words. "
        "She is protective of Steph but expresses it as irritation. She respects Marcus's food knowledge "
        "but finds his verbosity exhausting. She thinks Julian is everything wrong with modern food culture "
        "but won't admit his photos make her recipes look better. Devon is tolerable because he doesn't small talk. "
        "Her internal war: she resents Instagram culture but checks engagement numbers. She says she's here for the paycheck "
        "but works late perfecting recipes nobody asked her to improve. She is deeply lonely and pushes people away with grumpiness."
    ),
    "Stephanie 'Steph' Whitmore": (
        "Steph hedges. She qualifies. She trails off. She apologizes before giving feedback. "
        "Her sentences are longer because she's padding them with uncertainty. 'I think maybe we could possibly...' "
        "1-2 longer sentences. Average message: 15-25 words. "
        "She's terrified of Margaret but desperately wants her approval. She envies Julian's confidence. "
        "She finds Marcus's over-writing exhausting but is afraid to edit too heavily. "
        "She knows Devon isn't working full hours but can't address it. "
        "Her internal war: she has good instincts but doesn't trust them. She craves validation but dismisses compliments. "
        "She reads about decisive leadership but practices collaborative indecisiveness."
    ),
    "Julian Torres": (
        "Julian is declarative and theory-laden. Confident surface, fragile underneath. "
        "He speaks in aesthetic terms and art-school vocabulary. References photographers nobody's heard of. "
        "1-2 sentences. Average message: 12-20 words. "
        "He's intimidated by Margaret's competence. He finds Steph's indecisiveness frustrating but has learned "
        "she'll approve anything if he uses enough jargon. He and Marcus have an unspoken rivalry over who's 'the creative one.' "
        "Devon's indifference to aesthetics annoys him. "
        "His internal war: he mocks Instagram culture but checks his engagement obsessively. He claims to be above commercial work "
        "but desperately needs this job. He's actually good at making content but frames everything as ART."
    ),
    "Marcus Reid": (
        "Marcus over-explains. He's literary, referential, always one sentence too many. "
        "He uses 'whom' in Slack. He can't resist an analogy or a food-history tangent. "
        "2-3 sentences. Average message: 20-35 words. "
        "He respects Margaret's food knowledge. He's frustrated Steph won't edit his copy decisively. "
        "He sees Julian as a competitor for the 'artistic one' title. Devon's apparent lack of ambition baffles him. "
        "His internal war: he wants to be a serious novelist but is increasingly good at commercial writing. "
        "He resents the recipe work but puts more effort into it than anyone asks. "
        "He mourns his novel's failure but hasn't started a second book."
    ),
    "Devon Park": (
        "Devon is efficient and understated. Slightly condescending, then feels bad about it. "
        "Technical jargon used to hide gaps. Casual. Dry. "
        "1 sentence max. Average message: 5-12 words. "
        "He appreciates Margaret's no-small-talk policy. He wishes Steph would just tell him what to do. "
        "He finds Julian exhausting. He doesn't understand why Marcus writes so much. "
        "His internal war: he lied to get the job but is honest about the work itself. "
        "He appears lazy but has high personal standards for his code. "
        "He automated most of his job and isn't sure if he should tell anyone."
    ),
}


def build_system_prompt(persona: dict[str, Any]) -> str:
    name = persona["name"]
    comm = persona["communication_style"]
    voice_guide = _CHARACTER_VOICE_GUIDES.get(name, "")

    # Build relationship summary as emotional tensions, not data dump
    relationships = persona.get("relationships", {})
    rel_lines = []
    for role, desc in relationships.items():
        # Take first 2 sentences of each relationship — the emotional core
        sentences = desc.split(". ")
        rel_lines.append(f"- {role}: {'. '.join(sentences[:2])}.")

    return (
        f"You are {name} ({persona['role']}). Stay strictly in character.\n"
        "Write ONE group chat message. No narration, no markdown, no role labels.\n\n"
        f"WHO YOU ARE:\n{persona['backstory'][:600]}\n\n"
        f"HOW YOU SPEAK:\n{voice_guide}\n\n"
        f"SIGNATURE PHRASES (use as OCCASIONAL spice — once or twice a WEEK, not every message):\n"
        f"{', '.join(comm.get('signature_phrases', []))}\n\n"
        f"INTERNAL CONTRADICTIONS (these make you human — lean into them):\n"
        f"{chr(10).join('- ' + c for c in persona.get('internal_contradictions', []))}\n\n"
        f"KEY RELATIONSHIPS:\n{chr(10).join(rel_lines)}\n\n"
        f"TRIGGERS (these make you react strongly): {', '.join(persona.get('triggers', []))}\n\n"
        "RULES:\n"
        "- Signature phrases are spice, not default. Vary your openings.\n"
        "- Email habits (signing with initials, etc.) do NOT apply in group chat.\n"
        "- NEVER use em dashes (\u2014), en dashes (\u2013), or curly quotes (\u2018\u2019\u201c\u201d). "
        "Use plain hyphens and straight apostrophes only."
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


def _deadline_meridiem(deadline: str) -> str | None:
    m = re.search(r"\b(AM|PM)\b", deadline, re.IGNORECASE)
    return m.group(1).lower() if m else None


def _normalize_time_notation(text: str, deadline: str) -> str:
    """Normalize chat times to explicit style like '4:00 pm'."""
    meridiem = _deadline_meridiem(deadline)

    # 4pm / 4 pm -> 4:00 pm
    text = re.sub(
        r"\b([1-9]|1[0-2])\s*(am|pm)\b",
        lambda m: f"{m.group(1)}:00 {m.group(2).lower()}",
        text,
        flags=re.IGNORECASE,
    )

    # 4:00 (without am/pm) -> 4:00 pm when deadline gives a meridiem hint
    if meridiem:
        text = re.sub(
            r"\b([1-9]|1[0-2]):([0-5][0-9])\b(?!\s*(?:am|pm)\b)",
            lambda m: f"{m.group(1)}:{m.group(2)} {meridiem}",
            text,
            flags=re.IGNORECASE,
        )
        # by 5 / at 3 / due 4 -> by 5:00 pm / at 3:00 pm / due 4:00 pm
        text = re.sub(
            r"\b(by|at|due)\s+([1-9]|1[0-2])\b(?!\s*[:0-9]|(?:\s*(?:am|pm)\b))",
            lambda m: f"{m.group(1)} {m.group(2)}:00 {meridiem}",
            text,
            flags=re.IGNORECASE,
        )

    return text


def _canonicalize_flour_ricotta_ratios(text: str) -> str:
    """Canonicalize ratio wording to flour:ricotta to avoid contradictory phrasing."""
    number = r"\d+(?:\.\d+)?"

    # "1:1 flour to ricotta" -> "1:1 flour:ricotta"
    text = re.sub(
        rf"\b({number})\s*:\s*({number})\s*flour\s+to\s+ricotta\b",
        lambda m: f"{m.group(1)}:{m.group(2)} flour:ricotta",
        text,
        flags=re.IGNORECASE,
    )
    # "1:1 ricotta to flour" -> "1:1 flour:ricotta" (swap values)
    text = re.sub(
        rf"\b({number})\s*:\s*({number})\s*ricotta\s+to\s+flour\b",
        lambda m: f"{m.group(2)}:{m.group(1)} flour:ricotta",
        text,
        flags=re.IGNORECASE,
    )
    # "1:1 flour:ricotta" (already canonical shape)
    text = re.sub(
        rf"\b({number})\s*:\s*({number})\s*flour\s*:\s*ricotta\b",
        lambda m: f"{m.group(1)}:{m.group(2)} flour:ricotta",
        text,
        flags=re.IGNORECASE,
    )
    # "1:1 ricotta:flour" -> "1:1 flour:ricotta" (swap values)
    text = re.sub(
        rf"\b({number})\s*:\s*({number})\s*ricotta\s*:\s*flour\b",
        lambda m: f"{m.group(2)}:{m.group(1)} flour:ricotta",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _jaccard_similarity(a: str, b: str) -> float:
    ta = token_set(a)
    tb = token_set(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, len(ta | tb))


def _is_repetitive_candidate(candidate: str, recent_lines: list[str], threshold: float = 0.72) -> bool:
    """Detect near-duplicate wording against recent messages."""
    recent_msgs = []
    for line in recent_lines[-6:]:
        parts = line.split(": ", 1)
        recent_msgs.append(parts[1] if len(parts) == 2 else line)
    return any(_jaccard_similarity(candidate.lower(), prev.lower()) >= threshold for prev in recent_msgs)


def _shared_trigram_with_recent(candidate: str, recent_lines: list[str]) -> bool:
    candidate_tokens = re.findall(r"[a-z']+", candidate.lower())
    if len(candidate_tokens) < 3:
        return False
    candidate_ngrams = {
        " ".join(candidate_tokens[i:i+3]) for i in range(len(candidate_tokens) - 2)
    }
    if not candidate_ngrams:
        return False

    stop = {"the", "a", "an", "and", "or", "in", "on", "is", "it", "to", "of", "at", "we", "i"}
    meaningful = {ng for ng in candidate_ngrams if not all(w in stop for w in ng.split())}
    if not meaningful:
        return False

    for line in recent_lines[-6:]:
        parts = line.split(": ", 1)
        prev = parts[1] if len(parts) == 2 else line
        prev_tokens = re.findall(r"[a-z']+", prev.lower())
        if len(prev_tokens) < 3:
            continue
        prev_ngrams = {" ".join(prev_tokens[i:i+3]) for i in range(len(prev_tokens) - 2)}
        if meaningful & prev_ngrams:
            return True
    return False


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
    is_last_turn: bool = False,
) -> str:
    if mode == "template":
        sig = persona["communication_style"].get("signature_phrases", ["Right."])
        pick = random.choice(sig)
        event_bit = f" Also: {event}." if event else ""
        return f"{pick} {day.title()} is {stage}; deadline is {deadline}. For {concept}, lock one decision now.{event_bit}"[:220]

    history = "\n".join(recent_lines[-8:]) if recent_lines else "(no prior messages)"
    event_line = f"Injected event: {event}" if event else "Injected event: none"

    if prompt_style == "scene":
        scene_sentence = f"{DAY_STAGE_DIRECTIONS[day]} {DAY_PROMPT[day]}"
        deadline_sentence = f"Deadline today is {deadline}."
        arc_summary = _build_dynamic_arc(day, concept)

        if day_turn == 1:
            # First message of the day: the speaker introduces the topic,
            # not reacts to it. Without this, models read the concept from
            # context and respond as if someone already pitched it off-screen.
            opener_directive = (
                "IMPORTANT: You are opening this conversation. Nobody has spoken yet today.\n"
                "STRUCTURE: Your FIRST sentence must be a greeting or arrival moment - "
                "'Morning', 'Hey all', 'Just got in', etc. Even one word counts. "
                "Your SECOND sentence introduces or pitches the day's topic. "
                "Do NOT skip the greeting and jump straight into work.\n"
                f"Arrival context: {_DAY_OPENER_CONTEXT[day]}"
            )
            prompt = (
                f"Episode concept: {concept}\n"
                f"Day: {day.title()} ({stage})\n"
                f"Scene context: {scene_sentence}\n"
                f"Story arc: {arc_summary}\n"
                f"Time pressure: {deadline_sentence}\n"
                f"{event_line}\n"
                f"Recent chat:\n{history}\n\n"
                f"{opener_directive}\n"
                "What do you say next?"
            )
        else:
            # Turn 2+: full scene context on EVERY turn — never let the model lose the scene
            previous_speaker = ""
            if recent_lines:
                last_line = recent_lines[-1]
                previous_speaker = last_line.split(":")[0].strip()

            # Role-chain reasoning — model thinks through the character before speaking
            name = persona["name"].split()[0]
            role_chain = (
                f"Before responding, consider: given {name}'s relationship with {previous_speaker} "
                f"and what was just said, what does {name} actually feel? "
                f"What would they want to say vs. what they actually say?\n"
                "Then write only their message."
            ) if previous_speaker else "React to what was just said. Stay in the scene."

            prompt = (
                f"Day: {day.title()} - {stage}. Deadline: {deadline}.\n"
                f"Scene: {scene_sentence}\n"
                f"Arc reminder: {arc_summary}\n"
                f"{event_line}\n"
                f"Recent chat:\n{history}\n\n"
                f"{role_chain}"
            )
    else:
        opener_hint = ""
        if day_turn == 1:
            opener_hint = (
                "\nIMPORTANT: You are opening this conversation. Nobody has spoken yet today. "
                "Your FIRST sentence must be a greeting or arrival moment. "
                "Your SECOND sentence introduces the topic. Don't skip the greeting.\n"
                f"Arrival context: {_DAY_OPENER_CONTEXT[day]}\n"
            )
        prompt = (
            f"Episode concept: {concept}\n"
            f"Day: {day.title()} ({stage})\n"
            f"Deadline pressure: {deadline}\n"
            f"Scene goal: {DAY_PROMPT[day]}\n"
            f"{event_line}\n"
            f"Recent chat:\n{history}\n\n"
            f"{opener_hint}"
            "Write this character's next message. Keep it natural and specific."
        )

    # Closer directive for the last message of the day
    if is_last_turn:
        prompt += (
            "\n\nIMPORTANT: This is the LAST message of today's conversation. "
            "STRUCTURE: Briefly confirm what was decided (one sentence max), "
            "then END with a sign-off — 'heading out', 'see you tomorrow', 'night', "
            "'done for today', 'logging off', etc. Your FINAL sentence must be a goodbye or departure. "
            "Keep it short. Don't introduce new topics or ask questions.\n"
            f"Wrap-up context: {_DAY_CLOSER_CONTEXT[day]}"
        )

    msg = generate_response(
        prompt=prompt,
        system_prompt=build_system_prompt(persona),
        model=model,
        temperature=0.8,
    ).strip()
    if _is_repetitive_candidate(msg, recent_lines) or _shared_trigram_with_recent(msg, recent_lines):
        rewrite_prompt = (
            f"Recent chat:\n{history}\n\n"
            f"Your draft is too repetitive:\n{msg}\n\n"
            "Rewrite ONE message with different structure and new specific detail. "
            "Do not repeat existing phrasing."
        )
        msg = generate_response(
            prompt=rewrite_prompt,
            system_prompt=build_system_prompt(persona),
            model=model,
            temperature=0.9,
        ).strip()

    msg = sanitize_typographic_tells(msg)
    msg = _normalize_time_notation(msg, deadline)
    msg = _canonicalize_flour_ricotta_ratios(msg)
    return " ".join(msg.split())


def is_prompt_echo(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in PROMPT_ECHO_PATTERNS)


_TYPOGRAPHIC_REPLACEMENTS = [
    ("\u2014", " - "),   # em dash -> spaced hyphen
    ("\u2013", " - "),   # en dash -> spaced hyphen
    ("\u2019", "'"),     # right curly apostrophe -> straight apostrophe
    ("\u201c", '"'),     # left curly double quote -> straight double quote
    ("\u201d", '"'),     # right curly double quote -> straight double quote
]


def sanitize_typographic_tells(text: str) -> str:
    """Strip AI-telltale typographic characters before they reach QA scoring.

    Replaces em dashes, en dashes, and curly quotes with plain ASCII equivalents.
    Defense-in-depth layer -- the QA scorer still hard-fails on these if any
    slip through, but sanitizing at generation time prevents wasted runs.
    """
    for bad, replacement in _TYPOGRAPHIC_REPLACEMENTS:
        text = text.replace(bad, replacement)
    return text


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


def _voice_pattern_score(messages: list[Message]) -> tuple[int, dict[str, list[str]]]:
    """Score voice authenticity by checking character-specific speech patterns.

    Returns (bonus points, {character: [patterns matched]}).
    """
    by_char: dict[str, list[str]] = {}
    for m in messages:
        by_char.setdefault(m.character, []).append(m.message)

    total_bonus = 0
    pattern_hits: dict[str, list[str]] = {}

    for char, msgs in by_char.items():
        hits: list[str] = []
        avg_words = mean([len(re.findall(r"\w+", m)) for m in msgs]) if msgs else 0
        all_text = " ".join(m.lower() for m in msgs)

        if "Margaret" in char:
            if avg_words < 15:
                hits.append(f"terse (avg {avg_words:.0f} words)")
                total_bonus += 2
            # Check for sentence fragments (sentences without verbs / very short)
            fragment_count = sum(1 for m in msgs if len(re.findall(r"\w+", m)) <= 6)
            if fragment_count >= 1:
                hits.append(f"fragments ({fragment_count})")
                total_bonus += 2

        elif "Steph" in char:
            hedging = ["maybe", "could we", "i think", "possibly", "i'm probably", "sorry", "what do you"]
            hedge_count = sum(1 for h in hedging if h in all_text)
            if hedge_count >= 2:
                hits.append(f"hedging ({hedge_count} markers)")
                total_bonus += 2

        elif "Julian" in char:
            aesthetic = ["visual", "negative space", "composition", "aesthetic", "intentional", "lighting", "narrative", "framework"]
            aesthetic_count = sum(1 for a in aesthetic if a in all_text)
            if aesthetic_count >= 1:
                hits.append(f"aesthetic terms ({aesthetic_count})")
                total_bonus += 2

        elif "Marcus" in char:
            if avg_words > 20:
                hits.append(f"verbose (avg {avg_words:.0f} words)")
                total_bonus += 2
            # Literary references or analogies
            literary = ["proust", "fisher", "hemingway", "like a", "as if", "reminds me of", "there's a tradition", "narrative arc"]
            lit_count = sum(1 for l in literary if l in all_text)
            if lit_count >= 1:
                hits.append(f"literary ({lit_count})")
                total_bonus += 2

        elif "Devon" in char:
            if avg_words < 12:
                hits.append(f"minimal (avg {avg_words:.0f} words)")
                total_bonus += 2

        pattern_hits[char] = hits

    return min(14, total_bonus), pattern_hits


def _catchphrase_restraint_bonus(messages: list[Message], personas: dict[str, dict[str, Any]]) -> tuple[int, dict[str, int]]:
    """Bonus if signature phrases are used sparingly. Penalty if overused."""
    per_char_hits: dict[str, int] = {}
    for m in messages:
        sigs = [s.lower() for s in personas[m.character]["communication_style"].get("signature_phrases", [])]
        hits = sum(1 for s in sigs if s and s in m.message.lower())
        per_char_hits[m.character] = per_char_hits.get(m.character, 0) + hits

    max_hits = max(per_char_hits.values()) if per_char_hits else 0
    total_hits = sum(per_char_hits.values())

    if max_hits <= 1 and total_hits <= 3:
        # Sparing, natural use — reward
        return 3, per_char_hits
    elif max_hits >= 3:
        # One character hammering catchphrases — penalize
        return -5, per_char_hits
    else:
        return 0, per_char_hits


def score_quality(messages: list[Message], personas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """
    Scoring categories (base = 72):
    1.  Prohibited phrases: -14 per hit
    2.  Em dash/en dash/curly quotes (\u2014\u2013\u2019\u201C\u201D): hard fail → 0
    3.  Prompt echo: hard fail → 0
    4.  Voice pattern authenticity: +up to 14 (replaces old signature match)
    5.  Catchphrase restraint: +3 if sparing, -5 if overused
    6.  Rhythm/length variation: +up to 8
    7.  Distinctiveness spread: +up to 8
    8.  Min-content failures (<4 words): -8 per hit
    9.  Cross-character lexical overlap (Jaccard): penalty via pairwise_overlap_penalty()
    10. Cross-character phrase repetition: -3 per shared 3-gram, max -20
    11. Participation balance: -2/-5 per under/over-represented character
    12. Conflict/disagreement bonus: +up to 5
    13. Stage coherence (topic stagnation): up to -15
    14. Formal name usage: -1 per hit
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

    # Voice pattern scoring (replaces old exact signature match)
    voice_bonus, voice_patterns = _voice_pattern_score(messages)
    catchphrase_bonus, per_character_signature_hits = _catchphrase_restraint_bonus(messages, personas)

    avg_len_by_character: dict[str, float] = {}
    for char in set(m.character for m in messages):
        c_lengths = [len(re.findall(r"\w+", m.message)) for m in messages if m.character == char]
        avg_len_by_character[char] = round(mean(c_lengths), 2) if c_lengths else 0.0

    distinctiveness_spread = 0.0
    if avg_len_by_character:
        vals = list(avg_len_by_character.values())
        distinctiveness_spread = max(vals) - min(vals)

    prompt_echo_hits = sum(1 for msg in lowered if is_prompt_echo(msg))
    min_content_failures = sum(1 for msg_len in lengths if msg_len < 4)
    overlap_penalty, overlaps = pairwise_overlap_penalty(messages)

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
        score += voice_bonus               # voice pattern authenticity (+up to 14)
        score += catchphrase_bonus          # restraint bonus (+3) or overuse penalty (-5)
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
        "voice_pattern_bonus": voice_bonus,
        "voice_patterns_matched": voice_patterns,
        "catchphrase_bonus": catchphrase_bonus,
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


def _build_dynamic_arc(day: str, concept: str, photography_context: dict | None = None) -> str:
    """Enrich static DAY_ARC with concept-specific tension."""
    base = DAY_ARC[day]

    if day == "monday":
        return (
            f"Margaret pitches '{concept}'. She has opinions about whether this respects the craft. "
            f"Marcus sees a literary angle in it. Steph worries about audience appeal. "
            f"{base}"
        )
    elif day == "tuesday":
        return (
            f"The team is developing the recipe for '{concept}'. Ratios, technique, substitutions. "
            f"Margaret is skeptical of shortcuts. Someone wants to experiment. "
            f"{base}"
        )
    elif day == "wednesday":
        winner_variant = ""
        if photography_context and photography_context.get("winner"):
            winner_variant = photography_context["winner"].get("variant", "")
        if winner_variant:
            return (
                f"Julian has distinct shots of '{concept}'. Each character has a different favorite. "
                f"Margaret wants food shown honestly. Julian wants the artistic angle. "
                f"Steph thinks about feed performance. After real debate, they land on '{winner_variant}'. "
                f"{base}"
            )
        return (
            f"Julian has three distinct shots of '{concept}'. Each character has a different favorite. "
            f"Margaret wants food shown honestly. Julian wants the artistic angle. "
            f"Steph thinks about what performs in feed. They disagree before converging. "
            f"{base}"
        )
    elif day == "thursday":
        return (
            f"Marcus shares copy for '{concept}'. It's too long, too literary, or too something. "
            f"Margaret edits it harshly. Steph tries to mediate. "
            f"{base}"
        )
    elif day == "friday":
        return (
            f"Final review of '{concept}'. The recipe was locked Tuesday. "
            f"Photos were selected Wednesday. Now something isn't quite right. "
            f"{base}"
        )
    elif day == "saturday":
        return (
            f"Devon is staging '{concept}' for deployment. It's mostly quiet. "
            f"{base}"
        )
    elif day == "sunday":
        return (
            f"Publish window for '{concept}'. The week's work comes to a point. "
            f"{base}"
        )
    return base


# Domain keywords that boost a character's likelihood of speaking next
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "Margaret Chen": ["recipe", "ratio", "batter", "flour", "butter", "oven", "temperature", "dough", "technique", "crust", "filling", "ingredient"],
    "Stephanie 'Steph' Whitmore": ["approve", "decision", "timeline", "deadline", "team", "consensus", "feedback", "direction", "strategy"],
    "Julian Torres": ["photo", "shot", "lighting", "angle", "visual", "image", "composition", "negative space", "styling", "aesthetic"],
    "Marcus Reid": ["copy", "headline", "description", "words", "writing", "draft", "tone", "voice", "narrative", "story"],
    "Devon Park": ["deploy", "staging", "site", "push", "build", "server", "code", "fix", "automated", "script"],
}

# Day leads — who opens each day (deterministic)
_DAY_LEADS: dict[str, str] = {
    "monday": "Margaret Chen",
    "tuesday": "Margaret Chen",
    "wednesday": "Julian Torres",
    "thursday": "Marcus Reid",
    "friday": "Margaret Chen",
    "saturday": "Devon Park",
    "sunday": "Stephanie 'Steph' Whitmore",
}


def _select_next_speaker(
    names: list[str],
    day: str,
    tick: int,
    recent_lines: list[str],
    speak_counts: dict[str, int],
    total_ticks: int,
) -> str:
    """Select next speaker using weighted reactive selection instead of round-robin."""
    # Turn 1: day's lead character
    if tick == 0:
        lead = _DAY_LEADS.get(day, names[0])
        return lead if lead in names else names[0]

    scores: dict[str, float] = {name: 1.0 for name in names}

    # Who just spoke? Suppress back-to-back
    last_speaker = ""
    if recent_lines:
        last_speaker = recent_lines[-1].split(":")[0].strip()
    for name in names:
        first = name.split()[0]
        if first == last_speaker:
            scores[name] *= 0.15  # strong suppression, not zero

    # Was this character addressed or referenced in recent messages?
    last_few = " ".join(recent_lines[-3:]).lower() if recent_lines else ""
    for name in names:
        first = name.split()[0].lower()
        if first in last_few:
            # Don't boost if they were the one speaking (already counted above)
            if first != last_speaker.lower():
                scores[name] *= 2.5

    # Domain relevance — was their topic area mentioned?
    for name in names:
        keywords = _DOMAIN_KEYWORDS.get(name, [])
        if any(kw in last_few for kw in keywords):
            scores[name] *= 1.8

    # Hasn't spoken in a while? Boost
    for name in names:
        count = speak_counts.get(name, 0)
        if count == 0 and tick >= 2:
            scores[name] *= 3.0  # strong pull for silent characters
        elif tick > 0 and count > 0:
            # Check recency — how many turns since they last spoke
            recency = 0
            first = name.split()[0]
            for line in reversed(recent_lines):
                if line.split(":")[0].strip() == first:
                    break
                recency += 1
            if recency >= 3:
                scores[name] *= 1.5 + (recency - 3) * 0.3

    # Cap any character at ~40% of day's messages
    max_count = max(1, int(total_ticks * 0.4))
    for name in names:
        if speak_counts.get(name, 0) >= max_count:
            scores[name] *= 0.05

    # Weighted random selection
    total = sum(scores.values())
    if total <= 0:
        return random.choice(names)
    r = random.random() * total
    cumulative = 0.0
    for name in names:
        cumulative += scores[name]
        if r <= cumulative:
            return name
    return names[-1]


def _build_photography_scene_direction(photography_context: dict | None, day: str) -> str | None:
    """Build dynamic scene direction based on photography results.

    Returns a scene override string or None to use the default.
    """
    if not photography_context or not isinstance(photography_context, dict):
        return None

    reshoot_happened = photography_context.get("reshoot_happened", False)
    winner = photography_context.get("winner", {})
    winner_variant = winner.get("variant", "unknown")
    winner_round = winner.get("round", 1)

    # Build human-readable variant descriptions for the characters to reference
    rounds = photography_context.get("rounds", [])
    variant_descriptions = {
        "macro_closeup": "extreme close-up (one item filling the frame, crumb detail, shallow focus)",
        "overhead_flatlay": "overhead flat lay (full tin from above, everything in focus, geometric)",
        "hero_threequarter": "hero three-quarter angle (2-3 items on a board, one broken open, warm and inviting)",
    }
    shot_list = []
    last_round = rounds[-1] if rounds else {}
    for v in last_round.get("variants", []):
        desc = variant_descriptions.get(v["variant"], v["variant"])
        shot_list.append(f"'{v['variant']}' - {desc}")
    shots_text = "; ".join(shot_list) if shot_list else "three distinctly different angles"

    if day == "wednesday":
        if reshoot_happened:
            rejection = ""
            if rounds and not rounds[0].get("passed", True):
                rejection = rounds[0].get("rejection_reason", "the shots all looked the same")
            return (
                f"Julian drops the first batch of photos and it's a disaster - {rejection}. "
                "Steph calls it out immediately. Panic about the timeline. "
                "'Can we even reshoot by Thursday?' Julian pushes back but agrees to a rush reshoot. "
                f"He delivers a second batch with three new angles: {shots_text}. "
                "Now the team has to pick the hero image. Each character has a different favorite. "
                "They argue about which shot tells the right story for this recipe. "
                f"After real debate, they land on '{winner_variant}' as the hero."
            )
        else:
            return (
                f"Julian drops three distinctly different shots: {shots_text}. "
                "Now the team has to pick the hero image - the ONE shot that represents this recipe everywhere. "
                "Each character has a strong opinion about which shot should lead. "
                "Julian has his artistic preference. Margaret cares about showing the food honestly. "
                "Steph is thinking about what performs in feed. They disagree before converging. "
                f"After real debate, they land on '{winner_variant}' as the hero."
            )
    elif day == "friday":
        if reshoot_happened:
            return (
                f"Final review is tense. Julian's rush reshoot on Wednesday saved the week - "
                f"the '{winner_variant}' from round {winner_round} is the hero. "
                "Devon Park joins to confirm deployment readiness. Small fixes and sign-off."
            )
        else:
            return (
                f"Final review. Julian's '{winner_variant}' was selected as hero on Wednesday. "
                "Devon Park joins to confirm deployment readiness. Approvals hinge on tiny fixes."
            )

    return None


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
    photography_context: dict | None = None,  # full photography data with rounds/reshoot
) -> dict[str, Any]:
    personas = load_personas()
    start = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    messages: list[Message] = []
    recent_lines: list[str] = []

    days = [stage_only] if stage_only else DAY_ORDER
    for day_i, day in enumerate(days):
        stage = DAY_STAGE[day]
        names = participants_for_day(day)

        # Override scene direction if photography context provides one
        photo_scene = _build_photography_scene_direction(photography_context, day)
        if photo_scene:
            # Temporarily override for this day's generation
            _original_direction = DAY_STAGE_DIRECTIONS.get(day, "")
            DAY_STAGE_DIRECTIONS[day] = photo_scene

        # Variable message count — sample fresh each day/run
        if ticks_per_day > 0:
            # Caller passed explicit count (e.g. pipeline stage calling with ticks_per_day=4)
            day_ticks = ticks_per_day
        else:
            lo, hi = TICKS_RANGE.get(day, (4, 6))
            day_ticks = random.randint(lo, hi)

        # Wednesday with photography context needs enough messages for hero debate
        if day == "wednesday" and photography_context:
            if photography_context.get("reshoot_happened"):
                day_ticks = max(day_ticks, 10)  # panic + reshoot + hero debate
            else:
                day_ticks = max(day_ticks, 7)  # drop shots + disagree + converge on hero

        speak_counts: dict[str, int] = {name: 0 for name in names}
        for tick in range(day_ticks):
            speaker = _select_next_speaker(names, day, tick, recent_lines, speak_counts, day_ticks)
            speak_counts[speaker] = speak_counts.get(speaker, 0) + 1
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
                is_last_turn=(tick == day_ticks - 1),
            )
            recent_lines.append(f"{speaker.split()[0]}: {line}")
            messages.append(Message(day=day, stage=stage, character=speaker, message=line, timestamp=ts.isoformat(), model=model))

        # After all Wednesday messages are generated, distribute image attachments
        if day == "wednesday" and image_paths:
            _distribute_images_wednesday(messages, image_paths, day)

        # Restore original scene direction if we overrode it
        if photo_scene:
            DAY_STAGE_DIRECTIONS[day] = _original_direction

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
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
    parser.add_argument("--model", default=config.dialogue_model, help=f"Single default model (current: {config.dialogue_model})")
    parser.add_argument("--models", default=None, help="Comma-separated models for comparison")
    parser.add_argument("--character-models", default=None, help="JSON map of character=>model, with optional default")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--stage", choices=DAY_ORDER, default=None)
    parser.add_argument("--event", default=None)
    parser.add_argument("--ticks-per-day", type=int, default=6)
    parser.add_argument("--mode", choices=["llm", "template"], default="llm")
    parser.add_argument("--prompt-style", choices=["scene", "full"], default="full")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    models = parse_models(args.models) if args.models else [args.model]
    character_models = json.loads(args.character_models) if args.character_models else None

    all_results: list[dict[str, Any]] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
