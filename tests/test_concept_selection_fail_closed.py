"""Monday must fail closed when it cannot pick a real concept (#6855).

W36 (2026-08-31) published a duplicate — "Greek Spanakopita Cups" against an
existing "Spanakopita Phyllo Cups" — because `pick_concept()` never ran.
`scripts/pick_concept.py` was excluded from the Vercel bundle, so the import
raised ModuleNotFoundError on every production Monday, and the old
`except Exception` downgraded that to a logger.warning and continued with the
placeholder concept. The catalog-aware novelty scorer is where duplicate
avoidance lives, so the week ran with its strongest defense switched off and
every stage still reported `complete`.

Two behaviours are pinned here:
  1. A concept pick that fails must NOT leave a completed Monday stage.
  2. A stored placeholder must not out-rank a freshly picked concept, or
     re-running Monday can never repair the week.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, Request

from backend.admin import cron_routes


def _request() -> Request:
    return cast(Request, SimpleNamespace(url=SimpleNamespace(path="/api/cron/monday")))


def _body(**kwargs) -> cron_routes.StageRequest:
    return cron_routes.StageRequest(episode_id="2026-W36", force=True, **kwargs)


def _fresh_episode() -> dict:
    return {
        "episode_id": "2026-W36",
        "concept": cron_routes.PLACEHOLDER_CONCEPT,
        "stages": {},
        "events": [],
        "recipe_id": None,
    }


def _run_monday(episode: dict, body: cron_routes.StageRequest):
    """Invoke cron_monday with everything expensive stubbed out."""
    saved: list[dict] = []

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(
             cron_routes.storage, "save_episode",
             side_effect=lambda _id, ep: saved.append(ep),
         ), \
         patch.object(cron_routes, "_get_orchestrator") as orchestrator, \
         patch.object(cron_routes, "_generate_and_judge_dialogue") as dialogue, \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        try:
            result = asyncio.run(cron_routes.cron_monday(_request()))
            error = None
        except HTTPException as exc:
            result = None
            error = exc

    return SimpleNamespace(
        result=result,
        error=error,
        saved=saved,
        notify=notify,
        orchestrator=orchestrator,
        dialogue=dialogue,
    )


# ---------------------------------------------------------------------------
# 1. Fail closed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "failure_mode,patch_target,side_effect",
    [
        # The real W36 shape: the module is not in the deployment at all.
        ("import", "pick_target_category", ImportError("No module named 'scripts.pick_concept'")),
        # A genuine throw from the picker (all five scrape sources down).
        ("raises", "pick_concept", RuntimeError("every source returned 429")),
    ],
)
def test_failed_concept_pick_does_not_complete_monday(
    failure_mode: str, patch_target: str, side_effect: Exception
) -> None:
    episode = _fresh_episode()

    if failure_mode == "import":
        # Simulate the module missing from the bundle.
        with patch.dict("sys.modules", {"scripts.pick_concept": None}):
            run = _run_monday(episode, _body())
    else:
        with patch(f"scripts.pick_concept.{patch_target}", side_effect=side_effect):
            run = _run_monday(episode, _body())

    # The stage must not report success.
    assert run.result is None
    assert run.error is not None and run.error.status_code == 500

    # And it must not have left a completed Monday behind.
    assert episode["stages"].get("monday", {}).get("status") == "failed"
    assert episode["stages"]["monday"].get("recipe_data") is None

    # Nothing expensive ran.
    run.orchestrator.assert_not_called()
    run.dialogue.assert_not_called()

    # The failure is loud.
    run.notify.assert_called_once()
    assert run.notify.call_args.kwargs["stage"] == "monday"


def test_empty_pick_is_a_failure_not_a_placeholder() -> None:
    """No candidate is a hard stop — never a fallback to the placeholder."""
    episode = _fresh_episode()
    with patch("scripts.pick_concept.pick_target_category", return_value="Sweet"), \
         patch("scripts.pick_concept.pick_concept", return_value=[]):
        run = _run_monday(episode, _body())

    assert run.error is not None and run.error.status_code == 500
    assert episode["stages"]["monday"]["status"] == "failed"
    # The week is left with no recipe rather than one built on the placeholder.
    assert "recipe_data" not in episode["stages"]["monday"]
    run.notify.assert_called_once()


def test_placeholder_concept_is_never_accepted_as_a_pick() -> None:
    """A picker that literally returns the placeholder is still a failure."""
    with patch("scripts.pick_concept.pick_target_category", return_value="Sweet"), \
         patch(
             "scripts.pick_concept.pick_concept",
             return_value=[cron_routes.PLACEHOLDER_CONCEPT],
         ):
        with pytest.raises(cron_routes.ConceptSelectionError):
            cron_routes._pick_weekly_concept()


def test_pick_is_retried_before_giving_up() -> None:
    """A transient throw on the first attempt must not sink the week."""
    with patch("scripts.pick_concept.pick_target_category", return_value="Sweet"), \
         patch(
             "scripts.pick_concept.pick_concept",
             side_effect=[RuntimeError("429"), ["Portuguese Custard Tarts"]],
         ):
        concept, category = cron_routes._pick_weekly_concept()

    assert concept == "Portuguese Custard Tarts"
    assert category == "Sweet"


# ---------------------------------------------------------------------------
# 2. Concept precedence — the second W36 bug
# ---------------------------------------------------------------------------

def test_stored_placeholder_does_not_block_a_fresh_pick() -> None:
    """The bug: `body.concept or ep.get("concept") or concept`.

    An episode carrying the placeholder would hand that placeholder back and
    discard the concept we had just picked, so re-firing Monday on a degraded
    week could never repair it.
    """
    episode = _fresh_episode()  # concept == PLACEHOLDER_CONCEPT
    with patch.object(
        cron_routes, "_pick_weekly_concept", return_value=("Miso Corn Tartlets", "Savory")
    ):
        concept, category = cron_routes._resolve_monday_concept(
            cron_routes.StageRequest(episode_id="2026-W36"), episode
        )

    assert concept == "Miso Corn Tartlets"
    assert category == "Savory"


def test_in_progress_week_keeps_its_stored_concept() -> None:
    """The normal path: a re-fire mid-week must not swap the dish."""
    episode = _fresh_episode()
    episode["concept"] = "Portuguese Custard Tarts"
    episode["target_category"] = "Sweet"

    with patch.object(cron_routes, "_pick_weekly_concept") as picker:
        concept, category = cron_routes._resolve_monday_concept(
            cron_routes.StageRequest(episode_id="2026-W36"), episode
        )

    picker.assert_not_called()
    assert concept == "Portuguese Custard Tarts"
    assert category == "Sweet"


def test_force_re_picks_even_with_a_real_stored_concept() -> None:
    """An explicit re-run is allowed to choose a new dish."""
    episode = _fresh_episode()
    episode["concept"] = "Portuguese Custard Tarts"

    with patch.object(
        cron_routes, "_pick_weekly_concept", return_value=("Miso Corn Tartlets", "Savory")
    ):
        concept, _ = cron_routes._resolve_monday_concept(
            cron_routes.StageRequest(episode_id="2026-W36", force=True), episode
        )

    assert concept == "Miso Corn Tartlets"


def test_explicit_concept_always_wins() -> None:
    episode = _fresh_episode()
    episode["concept"] = "Portuguese Custard Tarts"

    with patch.object(cron_routes, "_pick_weekly_concept") as picker, \
         patch("scripts.pick_concept.pick_target_category", return_value="Sweet"):
        concept, _ = cron_routes._resolve_monday_concept(
            cron_routes.StageRequest(episode_id="2026-W36", concept="Gochujang Pork Bites"),
            episode,
        )

    picker.assert_not_called()
    assert concept == "Gochujang Pork Bites"


def test_explicit_concept_still_picks_a_target_category() -> None:
    """The RUNBOOK's own INCIDENT 4 recovery command must leave a clean week.

    The recovery path passes an explicit concept, which skips the concept
    picker. If it skipped the CATEGORY picker too, target_category would stay
    None — and `episode_integrity_failures` reads a None target_category as
    "the picker never ran". The documented fix would permanently look
    degraded, and the check would cry wolf on its own recovery procedure.
    pick_target_category only reads the catalog, so it costs nothing to run.
    """
    episode = _fresh_episode()
    with patch("scripts.pick_concept.pick_target_category", return_value="Sweet") as picker:
        _concept, category = cron_routes._resolve_monday_concept(
            cron_routes.StageRequest(episode_id="2026-W36", concept="Gochujang Pork Bites"),
            episode,
        )

    picker.assert_called_once()
    assert category == "Sweet"


def test_explicit_concept_keeps_an_existing_category_without_re_picking() -> None:
    episode = _fresh_episode()
    episode["target_category"] = "Savory"
    with patch("scripts.pick_concept.pick_target_category") as picker:
        _concept, category = cron_routes._resolve_monday_concept(
            cron_routes.StageRequest(episode_id="2026-W36", concept="Gochujang Pork Bites"),
            episode,
        )

    picker.assert_not_called()
    assert category == "Savory"


def test_explicit_concept_survives_a_broken_category_picker() -> None:
    """Recovery must still work when the picker module is the thing that broke."""
    episode = _fresh_episode()
    with patch(
        "scripts.pick_concept.pick_target_category", side_effect=RuntimeError("blob down")
    ):
        concept, category = cron_routes._resolve_monday_concept(
            cron_routes.StageRequest(episode_id="2026-W36", concept="Gochujang Pork Bites"),
            episode,
        )

    assert concept == "Gochujang Pork Bites"
    assert category is None
