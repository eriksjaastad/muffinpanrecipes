"""Cast coverage is structural, not probabilistic (#7079).

W37 paused twice — Thursday and Sunday — because a character on the day's
expected roster never spoke, and the judge's `cast_coverage` dimension
(#6861) fails a stage for exactly that. The cause was in two places at
once, and both are locked down here:

1. `TICKS_RANGE` let a day sample fewer turns than it had cast members,
   which makes coverage arithmetically impossible before any model runs.
2. `_select_next_speaker` only ever *nudged* toward a silent character
   (a 3.0x weight), so even with enough turns it could skip someone.

Together these produced a measured ~50% miss rate on Thursday and Sunday
and a nonzero one on every other multi-character day.
"""

from __future__ import annotations

import random

import pytest

from scripts.simulate_dialogue_week import (
    DAY_ORDER,
    TICKS_RANGE,
    _select_next_speaker,
    participants_for_day,
)


def _run_day(day: str, ticks: int) -> dict[str, int]:
    """Drive the real selector for one day and return who spoke how often."""
    names = participants_for_day(day)
    recent: list[str] = []
    counts: dict[str, int] = {}
    for tick in range(ticks):
        speaker = _select_next_speaker(names, day, tick, recent, counts, ticks)
        assert speaker in names, f"{speaker!r} is not on {day}'s roster"
        counts[speaker] = counts.get(speaker, 0) + 1
        recent.append(f"{speaker.split()[0]}: line {tick}")
    return counts


@pytest.mark.parametrize("day", DAY_ORDER)
def test_ticks_range_seats_full_cast(day: str) -> None:
    """No day may budget fewer turns than it has cast members.

    This is the arithmetic half of #7079. Thursday was (3, 5) against a
    4-person roster and Sunday (3, 4) against the same, so a third of
    Thursdays and half of Sundays could not seat their cast no matter how
    well the model wrote. Raise a roster and this test is what fails.
    """
    low, _high = TICKS_RANGE[day]
    cast_size = len(participants_for_day(day))
    assert low >= cast_size, (
        f"{day} budgets a minimum of {low} turn(s) for a {cast_size}-person "
        f"cast; at least {cast_size} are needed for cast_coverage to be "
        f"achievable at all"
    )


@pytest.mark.parametrize("day", DAY_ORDER)
def test_every_expected_character_speaks(day: str) -> None:
    """The selector must seat the whole cast at every tick count it can sample.

    Runs the real weighting logic — no mocks — across the day's full range
    and many seeds. Before the deadline constraint this failed on every
    multi-character day.
    """
    names = participants_for_day(day)
    low, high = TICKS_RANGE[day]
    for ticks in range(low, high + 1):
        for seed in range(60):
            random.seed(f"{day}-{ticks}-{seed}")
            counts = _run_day(day, ticks)
            silent = [n for n in names if counts.get(n, 0) == 0]
            assert not silent, (
                f"{day} with {ticks} turns (seed {seed}) left {silent} silent; "
                f"spoken: {counts}"
            )


def test_deadline_constraint_degrades_when_turns_are_short() -> None:
    """Fewer turns than cast: cover as many as possible, never repeat a speaker.

    `run_simulation(ticks_per_day=N)` lets a caller force a turn count that
    can't seat the cast. That must not regress to one character monologuing
    — every available turn should go to someone new.
    """
    day = "friday"
    names = participants_for_day(day)
    ticks = len(names) - 2
    for seed in range(40):
        random.seed(f"short-{seed}")
        counts = _run_day(day, ticks)
        assert len(counts) == ticks, f"expected {ticks} distinct speakers, got {counts}"
        assert max(counts.values()) == 1, f"a character repeated while others waited: {counts}"


def test_day_lead_still_opens_the_scene() -> None:
    """The deadline constraint must not displace the day-lead opener."""
    from scripts.simulate_dialogue_week import _DAY_LEADS

    for day in DAY_ORDER:
        names = participants_for_day(day)
        expected = _DAY_LEADS.get(day, names[0])
        if expected not in names:
            continue
        for seed in range(20):
            random.seed(f"lead-{day}-{seed}")
            ticks = TICKS_RANGE[day][0]
            assert _select_next_speaker(names, day, 0, [], {}, ticks) == expected


@pytest.mark.parametrize("day", DAY_ORDER)
def test_closing_turn_goes_to_someone_who_already_spoke(day: str) -> None:
    """The scene's closer must not be a character making their first entrance.

    The final turn runs with `phase="closing"` and `is_last_turn=True`. A
    character who has been silent all scene delivering the wrap-up reads
    like a stranger closing a meeting they weren't in, and it costs
    `turn_taking` and `natural_progression` on the judge's rubric. The
    deadline therefore lands one turn early whenever the day can spare a
    turn. When it can't (turns == cast size) the day is one line each and
    the closer is necessarily a first-timer — that case is excluded here,
    not silently tolerated everywhere.
    """
    names = participants_for_day(day)
    low, high = TICKS_RANGE[day]
    for ticks in range(low, high + 1):
        if ticks <= len(names):
            continue  # no spare turn exists to reserve
        for seed in range(60):
            random.seed(f"closer-{day}-{ticks}-{seed}")
            recent: list[str] = []
            counts: dict[str, int] = {}
            order: list[str] = []
            for tick in range(ticks):
                speaker = _select_next_speaker(names, day, tick, recent, counts, ticks)
                counts[speaker] = counts.get(speaker, 0) + 1
                order.append(speaker)
                recent.append(f"{speaker.split()[0]}: line {tick}")
            closer = order[-1]
            assert order.index(closer) != ticks - 1, (
                f"{day} with {ticks} turns (seed {seed}) let {closer} close the "
                f"scene on their first line; order was {order}"
            )
            assert not [n for n in names if counts.get(n, 0) == 0]
