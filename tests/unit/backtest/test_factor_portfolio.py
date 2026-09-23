"""Quantile portfolio returns with real A-share costs (`V2-P3-006`).

Five properties this file exists to hold, each of which is a place a grouped return silently
becomes a different number:

1. **The gross return is the label contract's and the fees are the execution policy's.** A fill is
   at `MarketBar.close`, so a round trip priced from the two fills is `close_exit / close_entry`
   -- the path `domain/daily_prices.py` measured at `-0.530973%` where the truth is `+2.742251%`,
   sign reversed. `test_the_gross_value_is_the_labels_adjusted_return_and_not_the_two_fills_ratio`
   drives the ex-rights session that produced those numbers and pins the position's value to the
   label with `==` and against the fill notional with `!=`.
2. **A refused order is never a zero.** The test that matters is not that a rejection is counted
   -- it is that *counting it moved the answer*, which is what separates a live exclusion from a
   fixture where either choice looks the same.
   `test_a_rejected_entry_leaves_the_group_rather_than_reading_flat` computes the group return the
   substituted-zero way as well and requires the two to differ.
3. **The coverage codes are decided in a declared order and every one of them has a fixture that
   the neighbouring code would answer differently.** `degenerate_scores` and `unfillable_groups`
   are the pair that matters: an all-tied cross section satisfies both conditions, so the order is
   the only thing that decides it and a fixture that could not tell them apart would pin nothing.
4. **`direction` reaches which group is long, and the fixture separates the two answers.** Two
   definitions differing in nothing but `direction` run over the same cross section, and the
   assertion is that the group table is byte-identical while the spread is the exact negation.
5. **Every number reported has a fixture that separates it from its neighbour.** The census is
   built so that no two of its cells share a value, and the three groups hold 2, 3 and 4 positions
   so that a held count swapped for a group index fails.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import (
    KNOWN_EXECUTION_LIMITATIONS,
    AShareExecutionPolicy,
    CostSchedule,
    ExecutionRequest,
    MarketBar,
)
from openalpha_cn.backtest.factor_ic import (
    ICCensus,
    neutralized_cross_section,
    processed_cross_section,
    raw_cross_section,
)
from openalpha_cn.backtest.factor_portfolio import (
    BOARD_MINIMUM_QUANTITY,
    HOLDING_OUTCOME_ORDER,
    KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS,
    MAXIMUM_PORTFOLIO_GROUPS,
    MINIMUM_GROUP_SECURITIES,
    MINIMUM_PORTFOLIO_GROUPS,
    MINIMUM_PORTFOLIO_PERIODS,
    PERIOD_COVERAGE_ORDER,
    PRE_EXECUTION_COVERAGE,
    QUANTILE_PORTFOLIO_LIMITATION_CODES,
    SERIES_COVERAGE_CODES,
    SHARE_LOT,
    FactorPortfolioError,
    GroupReturn,
    PeriodPortfolio,
    PortfolioCensus,
    PositionRejection,
    QuantilePortfolioSpec,
    QuantilePortfolioStudy,
    QuantilePortfolioSummary,
    RoundTrip,
    position_quantity,
    rank_groups,
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

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
AS_OF: Final[datetime] = datetime(2026, 6, 10, 8, 30, tzinfo=UTC)
"""16:30 Asia/Shanghai on 2026-06-10, so the prediction day is the 10th and the entry the 11th."""

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
"""Enough notional at a ¥100 close for the ¥5 minimum commission never to bind: 1,000 shares is
¥100,000, six times the ¥16,666.67 the floor reaches. The one test that varies it says so."""


def code(index: int) -> str:
    return f"{index:06d}.SZ"


def _definition(
    direction: str = "higher_is_better", *, key: str = "probe_group"
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


def _spec(
    direction: str = "higher_is_better",
    *,
    group_count: int = 3,
    min_securities_per_group: int = 2,
    position_capital: Decimal = CAPITAL,
    min_periods: int = 2,
    key: str = "probe_group",
) -> QuantilePortfolioSpec:
    return QuantilePortfolioSpec(
        definition=_definition(direction, key=key),
        group_count=group_count,
        min_securities_per_group=min_securities_per_group,
        position_capital=position_capital,
        min_periods=min_periods,
    )


def _study(
    spec: QuantilePortfolioSpec | None = None,
    *,
    costs: CostSchedule | None = None,
) -> QuantilePortfolioStudy:
    return QuantilePortfolioStudy(
        spec if spec is not None else _spec(),
        execution=AShareExecutionPolicy(costs=costs or CostSchedule()),
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
) -> OutcomeLabel:
    """A real `label_outcome` over a synthetic path whose cumulative adjusted return is chosen.

    Adjustment factors are `1.0` on every session, so `WindowReturn.adjusted` is exactly
    `close_exit / close_entry - 1` and the published path agrees with it term by term. The price
    band is `0.01..10,000`, so no session is locked and the label admits every security -- which
    is the shape this module needs, because a name the *label* refuses never reaches an order and
    could not exercise `AShareExecutionPolicy` at all.
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
        limits={
            day: PriceLimit(ts_code=ts_code, trade_date=day, up_limit=10_000.0, down_limit=0.01)
            for day in span.sessions
        },
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=UNIVERSE,
    )


def _observation(subject: str, value: float, *, as_of: datetime = AS_OF) -> FactorObservation:
    return FactorObservation(
        subject=subject,
        as_of=as_of,
        value=value,
        coverage="computed",
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
    published_band: tuple[float, float] | None = None,
) -> MarketBar:
    fields: dict[str, object] = {}
    if published_band is not None:
        fields = {
            "up_limit": Decimal(str(published_band[0])),
            "down_limit": Decimal(str(published_band[1])),
        }
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
        **fields,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------------------------
# The nineteen-security fixture, built so that no two census cells share a value
# --------------------------------------------------------------------------------------------

HELD: Final[str] = "held"
UNBARRED: Final[str] = "unbarred"
BELOW_MINIMUM: Final[str] = "below_board_minimum"
REJECTED_ENTRY: Final[str] = "rejected_entry"
REJECTED_EXIT: Final[str] = "rejected_exit"

CENSUS_CASES: Final[dict[int, str]] = {
    1: UNBARRED,
    2: BELOW_MINIMUM,
    3: BELOW_MINIMUM,
    4: REJECTED_ENTRY,
    5: HELD,
    6: HELD,
    7: REJECTED_ENTRY,
    8: REJECTED_ENTRY,
    9: REJECTED_EXIT,
    10: REJECTED_EXIT,
    11: HELD,
    12: HELD,
    13: HELD,
    14: REJECTED_EXIT,
    15: REJECTED_EXIT,
    16: HELD,
    17: HELD,
    18: HELD,
    19: HELD,
}
"""Nineteen securities scored `1.0 .. 19.0`, cut into three groups of 6, 7 and 6 by rank.

The exclusions are placed so that every census cell is a different number -- 1 `unbarred`, 2
`below_board_minimum`, 3 `rejected_entry`, 4 `rejected_exit`, 9 `held`, 0 `unattempted` against 19
offered -- and so that the three groups keep **2, 3 and 4** positions rather than an equal share.
Both are deliberate: `V2-P3-004`'s review found a column that was asserted and whose assertion
could not tell two answers apart, because the fixture made them equal, and a group whose
`held_count` equalled its index would let the two be swapped with every test still green.
"""

REJECTED_ENTRY_LIMIT_CLOSE: Final[float] = 110.0
"""A ¥100 previous close and a ¥110 one-price bar: exactly `_board_limit("main")`'s 10%, so
`_price_band` derives an upper limit of `110.00` and `low >= upper` refuses the buy.

The bar carries **no published band**, which is not incidental -- it is the only way a security the
label contract admitted can be refused an entry at all, and it is
`an_unpublished_band_on_a_bar_is_judged_by_the_derived_one` happening in a fixture. The label reads
its own `PriceLimit` corpus (a `0.01..10,000` band, so nothing is locked) and the policy reads the
bar; a caller who supplies both consistently sees this security refused by neither.
"""


def _census_inputs(
    cases: dict[int, str] | None = None,
    *,
    capital_price: float = 100.0,
) -> tuple[object, dict[str, tuple[MarketBar, MarketBar]]]:
    """The cross section and the bar pairs for `cases`, keyed by the rank each security carries."""
    chosen = CENSUS_CASES if cases is None else cases
    window = _window()
    labels: dict[str, OutcomeLabel] = {}
    observations: list[FactorObservation] = []
    bars: dict[str, tuple[MarketBar, MarketBar]] = {}
    for rank, outcome in chosen.items():
        subject = code(rank)
        price = 1500.0 if outcome == BELOW_MINIMUM else capital_price
        total_return = rank / 100.0
        labels[subject] = _label(subject, total_return=total_return, start_price=price)
        observations.append(_observation(subject, float(rank)))
        if outcome == UNBARRED:
            continue
        entry_close = REJECTED_ENTRY_LIMIT_CLOSE if outcome == REJECTED_ENTRY else price
        bars[subject] = (
            _bar(subject, window.entry_day, close=entry_close, previous_close=price),
            _bar(
                subject,
                window.exit_day,
                close=price * (1.0 + total_return),
                previous_close=price * (1.0 + total_return),
                suspended=outcome == REJECTED_EXIT,
            ),
        )
    return (
        raw_cross_section(as_of=AS_OF, observations=observations, labels=labels),
        bars,
    )


def _simple_inputs(
    ranks: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
    *,
    scores: dict[int, float] | None = None,
    returns: dict[int, float] | None = None,
    price: float = 100.0,
    drop_bars: tuple[int, ...] = (),
    as_of: datetime = AS_OF,
    horizon: str = "1d",
) -> tuple[object, dict[str, tuple[MarketBar, MarketBar]]]:
    """A cross section of distinct-scored securities that fills a three-way cut evenly."""
    window = _window(as_of=as_of, horizon=horizon)
    labels: dict[str, OutcomeLabel] = {}
    observations: list[FactorObservation] = []
    bars: dict[str, tuple[MarketBar, MarketBar]] = {}
    for rank in ranks:
        subject = code(rank)
        total_return = (returns or {}).get(rank, rank / 100.0)
        labels[subject] = _label(
            subject, window=window, total_return=total_return, start_price=price
        )
        observations.append(
            _observation(subject, (scores or {}).get(rank, float(rank)), as_of=as_of)
        )
        if rank in drop_bars:
            continue
        bars[subject] = (
            _bar(subject, window.entry_day, close=price, previous_close=price),
            _bar(
                subject,
                window.exit_day,
                close=price * (1.0 + total_return),
                previous_close=price * (1.0 + total_return),
            ),
        )
    return (
        raw_cross_section(as_of=as_of, observations=observations, labels=labels),
        bars,
    )


def _processed_inputs(
    coverages: dict[int, str],
    *,
    price: float = 100.0,
) -> tuple[object, dict[str, tuple[MarketBar, MarketBar]]]:
    """A `factor_proc_*` cross section, so the tier travels and `imputed` can be offered."""
    window = _window()
    labels: dict[str, OutcomeLabel] = {}
    observations: list[ProcessedFactorObservation] = []
    bars: dict[str, tuple[MarketBar, MarketBar]] = {}
    for rank, coverage in coverages.items():
        subject = code(rank)
        total_return = rank / 100.0
        labels[subject] = _label(subject, window=window, total_return=total_return)
        observations.append(
            ProcessedFactorObservation(
                subject=subject,
                as_of=AS_OF,
                value=float(rank),
                coverage=coverage,  # type: ignore[arg-type]
                transform_id="ftx_probe",
                transform_manifest_id="ftm_probe",
                source_factor_id="fct_probe",
                source_manifest_id="fmn_probe",
                source_coverage="computed" if coverage == "processed" else "input_missing",
            )
        )
        if coverage != "processed":
            continue
        bars[subject] = (
            _bar(subject, window.entry_day, close=price, previous_close=price),
            _bar(
                subject,
                window.exit_day,
                close=price * (1.0 + total_return),
                previous_close=price * (1.0 + total_return),
            ),
        )
    return (
        processed_cross_section(as_of=AS_OF, observations=observations, labels=labels),
        bars,
    )


# --------------------------------------------------------------------------------------------
# What a period says about the cross section it was measured from
# --------------------------------------------------------------------------------------------


def test_a_period_names_the_cross_section_and_the_spec_it_was_measured_from() -> None:
    """Every identifying field a stored period renders, against the object it came from.

    Each is checked against a *different* source, so no two can be swapped: `as_of`, the horizon
    and the two session dates come from the cross section (and the horizon is `5d` here rather
    than the `1d` every other fixture uses, so `horizon_sessions` cannot be confused with a group
    index or a count), while `factor_id`, `direction` and `group_count` come from the spec.
    """
    section, bars = _simple_inputs(horizon="5d")
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    window = _window(horizon="5d")

    assert period.as_of == AS_OF
    assert period.tier == "raw"
    assert period.horizon_sessions == 5
    assert period.entry_day == window.entry_day == date(2026, 6, 11)
    assert period.exit_day == window.exit_day == date(2026, 6, 18)
    assert period.entry_day != period.exit_day
    assert period.factor_id == _spec().factor_id
    assert period.direction == "higher_is_better"
    assert period.group_count == 3
    assert period.coverage == "measured"


def test_the_processed_tier_travels_and_an_imputed_value_never_enters_a_group() -> None:
    """Requirement four: the three tiers, with `factor_ic`'s admission table inherited whole.

    `V2-P3-003` stored imputed values under their own code precisely so a later consumer could
    decline them, and `V2-P3-005` declined them for a correlation. A portfolio has the same
    objection and does not restate the rule: `TIER_ADMITTED_CODES` is imported, so a sixth
    processed code that carried a value would fail `factor_ic`'s import-time reconciliation rather
    than being dropped from every group with nothing able to say so.

    The assertion that matters is not that `imputed` is counted -- it is that **admitting it would
    have moved the answer**, which is what separates a live exclusion from a fixture where either
    choice looks the same. The same seven securities are measured twice, once with the fourth
    stored as `imputed` and once with it stored as `processed`, and the middle group's return has
    to differ: six securities cut three ways is `2, 2, 2` and seven is `2, 3, 2`.
    """
    coverages = dict.fromkeys(range(1, 8), "processed")
    section, bars = _processed_inputs({**coverages, 4: "imputed"})
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    whole_section, whole_bars = _processed_inputs(coverages)
    whole = _study().measure(whole_section, bars=whole_bars)  # type: ignore[arg-type]
    admitted = {item.subject for group in period.groups for item in group.holdings}

    assert period.tier == "processed"
    assert period.source_census.subject_count == 7
    assert period.source_census.admitted_count == 6
    assert dict(period.source_census.excluded_by_coverage)["imputed"] == 1
    assert code(4) not in admitted
    assert [group.held_count for group in period.groups] == [2, 2, 2]
    assert [group.held_count for group in whole.groups] == [2, 3, 2]
    assert code(4) in {item.subject for item in whole.groups[1].holdings}
    assert period.group_net_returns[1] != whole.group_net_returns[1]
    assert period.group_net_returns[0] == whole.group_net_returns[0]


def test_the_neutralized_tier_travels_and_carries_the_year_end_snapshot_limitation() -> None:
    """The third tier, and the one constraint a reader of its numbers has to be told about.

    See `KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS.neutralised_residuals_are_read_at_a_year_end
    _snapshot`: the residuals' content is clean and their timestamps are not, so a neutralised
    series is not point-in-time the way a raw or processed one is. `V2-P4-026` is the fix.
    """
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
    period = _study().measure(section, bars=bars)

    assert period.tier == "neutralized"
    assert period.coverage == "measured"
    assert period.census.held_count == 6
    assert "a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule" in (
        QUANTILE_PORTFOLIO_LIMITATION_CODES
    )


# --------------------------------------------------------------------------------------------
# The cut: declared, tie-safe, and empty groups are a code rather than a repair
# --------------------------------------------------------------------------------------------


def test_no_cross_section_can_be_ranked_into_a_group_that_does_not_exist() -> None:
    """The property `rank_groups` has instead of a clamp, driven over random shapes.

    `rank <= n` for every security, so `int((rank - 0.5) * group_count / n)` is short of
    `group_count` by `group_count / (2n) > 0` at worst. A `min(...)` guard would be a branch no
    input reaches, and an unreachable branch is not evidence of anything -- so the claim is
    checked here, on 400 random `(size, group_count, tie density)` combinations rather than on one.
    """
    generator = random.Random(20260813)
    seen_top = 0
    for _case in range(400):
        size = generator.randint(1, 60)
        groups = generator.randint(MINIMUM_PORTFOLIO_GROUPS, 25)
        distinct = generator.randint(1, size)
        values = [float(generator.randrange(distinct)) for _index in range(size)]
        assignment = rank_groups(values, group_count=groups)

        assert len(assignment) == size
        assert all(0 <= index < groups for index in assignment), (values, groups)
        seen_top += groups - 1 in assignment

    assert seen_top > 200, "the corpus has to reach the top group, or it pins the easy half"


def test_tied_scores_share_a_group_whatever_order_they_arrive_in() -> None:
    """The reason the cut is by average rank and not by sort position.

    A sort-and-slice implementation puts two securities with the *same* factor value on either
    side of a boundary according to its tie-break, which makes group membership a function of the
    caller's row order. A discretised factor ties constantly, so this is the ordinary case. The
    assertion is on a value-to-group mapping, which is the thing that has to be a function.
    """
    values = [1.0, 2.0, 2.0, 2.0, 3.0, 3.0]
    assignment = rank_groups(values, group_count=3)

    assert assignment == (0, 1, 1, 1, 2, 2)
    generator = random.Random(7)
    for _case in range(50):
        order = list(range(len(values)))
        generator.shuffle(order)
        shuffled = [values[index] for index in order]
        rotated = rank_groups(shuffled, group_count=3)
        assert {shuffled[i]: rotated[i] for i in range(len(order))} == {
            1.0: 0,
            2.0: 1,
            3.0: 2,
        }


def test_the_cut_of_a_distinctly_scored_cross_section_is_the_hand_computed_one() -> None:
    """Nineteen ranks into three groups: `6, 7, 6` and not `6, 6, 7` or `7, 6, 6`.

    Hand-checkable from `int((r - 0.5) * 3 / 19)`, and the sizes are unequal by one, which is what
    makes this fixture able to tell the formula from a `floor(r * k / n)` or a `(r - 1) * k / n`
    spelling -- both of those move a single security across a boundary and would still produce
    three non-empty groups.
    """
    assignment = rank_groups([float(rank) for rank in range(1, 20)], group_count=3)

    assert assignment == (0,) * 6 + (1,) * 7 + (2,) * 6
    assert [assignment.count(index) for index in range(3)] == [6, 7, 6]


def test_rank_groups_refuses_a_single_group_and_an_empty_cross_section() -> None:
    """Both are malformed questions rather than facts: one group has no spread to read, and an
    empty cross section is not a thin one."""
    with pytest.raises(FactorPortfolioError, match="at least 2 groups"):
        rank_groups([1.0, 2.0], group_count=1)
    with pytest.raises(FactorPortfolioError, match="empty cross section has no ranks"):
        rank_groups([], group_count=3)


# --------------------------------------------------------------------------------------------
# Sizing: the board's own rule, restated and held against the policy that owns it
# --------------------------------------------------------------------------------------------


def test_every_board_minimum_is_the_quantity_the_execution_policy_itself_accepts() -> None:
    """`BOARD_MINIMUM_QUANTITY` against `AShareExecutionPolicy`, board by board.

    The table is a restatement -- the rule lives in two branches of a private function and there
    is nothing to import -- so it is pinned rather than trusted: at each board's declared minimum
    the real policy must fill, and at one share below it the real policy must reject. A board
    whose rule moved in `execution.py` fails here rather than silently sizing orders that get
    refused one call later.
    """
    policy = AShareExecutionPolicy(costs=CostSchedule())
    day = date(2026, 6, 11)
    verdicts = {}
    for board, minimum in BOARD_MINIMUM_QUANTITY.items():
        market = _bar(code(1), day, close=10.0, previous_close=10.0, board=board)
        verdicts[board] = (
            policy.execute(ExecutionRequest(side="buy", quantity=minimum), market).status,
            policy.execute(ExecutionRequest(side="buy", quantity=minimum - 1), market).status,
        )

    assert verdicts == {board: ("filled", "rejected") for board in BOARD_MINIMUM_QUANTITY}
    assert dict(BOARD_MINIMUM_QUANTITY) == {
        "main": 100,
        "star": 200,
        "growth": 100,
        "bse": 100,
    }
    assert SHARE_LOT == 100
    assert {board: minimum == SHARE_LOT for board, minimum in BOARD_MINIMUM_QUANTITY.items()} == {
        "main": True,
        "star": False,
        "growth": True,
        "bse": True,
    }


def test_the_star_board_spends_the_whole_budget_and_every_other_board_rounds_to_lots() -> None:
    """STAR's rule is a floor and every other board's is a multiple, so the two sizers differ.

    `¥2,550` at a `¥10` close buys 255 shares on STAR and 200 anywhere else -- two numbers, not
    one rounded twice. Sizing STAR to 100-share lots as well would leave up to 99 shares of the
    declared capital idle on every STAR position and make the reported cost drag a function of
    which board a name happens to be on.
    """
    day = date(2026, 6, 11)
    sized = {
        board: position_quantity(
            capital=Decimal("2550"),
            market=_bar(code(1), day, close=10.0, previous_close=10.0, board=board),
        )
        for board in BOARD_MINIMUM_QUANTITY
    }

    assert sized == {"main": 200, "star": 255, "growth": 200, "bse": 200}
    edges = {
        ("star", "2000"): 200,
        ("star", "1999"): 0,
        ("main", "9999"): 900,
        ("main", "99"): 0,
    }

    assert {
        (board, capital): position_quantity(
            capital=Decimal(capital),
            market=_bar(code(1), day, close=10.0, previous_close=10.0, board=board),
        )
        for board, capital in edges
    } == edges


def test_position_quantity_refuses_a_capital_that_is_not_positive() -> None:
    """A zero budget would report the whole market as `below_board_minimum` and call that a fact
    about the market rather than about the declaration."""
    with pytest.raises(FactorPortfolioError, match=r"position capital .* is not positive"):
        position_quantity(
            capital=Decimal("0"),
            market=_bar(code(1), date(2026, 6, 11), close=10.0, previous_close=10.0),
        )


# --------------------------------------------------------------------------------------------
# The join: the label supplies the move, the policy supplies the money
# --------------------------------------------------------------------------------------------


def test_the_gross_value_is_the_labels_adjusted_return_and_not_the_two_fills_ratio() -> None:
    """The measured hazard, driven on the session that produced it.

    `000001.SZ` on 2026-06-12: `close/pre_close` gives `+2.742230%`, the `adj_factor` path
    `+2.742251%`, and `close[t]/close[t-1]` -- which is exactly what the two `ExecutionResult`
    notionals would give -- gives `-0.530973%`, the sign reversed. So this holds one position over
    those two bars and asserts that the position's exit value is the label's number and is **not**
    the sell leg's notional, which is what an implementation that priced the round trip from its
    own fills would have produced. The two are 3.2 percentage points apart here.

    It also pins the direction and the size of `the_exit_leg_is_priced_on_the_entry_share_count`:
    the sell-side fee really is charged on `q * published exit close`, which is the correct
    treatment of a cash dividend (the dividend is cash and pays no stamp duty) and is the
    approximation on a share change.
    """
    window = _window()
    entry, exit_day = window.entry_day, window.exit_day
    ex_rights = label_outcome(
        window,
        ts_code=code(1),
        bars={
            entry: _daily(code(1), entry, close=11.30, pre_close=11.32),
            exit_day: _daily(code(1), exit_day, close=11.24, pre_close=10.94),
        },
        factors=build_adjustment_history(
            code(1),
            [
                AdjustmentFactor(ts_code=code(1), observed_on=entry, factor=134.5794),
                AdjustmentFactor(ts_code=code(1), observed_on=exit_day, factor=139.008),
            ],
        ),
        limits={
            day: PriceLimit(ts_code=code(1), trade_date=day, up_limit=1000.0, down_limit=0.01)
            for day in window.sessions
        },
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=UNIVERSE,
    )
    others = {name: _label(name, total_return=0.01) for name in (code(2), code(3), code(4))}
    section = raw_cross_section(
        as_of=AS_OF,
        observations=[
            _observation(name, float(rank))
            for rank, name in enumerate((code(1), code(2), code(3), code(4)), start=1)
        ],
        labels={code(1): ex_rights, **others},
    )
    bars = {
        code(1): (
            _bar(code(1), entry, close=11.30, previous_close=11.32),
            _bar(code(1), exit_day, close=11.24, previous_close=10.94),
        ),
        **{
            name: (
                _bar(name, entry, close=100.0, previous_close=100.0),
                _bar(name, exit_day, close=101.0, previous_close=100.0),
            )
            for name in (code(2), code(3), code(4))
        },
    }
    period = _study(_spec(group_count=2, min_securities_per_group=2)).measure(
        section,  # type: ignore[arg-type]
        bars=bars,
    )
    position = next(
        item for group in period.groups for item in group.holdings if item.subject == code(1)
    )

    assert position.gross_return == ex_rights.realized_return
    assert position.gross_return == pytest.approx(0.0274225, abs=1e-7)
    assert position.quantity == 8800
    assert position.entry_notional == Decimal("99440.00")
    assert position.gross_value == Decimal("102166.89")
    assert position.exit_fill.notional == Decimal("98912.00")
    assert position.gross_value != position.exit_fill.notional
    gap = float(position.exit_fill.notional) / float(position.gross_value) - 1.0
    assert gap == pytest.approx(-0.0318586, abs=1e-7)


def test_a_window_with_no_corporate_action_prices_the_exit_leg_at_the_published_close() -> None:
    """The other side of the claim above, and it is measured over a corpus rather than a fixture.

    With a constant adjustment factor `realized_return` **is** `close_exit / close_entry - 1`, so
    `entry_notional * (1 + realized)` and `quantity * close_exit` are the same money -- but only up
    to a `float` round trip and two cent quantizations, which is a claim about arithmetic and not
    an algebraic identity. Driven over 2,000 random `(quantity, entry, exit)` combinations spanning
    `¥1.00`..`¥3,000.00` and 100..10,000 shares: **0 disagree**, and a wider 200,000-case sweep of
    the same shape also found none.

    That is the sentence this module's docstring makes, and it is what makes the ex-rights gap
    above readable as the corporate action rather than as a modelling choice made everywhere.
    """
    section, bars = _simple_inputs()
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    for group in period.groups:
        for position in group.holdings:
            assert position.gross_value == position.exit_fill.notional

    policy = AShareExecutionPolicy(costs=CostSchedule())
    generator = random.Random(20260813)
    checked = 0
    for _case in range(2_000):
        quantity = generator.choice((100, 200, 500, 1000, 8800, 10_000))
        entry = round(generator.uniform(1.0, 3000.0), 2)
        exit_price = round(generator.uniform(1.0, 3000.0), 2)
        day = date(2026, 6, 11)
        position = RoundTrip(
            subject=code(1),
            group=0,
            quantity=quantity,
            entry_fill=policy.execute(
                ExecutionRequest(side="buy", quantity=quantity),
                _bar(code(1), day, close=entry, previous_close=entry),
            ),
            exit_fill=policy.execute(
                ExecutionRequest(side="sell", quantity=quantity),
                _bar(code(1), day, close=exit_price, previous_close=exit_price),
            ),
            gross_return=exit_price / entry - 1.0,
            unpublished_band_legs=0,
        )
        assert position.gross_value == position.exit_fill.notional, (quantity, entry, exit_price)
        checked += 1

    assert checked == 2_000


def test_the_position_arithmetic_is_the_two_execution_results_and_nothing_re_derived() -> None:
    """Every money number on a `RoundTrip` traces to a fill or to the label, term by term.

    Driven on a `¥100` close at `¥100,000` of capital where each term is hand-checkable: 1,000
    shares, `¥30.00` commission (3bp, above the `¥5` floor), `¥1.00` transfer fee on the buy, and
    the same plus `¥52.50` of stamp duty on a `¥105,000` sale.
    """
    section, bars = _simple_inputs(returns={5: 0.05})
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    position = next(
        item for group in period.groups for item in group.holdings if item.subject == code(5)
    )

    assert position.quantity == 1000
    assert position.entry_fill.notional == Decimal("100000.00")
    assert position.entry_fill.commission == Decimal("30.00")
    assert position.entry_fill.transfer_fee == Decimal("1.00")
    assert position.entry_fill.stamp_duty == Decimal("0.00")
    assert position.entry_outlay == Decimal("100031.00")
    assert position.gross_value == Decimal("105000.00")
    assert position.exit_fill.commission == Decimal("31.50")
    assert position.exit_fill.stamp_duty == Decimal("52.50")
    assert position.exit_fill.total_cost == Decimal("85.05")
    assert position.net_proceeds == Decimal("104914.95")
    assert position.net_return == pytest.approx(0.04882436, abs=1e-8)
    assert position.gross_return == pytest.approx(0.05, abs=1e-12)
    assert position.entry_fill.total_cost / position.entry_notional * 10_000 == Decimal("3.1")
    assert position.exit_fill.total_cost / position.exit_fill.notional * 10_000 == Decimal("8.1")


def test_the_float_to_decimal_bridge_is_the_repr_and_a_binary_double_loses_a_cent() -> None:
    """`Decimal(str(g))` and not `Decimal(g)`, on a pair where the two answer different money.

    `published_limit_fields` names this trap and this is the second place in the repository that
    has to cross it: the label contract holds a `float` and the fee schedule holds `Decimal`s, so
    something must bridge them, and `Decimal(0.1)` is `0.1000000000000000055511151231257827...`
    -- the binary double carried into a type whose whole point is that it does not do that.

    **A fixture had to be constructed for it, and that is worth saying rather than hiding.** The
    two paths differ by about `notional * 1e-16`, so they round to the same cent unless the exact
    product lands within that of a half-cent boundary. `¥101.00` of notional (100 shares at
    `¥1.01`, both real fills from the real policy) against a `-46.5%` window puts it exactly there:
    `101.00 * 0.535` is `54.035`, which `ROUND_HALF_UP` takes to `54.04`, while the double for
    `-0.465` sits just below `-0.465` and drags the product to `54.0349999...`, which rounds to
    `54.03`. One cent, and it is the whole claim the docstring makes.
    """
    policy = AShareExecutionPolicy(costs=CostSchedule())
    market = _bar(code(1), date(2026, 6, 11), close=1.01, previous_close=1.01)
    position = RoundTrip(
        subject=code(1),
        group=0,
        quantity=100,
        entry_fill=policy.execute(ExecutionRequest(side="buy", quantity=100), market),
        exit_fill=policy.execute(ExecutionRequest(side="sell", quantity=100), market),
        gross_return=-0.465,
        unpublished_band_legs=2,
    )
    notional = position.entry_notional
    measured = position.gross_return
    binary = (notional * (Decimal(1) + Decimal(measured))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )

    assert notional == Decimal("101.00")
    assert position.gross_value == Decimal("54.04")
    assert binary == Decimal("54.03")
    assert position.gross_value != binary
    assert Decimal(measured) < Decimal(str(measured))


def test_a_group_return_is_the_portfolios_own_and_not_the_mean_of_its_members() -> None:
    """`sum(net_proceeds) / sum(entry_outlay) - 1`, which differs from the members' mean whenever
    the positions are not the same size.

    Two securities at `¥100` and `¥300`, one lot budget each: the `¥300` name takes 300 shares
    (`¥90,000`) and the `¥100` name 1,000 (`¥100,000`), so the two carry 47.4% and 52.6% of the
    group. A mean of the two returns would weight them equally and comes out a different number,
    which is what makes this assertion able to tell the two definitions apart.
    """
    window = _window()
    labels = {
        code(1): _label(code(1), total_return=0.10, start_price=100.0),
        code(2): _label(code(2), total_return=-0.10, start_price=300.0),
    }
    section = raw_cross_section(
        as_of=AS_OF,
        observations=[_observation(code(1), 1.0), _observation(code(2), 2.0)],
        labels=labels,
    )
    bars = {
        code(1): (
            _bar(code(1), window.entry_day, close=100.0, previous_close=100.0),
            _bar(code(1), window.exit_day, close=110.0, previous_close=100.0),
        ),
        code(2): (
            _bar(code(2), window.entry_day, close=300.0, previous_close=300.0),
            _bar(code(2), window.exit_day, close=270.0, previous_close=270.0),
        ),
    }
    period = _study(_spec(group_count=2, min_securities_per_group=1)).measure(
        section,  # type: ignore[arg-type]
        bars=bars,
    )
    group = GroupReturn(
        group=0,
        holdings=tuple(
            replace(item, group=0) for grouped in period.groups for item in grouped.holdings
        ),
    )
    members = [item.net_return for item in group.holdings]

    assert group.entry_notional == Decimal("190000.00")
    assert group.gross_value == Decimal("191000.00")
    assert group.entry_cost == Decimal("58.90")
    assert group.exit_cost == Decimal("154.71")
    assert group.gross_return == pytest.approx(0.00526316, abs=1e-8)
    assert group.net_return == pytest.approx(0.00413761, abs=1e-8)
    assert sum(members) / len(members) == pytest.approx(-0.00111965, abs=1e-8)
    assert group.net_return != pytest.approx(sum(members) / len(members), abs=1e-4)
    assert group.cost_drag == group.gross_return - group.net_return


# --------------------------------------------------------------------------------------------
# What a refused order is worth: nothing, and the fixture proves the nothing moved the answer
# --------------------------------------------------------------------------------------------


def test_a_rejected_entry_leaves_the_group_rather_than_reading_flat() -> None:
    """The load-bearing test of this module, and it is not "a rejection is counted".

    It is that *counting it moved the answer*. The same group is recomputed with the refused
    security carried at a `0.0` return -- the substitution this module refuses -- and the two group
    returns must differ. Without that half, a fixture in which the refused name happened to return
    zero anyway would pass while proving nothing about the rule.
    """
    window = _window()
    labels = {
        code(rank): _label(code(rank), total_return=0.10, start_price=100.0)
        for rank in (1, 2, 3, 4)
    }
    section = raw_cross_section(
        as_of=AS_OF,
        observations=[_observation(code(rank), float(rank)) for rank in (1, 2, 3, 4)],
        labels=labels,
    )
    bars = {
        code(rank): (
            _bar(
                code(rank),
                window.entry_day,
                close=REJECTED_ENTRY_LIMIT_CLOSE if rank == 1 else 100.0,
                previous_close=100.0,
            ),
            _bar(code(rank), window.exit_day, close=110.0, previous_close=100.0),
        )
        for rank in (1, 2, 3, 4)
    }
    period = _study(_spec(group_count=2, min_securities_per_group=1)).measure(
        section,  # type: ignore[arg-type]
        bars=bars,
    )
    bottom = period.groups[0]
    substituted = GroupReturn(
        group=0,
        holdings=(
            *bottom.holdings,
            replace(period.groups[1].holdings[0], group=0, subject=code(1), gross_return=0.0),
        ),
    )

    assert [item.subject for item in bottom.holdings] == [code(2)]
    assert period.census.held_count == 3
    assert dict(period.census.excluded_by_outcome)["rejected_entry"] == 1
    assert [item.subject for item in period.rejections] == [code(1)]
    assert period.rejections[0].outcome == "rejected_entry"
    assert period.rejections[0].reason == "buy cannot fill on a one-price limit-up bar"
    assert bottom.net_return == pytest.approx(0.0988, abs=1e-4)
    assert substituted.net_return == pytest.approx(0.0487, abs=1e-4)
    assert bottom.net_return != pytest.approx(substituted.net_return, abs=1e-3)


def test_a_rejected_exit_leaves_the_group_after_the_position_was_opened() -> None:
    """The exit leg is judged separately and against the exit bar, so a name that could be bought
    and not sold is `rejected_exit` and not `rejected_entry`.

    Two codes rather than one because the remedies differ: an entry nobody could place is a
    position that never existed, and an exit nobody could take is capital that is still committed
    when the study wants to stop measuring it.
    """
    section, bars = _census_inputs()
    period = _study(_spec(group_count=3, min_securities_per_group=2)).measure(
        section,  # type: ignore[arg-type]
        bars=bars,
    )
    outcomes = {item.subject: item.outcome for item in period.rejections}

    assert outcomes[code(9)] == "rejected_exit"
    assert outcomes[code(4)] == "rejected_entry"
    assert {item.reason for item in period.rejections if item.outcome == "rejected_exit"} == {
        "security is suspended"
    }


def test_a_security_with_no_bar_pair_is_unbarred_and_not_rejected() -> None:
    """A short read looks exactly like a refusal until the two are counted apart, which is
    `ICCensus.unmatched_count`'s argument arriving one plane along."""
    section, bars = _census_inputs()
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    outcomes = {item.subject: item.outcome for item in period.rejections}

    assert outcomes[code(1)] == "unbarred"
    assert "no entry/exit bar pair was offered" in period.rejections[0].reason


def test_the_period_census_adds_up_and_no_two_of_its_cells_share_a_value() -> None:
    """The whole funnel, in one assertion, over a fixture engineered so nothing can be swapped.

    Nineteen securities offered, 1 `unbarred`, 2 `below_board_minimum`, 3 `rejected_entry`, 4
    `rejected_exit`, 9 held, 0 unattempted -- six distinct numbers, so a census that reported one
    cell's count under another heading fails. The groups keep 2, 3 and 4 positions rather than an
    equal share for the same reason.
    """
    section, bars = _census_inputs()
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    cells = dict(period.census.excluded_by_outcome)

    assert period.coverage == "measured"
    assert period.census.offered_count == 19
    assert period.census.held_count == 9
    assert period.census.unattempted_count == 0
    assert cells == {
        "unbarred": 1,
        "below_board_minimum": 2,
        "rejected_entry": 3,
        "rejected_exit": 4,
    }
    assert len({*cells.values(), period.census.held_count, period.census.offered_count}) == 6
    assert [group.held_count for group in period.groups] == [2, 3, 4]
    assert period.source_census.admitted_count == 19
    assert period.source_census.subject_count == 19


def test_a_below_minimum_position_is_the_declared_capitals_verdict_and_not_the_policys() -> None:
    """`ExecutionRequest.quantity` is `gt=0`, so a budget that buys nothing has no order to place
    at all -- which is why this code is decided by the sizer and names the capital and the close
    that produced it rather than quoting a rejection the policy never issued."""
    section, bars = _census_inputs()
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    reasons = {
        item.subject: item.reason
        for item in period.rejections
        if item.outcome == "below_board_minimum"
    }

    assert set(reasons) == {code(2), code(3)}
    assert "does not reach the main board's minimum of 100 shares" in reasons[code(2)]
    assert "a position capital of 100000" in reasons[code(2)]


# --------------------------------------------------------------------------------------------
# The coverage codes, and the neighbour each one has to be told apart from
# --------------------------------------------------------------------------------------------


def test_a_thin_cross_section_is_insufficient_sample_and_places_no_order_at_all() -> None:
    """Decided before the cut, so every offered security is `unattempted` rather than carrying a
    per-security verdict about an order nobody submitted."""
    section, bars = _simple_inputs(ranks=(1, 2, 3, 4, 5))
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]

    assert period.coverage == "insufficient_sample"
    assert period.groups == ()
    assert period.census.unattempted_count == 5
    assert period.census.held_count == 0
    assert dict(period.census.excluded_by_outcome) == dict.fromkeys(
        (code for code in HOLDING_OUTCOME_ORDER if code != "held"), 0
    )
    assert period.rejections == ()
    assert period.long_short_spread is None
    assert period.raw_spread is None
    assert period.gross_long_short_spread is None


def test_an_all_tied_cross_section_is_degenerate_scores_and_not_unfillable_groups() -> None:
    """The pair the declared **order** is the only thing that separates.

    Six securities all scoring `1.0` share one average rank, so they all land in one group and two
    groups come out empty -- which satisfies `unfillable_groups`' condition as well. Deciding
    `degenerate_scores` first is what puts the report on the half somebody can act on (the factor
    produced one value for the whole market), and this fixture is the one that would flip if the
    two checks were reordered.
    """
    section, bars = _simple_inputs(scores=dict.fromkeys(range(1, 7), 1.0))
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]

    assert period.coverage == "degenerate_scores"
    assert rank_groups([1.0] * 6, group_count=3) == (1,) * 6
    assert period.census.unattempted_count == 6


def test_ties_that_leave_a_group_below_the_floor_are_unfillable_groups() -> None:
    """The values order and the cut still cannot be filled: four securities tied at `1.0` take
    rank 2.5 and land together, leaving the bottom group empty. A fact about the factor's
    granularity against the declared cut, and distinguishable from the all-tied case above."""
    scores = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 2.0, 6: 3.0}
    section, bars = _simple_inputs(scores=scores)
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]

    assert period.coverage == "unfillable_groups"
    assert rank_groups([scores[rank] for rank in range(1, 7)], group_count=3) == (
        1,
        1,
        1,
        1,
        2,
        2,
    )
    assert period.census.unattempted_count == 6


def test_a_group_the_market_empties_is_unfillable_after_execution_and_keeps_its_verdicts() -> None:
    """The cut worked and the *market* emptied a group, which is a different finding with a
    different remedy -- a coarser cut fixes `unfillable_groups` and nothing fixes this.

    It is also the one refused code under which the per-security verdicts are kept, because the
    orders really were placed: `unattempted_count` is zero and the census still names the security
    that could not be traded. Throwing that away would discard the measurement that produced the
    code, which is the reading `V2-P3-007` exists to surface.
    """
    section, bars = _simple_inputs(drop_bars=(1,))
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]

    assert period.coverage == "unfillable_after_execution"
    assert period.coverage not in PRE_EXECUTION_COVERAGE
    assert period.groups == ()
    assert period.census.unattempted_count == 0
    assert period.census.held_count == 5
    assert dict(period.census.excluded_by_outcome)["unbarred"] == 1
    assert [item.subject for item in period.rejections] == [code(1)]


def test_the_declared_coverage_vocabulary_is_the_order_the_codes_are_decided_in() -> None:
    """The table and the three codes that skip the execution step, pinned as literals so a
    reordering or a sixth spelling of "no answer" fails rather than becoming a group of one."""
    assert PERIOD_COVERAGE_ORDER == (
        "measured",
        "insufficient_sample",
        "degenerate_scores",
        "unfillable_groups",
        "unfillable_after_execution",
    )
    assert {
        "insufficient_sample",
        "degenerate_scores",
        "unfillable_groups",
    } == PRE_EXECUTION_COVERAGE
    assert HOLDING_OUTCOME_ORDER == (
        "held",
        "unbarred",
        "below_board_minimum",
        "rejected_entry",
        "rejected_exit",
    )
    assert {"measured", "insufficient_periods"} == SERIES_COVERAGE_CODES
    assert set(PERIOD_COVERAGE_ORDER) - PRE_EXECUTION_COVERAGE == {
        "measured",
        "unfillable_after_execution",
    }


# --------------------------------------------------------------------------------------------
# `direction`: which group is long, and the fixture that separates the two answers
# --------------------------------------------------------------------------------------------


def test_the_declared_direction_decides_which_group_is_long_and_negates_the_spread() -> None:
    """Two definitions differing in nothing but `direction`, over one cross section.

    The assertion is not that both produce a number. It is that the **group table is identical**
    -- group 0 is the lowest factor value under both declarations, so a stored table stays
    diffable against the values it was cut from -- while `long_short_spread` is the *exact*
    negation, bit for bit, because IEEE subtraction is exactly rounded and `a - b` and `-(b - a)`
    are the same double.
    """
    section, bars = _simple_inputs()
    up = _study(_spec("higher_is_better")).measure(section, bars=bars)  # type: ignore[arg-type]
    down = _study(_spec("lower_is_better")).measure(section, bars=bars)  # type: ignore[arg-type]

    assert up.group_net_returns == down.group_net_returns
    assert [[item.subject for item in group.holdings] for group in up.groups] == [
        [item.subject for item in group.holdings] for group in down.groups
    ]
    assert (up.top_group_index, up.bottom_group_index) == (2, 0)
    assert (down.top_group_index, down.bottom_group_index) == (0, 2)
    assert up.raw_spread == down.raw_spread
    assert up.long_short_spread == up.raw_spread
    assert down.long_short_spread == -up.long_short_spread
    assert up.long_short_spread is not None and up.long_short_spread > 0.0
    assert up.gross_long_short_spread == pytest.approx(0.04, abs=1e-9)
    assert up.gross_long_short_spread != up.long_short_spread


# --------------------------------------------------------------------------------------------
# The seam: where `AShareExecutionPolicy` and `domain/labels.py` stop agreeing
# --------------------------------------------------------------------------------------------


def test_the_two_contracts_agree_on_the_long_round_trip_and_not_on_the_short_one() -> None:
    """`execution.py`'s third limitation, driven -- and it is the reason no short leg is offered.

    All four combinations, because the claim is about a pair of round trips and half of it would
    prove nothing. The policy refuses a **buy** into a one-price limit-**up** bar and a **sell**
    into a one-price limit-**down** one, and fills the other two; `domain/labels.py` refuses the
    session on either flag at either end. So:

        long entry  (buy into a limit-up lock)     policy refuses   label refuses   AGREE
        long exit   (sell into a limit-down lock)  policy refuses   label refuses   AGREE
        short entry (sell into a limit-up lock)    policy FILLS     label refuses   DISAGREE
        short exit  (buy into a limit-down lock)   policy FILLS     label refuses   DISAGREE

    A short leg would therefore be built out of the two verdicts this repository has already
    measured as not interchangeable, with the policy fail-open where the label refuses.
    """
    window = _window()
    locked = _bar(code(1), window.entry_day, close=110.0, previous_close=100.0)
    floored = _bar(code(1), window.exit_day, close=90.0, previous_close=100.0)
    policy = AShareExecutionPolicy(costs=CostSchedule())
    long_entry = policy.execute(ExecutionRequest(side="buy", quantity=100), locked)
    short_entry = policy.execute(ExecutionRequest(side="sell", quantity=100), locked)
    long_exit = policy.execute(ExecutionRequest(side="sell", quantity=100), floored)
    short_exit = policy.execute(ExecutionRequest(side="buy", quantity=100), floored)
    labelled = label_outcome(
        window,
        ts_code=code(1),
        bars={
            window.entry_day: _daily(code(1), window.entry_day, close=110.0, pre_close=100.0),
            window.exit_day: _daily(code(1), window.exit_day, close=110.0, pre_close=110.0),
        },
        factors=build_adjustment_history(
            code(1),
            [
                AdjustmentFactor(ts_code=code(1), observed_on=day, factor=1.0)
                for day in window.sessions
            ],
        ),
        limits={
            window.entry_day: PriceLimit(
                ts_code=code(1),
                trade_date=window.entry_day,
                up_limit=110.0,
                down_limit=90.0,
            ),
            window.exit_day: PriceLimit(
                ts_code=code(1),
                trade_date=window.exit_day,
                up_limit=121.0,
                down_limit=99.0,
            ),
        },
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=UNIVERSE,
    )

    assert (long_entry.status, short_entry.status) == ("rejected", "filled")
    assert (long_exit.status, short_exit.status) == ("rejected", "filled")
    assert not labelled.is_labelled
    assert [item.code for item in labelled.refusals] == ["locked_at_limit"]
    assert "a_one_price_session_refuses_one_side_here_and_both_ends_there" in {
        item.code for item in KNOWN_EXECUTION_LIMITATIONS
    }
    assert "the_long_short_spread_is_not_a_shortable_portfolio" in (
        QUANTILE_PORTFOLIO_LIMITATION_CODES
    )


def test_a_bar_without_a_published_band_is_counted_as_a_leg_judged_by_the_derived_one() -> None:
    """`an_unpublished_band_on_a_bar_is_judged_by_the_derived_one`, made into an integer.

    A period reporting zero is a period every one of whose fills was judged against the exchange's
    own numbers, and the fixture separates the two answers rather than asserting the field
    renders: the same six securities are measured with and without a published band on both bars,
    and the count goes from twelve legs to none while the returns are unchanged.
    """
    section, bars = _simple_inputs()
    derived = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    published = {
        subject: (
            replace_band(entry, (110.0, 90.0)),
            replace_band(exit_bar, (200.0, 50.0)),
        )
        for subject, (entry, exit_bar) in bars.items()
    }
    banded = _study().measure(section, bars=published)  # type: ignore[arg-type]

    assert derived.unpublished_band_legs == 12
    assert banded.unpublished_band_legs == 0
    assert derived.group_net_returns == banded.group_net_returns
    assert {item.unpublished_band_legs for group in derived.groups for item in group.holdings} == {
        2
    }


def replace_band(bar: MarketBar, band: tuple[float, float]) -> MarketBar:
    """`bar` with the exchange's published band attached, built the supported way."""
    return MarketBar(
        **bar.model_dump(exclude={"up_limit", "down_limit"}),
        up_limit=Decimal(str(band[0])),
        down_limit=Decimal(str(band[1])),
    )


def test_the_declared_position_capital_moves_the_net_return_by_the_minimum_commission() -> None:
    """`the_reported_returns_move_with_the_declared_position_capital`, measured rather than warned
    about.

    `CostSchedule.minimum_commission` is a `¥5` floor under a 3bp rate, so it binds below
    `¥16,666.67` of notional. The same flat cross section measured at `¥100,000` and at `¥10,000`
    of capital gives a round-trip drag of **0.1120%** and **0.1520%** of notional -- 35.7% more
    cost for the same trade -- and the two group returns differ by four basis points on a position
    that moved not at all.
    """
    section, bars = _simple_inputs(returns=dict.fromkeys(range(1, 7), 0.0), price=10.0)
    wide = _study(_spec(position_capital=Decimal("100000"))).measure(
        section,  # type: ignore[arg-type]
        bars=bars,
    )
    narrow = _study(_spec(position_capital=Decimal("10000"))).measure(
        section,  # type: ignore[arg-type]
        bars=bars,
    )
    wide_group, narrow_group = wide.groups[0], narrow.groups[0]

    assert wide_group.holdings[0].quantity == 10_000
    assert narrow_group.holdings[0].quantity == 1_000
    assert wide_group.gross_return == 0.0
    assert narrow_group.gross_return == 0.0
    assert float(wide_group.entry_cost + wide_group.exit_cost) / float(
        wide_group.entry_notional
    ) == pytest.approx(0.001120, abs=1e-9)
    assert float(narrow_group.entry_cost + narrow_group.exit_cost) / float(
        narrow_group.entry_notional
    ) == pytest.approx(0.001520, abs=1e-9)
    assert wide_group.net_return == pytest.approx(-0.00111965, abs=1e-8)
    assert narrow_group.net_return == pytest.approx(-0.00151923, abs=1e-8)
    assert pytest.approx(1.357, abs=1e-3) == 0.001520 / 0.001120


def test_the_t_plus_one_rule_is_passed_and_cannot_bind_on_a_constructible_window() -> None:
    """`position_open_date` is the entry session on every sell this module places, so T+1 is a live
    input rather than a defaulted one -- and it cannot fire, because `exit_day` is
    `calendar.shift(entry_day, horizon.sessions)` with `sessions >= 1`.

    Stated and driven so it is not mistaken for a rule this module disabled: the same policy called
    with the *exit* day as the open date does reject, which is what the argument is guarding
    against.
    """
    section, bars = _simple_inputs()
    period = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    entry_day, exit_day = period.entry_day, period.exit_day
    policy = AShareExecutionPolicy(costs=CostSchedule())
    market = bars[code(1)][1]

    assert exit_day > entry_day
    assert (
        policy.execute(
            ExecutionRequest(side="sell", quantity=1000, position_open_date=entry_day), market
        ).status
        == "filled"
    )
    assert (
        policy.execute(
            ExecutionRequest(side="sell", quantity=1000, position_open_date=exit_day), market
        ).reason
        == "A-share cash equities cannot be sold on the purchase date"
    )


# --------------------------------------------------------------------------------------------
# The boundary: bars that are not this period's
# --------------------------------------------------------------------------------------------


def test_a_bar_for_a_security_the_cross_section_never_admitted_is_refused() -> None:
    """A caller that read its two sides from two universes wants to be told rather than to get a
    shorter answer -- `ic_cross_section`'s own rule at this module's boundary."""
    section, bars = _simple_inputs()
    window = _window()
    bars[code(40)] = (
        _bar(code(40), window.entry_day, close=100.0, previous_close=100.0),
        _bar(code(40), window.exit_day, close=100.0, previous_close=100.0),
    )

    with pytest.raises(FactorPortfolioError, match="carries a bar pair and no admitted"):
        _study().measure(section, bars=bars)  # type: ignore[arg-type]


def test_a_bar_from_another_session_or_another_security_is_refused() -> None:
    """Two different malformed questions, each with its own message, because a bar dated wrong and
    a bar filed under the wrong security fail for different reasons."""
    section, bars = _simple_inputs()
    window = _window()
    strayed = dict(bars)
    strayed[code(1)] = (
        _bar(code(1), window.exit_day, close=100.0, previous_close=100.0),
        bars[code(1)][1],
    )
    strayed_match = r"entry bar is dated .* and this window's entry"
    with pytest.raises(FactorPortfolioError, match=strayed_match):
        _study().measure(section, bars=strayed)  # type: ignore[arg-type]

    misfiled = dict(bars)
    misfiled[code(1)] = (
        _bar(code(2), window.entry_day, close=100.0, previous_close=100.0),
        bars[code(1)][1],
    )
    with pytest.raises(FactorPortfolioError, match=r"entry bar filed under .* names"):
        _study().measure(section, bars=misfiled)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# The series
# --------------------------------------------------------------------------------------------


def _series(
    spreads: tuple[float, ...],
    *,
    measured: int | None = None,
) -> list[PeriodPortfolio]:
    """One period per element, whose top group returns `value` more than its bottom group.

    Built through `measure` rather than by hand so that every period in a summary is one this
    module actually produced. `measured` truncates the run: the periods past it are given five
    securities and come out `insufficient_sample`, which is the attrition a summary has to report
    rather than silently drop.
    """
    study = _study()
    periods: list[PeriodPortfolio] = []
    for index, value in enumerate(spreads):
        as_of = AS_OF + timedelta(days=index)
        ranks = (1, 2, 3, 4, 5, 6)
        if measured is not None and index >= measured:
            ranks = (1, 2, 3, 4, 5)
        section, bars = _simple_inputs(
            ranks=ranks,
            returns={rank: (value if rank > 4 else 0.0) for rank in ranks},
            as_of=as_of,
        )
        periods.append(study.measure(section, bars=bars))  # type: ignore[arg-type]
    return periods


def test_the_summary_statistics_are_over_the_measured_periods_and_carry_the_attrition() -> None:
    """Non-measured periods contribute nothing and are not zeros; `measured_count` against
    `len(as_ofs)` is what says how many were dropped, which is `ICSummary`'s own rule."""
    periods = _series((0.10, 0.20, 0.30, 0.40), measured=3)
    summary = _study().summarize(periods)

    assert summary.coverage == "measured"
    assert len(summary.as_ofs) == 4
    assert summary.measured_count == 3
    assert summary.as_ofs == tuple(sorted(summary.as_ofs))
    assert len(summary.group_mean_net_returns) == 3
    assert summary.group_mean_net_returns[0] == pytest.approx(-0.00111965, abs=1e-8)
    assert summary.group_mean_gross_returns[0] == 0.0
    assert summary.group_mean_net_returns[2] == pytest.approx(0.19865642, abs=1e-8)
    assert summary.group_mean_gross_returns[2] == pytest.approx(0.2, abs=1e-9)
    assert summary.group_mean_cost_drag[0] == pytest.approx(0.00111965, abs=1e-8)
    assert summary.mean_spread == pytest.approx(0.19977607, abs=1e-8)
    assert summary.stdev_spread == pytest.approx(0.09988803, abs=1e-8)
    assert summary.spread_ir == summary.mean_spread / summary.stdev_spread
    assert (summary.positive_count, summary.negative_count, summary.zero_count) == (3, 0, 0)
    assert summary.hit_rate == 1.0


def test_a_series_below_the_declared_floor_is_insufficient_periods_and_carries_no_statistic() -> (
    None
):
    """`min_periods` has no default for `min_as_ofs`' reason, and the code carries the counts so a
    reader can see how far short the series fell."""
    periods = _series((0.10, 0.20, 0.30), measured=1)
    summary = _study(_spec(min_periods=2)).summarize(periods)

    assert summary.coverage == "insufficient_periods"
    assert summary.measured_count == 1
    assert summary.group_mean_net_returns == ()
    assert summary.group_mean_gross_returns == ()
    assert summary.group_mean_cost_drag == ()
    assert (summary.mean_spread, summary.stdev_spread, summary.spread_ir) == (None, None, None)
    assert summary.hit_rate is None
    assert (summary.positive_count, summary.negative_count, summary.zero_count) == (1, 0, 0)
    assert MINIMUM_PORTFOLIO_PERIODS == 2


def test_the_spread_ir_is_none_when_there_is_no_dispersion_and_is_never_an_infinity() -> None:
    """`factor_ic`'s deliberate divergence from `EventStudy.t_statistic`, inherited.

    `math.inf` discards the sign of a constant negative spread and is a value this repository's
    stored-observation validators refuse one plane over, so a zero dispersion answers `None` --
    while the mean it would have divided is still reported and is still non-zero.
    """
    summary = _study().summarize(_series((0.10, 0.10, 0.10)))

    assert summary.coverage == "measured"
    assert summary.stdev_spread == 0.0
    assert summary.spread_ir is None
    assert summary.mean_spread is not None
    assert summary.mean_spread > 0.0
    assert not math.isinf(summary.mean_spread)


def test_a_zero_spread_is_in_the_hit_rates_denominator_and_in_neither_bucket() -> None:
    """A zero is not evidence for the factor, and `zero_count` is reported so a reader can see the
    denominator it eroded."""
    summary = _study().summarize(_series((0.10, 0.0, -0.10)))

    assert (summary.positive_count, summary.negative_count, summary.zero_count) == (1, 1, 1)
    assert summary.measured_count == 3
    assert summary.hit_rate == pytest.approx(1 / 3)


def test_the_summary_refuses_a_series_that_is_not_one_study() -> None:
    """A period from another cut, another factor or a repeated `as_of` is a malformed question and
    not a thin sample: a mean over two cuts is the average of two different quantities."""
    periods = _series((0.10, 0.20))
    with pytest.raises(FactorPortfolioError, match="needs at least one period"):
        _study().summarize([])
    with pytest.raises(FactorPortfolioError, match="appears more than once in this series"):
        _study().summarize([periods[0], periods[0]])

    wider_section, wider_bars = _simple_inputs(
        ranks=tuple(range(1, 9)), as_of=AS_OF + timedelta(days=5)
    )
    other = _study(_spec(group_count=4)).measure(wider_section, bars=wider_bars)  # type: ignore[arg-type]
    assert other.coverage == "measured"
    with pytest.raises(FactorPortfolioError, match="group_count=4 against 3"):
        _study().summarize([periods[0], other])

    foreign = replace(periods[1], factor_id="fct_other")
    with pytest.raises(FactorPortfolioError, match="factor_id='fct_other'"):
        _study().summarize([periods[0], foreign])


def test_a_summary_over_one_study_declares_the_factor_the_study_declares() -> None:
    """`_refuse_periods_that_are_not_one_study` checks the head against the spec as well as the
    periods against each other, so a series that is internally consistent and about another factor
    is still refused."""
    periods = _series((0.10, 0.20))
    renamed = [replace(period, factor_id="fct_other") for period in periods]

    with pytest.raises(FactorPortfolioError, match="was measured for factor 'fct_other'"):
        _study().summarize(renamed)


# --------------------------------------------------------------------------------------------
# The contracts: what is not constructible
# --------------------------------------------------------------------------------------------


def _round_trip(subject: str = "000001.SZ", *, group: int = 0) -> RoundTrip:
    policy = AShareExecutionPolicy(costs=CostSchedule())
    market = _bar(subject, date(2026, 6, 11), close=100.0, previous_close=100.0)
    return RoundTrip(
        subject=subject,
        group=group,
        quantity=1000,
        entry_fill=policy.execute(ExecutionRequest(side="buy", quantity=1000), market),
        exit_fill=policy.execute(ExecutionRequest(side="sell", quantity=1000), market),
        gross_return=0.05,
        unpublished_band_legs=2,
    )


def test_a_round_trip_refuses_a_rejected_leg_a_quantity_mismatch_and_a_bad_return() -> None:
    """Four ways a position could carry a number nothing accounts for, each refused by name."""
    policy = AShareExecutionPolicy(costs=CostSchedule())
    locked = _bar(code(1), date(2026, 6, 11), close=110.0, previous_close=100.0)
    rejected = policy.execute(ExecutionRequest(side="buy", quantity=1000), locked)
    base = _round_trip()

    with pytest.raises(FactorPortfolioError, match="is a rejected buy"):
        RoundTrip(**{**_fields(base), "entry_fill": rejected})
    with pytest.raises(FactorPortfolioError, match="exit_fill is a filled buy"):
        RoundTrip(**{**_fields(base), "exit_fill": base.entry_fill})
    with pytest.raises(FactorPortfolioError, match="filled 1000 shares against a position of 900"):
        RoundTrip(**{**_fields(base), "quantity": 900})
    with pytest.raises(FactorPortfolioError, match=r"gross return is -1\.0"):
        RoundTrip(**{**_fields(base), "gross_return": -1.0})
    with pytest.raises(FactorPortfolioError, match="gross return is nan"):
        RoundTrip(**{**_fields(base), "gross_return": math.nan})
    with pytest.raises(FactorPortfolioError, match="must name a subject"):
        RoundTrip(**{**_fields(base), "subject": "  "})
    with pytest.raises(FactorPortfolioError, match="is filed in group -1"):
        RoundTrip(**{**_fields(base), "group": -1})
    with pytest.raises(FactorPortfolioError, match="unpublished band leg"):
        RoundTrip(**{**_fields(base), "unpublished_band_legs": 3})


def _fields(item: RoundTrip) -> dict[str, object]:
    return {
        "subject": item.subject,
        "group": item.group,
        "quantity": item.quantity,
        "entry_fill": item.entry_fill,
        "exit_fill": item.exit_fill,
        "gross_return": item.gross_return,
        "unpublished_band_legs": item.unpublished_band_legs,
    }


def test_a_group_return_refuses_an_empty_a_misfiled_and_a_duplicated_position() -> None:
    """A group with no member has no return at all -- the denominator is zero -- and a position
    counted in the wrong quantile is a return attributed to the wrong signal."""
    with pytest.raises(FactorPortfolioError, match="holds nothing"):
        GroupReturn(group=0, holdings=())
    with pytest.raises(FactorPortfolioError, match="is filed under group 1 and reports another"):
        GroupReturn(group=1, holdings=(_round_trip(group=0),))
    with pytest.raises(FactorPortfolioError, match="is held twice in group 0"):
        GroupReturn(group=0, holdings=(_round_trip(), _round_trip()))


def test_a_position_rejection_refuses_the_held_outcome_and_an_unnamed_reason() -> None:
    """`held` is the one member of the vocabulary that is not a reason for anything, and a
    rejection with no reason is a count wearing a carrier's clothes."""
    with pytest.raises(FactorPortfolioError, match="recorded as a rejection under the 'held'"):
        PositionRejection(subject=code(1), outcome="held", reason="x")
    with pytest.raises(FactorPortfolioError, match="not a declared holding outcome"):
        PositionRejection(subject=code(1), outcome="vanished", reason="x")  # type: ignore[arg-type]
    with pytest.raises(FactorPortfolioError, match="must name a subject and carry a reason"):
        PositionRejection(subject=code(1), outcome="unbarred", reason=" ")


def _census(**overrides: object) -> PortfolioCensus:
    fields: dict[str, object] = {
        "offered_count": 3,
        "held_count": 1,
        "excluded_by_outcome": (
            ("unbarred", 1),
            ("below_board_minimum", 1),
            ("rejected_entry", 0),
            ("rejected_exit", 0),
        ),
        "unattempted_count": 0,
    }
    return PortfolioCensus(**{**fields, **overrides})  # type: ignore[arg-type]


def test_a_census_that_does_not_add_up_or_drops_a_zero_row_is_refused() -> None:
    """The four directions `ICCensus` established one plane along: a negative count, a missing
    code, a negative cell and a total that does not reconcile."""
    assert _census().offered_count == 3
    with pytest.raises(FactorPortfolioError, match="held_count cannot be negative"):
        _census(held_count=-1)
    with pytest.raises(FactorPortfolioError, match="a census missing a code cannot be told"):
        _census(excluded_by_outcome=(("unbarred", 1), ("below_board_minimum", 1)))
    with pytest.raises(FactorPortfolioError, match="excluded-outcome count cannot be negative"):
        _census(
            excluded_by_outcome=(
                ("unbarred", -1),
                ("below_board_minimum", 1),
                ("rejected_entry", 0),
                ("rejected_exit", 0),
            ),
            held_count=3,
        )
    with pytest.raises(FactorPortfolioError, match="census accounts for 2 securities and 3"):
        _census(held_count=0)


def _period(**overrides: object) -> PeriodPortfolio:
    section, bars = _simple_inputs()
    built = _study().measure(section, bars=bars)  # type: ignore[arg-type]
    return replace(built, **overrides)  # type: ignore[arg-type]


def test_a_measured_period_carries_every_group_and_a_refused_one_carries_none() -> None:
    """The relationship between the coverage code and the returns, in both directions: a refusal
    beside a partial set of returns is a spread over quantiles that were not all filled."""
    assert _period().coverage == "measured"
    with pytest.raises(FactorPortfolioError, match="carries groups \\[\\] against a cut of 3"):
        _period(coverage="measured", groups=())
    built = _period()
    with pytest.raises(FactorPortfolioError, match="carries groups \\[0, 1, 2\\]"):
        replace(built, coverage="unfillable_groups")


def test_a_period_refuses_an_unattempted_count_that_contradicts_its_coverage() -> None:
    """`PRE_EXECUTION_COVERAGE` made executable, in **both** directions.

    A `measured` period claiming its securities were never asked about, and an
    `insufficient_sample` one claiming they were -- the second is the direction that matters,
    because it is how a refused period would come to carry per-security verdicts about orders
    nobody submitted.
    """
    with pytest.raises(FactorPortfolioError, match="reports 6 unattempted of 6 offered"):
        replace(
            _period(),
            census=PortfolioCensus(
                offered_count=6,
                held_count=0,
                excluded_by_outcome=tuple(
                    (name, 0) for name in HOLDING_OUTCOME_ORDER if name != "held"
                ),
                unattempted_count=6,
            ),
        )
    thin_section, thin_bars = _simple_inputs(ranks=(1, 2, 3, 4, 5))
    refused = _study().measure(thin_section, bars=thin_bars)  # type: ignore[arg-type]

    assert refused.coverage == "insufficient_sample"
    with pytest.raises(FactorPortfolioError, match="reports 0 unattempted of 5 offered"):
        replace(
            refused,
            census=PortfolioCensus(
                offered_count=5,
                held_count=0,
                excluded_by_outcome=(
                    ("unbarred", 5),
                    ("below_board_minimum", 0),
                    ("rejected_entry", 0),
                    ("rejected_exit", 0),
                ),
                unattempted_count=0,
            ),
        )


def test_a_period_refuses_a_broken_funnel_a_stray_tier_and_a_backwards_window() -> None:
    """Five more directions the constructor closes, each of which would make a stored period
    describe a portfolio nobody held."""
    built = _period()
    with pytest.raises(FactorPortfolioError, match="is not a declared tier"):
        replace(built, tier="quarterly")
    with pytest.raises(FactorPortfolioError, match="is not a declared coverage code"):
        replace(built, coverage="probably_fine")
    with pytest.raises(FactorPortfolioError, match="cut into at least 2 groups"):
        replace(built, group_count=1)
    with pytest.raises(FactorPortfolioError, match="the two censuses are one funnel"):
        replace(
            built,
            source_census=ICCensus(
                tier="raw",
                subject_count=7,
                admitted_count=7,
                excluded_by_coverage=(
                    ("not_in_universe", 0),
                    ("insufficient_history", 0),
                    ("ambiguous_filing", 0),
                    ("input_missing", 0),
                    ("undefined_value", 0),
                ),
                unlabelled_count=0,
                unmatched_count=0,
            ),
        )
    with pytest.raises(FactorPortfolioError, match="cannot be a second source of truth"):
        replace(
            built,
            census=PortfolioCensus(
                offered_count=6,
                held_count=5,
                excluded_by_outcome=(
                    ("unbarred", 1),
                    ("below_board_minimum", 0),
                    ("rejected_entry", 0),
                    ("rejected_exit", 0),
                ),
                unattempted_count=0,
            ),
        )
    with pytest.raises(FactorPortfolioError, match="not a holding period"):
        replace(built, exit_day=built.entry_day)
    with pytest.raises(ValueError, match="datetime must be timezone-aware"):
        replace(built, as_of=datetime(2026, 6, 10, 8, 30))


def test_the_summary_contract_refuses_statistics_that_contradict_its_coverage() -> None:
    """`QuantilePortfolioSummary`'s validator, in the six directions a hand-built report could
    take: a statistic under a refusal, a missing one under `measured`, an `spread_ir` with no
    coverage to carry it, a group row of the wrong width, a mis-signed count, and a repeated
    `as_of`."""
    base: dict[str, object] = {
        "tier": "raw",
        "factor_id": "fct_probe",
        "direction": "higher_is_better",
        "group_count": 3,
        "horizon_sessions": 1,
        "coverage": "measured",
        "as_ofs": (AS_OF, AS_OF + timedelta(days=1)),
        "measured_count": 2,
        "group_mean_net_returns": (0.01, 0.02, 0.03),
        "group_mean_gross_returns": (0.02, 0.03, 0.04),
        "mean_spread": 0.02,
        "stdev_spread": 0.01,
        "spread_ir": 2.0,
        "positive_count": 2,
        "negative_count": 0,
        "zero_count": 0,
        "hit_rate": 1.0,
    }
    assert QuantilePortfolioSummary(**base).group_mean_cost_drag == pytest.approx(  # type: ignore[arg-type]
        (0.01, 0.01, 0.01)
    )
    for overrides, message in (
        ({"mean_spread": None}, "exactly the 'measured' code carries the statistics"),
        (
            {
                "coverage": "insufficient_periods",
                "mean_spread": None,
                "stdev_spread": None,
                "hit_rate": None,
                "group_mean_net_returns": (),
                "group_mean_gross_returns": (),
            },
            "cannot carry a spread_ir",
        ),
        ({"group_mean_net_returns": (0.01, 0.02)}, "carries 2 value\\(s\\) in group_mean_net"),
        ({"measured_count": 1}, "were signed and 1 were measured"),
        ({"as_ofs": (AS_OF,), "positive_count": 1}, "were measured and 1 as_ofs were offered"),
        ({"group_mean_gross_returns": (0.02, math.nan, 0.04)}, "carries a non-finite mean"),
        ({"as_ofs": (AS_OF, AS_OF)}, "must be distinct and ascending"),
        ({"stdev_spread": math.inf}, "is not a finite statistic"),
    ):
        with pytest.raises(ValidationError, match=message):
            QuantilePortfolioSummary(**{**base, **overrides})  # type: ignore[arg-type]


def test_the_spec_refuses_a_cut_a_floor_and_a_capital_outside_its_declared_range() -> None:
    """Every bound on the declaration, and the three properties a study reads off it."""
    spec = _spec()

    assert (spec.direction, spec.factor_id[:4]) == ("higher_is_better", "fct_")
    assert spec.minimum_cross_section == 6
    assert (MINIMUM_PORTFOLIO_GROUPS, MINIMUM_GROUP_SECURITIES) == (2, 1)
    for overrides in (
        {"group_count": MINIMUM_PORTFOLIO_GROUPS - 1},
        {"group_count": MAXIMUM_PORTFOLIO_GROUPS + 1},
        {"min_securities_per_group": 0},
        {"min_periods": MINIMUM_PORTFOLIO_PERIODS - 1},
    ):
        with pytest.raises(ValidationError):
            _spec(**overrides)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        QuantilePortfolioSpec(
            definition=_definition(),
            group_count=3,
            min_securities_per_group=2,
            position_capital=Decimal("0"),
            min_periods=2,
        )


def test_the_study_exposes_the_spec_and_the_policy_it_was_built_with() -> None:
    """The execution policy is a required argument and is not defaulted, unlike
    `PortfolioSimulator`'s: it carries the `CostSchedule`, every rate of which moves every number
    this study reports."""
    costs = CostSchedule(sell_stamp_duty_rate=Decimal("0.001"))
    study = _study(costs=costs)

    assert study.spec.group_count == 3
    assert study.execution.costs == costs
    assert study.execution.costs.sell_stamp_duty_rate == Decimal("0.001")


def test_a_doubled_stamp_duty_reaches_the_reported_net_return() -> None:
    """The `CostSchedule` is not decoration: the same period under two schedules produces two
    different net returns, and the difference is the extra duty divided by the outlay."""
    section, bars = _simple_inputs(returns=dict.fromkeys(range(1, 7), 0.0))
    default = _study().measure(section, bars=bars).groups[0]  # type: ignore[arg-type]
    doubled = (
        _study(costs=CostSchedule(sell_stamp_duty_rate=Decimal("0.001")))
        .measure(section, bars=bars)  # type: ignore[arg-type]
        .groups[0]
    )

    assert default.exit_cost == Decimal("162.00")
    assert doubled.exit_cost == Decimal("262.00")
    assert doubled.net_return < default.net_return
    assert default.net_return - doubled.net_return == pytest.approx(0.00049984, abs=1e-8)


# --------------------------------------------------------------------------------------------
# The limitation registry
# --------------------------------------------------------------------------------------------


def test_the_known_limitations_are_the_declared_set_and_each_is_bound_to_this_module() -> None:
    """Equality rather than membership: a membership assertion can see a code that was renamed and
    never one that was removed. `tests/unit/test_known_limitation_registries.py` requires every one
    of these to appear as a string literal in executable test code, which this set is."""
    assert {
        "a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule",
        "the_long_short_spread_is_not_a_shortable_portfolio",
        "a_group_return_is_conditioned_on_the_names_that_could_be_traded",
        "the_exit_leg_is_priced_on_the_entry_share_count",
        "overlapping_periods_are_not_compounded_into_a_cumulative_curve",
        "every_period_is_an_independent_round_trip_so_turnover_is_total",
        "an_unpublished_band_on_a_bar_is_judged_by_the_derived_one",
        "the_reported_returns_move_with_the_declared_position_capital",
    } == QUANTILE_PORTFOLIO_LIMITATION_CODES
    assert len(KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS) == 8
    assert all(item.detail.strip() for item in KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS)


def test_the_neutralised_snapshot_limitation_is_the_one_the_ic_module_already_carries() -> None:
    """One fact about the plane, not two about two modules: `V2-P4-026` is the fix and is a hard
    precondition of `V2-P4-013`, and renaming the code per module would make a reader think there
    were two of them to close."""
    from openalpha_cn.backtest.factor_ic import IC_LIMITATION_CODES

    shared = "a_neutralised_series_is_only_as_point_in_time_as_its_build_schedule"

    assert shared in IC_LIMITATION_CODES
    assert shared in QUANTILE_PORTFOLIO_LIMITATION_CODES
    assert "V2-P4-026" in next(
        item.detail for item in KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS if item.code == shared
    )
