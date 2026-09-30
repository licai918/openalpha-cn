"""`V2-P6-022`: the registry and a factor history answer a lookup without rescanning themselves.

`StockUniverse.security` walked every registry row for every lookup, and a label asks it once per
session of every window: 114.9M calls and 5,623 s of one horizon-20 IC series on the research
store, about 49 us each. `AdjustmentHistory.factor_on` rebuilt the list of its observation days on
every call before bisecting it. Both now build their index once, at construction.

Two things are held here. **No rescan**: after construction a lookup does not iterate the rows it
was built from, which is asserted by counting iterations rather than by a stopwatch. **The same
answer**: every lookup agrees with the linear scan it replaced, including which of two entries
for one code wins (the first -- the constructor validates nothing and a duplicate is its
caller's), the refusal of an unknown code, and the two horizons of a factor series.
"""

from __future__ import annotations

import dataclasses
from bisect import bisect_right
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest

from openalpha_cn.domain.adjustment import (
    AdjustmentHistory,
    AdjustmentHorizonError,
    FactorObservation,
)
from openalpha_cn.domain.stock_universe import (
    ListingStatus,
    SecurityLifecycle,
    StockUniverse,
    StockUniverseError,
)

SNAPSHOT = date(2026, 8, 8)


class _CountingTuple(tuple[Any, ...]):
    """A tuple that counts how many times it is walked -- the cost the index removes."""

    walks = 0

    def __iter__(self) -> Iterator[Any]:
        type(self).walks += 1
        return super().__iter__()


def _securities(count: int) -> tuple[SecurityLifecycle, ...]:
    return tuple(
        SecurityLifecycle(
            ts_code=f"{index:06d}.SZ",
            exchange="SZSE",
            listed_on=date(2000, 1, 1) + timedelta(days=37 * index),
            delisted_on=None if index % 3 else date(2012, 1, 1) + timedelta(days=11 * index),
        )
        for index in range(count)
    )


def _reference_security(universe: StockUniverse, ts_code: str) -> SecurityLifecycle:
    """The lookup as it was before `V2-P6-022`: the first row whose code matches."""
    for entry in tuple.__iter__(universe.securities):
        if entry.ts_code == ts_code:
            return entry
    raise StockUniverseError(ts_code)


def test_a_registry_lookup_does_not_walk_the_registry() -> None:
    rows = _CountingTuple(_securities(400))
    universe = StockUniverse(snapshot_date=SNAPSHOT, securities=rows)
    _CountingTuple.walks = 0

    for entry in _securities(400):
        assert universe.security(entry.ts_code) == entry
        universe.status_on(entry.ts_code, date(2015, 6, 1))
    with pytest.raises(StockUniverseError):
        universe.security("999999.SZ")

    assert _CountingTuple.walks == 0, (
        f"{_CountingTuple.walks} walks of the registry for 801 lookups; a lookup must not scan it"
    )


def test_every_lookup_answers_what_the_scan_answered() -> None:
    securities = _securities(120)
    universe = StockUniverse(snapshot_date=SNAPSHOT, securities=securities)
    days = [date(1999, 12, 31) + timedelta(days=97 * step) for step in range(100)]

    for entry in securities:
        assert universe.security(entry.ts_code) is _reference_security(universe, entry.ts_code)
        for day in days:
            reference = _reference_security(universe, entry.ts_code)
            expected = (
                ListingStatus.beyond_snapshot
                if day > SNAPSHOT
                else ListingStatus.not_yet_listed
                if day < reference.listed_on
                else ListingStatus.delisted
                if reference.delisted_on is not None and day >= reference.delisted_on
                else ListingStatus.listed
            )
            assert universe.status_on(entry.ts_code, day) is expected


def test_a_duplicated_code_answers_with_its_first_entry_as_the_scan_did() -> None:
    first = SecurityLifecycle(ts_code="000001.SZ", exchange="SZSE", listed_on=date(1991, 4, 3))
    second = SecurityLifecycle(
        ts_code="000001.SZ", exchange="SZSE", listed_on=date(2001, 1, 1), delisted_on=None
    )
    universe = StockUniverse(snapshot_date=SNAPSHOT, securities=(first, second))

    assert universe.security("000001.SZ") is first


def test_an_unknown_code_is_refused_with_the_same_sentence() -> None:
    universe = StockUniverse(snapshot_date=SNAPSHOT, securities=_securities(3))

    with pytest.raises(StockUniverseError) as refused:
        universe.security("600000.SH")

    assert str(refused.value) == (
        "'600000.SH' is not in the 2026-08-08 registry snapshot; an absent code is not a "
        "security that was never listed"
    )


def test_the_index_is_not_part_of_the_value() -> None:
    """Equality, hashing, `repr` and `replace` see the registry and nothing built from it."""
    universe = StockUniverse(snapshot_date=SNAPSHOT, securities=_securities(5), years_read=(2020,))
    twin = StockUniverse(snapshot_date=SNAPSHOT, securities=_securities(5), years_read=(2020,))

    assert universe == twin
    assert hash(universe) == hash(twin)
    assert repr(universe) == (
        f"StockUniverse(snapshot_date={SNAPSHOT!r}, securities={_securities(5)!r}, "
        "years_read=(2020,))"
    )
    moved = dataclasses.replace(universe, securities=_securities(7))
    assert moved.security("000006.SZ") == _securities(7)[6]
    with pytest.raises(StockUniverseError):
        universe.security("000006.SZ")


# --- AdjustmentHistory -------------------------------------------------------------------------


def _observations(count: int) -> tuple[FactorObservation, ...]:
    return tuple(
        FactorObservation(
            ts_code="000001.SZ",
            observed_on=date(2020, 1, 2) + timedelta(days=9 * index),
            factor=1.0 + 0.125 * index,
        )
        for index in range(count)
    )


def _reference_factor(history: AdjustmentHistory, day: date) -> float:
    """`factor_on`'s arithmetic as it was before `V2-P6-022`, horizons included."""
    observations = tuple(tuple.__iter__(history.observations))
    if day < observations[0].observed_on or day > history.covered_through:
        raise AdjustmentHorizonError(day.isoformat())
    position = bisect_right([entry.observed_on for entry in observations], day)
    return observations[position - 1].factor


def test_a_factor_lookup_does_not_walk_the_history() -> None:
    history = AdjustmentHistory(ts_code="000001.SZ", observations=_CountingTuple(_observations(60)))
    _CountingTuple.walks = 0

    for offset in range(0, 530, 3):
        history.factor_on(date(2020, 1, 2) + timedelta(days=offset))

    assert _CountingTuple.walks == 0, (
        f"{_CountingTuple.walks} walks of the factor series for 177 lookups; the observation "
        "days are an index built once"
    )


@pytest.mark.parametrize("answerable_through", [None, date(2021, 12, 31)])
def test_every_factor_lookup_answers_what_the_rebuilt_list_answered(
    answerable_through: date | None,
) -> None:
    history = AdjustmentHistory(
        ts_code="000001.SZ",
        observations=_observations(60),
        answerable_through=answerable_through,
    )

    for offset in range(-5, 800):
        day = date(2020, 1, 2) + timedelta(days=offset)
        try:
            expected: float | type[Exception] = _reference_factor(history, day)
        except AdjustmentHorizonError:
            expected = AdjustmentHorizonError
        if expected is AdjustmentHorizonError:
            with pytest.raises(AdjustmentHorizonError):
                history.factor_on(day)
        else:
            assert history.factor_on(day) == expected


def test_the_factor_index_is_not_part_of_the_value() -> None:
    history = AdjustmentHistory(ts_code="000001.SZ", observations=_observations(4))
    twin = AdjustmentHistory(ts_code="000001.SZ", observations=_observations(4))

    assert history == twin
    assert hash(history) == hash(twin)
    assert repr(history) == (
        f"AdjustmentHistory(ts_code='000001.SZ', observations={_observations(4)!r}, "
        "answerable_through=None)"
    )
    longer = dataclasses.replace(history, observations=_observations(8))
    assert longer.factor_on(date(2020, 3, 5)) == _observations(8)[7].factor
