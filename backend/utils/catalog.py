"""One strict reader for the published recipe catalog (``pages/recipes.json``).

Why this module exists. Three catalog readers grew up independently:

* ``scripts/pick_concept.py`` read through the storage layer (prefix-aware, so a
  ``test=true`` cron scored novelty against ``test/pages/recipes.json``) and
  fell back to ``src/recipes.json``;
* ``backend/utils/title_validator.py`` read the public blob CDN and fell back
  to ``src/recipes.json``;
* the Sunday publisher read through the storage layer.

``src/recipes.json`` is the ten launch seeds and nothing else. So every time
the live catalog could not be read, the catalog-aware gates on Monday quietly
shrank to a ten-recipe view and reported success. That is the same failure
class as RUNBOOK INCIDENT 4 (a duplicate defense switching itself off without
a trace) — cards #6854 and #6858.

Monday's gates now read through :func:`load_published_catalog`, which retries
briefly and then **raises**. A Monday that cannot see the catalog cannot check
for duplicates, so it must stop rather than guess. ``_pick_weekly_concept``
already treats any exception from the picker as a fail-closed condition.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections import Counter
from typing import Any

from backend.utils.logging import get_logger

logger = get_logger(__name__)

# The four shelves on /recipes. One source of truth for the category picker,
# the Monday category enforcement and request validation (#6858).
VALID_CATEGORIES: tuple[str, ...] = ("Breakfast", "Savory", "Sweet", "Party")


def normalize_category(value: object) -> str | None:
    """Title-case a category label if it is one of VALID_CATEGORIES, else None."""
    label = str(value or "").strip().title()
    return label if label in VALID_CATEGORIES else None


# Public, prefix-free URL of the live catalog. Reading it directly bypasses the
# storage layer's test-mode prefix so duplicate detection always sees the real
# production catalog — even during ``test=true`` cron invocations.
CATALOG_PUBLIC_URL = (
    "https://gtczmjysc51nh8fq.public.blob.vercel-storage.com/pages/recipes.json"
)

# Short backoff between attempts. Monday's Lambda budget is 300s and the
# catalog fetch is one of several network calls on that path, so the total
# worst case here is bounded at roughly attempts × timeout + 1.5s.
_BACKOFF_SECONDS = (0.5, 1.0)


class CatalogUnavailableError(RuntimeError):
    """The published catalog could not be read or was malformed.

    Deliberately fatal for callers on the publishing path: a gate that cannot
    see the catalog has nothing to compare against.
    """


def load_published_catalog(
    *,
    attempts: int = 3,
    timeout: float = 8.0,
    url: str = CATALOG_PUBLIC_URL,
) -> dict[str, Any]:
    """Fetch and validate the live catalog, or raise CatalogUnavailableError.

    Returns the parsed catalog dict, guaranteed to carry a ``recipes`` list.
    Never falls back to the static seed file — see the module docstring.
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "muffinpanrecipes-catalog-reader/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network, HTTP, decode — all retryable
            last_error = exc
            logger.warning(
                f"catalog fetch attempt {attempt}/{attempts} failed: "
                f"{type(exc).__name__}: {exc}"
            )
            if attempt < attempts:
                time.sleep(_BACKOFF_SECONDS[min(attempt - 1, len(_BACKOFF_SECONDS) - 1)])
            continue

        recipes = payload.get("recipes") if isinstance(payload, dict) else None
        if not isinstance(recipes, list):
            # A well-formed response with the wrong shape is not transient.
            raise CatalogUnavailableError(
                f"catalog at {url} is malformed: expected a dict with a "
                f"'recipes' list, got {type(payload).__name__}"
            )
        return payload

    raise CatalogUnavailableError(
        f"could not read the published catalog after {attempts} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error


def catalog_recipes(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Recipe entries from a loaded catalog, newest first, dicts only."""
    return [r for r in catalog.get("recipes", []) if isinstance(r, dict)]


def catalog_titles(catalog: dict[str, Any]) -> list[str]:
    """Lower-cased, stripped, non-empty titles — the title validator's input."""
    titles: list[str] = []
    for recipe in catalog_recipes(catalog):
        title = str(recipe.get("title") or "").strip().lower()
        if title:
            titles.append(title)
    return titles


def category_counts(catalog: dict[str, Any]) -> Counter[str]:
    """Count published recipes per Title-cased category (e.g. ``Sweet``)."""
    counts: Counter[str] = Counter()
    for recipe in catalog_recipes(catalog):
        category = str(recipe.get("category") or "").strip().title()
        if category:
            counts[category] += 1
    return counts
