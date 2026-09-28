"""The weekly forward-tracking report (`V2-P6-012`).

The daily command (`scripts/daily_selection.py`, `V2-P6-011`) registers one prediction per trading
day -- a `PredictionRecord` (`domain/prediction_record.py`) -- under the same registration the
holdout ran on (`docs/research/p6-registration.json`). This report, run weekly, chains every
admitted record into **one continuous book** and prices it with the registered configuration's own
portfolio rules -- the same holding count, rebalance interval, buffer, industry cap and costs the
holdout was measured under, sell side included -- so forward results are comparable with it. Fix
round 1 replaces this module's first design, which priced each prediction as an isolated,
fresh-capital, buy-only trade: that could not be compared with the holdout's own continuously
compounding book, and its statistics used an overlapping sample.

**Nothing here re-derives execution, costs or statistics.** The continuous book is one
`strategy_view.backtest_strategy` call, the exact path
`test_a_backtest_reading_the_registered_records_trades_as_the_configuration_would`
(`tests/unit/scripts/test_daily_selection.py`) proves trades identically to a backtest of the
registered configuration itself: a `ScoreSource` of `prediction_ids` naming every admitted record,
read through the prediction store's own `get`. Per-benchmark statistics are
`scripts.research.grid.strategy_result` unchanged -- the same reduction (complete-period
filtering, `sign_flip_test`, the one-sided conversion, the annualised information ratio) the
research grid gives the holdout. The code-binding refusal is `registry.admit_registered_code`
unchanged, with this file added to what it binds the way `scripts/daily_selection.py` binds
itself.

## What is excluded, and why it is never silent

A registered record enters the continuous book only when all of the following hold; every record
that fails one is excluded and returned with the reason (`ExcludedPrediction`), never dropped
silently:

1. **Not registered at or after its cutoff.** The book trades a session's scores at the next
   session's open, whose price the call auction fixes from 09:15
   (`strategy_registration.registration_cutoff`, the daily command's own rule, `V2-P6-011`). A
   record filed at or after that instant (a late or a caught-up run) is look-ahead, whatever
   `PredictionRecord.standing` says about the later outcome deadline.
2. **Its outcome is knowable at the report's `as_of`** (`PredictionRecord.outcome_known_at`, the
   label window's own close instant) -- a record whose window has not closed yet has no return
   to report.
3. **It declares this registration's own configuration.** A composite record must declare
   `strategy_registration.COMPOSITE_MODEL_NAME`, the family `strategy_<kind>` the registered
   source is, this registration's `config_id` as its `feature_version`, and the registered
   rebalance interval as its horizon; a walk-forward record must declare the registered
   `walk_forward` family and horizon. A record from a different configuration -- a stale
   registration, or a store shared with another one -- is not this registration's evidence.

## Reuse map

- The continuous book: `strategy_view.strategy_request` and `strategy_view.backtest_strategy`
  (`V2-P6-007`, `V2-P6-011`), with the registered configuration read through
  `daily_selection.strategy_arguments` -- the same parser `scripts/daily_selection.py` itself
  uses to turn a registration's canonical-JSON `config` back into `strategy_request`'s keywords.
- The registration-cutoff rule: `strategy_registration.registration_cutoff` (`V2-P6-011`).
- Per-benchmark statistics: `scripts.research.grid.strategy_result` (`V2-P6-008`), called once per
  `spec.benchmarks` name, imported the way `registry.py` imports its own sibling `grid` -- by
  adding `scripts/research` to `sys.path` once, the arrangement this repository already uses
  because `scripts/` carries no `__init__.py`.
- The registry's code-binding check: `registry.admit_registered_code` (`V2-P6-008`), with this
  file (`THIS_SCRIPT`) added to `also_bound` the way `scripts/daily_selection.py` binds itself.
  `registration` and `repo` are mandatory: there is no report without a registration to compare
  against, the way there is no forward result without one.
- The stored configuration's own identity: `grid.config_id`, re-derived from the registration's
  `config` and checked against its own `config_id` -- `run_holdout`'s own defence against a
  registration whose two halves disagree, applied here because this module has no external
  caller-supplied configuration to check `config_id` against the way `run_holdout` does.
- The panel and the prediction store: `panel_view.panel_store` and
  `runtime.composition.build_storage`, the same composition helpers the daily command uses
  (`main`, below).

Research wording stays "candidate"; this report states results, never expectations
(`AGENTS.md`, "Research honesty").
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from openalpha_cn.backtest.strategy_backtest import RETURN_QUANTUM, StrategyBacktest
from openalpha_cn.domain.prediction_record import PredictionRecord
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_trading_calendar, newest_published_session
from openalpha_cn.panel_view import panel_store
from openalpha_cn.providers.tushare import TRADING_CALENDAR_DEFAULT_EXCHANGE
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import (
    COMPOSITE_MODEL_NAME,
    HeldRecordLookup,
    registration_cutoff,
)
from openalpha_cn.strategy_view import (
    SHANGHAI,
    StrategyViewError,
    backtest_strategy,
    strategy_request,
)

_THIS_SCRIPT: Final[str] = "scripts/forward_report.py"
"""This file's repository-relative path, added to what a registration binds -- `daily_selection
.THIS_SCRIPT`'s own arrangement for its own file."""

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
_RESEARCH_ROOT: Final[Path] = _SCRIPTS / "research"
for _root in (_SCRIPTS, _RESEARCH_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402  (scripts/daily_selection.py: the registered configuration's own parser)
import grid  # noqa: E402  (scripts/research/grid.py; registry.py's own sibling-import arrangement)
import registry  # noqa: E402  (scripts/research/registry.py)

REGISTERED_AT_OR_AFTER_CUTOFF: Final[str] = "registered_at_or_after_its_registration_cutoff"
"""A record whose `max(predicted_at, recorded_at)` is at or after the next session's 09:15
registration cutoff (`strategy_registration.registration_cutoff`): its outcome window had already
opened, whatever `PredictionRecord.standing` says about the later deadline."""

OUTCOME_NOT_YET_KNOWABLE: Final[str] = "outcome_not_yet_knowable"
"""A record whose label window has not closed by the report's `as_of`."""

FOREIGN_TO_THE_REGISTRATION: Final[str] = (
    "declares_a_different_horizon_or_model_than_the_registration"
)
"""A record that does not declare this registration's own composite or model: a stale
registration, or a prediction store shared with another one."""

STANDING_FORWARD_CRITERIA: Final[Mapping[str, object]] = {
    "report": "openalpha-forward-tracking-weekly/v1"
}
"""The `criteria` a standing forward registration is written with (`registry.register`'s second
argument, the same generic registration `register()` already writes for the holdout). This report
evaluates whatever the daily command registered rather than testing a pass/fail bound of its own,
so this is a fixed label rather than a numeric threshold."""

_PORTFOLIO_ARGUMENT_KEYS: Final[tuple[str, ...]] = (
    "exchange",
    "rebalance_every_sessions",
    "holding_count",
    "buffer_rank",
    "max_industry_weight",
    "position_capital",
    "participation_cap",
    "slippage_rate",
    "benchmarks",
)
"""The registered configuration's portfolio-rule keywords, everything `strategy_request` needs
beside the score source -- read through `daily_selection.strategy_arguments` and carried over
unchanged onto the `prediction_ids` source this report builds."""


class ForwardReportError(ValueError):
    """The report cannot be produced as asked."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ExcludedPrediction:
    """One registered record this report did not chain into the book, and why."""

    record_id: str
    signal_day: date
    reason: str
    detail: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkStatistics:
    """One benchmark's forward numbers: `grid.strategy_result`'s reduction, unchanged, plus the
    cumulative net excess this report adds -- the plain sum of every period's net excess
    (`result["net_excess"]`), because a forward record is one continuously-compounding book and
    summing its own periods double-counts nothing a `net_return` has not already compounded."""

    result: Mapping[str, Any]
    cumulative_net_excess: Decimal

    @property
    def p_value(self) -> float:
        return float(self.result["p_excess"])

    @property
    def p_value_one_sided(self) -> float:
        return float(self.result["p_excess_one_sided"])

    @property
    def exact(self) -> bool:
        return bool(self.result["p_excess_exact"])

    @property
    def sign_patterns(self) -> int:
        return int(self.result["p_excess_sign_patterns"])

    @property
    def random_seed(self) -> int | None:
        seed = self.result["p_excess_random_seed"]
        return None if seed is None else int(seed)


@dataclass(frozen=True, slots=True, kw_only=True)
class ForwardReport:
    """What the weekly report answers: the continuous book, what was excluded and why, and the
    statistics per benchmark (`spec.benchmarks`, the all-A equal-weight series and 000905.SH in
    the protocol)."""

    as_of: datetime
    config_id: str
    backtest: StrategyBacktest
    excluded: tuple[ExcludedPrediction, ...]
    statistics: Mapping[str, BenchmarkStatistics]


def read_registered_predictions(store: HeldRecordLookup) -> tuple[PredictionRecord, ...]:
    """Every prediction the daily command has registered, in `store`'s own id order.

    A record `list_ids` names but `get` no longer holds under (a race with a concurrent write
    between the two calls) is skipped rather than raising on `None`; `test_a_record_removed_
    between_list_ids_and_get_is_skipped_not_raised` drives that race.
    """
    return tuple(
        record
        for record in (store.get(record_id) for record_id in store.list_ids())
        if record is not None
    )


def _expected_declaration(config: Mapping[str, object]) -> tuple[str | None, str, str]:
    """`(name, family, horizon)` a record must declare to be this registration's own.

    `name` is `None` for a walk-forward source: the fit's own declared name is whatever `model
    fit`'s caller named it, not `COMPOSITE_MODEL_NAME` (`strategy_registration`'s module
    docstring: "walk-forward uses the fit's own family").
    """
    rebalance_value = config["rebalance_every_sessions"]
    if not isinstance(rebalance_value, int) or isinstance(rebalance_value, bool):
        raise ForwardReportError(
            f"the registered rebalance_every_sessions {rebalance_value!r} is not an integer"
        )
    walk_forward = config.get("walk_forward")
    if isinstance(walk_forward, Mapping):
        family = str(walk_forward["family"])
        horizon_sessions = walk_forward.get("horizon_sessions")
        if not isinstance(horizon_sessions, int) or isinstance(horizon_sessions, bool):
            raise ForwardReportError(
                f"the registered walk_forward.horizon_sessions {horizon_sessions!r} is not an "
                "integer"
            )
        return None, family, f"{horizon_sessions}d"
    kind = "trailing_ic" if isinstance(config.get("trailing_ic"), Mapping) else "static"
    return COMPOSITE_MODEL_NAME, f"strategy_{kind}", f"{rebalance_value}d"


def classify_prediction(
    record: PredictionRecord,
    *,
    calendar: TradingCalendar,
    as_of: datetime,
    config: Mapping[str, object],
    config_id: str,
) -> tuple[date, str | None, str]:
    """`(signal_day, exclusion_reason, detail)`; `exclusion_reason` is `None` when `record` belongs
    in the continuous book.

    Checked in this order: the registration cutoff (`strategy_registration.registration_cutoff`,
    the daily command's own point-in-time rule -- not re-derived), the outcome deadline
    (`PredictionRecord.outcome_known_at`, read back rather than recomputed), then whether the
    record declares this registration's own configuration.
    """
    signal_day = record.batch.as_of.astimezone(SHANGHAI).date()
    cutoff = registration_cutoff(calendar, signal_day)
    available = max(record.batch.predicted_at, record.recorded_at)
    if available >= cutoff:
        return (
            signal_day,
            REGISTERED_AT_OR_AFTER_CUTOFF,
            f"available at {available.isoformat()}, at or after {signal_day.isoformat()}'s "
            f"registration cutoff {cutoff.isoformat()}",
        )
    if record.outcome_known_at > as_of:
        return (
            signal_day,
            OUTCOME_NOT_YET_KNOWABLE,
            f"the outcome is known at {record.outcome_known_at.isoformat()}, after the report's "
            f"as_of {as_of.isoformat()}",
        )
    declaration = record.batch.artifact.declaration
    expected_name, expected_family, expected_horizon = _expected_declaration(config)
    matches = declaration.family == expected_family and declaration.horizon == expected_horizon
    if expected_name is not None:
        matches = (
            matches
            and declaration.name == expected_name
            and declaration.feature_version == config_id
        )
    if not matches:
        return (
            signal_day,
            FOREIGN_TO_THE_REGISTRATION,
            f"declares name={declaration.name!r} family={declaration.family!r} "
            f"horizon={declaration.horizon!r} feature_version={declaration.feature_version!r}; "
            f"this registration expects family={expected_family!r} horizon={expected_horizon!r}"
            + (
                ""
                if expected_name is None
                else f" name={expected_name!r} feature_version={config_id!r}"
            ),
        )
    return signal_day, None, ""


def _forward_request_arguments(
    config: Mapping[str, object], *, prediction_ids: Sequence[str]
) -> dict[str, Any]:
    """The registered configuration's portfolio rules, as a `prediction_ids` source's keywords.

    Reuses `daily_selection.strategy_arguments` to parse the registration's canonical-JSON
    `config` -- Decimal and date parsing, the unknown-key refusal -- and keeps only the portfolio
    keys (`_PORTFOLIO_ARGUMENT_KEYS`): a static, trailing-IC or walk-forward source's own
    `components`/`trailing_ic`/`walk_forward` is replaced by `prediction_ids`, the daily command's
    own equality test's arrangement (`test_a_backtest_reading_the_registered_records_trades_as_
    the_configuration_would`).
    """
    resolved = daily_selection.strategy_arguments(config)
    arguments = {key: resolved[key] for key in _PORTFOLIO_ARGUMENT_KEYS if key in resolved}
    arguments.update(
        combine="zscore_sum",
        components=(),
        transform=None,
        neutralization=None,
        trailing_ic=None,
        walk_forward=None,
        prediction_ids=tuple(prediction_ids),
    )
    return arguments


def forward_report(
    store: PanelStore,
    prediction_store: FilePredictionStore,
    *,
    registration: Path,
    repo: Path,
    as_of: datetime,
) -> ForwardReport:
    """The weekly forward-tracking report: the registered configuration's continuous book over
    every admitted record, and the per-benchmark statistics of its own periods.

    `registration` and `repo` are mandatory -- `registry.admit_registered_code` (`V2-P6-008`)
    refuses before anything is read unless the registration is committed, byte-equal to `HEAD`,
    and the code running now (`REGISTERED_PATHS` plus this file) is the code it registered. The
    registration's own `config_id` is re-derived from its `config` and checked
    (`ForwardReportError` on a mismatch), `run_holdout`'s own defence against a doctored
    registration file, applied here because this module has no external caller-supplied
    configuration to check it against the way `run_holdout` does. The measurement settings
    (`bootstrap_samples`, `random_seed`) are read from the registration's own `settings`, never
    chosen by this report.
    """
    _root, admitted = registry.admit_registered_code(registration, repo, also_bound=(_THIS_SCRIPT,))
    body = admitted.registered
    config = body.get("config")
    if not isinstance(config, Mapping):
        raise ForwardReportError(f"{registration} registers no configuration object")
    config_id = grid.config_id(config)
    if config_id != body.get("config_id"):
        raise ForwardReportError(
            f"{registration}'s config_id {body.get('config_id')!r} does not match its own "
            f"config (re-derived: {config_id!r}); the registration file may have been edited "
            "after it was written"
        )
    settings = body.get("settings")
    if not isinstance(settings, Mapping):
        raise ForwardReportError(f"{registration} registers no measurement settings")
    bootstrap_samples, random_seed = settings.get("bootstrap_samples"), settings.get("random_seed")
    if not isinstance(bootstrap_samples, int) or isinstance(bootstrap_samples, bool):
        raise ForwardReportError(f"{registration}'s settings name no integer bootstrap_samples")
    if not isinstance(random_seed, int) or isinstance(random_seed, bool):
        raise ForwardReportError(f"{registration}'s settings name no integer random_seed")

    exchange = str(config.get("exchange", TRADING_CALENDAR_DEFAULT_EXCHANGE))
    try:
        anchor = daily_selection.anchor_of(config)
    except (KeyError, ValueError) as error:
        raise ForwardReportError(
            f"the registered configuration names no start date: {error}"
        ) from error

    records = read_registered_predictions(prediction_store)
    if not records:
        raise ForwardReportError("no prediction is registered; there is nothing to report")

    wanted = range(
        min(anchor.year, as_of.astimezone(SHANGHAI).year),
        max(record.batch.as_of.astimezone(SHANGHAI).year for record in records) + 2,
    )
    held = set(store.registered_years(TRADING_CALENDAR_DATASET))
    years = tuple(year for year in wanted if year in held)
    if not years:
        raise ForwardReportError(
            f"the panel holds no {exchange} trading calendar for any of {tuple(wanted)}"
        )
    calendar = load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)

    included_ids: list[str] = []
    signal_days: dict[str, date] = {}
    excluded: list[ExcludedPrediction] = []
    for record in records:
        signal_day, reason, detail = classify_prediction(
            record, calendar=calendar, as_of=as_of, config=config, config_id=config_id
        )
        if reason is not None:
            excluded.append(
                ExcludedPrediction(
                    record_id=record.record_id, signal_day=signal_day, reason=reason, detail=detail
                )
            )
            continue
        included_ids.append(record.record_id)
        signal_days[record.record_id] = signal_day

    if not included_ids:
        raise ForwardReportError(
            f"no registered prediction is chained into the book ({len(excluded)} excluded); "
            "there is nothing to test"
        )

    arguments = _forward_request_arguments(config, prediction_ids=included_ids)
    rebalance_every_sessions = int(arguments["rebalance_every_sessions"])
    start = min(signal_days.values())
    last_admitted = max(signal_days.values())
    try:
        published = newest_published_session(calendar, as_of=as_of)
        scheduled = calendar.shift(last_admitted, rebalance_every_sessions)
    except TradingCalendarError as error:
        raise ForwardReportError(f"the book's range cannot be resolved: {error}") from error
    end = min(published, scheduled)
    if end <= start:
        raise ForwardReportError(
            f"{end.isoformat()} is not after the first admitted record's signal day "
            f"{start.isoformat()}; there is no session to trade the book on"
        )
    try:
        request = strategy_request(**arguments, start=start, end=end, as_of=as_of)
        backtest = backtest_strategy(store, request, predictions=prediction_store.get)
    except StrategyViewError as error:
        raise ForwardReportError(f"the continuous book could not be run: {error}") from error

    stats: dict[str, BenchmarkStatistics] = {}
    for name in request.spec.benchmarks:
        try:
            result = grid.strategy_result(
                backtest,
                excess_benchmark=name,
                bootstrap_samples=bootstrap_samples,
                random_seed=random_seed,
            )
        except Exception as error:  # grid.strategy_result's own StrategyBacktestError
            raise ForwardReportError(
                f"benchmark {name!r} could not be measured: {error}"
            ) from error
        net_excess = result["net_excess"]
        if not isinstance(net_excess, list):
            raise ForwardReportError(
                f"benchmark {name!r}'s net_excess is not a list: {net_excess!r}"
            )
        cumulative = sum((Decimal(value) for value in net_excess), Decimal(0))
        stats[name] = BenchmarkStatistics(
            result=result, cumulative_net_excess=cumulative.quantize(RETURN_QUANTUM)
        )

    return ForwardReport(
        as_of=as_of,
        config_id=config_id,
        backtest=backtest,
        excluded=tuple(excluded),
        statistics=stats,
    )


# --- rendering and the command line --------------------------------------------------------------


def forward_report_view(report: ForwardReport) -> dict[str, Any]:
    """`report` as a JSON-safe mapping: the CLI's `--json` output and what it writes to disk."""
    return {
        "as_of": report.as_of.isoformat(),
        "config_id": report.config_id,
        "periods": [
            {
                "start": period.start.isoformat(),
                "end": period.end.isoformat(),
                "sessions": period.sessions,
                "net_return": str(period.net_return),
                "cost": str(period.cost),
                "gross_return": str(period.gross_return),
                "turnover": str(period.turnover),
                "held": period.held,
                "holdings": list(period.holdings),
                "rejected_orders": period.rejected_orders,
                "capped_orders": period.capped_orders,
            }
            for period in report.backtest.periods
        ],
        "excluded": [
            {
                "record_id": excluded.record_id,
                "signal_day": excluded.signal_day.isoformat(),
                "reason": excluded.reason,
                "detail": excluded.detail,
            }
            for excluded in report.excluded
        ],
        "statistics": {
            name: {
                "cumulative_net_excess": str(stat.cumulative_net_excess),
                "p_value": stat.p_value,
                "p_value_one_sided": stat.p_value_one_sided,
                "exact": stat.exact,
                "sign_patterns": stat.sign_patterns,
                "random_seed": stat.random_seed,
            }
            for name, stat in report.statistics.items()
        },
    }


def summary_lines(payload: Mapping[str, Any]) -> list[str]:
    lines = [
        f"as_of              {payload['as_of']}",
        f"config_id          {payload['config_id']}",
        f"periods            {len(payload['periods'])} "
        f"({sum(1 for p in payload['periods'] if not p['held'])} traded)",
        f"excluded           {len(payload['excluded'])}",
    ]
    for excluded in payload["excluded"]:
        lines.append(f"  {excluded['signal_day']} {excluded['record_id']} {excluded['reason']}")
    for name, stat in payload["statistics"].items():
        lines.append(
            f"benchmark {name:<20} cumulative net excess {stat['cumulative_net_excess']} "
            f"p={stat['p_value']:.4f} one-sided={stat['p_value_one_sided']:.4f}"
        )
    return lines


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] = _utc_now) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--registration", type=Path, default=daily_selection.DEFAULT_REGISTRATION)
    parser.add_argument("--repo", type=Path, default=daily_selection.REPOSITORY)
    parser.add_argument(
        "--as-of",
        type=daily_selection._instant,
        default=None,
        help="Pin the report's clock (default: now).",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    arguments = parser.parse_args(argv)
    as_of = arguments.as_of if arguments.as_of is not None else clock()
    runtime_dir = arguments.runtime_dir.resolve()
    store = panel_store(runtime_dir)
    prediction_store = build_storage(runtime_dir=runtime_dir, clock=lambda: as_of).prediction_store
    try:
        report = forward_report(
            store,
            prediction_store,
            registration=arguments.registration.resolve(),
            repo=arguments.repo.resolve(),
            as_of=as_of,
        )
    except (ForwardReportError, registry.HoldoutRefusedError, StrategyViewError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    payload = forward_report_view(report)
    reports_dir = runtime_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"forward-{as_of.astimezone(SHANGHAI).date().isoformat()}.json"
    out.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )
    if arguments.json:
        print(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    else:
        print("\n".join(summary_lines(payload)))
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
