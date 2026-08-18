"""The immutable factor experiment artifact and its three-tier report (`V2-P3-014`).

Seven properties this file exists to hold, and the first is the issue's acceptance criterion:

1. **A factor that earns an exposure rather than its own edge is visible without arithmetic.**
   `test_a_factor_whose_edge_is_its_exposure_reports_removed_on_the_neutralisation_step` builds
   one factor whose raw and processed rank IC is exactly 1.0 and whose neutralised rank IC is
   exactly 0.0 -- the neutralised values are a permutation chosen so that the Pearson correlation
   of the two average-rank vectors is 0.0 at the last bit -- and requires the artifact to say
   `removed` in a named cell with a retention of exactly 0.0. Nothing in that assertion compares
   two numbers; the comparison is the artifact's.
2. **The verdict cannot contradict its own two numbers.** `TierAttribution` recomputes the ratio
   and the ladder in its validator, so the six ways of mislabelling a cell are refused at the
   type rather than caught by a reader. Every rung of the ladder is driven, and the two rungs a
   naive version would pool -- `no_baseline` against `removed` -- are driven on fixtures that
   differ in exactly the sign of the earlier tier's statistic.
3. **Immutability is two mechanisms and both are measured.** Editing any declared field of the
   spec moves `experiment_id`; editing any number in any row moves `content_digest` and leaves
   `experiment_id` alone; a serialised record with one leaf perturbed refuses to reopen; and two
   records that share an `experiment_id` and disagree are refused while two that agree are not.
4. **The identity's contents are audited, not listed.** The builder's own signature is read and
   partitioned into "measured to move the id" and "named in `IDENTITY_EXEMPT_PARAMETERS` with a
   reason", so a twelfth parameter fails until somebody classifies it.
5. **Four upstream studies keep their own vocabularies.** A tier report carries four coverage
   codes side by side and this module adds no fifth spelling of "insufficient"; the only place a
   shortfall is synthesised is the grid, where it is `not_measured` with the two tiers' own codes
   still readable.
6. **Every field of the report is separately falsifiable.**
   `test_every_field_of_a_sealed_record_refuses_to_reopen_after_a_single_edit` walks every scalar
   leaf of the serialised record and perturbs exactly one at a time, so a field that were rendered
   and never asserted would still be held by the seal -- which is the direction 100% line coverage
   is worth nothing in, and on which `V2-P3-006`, `007` and `008` between them had twenty
   surviving mutants.
7. **The tiers are held to one sample.** Three rows over three different sets of `as_of`s would
   make every attribution a comparison of a factor against a calendar, and the refusal is driven
   at both the row level and the artifact level.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import AShareExecutionPolicy, CostSchedule, MarketBar
from openalpha_cn.backtest.factor_experiment import (
    ATTRIBUTION_CELL_ORDER,
    ATTRIBUTION_STATISTIC_ORDER,
    ATTRIBUTION_STEPS,
    ATTRIBUTION_VERDICT_CODES,
    ATTRIBUTION_VERDICT_ORDER,
    EXPERIMENT_LIMITATION_CODES,
    IDENTITY_EXEMPT_PARAMETERS,
    KNOWN_EXPERIMENT_LIMITATIONS,
    MINIMUM_EXPERIMENT_TIERS,
    RETENTIONLESS_VERDICTS,
    AttributionStatistic,
    FactorExperimentArtifact,
    FactorExperimentError,
    FactorExperimentRecord,
    FactorExperimentSpec,
    TierAttribution,
    TierReport,
    build_factor_experiment,
    builder_parameters,
    experiment_payload,
    open_experiment,
    refuse_a_restated_experiment,
)
from openalpha_cn.backtest.factor_ic import (
    FACTOR_TIER_ORDER,
    FactorICSpec,
    FactorICStudy,
    FactorTier,
    ICCrossSection,
    ICPoint,
    ICSummary,
    neutralized_cross_section,
    processed_cross_section,
    raw_cross_section,
)
from openalpha_cn.backtest.factor_portfolio import (
    PeriodPortfolio,
    QuantilePortfolioSpec,
    QuantilePortfolioStudy,
    QuantilePortfolioSummary,
)
from openalpha_cn.backtest.factor_redundancy import (
    RedundancySpec,
    RedundancyStudy,
    RedundancySummary,
    correlate_cross_section,
    factor_vector,
)
from openalpha_cn.backtest.factor_tradeability import (
    PeriodTradeability,
    TradeabilitySpec,
    TradeabilityStudy,
    TradeabilitySummary,
    TurnoverSeries,
    liquidity_from_amount,
)
from openalpha_cn.domain.adjustment import FactorObservation as AdjustmentFactor
from openalpha_cn.domain.adjustment import build_adjustment_history
from openalpha_cn.domain.daily_prices import DailyBar
from openalpha_cn.domain.factor import FactorDefinition, FactorField, FactorNote, FactorObservation
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
from openalpha_cn.panel_factors import FACTOR_DEFINITIONS

SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")

AS_OF: Final[datetime] = datetime(2026, 6, 10, 8, 30, tzinfo=UTC)
"""16:30 Asia/Shanghai on 2026-06-10, so the prediction day is the 10th and the entry the 11th."""

AS_OF_2: Final[datetime] = datetime(2026, 6, 11, 8, 30, tzinfo=UTC)
"""The next session. At `1d` its entry is 2026-06-12, which is `AS_OF`'s exit exactly -- the
tightest schedule on which a holdings state exists, so `TurnoverSeries` is `measured` and not
`overlapping_schedule`."""

AS_OFS: Final[tuple[datetime, ...]] = (AS_OF, AS_OF_2)
"""Two as_ofs, which is `MINIMUM_IC_AS_OFS` and `MINIMUM_PORTFOLIO_PERIODS` exactly -- the
smallest sample on which a mean, a sample standard deviation and one rebalance all exist."""

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
        for index in range(1, 20)
    ),
)

RANKS: Final[tuple[int, ...]] = (1, 2, 3, 4)
"""Four securities: `MINIMUM_REDUNDANCY_SECURITIES` exactly, and `group_count *
min_securities_per_group` for the declared two-by-two cut. Four is also the first size at which a
rank correlation of exactly zero is attainable, which the neutralised tier below needs."""

CAPITAL: Final[Decimal] = Decimal("100000")
"""Enough notional at a ¥100 close that `CostSchedule.minimum_commission`'s ¥5 floor never binds,
so the cost drag on every position is the same 0.112% and no group's fee is a fixture accident."""

RETENTION_FLOOR: Final[float] = 0.4
"""The declared line, chosen **away** from the fixture's own spread retention rather than at it.

The neutralised tier keeps `0.49999999999999445` of the processed tier's net spread on this
fixture -- half, to fourteen places, because a two-by-two cut of four names either keeps a name in
the long group or does not. A floor of `0.5` would therefore decide that cell by the last bits of
two fee-bearing quotients, and a test whose verdict turns on a rounding is a test that will change
its answer when `CostSchedule`'s defaults do. `0.4` puts the line clear of it in one direction and
`test_the_declared_floor_moves_the_verdict_and_nothing_else` drives it across in the other.
"""

SESSION_TURNOVER: Final[float] = 1_000_000.0
"""Every admitted name's `daily.amount`, in **thousands** of yuan: ¥1,000,000,000 a session.

Flat across the four securities on purpose: `GroupCapacity.binding_capacity` is a `min`, so a
fixture whose turnovers differed would make every capital multiple below a property of whichever
name happened to be cheapest rather than of the declared cap. At the declared 1% cap and
`CAPITAL`, this gives a `capital_multiple` of exactly 100.0 --
`test_the_declared_participation_cap_moves_a_number_in_the_sealed_artifact` moves the cap and
takes the difference.
"""

CODE_COMMIT: Final[str] = "abcdef1234567890"

BUILT_AT: Final[datetime] = datetime(2026, 6, 13, 2, 0, tzinfo=UTC)

RAW_SCORES: Final[dict[int, float]] = {1: 1.0, 2: 2.0, 3: 3.0, 4: 4.0}
"""Perfectly ordered against the forward returns, so the raw rank IC is 1.0."""

PROCESSED_SCORES: Final[dict[int, float]] = {1: -1.5, 2: -0.5, 3: 0.5, 4: 1.5}
"""The same ordering on a standardized scale, so the processed rank IC is 1.0 as well and the
raw -> processed step is a `survives` with a retention of exactly 1.0. Different *numbers* from
the raw tier on purpose: a fixture whose two tiers carried one vector would let a report that read
the wrong row still look right."""

NEUTRALIZED_SCORES: Final[dict[int, float]] = {1: 20.0, 2: 40.0, 3: 10.0, 4: 30.0}
"""Ranks `(2, 4, 1, 3)` against forward-return ranks `(1, 2, 3, 4)`.

The one four-element permutation whose Spearman correlation with the identity is exactly zero:
`sum(d ** 2)` is `1 + 4 + 4 + 1 = 10` and `1 - 6 * 10 / (4 * 15)` is `0`. Driven rather than
asserted from the formula -- `test_the_neutralised_tiers_information_coefficient_is_exactly_zero`
takes the number off `ICSummary.mean_ic` with `==`, because `factor_ic._pearson` computes a
scaled product-moment correlation and not the rank-difference formula, and "these two agree at
the last bit" is a claim about floating point rather than about algebra.
"""

SCORES: Final[dict[FactorTier, dict[int, float]]] = {
    "raw": RAW_SCORES,
    "processed": PROCESSED_SCORES,
    "neutralized": NEUTRALIZED_SCORES,
}

SOURCE_BUILDS: Final[dict[FactorTier, tuple[str, ...]]] = {
    "raw": ("fmn_probe_a", "fmn_probe_b"),
    "processed": ("ftm_probe_a", "ftm_probe_b"),
    "neutralized": ("fnm_probe_a", "fnm_probe_b"),
}
"""One stored build per `as_of` per tier, with the three tiers' prefixes kept apart so a digest
computed off the wrong tier is a different string rather than a coincidence."""


def code(index: int) -> str:
    return f"{index:06d}.SZ"


def _definition(*, key: str = "probe_experiment", version: int = 1) -> FactorDefinition:
    return FactorDefinition(
        key=key,
        version=version,
        family="momentum_reversal",
        direction="higher_is_better",
        required_fields=(FactorField(dataset="daily", column="close"),),
        lookback_sessions=1,
        max_window_sessions=1,
        lookback_periods=None,
        max_window_periods=None,
    )


def _ic_spec(
    *, definition: FactorDefinition | None = None, min_securities: int = 4, min_as_ofs: int = 2
) -> FactorICSpec:
    return FactorICSpec(
        definition=definition if definition is not None else _definition(),
        method="spearman",
        min_securities=min_securities,
        min_as_ofs=min_as_ofs,
    )


def _quantile_spec(
    *,
    definition: FactorDefinition | None = None,
    group_count: int = 2,
    min_securities_per_group: int = 2,
    position_capital: Decimal = CAPITAL,
    min_periods: int = 2,
) -> QuantilePortfolioSpec:
    return QuantilePortfolioSpec(
        definition=definition if definition is not None else _definition(),
        group_count=group_count,
        min_securities_per_group=min_securities_per_group,
        position_capital=position_capital,
        min_periods=min_periods,
    )


def _tradeability_spec(
    *, participation_cap: Decimal = Decimal("0.01"), min_rebalances: int = 1
) -> TradeabilitySpec:
    return TradeabilitySpec(participation_cap=participation_cap, min_rebalances=min_rebalances)


def _survival_spec(
    *, min_securities: int = 4, min_as_ofs: int = 2, redundancy_threshold: float = 0.8
) -> RedundancySpec:
    return RedundancySpec(
        method="spearman",
        min_securities=min_securities,
        min_as_ofs=min_as_ofs,
        redundancy_threshold=redundancy_threshold,
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


def _window(as_of: datetime, *, horizon: str = "1d") -> LabelWindow:
    return build_label_window(
        as_of=as_of, zone=SHANGHAI, horizon=parse_horizon(horizon), calendar=CALENDAR
    )


def _label(ts_code: str, *, window: LabelWindow, total_return: float) -> OutcomeLabel:
    """A real `label_outcome` over a synthetic path whose cumulative adjusted return is chosen."""
    growth = (1.0 + total_return) ** (1.0 / window.session_count)
    bars: dict[date, DailyBar] = {}
    price = 100.0
    for position, day in enumerate(window.sessions):
        if position == 0:
            bars[day] = _daily(ts_code, day, close=price, pre_close=price)
            continue
        moved = price * growth
        bars[day] = _daily(ts_code, day, close=moved, pre_close=price)
        price = moved
    limits = {
        day: PriceLimit(ts_code=ts_code, trade_date=day, up_limit=10_100.0, down_limit=0.01)
        for day in window.sessions
    }
    return label_outcome(
        window,
        ts_code=ts_code,
        bars=bars,
        factors=build_adjustment_history(
            ts_code,
            [
                AdjustmentFactor(ts_code=ts_code, observed_on=day, factor=1.0)
                for day in window.sessions
            ],
        ),
        limits=limits,
        halts=halt_corpus_for_years({}, years=(2026,)),
        universe=UNIVERSE,
    )


def _bar(subject: str, day: date, *, close: float) -> MarketBar:
    price = Decimal(str(round(close, 2)))
    return MarketBar(
        subject=subject,
        trade_date=day,
        board="main",
        previous_close=price,
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=False,
        is_st=False,
    )


def _rows(tier: FactorTier, as_of: datetime, scores: dict[int, float]) -> list[Any]:
    """One tier's observation rows, on that tier's own contract."""
    if tier == "raw":
        return [
            FactorObservation(
                subject=code(rank),
                as_of=as_of,
                value=scores[rank],
                coverage="computed",
                factor_id="fct_probe",
                manifest_id="fmn_probe",
                input_row_count=1,
                input_session_first=as_of.date(),
                input_session_last=as_of.date(),
            )
            for rank in RANKS
        ]
    if tier == "processed":
        return [
            ProcessedFactorObservation(
                subject=code(rank),
                as_of=as_of,
                value=scores[rank],
                coverage="processed",
                transform_id="ftx_probe",
                transform_manifest_id="ftm_probe",
                source_factor_id="fct_probe",
                source_manifest_id="fmn_probe",
                source_coverage="computed",
            )
            for rank in RANKS
        ]
    return [
        NeutralizedFactorObservation(
            subject=code(rank),
            as_of=as_of,
            value=scores[rank],
            coverage="neutralized",
            neutralization_id="fnz_probe",
            neutralization_manifest_id="fnm_probe",
            source_factor_id="fct_probe",
            source_transform_id="ftx_probe",
            source_transform_manifest_id="ftm_probe",
            source_coverage="processed",
            industry_code="801080",
        )
        for rank in RANKS
    ]


def _cross_section(
    tier: FactorTier, as_of: datetime, scores: dict[int, float], *, horizon: str = "1d"
) -> tuple[ICCrossSection, dict[str, tuple[MarketBar, MarketBar]]]:
    """One tier's cross section at one `as_of`, and the bar pair each admitted name trades on."""
    window = _window(as_of, horizon=horizon)
    labels = {
        code(rank): _label(code(rank), window=window, total_return=rank / 100.0) for rank in RANKS
    }
    bars = {
        code(rank): (
            _bar(code(rank), window.entry_day, close=100.0),
            _bar(code(rank), window.exit_day, close=100.0 * (1.0 + rank / 100.0)),
        )
        for rank in RANKS
    }
    rows = _rows(tier, as_of, scores)
    if tier == "raw":
        section = raw_cross_section(as_of=as_of, observations=rows, labels=labels)
    elif tier == "processed":
        section = processed_cross_section(as_of=as_of, observations=rows, labels=labels)
    else:
        section = neutralized_cross_section(as_of=as_of, observations=rows, labels=labels)
    return section, bars


def _summaries(
    tier: FactorTier,
    scores: dict[int, float],
    *,
    ic_spec: FactorICSpec,
    quantile_spec: QuantilePortfolioSpec,
    tradeability_spec: TradeabilitySpec,
    as_ofs: tuple[datetime, ...] = AS_OFS,
    horizon: str = "1d",
) -> tuple[ICSummary, QuantilePortfolioSummary, TurnoverSeries, TradeabilitySummary]:
    """One tier's four upstream summaries, driven through the real studies.

    `V2-P3-007` produces two of them and both are driven here: the rolling turnover and the
    coverage-and-capacity summary. Every held name is offered a session turnover, so the capacity
    statistics on the row below are real numbers rather than a refusal code -- which is what makes
    `TradeabilitySpec.participation_cap` a determinant of this artifact's content.
    """
    ic_study = FactorICStudy(ic_spec)
    quantile_study = QuantilePortfolioStudy(
        quantile_spec, execution=AShareExecutionPolicy(costs=CostSchedule())
    )
    turnover_study = TradeabilityStudy(tradeability_spec, portfolio=quantile_spec)
    periods: list[PeriodPortfolio] = []
    points: list[ICPoint] = []
    reports: list[PeriodTradeability] = []
    for as_of in as_ofs:
        section, bars = _cross_section(tier, as_of, scores, horizon=horizon)
        points.append(ic_study.measure(section))
        period = quantile_study.measure(section, bars=bars)
        periods.append(period)
        reports.append(
            turnover_study.measure(
                period,
                cross_section=section,
                liquidity={
                    pair.subject: liquidity_from_amount(
                        subject=pair.subject,
                        trade_date=period.entry_day,
                        amount=SESSION_TURNOVER,
                    )
                    for pair in section.pairs
                },
            )
        )
    return (
        ic_study.summarize(points),
        quantile_study.summarize(periods),
        turnover_study.turnover(periods),
        turnover_study.summarize(reports),
    )


def _survival(
    tier: FactorTier,
    scores: dict[int, float],
    *,
    survival_spec: RedundancySpec,
    definition: FactorDefinition,
    as_ofs: tuple[datetime, ...] = AS_OFS,
) -> RedundancySummary:
    """`raw` against `tier` for one factor: the cross-tier self-pair `factor_redundancy` supports.

    Driven through `correlate_cross_section` rather than through `RedundancyStudy.measure`, and
    the reason is a property of the upstream module rather than a preference: `measure` keys the
    vectors it may need for an identity by factor **key**, and a cross-tier self-pair offers one
    key twice, so it refuses with "a second vector was offered". `correlate_cross_section` is the
    function whose own docstring says the cross-tier pair "is the supported reading and says how
    much of it survived the transform", and it takes `identity=None` -- which is the whole of what
    a self-pair could ever have, since an identity relates two *factors*. `summarize` is the
    study's and is reached unchanged.
    """
    study = RedundancyStudy(survival_spec, identities=())
    points = []
    for as_of in as_ofs:
        left = factor_vector(
            as_of=as_of,
            tier="raw",
            definition=definition,
            rows=tuple((code(rank), RAW_SCORES[rank], "computed") for rank in RANKS),
        )
        right = factor_vector(
            as_of=as_of,
            tier=tier,
            definition=definition,
            rows=tuple(
                (
                    code(rank),
                    scores[rank],
                    "processed" if tier == "processed" else "neutralized",
                )
                for rank in RANKS
            ),
        )
        points.append(correlate_cross_section(left=left, right=right, spec=survival_spec))
    return study.summarize(points)


def _tier_report(
    tier: FactorTier,
    *,
    scores: dict[int, float] | None = None,
    definition: FactorDefinition | None = None,
    ic_spec: FactorICSpec | None = None,
    quantile_spec: QuantilePortfolioSpec | None = None,
    tradeability_spec: TradeabilitySpec | None = None,
    survival_spec: RedundancySpec | None = None,
    as_ofs: tuple[datetime, ...] = AS_OFS,
    source_manifest_ids: tuple[str, ...] | None = None,
) -> TierReport:
    """One row of the report, with all four upstream studies really run."""
    declared = definition if definition is not None else _definition()
    ic = ic_spec if ic_spec is not None else _ic_spec(definition=declared)
    quantile = quantile_spec if quantile_spec is not None else _quantile_spec(definition=declared)
    trade = tradeability_spec if tradeability_spec is not None else _tradeability_spec()
    survive = survival_spec if survival_spec is not None else _survival_spec()
    values = scores if scores is not None else SCORES[tier]
    ic_summary, quantile_summary, turnover_summary, tradeability_summary = _summaries(
        tier,
        values,
        ic_spec=ic,
        quantile_spec=quantile,
        tradeability_spec=trade,
        as_ofs=as_ofs,
    )
    return TierReport(
        tier=tier,
        source_manifest_ids=(
            source_manifest_ids if source_manifest_ids is not None else SOURCE_BUILDS[tier]
        ),
        ic=ic_summary,
        portfolio=quantile_summary,
        turnover=turnover_summary,
        tradeability=tradeability_summary,
        survival=(
            None
            if tier == "raw"
            else _survival(
                tier,
                values,
                survival_spec=survive,
                definition=declared,
                as_ofs=as_ofs,
            )
        ),
    )


def _record(
    *,
    retention_floor: float = RETENTION_FLOOR,
    code_commit: str = CODE_COMMIT,
    built_at: datetime = BUILT_AT,
    note: FactorNote | None = None,
    definition: FactorDefinition | None = None,
    scores: dict[FactorTier, dict[int, float]] | None = None,
    ic_spec: FactorICSpec | None = None,
    quantile_spec: QuantilePortfolioSpec | None = None,
    tradeability_spec: TradeabilitySpec | None = None,
    survival_spec: RedundancySpec | None = None,
    source_builds: dict[FactorTier, tuple[str, ...]] | None = None,
) -> FactorExperimentRecord:
    """The whole assembly, end to end, through the public builder."""
    declared = definition if definition is not None else _definition()
    ic = ic_spec if ic_spec is not None else _ic_spec(definition=declared)
    quantile = quantile_spec if quantile_spec is not None else _quantile_spec(definition=declared)
    trade = tradeability_spec if tradeability_spec is not None else _tradeability_spec()
    survive = survival_spec if survival_spec is not None else _survival_spec()
    table = scores if scores is not None else SCORES
    builds = source_builds if source_builds is not None else SOURCE_BUILDS
    rows = {
        tier: _tier_report(
            tier,
            scores=table[tier],
            definition=declared,
            ic_spec=ic,
            quantile_spec=quantile,
            tradeability_spec=trade,
            survival_spec=survive,
            source_manifest_ids=builds[tier],
        )
        for tier in FACTOR_TIER_ORDER
    }
    return build_factor_experiment(
        ic_spec=ic,
        portfolio_spec=quantile,
        tradeability_spec=trade,
        survival_spec=survive,
        retention_floor=retention_floor,
        code_commit=code_commit,
        raw=rows["raw"],
        processed=rows["processed"],
        neutralized=rows["neutralized"],
        built_at=built_at,
        note=note,
    )


# --- 1. the acceptance criterion ------------------------------------------------------------


def test_a_factor_whose_edge_is_its_exposure_reports_removed_on_the_neutralisation_step() -> None:
    """**The acceptance demonstration.** One factor, three tiers, one sample, one named cell.

    The roadmap's annotation is the criterion: *否则分不清「因子有效」与「暴露没控住」*. The
    fixture is a factor whose ordering predicts the forward return perfectly on the raw and the
    processed tier and not at all once the industry and size exposure is regressed out, which is
    exactly the shape a factor that was really long a sector has.

    What is asserted is that a reader does **not** have to compare `0.9999999999999998` against
    `0.0` and decide whether the fall matters. The artifact carries the comparison: one lookup by
    `(from_tier, to_tier, statistic)` returns a verdict from a closed set, and the two rungs that
    would let a reader off -- `survives` and `amplified` -- are different strings.

    The raw -> processed cell is asserted in the same breath and it is the control: a report in
    which every step said `removed` would be a report that had stopped reading its inputs.
    """
    record = _record()
    artifact = record.artifact

    exposure = artifact.attribution(
        from_tier="processed", to_tier="neutralized", statistic="mean_ic"
    )
    preprocessing = artifact.attribution(from_tier="raw", to_tier="processed", statistic="mean_ic")
    end_to_end = artifact.attribution(from_tier="raw", to_tier="neutralized", statistic="mean_ic")

    assert exposure.verdict == "removed"
    assert exposure.retention == 0.0
    assert exposure.to_value == 0.0
    assert preprocessing.verdict == "survives"
    assert preprocessing.retention == 1.0
    assert end_to_end.verdict == "removed"
    assert end_to_end.retention == 0.0
    assert artifact.tier_report("raw").ic.mean_ic == preprocessing.from_value
    assert artifact.tier_report("neutralized").ic.mean_ic == 0.0


def test_the_survival_row_corroborates_the_verdict_instead_of_repeating_it() -> None:
    """The fourth upstream module doing work no other row can, on the same artifact.

    A `removed` verdict says the statistic did not survive the step. It does **not** say the step
    did anything to the values, and those are different claims that come apart in the direction a
    reader would be misled by: a tier whose ordering is unchanged and whose IC collapsed is a
    contradiction, and a tier whose ordering was rewritten wholesale is at least consistent with
    the transform having done something.

    On this fixture the two rows say it: the processed tier is in **rank lockstep** with raw
    (`undeclared_lockstep`, a mean magnitude of exactly 1.0), which is what a monotone
    standardization must produce and is why its IC is unchanged; the neutralised tier correlates
    with raw at exactly 0.0 and is `distinct`, which is what a residual that reordered the market
    looks like. So "the edge was the exposure" is carried by two independent readings of one
    artifact rather than by one number wearing two hats.
    """
    artifact = _record().artifact
    processed = artifact.tier_report("processed").survival
    neutralized = artifact.tier_report("neutralized").survival

    assert processed is not None
    assert neutralized is not None
    assert processed.mean_abs_correlation == 1.0
    assert processed.verdict == "undeclared_lockstep"
    assert neutralized.mean_abs_correlation == 0.0
    assert neutralized.verdict == "distinct"
    assert artifact.tier_report("processed").ic.mean_ic == artifact.tier_report("raw").ic.mean_ic
    assert artifact.tier_report("neutralized").ic.mean_ic == 0.0


def test_the_neutralised_tiers_information_coefficient_is_exactly_zero() -> None:
    """The fixture's load-bearing number, taken off the study rather than off the formula.

    `NEUTRALIZED_SCORES` is chosen so the rank-difference formula gives exactly zero, but
    `factor_ic._pearson` computes a *scaled* product-moment correlation of two average-rank
    vectors and not that formula, so "the two agree at the last bit" is a claim about floating
    point. It is measured here with `==` rather than assumed, because every assertion in the
    acceptance test above rests on the retention being exactly `0.0` and a value of `1e-17` would
    make `removed` a verdict about a rounding.
    """
    ic_summary, _quantile, _turnover, _tradeability = _summaries(
        "neutralized",
        NEUTRALIZED_SCORES,
        ic_spec=_ic_spec(),
        quantile_spec=_quantile_spec(),
        tradeability_spec=_tradeability_spec(),
    )

    assert ic_summary.coverage == "measured"
    assert ic_summary.mean_ic == 0.0
    assert ic_summary.measured_count == 2


def test_the_spread_and_the_information_coefficient_are_attributed_apart() -> None:
    """Two statistics, and the fixture separates them rather than letting one stand for both.

    On this factor the neutralisation destroys the whole rank IC and leaves part of the net
    long-short spread, because a two-by-two cut is coarser than an ordering: the permutation that
    scrambles all four ranks still puts two of the four high-return names in the long group. So
    the `mean_ic` cell says `removed` and the `mean_spread` cell does not, on **one** artifact --
    which is the direction a grid keyed only by step would be unable to report at all.

    The spread's retention is asserted as an interval rather than as a constant because it is a
    quotient of two fee-bearing quantities and pinning its last bits would be pinning
    `CostSchedule`'s defaults from here; what the interval does separate is the two verdicts, and
    `test_the_declared_floor_moves_the_verdict_and_nothing_else` shows the same number crossing
    the line when the line moves.
    """
    artifact = _record().artifact

    ic_cell = artifact.attribution(
        from_tier="processed", to_tier="neutralized", statistic="mean_ic"
    )
    spread_cell = artifact.attribution(
        from_tier="processed", to_tier="neutralized", statistic="mean_spread"
    )

    assert ic_cell.verdict == "removed"
    assert ic_cell.retention == 0.0
    assert spread_cell.verdict == "survives"
    assert spread_cell.retention is not None
    assert 0.4 < spread_cell.retention < 0.5
    assert spread_cell.from_value == artifact.tier_report("processed").portfolio.mean_spread
    assert spread_cell.to_value == artifact.tier_report("neutralized").portfolio.mean_spread


def test_the_declared_floor_moves_the_verdict_and_nothing_else() -> None:
    """The line is declared and the module refuses to choose it, so moving it moves the verdict.

    Three floors over one fixture. The retention is the same number in all three -- it is a ratio
    of two measurements and no policy enters it -- and the verdict crosses from `survives` to
    `removed` as the declared line passes it. A cell whose verdict did not move with the floor
    would be a verdict decided somewhere the caller cannot see.
    """
    verdicts = {}
    retentions = set()
    for floor in (0.25, 0.4, 0.75):
        cell = _record(retention_floor=floor).artifact.attribution(
            from_tier="processed", to_tier="neutralized", statistic="mean_spread"
        )
        verdicts[floor] = cell.verdict
        retentions.add(cell.retention)

    assert len(retentions) == 1
    assert verdicts == {0.25: "survives", 0.4: "survives", 0.75: "removed"}


# --- 2. the ladder --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_value", "to_value", "verdict", "retention"),
    [
        (None, 0.5, "not_measured", None),
        (0.5, None, "not_measured", None),
        (None, None, "not_measured", None),
        (0.0, 0.5, "no_baseline", None),
        (-0.2, -0.1, "no_baseline", None),
        (0.4, -0.2, "reversed", -0.5),
        (0.4, 0.8, "amplified", 2.0),
        (0.4, 0.0, "removed", 0.0),
        (0.4, 0.1, "removed", 0.25),
        (0.5, 0.2, "survives", 0.4),
        (0.5, 0.19, "removed", 0.38),
        (0.4, 0.4, "survives", 1.0),
    ],
)
def test_every_rung_of_the_attribution_ladder_is_reachable_and_carries_its_own_ratio(
    from_value: float | None,
    to_value: float | None,
    verdict: str,
    retention: float | None,
) -> None:
    """One case per rung, with the boundary driven from both sides and **on** it.

    `0.2 / 0.5` is exactly the double nearest `0.4` (dividing by a power of two is exact), so the
    fifth case sits on the declared floor itself and requires `survives` -- the ladder asks
    `retention < floor`, so the line is inclusive on the surviving side, and a version that asked
    `<=` would fail here and nowhere else. The case below it is one hundredth away on the other
    side.
    """
    cell = TierAttribution(
        from_tier="processed",
        to_tier="neutralized",
        statistic="mean_ic",
        retention_floor=RETENTION_FLOOR,
        from_value=from_value,
        to_value=to_value,
        retention=retention,
        verdict=verdict,  # type: ignore[arg-type]
    )

    assert cell.verdict == verdict
    assert cell.retention == retention
    assert (cell.retention is None) == (cell.verdict in RETENTIONLESS_VERDICTS)


def test_a_factor_that_never_worked_reports_no_baseline_rather_than_removed() -> None:
    """The split this module's docstring argues for, driven on the two fixtures that differ only
    in the sign of the earlier tier's statistic.

    A factor whose raw mean IC is `-0.05` and whose neutralised one is `0.00` has a retention of
    zero and is **not** a factor whose edge was an exposure -- it had no edge. Pooling the two
    would put this module's loudest verdict on a factor that never worked, which is the exact
    shape of a report that verified existence and not magnitude.
    """
    losing = TierAttribution(
        from_tier="processed",
        to_tier="neutralized",
        statistic="mean_ic",
        retention_floor=RETENTION_FLOOR,
        from_value=-0.05,
        to_value=0.0,
        retention=None,
        verdict="no_baseline",
    )
    winning = TierAttribution(
        from_tier="processed",
        to_tier="neutralized",
        statistic="mean_ic",
        retention_floor=RETENTION_FLOOR,
        from_value=0.05,
        to_value=0.0,
        retention=0.0,
        verdict="removed",
    )

    assert losing.verdict == "no_baseline"
    assert losing.retention is None
    assert winning.verdict == "removed"
    assert winning.retention == 0.0


def test_a_verdict_that_contradicts_its_own_two_numbers_is_not_constructible() -> None:
    """The `ICPoint` shape: the report cannot say a statistic survived a step that took it away.

    Without this rule `from_value=0.08, to_value=0.0, verdict="survives"` builds, and a reader
    consuming the verdict is told the factor kept working while the measurement says its whole
    edge was the exposure. That is the acceptance criterion failing silently.
    """
    with pytest.raises(ValidationError, match="the declared ladder gives"):
        TierAttribution(
            from_tier="processed",
            to_tier="neutralized",
            statistic="mean_ic",
            retention_floor=RETENTION_FLOOR,
            from_value=0.08,
            to_value=0.0,
            retention=0.0,
            verdict="survives",
        )


def test_a_retention_that_is_not_the_quotient_of_its_own_two_values_is_refused() -> None:
    """The ratio is recomputed rather than trusted, so a cell cannot carry a plausible number
    that its two operands do not produce."""
    with pytest.raises(ValidationError, match="the declared ladder gives"):
        TierAttribution(
            from_tier="processed",
            to_tier="neutralized",
            statistic="mean_ic",
            retention_floor=RETENTION_FLOOR,
            from_value=0.4,
            to_value=0.2,
            retention=0.9,
            verdict="survives",
        )


def test_a_verdict_that_carries_a_ratio_it_could_not_have_computed_is_refused() -> None:
    """`RETENTIONLESS_VERDICTS` is the declared table and membership in it is exactly the
    condition under which there is no ratio; a `not_measured` cell carrying one is refused before
    the ladder is consulted."""
    with pytest.raises(ValidationError, match="carry none, because those are the two codes"):
        TierAttribution(
            from_tier="processed",
            to_tier="neutralized",
            statistic="mean_ic",
            retention_floor=RETENTION_FLOOR,
            from_value=None,
            to_value=None,
            retention=0.5,
            verdict="not_measured",
        )


def test_a_cell_filed_under_a_step_the_grid_does_not_declare_is_refused() -> None:
    """`neutralized -> raw` is arithmetic and is not a step: a report that attributed backwards
    would invert every verdict on it."""
    with pytest.raises(ValidationError, match="is not a declared attribution step"):
        TierAttribution(
            from_tier="neutralized",
            to_tier="raw",
            statistic="mean_ic",
            retention_floor=RETENTION_FLOOR,
            from_value=0.4,
            to_value=0.4,
            retention=1.0,
            verdict="survives",
        )


def test_the_end_to_end_step_is_not_the_product_of_the_two_links() -> None:
    """`raw -> neutralized` is computed from the two rows and never from the other two cells.

    The composition is not the product once either link is `not_measured`, and the three tiers'
    admitted sets are their own contracts' -- an `imputed` processed row carries a number and
    enters no statistic, and the residual plane's vocabulary is a third one again. So the cell is
    asserted against the two *rows* rather than against the arithmetic a reader might attempt.
    """
    artifact = _record().artifact
    first = artifact.attribution(from_tier="raw", to_tier="processed", statistic="mean_spread")
    second = artifact.attribution(
        from_tier="processed", to_tier="neutralized", statistic="mean_spread"
    )
    whole = artifact.attribution(from_tier="raw", to_tier="neutralized", statistic="mean_spread")

    assert whole.from_value == artifact.tier_report("raw").portfolio.mean_spread
    assert whole.to_value == artifact.tier_report("neutralized").portfolio.mean_spread
    assert first.retention is not None
    assert second.retention is not None
    assert whole.retention is not None


# --- 3. immutability ------------------------------------------------------------------------


def test_every_spec_field_reaches_the_experiment_identity() -> None:
    """Roadmap section 9's lesson, applied field by field: an identity is only what the hashed
    model declares, and that has to be measured rather than assumed.

    Each declared field of `FactorExperimentSpec` is varied alone and the id is required to move.
    Varying it through `model_copy(update=...)` rather than through the builder is deliberate --
    the builder would recompute the digests and mask a field that decides nothing.
    """
    spec = _record().artifact.spec
    baseline = spec.experiment_id
    changes: dict[str, object] = {
        "ic": _ic_spec(min_securities=3),
        "portfolio": _quantile_spec(min_periods=3),
        "tradeability": _tradeability_spec(participation_cap=Decimal("0.02")),
        "survival": _survival_spec(redundancy_threshold=0.7),
        "retention_floor": 0.75,
        "code_commit": "0123456789abcdef",
        "horizon_sessions": 5,
        "as_of_digest": "set_othersample",
        "raw_source_digest": "set_otherraw",
        "processed_source_digest": "set_otherprocessed",
        "neutralized_source_digest": "set_otherneutralized",
    }

    assert set(changes) | {"schema_version"} == set(FactorExperimentSpec.model_fields)
    moved = {
        name: spec.model_copy(update={name: value}).experiment_id != baseline
        for name, value in changes.items()
    }
    assert moved == dict.fromkeys(changes, True)


def test_every_parameter_of_the_builder_moves_the_identity_or_is_exempted_by_name() -> None:
    """`V2-P3-002`'s audit, on this module's builder: the signature is read, not listed.

    Every parameter is either measured to move `experiment_id` or carried in
    `IDENTITY_EXEMPT_PARAMETERS` with the reason it does not. A twelfth parameter fails here until
    somebody classifies it, which is the direction a hand-written list cannot cover -- the version
    of `FactorBuildManifest` that recorded `subject_count` and not `subject_digest` passed every
    per-field test it had.
    """
    baseline = _record().experiment_id
    varied: dict[str, str] = {
        "ic_spec": _record(ic_spec=_ic_spec(min_securities=3)).experiment_id,
        "portfolio_spec": _record(quantile_spec=_quantile_spec(min_periods=3)).experiment_id,
        "tradeability_spec": _record(
            tradeability_spec=_tradeability_spec(participation_cap=Decimal("0.05"))
        ).experiment_id,
        "survival_spec": _record(
            survival_spec=_survival_spec(redundancy_threshold=0.6)
        ).experiment_id,
        "retention_floor": _record(retention_floor=0.25).experiment_id,
        "code_commit": _record(code_commit="fedcba9876543210").experiment_id,
        "raw": _record(
            source_builds={**SOURCE_BUILDS, "raw": ("fmn_probe_a", "fmn_probe_c")}
        ).experiment_id,
        "processed": _record(
            source_builds={**SOURCE_BUILDS, "processed": ("ftm_probe_a", "ftm_probe_c")}
        ).experiment_id,
        "neutralized": _record(
            source_builds={**SOURCE_BUILDS, "neutralized": ("fnm_probe_a", "fnm_probe_c")}
        ).experiment_id,
    }
    exempt: dict[str, str] = {
        "built_at": _record(built_at=datetime(2030, 1, 1, tzinfo=UTC)).experiment_id,
        "note": _record(
            note=FactorNote(subject="probe_experiment", summary="a note about nothing")
        ).experiment_id,
    }

    assert set(varied) | set(exempt) == set(builder_parameters())
    assert set(exempt) == set(IDENTITY_EXEMPT_PARAMETERS)
    assert all(len(reason) > 100 for reason in IDENTITY_EXEMPT_PARAMETERS.values())
    assert {name: value != baseline for name, value in varied.items()} == dict.fromkeys(
        varied, True
    )
    assert {name: value == baseline for name, value in exempt.items()} == dict.fromkeys(
        exempt, True
    )


def test_a_measurement_that_changes_moves_the_content_and_not_the_identity() -> None:
    """The other direction, which is the whole reason the answers are out of the key.

    Two experiments over one declaration, one set of stored builds and one commit, whose numbers
    differ. `experiment_id` holds and `content_digest` moves -- so the pair is a *drift detector*
    and not merely a name. A design that hashed the answers into the key would report this as two
    unrelated experiments and nothing would ever be refused.
    """
    baseline = _record()
    drifted = _record(
        scores={
            "raw": RAW_SCORES,
            "processed": {1: -1.5, 2: -0.5, 3: 0.5, 4: 1.5},
            "neutralized": {1: 40.0, 2: 30.0, 3: 20.0, 4: 10.0},
        }
    )

    assert drifted.experiment_id == baseline.experiment_id
    assert drifted.content_digest != baseline.content_digest
    assert drifted.artifact.tier_report("neutralized").ic.mean_ic != 0.0


def test_two_records_that_disagree_under_one_experiment_id_are_refused() -> None:
    """`_refuse_to_drop_a_stored_build`'s shape on the experiment plane.

    The same four specs over the same builds at the same commit have one answer. A second one is a
    build that did not reproduce, and the honest reading is a refusal naming both digests rather
    than a second row a reader has to choose between.
    """
    held = _record()
    drifted = _record(
        scores={
            "raw": RAW_SCORES,
            "processed": PROCESSED_SCORES,
            "neutralized": {1: 40.0, 2: 30.0, 3: 20.0, 4: 10.0},
        }
    )

    with pytest.raises(FactorExperimentError, match="is already held at content"):
        refuse_a_restated_experiment(held=[held], arriving=drifted)


def test_a_recomputed_record_that_agrees_is_admitted_rather_than_refused() -> None:
    """The direction `FactorInputRef` lost and had to be given back: a re-derivation that
    reproduces its own content is a no-op, and an identity that moved for nothing would make a
    rebuild unwritable and its predecessor unreproducible.

    The two records differ in their wall clocks and in their prose, which is exactly what the two
    exemptions promise does not move anything.
    """
    held = _record()
    again = _record(
        built_at=datetime(2027, 1, 1, tzinfo=UTC),
        note=FactorNote(subject="probe_experiment", summary="recomputed on a later machine"),
    )

    refuse_a_restated_experiment(held=[held], arriving=again)

    assert again.experiment_id == held.experiment_id
    assert again.content_digest == held.content_digest


def test_a_held_collection_that_already_contradicts_itself_is_reported_against_itself() -> None:
    """A guard that blamed the newcomer for a contradiction it inherited would name the wrong
    record, so the held side is judged first."""
    held = _record()
    drifted = _record(
        scores={
            "raw": RAW_SCORES,
            "processed": PROCESSED_SCORES,
            "neutralized": {1: 40.0, 2: 30.0, 3: 20.0, 4: 10.0},
        }
    )

    with pytest.raises(FactorExperimentError, match="already carry two answers"):
        refuse_a_restated_experiment(held=[held, drifted], arriving=held)


def _leaf_paths(payload: Any, trail: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Every path to a scalar leaf of a JSON document, depth first."""
    if isinstance(payload, dict):
        return [
            path for key, value in payload.items() for path in _leaf_paths(value, (*trail, key))
        ]
    if isinstance(payload, list):
        return [
            path
            for index, value in enumerate(payload)
            for path in _leaf_paths(value, (*trail, index))
        ]
    return [trail]


def _perturb(value: Any) -> Any:
    """One different value of the same JSON type, so the edit is a change and not a type error."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, str):
        return f"{value}x"
    return "not-null"


def test_every_field_of_a_sealed_record_refuses_to_reopen_after_a_single_edit() -> None:
    """The per-field tamper audit, over every scalar leaf of the serialised artifact.

    This is the answer to a defect this repository has measured eight times: 100% line coverage
    says nothing about a field that is rendered and never asserted, and the upgraded version --
    an assertion that exists but cannot tell two answers apart on the fixture it runs on -- left
    five, eleven and four live mutants in `V2-P3-006`, `008` and `007` respectively.

    A digest over the whole document turns that from a discipline into a property: **every** leaf
    is covered because the seal does not know which leaf it is. The audit walks them, perturbs one
    at a time, and requires the reopened record to be refused on each -- so a field added to any
    of the four upstream summaries is protected the day it lands, without anybody extending a
    list.

    The `sealed_digest` leaf itself is excluded and driven separately below, because perturbing it
    tests the other side of the same comparison and would otherwise make this audit pass for a
    reason it is not about.
    """
    record = _record()
    payload = json.loads(experiment_payload(record))
    paths = [path for path in _leaf_paths(payload) if path[0] == "artifact"]

    assert len(paths) > 300

    survived: list[tuple[Any, ...]] = []
    for path in paths:
        edited = deepcopy(payload)
        cursor: Any = edited
        for step in path[:-1]:
            cursor = cursor[step]
        cursor[path[-1]] = _perturb(cursor[path[-1]])
        try:
            open_experiment(json.dumps(edited))
        except FactorExperimentError:
            continue
        survived.append(path)

    assert survived == []


def test_a_record_whose_seal_was_moved_instead_of_its_content_is_refused() -> None:
    """The other side of the comparison: editing the digest rather than the artifact."""
    record = _record()
    payload = json.loads(experiment_payload(record))
    payload["sealed_digest"] = f"{payload['sealed_digest'][:-1]}0"

    with pytest.raises(FactorExperimentError, match="was sealed at"):
        open_experiment(json.dumps(payload))


def test_a_payload_that_is_not_a_sealed_experiment_at_all_is_refused_the_same_way() -> None:
    """One exception type for "this document is not a valid sealed experiment", whether the fault
    was malformed JSON, a missing field or a broken seal -- `validate_notes`' argument for taking
    the caller's own error class, arriving at a read boundary."""
    with pytest.raises(FactorExperimentError, match="is not a sealed factor experiment"):
        open_experiment("{")
    with pytest.raises(FactorExperimentError, match="is not a sealed factor experiment"):
        open_experiment('{"schema_version": "factor-experiment-record/v1"}')


def test_a_sealed_record_round_trips_through_its_own_transport_unchanged() -> None:
    """The audit above would pass vacuously against a record that never reopens at all, so the
    untouched document is required to reopen, to carry the same two addresses, and to serialise
    back to the identical bytes.

    The byte equality is the part that makes the canonicalisation checkable: two processes that
    round-tripped one artifact and produced two documents would make a digest over the document a
    number nobody could reproduce.
    """
    record = _record(note=FactorNote(subject="probe_experiment", summary="the shipped probe"))
    payload = experiment_payload(record)
    reopened = open_experiment(payload)

    assert reopened.experiment_id == record.experiment_id
    assert reopened.content_digest == record.content_digest
    assert reopened.built_at == record.built_at
    assert reopened.note == record.note
    assert experiment_payload(reopened) == payload


def test_prose_and_the_wall_clock_reach_no_digest_at_all() -> None:
    """`FactorNote`'s and `FactorBuildManifest.built_at`'s arrangement, measured on both digests.

    A typo fix that moved a content address is the defect the three specs' `summary` fields were
    removed to correct, and a wall clock in an identity is what makes a rebuild unwritable. Both
    are carried on the record and neither is a field of anything hashed, so the assertion is on
    the hashed payload's key set as well as on the two addresses.
    """
    plain = _record()
    annotated = _record(
        built_at=datetime(2031, 3, 3, tzinfo=UTC),
        note=FactorNote(subject="probe_experiment", summary="a much later run"),
    )

    assert annotated.experiment_id == plain.experiment_id
    assert annotated.content_digest == plain.content_digest
    hashed = set(FactorExperimentArtifact.model_fields)
    assert "built_at" not in hashed
    assert "note" not in hashed


# --- 4. the report's shape ------------------------------------------------------------------


def test_the_artifact_carries_every_declared_tier_once() -> None:
    """D8 asks for raw, processed **and** neutralised, so a report of two rows would have a
    missing row indistinguishable from a tier that measured nothing."""
    artifact = _record().artifact

    assert tuple(report.tier for report in artifact.tiers) == FACTOR_TIER_ORDER
    assert len(artifact.tiers) == MINIMUM_EXPERIMENT_TIERS == len(FACTOR_TIER_ORDER)


def test_every_shipped_factor_produces_all_three_tiers_under_an_identity_of_its_own() -> None:
    """The phase gate says **every** factor gets a three-tier report; it was proved on one.

    The gate's sentence is registry-wide and the evidence was `reversal_1d/v1` -- the one shipped
    factor whose `lookback_sessions=2` fits the ten-session generated panel that
    `tests/integration/test_factor_interfaces.py` builds. Nothing in the repository iterated
    `FACTOR_DEFINITIONS.definitions` and built anything: the fifteen tests that do iterate it are
    all metadata assertions about reaches and families. So "every factor" rested on one factor and
    a family resemblance.

    **What this proves and what it does not.** It drives the *report* half over all twenty: the
    four studies run for real on each definition, `build_factor_experiment` binds them, the
    artifact carries `FACTOR_TIER_ORDER` once, and the twenty identities are pairwise distinct --
    which is the property a shared `experiment_id` would silently break, since a report is stored
    under it. It does **not** prove the *panel* half, that each factor's evaluator can be computed
    over a real store: that needs 120 sessions of prices for the momentum family and a filing axis
    for the value, quality and growth families, and it is what
    `tests/integration/panel/` covers family by family.

    The scores are the same synthetic table for every definition, on purpose. A per-factor fixture
    would make a failure here ambiguous between "this factor cannot be reported on" and "this
    fixture did not suit it", and the question being asked is the first one.
    """
    records = {
        definition.qualified_key: _record(definition=definition)
        for definition in FACTOR_DEFINITIONS.definitions
    }

    assert len(records) == 21
    assert len({record.experiment_id for record in records.values()}) == len(records)
    for handle, record in records.items():
        artifact = record.artifact
        assert tuple(report.tier for report in artifact.tiers) == FACTOR_TIER_ORDER, handle
        assert len(artifact.tiers) == MINIMUM_EXPERIMENT_TIERS, handle
        assert tuple(cell.key for cell in artifact.attributions) == ATTRIBUTION_CELL_ORDER, handle
        assert artifact.spec.factor_id == FACTOR_DEFINITIONS.get(handle).factor_id, handle
        assert {report.direction for report in artifact.tiers} == {
            FACTOR_DEFINITIONS.get(handle).direction
        }, handle


def test_the_grid_carries_every_declared_cell_in_the_declared_order() -> None:
    """`ICCensus.excluded_by_coverage`' rule one plane up: a cell missing from the tuple and a
    cell present with `not_measured` are different claims, so the whole grid is always there."""
    artifact = _record().artifact

    assert tuple(cell.key for cell in artifact.attributions) == ATTRIBUTION_CELL_ORDER
    assert len(artifact.attributions) == len(ATTRIBUTION_STEPS) * len(ATTRIBUTION_STATISTIC_ORDER)
    assert {cell.verdict for cell in artifact.attributions} <= ATTRIBUTION_VERDICT_CODES


def test_a_grid_that_drops_a_cell_is_refused() -> None:
    """Driven by removing one cell from a valid artifact, so the refusal is about the grid and not
    about an unrelated malformed cell."""
    artifact = _record().artifact

    with pytest.raises(ValidationError, match="a grid missing a cell"):
        FactorExperimentArtifact(
            spec=artifact.spec,
            tiers=artifact.tiers,
            attributions=artifact.attributions[1:],
        )


def test_a_cell_that_reports_a_number_no_row_carries_is_refused() -> None:
    """The run-time audit between a declared table and the implementation that fills it, which
    this repository has measured is the only thing that catches drift between the two.

    The doctored cell is internally consistent -- its verdict is the one its own two numbers
    produce -- so nothing but the cross-check against the rows can see it.
    """
    artifact = _record().artifact
    honest = artifact.attributions[0]
    doctored = TierAttribution(
        from_tier=honest.from_tier,
        to_tier=honest.to_tier,
        statistic=honest.statistic,
        retention_floor=honest.retention_floor,
        from_value=0.25,
        to_value=0.25,
        retention=1.0,
        verdict="survives",
    )

    with pytest.raises(ValidationError, match="a cell that carries a number no row carries"):
        FactorExperimentArtifact(
            spec=artifact.spec,
            tiers=artifact.tiers,
            attributions=(doctored, *artifact.attributions[1:]),
        )


def test_an_artifact_whose_rows_are_not_the_declared_tiers_in_order_is_refused() -> None:
    """The rows are reordered and nothing else, so the refusal is about the order alone.

    `build_factor_experiment` cannot produce this -- it names the three rows -- which is exactly
    why the rule is asserted against the contract directly: a validator whose only exercise comes
    through a builder that already satisfies it is a validator nobody has seen fire. An unordered
    tuple also makes two identical experiments compare unequal and hash to two `content_digest`s.
    """
    artifact = _record().artifact
    rows = {report.tier: report for report in artifact.tiers}

    with pytest.raises(ValidationError, match="in that order"):
        FactorExperimentArtifact(
            spec=artifact.spec,
            tiers=(rows["processed"], rows["raw"], rows["neutralized"]),
            attributions=artifact.attributions,
        )


def test_an_artifact_whose_three_rows_are_about_two_factors_is_refused() -> None:
    """The cross-tier half of `TierReport`'s own rule, and it is a separate rule with a separate
    failure: the processed row below is internally consistent, carries the same statistics as the
    honest one and satisfies every cell of the grid, so nothing but the row-to-row comparison can
    see that it is about another factor.

    Two definitions differing only in `key` produce two `factor_id`s and identical numbers, which
    is the fixture that separates "the rows disagree" from "the numbers disagree".
    """
    artifact = _record().artifact
    rows = {report.tier: report for report in artifact.tiers}
    stranger = _tier_report("processed", definition=_definition(key="probe_stranger"))

    assert stranger.ic.mean_ic == rows["processed"].ic.mean_ic
    assert stranger.factor_id != rows["processed"].factor_id
    with pytest.raises(ValidationError, match="disagree about factor_id"):
        FactorExperimentArtifact(
            spec=artifact.spec,
            tiers=(rows["raw"], stranger, rows["neutralized"]),
            attributions=artifact.attributions,
        )


def test_an_artifact_whose_rows_are_about_another_factor_than_the_spec_declares_is_refused() -> (
    None
):
    """The rows agree with each other and disagree with the declaration, which is a *third*
    failure and not the one above: three consistent rows filed under somebody else's
    `experiment_id` would be an artifact whose identity names a factor none of its numbers is
    about, and every later reader resolves the factor through the spec."""
    artifact = _record().artifact
    stranger = _definition(key="probe_stranger")
    moved = artifact.spec.model_copy(
        update={
            "ic": _ic_spec(definition=stranger),
            "portfolio": _quantile_spec(definition=stranger),
        }
    )

    assert moved.factor_id != artifact.tiers[0].factor_id
    with pytest.raises(ValidationError, match="and the spec declares"):
        FactorExperimentArtifact(
            spec=moved, tiers=artifact.tiers, attributions=artifact.attributions
        )


def test_an_artifact_whose_rows_were_cut_differently_from_the_declared_spec_is_refused() -> None:
    """The same rule for the cut. Every group mean and every spread in the rows was produced by a
    `group_count` the spec has to be the record of, and a spec claiming a ten-way cut over a
    two-way one is a stored experiment nobody can re-derive."""
    artifact = _record().artifact
    moved = artifact.spec.model_copy(
        update={
            "portfolio": _quantile_spec(group_count=4, min_securities_per_group=1),
        }
    )

    assert moved.portfolio.group_count != artifact.tiers[0].portfolio.group_count
    with pytest.raises(ValidationError, match="the declared quantile spec cuts into"):
        FactorExperimentArtifact(
            spec=moved, tiers=artifact.tiers, attributions=artifact.attributions
        )


def test_a_tier_report_whose_studies_are_at_two_horizons_is_refused() -> None:
    """An IC at one session beside a spread at five is two windows in one row, and the two are
    over the *same* `as_of`s -- so nothing but the horizon comparison can see it."""
    short = _tier_report("raw")
    long_ic, long_portfolio, long_turnover, long_tradeability = _summaries(
        "raw",
        RAW_SCORES,
        ic_spec=_ic_spec(),
        quantile_spec=_quantile_spec(),
        tradeability_spec=_tradeability_spec(),
        horizon="5d",
    )

    assert long_portfolio.as_ofs == short.ic.as_ofs
    assert long_ic.horizon_sessions != short.ic.horizon_sessions
    with pytest.raises(ValidationError, match="two windows in one row"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=short.ic,
            portfolio=long_portfolio,
            turnover=long_turnover,
            tradeability=long_tradeability,
            survival=None,
        )


def test_a_tier_report_carrying_another_tiers_study_is_refused() -> None:
    """The row that mixes tiers is the one thing a three-tier report exists to keep apart, and it
    is refused before anything else on the row is asked -- a raw IC filed under the processed
    heading would put the untransformed factor's number into every processed cell of the grid."""
    raw = _tier_report("raw")
    processed = _tier_report("processed")

    with pytest.raises(ValidationError, match="a row that mixes tiers"):
        TierReport(
            tier="processed",
            source_manifest_ids=SOURCE_BUILDS["processed"],
            ic=raw.ic,
            portfolio=processed.portfolio,
            turnover=processed.turnover,
            tradeability=processed.tradeability,
            survival=processed.survival,
        )

    # The fifth summary is held to the same rule as the other three: a raw coverage funnel filed
    # under the processed heading would report the untransformed tier's admission rate as the
    # transformed one's, and the two tiers' admitted vocabularies are exactly what differ.
    with pytest.raises(ValidationError, match="a row that mixes tiers"):
        TierReport(
            tier="processed",
            source_manifest_ids=SOURCE_BUILDS["processed"],
            ic=processed.ic,
            portfolio=processed.portfolio,
            turnover=processed.turnover,
            tradeability=raw.tradeability,
            survival=processed.survival,
        )


def test_a_tier_report_whose_capacity_follows_another_group_than_its_churn_is_refused() -> None:
    """Both `V2-P3-007` objects follow the long group the declared direction picks, so a row
    carrying two of them is a capacity for one portfolio beside the churn of another.

    The **turnover** series is the one doctored, and that is not arbitrary:
    `TradeabilitySummary` refuses a group its own declared direction does not make long, so a
    summary moved off the long group is not constructible at all and the disagreement has to be
    built from the other side. `TurnoverSeries.group` has no such rule -- it takes the index it is
    given -- which is exactly why this cross-check exists on the row rather than being left to the
    two contracts.
    """
    row = _tier_report("raw")
    thin = _tier_report("raw", tradeability_spec=_tradeability_spec(min_rebalances=5))

    assert row.tradeability.group == row.turnover.group == 1
    assert thin.turnover.coverage == "insufficient_rebalances"
    with pytest.raises(ValidationError, match="beside the churn of another"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=row.ic,
            portfolio=row.portfolio,
            turnover=thin.turnover.model_copy(update={"group": 0}),
            tradeability=row.tradeability,
            survival=None,
        )


def test_a_tier_report_whose_tradeability_covers_other_days_is_refused() -> None:
    """The sample rule reaches the fifth summary too: a coverage funnel pooled over other days
    than the IC was measured on is a report about a market the row does not describe."""
    honest = _tier_report("raw")
    short = _tier_report("raw", as_ofs=(AS_OF,), ic_spec=_ic_spec(min_as_ofs=2))

    assert short.tradeability.as_ofs != honest.tradeability.as_ofs
    with pytest.raises(ValidationError, match="have to be asked about the same days"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=honest.ic,
            portfolio=honest.portfolio,
            turnover=honest.turnover,
            tradeability=short.tradeability,
            survival=None,
        )


def test_a_cell_decided_at_another_floor_than_the_declared_one_is_refused() -> None:
    """The floor on a cell is a projection of the spec's, so the two cannot disagree --
    `FactorBuildManifest.direction`'s arrangement for its reason."""
    artifact = _record(retention_floor=0.5).artifact
    honest = artifact.attribution(from_tier="raw", to_tier="processed", statistic="mean_ic")
    doctored = TierAttribution(
        from_tier=honest.from_tier,
        to_tier=honest.to_tier,
        statistic=honest.statistic,
        retention_floor=0.9,
        from_value=honest.from_value,
        to_value=honest.to_value,
        retention=honest.retention,
        verdict=honest.verdict,
    )

    with pytest.raises(ValidationError, match="is a projection of the declared one"):
        FactorExperimentArtifact(
            spec=artifact.spec,
            tiers=artifact.tiers,
            attributions=(doctored, *artifact.attributions[1:]),
        )


def test_the_spec_digests_are_the_rows_own_builds_and_days() -> None:
    """A stored experiment whose identity names other builds than the ones it reports cannot be
    re-derived from either, so the four digests are recomputed from the rows and compared."""
    artifact = _record().artifact
    moved = artifact.spec.model_copy(update={"raw_source_digest": "set_somethingelse"})

    with pytest.raises(ValidationError, match="was read from builds digesting to"):
        FactorExperimentArtifact(
            spec=moved, tiers=artifact.tiers, attributions=artifact.attributions
        )

    other_days = artifact.spec.model_copy(update={"as_of_digest": "set_otherdays"})
    with pytest.raises(ValidationError, match="the rows were offered a sample digesting to"):
        FactorExperimentArtifact(
            spec=other_days, tiers=artifact.tiers, attributions=artifact.attributions
        )


def test_the_four_upstream_coverage_codes_are_reported_side_by_side_and_not_collapsed() -> None:
    """The answer to how five refusal vocabularies coexist: they are not reconciled.

    Every code on a row is its own study's, and the raw row carries four cells rather than five
    because `factor_redundancy` refuses one factor on one tier against itself -- an absence forced
    by an upstream contract rather than chosen here. `V2-P3-007` contributes two of the five and
    they are two cells rather than one: `TurnoverCoverage` says whether a holdings state exists at
    all and `TradeabilityCoverage` says whether any period reached the cut, and a row that merged
    them would need a fifth "N/A" of this module's own.
    """
    artifact = _record().artifact

    assert artifact.tier_report("raw").coverage_codes == (
        ("ic", "measured"),
        ("portfolio", "measured"),
        ("turnover", "measured"),
        ("tradeability", "measured"),
    )
    assert artifact.tier_report("neutralized").coverage_codes == (
        ("ic", "measured"),
        ("portfolio", "measured"),
        ("turnover", "measured"),
        ("tradeability", "measured"),
        ("survival", "measured"),
    )
    assert artifact.tier_report("raw").survival is None
    assert artifact.tier_report("processed").survival is not None


def test_every_tier_row_carries_the_coverage_funnel_and_the_long_groups_capacity() -> None:
    """`V2-P3-007`'s instrument, read out of the sealed document rather than out of a study.

    Before `TierReport.tradeability` existed, `TradeabilityStudy.measure` had no caller anywhere
    in `src/` and `CoverageFunnel`, `GroupCapacity`, `SessionLiquidity` and `liquidity_from_amount`
    were referenced nowhere outside their own module -- so an artifact reader could not answer
    "what fraction of the offered universe became a position" at any tier, and the roadmap's
    annotation for that issue reached no face at all. Every number below is asserted on the
    **reopened** document, so it is a statement about what a stored artifact carries rather than
    about what an object in this process holds.
    """
    reopened = open_experiment(experiment_payload(_record()))
    document = json.loads(experiment_payload(reopened))

    for tier in FACTOR_TIER_ORDER:
        row = reopened.artifact.tier_report(tier)
        assert row.tradeability.coverage == "measured"
        assert row.tradeability.funnel.universe_count == len(RANKS) * len(AS_OFS)
        assert row.tradeability.funnel.implementable_rate == 1.0
        assert row.tradeability.mean_top_group_execution_rate == 1.0
        assert row.tradeability.mean_top_group_execution_shortfall == 0.0
        assert row.tradeability.binding_capital_multiple == 100.0
        assert row.tradeability.binding_subject in {code(rank) for rank in RANKS}
        assert row.tradeability.sized_at_the_entry_session_count == len(AS_OFS)
    rendered = document["artifact"]["tiers"][0]["tradeability"]
    assert rendered["funnel"]["held_count"] == len(RANKS) * len(AS_OFS)
    assert rendered["binding_capital_multiple"] == 100.0
    assert [name for name, _count in rendered["capacity_coverage_counts"]] == [
        "measured",
        "no_holdings",
        "unpriced_holdings",
    ]
    # The field is REQUIRED and not merely usually present. An optional one would let a caller
    # assemble a sealed three-tier report with no funnel and no capacity in it -- which is the
    # state this repository shipped in, and a default of `None` restores it without failing a
    # single assertion about the runs that do supply one.
    row = reopened.artifact.tier_report("raw")
    without = {
        name: getattr(row, name) for name in TierReport.model_fields if name != "tradeability"
    }
    with pytest.raises(ValidationError, match="tradeability"):
        TierReport(**without)


def test_the_declared_participation_cap_moves_a_number_in_the_sealed_artifact() -> None:
    """**The falsification the acceptance test above needs beside it.**

    `TradeabilitySpec.participation_cap` is a required declaration with no default and it is a
    field of `FactorExperimentSpec`, so it has always moved `experiment_id` -- and moving an
    identity is not the same as deciding an answer. Until the tradeability summary reached a tier
    row, two runs at two caps differed in their two addresses and in **no measured quantity**,
    which is this repository's own recorded shape for "only verified existence and not magnitude".

    Two caps a factor of fifty apart are run here and the difference is taken leaf by leaf on the
    two rendered documents: the capital multiples move by exactly fifty on all three tiers, and
    the funnel, the group decomposition and every IC and spread stay equal. That the mean
    concentration does **not** move is asserted too, because it is a ratio the cap scales alike
    and a test that expected every capacity number to move would be asserting arithmetic that is
    false.
    """
    cheap = _record(tradeability_spec=_tradeability_spec(participation_cap=Decimal("0.01")))
    rich = _record(tradeability_spec=_tradeability_spec(participation_cap=Decimal("0.5")))

    assert cheap.experiment_id != rich.experiment_id
    assert cheap.content_digest != rich.content_digest
    moved: list[str] = []
    for tier in FACTOR_TIER_ORDER:
        left = cheap.artifact.tier_report(tier).tradeability
        right = rich.artifact.tier_report(tier).tradeability
        assert left.binding_capital_multiple == 100.0
        assert right.binding_capital_multiple == 5000.0
        assert right.mean_capital_multiple == left.mean_capital_multiple * 50.0
        assert left.mean_concentration == right.mean_concentration == 1.0
        assert left.funnel == right.funnel
        assert left.by_group == right.by_group
        moved.append(tier)
    assert moved == list(FACTOR_TIER_ORDER)
    assert cheap.artifact.attributions == rich.artifact.attributions
    differing = _differing_leaves(
        json.loads(experiment_payload(cheap)), json.loads(experiment_payload(rich))
    )
    assert {path for path in differing if "capital_multiple" in path} == {
        f"/artifact/tiers/{index}/tradeability/{name}"
        for index in range(len(FACTOR_TIER_ORDER))
        for name in ("binding_capital_multiple", "mean_capital_multiple")
    }
    assert not [path for path in differing if "/funnel/" in path or "/by_group/" in path]


def _differing_leaves(left: Any, right: Any, trail: str = "") -> list[str]:
    """Every JSON path at which two rendered documents carry different scalars."""
    if isinstance(left, dict) and isinstance(right, dict) and set(left) == set(right):
        return [
            path
            for key in left
            for path in _differing_leaves(left[key], right[key], f"{trail}/{key}")
        ]
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        return [
            path
            for index, (one, other) in enumerate(zip(left, right, strict=True))
            for path in _differing_leaves(one, other, f"{trail}/{index}")
        ]
    return [] if left == right else [trail]


def test_a_tier_that_never_cleared_its_floors_reports_not_measured_and_not_a_refusal() -> None:
    """The shortfall case, and it is a finding rather than a malformed question.

    The IC study's declared `min_as_ofs` is raised above the sample, so every tier's `ICSummary`
    arrives `insufficient_as_ofs` with no statistics -- which is the shape an in-year read of the
    neutralised plane produces for real, since `read_visible_at` returns *nothing* inside a year
    whose residuals were stamped at its end (see `KNOWN_EXPERIMENT_LIMITATIONS`). Three things
    are required of the artifact:

    - it still **builds**, because a tier that cleared no floor is a finding and not a malformed
      question;
    - the grid still carries all six cells, and every `mean_ic` cell says `not_measured` with no
      ratio;
    - the `mean_spread` cells are **unaffected**, which is what says the two statistics degrade
      independently rather than through one artifact-level "insufficient" of this module's own
      invention. The tiers' own `insufficient_as_ofs` stays readable beside the grid.
    """
    thin_ic = _ic_spec(min_as_ofs=3)
    record = _record(ic_spec=thin_ic)
    artifact = record.artifact

    assert artifact.tier_report("neutralized").ic.coverage == "insufficient_as_ofs"
    assert artifact.tier_report("raw").ic.coverage == "insufficient_as_ofs"
    ic_cells = [cell for cell in artifact.attributions if cell.statistic == "mean_ic"]
    assert [cell.verdict for cell in ic_cells] == ["not_measured"] * len(ATTRIBUTION_STEPS)
    assert [cell.retention for cell in ic_cells] == [None] * len(ATTRIBUTION_STEPS)
    spread_cells = [cell for cell in artifact.attributions if cell.statistic == "mean_spread"]
    assert all(cell.verdict != "not_measured" for cell in spread_cells)


# --- 5. one factor, one sample, one cut -----------------------------------------------------


def test_three_tiers_over_three_samples_are_refused() -> None:
    """`_refuse_rungs_over_different_samples`' argument one plane up, and the rule the acceptance
    criterion rests on: a fall between two tiers is a finding only if the two tiers were asked
    about the same days."""
    rows = {tier: _tier_report(tier) for tier in FACTOR_TIER_ORDER}
    short = _tier_report("neutralized", as_ofs=(AS_OF,), ic_spec=_ic_spec(min_as_ofs=2))

    with pytest.raises(FactorExperimentError, match="an attribution is a difference between two"):
        build_factor_experiment(
            ic_spec=_ic_spec(),
            portfolio_spec=_quantile_spec(),
            tradeability_spec=_tradeability_spec(),
            survival_spec=_survival_spec(),
            retention_floor=RETENTION_FLOOR,
            code_commit=CODE_COMMIT,
            raw=rows["raw"],
            processed=rows["processed"],
            neutralized=short,
            built_at=BUILT_AT,
        )


def test_a_row_passed_under_another_tiers_argument_is_refused() -> None:
    """The three rows are named arguments so a row cannot be attributed to a step it did not come
    from; swapping two would put one transform's verdict on another transform."""
    rows = {tier: _tier_report(tier) for tier in FACTOR_TIER_ORDER}

    with pytest.raises(FactorExperimentError, match=r"carries a 'neutralized' tier report"):
        build_factor_experiment(
            ic_spec=_ic_spec(),
            portfolio_spec=_quantile_spec(),
            tradeability_spec=_tradeability_spec(),
            survival_spec=_survival_spec(),
            retention_floor=RETENTION_FLOOR,
            code_commit=CODE_COMMIT,
            raw=rows["raw"],
            processed=rows["neutralized"],
            neutralized=rows["processed"],
            built_at=BUILT_AT,
        )


def test_a_tier_report_whose_studies_are_about_two_factors_is_refused() -> None:
    """Four studies of two factors in one row is a comparison wearing a heading."""
    honest = _tier_report("raw")
    other = _tier_report("raw", definition=_definition(key="probe_other"))

    with pytest.raises(ValidationError, match="one row is one factor"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=other.ic,
            portfolio=honest.portfolio,
            turnover=honest.turnover,
            tradeability=honest.tradeability,
            survival=None,
        )


def test_a_tier_report_whose_studies_are_over_two_samples_is_refused() -> None:
    """The row-level half of the sample rule: an IC over two days beside a spread over one is two
    questions in one row."""
    honest = _tier_report("raw")
    short = _tier_report("raw", as_ofs=(AS_OF,), ic_spec=_ic_spec(min_as_ofs=2))

    with pytest.raises(ValidationError, match="have to be asked about the same days"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=honest.ic,
            portfolio=short.portfolio,
            turnover=honest.turnover,
            tradeability=honest.tradeability,
            survival=None,
        )


def test_the_raw_row_carries_no_survival_and_the_others_must() -> None:
    """The asymmetry is forced by `factor_redundancy`, which refuses one factor on one tier
    against itself, so a raw-against-raw summary is not constructible at all."""
    processed = _tier_report("processed")

    with pytest.raises(ValidationError, match="exactly the raw tier carries none"):
        TierReport(
            tier="processed",
            source_manifest_ids=SOURCE_BUILDS["processed"],
            ic=processed.ic,
            portfolio=processed.portfolio,
            turnover=processed.turnover,
            tradeability=processed.tradeability,
            survival=None,
        )

    raw = _tier_report("raw")
    with pytest.raises(ValidationError, match="exactly the raw tier carries none"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=raw.ic,
            portfolio=raw.portfolio,
            turnover=raw.turnover,
            tradeability=raw.tradeability,
            survival=processed.survival,
        )


def test_a_survival_summary_of_another_pair_than_raw_against_this_tier_is_refused() -> None:
    """The survival number is the share of the ordering this transform left, and a pair that is
    not `raw` against this tier is not that number."""
    processed = _tier_report("processed")
    neutralized = _tier_report("neutralized")

    with pytest.raises(ValidationError, match="the pair has to be raw on the left"):
        TierReport(
            tier="processed",
            source_manifest_ids=SOURCE_BUILDS["processed"],
            ic=processed.ic,
            portfolio=processed.portfolio,
            turnover=processed.turnover,
            tradeability=processed.tradeability,
            survival=neutralized.survival,
        )


def test_two_specs_that_declare_two_factors_are_refused() -> None:
    """One experiment is one factor: two declarations would let the sign of the IC and the choice
    of the long group come from two different definitions."""
    with pytest.raises(ValidationError, match="one experiment is one factor"):
        FactorExperimentSpec(
            ic=_ic_spec(definition=_definition(key="probe_left")),
            portfolio=_quantile_spec(definition=_definition(key="probe_right")),
            tradeability=_tradeability_spec(),
            survival=_survival_spec(),
            retention_floor=RETENTION_FLOOR,
            code_commit=CODE_COMMIT,
            horizon_sessions=1,
            as_of_digest="set_probe",
            raw_source_digest="set_raw",
            processed_source_digest="set_processed",
            neutralized_source_digest="set_neutralized",
        )


def test_source_build_ids_are_distinct_and_ascending() -> None:
    """Two callers who assembled one set in two orders must produce one identity, so the tuple is
    normalised at the contract rather than by whoever built it."""
    honest = _tier_report("raw")

    with pytest.raises(ValidationError, match="distinct and ascending"):
        TierReport(
            tier="raw",
            source_manifest_ids=("fmn_probe_b", "fmn_probe_a"),
            ic=honest.ic,
            portfolio=honest.portfolio,
            turnover=honest.turnover,
            tradeability=honest.tradeability,
            survival=None,
        )


def test_a_tier_report_whose_studies_declare_two_directions_is_refused() -> None:
    """The direction decides the sign of the IC *and* which group is long, so two of them in one
    row make the row's two headline numbers point in two ways.

    Doctored with `model_copy`, which skips validation on purpose: the point is to hand the row
    validator an input the four studies could only produce by being run under two specs, and a
    fixture that ran them that way would be testing the two studies rather than this rule.
    """
    row = _tier_report("raw")

    with pytest.raises(ValidationError, match="the row's numbers point in two ways"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=row.ic.model_copy(update={"direction": "lower_is_better"}),
            portfolio=row.portfolio,
            turnover=row.turnover,
            tradeability=row.tradeability,
            survival=None,
        )


def test_a_tier_report_whose_quantile_and_turnover_studies_disagree_about_the_cut_is_refused() -> (
    None
):
    """One row is one cut, and the rolling portfolio has to be one of the groups that cut has."""
    row = _tier_report("raw")

    with pytest.raises(ValidationError, match="one row is one cut"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=row.ic,
            portfolio=row.portfolio,
            turnover=row.turnover.model_copy(update={"group_count": 3}),
            tradeability=row.tradeability,
            survival=None,
        )

    thin = _tier_report("raw", tradeability_spec=_tradeability_spec(min_rebalances=5))
    assert thin.turnover.coverage == "insufficient_rebalances"
    with pytest.raises(ValidationError, match="has to be one of the groups"):
        TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=row.ic,
            portfolio=row.portfolio,
            turnover=thin.turnover.model_copy(update={"group": 7}),
            tradeability=thin.tradeability,
            survival=None,
        )


def test_a_survival_summary_over_other_days_or_another_factor_is_refused() -> None:
    """The survival number is only the share of *this row's* ordering that survived, so a pair
    over other days or about another factor says nothing about the row it sits in."""
    row = _tier_report("processed")
    assert row.survival is not None

    one_day = _survival(
        "processed",
        PROCESSED_SCORES,
        survival_spec=_survival_spec(),
        definition=_definition(),
        as_ofs=(AS_OF,),
    )
    assert one_day.as_ofs == (AS_OF,)
    with pytest.raises(ValidationError, match="a survival correlation over other days"):
        TierReport(
            tier="processed",
            source_manifest_ids=SOURCE_BUILDS["processed"],
            ic=row.ic,
            portfolio=row.portfolio,
            turnover=row.turnover,
            tradeability=row.tradeability,
            survival=one_day,
        )

    with pytest.raises(ValidationError, match="a survival correlation is one factor's raw tier"):
        TierReport(
            tier="processed",
            source_manifest_ids=SOURCE_BUILDS["processed"],
            ic=row.ic,
            portfolio=row.portfolio,
            turnover=row.turnover,
            tradeability=row.tradeability,
            survival=row.survival.model_copy(update={"left_factor_id": "fct_somebody_else"}),
        )


def test_a_blank_source_build_id_is_refused() -> None:
    """A blank build id digests to something and names nothing, so an experiment identity built
    on one would be reproducible and unresolvable at the same time."""
    row = _tier_report("raw")

    with pytest.raises(ValidationError, match="a source build id cannot be blank"):
        TierReport(
            tier="raw",
            source_manifest_ids=("   ",),
            ic=row.ic,
            portfolio=row.portfolio,
            turnover=row.turnover,
            tradeability=row.tradeability,
            survival=None,
        )


def test_the_rolling_portfolio_may_follow_the_last_group_and_not_the_one_past_it() -> None:
    """The upper bound is exclusive, driven **at** the bound rather than seven groups past it.

    The rule is `0 <= group < group_count`, and the suite drove it with `group=7` against a cut of
    two -- a value on which `<` and `<=` agree, so the boundary itself was undecidable and a `<=`
    survived the whole suite. Off by one here is not cosmetic: `group_count - 1` is the *long*
    group, the one the acceptance criterion's spread is measured on, so a bound that admitted
    `group_count` would let a row declare a rolling portfolio over a group the quantile study never
    cut and report its churn beside a spread it does not belong to.

    Both sides are driven on one cut: the top legal group constructs, and the next integer does
    not. The `match=` is this rule's own phrase, because a `group` past the cut also disagrees with
    `tradeability.group` -- a bare `ValidationError` would pass on the neighbouring rule.

    Built on the `insufficient_rebalances` row the sibling above uses, and for that test's unstated
    reason: `TurnoverSeries` carries its own "a rebalance is filed under another group" validator,
    which fires first on a series that actually has rebalances in it, so a row-level bound can only
    be reached through a series with none.
    """
    thin = _tier_report("raw", tradeability_spec=_tradeability_spec(min_rebalances=5))
    count = thin.portfolio.group_count

    def _with_group(group: int) -> TierReport:
        return TierReport(
            tier="raw",
            source_manifest_ids=SOURCE_BUILDS["raw"],
            ic=thin.ic,
            portfolio=thin.portfolio,
            turnover=thin.turnover.model_copy(update={"group": group}),
            tradeability=thin.tradeability,
            survival=None,
        )

    assert count == 2
    assert thin.turnover.coverage == "insufficient_rebalances"
    assert _with_group(count - 1).turnover.group == count - 1
    with pytest.raises(ValidationError, match="has to be one of the groups"):
        _with_group(count)


def test_the_builder_refuses_three_tiers_at_two_horizons_rather_than_reading_one_of_them() -> None:
    """The builder's own refusal, and the reason the spec's horizon is unpacked rather than picked.

    `build_factor_experiment` reads two values off `raw` alone -- `as_of_digest` and
    `horizon_sessions` -- and only the first had a check saying the other two agree. The horizon's
    agreement was left to the artifact validator three constructions later, so `raw.horizon_
    sessions` was a positional pick out of an unchecked set and swapping it for `processed.horizon_
    sessions` changed nothing any test could observe.

    The refusal now comes from the builder, as a `FactorExperimentError` naming both horizons,
    rather than from a pydantic validator saying the rows "disagree about horizon_sessions" -- the
    same difference `as_ofs` already had. `model_copy` is used to move the horizon because it skips
    validation, which is what lets a row that the row-level "two windows in one row" rule would
    refuse reach the builder at all.
    """
    rows = {report.tier: report for report in _record().artifact.tiers}
    moved = rows["processed"].model_copy(
        update={"ic": rows["processed"].ic.model_copy(update={"horizon_sessions": 5})}
    )

    assert rows["raw"].horizon_sessions == 1
    assert moved.horizon_sessions == 5
    with pytest.raises(FactorExperimentError, match=r"the three tiers are at \[1, 5\] session"):
        build_factor_experiment(
            ic_spec=_ic_spec(),
            portfolio_spec=_quantile_spec(),
            tradeability_spec=_tradeability_spec(),
            survival_spec=_survival_spec(),
            retention_floor=RETENTION_FLOOR,
            code_commit=CODE_COMMIT,
            raw=rows["raw"],
            processed=moved,
            neutralized=rows["neutralized"],
            built_at=BUILT_AT,
            note=None,
        )


def test_an_artifact_whose_rows_are_at_another_horizon_than_the_spec_is_refused() -> None:
    """The horizon is on the spec because it is in the identity, and a spec claiming five
    sessions over one-session rows is an experiment nobody can re-derive."""
    artifact = _record().artifact
    moved = artifact.spec.model_copy(update={"horizon_sessions": 5})

    with pytest.raises(ValidationError, match="session\\(s\\) and the spec declares"):
        FactorExperimentArtifact(
            spec=moved, tiers=artifact.tiers, attributions=artifact.attributions
        )


def test_an_artifact_whose_rows_were_cut_differently_from_each_other_is_refused() -> None:
    """A spread compared across two cuts is two quantities, and each row is internally consistent
    -- both its quantile summary and its turnover series carry the same `group_count` -- so only
    the row-to-row comparison can see it."""
    artifact = _record().artifact
    rows = {report.tier: report for report in artifact.tiers}
    recut = _tier_report(
        "processed",
        quantile_spec=_quantile_spec(group_count=4, min_securities_per_group=1),
    )

    assert recut.portfolio.group_count == 4
    with pytest.raises(ValidationError, match="a spread compared across two cuts"):
        FactorExperimentArtifact(
            spec=artifact.spec,
            tiers=(rows["raw"], recut, rows["neutralized"]),
            attributions=artifact.attributions,
        )


def test_a_cell_that_carries_a_non_finite_number_is_refused() -> None:
    """Both statistics arrive from contracts that already refuse a non-finite one, so a
    non-finite here is a cell computed off contract rather than a measurement."""
    with pytest.raises(ValidationError, match="is not a finite number"):
        TierAttribution(
            from_tier="raw",
            to_tier="processed",
            statistic="mean_ic",
            retention_floor=RETENTION_FLOOR,
            from_value=float("inf"),
            to_value=1.0,
            retention=0.0,
            verdict="removed",
        )


def test_a_note_about_another_subject_is_refused() -> None:
    """A note that names another subject is prose about nothing, which is how a note survives the
    thing it described and goes on being shown."""
    record = _record()

    with pytest.raises(ValidationError, match="is prose about nothing"):
        FactorExperimentRecord(
            artifact=record.artifact,
            sealed_digest=record.content_digest,
            built_at=BUILT_AT,
            note=FactorNote(subject="somebody_else", summary="about another factor"),
        )


def test_reading_a_tier_or_a_cell_the_artifact_does_not_carry_names_what_it_does() -> None:
    """Both lookups refuse rather than returning `None`, so a typo in a handle does not read as an
    absent measurement."""
    artifact = _record().artifact

    with pytest.raises(FactorExperimentError, match="is not a tier this artifact reports"):
        artifact.tier_report("processed_v2")  # type: ignore[arg-type]
    with pytest.raises(FactorExperimentError, match="is not a declared attribution cell"):
        artifact.attribution(
            from_tier="neutralized",
            to_tier="raw",
            statistic="mean_ic",
        )


# --- 6. the registry ------------------------------------------------------------------------


def test_the_known_experiment_limitations_are_the_declared_six() -> None:
    """The registry is bound to this suite by an equality on its whole code set, which is the form
    `tests/unit/test_known_limitation_registries.py` requires: a membership assertion is additive
    and can see a rename but never a removal."""
    assert {
        "neutralised_residuals_are_read_at_a_year_end_snapshot",
        "the_seal_detects_an_edit_and_does_not_authenticate_one",
        "an_attribution_is_a_difference_between_two_declared_tiers_and_not_a_controlled_test",
        "the_retention_ratio_carries_no_test_of_the_difference_between_two_means",
        "the_attribution_grid_is_over_two_performance_statistics_and_not_over_tradeability",
        "nothing_in_this_module_stores_an_artifact_or_can_be_made_to",
    } == EXPERIMENT_LIMITATION_CODES
    assert len(KNOWN_EXPERIMENT_LIMITATIONS) == len(EXPERIMENT_LIMITATION_CODES)
    assert all(len(item.detail) > 200 for item in KNOWN_EXPERIMENT_LIMITATIONS)


def test_the_year_end_snapshot_constraint_is_the_code_the_two_upstream_modules_carry() -> None:
    """The same fact under the same name in three registries, because renaming it per module
    would make it three facts and a reader chasing one would find two.

    The neutralised row is the one the acceptance criterion turns on, so the constraint that its
    timestamps are a year-end snapshot has to be readable from this module's own registry rather
    than only from the two it consumes.
    """
    from openalpha_cn.backtest.factor_ic import IC_LIMITATION_CODES
    from openalpha_cn.backtest.factor_portfolio import QUANTILE_PORTFOLIO_LIMITATION_CODES

    code_name = "neutralised_residuals_are_read_at_a_year_end_snapshot"

    assert code_name in EXPERIMENT_LIMITATION_CODES
    assert code_name in IC_LIMITATION_CODES
    assert code_name in QUANTILE_PORTFOLIO_LIMITATION_CODES
    detail = next(item.detail for item in KNOWN_EXPERIMENT_LIMITATIONS if item.code == code_name)
    assert "V2-P4-026" in detail
    assert "read_visible_at" in detail


def test_the_declared_vocabularies_are_closed_and_ordered() -> None:
    """The three tables a report groups by, pinned as tuples rather than as sets, because the
    order is what a stored grid is laid out in."""
    assert ATTRIBUTION_VERDICT_ORDER == (
        "not_measured",
        "no_baseline",
        "reversed",
        "amplified",
        "removed",
        "survives",
    )
    assert ATTRIBUTION_STATISTIC_ORDER == ("mean_ic", "mean_spread")
    assert ATTRIBUTION_STEPS == (
        ("raw", "processed"),
        ("processed", "neutralized"),
        ("raw", "neutralized"),
    )
    assert RETENTIONLESS_VERDICTS < ATTRIBUTION_VERDICT_CODES
    assert {step for step, _target in ATTRIBUTION_STEPS} <= set(FACTOR_TIER_ORDER)
    assert {target for _step, target in ATTRIBUTION_STEPS} <= set(FACTOR_TIER_ORDER)


def test_every_declared_statistic_is_read_off_a_tier_report() -> None:
    """A statistic added to the vocabulary with no branch behind it would be a column of `None`s
    that no test could tell from a tier that measured nothing -- the failure this repository
    measured once already, where a table gained a key with no branch and the command exited 0."""
    row = _tier_report("raw")
    statistics: dict[AttributionStatistic, float | None] = {
        statistic: row.statistic(statistic) for statistic in ATTRIBUTION_STATISTIC_ORDER
    }

    assert set(statistics) == set(ATTRIBUTION_STATISTIC_ORDER)
    assert statistics["mean_ic"] == row.ic.mean_ic
    assert statistics["mean_spread"] == row.portfolio.mean_spread
    assert all(value is not None for value in statistics.values())
