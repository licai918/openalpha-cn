"""The weekly forward-tracking report (`V2-P6-012`).

The daily command (`V2-P6-011`, landing beside this issue) registers one prediction per trading
day -- a `PredictionRecord` (`domain/prediction_record.py`) -- before that day's outcome is
knowable. This report, run weekly, measures what those registered predictions actually earned
once their outcomes have become knowable, so forward results are comparable with the research
holdout: the same portfolio rules, the same cost schedule and the same execution policy
(`backtest/strategy_backtest.py`, `V2-P6-007`), and the same statistical test
(`backtest/outcome_statistics.sign_flip_test`) the research grid uses
(`scripts/research/grid.py`, `V2-P6-008`).

**Nothing here re-derives costs, execution or statistics.** Every fill is priced by
`run_strategy_backtest` unchanged: one isolated backtest per registered prediction, spanning
exactly that prediction's own label window (`domain/labels.build_label_window`, the same
construction `outcome_known_at` was derived from when the daily command registered it), entering
at the window's first session's open and marked at its last session's close. The one-sided
p-value is `grid.one_sided_p_value` unchanged, and the code-binding refusal below reuses
`registry`'s own guard pieces (`V2-P6-008`) rather than restating them.

## What is excluded, and why it is never silent

A registered prediction enters this report's statistics only when both hold:

1. it was available (`max(predicted_at, recorded_at)`, `strategy_view._prediction_rows`'s own
   point-in-time rule) at or before its own signal day's publication instant
   (`panel_ingest.session_publication_instant`) -- a prediction the daily command produced or
   filed *after* the instant it claims to trade at is look-ahead, whatever `PredictionRecord
   .standing` says about the much later outcome deadline;
2. its outcome is knowable at the report's `as_of` (`PredictionRecord.outcome_known_at`, the
   label window's own close instant) -- a prediction whose window has not closed yet has no
   return to report.

Every prediction that fails either check is excluded and returned with the reason
(`ExcludedPrediction`); this module never drops a row silently. A prediction is priced
independently of every other: each is its own one-period backtest with the protocol's starting
capital, not a single continuously-compounding book, because the daily command may skip a day or
register overlapping horizons and nothing here assumes otherwise.

## Reuse map

- Costs, execution, the T+1-open/mark-at-close book: `backtest.strategy_backtest
  .run_strategy_backtest` (`V2-P6-007`), driven directly rather than through the panel-reading
  `strategy_view` layer -- the panel quotes and benchmark return series this report needs are
  supplied by the caller (`quotes`, `benchmark_returns`), which keeps this module ignorant of
  where they come from (a real `PanelStore`, in production) and keeps its own tests hermetic.
- The statistic: `backtest.outcome_statistics.sign_flip_test`, and the one-sided conversion,
  `scripts.research.grid.one_sided_p_value` (`p_two/2` when the mean is positive, else
  `1 - p_two/2`), imported the way `registry.py` imports its own sibling `grid` -- by adding
  `scripts/research` to `sys.path` once, the arrangement this repository already uses because
  `scripts/` carries no `__init__.py`.
- The registry's code-binding check: `registry._committed_registration`,
  `_refuse_a_changed_source`, `_refuse_a_foreign_package` and `_refuse_foreign_scripts`
  (`V2-P6-008`), called directly rather than through `assert_holdout_allowed`, whose other two
  refusals (`RegistrationAfterHoldoutError`, `HoldoutAlreadyRanError`) are about the *one-shot*
  holdout and do not apply to a report meant to run every week.

Research wording stays "candidate"; this report states results, never expectations
(`AGENTS.md`, "Research honesty").
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, tzinfo
from decimal import Decimal
from pathlib import Path
from typing import Final

from openalpha_cn.backtest.outcome_statistics import sign_flip_test
from openalpha_cn.backtest.strategy_backtest import (
    PREDICTION_COMPONENT,
    RETURN_QUANTUM,
    PeriodResult,
    ScoreRow,
    ScoreSource,
    SessionQuote,
    StrategyInputs,
    StrategySpec,
    run_strategy_backtest,
)
from openalpha_cn.domain.horizon import parse_horizon
from openalpha_cn.domain.labels import build_label_window
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.trading_calendar import TradingCalendar
from openalpha_cn.panel_ingest import session_publication_instant
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_view import SHANGHAI

_RESEARCH_ROOT: Final[Path] = Path(__file__).resolve().parent / "research"
if str(_RESEARCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_RESEARCH_ROOT))

import grid  # noqa: E402  (scripts/research/grid.py; registry.py's own sibling-import arrangement)
import registry  # noqa: E402  (scripts/research/registry.py)

REGISTERED_AFTER_SIGNAL_INSTANT: Final[str] = "registered_after_its_signal_instant"
"""A prediction whose `max(predicted_at, recorded_at)` is after its own signal day's publication
instant: look-ahead, whatever the outcome deadline says."""

OUTCOME_NOT_YET_KNOWABLE: Final[str] = "outcome_not_yet_knowable"
"""A prediction whose label window has not closed by the report's `as_of`."""

STANDING_FORWARD_CRITERIA: Final[Mapping[str, object]] = {
    "report": "openalpha-forward-tracking-weekly/v1"
}
"""The `criteria` a standing forward registration is written with (`registry.register`'s second
argument). The forward report evaluates whatever the daily command registered rather than testing
a pass/fail bound of its own, so this is a fixed label rather than a numeric threshold."""


class ForwardReportError(ValueError):
    """The report cannot be produced as asked."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ExcludedPrediction:
    """One registered prediction this report did not price, and why."""

    record_id: str
    signal_day: date
    reason: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ForwardPeriod:
    """One registered prediction, priced: the record it came from and the one period it earned.

    `period` is `run_strategy_backtest`'s own `PeriodResult` (`V2-P6-007`), unmodified -- every
    field on it (fills, costs, turnover, the benchmark returns) is the book's, not restated here.
    """

    record_id: str
    signal_day: date
    period: PeriodResult


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkStatistics:
    """One benchmark's forward numbers: the cumulative net excess and the sign-flip test of the
    per-period excess over it."""

    cumulative_net_excess: Decimal
    p_value: float
    p_value_one_sided: float
    exact: bool
    sign_patterns: int
    random_seed: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ForwardReport:
    """What the weekly report answers: what was priced, what was not and why, and the statistics
    per benchmark (`spec.benchmarks`, the all-A equal-weight series and 000905.SH in the
    protocol)."""

    as_of: datetime
    included: tuple[ForwardPeriod, ...]
    excluded: tuple[ExcludedPrediction, ...]
    statistics: Mapping[str, BenchmarkStatistics]


def refuse_registered_code_drift(registration: Path, repo: Path) -> None:
    """Refuse to run this report when the registration's bound code has moved since it was
    written.

    Reuses `registry`'s own guard pieces (`V2-P6-008`) rather than re-deriving them: the
    registration must be committed and byte-identical to `HEAD` (`registry
    .RegistrationNotCommittedError`); `HEAD`'s bound paths (`src/`, `scripts/research/`,
    `pyproject.toml`, `uv.lock`) and the working tree must match the registration's `code_commit`
    (`registry.SourceChangedError`); and the `openalpha_cn` and the `grid`/`registry` modules this
    process imported must be this repository's own (`registry.ForeignPackageError`,
    `registry.ForeignScriptsError`). The two holdout-only refusals inside `registry
    .assert_holdout_allowed` -- a registration committed after a holdout row, and a holdout
    already run -- are about the *one-shot* holdout and are not asked here: the forward report is
    meant to run every week, not once.
    """
    root, admitted = registry._committed_registration(registration, repo)
    registry._refuse_a_changed_source(root, admitted.registered.get("code_commit"))
    registry._refuse_a_foreign_package(root)
    registry._refuse_foreign_scripts(root)


def read_registered_predictions(store: FilePredictionStore) -> tuple[PredictionRecord, ...]:
    """Every prediction the daily command has registered, in `store`'s own id order."""
    records = []
    for record_id in store.list_ids():
        record = store.get(record_id)
        if record is not None:  # pragma: no cover - a race with a concurrent write
            records.append(record)
    return tuple(records)


def classify_prediction(
    record: PredictionRecord, *, as_of: datetime
) -> tuple[date, str | None, str]:
    """`(signal_day, exclusion_reason, detail)`; `exclusion_reason` is `None` when `record` is
    priceable by this report.

    Mirrors `strategy_view._prediction_rows`'s own point-in-time reading of a `PredictionRecord`
    (`available = max(predicted_at, recorded_at)`, `signal_day = as_of` in `SHANGHAI`) rather than
    a second derivation of it, and adds nothing that reading does not already carry:
    `outcome_known_at` is the daily command's own deadline, read back rather than recomputed.
    """
    signal_day = record.batch.as_of.astimezone(SHANGHAI).date()
    signal_instant = session_publication_instant(signal_day)
    available = max(record.batch.predicted_at, record.recorded_at)
    if available > signal_instant:
        return (
            signal_day,
            REGISTERED_AFTER_SIGNAL_INSTANT,
            f"available at {available.isoformat()}, after {signal_day.isoformat()}'s signal "
            f"instant {signal_instant.isoformat()}",
        )
    if record.outcome_known_at > as_of:
        return (
            signal_day,
            OUTCOME_NOT_YET_KNOWABLE,
            f"the outcome is known at {record.outcome_known_at.isoformat()}, after the report's "
            f"as_of {as_of.isoformat()}",
        )
    return signal_day, None, ""


def _prediction_score_rows(record: PredictionRecord, *, signal_day: date) -> tuple[ScoreRow, ...]:
    """The one signal day's `ScoreRow`s a priced record contributes -- `strategy_view
    ._prediction_rows`'s construction for one already-admitted record."""
    available = max(record.batch.predicted_at, record.recorded_at)
    return tuple(
        ScoreRow(
            component=PREDICTION_COMPONENT,
            subject=prediction.ts_code,
            signal_day=signal_day,
            value=prediction.score,
            available_time=available,
            revision_time=record.recorded_at,
        )
        for prediction in record.batch.predictions
        if prediction.score is not None
    )


def price_prediction(
    record: PredictionRecord,
    *,
    quotes: Mapping[date, Mapping[str, SessionQuote]],
    benchmark_returns: Mapping[str, Mapping[date, Decimal]],
    spec: StrategySpec,
    calendar: TradingCalendar,
    zone: tzinfo = SHANGHAI,
) -> PeriodResult:
    """Price one already-admitted prediction with the real book, over exactly its own label
    window.

    The window is `domain.labels.build_label_window` over the record's own `batch.horizon` --
    the same construction `outcome_known_at_for` used when the daily command registered it, read
    again here (never re-derived) because the record does not store the session list itself.
    `spec.rebalance_every_sessions` is overridden to the window's length, so `run_strategy_backtest`
    produces exactly one `PeriodResult`, entering at the window's first session's open and marked
    at its last session's close -- the whole point of a *forward-tracking* report: what this one
    registered prediction actually earned, not a continuously-compounding book.
    """
    batch = record.batch
    signal_day = batch.as_of.astimezone(zone).date()
    window = build_label_window(
        as_of=batch.as_of, zone=zone, horizon=parse_horizon(batch.horizon), calendar=calendar
    )
    sessions = (signal_day, *window.sessions)
    inputs = StrategyInputs(
        source=ScoreSource(combine="zscore_sum", prediction_ids=(record.record_id,)),
        sessions=sessions,
        signal_instants={signal_day: session_publication_instant(signal_day)},
        scores=_prediction_score_rows(record, signal_day=signal_day),
        quotes=quotes,
        benchmark_returns=benchmark_returns,
    )
    resolved_spec = spec.model_copy(update={"rebalance_every_sessions": len(window.sessions)})
    result = run_strategy_backtest(inputs, resolved_spec)
    (period,) = result.periods
    return period


def forward_report(
    records: Sequence[PredictionRecord],
    *,
    as_of: datetime,
    quotes: Mapping[date, Mapping[str, SessionQuote]],
    benchmark_returns: Mapping[str, Mapping[date, Decimal]],
    spec: StrategySpec,
    calendar: TradingCalendar,
    zone: tzinfo = SHANGHAI,
    bootstrap_samples: int = grid.PROTOCOL_BOOTSTRAP_SAMPLES,
    random_seed: int = grid.PROTOCOL_RANDOM_SEED,
    registration: Path | None = None,
    repo: Path | None = None,
) -> ForwardReport:
    """The weekly forward-tracking report over `records`.

    Every record is classified first (`classify_prediction`); an excluded one is never priced.
    Each admitted record is then priced independently (`price_prediction`), and the per-benchmark
    statistics are the sign-flip test of the resulting per-period net excess
    (`net_return - period.benchmark_returns[name]`) on that non-overlapping sample -- each period
    spans exactly one record's own label window and no two overlap unless the caller registered
    two predictions over the same days, which is the caller's fact to report, not this function's
    to hide. `registration` and `repo`, given together, refuse the report
    (`refuse_registered_code_drift`) before anything is priced when the bound code has moved.
    """
    if (registration is None) != (repo is None):
        raise ForwardReportError("registration and repo must be given together or not at all")
    if registration is not None:
        assert repo is not None
        refuse_registered_code_drift(registration, repo)

    included: list[ForwardPeriod] = []
    excluded: list[ExcludedPrediction] = []
    for record in records:
        signal_day, reason, detail = classify_prediction(record, as_of=as_of)
        if reason is not None:
            excluded.append(
                ExcludedPrediction(
                    record_id=record.record_id, signal_day=signal_day, reason=reason, detail=detail
                )
            )
            continue
        period = price_prediction(
            record,
            quotes=quotes,
            benchmark_returns=benchmark_returns,
            spec=spec,
            calendar=calendar,
            zone=zone,
        )
        included.append(
            ForwardPeriod(record_id=record.record_id, signal_day=signal_day, period=period)
        )

    if not included:
        raise ForwardReportError(
            f"no registered prediction is priced by this report ({len(excluded)} excluded); "
            "there is nothing to test"
        )

    stats: dict[str, BenchmarkStatistics] = {}
    for name in spec.benchmarks:
        excess = tuple(
            forward.period.net_return - forward.period.benchmark_returns[name]
            for forward in included
        )
        values = tuple(float(value) for value in excess)
        test = sign_flip_test(values, bootstrap_samples=bootstrap_samples, random_seed=random_seed)
        mean = statistics.fmean(values)
        one_sided = grid.one_sided_p_value(test.p_value, mean)
        cumulative = sum(excess, Decimal(0)).quantize(RETURN_QUANTUM)
        stats[name] = BenchmarkStatistics(
            cumulative_net_excess=cumulative,
            p_value=test.p_value,
            p_value_one_sided=one_sided,
            exact=test.exact,
            sign_patterns=test.sign_patterns,
            random_seed=test.random_seed,
        )

    return ForwardReport(
        as_of=as_of, included=tuple(included), excluded=tuple(excluded), statistics=stats
    )
