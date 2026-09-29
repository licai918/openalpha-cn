"""The strategy backtest's panel side: read the stored panel into `StrategyInputs`, run, render.

`V2-P6-007`. `backtest/strategy_backtest.py` is the book and reads nothing;
`backtest-no-numeric-stack-or-panel-plane` forbids it every `panel*` module. This module is the
other half, and it is a sixth top-level research-plane family (`strategy_*`) for `model_view`'s
reason: it joins the panel plane to a `backtest/` study, which only a top-level module may do.
`openalpha strategy backtest` and `OpenAlphaSDK.run_strategy_backtest` both resolve through
`strategy_request` and run through `backtest_strategy`, so the two faces ask one question.

## What is read, and at which instant

Everything is read at the request's `as_of` -- the instant the backtest is evaluated at, at or
after the last session's publication instant -- through the same gated loaders `factor run`
uses. That is safe for the price side because the book only ever reads a session's bar on or
after that session. What must be point-in-time relative to each **signal** is decided finer:

- **Scores.** Every stored cross section carries its build's `as_of` on all four clocks, so each
  one is handed to the book with `available_time = revision_time = ` that `as_of`, filed under
  the date it was built on in Asia/Shanghai. A signal day with several builds gets the latest
  one at or before its signal instant; one with only later builds gets the earliest, which the
  book then refuses as look-ahead rather than this module quietly dropping it.
- **Signal instants** are `panel_ingest.session_publication_instant`, not a restated 16:30.
- **Industries** (only when `max_industry_weight` is declared) are read per signal day at that
  day's signal instant through `load_industry_cross_section`, the as-of-sensitive door, in the
  taxonomy in force that day (`V2-P6-015`): SW2014 level one through 2021-12-10, SW2021 from
  2021-12-13. A cap is per day, so it never weighs one day's groups against another's.
- **Turnover** for the participation cap is the signal session's own `daily.amount`, converted
  from thousands of yuan by `factor_tradeability.liquidity_from_amount`'s constant.

Values are oriented here, once: a `lower_is_better` factor's stored value is negated so every
component the book combines is higher-is-better. Only values the tier admits
(`factor_ic.TIER_ADMITTED_CODES`) become rows; an imputed processed value is never a score.

## The two dynamic sources (`V2-P6-014`) reuse the planes that already price outcomes

Both need history in front of `--start` -- a trailing window or a training window -- and read
it from the contiguous run of registered `trade_cal` years before the start year, no further
back than the window needs (`_lookback`). Their forward returns are priced by
`model_view.OutcomeLabels`, the label reader `model evaluate` and `model daily-run` use and the
same `build_label_window` + `label_outcome` construction `factor run` uses, so no return here is
derived a third way.

- **Trailing IC** (`_ic_observations`). Each prediction day's IC is `factor_ic.ic_cross_section`
  over the tier's stored build visible at that day's signal instant (the build `_chosen_build`
  picks) against its labels, measured by `FactorICStudy` under the declared method and floor.
  Each carries `known_at = max(build instant, signal instant of the session its label window
  exits on)`; the book decides which of them a signal day may use.
- **Walk-forward model** (`_model_rows`). The declared features resolve through
  `model_view.feature_columns` and the family through `MODEL_FAMILIES`; the training panel is
  `model_view.training_panel` (`run_daily`'s assembly), each refit is
  `strategy_backtest.walk_forward_fits`, and the cross section a fit scores on a signal day is
  `model_view.feature_cross_section` at that day's signal instant.

## Quotes are built lazily

A multi-year whole-market run is millions of `(session, security)` pairs and the book touches a
few dozen per rebalance. `StrategyInputs.quotes` is therefore a mapping that reads one session's
bars and bands when first asked and builds a `SessionQuote` only for the securities the book
asks about, keeping a small window of recent sessions cached. The equal-weight benchmark reads
the same cached sessions. A session without a published band, or a security without an
adjustment factor covering it, has no quote: the book cannot trade it and keeps its last mark.

## A registered prediction registered late is read only when it is witnessed (`V2-P6-011`)

A source of `prediction_ids` reads records the daily command filed at about 18:30, after the
16:30 signal instant. `_prediction_rows` reads a record one of two ways:

- **registered at or before its signal instant** (`max(predicted_at, recorded_at)`): nothing
  later existed yet, and the row carries that custody stamp as its clocks -- the book's own rule
  for every score row, unchanged;
- **registered after it**, which the book's rule alone would refuse: the batch's `as_of` -- the
  instant its inputs were read -- is only what its writer declares, and a record fixed at 18:30
  or the next morning could carry what arrived since. It is read only when (1) it was registered
  before 09:15 on the session that trades it (`REGISTRATION_CUTOFF`: the call auction that prices
  the book's trade has not begun), and (2) the caller's `verify_late` -- the registration's check,
  `strategy_registration.late_record_check` -- admits it: bound to the registered declaration,
  and its scores recomputed equal from the stored builds, which are point-in-time gated at
  `as_of`. Then its rows carry `as_of` as their clocks (a record is never revised). Without a
  check, or refused by it, the book is refused by name.
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from array import array
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from functools import partial
from itertools import pairwise
from typing import Any, ClassVar, Final, Literal, Protocol, TypeVar
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.execution import (
    CostSchedule,
    MarketBar,
    published_limit_fields,
    suspended_at_the_close,
)
from openalpha_cn.backtest.factor_ic import (
    IC_METHODS,
    MINIMUM_IC_AS_OFS,
    MINIMUM_IC_SECURITIES,
    TIER_ADMITTED_CODES,
    FactorICError,
    FactorICSpec,
    FactorICStudy,
    ICCensus,
    ICMethod,
    ICPoint,
    ic_cross_section,
)
from openalpha_cn.backtest.factor_tradeability import CNY_PER_TURNOVER_UNIT
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A,
    KNOWN_STRATEGY_BACKTEST_LIMITATIONS,
    MODEL_COMPONENT,
    PREDICTION_COMPONENT,
    STRATEGY_TIERS,
    ICObservation,
    ScoreFeed,
    ScoreRow,
    ScoreSource,
    SessionQuote,
    SignalDayInputs,
    SignalDayScores,
    StrategyBacktest,
    StrategyBacktestError,
    StrategyInputs,
    StrategySpec,
    TrailingICWeights,
    WalkForwardFit,
    WalkForwardModel,
    component_key,
    rebalance_indices,
    run_strategy_backtest,
    score_signal_day,
    usable_fit,
    walk_forward_fits,
)
from openalpha_cn.domain.adjustment import (
    ADJ_FACTOR_DATASET,
    AdjustmentHistory,
    AdjustmentHorizonError,
)
from openalpha_cn.domain.alpha_model import (
    AlphaModel,
    AlphaModelDeclaration,
    AlphaModelError,
    PredictionBatch,
    TrainingExample,
)
from openalpha_cn.domain.daily_prices import DAILY_DATASET, DailyBar, PriceDataError
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.factor_transform import FactorTransformRegistry, FactorTransformSpec
from openalpha_cn.domain.horizon import ResearchHorizon, parse_horizon
from openalpha_cn.domain.index_prices import IndexPriceError, index_session_returns
from openalpha_cn.domain.industry_classification import (
    IndustryClassificationError,
    IndustryHorizonError,
    industry_membership_source_on,
)
from openalpha_cn.domain.labels import (
    HaltCorpus,
    OutcomeLabel,
    WindowReturn,
    halt_corpus_for_years,
)
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.price_limits import (
    SUSPENSION_DATASET,
    PriceLimit,
    TradingState,
)
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.factor_view import FactorRequestError, resolve_factor
from openalpha_cn.feature_matrix import FeatureColumn, FeatureMatrixError, feature_spec
from openalpha_cn.model_view import (
    MODEL_FAMILIES,
    UNFILED_CONFIG_DIGEST,
    LabelReach,
    ModelRequestError,
    ModelRunRequest,
    ModelViewError,
    OutcomeLabels,
    declared_hyperparameters,
    feature_columns,
    feature_cross_section,
    training_panel,
)
from openalpha_cn.panel.catalog import DEFAULT_DATE_TIMEZONE, PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_factors import (
    FACTOR_TRANSFORMS,
    FactorEngineError,
    factor_manifest_dataset,
    factor_observation_dataset,
    factor_transform_manifest_dataset,
    load_factor_observations,
    load_processed_factor_observations,
    processed_factor_dataset,
)
from openalpha_cn.panel_ingest import (
    UPSTREAM_DEFECTS_DATASET,
    load_adjustment_histories,
    load_daily_bars,
    load_index_prices,
    load_industry_cross_section,
    load_price_limits,
    load_suspensions,
    load_trading_calendar,
    session_publication_instant,
)
from openalpha_cn.panel_neutralization import (
    FACTOR_NEUTRALIZATIONS,
    NeutralizationEngineError,
    factor_neutralization_manifest_dataset,
    load_neutralized_factor_observations,
    neutralized_factor_dataset,
)
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER, without_store_path

__all__ = [
    "PROTOCOL_BENCHMARKS",
    "PROTOCOL_COSTS",
    "PROTOCOL_PARTICIPATION_CAP",
    "PROTOCOL_POSITION_CAPITAL",
    "PROTOCOL_SLIPPAGE_RATE",
    "REGISTRATION_CUTOFF",
    "ICSeries",
    "ICSeriesPoint",
    "ICSeriesRequest",
    "SignalDay",
    "StrategyPanelUnreadableError",
    "StrategyRequest",
    "StrategyRequestError",
    "StrategyRunBlockedError",
    "StrategyViewError",
    "backtest_strategy",
    "backtest_view",
    "factor_ic_series",
    "ic_series_request",
    "ic_series_view",
    "input_datasets",
    "load_strategy_inputs",
    "registration_deadline",
    "score_day",
    "strategy_request",
]

SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)

REGISTRATION_CUTOFF: Final[time] = time(9, 15)
"""The latest instant (Shanghai) on the session that trades a signal day's scores at which they
may have been registered.

The book trades them at that session's open, and the opening price is fixed by the call auction
that starts at 09:15: from then on an order placed on them is no longer the order the book
assumes, and the auction's indicative price is already a published piece of the outcome.
`strategy_registration` re-exports it for the daily command, which refuses to file after it.
"""


LABEL_INPUTS: Final[tuple[str, ...]] = (
    DAILY_DATASET,
    ADJ_FACTOR_DATASET,
    SUSPENSION_DATASET,
    UPSTREAM_DEFECTS_DATASET,
)
"""What a label is priced from, beside the factor builds, for the two sources that read labels
(trailing IC, walk-forward): the price base, the adjustment factors, the halts, and the recorded
decisions a return path follows (`upstream_defects`: `V2-P6-013`'s drops today, `V2-P6-020`'s
return-path decisions where that is merged)."""


def _tier_datasets(definition: FactorDefinition, tier: str) -> tuple[str, str]:
    """The observation and manifest datasets one factor tier is read from."""
    if tier == "processed":
        return processed_factor_dataset(definition), factor_transform_manifest_dataset(definition)
    if tier == "neutralized":
        return (
            neutralized_factor_dataset(definition),
            factor_neutralization_manifest_dataset(definition),
        )
    return factor_observation_dataset(definition), factor_manifest_dataset(definition)


def input_datasets(request: StrategyRequest) -> tuple[str, ...]:
    """The stored datasets scoring one day of `request`'s source reads (`V2-P6-011` round 12).

    Derived from the source, for an input provenance: a correction to anything else is not a
    correction of what the day read, and must not make a record's mismatch look explained.

    - **static**: each component's factor tier and its build manifests -- the day's score rows;
    - **trailing IC**: the same for its components, and `LABEL_INPUTS`, which its ICs are priced
      from;
    - **walk-forward**: each declared feature's tier and manifests, and `LABEL_INPUTS`, which its
      training labels are priced from.
    """
    source = request.source
    pairs: list[tuple[FactorDefinition, str]]
    if source.walk_forward is not None:
        pairs = [(column.definition, column.tier) for column in request.columns]
    elif source.trailing_ic is not None:
        pairs = [
            (request.definitions[token], tier) for token, tier in source.trailing_ic.components
        ]
    else:
        pairs = [(request.definitions[token], tier) for token, tier, _ in source.components]
    factors = tuple(
        dict.fromkeys(
            dataset
            for definition, tier in sorted(pairs, key=lambda pair: (pair[0].qualified_key, pair[1]))
            for dataset in _tier_datasets(definition, tier)
        )
    )
    labels = LABEL_INPUTS if source.walk_forward is not None or source.trailing_ic else ()
    return (*factors, *labels)


def registration_deadline(trading_session: date) -> datetime:
    """09:15 Shanghai on `trading_session`: a record of the signal day before it registered at or
    after this instant is not a prediction made before the trade it drives."""
    return datetime.combine(trading_session, REGISTRATION_CUTOFF, SHANGHAI)


PROTOCOL_POSITION_CAPITAL: Final[Decimal] = Decimal("100000")
"""The research protocol's per-position capital (section 2, a measurement setting)."""
PROTOCOL_PARTICIPATION_CAP: Final[Decimal] = Decimal("0.01")
"""The research protocol's turnover participation cap: 1% of the signal session's turnover."""
PROTOCOL_COSTS: Final[CostSchedule] = CostSchedule(
    commission_rate=Decimal("0.00025"),
    minimum_commission=Decimal("5.00"),
    transfer_fee_rate=Decimal("0"),
    sell_stamp_duty_rate=Decimal("0.0005"),
)
"""The protocol's costs: commission 2.5bp both sides with a 5-yuan minimum, stamp duty 0.5 per
mille on sells. The protocol names no transfer fee, so none is charged."""
PROTOCOL_SLIPPAGE_RATE: Final[Decimal] = Decimal("0.001")
"""The protocol's slippage: 10bp per side."""
PROTOCOL_BENCHMARKS: Final[tuple[str, ...]] = ("000905.SH", EQUAL_WEIGHT_ALL_A)
"""The protocol's two benchmarks, side by side: 中证500 and the all-A equal-weight series."""

_CACHED_SESSIONS: Final[int] = 8
"""How many sessions' bars and bands stay in memory. The book walks sessions in order and asks
about the signal session, the next one and each marked session, so a small window suffices."""

_ADJUSTMENT_YEARS_HELD: Final[int] = 2
"""How many quote years' adjustment histories stay resident: a period spans at most two years."""

_T = TypeVar("_T")


class StrategyViewError(RuntimeError):
    """Base for every fault a strategy face reports; `reason` picks the envelope row."""

    reason: ClassVar[str] = "strategy_view_error"

    def __init__(self, message: str, *, disclosable: str | None = None) -> None:
        super().__init__(message)
        self.disclosable: str = message if disclosable is None else disclosable


class StrategyRequestError(StrategyViewError):
    """The question cannot be put at all, whatever the store holds."""

    reason: ClassVar[str] = "bad_request"


class StrategyPanelUnreadableError(StrategyViewError):
    """A stored partition the backtest needs could not be read at the requested instant."""

    reason: ClassVar[str] = "panel_unreadable"


class StrategyRunBlockedError(StrategyViewError):
    """The store was read and the backtest refused what it held (look-ahead, gaps, ...)."""

    reason: ClassVar[str] = "blocked"


_PANEL_FAULTS: Final[tuple[type[Exception], ...]] = (
    PanelStorageError,
    FactorEngineError,
    NeutralizationEngineError,
    TradingCalendarError,
    PriceDataError,
    IndexPriceError,
    IndustryClassificationError,
)
"""Refusals that are statements about stored data, enveloped as `panel_unreadable`."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StrategyRequest:
    """One resolved backtest: what scores, which rules, which sessions, read at which instant."""

    source: ScoreSource
    spec: StrategySpec
    definitions: Mapping[str, FactorDefinition]
    """Each component's factor token, resolved to its definition."""
    transform: FactorTransformSpec | None
    neutralization: FactorNeutralizationSpec | None
    start: date
    end: date
    as_of: datetime
    exchange: str
    columns: tuple[FeatureColumn, ...] = ()
    """A walk-forward source's declared features, resolved by `model_view.feature_columns`."""
    model: AlphaModel | None = None
    """A walk-forward source's unfitted model, built from `MODEL_FAMILIES` at request time."""
    rebalance_days: tuple[date, ...] | None = None
    """The sessions the book rebalances on, replacing the fixed grid; `None` is the grid
    (`strategy_backtest.rebalance_indices`, `V2-P6-011`)."""

    @property
    def years(self) -> tuple[int, ...]:
        """Every partition year the range touches, ascending."""
        return tuple(range(self.start.year, self.end.year + 1))


def strategy_request(
    *,
    components: Sequence[tuple[str, str, Decimal]],
    combine: str,
    prediction_ids: Sequence[str] = (),
    transform: str | None,
    neutralization: str | None,
    start: date,
    end: date,
    as_of: datetime,
    exchange: str,
    rebalance_every_sessions: int,
    holding_count: int,
    buffer_rank: int | None,
    max_industry_weight: Decimal | None,
    position_capital: Decimal = PROTOCOL_POSITION_CAPITAL,
    participation_cap: Decimal = PROTOCOL_PARTICIPATION_CAP,
    costs: CostSchedule = PROTOCOL_COSTS,
    slippage_rate: Decimal = PROTOCOL_SLIPPAGE_RATE,
    benchmarks: Sequence[str] = PROTOCOL_BENCHMARKS,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
    trailing_ic: TrailingICWeights | Mapping[str, object] | None = None,
    walk_forward: WalkForwardModel | Mapping[str, object] | None = None,
    rebalance_days: Sequence[date] | None = None,
) -> StrategyRequest:
    """Resolve one face's parameters into the request both faces ask. Touches no store.

    The measurement settings default to the research protocol's (section 2 of the selection-ready
    plan) because the protocol fixes them once and forbids searching them; every one of them is
    echoed back on the answer's `spec`. The portfolio rules have no default.

    `transform` is required exactly when a component reads the `processed` or `neutralized`
    tier, `neutralization` exactly when one reads `neutralized`, and both apply to every
    component on those tiers. The two registries default to the build's own, which is what both
    faces resolve against; they are parameters so a study over a probe transform or
    neutralisation can be driven without a second resolver -- `factor_view.factor_request`'s
    arrangement and its reason.

    `trailing_ic` and `walk_forward` (`V2-P6-014`) are the two dynamic kinds, as a model or as
    plain mappings a research grid can pass. A trailing-IC source's components obey the
    transform rules above; a walk-forward source's features name their own transform, so the
    two request-level ones are refused beside it, and its features, family and hyperparameters
    are resolved here -- a neutralized feature, an unknown family or a hyperparameter the family
    refuses is `bad_request` before any store is opened.

    `rebalance_days` (`V2-P6-011`) replaces the fixed grid with named sessions -- the days a daily
    command actually rebalanced on (`scripts/daily_selection.journalled_rebalances`). Omitted,
    the grid runs and every existing answer is unchanged. They are checked against the range's
    sessions when the store is read (`strategy_backtest.rebalance_indices`).
    """
    try:
        source = ScoreSource.model_validate(
            {
                "components": tuple(
                    (token.strip(), tier, weight) for token, tier, weight in components
                ),
                "combine": combine,
                "prediction_ids": tuple(prediction_ids),
                "trailing_ic": trailing_ic,
                "walk_forward": walk_forward,
            }
        )
        spec = StrategySpec(
            rebalance_every_sessions=rebalance_every_sessions,
            holding_count=holding_count,
            buffer_rank=buffer_rank,
            max_industry_weight=max_industry_weight,
            position_capital=position_capital,
            participation_cap=participation_cap,
            costs=costs,
            slippage_rate=slippage_rate,
            benchmarks=tuple(benchmarks),
        )
    except ValueError as error:
        raise StrategyRequestError(str(error)) from error
    factor_tiers: tuple[tuple[str, str], ...] = (
        source.trailing_ic.components
        if source.trailing_ic is not None
        else tuple((token, tier) for token, tier, _ in source.components)
    )
    try:
        definitions = {token: resolve_factor(token) for token, _ in factor_tiers}
    except FactorRequestError as error:
        raise StrategyRequestError(str(error)) from error
    columns: tuple[FeatureColumn, ...] = ()
    model: AlphaModel | None = None
    if source.walk_forward is not None:
        if transform is not None or neutralization is not None:
            raise StrategyRequestError(
                "--transform and --neutralization are refused beside a walk-forward source: "
                "each feature names its own transform as <factor>@<tier>:<transform>"
            )
        columns, model = _walk_forward_model(
            source.walk_forward, transforms=transforms, neutralizations=neutralizations
        )
    tiers = {tier for _, tier in factor_tiers}
    needs_transform = bool(tiers & {"processed", "neutralized"})
    if needs_transform != (transform is not None):
        raise StrategyRequestError(
            "--transform is required when a component reads the processed or neutralized tier "
            "and refused otherwise; a transform no component reads is a declaration nothing "
            "checks"
        )
    if ("neutralized" in tiers) != (neutralization is not None):
        raise StrategyRequestError(
            "--neutralization is required when a component reads the neutralized tier and "
            "refused otherwise"
        )
    try:
        transform_spec = None if transform is None else transforms.get(transform.strip())
        neutralization_spec = (
            None if neutralization is None else neutralizations.get(neutralization.strip())
        )
    except ValueError as error:
        raise StrategyRequestError(str(error)) from error
    if end <= start:
        raise StrategyRequestError(
            f"--start {start.isoformat()} must be before --end {end.isoformat()}; a backtest "
            "needs a signal session and a later one to trade on"
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise StrategyRequestError(f"--as-of must be timezone-aware; got {as_of.isoformat()!r}")
    published = session_publication_instant(end)
    if as_of < published:
        raise StrategyRequestError(
            f"--as-of {as_of.isoformat()} is before {end.isoformat()}'s publication instant "
            f"{published.isoformat()}; the last session's close is not knowable yet"
        )
    if not exchange or exchange != exchange.strip():
        raise StrategyRequestError(f"exchange must be a non-empty name; got {exchange!r}")
    return StrategyRequest(
        source=source,
        spec=spec,
        definitions=definitions,
        transform=transform_spec,
        neutralization=neutralization_spec,
        start=start,
        end=end,
        as_of=as_of,
        exchange=exchange,
        columns=columns,
        model=model,
        rebalance_days=None if rebalance_days is None else tuple(rebalance_days),
    )


WALK_FORWARD_MODEL_NAME: Final[str] = "strategy_walk_forward"
"""The `AlphaModelDeclaration.name` every walk-forward source's fits are declared under."""


def _feature_mapping(token: str) -> dict[str, str]:
    """`<factor>@<tier>[:<transform>[:<neutralization>]]` as `feature_columns`' mapping."""
    factor, _, rest = token.strip().partition("@")
    tier, *specs = rest.split(":")
    if len(specs) > 2:
        raise StrategyRequestError(
            f"feature {token!r} names more than a transform and a neutralization"
        )
    mapping = {"factor": factor, "tier": tier}
    mapping.update(zip(("transform", "neutralization"), specs, strict=False))
    return mapping


def _walk_forward_model(
    spec: WalkForwardModel,
    *,
    transforms: FactorTransformRegistry,
    neutralizations: FactorNeutralizationRegistry,
) -> tuple[tuple[FeatureColumn, ...], AlphaModel]:
    """The declared features and the unfitted model, resolved by the model faces' own tables.

    `feature_columns` is the one resolver `model evaluate` and `model daily-run` share, and it
    refuses a neutralized-tier feature; see
    `a_walk_forward_model_reads_no_neutralized_feature` for why that refusal stays here.
    """
    try:
        columns = feature_columns(
            [_feature_mapping(token) for token in spec.features],
            transforms=transforms,
            neutralizations=neutralizations,
        )
    except ModelRequestError as error:
        raise StrategyRequestError(str(error)) from error
    if spec.family not in MODEL_FAMILIES:
        raise StrategyRequestError(
            f"{spec.family!r} is not a model family this build can fit; it answers to "
            f"{sorted(MODEL_FAMILIES)}"
        )
    try:
        declaration = AlphaModelDeclaration(
            name=WALK_FORWARD_MODEL_NAME,
            family=spec.family,
            horizon=f"{spec.horizon_sessions}d",
            feature_version=feature_spec(columns=columns, missing=spec.missing).feature_version,
            seed=spec.seed,
            code_commit=spec.code_commit,
            hyperparameters=declared_hyperparameters(spec.hyperparameters),
        )
        return columns, MODEL_FAMILIES[spec.family](declaration=declaration)
    except (FeatureMatrixError, AlphaModelError, ValueError) as error:
        raise StrategyRequestError(f"the walk-forward model cannot be declared: {error}") from error


def _read(reader: Callable[[], _T], *, store: PanelStore, what: str) -> _T:
    """Run one panel read, turning a refusal about stored data into `panel_unreadable`."""
    try:
        return reader()
    except _PANEL_FAULTS as error:
        raise StrategyPanelUnreadableError(
            f"{what} could not be read out of {store.root}: {error}",
            disclosable=(
                f"{what} could not be read out of {PANEL_STORE_PLACEHOLDER}: "
                f"{without_store_path(str(error), store.root)}"
            ),
        ) from error


def backtest_strategy(
    store: PanelStore,
    request: StrategyRequest,
    *,
    predictions: Callable[[str], PredictionRecord | None] | None = None,
    verify_late: Callable[[PredictionRecord], str | None] | None = None,
) -> StrategyBacktest:
    """Read the panel into `StrategyInputs` and run the book: the one entry both faces call.

    `predictions` looks a registered prediction up by id; it is required exactly when the
    source names `prediction_ids`. `verify_late` is the check a record registered after its
    signal instant must pass; without one such a record is refused (see the module docstring).
    A `StrategyBacktestError` -- look-ahead, a signal day with no
    cross section, a benchmark gap -- is `blocked`, and that holds for one raised while the
    inputs are ASSEMBLED as much as for one raised while the book runs: a stored score that
    `ScoreRow`'s own contract refuses (a non-finite value, a naive clock) is the same kind of
    refusal and must not reach a face as an unanticipated error.

    The run is STREAMED (`V2-P6-014`): the book asks for each signal day's scores when it books
    that period, so memory is bounded by a window rather than by the range.
    """
    try:
        inputs = load_strategy_inputs(
            store, request, predictions=predictions, verify_late=verify_late, stream=True
        )
        return _read(
            lambda: run_strategy_backtest(inputs, request.spec),
            store=store,
            what="a session the backtest walked",
        )
    except StrategyBacktestError as error:
        raise StrategyRunBlockedError(f"the backtest refused its inputs: {error}") from error


def load_strategy_inputs(
    store: PanelStore,
    request: StrategyRequest,
    *,
    predictions: Callable[[str], PredictionRecord | None] | None = None,
    verify_late: Callable[[PredictionRecord], str | None] | None = None,
    stream: bool = False,
) -> StrategyInputs:
    """Everything the book reads, out of the panel, at `request.as_of`.

    With `stream=True` the scores stay behind a `ScoreFeed` (`_FactorFeed` or `_ModelFeed`) and
    are read as the book asks for them, which is how `backtest_strategy` runs. With the default
    the same feed is drained into `StrategyInputs`' four score fields -- the materialised run, the
    same answer held all at once. A registered-prediction source is always materialised: its
    batches are already in memory.
    """
    calendar = _read(
        lambda: load_trading_calendar(
            store, exchange=request.exchange, years=request.years, as_of=request.as_of
        ),
        store=store,
        what=f"the {request.exchange} trading calendar",
    )
    sessions = _read(
        lambda: calendar.trading_days_between(request.start, request.end),
        store=store,
        what="the sessions of the range",
    )
    if len(sessions) < 2:
        raise StrategyRunBlockedError(
            f"{request.start.isoformat()}..{request.end.isoformat()} holds "
            f"{len(sessions)} open session(s); a backtest needs a signal and a session to trade on"
        )
    signal_days = frozenset(
        sessions[index]
        for index in rebalance_indices(
            sessions, every=request.spec.rebalance_every_sessions, days=request.rebalance_days
        )
    )
    source = request.source
    lookback: tuple[date, ...] = ()
    years = request.years
    if source.trailing_ic is not None:
        lookback, years = _lookback(store, request, source.trailing_ic.ic_window_sessions - 1)
    elif source.walk_forward is not None:
        lookback, years = _lookback(store, request, source.walk_forward.train_sessions - 1)
    instants = {day: session_publication_instant(day) for day in lookback + sessions}
    days = _PanelDays(store, request, calendar)
    benchmarks: dict[str, Mapping[date, Decimal]] = {}
    for name in request.spec.benchmarks:
        if name == EQUAL_WEIGHT_ALL_A:
            benchmarks[name] = _EqualWeightReturns(days, sessions)
        else:
            benchmarks[name] = _index_returns(store, request, calendar, name, sessions)
    industries: Mapping[date, Mapping[str, str]] = (
        {}
        if request.spec.max_industry_weight is None
        else _IndustryDays(store, signal_days, instants)
    )
    shared = {
        "source": source,
        "sessions": sessions,
        "signal_instants": instants,
        "quotes": _QuoteDays(days, sessions),
        "benchmark_returns": benchmarks,
        "industries": industries,
        "lookback_sessions": lookback,
        "rebalance_days": request.rebalance_days,
    }
    if source.prediction_ids:
        return StrategyInputs(
            scores=_prediction_rows(
                source.prediction_ids, predictions, sessions=sessions, verify_late=verify_late
            ),
            **shared,  # type: ignore[arg-type]
        )
    feed: ScoreFeed
    if source.walk_forward is not None:
        feed = _ModelFeed(
            store,
            request,
            sessions=sessions,
            calendar=lookback + sessions,
            signal_days=signal_days,
            instants=instants,
            years=years,
        )
    else:
        feed = _FactorFeed(
            store,
            request,
            pairs=(
                source.trailing_ic.components
                if source.trailing_ic is not None
                else tuple((token, tier) for token, tier, _ in source.components)
            ),
            signal_days=signal_days,
            calendar=lookback + sessions,
            instants=instants,
            years=years,
        )
    if stream:
        return StrategyInputs(scores=(), feed=feed, **shared)  # type: ignore[arg-type]
    rows, observations, refits, fits = _drained(feed, signal_days, lookback + sessions)
    return StrategyInputs(
        scores=rows,
        ic_observations=observations,
        model_fits=refits,
        fit_for_day=fits,
        **shared,  # type: ignore[arg-type]
    )


def _from_the_model_plane(reader: Callable[[], _T]) -> _T:
    """Run a read through `model_view`, re-enveloping its faults under this face's rows.

    The model plane's three rows are this face's three, by `reason`: a request that cannot be
    put, a panel that cannot be read, and a stored state the question conflicts with.
    """
    try:
        return reader()
    except ModelViewError as error:
        envelope: type[StrategyViewError] = {
            StrategyRequestError.reason: StrategyRequestError,
            StrategyPanelUnreadableError.reason: StrategyPanelUnreadableError,
        }.get(error.reason, StrategyRunBlockedError)
        raise envelope(str(error), disclosable=error.disclosable) from error


def _lookback(
    store: PanelStore, request: StrategyRequest, needed: int
) -> tuple[tuple[date, ...], tuple[int, ...]]:
    """The `needed` open sessions before `--start`, and every partition year a read now spans.

    Read from the start year's own sessions before `--start` and the contiguous run of
    registered `trade_cal` years ending the year before it, reaching back no more years than
    `needed` sessions can span (an A-share year has well over 200 sessions). Fewer are returned
    when the stored calendar stops earlier -- see
    `a_lookback_reaching_before_the_stored_calendar_is_shorter_rather_than_refused`.
    """
    if needed <= 0:
        return (), request.years
    registered = set(store.registered_years(TRADING_CALENDAR_DATASET))
    earlier: list[int] = []
    year = request.start.year - 1
    while year in registered and len(earlier) < needed // 200 + 1:
        earlier.append(year)
        year -= 1
    years = (*sorted(earlier), *request.years)
    calendar = _read(
        lambda: load_trading_calendar(
            store, exchange=request.exchange, years=years, as_of=request.as_of
        ),
        store=store,
        what=f"the {request.exchange} trading calendar before {request.start.isoformat()}",
    )
    before = tuple(day for day in calendar.trading_days if day < request.start)
    return before[-needed:], years


# --- scores -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class _Observed:
    subject: str
    as_of: datetime
    value: float | None
    coverage: str


class _StoredRow(Protocol):
    """What the three tiers' stored rows have in common, and all a score needs of one."""

    @property
    def subject(self) -> str: ...
    @property
    def as_of(self) -> datetime: ...
    @property
    def value(self) -> float | None: ...
    @property
    def coverage(self) -> str: ...


def _observed(rows: Sequence[_StoredRow]) -> list[_Observed]:
    return [
        _Observed(subject=row.subject, as_of=row.as_of, value=row.value, coverage=row.coverage)
        for row in rows
    ]


class _TierRead(Protocol):
    """What a tier read needs of a request: the reading instant and the tier's two specs."""

    @property
    def as_of(self) -> datetime: ...
    @property
    def transform(self) -> FactorTransformSpec | None: ...
    @property
    def neutralization(self) -> FactorNeutralizationSpec | None: ...


def _tier_rows(
    store: PanelStore,
    request: _TierRead,
    definition: FactorDefinition,
    tier: str,
    year: int,
) -> list[_Observed]:
    """One year of one tier of one factor, read at `request.as_of`, admitted values only."""
    what = f"the {tier} {definition.qualified_key} observations of {year}"
    rows: Sequence[_StoredRow]
    if tier == "raw":
        rows = _read(
            lambda: load_factor_observations(store, definition, years=(year,), as_of=request.as_of),
            store=store,
            what=what,
        )
    elif tier == "processed":
        transform = request.transform
        assert transform is not None  # strategy_request requires it for this tier
        rows = _read(
            lambda: load_processed_factor_observations(
                store, definition, transform, years=(year,), as_of=request.as_of
            ),
            store=store,
            what=what,
        )
    else:
        neutralization = request.neutralization
        assert neutralization is not None  # strategy_request requires it for this tier
        rows = _read(
            lambda: load_neutralized_factor_observations(
                store, definition, neutralization, years=(year,), as_of=request.as_of
            ),
            store=store,
            what=what,
        )
    return _observed(rows)


def _chosen_build(builds: Mapping[datetime, object], signal: datetime) -> datetime:
    """The latest build at or before the signal instant, else the earliest.

    On a signal day the earliest is handed to the book, which refuses it as look-ahead
    (`ScoreRow`'s clocks carry the build's instant). On an IC day it is used, and that is safe
    for a precise reason rather than by accident: an IC is used on a signal day only once its
    `ICObservation.known_at` has passed, and `known_at` is the LATER of this build's instant and
    its label's exit -- so a build stamped after its own day's 16:30 is an IC nobody could use
    before that build existed. The label window is dated from the build's own instant
    (`build_label_window`), so its entry is still the session after the one the values were
    computed from and no return that preceded them is paired against them.
    """
    visible = [instant for instant in builds if instant <= signal]
    return max(visible) if visible else min(builds)


def _prediction_rows(
    identifiers: Sequence[str],
    predictions: Callable[[str], PredictionRecord | None] | None,
    *,
    sessions: Sequence[date],
    verify_late: Callable[[PredictionRecord], str | None] | None = None,
) -> tuple[ScoreRow, ...]:
    """Each record's scored rows, read as the module docstring states.

    Registered at or before its signal instant, a record's rows carry its custody stamp. After
    it, the record must be registered before `registration_deadline` of the session after its
    day in `sessions` -- the session that trades it (a record of the range's last session, or of
    a day outside the range, trades nothing in this run) -- and admitted by `verify_late`; its
    rows then carry `batch.as_of`, which the book still holds to the signal instant.
    """
    if predictions is None:
        raise StrategyRequestError(
            "the source names prediction_ids and no prediction store was supplied to read them"
        )
    trading = dict(pairwise(sessions))
    rows: list[ScoreRow] = []
    for identifier in identifiers:
        record = predictions(identifier)
        if record is None:
            raise StrategyRunBlockedError(f"no prediction is held under {identifier}")
        batch = record.batch
        day = batch.as_of.astimezone(SHANGHAI).date()
        registered = max(batch.predicted_at, record.recorded_at)
        instant = session_publication_instant(day)
        available, revised = registered, record.recorded_at
        if registered > instant:
            session = trading.get(day)
            if session is not None and registered >= registration_deadline(session):
                raise StrategyBacktestError(
                    f"{identifier} holds the scores of {day.isoformat()}, which the book trades "
                    f"at {session.isoformat()}'s open, and it was registered at "
                    f"{registered.astimezone(SHANGHAI).isoformat()}, at or after that session's "
                    f"call auction started ({registration_deadline(session).isoformat()}). Its "
                    "scores may have seen part of the outcome of the trade they drive"
                )
            if verify_late is None:
                raise StrategyBacktestError(
                    f"{identifier} was registered at {registered.astimezone(SHANGHAI).isoformat()}"
                    f", after its signal instant {instant.isoformat()}; its as_of is only what its "
                    "writer declared, and no registration's check (verify_late) was given to "
                    "recompute it against"
                )
            refusal = verify_late(record)
            if refusal is not None:
                raise StrategyBacktestError(refusal)
            available = revised = batch.as_of
        rows.extend(
            ScoreRow(
                component=PREDICTION_COMPONENT,
                subject=prediction.ts_code,
                signal_day=day,
                value=prediction.score,
                available_time=available,
                revision_time=revised,
            )
            for prediction in batch.predictions
            if prediction.score is not None
        )
    return tuple(rows)


def _cached_label_sessions(horizon_sessions: int) -> int:
    """How many sessions' bars the label reader keeps: every window's, with room to spare."""
    return horizon_sessions + 8


def _years_around(years: Sequence[int], *, first: int, last: int) -> tuple[int, ...]:
    """The run's partition years inside `[first, last]`, ascending: one window's reads."""
    return tuple(year for year in years if first <= year <= last)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Section:
    """One component's stored cross section on one day -- every row, admitted or not -- held
    compactly.

    A tuple of (interned) codes and an `array('d')` of the stored values rather than one object
    per row, so a year of daily cross sections costs a few megabytes rather than hundreds.
    `present` marks the rows that carry a value (a row without one holds `nan` in `values`).
    The rows a tier does not admit are kept so an IC's census counts them by their own code;
    only `admitted` rows ever become scores.
    """

    build: datetime
    tier: str
    subjects: tuple[str, ...]
    values: array[float]
    present: bytes
    coverage: tuple[str, ...]

    def admitted(self) -> Iterator[tuple[str, float]]:
        """The `(subject, value)` rows the tier admits: `TIER_ADMITTED_CODES`, with a value."""
        codes = TIER_ADMITTED_CODES[self.tier]
        for subject, value, present, coverage in zip(
            self.subjects, self.values, self.present, self.coverage, strict=True
        ):
            if present and coverage in codes:
                yield subject, value

    def rows(self) -> list[tuple[str, float | None, str]]:
        """Every row as `ic_cross_section` takes it: `(subject, value or None, coverage)`."""
        return [
            (subject, value if present else None, coverage)
            for subject, value, present, coverage in zip(
                self.subjects, self.values, self.present, self.coverage, strict=True
            )
        ]


def _year_sections(
    store: PanelStore,
    request: _TierRead,
    definition: FactorDefinition,
    tier: str,
    year: int,
    *,
    days: frozenset[date],
    instants: Mapping[date, datetime],
    names: dict[str, str],
) -> dict[date, _Section]:
    """One tier-year partition, read ONCE, as the compact cross section of each wanted day.

    A day's build is `_chosen_build`'s pick at that day's signal instant, among the builds that
    carry at least one admitted value -- a build that admits nothing is no cross section. The
    loader's own rows are dropped when this returns; only the wanted days' sections survive it.
    """
    admitted = TIER_ADMITTED_CODES[tier]
    by_day: dict[date, dict[datetime, list[_Observed]]] = {}
    for observed in _tier_rows(store, request, definition, tier, year):
        day = observed.as_of.astimezone(SHANGHAI).date()
        if day in days:
            by_day.setdefault(day, {}).setdefault(observed.as_of, []).append(observed)
    sections: dict[date, _Section] = {}
    for day, all_builds in by_day.items():
        builds = {
            instant: rows
            for instant, rows in all_builds.items()
            if any(row.coverage in admitted and row.value is not None for row in rows)
        }
        if not builds:
            continue
        build = _chosen_build(builds, instants[day])
        rows = builds[build]
        sections[day] = _Section(
            build=build,
            tier=tier,
            subjects=tuple(names.setdefault(row.subject, row.subject) for row in rows),
            values=array("d", (math.nan if row.value is None else row.value for row in rows)),
            present=bytes(row.value is not None for row in rows),
            coverage=tuple(names.setdefault(row.coverage, row.coverage) for row in rows),
        )
    return sections


def _ic_days(
    spec: TrailingICWeights, calendar: Sequence[date], signal_days: frozenset[date]
) -> frozenset[date]:
    """Every prediction day a trailing window could use, by calendar arithmetic alone.

    From the first signal day's window start to the last day whose label can have exited by the
    last signal day, so no window is priced that no signal could ever use; which ICs a signal
    day DOES use is the book's decision (`_ICIndex.weights`).
    """
    position = {day: index for index, day in enumerate(calendar)}
    first = max(position[min(signal_days)] - spec.ic_window_sessions + 1, 0)
    last = position[max(signal_days)] - spec.horizon_sessions - 1
    return frozenset(calendar[first : last + 1]) if last >= first else frozenset()


def _ic_study(
    definition: FactorDefinition, *, method: ICMethod, min_securities: int
) -> FactorICStudy:
    return FactorICStudy(
        FactorICSpec(
            definition=definition,
            method=method,
            min_securities=min_securities,
            min_as_ofs=MINIMUM_IC_AS_OFS,
        )
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _MeasuredIC:
    """One component's IC on one prediction day: the point, its census, and when it was known."""

    prediction_day: date
    build: datetime
    point: ICPoint
    census: ICCensus
    known_at: datetime


def _measure_year(
    store: PanelStore,
    request: _TierRead,
    *,
    exchange: str,
    years: Sequence[int],
    year: int,
    sections: Mapping[tuple[str, str], Mapping[date, _Section]],
    studies: Mapping[tuple[str, str], FactorICStudy],
    horizon: ResearchHorizon,
    instants: Mapping[date, datetime],
) -> list[tuple[tuple[str, str], _MeasuredIC]]:
    """Every component's IC on every day of one year it has a section for, in day order.

    The label reader is this year's: its calendar, registry, halts and adjustment factors span
    the year before through the year after, which is every session a label window of this year
    reads and every factor it carries forward -- and nothing older, so the history does not
    accumulate. Labels are shared across one day's components, whose windows are one window.
    """
    reader = OutcomeLabels(
        store,
        LabelReach(
            as_of=request.as_of,
            years=_years_around(years, first=year - 1, last=year + 1),
            exchange=exchange,
        ),
        cached_sessions=_cached_label_sessions(horizon.sessions),
    )
    days = sorted({day for by_day in sections.values() for day in by_day})
    measured: list[tuple[tuple[str, str], _MeasuredIC]] = []
    for day in days:
        labels: dict[tuple[str, date, date], OutcomeLabel | None] = {}
        for pair, by_day in sections.items():
            section = by_day.get(day)
            if section is not None:
                measured.append(
                    (
                        pair,
                        _measure(
                            reader,
                            studies[pair],
                            tier=pair[1],
                            section=section,
                            horizon=horizon,
                            labels=labels,
                            instants=instants,
                        ),
                    )
                )
    return measured


def _measure(
    reader: OutcomeLabels,
    study: FactorICStudy,
    *,
    tier: str,
    section: _Section,
    horizon: ResearchHorizon,
    labels: dict[tuple[str, date, date], OutcomeLabel | None],
    instants: Mapping[date, datetime],
) -> _MeasuredIC:
    """One build's IC against its labels (`ic_cross_section` + `FactorICStudy.measure`)."""
    window = reader.window(section.build, horizon=horizon)
    paired: dict[str, OutcomeLabel] = {}
    for subject, _value in section.admitted():
        key = (subject, window.entry_day, window.exit_day)
        if key not in labels:
            labels[key] = reader.label(subject, window)
        label = labels[key]
        if label is not None:
            paired[subject] = label
    try:
        cross_section = ic_cross_section(
            as_of=section.build,
            tier=tier,  # type: ignore[arg-type]
            rows=section.rows(),
            labels=paired,
        )
        point = study.measure(cross_section)
    except FactorICError as error:
        raise StrategyBacktestError(
            f"the IC at {section.build.isoformat()} could not be measured: {error}"
        ) from error
    return _MeasuredIC(
        prediction_day=window.prediction_day,
        build=section.build,
        point=point,
        census=cross_section.census,
        known_at=max(section.build, instants[window.exit_day]),
    )


class _FactorFeed:
    """A `ScoreFeed` over stored factor tiers, for a static or a trailing-IC source.

    Reads each tier-year partition once, the first time the book asks about that year, and
    derives both halves from that one read: the compact sections of the year's signal days, and
    -- for a trailing-IC source -- every IC of the year's IC days. A signal day's section is
    handed over as `ScoreRow`s and dropped when the book asks for it, so what is held is at most
    one year of signal-day sections and the ICs (one small record per component per day).
    """

    def __init__(
        self,
        store: PanelStore,
        request: StrategyRequest,
        *,
        pairs: Sequence[tuple[str, str]],
        signal_days: frozenset[date],
        calendar: Sequence[date],
        instants: Mapping[date, datetime],
        years: Sequence[int],
    ) -> None:
        self._store = store
        self._request = request
        self._pairs = tuple(pairs)
        self._signal_days = signal_days
        self._instants = instants
        self._years = tuple(years)
        self._names: dict[str, str] = {}
        self._rows: dict[date, list[tuple[str, float, _Section]]] = {}
        self._ics: list[ICObservation] = []
        self._loaded: set[int] = set()
        spec = request.source.trailing_ic
        self._spec = spec
        self._ic_days = frozenset() if spec is None else _ic_days(spec, calendar, signal_days)

    def rows_on(self, day: date) -> Sequence[ScoreRow]:
        self._load(day.year)
        return [
            ScoreRow(
                component=key,
                subject=subject,
                signal_day=day,
                value=sign * value,
                available_time=section.build,
                revision_time=section.build,
            )
            for key, sign, section in self._rows.pop(day, [])
            for subject, value in section.admitted()
        ]

    def ic_observations_through(self, day: date) -> Sequence[ICObservation]:
        for year in sorted({item.year for item in self._ic_days}):
            if year <= day.year:
                self._load(year)
        handed = sorted(
            (item for item in self._ics if item.prediction_day <= day),
            key=lambda item: (item.prediction_day, item.component),
        )
        self._ics = [item for item in self._ics if item.prediction_day > day]
        return handed

    def fit_on(self, day: date) -> WalkForwardFit | None:
        return None

    def refits(self) -> tuple[WalkForwardFit, ...]:
        return ()

    def _load(self, year: int) -> None:
        if year in self._loaded:
            return
        self._loaded.add(year)
        request = self._request
        signal = frozenset(day for day in self._signal_days if day.year == year)
        ic_days = frozenset(day for day in self._ic_days if day.year == year)
        if not signal and not ic_days:
            return
        sections = {
            (token, tier): _year_sections(
                self._store,
                request,
                request.definitions[token],
                tier,
                year,
                days=signal | ic_days,
                instants=self._instants,
                names=self._names,
            )
            for token, tier in self._pairs
        }
        for token, tier in self._pairs:
            definition = request.definitions[token]
            sign = -1.0 if definition.direction == "lower_is_better" else 1.0
            key = component_key(token, tier)
            for day in sorted(signal):
                section = sections[(token, tier)].get(day)
                if section is not None:
                    self._rows.setdefault(day, []).append((key, sign, section))
        spec = self._spec
        if spec is None or not ic_days:
            return
        on_ic_days = {
            pair: {day: section for day, section in by_day.items() if day in ic_days}
            for pair, by_day in sections.items()
        }
        del sections
        measured = _from_the_model_plane(
            lambda: _measure_year(
                self._store,
                request,
                exchange=request.exchange,
                years=self._years,
                year=year,
                sections=on_ic_days,
                studies={
                    (token, tier): _ic_study(
                        request.definitions[token],
                        method=spec.ic_method,
                        min_securities=spec.min_ic_securities,
                    )
                    for token, tier in self._pairs
                },
                horizon=parse_horizon(f"{spec.horizon_sessions}d"),
                instants=self._instants,
            )
        )
        self._ics.extend(
            ICObservation(
                component=component_key(*pair),
                prediction_day=item.prediction_day,
                known_at=item.known_at,
                ic=item.point.ic,
            )
            for pair, item in measured
        )


class _ModelFeed:
    """A `ScoreFeed` for a walk-forward source: rolling training windows, fitted as asked.

    Refits fall on `sessions[0]`, `sessions[F]`, ... up to the last signal day and are made in
    order, when the first signal day on or after one is asked about. Each refit's examples are
    the labelled cross sections of its own `train_sessions` window, kept by prediction day: the
    days the window has moved past are dropped and the days it has moved onto are labelled
    through `model_view.training_panel` over just those days -- so what is held is one window,
    never the history. A signal day with a fit in use is scored on
    `model_view.feature_cross_section` at its signal instant, and every score row carries that
    instant on both clocks.
    """

    def __init__(
        self,
        store: PanelStore,
        request: StrategyRequest,
        *,
        sessions: tuple[date, ...],
        calendar: tuple[date, ...],
        signal_days: frozenset[date],
        instants: Mapping[date, datetime],
        years: Sequence[int],
        fit_cache: MutableMapping[tuple[date, datetime, object], WalkForwardFit] | None = None,
    ) -> None:
        spec, model = request.source.walk_forward, request.model
        assert spec is not None and model is not None  # strategy_request resolved both together
        self._store = store
        self._request = request
        self._spec = spec
        self._model = model
        self._calendar = calendar
        self._position = {day: index for index, day in enumerate(calendar)}
        self._instants = instants
        self._years = tuple(years)
        self._horizon = parse_horizon(f"{spec.horizon_sessions}d")
        self._feature_ids = feature_spec(columns=request.columns, missing=spec.missing).feature_ids
        self._pending = list(
            day for day in sessions[:: spec.refit_every_sessions] if day <= max(signal_days)
        )
        self._fits: list[WalkForwardFit] = []
        self._fit_cache = fit_cache
        self._window: dict[date, tuple[TrainingExample, ...]] = {}
        self.last_batch: PredictionBatch | None = None
        """The batch `rows_on` scored last: the one a caller registering that day's scores files
        (`score_day`). Only the last is kept, so a long run holds one batch, not the history."""

    def rows_on(self, day: date) -> Sequence[ScoreRow]:
        fit = self.fit_on(day)
        if fit is None or fit.fitted is None:
            return []
        fitted = fit.fitted
        instant = self._instants[day]
        section = _from_the_model_plane(
            lambda: feature_cross_section(
                self._store,
                self._run(start=day, end=day, first_year=day.year - 1, last_year=day.year),
                as_of=instant,
            )
        )
        try:
            batch = fitted.predict(section.cross_section, predicted_at=instant, shelf_life=None)
        except (AlphaModelError, ValueError) as error:
            raise StrategyBacktestError(
                f"the fit refitted on {fit.refit_day.isoformat()} could not score the cross "
                f"section visible at {instant.isoformat()}: {error}"
            ) from error
        self.last_batch = batch
        return [
            ScoreRow(
                component=MODEL_COMPONENT,
                subject=prediction.ts_code,
                signal_day=day,
                value=prediction.score,
                available_time=instant,
                revision_time=instant,
            )
            for prediction in batch.predictions
            if prediction.score is not None
        ]

    def ic_observations_through(self, day: date) -> Sequence[ICObservation]:
        return ()

    def fit_on(self, day: date) -> WalkForwardFit | None:
        while self._pending and self._pending[0] <= day:
            self._fits.append(self._cached_refit(self._pending.pop(0)))
        return usable_fit(self._fits, signal_day=day)

    def _cached_refit(self, refit_day: date) -> WalkForwardFit:
        """`_refit`, served from `fit_cache` when one was handed in: a fit is a function of the
        refit day, the instant the store is read at and the model declared, so one computed
        for another day of the same report is that day's too (`V2-P6-011` round 11)."""
        if self._fit_cache is None:
            return self._refit(refit_day)
        key = (refit_day, self._request.as_of, self._model.declaration)
        held = self._fit_cache.get(key)
        if held is None:
            held = self._fit_cache[key] = self._refit(refit_day)
        return held

    def refits(self) -> tuple[WalkForwardFit, ...]:
        return tuple(self._fits)

    def _run(self, *, start: date, end: date, first_year: int, last_year: int) -> ModelRunRequest:
        model = self._model
        return ModelRunRequest(
            declaration=model.declaration,
            columns=self._request.columns,
            missing=self._spec.missing,
            start=start,
            end=end,
            as_of=self._request.as_of,
            years=_years_around(self._years, first=first_year, last=last_year),
            exchange=self._request.exchange,
            horizon=self._horizon,
            minimum_scored_ratio=0.0,
            shelf_life=None,
            config_digest=UNFILED_CONFIG_DIGEST,
            declared_feature_version=None,
        )

    def _refit(self, refit_day: date) -> WalkForwardFit:
        spec, calendar = self._spec, self._calendar
        at = self._position[refit_day]
        first = max(at - spec.train_sessions + 1, 0)
        newest = at - spec.embargo_sessions - spec.horizon_sessions - 2
        for day in [day for day in self._window if self._position[day] < first]:
            del self._window[day]
        if newest >= first:
            missing = [
                calendar[index]
                for index in range(first, newest + 1)
                if calendar[index] not in self._window
            ]
            if missing:
                deadline_day = calendar[at - spec.embargo_sessions]
                panel = _from_the_model_plane(
                    lambda: training_panel(
                        self._store,
                        self._run(
                            start=missing[0],
                            end=missing[-1],
                            first_year=missing[0].year - 1,
                            last_year=deadline_day.year,
                        ),
                        deadline=self._instants[deadline_day],
                        cached_sessions=_cached_label_sessions(spec.horizon_sessions),
                    )
                )
                by_day: dict[date, list[TrainingExample]] = {day: [] for day in missing}
                for example in () if panel is None else panel.examples:
                    by_day.setdefault(example.label.window.prediction_day, []).append(
                        _held_example(example)
                    )
                self._window.update((day, tuple(rows)) for day, rows in by_day.items())
        (fit,) = walk_forward_fits(
            self._model,
            [example for day in sorted(self._window) for example in self._window[day]],
            feature_ids=self._feature_ids,
            spec=spec,
            calendar=calendar,
            instants=self._instants,
            refit_days=(refit_day,),
        )
        return fit


@dataclass(frozen=True, slots=True, kw_only=True)
class _HeldWindowReturn(WindowReturn):
    """A `WindowReturn` a training window holds: every number, less the per-session chain.

    `per_session` is the audit trail `session_returns` checked, one link at a time, when
    `label_outcome` built the label -- a check that has already passed or the label would not
    exist. Nothing a fit reads comes from it: the target is `adjusted` (`realized_return`) and
    the cutoff is the window's exit, both kept exactly. `tolerance` is the one number derived
    from the chain, so it refuses here rather than answering `0.0` for an emptied chain.
    """

    @property
    def tolerance(self) -> float:
        raise AttributeError(
            "this window return is held by a walk-forward training window without its "
            "per-session chain, so its tolerance is not available; read the label that "
            "label_outcome built"
        )


def _held_example(example: TrainingExample) -> TrainingExample:
    """`example` as a training window holds it: the same label on a `_HeldWindowReturn`.

    Dropping the chain is what makes a whole-market two-year window fit in memory (measured
    2,544 -> 1,178 bytes per example); see `_HeldWindowReturn` for what it cannot answer.
    """
    label = example.label
    returned = label.window_return
    if returned is None or isinstance(returned, _HeldWindowReturn):
        return example
    fields = {field.name: getattr(returned, field.name) for field in dataclasses.fields(returned)}
    return TrainingExample(
        label=dataclasses.replace(
            label, window_return=_HeldWindowReturn(**{**fields, "per_session": ()})
        ),
        features=example.features,
    )


def _drained(feed: ScoreFeed, signal_days: frozenset[date], calendar: Sequence[date]) -> Any:
    """The feed's whole answer at once -- the materialised twin of a streamed run."""
    ordered = sorted(signal_days)
    observations: list[ICObservation] = []
    rows: list[ScoreRow] = []
    fits: dict[date, WalkForwardFit] = {}
    for day in ordered:
        observations.extend(feed.ic_observations_through(day))
        rows.extend(feed.rows_on(day))
        fit = feed.fit_on(day)
        if fit is not None:
            fits[day] = fit
    observations.extend(feed.ic_observations_through(calendar[-1]))
    return tuple(rows), tuple(observations), feed.refits(), fits


# --- sessions -----------------------------------------------------------------------------------


class _PanelDays:
    """One session's bars and bands, read on demand and kept for a few sessions."""

    def __init__(self, store: PanelStore, request: StrategyRequest, calendar: TradingCalendar):
        self._store = store
        self._request = request
        self._calendar = calendar
        self._sessions: OrderedDict[date, tuple[dict[str, DailyBar], dict[str, PriceLimit]]] = (
            OrderedDict()
        )
        self.halts: HaltCorpus = halt_corpus_for_years(
            _read(
                lambda: load_suspensions(
                    store, years=request.years, as_of=request.as_of, max_staleness=None
                ),
                store=store,
                what="the halt corpus",
            ),
            years=request.years,
        )
        self._adjustments: OrderedDict[int, Mapping[str, AdjustmentHistory]] = OrderedDict()

    def adjustments(self, year: int) -> Mapping[str, AdjustmentHistory]:
        """The adjustment histories a quote in `year` reads: that year's and the one before.

        Two years rather than the range, so the factors held do not grow with the history; the
        year before is what a name with no factor row yet this year carries forward from.

        **Two slots, not one.** A period whose signal day is in December and whose trade day is
        in January asks, order by order, for the signal session's quote (the participation cap)
        and the trade session's quote -- two years, alternating. One slot reloaded both on every
        order; two keep each year's histories resident until a third year is asked for, so a
        year boundary costs exactly one load per year.
        """
        held = self._adjustments.get(year)
        if held is not None:
            self._adjustments.move_to_end(year)
            return held
        store, request = self._store, self._request
        years = _years_around(request.years, first=year - 1, last=year)
        loaded = _read(
            lambda: load_adjustment_histories(
                store, years=years, as_of=request.as_of, max_staleness=None
            ),
            store=store,
            what="the adjustment factors",
        )
        self._adjustments[year] = loaded
        while len(self._adjustments) > _ADJUSTMENT_YEARS_HELD:
            self._adjustments.popitem(last=False)
        return loaded

    def session(self, day: date) -> tuple[dict[str, DailyBar], dict[str, PriceLimit]]:
        held = self._sessions.get(day)
        if held is not None:
            self._sessions.move_to_end(day)
            return held
        store, calendar, as_of = self._store, self._calendar, self._request.as_of
        bars = _read(
            lambda: load_daily_bars(
                store, day=day, calendar=calendar, as_of=as_of, max_staleness=None
            ),
            store=store,
            what=f"the price bars for {day.isoformat()}",
        )
        limits = _read(
            lambda: load_price_limits(
                store, day=day, calendar=calendar, as_of=as_of, max_staleness=None
            ),
            store=store,
            what=f"the published limit bands for {day.isoformat()}",
        )
        self._sessions[day] = (bars, limits)
        if len(self._sessions) > _CACHED_SESSIONS:
            self._sessions.popitem(last=False)
        return bars, limits

    def quote(self, day: date, subject: str) -> SessionQuote | None:
        bars, limits = self.session(day)
        bar, limit = bars.get(subject), limits.get(subject)
        history = self.adjustments(day.year).get(subject)
        if bar is None or limit is None or history is None:
            return None
        try:
            adj_factor = Decimal(str(history.factor_on(day)))
        except AdjustmentHorizonError:
            return None
        state = self.halts.state_on(day, subject)
        return SessionQuote(
            bar=MarketBar(
                subject=subject,
                trade_date=day,
                board=_board(subject),
                previous_close=Decimal(str(bar.pre_close)),
                open=Decimal(str(bar.open)),
                high=Decimal(str(bar.high)),
                low=Decimal(str(bar.low)),
                close=Decimal(str(bar.close)),
                suspended=(
                    suspended_at_the_close(state, self.halts.timing_on(day, subject))
                    or state is TradingState.interrupted
                ),
                # Never read: with a published band `_price_band` uses the exchange's numbers.
                is_st=False,
                **published_limit_fields(limit),
            ),
            turnover_yuan=Decimal(str(bar.amount)) * CNY_PER_TURNOVER_UNIT,
            adj_factor=adj_factor,
        )


def _board(ts_code: str) -> Literal["main", "star", "growth", "bse"]:
    """The board a security trades on, from its code."""
    if ts_code.endswith(".BJ"):
        return "bse"
    if ts_code.startswith(("688", "689")):
        return "star"
    if ts_code.startswith(("300", "301")):
        return "growth"
    return "main"


class _DayQuotes(Mapping[str, SessionQuote]):
    def __init__(self, days: _PanelDays, day: date) -> None:
        self._days = days
        self._day = day

    def __getitem__(self, subject: str) -> SessionQuote:
        quote = self._days.quote(self._day, subject)
        if quote is None:
            raise KeyError(subject)
        return quote

    def __iter__(self) -> Iterator[str]:
        bars, _ = self._days.session(self._day)
        return (subject for subject in sorted(bars) if subject in self)

    def __len__(self) -> int:
        return sum(1 for _ in self)


class _QuoteDays(Mapping[date, Mapping[str, SessionQuote]]):
    def __init__(self, days: _PanelDays, sessions: tuple[date, ...]) -> None:
        self._days = days
        self._sessions = frozenset(sessions)
        self._order = sessions

    def __getitem__(self, day: date) -> Mapping[str, SessionQuote]:
        if day not in self._sessions:
            raise KeyError(day)
        return _DayQuotes(self._days, day)

    def __iter__(self) -> Iterator[date]:
        return iter(self._order)

    def __len__(self) -> int:
        return len(self._order)


class _EqualWeightReturns(Mapping[date, Decimal]):
    """The mean of `close / pre_close - 1` over every security with a bar on the session."""

    def __init__(self, days: _PanelDays, sessions: tuple[date, ...]) -> None:
        self._days = days
        self._sessions = sessions
        self._known = frozenset(sessions)

    def __getitem__(self, day: date) -> Decimal:
        if day not in self._known:
            raise KeyError(day)
        bars, _ = self._days.session(day)
        returns = [bar.close / bar.pre_close - 1.0 for bar in bars.values() if bar.pre_close > 0]
        if not returns:
            raise KeyError(day)
        return Decimal(repr(statistics.fmean(returns)))

    def __iter__(self) -> Iterator[date]:
        return iter(self._sessions)

    def __len__(self) -> int:
        return len(self._sessions)


def _index_returns(
    store: PanelStore,
    request: StrategyRequest,
    calendar: TradingCalendar,
    code: str,
    sessions: tuple[date, ...],
) -> Mapping[date, Decimal]:
    series = _read(
        lambda: load_index_prices(
            store, calendar, years=request.years, as_of=request.as_of, max_staleness=None
        ),
        store=store,
        what="the index levels",
    )
    bars = [bar for bar in series.get(code, ()) if sessions[0] < bar.trade_date <= sessions[-1]]
    if not bars:
        raise StrategyRunBlockedError(
            f"benchmark {code} has no stored level inside {sessions[0].isoformat()}.."
            f"{sessions[-1].isoformat()}; build it with `openalpha panel build --dataset "
            "index_daily` or name a benchmark the panel holds"
        )
    try:
        returns = index_session_returns(bars)
    except IndexPriceError as error:
        raise StrategyRunBlockedError(f"benchmark {code}: {error}") from error
    return {bar.trade_date: Decimal(repr(value)) for bar, value in zip(bars, returns, strict=True)}


class _IndustryDays(Mapping[date, Mapping[str, str]]):
    """Each signal day's level-one industry per security, read at that day's signal instant."""

    def __init__(
        self,
        store: PanelStore,
        signal_days: frozenset[date],
        instants: Mapping[date, datetime],
    ) -> None:
        self._store = store
        self._days = signal_days
        self._instants = instants
        self._held: dict[date, Mapping[str, str]] = {}

    def __getitem__(self, day: date) -> Mapping[str, str]:
        if day not in self._days:
            raise KeyError(day)
        if day not in self._held:
            # The dataset whose taxonomy was in force on the day (`V2-P6-015`): SW2014's
            # level-one memberships through 2021-12-10, `index_member_all` from 2021-12-13.
            try:
                dataset = industry_membership_source_on(day).dataset
            except IndustryHorizonError as error:
                raise StrategyRunBlockedError(
                    f"--max-industry-weight needs an industry cross section on "
                    f"{day.isoformat()}: {error}"
                ) from error
            registered = self._store.registered_years(dataset)
            years = tuple(sorted(year for year in registered if year <= day.year))
            if not years:
                raise StrategyRunBlockedError(
                    f"--max-industry-weight needs industry memberships and no "
                    f"{dataset} partition at or before {day.year} is "
                    "registered in this panel"
                )
            store, instant = self._store, self._instants[day]
            answers = _read(
                lambda: load_industry_cross_section(
                    store, day=day, years=years, as_of=instant, max_staleness=None
                ),
                store=store,
                what=f"the industry cross section for {day.isoformat()}",
            )
            self._held = {day: {code: answer.l1_code for code, answer in answers.items()}}
        return self._held[day]

    def __iter__(self) -> Iterator[date]:
        return iter(sorted(self._days))

    def __len__(self) -> int:
        return len(self._days)


# --- one signal day, today (V2-P6-011) -----------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class SignalDay:
    """One session scored exactly as a backtest of the same source would score it on that day.

    `scores` is `strategy_backtest.score_signal_day`'s answer: the ranking the book would trade
    on (or `None` when the source held), the composite each ranked security was ordered by, and
    the weights. `fit` and `model_batch` are a walk-forward source's fit in use and the batch it
    scored the day's cross section with -- the very batch whose scores became the book's rows.
    `knowable_through` is the newest instant any score row or counted IC the day read became
    knowable (at or before the signal instant, or the book would have refused it), and
    `values_consumed` how many rows and ICs that was. `industries` is each security's level-one
    industry at the signal instant when the spec caps industries, else empty. `refit_day` is the
    walk-forward refit the fit in use came from, on the schedule anchored at `anchor`, and
    `position` how many sessions `day` is after the anchor's first session -- a backtest from the
    anchor rebalances on every day whose position is a multiple of `rebalance_every_sessions`.
    """

    day: date
    instant: datetime
    anchor: date
    position: int
    scores: SignalDayScores
    fit: WalkForwardFit | None
    model_batch: PredictionBatch | None
    knowable_through: datetime | None
    values_consumed: int
    industries: Mapping[str, str]
    refit_day: date | None


class _RecordingFeed:
    """A `ScoreFeed` that remembers what it handed over, so the day's inputs can be dated."""

    def __init__(self, feed: ScoreFeed) -> None:
        self._feed = feed
        self.rows: list[ScoreRow] = []
        self.observations: list[ICObservation] = []

    def rows_on(self, day: date) -> Sequence[ScoreRow]:
        rows = self._feed.rows_on(day)
        self.rows.extend(rows)
        return rows

    def ic_observations_through(self, day: date) -> Sequence[ICObservation]:
        handed = self._feed.ic_observations_through(day)
        self.observations.extend(handed)
        return handed

    def fit_on(self, day: date) -> WalkForwardFit | None:
        return self._feed.fit_on(day)

    def refits(self) -> tuple[WalkForwardFit, ...]:
        return self._feed.refits()


def score_day(
    store: PanelStore,
    request: StrategyRequest,
    *,
    day: date,
    anchor: date,
    read_industries: bool | None = None,
    fit_cache: MutableMapping[tuple[date, datetime, object], WalkForwardFit] | None = None,
) -> SignalDay:
    """Score one session under `request`'s source, as a backtest over it would (`V2-P6-011`).

    The daily command's reader. After a session's close there is no next session to trade on,
    so `backtest_strategy` cannot be asked about it; this runs the same feeds and the book's own
    scorer (`score_signal_day`) for that one day instead. `request.start` and `request.end` are
    not read -- the day is `day`, read at `request.as_of` -- and everything else is: the source,
    the spec, the transforms and the exchange.

    `anchor` is the session a backtest of this source would have started on. It decides one
    thing: which refit a walk-forward source's fit in use on `day` came from. Refits fall on every
    `refit_every_sessions`-th session from `anchor`, so the fit in use is the newest one on or
    before `day`, trained on the window ending on that refit session -- the fit a backtest from
    `anchor` through `day` would have used. A static or trailing-IC source reads the same
    lookback a backtest starting on `day` reads, which is the one a backtest from `anchor` reads
    on that day.

    `read_industries` decides whether the day's industry cross section is read: by default
    exactly when the spec caps industries. A caller that knows the day's decision will not read
    it -- no rebalance today -- passes `False`, and then the day reads no membership at all.

    A registered-prediction source is refused: there is nothing to score forward. Refusals are
    this face's three rows, as `backtest_strategy` raises them.
    """
    source = request.source
    if source.prediction_ids:
        raise StrategyRequestError(
            "a source of registered predictions has nothing to score for a new session; name "
            "the components, trailing-IC weights or walk-forward model that produced them"
        )
    if anchor > day:
        raise StrategyRequestError(
            f"the schedule anchor {anchor.isoformat()} is after the day scored {day.isoformat()}"
        )
    calendar = _read(
        lambda: load_trading_calendar(
            store,
            exchange=request.exchange,
            years=tuple(range(anchor.year, day.year + 1)),
            as_of=request.as_of,
        ),
        store=store,
        what=f"the {request.exchange} trading calendar",
    )
    schedule = _read(
        lambda: calendar.trading_days_between(anchor, day),
        store=store,
        what="the sessions from the anchor to the day scored",
    )
    if not schedule or schedule[-1] != day:
        raise StrategyRunBlockedError(
            f"{day.isoformat()} is not an open {request.exchange} session of the stored calendar"
        )
    first, refit_day = day, None
    if source.walk_forward is not None:
        position = len(schedule) - 1
        refit_day = schedule[position - position % source.walk_forward.refit_every_sessions]
        first = refit_day
    day_request = dataclasses.replace(request, start=first, end=day)
    sessions = tuple(session for session in schedule if session >= first)
    lookback: tuple[date, ...] = ()
    years = day_request.years
    if source.trailing_ic is not None:
        lookback, years = _lookback(store, day_request, source.trailing_ic.ic_window_sessions - 1)
    elif source.walk_forward is not None:
        lookback, years = _lookback(store, day_request, source.walk_forward.train_sessions - 1)
    instants = {session: session_publication_instant(session) for session in lookback + sessions}
    signal_days = frozenset({day})
    model_feed: _ModelFeed | None = None
    feed: ScoreFeed
    if source.walk_forward is not None:
        model_feed = _ModelFeed(
            store,
            day_request,
            sessions=sessions,
            calendar=lookback + sessions,
            signal_days=signal_days,
            instants=instants,
            years=years,
            fit_cache=fit_cache,
        )
        feed = model_feed
    else:
        feed = _FactorFeed(
            store,
            day_request,
            pairs=(
                source.trailing_ic.components
                if source.trailing_ic is not None
                else tuple((token, tier) for token, tier, _ in source.components)
            ),
            signal_days=signal_days,
            calendar=lookback + sessions,
            instants=instants,
            years=years,
        )
    recording = _RecordingFeed(feed)
    context = SignalDayInputs(source=source, calendar=lookback + sessions, signal_instants=instants)
    try:
        scores = _read(
            lambda: score_signal_day(context, recording, day),
            store=store,
            what=f"the scores of {day.isoformat()}",
        )
    except StrategyBacktestError as error:
        raise StrategyRunBlockedError(f"the day could not be scored: {error}") from error
    instant = instants[day]
    known = [row.available_time for row in recording.rows] + [
        item.known_at
        for item in recording.observations
        if item.ic is not None and item.known_at <= instant
    ]
    if read_industries is None:
        read_industries = request.spec.max_industry_weight is not None
    industries: Mapping[str, str] = (
        dict(_IndustryDays(store, signal_days, instants)[day]) if read_industries else {}
    )
    return SignalDay(
        day=day,
        instant=instant,
        anchor=anchor,
        position=len(schedule) - 1,
        scores=scores,
        fit=None if model_feed is None else model_feed.fit_on(day),
        model_batch=None if model_feed is None else model_feed.last_batch,
        knowable_through=max(known) if known else None,
        values_consumed=len(known),
        industries=industries,
        refit_day=refit_day,
    )


# --- the per-instant IC series (V2-P6-014, for V2-P6-008) ---------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ICSeriesRequest:
    """One factor tier's daily IC series: which tier, which label, which correlation, which days.

    Built by `ic_series_request`, which refuses a question that cannot be put before a store is
    opened. `min_securities` is `FactorICSpec.min_securities` and has no default for its reason.
    """

    definition: FactorDefinition
    tier: str
    transform: FactorTransformSpec | None
    neutralization: FactorNeutralizationSpec | None
    horizon_sessions: int
    ic_method: ICMethod
    min_securities: int
    start: date
    end: date
    as_of: datetime
    exchange: str

    @property
    def years(self) -> tuple[int, ...]:
        """Every partition year the prediction days touch, ascending."""
        return tuple(range(self.start.year, self.end.year + 1))


@dataclass(frozen=True, slots=True, kw_only=True)
class ICSeriesPoint:
    """One prediction day's IC, as `factor_ic` measured it, and what its sample was.

    `point` is `FactorICStudy.measure`'s own `ICPoint` -- coverage code, sample size, raw and
    oriented IC -- and `census` is the `ICCensus` of the cross section it measured, so a day that
    has no IC says why and a day that has one says how many names it stood on.
    """

    prediction_day: date
    build: datetime
    """The stored cross section measured: the newest build at or before the day's 16:30."""
    known_at: datetime
    """The later of that build's instant and the 16:30 of the session its label exits on."""
    point: ICPoint
    census: ICCensus

    @property
    def ic(self) -> float | None:
        """The oriented IC (positive means the factor worked), or `None` when not measured."""
        return self.point.ic

    @property
    def n_securities(self) -> int:
        """How many labelled securities the correlation was taken over."""
        return self.point.sample_size


@dataclass(frozen=True, slots=True, kw_only=True)
class ICSeries:
    """A factor tier's IC on every prediction day in the range it has a stored build for."""

    request: ICSeriesRequest
    points: tuple[ICSeriesPoint, ...]


def ic_series_request(
    *,
    factor: str,
    tier: str,
    transform: str | None,
    neutralization: str | None,
    horizon_sessions: int,
    ic_method: str,
    min_securities: int,
    start: date,
    end: date,
    as_of: datetime,
    exchange: str,
    transforms: FactorTransformRegistry = FACTOR_TRANSFORMS,
    neutralizations: FactorNeutralizationRegistry = FACTOR_NEUTRALIZATIONS,
) -> ICSeriesRequest:
    """Resolve an IC-series question. Touches no store; every refusal is `bad_request`.

    The transform and neutralization rules are `strategy_request`'s: a transform exactly when the
    tier is `processed` or `neutralized`, a neutralization exactly when it is `neutralized`.
    """
    try:
        definition = resolve_factor(factor.strip())
    except FactorRequestError as error:
        raise StrategyRequestError(str(error)) from error
    if tier not in STRATEGY_TIERS:
        raise StrategyRequestError(f"tier {tier!r} is not one of {list(STRATEGY_TIERS)}")
    if (tier in ("processed", "neutralized")) != (transform is not None):
        raise StrategyRequestError(
            "transform is required exactly when the tier is processed or neutralized"
        )
    if (tier == "neutralized") != (neutralization is not None):
        raise StrategyRequestError(
            "neutralization is required exactly when the tier is neutralized"
        )
    if ic_method not in IC_METHODS:
        raise StrategyRequestError(f"ic_method {ic_method!r} is not one of {sorted(IC_METHODS)}")
    if min_securities < MINIMUM_IC_SECURITIES:
        raise StrategyRequestError(
            f"min_securities {min_securities} is below {MINIMUM_IC_SECURITIES}, the fewest names "
            "a correlation is not decided by arithmetic alone"
        )
    if horizon_sessions < 1:
        raise StrategyRequestError(f"horizon_sessions {horizon_sessions} must be at least 1")
    if end < start:
        raise StrategyRequestError(
            f"--start {start.isoformat()} must not be after --end {end.isoformat()}; the end is "
            "before the start"
        )
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise StrategyRequestError(f"as_of must be timezone-aware; got {as_of.isoformat()!r}")
    try:
        transform_spec = None if transform is None else transforms.get(transform.strip())
        neutralization_spec = (
            None if neutralization is None else neutralizations.get(neutralization.strip())
        )
    except ValueError as error:
        raise StrategyRequestError(str(error)) from error
    return ICSeriesRequest(
        definition=definition,
        tier=tier,
        transform=transform_spec,
        neutralization=neutralization_spec,
        horizon_sessions=horizon_sessions,
        ic_method=ic_method,  # type: ignore[arg-type]
        min_securities=min_securities,
        start=start,
        end=end,
        as_of=as_of,
        exchange=exchange,
    )


def factor_ic_series(store: PanelStore, request: ICSeriesRequest) -> ICSeries:
    """The tier's IC on every session in `[start, end]` it has a stored build for.

    The same computation a trailing-IC source weighs with -- `_year_sections` picks each day's
    build at its 16:30, `_measure_year` labels it through `model_view.OutcomeLabels` and measures
    it with `ic_cross_section` and `FactorICStudy` -- so the series and the weights cannot
    disagree. Each tier-year partition is read once and dropped before the next year's.

    Refuses, as `blocked`, a range whose last label has not closed at `as_of`: those ICs do not
    exist yet, and a series quietly cut short would read as a complete one.
    """
    try:
        return _ic_series(store, request)
    except StrategyBacktestError as error:
        raise StrategyRunBlockedError(f"the IC series could not be measured: {error}") from error


def _ic_series(store: PanelStore, request: ICSeriesRequest) -> ICSeries:
    registered = set(store.registered_years(TRADING_CALENDAR_DATASET))
    following = request.end.year + 1
    years = (*request.years, *((following,) if following in registered else ()))
    calendar = _read(
        lambda: load_trading_calendar(
            store, exchange=request.exchange, years=years, as_of=request.as_of
        ),
        store=store,
        what=f"the {request.exchange} trading calendar",
    )
    sessions = calendar.trading_days
    days = tuple(day for day in sessions if request.start <= day <= request.end)
    if not days:
        raise StrategyRunBlockedError(
            f"{request.start.isoformat()}..{request.end.isoformat()} holds no open session"
        )
    exit_position = sessions.index(days[-1]) + 1 + request.horizon_sessions
    if (
        exit_position >= len(sessions)
        or session_publication_instant(sessions[exit_position]) > request.as_of
    ):
        raise StrategyRunBlockedError(
            f"the {request.horizon_sessions}-session label of {days[-1].isoformat()} has not "
            f"closed at {request.as_of.isoformat()}: its exit session is not published yet, so "
            "that IC does not exist. End the range earlier or read later"
        )
    instants = {day: session_publication_instant(day) for day in sessions}
    pair = (request.definition.qualified_key, request.tier)
    study = _ic_study(
        request.definition, method=request.ic_method, min_securities=request.min_securities
    )
    names: dict[str, str] = {}
    points: list[ICSeriesPoint] = []
    for year in sorted({day.year for day in days}):
        sections = {
            pair: _year_sections(
                store,
                request,
                request.definition,
                request.tier,
                year,
                days=frozenset(day for day in days if day.year == year),
                instants=instants,
                names=names,
            )
        }
        measured = _from_the_model_plane(
            partial(
                _measure_year,
                store,
                request,
                exchange=request.exchange,
                years=years,
                year=year,
                sections=sections,
                studies={pair: study},
                horizon=parse_horizon(f"{request.horizon_sessions}d"),
                instants=instants,
            )
        )
        points.extend(
            ICSeriesPoint(
                prediction_day=item.prediction_day,
                build=item.build,
                known_at=item.known_at,
                point=item.point,
                census=item.census,
            )
            for _, item in measured
        )
    return ICSeries(request=request, points=tuple(points))


def ic_series_view(series: ICSeries) -> dict[str, object]:
    """One IC series as plain JSON-shaped data: the question, then one row per prediction day."""
    request = series.request
    return {
        "factor": request.definition.qualified_key,
        "factor_id": request.definition.factor_id,
        "tier": request.tier,
        "transform": None if request.transform is None else request.transform.qualified_key,
        "neutralization": (
            None if request.neutralization is None else request.neutralization.qualified_key
        ),
        "horizon_sessions": request.horizon_sessions,
        "ic_method": request.ic_method,
        "min_securities": request.min_securities,
        "start": request.start.isoformat(),
        "end": request.end.isoformat(),
        "as_of": request.as_of.isoformat(),
        "points": [
            {
                "prediction_day": point.prediction_day.isoformat(),
                "build": point.build.isoformat(),
                "known_at": point.known_at.isoformat(),
                "coverage": point.point.coverage,
                "ic": point.ic,
                "raw_ic": point.point.raw_ic,
                "n_securities": point.n_securities,
                "census": {
                    "subject_count": point.census.subject_count,
                    "admitted_count": point.census.admitted_count,
                    "excluded_by_coverage": dict(point.census.excluded_by_coverage),
                    "unlabelled_count": point.census.unlabelled_count,
                    "unmatched_count": point.census.unmatched_count,
                },
            }
            for point in series.points
        ],
    }


# --- rendering ----------------------------------------------------------------------------------


def backtest_view(result: StrategyBacktest) -> dict[str, object]:
    """One backtest as `openalpha strategy backtest --json` prints it and the SDK can render it.

    `model_dump(mode="json")` of the answer, so every `Decimal` is a string and nothing is
    rounded through a float, plus each limitation's detail beside its code.
    """
    body: dict[str, object] = result.model_dump(mode="json")
    body["limitation_details"] = {
        item.code: item.detail for item in KNOWN_STRATEGY_BACKTEST_LIMITATIONS
    }
    return body
