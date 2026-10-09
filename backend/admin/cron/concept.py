"""Monday's concept selection: pick this week's dish, or keep a stored one (#6855, #8148).

Moved verbatim from backend/admin/cron_routes.py; cron_routes' Monday handler calls
`_resolve_monday_concept` and raises `ConceptSelectionError` from here. Tests that stub the
pick or the catalog read on this path patch THIS module (`_pick_weekly_concept`,
`load_published_catalog`), since these functions read their own module's names.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.utils.catalog import load_published_catalog, normalize_category
from backend.utils.episode_integrity import PLACEHOLDER_CONCEPT
from backend.utils.logging import get_logger

if TYPE_CHECKING:
    from backend.admin.cron.stage_request import StageRequest

# The cron routes' own logger, so these log lines read exactly as before the split.
logger = get_logger("backend.admin.cron_routes")


class ConceptSelectionError(RuntimeError):
    """Monday could not select a real concept for the week.

    Deliberately fatal. A week with no concept has no duplicate avoidance,
    so it must stop at Monday rather than publish something the novelty
    scorer never saw.
    """


# ---------------------------------------------------------------------------
# Monday concept selection (#6855)
# ---------------------------------------------------------------------------

# Two attempts, no sleep. pick_concept() spends one LLM brainstorm call plus at
# most ~6s of optional, concurrent inspiration scraping (#6858), and Monday's
# Lambda budget is 300s; a second pass is worth it for a transient throw (a
# provider blip, a catalog fetch that timed out), a third is not.
_CONCEPT_PICK_ATTEMPTS = 2


def _pick_weekly_concept() -> tuple[str, str | None, str]:
    """Pick this week's concept and target category, or fail closed.

    Returns (concept, target_category, source) where source is "brainstorm"
    or "curated" — the Monday stage records it, so a brainstorm that has been
    failing for weeks shows up in the episode JSON, not only in a Lambda log
    line. Raises ConceptSelectionError rather
    than falling back to PLACEHOLDER_CONCEPT: pick_concept() is where
    duplicate avoidance lives (it scores candidates against the entire
    published catalog), so a week that skips it has one weak defense left.

    pick_concept() no longer depends on third-party recipe sites (#6858):
    candidates come from an LLM brainstorm over the published catalog with a
    curated per-category pool behind it, and the scrape is optional
    inspiration that can fail with no effect. So "sources unreachable" is a
    non-event; what reaches the raise below is "the catalog could not be read"
    or "no candidate survived the category / form / novelty filters" — both of
    which mean nothing is standing between this week and a duplicate.
    """
    try:
        from scripts.pick_concept import pick_concept_candidates, pick_target_category
    except ImportError as exc:
        # NOT transient. scripts/ is excluded from the Vercel bundle except
        # for the explicit re-includes in .vercelignore, and pick_concept.py
        # was not one of them — so this import raised on EVERY production
        # Monday and the old `except Exception` turned it into the
        # placeholder concept. 2026-W30 through 2026-W35 all stored
        # "Weekly Muffin Pan Recipe" with target_category None. See #6855.
        raise ConceptSelectionError(
            f"Concept picker is not importable in this deployment: {exc}. "
            f"This is a packaging bug, not a transient one — check the "
            f"scripts/ re-includes in .vercelignore."
        ) from exc

    last_error: Exception | None = None
    for attempt in range(1, _CONCEPT_PICK_ATTEMPTS + 1):
        try:
            # One catalog read per attempt, shared by the category pick and the
            # concept pick — each used to fetch (and retry) on its own.
            catalog = load_published_catalog()
            target_category = pick_target_category(catalog)
            picks = pick_concept_candidates(
                count=1, target_category=target_category, catalog=catalog,
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                f"Concept pick attempt {attempt}/{_CONCEPT_PICK_ATTEMPTS} "
                f"raised {type(exc).__name__}: {exc}"
            )
            continue

        concept = str(picks[0][1].concept).strip() if picks else ""
        source = str(picks[0][1].source) if picks else ""
        if concept and concept != PLACEHOLDER_CONCEPT:
            if source == "curated":
                # Loud on purpose: the curated pool is eight concepts per shelf.
                # One week of this is fine; several in a row means the LLM
                # brainstorm is broken and variety is silently capped.
                logger.warning(
                    f"Concept {concept!r} came from the CURATED fallback pool — "
                    f"the brainstorm failed or every candidate was rejected. "
                    f"Check the picker if this repeats."
                )
            logger.info(
                f"Auto-selected concept={concept!r}, category={target_category}, "
                f"source={source}"
            )
            return concept, target_category, source

        last_error = ConceptSelectionError(
            "concept picker returned no candidate: every brainstormed and "
            "curated candidate was filtered out by the category, form and "
            "novelty checks"
        )
        logger.warning(
            f"Concept pick attempt {attempt}/{_CONCEPT_PICK_ATTEMPTS} "
            f"produced no usable concept"
        )

    raise ConceptSelectionError(
        f"Could not select a concept after {_CONCEPT_PICK_ATTEMPTS} attempts: "
        f"{type(last_error).__name__}: {last_error}. Re-fire "
        f"/api/cron/monday with an explicit 'concept' in the body to proceed."
    ) from last_error


def _resolve_monday_concept(body: StageRequest, ep: dict) -> tuple[str, str | None, str]:
    """Decide which concept this Monday run uses.

    Returns (concept, target_category, source); source is one of "explicit",
    "stored", "brainstorm" or "curated" and is written to the Monday stage.

    Precedence:
      1. An explicit body.concept always wins — that is the documented manual
         override and the RUNBOOK recovery path. Its category is
         body.target_category, else the stored one, else None — and None is
         resolved AFTER the baker runs, from the baker's own classification.
      2. A real stored concept is kept, so re-firing Monday mid-week does not
         swap the dish out from under stages that already ran.
      3. Otherwise pick fresh.

    A stored PLACEHOLDER_CONCEPT is NOT a real concept, so it never blocks a
    re-pick. That was the second half of the W36 bug: the old line

        concept = body.concept or ep.get("concept") or concept

    let a stored placeholder override a concept we had just picked
    successfully, so re-running Monday on an existing episode could never
    repair the week. `force=true` — already the flag for "I am deliberately
    re-running this stage" — also re-picks.
    """
    stored_category = ep.get("target_category") or ep.get("stages", {}).get(
        "monday", {}
    ).get("target_category")

    if body.concept:
        # An explicit concept is the operator's choice of dish, so its category
        # is the dish's own — never a guess at the thinnest shelf. The old code
        # ran pick_target_category() here, which would have labelled W36's
        # Portuguese custard tarts by whatever shelf happened to be thinnest
        # (#6858, #6877) — and now that Monday enforces the target category on
        # the recipe, that guess would have overwritten the right answer.
        #
        # Precedence: body.target_category (validated to one of
        # VALID_CATEGORIES in _parse_body), then a category already stored on
        # the episode, else None. cron_monday resolves None from the baker's
        # own CATEGORY: line after baking, so target_category is still written
        # — the integrity check reads a null as "the picker never ran", and the
        # RUNBOOK INCIDENT 4 recovery must leave a clean week.
        category = normalize_category(body.target_category) or stored_category
        return body.concept.strip(), category, "explicit"

    stored = str(ep.get("concept") or "").strip()
    if stored and stored != PLACEHOLDER_CONCEPT and not body.force:
        logger.info(
            f"Keeping stored concept {stored!r} for {ep.get('episode_id')} "
            f"(pass force=true or an explicit concept to re-pick)"
        )
        return stored, stored_category, "stored"

    return _pick_weekly_concept()
