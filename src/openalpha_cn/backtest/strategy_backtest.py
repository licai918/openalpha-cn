"""A multi-year, net-of-cost backtest of a composite score (`V2-P6-007`).

The quantile study (`V2-P3-006`) answers "what did one ordering pay over one label window" and
holds nothing across periods; the tradeability study (`V2-P3-007`) brackets what a rolling book
would have saved without pricing one. This module is the rolling book: a portfolio that carries
its holdings, its cash and its costs from one rebalance to the next over as many years as the
caller offers, and reports every period's gross, cost and net return beside the benchmarks the
research protocol names.

It stores nothing and reads no partition. `strategy_view.py` reads the panel and hands this
module plain inputs (`StrategyInputs`); `backtest-no-numeric-stack-or-panel-plane` forbids the
other arrangement.

## Time: signal at T's close, trade at T+1's open

A rebalance is decided on signal day `T` from the score cross section visible at `T`'s signal
instant (16:30 Asia/Shanghai, supplied per session as `StrategyInputs.signal_instants` by the
view, which takes it from `panel_ingest.session_publication_instant` rather than restating the
rule). A score row whose `available_time` or `revision_time` is later than that instant is
**refused**, not dropped -- a backtest that silently read a late row would be the look-ahead the
P2 red team exists to find, and one that silently dropped it would shrink a cross section nobody
asked to shrink.

The orders execute at the **open** of the next session. `AShareExecutionPolicy` fills at a
bar's close, so each order is judged against the next session's bar *collapsed to its open
print*: `open = high = low = close = open`, same previous close, same published band, same
suspension flag. Under that bar the policy's own rules are exactly the open-auction rules --
a buy is refused when the open is at or above the limit-up price, a sell when it is at or below
the limit-down price, a suspended security is refused both ways, the lot and STAR-floor rules
apply to buys, and T+1 is enforced from each holding's purchase session. Nothing here re-states
any of those rules.

## Periods, money and the one identity every period satisfies

Period `k` runs from the close of signal day `k` to the close of signal day `k + 1` (or the last
session of the range, whichever comes first), so the trades of rebalance `k` sit inside period
`k`. Every holding is marked at each session's close times its adjustment factor relative to
the one it was bought at; a holding with no bar on a session keeps its last mark.

    net_return   = (end_value - start_value) / start_value
    cost         = cost_yuan / start_value
    gross_return = net_return + cost

`cost_yuan` is everything the period's fills paid: commission (with its floor), transfer fee,
stamp duty on sells -- all three from `AShareExecutionPolicy` under the declared
`CostSchedule` -- plus slippage at `StrategySpec.slippage_rate` of each fill's notional. So
`gross_return` is "the same trades with their fees handed back", not a second simulation. Each
return is quantized to ten decimal places and the identity is checked on the quantized values.

Capital is `position_capital` per position: the book starts with `position_capital x
holding_count` in cash, and a new position's notional plus its fees is bounded by
`position_capital`, by the cash on hand after the same session's sales, and its notional by
`participation_cap` times the signal session's turnover. See
`KNOWN_STRATEGY_BACKTEST_LIMITATIONS` for what those choices leave out.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    CostSchedule,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
)
from openalpha_cn.backtest.factor_ic import average_ranks
from openalpha_cn.backtest.factor_portfolio import (
    BOARD_MINIMUM_QUANTITY,
    SHARE_LOT,
    position_quantity,
)

__all__ = [
    "EQUAL_WEIGHT_ALL_A",
    "KNOWN_STRATEGY_BACKTEST_LIMITATIONS",
    "PREDICTION_COMPONENT",
    "RETURN_QUANTUM",
    "STRATEGY_BACKTEST_LIMITATION_CODES",
    "STRATEGY_TIERS",
    "PeriodResult",
    "ScoreRow",
    "ScoreSource",
    "SessionQuote",
    "StrategyBacktest",
    "StrategyBacktestError",
    "StrategyBacktestLimitation",
    "StrategyFill",
    "StrategyInputs",
    "StrategyRejection",
    "StrategySpec",
    "component_key",
    "run_strategy_backtest",
]

StrategyTier = Literal["raw", "processed", "neutralized"]
STRATEGY_TIERS: Final[tuple[str, ...]] = ("raw", "processed", "neutralized")
"""The three stored tiers a component may read -- including `neutralized`, which
`model evaluate` refuses by name and this backtest accepts like the other two."""

EQUAL_WEIGHT_ALL_A: Final[str] = "equal_weight_all_a"
"""The benchmark name of the all-A equal-weight series the research protocol pairs with
000905.SH. Every other benchmark name is an index code read from `index_daily`."""

PREDICTION_COMPONENT: Final[str] = "prediction"
"""The one component key a `ScoreSource` built from `prediction_ids` has."""

RETURN_QUANTUM: Final[Decimal] = Decimal("0.0000000001")
"""Ten decimal places: every return, cost fraction and turnover is quantized to this."""

_CENT: Final[Decimal] = Decimal("0.01")
_ZERO_MONEY: Final[Decimal] = Decimal("0.00")
_UNCLASSIFIED: Final[str] = "unclassified"


class StrategyBacktestError(ValueError):
    """A backtest this module refuses to run, with the reason."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyBacktestLimitation:
    """One named boundary on what a strategy backtest's numbers can be trusted to mean."""

    code: str
    detail: str


KNOWN_STRATEGY_BACKTEST_LIMITATIONS: Final[tuple[StrategyBacktestLimitation, ...]] = (
    StrategyBacktestLimitation(
        code="fills_are_at_the_open_print_judged_as_a_one_price_bar",
        detail=(
            "Every order is judged by AShareExecutionPolicy against the execution session's bar "
            "collapsed to its open print (open = high = low = close). A name that opened at its "
            "limit-up price is therefore refused as a buy even if it traded below the limit "
            "later in the session, and one that opened at limit-down is refused as a sell. That "
            "is the right verdict for an order placed at the open and a pessimistic one for an "
            "order that could have waited; nothing here models an intraday fill."
        ),
    ),
    StrategyBacktestLimitation(
        code="slippage_is_a_flat_rate_and_not_a_market_impact_model",
        detail=(
            "Slippage is StrategySpec.slippage_rate of each fill's notional on both sides "
            "(10bp in the research protocol), charged as a cost beside commission and stamp "
            "duty rather than moved into the fill price, so commission is computed on the "
            "unslipped notional. It does not grow with order size or shrink with liquidity: "
            "this repository holds no dataset an impact coefficient could be estimated from."
        ),
    ),
    StrategyBacktestLimitation(
        code="the_participation_cap_reads_the_signal_sessions_turnover",
        detail=(
            "An order's notional is capped at participation_cap times the turnover of the "
            "SIGNAL session T, the last turnover knowable when the order is decided; the "
            "execution session's own turnover is not known at its open. A name with no bar on "
            "T has no turnover and every order in it is refused. A capped buy is filled smaller "
            "and counted in capped_orders; a cap below one board lot is a rejection. A capped "
            "sale is cut to a size the board accepts -- whole lots off STAR, at least 200 shares "
            "in single-share steps on STAR -- and leaves the remainder held until a later "
            "rebalance; a remainder under its board's minimum may only be sold whole, so a cap "
            "that does not reach all of it refuses the sale. capped_orders counts only orders "
            "that FILLED smaller than they would have without the cap, on both sides."
        ),
    ),
    StrategyBacktestLimitation(
        code="a_rejected_buy_leaves_its_slot_in_cash_until_the_next_rebalance",
        detail=(
            "The buy list is fixed at the signal: the best-ranked names not already held, in "
            "rank order, as many as the book has free slots after the session's sales. A buy "
            "the market refuses (limit-up, halt, no bar, below one lot) is not replaced by the "
            "next name; its capital stays in cash for the whole period. A failed sale shrinks "
            "the free slots the same way, so the book never holds more than holding_count names."
        ),
    ),
    StrategyBacktestLimitation(
        code="a_retained_position_is_not_resized_to_equal_weight",
        detail=(
            "A new position is sized to position_capital (100,000 yuan in the protocol), not to "
            "the book's value divided by holding_count, and a position that is kept is never "
            "topped up or trimmed. Weights therefore drift with prices between entries, gains "
            "accumulate as idle cash, and after losses a new position may be smaller than "
            "position_capital because cash bounds it. The book is equal-weight at entry only."
        ),
    ),
    StrategyBacktestLimitation(
        code="the_exit_leg_is_priced_on_the_entry_share_count",
        detail=(
            "A sale is placed for the share count that was bought, and its fees are charged on "
            "that count at the open. The proceeds are scaled by the adjustment factor's ratio "
            "since entry, so a bonus issue or split inside the holding is valued correctly while "
            "its sell-side fee is charged on the pre-split share count -- understated on a share "
            "increase. KNOWN_QUANTILE_PORTFOLIO_LIMITATIONS carries this same code for the same "
            "fact: Tushare's adj_factor cannot tell a cash dividend from a share change, so the "
            "true exit share count is not reconstructible from what this repository stores."
        ),
    ),
    StrategyBacktestLimitation(
        code="dividends_are_reinvested_through_the_adjustment_factor",
        detail=(
            "A holding is valued at close x adj_factor / entry adj_factor, which reinvests every "
            "cash dividend into the same security on its ex-date and pays no dividend tax. A "
            "real account receives the dividend as cash, pays the holding-period dividend tax "
            "and has to buy the shares back. The difference is small per event and it is "
            "systematically in the book's favour."
        ),
    ),
    StrategyBacktestLimitation(
        code="the_industry_cap_counts_names_and_is_applied_at_the_signal",
        detail=(
            "max_industry_weight caps the NUMBER of names per level-one industry at "
            "floor(max_industry_weight x holding_count), counted over the names kept at the "
            "signal plus the names bought; with equal entry weights that is a weight cap at "
            "entry only and drifts afterwards. A candidate with no industry answer on the signal "
            "day is counted in one shared 'unclassified' bucket under the same cap rather than "
            "admitted freely. A sale the market refuses keeps its name in the book and can leave "
            "an industry over the cap until it is sold."
        ),
    ),
    StrategyBacktestLimitation(
        code="a_holding_that_cannot_trade_is_marked_at_its_last_close",
        detail=(
            "A holding with no bar on a session -- a whole-day halt, most often -- keeps the "
            "last close it had, so the book's value is stale for exactly the securities whose "
            "value is least certain. A sale on a session the security is halted or unbarred is "
            "refused and the holding is carried to the next rebalance."
        ),
    ),
    StrategyBacktestLimitation(
        code="the_last_period_may_be_shorter_than_the_rebalance_interval",
        detail=(
            "Periods run from one signal day's close to the next, and the last one ends at the "
            "range's last session, so it may span fewer sessions than rebalance_every_sessions. "
            "PeriodResult.sessions says how many each period spans; a statistic that assumes "
            "equal-length periods should drop or down-weight a short last one."
        ),
    ),
    StrategyBacktestLimitation(
        code="an_intraday_halt_makes_the_whole_session_untradeable_at_the_open",
        detail=(
            "strategy_view builds SessionQuote.bar.suspended as 'no fill possible at the open': "
            "true for a halted session (suspended_at_the_close) and ALSO for any timed "
            "interruption, whether or not its window covers the open, because a stored timing "
            "string is not parsed for its left endpoint here. That refuses some orders that "
            "could have filled -- the fail-closed direction."
        ),
    ),
    StrategyBacktestLimitation(
        code="the_equal_weight_benchmark_is_every_priced_name_and_is_not_investable",
        detail=(
            "equal_weight_all_a is the mean of close / pre_close - 1 over every security with a "
            "bar on each session, compounded over the period. It includes names locked at a "
            "limit and names no order could have reached, pays no cost and rebalances daily, so "
            "it is a market measurement rather than a portfolio anyone could have held; an excess "
            "return over it is not an excess over an alternative investment."
        ),
    ),
    StrategyBacktestLimitation(
        code="a_passing_backtest_is_not_evidence_that_a_signal_is_real",
        detail=(
            "A net-of-cost return over a historical range is one draw from a search over "
            "configurations. Nothing here counts how many configurations were tried, controls "
            "the false-discovery rate, or holds a sample back; those belong to the research "
            "protocol around this module (pre-registration, a one-shot holdout, "
            "control_false_discovery_rate). A ranked list built from a configuration that did "
            "well here is still a candidate list."
        ),
    ),
)
"""What a strategy backtest does not answer, as a closed registry rather than as prose."""

STRATEGY_BACKTEST_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_STRATEGY_BACKTEST_LIMITATIONS
)


def component_key(factor: str, tier: str) -> str:
    """The key a score row names its component by: `<factor>@<tier>`."""
    return f"{factor}@{tier}"


class ScoreSource(BaseModel):
    """Where the scores come from: stored factor tiers combined, or registered predictions.

    Exactly one of `components` and `prediction_ids` is non-empty. A component is `(factor key,
    tier, weight)`; the view orients every stored value so that higher is better before it
    reaches `combine` (a `lower_is_better` factor is negated), so a weight's sign is a statement
    about the combination and never a repair of a factor's direction. A prediction source is
    one component, `PREDICTION_COMPONENT`, with weight one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    components: tuple[tuple[str, StrategyTier, Decimal], ...]
    combine: Literal["zscore_sum", "rank_sum"]
    prediction_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_one_series(self) -> Self:
        if bool(self.components) == bool(self.prediction_ids):
            raise ValueError(
                "a score source names exactly one of components and prediction_ids; both or "
                "neither is not a series anyone could rank on"
            )
        seen: set[tuple[str, str]] = set()
        for factor, tier, weight in self.components:
            if not factor.strip():
                raise ValueError("a component must name a factor")
            if not weight.is_finite() or weight == 0:
                raise ValueError(
                    f"component {factor}@{tier} has weight {weight}; a zero or non-finite weight "
                    "is a component that does not contribute"
                )
            if (factor, tier) in seen:
                raise ValueError(f"component {factor}@{tier} is declared twice")
            seen.add((factor, tier))
        if any(not identifier.strip() for identifier in self.prediction_ids):
            raise ValueError("a prediction id must not be blank")
        if len(set(self.prediction_ids)) != len(self.prediction_ids):
            raise ValueError("a prediction id is declared twice")
        return self

    @property
    def weights(self) -> Mapping[str, Decimal]:
        """Each component key's weight."""
        if self.prediction_ids:
            return {PREDICTION_COMPONENT: Decimal(1)}
        return {component_key(factor, tier): weight for factor, tier, weight in self.components}


class StrategySpec(BaseModel):
    """The portfolio rules and the measurement settings of one backtest.

    `slippage_rate` is a field the `V2-P6-007` brief did not list and the research protocol
    requires (10bp per side): `CostSchedule` has no slippage term and is reused unchanged.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rebalance_every_sessions: int = Field(ge=1)
    holding_count: int = Field(ge=1)
    buffer_rank: int | None
    max_industry_weight: Decimal | None
    position_capital: Decimal = Field(gt=0)
    participation_cap: Decimal = Field(gt=0, le=1)
    costs: CostSchedule
    slippage_rate: Decimal = Field(ge=0, lt=1)
    benchmarks: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_portfolio(self) -> Self:
        negative = sorted(
            name for name, value in self.costs.model_dump().items() if Decimal(value) < 0
        )
        if negative:
            raise ValueError(
                f"costs.{', costs.'.join(negative)} is negative; a rebate no broker pays would "
                "make every net return look better than an account could have earned"
            )
        if self.buffer_rank is not None and self.buffer_rank < self.holding_count:
            raise ValueError(
                f"buffer_rank {self.buffer_rank} is inside holding_count {self.holding_count}; "
                "a band narrower than the book would sell names that are still in the top N"
            )
        if self.max_industry_weight is not None:
            if not 0 < self.max_industry_weight <= 1:
                raise ValueError("max_industry_weight must be in (0, 1]")
            if self.industry_slots < 1:
                raise ValueError(
                    f"max_industry_weight {self.max_industry_weight} of {self.holding_count} "
                    "names allows no name in any industry"
                )
        if len(set(self.benchmarks)) != len(self.benchmarks) or any(
            not name.strip() for name in self.benchmarks
        ):
            raise ValueError("benchmarks must be distinct, non-blank names")
        return self

    @property
    def initial_capital(self) -> Decimal:
        """`position_capital x holding_count`, the cash the book starts with."""
        return (self.position_capital * self.holding_count).quantize(_CENT)

    @property
    def industry_slots(self) -> int:
        """How many names one industry may hold, or `holding_count` when uncapped."""
        if self.max_industry_weight is None:
            return self.holding_count
        return int(
            (self.max_industry_weight * self.holding_count).to_integral_value(rounding=ROUND_FLOOR)
        )


class StrategyFill(BaseModel):
    """One order that filled: when, what, how many, at what price, and what it paid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    subject: str
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0)
    price: Decimal
    notional: Decimal
    commission: Decimal
    transfer_fee: Decimal
    stamp_duty: Decimal
    slippage: Decimal
    fees: Decimal


class StrategyRejection(BaseModel):
    """One order the market or the sizing refused, and why."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date
    subject: str
    side: Literal["buy", "sell"]
    reason: str


class PeriodResult(BaseModel):
    """One rebalance period, from one signal close to the next.

    The brief's eight fields, plus the ledger that makes each of them re-derivable:
    `sessions`, `start_value`/`end_value`, `cost_yuan`, `capped_orders`, `holdings` and the
    fills and rejections themselves.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date
    gross_return: Decimal
    cost: Decimal
    net_return: Decimal
    benchmark_returns: Mapping[str, Decimal]
    turnover: Decimal
    rejected_orders: int = Field(ge=0)
    sessions: int = Field(ge=1)
    start_value: Decimal
    end_value: Decimal
    cost_yuan: Decimal
    capped_orders: int = Field(ge=0)
    holdings: tuple[str, ...]
    fills: tuple[StrategyFill, ...]
    rejections: tuple[StrategyRejection, ...]

    @model_validator(mode="after")
    def validate_ledger(self) -> Self:
        if self.gross_return != self.net_return + self.cost:
            raise ValueError("gross_return must equal net_return + cost")
        if self.rejected_orders != len(self.rejections):
            raise ValueError("rejected_orders must count the rejections")
        if self.end <= self.start:
            raise ValueError("a period ends after it starts")
        return self


class StrategyBacktest(BaseModel):
    """The whole answer: the rules, the score source, every period, and what it cannot say."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec: StrategySpec
    source: ScoreSource
    periods: tuple[PeriodResult, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ScoreRow:
    """One security's value of one component on one signal day, with its two clocks.

    `value` is already oriented higher-is-better (see `ScoreSource`). `available_time` and
    `revision_time` are when the row became knowable and when it was last restated; both must
    be at or before the signal day's instant or the backtest refuses to run.
    """

    component: str
    subject: str
    signal_day: date
    value: float
    available_time: datetime
    revision_time: datetime

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise StrategyBacktestError(
                f"{self.subject} carries a non-finite {self.component} score on "
                f"{self.signal_day.isoformat()}"
            )
        for instant in (self.available_time, self.revision_time):
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise StrategyBacktestError("a score row's clocks must be timezone-aware")


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionQuote:
    """One security's stored session, as the execution policy and the book need it.

    `bar` is the stored bar with the exchange's published band; `bar.suspended` means no fill
    was possible at the OPEN. `turnover_yuan` is the session's traded value in yuan and
    `adj_factor` its cumulative adjustment factor.
    """

    bar: MarketBar
    turnover_yuan: Decimal
    adj_factor: Decimal

    def __post_init__(self) -> None:
        if self.turnover_yuan < 0:
            raise StrategyBacktestError(f"{self.bar.subject} reports negative turnover")
        if self.adj_factor <= 0:
            raise StrategyBacktestError(f"{self.bar.subject} reports a non-positive adj_factor")


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyInputs:
    """Everything a backtest reads, already out of the panel.

    `sessions` are the open sessions of the range, ascending; the first is the first signal
    day. `signal_instants` is each session's signal instant. `quotes` is per session per
    security; `benchmark_returns` is per benchmark name per session; `industries` is per signal
    day per security and is read only when the spec caps industries.
    """

    source: ScoreSource
    sessions: tuple[date, ...]
    signal_instants: Mapping[date, datetime]
    scores: tuple[ScoreRow, ...]
    quotes: Mapping[date, Mapping[str, SessionQuote]]
    benchmark_returns: Mapping[str, Mapping[date, Decimal]]
    industries: Mapping[date, Mapping[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.sessions) < 2:
            raise StrategyBacktestError(
                "a backtest needs at least two sessions: a signal and the session it trades on"
            )
        if any(
            later <= earlier
            for earlier, later in zip(self.sessions, self.sessions[1:], strict=False)
        ):
            raise StrategyBacktestError("sessions must be strictly ascending")


def run_strategy_backtest(inputs: StrategyInputs, spec: StrategySpec) -> StrategyBacktest:
    """Run the book over every period `inputs` spans and return the answer.

    Signal days are `sessions[0]`, `sessions[R]`, `sessions[2R]`, ... for as long as a session
    follows to trade on, where `R = spec.rebalance_every_sessions`. Refuses, with
    `StrategyBacktestError`: a signal day with no signal instant, a score row that was not
    visible at its signal day's instant, a row naming a component the source does not declare,
    a signal day on which some component has no cross section or a degenerate one, and a
    benchmark with no return for some session a period spans.
    """
    missing = [name for name in spec.benchmarks if name not in inputs.benchmark_returns]
    if missing:
        raise StrategyBacktestError(f"no return series was supplied for benchmark(s) {missing}")
    sessions = inputs.sessions
    signal_indices = tuple(range(0, len(sessions) - 1, spec.rebalance_every_sessions))
    signal_days = frozenset(sessions[index] for index in signal_indices)
    scores = _combined_scores(inputs, signal_days)
    book = _Book(spec=spec, cash=spec.initial_capital)
    periods: list[PeriodResult] = []
    for index in signal_indices:
        end_index = min(index + spec.rebalance_every_sessions, len(sessions) - 1)
        periods.append(
            _run_period(
                inputs,
                spec,
                book,
                signal_index=index,
                end_index=end_index,
                ranked=scores[sessions[index]],
            )
        )
    return StrategyBacktest(
        spec=spec,
        source=inputs.source,
        periods=tuple(periods),
        limitations=tuple(item.code for item in KNOWN_STRATEGY_BACKTEST_LIMITATIONS),
    )


# --- scores -------------------------------------------------------------------------------------


def _combined_scores(
    inputs: StrategyInputs, signal_days: frozenset[date]
) -> dict[date, tuple[str, ...]]:
    """Each signal day's securities, best first, after the look-ahead guard and the combiner.

    Rows dated on a session that is not a signal day are never read: no rebalance happens there,
    so nothing could trade on them. Ties in the combined score are broken by security code,
    ascending, so one input has one answer.
    """
    weights = inputs.source.weights
    by_day: dict[date, dict[str, dict[str, float]]] = {day: {} for day in signal_days}
    for row in inputs.scores:
        if row.signal_day not in signal_days:
            continue
        if row.component not in weights:
            raise StrategyBacktestError(
                f"a score row names component {row.component!r} and the source declares no "
                f"component by that key; it declares {sorted(weights)}"
            )
        instant = inputs.signal_instants.get(row.signal_day)
        if instant is None:
            raise StrategyBacktestError(
                f"signal day {row.signal_day.isoformat()} has no signal instant"
            )
        if row.available_time > instant or row.revision_time > instant:
            raise StrategyBacktestError(
                f"{row.subject}'s {row.component} score for {row.signal_day.isoformat()} is not "
                f"visible at that day's signal instant {instant.isoformat()}: it became "
                f"available at {row.available_time.isoformat()} and was revised at "
                f"{row.revision_time.isoformat()}. Trading on it would be look-ahead; build the "
                "cross section at or before the signal instant"
            )
        cross_section = by_day[row.signal_day].setdefault(row.component, {})
        if row.subject in cross_section:
            raise StrategyBacktestError(
                f"{row.subject} has two {row.component} scores on {row.signal_day.isoformat()}"
            )
        cross_section[row.subject] = row.value
    return {
        day: _rank(day, by_day[day], weights, inputs.source.combine) for day in sorted(signal_days)
    }


def _rank(
    day: date,
    components: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, Decimal],
    combine: Literal["zscore_sum", "rank_sum"],
) -> tuple[str, ...]:
    absent = sorted(key for key in weights if not components.get(key))
    if absent:
        raise StrategyBacktestError(
            f"there is no {', '.join(absent)} cross section for signal day {day.isoformat()}; a "
            "rebalance with nothing to rank is refused rather than skipped"
        )
    subjects = sorted(set.intersection(*(set(components[key]) for key in weights)))
    if not subjects:
        raise StrategyBacktestError(
            f"no security carries every component on signal day {day.isoformat()}"
        )
    total = dict.fromkeys(subjects, 0.0)
    for key, weight in weights.items():
        values = [components[key][subject] for subject in subjects]
        if len(values) > 1 and min(values) == max(values):
            raise StrategyBacktestError(
                f"the {key} cross section on {day.isoformat()} is degenerate: all "
                f"{len(values)} values are equal, so it orders nothing"
            )
        standardized = _zscores(values) if combine == "zscore_sum" else _rank_fractions(values)
        for subject, value in zip(subjects, standardized, strict=True):
            total[subject] += float(weight) * value
    return tuple(sorted(subjects, key=lambda subject: (-total[subject], subject)))


def _zscores(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) == 1:
        return (0.0,)
    mean = statistics.fmean(values)
    deviation = statistics.pstdev(values)
    return tuple((value - mean) / deviation for value in values)


def _rank_fractions(values: Sequence[float]) -> tuple[float, ...]:
    size = len(values)
    return tuple(rank / size for rank in average_ranks(values))


# --- the book -----------------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class _Holding:
    shares: int
    opened: date
    entry_adj: Decimal
    mark: Decimal
    """The last close times its adjustment factor."""

    def value(self) -> Decimal:
        return (self.shares * self.mark / self.entry_adj).quantize(_CENT, rounding=ROUND_HALF_UP)


@dataclass(slots=True, kw_only=True)
class _Book:
    spec: StrategySpec
    cash: Decimal
    holdings: dict[str, _Holding] = field(default_factory=dict)

    def value(self) -> Decimal:
        return self.cash + sum((holding.value() for holding in self.holdings.values()), _ZERO_MONEY)

    def mark(self, day_quotes: Mapping[str, SessionQuote]) -> None:
        for subject, holding in self.holdings.items():
            quote = day_quotes.get(subject)
            if quote is not None:
                holding.mark = quote.bar.close * quote.adj_factor


@dataclass(slots=True)
class _Ledger:
    fills: list[StrategyFill] = field(default_factory=list)
    rejections: list[StrategyRejection] = field(default_factory=list)
    capped: int = 0
    bought: Decimal = _ZERO_MONEY
    sold: Decimal = _ZERO_MONEY
    cost: Decimal = _ZERO_MONEY

    def reject(self, day: date, subject: str, side: Literal["buy", "sell"], reason: str) -> None:
        self.rejections.append(
            StrategyRejection(day=day, subject=subject, side=side, reason=reason)
        )


def _run_period(
    inputs: StrategyInputs,
    spec: StrategySpec,
    book: _Book,
    *,
    signal_index: int,
    end_index: int,
    ranked: tuple[str, ...],
) -> PeriodResult:
    sessions = inputs.sessions
    signal_day = sessions[signal_index]
    trade_day = sessions[signal_index + 1]
    book.mark(inputs.quotes.get(signal_day, {}))
    start_value = book.value()
    if start_value <= 0:
        raise StrategyBacktestError(f"the book is worth {start_value} on {signal_day.isoformat()}")
    keep, buy = _decide(inputs, spec, book, signal_day=signal_day, ranked=ranked)
    ledger = _Ledger()
    policy = AShareExecutionPolicy(spec.costs)
    signal_quotes = inputs.quotes.get(signal_day, {})
    trade_quotes = inputs.quotes.get(trade_day, {})
    for subject in sorted(set(book.holdings) - keep):
        _sell(book, ledger, policy, spec, subject, trade_day, signal_quotes, trade_quotes)
    for subject in buy[: max(spec.holding_count - len(book.holdings), 0)]:
        _buy(book, ledger, policy, spec, subject, trade_day, signal_quotes, trade_quotes)
    for index in range(signal_index + 1, end_index + 1):
        book.mark(inputs.quotes.get(sessions[index], {}))
    end_value = book.value()
    net = _quantized((end_value - start_value) / start_value)
    cost = _quantized(ledger.cost / start_value)
    return PeriodResult(
        start=signal_day,
        end=sessions[end_index],
        gross_return=net + cost,
        cost=cost,
        net_return=net,
        benchmark_returns={
            name: _compound(inputs, name, sessions[signal_index + 1 : end_index + 1])
            for name in spec.benchmarks
        },
        turnover=_quantized((ledger.bought + ledger.sold) / (2 * start_value)),
        rejected_orders=len(ledger.rejections),
        sessions=end_index - signal_index,
        start_value=start_value,
        end_value=end_value,
        cost_yuan=ledger.cost,
        capped_orders=ledger.capped,
        holdings=tuple(sorted(book.holdings)),
        fills=tuple(ledger.fills),
        rejections=tuple(ledger.rejections),
    )


def _decide(
    inputs: StrategyInputs,
    spec: StrategySpec,
    book: _Book,
    *,
    signal_day: date,
    ranked: tuple[str, ...],
) -> tuple[set[str], list[str]]:
    """Which holdings stay, and the buy list in rank order, both decided at the signal."""
    band = spec.buffer_rank if spec.buffer_rank is not None else spec.holding_count
    rank = {subject: position for position, subject in enumerate(ranked, start=1)}
    keep = {subject for subject in book.holdings if rank.get(subject, band + 1) <= band}
    industries = inputs.industries.get(signal_day, {})
    counts: dict[str, int] = {}
    for subject in keep:
        industry = industries.get(subject, _UNCLASSIFIED)
        counts[industry] = counts.get(industry, 0) + 1
    wanted = spec.holding_count - len(keep)
    buy: list[str] = []
    for subject in ranked:
        if len(buy) >= wanted:
            break
        if subject in book.holdings:
            continue
        if spec.max_industry_weight is not None:
            industry = industries.get(subject, _UNCLASSIFIED)
            if counts.get(industry, 0) >= spec.industry_slots:
                continue
            counts[industry] = counts.get(industry, 0) + 1
        buy.append(subject)
    return keep, buy


def _at_the_open(bar: MarketBar) -> MarketBar:
    """The session's bar collapsed to its open print, for an order placed at the open."""
    return bar.model_copy(update={"high": bar.open, "low": bar.open, "close": bar.open})


def _slippage(spec: StrategySpec, notional: Decimal) -> Decimal:
    return (notional * spec.slippage_rate).quantize(_CENT, rounding=ROUND_HALF_UP)


def _participation_limit(
    spec: StrategySpec, subject: str, signal_quotes: Mapping[str, SessionQuote]
) -> Decimal | None:
    quote = signal_quotes.get(subject)
    if quote is None:
        return None
    return spec.participation_cap * quote.turnover_yuan


def _record_fill(
    ledger: _Ledger,
    spec: StrategySpec,
    result: ExecutionResult,
    *,
    subject: str,
    day: date,
    price: Decimal,
) -> Decimal:
    slippage = _slippage(spec, result.notional)
    fees = result.total_cost + slippage
    ledger.fills.append(
        StrategyFill(
            day=day,
            subject=subject,
            side=result.side,
            quantity=result.quantity,
            price=price,
            notional=result.notional,
            commission=result.commission,
            transfer_fee=result.transfer_fee,
            stamp_duty=result.stamp_duty,
            slippage=slippage,
            fees=fees,
        )
    )
    ledger.cost += fees
    return fees


def _legal_sale(held: int, *, limit: Decimal, bar: MarketBar) -> int:
    """The largest sale of `held` shares the cap allows and the board accepts, or `0`.

    The whole position is always a legal sale, so when the cap reaches it the answer is `held`.
    Otherwise the sale is a part of the position, and a part is legal only in the shape a buy of
    that board is: whole 100-share lots off STAR, and on STAR at least 200 shares in single-share
    steps -- `position_quantity` sized at the cap, the same helper and the same
    `BOARD_MINIMUM_QUANTITY` the buy side uses. A position smaller than its board's minimum is an
    odd remainder that may only be sold whole, so a cap that does not reach all of it allows
    nothing.
    """
    if held * bar.open <= limit:
        return held
    if held < BOARD_MINIMUM_QUANTITY[bar.board] or limit <= 0:
        return 0
    return min(position_quantity(capital=limit, market=bar), held)


def _sell(
    book: _Book,
    ledger: _Ledger,
    policy: AShareExecutionPolicy,
    spec: StrategySpec,
    subject: str,
    day: date,
    signal_quotes: Mapping[str, SessionQuote],
    trade_quotes: Mapping[str, SessionQuote],
) -> None:
    holding = book.holdings[subject]
    quote = trade_quotes.get(subject)
    if quote is None:
        ledger.reject(day, subject, "sell", "no bar on the execution session")
        return
    limit = _participation_limit(spec, subject, signal_quotes)
    if limit is None:
        ledger.reject(day, subject, "sell", "no signal-session turnover for the participation cap")
        return
    bar = _at_the_open(quote.bar)
    quantity = _legal_sale(holding.shares, limit=limit, bar=bar)
    if quantity <= 0:
        ledger.reject(
            day,
            subject,
            "sell",
            "the participation cap does not reach a sale the board accepts from this position",
        )
        return
    result = policy.execute(
        ExecutionRequest(side="sell", quantity=quantity, position_open_date=holding.opened), bar
    )
    if result.status != "filled":
        ledger.reject(day, subject, "sell", result.reason or "rejected")
        return
    if quantity < holding.shares:
        ledger.capped += 1
    fees = _record_fill(ledger, spec, result, subject=subject, day=day, price=bar.open)
    proceeds = (quantity * bar.open * quote.adj_factor / holding.entry_adj).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    book.cash += proceeds - fees
    ledger.sold += result.notional
    holding.shares -= quantity
    if holding.shares == 0:
        del book.holdings[subject]


def _sized_buy(
    policy: AShareExecutionPolicy,
    spec: StrategySpec,
    bar: MarketBar,
    *,
    capital: Decimal,
    budget: Decimal,
) -> tuple[int, ExecutionResult] | str | None:
    """The largest board-legal buy whose notional fits `capital` and whose outlay fits `budget`.

    Returns the quantity and its fill, the policy's refusal reason when the market refuses the
    order at any size, or `None` when no legal size fits.
    """
    quantity = position_quantity(capital=capital, market=bar) if capital > 0 else 0
    step = 1 if bar.board == "star" else SHARE_LOT
    while quantity >= BOARD_MINIMUM_QUANTITY[bar.board]:
        result = policy.execute(ExecutionRequest(side="buy", quantity=quantity), bar)
        if result.status != "filled":
            return result.reason or "rejected"
        if result.notional + result.total_cost + _slippage(spec, result.notional) <= budget:
            return quantity, result
        quantity -= step
    return None


def _buy(
    book: _Book,
    ledger: _Ledger,
    policy: AShareExecutionPolicy,
    spec: StrategySpec,
    subject: str,
    day: date,
    signal_quotes: Mapping[str, SessionQuote],
    trade_quotes: Mapping[str, SessionQuote],
) -> None:
    quote = trade_quotes.get(subject)
    if quote is None:
        ledger.reject(day, subject, "buy", "no bar on the execution session")
        return
    limit = _participation_limit(spec, subject, signal_quotes)
    if limit is None:
        ledger.reject(day, subject, "buy", "no signal-session turnover for the participation cap")
        return
    bar = _at_the_open(quote.bar)
    budget = min(spec.position_capital, book.cash)
    sized = _sized_buy(policy, spec, bar, capital=min(budget, limit), budget=budget)
    if isinstance(sized, str):
        ledger.reject(day, subject, "buy", sized)
        return
    if sized is None:
        reason = (
            "the participation cap is below one board lot"
            if limit < budget
            else "the budget is below one board lot after costs"
        )
        ledger.reject(day, subject, "buy", reason)
        return
    quantity, result = sized
    if limit < budget:
        uncapped = _sized_buy(policy, spec, bar, capital=budget, budget=budget)
        if isinstance(uncapped, tuple) and quantity < uncapped[0]:
            ledger.capped += 1
    fees = _record_fill(ledger, spec, result, subject=subject, day=day, price=bar.open)
    book.cash -= result.notional + fees
    ledger.bought += result.notional
    book.holdings[subject] = _Holding(
        shares=quantity,
        opened=day,
        entry_adj=quote.adj_factor,
        mark=bar.open * quote.adj_factor,
    )


def _compound(inputs: StrategyInputs, name: str, days: Sequence[date]) -> Decimal:
    series = inputs.benchmark_returns[name]
    growth = Decimal(1)
    for day in days:
        daily = series.get(day)
        if daily is None:
            raise StrategyBacktestError(
                f"benchmark {name} has no return for {day.isoformat()}, a session a period spans"
            )
        growth *= Decimal(1) + daily
    return _quantized(growth - 1)


def _quantized(value: Decimal) -> Decimal:
    """Ten decimal places, with a negative zero read as zero so equal answers print equally."""
    quantized = value.quantize(RETURN_QUANTUM)
    return quantized.copy_abs() if quantized.is_zero() else quantized
