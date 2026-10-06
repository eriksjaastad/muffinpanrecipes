"""#7853: Marcus's Thursday intro is the published description.

Margaret's Monday one-liner becomes the internal pitch (dialogue, judge and
form-gate context, never published); Marcus writes the description on
Thursday; nothing canned ever publishes in its place.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.admin import cron_routes
from backend.utils import recipe_copy
from backend.utils.recipe_copy import (
    INTRO_PLACEHOLDER,
    IntroError,
    generate_intro,
    intro_problems,
    opener_word,
    recent_openers,
    recipe_pitch,
)

@pytest.fixture(autouse=True)
def _recipe_model(monkeypatch):
    """config.recipe_model refuses to guess a model; the tests name one."""
    monkeypatch.setenv("RECIPE_MODEL", "openai/test-model")


PITCH = "These pistachio spirals bake into tidy, caramel-edged cups."
INTRO = (
    "Feather-soft spirals pull apart in buttery layers, with floral cardamom and toasted "
    "pistachio landing first. The muffin pan keeps every swirl tidy and caramel-edged, "
    "ready for a brunch platter or an afternoon coffee."
)


def _catalog(openers: list[str]) -> list[dict]:
    """Newest first, like the live catalog; plus two seed recipes with no episode_id."""
    weekly = [
        {"episode_id": f"2026-W{40 - i:02d}", "description": f"{word} something tasty."}
        for i, word in enumerate(openers)
    ]
    return weekly + [{"description": "Seed recipe text."}, {"description": "Another seed."}]


# --- opener ban -------------------------------------------------------------------

def test_recent_openers_reads_the_last_ten_weekly_recipes_newest_first():
    openers = ["These"] * 3 + ["Warm", "Golden", "Crisp", "Tender", "Smoky", "Bright", "Silky", "Old", "Older"]
    shuffled = list(reversed(_catalog(openers)))  # order in the file must not matter
    banned = recent_openers(shuffled)
    assert banned == sorted({"these", "warm", "golden", "crisp", "tender", "smoky", "bright", "silky"})
    assert "old" not in banned and "seed" not in banned  # 11th+ week and seeds are not recent


def test_the_rolling_ban_keeps_every_opener_to_at_most_two_of_the_last_ten():
    """The card's AC, simulated over 30 weeks: each week bans the openers of
    the 10 before it, so no opener can recur inside a 10-week window."""
    pool = ["warm", "golden", "crisp", "tender", "smoky", "bright", "silky", "flaky",
            "buttery", "savory", "zesty", "fluffy", "creamy", "crunchy"]
    published: list[str] = []
    for week in range(30):
        banned = set(recent_openers([
            {"episode_id": f"2026-W{w + 1:02d}", "description": f"{o} bites."}
            for w, o in enumerate(published)
        ]))
        published.append(next(o for o in pool if o not in banned))
        window = published[-10:]
        assert max(window.count(o) for o in window) <= 2


def test_opener_word_is_case_and_punctuation_insensitive():
    assert opener_word('"These" cups') == "these"
    assert opener_word("  Warm, puffy bites") == "warm"
    assert opener_word("") == ""


# --- intro rules and generation ------------------------------------------------------

def test_intro_problems_names_each_rule_broken():
    assert intro_problems(INTRO, ["these"]) == []
    assert intro_problems("", []) == ["the intro is empty"]
    assert "words" in intro_problems("Too short.", [])[0]
    assert any("feather-soft" in p for p in intro_problems(INTRO, ["feather-soft"]))


def test_generate_intro_returns_a_valid_intro_on_the_first_call():
    with patch("backend.utils.model_router.generate_response", return_value=INTRO) as call:
        assert generate_intro({"title": "Spiral Cups"}, ["these"]) == INTRO
    assert call.call_count == 1
    assert '"these"' in call.call_args.kwargs["system_prompt"]


def test_a_rule_breaking_intro_gets_one_retry_that_names_the_problem():
    bad = "These " + INTRO
    with patch("backend.utils.model_router.generate_response", side_effect=[bad, INTRO]) as call:
        assert generate_intro({"title": "Spiral Cups"}, ["these"]) == INTRO
    assert call.call_count == 2
    assert "broke a rule" in call.call_args_list[1].kwargs["prompt"]


def test_an_intro_still_breaking_the_rules_after_the_retry_raises():
    bad = "These " + INTRO
    with patch("backend.utils.model_router.generate_response", side_effect=[bad, bad]) as call, \
            pytest.raises(IntroError, match="after 2 attempts"):
        generate_intro({"title": "Spiral Cups"}, ["these"])
    assert call.call_count == recipe_copy.INTRO_ATTEMPTS


def test_a_failed_model_call_raises_and_never_substitutes_text():
    with patch("backend.utils.model_router.generate_response", side_effect=RuntimeError("503")), \
            pytest.raises(IntroError, match="503"):
        generate_intro({"title": "Spiral Cups"}, [])


def test_the_copywriter_fails_instead_of_returning_canned_copy():
    from backend.agents.factory import create_agent
    from backend.core.task import Task

    try:
        marcus = create_agent("copywriter")
    except Exception as exc:  # noqa: BLE001 - factory needs data files in some setups
        pytest.skip(f"copywriter agent unavailable here: {exc}")
    task = Task(type="write_description", content="Write description", context={"recipe_data": {"title": "X"}})
    with patch("backend.agents.copywriter.generate_description", side_effect=RuntimeError("down")):
        result = marcus.process_task(task)
    assert result.success is False
    assert "poetry" not in str(result.output)


# --- Monday pitch -------------------------------------------------------------------

def test_mondays_description_line_becomes_the_internal_pitch():
    from backend.utils.recipe_prompts import _parse_recipe_response

    parsed = _parse_recipe_response(f"TITLE: Spiral Cups\nDESCRIPTION: {PITCH}\n", "Spiral Cups")
    assert parsed["pitch"] == PITCH
    assert parsed["description"] == ""


def test_recipe_pitch_falls_back_to_a_pre_7853_description():
    assert recipe_pitch({"pitch": PITCH, "description": INTRO}) == PITCH
    assert recipe_pitch({"description": PITCH}) == PITCH
    assert recipe_pitch({}) == ""


def test_dialogue_judge_and_form_gate_read_the_pitch_not_the_intro():
    from backend.utils.muffin_pan_form import _flatten_recipe_text

    recipe = {"title": "Spiral Cups", "pitch": PITCH, "description": INTRO,
              "ingredients": [{"amount": "1", "item": "flour"}], "instructions": ["Bake."]}
    for text in (
        cron_routes._build_recipe_context(recipe),
        cron_routes._build_judge_recipe_facts(recipe),
        _flatten_recipe_text(recipe),
    ):
        assert "caramel-edged cups" in text
        assert "Feather-soft" not in text


# --- Thursday -------------------------------------------------------------------------

def _thursday(ep: dict, *, intro=INTRO, catalog=None):
    body = cron_routes.StageRequest(episode_id="2026-W41", force=True)
    saved: list[dict] = []
    gen = patch.object(cron_routes, "generate_intro", side_effect=intro) if isinstance(intro, Exception) \
        else patch.object(cron_routes, "generate_intro", return_value=intro)
    with patch.object(cron_routes, "_verify_cron_secret"), \
            patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
            patch.object(cron_routes, "_verify_day_of_week"), \
            patch.object(cron_routes, "_load_or_create_episode", return_value=ep), \
            patch.object(cron_routes.storage, "save_episode", side_effect=lambda _id, data: saved.append(data)), \
            patch.object(cron_routes, "regenerate_and_upload"), \
            patch.object(cron_routes, "_photo_review_only_context", return_value=""), \
            patch.object(cron_routes, "_generate_and_judge_dialogue",
                         return_value=([{"character": "Marcus Reid", "message": "Done."}], "PASS")), \
            patch.object(cron_routes, "load_published_catalog",
                         return_value={"recipes": catalog if catalog is not None else _catalog(["These"])}), \
            gen as generate:
        try:
            result = asyncio.run(cron_routes.cron_thursday(
                SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/cron/thursday"))
            ))
        except Exception as exc:  # noqa: BLE001 - asserted by the caller
            result = exc
    return result, generate, saved


def _week(recipe: dict) -> dict:
    return {"episode_id": "2026-W41", "concept": "Spiral Cups", "recipe_id": "r1", "events": [],
            "stages": {"monday": {"status": "complete", "recipe_data": recipe}}}


def test_thursday_publishes_marcus_intro_as_the_description_and_keeps_the_pitch():
    ep = _week({"title": "Spiral Cups", "pitch": PITCH, "description": ""})
    result, generate, saved = _thursday(ep)
    recipe = ep["stages"]["monday"]["recipe_data"]
    assert recipe["description"] == INTRO and recipe["pitch"] == PITCH
    assert ep["stages"]["thursday"]["copy_text"] == {"body": INTRO, "kind": "intro", "banned_openers": ["these"]}
    assert generate.call_args.args[1] == ["these"]
    assert saved and saved[-1]["stages"]["monday"]["recipe_data"]["description"] == INTRO


def test_a_week_from_before_7853_keeps_its_monday_text_as_the_pitch():
    ep = _week({"title": "Spiral Cups", "description": PITCH})
    _thursday(ep)
    recipe = ep["stages"]["monday"]["recipe_data"]
    assert recipe["pitch"] == PITCH and recipe["description"] == INTRO


def test_a_failed_intro_fails_thursday_and_publishes_nothing_in_its_place():
    ep = _week({"title": "Spiral Cups", "pitch": PITCH, "description": ""})
    result, _generate, _saved = _thursday(ep, intro=IntroError("model down"))
    # _run_stage records the failure, alerts, and surfaces it as a 500.
    assert getattr(result, "status_code", None) == 500 and "model down" in result.detail
    assert ep["stages"]["thursday"]["status"] == "failed"
    assert ep["stages"]["monday"]["recipe_data"]["description"] == ""


def test_an_unreadable_catalog_fails_thursday_before_any_paid_call():
    from backend.utils.catalog import CatalogUnavailableError

    ep = _week({"title": "Spiral Cups", "pitch": PITCH, "description": ""})
    with patch.object(cron_routes, "load_published_catalog", side_effect=CatalogUnavailableError("down")):
        body = cron_routes.StageRequest(episode_id="2026-W41", force=True)
        with patch.object(cron_routes, "_verify_cron_secret"), \
                patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
                patch.object(cron_routes, "_verify_day_of_week"), \
                patch.object(cron_routes, "_load_or_create_episode", return_value=ep), \
                patch.object(cron_routes.storage, "save_episode"), \
                patch.object(cron_routes, "generate_intro") as generate, \
                patch.object(cron_routes, "_generate_and_judge_dialogue") as dialogue, \
                pytest.raises(Exception) as raised:
            asyncio.run(cron_routes.cron_thursday(
                SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/cron/thursday"))
            ))
    assert getattr(raised.value, "status_code", None) == 500 and "down" in raised.value.detail
    assert ep["stages"]["thursday"]["status"] == "failed"
    generate.assert_not_called()
    dialogue.assert_not_called()


# --- Sunday ----------------------------------------------------------------------------

def test_sunday_refuses_a_week_without_an_intro_before_any_paid_work():
    from tests.test_photo_approval_7936 import EP_ID, _approved_store, _status, _sunday

    store, _ep, _wed = _approved_store()
    stored = store.get(EP_ID)
    stored["stages"]["monday"]["recipe_data"]["description"] = ""
    store.put(EP_ID, stored)
    env = _sunday(store)
    assert _status(env) == 400
    assert "Marcus's intro is missing" in env.error.detail
    env.dialogue.assert_not_called()
    env.qa.assert_not_called()
    assert not store.get(EP_ID).get("published_at")


# --- the page --------------------------------------------------------------------------

def test_the_page_shows_the_placeholder_until_thursday_and_never_once_published():
    from backend.publishing.episode_renderer import render_episode_page

    week = _week({"title": "Spiral Cups", "pitch": PITCH, "description": "",
                  "ingredients": [{"amount": "1", "item": "flour"}], "instructions": ["Bake."]})
    page = render_episode_page(week, catalog=[])
    assert INTRO_PLACEHOLDER in page
    assert "caramel-edged cups" not in page  # the pitch is never published

    week["stages"]["monday"]["recipe_data"]["description"] = INTRO
    page = render_episode_page(week, catalog=[])
    assert INTRO_PLACEHOLDER not in page and "Feather-soft spirals" in page

    week["stages"]["monday"]["recipe_data"]["description"] = ""
    week["published_at"] = "2026-10-11T23:00:00+00:00"
    assert INTRO_PLACEHOLDER not in render_episode_page(week, catalog=[])
