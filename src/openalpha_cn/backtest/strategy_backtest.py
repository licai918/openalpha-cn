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

## Four kinds of score source, and the two whose scores are reconstructed (`V2-P6-014`)

A `ScoreSource` is exactly one of: stored factor tiers under **static** weights; registered
**predictions**; stored factor tiers weighted by each factor's **trailing IC**; or a
**walk-forward** model refitted on a schedule. The last two are computed now, over history, and
each has one point-in-time rule this module enforces rather than trusts:

- **Trailing IC.** At signal day `d`, factor `i`'s weight is the mean of its ICs over the
  prediction days `t` among the `ic_window_sessions` calendar sessions ending at `d` whose
  `ICObservation.known_at` -- the later of the build's instant and the 16:30 of the session the
  label's window exits on -- is at or before `d`'s signal instant (`_ICIndex.weights`). Fewer
  than `min_ic_observations` known ICs is an abstention (weight 0); a day on which every factor
  weighs 0 has no scores and the book **holds**.
- **Walk-forward model.** A fit on refit session `r` trains on the prediction days among the
  `train_sessions` sessions ending at `r` whose label is known **strictly before** the signal
  instant of the session `embargo_sessions` before `r` (`walk_forward_fits`). The fit in use on
  `d` is the newest one refitted on or before `d`, and the book **refuses** a fit whose own
  artifact's training cutoff is not known strictly before the session `embargo_sessions` before
  `d` (`_refuse_a_fit_not_closed_by_the_embargo`). No fit yet is no scores, and the book holds.

Both are walk-forward reconstructions made now, not predictions registered before their
outcomes; every answer from either kind says so (`limitation_codes_for`).
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Final, Literal, Protocol, Self, cast, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from openalpha_cn.backtest.execution import (
    AShareExecutionPolicy,
    CostSchedule,
    ExecutionRequest,
    ExecutionResult,
    MarketBar,
)
from openalpha_cn.backtest.factor_ic import MINIMUM_IC_SECURITIES, average_ranks
from openalpha_cn.backtest.factor_portfolio import (
    BOARD_MINIMUM_QUANTITY,
    SHARE_LOT,
    position_quantity,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    AlphaModelArtifact,
    AlphaModelError,
    FittedAlphaModel,
    TrainingExample,
    TrainingSet,
)

__all__ = [
    "DYNAMIC_SOURCE_KINDS",
    "EQUAL_WEIGHT_ALL_A",
    "KNOWN_STRATEGY_BACKTEST_LIMITATIONS",
    "MODEL_COMPONENT",
    "PREDICTION_COMPONENT",
    "RETURN_QUANTUM",
    "SCORE_SOURCE_KINDS",
    "STRATEGY_BACKTEST_LIMITATION_CODES",
    "STRATEGY_TIERS",
    "ICObservation",
    "ModelFit",
    "PeriodResult",
    "ScoreFeed",
    "ScoreRow",
    "ScoreSource",
    "ScoreSourceKind",
    "SessionQuote",
    "SignalDayInputs",
    "SignalDayScores",
    "StrategyBacktest",
    "StrategyBacktestError",
    "StrategyBacktestLimitation",
    "StrategyFill",
    "StrategyInputs",
    "StrategyRejection",
    "StrategySpec",
    "TrailingICWeight",
    "TrailingICWeights",
    "UnknowableCrossing",
    "WalkForwardFit",
    "WalkForwardModel",
    "component_key",
    "limitation_codes_for",
    "rebalance_indices",
    "run_strategy_backtest",
    "score_signal_day",
    "target_holdings",
    "trailing_ic_weights",
    "usable_fit",
    "walk_forward_fits",
]

ScoreSourceKind = Literal["static", "prediction", "trailing_ic", "walk_forward"]
SCORE_SOURCE_KINDS: Final[tuple[ScoreSourceKind, ...]] = get_args(ScoreSourceKind)
"""The four mutually exclusive kinds a `ScoreSource` can be."""

DYNAMIC_SOURCE_KINDS: Final[frozenset[str]] = frozenset({"trailing_ic", "walk_forward"})
"""The two kinds whose scores this repository reconstructs over history rather than reads."""

StrategyTier = Literal["raw", "processed", "neutralized"]
STRATEGY_TIERS: Final[tuple[str, ...]] = ("raw", "processed", "neutralized")
"""The three stored tiers a component may read -- including `neutralized`, which
`model evaluate` refuses by name and this backtest accepts like the other two."""

EQUAL_WEIGHT_ALL_A: Final[str] = "equal_weight_all_a"
"""The benchmark name of the all-A equal-weight series the research protocol pairs with
000905.SH. Every other benchmark name is an index code read from `index_daily`."""

PREDICTION_COMPONENT: Final[str] = "prediction"
"""The one component key a `ScoreSource` built from `prediction_ids` has."""

MODEL_COMPONENT: Final[str] = "walk_forward_model"
"""The one component key a walk-forward `ScoreSource` has: the fitted model's score."""

RETURN_QUANTUM: Final[Decimal] = Decimal("0.0000000001")
"""Ten decimal places: every return, cost fraction and turnover is quantized to this."""

_CENT: Final[Decimal] = Decimal("0.01")
_ZERO_MONEY: Final[Decimal] = Decimal("0.00")
_UNCLASSIFIED: Final[str] = "unclassified"


class StrategyBacktestError(ValueError):
    """A backtest this module refuses to run, with the reason."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyBacktestLimitation:
    """One named boundary on what a strategy backtest's numbers can be trusted to mean.

    `applies_to` is the source kinds the entry speaks for, so an answer carries the entries
    about its own kind and not a reconstruction warning on a run that reconstructed nothing.
    """

    code: str
    detail: str
    applies_to: frozenset[str] = frozenset(SCORE_SOURCE_KINDS)

    def __post_init__(self) -> None:
        if not self.applies_to or not self.applies_to <= set(SCORE_SOURCE_KINDS):
            raise ValueError(
                f"{self.code} applies to {sorted(self.applies_to)}; the kinds are "
                f"{list(SCORE_SOURCE_KINDS)}"
            )


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
        code="a_session_whose_return_is_unknowable_is_valued_by_the_adjustment_factor",
        detail=(
            "V2-P6-020. Where daily's pre_close and adj_factor disagree about a session, "
            "upstream_defects records which one the day's own stk_limit band corroborates, and "
            "a held position follows that path: through a corroborated pre_close the session "
            "return is close / pre_close and every later mark and sale is rescaled by that "
            "session's own implied / pre_close ratio. Where neither is corroborated the return is "
            "unknowable, and the book values the position exactly as it values every other "
            "session and every resumption after a halt -- close x adj_factor / entry adj_factor. "
            "The research store holds seven such sessions, every one a resumption after a halt "
            "that spanned a corporate action, and the evidence is why the factor path is the one "
            "taken: on five of them pre_close is still the pre-halt close while the factor rose "
            "1.5 to 4 times, so the published path books a spurious -43% to -72%. Published and "
            "factor-path returns, in percent: 000010.SZ 2013-07-19 -70.7 / +17.3, 000509.SZ "
            "2014-01-14 -72.4 / -3.6, 000670.SZ 2014-07-15 -52.5 / +42.6, 600610.SH 2014-11-25 "
            "+12.7 / +57.8, 600688.SH 2013-08-20 -43.2 / -14.9, 600871.SH 2013-08-20 -42.7 / "
            "-14.1, 600733.SH 2018-09-27 -36.9 / -27.9. Neither is corroborated: every one of "
            "those closes lies outside the band centred on its published pre_close. The book does "
            "not exclude the security before the session (no signal could have known of it) and "
            "does not refuse the period -- a refused period leaves the book's value unknown until "
            "the position is sold, which is every later period rather than one. What the choice "
            "is worth is on the answer: every crossing is on the run's unknowable_crossings with "
            "the position's booked value and the published path's difference from it, in yuan "
            "and as a share of the period's starting book, and on its period's "
            "unknowable_sessions; a run with none is a run this entry did not touch."
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
    StrategyBacktestLimitation(
        code="a_dynamic_score_is_a_walk_forward_reconstruction_made_now",
        detail=(
            "A trailing-IC weight and a walk-forward model's score are computed now, over "
            "history, from inputs each rule proves were visible at the signal instant -- they "
            "are not predictions anybody registered before the outcome was known, and nothing "
            "here can show that the configuration itself was chosen without looking at the "
            "period it is run over. The score rows carry the signal instant on both clocks "
            "because that is the instant their inputs were visible at, not because anything was "
            "recorded then. Forward evidence is what the forward period and registered "
            "predictions are for; a result here stays a candidate."
        ),
        applies_to=DYNAMIC_SOURCE_KINDS,
    ),
    StrategyBacktestLimitation(
        code="the_labels_behind_a_dynamic_score_are_read_at_the_backtests_as_of",
        detail=(
            "The forward returns an IC or a fit is measured against are priced by the same "
            "label construction factor run and model evaluate use (build_label_window and "
            "label_outcome over the stored bars, bands, halts, registry and adjustment "
            "factors), and every one of those reads is made at the backtest's as_of rather than "
            "at each signal instant. Which labels a signal day may USE is decided by each "
            "label's own exit session against that day's 16:30, so no outcome is read before it "
            "closed; what is not point-in-time is the corpus's shape -- a bar restated after the "
            "fact is read in its restated form, and the registry and calendar are today's. "
            "model_view's the_evaluation_reads_its_labels_at_one_as_of_and_that_is_not_a_point_"
            "in_time_fit is the same boundary on the model plane."
        ),
        applies_to=DYNAMIC_SOURCE_KINDS,
    ),
    StrategyBacktestLimitation(
        code="a_lookback_reaching_before_the_stored_calendar_is_shorter_rather_than_refused",
        detail=(
            "A trailing window or a training window that starts before the backtest's first "
            "session reads the sessions in front of it from the contiguous run of registered "
            "trade_cal years ending the year before --start, as far back as the window needs. "
            "Where the stored calendar stops earlier the window is shorter than declared, and "
            "the shortfall shows up as fewer IC observations (an abstention under "
            "min_ic_observations) or a fit on fewer prediction days (ModelFit."
            "prediction_day_count), not as a refusal. A factor, price or adjustment year the "
            "calendar reaches and the panel does not hold is refused as panel_unreadable."
        ),
        applies_to=DYNAMIC_SOURCE_KINDS,
    ),
    StrategyBacktestLimitation(
        code="a_signal_day_whose_source_answers_nothing_is_held_rather_than_traded",
        detail=(
            "A signal day on which every trailing-IC factor weighs zero, or on which no "
            "walk-forward fit is in use yet (or the fit in use abstained on every security), "
            "has no scores: the book neither buys nor sells, keeps what it holds, and marks the "
            "period held. That is the only arrangement that does not invent a ranking, and it "
            "makes an early period's return a return on cash and on whatever was already held. "
            "A held run is an answer: every weight clipped to zero, or a fit that abstained on "
            "every name, is reported as the zero-trade run it is. Only a source that could not "
            "answer on ANY signal day -- no factor with min_ic_observations known ICs, or no fit "
            "in use -- is refused, with a diagnosis saying which, because that run measures the "
            "lookback's length rather than the source."
        ),
        applies_to=DYNAMIC_SOURCE_KINDS,
    ),
    StrategyBacktestLimitation(
        code="a_trailing_ic_weight_is_the_mean_of_the_ics_known_at_the_signal",
        detail=(
            "A factor's weight on signal day d is the plain mean of its daily ICs (FactorICStudy "
            "under the declared ic_method and min_ic_securities, oriented so a lower_is_better "
            "factor's working IC is positive) over the prediction days among the "
            "ic_window_sessions calendar sessions ending at d whose IC was knowable by d's 16:30: "
            "the later of the build's instant and the 16:30 of the session its label window "
            "exits on. An IC day whose cross section was too thin or degenerate is not an "
            "observation. The mean is not shrunk, not scaled by its dispersion and not tested "
            "for significance, so a factor with three noisy ICs weighs as confidently as one "
            "with three hundred; min_ic_observations is the only guard. clip_to_zero turns a "
            "negative mean into a zero weight; keep_sign trades the factor reversed. A weight "
            "of zero, abstained or clipped, takes the factor out of that day's ranking."
        ),
        applies_to=frozenset({"trailing_ic"}),
    ),
    StrategyBacktestLimitation(
        code="a_walk_forward_fit_trains_on_labels_closed_before_the_embargo_deadline",
        detail=(
            "A fit on refit session r trains on the prediction days among the train_sessions "
            "sessions ending at r whose label is known strictly before the 16:30 of the session "
            "embargo_sessions before r, so the embargo_sessions + horizon_sessions + 2 most "
            "recent sessions, r itself included, never train. Refits fall every "
            "refit_every_sessions "
            "sessions from the backtest's first session, and the fit in use on a signal day is "
            "the newest one refitted on or before it; the book refuses any fit whose own "
            "training cutoff is not known strictly before the session embargo_sessions before "
            "the signal. The feature cross section a fit scores is read at the signal instant. "
            "Hyperparameters are passed through, never selected here: choosing among grid "
            "configurations by their results is a model selection the research protocol has "
            "to account for."
        ),
        applies_to=frozenset({"walk_forward"}),
    ),
    StrategyBacktestLimitation(
        code="a_refit_the_model_refuses_leaves_the_previous_fit_in_use",
        detail=(
            "A refit with no closed label in its window, or one the model refuses (too few "
            "securities, a column no day could measure), is reported on the answer's model_fits "
            "with its reason and is not used; the newest successful earlier fit stays in use, "
            "which is what a live schedule would do and which ages the model past its declared "
            "refit interval. Each period's model_fit names the refit session it traded on, so a "
            "stale fit is visible rather than silent."
        ),
        applies_to=frozenset({"walk_forward"}),
    ),
    StrategyBacktestLimitation(
        code="a_walk_forward_model_reads_no_neutralized_feature",
        detail=(
            "A neutralized-tier feature is refused, by model_view.feature_columns, the one "
            "resolver the model faces share. The reason that function states -- a residual can "
            "only be built at its year's last stored session -- no longer holds (V2-P4-026 and "
            "V2-P4-028 retracted it; factor_view's "
            "the_three_tiers_must_have_been_built_at_the_same_instants says so). The reason this "
            "entry gave in its place -- no industry cross section before 2021-12-13, so a "
            "neutralized column was empty over the whole 2015-2021 walk-forward period -- is gone "
            "too: V2-P6-015 reads each day in the taxonomy in force then, SW2014 level one from "
            "2014-02-21, and a neutralized build at a 2016 instant succeeds. So the refusal now "
            "stands on no data limit at all; it stands because no test has driven a walk-forward "
            "fit on a neutralized column end to end on either face, and lifting it is a separate "
            "change with that test as its acceptance. A fit that did read one would also meet "
            "the_taxonomy_in_force_switches_on_2021_12_13 if its window spans that date. The "
            "trailing-IC and static sources read the neutralized tier."
        ),
        applies_to=frozenset({"walk_forward"}),
    ),
)
"""What a strategy backtest does not answer, as a closed registry rather than as prose."""

STRATEGY_BACKTEST_LIMITATION_CODES: Final[frozenset[str]] = frozenset(
    limitation.code for limitation in KNOWN_STRATEGY_BACKTEST_LIMITATIONS
)


def limitation_codes_for(kind: str) -> tuple[str, ...]:
    """The registry's codes that speak for a source of `kind`, in registry order."""
    if kind not in SCORE_SOURCE_KINDS:
        raise ValueError(f"{kind!r} is not a score source kind; the kinds are {SCORE_SOURCE_KINDS}")
    return tuple(
        item.code for item in KNOWN_STRATEGY_BACKTEST_LIMITATIONS if kind in item.applies_to
    )


def component_key(factor: str, tier: str) -> str:
    """The key a score row names its component by: `<factor>@<tier>`."""
    return f"{factor}@{tier}"


class TrailingICWeights(BaseModel):
    """Stored factor tiers weighted, each signal day, by their own trailing mean IC.

    Plain configuration (`V2-P6-014`): a research grid passes it as data. See
    `a_trailing_ic_weight_is_the_mean_of_the_ics_known_at_the_signal` for the rule and
    `_ICIndex.weights` for the line that enforces it. `min_ic_securities` is `FactorICSpec
    .min_securities` -- the floor a day's cross section must clear to have an IC at all -- and
    has no default for that contract's reason.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    components: tuple[tuple[str, StrategyTier], ...] = Field(min_length=1)
    ic_window_sessions: int = Field(ge=1)
    min_ic_observations: int = Field(ge=1)
    ic_method: Literal["spearman", "pearson"]
    horizon_sessions: int = Field(ge=1)
    negative_ic: Literal["clip_to_zero", "keep_sign"]
    min_ic_securities: int = Field(ge=MINIMUM_IC_SECURITIES)

    @model_validator(mode="after")
    def validate_weighable(self) -> Self:
        seen: set[tuple[str, str]] = set()
        for factor, tier in self.components:
            if not factor.strip():
                raise ValueError("a trailing-IC component must name a factor")
            if (factor, tier) in seen:
                raise ValueError(f"component {factor}@{tier} is declared twice")
            seen.add((factor, tier))
        if self.min_ic_observations > self.ic_window_sessions:
            raise ValueError(
                f"min_ic_observations {self.min_ic_observations} exceeds ic_window_sessions "
                f"{self.ic_window_sessions}; no window that short can hold that many ICs, so "
                "every factor would abstain on every day"
            )
        return self

    @property
    def component_keys(self) -> tuple[str, ...]:
        """Each component's `<factor>@<tier>` key, in declared order."""
        return tuple(component_key(factor, tier) for factor, tier in self.components)


class WalkForwardModel(BaseModel):
    """An `AlphaModelDeclaration`-shaped model refitted on a schedule, every fit point-in-time.

    `family` is one of `model_view.MODEL_FAMILIES`' keys and `features` are
    `<factor>@<tier>[:<transform>]` tokens; both are resolved by the view against the model
    faces' own tables. `hyperparameters` pass through to the declaration unchanged; a mapping is
    accepted and sorted by name. `code_commit` reaches the fitted artifact's address and is the
    caller's to state, since this module cannot read git (rule 8: a reproducibility claim must be
    real).

    The schedule's one arithmetic floor is `train_sessions >= embargo_sessions +
    horizon_sessions + 3`: a label entered on the session after its prediction day and exiting
    `horizon_sessions` later is known strictly before the session `embargo_sessions` before a
    refit only for prediction days at least that many sessions back, so a shorter window can
    never hold a training day.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    family: str = Field(min_length=1)
    features: tuple[str, ...] = Field(min_length=1)
    hyperparameters: tuple[tuple[str, bool | int | float | str], ...] = ()
    seed: int = Field(ge=0)
    code_commit: str = Field(min_length=7, max_length=64)
    train_sessions: int = Field(ge=1)
    refit_every_sessions: int = Field(ge=1)
    embargo_sessions: int = Field(ge=0)
    horizon_sessions: int = Field(ge=1)
    missing: Literal["abstain", "drop_security", "cross_section_median"] = "abstain"

    @field_validator("hyperparameters", mode="before")
    @classmethod
    def accept_a_mapping(cls, value: object) -> object:
        if isinstance(value, Mapping):
            return tuple(sorted(value.items(), key=lambda pair: str(pair[0])))
        return value

    @model_validator(mode="after")
    def validate_point_in_time_schedule(self) -> Self:
        if any(not token.strip() or "@" not in token for token in self.features):
            raise ValueError(
                f"features {list(self.features)} must each be <factor>@<tier>[:<transform>]"
            )
        if len(set(self.features)) != len(self.features):
            raise ValueError("a feature is declared twice; a fit would weight it twice")
        if self.embargo_sessions < self.horizon_sessions:
            raise ValueError(
                f"embargo_sessions {self.embargo_sessions} is shorter than horizon_sessions "
                f"{self.horizon_sessions}; an embargo inside one label's own window lets a "
                "training label overlap the returns the signal it feeds is scored on"
            )
        floor = self.embargo_sessions + self.horizon_sessions + 3
        if self.train_sessions < floor:
            raise ValueError(
                f"train_sessions {self.train_sessions} is below {floor} (embargo_sessions + "
                "horizon_sessions + 3), so no prediction day in the window could have a label "
                "known before the embargo deadline"
            )
        return self


class ScoreSource(BaseModel):
    """Where the scores come from: exactly one of four kinds (`ScoreSourceKind`).

    - `components` (**static**): `(factor key, tier, weight)` triples. The view orients every
      stored value so that higher is better before it reaches `combine` (a `lower_is_better`
      factor is negated), so a weight's sign is a statement about the combination and never a
      repair of a factor's direction.
    - `prediction_ids`: registered predictions, one component `PREDICTION_COMPONENT` weighing 1.
    - `trailing_ic` (`V2-P6-014`): stored tiers weighted per signal day by their trailing IC.
    - `walk_forward` (`V2-P6-014`): one component `MODEL_COMPONENT`, a refitted model's score.

    Not persisted anywhere: a field added here is configuration, not a stored contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    components: tuple[tuple[str, StrategyTier, Decimal], ...] = ()
    combine: Literal["zscore_sum", "rank_sum"]
    prediction_ids: tuple[str, ...] = ()
    trailing_ic: TrailingICWeights | None = None
    walk_forward: WalkForwardModel | None = None

    @model_validator(mode="after")
    def validate_one_series(self) -> Self:
        named = (
            bool(self.components)
            + bool(self.prediction_ids)
            + (self.trailing_ic is not None)
            + (self.walk_forward is not None)
        )
        if named != 1:
            raise ValueError(
                "a score source names exactly one of components, prediction_ids, trailing_ic "
                "and walk_forward; two or none is not a series anyone could rank on"
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
    def kind(self) -> ScoreSourceKind:
        """Which of the four kinds this source is."""
        if self.trailing_ic is not None:
            return "trailing_ic"
        if self.walk_forward is not None:
            return "walk_forward"
        return "prediction" if self.prediction_ids else "static"

    @property
    def component_keys(self) -> tuple[str, ...]:
        """Every component key a score row of this source may name."""
        if self.trailing_ic is not None:
            return self.trailing_ic.component_keys
        if self.walk_forward is not None:
            return (MODEL_COMPONENT,)
        return tuple(self.weights)

    @property
    def weights(self) -> Mapping[str, Decimal]:
        """Each component key's fixed weight; empty for the two dynamic kinds, whose weights
        are decided per signal day."""
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


class TrailingICWeight(BaseModel):
    """One factor's trailing-IC weight on one signal day, with what it was computed from.

    `observations` counts the ICs known at the signal instant inside the window, and `mean_ic`
    is their mean (`None` when there were none); `weight` is `0.0` when the factor abstained
    (fewer than `min_ic_observations`) or was clipped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    component: str
    observations: int = Field(ge=0)
    mean_ic: float | None
    weight: float


class ModelFit(BaseModel):
    """One walk-forward refit as the answer reports it: the fit, or why there is none.

    `labels_known_at` is the signal instant of the session the newest training label exited
    on; `training_cutoff` is the artifact's own (that session's 15:00 close).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    refit_day: date
    refusal: str | None
    artifact_id: str | None
    training_cutoff: datetime | None
    labels_known_at: datetime | None
    example_count: int = Field(ge=0)
    prediction_day_count: int = Field(ge=0)


class PeriodResult(BaseModel):
    """One rebalance period, from one signal close to the next.

    The brief's eight fields, plus the ledger that makes each of them re-derivable:
    `sessions`, `start_value`/`end_value`, `cost_yuan`, `capped_orders`, `holdings` and the
    fills and rejections themselves. `V2-P6-014` adds three: `held` (the source had no scores on
    the signal day, so nothing traded), `ic_weights` (a trailing-IC source's weights that day)
    and `model_fit` (the walk-forward fit in use that day).
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
    held: bool = False
    ic_weights: tuple[TrailingICWeight, ...] = ()
    model_fit: ModelFit | None = None
    unknowable_sessions: tuple[str, ...] = ()
    """`<security>@<session>` for every session inside the period whose return a held position
    crossed and no witness decides (`V2-P6-020`); valued by the adjustment factor, see
    `a_session_whose_return_is_unknowable_is_valued_by_the_adjustment_factor`."""

    @model_validator(mode="after")
    def validate_ledger(self) -> Self:
        if self.gross_return != self.net_return + self.cost:
            raise ValueError("gross_return must equal net_return + cost")
        if self.rejected_orders != len(self.rejections):
            raise ValueError("rejected_orders must count the rejections")
        if self.end <= self.start:
            raise ValueError("a period ends after it starts")
        if self.held and (self.fills or self.rejections):
            raise ValueError("a held period placed no order")
        return self


class UnknowableCrossing(BaseModel):
    """One session a held position crossed whose return no witness decides (`V2-P6-020`).

    The book values it by the adjustment factor
    (`a_session_whose_return_is_unknowable_is_valued_by_the_adjustment_factor`); these fields say
    what that choice is worth. `held_value` is what the book booked for the position at that
    session's price -- its close, or its open when it was sold there -- and
    `valuation_difference` is what the published path would have booked instead, less that:
    `held_value x (path_ratio - 1)`, in yuan, from the session's own two statements and with no
    second run. `share_of_book` is that difference over the book's value at the start of the
    period it fell in (`period_start`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str
    day: date
    period_start: date
    held_value: Decimal
    valuation_difference: Decimal
    share_of_book: Decimal


class StrategyBacktest(BaseModel):
    """The whole answer: the rules, the score source, every period, and what it cannot say.

    `model_fits` is every walk-forward refit of the run, fitted or refused, in refit order.
    `rebalance_days` are the sessions the run was told to rebalance on (`V2-P6-011`), `None` on
    the fixed grid -- part of the answer, so a run on named days never reads as a grid run of
    the same spec and source. An answer model, not a stored row: nothing persists it.
    `unknowable_crossings` (`V2-P6-020`) is every session a held position crossed whose return is
    unknowable, in the order the book met them -- empty for a run the limitation of that name did
    not touch, which is how a reader tells the two apart.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    spec: StrategySpec
    source: ScoreSource
    periods: tuple[PeriodResult, ...]
    limitations: tuple[str, ...]
    model_fits: tuple[ModelFit, ...] = ()
    rebalance_days: tuple[date, ...] | None = None
    unknowable_crossings: tuple[UnknowableCrossing, ...] = ()


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

    `recorded_path` (`V2-P6-020`) is set only on a session whose `pre_close` and `adj_factor`
    disagree and whose `upstream_defects` record decided it: `published` (the book follows
    `close / bar.previous_close` through the session), `adjusted` (the factor path, which the
    book follows anyway) or `unknowable` (neither is corroborated). See
    `a_session_whose_return_is_unknowable_is_valued_by_the_adjustment_factor`.

    `path_ratio` is that session's own `implied_pre_close / pre_close` -- the published path's
    gross return over the factor path's -- and is required exactly when `recorded_path` is set.
    The book rescales by it, so the correction does not depend on when it last valued the
    position (review round 1, Minor 2), and prices an unknowable crossing against the other path
    with it.
    """

    bar: MarketBar
    turnover_yuan: Decimal
    adj_factor: Decimal
    recorded_path: Literal["published", "adjusted", "unknowable"] | None = None
    path_ratio: Decimal | None = None

    def __post_init__(self) -> None:
        if self.turnover_yuan < 0:
            raise StrategyBacktestError(f"{self.bar.subject} reports negative turnover")
        if self.adj_factor <= 0:
            raise StrategyBacktestError(f"{self.bar.subject} reports a non-positive adj_factor")
        if (self.recorded_path is None) != (self.path_ratio is None):
            raise StrategyBacktestError(
                f"{self.bar.subject} on {self.bar.trade_date.isoformat()}: path_ratio is carried "
                "exactly when a recorded path is, and this quote has one without the other"
            )
        if self.path_ratio is not None and self.path_ratio <= 0:
            raise StrategyBacktestError(f"{self.bar.subject} reports a non-positive path_ratio")


@dataclass(frozen=True, slots=True, kw_only=True)
class ICObservation:
    """One component's IC on one prediction day, and the instant it became knowable.

    `ic` is `FactorICStudy.measure`'s oriented IC, or `None` when that day's cross section was
    too thin or degenerate to have one. `known_at` is the later of the build's instant and the
    signal instant of the session the label window exits on: before it, nobody could have
    computed this number.
    """

    component: str
    prediction_day: date
    known_at: datetime
    ic: float | None

    def __post_init__(self) -> None:
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise StrategyBacktestError("an IC observation's known_at must be timezone-aware")
        if self.ic is not None and not (math.isfinite(self.ic) and -1.0 <= self.ic <= 1.0):
            raise StrategyBacktestError(
                f"{self.component}'s IC on {self.prediction_day.isoformat()} is {self.ic!r}, "
                "which is not a correlation"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class WalkForwardFit:
    """One refit: the fitted model and what it trained on, or the reason there is none.

    `labels_known_at` is the signal instant of the session the newest training label exited on.
    The book does not trust it: its guard re-derives the same instant from `artifact
    .training_cutoff`, which the model computed from the training set itself.
    """

    refit_day: date
    fitted: FittedAlphaModel | None
    artifact: AlphaModelArtifact | None
    refusal: str | None
    labels_known_at: datetime | None
    example_count: int
    prediction_day_count: int

    def __post_init__(self) -> None:
        if (self.fitted is None) != (self.refusal is not None) or (self.fitted is None) != (
            self.artifact is None
        ):
            raise StrategyBacktestError(
                f"the refit on {self.refit_day.isoformat()} must carry a fitted model and its "
                "artifact, or a refusal, and not both"
            )

    @property
    def record(self) -> ModelFit:
        """This refit as the answer reports it."""
        return ModelFit(
            refit_day=self.refit_day,
            refusal=self.refusal,
            artifact_id=None if self.artifact is None else self.artifact.artifact_id,
            training_cutoff=None if self.artifact is None else self.artifact.training_cutoff,
            labels_known_at=self.labels_known_at,
            example_count=self.example_count,
            prediction_day_count=self.prediction_day_count,
        )


class ScoreFeed(Protocol):
    """Where a streamed backtest's scores come from, one signal day at a time (`V2-P6-014`).

    The book asks in signal-day order and asks about each day once, so a feed may read what a
    period needs when it is asked and drop it afterwards; memory is then bounded by a window
    rather than by the history. Each method is the streamed twin of a `StrategyInputs` field.
    """

    def rows_on(self, day: date) -> Sequence[ScoreRow]:
        """Every score row dated `day` (a signal day), for every declared component."""

    def ic_observations_through(self, day: date) -> Sequence[ICObservation]:
        """The ICs whose prediction day is at or before `day`, not handed over before."""

    def fit_on(self, day: date) -> WalkForwardFit | None:
        """The walk-forward fit `day`'s model rows were scored by, or `None` (no fit yet)."""

    def refits(self) -> tuple[WalkForwardFit, ...]:
        """Every refit made so far, fitted or refused, in refit order."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyInputs:
    """Everything a backtest reads, already out of the panel -- or a feed that reads it.

    `sessions` are the open sessions of the range, ascending; the first is the first signal
    day. `signal_instants` is each session's signal instant -- the lookback's too, when there is
    one. `quotes` is per session per security; `benchmark_returns` is per benchmark name per
    session; `industries` is per signal day per security and is read only when the spec caps
    industries.

    The two dynamic kinds (`V2-P6-014`) read three more. `lookback_sessions` are the open
    sessions before `sessions[0]` a trailing or training window may reach, ascending.
    `ic_observations` are a trailing-IC source's daily ICs. `model_fits` is every refit of a
    walk-forward source and `fit_for_day` the one each signal day's model rows were scored by.

    `feed` replaces the four score fields with a `ScoreFeed`, which the view uses so a run holds
    one period's scores at a time. A run over a feed and a run over the same scores materialised
    into the four fields are the same run.

    `rebalance_days` (`V2-P6-011`) replaces the fixed grid with the sessions the book actually
    rebalanced on -- a forward book priced on the days its daily command recommended, which
    catches a missed rebalance up at its next run and does not rebalance on a day its source
    held. `None`, the default, is the grid; see `rebalance_indices`.
    """

    source: ScoreSource
    sessions: tuple[date, ...]
    signal_instants: Mapping[date, datetime]
    scores: tuple[ScoreRow, ...]
    quotes: Mapping[date, Mapping[str, SessionQuote]]
    benchmark_returns: Mapping[str, Mapping[date, Decimal]]
    industries: Mapping[date, Mapping[str, str]] = field(default_factory=dict)
    lookback_sessions: tuple[date, ...] = ()
    ic_observations: tuple[ICObservation, ...] = ()
    model_fits: tuple[WalkForwardFit, ...] = ()
    fit_for_day: Mapping[date, WalkForwardFit] = field(default_factory=dict)
    feed: ScoreFeed | None = None
    rebalance_days: tuple[date, ...] | None = None

    def __post_init__(self) -> None:
        if len(self.sessions) < 2:
            raise StrategyBacktestError(
                "a backtest needs at least two sessions: a signal and the session it trades on"
            )
        calendar = self.calendar
        if any(later <= earlier for earlier, later in pairwise(calendar)):
            raise StrategyBacktestError(
                "sessions must be strictly ascending, and every lookback session before them"
            )
        if self.feed is not None and (
            self.scores or self.ic_observations or self.model_fits or self.fit_for_day
        ):
            raise StrategyBacktestError(
                "scores come from the feed or from the four score fields, not from both"
            )

    @property
    def calendar(self) -> tuple[date, ...]:
        """The lookback sessions and the range's sessions, one ascending calendar."""
        return self.lookback_sessions + self.sessions


class _MaterialisedFeed:
    """A `ScoreFeed` over scores already held in `StrategyInputs`' four fields."""

    def __init__(self, inputs: StrategyInputs, signal_days: frozenset[date]) -> None:
        self._rows: dict[date, list[ScoreRow]] = {}
        for row in inputs.scores:
            if row.signal_day in signal_days:
                self._rows.setdefault(row.signal_day, []).append(row)
        self._observations = sorted(inputs.ic_observations, key=lambda item: item.prediction_day)
        self._handed = 0
        self._fits = inputs.fit_for_day
        self._refits = inputs.model_fits

    def rows_on(self, day: date) -> Sequence[ScoreRow]:
        return self._rows.pop(day, [])

    def ic_observations_through(self, day: date) -> Sequence[ICObservation]:
        end = self._handed
        while end < len(self._observations) and self._observations[end].prediction_day <= day:
            end += 1
        handed, self._handed = self._observations[self._handed : end], end
        return handed

    def fit_on(self, day: date) -> WalkForwardFit | None:
        return self._fits.get(day)

    def refits(self) -> tuple[WalkForwardFit, ...]:
        return self._refits


def rebalance_indices(
    sessions: Sequence[date], *, every: int, days: Sequence[date] | None = None
) -> tuple[int, ...]:
    """The positions in `sessions` the book rebalances at, or `StrategyBacktestError`.

    `days is None` is the fixed grid: `0, R, 2R, ...` for as long as a session follows to trade
    on, `R = every`. Otherwise `days` are the sessions themselves (`V2-P6-011`): ascending, each a
    session of the range with one after it to trade on, and the first the range's first session
    -- the book starts in cash, so a range that opened before its first rebalance would report a
    period no decision made.
    """
    if days is None:
        return tuple(range(0, len(sessions) - 1, every))
    if not days:
        raise StrategyBacktestError("rebalance_days names no day; a book needs at least one")
    position = {day: index for index, day in enumerate(sessions)}
    absent = [day.isoformat() for day in days if day not in position]
    if absent:
        raise StrategyBacktestError(
            f"rebalance day(s) {absent} are not a session of the range "
            f"{sessions[0].isoformat()}..{sessions[-1].isoformat()}"
        )
    indices = tuple(position[day] for day in days)
    if any(later <= earlier for earlier, later in pairwise(indices)):
        raise StrategyBacktestError(
            f"rebalance_days must be strictly ascending sessions; got "
            f"{[day.isoformat() for day in days]}"
        )
    if indices[-1] == len(sessions) - 1:
        raise StrategyBacktestError(
            f"rebalance day {days[-1].isoformat()} is the range's last session, with no session "
            "after it to trade on"
        )
    if indices[0] != 0:
        raise StrategyBacktestError(
            f"the first rebalance day {days[0].isoformat()} is not the range's first session "
            f"{sessions[0].isoformat()}; start the range on it"
        )
    return indices


def run_strategy_backtest(inputs: StrategyInputs, spec: StrategySpec) -> StrategyBacktest:
    """Run the book over every period `inputs` spans and return the answer.

    Signal days are `sessions[0]`, `sessions[R]`, `sessions[2R]`, ... for as long as a session
    follows to trade on, where `R = spec.rebalance_every_sessions` -- or, when
    `inputs.rebalance_days` names them, those sessions (`rebalance_indices`); each period runs to
    the next signal day or the range's last session. Each signal day's scores are
    asked for when its period is booked and not before, so a streamed feed holds one period at a
    time. Refuses, with `StrategyBacktestError`: a signal day with no signal instant, a score row
    that was not visible at its signal day's instant, a row naming a component the source does
    not declare, a signal day on which some weighted component has no cross section or a
    degenerate one, and a benchmark with no return for some session a period spans. For the two
    dynamic kinds it also refuses a walk-forward fit not closed by its signal's embargo deadline,
    model rows on a day no fit is named for, and a source that could not answer at all
    (`_Scorer.refuse_a_source_that_never_answered`). A source that answered and chose to trade
    nothing -- every trailing weight clipped to zero, a fit that abstained on every name -- is an
    answer, and is reported.
    """
    missing = [name for name in spec.benchmarks if name not in inputs.benchmark_returns]
    if missing:
        raise StrategyBacktestError(f"no return series was supplied for benchmark(s) {missing}")
    sessions = inputs.sessions
    signal_indices = rebalance_indices(
        sessions, every=spec.rebalance_every_sessions, days=inputs.rebalance_days
    )
    signal_days = frozenset(sessions[index] for index in signal_indices)
    feed: ScoreFeed = inputs.feed or _MaterialisedFeed(inputs, signal_days)
    scorer = _Scorer(inputs, feed)
    book = _Book(spec=spec, cash=spec.initial_capital)
    periods: list[PeriodResult] = []
    for position, index in enumerate(signal_indices):
        end_index = (
            signal_indices[position + 1]
            if position + 1 < len(signal_indices)
            else len(sessions) - 1
        )
        periods.append(
            _run_period(
                inputs,
                spec,
                book,
                signal_index=index,
                end_index=end_index,
                signal=scorer.signal(sessions[index]),
            )
        )
    refits = feed.refits()
    scorer.refuse_a_source_that_never_answered(len(signal_indices), refits)
    return StrategyBacktest(
        spec=spec,
        source=inputs.source,
        periods=tuple(periods),
        limitations=limitation_codes_for(inputs.source.kind),
        model_fits=tuple(fit.record for fit in refits),
        rebalance_days=inputs.rebalance_days,
        unknowable_crossings=tuple(book.unknowable),
    )


# --- one signal day, scored with no book to run (V2-P6-011) -------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalDayInputs:
    """What scoring one signal day reads, when there is no range and no book around it.

    `calendar` is every session a trailing or training window may reach, ascending and ending at
    or after the day scored; `signal_instants` is each of those sessions' signal instant. The
    daily command (`V2-P6-011`) scores today with this: a `StrategyInputs` needs a session after
    the signal to trade on, and after today's close there is none yet.
    """

    source: ScoreSource
    calendar: tuple[date, ...]
    signal_instants: Mapping[date, datetime]

    def __post_init__(self) -> None:
        if not self.calendar:
            raise StrategyBacktestError("a signal day is scored on a calendar holding it")
        if any(later <= earlier for earlier, later in pairwise(self.calendar)):
            raise StrategyBacktestError("the calendar a signal day is scored on must ascend")


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalDayScores:
    """One signal day's answer exactly as `run_strategy_backtest` computes it for the book.

    `ranked` is the order the book trades on, or `None` when the source held (no trailing weight
    and no fit in use). `scores` is the composite each ranked security was ordered by -- the
    number a registered prediction of this day must carry for a backtest reading it to order the
    market the same way; `incomplete` are the securities carrying some weighted component but
    not every one; `weights` the weight each component was combined under.
    """

    day: date
    ranked: tuple[str, ...] | None
    scores: Mapping[str, float]
    incomplete: tuple[str, ...]
    weights: Mapping[str, float]
    ic_weights: tuple[TrailingICWeight, ...]
    model_fit: ModelFit | None


def score_signal_day(inputs: SignalDayInputs, feed: ScoreFeed, day: date) -> SignalDayScores:
    """Score `day` through the book's own scorer: the same guards, weights and ranking.

    `run_strategy_backtest` asks its `_Scorer` for each signal day's ranking; this asks the same
    scorer for one day, so a caller registering today's scores (`V2-P6-011`) holds the numbers
    the backtest would have ranked on, by construction rather than by a second implementation.
    Every refusal the book makes while scoring -- a row not visible at the signal instant, a
    component with no cross section, a fit not closed by its embargo -- is made here too.
    """
    if day not in inputs.calendar:
        raise StrategyBacktestError(f"{day.isoformat()} is not a session of the calendar given")
    signal = _Scorer(inputs, feed).signal(day)
    return SignalDayScores(
        day=day,
        ranked=signal.ranked,
        scores=dict(signal.scores),
        incomplete=signal.incomplete,
        weights=dict(signal.weights),
        ic_weights=signal.ic_weights,
        model_fit=signal.model_fit,
    )


# --- scores -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class _Signal:
    """One signal day's decision input: the ranking, or `None` for a day the book holds.

    `scores` is the composite each ranked security was ordered by, `incomplete` the securities
    that carried some weighted component but not every one (so were not ranked), and `weights`
    the weight each component was combined under that day -- what `score_signal_day` hands a
    caller that registers the day's scores (`V2-P6-011`). The book reads `ranked` alone.
    """

    ranked: tuple[str, ...] | None
    scores: Mapping[str, float] = field(default_factory=dict)
    incomplete: tuple[str, ...] = ()
    weights: Mapping[str, float] = field(default_factory=dict)
    ic_weights: tuple[TrailingICWeight, ...] = ()
    model_fit: ModelFit | None = None


class _ScoringContext(Protocol):
    """What scoring one signal day reads: the source, the calendar and each session's instant.

    `StrategyInputs` is one; `SignalDayInputs` is the other, for a caller scoring one day with no
    book to run (`score_signal_day`).
    """

    @property
    def source(self) -> ScoreSource: ...
    @property
    def calendar(self) -> tuple[date, ...]: ...
    @property
    def signal_instants(self) -> Mapping[date, datetime]: ...


class _Scorer:
    """Each signal day's ranking under the source's kind, asked for in signal-day order.

    Also keeps the one fact the end of the run needs: whether the source could answer on any
    signal day at all -- a trailing factor with at least `min_ic_observations` known ICs, or a
    walk-forward fit in use.
    """

    def __init__(self, inputs: _ScoringContext, feed: ScoreFeed) -> None:
        self._inputs = inputs
        self._feed = feed
        source = inputs.source
        self._index = (
            None
            if source.trailing_ic is None
            else _ICIndex(source.trailing_ic, calendar=inputs.calendar)
        )
        self._answered = False
        self._most_known = 0

    def signal(self, day: date) -> _Signal:
        inputs, source = self._inputs, self._inputs.source
        if self._index is not None:
            self._index.add(self._feed.ic_observations_through(day))
        components = _cross_section(inputs, day, self._feed.rows_on(day))
        if self._index is not None:
            spec = source.trailing_ic
            assert spec is not None  # the index exists only for a trailing-IC source
            weights = self._index.weights(day, instant=_instant(inputs, day))
            self._most_known = max([self._most_known, *(item.observations for item in weights)])
            if any(item.observations >= spec.min_ic_observations for item in weights):
                self._answered = True
            active = {item.component: item.weight for item in weights if item.weight != 0.0}
            if not active:
                return _Signal(ranked=None, ic_weights=weights)
            return _ranked(day, components, active, source.combine, ic_weights=weights)
        if source.walk_forward is not None:
            fit = self._feed.fit_on(day)
            if fit is not None:
                self._answered = True
            return _model_signal(inputs, source.walk_forward, day, components, fit)
        fixed = {key: float(weight) for key, weight in source.weights.items()}
        return _ranked(day, components, fixed, source.combine)

    def refuse_a_source_that_never_answered(
        self, signal_count: int, refits: Sequence[WalkForwardFit]
    ) -> None:
        """Refuse a dynamic source that could not answer on any signal day, saying which way.

        Not a source that answered "trade nothing": a trailing factor whose known ICs are all
        clipped to zero, or a fit that abstained on every security, is a legitimate zero-trade
        answer and is reported. What is refused is a source with nothing to answer WITH on every
        one of the run's signal days -- no factor with `min_ic_observations` known ICs, or no fit
        in use -- because that run measures the lookback's length, not the source.
        """
        source = self._inputs.source
        if self._answered or source.kind not in DYNAMIC_SOURCE_KINDS:
            return
        if source.trailing_ic is not None:
            floor = source.trailing_ic.min_ic_observations
            raise StrategyBacktestError(
                f"the trailing-IC source could not answer on any of the {signal_count} signal "
                f"days: no component had min_ic_observations={floor} ICs known at any signal "
                f"instant (the most any component knew was {self._most_known}). Lengthen the "
                "lookback, lower min_ic_observations or start the range later"
            )
        reasons = sorted({fit.refusal for fit in refits if fit.refusal is not None})
        raise StrategyBacktestError(
            f"the walk-forward source could not answer on any of the {signal_count} signal days: "
            f"no admissible fit was in use on any of them ({len(refits)} refit(s), "
            f"{sum(fit.fitted is not None for fit in refits)} fitted; refusals: {reasons}). "
            "No refit window held a label closed before its embargo deadline, or the model "
            "refused every one it had: store more history before --start (the lookback reads "
            "the registered trade_cal years and the factor builds in them), or start the "
            "range later"
        )


def _instant(inputs: _ScoringContext, day: date) -> datetime:
    instant = inputs.signal_instants.get(day)
    if instant is None:
        raise StrategyBacktestError(f"session {day.isoformat()} has no signal instant")
    return instant


def _model_signal(
    inputs: _ScoringContext,
    spec: WalkForwardModel,
    day: date,
    components: Mapping[str, Mapping[str, float]],
    fit: WalkForwardFit | None,
) -> _Signal:
    rows = components.get(MODEL_COMPONENT)
    if fit is not None:
        _refuse_a_fit_not_closed_by_the_embargo(inputs, spec, fit, day)
    if rows and fit is None:
        raise StrategyBacktestError(
            f"model scores are supplied for {day.isoformat()} and no fit is named for that "
            "day, so nothing says which training labels produced them"
        )
    record = None if fit is None else fit.record
    if not rows:
        return _Signal(ranked=None, model_fit=record)
    return _ranked(day, components, {MODEL_COMPONENT: 1.0}, "zscore_sum", model_fit=record)


def _embargo_deadline(inputs: _ScoringContext, day: date, embargo_sessions: int) -> datetime:
    """The signal instant of the session `embargo_sessions` before `day` on the calendar."""
    calendar = inputs.calendar
    position = calendar.index(day) - embargo_sessions
    if position < 0:
        raise StrategyBacktestError(
            f"the calendar supplied does not reach {embargo_sessions} session(s) before "
            f"{day.isoformat()}, so no fit can be shown to be embargoed from it"
        )
    return _instant(inputs, calendar[position])


def _refuse_a_fit_not_closed_by_the_embargo(
    inputs: _ScoringContext, spec: WalkForwardModel, fit: WalkForwardFit, day: date
) -> None:
    """The walk-forward rule at the point of use: every training label closed strictly before
    the signal instant of the session `embargo_sessions` before `day`.

    Read off the artifact's own `training_cutoff` -- the newest exit session any training
    example's window closed on, computed by the model's `TrainingSet` -- rather than off
    `WalkForwardFit.labels_known_at`, which the producer of the fit wrote.
    """
    if fit.artifact is None:
        raise StrategyBacktestError(
            f"the fit named for {day.isoformat()} was refused ({fit.refusal}); a refused refit "
            "scores nothing"
        )
    deadline = _embargo_deadline(inputs, day, spec.embargo_sessions)
    exit_day = fit.artifact.training_cutoff.astimezone(deadline.tzinfo).date()
    known = _instant(inputs, exit_day)
    if not known < deadline:
        raise StrategyBacktestError(
            f"the fit refitted on {fit.refit_day.isoformat()} trained on a label that closed on "
            f"{exit_day.isoformat()} (knowable {known.isoformat()}), and on "
            f"{day.isoformat()} every training label must have closed strictly before "
            f"{deadline.isoformat()}, the signal instant {spec.embargo_sessions} session(s) "
            "earlier (the embargo). Scoring with it would be look-ahead"
        )


def _cross_section(
    inputs: _ScoringContext, day: date, rows: Sequence[ScoreRow]
) -> dict[str, dict[str, float]]:
    """One signal day's cross section per component, after the look-ahead guard.

    Only the signal day's own rows are ever asked for: no rebalance happens on another session,
    so nothing could trade on them.
    """
    keys = inputs.source.component_keys
    instant = inputs.signal_instants.get(day)
    if instant is None:
        raise StrategyBacktestError(f"signal day {day.isoformat()} has no signal instant")
    by_component: dict[str, dict[str, float]] = {}
    for row in rows:
        if row.signal_day != day:
            raise StrategyBacktestError(
                f"a score row dated {row.signal_day.isoformat()} was handed over for "
                f"{day.isoformat()}"
            )
        if row.component not in keys:
            raise StrategyBacktestError(
                f"a score row names component {row.component!r} and the source declares no "
                f"component by that key; it declares {sorted(keys)}"
            )
        if row.available_time > instant or row.revision_time > instant:
            raise StrategyBacktestError(
                f"{row.subject}'s {row.component} score for {row.signal_day.isoformat()} is not "
                f"visible at that day's signal instant {instant.isoformat()}: it became "
                f"available at {row.available_time.isoformat()} and was revised at "
                f"{row.revision_time.isoformat()}. Trading on it would be look-ahead; build the "
                "cross section at or before the signal instant"
            )
        cross_section = by_component.setdefault(row.component, {})
        if row.subject in cross_section:
            raise StrategyBacktestError(
                f"{row.subject} has two {row.component} scores on {row.signal_day.isoformat()}"
            )
        cross_section[row.subject] = row.value
    return by_component


def _ranked(
    day: date,
    components: Mapping[str, Mapping[str, float]],
    weights: Mapping[str, float],
    combine: Literal["zscore_sum", "rank_sum"],
    *,
    ic_weights: tuple[TrailingICWeight, ...] = (),
    model_fit: ModelFit | None = None,
) -> _Signal:
    """The securities carrying every weighted component, best first; ties by code, ascending.

    Each one's composite is the weighted sum of its standardized component values -- z-scores
    or rank fractions over the securities carrying every component -- and the order is by that
    composite, descending, then by code. The securities carrying some weighted component but not
    all of them are `incomplete`: they are not ranked, and are named rather than dropped.
    """
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
            total[subject] += weight * value
    carried: set[str] = set().union(*(set(components[key]) for key in weights))
    return _Signal(
        ranked=tuple(sorted(subjects, key=lambda subject: (-total[subject], subject))),
        scores=total,
        incomplete=tuple(sorted(carried - set(subjects))),
        weights=dict(weights),
        ic_weights=ic_weights,
        model_fit=model_fit,
    )


def _zscores(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) == 1:
        return (0.0,)
    mean = statistics.fmean(values)
    deviation = statistics.pstdev(values)
    return tuple((value - mean) / deviation for value in values)


def _rank_fractions(values: Sequence[float]) -> tuple[float, ...]:
    size = len(values)
    return tuple(rank / size for rank in average_ranks(values))


# --- trailing-IC weights (V2-P6-014) ------------------------------------------------------------


class _ICIndex:
    """One trailing-IC source's observations, indexed by component and calendar position.

    Filled incrementally -- a streamed run adds each year's ICs as it reaches them -- so each
    signal day reads only its own window; the rule itself is `weights`, and
    `trailing_ic_weights` is the same rule for one day.
    """

    def __init__(
        self,
        spec: TrailingICWeights,
        observations: Sequence[ICObservation] = (),
        *,
        calendar: Sequence[date],
    ) -> None:
        self._spec = spec
        self._position = {day: index for index, day in enumerate(calendar)}
        self._entries: dict[str, list[tuple[int, datetime, float | None]]] = {
            key: [] for key in spec.component_keys
        }
        self._positions: dict[str, list[int]] = {key: [] for key in spec.component_keys}
        self._seen: set[tuple[str, date]] = set()
        self.add(observations)

    def add(self, observations: Sequence[ICObservation]) -> None:
        """Index more observations, refusing a foreign component, a stray day or a repeat."""
        for item in observations:
            if item.component not in self._entries:
                raise StrategyBacktestError(
                    f"an IC observation names component {item.component!r} and the source "
                    f"declares no component by that key; it declares {sorted(self._entries)}"
                )
            position = self._position.get(item.prediction_day)
            if position is None:
                raise StrategyBacktestError(
                    f"{item.component}'s IC is dated {item.prediction_day.isoformat()}, which is "
                    "not a session of the calendar the trailing window is counted on"
                )
            if (item.component, item.prediction_day) in self._seen:
                raise StrategyBacktestError(
                    f"{item.component} carries two ICs on {item.prediction_day.isoformat()}"
                )
            self._seen.add((item.component, item.prediction_day))
            positions = self._positions[item.component]
            at = bisect_right(positions, position)
            positions.insert(at, position)
            self._entries[item.component].insert(at, (position, item.known_at, item.ic))

    def weights(self, day: date, *, instant: datetime) -> tuple[TrailingICWeight, ...]:
        """Each component's weight on `day`: the mean of the ICs in its window known by `instant`.

        The window is the `ic_window_sessions` calendar sessions ending at `day`; an IC counts
        only when its `known_at` is AT OR BEFORE the signal instant -- the look-ahead rule, and
        the one line below that enforces it.
        """
        position = self._position.get(day)
        if position is None:
            raise StrategyBacktestError(f"{day.isoformat()} is not a session of the calendar")
        spec = self._spec
        first = position - spec.ic_window_sessions + 1
        answer: list[TrailingICWeight] = []
        for key, rows in self._entries.items():
            positions = self._positions[key]
            window = rows[bisect_left(positions, first) : bisect_right(positions, position)]
            known = [
                ic for _position, known_at, ic in window if ic is not None and known_at <= instant
            ]
            mean = statistics.fmean(known) if known else None
            if mean is None or len(known) < spec.min_ic_observations:
                weight = 0.0
            elif spec.negative_ic == "clip_to_zero":
                weight = max(mean, 0.0)
            else:
                weight = mean
            answer.append(
                TrailingICWeight(
                    component=key, observations=len(known), mean_ic=mean, weight=weight
                )
            )
        return tuple(answer)


def trailing_ic_weights(
    spec: TrailingICWeights,
    observations: Sequence[ICObservation],
    *,
    calendar: Sequence[date],
    signal_day: date,
    instant: datetime,
) -> tuple[TrailingICWeight, ...]:
    """Each declared component's trailing-IC weight on one signal day, in declared order."""
    return _ICIndex(spec, observations, calendar=calendar).weights(signal_day, instant=instant)


# --- walk-forward fits (V2-P6-014) --------------------------------------------------------------


def walk_forward_fits(
    model: AlphaModel,
    examples: Sequence[TrainingExample],
    *,
    feature_ids: tuple[str, ...],
    spec: WalkForwardModel,
    calendar: Sequence[date],
    instants: Mapping[date, datetime],
    refit_days: Sequence[date],
) -> tuple[WalkForwardFit, ...]:
    """Fit `model` once per refit session, each on labels known before its embargo deadline.

    A refit on `r` takes the examples whose prediction day is among the `train_sessions`
    calendar sessions ending at `r` and whose label is known -- the signal instant of the session
    its window exits on -- STRICTLY BEFORE the signal instant of the session `embargo_sessions`
    before `r`. A refit with no such example, or one the model refuses, is a `WalkForwardFit`
    carrying the refusal rather than an exception: it is a fact about that point in history.
    """
    position = {day: index for index, day in enumerate(calendar)}
    by_position: dict[int, list[TrainingExample]] = {}
    for example in examples:
        day = example.label.window.prediction_day
        if day not in position:
            raise StrategyBacktestError(
                f"a training example is dated {day.isoformat()}, which is not a session of the "
                "calendar the training window is counted on"
            )
        by_position.setdefault(position[day], []).append(example)
    fits: list[WalkForwardFit] = []
    for refit_day in refit_days:
        if refit_day not in position:
            raise StrategyBacktestError(f"refit day {refit_day.isoformat()} is not a session")
        at = position[refit_day]
        if at - spec.embargo_sessions < 0:
            fits.append(_refused(refit_day, "the calendar does not reach the embargo deadline"))
            continue
        deadline = instants[calendar[at - spec.embargo_sessions]]
        chosen: list[tuple[TrainingExample, datetime]] = []
        for index in range(max(0, at - spec.train_sessions + 1), at + 1):
            for example in by_position.get(index, ()):
                known = _label_known_at(example, calendar=calendar, instants=instants)
                if known is not None and known < deadline:
                    chosen.append((example, known))
        if not chosen:
            fits.append(
                _refused(
                    refit_day,
                    f"no training example in the {spec.train_sessions} sessions ending "
                    f"{refit_day.isoformat()} had a label known before {deadline.isoformat()}",
                )
            )
            continue
        try:
            fitted = model.fit(
                TrainingSet(
                    feature_ids=feature_ids, examples=tuple(example for example, _ in chosen)
                )
            )
        except AlphaModelError as error:
            fits.append(_refused(refit_day, f"the model refused the fit: {error}"))
            continue
        fits.append(
            WalkForwardFit(
                refit_day=refit_day,
                fitted=fitted,
                artifact=fitted.artifact,
                refusal=None,
                labels_known_at=max(known for _, known in chosen),
                example_count=len(chosen),
                prediction_day_count=len(
                    {example.label.window.prediction_day for example, _ in chosen}
                ),
            )
        )
    return tuple(fits)


def _refused(refit_day: date, reason: str) -> WalkForwardFit:
    return WalkForwardFit(
        refit_day=refit_day,
        fitted=None,
        artifact=None,
        refusal=reason,
        labels_known_at=None,
        example_count=0,
        prediction_day_count=0,
    )


def _label_known_at(
    example: TrainingExample, *, calendar: Sequence[date], instants: Mapping[date, datetime]
) -> datetime | None:
    """The signal instant of the session `example`'s window exits on; `None` past the calendar."""
    exit_day = example.label.window.exit_day
    known = instants.get(exit_day)
    if known is None:
        if exit_day > calendar[-1]:
            return None
        raise StrategyBacktestError(
            f"{exit_day.isoformat()}, the exit of a training label, has no signal instant"
        )
    return known


def usable_fit(fits: Sequence[WalkForwardFit], *, signal_day: date) -> WalkForwardFit | None:
    """The newest successful fit refitted on or before `signal_day`, or `None` (no fit yet)."""
    candidates = [fit for fit in fits if fit.fitted is not None and fit.refit_day <= signal_day]
    return max(candidates, key=lambda fit: fit.refit_day, default=None)


# --- the book -----------------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class _Holding:
    shares: int
    opened: date
    entry_adj: Decimal
    mark: Decimal
    """The last close times its adjustment factor, times `correction`."""
    correction: Decimal = Decimal(1)
    """What the adjustment factor is rescaled by after a session whose recorded path is the
    published one (`V2-P6-020`): the product of those sessions' `path_ratio`s; exactly `1` until
    one is crossed."""
    observed: date | None = None
    """The newest session whose recorded path this holding has taken into account."""

    def value(self) -> Decimal:
        return (self.shares * self.mark / self.entry_adj).quantize(_CENT, rounding=ROUND_HALF_UP)

    def adjusted(self, quote: SessionQuote) -> Decimal:
        """The session's adjustment factor on this holding's own scale."""
        return quote.adj_factor * self.correction


@dataclass(slots=True, kw_only=True)
class _Book:
    spec: StrategySpec
    cash: Decimal
    holdings: dict[str, _Holding] = field(default_factory=dict)
    unknowable: list[UnknowableCrossing] = field(default_factory=list)
    """Every session a held position crossed with no decided return, in the order met."""
    period_start: date | None = None
    start_value: Decimal = _ZERO_MONEY
    """The current period's signal day and starting value, which a crossing's share is over."""

    def value(self) -> Decimal:
        return self.cash + sum((holding.value() for holding in self.holdings.values()), _ZERO_MONEY)

    def observe(self, holding: _Holding, quote: SessionQuote) -> Decimal | None:
        """Take a session's recorded path into account, once, before its first price is used.

        Only for a position held into the session -- opened on an earlier one -- because the
        disagreement is about the overnight link from the previous close, which a position
        bought at this session's open never held. On `published` the correction is multiplied
        by the session's own `path_ratio`, which makes this session's gross return
        `close / pre_close` and rescales every later mark by the same factor. On `unknowable`
        nothing is rescaled, and the ratio is returned so the caller can price the crossing once
        it knows what it booked.
        """
        day = quote.bar.trade_date
        if holding.opened >= day or holding.observed == day:
            return None
        holding.observed = day
        if quote.recorded_path == "published" and quote.path_ratio is not None:
            holding.correction *= quote.path_ratio
        elif quote.recorded_path == "unknowable":
            return quote.path_ratio
        return None

    def cross(self, subject: str, day: date, *, held_value: Decimal, ratio: Decimal) -> None:
        """Name one unknowable crossing on the run, priced against the published path."""
        difference = (held_value * (ratio - 1)).quantize(_CENT, rounding=ROUND_HALF_UP)
        self.unknowable.append(
            UnknowableCrossing(
                subject=subject,
                day=day,
                period_start=cast(date, self.period_start),
                held_value=held_value,
                valuation_difference=difference,
                share_of_book=_quantized(difference / self.start_value),
            )
        )

    def mark(self, day_quotes: Mapping[str, SessionQuote]) -> None:
        for subject, holding in self.holdings.items():
            quote = day_quotes.get(subject)
            if quote is not None:
                ratio = self.observe(holding, quote)
                holding.mark = quote.bar.close * holding.adjusted(quote)
                if ratio is not None:
                    self.cross(
                        subject, quote.bar.trade_date, held_value=holding.value(), ratio=ratio
                    )


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
    signal: _Signal,
) -> PeriodResult:
    sessions = inputs.sessions
    signal_day = sessions[signal_index]
    trade_day = sessions[signal_index + 1]
    book.mark(inputs.quotes.get(signal_day, {}))
    crossed = len(book.unknowable)
    start_value = book.value()
    if start_value <= 0:
        raise StrategyBacktestError(f"the book is worth {start_value} on {signal_day.isoformat()}")
    book.period_start, book.start_value = signal_day, start_value
    ledger = _Ledger()
    if signal.ranked is not None:
        keep, buy = _decide(inputs, spec, book, signal_day=signal_day, ranked=signal.ranked)
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
        held=signal.ranked is None,
        ic_weights=signal.ic_weights,
        model_fit=signal.model_fit,
        unknowable_sessions=tuple(
            f"{item.subject}@{item.day.isoformat()}" for item in book.unknowable[crossed:]
        ),
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
    keep, buy = target_holdings(
        ranked,
        held=book.holdings,
        spec=spec,
        industries=inputs.industries.get(signal_day, {}),
    )
    return set(keep), list(buy)


def target_holdings(
    ranked: Sequence[str],
    *,
    held: Collection[str],
    spec: StrategySpec,
    industries: Mapping[str, str],
) -> tuple[frozenset[str], tuple[str, ...]]:
    """The book's rebalance rule on one signal day: which held names stay, and what it buys.

    A held name stays while its rank is within `buffer_rank` (or `holding_count` without a band);
    the free slots are filled in rank order with names not already held, skipping a name whose
    industry already fills `industry_slots` when industries are capped (an unclassified name
    counts under one shared industry). The buy list is at most the free slots long; the book then
    buys from it in order while it has slots and cash. `run_strategy_backtest` decides every
    rebalance through this function, and the daily command (`V2-P6-011`) decides today's targets
    through it, so the two cannot come to hold different books from one ranking.
    """
    band = spec.buffer_rank if spec.buffer_rank is not None else spec.holding_count
    rank = {subject: position for position, subject in enumerate(ranked, start=1)}
    keep = {subject for subject in held if rank.get(subject, band + 1) <= band}
    counts: dict[str, int] = {}
    for subject in keep:
        industry = industries.get(subject, _UNCLASSIFIED)
        counts[industry] = counts.get(industry, 0) + 1
    wanted = spec.holding_count - len(keep)
    buy: list[str] = []
    for subject in ranked:
        if len(buy) >= wanted:
            break
        if subject in held:
            continue
        if spec.max_industry_weight is not None:
            industry = industries.get(subject, _UNCLASSIFIED)
            if counts.get(industry, 0) >= spec.industry_slots:
                continue
            counts[industry] = counts.get(industry, 0) + 1
        buy.append(subject)
    return frozenset(keep), tuple(buy)


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
    ratio = book.observe(holding, quote)
    proceeds = (quantity * bar.open * holding.adjusted(quote) / holding.entry_adj).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )
    if ratio is not None:
        # The whole position held into the session, at the open it was sold at -- a capped sale
        # sells part of it, and `observe` has already marked the session seen, so the shares kept
        # would otherwise never be counted (review round 2, Minor 2).
        held = (holding.shares * bar.open * holding.adjusted(quote) / holding.entry_adj).quantize(
            _CENT, rounding=ROUND_HALF_UP
        )
        book.cross(subject, day, held_value=held, ratio=ratio)
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
