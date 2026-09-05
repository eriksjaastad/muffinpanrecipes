"""The fail-open handlers in cron_routes that were triaged as fail-closed (#6856).

Erik, 2026-09-05: "I thought we made all failures loud." The premise was
wrong — failures were made loud on specific paths (Sunday publish in PR #91,
the dialogue judge in PR #45, the Monday stage gate in PR #51) and everything
else still degraded to a logger.warning in a Lambda log nobody reads.

Each test below pins one handler that the audit moved from fail-open to
fail-closed. The handlers deliberately left non-fatal (QA scoring, auto-fix,
character memories, image-variant cleanup) carry their reasoning in a
`TRIAGE (#6856)` comment at the site instead.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request

from backend.admin import cron_routes


# ---------------------------------------------------------------------------
# Editorial QA must fail CLOSED (#6505)
# ---------------------------------------------------------------------------

def _reviewable_episode() -> dict:
    return {
        "episode_id": "2026-W36",
        "concept": "Portuguese Custard Tarts",
        "recipe_id": "abc12345",
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {
                    "title": "Portuguese Custard Tart Cups",
                    "description": (
                        "Self-contained custard tart cups baked in a muffin pan."
                    ),
                    "ingredients": [{"item": "puff pastry", "amount": "1 sheet"}],
                    "instructions": [
                        "Press a puff pastry round into each muffin cup to form "
                        "a shell.",
                        "Bake at 425F until the custard sets and the shells hold "
                        "their shape.",
                        "Cool 10 minutes, then lift each cup from the pan.",
                    ],
                    "chef_notes": "Serve warm.",
                },
            }
        },
        "events": [],
    }


def test_editorial_qa_fails_closed_when_the_reviewer_throws() -> None:
    """It used to return (True, 'defaulting to PASS') — publishing unreviewed."""
    with patch.object(cron_routes, "_recent_catalog_titles", return_value=["Something Else"]), \
         patch.object(
             cron_routes, "generate_judge_response",
             side_effect=RuntimeError("provider 503"),
         ):
        passed, report = cron_routes._editorial_qa_review(_reviewable_episode())

    assert passed is False
    assert cron_routes.QA_UNAVAILABLE_MARKER in report
    assert "STATUS: FAIL" in report


def test_editorial_qa_still_passes_a_clean_recipe() -> None:
    with patch.object(cron_routes, "_recent_catalog_titles", return_value=["Something Else"]), \
         patch.object(
             cron_routes, "generate_judge_response",
             return_value="STATUS: PASS\nISSUES: None\nRECOMMENDATION: Ship it.",
         ):
        passed, _report = cron_routes._editorial_qa_review(_reviewable_episode())

    assert passed is True


def test_missing_catalog_context_alerts_instead_of_passing_silently() -> None:
    """The `except Exception: pass` with no log line at all.

    When it fired, the QA prompt's title-repetition rule reviewed the recipe
    against an empty list of published titles and nothing said so.
    """
    with patch.object(cron_routes, "_recent_catalog_titles", return_value=[]), \
         patch.object(
             cron_routes, "generate_judge_response",
             return_value="STATUS: PASS\nISSUES: None\nRECOMMENDATION: Ship it.",
         ), \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        cron_routes._editorial_qa_review(_reviewable_episode())

    notify.assert_called_once()
    assert "DEGRADED" in notify.call_args.kwargs["error_message"]


def test_catalog_titles_fall_back_to_the_public_cdn_reader() -> None:
    """A blob read failure is survivable; a total blackout is reported empty."""
    with patch.object(
        cron_routes.storage, "load_page", side_effect=RuntimeError("blob down")
    ), patch(
        "backend.utils.title_validator.load_catalog_titles",
        return_value=["Spanakopita Phyllo Cups"],
    ):
        assert cron_routes._recent_catalog_titles() == ["Spanakopita Phyllo Cups"]

    with patch.object(
        cron_routes.storage, "load_page", side_effect=RuntimeError("blob down")
    ), patch(
        "backend.utils.title_validator.load_catalog_titles",
        side_effect=RuntimeError("cdn down"),
    ):
        assert cron_routes._recent_catalog_titles() == []


def test_catalog_titles_read_the_storage_layer_first() -> None:
    payload = json.dumps({"recipes": [{"title": "Kimchi Cheddar Rice Cups"}]})
    with patch.object(cron_routes.storage, "load_page", return_value=payload):
        assert cron_routes._recent_catalog_titles() == ["Kimchi Cheddar Rice Cups"]


# ---------------------------------------------------------------------------
# An empty dialogue is a stage failure, not a completed day
# ---------------------------------------------------------------------------

def test_empty_dialogue_raises_instead_of_completing_the_stage() -> None:
    """It used to return the sentinel verdict 'NO DIALOGUE GENERATED'.

    Handlers stored that verbatim and marked the stage `complete`: a day with
    zero turns, a green status and no alert anywhere.
    """
    episode = {"episode_id": "2026-W36", "stages": {}, "events": []}
    with patch.object(cron_routes, "_generate_dialogue", return_value=[]):
        with pytest.raises(RuntimeError, match="no messages"):
            cron_routes._generate_and_judge_dialogue("tuesday", "Custard Tarts", episode)


# ---------------------------------------------------------------------------
# A stage failure is now announced, not just written to blob
# ---------------------------------------------------------------------------

def test_stage_failure_notifies_discord() -> None:
    episode = {"episode_id": "2026-W36", "concept": "Custard Tarts", "recipe_id": "abc12345"}
    with patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        with pytest.raises(HTTPException) as exc_info:
            with cron_routes._run_stage(episode, "monday"):
                raise RuntimeError("baker exploded")

    assert exc_info.value.status_code == 500
    assert episode["stages"]["monday"]["status"] == "failed"
    notify.assert_called_once()
    assert notify.call_args.kwargs["stage"] == "monday"
    assert "baker exploded" in notify.call_args.kwargs["error_message"]


def test_explicit_http_errors_pass_through_run_stage_untouched() -> None:
    """_require_monday_recipe and the Sunday source-write already alerted."""
    episode = {"episode_id": "2026-W36", "stages": {}}
    with patch.object(cron_routes.storage, "save_episode") as save, \
         patch.object(cron_routes, "notify_pipeline_failure") as notify:
        with pytest.raises(HTTPException) as exc_info:
            with cron_routes._run_stage(episode, "sunday"):
                raise HTTPException(status_code=409, detail="already handled")

    assert exc_info.value.status_code == 409
    save.assert_not_called()
    notify.assert_not_called()


# ---------------------------------------------------------------------------
# A malformed cron body is a 400, not a silent run with defaults
# ---------------------------------------------------------------------------

def _post(body: bytes) -> Request:
    async def _body() -> bytes:
        return body

    return cast(Request, SimpleNamespace(method="POST", body=_body))


def test_bodyless_post_still_gets_defaults() -> None:
    parsed = asyncio.run(cron_routes._parse_body(_post(b"")))
    assert parsed.episode_id is None
    assert parsed.force is False


def test_malformed_post_body_is_rejected() -> None:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cron_routes._parse_body(_post(b'{"episode_id": "2026-W36"')))
    assert exc_info.value.status_code == 400


def test_unknown_field_in_post_body_is_rejected() -> None:
    """A typo'd key used to be swallowed into an all-defaults run."""
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(cron_routes._parse_body(_post(b'{"episode_id": ["not", "a", "string"]}')))
    assert exc_info.value.status_code == 400


def test_valid_post_body_is_parsed() -> None:
    parsed = asyncio.run(
        cron_routes._parse_body(_post(b'{"episode_id": "2026-W36", "force": true}'))
    )
    assert parsed.episode_id == "2026-W36"
    assert parsed.force is True


def test_get_requests_never_read_a_body() -> None:
    """Vercel crons send GET with no body at all."""
    request = cast(Request, SimpleNamespace(method="GET"))
    parsed = asyncio.run(cron_routes._parse_body(request))
    assert parsed.episode_id is None
