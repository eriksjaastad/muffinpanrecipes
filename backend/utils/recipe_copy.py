"""Reader-facing recipe copy: Marcus's Thursday intro (#7853).

Margaret's Monday recipe owns ingredients, instructions and chef notes. The
one-line DESCRIPTION her prompt still writes is kept as the recipe's internal
``pitch``: it anchors the week's dialogue and judge context and the Monday
muffin-pan form gate, and is never published. The published description is
Marcus's intro, written on Thursday as if he had just tasted the dish. Until
then the page shows INTRO_PLACEHOLDER.

The intro shape was chosen from a 5-call probe on 2026-10-05 against W35-W40
(card #7853): the prompt asks for 2 sentences of 30-50 words, and the code
accepts exactly 2 sentences of 25-60 words (see INTRO_MIN_WORDS). That keeps it
the size of the descriptions it replaces (median 45 words, ~285 characters),
which feed the homepage card, the meta description and the recipe JSON-LD.

There is no fallback text. A failed model call, or an intro that still breaks
the rules after its retry, raises IntroError: a canned paragraph must never
reach readers.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

INTRO_PLACEHOLDER = "Marcus adds his intro on Thursday after tasting the new recipe."

# Openers of this many most recent published descriptions are banned (#7853 AC:
# no opener word on more than 2 of the last 10). Banning all of them is
# stricter: no opener repeats inside the window at all.
OPENER_WINDOW = 10
# The prompt asks for 30-50 words. Enforcement accepts 25-60: the 5-call probe
# landed at 46-54, so a hard 50 would turn ordinary output into retries and
# failed Thursdays, while 25-60 still keeps the card/meta/JSON-LD slot the
# size of the descriptions it replaces (median 45 words).
INTRO_MIN_WORDS = 25
INTRO_MAX_WORDS = 60
INTRO_SENTENCES = 2
INTRO_ATTEMPTS = 2

# Letters and apostrophes only: "Golden-brown" and "Golden-crisp" both open
# with "golden".
_OPENER_RE = re.compile(r"[^A-Za-zÀ-ÿ]*([A-Za-zÀ-ÿ'’]+)")


class IntroError(RuntimeError):
    """Marcus's intro could not be produced within the rules."""


def recipe_pitch(recipe: dict[str, Any]) -> str:
    """The recipe's internal one-line pitch (context only, never published).

    Episodes from before #7853 have no ``pitch`` key; their Monday description
    is the same text, so it stands in. A present key wins even when empty:
    falling back then would hand Marcus's intro to the dialogue and judge
    after Thursday, changing their inputs mid-week.
    """
    if "pitch" in recipe:
        return str(recipe.get("pitch") or "")
    return str(recipe.get("description") or "")


def opener_word(text: str) -> str:
    match = _OPENER_RE.match(text or "")
    return match.group(1).lower() if match else ""


def recent_openers(catalog_recipes: Iterable[dict[str, Any]], window: int = OPENER_WINDOW) -> list[str]:
    """Opener words of the ``window`` most recent published weekly recipes.

    Weekly recipes carry an ``episode_id`` (``YYYY-Www``, sortable); the seed
    recipes have none and are never "recent".
    """
    weekly = [
        r for r in catalog_recipes
        if isinstance(r, dict) and r.get("episode_id") and r.get("description")
    ]
    weekly.sort(key=lambda r: str(r["episode_id"]), reverse=True)
    return sorted({opener_word(r["description"]) for r in weekly[:window]} - {""})


# Abbreviations whose periods do not end a sentence.
_ABBREVIATIONS = re.compile(r"\b(?:e\.g|i\.e|etc|vs|approx|oz|lb|tbsp|tsp)\.", re.IGNORECASE)
# A sentence ends at . ! or ?, optionally followed by closing quotes or
# brackets, then whitespace. Decimals ("1.5") have no whitespace after the dot.
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*\s+")


def sentence_count(text: str) -> int:
    """Sentences ending in . ! or ? (a trailing fragment counts as one)."""
    plain = _ABBREVIATIONS.sub(lambda m: m.group(0).replace(".", ""), text.strip())
    return len([part for part in _SENTENCE_END.split(plain) if part.strip()])


def intro_problems(text: str, banned_openers: Iterable[str]) -> list[str]:
    """Rule violations for a candidate intro; empty means acceptable."""
    problems: list[str] = []
    words = len(text.split())
    if not text.strip():
        return ["the intro is empty"]
    if words < INTRO_MIN_WORDS or words > INTRO_MAX_WORDS:
        problems.append(f"it is {words} words; write 30 to 50")
    sentences = sentence_count(text)
    if sentences != INTRO_SENTENCES:
        problems.append(f"it has {sentences} sentences; write exactly 2")
    opener = opener_word(text)
    if opener in set(banned_openers):
        problems.append(f'it opens with "{opener}", which a recent intro already used')
    return problems


_SYSTEM = """You are Marcus Reid, the copywriter for Muffin Pan Recipes. You have just tasted this week's recipe, fresh out of the muffin pan, and you are writing its intro: the short paragraph that sits under the recipe title on the site and shows in search results and on the homepage card.

Write exactly 2 sentences, 30 to 50 words in total.
- Sentence 1: what it is like to eat, from someone who just tasted it: texture, temperature, the flavor that lands first. Concrete, not abstract.
- Sentence 2: what the muffin pan does for this dish, or when you would serve it.
- Plain, vivid English. No literary references, no quotations, no "I", no exclamation marks, no em dashes, no hashtags.
- Do not open with any of these words: {banned}.
- Output only the intro text, nothing else."""


def _user_prompt(recipe: dict[str, Any]) -> str:
    ingredients = ", ".join(
        str(i.get("item", "")) if isinstance(i, dict) else str(i)
        for i in (recipe.get("ingredients") or [])[:10]
    )
    steps = " ".join(str(s) for s in (recipe.get("instructions") or [])[:4])[:600]
    return (
        f"Recipe: {recipe.get('title', '')}\n"
        f"Category: {recipe.get('category', '')}\n"
        f"Ingredients: {ingredients}\n"
        f"Key steps: {steps}"
    )


def generate_intro(
    recipe: dict[str, Any],
    banned_openers: Iterable[str],
    *,
    model: Optional[str] = None,
) -> str:
    """Marcus's intro for ``recipe``, or IntroError. At most INTRO_ATTEMPTS calls.

    A rule violation gets one retry that names the problem. A model error is
    not retried here (the cron stage's own failure handling and a re-fire
    cover that) and is raised as IntroError with its cause.
    """
    from backend.config import config
    from backend.utils.model_router import generate_response

    banned = sorted(set(banned_openers))
    system = _SYSTEM.format(banned=", ".join(f'"{b}"' for b in banned) or "(none)")
    prompt = _user_prompt(recipe)
    problems: list[str] = []
    for attempt in range(1, INTRO_ATTEMPTS + 1):
        if problems:
            prompt = (
                _user_prompt(recipe)
                + "\n\nYour previous intro broke a rule: " + "; ".join(problems)
                + ". Write a new one that follows every rule."
            )
        try:
            text = " ".join(
                generate_response(
                    prompt=prompt,
                    system_prompt=system,
                    model=model or config.recipe_model,
                    temperature=0.85,
                ).split()
            )
        except Exception as exc:
            raise IntroError(f"intro generation failed on attempt {attempt}: {type(exc).__name__}: {exc}") from exc
        problems = intro_problems(text, banned)
        if not problems:
            return text
    raise IntroError(f"intro broke the rules after {INTRO_ATTEMPTS} attempts: {'; '.join(problems)}")
