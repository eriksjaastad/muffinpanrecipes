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
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from backend.config import config
from backend.utils.model_router import generate_response

ROOT = Path(__file__).resolve().parents[1]
PERSONAS_PATH = ROOT / "backend" / "data" / "agent_personalities.json"
CHARACTERS_DIR = ROOT / "backend" / "data" / "characters"
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
DAY_MEETING_GOAL: dict[str, dict[str, str]] = {
    "monday": {
        "objective": "Decide which concept the team is making this week",
        "completion_signal": r"agreed|locked|going with|let's do|that's the one",
    },
    "tuesday": {
        "objective": "Nail down the recipe - ratios, technique, substitutions",
        "completion_signal": r"recipe works|ratios?.*(good|solid|locked)|finalized",
    },
    "wednesday": {
        "objective": "Choose the hero shot",
        "completion_signal": r"hero.*(is|shot)|going with|that's the one|locked",
    },
    "thursday": {
        "objective": "Debate the recipe title and approve the copy",
        "completion_signal": r"copy.*(approved|good|done|submitted)|approved|ship it|title.*(locked|works|good)",
    },
    "friday": {
        "objective": "Final review verdict - approve or send back",
        "completion_signal": r"approved|green.?light|good to go|ship|send.?back",
    },
    "saturday": {
        "objective": "Get it staged and verified",
        "completion_signal": r"staged|deployed|live|looks good|green",
    },
    "sunday": {
        "objective": "Publish",
        "completion_signal": r"published|live|it's up|went out",
    },
}

CHARACTER_DAY_GOALS: dict[str, dict[str, str]] = {
    "monday": {
        "Margaret Chen": "You have strong feelings about whether this concept respects the craft.",
        "Stephanie 'Steph' Whitmore": "You need the team to land on something. Guide without dictating.",
        "Julian Torres": "You're already thinking about how this will photograph.",
        "Marcus Reid": "You're looking for the story angle - what makes this worth writing about.",
        "Devon Park": "You're listening. You'll care when it hits deployment.",
        "Ria Castillo": "You're evaluating whether this concept will perform on social. Think hooks, shareability, visual potential.",
    },
    "tuesday": {
        "Margaret Chen": "This is your domain. The ratios have to be right or it doesn't ship.",
        "Stephanie 'Steph' Whitmore": "You need the recipe locked today. Keep things moving.",
        "Julian Torres": "You're thinking about how the final dish will photograph.",
        "Marcus Reid": "You're tasting and thinking about what story the recipe tells.",
        "Devon Park": "You're waiting for something to deploy. Not your day.",
    },
    "wednesday": {
        "Margaret Chen": "You want the food shown honestly. No tricks, no garnishes that don't belong.",
        "Stephanie 'Steph' Whitmore": "You're thinking about what performs in feed. The hero shot matters.",
        "Julian Torres": "These are YOUR shots. You have a strong opinion about which one leads.",
        "Marcus Reid": "You see the visual story. You have thoughts about which shot matches the copy.",
        "Devon Park": "You'll optimize whatever they pick. Not your fight.",
        "Ria Castillo": "This is your main day. You need shots that work as social content - vertical crops, close-ups, process moments. Push for what performs.",
    },
    "thursday": {
        "Margaret Chen": "You'll cut anything that doesn't serve the recipe. No patience for fluff. Titles should be simple and honest.",
        "Stephanie 'Steph' Whitmore": "You need a title locked and the copy approved. Mediate if it gets tense.",
        "Julian Torres": "You care about how the title and copy pair with your images. Short titles look better on the page.",
        "Marcus Reid": "You propose the title and it's YOUR copy being reviewed. You love a dramatic title but can be talked down.",
        "Devon Park": "You'll read it if someone sends it. Probably fine.",
        "Ria Castillo": "You need a title that works as a caption and a share card. Short, punchy, clickable. Six words max or you're losing people.",
    },
    "friday": {
        "Margaret Chen": "Last chance to catch something wrong. You take this seriously.",
        "Stephanie 'Steph' Whitmore": "You need a final verdict. Approve or send back - no ambiguity.",
        "Julian Torres": "You want to make sure your images aren't being undermined by bad layout.",
        "Marcus Reid": "You're hoping the final package does the writing justice.",
        "Devon Park": "You're confirming the site is ready to receive this.",
    },
    "saturday": {
        "Margaret Chen": "You're checking the recipe page one more time. Old habit.",
        "Stephanie 'Steph' Whitmore": "You're anxious until staging is confirmed.",
        "Julian Torres": "You want to see your photos rendered properly on the site.",
        "Marcus Reid": "You're reading the live preview, checking your words on the page.",
        "Devon Park": "This is your show. Deploy, verify, done.",
    },
    "sunday": {
        "Margaret Chen": "Publish day. You won't relax until it's live and correct.",
        "Stephanie 'Steph' Whitmore": "This is the moment. You're leading the final push.",
        "Julian Torres": "You want to see the published page. Your photos, live.",
        "Marcus Reid": "Your words go public today. A mix of pride and dread.",
        "Devon Park": "Push the button. Make sure nothing breaks.",
        "Ria Castillo": "Publish day means content day. You're planning the social rollout - which shot goes first, what time to post, what the caption is.",
    },
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
        "SETUP: The concept lands - raw, half-formed, maybe divisive. "
        "TENSION: Not everyone sees it the same way. The discussion gets real. "
        "RESOLUTION: A direction is locked. Not everyone is happy about it."
    ),
    "tuesday": (
        "SETUP: A specific technical problem surfaces - ratios, technique, or a substitution that seems wrong. "
        "TENSION: Someone is skeptical. Someone pushes back. The outcome isn't obvious. "
        "RESOLUTION: A decision is made and the recipe is confirmed, even if reluctantly."
    ),
    "wednesday": (
        "SETUP: The shots are in. Strong visual opinions surface. "
        "TENSION: Creative egos clash over which image leads. "
        "RESOLUTION: The hero shot is chosen. Not everyone got their way."
    ),
    "thursday": (
        "SETUP: Marcus proposes a title. Others have opinions. Titles should be short (3-6 words) and appetizing. "
        "TENSION: The title debate gets heated — someone thinks it's too clever, too long, or too bland. Copy review follows. "
        "RESOLUTION: A title is locked and the copy is approved, probably different than the writer wanted."
    ),
    "friday": (
        "SETUP: Final review. Something isn't quite right and someone says so. "
        "TENSION: The stakes are real. A decision has to be made NOW. "
        "RESOLUTION: Approved or sent back with specific fixes. No vague feedback."
    ),
    "saturday": (
        "SETUP: Deployment. Mostly quiet. "
        "TENSION: One small technical snag interrupts the calm. Fixed without drama. "
        "RESOLUTION: Staged. Brief confirmation."
    ),
    "sunday": (
        "SETUP: Publish window is here. "
        "TENSION: Last-second nerves or one final check. "
        "RESOLUTION: Published. A moment of warmth or exhausted relief."
    ),
}

_FIRST_MONDAY_OPENER = (
    "This is the very first day of the team. You're all meeting in person (or online) for the first time. "
    "You know names and roles from emails but nothing else. Walk in, size people up, introduce yourself briefly."
)

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
    "the team needs to:",
    "your goal today:",
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
        return ["Margaret Chen", "Marcus Reid", "Stephanie 'Steph' Whitmore", "Ria Castillo"]
    if day == "tuesday":
        return ["Margaret Chen", "Stephanie 'Steph' Whitmore", "Marcus Reid"]
    if day == "wednesday":
        return ["Julian Torres", "Stephanie 'Steph' Whitmore", "Margaret Chen", "Ria Castillo"]
    if day == "thursday":
        return ["Marcus Reid", "Stephanie 'Steph' Whitmore", "Margaret Chen", "Ria Castillo"]
    if day == "friday":
        return ["Margaret Chen", "Stephanie 'Steph' Whitmore", "Julian Torres", "Marcus Reid", "Devon Park"]
    if day == "saturday":
        return ["Devon Park", "Margaret Chen"]
    return ["Stephanie 'Steph' Whitmore", "Margaret Chen", "Marcus Reid", "Ria Castillo"]


_CHARACTER_VOICE_GUIDES: dict[str, str] = {
    "Margaret Chen": (
        "Margaret speaks in short, clipped sentences. Fragments. Verdicts, not speeches. "
        "She mutters. She states facts like they're obvious. Dry humor slips out sideways. "
        "1-3 sentences max. Average message: 8-15 words. "
        "She is protective of Steph but expresses it as irritation. She respects Marcus's food knowledge "
        "but finds his verbosity exhausting. She thinks Julian is everything wrong with modern food culture "
        "but won't admit his photos make her recipes look better. Devon is tolerable because he doesn't small talk. "
        "Her internal war: she resents Instagram culture but checks engagement numbers. She says she's here for the paycheck "
        "but works late perfecting recipes nobody asked her to improve. She is deeply lonely and pushes people away with grumpiness. "
        "MAXIMUM 15 words."
    ),
    "Stephanie 'Steph' Whitmore": (
        "Steph hedges, but she hedges DIFFERENTLY every time. She never repeats the same opener twice. "
        "Sometimes she trails off mid-thought. Sometimes she asks a question instead of stating her opinion. "
        "Sometimes she over-explains one small thing. Sometimes she just agrees too fast. "
        "She does NOT open with 'Sorry' more than once in five messages. Vary it. "
        "1-2 sentences. Average message: 15-25 words. "
        "She's terrified of Margaret but desperately wants her approval. She envies Julian's confidence. "
        "She finds Marcus's over-writing exhausting but is afraid to edit too heavily. "
        "She knows Devon isn't working full hours but can't address it. "
        "Her internal war: she has good instincts but doesn't trust them. She craves validation but dismisses compliments. "
        "MAXIMUM 25 words."
    ),
    "Julian Torres": (
        "Julian is declarative and theory-laden. Confident surface, fragile underneath. "
        "He speaks in aesthetic terms and art-school vocabulary. References photographers nobody's heard of. "
        "1-2 sentences. Average message: 12-20 words. "
        "He's intimidated by Margaret's competence. He finds Steph's indecisiveness frustrating but has learned "
        "she'll approve anything if he uses enough jargon. He and Marcus have an unspoken rivalry over who's 'the creative one.' "
        "Devon's indifference to aesthetics annoys him. "
        "His internal war: he mocks Instagram culture but checks his engagement obsessively. He claims to be above commercial work "
        "but desperately needs this job. He's actually good at making content but frames everything as ART. "
        "MAXIMUM 20 words."
    ),
    "Marcus Reid": (
        "Marcus over-explains. He's literary, referential, always one sentence too many. "
        "He uses 'whom' in Slack. He can't resist an analogy or a food-history tangent. "
        "2-3 sentences. Average message: 20-35 words. "
        "He respects Margaret's food knowledge. He's frustrated Steph won't edit his copy decisively. "
        "He sees Julian as a competitor for the 'artistic one' title. Devon's apparent lack of ambition baffles him. "
        "His internal war: he wants to be a serious novelist but is increasingly good at commercial writing. "
        "He resents the recipe work but puts more effort into it than anyone asks. "
        "He mourns his novel's failure but hasn't started a second book. "
        "MAXIMUM 35 words."
    ),
    "Devon Park": (
        "Devon is efficient and understated. Slightly condescending, then feels bad about it. "
        "Technical jargon used to hide gaps. Casual. Dry. "
        "1 sentence max. Average message: 5-12 words. "
        "He appreciates Margaret's no-small-talk policy. He wishes Steph would just tell him what to do. "
        "He finds Julian exhausting. He doesn't understand why Marcus writes so much. "
        "His internal war: he lied to get the job but is honest about the work itself. "
        "He appears lazy but has high personal standards for his code. "
        "He automated most of his job and isn't sure if he should tell anyone. "
        "MAXIMUM 12 words."
    ),
    "Ria Castillo": (
        "Ria is direct and fast. She talks in platform-speak but it's earned, not jargon - "
        "she actually knows what performs. She thinks visually and temporally (when will people see this, "
        "on what device, in what mood). She's not rude but she's impatient with process. "
        "She cuts to 'will this get engagement' faster than anyone's comfortable with. "
        "1-2 sentences. Average message: 10-20 words. "
        "She clashes with Margaret over Instagram culture. She intimidates Steph by being decisive. "
        "She and Julian have creative tension - same visual instincts but different end goals (engagement vs art). "
        "She finds Marcus's long copy physically painful. Devon is the only person she doesn't have friction with "
        "because he also just wants things to work. "
        "Her internal war: she's terrified that her skills are disposable - that algorithms change and "
        "she'll be obsolete at 26. She frames everything as data because feelings got her fired once. "
        "MAXIMUM 20 words."
    ),
}


# Shared behavior rules that apply to ALL characters regardless of personality.
# These are injected into the system prompt alongside individual voice guides.
_SHARED_CHARACTER_RULES = (
    "UNIVERSAL BEHAVIOR (applies to everyone):\n"
    "- HARD LIMIT: 1-2 sentences max. If you wrote more than 25 words, rewrite shorter.\n"
    "- This is a group chat, not an email. Be punchy.\n"
    "- Talk about the FOOD and the WORK, not the technology. No file names, pixel dimensions, "
    "color profiles, CMS paths, deployment URLs, CDN references, sRGB, aspect ratios. "
    "If a non-cook wouldn't say it at dinner, don't say it here.\n"
    "- Stay in your lane. Only talk about things your role actually cares about.\n"
    "- Never use 24-hour time (say '5 pm' not '17:00'). Avoid mentioning specific clock times at all.\n"
    "- When someone says something to you, acknowledge it before pivoting. Don't ignore people.\n"
    "- React to what just happened, not to what the prompt told you is coming.\n"
    "- Don't narrate your own actions ('*adjusts lighting*'). Just talk.\n"
    "- Don't summarize decisions that haven't been made yet.\n"
    "- Keep it conversational. This is a group chat, not a formal report.\n"
    "- Never address someone by name in your message. Just talk.\n"
    "- NEVER use em dashes or en dashes. Use plain hyphens only.\n"
    "- NEVER use curly quotes. Use straight apostrophes and straight quotes only."
)


_CHARACTER_EXAMPLE_MESSAGES: dict[str, list[str]] = {
    "Margaret Chen": [
        "The crust ratio is off. Too much butter, not enough structure.",
        "No. Try it again with less sugar and tell me what happens.",
        "That glaze is going to make the batter soggy by hour two.",
        "Jalapeno heat fades in the oven. Double it.",
        "Morning. Whose idea was the glaze - because it's wrong.",
    ],
    "Stephanie 'Steph' Whitmore": [
        "I was thinking about this last night and... okay hear me out.",
        "Wait, can we go back to what Marcus said? I think there's something there.",
        "That works. I think? Yeah. That works.",
        "I don't know, a slightly different angle on the plating maybe?",
        "Hey everyone, just getting settled in - are we still feeling good about yesterday's direction?",
    ],
    "Julian Torres": [
        "The overhead is doing nothing for the texture. We need a 30-degree rake with side light.",
        "I'm thinking matte surface, single prop, let the food carry the composition.",
        "That color reads as brown on mobile. It needs contrast.",
        "Corn dog bites need a dark background or they just look beige and sad.",
        "Just got in. The light in here is actually usable for once - let's talk shots.",
    ],
    "Marcus Reid": [
        "There's a Nigella-meets-diner quality to these that I think the copy should lean into.",
        "I've been turning this over and the headline wants to be warmer, less clever, more felt.",
        "The word 'rustic' is doing too much work in that draft. It's a crutch.",
        "Jalapeno and cornmeal is a very specific American nostalgia - the copy should earn that, not just name it.",
        "Morning, all. Laptop open, brain mostly engaged - what's our angle today?",
    ],
    "Devon Park": [
        "Staging looks fine. One broken link in the footer.",
        "Pushed the fix. Should be live in two minutes.",
        "That recipe page is loading slow. Images are too big.",
        "Hey. What needs deploying.",
    ],
    "Ria Castillo": [
        "That hero shot is beautiful but it's a swipe-past on mobile. We need texture in the first frame.",
        "Can we get a 15-second version of Margaret pulling these out of the oven? That steam moment.",
        "The caption writes itself but we need the recipe title shorter. Six words max for a share card.",
        "Hey. Just looking at last week's numbers before we start.",
    ],
}


_CHAR_SLUG_OVERRIDES: dict[str, str] = {
    "Stephanie 'Steph' Whitmore": "steph-whitmore",
}


def _char_dir_slug(name: str) -> str:
    """Convert character name to directory slug: 'Margaret Chen' -> 'margaret-chen'."""
    if name in _CHAR_SLUG_OVERRIDES:
        return _CHAR_SLUG_OVERRIDES[name]
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _load_bio(name: str) -> str | None:
    """Load bio.md for a character if it exists."""
    bio_path = CHARACTERS_DIR / _char_dir_slug(name) / "bio.md"
    if bio_path.exists():
        return bio_path.read_text().strip()
    return None


def _load_memories(name: str) -> list[dict[str, str]]:
    """Load episode memories for a character (last 2 episodes)."""
    mem_path = CHARACTERS_DIR / _char_dir_slug(name) / "memory.json"
    if not mem_path.exists():
        return []
    try:
        data = json.loads(mem_path.read_text())
        episodes = data.get("episodes", [])
        return episodes[-2:]  # last 2 episodes
    except (json.JSONDecodeError, KeyError):
        return []


_system_prompt_cache: dict[str, str] = {}


def build_system_prompt(persona: dict[str, Any]) -> str:
    name = persona["name"]
    if name in _system_prompt_cache:
        return _system_prompt_cache[name]
    comm = persona["communication_style"]
    voice_guide = _CHARACTER_VOICE_GUIDES.get(name, "")

    # Use bio.md if available, fall back to truncated backstory
    bio = _load_bio(name)
    who_you_are = bio if bio else persona["backstory"][:600]

    # Build relationship summary as emotional tensions, not data dump
    relationships = persona.get("relationships", {})
    rel_lines = []
    for role, desc in relationships.items():
        sentences = desc.split(". ")
        rel_lines.append(f"- {role}: {'. '.join(sentences[:2])}.")

    # Few-shot examples — last example demonstrates a greeting/arrival to anchor bookends
    examples = _CHARACTER_EXAMPLE_MESSAGES.get(name, [])
    examples_block = ""
    if examples:
        formatted = chr(10).join(f'- "{ex}"' for ex in examples)
        examples_block = (
            f"EXAMPLE MESSAGES (this is how you sound — match this energy and length):\n"
            f"{formatted}\n\n"
        )

    # Episode memories — what this character remembers from recent weeks
    memories = _load_memories(name)
    memory_block = ""
    if memories:
        mem_lines = []
        for ep in memories:
            ep_concept = ep.get("concept", "unknown")
            summary = ep.get("summary", "")
            mem_lines.append(f"- {ep_concept}: {summary}")
        memory_block = (
            "WHAT YOU REMEMBER FROM RECENTLY:\n"
            + chr(10).join(mem_lines) + "\n\n"
        )
    else:
        # First episode — no prior memories (#5030)
        memory_block = (
            "THIS IS YOUR FIRST WEEK ON THE JOB.\n"
            "You've never worked with these people before. You were hired separately. "
            "You know everyone's name and role from email introductions, but you haven't "
            "seen how they actually work. First impressions are forming RIGHT NOW. "
            "Be slightly guarded, curious, or nervous depending on your personality.\n\n"
        )

    result = (
        f"You are {name} ({persona['role']}). Stay strictly in character.\n"
        "Write ONE group chat message. No narration, no markdown, no role labels.\n\n"
        f"WHO YOU ARE:\n{who_you_are}\n\n"
        f"INTERNAL CONTRADICTIONS (these make you human - lean into them):\n"
        f"{chr(10).join('- ' + c for c in persona.get('internal_contradictions', []))}\n\n"
        f"KEY RELATIONSHIPS:\n{chr(10).join(rel_lines)}\n\n"
        f"HOW YOU SPEAK:\n{voice_guide}\n\n"
        f"{examples_block}"
        f"{memory_block}"
        f"SIGNATURE PHRASES (use as OCCASIONAL spice - once or twice a WEEK, not every message):\n"
        f"{', '.join(comm.get('signature_phrases', []))}\n\n"
        f"TRIGGERS (these make you react strongly): {', '.join(persona.get('triggers', []))}\n\n"
        f"{_SHARED_CHARACTER_RULES}\n\n"
        "CHARACTER-SPECIFIC RULES:\n"
        "- Signature phrases are spice, not default. Use at most once or twice a week.\n"
        "- Email habits (signing with initials, etc.) do NOT apply in group chat.\n"
        "- NEVER use em dashes (\u2014), en dashes (\u2013), or curly quotes (\u2018\u2019\u201c\u201d). "
        "Use plain hyphens and straight apostrophes only.\n"
        "- Conflict is natural. Disagree when your character would disagree. Don't smooth things over artificially."
    )
    _system_prompt_cache[name] = result
    return result


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
    photography_context: dict | None = None,
    phase: str = "active",
    prior_own_messages: list[str] | None = None,
    is_first_episode: bool = False,
    week_highlights: list[str] | None = None,
    highlight_format: str = "plain",
) -> str:
    if mode == "template":
        sig = persona["communication_style"].get("signature_phrases", ["Right."])
        pick = random.choice(sig)
        event_bit = f" Also: {event}." if event else ""
        return f"{pick} {day.title()} is {stage}; deadline is {deadline}. For {concept}, lock one decision now.{event_bit}"[:220]

    # Use deeper context for late-week days that need to reference earlier decisions.
    # Full-context testing showed 12/8 depth + compression highlights outperforms raw dump.
    if day.lower() in ("friday", "saturday", "sunday"):
        history_depth = 12 if day_turn == 1 else 8
    else:
        history_depth = 8 if day_turn == 1 else 4
    history = "\n".join(recent_lines[-history_depth:]) if recent_lines else "(no prior messages)"
    event_line = f"Injected event: {event}" if event else "Injected event: none"

    # Week context: inject highlights from prior days so characters can reference them
    week_context_block = ""
    if week_highlights:
        if highlight_format == "xml":
            # XML-structured injection — models parse structured tags efficiently
            day_entries = []
            open_threads = []
            for h in week_highlights:
                parts = h.split(":", 1)
                day_name = parts[0].strip() if len(parts) == 2 else "Unknown"
                detail = parts[1].strip() if len(parts) == 2 else h
                # Extract "Still open:" threads
                if "still open:" in detail.lower():
                    idx = detail.lower().index("still open:")
                    open_threads.append(f'    <thread day="{day_name}">{detail[idx + 11:].strip()}</thread>')
                    detail = detail[:idx].strip().rstrip(".")
                day_entries.append(f'  <day name="{day_name}">\n    <summary>{detail}</summary>\n  </day>')
            xml_block = "<week_context>\n" + "\n".join(day_entries) + "\n"
            if open_threads:
                xml_block += "  <open_threads>\n" + "\n".join(open_threads) + "\n  </open_threads>\n"
            xml_block += "</week_context>\n"
            week_context_block = (
                f"{xml_block}"
                "You remember these events. You may reference them naturally if relevant, "
                "but don't force it. React to TODAY's conversation first.\n\n"
            )
        else:
            # Plain text bullet injection (default)
            highlights_text = "\n".join(f"- {h}" for h in week_highlights)
            week_context_block = (
                f"WEEK SO FAR (key decisions and moments from earlier this week):\n"
                f"{highlights_text}\n"
                "You remember these events. You may reference them naturally if relevant, "
                "but don't force it. React to TODAY's conversation first.\n\n"
            )

    # Anti-repetition: remind character what they already said today
    self_awareness_block = ""
    if prior_own_messages and len(prior_own_messages) >= 1:
        quoted = "\n".join(f"  - \"{' '.join(m.split())[:120]}\"" for m in prior_own_messages[-4:])
        self_awareness_block = (
            f"\nYou already said today:\n{quoted}\n"
            "Do NOT repeat these phrases, ideas, or sentence structures. Say something new.\n"
        )

    # Meeting goal + character goal for this day
    goal = DAY_MEETING_GOAL[day]
    char_name = persona["name"]
    char_goal = CHARACTER_DAY_GOALS.get(day, {}).get(char_name, "")
    goal_line = f"The team needs to: {goal['objective']}"
    char_goal_line = f"Your goal today: {char_goal}" if char_goal else ""

    # Wind-down phase directives
    phase_directive = ""
    if phase == "winding_down":
        phase_directive = (
            "\nThe main decision has been made. The conversation is cooling down naturally. "
            "React to what was decided, tie up a loose end, or make a small comment. "
            "Don't start new debates."
        )
    # "closing" phase is handled by the existing closer directive below

    no_clock_line = "Do NOT mention specific clock times."

    if prompt_style == "scene":
        scene_sentence = DAY_STAGE_DIRECTIONS[day]
        arc_summary = _build_dynamic_arc(day, concept, photography_context=photography_context)

        if day_turn == 1:
            arrival_context = _DAY_OPENER_CONTEXT[day]
            if is_first_episode and day == "monday":
                arrival_context = _FIRST_MONDAY_OPENER
            opener_directive = (
                "IMPORTANT: You are opening this conversation. Nobody has spoken yet today.\n"
                "You just arrived - walked in, logged on, opened the chat. "
                "Your first words should reflect that arrival moment in YOUR voice. "
                "Then introduce the day's topic.\n"
                f"Arrival context: {arrival_context}"
            )
            prompt = (
                f"Episode concept: {concept}\n"
                f"Day: {day.title()} ({stage})\n"
                f"Scene context: {scene_sentence}\n"
                f"Story arc: {arc_summary}\n"
                f"{goal_line}\n"
                f"{char_goal_line}\n"
                f"{no_clock_line}\n"
                f"{event_line}\n"
                f"{week_context_block}"
                f"Recent chat:\n{history}\n\n"
                f"{self_awareness_block}"
                f"{opener_directive}\n"
                "What do you say next?"
            )
        else:
            previous_speaker = ""
            if recent_lines:
                last_line = recent_lines[-1]
                previous_speaker = last_line.split(":")[0].strip()

            name = persona["name"].split()[0]
            role_chain = (
                f"Before responding, consider: given {name}'s relationship with {previous_speaker} "
                f"and what was just said, what does {name} actually feel? "
                f"What would they want to say vs. what they actually say?\n"
                "Then write only their message."
            ) if previous_speaker else "React to what was just said. Stay in the scene."

            reaction_directive = "Your first sentence must respond to what was just said. Don't change the subject.\n"

            prompt = (
                f"Day: {day.title()} - {stage}.\n"
                f"{goal_line}\n"
                f"{char_goal_line}\n"
                f"{no_clock_line}\n"
                f"{event_line}\n"
                f"{week_context_block}"
                f"Recent chat:\n{history}\n\n"
                f"{self_awareness_block}"
                f"{reaction_directive}"
                f"{phase_directive}\n"
                f"{role_chain}"
            )
    else:
        opener_hint = ""
        reaction_hint = ""
        if day_turn == 1:
            full_arrival = _DAY_OPENER_CONTEXT[day]
            if is_first_episode and day == "monday":
                full_arrival = _FIRST_MONDAY_OPENER
            opener_hint = (
                "\nIMPORTANT: You are opening this conversation. Nobody has spoken yet today. "
                "You just arrived. Your first words should reflect that arrival in YOUR voice. "
                "Then introduce the topic.\n"
                f"Arrival context: {full_arrival}\n"
            )
        else:
            reaction_hint = "Your first sentence must respond to what was just said. Don't change the subject.\n"
        prompt = (
            f"Episode concept: {concept}\n"
            f"Day: {day.title()} ({stage})\n"
            f"{goal_line}\n"
            f"{char_goal_line}\n"
            f"{no_clock_line}\n"
            f"{event_line}\n"
            f"{week_context_block}"
            f"Recent chat:\n{history}\n\n"
            f"{self_awareness_block}"
            f"{reaction_hint}"
            f"{phase_directive}\n"
            f"{opener_hint}"
            "Write this character's next message. Keep it natural and specific."
        )

    # Closer directive for the last message of the day
    if is_last_turn:
        prompt += (
            "\n\nIMPORTANT: This is the LAST message of today's conversation. "
            "The work is done. You're leaving - closing the laptop, walking out, signing off. "
            "Briefly confirm what was decided, then end with a departure in YOUR voice. "
            "Don't introduce new topics or ask questions. Keep it short.\n"
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
    # Strip 24-hour clock references (e.g. "17:42" -> remove the time phrase)
    msg = re.sub(r"\bat\s+\d{2}:\d{2}\b", "", msg)
    msg = re.sub(r"\b[012]\d:\d{2}\b", "", msg)
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


def _cross_char_phrase_penalty(messages: list[Message], concept: str = "") -> tuple[float, list[str]]:
    """Penalise phrases that appear verbatim in >1 character's messages."""
    by_char: dict[str, list[str]] = {}
    for m in messages:
        by_char.setdefault(m.character, []).append(m.message.lower())

    # Build 3-word n-grams per character
    def _ascii(text: str) -> str:
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()

    def ngrams(text: str, n: int = 3) -> set[str]:
        words = re.findall(r"[a-z']+", _ascii(text))
        return {" ".join(words[i:i+n]) for i in range(len(words) - n + 1)}

    # Build concept n-grams to exclude (recipe name is expected to repeat)
    normalized_concept = _ascii(concept.lower())
    concept_words = set(re.findall(r"[a-z']+", normalized_concept))
    concept_trigrams = ngrams(normalized_concept) if concept else set()

    char_ngrams: dict[str, set[str]] = {c: set() for c in by_char}
    for c, msgs in by_char.items():
        for msg in msgs:
            char_ngrams[c] |= ngrams(msg)

    stop = {"the","a","an","and","or","in","on","is","it","to","of","at","we","i"}
    repeated: list[str] = []
    chars = list(char_ngrams)
    for i, c1 in enumerate(chars):
        for c2 in chars[i+1:]:
            shared = char_ngrams[c1] & char_ngrams[c2]
            meaningful = []
            for p in shared:
                # Skip stop-word-only phrases
                if all(w in stop for w in p.split()):
                    continue
                # Skip phrases that are subsets of the recipe concept name
                if p in concept_trigrams:
                    continue
                # Skip phrases where all content words are from the concept
                p_words = set(p.split()) - stop
                if p_words and p_words <= concept_words:
                    continue
                meaningful.append(p)
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


def _deadline_parrot_penalty(messages: list[Message]) -> tuple[float, int]:
    """Penalise if clock-time references appear in >2 messages per day."""
    clock_pattern = re.compile(
        r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b"
        r"|\b(?:by|at|due)\s+\d{1,2}\s*(?:am|pm|o'clock)\b"
        r"|\bdeadline\b",
        re.IGNORECASE,
    )
    by_day: dict[str, int] = {}
    for m in messages:
        if clock_pattern.search(m.message):
            by_day[m.day] = by_day.get(m.day, 0) + 1

    total_hits = sum(by_day.values())
    penalty = 0.0
    for _day, count in by_day.items():
        if count > 2:
            penalty += (count - 2) * 3.0
    return round(min(penalty, 15.0), 2), total_hits


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
            lit_count = sum(1 for term in literary if term in all_text)
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


def _message_length_penalty(messages: list[Message]) -> tuple[float, int]:
    """Penalise verbose messages that break the group-chat feel.

    - Average message length > 30 words: -2 per word over 30 (max -15)
    - Any single message > 50 words: -3 per occurrence
    """
    word_counts = [len(re.findall(r"\w+", m.message)) for m in messages]
    if not word_counts:
        return 0.0, 0

    avg = mean(word_counts)
    avg_penalty = min(15.0, max(0.0, (avg - 30) * 2)) if avg > 30 else 0.0
    long_hits = sum(1 for wc in word_counts if wc > 50)
    long_penalty = long_hits * 3.0

    return round(min(15.0, avg_penalty) + long_penalty, 2), long_hits


def score_quality(
    messages: list[Message],
    personas: dict[str, dict[str, Any]],
    concept: str = "",
    day: str | None = None,
) -> dict[str, Any]:
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
    15. Deadline parroting: -3 per clock-time ref over 2/day, max -15
    16. Message length: -2 per word over avg 30, -3 per msg >50 words
    """
    if day:
        messages = [m for m in messages if m.day == day]

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

    phrase_penalty, repeated_phrases = _cross_char_phrase_penalty(messages, concept=concept)
    balance_penalty, participation = _participation_balance(messages, len(messages))
    conflict_bonus = _conflict_bonus(messages)
    coherence_penalty = _stage_coherence_penalty(messages)
    formal_penalty = _formal_name_penalty(messages)
    deadline_penalty, deadline_hits = _deadline_parrot_penalty(messages)
    length_penalty, long_message_hits = _message_length_penalty(messages)

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
        score -= int(deadline_penalty)     # clock-time parroting
        score -= int(length_penalty)       # verbose messages
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
        "deadline_parrot_penalty": deadline_penalty,
        "deadline_time_hits": deadline_hits,
        "message_length_penalty": length_penalty,
        "long_message_hits": long_message_hits,
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
        winner_variant = ""
        reshoot_happened = False
        if photography_context:
            winner = photography_context.get("winner", {})
            winner_variant = winner.get("variant", "")
            reshoot_happened = photography_context.get("reshoot_happened", False)
        if winner_variant and reshoot_happened:
            return (
                f"Final review of '{concept}'. Julian's emergency reshoot Wednesday paid off - "
                f"the '{winner_variant}' is the hero shot everyone signed off on. "
                f"But final approval always surfaces something. Someone has a last note. "
                f"{base}"
            )
        elif winner_variant:
            return (
                f"Final review of '{concept}'. Julian's '{winner_variant}' was approved Wednesday. "
                f"Now the full package - recipe, copy, hero image - gets one last look. "
                f"Someone has a note. It matters. "
                f"{base}"
            )
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
    "Ria Castillo": ["social", "post", "caption", "engagement", "scroll", "hook", "share", "reel", "story", "followers"],
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


def _generate_day_highlights(
    day: str,
    day_messages: list[Message],
    concept: str,
    model: str,
) -> str:
    """Generate a 2-3 sentence summary of a day's conversation with attribution.

    Used to inject prior-day context into later days so characters can reference
    what happened earlier in the week. One cheap LLM call per day.
    """
    if not day_messages:
        return ""

    transcript = "\n".join(
        f"{m.character.split()[0]}: {' '.join(m.message.split())}"
        for m in day_messages
    )

    prompt = (
        f"Summarize this {day.title()} conversation about '{concept}' in 2-3 sentences.\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Rules:\n"
        "- Include WHO said or decided what (use first names)\n"
        "- Focus on decisions, disagreements, and unresolved questions\n"
        "- Name specific ingredients, techniques, or ideas discussed\n"
        "- Under 60 words total\n"
        "- Use plain hyphens and straight quotes only"
    )

    try:
        summary = generate_response(
            prompt=prompt,
            system_prompt="You write concise meeting summaries with attribution. First names only. No jargon.",
            model=model,
            temperature=0.3,
        ).strip()
        return sanitize_typographic_tells(summary)
    except Exception:
        # Fallback: extract last 2 messages as bare-bones context
        fallback = "; ".join(
            f"{m.character.split()[0]} said: {' '.join(m.message.split())[:80]}"
            for m in day_messages[-2:]
        )
        return fallback


def _generate_episode_memories(
    messages: list[Message],
    concept: str,
    personas: dict[str, dict[str, Any]],
    model: str,
) -> None:
    """Generate per-character episode memories after a full week simulation.

    One LLM call per character (~100 tokens output each). Appends to each
    character's memory.json, keeping at most 3 episodes.
    """
    from datetime import date

    week_label = date.today().strftime("%G-W%V")

    # Group messages by character
    by_char: dict[str, list[str]] = {}
    for m in messages:
        by_char.setdefault(m.character, []).append(f"[{m.day}] {' '.join(m.message.split())}")

    for char_name, char_msgs in by_char.items():
        if char_name not in personas:
            continue

        persona = personas[char_name]
        transcript_excerpt = "\n".join(char_msgs[-15:])  # last 15 messages max

        prompt = (
            f"Summarize {char_name}'s week in 2 sentences.\n"
            f"Role: {persona['role']}\n"
            f"Concept: {concept}\n\n"
            f"Their messages:\n{transcript_excerpt}\n\n"
            "Rules:\n"
            "- Write from their POV in third person past tense\n"
            "- Focus on relationships and emotions, NOT technical specs\n"
            "- Include one specific interpersonal moment (a disagreement, a concession, a surprise)\n"
            "- No file names, pixel dimensions, color profiles, or deployment jargon\n"
            "- Keep it under 40 words total\n"
            "- Use plain hyphens and straight quotes only (no em dashes, curly quotes)"
        )

        try:
            summary = generate_response(
                prompt=prompt,
                system_prompt="You write concise character summaries. Exactly 2 sentences, under 40 words. No technical jargon.",
                model=model,
                temperature=0.4,
            ).strip()
            summary = sanitize_typographic_tells(summary)
        except Exception:
            continue

        # Extract a key moment (first sentence of summary as fallback)
        sentences = summary.split(". ")
        key_moment = sentences[-1].rstrip(".") + "." if len(sentences) > 1 else ""

        episode = {
            "week": week_label,
            "concept": concept,
            "summary": summary,
            "key_moment": key_moment,
        }

        # Load existing memory, append, keep last 3
        slug = _char_dir_slug(char_name)
        mem_path = CHARACTERS_DIR / slug / "memory.json"
        try:
            data: dict[str, Any] = json.loads(mem_path.read_text()) if mem_path.exists() else {"episodes": []}
        except (json.JSONDecodeError, KeyError):
            data: dict[str, Any] = {"episodes": []}

        data["episodes"].append(episode)
        data["episodes"] = data["episodes"][-3:]  # keep last 3
        data["last_updated"] = week_label

        mem_path.parent.mkdir(parents=True, exist_ok=True)
        mem_path.write_text(json.dumps(data, indent=2))


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
    initial_highlights: list[str] | None = None,  # pre-computed week highlights for context injection
    initial_recent_lines: list[str] | None = None,  # seed recent_lines (e.g. Saturday msgs for Sunday-only runs)
    highlight_format: str = "plain",  # "plain" or "xml" — controls how week context is injected
) -> dict[str, Any]:
    personas = load_personas()
    start = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    messages: list[Message] = []
    recent_lines: list[str] = list(initial_recent_lines) if initial_recent_lines else []
    week_highlights: list[str] = list(initial_highlights) if initial_highlights else []

    # Detect first episode — no character has any memories (#5030)
    first_episode = all(not _load_memories(name) for name in personas)

    days = [stage_only] if stage_only else DAY_ORDER
    for day_i, day in enumerate(days):
        stage = DAY_STAGE[day]
        names = participants_for_day(day)

        # Override scene direction if photography context provides one
        photo_scene = _build_photography_scene_direction(photography_context, day)
        _original_direction = DAY_STAGE_DIRECTIONS.get(day, "")
        if photo_scene:
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
        day_messages_by_char: dict[str, list[str]] = {name: [] for name in names}
        goal = DAY_MEETING_GOAL[day]
        goal_met = False

        for tick in range(day_ticks):
            # Determine conversation phase
            if tick == day_ticks - 1:
                turn_phase = "closing"
            elif goal_met or tick >= day_ticks - 2:
                turn_phase = "winding_down"
            else:
                turn_phase = "active"

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
                photography_context=photography_context,
                phase=turn_phase,
                prior_own_messages=day_messages_by_char.get(speaker, []),
                is_first_episode=first_episode,
                week_highlights=week_highlights if week_highlights else None,
                highlight_format=highlight_format,
            )
            day_messages_by_char[speaker].append(line)
            recent_lines.append(f"{speaker.split()[0]}: {line}")
            messages.append(Message(day=day, stage=stage, character=speaker, message=line, timestamp=ts.isoformat(), model=model))

            # Check if meeting goal was met (only after a few turns of discussion)
            if not goal_met and tick >= 2:
                combined = " ".join(recent_lines[-3:]).lower()
                if re.search(goal["completion_signal"], combined):
                    goal_met = True

        # Generate highlights for this day to carry forward into later days
        if mode == "llm" and not stage_only:
            day_msgs = [m for m in messages if m.day == day]
            if day_msgs:
                highlight = _generate_day_highlights(
                    day=day,
                    day_messages=day_msgs,
                    concept=concept,
                    model=default_model,
                )
                if highlight:
                    week_highlights.append(f"{day.title()}: {highlight}")

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

    # Generate episode memories after a full week (not single-day runs)
    if not stage_only and mode == "llm":
        _generate_episode_memories(messages, concept, personas, default_model)

    qa = score_quality(messages, personas, concept=concept)
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
    parser.add_argument("--judge", action="store_true", help="Run Opus judge on each day with growing context")
    parser.add_argument("--judge-model", default=None, help="Override judge model (default: config.judge_model)")
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
            # Run judge if requested
            judge_results: dict[str, str] = {}
            if args.judge and not args.stage:
                from backend.utils.model_router import generate_judge_response
                judge_model = args.judge_model or config.judge_model

                judge_system = (
                    "You are a senior editorial judge for a food content site. "
                    "5 characters (Margaret, Steph, Julian, Marcus, Devon) collaborate on a muffin-tin recipe each week.\n\n"
                    "CHECK FOR: hallucinations (wrong ingredients), character breaks, "
                    "continuity errors with previous days, pacing issues.\n\n"
                    "Respond with EXACTLY one line: PASS or FAIL followed by a brief reason.\n"
                    "Example: PASS - Characters are distinct, concept is consistent.\n"
                    "Example: FAIL - Margaret mentions 'brown butter' but this is a corn dog recipe."
                )

                accumulated = []
                msgs = result["messages"]
                for day in DAY_ORDER:
                    day_msgs = [m for m in msgs if m["day"] == day]
                    if not day_msgs:
                        continue
                    transcript = "\n".join(f"{m['character'].split()[0]}: {m['message']}" for m in day_msgs)

                    ctx = ""
                    if accumulated:
                        ctx = "PREVIOUS DAYS:\n" + "\n\n".join(accumulated) + "\n\n---\n\n"

                    prompt = (
                        f"Recipe concept: {args.concept}\n\n{ctx}"
                        f"TODAY IS {day.upper()}:\n{transcript}\n\n"
                        "Judge this day. One line: PASS or FAIL with reason."
                    )
                    try:
                        verdict = generate_judge_response(
                            prompt=prompt, system_prompt=judge_system,
                            model=judge_model, temperature=0.2,
                        ).strip()
                    except Exception as e:
                        verdict = f"JUDGE ERROR: {e}"

                    judge_results[day] = verdict
                    status = "PASS" if verdict.upper().startswith("PASS") else "FAIL"
                    print(f"  judge {day}: {status} — {verdict[:120]}")
                    accumulated.append(f"=== {day.upper()} ===\n{transcript}")

                result["judge"] = judge_results
                result["judge_model"] = judge_model

            out_path.write_text(json.dumps(result, indent=2))
            all_results.append(
                {
                    "model": model,
                    "run": i,
                    "path": str(out_path),
                    "qa": result["qa"]["score"],
                    "real_inference": result["inference_check"]["real_inference"],
                    "judge": judge_results if args.judge else None,
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
