"""Monday's baker gates run together on every attempt and fail closed (#6854, #6858).

W36 (2026-08-31) shipped "Greek Spanakopita Cups" against the live
"Spanakopita Phyllo Cups": a different title, the same dish. The title gate
cannot see that class of duplicate, so Monday now also scores the baker's
ingredient list against the catalog — and the three gates (title, muffin-pan
form, ingredients) run as one loop so a retry that satisfies one cannot slip
past another. The category the week was picked for is stamped on the recipe,
because the baker's own CATEGORY: line filed W36's custard tart under Savory
(#6877).

Everything expensive is stubbed; the catalog is injected. No network.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.admin import cron_routes
from backend.utils.catalog import CatalogUnavailableError

# 12 items each — comfortably above recipe_overlap.MIN_ITEMS on both sides.
SPANAKOPITA = [
    "phyllo dough", "spinach", "feta cheese", "parmesan cheese", "fresh dill",
    "parsley", "scallions", "garlic", "nutmeg", "eggs", "olive oil", "kosher salt",
]
NATA = [
    "puff pastry", "granulated sugar", "water", "cinnamon stick", "lemon peel",
    "whole milk", "all-purpose flour", "egg yolks", "vanilla extract",
    "heavy cream", "cornstarch", "unsalted butter",
]


def _recipe(title: str, items: list[str], *, category: str = "savory") -> dict:
    return {
        "title": title,
        "description": f"{title}: self-contained cups that hold their shape.",
        "category": category,
        "cuisine": "Portuguese",
        "ingredients": [{"item": it, "amount": "1 cup", "notes": ""} for it in items],
        "instructions": [
            "Press the base into each muffin cup.",
            "Bake until set and the cups hold together.",
            "Cool 5 minutes, then release each cup with a thin spatula.",
        ],
        "chef_notes": "",
    }


def _catalog_entry(title: str, items: list[str], *, episode_id: str = "2026-W28") -> dict:
    return {
        "slug": title.lower().replace(" ", "-"),
        "title": title,
        "episode_id": episode_id,
        "category": "Savory",
        "cuisine": "Greek",
        "ingredients": [f"1 cup {it}" for it in items],
        "instructions": ["Bake."],
    }


SPANAKOPITA_CATALOG = {"recipes": [_catalog_entry("Spanakopita Phyllo Cups", SPANAKOPITA)]}


def _run_monday(recipes: list[dict], *, body: dict | None = None, catalog=None, loader_side_effect=None):
    """Drive cron_monday with a scripted baker and an injected catalog."""
    baker_calls: list[tuple[str, dict]] = []
    queue = list(recipes)

    class FakePipeline:
        def start_recipe(self, *_a, **_k):
            return None

    class FakeOrchestrator:
        def __init__(self, *_a, **_k):
            self.pipeline = FakePipeline()

        def _execute_stage_baker(self, _recipe_id, concept, **kwargs):
            baker_calls.append((concept, kwargs))
            assert queue, "baker called more times than recipes were scripted"
            return queue.pop(0)

    episode = {
        "episode_id": "2026-W99", "concept": cron_routes.PLACEHOLDER_CONCEPT,
        "stages": {}, "events": [], "recipe_id": None,
    }
    body_fields = {"concept": "Pastel de Nata Cups", "target_category": "Sweet"} if body is None else body
    request_body = cron_routes.StageRequest(episode_id="2026-W99", force=True, **body_fields)
    request = SimpleNamespace(url=SimpleNamespace(path="/api/cron/monday"))
    loader_kwargs = (
        {"side_effect": loader_side_effect} if loader_side_effect
        else {"return_value": {"recipes": []} if catalog is None else catalog}
    )

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=request_body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes, "_test_mode_scope", return_value=nullcontext()), \
         patch.object(cron_routes, "_load_or_create_episode", return_value=episode), \
         patch.object(cron_routes, "_get_orchestrator", return_value=FakeOrchestrator), \
         patch.object(cron_routes, "load_published_catalog", **loader_kwargs), \
         patch("backend.utils.title_validator.load_recent_cuisines", return_value=[]), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Margaret", "message": "These hold together."}], "PASS",
         )), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        try:
            result = asyncio.run(cron_routes.cron_monday(request))
            error = None
        except HTTPException as exc:
            result, error = None, exc

    return SimpleNamespace(
        result=result, error=error, episode=episode, baker_calls=baker_calls,
        save_episode=save_episode, notify=notify,
    )


# ---------------------------------------------------------------------------
# Ingredient gate (#6854)
# ---------------------------------------------------------------------------

def test_same_dish_under_a_new_name_triggers_one_retry_naming_the_match() -> None:
    """The W36 shape: title clears, ingredients do not."""
    duplicate = _recipe("Greek Spinach Pastry Cups", SPANAKOPITA[:11] + ["lemon zest"])
    fresh = _recipe("Pastel de Nata Cups", NATA)

    run = _run_monday([duplicate, fresh], catalog=SPANAKOPITA_CATALOG)

    assert run.error is None, run.error
    assert len(run.baker_calls) == 2
    retry_concept = run.baker_calls[1][0]
    assert "same dish as the already-published 'Spanakopita Phyllo Cups'" in retry_concept
    assert "genuinely different dish" in retry_concept

    monday = run.episode["stages"]["monday"]
    assert monday["status"] == "complete"
    assert monday["recipe_data"]["title"] == "Pastel de Nata Cups"
    # The stage carries the evidence that the gate ran and what it saw.
    trace = monday["gate_trace"]
    assert [t["gate"] for t in trace] == ["ingredients", "ingredients"]
    assert trace[0]["status"] == "duplicate"
    assert trace[0]["closest"] == "Spanakopita Phyllo Cups"
    assert trace[0]["score"] >= 0.80
    assert trace[1]["status"] == "clear"
    run.notify.assert_not_called()


def test_three_duplicate_attempts_fail_monday_closed() -> None:
    attempts = [
        _recipe(f"Greek Spinach Pastry Cups {n}", SPANAKOPITA[:11] + [f"extra {n}"])
        for n in ("One", "Two", "Three")
    ]

    run = _run_monday(attempts, catalog=SPANAKOPITA_CATALOG)

    assert run.result is None
    assert run.error is not None and run.error.status_code == 500
    assert "could not clear the Monday gates in 3 attempts" in run.error.detail
    assert "Spanakopita Phyllo Cups" in run.error.detail
    assert len(run.baker_calls) == 3
    assert run.episode["stages"]["monday"]["status"] == "failed"
    assert run.episode["stages"]["monday"].get("recipe_data") is None
    run.notify.assert_called_once()
    assert run.notify.call_args.kwargs["stage"] == "monday"


def test_thin_recipe_is_recorded_as_skipped_not_silently_passed() -> None:
    thin = _recipe("Pastel de Nata Cups", NATA[:6])

    run = _run_monday([thin], catalog=SPANAKOPITA_CATALOG)

    assert run.error is None, run.error
    trace = run.episode["stages"]["monday"]["gate_trace"]
    assert trace == [{
        "attempt": 1, "gate": "ingredients", "status": "skipped_thin",
        "new_items": 6, "closest": None, "score": None,
    }]


def test_own_catalog_entry_is_excluded_when_re_running_a_published_week() -> None:
    """Re-firing Monday on a week already in the catalog must not self-collide."""
    catalog = {"recipes": [_catalog_entry("Pastel de Nata Cups", NATA, episode_id="2026-W99")]}

    run = _run_monday([_recipe("Pastel de Nata Cups", NATA)], catalog=catalog)

    # Title gate: exact title match IS a conflict, so this recipe retries on the
    # title first — give it a fresh title to isolate the ingredient exclusion.
    assert run.error is not None  # exact-title conflict, expected
    run = _run_monday([_recipe("Portuguese Custard Cups", NATA)], catalog=catalog)
    assert run.error is None, run.error
    assert run.episode["stages"]["monday"]["gate_trace"][0]["status"] == "clear"


# ---------------------------------------------------------------------------
# One loop for every gate
# ---------------------------------------------------------------------------

def test_title_conflict_then_clean_recipe_passes_and_is_traced() -> None:
    catalog = {"recipes": [_catalog_entry("Pastel de Nata Cups", SPANAKOPITA)]}
    colliding = _recipe("Pastel de Nata Cups", NATA)         # exact title match
    clean = _recipe("Portuguese Custard Cups", NATA)

    run = _run_monday([colliding, clean], catalog=catalog)

    assert run.error is None, run.error
    assert "must NOT be similar to any of these already-published recipes" in run.baker_calls[1][0]
    trace = run.episode["stages"]["monday"]["gate_trace"]
    assert trace[0]["gate"] == "title" and "exact match" in trace[0]["result"]
    # A clear verdict still names the nearest neighbour and its score, so the
    # episode JSON shows how close the week came, not just that it passed.
    assert trace[1]["attempt"] == 2 and trace[1]["gate"] == "ingredients"
    assert trace[1]["status"] == "clear"
    assert trace[1]["closest"] == "Pastel de Nata Cups"
    assert trace[1]["score"] < 0.80


def test_every_gate_runs_on_the_retry_too() -> None:
    """A retry that fixes the title but produces a duplicate dish is still caught."""
    catalog = {"recipes": [
        _catalog_entry("Spanakopita Phyllo Cups", SPANAKOPITA),
        _catalog_entry("Pastel de Nata Cups", NATA[:10] + ["saffron", "orange zest"]),
    ]}
    title_clash = _recipe("Pastel de Nata Cups", SPANAKOPITA[:11] + ["lemon zest"])
    dish_clash = _recipe("Greek Spinach Pastry Cups", SPANAKOPITA[:11] + ["lemon zest"])
    clean = _recipe("Cardamom Rice Pudding Cups", [
        "basmati rice", "coconut milk", "cardamom", "jaggery", "pistachios",
        "rose water", "ghee", "raisins", "saffron", "condensed milk", "almonds", "salt",
    ])

    run = _run_monday([title_clash, dish_clash, clean], catalog=catalog)

    assert run.error is None, run.error
    assert [t["gate"] for t in run.episode["stages"]["monday"]["gate_trace"]] == [
        "title", "ingredients", "ingredients",
    ]
    assert run.episode["stages"]["monday"]["recipe_data"]["title"] == "Cardamom Rice Pudding Cups"


# ---------------------------------------------------------------------------
# Catalog must be readable — fail before spending (#6854, #6858)
# ---------------------------------------------------------------------------

def test_unreadable_catalog_fails_closed_before_the_baker_runs() -> None:
    run = _run_monday([_recipe("Pastel de Nata Cups", NATA)],
                      loader_side_effect=CatalogUnavailableError("CDN down"))

    assert run.result is None
    assert run.error is not None and run.error.status_code == 500
    assert "CDN down" in run.error.detail
    assert run.baker_calls == []
    assert run.episode["stages"]["monday"]["status"] == "failed"
    run.notify.assert_called_once()


# ---------------------------------------------------------------------------
# Category enforcement (#6858, #6877)
# ---------------------------------------------------------------------------

def test_target_category_overrides_the_bakers_own_label() -> None:
    """W36's custard tart came back 'savory' and was shelved there."""
    run = _run_monday([_recipe("Pastel de Nata Cups", NATA, category="savory")],
                      body={"concept": "Pastel de Nata Cups", "target_category": "Sweet"})

    assert run.error is None, run.error
    monday = run.episode["stages"]["monday"]
    assert monday["recipe_data"]["category"] == "sweet"
    assert monday["target_category"] == "Sweet"
    assert run.episode["target_category"] == "Sweet"
    assert run.result["target_category"] == "Sweet"
    # The baker was told the target too.
    assert run.baker_calls[0][1]["target_category"] == "Sweet"


def test_explicit_concept_without_a_category_adopts_the_bakers_label() -> None:
    """No target → no override, but target_category is still written so the
    week does not read as degraded (episode_integrity treats null as 'the
    picker never ran')."""
    run = _run_monday([_recipe("Pastel de Nata Cups", NATA, category="sweet")],
                      body={"concept": "Pastel de Nata Cups"})

    assert run.error is None, run.error
    monday = run.episode["stages"]["monday"]
    assert monday["recipe_data"]["category"] == "sweet"
    assert monday["target_category"] == "Sweet"
    assert run.episode["target_category"] == "Sweet"
    assert run.baker_calls[0][1]["target_category"] is None


def test_picked_category_is_enforced_when_the_picker_chooses() -> None:
    with patch.object(cron_routes, "_pick_weekly_concept", return_value=("Pastel de Nata Cups", "Party")):
        run = _run_monday([_recipe("Pastel de Nata Cups", NATA, category="sweet")], body={})

    assert run.error is None, run.error
    assert run.episode["stages"]["monday"]["recipe_data"]["category"] == "party"
    assert run.episode["target_category"] == "Party"


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["Dessert", "sweets", "", " "])
def test_unknown_target_category_is_a_400(value: str) -> None:
    request = SimpleNamespace(
        method="POST",
        body=AsyncMock(return_value=json.dumps({"concept": "x", "target_category": value}).encode()),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(cron_routes._parse_body(request))
    assert exc.value.status_code == 400
    assert "target_category" in exc.value.detail


@pytest.mark.parametrize("value", ["Sweet", "sweet", "PARTY"])
def test_known_target_category_is_accepted_in_any_case(value: str) -> None:
    request = SimpleNamespace(
        method="POST",
        body=AsyncMock(return_value=json.dumps({"concept": "x", "target_category": value}).encode()),
    )
    body = asyncio.run(cron_routes._parse_body(request))
    assert body.target_category == value  # normalised later, in the resolver
