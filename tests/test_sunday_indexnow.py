"""IndexNow submission from the Sunday publish hook (#7806).

Two things matter here beyond the happy path already covered in
test_sunday_publish_idempotency.py: a test-mode Sunday must make ZERO
IndexNow calls (RUNBOOK Incident 1 was exactly this shape of leak — test data
reaching a real destination), and a submission failure must never raise into
the publish path. No network: `_indexnow_submit_urls` is mocked throughout.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.admin import cron_routes
from backend.utils.indexnow import IndexNowResult
from tests.photo_review_helpers import approved_wednesday


@pytest.fixture(autouse=True)
def _test_namespace_as_on_cloud(monkeypatch):
    """Model cloud storage, where the test prefix separates episodes too.

    Local storage refuses a test-namespace photo review (#7936); here the
    episode reads are mocked per run, so the test namespace stands in for
    cloud's prefixed episodes.
    """
    from backend.storage import _FilesystemBackend

    monkeypatch.setattr(_FilesystemBackend, "namespaces_episodes", lambda self: True)


def _request() -> SimpleNamespace:
    return SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/cron/sunday"))


def _episode(prefix: str = "") -> dict:
    return {
        "episode_id": "2026-W20",
        "concept": "Herbed Sausage Sunrise Cups",
        "recipe_id": "e9f30301",
        "stages": {
            "monday": {
                "status": "complete",
                "recipe_data": {
                    "title": "Herbed Sausage Sunrise Cups",
                    "description": "A savory breakfast bite.",
                    "ingredients": [{"amount": "2", "item": "eggs"}],
                    "instructions": ["Whisk and bake."],
                },
            },
            "wednesday": approved_wednesday(episode_id="2026-W20", prefix=prefix),
        },
        "events": [],
        "image_urls": [],
    }


def _run_sunday(body: cron_routes.StageRequest, indexnow_result: IndexNowResult):
    """Drive cron_sunday through the full first-time-publish path."""
    episode = _episode("test/" if body.test else "")

    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode"), \
         patch.object(cron_routes.storage, "save_page"), \
         patch.object(cron_routes, "_generate_and_judge_dialogue", return_value=(
             [{"character": "Devon Park", "message": "Ready."}],
             "PASS - ready",
         )), \
         patch.object(cron_routes, "_editorial_qa_review", return_value=(True, "STATUS: PASS")), \
         patch.object(cron_routes, "_generate_episode_memories"), \
         patch.object(cron_routes, "regenerate_and_upload"), \
         patch.object(cron_routes, "_indexnow_submit_urls", return_value=indexnow_result) as indexnow_submit, \
         patch("backend.publishing.episode_renderer.publish_recipe_to_catalog"), \
         patch("backend.publishing.episode_renderer.render_episode_page", return_value="<html></html>"):
        result = asyncio.run(cron_routes.cron_sunday(_request()))

    return result, episode, indexnow_submit


def test_test_mode_sunday_makes_zero_indexnow_calls(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=True)

    result, episode, indexnow_submit = _run_sunday(
        body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )

    assert result["published"] is True
    indexnow_submit.assert_not_called()
    assert not any("indexnow" in event for event in episode["events"])


def test_production_sunday_submits_indexnow(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=False)

    result, episode, indexnow_submit = _run_sunday(
        body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )

    assert result["published"] is True
    indexnow_submit.assert_called_once_with([
        "https://muffinpanrecipes.com/recipes/herbed-sausage-sunrise-cups",
        "https://muffinpanrecipes.com/",
        "https://muffinpanrecipes.com/recipes",
    ])
    assert any("indexnow submitted" in event for event in episode["events"])


def test_indexnow_failure_is_recorded_and_never_raises(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=False)

    result, episode, indexnow_submit = _run_sunday(
        body, IndexNowResult(ok=False, status_code=429, detail="IndexNow returned HTTP 429: rate limited")
    )

    # The publish itself must succeed regardless of the IndexNow outcome.
    assert result["published"] is True
    indexnow_submit.assert_called_once()
    assert any("indexnow submission failed" in event for event in episode["events"])


def test_submitted_recipe_url_uses_the_catalog_slug_for_a_qualified_title():
    """The catalog strips a trailing parenthetical before slugifying; the
    IndexNow URL must be that same page, not a slug of the raw title that
    would 404."""
    from backend.publishing import episode_renderer

    episode = {
        "stages": {"monday": {"recipe_data": {
            "title": "Make-Ahead Veggie & Sausage Egg Cups (Weekly Muffin Pan Breakfast)",
        }}},
        "events": [],
    }
    captured = {}

    def fake_submit(urls):
        captured["urls"] = urls
        return IndexNowResult(ok=True, status_code=200, detail="submitted")

    with patch.object(cron_routes, "_indexnow_submit_urls", side_effect=fake_submit), \
         patch.object(cron_routes.storage, "save_episode"):
        cron_routes._submit_sunday_indexnow(episode, "2026-W20", "c")

    slug = episode_renderer.catalog_slug(episode)
    assert slug == episode_renderer._slugify("Make-Ahead Veggie & Sausage Egg Cups")
    assert captured["urls"][0] == f"https://muffinpanrecipes.com/recipes/{slug}"


def _published_episode() -> dict:
    return {
        "stages": {"monday": {"recipe_data": {"title": "Herbed Sausage Sunrise Cups"}}},
        "events": [],
    }


def test_an_error_deriving_the_slug_never_raises_and_is_recorded():
    """Codex round 1: the slug was derived outside the guard, so an error
    there reached _run_stage and marked a live publish failed."""
    episode = _published_episode()
    with patch("backend.publishing.episode_renderer.catalog_slug", side_effect=KeyError("boom")), \
         patch.object(cron_routes, "_indexnow_submit_urls") as submit, \
         patch.object(cron_routes.storage, "save_episode") as save_episode:
        cron_routes._submit_sunday_indexnow(episode, "2026-W20", "c")  # must not raise

    submit.assert_not_called()
    assert episode["events"] == ["sunday: indexnow submission failed (KeyError)"]
    save_episode.assert_called_once_with("2026-W20", episode)


def test_an_unexpected_submission_error_is_persisted_not_just_appended():
    """Codex round 1: the exception branch returned before save_episode."""
    episode = _published_episode()
    with patch.object(cron_routes, "_indexnow_submit_urls", side_effect=OSError("socket")), \
         patch.object(cron_routes.storage, "save_episode") as save_episode:
        cron_routes._submit_sunday_indexnow(episode, "2026-W20", "c")

    assert episode["events"] == ["sunday: indexnow submission failed (OSError)"]
    save_episode.assert_called_once_with("2026-W20", episode)


def test_a_malformed_result_object_never_raises():
    episode = _published_episode()
    with patch.object(cron_routes, "_indexnow_submit_urls", return_value=object()), \
         patch.object(cron_routes.storage, "save_episode") as save_episode:
        cron_routes._submit_sunday_indexnow(episode, "2026-W20", "c")

    assert episode["events"] == ["sunday: indexnow submission failed (AttributeError)"]
    save_episode.assert_called_once()


def test_the_network_guard_cannot_be_swallowed_by_the_hook():
    """Codex round 1: the conftest guard raised RuntimeError, which the
    hook's `except Exception` turned into a recorded failure, so a test that
    forgot its mock still passed. pytest.fail raises a BaseException."""
    import pytest

    from backend.utils import indexnow

    with pytest.raises(pytest.fail.Exception) as excinfo:
        indexnow.requests.post("https://api.indexnow.org/indexnow", json={})
    assert not isinstance(excinfo.value, Exception)


def _run_already_published(episode: dict, body: cron_routes.StageRequest, indexnow_result: IndexNowResult):
    """Drive cron_sunday through the already-published fast path."""
    with patch.object(cron_routes, "_verify_cron_secret"), \
         patch.object(cron_routes, "_parse_body", new=AsyncMock(return_value=body)), \
         patch.object(cron_routes, "_verify_day_of_week"), \
         patch.object(cron_routes.storage, "load_episode", return_value=episode), \
         patch.object(cron_routes.storage, "save_episode") as save_episode, \
         patch.object(cron_routes, "_announce_advisory_publication"), \
         patch.object(cron_routes, "_indexnow_submit_urls", return_value=indexnow_result) as submit:
        result = asyncio.run(cron_routes.cron_sunday(_request()))
    return result, submit, save_episode


def _published(**extra) -> dict:
    episode = _episode()
    episode["published_at"] = "2026-05-17T23:00:00+00:00"
    episode.update(extra)
    return episode


def test_first_publish_saves_the_submission_as_owed_then_settles_it(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=False)
    _result, episode, _submit = _run_sunday(
        body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )
    assert episode["indexnow_pending"] is False


def test_resumed_sunday_retries_an_owed_submission(monkeypatch):
    """Codex round 2: a run that saved published_at but died before the
    IndexNow outcome was persisted never submitted on the resumed run."""
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    episode = _published(indexnow_pending=True)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=False)

    result, submit, save_episode = _run_already_published(
        episode, body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )

    assert result["already_published"] is True
    submit.assert_called_once()
    saved = save_episode.call_args.args[1]
    assert saved["indexnow_pending"] is False
    assert "sunday: indexnow submitted (3 urls)" in saved["events"]


@pytest.mark.parametrize("extra", [{}, {"indexnow_pending": False}], ids=["pre-flag-record", "settled"])
def test_resumed_sunday_does_not_resubmit_a_settled_or_legacy_record(monkeypatch, extra):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=False)
    _result, submit, _save = _run_already_published(
        _published(**extra), body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )
    submit.assert_not_called()


def test_resumed_test_mode_sunday_never_submits(monkeypatch):
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    body = cron_routes.StageRequest(episode_id="2026-W20", force=True, test=True)
    _result, submit, _save = _run_already_published(
        _published(indexnow_pending=True), body, IndexNowResult(ok=True, status_code=200, detail="submitted")
    )
    submit.assert_not_called()


def test_a_failed_outcome_save_leaves_the_submission_owed():
    """Codex round 2: a failed save left the outcome only in memory and the
    catch-up never retried it. Now the episode is untouched on a failed
    save, so it stays owed in memory and in Blob."""
    episode = _published(indexnow_pending=True)
    with patch.object(cron_routes, "_indexnow_submit_urls",
                      return_value=IndexNowResult(ok=True, status_code=200, detail="submitted")), \
         patch.object(cron_routes.storage, "save_episode", side_effect=OSError("blob down")):
        cron_routes._submit_sunday_indexnow(episode, "2026-W20", "c")

    assert episode["indexnow_pending"] is True
    assert not any("indexnow" in event for event in episode["events"])
