"""The research-horizon grammar (`V2-P1-017`).

Two different claims are pinned here. The **grammar** is what `SignalFrame.horizon` now
accepts, and the test that matters for it is that the three values already in this repository
still parse *unchanged* -- constraining a field's domain must not restate any accepted value,
because `signal_id` is a hash of the canonical JSON of the fields. The **countability** rule is
separate: only the session unit turns into a number of trading days, and every other unit
refuses rather than converting through an invented sessions-per-month constant.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from openalpha_cn.domain.horizon import (
    HORIZON_PATTERN,
    MAX_HORIZON_COUNT,
    HorizonError,
    HorizonUnit,
    ResearchHorizon,
    parse_horizon,
)
from openalpha_cn.domain.signal import SignalFrame

REPOSITORY_HORIZONS = ("5d", "10d", "3m")
"""Every `horizon` literal this repository has ever written, as `parse_horizon` sees them.

`agents/baseline.py` writes `5d` and `10d`, `runtime/engine.py` writes `5d`, and `3m` was
stored on a `SignalFrame` by `tests/integration/storage/test_versioned_reads.py` until
`V2-P4-001`. `HORIZON_PATTERN` -- the *label window* grammar -- still has to accept all three
unchanged: it is what `domain/labels.py`, `factor_view.py` and `openalpha factor run
--horizon` parse, and a caller may legitimately ask for a window in a unit `sessions` then
refuses to count.
"""

SIGNAL_HORIZONS = ("5d", "10d")
"""The subset a `SignalFrame` still admits after `V2-P4-001` narrowed the field.

`COUNTABLE_HORIZON_PATTERN` restricts the field to the one unit with a session count, so every
horizon two signals carry is comparable and every one of them sizes a return window. Both of
these are literals `agents/baseline.py` and `runtime/engine.py` write today, so the narrowing
moved no stored `signal_id` -- measured in
`tests/unit/domain/test_contract_identity.py::test_narrowing_the_signal_horizon_moved_no_stored_signal_id`.
"""


@pytest.mark.parametrize("text", REPOSITORY_HORIZONS)
def test_every_horizon_literal_already_in_this_repository_still_parses(text: str) -> None:
    assert parse_horizon(text).text == text


@pytest.mark.parametrize(
    ("text", "count", "unit"),
    [
        ("1d", 1, HorizonUnit.trading_days),
        ("5d", 5, HorizonUnit.trading_days),
        ("10d", 10, HorizonUnit.trading_days),
        ("999d", 999, HorizonUnit.trading_days),
        ("2w", 2, HorizonUnit.weeks),
        ("3m", 3, HorizonUnit.months),
        ("1y", 1, HorizonUnit.years),
    ],
)
def test_a_well_formed_horizon_parses_into_its_count_and_unit(
    text: str, count: int, unit: HorizonUnit
) -> None:
    parsed = parse_horizon(text)

    assert parsed == ResearchHorizon(count=count, unit=unit)
    assert parsed.text == text


@pytest.mark.parametrize(
    "text",
    [
        "whenever",
        "五天",
        "0d",
        "5 d",
        " 5d",
        "5d ",
        "5D",
        "5",
        "d",
        "-5d",
        "5days",
        "1000d",
        "05d",
        "5d5d",
        "",
    ],
)
def test_a_malformed_horizon_is_refused_by_name(text: str) -> None:
    with pytest.raises(HorizonError, match="is not a research horizon"):
        parse_horizon(text)


def test_a_session_horizon_counts_trading_days() -> None:
    assert parse_horizon("5d").sessions == 5


@pytest.mark.parametrize("text", ["2w", "3m", "1y"])
def test_a_calendar_horizon_refuses_to_invent_a_sessions_per_unit_constant(text: str) -> None:
    horizon = parse_horizon(text)

    with pytest.raises(HorizonError, match="is not a whole number of trading sessions"):
        _ = horizon.sessions


@pytest.mark.parametrize("count", [0, -3, 1000, 1.0, True, "5"])
def test_the_direct_constructor_refuses_a_count_the_grammar_would_never_produce(
    count: object,
) -> None:
    """`parse_horizon` is not the only way in, and the second way used to be unguarded.

    A bare `ResearchHorizon(count=0, unit=trading_days)` built a window collapsed onto a single
    session and failed several frames later inside `domain/adjustment.py` as an
    `AdjustmentError`; `count=-3` failed inside `domain/trading_calendar.py` as a
    `TradingCalendarError`. Both fail-closed and both answered for a malformed horizon in a
    sibling module's vocabulary, which is the wrong exception for a caller to have to catch.
    """
    with pytest.raises(HorizonError, match="is not a horizon count"):
        ResearchHorizon(count=count, unit=HorizonUnit.trading_days)  # type: ignore[arg-type]


def test_the_direct_constructor_refuses_a_unit_that_is_not_one_of_the_four() -> None:
    with pytest.raises(HorizonError, match="is not a HorizonUnit"):
        ResearchHorizon(count=5, unit="d")  # type: ignore[arg-type]


def test_the_direct_constructor_and_the_grammar_admit_the_same_counts() -> None:
    """`MAX_HORIZON_COUNT` and `HORIZON_PATTERN`'s three digits are two statements of one bound,
    so the boundary is walked through both rather than asserted against either alone.
    """
    assert parse_horizon(f"{MAX_HORIZON_COUNT}d").count == MAX_HORIZON_COUNT
    assert ResearchHorizon(count=MAX_HORIZON_COUNT, unit=HorizonUnit.trading_days).sessions == (
        MAX_HORIZON_COUNT
    )
    with pytest.raises(HorizonError, match="is not a research horizon"):
        parse_horizon(f"{MAX_HORIZON_COUNT + 1}d")
    with pytest.raises(HorizonError, match="is not a horizon count"):
        ResearchHorizon(count=MAX_HORIZON_COUNT + 1, unit=HorizonUnit.trading_days)


def test_the_unit_letters_are_exactly_the_ones_the_pattern_admits() -> None:
    """The enum and the regex are two statements of one closed set, so they are compared.

    A member added to `HorizonUnit` without widening `HORIZON_PATTERN` would be unreachable
    through `parse_horizon`; a letter added to the pattern with no member behind it would raise
    a bare `KeyError` from inside the parser instead of a `HorizonError`.
    """
    letters = "".join(unit.value for unit in HorizonUnit)
    expected = rf"^[1-9][0-9]{{0,2}}[{letters}]$"

    assert expected == HORIZON_PATTERN


# --- the field this grammar is attached to --------------------------------------------------


def _signal(horizon: str) -> SignalFrame:
    return SignalFrame(
        subject="000001.SZ",
        as_of=datetime(2026, 1, 16, 7, 0, tzinfo=UTC),
        direction="bullish",
        strength=0.4,
        confidence=0.6,
        horizon=horizon,
        evidence_ids=("ev_1",),
    )


@pytest.mark.parametrize("text", SIGNAL_HORIZONS)
def test_signal_frame_still_accepts_every_horizon_this_repository_writes(text: str) -> None:
    assert _signal(text).horizon == text


@pytest.mark.parametrize("text", ["3m", "2w", "1y"])
def test_signal_frame_refuses_a_calendar_horizon_it_could_never_have_scored(text: str) -> None:
    """`V2-P4-001`'s narrowing, from the refusing side.

    A calendar horizon parses (the grammar keeps all four units) and is still a legal request
    for a *label window*; what it can no longer be is a `SignalFrame`'s own horizon, because
    `ResearchHorizon.sessions` refuses to turn it into a return window and a signal nothing
    can score is the failure this repository keeps closing elsewhere.
    """
    with pytest.raises(ValidationError, match="String should match pattern"):
        _signal(text)

    assert parse_horizon(text).text == text


def test_signal_frame_refuses_a_horizon_that_is_not_a_countable_span() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        _signal("whenever")


@pytest.mark.parametrize("text", SIGNAL_HORIZONS)
def test_constraining_the_horizon_field_did_not_restate_any_accepted_value(text: str) -> None:
    """The identity guard. `signal_id` hashes the canonical JSON, so a validator that
    *normalised* the field -- lower-casing a unit, stripping a leading zero -- would silently
    move the ID of every stored signal. Constraining the domain does not, and this pins that:
    what serialises is the string that was passed in, byte for byte.
    """
    assert _signal(text).model_dump(mode="json")["horizon"] == text


# --- V2-P4-001: the comparability half of PRD D36 --------------------------------------


def test_two_signal_horizons_order_by_the_sessions_they_span() -> None:
    """The property `COUNTABLE_HORIZON_PATTERN` exists to make true.

    Sorting is asserted rather than a single `<`, because a comparison that is only correct
    pairwise is not what a ranking needs, and `@total_ordering` deriving `>`/`<=`/`>=` from
    one `__lt__` is exactly the kind of thing that is right until it is not.
    """
    horizons = [parse_horizon(text) for text in ("10d", "1d", "5d", "999d")]

    assert [item.text for item in sorted(horizons)] == ["1d", "5d", "10d", "999d"]
    assert parse_horizon("5d") < parse_horizon("10d")
    assert parse_horizon("10d") > parse_horizon("5d")
    assert parse_horizon("5d") <= parse_horizon("5d")


def test_ordering_a_calendar_horizon_refuses_with_the_reason_sessions_gives() -> None:
    """The refusal is `sessions`\' own, reused -- not a second, weaker story about units.

    A lexicographic `(unit, count)` order would have ranked `999d` (about four years of
    sessions) below `1w`, which is a total order that is wrong. Refusing is the honest answer
    while the sessions-per-week constant stays unmeasured.
    """
    with pytest.raises(HorizonError, match="is not a whole number of trading sessions"):
        _ = parse_horizon("1w") < parse_horizon("5d")

    with pytest.raises(HorizonError, match="is not a whole number of trading sessions"):
        _ = parse_horizon("5d") < parse_horizon("1w")


def test_equality_still_answers_for_all_four_units_even_though_ordering_does_not() -> None:
    """Two questions, two answers, neither guessed.

    `1w == 5d` is `False` -- they are different horizons, and that is decidable without
    knowing which is longer -- while `1w < 5d` refuses. Equality has to keep working for the
    calendar units because `parse_horizon(h.text) == h` is what makes a horizon safe to write
    back into a field, and `factor_view` still parses calendar horizons.
    """
    assert parse_horizon("1w") != parse_horizon("5d")
    assert parse_horizon("3m") == ResearchHorizon(count=3, unit=HorizonUnit.months)
    assert parse_horizon("3m") != parse_horizon("4m")
