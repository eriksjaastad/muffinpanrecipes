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

import scripts.simulate_dialogue_week as sim
from scripts.conversation_metrics import cast_coverage
from scripts.simulate_dialogue_week import (
    DAY_ORDER,
    TICKS_RANGE,
    _select_next_speaker,
    participants_for_day,
)


@pytest.fixture(autouse=True)
def _restore_global_rng():
    """Seed freely without leaking RNG state into whatever runs next.

    The simulator draws from the module-level `random`, so these tests have
    to seed the global RNG rather than a private `random.Random()`. Snapshot
    and restore it so ordering with other test files stays irrelevant.
    """
    state = random.getstate()
    try:
        yield
    finally:
        random.setstate(state)


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


# ---------------------------------------------------------------------------
# End-to-end: the real run_simulation() entry point
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_turns(monkeypatch):
    """Run the real day loop with only the model call replaced.

    Everything the production path does around speaker selection — tick
    sampling from TICKS_RANGE, the closing/winding_down phases, the
    Wednesday photography tick override — runs for real. Only the network
    call is stubbed, so these stay free and fast.
    """
    def _fake_turn(**kwargs):
        return f"{kwargs['persona']['name'].split()[0]} says something about the pan."

    monkeypatch.setattr(sim, "generate_turn", _fake_turn)


def _simulate(day: str, **overrides) -> list[dict]:
    kwargs = dict(
        concept="Brazilian Pao de Queijo Bites",
        default_model="stub",
        run_index=0,
        stage_only=day,
        injected_event=None,
        ticks_per_day=0,
        mode="normal",
        prompt_style="plain",
        character_models=None,
    )
    kwargs.update(overrides)
    out = sim.run_simulation(**kwargs)
    return [
        {"character": m["character"], "message": m["message"]}
        for m in out["messages"]
    ]


@pytest.mark.parametrize("day", DAY_ORDER)
def test_run_simulation_covers_the_cast(day: str, stub_turns) -> None:
    """The production entry point, scored with the same metric the judge uses.

    `_select_next_speaker` is unit-tested above, but the cron calls
    `run_simulation`, and the gap between the two is where the Wednesday
    tick override and the phase logic live. This closes it.
    """
    expected = participants_for_day(day)
    for seed in range(8):
        random.seed(f"e2e-{day}-{seed}")
        coverage = cast_coverage(expected, _simulate(day))
        assert not coverage["missing"], (
            f"{day} (seed {seed}) left {coverage['missing']} silent"
        )
        assert not coverage["unexpected"], (
            f"{day} (seed {seed}) seated off-roster {coverage['unexpected']}"
        )


# These two run more seeds than the tests above, and the reason is worth
# stating. Both exercise generous turn counts relative to cast size (7-10
# turns for 4 people; 3 turns with no repeat possible), and at those ratios
# the OLD pure-nudge selector already landed full coverage ~96-99% of the
# time by luck. Measured pre-fix miss rates: 2.3% at 7 ticks, 4.3% at 10,
# 1.3% on the forced-3 Friday. At 8 seeds these would catch a full revert
# only ~10-17% of the time - honest assertions, but decorative as guards.
# The coverage guarantee itself is held by the direct-selector tests above,
# which run tighter tick counts where the old code failed 24-56% of the
# time. What these two are really for is wiring: that the tick overrides
# reach the selector at all. The seed counts below buy back real power for
# the coverage assertions that ride along.
_WIRING_SEEDS = 200


@pytest.mark.parametrize("reshoot", [False, True])
def test_wednesday_photography_override_reaches_the_selector(reshoot: bool, stub_turns) -> None:
    """Wednesday raises its own tick count when photography context is present.

    `run_simulation` bumps day_ticks to 7 (or 10 on a reshoot) *after*
    sampling TICKS_RANGE, so the override has to reach
    `_select_next_speaker` as total_ticks or the deadline is computed
    against the wrong horizon. The length floor is the load-bearing
    assertion here: TICKS_RANGE["wednesday"] tops out at 6, so a message
    count of 7+ can only come from the override actually firing.
    """
    expected = participants_for_day("wednesday")
    context = {"reshoot_happened": reshoot, "rounds": []}
    floor = 10 if reshoot else 7
    for seed in range(_WIRING_SEEDS):
        random.seed(f"photo-{reshoot}-{seed}")
        messages = _simulate("wednesday", photography_context=context)
        assert len(messages) >= floor, (
            f"reshoot={reshoot} seed {seed} produced {len(messages)} messages; "
            f"the photography override should have forced at least {floor}"
        )
        assert not cast_coverage(expected, messages)["missing"]


def test_forced_tick_count_below_cast_size_still_spreads(stub_turns) -> None:
    """An explicit ticks_per_day under the cast size must not regress to a monologue.

    Production passes ticks_per_day=0, but the CLI exposes --ticks-per-day
    and run_simulation honours it. Coverage is impossible below the cast
    size; giving every available turn to a different character is not.
    """
    expected = participants_for_day("friday")
    for seed in range(_WIRING_SEEDS):
        random.seed(f"forced-{seed}")
        messages = _simulate("friday", ticks_per_day=3)
        speakers = [m["character"] for m in messages]
        assert len(messages) == 3
        assert len(set(speakers)) == 3, f"a character repeated while others waited: {speakers}"
        assert not cast_coverage(expected, messages)["unexpected"]


def test_sunday_can_always_close_its_own_scene() -> None:
    """Sunday budgets at least one turn more than it has cast members (#7082).

    At exactly cast-size turns the day is one line each: no character speaks
    twice, so nobody can answer anyone and nobody is left to wrap up. W37's
    Sunday sampled 4 turns for a 4-person cast, scored natural_progression 2,
    and failed the publish gate with "four lines of substance that resolve
    nothing meaningful". Offline reruns at production settings reproduced it —
    4-turn runs ended mid-explanation, 5-turn runs closed cleanly.

    Scoped to Sunday because that is where it was measured and fixed. The same
    exposure exists on tuesday, wednesday, thursday and friday, whose floors
    still equal their cast size; raising those is a week-wide pacing and cost
    change and belongs to the conversation lab, not to this guarantee.
    """
    low, _high = TICKS_RANGE["sunday"]
    cast_size = len(participants_for_day("sunday"))
    assert low >= cast_size + 1, (
        f"sunday budgets a minimum of {low} turns for {cast_size} characters; "
        f"at least {cast_size + 1} are needed for anyone to reply or sign off"
    )


@pytest.mark.parametrize("day", DAY_ORDER)
def test_days_at_exactly_cast_size_are_recorded(day: str) -> None:
    """Inventory of which days can still produce a reply-less scene.

    Not a guard — a ledger, and deliberately a temporary one. Any day whose
    floor equals its cast size can sample a one-line-each scene, verified at
    100% over 5000 seeds against the real selector. This names them so the
    list cannot drift silently while **#7105** decides whether to raise all
    four. When a day's floor goes up, delete it from here; when #7105 closes,
    delete this test.

    Do not reuse this pattern to record known bugs in place of filing a card.
    It earns its place only because it computes the at-risk set from live
    TICKS_RANGE and participants_for_day rather than restating prose, and
    because it points at an open card that will retire it.
    """
    known_at_risk = {"tuesday", "wednesday", "thursday", "friday"}
    low, _high = TICKS_RANGE[day]
    cast_size = len(participants_for_day(day))
    at_risk = low == cast_size
    assert at_risk == (day in known_at_risk), (
        f"{day} at-risk status changed (floor {low}, cast {cast_size}). "
        f"Update known_at_risk and card #7105."
    )
