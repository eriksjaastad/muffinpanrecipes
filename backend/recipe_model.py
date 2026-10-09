"""Recipe facts derived the same way everywhere: slugs, titles and ingredients (#8147).

One implementation of each. A published recipe's URL is frozen at publish time, so
`slugify` must not change behaviour: every published /recipes/<slug> page depends on it.

- `slugify` / `clean_title` / `catalog_slug`: the recipe URL slug and the catalog's title.
- `ingredient_text`: one ingredient as the catalog shows it ("amount item (notes)").
- `ingredient_names`: deduped display names for prompts (quantities and notes dropped).

The ingredient-overlap normalizer (backend/utils/recipe_overlap.py) and the LLM-line
parser (backend/utils/recipe_prompts.py `_parse_ingredient`) are deliberately separate:
they return different shapes for different jobs. The week id lives in
backend/utils/episode_integrity.py `current_episode_id`.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def slugify(title: str) -> str:
    """Convert a recipe title to a URL slug.

    'Make-Ahead Veggie & Sausage Egg Cups (Weekly Muffin Pan Breakfast)'
    -> 'make-ahead-veggie-sausage-egg-cups'
    """
    # Remove parenthetical suffixes
    title = re.sub(r'\s*\(.*?\)\s*', '', title)
    # #7106: transliterate accents BEFORE the non-alphanumeric pass, or every
    # accented letter becomes a hyphen - W37's "Pao" (with a tilde) published at
    # /recipes/brazilian-p-o-de-queijo-bites. NFKD splits "a-tilde" into "a" plus a
    # combining mark; dropping category Mn keeps the base letter.
    title = "".join(
        c for c in unicodedata.normalize("NFKD", title)
        if unicodedata.category(c) != "Mn"
    )
    # Lowercase, replace non-alphanumeric with hyphens
    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    # Collapse multiple hyphens
    slug = re.sub(r'-+', '-', slug)
    return slug


def clean_title(title: str) -> str:
    """Strip parenthetical qualifiers from recipe titles.

    'Make-Ahead Veggie & Sausage Egg Cups (Weekly Muffin Pan Breakfast)'
    -> 'Make-Ahead Veggie & Sausage Egg Cups'
    """
    return re.sub(r'\s*\(.*?\)\s*$', '', title).strip()


def catalog_slug(episode: dict) -> str:
    """The slug this episode's recipe gets in the catalog (and so its
    /recipes/<slug> URL), or "" when it has no usable title. One derivation
    for the catalog and every caller that needs the same URL (#7806)."""
    recipe = episode.get("stages", {}).get("monday", {}).get("recipe_data", {})
    title = clean_title(recipe.get("title", ""))
    return slugify(title) if title else ""


def ingredient_text(ing: Any) -> str:
    """One ingredient as catalog entries list it: "amount item (notes)"."""
    if isinstance(ing, dict):
        text = f"{ing.get('amount', '')} {ing.get('item', '')}".strip()
        if ing.get("notes"):
            text += f" ({ing['notes']})"
        return text
    return str(ing)


_INGREDIENT_NUMBER = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|[¼½¾⅓⅔⅛⅜⅝⅞])"
_INGREDIENT_RANGE = rf"{_INGREDIENT_NUMBER}(?:\s*(?:-|–|—|to)\s*{_INGREDIENT_NUMBER})?"
_INGREDIENT_MEASURE = (
    r"(?:cups?|tablespoons?|tbsp|teaspoons?|tsp|ounces?|oz|pounds?|lbs?|lb|"
    r"grams?|grammes?|g|kilograms?|kg|milliliters?|millilitres?|ml|"
    r"liters?|litres?|l|inches?|inch|in\.)"
)
_INGREDIENT_QUANTITY_RE = re.compile(
    rf"(?:about\s+)?{_INGREDIENT_RANGE}(?:\s+(?:heaping|full)\s+{_INGREDIENT_MEASURE}|\s+{_INGREDIENT_MEASURE})?"
    rf"(?:\s+plus\s+(?:more|extra|(?:about\s+)?{_INGREDIENT_RANGE}(?:\s+{_INGREDIENT_MEASURE})?))?",
    re.IGNORECASE,
)
_INGREDIENT_COUNT_RE = re.compile(
    rf"{_INGREDIENT_NUMBER}\s+(?:(?:thin|small|large|full)\s+)?"
    r"(?:sheets?|sticks?|strips?|cloves?|pieces?|slices?|cans?|packages?|"
    r"bunch(?:es)?|heads?|large|medium|small)"
    rf"(?:\s*\((?:about\s+)?{_INGREDIENT_RANGE}\s+{_INGREDIENT_MEASURE}\))?",
    re.IGNORECASE,
)
_PRESENTATION_AMOUNT_RE = re.compile(
    r"(?:optional\s*:\s*)?(?:pinch(?:\s+of)?|dash(?:\s+of)?|handful(?:\s+of)?)|"
    r"optional\s*:|extra|as\s+needed|to\s+taste",
    re.IGNORECASE,
)
_RESIDUAL_MEASURE_RE = re.compile(
    rf"^{_INGREDIENT_MEASURE}\b\s*", re.IGNORECASE
)


def _ingredient_amount_kind(amount: str) -> str | None:
    """Classify only known quantity and presentation forms from stored recipe data."""
    normalized = " ".join(amount.split())
    if not normalized:
        return None
    if _PRESENTATION_AMOUNT_RE.fullmatch(normalized):
        return "presentation"
    if _INGREDIENT_QUANTITY_RE.fullmatch(normalized) or _INGREDIENT_COUNT_RE.fullmatch(normalized):
        return "quantity"
    return None


def _split_plain_ingredient_quantity(value: str) -> tuple[str, str]:
    """Split a leading supported quantity from a plain-string ingredient."""
    boundaries = [match.start() for match in re.finditer(r"\s+", value)]
    for boundary in reversed(boundaries):
        amount = value[:boundary].strip()
        item = value[boundary:].strip()
        if _ingredient_amount_kind(amount) == "quantity" and item and not item.startswith(("%", "/")):
            return amount, item
    return "", value


def ingredient_names(recipe_data: dict | None) -> list[str]:
    """Return normalized, deduped ingredient names from stored recipe shapes.

    Dict entries use separate ``amount`` and ``item`` strings; plain strings
    are either names or may begin with a supported quantity. Known full
    quantities and presentation labels are omitted. Unknown amount prefixes
    are conservatively restored before the item, since recipe generation can
    split ingredient names across these fields. ``notes`` are never included.
    This is a bounded normalizer for these stored shapes, not a general recipe
    parser. A comma in the reconstructed item still starts the existing prep
    clause convention; supported measurement units left in ``item`` are
    removed only when the amount itself supplies quantity evidence.
    """
    if not isinstance(recipe_data, dict):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for ing in (recipe_data.get("ingredients") or []):
        if isinstance(ing, dict):
            amount = " ".join(str(ing.get("amount") or "").split())
            item = " ".join(str(ing.get("item") or "").split())
        else:
            amount = ""
            item = " ".join(str(ing or "").split())
            amount, item = _split_plain_ingredient_quantity(item)

        amount_kind = _ingredient_amount_kind(amount)
        quantity_evidence = amount_kind == "quantity"
        if amount_kind:
            name = item
        elif amount:
            separator = "" if amount.endswith(",") else " "
            name = f"{amount}{separator}{item}".strip()
        else:
            name = item

        # These are anchored presentation prefixes found in stored recipe data.
        # Do not remove ordinary identity words such as "whole" or "cloves".
        name = re.sub(r"^optional\s*:\s*", "", name, flags=re.IGNORECASE)
        name = re.sub(r"^(?:pinch|dash|handful)\s+of\s+", "", name, flags=re.IGNORECASE)
        if amount_kind:
            name = re.sub(r"^of\s+", "", name, flags=re.IGNORECASE)
        if quantity_evidence:
            name = _RESIDUAL_MEASURE_RE.sub("", name)
        if not amount or amount_kind:
            # Some parsed rows leave a presentation "or" in the item field
            # after its first choice was omitted. Preserve it when an unknown
            # lexical amount (for example, "Ghee") is restored above.
            name = re.sub(r"^or\s+", "", name, flags=re.IGNORECASE)
        name = name.split(",", 1)[0].strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names
