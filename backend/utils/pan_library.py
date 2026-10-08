"""The photographer's pan library (#8067, #8068).

Image models invent a pan when the prompt only describes the food: W41's
Wednesday set had mesh discs instead of wells, a 30-well pan with liners and
a riveted 20-well grid. The prompt now carries one real pan, described by
its geometry (fixed per size, so it reads as a real object) and its material
(rotated week to week, so the pans do not all look alike).

Sizes and well dimensions come from published size guides (Quaker, Preppy
Kitchen, Once Upon a Chef) and a commercial jumbo pan listing; materials from
current muffin-pan buying guides and vintage cast-iron gem pan and tinware
references. Tested on W41: docs/image-lab/results/2026-10-07-w41-pan/.

Only the standard 12-cup pan is used in production for now: every recipe is
written for it (utils/recipe_prompts.py). The other sizes are the library
the baker will choose from in #8067.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

PAN_LIBRARY_VERSION = "panV1"

SIZES: dict[str, dict[str, str]] = {
    "mini": {
        "label": "24-cup mini muffin pan",
        "geometry": (
            "24 identical round wells in a precise 4-by-6 grid, each well about 1 3/4 inches "
            "across the top, tapering to about 1 1/4 inches at the base, about 7/8 inch deep, "
            "holding about 2 tablespoons; the whole pan is about 15 by 10 inches"
        ),
    },
    "standard": {
        "label": "12-cup standard muffin pan",
        "geometry": (
            "12 identical round wells in a precise 3-by-4 grid, each well about 2 3/4 inches "
            "across the top, tapering to about 2 inches at the base, about 1 3/8 inches deep, "
            "holding about 1/2 cup; the whole pan is about 14 by 10.5 inches"
        ),
    },
    "jumbo": {
        "label": "6-cup jumbo muffin pan",
        "geometry": (
            "6 identical round wells in a precise 2-by-3 grid, each well about 3 1/2 inches "
            "across the top, tapering to about 2 1/2 inches at the base, about 1 3/4 inches deep, "
            "holding about 1 cup; the whole pan is about 14 by 10 inches"
        ),
    },
}

# Rotation order: neighbours are chosen to look unlike each other (light,
# black, gold, sand, charcoal, tin, green, silver, steel), so consecutive
# weeks never share a colour family.
MATERIALS: dict[str, dict[str, object]] = {
    "light_aluminized_steel": {
        "sizes": ("mini", "standard", "jumbo"),
        "description": (
            "light silver-gold aluminized steel with a faintly corrugated, fluted surface "
            "between the wells, satin finish, crisp rolled rim along the outer edge"
        ),
    },
    "cast_iron_gem_pan": {
        "sizes": ("standard",),
        "description": (
            "seasoned black cast iron with a slightly pebbled, glossy-oiled surface, the round "
            "cups cast as one heavy piece and joined by thick cast bars with open gaps between "
            "the cups, a short cast handle at each end, in the style of an antique gem pan"
        ),
    },
    "gold_nonstick": {
        "sizes": ("mini", "standard"),
        "description": (
            "champagne-gold nonstick-coated steel with a soft metallic sheen, smooth wells, "
            "neat rolled rim"
        ),
    },
    "stoneware": {
        "sizes": ("standard",),
        "description": (
            "unglazed sand-colored stoneware, thick matte walls and rounded rim, darkened "
            "seasoning blush inside the wells from use, heavy and solid"
        ),
    },
    "dark_nonstick": {
        "sizes": ("mini", "standard", "jumbo"),
        "description": (
            "matte charcoal-black nonstick-coated carbon steel, smooth and uniform, slightly "
            "raised flat rim, faint wear on the rim from use"
        ),
    },
    "vintage_starburst_tin": {
        "sizes": ("mini", "standard"),
        "description": (
            "vintage tinned steel, dull silver with spotted age patina and a few darkened bake "
            "marks, each well pressed with a swirling starburst of fluted ridges, thin rolled "
            "edge, 1940s kitchenware"
        ),
    },
    "silicone": {
        "sizes": ("mini", "standard", "jumbo"),
        "description": (
            "flexible matte silicone in a muted sage green, soft rounded rim, sitting on a "
            "plain metal baking sheet for support"
        ),
    },
    "commercial_aluminum": {
        "sizes": ("mini", "standard", "jumbo"),
        "description": (
            "heavy-gauge natural commercial bakery aluminum, dull satin silver with a mottled, "
            "darkened patina from years of use, wide flat flanges at each end, thick wired rim"
        ),
    },
    "stainless_steel": {
        "sizes": ("standard",),
        "description": (
            "bright brushed stainless steel with soft reflections in the wells, clean rolled rim"
        ),
    },
}

PAN_CLAUSE_TEMPLATE = (
    "THE PAN - wherever any part of the pan appears, it is this exact real pan: a {size_label} "
    "made of {material}. It has {geometry}. It is a real manufactured baking pan: perfectly "
    "regular grid, every well identical, straight rows, no extra or missing wells, no mesh, "
    "no wire racks, no grates, no paper liners. "
)

_EPISODE_ID_RE = re.compile(r"^(\d{4})-W(\d{2})$")


@dataclass(frozen=True)
class Pan:
    size: str
    material: str
    clause: str


def _absolute_week(episode_id: str | None, today: date | None = None) -> int:
    """Weeks since day one for the episode's ISO week; consecutive weeks differ by 1.

    Falls back to the current UTC ISO week when the id is missing or not
    ``YYYY-Www`` (the Wednesday cron runs inside the episode's own week).
    """
    match = _EPISODE_ID_RE.match(episode_id or "")
    if match:
        try:
            monday = date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
        except ValueError:
            monday = None
        if monday is not None:
            return monday.toordinal() // 7
    current = today or datetime.now(timezone.utc).date()
    year, week, _ = current.isocalendar()
    return date.fromisocalendar(year, week, 1).toordinal() // 7


def pan_for_week(episode_id: str | None, size: str = "standard", today: date | None = None) -> Pan:
    """The week's pan: fixed geometry for ``size``, material rotated by ISO week.

    Deterministic, so a Wednesday re-fire in the same week shoots the same
    pan; never repeats a material within ``len(pool)`` consecutive weeks.
    """
    if size not in SIZES:
        raise ValueError(f"unknown pan size {size!r}")
    pool = [m for m, spec in MATERIALS.items() if size in spec["sizes"]]  # type: ignore[operator]
    material = pool[_absolute_week(episode_id, today) % len(pool)]
    clause = PAN_CLAUSE_TEMPLATE.format(
        size_label=SIZES[size]["label"],
        material=MATERIALS[material]["description"],
        geometry=SIZES[size]["geometry"],
    )
    return Pan(size=size, material=material, clause=clause)
