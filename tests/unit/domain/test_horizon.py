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
"""Every `horizon` literal this repository writes today.

`agents/baseline.py` writes `5d` and `10d`, `runtime/engine.py` writes `5d`, and
`tests/integration/storage/test_versioned_reads.py` stores `3m`. The grammar has to accept all
three unchanged or the `signal_id` of every one of them moves.
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


@pytest.mark.parametrize("text", REPOSITORY_HORIZONS)
def test_signal_frame_still_accepts_every_horizon_this_repository_writes(text: str) -> None:
    assert _signal(text).horizon == text


def test_signal_frame_refuses_a_horizon_that_is_not_a_countable_span() -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        _signal("whenever")


@pytest.mark.parametrize("text", REPOSITORY_HORIZONS)
def test_constraining_the_horizon_field_did_not_restate_any_accepted_value(text: str) -> None:
    """The identity guard. `signal_id` hashes the canonical JSON, so a validator that
    *normalised* the field -- lower-casing a unit, stripping a leading zero -- would silently
    move the ID of every stored signal. Constraining the domain does not, and this pins that:
    what serialises is the string that was passed in, byte for byte.
    """
    assert _signal(text).model_dump(mode="json")["horizon"] == text
