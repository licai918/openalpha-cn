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
  day's signal instant through `load_industry_cross_section`, the as-of-sensitive door.
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
"""

from __future__ import annotations

import statistics
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import ClassVar, Final, Literal, Protocol, TypeVar
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.execution import (
    CostSchedule,
    MarketBar,
    published_limit_fields,
    suspended_at_the_close,
)
from openalpha_cn.backtest.factor_ic import (
    MINIMUM_IC_AS_OFS,
    TIER_ADMITTED_CODES,
    FactorICError,
    FactorICSpec,
    FactorICStudy,
    ic_cross_section,
)
from openalpha_cn.backtest.factor_tradeability import CNY_PER_TURNOVER_UNIT
from openalpha_cn.backtest.strategy_backtest import (
    EQUAL_WEIGHT_ALL_A,
    KNOWN_STRATEGY_BACKTEST_LIMITATIONS,
    MODEL_COMPONENT,
    PREDICTION_COMPONENT,
    ICObservation,
    ScoreRow,
    ScoreSource,
    SessionQuote,
    StrategyBacktest,
    StrategyBacktestError,
    StrategyInputs,
    StrategySpec,
    TrailingICWeights,
    WalkForwardFit,
    WalkForwardModel,
    component_key,
    run_strategy_backtest,
    usable_fit,
    walk_forward_fits,
)
from openalpha_cn.domain.adjustment import AdjustmentHistory, AdjustmentHorizonError
from openalpha_cn.domain.alpha_model import AlphaModel, AlphaModelDeclaration, AlphaModelError
from openalpha_cn.domain.daily_prices import DailyBar, PriceDataError
from openalpha_cn.domain.factor import FactorDefinition
from openalpha_cn.domain.factor_neutralization import (
    FactorNeutralizationRegistry,
    FactorNeutralizationSpec,
)
from openalpha_cn.domain.factor_transform import FactorTransformRegistry, FactorTransformSpec
from openalpha_cn.domain.horizon import ResearchHorizon, parse_horizon
from openalpha_cn.domain.index_prices import IndexPriceError, index_session_returns
from openalpha_cn.domain.industry_classification import (
    INDUSTRY_MEMBERSHIP_DATASET,
    IndustryClassificationError,
)
from openalpha_cn.domain.labels import HaltCorpus, OutcomeLabel, halt_corpus_for_years
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.price_limits import PriceLimit, TradingState
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.factor_view import FactorRequestError, resolve_factor
from openalpha_cn.feature_matrix import FeatureColumn, FeatureMatrixError, feature_spec
from openalpha_cn.model_view import (
    MODEL_FAMILIES,
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
    load_factor_observations,
    load_processed_factor_observations,
)
from openalpha_cn.panel_ingest import (
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
    load_neutralized_factor_observations,
)
from openalpha_cn.panel_view import PANEL_STORE_PLACEHOLDER, without_store_path

__all__ = [
    "PROTOCOL_BENCHMARKS",
    "PROTOCOL_COSTS",
    "PROTOCOL_PARTICIPATION_CAP",
    "PROTOCOL_POSITION_CAPITAL",
    "PROTOCOL_SLIPPAGE_RATE",
    "StrategyPanelUnreadableError",
    "StrategyRequest",
    "StrategyRequestError",
    "StrategyRunBlockedError",
    "StrategyViewError",
    "backtest_strategy",
    "backtest_view",
    "load_strategy_inputs",
    "strategy_request",
]

SHANGHAI: Final[ZoneInfo] = ZoneInfo(DEFAULT_DATE_TIMEZONE)

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
) -> StrategyBacktest:
    """Read the panel into `StrategyInputs` and run the book: the one entry both faces call.

    `predictions` looks a registered prediction up by id; it is required exactly when the
    source names `prediction_ids`. A `StrategyBacktestError` -- look-ahead, a signal day with no
    cross section, a benchmark gap -- is `blocked`, and that holds for one raised while the
    inputs are ASSEMBLED as much as for one raised while the book runs: a stored score that
    `ScoreRow`'s own contract refuses (a non-finite value, a naive clock) is the same kind of
    refusal and must not reach a face as an unanticipated error.
    """
    try:
        inputs = load_strategy_inputs(store, request, predictions=predictions)
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
) -> StrategyInputs:
    """Everything the book reads, out of the panel, at `request.as_of`."""
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
    step = request.spec.rebalance_every_sessions
    signal_days = frozenset(sessions[index] for index in range(0, len(sessions) - 1, step))
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
    observations: tuple[ICObservation, ...] = ()
    fits: tuple[WalkForwardFit, ...] = ()
    fit_for_day: dict[date, WalkForwardFit] = {}
    if source.prediction_ids:
        scores = _prediction_rows(source.prediction_ids, predictions)
    elif source.walk_forward is not None:
        scores, fits, fit_for_day = _from_the_model_plane(
            lambda: _model_rows(
                store,
                request,
                sessions=sessions,
                calendar=lookback + sessions,
                signal_days=signal_days,
                instants=instants,
                years=years,
            )
        )
    else:
        pairs = tuple((token, tier) for token, tier, _ in source.components)
        if source.trailing_ic is not None:
            pairs = source.trailing_ic.components
            observations = _from_the_model_plane(
                lambda: _ic_observations(
                    store,
                    request,
                    calendar=lookback + sessions,
                    signal_days=signal_days,
                    instants=instants,
                    years=years,
                )
            )
        scores = _factor_rows(store, request, signal_days, instants, pairs=pairs)
    return StrategyInputs(
        source=source,
        sessions=sessions,
        signal_instants=instants,
        scores=scores,
        quotes=_QuoteDays(days, sessions),
        benchmark_returns=benchmarks,
        industries=industries,
        lookback_sessions=lookback,
        ic_observations=observations,
        model_fits=fits,
        fit_for_day=fit_for_day,
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

    Read from the contiguous run of registered `trade_cal` years ending the year before
    `--start`, reaching back no more years than `needed` sessions can span (an A-share year has
    well over 200 sessions). Fewer are returned when the stored calendar stops earlier -- see
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
    if not earlier:
        return (), request.years
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
    value: float
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


def _admitted(rows: Sequence[_StoredRow], tier: str) -> list[_Observed]:
    admitted = TIER_ADMITTED_CODES[tier]
    return [
        _Observed(subject=row.subject, as_of=row.as_of, value=row.value, coverage=row.coverage)
        for row in rows
        if row.coverage in admitted and row.value is not None
    ]


def _tier_rows(
    store: PanelStore,
    request: StrategyRequest,
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
    return _admitted(rows, tier)


def _factor_rows(
    store: PanelStore,
    request: StrategyRequest,
    signal_days: frozenset[date],
    instants: Mapping[date, datetime],
    *,
    pairs: Sequence[tuple[str, str]],
) -> tuple[ScoreRow, ...]:
    """Every `(factor, tier)` pair's rows on the signal days, oriented, one build per day."""
    rows: list[ScoreRow] = []
    for token, tier in pairs:
        definition = request.definitions[token]
        sign = -1.0 if definition.direction == "lower_is_better" else 1.0
        key = component_key(token, tier)
        for year in request.years:
            by_build: dict[date, dict[datetime, list[_Observed]]] = {}
            for observed in _tier_rows(store, request, definition, tier, year):
                day = observed.as_of.astimezone(SHANGHAI).date()
                if day in signal_days:
                    by_build.setdefault(day, {}).setdefault(observed.as_of, []).append(observed)
            for day, builds in by_build.items():
                instant = _chosen_build(builds, instants[day])
                rows.extend(
                    ScoreRow(
                        component=key,
                        subject=observed.subject,
                        signal_day=day,
                        value=sign * observed.value,
                        available_time=instant,
                        revision_time=instant,
                    )
                    for observed in builds[instant]
                )
    return tuple(rows)


def _chosen_build(builds: Mapping[datetime, object], signal: datetime) -> datetime:
    """The latest build at or before the signal instant, else the earliest (which is refused)."""
    visible = [instant for instant in builds if instant <= signal]
    return max(visible) if visible else min(builds)


def _prediction_rows(
    identifiers: Sequence[str],
    predictions: Callable[[str], PredictionRecord | None] | None,
) -> tuple[ScoreRow, ...]:
    if predictions is None:
        raise StrategyRequestError(
            "the source names prediction_ids and no prediction store was supplied to read them"
        )
    rows: list[ScoreRow] = []
    for identifier in identifiers:
        record = predictions(identifier)
        if record is None:
            raise StrategyRunBlockedError(f"no prediction is held under {identifier}")
        batch = record.batch
        day = batch.as_of.astimezone(SHANGHAI).date()
        available = max(batch.predicted_at, record.recorded_at)
        rows.extend(
            ScoreRow(
                component=PREDICTION_COMPONENT,
                subject=prediction.ts_code,
                signal_day=day,
                value=prediction.score,
                available_time=available,
                revision_time=record.recorded_at,
            )
            for prediction in batch.predictions
            if prediction.score is not None
        )
    return tuple(rows)


def _cached_label_sessions(horizon_sessions: int) -> int:
    """How many sessions' bars the label reader keeps: every window's, with room to spare."""
    return horizon_sessions + 8


def _ic_observations(
    store: PanelStore,
    request: StrategyRequest,
    *,
    calendar: tuple[date, ...],
    signal_days: frozenset[date],
    instants: Mapping[date, datetime],
    years: tuple[int, ...],
) -> tuple[ICObservation, ...]:
    """Every component's IC on every prediction day a trailing window could use.

    The prediction days are the calendar sessions from the first signal day's window start to the
    last day whose label can have exited by the last signal day -- a narrowing by calendar
    arithmetic only, so no window is priced that no signal could ever use; which ICs a signal day
    DOES use is the book's decision (`_ICIndex.weights`). Each day's cross section is the build
    `_chosen_build` picks at that day's signal instant, the IC is `ic_cross_section` measured by
    `FactorICStudy`, and one year of each tier is held at a time.
    """
    spec = request.source.trailing_ic
    assert spec is not None  # load_strategy_inputs calls this for a trailing-IC source only
    horizon = parse_horizon(f"{spec.horizon_sessions}d")
    position = {day: index for index, day in enumerate(calendar)}
    first = max(position[min(signal_days)] - spec.ic_window_sessions + 1, 0)
    last = position[max(signal_days)] - spec.horizon_sessions - 1
    wanted = frozenset(calendar[first : last + 1]) if last >= first else frozenset()
    if not wanted:
        return ()
    reader = OutcomeLabels(
        store,
        LabelReach(as_of=request.as_of, years=years, exchange=request.exchange),
        cached_sessions=_cached_label_sessions(spec.horizon_sessions),
    )
    studies = {
        component_key(token, tier): FactorICStudy(
            FactorICSpec(
                definition=request.definitions[token],
                method=spec.ic_method,
                min_securities=spec.min_ic_securities,
                min_as_ofs=MINIMUM_IC_AS_OFS,
            )
        )
        for token, tier in spec.components
    }
    observations: list[ICObservation] = []
    for year in sorted({day.year for day in wanted}):
        by_day: dict[date, dict[tuple[str, str], tuple[datetime, list[_Observed]]]] = {}
        for token, tier in spec.components:
            builds: dict[date, dict[datetime, list[_Observed]]] = {}
            for observed in _tier_rows(store, request, request.definitions[token], tier, year):
                day = observed.as_of.astimezone(SHANGHAI).date()
                if day in wanted:
                    builds.setdefault(day, {}).setdefault(observed.as_of, []).append(observed)
            for day, per_build in builds.items():
                chosen = _chosen_build(per_build, instants[day])
                by_day.setdefault(day, {})[(token, tier)] = (chosen, per_build[chosen])
        for day in sorted(by_day):
            labels: dict[tuple[str, date, date], OutcomeLabel | None] = {}
            for (token, tier_name), (build, rows) in by_day[day].items():
                observations.append(
                    _ic_observation(
                        reader,
                        studies[component_key(token, tier_name)],
                        component=component_key(token, tier_name),
                        tier=tier_name,
                        build=build,
                        rows=rows,
                        horizon=horizon,
                        labels=labels,
                        instants=instants,
                    )
                )
    return tuple(observations)


def _ic_observation(
    reader: OutcomeLabels,
    study: FactorICStudy,
    *,
    component: str,
    tier: str,
    build: datetime,
    rows: Sequence[_Observed],
    horizon: ResearchHorizon,
    labels: dict[tuple[str, date, date], OutcomeLabel | None],
    instants: Mapping[date, datetime],
) -> ICObservation:
    """One build's IC against its labels, and the instant it became knowable.

    `labels` is shared across one prediction day's components, whose windows are one window.
    """
    window = reader.window(build, horizon=horizon)
    paired: dict[str, OutcomeLabel] = {}
    for row in rows:
        key = (row.subject, window.entry_day, window.exit_day)
        if key not in labels:
            labels[key] = reader.label(row.subject, window)
        label = labels[key]
        if label is not None:
            paired[row.subject] = label
    try:
        point = study.measure(
            ic_cross_section(
                as_of=build,
                tier=tier,  # type: ignore[arg-type]
                rows=[(row.subject, row.value, row.coverage) for row in rows],
                labels=paired,
            )
        )
    except FactorICError as error:
        raise StrategyBacktestError(
            f"the {component} IC at {build.isoformat()} could not be measured: {error}"
        ) from error
    known_at = max(build, instants[window.exit_day])
    return ICObservation(
        component=component, prediction_day=window.prediction_day, known_at=known_at, ic=point.ic
    )


def _model_rows(
    store: PanelStore,
    request: StrategyRequest,
    *,
    sessions: tuple[date, ...],
    calendar: tuple[date, ...],
    signal_days: frozenset[date],
    instants: Mapping[date, datetime],
    years: tuple[int, ...],
) -> tuple[tuple[ScoreRow, ...], tuple[WalkForwardFit, ...], dict[date, WalkForwardFit]]:
    """A walk-forward source's fits, the fit each signal day scores with, and its scores.

    Refits fall on `sessions[0]`, `sessions[F]`, ... up to the last signal day. The training
    panel is `model_view.training_panel` over the prediction days any refit window can reach,
    closed by the last refit's embargo deadline; `walk_forward_fits` narrows it per refit. A
    signal day with a fit in use is scored on `model_view.feature_cross_section` at its signal
    instant, and every score row carries that instant on both clocks.
    """
    spec = request.source.walk_forward
    model = request.model
    assert spec is not None and model is not None  # strategy_request resolved both together
    position = {day: index for index, day in enumerate(calendar)}
    refit_days = tuple(
        day for day in sessions[:: spec.refit_every_sessions] if day <= max(signal_days)
    )
    last_refit = position[refit_days[-1]]
    newest_training = last_refit - spec.embargo_sessions - spec.horizon_sessions - 2
    run = ModelRunRequest(
        declaration=model.declaration,
        columns=request.columns,
        missing=spec.missing,
        start=calendar[max(position[sessions[0]] - spec.train_sessions + 1, 0)],
        end=calendar[max(newest_training, 0)],
        as_of=request.as_of,
        years=years,
        exchange=request.exchange,
        horizon=parse_horizon(f"{spec.horizon_sessions}d"),
        minimum_scored_ratio=0.0,
        shelf_life=None,
        # Never filed: this request drives reads only, and `config_digest` feeds a daily run's
        # manifest, which a backtest does not write.
        config_digest="0" * 64,
        declared_feature_version=None,
    )
    panel = (
        None
        if newest_training < 0
        else training_panel(
            store,
            run,
            deadline=instants[calendar[last_refit - spec.embargo_sessions]],
            cached_sessions=_cached_label_sessions(spec.horizon_sessions),
        )
    )
    fits = walk_forward_fits(
        model,
        () if panel is None else panel.examples,
        feature_ids=feature_spec(columns=request.columns, missing=spec.missing).feature_ids,
        spec=spec,
        calendar=calendar,
        instants=instants,
        refit_days=refit_days,
    )
    rows: list[ScoreRow] = []
    fit_for_day: dict[date, WalkForwardFit] = {}
    for day in sorted(signal_days):
        fit = usable_fit(fits, signal_day=day)
        if fit is None or fit.fitted is None:
            continue
        instant = instants[day]
        section = feature_cross_section(store, run, as_of=instant)
        try:
            batch = fit.fitted.predict(section.cross_section, predicted_at=instant, shelf_life=None)
        except (AlphaModelError, ValueError) as error:
            raise StrategyBacktestError(
                f"the fit refitted on {fit.refit_day.isoformat()} could not score the cross "
                f"section visible at {instant.isoformat()}: {error}"
            ) from error
        fit_for_day[day] = fit
        rows.extend(
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
        )
    return tuple(rows), fits, fit_for_day


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
        self.adjustments: Mapping[str, AdjustmentHistory] = _read(
            lambda: load_adjustment_histories(
                store, years=request.years, as_of=request.as_of, max_staleness=None
            ),
            store=store,
            what="the adjustment factors",
        )

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
        history = self.adjustments.get(subject)
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
        self._years = tuple(sorted(store.registered_years(INDUSTRY_MEMBERSHIP_DATASET)))
        self._held: dict[date, Mapping[str, str]] = {}

    def __getitem__(self, day: date) -> Mapping[str, str]:
        if day not in self._days:
            raise KeyError(day)
        if day not in self._held:
            years = tuple(year for year in self._years if year <= day.year)
            if not years:
                raise StrategyRunBlockedError(
                    f"--max-industry-weight needs industry memberships and no "
                    f"{INDUSTRY_MEMBERSHIP_DATASET} partition at or before {day.year} is "
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
            self._held[day] = {code: answer.l1_code for code, answer in answers.items()}
        return self._held[day]

    def __iter__(self) -> Iterator[date]:
        return iter(sorted(self._days))

    def __len__(self) -> int:
        return len(self._days)


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
