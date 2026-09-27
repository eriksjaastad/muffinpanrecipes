"""Director agent as a lab knob (#7679, absorbs #7677).

Erik's direction: the knobs are *outside* variables — a director plus a random
number generator. Code rolls the dice; the model only writes the specifics.
This module lives under backend/ so the Vercel Lambda bundle carries it; the
production default is OFF (``scripts.simulate_dialogue_week.DIRECTOR["enabled"]
is False``).

``roll_day`` is the random number generator. It draws one ``u`` and one
category per character from a deterministic RNG seeded by
``(rng_seed, seed_key, day)``, then decides ``has_something = u < probability``
*after* both draws. Because the draws happen in a fixed order regardless of
probability, a 25% run is always a subset of a 50% run for the same seed
(common random numbers across settings). Intensity is not rolled — it is the
knob value handed to the prompt.

``direct_day`` is the director. It makes one Haiku call (through
``backend.utils.model_router.generate_response``, so an OpenRouter model string
is billed there and lands in ``cost_by_model``) asking for strict JSON::

    {"scene": "<1-2 sentences>", "characters": {"<name>": "<one line>"}}

Lines are requested only for characters whose roll has ``has_something``.

Documented implementation choices:

- **Summaries are deterministic truncations.** History entries are
  ``{"day", "character", "category", "summary"}`` with ``summary`` <= 10 words.
  Rather than ask the model for a second field, each summary is the model's own
  line truncated to its first 10 words. This keeps the strict-JSON shape flat
  and removes a whole class of parse failures.
- **History entries may also carry ``scene``.** The no-repeat check compares a
  new scene's 4-word sequences against earlier scenes; the caller
  (``scripts.simulate_dialogue_week.run_simulation``) therefore records the
  day's scene alongside the required fields. The four required fields above are
  always present; ``scene`` is an extension for the phrase check.
- **The 4-word phrase check uses every history entry**, not just the
  ``no_repeat_window`` most recent ones, for both summaries and earlier scenes.
  The (character, category) pair check uses the per-character window as
  specified. No repeats is paramount; a longer phrase memory is stricter, not
  stricter than the brief.
- **One retry, then fallback.** A repeat rejection regenerates once with the
  offending items named. A second rejection returns ``Direction(fallback=True)``
  with no content — the caller runs the day with today's plain scene. Repeats
  never raise into the day loop. Model failures and malformed JSON raise
  :class:`DirectorError` (no silent empty result).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field

from backend.utils.model_router import generate_response

CATEGORIES = [
    "personal",
    "logistics",
    "kitchen mishap",
    "weather",
    "outside world",
    "work pressure",
    "private job fact",
]

# What each category means, shown to the director next to the roll. The dice
# pick the label; without a definition the model guesses what "private job
# fact" is asking for.
CATEGORY_MEANINGS = {
    "personal": "something in their life outside work (family, sleep, a friend, a mood)",
    "logistics": "getting here or getting set up (commute, a delivery, a room booking)",
    "kitchen mishap": "a small thing that went wrong in the test kitchen before the meeting",
    "weather": "the weather affecting them or the day",
    "outside world": "news or an event beyond the team that reached them this morning",
    "work pressure": "a deadline, a message from above, or another project pulling at them",
    "private job fact": (
        "something only this character knows that bears on today's decision, from their "
        "own job (e.g. the photographer's light window, the social manager's platform "
        "change, the site's constraint) - never a claim about the recipe's ingredients"
    ),
}

INTENSITY_SCALE = {
    1: "mundane (slept badly, forgot lunch)",
    2: "noticeable (running late, minor annoyance)",
    3: "real (car broke down, a supplier called, a sick kid)",
    4: "big (power cut in the test kitchen, a surprise visitor, bad news)",
    5: "absurd (a film crew shows up, a pipe bursts mid-bake, aliens in the parking lot)",
}


class DirectorError(RuntimeError):
    """The director could not produce a usable direction."""


@dataclass
class Roll:
    """One character's dice roll. ``u`` is kept for common-random-number tests."""

    character: str
    category: str
    u: float
    has_something: bool


@dataclass
class Direction:
    """A day's scene and per-character pre-meeting lines.

    ``fallback=True`` means the no-repeat guard gave up after one retry: both
    fields are empty and the caller runs the day with today's plain scene.
    """

    scene: str
    characters: dict[str, str] = field(default_factory=dict)
    fallback: bool = False


def _rng(rng_seed: int, seed_key: str, day: str) -> random.Random:
    """A deterministic RNG for (rng_seed, seed_key, day).

    Seeded with a SHA-256 digest of the JSON-encoded triple so seeds cannot
    collide the way a raw string concatenation could (``"a" + ":b"`` vs
    ``"a:" + "b"``), and so the stream is stable across processes.
    """
    seed_bytes = hashlib.sha256(
        json.dumps([rng_seed, seed_key, day], separators=(",", ":")).encode("utf-8")
    ).digest()
    return random.Random(seed_bytes)


def roll_day(
    seed_key: str,
    characters: list[str],
    probability: float,
    rng_seed: int,
    day: str = "",
) -> list[Roll]:
    """Roll every character deterministically from (rng_seed, seed_key, day).

    For EVERY character, in the given (fixed) order and regardless of
    ``probability``, draw ``u = rng.random()`` then
    ``category = rng.choice(CATEGORIES)``; only then set
    ``has_something = u < probability``. The draws happen before the threshold
    is applied, so for a fixed seed the rolls at probability 0.25 are a subset
    of the rolls at probability 0.5 with identical categories and ``u`` values.
    """
    rng = _rng(rng_seed, seed_key, day)
    rolls: list[Roll] = []
    for character in characters:
        u = rng.random()
        category = rng.choice(CATEGORIES)
        rolls.append(
            Roll(
                character=character,
                category=category,
                u=u,
                has_something=u < probability,
            )
        )
    return rolls


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z']+", text.lower())


def _four_word_sequences(text: str) -> set[tuple[str, ...]]:
    words = _words(text)
    return {tuple(words[i : i + 4]) for i in range(len(words) - 3)}


def _build_prompt(
    day: str,
    concept: str,
    objective: str,
    chosen: list[Roll],
    intensity: int,
    history: list[dict] | None,
    offenders: list[str] | None = None,
) -> str:
    intensity_text = INTENSITY_SCALE[intensity]
    parts = [
        f"Day: {day}",
        f"Recipe concept: {concept}",
        f"Today's meeting objective: {objective}",
        f"Intensity: {intensity} - {intensity_text}",
    ]
    if chosen:
        parts.append("Characters with something going on before this meeting:")
        for roll in chosen:
            meaning = CATEGORY_MEANINGS.get(roll.category, "")
            parts.append(f"- {roll.character}: {roll.category} ({meaning})")
    else:
        parts.append("Characters with something going on before this meeting: none")

    if history:
        parts.append("HISTORY (situations already used - never repeat any of these):")
        for entry in history[-8:]:
            summary = " ".join(str(entry.get("summary", "")).split()[:10])
            parts.append(
                f"- day {entry.get('day', '?')}, {entry.get('character', '?')}, "
                f"{entry.get('category', '?')}: {summary}"
            )

    parts.extend(
        [
            "RULES:",
            "- Never repeat a situation, a (character, category) pairing, or any phrasing "
            "from the history.",
            "- Do not decide the meeting's outcome.",
            "- Do not change the recipe. Never invent or mention ingredients, quantities, "
            "or techniques - you do not know the recipe's contents, and a wrong one fails "
            "the week.",
            "Respond with strict JSON only:",
            '{"scene": "<1-2 sentences: where the team is and what the room is like today>", '
            '"characters": {"<name>": "<one line: what happened to them before this meeting>"}}',
            "Write a character line ONLY for the characters listed above with something "
            "going on.",
        ]
    )
    if offenders:
        parts.append("")
        parts.append("Your previous draft repeated the history. Problems:")
        parts.extend(f"- {offender}" for offender in offenders)
        parts.append("Generate new JSON that fixes every problem above.")
    return "\n".join(parts)


def _parse_direction(raw: str, chosen_names: set[str]) -> Direction:
    if not isinstance(raw, str):
        raise DirectorError(f"director returned a non-string response: {raw!r}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DirectorError(f"director returned malformed JSON: {raw[:120]!r}") from exc
    if not isinstance(payload, dict):
        raise DirectorError("director JSON is not an object")

    scene = payload.get("scene")
    if not isinstance(scene, str) or not scene.strip():
        raise DirectorError("director JSON is missing a non-empty 'scene' string")

    characters = payload.get("characters")
    if not isinstance(characters, dict):
        raise DirectorError("director JSON 'characters' must be an object")

    kept: dict[str, str] = {}
    for name, line in characters.items():
        if not isinstance(name, str) or not isinstance(line, str):
            raise DirectorError("director JSON character entries must be string -> string")
        if name in chosen_names and line.strip():
            kept[name] = line.strip()
    return Direction(scene=scene.strip(), characters=kept)


def _find_repeats(
    direction: Direction,
    chosen: list[Roll],
    history: list[dict] | None,
    no_repeat_window: int,
) -> list[str]:
    """Name every way ``direction`` repeats ``history``.

    Two checks, per the brief:
    1. A (character, category) pair that already appears in that character's
       last ``no_repeat_window`` history entries.
    2. Any 4-word sequence in a new line or the new scene that appears in a
       history summary or an earlier scene.
    """
    entries = [entry for entry in history or [] if isinstance(entry, dict)]
    prior_summaries: list[tuple[object, str]] = []
    prior_scenes: list[tuple[object, str]] = []
    for entry in entries:
        summary = entry.get("summary")
        scene = entry.get("scene")
        day = entry.get("day", "?")
        if isinstance(summary, str) and summary.strip():
            prior_summaries.append((day, summary))
        if isinstance(scene, str) and scene.strip():
            prior_scenes.append((day, scene))

    offenders: list[str] = []

    scene_grams = _four_word_sequences(direction.scene)
    for day, text in prior_scenes:
        shared = scene_grams & _four_word_sequences(text)
        if shared:
            sample = " ".join(sorted(shared)[0])
            offenders.append(f"the scene repeats phrasing from the day {day} scene: {sample!r}")
            break
    for day, text in prior_summaries:
        shared = scene_grams & _four_word_sequences(text)
        if shared:
            sample = " ".join(sorted(shared)[0])
            offenders.append(f"the scene repeats phrasing from a day {day} summary: {sample!r}")
            break

    for roll in chosen:
        line = (direction.characters or {}).get(roll.character, "").strip()
        if not line:
            continue

        char_entries = [entry for entry in entries if entry.get("character") == roll.character]
        for entry in char_entries[-no_repeat_window:]:
            if entry.get("category") == roll.category:
                offenders.append(
                    f"{roll.character} repeats the {roll.category!r} situation from day "
                    f"{entry.get('day', '?')}"
                )
                break

        line_grams = _four_word_sequences(line)
        for day, text in prior_summaries:
            shared = line_grams & _four_word_sequences(text)
            if shared:
                sample = " ".join(sorted(shared)[0])
                offenders.append(
                    f"{roll.character}'s line repeats phrasing from a day {day} summary: {sample!r}"
                )
                break
        for day, text in prior_scenes:
            shared = line_grams & _four_word_sequences(text)
            if shared:
                sample = " ".join(sorted(shared)[0])
                offenders.append(
                    f"{roll.character}'s line repeats phrasing from the day {day} scene: {sample!r}"
                )
                break

    return offenders


def direct_day(
    day: str,
    concept: str,
    objective: str,
    rolls: list[Roll],
    intensity: int,
    history: list[dict] | None,
    model: str,
    no_repeat_window: int = 14,
) -> Direction:
    """Direct one simulated day: one Haiku call, one retry on repeat, else fallback.

    Raises :class:`DirectorError` on model failure or malformed JSON. Never
    raises for a repeat: after one regeneration naming the offending items, a
    still-repeating direction is returned as ``Direction(fallback=True)`` with
    no content.
    """
    if intensity not in INTENSITY_SCALE:
        raise DirectorError(
            f"intensity must be one of {sorted(INTENSITY_SCALE)}, got {intensity!r}"
        )

    chosen = [roll for roll in rolls if roll.has_something]
    chosen_names = {roll.character for roll in chosen}

    def call(prompt: str) -> str:
        try:
            return generate_response(
                prompt=prompt,
                system_prompt=(
                    "You write one-scene directions for a simulated creative team meeting. "
                    "Respond with strict JSON only."
                ),
                model=model,
                temperature=0.7,
            ).strip()
        except Exception as exc:
            raise DirectorError(f"director model call failed: {exc}") from exc

    raw = call(_build_prompt(day, concept, objective, chosen, intensity, history))
    direction = _parse_direction(raw, chosen_names)
    offenders = _find_repeats(direction, chosen, history, no_repeat_window)
    if not offenders:
        return direction

    retry_prompt = _build_prompt(
        day, concept, objective, chosen, intensity, history, offenders=offenders
    )
    retry_raw = call(retry_prompt)
    retry = _parse_direction(retry_raw, chosen_names)
    if _find_repeats(retry, chosen, history, no_repeat_window):
        return Direction(scene="", characters={}, fallback=True)
    return retry
