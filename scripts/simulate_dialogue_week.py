#!/usr/bin/env python3
"""Run compressed one-week dialogue simulations with model comparisons.

Examples:
  PYTHONPATH=. uv run scripts/simulate_dialogue_week.py \
    --concept "Jalapeño Corn Dog Bites" --runs 3 --models "openai/gpt-5-mini,anthropic/claude-haiku-4-5-20251001"

  PYTHONPATH=. uv run scripts/simulate_dialogue_week.py \
    --concept "Mini Shepherd's Pies" --stage friday --event "ingredient shortage: cheddar"
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
from backend.storage import CharacterMemoryUnavailable, storage
from backend.utils.logging import get_logger
from backend.utils.model_router import generate_response

logger = get_logger(__name__)

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
#
# INVARIANT (#7079, tightened by #7082/#7105, again by #7292): the low end of
# every range must be STRICTLY GREATER than the number of people
# participants_for_day() returns for that day, AND never below
# ABSOLUTE_TURN_FLOOR.
#
# At or below the cast size the day cannot seat its own cast, and the judge's
# cast_coverage dimension fails the stage. At exactly the cast size it seats
# everyone and still fails differently: every character spends their only line
# before anyone can wrap up, so nobody answers anybody and the scene ends
# mid-thought with no sign-off. Measured at 100% over 5000 seeds, and live -
# W37's 4-turn Sunday scored natural_progression 2 and blocked the publish
# while scoring cast_coverage 5.
#
# One spare turn is what buys a reply and a closer. `test_ticks_range_
# seats_full_cast` enforces it - if you shrink a range or add a character to a
# roster, that test is what stops you.
#
# #7292: cast-size+1 is necessary and NOT sufficient. Saturday has a 2-person
# cast, so the old rule was satisfied by a floor of 3 - and W38's Saturday drew
# 3, 3 and 4 turns, failed the judge all three times ("only three lines total
# with almost no substantive content", natural_progression 2) and lost the
# stage. The judge grades an arc, which is an absolute standard: a scene needs
# a problem raised and resolved no matter how few people are in the room. So
# the floor is now max(cast + 1, ABSOLUTE_TURN_FLOOR). W37, the only earlier
# week the structured judge ever scored, passed Saturday at 4 turns with
# natural_progression, arc_resolution and turn_taking all 3 - no margin either.

# The fewest turns any day may budget, whatever its cast size. Five is where
# the other five-and-six-turn days already sit; it is a floor under the
# cast-relative rule above, not a replacement for it.
ABSOLUTE_TURN_FLOOR = 5


TICKS_RANGE: dict[str, tuple[int, int]] = {
    "monday":    (6, 10),  # heated concept debate
    "tuesday":   (8, 10),  # was (5, 6): two lines wind down, too few to argue a ratio (#8285)
    # #8293: replaying W37-W42 (18 first attempts per day) at (8, 10) raised the
    # judge pass rate on every day with three or more speakers: Wed 4->6,
    # Thu 8->14, Fri 1->4, Sun 3->6. Saturday's two speakers stayed at 9/18.
    "wednesday": (8, 10),  # photography + image refs - was (5, 6) (#7105)
    "thursday":  (8, 10),  # copywriting - was (3, 5), (4, 6), (5, 7), (5, 6) (#7079, #7105)
    "friday":    (8, 10),  # approval discussion - was (6, 8) (#7105)
    "saturday":  (5, 6),   # was (3, 5) - a 2-person cast still needs an arc (#7292)
    "sunday":    (8, 10),  # publish + warmth - was (3, 4), (4, 6), (5, 6) (#7079, #7082)
}


_AUTHOR_PARSE_TRIES = 3


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
        # Julian joined Monday after W36 discussed photography without the photographer present.
        return ["Margaret Chen", "Marcus Reid", "Stephanie 'Steph' Whitmore", "Julian Torres", "Ria Castillo"]
    if day == "tuesday":
        # Devon was added in #6832 and taken off again in #8285. His Tuesday goal
        # was "Not your day", so he spent a line agreeing, and the judge named
        # him in 11 of 19 failed Tuesdays. Replaying the W37-W42 Tuesdays,
        # this cast with 8-10 turns and the amounts and method passed 12 of 24
        # first attempts. Production with Devon passed 5 of 24.
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
    "- Your word budget is the MAXIMUM stated in HOW YOU SPEAK above, and it is different "
    "for every character. Obey YOUR number, not a number you infer from how others talk. "
    "Going shorter than your budget is always fine; going over it never is.\n"
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
    "- NEVER use curly quotes. Use straight apostrophes and straight quotes only.\n"
    "\n"
    "THE MUFFIN PAN IS THE POINT (work this in naturally across the week - NOT every message):\n"
    "- This is Muffin Pan Recipes. Part of the job is making the honest case for WHY this "
    "dish belongs in a muffin pan. It is a real editorial question worth asking out loud.\n"
    "- Make the case with a property specific to THIS dish, not a generic list (#6832 - the "
    "generic menu used to sit right above this rule and read as license to fall back on it). "
    "Name the mechanism: what the pan's walls, depth, or heat do to THIS batter, dough, or "
    "filling. Only once that mechanism is on the table can a practical upside it produces "
    "follow - even portions, grab-and-go, holds together in your hand, make-ahead, portion "
    "control, easy cleanup - but the upside has to follow FROM the mechanism, never stand in "
    "for it.\n"
    "- Never state the pan-case as a bare conclusion. It has to follow from something a "
    "character just observed, so it reads as a person reasoning rather than a slogan.\n"
    "- Do not open a conversation with the pan-case. The first message of a stage has "
    "nothing to refer back to, so a 'that is exactly why...' construction there is broken.\n"
    "- Vary it. Do not all say the same thing. It is not just for show - it is for ease of use.\n"
    "\n"
    "PUSH BACK (#7159 - measured winners from the 2026-03 prompt research):\n"
    "- If someone just said something wrong about food or the work, push back. Don't let it slide.\n"
    "- Your message should sound like YOU and nobody else. Voice first, content second.\n"
    "- React to the specific food being discussed. Name the ingredient or technique. Don't be generic."
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


def _bio_path(name: str) -> Path:
    return CHARACTERS_DIR / _char_dir_slug(name) / "bio.md"


def _load_bio(name: str) -> str:
    """Load a character's bio.md; every cast member has one (#8146).

    A missing or empty bio raises. This used to return None and the prompt
    quietly used backstory[:600] instead, which is how production ran without
    any bio until 2026-10-08 (.vercelignore dropped them, #8113) while the lab
    used the full ones. A raise here fails the day's dialogue, and the stage
    alerts. `missing_bios` lets /health and health_check.py report it first.
    """
    bio = _bio_path(name).read_text().strip()
    if not bio:
        raise ValueError(f"bio for {name!r} is empty: {_bio_path(name)}")
    return bio


def missing_bios() -> list[str]:
    """Cast members whose bio.md is missing or empty in this deployment."""
    return [
        name for name in load_personas()
        if not (_bio_path(name).is_file() and _bio_path(name).read_text().strip())
    ]


def _load_legacy_memory_entries(slug: str) -> list[dict]:
    """Read-only DISPLAY fallback: the legacy bundled memory.json (#6968,
    redesigned in review round 3).

    Used ONLY when durable storage has zero weeks for this character — see
    _load_memories_or_unavailable. Never written to and never merged into
    durable storage. Its entries are returned exactly as stored — NOT
    deduped, NOT validated, NOT sorted by week — because round-3 review
    finding 2 established that each legacy seed's 3 identically-labelled
    "week": "2026-W11" entries are 3 DIFFERENT recipes the old, pre-#6968
    writer mislabelled with the same week key (e.g. margaret-chen's are
    Jalapeno Corn Dog Bites, Mini Shepherd's Pies, and Brown Butter Pecan
    Tassies). Deduping them, as an earlier round of this fix did, silently
    destroyed 2 of 3 real, distinct histories. Their week labels are known
    to be unreliable and are never parsed/sorted here — this returns the
    last 2 entries in file order, matching the file's own intended
    recency ordering.
    """
    legacy_path = CHARACTERS_DIR / slug / "memory.json"
    if not legacy_path.exists():
        return []
    # An existing but unreadable seed raises (json.JSONDecodeError/OSError):
    # returning [] here made a read failure look like "no history", which
    # can produce the false first-meeting opener. The caller maps it to
    # unavailable.
    data = json.loads(legacy_path.read_text())
    episodes = data.get("episodes") if isinstance(data, dict) else None
    if not isinstance(episodes, list):
        return []
    return [e for e in episodes if isinstance(e, dict)][-2:]


def _load_memories_or_unavailable(name: str) -> tuple[list[dict[str, str]], bool]:
    """Load a character's memories for the prompt, plus whether a needed
    durable read was unavailable (#6968, redesigned in review round 3).

    Returns ``(episodes, unavailable)``. ``unavailable`` is True ONLY when
    a durable-store LIST or a needed week's FETCH failed, or the legacy
    seed it falls back to exists but is unreadable — never when the
    character genuinely has no memory yet. A caller deciding whether this
    is truly the cast's first-ever episode (run_simulation's
    `first_episode`) must treat "unavailable" as "unknown", not as
    "confirmed no history": a read outage must never produce the false
    "meeting for the first time" opener (_FIRST_MONDAY_OPENER).

    Flow: list this character's durable weeks — authoritative, via the
    Blob LIST API, never a cached blob body (round-3 review finding 1). An
    empty list is a genuine not-found in durable storage and falls back to
    the read-only legacy seed display (_load_legacy_memory_entries — never
    written, never deduped). A non-empty list fetches only the most recent
    weeks' bodies (storage.load_character_memory_week, which validates
    each body's schema); a fetch or schema failure on any needed week
    makes the whole read unavailable (fail closed) rather than silently
    showing a partial or malformed picture (round-3 review finding 3).
    """
    slug = _char_dir_slug(name)
    try:
        weeks = storage.list_character_memory_weeks(slug)
    except CharacterMemoryUnavailable as e:
        logger.warning(f"Character memory listing failed for {name}, using fallback: {type(e).__name__}: {e}")
        return [], True

    if not weeks:
        # Genuine not-found in durable storage (#6968 review finding 2).
        try:
            return _load_legacy_memory_entries(slug), False
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Legacy memory seed unreadable for {name}, using fallback: {type(e).__name__}: {e}")
            return [], True

    latest_weeks = weeks[-PROMPT_MEMORY_WEEKS:]  # weeks is ascending; retention is a read-time choice
    episodes: list[dict] = []
    try:
        for week in latest_weeks:
            episodes.append(storage.load_character_memory_week(slug, week))
    except CharacterMemoryUnavailable as e:
        logger.warning(f"Character memory fetch failed for {name}, using fallback: {type(e).__name__}: {e}")
        return [], True
    return episodes, False


def _load_memories(name: str) -> list[dict[str, str]]:
    """Load episode memories for a character for the prompt (#6968).

    A durable-store read failure degrades to "no memories" (the
    empty-memory fallback in the whole-scene author prompt) rather than
    raising. See _load_memories_or_unavailable for the full list/fetch/
    legacy-fallback flow and for callers that need to distinguish this
    from a genuinely empty record.
    """
    episodes, _unavailable = _load_memories_or_unavailable(name)
    return episodes


# Deterministic patterns for removing obsolete per-line length limits. Each entry is a regex that
# matches one numeric length constraint and removes it. The list is explicit
# and covered by tests (tests/test_author_mode.py, test_author_prompt_keeps_persona_markers_and_drops_numeric_caps): a new phrasing in the voice
# guides or shared rules must be added here deliberately, not discovered by
# drifting past a catch-all.
WORD_CAP_PATTERNS: tuple[str, ...] = (
    # Sentence-count limits, with or without "max": "1-3 sentences max.",
    # "1-2 sentences.", "1 sentence max."
    r"\d+(?:\s*-\s*\d+)?\s+sentences?\s+max\.?",
    r"\d+(?:\s*-\s*\d+)?\s+sentences?\.?",
    # Average-word ranges: "Average message: 8-15 words."
    r"Average message:\s*\d+(?:\s*-\s*\d+)?\s+words\.?",
    # Hard per-character caps: "MAXIMUM 15 words."
    r"MAXIMUM\s+\d+\s+words\.?",
    # The shared word-budget bullet.
    r"- Your word budget is the MAXIMUM stated in HOW YOU SPEAK above, and it "
    r"is different for every character\. Obey YOUR number, not a number you "
    r"infer from how others talk\. Going shorter than your budget is always "
    r"fine; going over it never is\.\n?",
    # "Be punchy." — a style order whose only job is to police length.
    r"Be punchy\.",
)


def _strip_word_caps(text: str) -> str:
    """Remove every numeric length constraint from `text`, leaving the rest.

    Used on _CHARACTER_VOICE_GUIDES and _SHARED_CHARACTER_RULES when
    the obsolete per-speaker prompt needs its numeric limits removed. Deterministic: same input always maps to the same
    output, and every removed phrase is one of WORD_CAP_PATTERNS above.
    """
    for pattern in WORD_CAP_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    # Collapse the holes the removals leave behind.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()



# How many of a character's most recent durable weeks the prompt shows
# (#6968, redesigned round 3). Retention itself is unbounded in durable
# storage — nothing is ever deleted — so this is purely a read-time display
# choice, not a storage limit.
PROMPT_MEMORY_WEEKS: int = 2



# Prompt-echo scoring helpers.
def _word_shingles(text: str, n: int = 6) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z']+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


# #6840 - is_prompt_echo only knew about scaffolding LABELS (PROMPT_ECHO_PATTERNS
# below), not the rules' actual CONTENT. W36's Monday opener recited a
# _SHARED_CHARACTER_RULES example sentence verbatim and scored prompt_echo_hits: 0.
# Precomputed once at import time - _SHARED_CHARACTER_RULES is fixed at runtime.
_SHARED_RULES_SHINGLES = _word_shingles(_SHARED_CHARACTER_RULES)


def is_prompt_echo(text: str) -> bool:
    """True if a message recites prompt scaffolding or the shared rules verbatim.

    Two independent detectors:
    1. Static PROMPT_ECHO_PATTERNS - literal scaffolding labels ('day:',
       'scene goal:', etc.) that should never appear in spoken dialogue.
    2. Word-shingle overlap against _SHARED_CHARACTER_RULES (#6840) - any
       run of 6+ consecutive words shared between the message and the rules
       block, regardless of which sentence it came from or whether it
       matches a hardcoded pattern.
    """
    t = text.lower()
    if any(p in t for p in PROMPT_ECHO_PATTERNS):
        return True
    return bool(_word_shingles(t) & _SHARED_RULES_SHINGLES)


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
        # With fewer Julian messages than images, the last one carries the rest.
        for i, img in enumerate(image_paths[:3]):
            julian_msgs[min(i, len(julian_msgs) - 1)].attachments.append(img)

    else:  # two_one
        # First 2 together ("here's what I've got so far"), third after debate
        julian_msgs[0].attachments = image_paths[:2]
        if len(julian_msgs) > 1:
            julian_msgs[-1].attachments = image_paths[2:3]


def _photo_review_line(photography_context: dict | None) -> str:
    """One factual sentence about the human photo review, or '' (#7936).

    Present only when the cron passes ``human_review``; historical and lab
    runs never carry it, so their prompts are unchanged.
    """
    review = (photography_context or {}).get("human_review")
    if not isinstance(review, dict):
        return ""
    if review.get("status") == "approved" and review.get("selected_variant"):
        return f"The site editor picked the '{review['selected_variant']}' shot as the hero."
    if review.get("status") == "rejected":
        return "The site editor rejected every Wednesday photo; there is no usable hero yet."
    if review.get("status") == "unknown":
        return "The site editor's hero photo pick is not confirmed yet."
    return "The hero photo is still waiting on the site editor's pick."


def _with_review_line(text: str, photography_context: dict | None) -> str:
    """Append the review sentence when the cron supplied one; else unchanged."""
    line = _photo_review_line(photography_context)
    return f"{text} {line}" if line else text


def _hero_landing(winner_variant: str, photography_context: dict | None) -> str:
    """Wednesday's convergence. With a human review it is only a recommendation."""
    if isinstance((photography_context or {}).get("human_review"), dict):
        return (
            f"they land on '{winner_variant}' as their recommendation; "
            "the site editor makes the final pick"
        )
    return f"they land on '{winner_variant}'"


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
                f"Steph thinks about feed performance. After real debate, "
                f"{_hero_landing(winner_variant, photography_context)}. "
                f"{base}"
            )
        return (
            f"Julian has three distinct shots of '{concept}'. Each character has a different favorite. "
            f"Margaret wants food shown honestly. Julian wants the artistic angle. "
            f"Steph thinks about what performs in feed. They disagree before converging. "
            f"{base}"
        )
    elif day == "thursday":
        return _with_review_line((
            f"Marcus shares copy for '{concept}'. It's too long, too literary, or too something. "
            f"Margaret edits it harshly. Steph tries to mediate. "
            f"{base}"
        ), photography_context)
    elif day == "friday":
        review_line = _photo_review_line(photography_context)
        if review_line:
            return (
                f"Final review of '{concept}'. {review_line} "
                f"Now the full package - recipe, copy, hero image - gets one last look. "
                f"Someone has a note. It matters. "
                f"{base}"
            )
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
        return _with_review_line((
            f"Devon is staging '{concept}' for deployment. It's mostly quiet. "
            f"{base}"
        ), photography_context)
    elif day == "sunday":
        return _with_review_line((
            f"Publish window for '{concept}'. The week's work comes to a point. "
            f"{base}"
        ), photography_context)
    return base


def _scene_hero_landing(winner_variant: str, photography_context: dict | None) -> str:
    """Wednesday scene close. With a human review it is only a recommendation.

    Without a winner variant (a remade set nobody evaluated, #8073) nothing is
    recommended: the human-review line if there is one, else no sentence.
    """
    if not winner_variant:
        return _photo_review_line(photography_context)
    if isinstance((photography_context or {}).get("human_review"), dict):
        return (
            f"After real debate, they recommend '{winner_variant}' as the hero; "
            "the site editor makes the final pick."
        )
    return f"After real debate, they land on '{winner_variant}' as the hero."


def _build_photography_scene_direction(photography_context: dict | None, day: str) -> str | None:
    """Build dynamic scene direction based on photography results.

    Returns a scene override string or None to use the default.
    """
    if not photography_context or not isinstance(photography_context, dict):
        return None

    reshoot_happened = photography_context.get("reshoot_happened", False)
    winner = photography_context.get("winner", {})
    winner_variant = winner.get("variant", "")
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
                + _scene_hero_landing(winner_variant, photography_context)
            ).rstrip()
        else:
            return (
                f"Julian drops three distinctly different shots: {shots_text}. "
                "Now the team has to pick the hero image - the ONE shot that represents this recipe everywhere. "
                "Each character has a strong opinion about which shot should lead. "
                "Julian has his artistic preference. Margaret cares about showing the food honestly. "
                "Steph is thinking about what performs in feed. They disagree before converging. "
                + _scene_hero_landing(winner_variant, photography_context)
            ).rstrip()
    elif day == "friday":
        review_line = _photo_review_line(photography_context)
        if review_line:
            return (
                f"Final review. {review_line} "
                "Devon Park joins to confirm deployment readiness. Approvals hinge on tiny fixes."
            )
        if not winner_variant:
            return (
                "Final review. Julian's Wednesday photos are on the table. "
                "Devon Park joins to confirm deployment readiness. Approvals hinge on tiny fixes."
            )
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
    week_label: str | None = None,
) -> None:
    """Generate per-character episode memories after a full week simulation.

    One LLM call per character (~100 tokens output each). Persists through
    the same durable storage primitives as the production Sunday writer
    (backend.admin.cron.memories._generate_episode_memories) — NOT
    backend/data/characters/<slug>/memory.json (#6968, item 6). That bundled
    path is tracked source; a local full-week simulation must not dirty it.
    Each character's memory is ONE durable blob per week
    (storage.save_character_memory_week) — no read, no merge (#6968,
    redesigned in review round 3), so replaying the same week is an
    idempotent overwrite of only that week's own blob, and this can never
    touch or lose any other week's blob.

    `week_label` defaults to the current ISO week when not given (this
    function has no episode object to read an episode_id from, unlike the
    production writer). It MUST be a real ISO week string ("YYYY-Www") —
    storage.save_character_memory_week validates it and rejects anything
    else with ValueError, since a non-ISO/synthetic label (e.g.
    "test-week") is not a real week. A caller driving a synthetic/backfill
    run should pass a real, deliberately-chosen ISO week rather than an
    arbitrary string; passing a non-ISO label simply skips that
    character's write (logged), it does not corrupt durable storage.
    """
    from datetime import date

    week_label = week_label or date.today().strftime("%G-W%V")

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

        entry = {
            "week": week_label,
            "episode_id": week_label,
            "concept": concept,
            "summary": summary,
            "key_moment": key_moment,
        }

        # One durable blob for THIS week, PUT to its own key — never a
        # direct write to the tracked backend/data/characters/<slug>/
        # memory.json source, and no read-then-merge that could touch or
        # lose any other week's blob (#6968, redesigned round 3).
        slug = _char_dir_slug(char_name)
        try:
            storage.save_character_memory_week(slug, week_label, entry)
        except Exception as e:
            logger.warning(f"Local-simulation memory write failed for {char_name}: {type(e).__name__}: {e}")


def _author_prompt(
    personas: dict[str, dict[str, Any]], names: list[str], concept: str, day: str,
    recipe_context: str | None, recipe_facts: str | None, event: str | None,
    highlights: list[str], recent_lines: list[str],
    photography_context: dict | None = None, retry_note: str | None = None,
) -> str:
    """Give a scene author character references and the meeting's factual context."""
    sheets = []
    for name in names:
        persona = personas[name]
        # Give the author the saved context that informs each character.
        remembered = [f"- {m.get('concept', 'unknown')}: {m.get('summary', '')}" for m in _load_memories(name)]
        sheets.append(
            f"{name} ({persona['role']})\nBiography:\n{_load_bio(name)}\n"
            f"Voice reference:\n{_strip_word_caps(_CHARACTER_VOICE_GUIDES.get(name, ''))}\n"
            f"Example lines:\n" + "\n".join(_CHARACTER_EXAMPLE_MESSAGES.get(name, []))
            + "\nRemembers from recent weeks:\n" + ("\n".join(remembered) or "(nothing specific)")
        )
    lo, hi = TICKS_RANGE[day]
    labels = ['Steph' if name == "Stephanie 'Steph' Whitmore" else name.split()[0] for name in names]
    length = f"Write {lo}-{hi} dialogue lines."
    photography = (
        f"Photography context:\n{json.dumps(photography_context, ensure_ascii=False)}\n"
        if photography_context else ""
    )
    retry = f"An earlier draft of this meeting was rejected. {retry_note} Fix that in this draft.\n" if retry_note else ""
    return (
        f"Write the whole {day.title()} meeting as a fiction writer writing a scene.\n"
        f"Concept: {concept}\nTask: {DAY_MEETING_GOAL[day]['objective']}\n"
        f"Recipe: {recipe_context or concept}\nRecipe Method and facts:\n{recipe_facts or '(not supplied)'}\n"
        f"{photography}{retry}"
        f"Injected event: {event or '(none)'}\n"
        f"Prior-day highlights:\n" + "\n".join(highlights or ['(none)']) + "\n"
        "Earlier dialogue:\n" + "\n".join(recent_lines or ['(none)']) + "\n\n"
        "Character sheets describe tendencies, not rules. People have good days and bad days. "
        "Choose who speaks, when someone interrupts, and who stays silent. Roster members "
        "may stay silent. Let people answer each other and let the meeting reach a decision.\n"
        f"{length}\n"
        "Output only Name: followed by one JSON string containing the spoken words, "
        "one message per line. Wrap each utterance in double quotes and use JSON escaping "
        "for quotes inside it. The string is speech; no actions or narration may appear "
        "before or after it. Example format: Margaret: \"Spoken words.\"\n"
        f"The ONLY permitted speaker labels are: {', '.join(labels)}. "
        "Do not include coworkers outside this roster, even if their names occur in the biographies. "
        "No headings, narration, stage directions, action markers, or markdown.\n\n"
        + "\n\n".join(sheets)
    )


def _parse_author_scene(text: str, names: list[str]) -> list[tuple[str, str]]:
    """Parse Name: JSON-string speech; unquoted directions are syntax errors.

    Quoting is the speech boundary, so a recipe aside such as '(looks firm)'
    stays literal speech. We do not guess whether words describe stage actions.
    """
    aliases = {alias: name for name in names for alias in (name, name.split()[0])}
    if "Stephanie 'Steph' Whitmore" in names:
        aliases['Steph'] = "Stephanie 'Steph' Whitmore"
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip():
            continue
        label, sep, speech = raw.strip().partition(':')
        name = aliases.get(label.strip())
        if not sep or name is None:
            raise ValueError(f"Author scene line {number}: unknown/off-roster speaker or narration")
        try:
            spoken = json.loads(speech.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(f"Author scene line {number}: expected only a JSON speech string") from exc
        if not isinstance(spoken, str) or not spoken.strip():
            raise ValueError(f"Author scene line {number}: empty or non-string speech")
        lines.append((name, spoken))
    if not lines:
        raise ValueError("Author scene is empty")
    return lines


def run_simulation(
    concept: str,
    default_model: str,
    run_index: int,
    stage_only: str | None,
    injected_event: str | None,
    mode: str,
    image_paths: list[str] | None = None,  # 3 image paths from photography stage
    photography_context: dict | None = None,  # full photography data with rounds/reshoot
    initial_highlights: list[str] | None = None,  # pre-computed week highlights for context injection
    initial_recent_lines: list[str] | None = None,  # seed recent_lines (e.g. Saturday msgs for Sunday-only runs)
    recipe_context: str | None = None,  # one-line recipe summary; anchors dialogue to the actual dish
    recipe_facts: str | None = None,  # #7791 P4: the judge's ground-truth facts; only reaches a speaker
    # shown to the whole-scene author alongside the recipe summary
    message_sink: list | None = None,  # #7714: when given, IS the messages list - turns land here as
    # they are generated, so a caller that holds a reference to it can recover whatever was paid for
    # even if this function never returns (e.g. an exception mid-run). Production never passes it -
    # this parameter and the branch below are the only difference from before it existed.
    retry_note: str | None = None,  # #8264: the judge's reason an earlier attempt at this day failed
) -> dict[str, Any]:
    personas = load_personas()
    start = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    messages: list[Message] = message_sink if message_sink is not None else []
    recent_lines: list[str] = list(initial_recent_lines) if initial_recent_lines else []
    week_highlights: list[str] = list(initial_highlights) if initial_highlights else []

    days = [stage_only] if stage_only else DAY_ORDER
    for day_i, day in enumerate(days):
        stage = DAY_STAGE[day]
        names = participants_for_day(day)

        prompt = _author_prompt(
            personas, names, concept, day, recipe_context, recipe_facts,
            injected_event, week_highlights, recent_lines,
            photography_context, retry_note,
        )
        for attempt in range(_AUTHOR_PARSE_TRIES):
            if mode == "template":
                count = TICKS_RANGE[day][0]
                text = "\n".join(
                    f"{names[i % len(names)]}: {json.dumps(f'Let us discuss {concept}.')}"
                    for i in range(count)
                )
            else:
                text = generate_response(prompt=prompt, model=default_model, temperature=0.8)
            try:
                scene = _parse_author_scene(text, names)
                break
            except ValueError as exc:
                exc.author_invalid_scene = text
                if attempt + 1 == _AUTHOR_PARSE_TRIES:
                    raise
                prompt += f"\n\nYour previous draft was rejected: {exc}. Follow the output format exactly."
        for tick, (speaker, line) in enumerate(scene):
            ts = start + timedelta(days=day_i, minutes=tick * (480 // len(scene)))
            messages.append(Message(day, stage, speaker, line, ts.isoformat(), default_model))
            recent_lines.append(f"{speaker.split()[0]}: {line}")
        if day == "wednesday" and image_paths:
            _distribute_images_wednesday(messages, image_paths, day)

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
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--stage", choices=DAY_ORDER, default=None)
    parser.add_argument("--event", default=None)
    parser.add_argument("--mode", choices=["llm", "template"], default="llm")
    parser.add_argument("--judge", action="store_true", help="Run Opus judge on each day with growing context")
    parser.add_argument("--judge-model", default=None, help="Override judge model (default: config.judge_model)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    models = parse_models(args.models) if args.models else [args.model]

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
                mode=args.mode,
            )
            suffix = f"{args.stage}" if args.stage else "full-week"
            safe_model = model.replace("/", "_").replace(":", "-")
            out_path = OUT_DIR / f"sim-{stamp}-{concept_slug}-{safe_model}-authored-run{i}-{suffix}.json"
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
        "models": models,
        "results": all_results,
    }
    comparison_path = OUT_DIR / f"sim-{stamp}-comparison.json"
    comparison_path.write_text(json.dumps(comparison, indent=2))
    print(f"comparison: {comparison_path}")


if __name__ == "__main__":
    main()
