"""Monday's baker gates: title, muffin-pan form, ingredient overlap and category (#8148).

Moved verbatim from backend/admin/cron_routes.py; cron_routes' Monday handler calls
`_bake_through_gates` and `_category_from_recipe` from here.
"""

from __future__ import annotations

from backend.utils.catalog import (
    catalog_recipes as _catalog_recipes,
    catalog_titles as _catalog_titles,
    normalize_category,
)
from backend.utils.logging import get_logger
from backend.utils.muffin_pan_form import check_muffin_pan_form
from backend.utils.recipe_overlap import check_ingredient_overlap
from backend.utils.recipe_sanity import (
    OVEN_TEMP_MAX_F,
    OVEN_TEMP_MIN_F,
    check_recipe_sanity,
)
from backend.utils.title_validator import check_title_conflict

# The cron routes' own logger, so these log lines read exactly as before the split.
logger = get_logger("backend.admin.cron_routes")


# ---------------------------------------------------------------------------
# Monday baker gates — title (#5911), form, ingredients (#6854), category (#6858)
# ---------------------------------------------------------------------------

# First bake plus two retries. Same worst case as the old sequential
# title-retry-then-form-retry code (three baker calls), but every gate now runs
# on every attempt, so a retry that satisfies one gate cannot slip past another.
_MAX_BAKER_ATTEMPTS = 3


def _category_from_recipe(recipe_data: dict | None) -> str | None:
    """The baker's own CATEGORY: classification, title-cased, if it is valid."""
    if not isinstance(recipe_data, dict):
        return None
    return normalize_category(recipe_data.get("category"))


def _enforce_target_category(
    recipe_data: dict | None, target_category: str | None
) -> dict | None:
    """Make the recipe's category agree with the category the week was picked for.

    The picker chooses the concept FOR a category (the thinnest shelf), so the
    category is decided before the baker runs. The baker's CATEGORY: line is
    its own classification of its output, and it gets that wrong: W36's
    Portuguese custard tart came back "savory" (#6877). The Sunday publisher
    copies recipe_data.category into the catalog verbatim, so a wrong label
    both mis-shelves the recipe on /recipes and under-reports the count the
    next category pick reads — the imbalance quietly feeds itself (#6858).

    No-op without a target (explicit-concept path with no category supplied
    or stored): then the baker's classification is the honest one and stands.
    """
    if not isinstance(recipe_data, dict) or not target_category:
        return recipe_data
    want = target_category.strip().lower()
    have = str(recipe_data.get("category") or "").strip().lower()
    if have != want:
        logger.warning(
            f"Baker labelled '{recipe_data.get('title', '')}' as "
            f"{have or 'nothing'!r}; overriding to this week's target category "
            f"{want!r}"
        )
        recipe_data["category"] = want
    return recipe_data


def _bake_through_gates(
    orchestrator,
    ep: dict,
    concept: str,
    target_category: str | None,
    recent_cuisines: list[str],
    catalog: dict,
) -> tuple[dict, list[dict]]:
    """Run the baker until its recipe clears every Monday gate, or fail closed.

    Gates, on EVERY attempt, in order:
      1. title uniqueness against the catalog (#5911 — deliberately relaxed
         threshold; read RUNBOOK INCIDENT 3 before touching it),
      2. muffin-pan form (the pan must shape the food),
      3. ingredient overlap against the catalog (#6854) — the gate that would
         have caught W36, whose title was fine and whose dish was not.

    Each failure appends a targeted constraint to the concept for the next
    attempt, so the baker is told exactly what to change. After
    _MAX_BAKER_ATTEMPTS the last failure is raised as RuntimeError, which
    _run_stage turns into a failed-stage record, an alert and a 500. Nothing
    off-brand or duplicate is ever written to the episode.

    Returns (recipe_data, gate_trace). The trace is stored on the stage so the
    episode JSON shows that the gates ran and what they saw — the lesson of
    INCIDENT 4 is that a duplicate check which skips itself leaves no mark.
    """
    catalog_titles = _catalog_titles(catalog)
    catalog_recipes = _catalog_recipes(catalog)
    episode_id = str(ep.get("episode_id") or "")

    constraints: list[str] = []
    trace: list[dict] = []
    last_failure = "baker never ran"

    for attempt in range(1, _MAX_BAKER_ATTEMPTS + 1):
        attempt_concept = (
            concept if not constraints else f"{concept}. " + " ".join(constraints)
        )
        recipe_data = orchestrator._execute_stage_baker(
            ep["recipe_id"], attempt_concept, target_category=target_category,
            recent_cuisines=recent_cuisines,
        )
        recipe_data = _enforce_target_category(recipe_data, target_category)
        baker_title = recipe_data.get("title", "") if recipe_data else ""

        conflict = check_title_conflict(baker_title, catalog_titles)
        if conflict:
            last_failure = f"duplicate title '{baker_title}': {conflict}"
            trace.append({"attempt": attempt, "gate": "title", "result": conflict})
            logger.warning(
                f"Baker attempt {attempt}: {last_failure}. Retrying with an "
                f"explicit blacklist."
            )
            blacklist = ", ".join(f"'{t}'" for t in catalog_titles[:15])
            constraints.append(
                f"CRITICAL: the title must NOT be similar to any of these "
                f"already-published recipes: {blacklist}. Pick a distinctive "
                f"angle with different key words."
            )
            continue

        form_issue = check_muffin_pan_form(recipe_data)
        if form_issue:
            last_failure = f"off-brand muffin-pan form '{baker_title}': {form_issue}"
            trace.append({"attempt": attempt, "gate": "form", "result": form_issue})
            logger.warning(
                f"Baker attempt {attempt}: {last_failure}. Retrying with explicit "
                f"form constraints."
            )
            constraints.append(
                "CRITICAL MUFFIN-PAN FORM: the finished dish must bind into "
                "self-contained cups, bites, nests, or mini loaves that hold "
                "shape after removal from the pan. The muffin tin must shape "
                "the food, not merely portion loose fillings. Previous attempt "
                f"failed because: {form_issue}."
            )
            continue

        verdict = check_ingredient_overlap(
            recipe_data, catalog_recipes, exclude_episode_id=episode_id or None,
        )
        best = verdict.best
        trace.append({
            "attempt": attempt,
            "gate": "ingredients",
            "status": verdict.status,
            "new_items": verdict.new_items,
            "closest": best.title if best else None,
            "score": best.score if best else None,
        })
        if verdict.status == "duplicate" and best is not None:
            last_failure = (
                f"same dish as a published recipe — '{baker_title}': {verdict.reason}"
            )
            logger.warning(
                f"Baker attempt {attempt}: {last_failure}. Retrying with the "
                f"match named."
            )
            shared = ", ".join(best.shared[:8])
            constraints.append(
                f"CRITICAL: an earlier attempt produced the same dish as the "
                f"already-published '{best.title}' (ingredient overlap "
                f"{best.score:.0%}; shared: {shared}). This week's recipe must "
                f"be a genuinely different dish — change the core ingredients "
                f"and the construction, not just the name."
            )
            continue
        if verdict.status == "skipped_thin":
            # Visible, not silent: a recipe too thin to compare is itself a
            # quality signal, and the trace above records the skip.
            logger.warning(
                f"Ingredient gate skipped for '{baker_title}': {verdict.reason}"
            )

        sanity = check_recipe_sanity(recipe_data)
        trace.append({
            "attempt": attempt,
            "gate": "recipe_sanity",
            "status": sanity.status,
            "issues": list(sanity.issues),
            "warnings": list(sanity.warnings),
            **sanity.details,
        })
        for warning in sanity.warnings:
            # Advisory checks (#7099) - measured as false-positive-prone
            # against the stored corpus, so they are visible and never block.
            logger.warning(f"Recipe sanity note for '{baker_title}': {warning}")
        if sanity.blocking:
            last_failure = f"recipe is {sanity.status}: {sanity.reason}"
            logger.warning(
                f"Baker attempt {attempt}: {last_failure}. Retrying with the "
                f"defect named."
            )
            constraints.append(
                f"CRITICAL: an earlier attempt produced a recipe a reader "
                f"could not safely follow: {'; '.join(sanity.issues)}. Every "
                f"recipe must state an oven temperature between "
                f"{OVEN_TEMP_MIN_F}F and {OVEN_TEMP_MAX_F}F, and any raw "
                f"poultry, pork, ground meat or seafood must reach a stated "
                f"internal temperature."
            )
            continue

        if attempt > 1:
            logger.info(f"Baker attempt {attempt} cleared every gate: '{baker_title}'")
        return recipe_data, trace

    raise RuntimeError(
        f"Baker could not clear the Monday gates in {_MAX_BAKER_ATTEMPTS} "
        f"attempts. Last failure: {last_failure}. Re-fire /api/cron/monday "
        f"with an explicit 'concept' in the body to bypass auto-pick."
    )
