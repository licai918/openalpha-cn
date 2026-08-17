"""Turnover, coverage and capacity for a quantile study (`V2-P3-007`).

Six properties this file exists to hold, and the first is the issue's acceptance criterion:

1. **A signal that is statistically attractive and not implementable is visible.**
   `test_the_same_ic_and_the_same_funnel_are_told_apart_by_the_top_groups_execution_rate` runs
   **one** cross section -- so the IC is not merely equal but the same object's -- against two
   markets that refuse opposite ends of it, and requires the period-level funnel to be identical
   while the long group's execution rate differs by 0.75. That is the whole argument for
   publishing a per-group table: nothing above the group level can see it.
2. **Every one of the four funnel stages moves alone.** Four fixtures, each of which changes one
   security's fate at one stage, and each asserts the other three rates are unchanged. A
   `coverage` that only ever moved all four together would be one number wearing four names.
3. **The per-group counts survive the code they exist for.** Under `unfillable_after_execution`
   `PeriodPortfolio.groups` is empty, so the decomposition is derived from the rejections; under
   `measured` both readings exist and the module requires them to agree, which is checked in both
   directions -- a fixture that agrees and a doctored cross section that does not.
4. **The rebalance schedule is measured and never invented.** An overlapping series has no
   holdings state and says so; a rolling one retains what both ends rank highest; and the two
   turnover readings are pinned as *different numbers* on a fixture built to separate them, since
   a fixture where lot rounding did not bite would let either be dropped.
5. **The capacity's assumptions are all reachable from a test.** The declared cap scales it
   linearly, the `min` binds and is named, an unpriced holding refuses the group rather than
   averaging the rest, and the unit is pinned against the panel engine's own constant. The
   headline is driven on **real published turnover**: `000569.SZ`'s 2001-01-02 session gives a
   `capital_multiple` of 0.658 against a ¥100,000 position.
6. **Every number reported has a fixture that separates it from its neighbour.** The twelve-name
   census is built so that the four group cells, the five funnel counts and the three rebalance
   counts are all distinct, because `V2-P3-006`'s own review found five fields whose assertions
   could not tell two answers apart under 100% line coverage.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    CostSchedule,
    MarketBar,
)
from openalpha_cn.backtest.factor_ic import (
    TIER_ADMITTED_CODES,
    TIER_VALUE_CODES,
    FactorICSpec,
    FactorICStudy,
    ICCensus,
    ICCrossSection,
    neutralized_cross_section,
    processed_cross_section,
    raw_cross_section,
)
from openalpha_cn.backtest.factor_portfolio import (
    HOLDING_OUTCOME_ORDER,
    PRE_EXECUTION_COVERAGE,
    PeriodPortfolio,
    PortfolioCensus,
    PositionRejection,
    QuantilePortfolioSpec,
    QuantilePortfolioStudy,
)
from openalpha_cn.backtest.factor_tradeability import (
    CAPACITY_COVERAGE_CODES,
    CNY_PER_TURNOVER_UNIT,
    EXCLUDED_OUTCOME_ORDER,
    KNOWN_TRADEABILITY_LIMITATIONS,
    MAXIMUM_REBALANCES,
    MINIMUM_REBALANCES,
    TRADEABILITY_LIMITATION_CODES,
    TURNOVER_COVERAGE_CODES,
    TURNOVER_COVERAGE_ORDER,
    CoverageFunnel,
    FactorTradeabilityError,
    GroupCapacity,
    GroupCoverage,
    Rebalance,
    SessionLiquidity,
    TradeabilitySpec,
    TradeabilityStudy,
    TurnoverSeries,
    liquidity_from_amount,
)
from openalpha_cn.domain.adjustment import FactorObservation as AdjustmentFactor
from openalpha_cn.domain.adjustment import build_adjustment_history
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.factor import FactorDefinition, FactorField, FactorObservation
from openalpha_cn.domain.factor_neutralization import NeutralizedFactorObservation
from openalpha_cn.domain.factor_transform import ProcessedFactorObservation
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import (
    LabelWindow,
    OutcomeLabel,
    build_label_window,
    halt_corpus_for_years,
    label_outcome,
)
from openalpha_cn.domain.price_limits import PriceLimit
from openalpha_cn.domain.stock_universe import SecurityLifecycle, StockUniverse
from openalpha_cn.domain.trading_calendar import CalendarDay, build_trading_calendar
from openalpha_cn.panel_factors import CNY_PER_AMOUNT_UNIT

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

AS_OF: Final[datetime] = datetime(2026, 6, 10, 8, 30, tzinfo=UTC)
"""16:30 Asia/Shanghai on 2026-06-10, so the prediction day is the 10th and the entry the 11th."""

AS_OF_2: Final[datetime] = datetime(2026, 6, 11, 8, 30, tzinfo=UTC)
"""The next session's `as_of`. At `1d` its entry is 2026-06-12, which is `AS_OF`'s exit exactly --
the tightest schedule on which a holdings state exists at all."""

AS_OF_3: Final[datetime] = datetime(2026, 6, 12, 8, 30, tzinfo=UTC)
"""The third, so a series can carry two rebalances and a mean over them is not one number."""

CALENDAR = build_trading_calendar(
    "SZSE",
    [
        CalendarDay(
            calendar_date=date(2026, 6, 1) + timedelta(days=offset),
            is_trading=(date(2026, 6, 1) + timedelta(days=offset)).weekday() < 5,
        )
        for offset in range(180)
    ],
)

UNIVERSE = StockUniverse(
    snapshot_date=date(2026, 12, 31),
    securities=tuple(
        SecurityLifecycle(ts_code=f"{index:06d}.SZ", exchange="SZSE", listed_on=date(1991, 4, 3))
        for index in range(1, 60)
    ),
)

CAPITAL: Final[Decimal] = Decimal("100000")
"""`V2-P3-006`'s own test capital: enough notional at a ¥100 close that the ¥5 minimum commission
never binds. The capacity tests that vary it say so."""

CAP: Final[Decimal] = Decimal("0.01")
"""One percent of a session's turnover. A declared number and nothing else in this file assumes
it; `test_the_participation_cap_scales_the_capacity_linearly` drives three of them."""

TURNOVER: Final[float] = 1_000_000.0
"""A flat `daily.amount` in thousands of yuan, so a fixture that does not care about liquidity
gives every held name the same ¥1,000,000,000 and the `min` cannot be a fixture accident."""


def code(index: int) -> str:
    return f"{index:06d}.SZ"


def _definition(
    direction: str = "higher_is_better", *, key: str = "probe_trade"
) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        version=1,
        family="momentum_reversal",
        direction=direction,  # type: ignore[arg-type]
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )


def _quantile_spec(
    direction: str = "higher_is_better",
    *,
    group_count: int = 3,
    min_securities_per_group: int = 2,
    position_capital: Decimal = CAPITAL,
    min_periods: int = 2,
    key: str = "probe_trade",
) -> QuantilePortfolioSpec:
    return QuantilePortfolioSpec(
        definition=_definition(direction, key=key),
        group_count=group_count,
        min_securities_per_group=min_securities_per_group,
        position_capital=position_capital,
        min_periods=min_periods,
    )


def _spec(*, participation_cap: Decimal = CAP, min_rebalances: int = 1) -> TradeabilitySpec:
    return TradeabilitySpec(participation_cap=participation_cap, min_rebalances=min_rebalances)


def _study(
    spec: TradeabilitySpec | None = None,
    *,
    portfolio: QuantilePortfolioSpec | None = None,
) -> TradeabilityStudy:
    return TradeabilityStudy(
        spec if spec is not None else _spec(),
        portfolio=portfolio if portfolio is not None else _quantile_spec(),
    )


def _daily(ts_code: str, day: date, *, close: float, pre_close: float) -> DailyBar:
    return DailyBar(
        ts_code=ts_code,
        trade_date=day,
        open=close,
        high=close,
        low=close,
        close=close,
        pre_close=pre_close,
        pct_chg=(close / pre_close - 1.0) * 100.0,
        vol=1000.0,
        amount=10000.0,
    )


def _window(*, as_of: datetime = AS_OF, horizon: str = "1d") -> LabelWindow:
    return build_label_window(
        as_of=as_of, zone=SHANGHAI, horizon=parse_horizon(horizon), calendar=CALENDAR
    )


def _label(
    ts_code: str,
    *,
    window: LabelWindow | None = None,
    total_return: float = 0.0,
    start_price: float = 100.0,
    locked: bool = False,
) -> OutcomeLabel:
    """A real `label_outcome` over a synthetic path whose cumulative adjusted return is chosen.

    `locked=True` bands the entry session at its own close, so `domain/labels.py` refuses it with
    `locked_at_limit` and the security is `unlabelled` -- the label contract's own step of the
    funnel, provoked by the label contract rather than simulated.
    """
    span = window if window is not None else _window()
    growth = (1.0 + total_return) ** (1.0 / span.session_count)
    bars: dict[date, DailyBar] = {}
    price = start_price
    for position, day in enumerate(span.sessions):
        if position == 0:
            bars[day] = _daily(ts_code, day, close=price, pre_close=price)
            continue
        moved = price * growth
        bars[day] = _daily(ts_code, day, close=moved, pre_close=price)
        price = moved
    limits = {
        day: PriceLimit(
            ts_code=ts_code,
            trade_date=day,
            up_limit=(
                bars[day].close if locked and day == span.entry_day else 10_000.0 + start_price
            ),
            down_limit=0.01,
        )
        for day in span.sessions
    }
    return label_outcome(
        span,
        ts_code=ts_code,
        bars=bars,
        factors=build_adjustment_history(
            ts_code,
            [
                AdjustmentFactor(ts_code=ts_code, observed_on=day, factor=1.0)
                for day in span.sessions
            ],
        ),
        limits=limits,
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=UNIVERSE,
    )


def _observation(
    subject: str,
    value: float | None,
    *,
    as_of: datetime = AS_OF,
    coverage: str = "computed",
) -> FactorObservation:
    return FactorObservation(
        subject=subject,
        as_of=as_of,
        value=value,
        coverage=coverage,  # type: ignore[arg-type]
        factor_id="fct_probe",
        manifest_id="fmn_probe",
        input_row_count=1,
        input_session_first=date(2026, 6, 10),
        input_session_last=date(2026, 6, 10),
    )


def _bar(
    subject: str,
    day: date,
    *,
    close: float,
    previous_close: float,
    board: str = "main",
    suspended: bool = False,
) -> MarketBar:
    price = Decimal(str(round(close, 2)))
    return MarketBar(
        subject=subject,
        trade_date=day,
        board=board,  # type: ignore[arg-type]
        previous_close=Decimal(str(round(previous_close, 2))),
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=suspended,
        is_st=False,
    )


def _inputs(
    ranks: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
    *,
    scores: dict[int, float] | None = None,
    prices: dict[int, float] | None = None,
    price: float = 100.0,
    coverages: dict[int, str] | None = None,
    unlabelled: tuple[int, ...] = (),
    unmatched: tuple[int, ...] = (),
    drop_bars: tuple[int, ...] = (),
    suspend_entry: tuple[int, ...] = (),
    as_of: datetime = AS_OF,
    horizon: str = "1d",
) -> tuple[ICCrossSection, dict[str, tuple[MarketBar, MarketBar]]]:
    """One raw cross section and its bar pairs, with a knob for each stage of the funnel.

    `coverages` reaches the **factor engine's** step (a non-`computed` code carries no value),
    `unlabelled` the **label contract's** (a limit-locked entry session), `unmatched` the caller's
    short read, and `drop_bars` / `suspend_entry` the **execution policy's**. Each is a separate
    argument precisely so a test can move one and require the other three rates unchanged.
    """
    window = _window(as_of=as_of, horizon=horizon)
    labels: dict[str, OutcomeLabel] = {}
    observations: list[FactorObservation] = []
    bars: dict[str, tuple[MarketBar, MarketBar]] = {}
    for rank in ranks:
        subject = code(rank)
        coverage = (coverages or {}).get(rank, "computed")
        total_return = rank / 100.0
        close = (prices or {}).get(rank, price)
        observations.append(
            _observation(
                subject,
                (scores or {}).get(rank, float(rank)) if coverage == "computed" else None,
                as_of=as_of,
                coverage=coverage,
            )
        )
        if coverage != "computed" or rank in unmatched:
            continue
        labels[subject] = _label(
            subject,
            window=window,
            total_return=total_return,
            start_price=close,
            locked=rank in unlabelled,
        )
        if rank in unlabelled or rank in drop_bars:
            continue
        bars[subject] = (
            _bar(
                subject,
                window.entry_day,
                close=close,
                previous_close=close,
                suspended=rank in suspend_entry,
            ),
            _bar(
                subject,
                window.exit_day,
                close=close * (1.0 + total_return),
                previous_close=close * (1.0 + total_return),
            ),
        )
    return (
        raw_cross_section(as_of=as_of, observations=observations, labels=labels),
        bars,
    )


FUNNEL_RANKS: Final[tuple[int, ...]] = tuple(range(1, 10))
"""Nine securities, so a fixture may remove one or two at any stage and the survivors still fill
a three-way cut at the declared floor of two.

Six -- the width every other fixture here uses -- is exactly `minimum_cross_section`, so removing
one turns every funnel test into an `insufficient_sample` test measuring the wrong thing.
"""


def _processed_inputs(
    ranks: tuple[int, ...] = (1, 2, 3, 4, 5, 6, 7),
    *,
    imputed: tuple[int, ...] = (),
    as_of: datetime = AS_OF,
) -> tuple[ICCrossSection, dict[str, tuple[MarketBar, MarketBar]]]:
    """A `factor_proc_*` cross section, so the tier travels and `imputed` can be offered.

    An imputed row's `source_coverage` is `input_missing` rather than `computed`, which
    `domain/factor_transform.py` requires: a security that had a measured value gave the
    missing-value policy nothing to stand in for.
    """
    window = _window(as_of=as_of)
    labels: dict[str, OutcomeLabel] = {}
    observations: list[ProcessedFactorObservation] = []
    bars: dict[str, tuple[MarketBar, MarketBar]] = {}
    for rank in ranks:
        subject = code(rank)
        coverage = "imputed" if rank in imputed else "processed"
        labels[subject] = _label(subject, window=window, total_return=rank / 100.0)
        observations.append(
            ProcessedFactorObservation(
                subject=subject,
                as_of=as_of,
                value=float(rank),
                coverage=coverage,  # type: ignore[arg-type]
                transform_id="ftx_probe",
                transform_manifest_id="ftm_probe",
                source_factor_id="fct_probe",
                source_manifest_id="fmn_probe",
                source_coverage="input_missing" if rank in imputed else "computed",
            )
        )
        if rank in imputed:
            continue
        bars[subject] = (
            _bar(subject, window.entry_day, close=100.0, previous_close=100.0),
            _bar(
                subject,
                window.exit_day,
                close=100.0 * (1.0 + rank / 100.0),
                previous_close=100.0 * (1.0 + rank / 100.0),
            ),
        )
    return (
        processed_cross_section(as_of=as_of, observations=observations, labels=labels),
        bars,
    )


def _period(
    section: ICCrossSection,
    bars: dict[str, tuple[MarketBar, MarketBar]],
    *,
    portfolio: QuantilePortfolioSpec | None = None,
    costs: CostSchedule | None = None,
) -> PeriodPortfolio:
    study = QuantilePortfolioStudy(
        portfolio if portfolio is not None else _quantile_spec(),
        execution=AShareExecutionPolicy(costs=costs or CostSchedule()),
    )
    return study.measure(section, bars=bars)


def _liquidity(
    section: ICCrossSection,
    *,
    amounts: dict[int, float] | None = None,
    amount: float = TURNOVER,
    day: date | None = None,
    skip: tuple[int, ...] = (),
) -> dict[str, SessionLiquidity]:
    """`daily.amount` in thousands of yuan for every admitted pair, keyed as `measure` wants it."""
    session = day if day is not None else section.entry_day
    offered: dict[str, SessionLiquidity] = {}
    for pair in section.pairs:
        rank = int(pair.subject.split(".")[0])
        if rank in skip:
            continue
        offered[pair.subject] = liquidity_from_amount(
            subject=pair.subject,
            trade_date=session,
            amount=(amounts or {}).get(rank, amount),
        )
    return offered


# --------------------------------------------------------------------------------------------
# The acceptance criterion: a signal that scores well and cannot be traded
# --------------------------------------------------------------------------------------------


ACCEPTANCE_RANKS: Final[tuple[int, ...]] = tuple(range(1, 13))
"""Twelve securities scored 1..12 whose forward return is `rank / 100`, so the rank correlation is
one and the cut into three groups is 4, 4, 4. The refusals below are placed **three** at a time,
which leaves the refused group holding exactly one name -- so the period is still `measured` and
the two markets differ in which *end* of the same ordering they refuse and in nothing else."""


def _acceptance(refused: tuple[int, ...]) -> tuple[ICCrossSection, PeriodPortfolio]:
    """One cross section against a market that suspends `refused`'s entry sessions.

    The cross section is built **without** reference to `refused`, so the two markets below share
    a `census`, a set of pairs and therefore an IC to the bit. Only the bars differ, and a bar is
    not an input to a correlation.
    """
    section, _tradeable = _inputs(ACCEPTANCE_RANKS)
    _same, refused_bars = _inputs(ACCEPTANCE_RANKS, suspend_entry=refused)
    portfolio = _quantile_spec(min_securities_per_group=1)
    return section, _period(section, refused_bars, portfolio=portfolio)


def test_the_same_ic_and_the_same_funnel_are_told_apart_by_the_top_groups_execution_rate() -> None:
    """**The acceptance criterion.** A factor that only scores on names nobody can trade.

    One cross section, twelve securities, a perfectly ordered rank correlation, and two markets:
    one suspends the three highest-scored names' entry sessions and the other the three lowest.
    The correlation cannot tell them apart because a `MarketBar` is not one of its inputs, and
    **neither can the period-level funnel** -- both markets refuse three of twelve, so
    `execution_rate` is `0.75` in each and every count above it is identical.

    What separates them is one row of one table. The long group's own execution rate is `0.25`
    where the signal loads on the refused names and `1.0` where it does not, and
    `top_group_execution_shortfall` -- the overall rate less that one -- is `+0.5` against
    `-0.25`. That is the whole argument for publishing the per-group decomposition rather than a
    coverage number: the finding lives at a level the aggregate averages away.

    The IC is `0.9999999999999998` rather than `1.0`, which is `MINIMUM_IC_SECURITIES`' own
    measured note about a perfectly ordered cross section and is asserted as the value it is.
    """
    section, unimplementable = _acceptance((10, 11, 12))
    control_section, control = _acceptance((1, 2, 3))
    point = FactorICStudy(
        FactorICSpec(definition=_definition(), method="spearman", min_securities=3, min_as_ofs=2)
    ).measure(section)
    study = _study(portfolio=_quantile_spec(min_securities_per_group=1))
    bad = study.measure(unimplementable, cross_section=section, liquidity=_liquidity(section))
    good = study.measure(
        control, cross_section=control_section, liquidity=_liquidity(control_section)
    )

    assert section == control_section
    assert point.coverage == "measured"
    assert point.ic == 0.9999999999999998
    assert bad.funnel == good.funnel
    assert bad.funnel.execution_rate == good.funnel.execution_rate == 0.75
    assert bad.top_group_execution_rate == 0.25
    assert good.top_group_execution_rate == 1.0
    assert bad.top_group_execution_shortfall == 0.5
    assert good.top_group_execution_shortfall == -0.25
    assert [row.held_count for row in bad.by_group] == [4, 4, 1]
    assert [row.held_count for row in good.by_group] == [1, 4, 4]


def test_a_long_group_the_market_refuses_entirely_is_a_shortfall_and_not_a_division() -> None:
    """The corner the shortfall is a **difference** rather than a ratio for.

    A factor whose long group is refused down to nothing is the sharpest case of the acceptance
    criterion and it is exactly the case a ratio cannot report: `top / overall` divides by a zero
    that a `min_securities_per_group` of 1 makes reachable. Four of twelve are refused, so the
    overall execution rate is `8 / 12` and the shortfall is that whole number -- and the period is
    `unfillable_after_execution`, `V2-P3-006`'s code for "the market emptied a group", with the
    per-group table still carrying every verdict.
    """
    section, bars = _inputs(ACCEPTANCE_RANKS, suspend_entry=(9, 10, 11, 12))
    portfolio = _quantile_spec(min_securities_per_group=1)
    period = _period(section, bars, portfolio=portfolio)
    report = _study(portfolio=portfolio).measure(
        period, cross_section=section, liquidity=_liquidity(section)
    )

    assert period.coverage == "unfillable_after_execution"
    assert period.groups == ()
    assert report.by_group[2].held_count == 0
    assert report.funnel.execution_rate == 8 / 12
    assert report.top_group_execution_rate == 0.0
    assert report.top_group_execution_shortfall == 8 / 12
    assert report.capacity_coverage == "no_holdings"
    assert report.capacity is None


def test_a_period_the_market_emptied_still_carries_its_per_group_decomposition() -> None:
    """The reading `V2-P3-006` says this issue exists to surface, and why the counts are derived.

    Under `unfillable_after_execution` the quantile study keeps the per-security verdicts and
    throws away the group tables, so a decomposition that read `PeriodPortfolio.groups` would be
    blind on exactly the period a reader opened the report for. The counts here come from the cut
    re-derived over the cross section's scores minus the rejection list, and the assertion that
    matters is that the two group rows differ -- a fixture in which every group lost the same
    number would not distinguish a per-group table from a period-level one.
    """
    section, bars = _inputs(suspend_entry=(1, 5, 6))
    period = _period(section, bars)
    report = _study().measure(period, cross_section=section, liquidity=_liquidity(section))

    assert period.coverage == "unfillable_after_execution"
    assert period.groups == ()
    assert [row.held_count for row in report.by_group] == [1, 2, 0]
    assert [row.scored_count for row in report.by_group] == [2, 2, 2]
    assert dict(report.by_group[2].excluded_by_outcome)["rejected_entry"] == 2
    assert dict(report.by_group[1].excluded_by_outcome)["rejected_entry"] == 0
    assert report.funnel.held_count == 3


# --------------------------------------------------------------------------------------------
# The coverage funnel: four stages, four owners, each separable
# --------------------------------------------------------------------------------------------


def test_a_cross_section_nothing_refuses_reports_every_stage_at_one() -> None:
    """The control every fixture below is a one-stage perturbation of."""
    section, bars = _inputs()
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    assert report.funnel == CoverageFunnel(
        universe_count=6,
        valued_count=6,
        admissible_count=6,
        scored_count=6,
        held_count=6,
    )
    assert report.funnel.value_rate == 1.0
    assert report.funnel.admission_rate == 1.0
    assert report.funnel.label_rate == 1.0
    assert report.funnel.execution_rate == 1.0
    assert report.funnel.implementable_rate == 1.0


def test_a_security_the_factor_could_not_value_moves_the_value_rate_alone() -> None:
    """Stage one, and it belongs to the **factor engine**.

    A raw observation under `input_missing` carries no value at all, so it never reaches a label,
    a cut or an order. The three rates below it stay at `1.0` because their denominators shrink
    with their numerators -- which is the property that makes four rates worth publishing instead
    of one, and which a fixture that moved two stages at once could not show.
    """
    section, bars = _inputs(FUNNEL_RANKS, coverages={2: "input_missing"})
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    assert report.funnel.universe_count == 9
    assert report.funnel.valued_count == 8
    assert report.funnel.value_rate == 8 / 9
    assert report.funnel.admission_rate == 1.0
    assert report.funnel.label_rate == 1.0
    assert report.funnel.execution_rate == 1.0
    assert report.funnel.implementable_rate == 8 / 9


def test_an_imputed_processed_value_is_valued_and_is_not_admissible() -> None:
    """Stage two, and it is the **only** cell in which the two tier tables differ.

    `TIER_VALUE_CODES["processed"]` is `{processed, imputed}` and
    `TIER_ADMITTED_CODES["processed"]` is `{processed}`; on raw and neutralised the two are the
    same frozenset, so `admission_rate` is identically `1.0` there and this tier is the only place
    the stage is observable at all. An imputed value is a number the transform manufactured and
    `V2-P3-003` stored under its own code precisely so a later consumer could decline it: it is
    **valued** and it is not **admissible**, and reporting one coverage number would have to pick
    one of those two and lose the other.

    The two tables are imported rather than restated, so this test also fails if either moves.
    """
    section, bars = _processed_inputs(imputed=(4,))
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    assert "imputed" in TIER_VALUE_CODES["processed"]
    assert "imputed" not in TIER_ADMITTED_CODES["processed"]
    assert report.tier == "processed"
    assert report.funnel.universe_count == 7
    assert report.funnel.valued_count == 7
    assert report.funnel.admissible_count == 6
    assert report.funnel.value_rate == 1.0
    assert report.funnel.admission_rate == 6 / 7
    assert report.funnel.label_rate == 1.0
    assert report.funnel.execution_rate == 1.0


def test_a_label_the_market_refused_moves_the_label_rate_alone() -> None:
    """Stage three, and it belongs to **`domain/labels.py`**.

    The security's entry session is banded at its own close, so the label contract refuses it with
    `locked_at_limit` -- one of the four refusals that fire on exactly the sessions a security
    moved hardest. It has a value and the tier admits the value; what it has no forward return
    for is the window, so it never enters the cut.
    """
    section, bars = _inputs(FUNNEL_RANKS, unlabelled=(3,))
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    assert section.census.unlabelled_count == 1
    assert report.funnel.valued_count == 9
    assert report.funnel.admissible_count == 9
    assert report.funnel.scored_count == 8
    assert report.funnel.value_rate == 1.0
    assert report.funnel.admission_rate == 1.0
    assert report.funnel.label_rate == 8 / 9
    assert report.funnel.execution_rate == 1.0


def test_a_security_the_caller_offered_no_label_for_is_admissible_and_unscored() -> None:
    """The other half of stage three, and it is a different finding with a different remedy.

    `ICCensus` counts a refused label and an absent one apart for `explain_unpriced`'s reason: a
    short read looks exactly like a refusal. Both land in the same funnel step here -- the step is
    "did this security get a forward return" -- and the census the report carries is what says
    which, so the funnel does not have to grow a fifth stage to keep them distinguishable.
    """
    section, bars = _inputs(FUNNEL_RANKS, unmatched=(3,))
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    assert section.census.unmatched_count == 1
    assert section.census.unlabelled_count == 0
    assert report.funnel.admissible_count == 9
    assert report.funnel.scored_count == 8
    assert report.funnel.label_rate == 8 / 9


def test_a_bar_the_policy_refused_moves_the_execution_rate_alone() -> None:
    """Stage four, and it belongs to **`AShareExecutionPolicy`**.

    A suspended entry bar is the policy's own first rejection branch and the label contract knows
    nothing about it -- the halt corpus this fixture builds is empty, so the label admits the name
    and the order is the thing that fails. That is the seam
    `KNOWN_EXECUTION_LIMITATIONS.the_registry_verdict_is_not_an_input` records, arriving as one
    row of a funnel.
    """
    section, bars = _inputs(FUNNEL_RANKS, suspend_entry=(3,))
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    assert report.funnel.scored_count == 9
    assert report.funnel.held_count == 8
    assert report.funnel.value_rate == 1.0
    assert report.funnel.admission_rate == 1.0
    assert report.funnel.label_rate == 1.0
    assert report.funnel.execution_rate == 8 / 9
    assert report.funnel.implementable_rate == 8 / 9


def test_the_implementable_rate_is_the_product_of_the_four_stages() -> None:
    """Every stage moved at once, and the one arithmetic claim the funnel's docstring makes.

    `implementable_rate` is computed as `held / universe` rather than multiplied, so that it
    survives a stage whose own rate is `None`. The claim that it *equals* the product is therefore
    a claim about four divisions rather than a definition, and it is measured to `1e-12` rather
    than asserted with `==` -- IEEE division is exactly rounded and the product of four of them
    need not be.
    """
    section, bars = _inputs(
        tuple(range(1, 13)),
        coverages={1: "input_missing", 2: "not_in_universe"},
        unlabelled=(3,),
        suspend_entry=(4, 5),
    )
    funnel = (
        _study()
        .measure(
            _period(section, bars, portfolio=_quantile_spec(min_securities_per_group=1)),
            cross_section=section,
            liquidity=_liquidity(section),
        )
        .funnel
    )

    assert (funnel.universe_count, funnel.valued_count) == (12, 10)
    assert (funnel.admissible_count, funnel.scored_count, funnel.held_count) == (10, 9, 7)
    assert funnel.value_rate == 10 / 12
    assert funnel.admission_rate == 1.0
    assert funnel.label_rate == 9 / 10
    assert funnel.execution_rate == 7 / 9
    assert funnel.implementable_rate is not None
    assert math.isclose(
        funnel.implementable_rate,
        (10 / 12) * 1.0 * (9 / 10) * (7 / 9),
        rel_tol=1e-12,
        abs_tol=0.0,
    )


def test_a_rate_over_an_empty_denominator_is_none_rather_than_zero() -> None:
    """`None` and `0.0` are opposite claims -- "there was nothing to ask" against "none of them
    made it" -- and a funnel over an empty stage has to be able to say the first.

    The second funnel is why `implementable_rate` is computed as `held / universe` rather than as
    the product of the four: three of its stages have no denominator and it is `0.0` anyway, which
    is the true statement about a universe five of whose securities reached nothing.
    """
    funnel = CoverageFunnel(
        universe_count=0, valued_count=0, admissible_count=0, scored_count=0, held_count=0
    )

    empty_stage = CoverageFunnel(
        universe_count=5, valued_count=0, admissible_count=0, scored_count=0, held_count=0
    )

    assert funnel.value_rate is None
    assert funnel.admission_rate is None
    assert funnel.label_rate is None
    assert funnel.execution_rate is None
    assert funnel.implementable_rate is None
    assert empty_stage.value_rate == 0.0
    assert empty_stage.admission_rate is None
    assert empty_stage.label_rate is None
    assert empty_stage.execution_rate is None
    assert empty_stage.implementable_rate == 0.0


def test_a_funnel_that_widens_at_any_step_is_refused() -> None:
    """Each of the four steps, one at a time, so no single comparison can carry the others."""
    counts = {
        "universe_count": 10,
        "valued_count": 9,
        "admissible_count": 8,
        "scored_count": 7,
        "held_count": 6,
    }
    for name, value in (
        ("valued_count", 11),
        ("admissible_count", 10),
        ("scored_count", 9),
        ("held_count", 8),
    ):
        with pytest.raises(FactorTradeabilityError, match="has lost the direction"):
            CoverageFunnel(**{**counts, name: value})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="cannot be negative"):
        CoverageFunnel(**{**counts, "held_count": -1})  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# The per-group table, and the join it is derived through
# --------------------------------------------------------------------------------------------


def test_a_group_exclusion_table_is_keyed_by_the_quantile_studys_own_outcomes() -> None:
    """The table's keys are `V2-P3-006`'s vocabulary minus `held`, derived and not restated.

    A fifth `HoldingOutcome` would arrive here without anybody remembering to add it, and a
    reordering of the four would fail rather than silently transposing two columns of every
    report. Both directions are asserted: the derived tuple against the source, and a
    hand-constructed row keyed the other way round.
    """
    section, bars = _inputs(suspend_entry=(3,))
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    census = PortfolioCensus(
        offered_count=0,
        held_count=0,
        excluded_by_outcome=tuple((name, 0) for name in EXCLUDED_OUTCOME_ORDER),
        unattempted_count=0,
    )

    assert tuple(code for code in HOLDING_OUTCOME_ORDER if code != "held") == EXCLUDED_OUTCOME_ORDER
    assert EXCLUDED_OUTCOME_ORDER == (
        "unbarred",
        "below_board_minimum",
        "rejected_entry",
        "rejected_exit",
    )
    assert tuple(name for name, _count in census.excluded_by_outcome) == EXCLUDED_OUTCOME_ORDER
    for row in report.by_group:
        assert tuple(name for name, _count in row.excluded_by_outcome) == EXCLUDED_OUTCOME_ORDER
    with pytest.raises(FactorTradeabilityError, match="cannot be told from one whose count"):
        GroupCoverage(
            group=0,
            scored_count=1,
            held_count=1,
            excluded_by_outcome=tuple(reversed(EXCLUDED_OUTCOME_ORDER_ZEROS)),
        )


EXCLUDED_OUTCOME_ORDER_ZEROS: Final[tuple[tuple[str, int], ...]] = tuple(
    (name, 0) for name in EXCLUDED_OUTCOME_ORDER
)
"""The four cells at zero, so a test that only wants to perturb the *order* does not also have to
perturb the counts."""


def test_the_per_group_counts_are_the_cut_minus_the_rejections_and_agree_with_the_groups() -> None:
    """The derivation and its cross-check, on a period where both readings exist.

    Under `measured` the module has two ways to know who was held -- the group tables and the
    rejection list against the re-derived cut -- and it requires them to agree. The fixture holds
    2, 3 and 4 positions so a group's held count cannot be confused with its index, which is the
    shape `V2-P3-006`'s census fixture was built in and for the same measured reason.
    """
    section, bars = _inputs(tuple(range(1, 13)), drop_bars=(1,), suspend_entry=(2, 5))
    period = _period(section, bars)
    report = _study().measure(period, cross_section=section, liquidity=_liquidity(section))

    assert period.coverage == "measured"
    assert [row.held_count for row in report.by_group] == [2, 3, 4]
    assert [row.scored_count for row in report.by_group] == [4, 4, 4]
    assert [len(group.holdings) for group in period.groups] == [2, 3, 4]
    assert dict(report.by_group[0].excluded_by_outcome) == {
        "unbarred": 1,
        "below_board_minimum": 0,
        "rejected_entry": 1,
        "rejected_exit": 0,
    }
    assert report.by_group[0].execution_rate == 0.5


def test_a_cross_section_that_re_derives_another_cut_is_refused() -> None:
    """The direction the census equality check cannot cover: the same census, a different cut.

    Two cross sections can agree on every count and disagree on which security scored what, and a
    report built from the wrong one would decompose this period's rejections against another
    period's ordering. The scores are reversed here, so every count is identical and every group
    assignment is not.
    """
    section, bars = _inputs()
    period = _period(section, bars)
    reversed_scores = {rank: float(7 - rank) for rank in range(1, 7)}
    other, _bars = _inputs(scores=reversed_scores)

    assert other.census == section.census
    with pytest.raises(FactorTradeabilityError, match="the cut re-derived"):
        _study().measure(period, cross_section=other, liquidity={})


def test_a_period_refused_before_the_cut_carries_no_group_table_and_no_capacity() -> None:
    """The three `PRE_EXECUTION_COVERAGE` codes: no order was placed, so there is nothing to
    decompose and nothing to size. The funnel is still reported, because the first three of its
    steps happened whatever the cut then did."""
    section, bars = _inputs((1, 2, 3))
    period = _period(section, bars)
    report = _study().measure(period, cross_section=section, liquidity=_liquidity(section))

    assert period.coverage == "insufficient_sample"
    assert period.coverage in PRE_EXECUTION_COVERAGE
    assert report.by_group == ()
    assert report.top_group_execution_rate is None
    assert report.top_group_execution_shortfall is None
    assert report.capacity_coverage == "no_holdings"
    assert report.funnel.scored_count == 3
    assert report.funnel.held_count == 0
    assert report.funnel.execution_rate == 0.0


def test_a_group_coverage_that_does_not_add_up_or_holds_nobodys_names_is_refused() -> None:
    """The row's own arithmetic, which is `PortfolioCensus`' at the scale of one quantile."""
    with pytest.raises(FactorTradeabilityError, match="has lost one of them"):
        GroupCoverage(
            group=1,
            scored_count=5,
            held_count=2,
            excluded_by_outcome=EXCLUDED_OUTCOME_ORDER_ZEROS,
        )
    with pytest.raises(FactorTradeabilityError, match="a coverage code on the period"):
        GroupCoverage(
            group=1,
            scored_count=0,
            held_count=0,
            excluded_by_outcome=EXCLUDED_OUTCOME_ORDER_ZEROS,
        )
    with pytest.raises(FactorTradeabilityError, match="filed under group"):
        GroupCoverage(
            group=-1,
            scored_count=1,
            held_count=1,
            excluded_by_outcome=EXCLUDED_OUTCOME_ORDER_ZEROS,
        )


# --------------------------------------------------------------------------------------------
# Turnover: the rolling portfolio, and the schedule it needs
# --------------------------------------------------------------------------------------------


def _rolling_periods(
    *,
    horizon: str = "1d",
    second_scores: dict[int, float] | None = None,
    prices: dict[int, float] | None = None,
) -> list[PeriodPortfolio]:
    """Two periods one session apart, the second re-ranking the cross section as asked.

    At `1d` the second period enters on the first's exit session exactly, which is the tightest
    schedule a holdings state exists on; at `5d` the two windows overlap by four sessions and the
    same fixture is the `overlapping_schedule` one.
    """
    first, first_bars = _inputs(horizon=horizon, prices=prices)
    second, second_bars = _inputs(
        as_of=AS_OF_2,
        horizon=horizon,
        scores=second_scores,
        prices=prices,
    )
    return [_period(first, first_bars), _period(second, second_bars)]


def test_a_rolling_portfolio_retains_the_names_both_periods_rank_highest() -> None:
    """The holdings state, and the three counts that describe a transition of it.

    The long group is `{5, 6}` at the first `as_of` and `{4, 6}` at the second, so exactly one
    name is retained, one leaves and one arrives. The three counts are deliberately not equal to
    each other in the other fixtures below; here they are 1, 1, 1 because the arithmetic of
    `name_turnover` is what is being pinned and a 2-2 group is the smallest shape that has a
    retained name at all.
    """
    periods = _rolling_periods(second_scores={4: 5.5, 5: 3.5})
    series = _study().turnover(periods)

    assert series.coverage == "measured"
    assert series.group == 2
    assert series.measured_count == 2
    assert len(series.rebalances) == 1
    move = series.rebalances[0]
    assert (move.retained_count, move.entered_count, move.exited_count) == (1, 1, 1)
    assert (move.from_count, move.to_count) == (2, 2)
    assert move.name_turnover == 0.5
    assert move.resolution == 0.25
    assert series.mean_name_turnover == 0.5


def test_an_overlapping_schedule_has_no_holdings_state_and_reports_so() -> None:
    """`a_rolling_portfolio_is_only_constructible_on_a_non_overlapping_schedule`, driven.

    At `5d` the second period enters on 2026-06-12 and the first does not exit until 2026-06-18,
    so a name "carried across" is held in two windows at once. The same two `as_of`s at `1d` roll
    perfectly, which is what makes this a property of the *schedule* rather than of the fixture.
    """
    overlapping = _study().turnover(_rolling_periods(horizon="5d"))
    rolling = _study().turnover(_rolling_periods(horizon="1d"))

    assert overlapping.coverage == "overlapping_schedule"
    assert overlapping.rebalances == ()
    assert overlapping.mean_name_turnover is None
    assert overlapping.measured_count == 2
    assert overlapping.round_trip_cost > 0
    assert overlapping.avoided_cost == 0
    assert rolling.coverage == "measured"


def test_an_overlapping_series_that_is_also_short_reports_the_schedule_rather_than_the_count() -> (
    None
):
    """The one fixture on which both refusals hold, which is the only thing that pins the order.

    Two overlapping periods against a declared floor of five rebalances satisfy
    `overlapping_schedule` and `insufficient_rebalances` at once. The declared order puts the
    schedule first, because a rebalance is a transition of a holdings state and counting
    transitions of a state that does not exist would report the wrong remedy -- "measure more
    days" against "space them out".
    """
    series = _study(_spec(min_rebalances=5)).turnover(_rolling_periods(horizon="5d"))

    assert series.coverage == "overlapping_schedule"
    assert TURNOVER_COVERAGE_ORDER == (
        "measured",
        "overlapping_schedule",
        "insufficient_rebalances",
    )
    assert set(TURNOVER_COVERAGE_ORDER) == TURNOVER_COVERAGE_CODES


def test_a_series_with_fewer_transitions_than_declared_is_insufficient_rebalances() -> None:
    """A rolling schedule and a floor above what it carries. Two periods are one transition, so a
    declared floor of two refuses them and a floor of one does not -- the boundary, both sides."""
    periods = _rolling_periods()

    assert _study(_spec(min_rebalances=2)).turnover(periods).coverage == "insufficient_rebalances"
    assert _study(_spec(min_rebalances=1)).turnover(periods).coverage == "measured"


def test_a_series_of_one_period_has_no_transition_to_follow() -> None:
    """The floor's own arithmetic: `n` measured periods carry `n - 1` transitions, so one period
    carries none and `MINIMUM_REBALANCES` is 1 for that reason rather than by taste."""
    first, first_bars = _inputs()
    series = _study().turnover([_period(first, first_bars)])

    assert MINIMUM_REBALANCES == 1
    assert MAXIMUM_REBALANCES == 10_000
    assert series.coverage == "insufficient_rebalances"
    assert series.measured_count == 1
    assert series.rebalances == ()


def test_non_measured_periods_are_dropped_rather_than_read_as_no_turnover() -> None:
    """A period the market emptied did not turn its portfolio over -- it had no portfolio.

    `measured_count` against `len(as_ofs)` is the attrition, and the transition that is followed
    runs between the two periods that *did* produce groups rather than between adjacent `as_of`s.
    Counting the refused period as a zero-turnover rebalance would put a `0.0` into the mean for
    a day on which nothing was held.
    """
    first, first_bars = _inputs()
    empty, empty_bars = _inputs(as_of=AS_OF_2, suspend_entry=(5, 6))
    third, third_bars = _inputs(as_of=AS_OF_3, scores={4: 5.5, 5: 3.5})
    series = _study().turnover(
        [
            _period(first, first_bars),
            _period(empty, empty_bars),
            _period(third, third_bars),
        ]
    )

    assert _period(empty, empty_bars).coverage == "unfillable_after_execution"
    assert len(series.as_ofs) == 3
    assert series.measured_count == 2
    assert len(series.rebalances) == 1
    assert series.rebalances[0].from_as_of == AS_OF
    assert series.rebalances[0].to_as_of == AS_OF_3


def test_the_avoided_cost_is_the_legs_the_quantile_study_charged_and_rolling_would_not() -> None:
    """The saving, taken from fills that already exist rather than from a new simulation.

    A retained name paid an exit fee at the first period and an entry fee at the second, and a
    portfolio that simply kept holding it paid neither. The assertion is against those two
    `ExecutionResult`s by name, so a change to `CostSchedule` moves both sides together and this
    test still measures the *join* rather than the fee.
    """
    periods = _rolling_periods(second_scores={4: 5.5, 5: 3.5})
    series = _study().turnover(periods)
    retained = code(6)
    first_leg = next(
        item for item in periods[0].groups[2].holdings if item.subject == retained
    ).exit_fill.total_cost
    second_leg = next(
        item for item in periods[1].groups[2].holdings if item.subject == retained
    ).entry_fill.total_cost

    assert series.rebalances[0].retained_count == 1
    assert series.rebalances[0].avoided_cost == first_leg + second_leg
    assert series.avoided_cost == first_leg + second_leg
    assert first_leg > 0
    assert second_leg > 0


def test_the_rolling_cost_and_the_round_trip_cost_bracket_what_a_rebalance_pays() -> None:
    """`V2-P3-006`'s stated solution: its 100% turnover is the upper bound and this is the lower.

    The bracket is asserted as an ordering rather than as a value, and the two ends are required
    to be **different** -- a series in which nothing was retained would leave them equal and would
    prove nothing about the bracket having a width.
    """
    series = _study().turnover(_rolling_periods(second_scores={4: 5.5, 5: 3.5}))

    assert series.round_trip_cost > series.rolling_cost > 0
    assert series.rolling_cost == series.round_trip_cost - series.avoided_cost
    assert series.cost_reduction is not None
    assert 0.0 < series.cost_reduction < 1.0
    assert series.cost_reduction == float(series.avoided_cost) / float(series.round_trip_cost)


def test_a_series_that_retains_nothing_avoids_nothing_and_the_bracket_has_no_width() -> None:
    """The other end of the same claim, so `avoided_cost` cannot be a constant.

    The second period's long group shares no name with the first's, so a rolling portfolio has to
    sell all of one and buy all of the other and saves exactly nothing -- `rolling_cost` collapses
    onto `round_trip_cost`, which is `V2-P3-006`'s reading and is right here.
    """
    series = _study().turnover(_rolling_periods(second_scores={1: 9.0, 2: 8.0, 5: 2.0, 6: 1.0}))
    move = series.rebalances[0]

    assert (move.retained_count, move.entered_count, move.exited_count) == (0, 2, 2)
    assert move.name_turnover == 1.0
    assert move.money_turnover == 1.0
    assert series.avoided_cost == 0
    assert series.rolling_cost == series.round_trip_cost
    assert series.cost_reduction == 0.0


def test_name_turnover_and_money_turnover_are_not_the_same_number() -> None:
    """Two readings of one transition, on a fixture built so lot rounding separates them.

    `V2-P3-006` gives every position the same **budget** and `position_quantity` floors it to
    whole lots, so a ¥600 close spends ¥60,000 of a ¥100,000 budget while a ¥100 close spends all
    of it. The retained name here is the expensive one, so the money that moved is a larger share
    of the portfolio than the names that moved -- and a fixture priced flat would report the two
    as equal and would let either be deleted.
    """
    prices = {6: 600.0}
    series = _study().turnover(_rolling_periods(second_scores={4: 5.5, 5: 3.5}, prices=prices))
    move = series.rebalances[0]
    flat = _study().turnover(_rolling_periods(second_scores={4: 5.5, 5: 3.5})).rebalances[0]

    assert move.name_turnover == 0.5
    assert move.money_turnover > move.name_turnover
    assert move.money_turnover == float(move.sold_value + move.bought_value) / float(
        move.from_value + move.to_value
    )
    assert flat.name_turnover == 0.5
    assert abs(flat.money_turnover - 0.5) < 0.01
    assert move.money_turnover != flat.money_turnover


def test_the_resolution_is_the_unit_the_name_turnover_counts_in() -> None:
    """The narrow-sample statement, as arithmetic rather than as prose.

    The numerator counts securities, so a turnover between two groups of `n` is an integer
    multiple of `1 / 2n` -- a three-name group resolves to sixths and has seven attainable values
    in all. Driven over the shapes a declared cut can produce rather than asserted once.
    """
    for held in (1, 2, 3, 10):
        move = Rebalance(
            from_as_of=AS_OF,
            to_as_of=AS_OF_2,
            group=1,
            retained_count=held - 1,
            entered_count=1,
            exited_count=1,
            avoided_cost=Decimal("0.00") if held == 1 else Decimal("1.00"),
            sold_value=Decimal("100.00"),
            bought_value=Decimal("100.00"),
            from_value=Decimal("1000.00"),
            to_value=Decimal("1000.00"),
        )
        assert move.resolution == 1.0 / (2 * held)
        assert math.isclose(move.name_turnover / move.resolution, 2.0, rel_tol=1e-12)


def test_a_rebalance_refuses_an_end_that_does_not_exist_and_a_saving_nobody_retained() -> None:
    """The carrier's own invariants, each provoked alone."""
    base = {
        "from_as_of": AS_OF,
        "to_as_of": AS_OF_2,
        "group": 0,
        "retained_count": 1,
        "entered_count": 1,
        "exited_count": 1,
        "avoided_cost": Decimal("1.00"),
        "sold_value": Decimal("100.00"),
        "bought_value": Decimal("100.00"),
        "from_value": Decimal("1000.00"),
        "to_value": Decimal("1000.00"),
    }
    with pytest.raises(FactorTradeabilityError, match="is not a transition"):
        Rebalance(**{**base, "to_as_of": AS_OF})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="a portfolio that does not exist"):
        Rebalance(**{**base, "retained_count": 0, "exited_count": 0, "sold_value": Decimal("0.00")})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="every avoided fee"):
        Rebalance(**{**base, "retained_count": 0, "exited_count": 2})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="not a turnover"):
        Rebalance(**{**base, "sold_value": Decimal("2000.00")})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="money turnover undefined"):
        Rebalance(**{**base, "from_value": Decimal("0.00"), "sold_value": Decimal("0.00")})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="entering nothing bought nothing"):
        Rebalance(**{**base, "entered_count": 0})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="exiting nothing sold nothing"):
        Rebalance(**{**base, "exited_count": 0})  # type: ignore[arg-type]


def test_a_turnover_series_cannot_avoid_more_than_the_round_trip_it_is_a_saving_on() -> None:
    """The claim that makes the bracket a bracket, as a validator rather than as an argument.

    Each avoidable leg is counted exactly once by `round_trip_cost` -- a period's entry leg
    belongs to the rebalance into it and its exit leg to the rebalance out of it -- so the saving
    cannot exceed the fee. A hand-built series that claims otherwise is refused, and so is one
    whose total disagrees with the rebalances it is a sum of.
    """
    move = Rebalance(
        from_as_of=AS_OF,
        to_as_of=AS_OF_2,
        group=2,
        retained_count=1,
        entered_count=1,
        exited_count=1,
        avoided_cost=Decimal("50.00"),
        sold_value=Decimal("100.00"),
        bought_value=Decimal("100.00"),
        from_value=Decimal("1000.00"),
        to_value=Decimal("1000.00"),
    )
    base = {
        "tier": "raw",
        "factor_id": "fct_probe",
        "direction": "higher_is_better",
        "group_count": 3,
        "horizon_sessions": 1,
        "group": 2,
        "coverage": "measured",
        "as_ofs": (AS_OF, AS_OF_2),
        "measured_count": 2,
        "rebalances": (move,),
        "mean_name_turnover": 0.5,
        "mean_money_turnover": 0.5,
        "round_trip_cost": Decimal("200.00"),
        "avoided_cost": Decimal("50.00"),
    }
    assert TurnoverSeries(**base).rolling_cost == Decimal("150.00")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="cannot exceed the fee"):
        TurnoverSeries(**{**base, "round_trip_cost": Decimal("10.00")})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="second source of truth"):
        TurnoverSeries(**{**base, "avoided_cost": Decimal("40.00")})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="one fewer transition"):
        TurnoverSeries(**{**base, "measured_count": 3, "as_ofs": (AS_OF, AS_OF_2, AS_OF_3)})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="one series follows one portfolio"):
        TurnoverSeries(**{**base, "group": 1})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="exactly the 'measured' code carries them"):
        TurnoverSeries(
            **{  # type: ignore[arg-type]
                **base,
                "coverage": "insufficient_rebalances",
                "mean_name_turnover": None,
                "mean_money_turnover": None,
            }
        )
    with pytest.raises(ValidationError, match="the 'measured' code carries the statistics"):
        TurnoverSeries(**{**base, "coverage": "insufficient_rebalances"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="an as_of it was not given"):
        TurnoverSeries(**{**base, "as_ofs": (AS_OF,)})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="distinct and ascending"):
        TurnoverSeries(**{**base, "as_ofs": (AS_OF_2, AS_OF)})  # type: ignore[arg-type]


def test_a_turnover_series_refuses_a_mixed_horizon_a_mixed_tier_and_a_repeated_as_of() -> None:
    """A mean over two holding periods is the average of two different quantities.

    `factor_id`, `direction` and `group_count` are refused one caller up against the declared
    quantile spec, so what this method has to catch is the pair a spec cannot see.
    """
    first, first_bars = _inputs()
    other_horizon, other_bars = _inputs(as_of=AS_OF_2, horizon="5d")
    with pytest.raises(FactorTradeabilityError, match="two different holding periods"):
        _study().turnover([_period(first, first_bars), _period(other_horizon, other_bars)])
    with pytest.raises(FactorTradeabilityError, match="appears more than once"):
        _study().turnover([_period(first, first_bars), _period(first, first_bars)])
    with pytest.raises(FactorTradeabilityError, match="at least one period"):
        _study().turnover([])


def test_a_turnover_series_refuses_a_period_measured_under_another_quantile_spec() -> None:
    """The capacity is compared against `position_capital` and the long group is chosen by
    `direction`; a period from another study would be sized against a budget it was not built
    with, which is the one number here that is a statement about a declaration."""
    section, bars = _inputs()
    period = _period(section, bars)
    other = _study(portfolio=_quantile_spec(key="probe_other"))

    with pytest.raises(FactorTradeabilityError, match="two different declarations"):
        other.turnover([period])
    with pytest.raises(FactorTradeabilityError, match="two different declarations"):
        other.measure(period, cross_section=section, liquidity={})


def test_the_long_group_a_series_follows_is_the_one_the_declared_direction_makes_long() -> None:
    """`lower_is_better` moves the followed portfolio from group 2 to group 0, and the two are
    genuinely different holdings -- the retained name differs, so a direction that did not reach
    the choice would leave the turnover unchanged."""
    high = _study().turnover(_rolling_periods(second_scores={4: 5.5, 5: 3.5}))
    low_spec = _quantile_spec("lower_is_better")
    periods = [
        _period(section, bars, portfolio=low_spec)
        for section, bars in (
            _inputs(),
            _inputs(as_of=AS_OF_2, scores={4: 5.5, 5: 3.5}),
        )
    ]
    low = TradeabilityStudy(_spec(), portfolio=low_spec).turnover(periods)

    assert high.group == 2
    assert low.group == 0
    assert high.rebalances[0].retained_count == 1
    assert low.rebalances[0].retained_count == 2
    assert low.rebalances[0].name_turnover == 0.0


def test_a_rebalance_between_groups_of_different_sizes_counts_both_of_them() -> None:
    """The reason both turnovers are the **symmetric** ratio rather than `entered / to_count`.

    The market empties the long group by different amounts on different days, so the two ends of a
    rebalance are not the same size. Here the second period's long group is one name where the
    first's was two, and nothing was entered at all: a one-sided reading calls that `0.0` --
    a portfolio that turned over nothing -- when a third of the position count was in fact sold.
    """
    portfolio = _quantile_spec(min_securities_per_group=1)
    first, first_bars = _inputs()
    second, second_bars = _inputs(as_of=AS_OF_2, suspend_entry=(5,))
    series = TradeabilityStudy(_spec(), portfolio=portfolio).turnover(
        [
            _period(first, first_bars, portfolio=portfolio),
            _period(second, second_bars, portfolio=portfolio),
        ]
    )
    move = series.rebalances[0]

    assert (move.retained_count, move.entered_count, move.exited_count) == (1, 0, 1)
    assert (move.from_count, move.to_count) == (2, 1)
    assert move.name_turnover == 1 / 3
    assert move.entered_count / move.to_count == 0.0
    assert move.resolution == 1 / 3


def test_the_mean_turnover_is_a_mean_over_the_transitions_and_not_a_total() -> None:
    """Two rebalances of different sizes, so a sum and a mean are different numbers.

    The long group changes one of its two names between the first and second periods and neither
    between the second and third, so the transitions are `0.5` and `0.0` and the mean is `0.25`.
    A series carrying only one transition -- which every other turnover fixture here does -- would
    leave the two indistinguishable.
    """
    first, first_bars = _inputs()
    second, second_bars = _inputs(as_of=AS_OF_2, scores={4: 5.5, 5: 3.5})
    third, third_bars = _inputs(as_of=AS_OF_3, scores={4: 5.5, 5: 3.5})
    series = _study().turnover(
        [
            _period(first, first_bars),
            _period(second, second_bars),
            _period(third, third_bars),
        ]
    )

    assert [move.name_turnover for move in series.rebalances] == [0.5, 0.0]
    assert series.mean_name_turnover == 0.25
    assert series.mean_money_turnover is not None
    assert series.mean_money_turnover < 0.5
    assert series.measured_count == 3


def test_a_turnover_series_refuses_a_period_from_another_tier() -> None:
    """The other half of "one study": a mean over the raw and processed planes at once.

    `factor_id`, `direction` and `group_count` are all identical across the two periods here --
    the same declared quantile spec measured both -- so the tier is the only thing that separates
    them, which is what makes this a test of the tier check rather than of the spec check.
    """
    raw, raw_bars = _inputs()
    processed, processed_bars = _processed_inputs(as_of=AS_OF_2)
    periods = [_period(raw, raw_bars), _period(processed, processed_bars)]

    assert periods[0].tier == "raw"
    assert periods[1].tier == "processed"
    assert periods[0].factor_id == periods[1].factor_id
    assert periods[0].horizon_sessions == periods[1].horizon_sessions
    with pytest.raises(FactorTradeabilityError, match="one factor on one tier at one horizon"):
        _study().turnover(periods)


# --------------------------------------------------------------------------------------------
# Capacity: one declared judgement and four pieces of arithmetic
# --------------------------------------------------------------------------------------------

REAL_TURNOVERS: Final[dict[str, float]] = {
    "000001.SZ": 2_263_042.93057,
    "600519.SH": 6_477_910.214,
    "002736.SZ": 155_786.2351,
    "000569.SZ": 6_579.5778,
}
"""Four real Tushare `daily.amount` values in thousands of yuan, from the eleven rows this
repository already stores in `tests/unit/domain/test_daily_prices.py`: `000001.SZ` on 2026-06-12,
`600519.SH` on 2026-06-12, `002736.SZ` on 2016-10-10 and `000569.SZ` on 2001-01-02.

They are here rather than a synthetic ladder because the *spread* is the finding: the widest pair
is 344x apart, which is what makes a `min` over a group a different number from its mean.
"""


def test_the_binding_capacity_is_the_least_liquid_held_name_and_it_is_named() -> None:
    """`the_binding_capacity_of_a_group_is_one_securitys_turnover`, driven on real rows.

    The long group holds two names whose sessions turned over ¥2,263,042,930.57 and ¥6,579,577.80
    -- a factor of 344 -- and the equal-budget capacity is twice the smaller, not the sum and not
    twice the mean. `concentration` is how far the binding name sits from the rest and is the
    number that says the group's capacity is being destroyed by one member.
    """
    section, bars = _inputs()
    report = _study().measure(
        _period(section, bars),
        cross_section=section,
        liquidity=_liquidity(
            section,
            amounts={5: REAL_TURNOVERS["000001.SZ"], 6: REAL_TURNOVERS["000569.SZ"]},
        ),
    )
    capacity = report.capacity

    assert report.capacity_coverage == "measured"
    assert capacity is not None
    assert capacity.group == 2
    assert capacity.held_count == 2
    assert capacity.binding_subject == code(6)
    assert capacity.binding_capacity == Decimal("65795.7780000")
    assert capacity.equal_weight_capacity == Decimal("131591.5560000")
    assert capacity.liquidity_weighted_capacity == Decimal("22696225.0837")
    assert capacity.concentration < 0.006
    assert capacity.equal_weight_capacity < capacity.liquidity_weighted_capacity


def test_a_capital_multiple_below_one_says_the_declared_capital_is_over_capacity() -> None:
    """The headline, and it is falsifiable in the right direction.

    `000569.SZ`'s 2001-01-02 session turned over ¥6,579,577.80, so at a 1% participation cap it
    absorbs **¥65,795.78** -- and `V2-P3-006`'s own ¥100,000 test position is a multiple of
    **0.658**. A study reporting that group's net return is reporting the return of a portfolio
    nobody could have opened at the size it declared. The same group at `000001.SZ`'s liquidity is
    a multiple of 226, which is the control: the number is about the pair and not about the cap.
    """
    section, bars = _inputs()
    thin = _study().measure(
        _period(section, bars),
        cross_section=section,
        liquidity=_liquidity(section, amount=REAL_TURNOVERS["000569.SZ"]),
    )
    deep = _study().measure(
        _period(section, bars),
        cross_section=section,
        liquidity=_liquidity(section, amount=REAL_TURNOVERS["000001.SZ"]),
    )

    assert thin.capacity is not None and deep.capacity is not None
    assert thin.capacity.binding_capacity == Decimal("65795.7780000")
    assert thin.capacity.position_capital == CAPITAL
    assert thin.capacity.capital_multiple == 0.657957780
    assert thin.capacity.capital_multiple < 1.0
    assert deep.capacity.capital_multiple > 226.0
    assert thin.capacity.concentration == 1.0


def test_the_participation_cap_scales_the_capacity_linearly() -> None:
    """The one declared judgement, and it is the only thing that moves.

    Three caps over one period: the capacity, the equal-weight capacity and the capital multiple
    are all exactly proportional, and `concentration` -- a ratio of two quantities the cap
    multiplies alike -- does not move at all. That separation is what makes the cap a *constraint*
    rather than a model of anything: it scales the answer and changes no shape.
    """
    section, bars = _inputs()
    period = _period(section, bars)
    sized = {
        cap: TradeabilityStudy(_spec(participation_cap=cap), portfolio=_quantile_spec()).measure(
            period,
            cross_section=section,
            liquidity=_liquidity(
                section, amounts={5: REAL_TURNOVERS["002736.SZ"], 6: REAL_TURNOVERS["000569.SZ"]}
            ),
        )
        for cap in (Decimal("0.001"), Decimal("0.01"), Decimal("0.1"))
    }
    capacities = {cap: report.capacity for cap, report in sized.items()}

    assert all(item is not None for item in capacities.values())
    assert capacities[Decimal("0.001")].binding_capacity == Decimal("6579.5778000")  # type: ignore[union-attr]
    assert capacities[Decimal("0.01")].binding_capacity == Decimal("65795.7780000")  # type: ignore[union-attr]
    assert capacities[Decimal("0.1")].binding_capacity == Decimal("657957.7800000")  # type: ignore[union-attr]
    multiples = {cap: item.capital_multiple for cap, item in capacities.items()}  # type: ignore[union-attr]
    assert math.isclose(multiples[Decimal("0.1")], 100 * multiples[Decimal("0.001")], rel_tol=1e-12)
    concentrations = {item.concentration for item in capacities.values()}  # type: ignore[union-attr]
    assert len(concentrations) == 1


def test_an_unpriced_holding_refuses_the_group_rather_than_averaging_the_rest() -> None:
    """`panel_factors._amihud_60`'s decision at this plane, and the reason is the same one.

    A capacity taken over whichever held names happened to have a turnover row is a number whose
    sample size is a function of the data, and the omission is not neutral: a security with no
    turnover on a session did not trade it, which is the end of the distribution a `min` is
    looking for. Dropping the sole *unheld* security's row changes nothing, which is the control:
    what is refused is an unpriced **holding**.
    """
    section, bars = _inputs()
    period = _period(section, bars)
    missing_holding = _study().measure(
        period, cross_section=section, liquidity=_liquidity(section, skip=(6,))
    )
    missing_other = _study().measure(
        period, cross_section=section, liquidity=_liquidity(section, skip=(1,))
    )

    assert missing_holding.capacity_coverage == "unpriced_holdings"
    assert missing_holding.capacity is None
    assert missing_other.capacity_coverage == "measured"
    assert missing_other.capacity is not None
    assert missing_other.capacity.held_count == 2
    assert (
        _study().measure(period, cross_section=section, liquidity={}).capacity_coverage
        == "unpriced_holdings"
    )


def test_the_turnover_unit_is_the_panel_engines_own_amount_unit() -> None:
    """`daily.amount` is thousands of yuan, and the constant is restated here rather than imported.

    `backtest/` may not reach `panel_factors` -- that is a top-level module over the panel plane
    and the edge would pull in DuckDB -- so the two are held together by a test rather than by a
    shared name, which is `RAW_COVERAGE_ORDER`'s arrangement one module over. The conversion is
    driven on a real published row: `000001.SZ`'s 2026-06-12 session turned over
    2,263,042.93057 thousand yuan, which is ¥2,263,042,930.57.
    """
    observed = liquidity_from_amount(
        subject="000001.SZ", trade_date=date(2026, 6, 12), amount=REAL_TURNOVERS["000001.SZ"]
    )

    assert Decimal(str(CNY_PER_AMOUNT_UNIT)) == CNY_PER_TURNOVER_UNIT
    assert Decimal(1000) == CNY_PER_TURNOVER_UNIT
    assert observed.turnover_yuan == Decimal("2263042930.57000")
    assert observed.trade_date == date(2026, 6, 12)
    assert observed.subject == "000001.SZ"


def test_the_float_to_decimal_bridge_is_the_repr_and_not_the_binary_double() -> None:
    """`Decimal(str(x))` and not `Decimal(x)`, which is `published_limit_fields`' measured reason.

    `0.1` as a binary double is `0.1000000000000000055511151231257827...`, and carrying that into
    a type whose whole purpose is not to do that would put the error into every capacity that
    quoted it. The `amount` is the panel plane's `float` and the capacity is money, so something
    has to bridge them and the obvious spelling is the wrong one.
    """
    observed = liquidity_from_amount(subject="000001.SZ", trade_date=date(2026, 6, 12), amount=0.1)

    assert observed.turnover_yuan == Decimal("100.0")
    assert observed.turnover_yuan != Decimal(0.1) * CNY_PER_TURNOVER_UNIT  # noqa: RUF032


def test_a_session_that_traded_nothing_has_no_capacity_rather_than_a_capacity_of_zero() -> None:
    """A zero turnover would set the whole group's `min` to zero and read as a measured fact.

    A caller who has no turnover for a name says so by not offering the row, which is
    `unpriced_holdings`; a caller who offers a zero is asserting that the session traded and
    traded nothing, which is not a session this contract will size against.
    """
    with pytest.raises(FactorTradeabilityError, match="no capacity rather than a capacity of"):
        SessionLiquidity(
            subject="000001.SZ", trade_date=date(2026, 6, 12), turnover_yuan=Decimal("0")
        )
    with pytest.raises(FactorTradeabilityError, match="must name a subject"):
        SessionLiquidity(subject="  ", trade_date=date(2026, 6, 12), turnover_yuan=Decimal("1"))


def test_a_liquidity_session_after_the_entry_day_is_refused_and_the_entry_day_itself_is_not() -> (
    None
):
    """`the_liquidity_session_is_the_callers_and_may_be_the_entry_session_itself`, both sides.

    Sizing an order on turnover that had not happened when the order was placed is look-ahead in
    the sizing, so a later session is refused; the entry session itself and every earlier one are
    admitted and `liquidity_day` is reported beside `entry_day` so a reader can see which reading
    was asked for.
    """
    section, bars = _inputs()
    period = _period(section, bars)
    at_entry = _study().measure(
        period, cross_section=section, liquidity=_liquidity(section, day=section.entry_day)
    )
    earlier = _study().measure(
        period, cross_section=section, liquidity=_liquidity(section, day=date(2026, 6, 10))
    )

    assert at_entry.liquidity_day == at_entry.entry_day == date(2026, 6, 11)
    assert earlier.liquidity_day == date(2026, 6, 10)
    assert earlier.entry_day == date(2026, 6, 11)
    with pytest.raises(FactorTradeabilityError, match="the turnovers are dated 2026-06-12"):
        _study().measure(
            period, cross_section=section, liquidity=_liquidity(section, day=date(2026, 6, 12))
        )


def test_liquidity_rows_from_two_sessions_have_no_day_to_report_and_are_refused() -> None:
    """One period is sized against one session. A mapping that mixes them has no `liquidity_day`
    to publish beside `entry_day`, so the pair a reader needs would be missing rather than
    wrong."""
    section, bars = _inputs()
    mixed = _liquidity(section)
    mixed[code(1)] = liquidity_from_amount(
        subject=code(1), trade_date=date(2026, 6, 10), amount=TURNOVER
    )

    with pytest.raises(FactorTradeabilityError, match="one period is sized against one session"):
        _study().measure(_period(section, bars), cross_section=section, liquidity=mixed)


def test_a_liquidity_row_for_a_security_the_cross_section_never_admitted_is_refused() -> None:
    """`QuantilePortfolioStudy.measure`'s rule at this module's own boundary, and the same
    message shape: a key with no admitted pair is refused rather than ignored, because ignoring it
    would hide a caller that read its two sides from two different universes. A row whose
    `subject` disagrees with its key is the other half."""
    section, bars = _inputs()
    period = _period(section, bars)
    stray = _liquidity(section)
    stray[code(50)] = liquidity_from_amount(
        subject=code(50), trade_date=section.entry_day, amount=TURNOVER
    )
    mislabelled = _liquidity(section)
    mislabelled[code(1)] = liquidity_from_amount(
        subject=code(2), trade_date=section.entry_day, amount=TURNOVER
    )

    with pytest.raises(FactorTradeabilityError, match="key the liquidity from cross_section"):
        _study().measure(period, cross_section=section, liquidity=stray)
    with pytest.raises(FactorTradeabilityError, match="a capacity from the wrong rows"):
        _study().measure(period, cross_section=section, liquidity=mislabelled)


def test_a_group_capacity_refuses_a_total_below_its_own_minimum_and_an_empty_group() -> None:
    """The carrier's own arithmetic: the binding capacity is a `min` over the same names the
    total sums, so the total can never be below the product."""
    base = {
        "group": 2,
        "held_count": 2,
        "binding_subject": code(6),
        "binding_capacity": Decimal("100.00"),
        "total_capacity": Decimal("500.00"),
        "position_capital": CAPITAL,
    }
    assert GroupCapacity(**base).equal_weight_capacity == Decimal("200.00")  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="cannot be below the product"):
        GroupCapacity(**{**base, "total_capacity": Decimal("150.00")})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="a capacity of zero, which is the"):
        GroupCapacity(**{**base, "held_count": 0})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="must name the security that binds it"):
        GroupCapacity(**{**base, "binding_subject": " "})  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="both are money and both"):
        GroupCapacity(**{**base, "binding_capacity": Decimal("0")})  # type: ignore[arg-type]


def test_the_capacity_is_sized_on_the_group_the_declared_direction_makes_long() -> None:
    """Two definitions differing in nothing but `direction`, over the same cross section.

    The group table is identical -- group indices stay ascending in the raw factor value, which is
    `V2-P3-006`'s rule and is not restated here -- and the sized group moves from 2 to 0. A
    capacity for a group nobody would hold is a size for a trade that was never going to be
    placed, so a report that filed one under the wrong group is refused outright.
    """
    section, bars = _inputs()
    liquidity = _liquidity(section, amounts={1: 10.0, 6: 20.0})
    high = _study().measure(_period(section, bars), cross_section=section, liquidity=liquidity)
    low_spec = _quantile_spec("lower_is_better")
    low = TradeabilityStudy(_spec(), portfolio=low_spec).measure(
        _period(section, bars, portfolio=low_spec), cross_section=section, liquidity=liquidity
    )

    assert [row.held_count for row in high.by_group] == [row.held_count for row in low.by_group]
    assert high.capacity is not None and low.capacity is not None
    assert high.capacity.group == 2
    assert low.capacity.group == 0
    assert high.capacity.binding_subject == code(6)
    assert low.capacity.binding_subject == code(1)
    with pytest.raises(FactorTradeabilityError, match="never going to be placed"):
        replace(high, capacity=replace(high.capacity, group=1))


# --------------------------------------------------------------------------------------------
# The three tiers, the spec, and the contracts' own refusals
# --------------------------------------------------------------------------------------------


def test_a_long_group_that_survived_is_sized_even_when_the_market_emptied_another() -> None:
    """The capacity does not follow the period's code -- it follows the long group's holdings.

    `unfillable_after_execution` says *some* group fell below the declared floor, not that the
    long one did. Here the market empties group `0` and leaves group `2` whole, so the period is
    refused and the portfolio a caller would actually run is still sizeable. A capacity that
    keyed off `period_coverage` rather than off the holdings would report `no_holdings` for a
    group holding two positions.
    """
    section, bars = _inputs(suspend_entry=(1, 2))
    period = _period(section, bars)
    report = _study().measure(
        period,
        cross_section=section,
        liquidity=_liquidity(section, amount=REAL_TURNOVERS["000569.SZ"]),
    )

    assert period.coverage == "unfillable_after_execution"
    assert period.groups == ()
    assert [row.held_count for row in report.by_group] == [0, 2, 2]
    assert report.capacity_coverage == "measured"
    assert report.capacity is not None
    assert report.capacity.group == 2
    assert report.capacity.held_count == 2
    assert report.capacity.capital_multiple < 1.0


def test_the_neutralized_tier_travels_and_reports_its_own_vocabulary() -> None:
    """The third tier, whose value and admitted code sets are the same frozenset, so its
    `admission_rate` is `1.0` for the same structural reason the raw tier's is."""
    window = _window()
    labels: dict[str, OutcomeLabel] = {}
    observations = []
    bars: dict[str, tuple[MarketBar, MarketBar]] = {}
    for rank in range(1, 7):
        subject = code(rank)
        labels[subject] = _label(subject, window=window, total_return=rank / 100.0)
        observations.append(
            NeutralizedFactorObservation(
                subject=subject,
                as_of=AS_OF,
                value=float(rank),
                coverage="neutralized",
                neutralization_id="fnz_probe",
                neutralization_manifest_id="fnm_probe",
                source_factor_id="fct_probe",
                source_transform_id="ftx_probe",
                source_transform_manifest_id="ftm_probe",
                source_coverage="processed",
                industry_code="801080",
            )
        )
        bars[subject] = (
            _bar(subject, window.entry_day, close=100.0, previous_close=100.0),
            _bar(
                subject,
                window.exit_day,
                close=100.0 * (1.0 + rank / 100.0),
                previous_close=100.0 * (1.0 + rank / 100.0),
            ),
        )
    section = neutralized_cross_section(as_of=AS_OF, observations=observations, labels=labels)
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    assert TIER_VALUE_CODES["neutralized"] == TIER_ADMITTED_CODES["neutralized"]
    assert TIER_VALUE_CODES["raw"] == TIER_ADMITTED_CODES["raw"]
    assert report.tier == "neutralized"
    assert report.funnel.admission_rate == 1.0
    assert report.funnel.implementable_rate == 1.0


def test_the_spec_declares_the_cap_and_the_floor_with_no_default_and_a_closed_range() -> None:
    """Neither field has a default, both are range-checked, and the cap's upper bound is `1`.

    A study that is more than the whole session's turnover is not a study that traded at that
    session's prices, so `le=1` is arithmetic rather than caution.
    """
    with pytest.raises(ValidationError):
        TradeabilitySpec()  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        TradeabilitySpec(participation_cap=Decimal("1.5"), min_rebalances=1)
    with pytest.raises(ValidationError):
        TradeabilitySpec(participation_cap=Decimal("0"), min_rebalances=1)
    with pytest.raises(ValidationError):
        TradeabilitySpec(participation_cap=CAP, min_rebalances=0)
    with pytest.raises(ValidationError):
        TradeabilitySpec(participation_cap=CAP, min_rebalances=MAXIMUM_REBALANCES + 1)
    spec = TradeabilitySpec(participation_cap=Decimal("1"), min_rebalances=MINIMUM_REBALANCES)

    assert spec.participation_cap == Decimal("1")
    assert _study().spec.participation_cap == CAP
    assert _study().portfolio.position_capital == CAPITAL


def test_a_cross_section_from_another_as_of_or_another_census_is_refused() -> None:
    """The pairing check, in both the cheap direction and the expensive one.

    The stamps are checked because they are free; the census is checked because two cross sections
    can share every stamp and disagree about what they were offered, and the period carries the
    census it was built from precisely so the join is checkable rather than trusted.
    """
    section, bars = _inputs()
    period = _period(section, bars)
    later, _later_bars = _inputs(as_of=AS_OF_2)
    thinner, _thin_bars = _inputs((1, 2, 3, 4, 5))

    with pytest.raises(FactorTradeabilityError, match="decomposes one period's rejections"):
        _study().measure(period, cross_section=later, liquidity={})
    with pytest.raises(FactorTradeabilityError, match="however well the stamps line up"):
        _study().measure(
            period,
            cross_section=replace(section, census=thinner.census),
            liquidity={},
        )


def test_a_rejection_the_cut_cannot_place_is_refused_rather_than_filed_somewhere() -> None:
    """A rejection whose subject is not an admitted pair has no group, and filing it under one
    would attribute a refusal to a quantile that never offered the security."""
    section, bars = _inputs()
    period = _period(section, bars)
    doctored = replace(
        period,
        rejections=(
            *period.rejections,
            PositionRejection(
                subject=code(50),
                outcome="unbarred",
                reason="a security this cross section never scored",
            ),
        ),
    )

    with pytest.raises(FactorTradeabilityError, match="has no group"):
        _study().measure(doctored, cross_section=section, liquidity={})


def test_a_period_tradeability_refuses_a_capacity_that_contradicts_its_own_codes() -> None:
    """The contract's own relationships, each provoked alone: a capacity under a refusal, a group
    table under a pre-execution code, and a liquidity session after the entry day."""
    section, bars = _inputs()
    report = _study().measure(
        _period(section, bars), cross_section=section, liquidity=_liquidity(section)
    )

    with pytest.raises(FactorTradeabilityError, match="exactly the 'measured' code carries one"):
        replace(report, capacity_coverage="unpriced_holdings")
    with pytest.raises(FactorTradeabilityError, match="placed orders and therefore carry one row"):
        replace(report, period_coverage="insufficient_sample")
    with pytest.raises(FactorTradeabilityError, match=r"a look-ahead this\s+contract will not"):
        replace(report, liquidity_day=date(2026, 6, 30))
    with pytest.raises(FactorTradeabilityError, match="is not a declared tier"):
        replace(report, tier="processedd")  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="is not a declared capacity code"):
        replace(report, capacity_coverage="unpriced")  # type: ignore[arg-type]
    with pytest.raises(FactorTradeabilityError, match="second source of truth"):
        replace(report, funnel=replace(report.funnel, held_count=5))


def test_the_capacity_vocabulary_is_closed_and_is_the_declared_three() -> None:
    """A fourth spelling of "no capacity" would silently become a group of one in
    `V2-P3-014`'s report, which is `ICCoverage`'s reason for being closed."""
    assert {"measured", "no_holdings", "unpriced_holdings"} == CAPACITY_COVERAGE_CODES
    assert {
        "measured",
        "overlapping_schedule",
        "insufficient_rebalances",
    } == TURNOVER_COVERAGE_CODES


def test_the_known_limitations_are_the_declared_set_and_each_is_bound_to_this_module() -> None:
    """The registry, as an equality against a set literal.

    Equality rather than membership because a membership assertion is additive: it can see a code
    that was renamed and never one that was removed. Every code below is therefore evaluated by
    `tests/unit/test_known_limitation_registries.py`'s audit as well, which is what stops a
    limitation from becoming prose.
    """
    assert {
        "capacity_is_a_declared_participation_cap_and_not_a_market_impact_model",
        "capacity_is_estimated_on_the_entry_leg_only",
        "the_binding_capacity_of_a_group_is_one_securitys_turnover",
        "a_rolling_portfolio_is_only_constructible_on_a_non_overlapping_schedule",
        "the_rolling_portfolio_is_a_turnover_and_cost_model_and_not_a_return_series",
        "the_avoided_cost_is_an_upper_bound_on_what_a_rebalance_saves",
        "turnover_is_counted_on_names_and_on_money_and_the_two_are_not_the_same_number",
        "a_turnover_over_a_short_series_is_the_schedule_rather_than_the_factor",
        "the_liquidity_session_is_the_callers_and_may_be_the_entry_session_itself",
        "every_rate_here_is_conditioned_on_the_universe_the_caller_offered",
    } == TRADEABILITY_LIMITATION_CODES
    assert len(KNOWN_TRADEABILITY_LIMITATIONS) == len(TRADEABILITY_LIMITATION_CODES)
    assert all(item.detail.strip() for item in KNOWN_TRADEABILITY_LIMITATIONS)


def test_the_universe_a_rate_is_conditioned_on_is_the_callers_and_is_reported_as_a_count() -> None:
    """`every_rate_here_is_conditioned_on_the_universe_the_caller_offered`, driven.

    The same six tradeable securities reported against two universes -- one that offered only
    them and one that also offered four names the factor could not value -- produce the same
    `held_count` and a `value_rate` of `1.0` against `0.6`. Nothing in this module can tell a
    pre-filtered universe from a complete one, which is why `universe_count` is published as a
    count and not only as a denominator.
    """
    narrow, narrow_bars = _inputs()
    wide, wide_bars = _inputs(
        tuple(range(1, 11)),
        coverages=dict.fromkeys((7, 8, 9, 10), "not_in_universe"),
    )
    narrow_report = _study().measure(
        _period(narrow, narrow_bars), cross_section=narrow, liquidity=_liquidity(narrow)
    )
    wide_report = _study().measure(
        _period(wide, wide_bars), cross_section=wide, liquidity=_liquidity(wide)
    )

    assert narrow_report.funnel.held_count == wide_report.funnel.held_count == 6
    assert narrow_report.funnel.universe_count == 6
    assert wide_report.funnel.universe_count == 10
    assert narrow_report.funnel.value_rate == 1.0
    assert wide_report.funnel.value_rate == 0.6


def test_a_funnel_built_from_a_census_the_period_did_not_carry_is_not_reachable() -> None:
    """The funnel reads both censuses off the period, so there is no argument a caller can pass
    to make the two disagree -- and `ICCensus`' own arithmetic is what makes the stages add up.

    Asserted here rather than left implicit because it is the reason this module publishes no
    `census=` parameter: a coverage report whose denominators came from somewhere other than the
    period would be a funnel about a different day.
    """
    section, bars = _inputs(coverages={2: "input_missing"}, unlabelled=(3,), suspend_entry=(4,))
    period = _period(section, bars)
    census: ICCensus = period.source_census
    report = _study().measure(period, cross_section=section, liquidity=_liquidity(section))

    assert census.subject_count == report.funnel.universe_count
    assert census.admitted_count == report.funnel.scored_count
    assert period.census.held_count == report.funnel.held_count
    assert (
        census.admitted_count + census.unlabelled_count + census.unmatched_count
        == report.funnel.admissible_count
    )


def test_a_report_names_the_period_the_cross_section_and_the_spec_it_was_measured_from() -> None:
    """Every identifying field a stored report renders, each against a **different** source.

    `V2-P3-006`'s own precedent, and the reason is its measured one: its first review found five
    fields that were rendered and never separably asserted, under 100% line coverage. So the
    horizon here is `5d` rather than the `1d` every other fixture uses (a `horizon_sessions` that
    was really a group index or a count would fail), the cut is five groups rather than three, and
    `direction` is `lower_is_better` so `top_group_index` is `0` where `group_count - 1` would be
    `4`. No two of the seven can be swapped for each other and stay green.
    """
    portfolio = _quantile_spec("lower_is_better", group_count=5, min_securities_per_group=1)
    section, bars = _inputs(FUNNEL_RANKS, horizon="5d")
    period = _period(section, bars, portfolio=portfolio)
    report = TradeabilityStudy(_spec(), portfolio=portfolio).measure(
        period, cross_section=section, liquidity=_liquidity(section)
    )

    assert report.as_of == AS_OF == section.as_of
    assert report.tier == "raw" == section.tier
    assert report.factor_id == portfolio.factor_id
    assert report.direction == "lower_is_better"
    assert report.group_count == 5
    assert report.top_group_index == 0
    assert report.period_coverage == period.coverage == "measured"
    assert report.entry_day == section.entry_day == date(2026, 6, 11)
    assert report.liquidity_day == date(2026, 6, 11)
    assert len(report.by_group) == 5
    assert report.capacity is not None
    assert report.capacity.group == 0


def test_a_turnover_series_names_the_study_it_followed() -> None:
    """The five identifying fields a series renders, on a fixture where none equals another.

    `horizon_sessions` is `1`, `group_count` is `4` and the followed `group` is `3`, so the three
    integers are distinct; `tier` and `direction` come from the periods and `factor_id` from the
    declared spec, which is a different object again.
    """
    portfolio = _quantile_spec(group_count=4, min_securities_per_group=1)
    periods = [
        _period(section, bars, portfolio=portfolio)
        for section, bars in (
            _inputs(FUNNEL_RANKS),
            _inputs(FUNNEL_RANKS, as_of=AS_OF_2, scores={4: 5.5, 5: 3.5}),
        )
    ]
    series = TradeabilityStudy(_spec(), portfolio=portfolio).turnover(periods)

    assert series.tier == "raw"
    assert series.factor_id == portfolio.factor_id
    assert series.direction == "higher_is_better"
    assert series.group_count == 4
    assert series.horizon_sessions == 1
    assert series.group == 3
    assert series.as_ofs == (AS_OF, AS_OF_2)
    assert series.measured_count == 2
    assert all(item.group == 3 for item in series.rebalances)
    assert series.rebalances[0].from_as_of == AS_OF
    assert series.rebalances[0].to_as_of == AS_OF_2


def test_the_study_exposes_the_two_specs_it_was_built_with() -> None:
    """Both are read back rather than only used, so a caller can record what a report was
    measured under -- which is the whole point of declaring them."""
    spec = _spec(participation_cap=Decimal("0.05"), min_rebalances=3)
    portfolio = _quantile_spec(group_count=5, min_securities_per_group=3)
    study = TradeabilityStudy(spec, portfolio=portfolio)

    assert study.spec is spec
    assert study.portfolio is portfolio
    assert study.portfolio.group_count == 5
