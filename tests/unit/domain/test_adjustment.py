"""The adjustment-factor contract (`V2-P1-006`): a step function with two horizons.

No network. Every number below was measured against the live Tushare `adj_factor` and
`daily` endpoints on 2026-08-08 and is inlined here as a fixture.

The acceptance this file carries is the one the roadmap names the issue after -- *without
this dataset every return is wrong*. The first test below is that acceptance, on a real
ex-dividend day, checked against Tushare's own `pct_chg`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from openalpha_cn.domain.adjustment import (
    ADJ_FACTOR_DATASET,
    ADJUSTMENT_PANEL_COLUMNS,
    KNOWN_ADJUSTMENT_LIMITATIONS,
    AdjustmentError,
    AdjustmentHorizonError,
    FactorObservation,
    PriceAdjustment,
    adjustment_factors_on,
    adjustment_histories_from_panel_rows,
    build_adjustment_history,
    load_bearing_observations,
)
from openalpha_cn.domain.stock_universe import SecurityLifecycle, build_stock_universe
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar

PING_AN = "000001.SZ"

# --- real data ------------------------------------------------------------------------

PING_AN_CHANGE_POINTS: tuple[tuple[str, float], ...] = (
    ("19910403", 1.0),
    ("19910502", 1.409),
    ("19910819", 2.608),
    ("19911111", 2.463),
    ("19911118", 2.549),
    ("19920210", 2.454),
    ("19920504", 2.296),
    ("19920506", 2.473),
    ("19921005", 2.462),
    ("19930104", 2.41),
    ("19930524", 3.531),
    ("19940711", 5.646),
    ("19950925", 6.984),
    ("19960527", 13.968),
    ("19970825", 21.088),
    ("19991018", 21.662),
    ("20001106", 24.359),
    ("20020723", 24.614),
    ("20030929", 25.015),
    ("20070620", 27.524),
    ("20081031", 35.906),
    ("20121019", 36.173),
    ("20130620", 58.387),
    ("20140612", 71.054),
    ("20150413", 85.994),
    ("20160616", 104.758),
    ("20170721", 106.309),
    ("20180712", 108.031),
    ("20190626", 109.169),
    ("20200102", 109.1694),
    ("20200528", 111.0487),
    ("20210107", 111.049),
    ("20210514", 111.922),
    ("20220722", 113.9362),
    ("20230601", 113.936),
    ("20230614", 116.713),
    ("20240614", 125.0496),
    ("20240625", 125.0493),
    ("20240626", 125.049),
    ("20241010", 127.7841),
    ("20250612", 131.7878),
    ("20251015", 134.5794),
    ("20260612", 139.008),
)
"""Every point at which `000001.SZ`'s factor moved, over its whole 8,627-row history.

Three numbers, one apart from each other, and only the third is the compression ratio's
denominator: the series takes **43 distinct values** (the 43 rows below), so it moves at
**42 change points**, and the stored window is **44 rows** -- these 43 plus the closing
anchor 2026-08-07, whose value repeats 2026-06-12's. 8,627 / 44 is a **196x** reduction.
Quoting the 43 as if it were the stored row count gives 201x, which is the shape of error
this fixture is here to prevent. The series runs from 1.0 on the listing day (1991-04-03) to
139.008 on 2026-08-07.
"""

PING_AN_2024_DAILY: tuple[tuple[str, float], ...] = (
    ("20240102", 116.713),
    ("20240613", 116.713),
    ("20240614", 125.0496),
    ("20240624", 125.0496),
    ("20240625", 125.0493),
    ("20240626", 125.049),
    ("20241009", 125.049),
    ("20241010", 127.7841),
    ("20241231", 127.7841),
)
"""A sample of the 242 rows the 2024 window actually returns, including both endpoints.

The full window's load-bearing rows are the anchor (2024-01-02, 116.713), the four change
points, and the closing observation (2024-12-31, 127.7841).
"""

# `000001.SZ` around its 2026 ex-dividend date, from the live `daily` endpoint.
EX_DIVIDEND_DAY = date(2026, 6, 12)
SESSION_BEFORE = date(2026, 6, 11)
CLOSE_BEFORE = 11.3
CLOSE_ON_EX_DAY = 11.24
TUSHARE_PCT_CHG = 2.7422
"""Tushare's own `pct_chg` for 2026-06-12, computed off its ex-dividend-adjusted
`pre_close` of 10.94 rather than off the previous close of 11.30. It is the independent
witness this file checks the adjusted return against."""


def _history(points: tuple[tuple[str, float], ...] = PING_AN_CHANGE_POINTS, ts_code=PING_AN):
    return build_adjustment_history(
        ts_code,
        [
            FactorObservation(
                ts_code=ts_code,
                observed_on=date(int(day[:4]), int(day[4:6]), int(day[6:])),
                factor=factor,
            )
            for day, factor in points
        ],
    )


# --- the acceptance -------------------------------------------------------------------


def test_the_unadjusted_return_is_wrong_and_the_adjusted_one_matches_tushares_own_pct_chg() -> None:
    history = _history()

    unadjusted = CLOSE_ON_EX_DAY / CLOSE_BEFORE - 1
    adjusted = history.adjusted_return(
        start=SESSION_BEFORE,
        end=EX_DIVIDEND_DAY,
        start_price=CLOSE_BEFORE,
        end_price=CLOSE_ON_EX_DAY,
    )

    # The unadjusted number says the security fell; it rose. The error is not a rounding
    # error and it is not even the right sign.
    assert unadjusted == pytest.approx(-0.00530973, abs=1e-8)
    assert unadjusted < 0 < adjusted
    assert adjusted == pytest.approx(TUSHARE_PCT_CHG / 100, abs=1e-6)


def test_both_conventions_disagree_on_price_and_agree_on_return() -> None:
    """Forward and backward adjustment are two scales of one series, so a *return* computed
    over either is the same number -- which is why `adjusted_return` needs no convention."""
    history = _history()
    base = date(2026, 6, 12)

    backward_before = history.adjust_price(
        CLOSE_BEFORE, SESSION_BEFORE, convention=PriceAdjustment.backward
    )
    forward_before = history.adjust_price(
        CLOSE_BEFORE, SESSION_BEFORE, convention=PriceAdjustment.forward, base_day=base
    )
    backward_after = history.adjust_price(
        CLOSE_ON_EX_DAY, EX_DIVIDEND_DAY, convention=PriceAdjustment.backward
    )
    forward_after = history.adjust_price(
        CLOSE_ON_EX_DAY, EX_DIVIDEND_DAY, convention=PriceAdjustment.forward, base_day=base
    )

    assert backward_before != forward_before
    # 前复权 leaves the base day's own price untouched; that is what makes it "forward".
    assert forward_after == pytest.approx(CLOSE_ON_EX_DAY)
    # 后复权 leaves the *listing* untouched instead: the factor is 1.0 there.
    assert history.adjust_price(
        7.0, date(1991, 4, 3), convention=PriceAdjustment.backward
    ) == pytest.approx(7.0)
    assert backward_after / backward_before == pytest.approx(forward_after / forward_before)


def test_forward_adjustment_refuses_to_run_without_a_base_day() -> None:
    """前复权 is only defined relative to a base day, so the contract will not guess one."""
    history = _history()
    with pytest.raises(AdjustmentError, match="forward adjustment needs a base_day"):
        history.adjust_price(CLOSE_BEFORE, SESSION_BEFORE, convention=PriceAdjustment.forward)


def test_backward_adjustment_refuses_a_base_day_rather_than_ignoring_it() -> None:
    """A caller who passes one has asked for the other convention and must be told."""
    history = _history()
    with pytest.raises(AdjustmentError, match="backward adjustment takes no base_day"):
        history.adjust_price(
            CLOSE_BEFORE,
            SESSION_BEFORE,
            convention=PriceAdjustment.backward,
            base_day=date(2026, 6, 12),
        )


def test_a_convention_that_is_not_one_of_the_two_is_refused() -> None:
    """`"forward"` is the natural mistake -- an `Enum` member's value, not the member -- and
    it would otherwise fall through to the backward branch and return the wrong scale."""
    history = _history()
    with pytest.raises(AdjustmentError, match="convention must be a PriceAdjustment"):
        history.adjust_price(
            CLOSE_BEFORE,
            SESSION_BEFORE,
            convention="forward",  # type: ignore[arg-type]
        )


def test_a_return_needs_two_distinct_days_in_order() -> None:
    history = _history()
    with pytest.raises(AdjustmentError, match="is not before end"):
        history.adjusted_return(
            start=EX_DIVIDEND_DAY,
            end=SESSION_BEFORE,
            start_price=CLOSE_ON_EX_DAY,
            end_price=CLOSE_BEFORE,
        )
    with pytest.raises(AdjustmentError, match="is not before end"):
        history.adjusted_return(
            start=EX_DIVIDEND_DAY,
            end=EX_DIVIDEND_DAY,
            start_price=CLOSE_ON_EX_DAY,
            end_price=CLOSE_ON_EX_DAY,
        )


def test_a_return_refuses_a_price_that_cannot_be_scaled() -> None:
    history = _history()
    with pytest.raises(AdjustmentError, match="start_price must be a finite positive float"):
        history.adjusted_return(
            start=SESSION_BEFORE, end=EX_DIVIDEND_DAY, start_price=0.0, end_price=CLOSE_ON_EX_DAY
        )
    with pytest.raises(AdjustmentError, match="end_price must be a finite positive float"):
        history.adjusted_return(
            start=SESSION_BEFORE, end=EX_DIVIDEND_DAY, start_price=CLOSE_BEFORE, end_price=-1.0
        )


def test_a_convention_has_no_truth_value() -> None:
    with pytest.raises(AdjustmentError, match="has no truth value"):
        bool(PriceAdjustment.backward)
    with pytest.raises(AdjustmentError, match="has no truth value"):
        bool(PriceAdjustment.forward)


# --- the step function ----------------------------------------------------------------


def test_the_factor_is_one_on_the_listing_day() -> None:
    assert _history().factor_on(date(1991, 4, 3)) == 1.0


def test_the_factor_holds_between_change_points() -> None:
    history = _history()
    assert history.factor_on(date(2024, 6, 13)) == 116.713
    assert history.factor_on(date(2024, 6, 14)) == 125.0496
    assert history.factor_on(date(2024, 6, 24)) == 125.0496
    assert history.factor_on(date(2024, 10, 9)) == 125.049
    assert history.factor_on(date(2024, 12, 31)) == 127.7841


def test_a_day_before_the_first_observation_is_refused_not_extrapolated() -> None:
    history = _history()
    with pytest.raises(AdjustmentHorizonError, match=r"before 000001\.SZ's first adjustment"):
        history.factor_on(date(1991, 4, 2))


def test_a_day_after_the_last_observation_is_refused_not_extrapolated() -> None:
    """The upper horizon is the difference between this contract and `NameHistory`.

    A name persists until it is changed, so extrapolating the last one forward is honest. A
    factor does not: `000001.SZ` moved four times in the two years after 2024, so answering
    "the last factor we happened to fetch" for a later day asserts that no dividend was paid
    in a window this read never looked at.
    """
    history = _history(PING_AN_2024_DAILY)
    with pytest.raises(AdjustmentHorizonError, match=r"after 000001\.SZ's last adjustment"):
        history.factor_on(date(2025, 1, 2))


def test_the_horizon_is_reported_rather_than_discovered_by_tripping_over_it() -> None:
    history = _history(PING_AN_2024_DAILY)
    assert history.covered_from == date(2024, 1, 2)
    assert history.covered_through == date(2024, 12, 31)


def test_a_read_that_declares_its_own_horizon_answers_to_it_and_not_to_its_newest_row() -> None:
    """`V2-P4-086`: on a step function the newest *row* is not the newest *day* a read covered.

    `compress_adjustment_batch` keeps the year's opening anchor, every change point and the year's
    closing anchor, so a point-in-time read that withholds the closing anchor is left holding a
    row from January -- and `covered_through` reading `observations[-1].observed_on` then says the
    series *stopped* in January, which is a statement about the read wearing the shape of a
    statement about the market. `V2-P4-079` measured what that costs on the shipped path: two of
    `panel doctor`'s eight cross-checks died on it, for a session that had published three days
    earlier.

    Both horizons are asserted, because the field is only worth having if the two come apart:
    `covered_through` is how far this read may be asked and `observed_through` is where its last
    measurement actually sits. A build that returned the same date for both would pass every
    assertion about the first alone.
    """
    history = _history(
        (("20240102", 116.713), ("20240613", 121.6), ("20240614", 121.6)),
    )
    bounded = build_adjustment_history(
        PING_AN,
        history.observations,
        answerable_through=date(2024, 9, 30),
    )

    assert bounded.observed_through == date(2024, 6, 14)
    assert bounded.covered_through == date(2024, 9, 30)
    assert bounded.factor_on(date(2024, 9, 30)) == bounded.factor_on(date(2024, 6, 14))

    with pytest.raises(AdjustmentHorizonError, match=r"the last day the read that built"):
        bounded.factor_on(date(2024, 10, 1))

    # ... and the message says both numbers, because "the read stopped" and "the series stopped"
    # are the two readings this field exists to separate.
    try:
        bounded.factor_on(date(2024, 10, 1))
    except AdjustmentHorizonError as error:
        assert "2024-09-30" in str(error) and "2024-06-14" in str(error)


def test_a_horizon_behind_the_rows_the_read_is_holding_is_refused() -> None:
    """A row the read holds is a row the read saw, so a horizon behind it describes another read.

    The direction that is easy to leave out. `answerable_through` is a *widening* of what the
    rows alone would say, and a caller that computed it from the wrong clock -- or clamped it
    against the wrong partition -- would narrow it instead, silently refusing days the read
    genuinely covered. Refused at construction rather than discovered at `factor_on`.
    """
    with pytest.raises(AdjustmentError, match="describes a different read"):
        build_adjustment_history(
            PING_AN,
            _history((("20240102", 116.713), ("20240613", 121.6))).observations,
            answerable_through=date(2024, 3, 1),
        )


def test_every_history_from_one_read_carries_that_reads_horizon() -> None:
    """`adjustment_histories_from_panel_rows` hands the bound to every security it rebuilds.

    One date for the whole read and not one per security, `statement_histories_from_panel_rows`'
    shape: it describes the read, and a read has one horizon however many securities came back.
    Which security's tail is genuinely finished and which is merely quiet is a different question
    and this contract answers neither -- `KNOWN_ADJUSTMENT_LIMITATIONS.suspension_is_invisible`.
    """
    histories = adjustment_histories_from_panel_rows(
        [
            (PING_AN, "2024-01-02", 116.713),
            (PING_AN, "2024-06-13", 121.6),
            ("600000.SH", "2024-01-02", 1.0),
        ],
        answerable_through=date(2024, 9, 30),
    )

    assert sorted(histories) == ["000001.SZ", "600000.SH"]
    assert {code: history.covered_through for code, history in histories.items()} == {
        PING_AN: date(2024, 9, 30),
        "600000.SH": date(2024, 9, 30),
    }
    assert {code: history.observed_through for code, history in histories.items()} == {
        PING_AN: date(2024, 6, 13),
        "600000.SH": date(2024, 1, 2),
    }

    unbounded = adjustment_histories_from_panel_rows([(PING_AN, "2024-01-02", 116.713)])
    assert unbounded[PING_AN].covered_through == date(2024, 1, 2), (
        "omitting the bound is the pre-V2-P4-086 behaviour and every caller that does not "
        "filter rows keeps it"
    )


def test_a_datetime_is_not_a_date() -> None:
    history = _history()
    with pytest.raises(AdjustmentError, match=r"must be a plain datetime\.date"):
        history.factor_on(datetime(2024, 6, 14))  # type: ignore[arg-type]


# --- the compression rule -------------------------------------------------------------


def _observations(points: tuple[tuple[str, float], ...]) -> list[FactorObservation]:
    return [
        FactorObservation(
            ts_code=PING_AN,
            observed_on=date(int(day[:4]), int(day[4:6]), int(day[6:])),
            factor=factor,
        )
        for day, factor in points
    ]


def test_compression_keeps_the_first_row_every_change_and_the_last_row() -> None:
    kept = load_bearing_observations(_observations(PING_AN_2024_DAILY))

    assert [entry.observed_on.isoformat() for entry in kept] == [
        "2024-01-02",  # the window's anchor
        "2024-06-14",
        "2024-06-25",
        "2024-06-26",
        "2024-10-10",
        "2024-12-31",  # the window's closing observation, unchanged since 2024-10-10
    ]


def test_the_closing_observation_is_kept_even_though_its_value_repeats() -> None:
    """Dropping it is the mistake that makes the upper horizon a lie.

    Without it the 2024 partition's last row would be 2024-10-10, and `covered_through`
    would claim the read stopped in October when it ran to New Year's Eve -- so every
    November and December question would be refused as beyond the horizon.
    """
    kept = load_bearing_observations(_observations(PING_AN_2024_DAILY))
    assert kept[-1].observed_on == date(2024, 12, 31)
    assert kept[-1].factor == kept[-2].factor


def test_a_compressed_history_answers_every_day_of_the_window_exactly_as_the_raw_one_does() -> None:
    raw = _history(PING_AN_2024_DAILY)
    compressed = build_adjustment_history(
        PING_AN, load_bearing_observations(_observations(PING_AN_2024_DAILY))
    )

    for day, factor in PING_AN_2024_DAILY:
        when = date(int(day[:4]), int(day[4:6]), int(day[6:]))
        assert compressed.factor_on(when) == raw.factor_on(when) == factor
    assert compressed.covered_from == raw.covered_from
    assert compressed.covered_through == raw.covered_through
    assert len(compressed.observations) < len(raw.observations)


def test_a_single_observation_compresses_to_itself() -> None:
    kept = load_bearing_observations(_observations((("20240102", 116.713),)))
    assert len(kept) == 1


def test_compressing_nothing_is_refused() -> None:
    with pytest.raises(AdjustmentError, match="needs at least one observation"):
        load_bearing_observations([])


def test_compression_refuses_an_unordered_input_rather_than_sorting_it() -> None:
    """Sorting here would hide a caller that assembled two partitions in the wrong order,
    and the compression's output depends on the order: `first` and `last` are positional."""
    with pytest.raises(AdjustmentError, match="must be ascending"):
        load_bearing_observations(_observations((("20241231", 1.0), ("20240102", 2.0))))


# --- construction ---------------------------------------------------------------------


def test_an_empty_history_is_refused() -> None:
    with pytest.raises(AdjustmentError, match="needs at least one factor observation"):
        build_adjustment_history(PING_AN, [])


def test_a_record_belonging_to_another_security_is_refused() -> None:
    with pytest.raises(AdjustmentError, match=r"carries 600519\.SH"):
        build_adjustment_history(
            PING_AN,
            [FactorObservation(ts_code="600519.SH", observed_on=date(2024, 1, 2), factor=1.0)],
        )


def test_two_different_factors_on_one_day_are_refused_and_an_identical_repeat_collapses() -> None:
    day = date(2024, 1, 2)
    with pytest.raises(AdjustmentError, match="two adjustment factors on 2024-01-02"):
        build_adjustment_history(
            PING_AN,
            [
                FactorObservation(ts_code=PING_AN, observed_on=day, factor=1.0),
                FactorObservation(ts_code=PING_AN, observed_on=day, factor=2.0),
            ],
        )
    collapsed = build_adjustment_history(
        PING_AN,
        [
            FactorObservation(ts_code=PING_AN, observed_on=day, factor=1.0),
            FactorObservation(ts_code=PING_AN, observed_on=day, factor=1.0),
        ],
    )
    assert len(collapsed.observations) == 1


@pytest.mark.parametrize(
    "factor",
    [0.0, -1.5, float("nan"), float("inf")],
)
def test_a_factor_that_cannot_scale_a_price_is_refused(factor: float) -> None:
    with pytest.raises(AdjustmentError, match="must be a finite positive float"):
        build_adjustment_history(
            PING_AN,
            [FactorObservation(ts_code=PING_AN, observed_on=date(2024, 1, 2), factor=factor)],
        )


def test_an_integer_factor_is_refused_because_bool_is_an_integer() -> None:
    """`True` is an `int` and `int` is not `float`; accepting either would let a
    `True` through as the factor 1.0 and silently make every adjusted price the raw one."""
    for value in (1, True):
        with pytest.raises(AdjustmentError, match="must be a finite positive float"):
            build_adjustment_history(
                PING_AN,
                [
                    FactorObservation(
                        ts_code=PING_AN,
                        observed_on=date(2024, 1, 2),
                        factor=value,  # type: ignore[arg-type]
                    )
                ],
            )


def test_a_float_subclass_is_refused_because_that_is_what_the_exact_type_check_is_for() -> None:
    """The check is `type(...) is float`, and this is the case that distinguishes it.

    `isinstance(True, float)` is already `False`, so `bool` does not tell the two spellings
    apart -- rewriting the guard as `isinstance` changes nothing for `True` or `1`. What only
    the exact type refuses is a **subclass**, and a subclass is where the danger actually is:
    the factor is about to be multiplied into a price, so the arithmetic that runs must be
    `float`'s own and not an override. `numpy.float64` is such a subclass, which makes this
    reachable rather than theoretical.
    """

    class _ScaledFloat(float):
        def __mul__(self, other: object) -> float:  # pragma: no cover - never reached
            return 0.0

    with pytest.raises(AdjustmentError, match="must be a finite positive float"):
        build_adjustment_history(
            PING_AN,
            [
                FactorObservation(
                    ts_code=PING_AN, observed_on=date(2024, 1, 2), factor=_ScaledFloat(125.049)
                )
            ],
        )


def test_the_history_is_sorted_ascending_whatever_order_it_arrives_in() -> None:
    history = build_adjustment_history(
        PING_AN,
        [
            FactorObservation(ts_code=PING_AN, observed_on=date(2024, 10, 10), factor=127.7841),
            FactorObservation(ts_code=PING_AN, observed_on=date(2024, 1, 2), factor=116.713),
        ],
    )
    assert [entry.observed_on for entry in history.observations] == [
        date(2024, 1, 2),
        date(2024, 10, 10),
    ]


# --- what the real data is *not* ------------------------------------------------------


def test_the_factor_is_not_monotone_and_this_contract_does_not_pretend_it_is() -> None:
    """A monotonicity assertion would fail on the real series, in two different ways.

    Eight of `000001.SZ`'s own 8,627 rows step *down*. Five are genuine 1991-93 movements
    of up to 6.4%; three are pure four-to-three-decimal rounding, worth 0.0002%.
    """
    history = _history()
    factors = [entry.factor for entry in history.observations]
    decreases = [
        (history.observations[index].observed_on, factors[index - 1], factors[index])
        for index in range(1, len(factors))
        if factors[index] < factors[index - 1]
    ]
    assert len(decreases) == 8
    assert (date(1991, 11, 11), 2.608, 2.463) in decreases
    assert (date(2024, 6, 25), 125.0496, 125.0493) in decreases


def test_the_compression_ratio_counts_stored_rows_and_not_distinct_values() -> None:
    """Three numbers one apart, and the docstrings quote the third.

    43 distinct values, 42 change points, 44 stored rows -- because the window's closing
    observation (2026-08-07, 139.008) repeats 2026-06-12's value and is kept anyway, which is
    the whole point of `load_bearing_observations`' third rule. 8,627 / 43 is 201x and 8,627 /
    44 is 196x; the second is the reduction the partition actually achieves.
    """
    whole_history = _observations((*PING_AN_CHANGE_POINTS, ("20260807", 139.008)))
    history = _history()

    assert len({entry.factor for entry in history.observations}) == 43
    assert len(history.change_points()) == 42
    stored = load_bearing_observations(whole_history)
    assert len(stored) == 44
    assert stored[-1].observed_on == date(2026, 8, 7)
    assert round(8627 / len(stored)) == 196


def test_the_known_limitations_are_named_rather_than_argued_away() -> None:
    """Set **equality**, not three memberships.

    The previous version checked that three of the five codes were present, which left the
    other two outside the sample it drew its verdict from -- so deleting either of them, or
    adding a sixth entry, changed nothing. That is the same shape as this repository's Task 28
    Critical: a universal claim whose test set excludes the counterexamples. An exact set is
    the only form in which "these are the boundaries, and only these" is a checkable claim.
    """
    codes = {entry.code for entry in KNOWN_ADJUSTMENT_LIMITATIONS}
    assert codes == {
        "factor_is_not_monotone",
        "delisted_securities_carry_unstable_factors",
        "silent_truncation_at_the_response_cap",
        "no_revision_history",
        "dates_are_not_the_stored_trading_calendar",
        "suspension_is_invisible",
    }
    assert len(KNOWN_ADJUSTMENT_LIMITATIONS) == len(codes), "a code is declared twice"


def test_the_delisted_instability_is_filed_as_a_data_defect_not_as_a_market_move() -> None:
    """`600069.SH`'s -89.7% is not a corporate action, and the split matters because the entry
    a reader finds it under is the entry that tells them what it means.

    Measured: its last daily bar is 2020-08-20 and its `delist_date` 2020-08-27, yet the
    factor series runs to 2025-04-28 and *alternates* between two constants 17 times after
    2020 (6.415 -> 0.6604 on 2022-06-24, back on 2022-09-06, ... , 6.415 -> 0.6604 on
    2024-06-17 -- the step that produced the -89.7%). A cumulative factor cannot return to a
    level it left, so this is upstream breakage. Listing it beside 1991-93's genuine -6.4%
    told the reader the factor really can fall by 90% in a day.
    """
    by_code = {entry.code: entry.detail for entry in KNOWN_ADJUSTMENT_LIMITATIONS}
    assert "600069.SH" not in by_code["factor_is_not_monotone"]
    delisted = by_code["delisted_securities_carry_unstable_factors"]
    assert "600069.SH" in delisted
    assert "2020-08-27" in delisted  # the delisting the factor rows outlive
    assert "2025-04-28" in delisted  # and how long they outlive it by
    # suspension_is_invisible covers "a factor row is no evidence of a session"; it does not
    # cover "the values in those rows are bad", which is why a second entry exists.
    assert "600069.SH" not in by_code["suspension_is_invisible"]


# --- stored rows ----------------------------------------------------------------------


def test_stored_rows_rebuild_one_history_per_security() -> None:
    histories = adjustment_histories_from_panel_rows(
        [
            (PING_AN, "2024-01-02", 116.713),
            (PING_AN, "2024-12-31", 127.7841),
            ("600519.SH", "2024-01-02", 7.8576),
            ("600519.SH", "2024-12-31", 8.1454),
        ]
    )
    assert sorted(histories) == ["000001.SZ", "600519.SH"]
    assert histories["600519.SH"].factor_on(date(2024, 6, 1)) == 7.8576


def test_a_stored_row_of_the_wrong_width_is_refused() -> None:
    with pytest.raises(AdjustmentError, match="row 0 has 2 values, expected 3"):
        adjustment_histories_from_panel_rows([(PING_AN, "2024-01-02")])


def test_a_stored_factor_that_is_not_a_float_is_refused() -> None:
    with pytest.raises(AdjustmentError, match="row 0: adj_factor must be a float"):
        adjustment_histories_from_panel_rows([(PING_AN, "2024-01-02", "116.713")])


def test_a_stored_subject_that_is_not_text_is_refused() -> None:
    """A `None` subject would otherwise become the key of its own history, so a whole
    security's factors would live under a code no caller can ask for."""
    with pytest.raises(AdjustmentError, match="row 0: subject must be a non-empty string"):
        adjustment_histories_from_panel_rows([(None, "2024-01-02", 116.713)])


def test_a_stored_date_that_is_not_text_at_all_is_refused() -> None:
    with pytest.raises(AdjustmentError, match="row 0: factor_date must be an ISO date string"):
        adjustment_histories_from_panel_rows([(PING_AN, date(2024, 1, 2), 116.713)])


def test_a_malformed_ts_code_is_refused_by_the_constructor() -> None:
    with pytest.raises(AdjustmentError, match="ts_code must be a non-empty string"):
        build_adjustment_history(
            " 000001.SZ ",
            [FactorObservation(ts_code=PING_AN, observed_on=date(2024, 1, 2), factor=1.0)],
        )


def test_a_stored_date_that_is_not_iso_text_is_refused() -> None:
    with pytest.raises(AdjustmentError, match="row 0: factor_date is not an ISO date"):
        adjustment_histories_from_panel_rows([(PING_AN, "2024/01/02", 116.713)])


def test_tushares_own_yyyymmdd_form_parses_because_python_calls_it_iso_too() -> None:
    """Pinned rather than assumed: `date.fromisoformat` accepts ISO 8601 *basic* format from
    Python 3.11, so `"20240102"` is not the rejection case it looks like. The provider still
    writes the extended form -- this test exists so that a future reader does not "fix" a
    rejection that was never there."""
    histories = adjustment_histories_from_panel_rows([(PING_AN, "20240102", 116.713)])
    assert histories[PING_AN].covered_from == date(2024, 1, 2)


def test_the_panel_column_order_is_one_tuple_used_for_both_directions() -> None:
    assert ADJUSTMENT_PANEL_COLUMNS == ("subject", "factor_date", "adj_factor")
    assert ADJ_FACTOR_DATASET == "adj_factor"


# --- the join with the calendar and the universe --------------------------------------


JUNE_2024_WEEK: tuple[bool, ...] = (True, True, True, True, True, False, False)
"""Monday 2024-06-24 through Sunday 2024-06-30, as the SZSE calendar has it."""


def _calendar():
    return build_trading_calendar(
        "SZSE",
        [
            CalendarDay(
                calendar_date=date(2024, 6, 24) + timedelta(days=offset), is_trading=trading
            )
            for offset, trading in enumerate(JUNE_2024_WEEK)
        ],
    )


def _universe():
    return build_stock_universe(
        snapshot_date=date(2026, 8, 8),
        securities=[
            SecurityLifecycle(
                ts_code=PING_AN, exchange="SZSE", listed_on=date(1991, 4, 3), delisted_on=None
            ),
            SecurityLifecycle(
                ts_code="000005.SZ",
                exchange="SZSE",
                listed_on=date(1990, 12, 10),
                delisted_on=date(2024, 4, 26),
            ),
        ],
    )


def test_the_cross_section_covers_exactly_the_names_listed_on_that_session() -> None:
    histories = adjustment_histories_from_panel_rows(
        [(PING_AN, "2024-06-24", 125.0496), (PING_AN, "2024-06-28", 125.049)]
    )
    factors = adjustment_factors_on(
        histories, universe=_universe(), calendar=_calendar(), day=date(2024, 6, 25)
    )
    # 000005.SZ delisted 2024-04-26, so it is not in the section at all -- and its absence
    # from `histories` is therefore not a hole.
    assert factors == ((PING_AN, 125.0496),)


def test_a_listed_security_with_no_factor_history_blocks_the_whole_cross_section() -> None:
    histories = adjustment_histories_from_panel_rows(
        [("600519.SH", "2024-06-24", 8.0204), ("600519.SH", "2024-06-28", 8.0205)]
    )
    with pytest.raises(AdjustmentError, match=r"000001\.SZ was listed on 2024-06-25"):
        adjustment_factors_on(
            histories, universe=_universe(), calendar=_calendar(), day=date(2024, 6, 25)
        )


def test_a_day_the_exchange_was_shut_has_no_cross_section() -> None:
    histories = adjustment_histories_from_panel_rows([(PING_AN, "2024-06-24", 125.0496)])
    with pytest.raises(AdjustmentError, match="2024-06-29 is not an open session"):
        adjustment_factors_on(
            histories, universe=_universe(), calendar=_calendar(), day=date(2024, 6, 29)
        )
