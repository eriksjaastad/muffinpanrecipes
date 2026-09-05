"""The degradation W36 hid behind seven green checks (#6857).

Acceptance criterion from the card: "reproduce W36 by hand (set concept to the
placeholder in a test episode) and confirm both the health check and the
session-start surface report it. The specific bug that motivated this must be
caught by the thing built to catch it."

The counter-pressure matters as much as the detection: health_check pings
Erik's Discord under `-c prd`, so a check that cries wolf trains him to ignore
the channel. The "healthy" cases below are the ones that keep it credible.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.utils.episode_integrity import (
    PLACEHOLDER_CONCEPT,
    current_episode_id,
    episode_integrity_failures,
    episode_summary,
    parse_episode_id,
    stage_deadline,
    stages_due,
)

# Saturday 2026-09-05, 17:00 UTC — the moment the W36 duplicate was found by
# hand, five days after the Monday that caused it.
SATURDAY_W36 = datetime(2026, 9, 5, 17, 0, tzinfo=timezone.utc)


def _healthy_episode(**overrides) -> dict:
    episode = {
        "episode_id": "2026-W36",
        "concept": "Portuguese Custard Tarts baked in a muffin pan",
        "target_category": "Sweet",
        "stages": {
            day: {"status": "complete"}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
        },
        "events": [],
    }
    episode["stages"]["monday"]["recipe_data"] = {"title": "Portuguese Custard Tart Cups"}
    episode["stages"]["monday"]["target_category"] = "Sweet"
    episode.update(overrides)
    return episode


CATALOG = [
    {"title": "Spanakopita Phyllo Cups", "slug": "spanakopita-phyllo-cups"},
    {"title": "Kimchi Cheddar Rice Cups", "slug": "kimchi-cheddar-rice-cups"},
    {"title": "Tandoori Chicken Naan Cups", "slug": "tandoori-chicken-naan-cups"},
]


# ---------------------------------------------------------------------------
# The W36 signature
# ---------------------------------------------------------------------------

def test_placeholder_concept_is_reported() -> None:
    """The single check that would have caught W36 on 2026-08-31."""
    episode = _healthy_episode(concept=PLACEHOLDER_CONCEPT)
    failures = episode_integrity_failures(episode, catalog=CATALOG, now=SATURDAY_W36)

    assert any(PLACEHOLDER_CONCEPT in f for f in failures), failures
    assert any("#6855" in f for f in failures)


def test_missing_target_category_is_reported() -> None:
    """The other half of the fingerprint, and what the live W36 still shows.

    W36's concept was repaired by hand on 2026-09-05, so the placeholder check
    passes on it — but target_category is only written on the concept
    picker's success path, so None still says the picker never ran.
    """
    episode = _healthy_episode()
    episode.pop("target_category")
    episode["stages"]["monday"].pop("target_category")
    failures = episode_integrity_failures(episode, catalog=CATALOG, now=SATURDAY_W36)

    assert any("target_category" in f for f in failures), failures


def test_the_full_w36_episode_shape_is_caught() -> None:
    """Everything green, a real title on the page, and still degraded."""
    episode = _healthy_episode(concept=PLACEHOLDER_CONCEPT)
    episode.pop("target_category")
    episode["stages"]["monday"].pop("target_category")
    episode["stages"]["monday"]["recipe_data"] = {"title": "Greek Spanakopita Cups"}

    failures = episode_integrity_failures(episode, catalog=CATALOG, now=SATURDAY_W36)

    # All six days say "complete" and the judge PASSed all of them.
    assert all(s["status"] == "complete" for s in episode["stages"].values())
    # And yet:
    assert len(failures) >= 2
    assert any(PLACEHOLDER_CONCEPT in f for f in failures)
    assert any("target_category" in f for f in failures)


# ---------------------------------------------------------------------------
# Stage completeness
# ---------------------------------------------------------------------------

def test_missing_stage_after_its_window_is_reported() -> None:
    episode = _healthy_episode()
    del episode["stages"]["wednesday"]
    failures = episode_integrity_failures(episode, catalog=CATALOG, now=SATURDAY_W36)

    assert any(f.startswith("wednesday stage is") for f in failures), failures


def test_failed_stage_surfaces_its_error() -> None:
    episode = _healthy_episode()
    episode["stages"]["monday"] = {
        "status": "failed",
        "error": "Baker produced duplicate title twice.",
    }
    failures = episode_integrity_failures(episode, catalog=CATALOG, now=SATURDAY_W36)

    assert any("Baker produced duplicate title twice" in f for f in failures), failures


def test_stage_still_inside_its_window_is_not_reported() -> None:
    """Do not alert on a cron that is mid-flight — that is how alerts die."""
    episode = _healthy_episode()
    del episode["stages"]["saturday"]
    # Saturday's cron fires at 14:30 UTC; 14:35 is inside the grace period.
    just_after_saturday_cron = datetime(2026, 9, 5, 14, 35, tzinfo=timezone.utc)
    failures = episode_integrity_failures(
        episode, catalog=CATALOG, now=just_after_saturday_cron
    )

    assert not any("saturday" in f for f in failures), failures


def test_sunday_is_not_due_before_the_week_ends() -> None:
    episode = _healthy_episode()
    failures = episode_integrity_failures(episode, catalog=CATALOG, now=SATURDAY_W36)

    assert not any("sunday" in f for f in failures), failures


# ---------------------------------------------------------------------------
# Title collision
# ---------------------------------------------------------------------------

def test_duplicate_title_against_the_catalog_is_reported() -> None:
    episode = _healthy_episode()
    episode["stages"]["monday"]["recipe_data"] = {"title": "Spanakopita Phyllo Cups"}
    failures = episode_integrity_failures(episode, catalog=CATALOG, now=SATURDAY_W36)

    assert any("collides with the published catalog" in f for f in failures), failures


def test_a_published_week_does_not_collide_with_its_own_catalog_entry() -> None:
    """Otherwise every published week would alert against itself."""
    episode = _healthy_episode(published_at="2026-09-06T00:05:00+00:00")
    catalog = CATALOG + [
        {
            "title": "Portuguese Custard Tart Cups",
            "slug": "portuguese-custard-tart-cups",
            "episode_id": "2026-W36",
        }
    ]
    failures = episode_integrity_failures(episode, catalog=catalog, now=SATURDAY_W36)

    assert not any("collides" in f for f in failures), failures


def test_self_exclusion_works_for_legacy_entries_without_episode_id() -> None:
    episode = _healthy_episode(published_at="2026-09-06T00:05:00+00:00")
    catalog = CATALOG + [{"title": "Portuguese Custard Tart Cups"}]
    failures = episode_integrity_failures(episode, catalog=catalog, now=SATURDAY_W36)

    assert not any("collides" in f for f in failures), failures


def test_an_unpublished_week_is_not_excused_by_an_identical_catalog_title() -> None:
    """The title fallback must not swallow the exact-duplicate rule.

    Before publish there is no catalog entry that could be this episode's
    own, so an identical title in the catalog is a real duplicate — the
    single most important thing to catch, and the one W36 needed.
    """
    episode = _healthy_episode()
    catalog = CATALOG + [{"title": "Portuguese Custard Tart Cups"}]
    failures = episode_integrity_failures(episode, catalog=catalog, now=SATURDAY_W36)

    assert any("collides with the published catalog" in f for f in failures), failures


def test_title_check_is_skipped_when_no_catalog_is_supplied() -> None:
    """A catalog fetch failure must not fabricate a duplicate alert."""
    episode = _healthy_episode()
    episode["stages"]["monday"]["recipe_data"] = {"title": "Spanakopita Phyllo Cups"}
    failures = episode_integrity_failures(episode, catalog=None, now=SATURDAY_W36)

    assert failures == []


# ---------------------------------------------------------------------------
# Quiet when healthy
# ---------------------------------------------------------------------------

def test_healthy_episode_reports_nothing() -> None:
    assert episode_integrity_failures(
        _healthy_episode(), catalog=CATALOG, now=SATURDAY_W36
    ) == []


def test_missing_episode_is_not_a_failure_here() -> None:
    """Pre-Monday, the current week has no episode. The caller decides."""
    assert episode_integrity_failures(None, catalog=CATALOG) == []


def test_early_monday_before_the_cron_reports_nothing() -> None:
    """Monday 09:00 UTC: the week exists but nothing is due yet."""
    episode = {"episode_id": "2026-W36", "concept": "Custard Tarts", "stages": {}}
    monday_morning = datetime(2026, 8, 31, 9, 0, tzinfo=timezone.utc)
    assert episode_integrity_failures(
        episode, catalog=CATALOG, now=monday_morning
    ) == []


# ---------------------------------------------------------------------------
# Schedule arithmetic
# ---------------------------------------------------------------------------

def test_stage_deadlines_match_vercel_json() -> None:
    assert stage_deadline("2026-W36", "monday") == datetime(
        2026, 8, 31, 14, 30, tzinfo=timezone.utc
    )
    assert stage_deadline("2026-W36", "sunday") == datetime(
        2026, 9, 6, 0, 0, tzinfo=timezone.utc
    )


def test_stages_due_grows_through_the_week() -> None:
    due = stages_due("2026-W36", now=SATURDAY_W36)
    assert due == ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


def test_parse_episode_id_rejects_junk() -> None:
    assert parse_episode_id("2026-W36") == (2026, 36)
    with pytest.raises(ValueError):
        parse_episode_id("test-episode")


def test_current_episode_id_is_iso_week_shaped() -> None:
    assert current_episode_id(SATURDAY_W36) == "2026-W36"


def test_episode_summary_is_one_line() -> None:
    summary = episode_summary(_healthy_episode())
    assert "\n" not in summary
    assert "2026-W36" in summary
    assert "Portuguese Custard Tart Cups" in summary
    assert "6/7" in summary
