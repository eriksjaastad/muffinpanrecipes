"""The editorial QA gate that runs before Sunday publishes, and its recipe auto-fix (#8148).

Moved verbatim from backend/admin/cron_routes.py; cron_routes' Sunday handler calls
`_editorial_qa_review` and `_auto_fix_recipe` from here. These functions read their own
module's names, so a test that needs to stub the QA judge, the fix model, the catalog lookup,
storage or the alert inside them patches THIS module, not cron_routes.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from backend.config import config
from backend.storage import storage
from backend.utils.discord import notify_pipeline_failure
from backend.utils.episode_integrity import DAY_ORDER
from backend.utils.logging import get_logger
from backend.utils.model_router import generate_judge_response, generate_response
from backend.utils.recipe_sanity import (
    OVEN_TEMP_MAX_F,
    OVEN_TEMP_MIN_F,
    check_recipe_sanity,
)
from backend.utils.text_sanitize import has_encoding_issues

# The cron routes' own logger, so these log lines read exactly as before the split.
logger = get_logger("backend.admin.cron_routes")


# ---------------------------------------------------------------------------
# Editorial QA gate — runs before Sunday publish
# ---------------------------------------------------------------------------

_EDITORIAL_QA_SYSTEM_PROMPT = (
    "You are the editorial director for Muffin Pan Recipes, a food website. "
    "You review every recipe before publication. You care deeply about quality, "
    "originality, and making readers hungry.\n\n"
    "CHECK FOR:\n"
    "1. ENCODING: Any garbled characters, mojibake, or broken symbols (e.g. Ã¢, ÃÂ°)\n"
    "2. PUNCTUATION: Mismatched quotes, missing apostrophes, broken dashes\n"
    "3. REPEATED WORDS: Duplicate adjacent words ('the the', 'and and')\n"
    "4. AI ARTIFACTS: Placeholder text, 'as an AI', instruction-like text that leaked in\n"
    "5. RECIPE COHERENCE: Title, description, ingredients, and instructions should align\n"
    "6. MEASUREMENTS: Temperatures in °F, US customary units (cups, tbsp, tsp, oz, lbs)\n"
    "7. PLAUSIBILITY: Oven temps 250-500°F, cook times reasonable, yields make sense for 12-cup muffin tin\n"
    "8. INGREDIENT-INSTRUCTION MATCH: Every ingredient should be used, no phantom ingredients\n"
    "9. BRAND VOICE: Warm, professional food writing — not robotic or generic\n"
    "10. TITLE RULES (automatic FAIL if violated):\n"
    "    a. Must be 3-6 words. No subtitles, parentheticals, or days of the week.\n"
    "    b. Must sound APPETIZING — like something you'd see on a food magazine cover.\n"
    "       FAIL words: 'prep', 'meal prep', 'make-ahead', 'batch', 'easy', 'quick',\n"
    "       'simple', 'weeknight', 'freezer', 'budget', 'healthy'. These are SEO slop,\n"
    "       not food writing.\n"
    "    c. Must NOT repeat key words from recently published recipes (provided below).\n"
    "       If the title shares a distinctive word (not 'mini'/'cups'/'bites') with\n"
    "       ANY of the last 3 published recipes, it FAILS for lack of variety.\n"
    "    d. The title should evoke the DISH, not the METHOD. 'Roasted Veggie Egg Cups'\n"
    "       is borderline. 'Herbed Vegetable Frittata Cups' is better.\n\n"
    "Respond with EXACTLY this format:\n"
    "STATUS: PASS or FAIL\n"
    "ISSUES: (list each issue on its own line, or 'None' if passing)\n"
    "RECOMMENDATION: (one sentence)\n\n"
    "Be strict. A recipe with ANY encoding issue, factual error, or title rule "
    "violation is an automatic FAIL."
)

MAX_QA_FIX_ATTEMPTS = 2

_RECIPE_FIX_SYSTEM_PROMPT = (
    "You are a meticulous recipe editor. You are given a recipe that failed editorial QA, "
    "along with the specific issues identified by the reviewer.\n\n"
    "Fix ALL identified issues while preserving the recipe's character and intent.\n\n"
    "RULES:\n"
    "1. TITLE: Must be 3-6 words. No subtitles, parentheticals, days of the week, or ellipsis.\n"
    "   The title must sound APPETIZING — like a food magazine cover. Never use utilitarian\n"
    "   words like 'prep', 'meal prep', 'make-ahead', 'batch', 'easy', 'quick', 'simple',\n"
    "   'weeknight', 'freezer', 'budget', 'healthy'. Evoke the DISH, not the METHOD.\n"
    "   If the title repeats key words from recent recipes, choose completely different words.\n"
    "2. INGREDIENTS: Consolidate duplicates. If the same ingredient appears multiple times, "
    "either combine into one entry with the total amount, or group under clear sub-headings "
    "(e.g. 'For the filling:', 'For the topping:').\n"
    "3. INSTRUCTIONS: Must reference every ingredient. Fix any quantity mismatches.\n"
    "4. MUFFIN-PAN FORM: Envision the pan — twelve ROUND, flared wells that mold "
    "the food. The dish must bind into self-contained cups, bites, nests, or mini "
    "loaves that hold shape after removal from the pan. No loose fillings or "
    "recipes where the tin is incidental. The title's shape word must be one a "
    "round muffin cup can produce (Cups, Bites, Nests, Tassies) — NEVER Squares, "
    "Bars, Slabs, Slices, or Wedges; a muffin pan cannot make those shapes.\n"
    "5. MEASUREMENTS: US customary only (cups, tbsp, tsp, oz, lbs, °F).\n"
    "6. Keep the recipe's personality and voice intact.\n\n"
    "Output the fixed recipe in EXACTLY this JSON format:\n"
    "```json\n"
    '{"title": "...", "description": "...", "servings": 12, "prep_time": 15, '
    '"cook_time": 20, "difficulty": "medium", "category": "savory", '
    '"ingredients": [{"item": "...", "amount": "...", "notes": "..."}], '
    '"instructions": ["Step 1...", "Step 2..."], '
    '"chef_notes": "..."}\n'
    "```\n"
    "Return ONLY the JSON block, no other text."
)


def _auto_fix_recipe(episode: dict, qa_report: str) -> bool:
    """Attempt to fix recipe issues identified by editorial QA.

    Modifies episode['stages']['monday']['recipe_data'] in place.
    Returns True if fixes were applied, False if unable to fix.
    """
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})
    if not recipe:
        return False

    # Format current recipe for the fixer
    ing_text = "\n".join(
        f"- {ing.get('amount', '')} {ing.get('item', '')} ({ing.get('notes', '')})"
        if isinstance(ing, dict) else f"- {ing}"
        for ing in recipe.get("ingredients", [])
    )
    inst_text = "\n".join(
        f"{i+1}. {s}" for i, s in enumerate(recipe.get("instructions", []))
    )
    repetition_guidance = ""
    if "TITLE REPETITION" in qa_report.upper():
        from backend.utils.title_validator import distinctive_title_words

        rejected_title = recipe.get("title", "")
        blocked_words = sorted(distinctive_title_words(rejected_title))
        blocked_text = ", ".join(blocked_words) if blocked_words else rejected_title
        repetition_guidance = (
            "\nTITLE REPETITION FIX:\n"
            f"The current title '{rejected_title}' was rejected for repeating "
            "recently published recipe language. You MUST create a completely "
            "new title with a different food angle. Do not reuse these title "
            f"words: {blocked_text}.\n"
        )

    fix_prompt = (
        f"RECIPE THAT FAILED QA:\n\n"
        f"Title: {recipe.get('title', '')}\n"
        f"Description: {recipe.get('description', '')}\n"
        f"Servings: {recipe.get('servings', 12)}\n"
        f"Prep Time: {recipe.get('prep_time', 15)} mins\n"
        f"Cook Time: {recipe.get('cook_time', 20)} mins\n"
        f"Difficulty: {recipe.get('difficulty', 'medium')}\n"
        f"Category: {recipe.get('category', 'savory')}\n\n"
        f"Ingredients:\n{ing_text}\n\n"
        f"Instructions:\n{inst_text}\n\n"
        f"Chef's Notes: {recipe.get('chef_notes', '')}\n\n"
        f"---\n\n"
        f"QA FAILURE REPORT:\n{qa_report}\n\n"
        f"{repetition_guidance}"
        f"Fix all issues listed above and return the corrected recipe as JSON."
    )

    try:
        response = generate_response(
            prompt=fix_prompt,
            system_prompt=_RECIPE_FIX_SYSTEM_PROMPT,
            model=config.recipe_model,
            temperature=0.3,
        )

        # Extract JSON from response
        import json as _json
        # Try to find JSON block in markdown code fence or raw
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            fixed = _json.loads(json_match.group(1))
        else:
            # Try raw JSON
            fixed = _json.loads(response.strip())

        # Apply title enforcement as extra safety
        from backend.utils.recipe_prompts import _enforce_title_rules
        fixed["title"] = _enforce_title_rules(fixed.get("title", recipe.get("title", "")))

        # Validate we got something reasonable
        if not fixed.get("title") or not fixed.get("ingredients") or not fixed.get("instructions"):
            logger.warning("Auto-fix returned incomplete recipe — skipping")
            return False

        # #7853: the description is Marcus's intro and the pitch is Monday's
        # internal one-liner. Neither is the recipe fixer's to rewrite or drop:
        # carry both over from the recipe that went in. (A QA failure about the
        # intro itself therefore cannot be "fixed" here; QA fails again and
        # Sunday refuses, which is the fail-closed outcome.)
        for owned in ("description", "pitch"):
            if owned in recipe:
                fixed[owned] = recipe[owned]
            else:
                fixed.pop(owned, None)

        # Update recipe data in place
        monday["recipe_data"] = fixed
        logger.info(f"Auto-fixed recipe: '{fixed['title']}' ({len(fixed['ingredients'])} ingredients)")
        return True

    # governance: allow-silent SF002: False is the failure signal; the Sunday fix loop breaks, then raises HTTP 400 and notify_judge_failure because QA never passed
    except Exception as e:
        # TRIAGE (#6856): NON-FATAL, and the surrounding gate fails closed.
        # Returning False breaks the Sunday auto-fix loop, which then raises
        # HTTP 400 + notify_judge_failure because editorial QA never passed.
        # The recipe is left untouched, so nothing half-fixed can publish.
        logger.error(f"Auto-fix failed (recipe left unmodified): {type(e).__name__}: {e}")
        return False


# Sentinel prefix marking a QA report that means "the reviewer never ran",
# as opposed to "the reviewer ran and rejected the recipe". The Sunday loop
# checks for it so it doesn't burn auto-fix LLM calls trying to repair a
# recipe nobody actually reviewed.
QA_UNAVAILABLE_MARKER = "EDITORIAL QA UNAVAILABLE"


def _recent_catalog_titles(limit: int = 8) -> list[str]:
    """Return the most recently published recipe titles, or [] if unavailable.

    Two independent readers, because this feeds a duplicate defense:
      1. the storage layer (respects the test/ prefix, so test runs review
         against test data), and
      2. title_validator.load_catalog_titles, which reads the public blob CDN
         directly and itself falls back to the static src/recipes.json.

    Every failure is logged. The caller alerts when the result is empty.
    """
    try:
        catalog_raw = storage.load_page("pages/recipes.json")
        if catalog_raw:
            catalog = json.loads(catalog_raw)
            titles = [
                str(r.get("title", "")).strip()
                for r in catalog.get("recipes", [])[:limit]
            ]
            titles = [t for t in titles if t]
            if titles:
                return titles
        logger.warning("Catalog read via storage returned no recipe titles")
    except Exception as e:
        logger.error(
            f"Catalog read via storage FAILED: {type(e).__name__}: {e}. "
            f"Falling back to the public CDN reader."
        )

    try:
        from backend.utils.title_validator import load_catalog_titles

        return load_catalog_titles()[:limit]
    # governance: allow-silent SF002: _editorial_qa_review treats [] as degraded, stamps catalog_context_degraded_at and sends notify_pipeline_failure
    except Exception as e:
        logger.error(f"Public-CDN catalog fallback FAILED: {type(e).__name__}: {e}")
        return []


def _editorial_qa_review(episode: dict) -> tuple[bool, str]:
    """Run editorial QA on the complete recipe before publish.

    Returns (passed: bool, report: str).
    """
    monday = episode.get("stages", {}).get("monday", {})
    recipe = monday.get("recipe_data", {})

    title = recipe.get("title", "")
    description = recipe.get("description", "")
    ingredients = recipe.get("ingredients", [])
    instructions = recipe.get("instructions", [])
    chef_notes = recipe.get("chef_notes", "")

    # Pre-check: scan for encoding issues programmatically
    encoding_flags = []
    all_text_fields = [
        ("title", title),
        ("description", description),
        ("chef_notes", chef_notes),
    ]
    for ing in ingredients:
        if isinstance(ing, dict):
            text = f"{ing.get('amount', '')} {ing.get('item', '')} {ing.get('notes', '')}".strip()
        else:
            text = str(ing)
        all_text_fields.append(("ingredient", text))
    for i, step in enumerate(instructions):
        step_text = step if isinstance(step, str) else str(step)
        all_text_fields.append((f"instruction_{i+1}", step_text))

    # Check dialogue too
    for day in DAY_ORDER:
        dialogue = episode.get("stages", {}).get(day, {}).get("dialogue", [])
        for msg in dialogue:
            all_text_fields.append((f"dialogue_{day}", msg.get("message", "")))

    for field_name, text in all_text_fields:
        if has_encoding_issues(text):
            encoding_flags.append(f"Encoding issue in {field_name}: {text[:80]!r}")

    if encoding_flags:
        report = (
            "STATUS: FAIL\n"
            "ISSUES:\n" + "\n".join(f"  - {f}" for f in encoding_flags) + "\n"
            "RECOMMENDATION: Fix encoding issues before publish. "
            "Text contains double-encoded UTF-8 (mojibake)."
        )
        logger.warning(f"Editorial QA FAIL (encoding): {len(encoding_flags)} issues found")
        return False, report

    from backend.utils.muffin_pan_form import check_muffin_pan_form

    form_issue = check_muffin_pan_form(recipe)
    if form_issue:
        report = (
            "STATUS: FAIL\n"
            f"ISSUES:\n  - MUFFIN PAN FORM: {form_issue}\n"
            "RECOMMENDATION: Rewrite the recipe so the muffin tin shapes a "
            "cohesive cup, bite, nest, or mini loaf that holds together after "
            "removal from the pan."
        )
        logger.warning(f"Editorial QA FAIL (muffin-pan form): {form_issue}")
        return False, report

    # Layer 2.5 (#7099): the recipe itself. Not redundant with Monday's gate
    # — _auto_fix_recipe REPLACES recipe_data wholesale with free-form LLM
    # JSON, and none of Monday's gates re-run on the result. Without this a
    # fixer could introduce an unsafe recipe that only the LLM reviewer, with
    # no ground truth, stands between and the reader.
    sanity = check_recipe_sanity(recipe)
    for warning in sanity.warnings:
        logger.warning(f"Editorial QA note (recipe sanity): {warning}")
    if sanity.blocking:
        issues = "\n".join(f"  - RECIPE SANITY: {i}" for i in sanity.issues)
        report = (
            "STATUS: FAIL\n"
            f"ISSUES:\n{issues}\n"
            "RECOMMENDATION: State an oven temperature between "
            f"{OVEN_TEMP_MIN_F}F and {OVEN_TEMP_MAX_F}F, and give any raw "
            "poultry, pork, ground meat or seafood an explicit internal "
            "temperature so a reader can confirm it is cooked through."
        )
        logger.warning(f"Editorial QA FAIL (recipe sanity): {sanity.reason}")
        return False, report

    # Format content for LLM review
    ing_text = "\n".join(
        f"- {ing.get('amount', '')} {ing.get('item', '')}" if isinstance(ing, dict) else f"- {ing}"
        for ing in ingredients
    )
    inst_text = "\n".join(
        f"{i+1}. {s}" if isinstance(s, str) else f"{i+1}. {s}"
        for i, s in enumerate(instructions)
    )

    # Load recently published recipe titles for the repetition check.
    #
    # TRIAGE (#6856): this is a DUPLICATE DEFENSE, so it must never vanish
    # quietly. It used to be a bare `except Exception: pass` with no log line
    # at all — the most invisible failure in this file. When it fired,
    # catalog_context became "" and rule 10c of the QA prompt ("must NOT
    # repeat key words from recently published recipes") silently reviewed
    # against an empty list. Now: the blob read is backed by the public-CDN
    # reader in title_validator (which has its own static fallback), and an
    # empty result on a site that has published before is alerted, not shrugged off.
    recent_titles = _recent_catalog_titles(limit=8)

    catalog_context = ""
    if recent_titles:
        catalog_context = (
            "RECENTLY PUBLISHED RECIPES (check title for repetition):\n"
            + "\n".join(f"  - {t}" for t in recent_titles)
            + "\n\n"
        )
    elif not episode.get("catalog_context_degraded_at"):
        # Alert once per episode, not once per auto-fix retry: this function
        # runs up to three times in the fix loop and the catalog is just as
        # absent each time. The stamp is persisted with the episode, so it
        # also records for later that this publish's duplicate detection was
        # running blind.
        episode["catalog_context_degraded_at"] = datetime.now(timezone.utc).isoformat()
        logger.error(
            "Editorial QA has NO catalog context: the title-repetition rule "
            "is running blind for episode %s",
            episode.get("episode_id"),
        )
        notify_pipeline_failure(
            recipe_id=episode.get("recipe_id") or "unknown",
            concept=episode.get("concept") or "unknown",
            stage="sunday (editorial QA)",
            error_message=(
                "Editorial QA could not load any published recipe titles. "
                "The title-repetition check reviewed this recipe against an "
                "empty catalog — duplicate detection is DEGRADED for this "
                "publish. Verify the recipe title by hand."
            ),
        )

    review_prompt = (
        f"{catalog_context}"
        f"RECIPE TO REVIEW:\n\n"
        f"Title: {title}\n\n"
        f"Description: {description}\n\n"
        f"Ingredients:\n{ing_text}\n\n"
        f"Instructions:\n{inst_text}\n\n"
        f"Chef's Notes: {chef_notes}\n\n"
        f"Review this recipe for publication."
    )

    try:
        verdict = generate_judge_response(
            prompt=review_prompt,
            system_prompt=_EDITORIAL_QA_SYSTEM_PROMPT,
            model=config.judge_model,
            temperature=0.2,
        ).strip()
        passed = "STATUS: PASS" in verdict.upper() or verdict.upper().startswith("PASS")
        logger.info(f"Editorial QA verdict: {verdict[:300]}")
        return passed, verdict
    except Exception as e:
        # FAIL CLOSED (#6505, #6856). This used to return
        # (True, "EDITORIAL QA ERROR (defaulting to PASS)"), so a provider
        # outage on a Sunday published an unreviewed recipe with a green
        # `editorial_qa.passed: true` in the episode JSON. The dialogue judge
        # was made fail-closed in PR #45; the publish gate is now symmetric.
        logger.error(f"Editorial QA review FAILED (treated as FAIL): {type(e).__name__}: {e}")
        return False, (
            "STATUS: FAIL\n"
            f"ISSUES:\n  - {QA_UNAVAILABLE_MARKER}: {type(e).__name__}: {e}\n"
            "RECOMMENDATION: The editorial reviewer could not run, so this "
            "recipe has NOT been reviewed. Do not publish until the reviewer "
            "is reachable; re-fire /api/cron/sunday once it is."
        )
