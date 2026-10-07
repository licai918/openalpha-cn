"""The weekly forward-tracking report (`V2-P6-012`).

The daily command (`scripts/daily_selection.py`, `V2-P6-011`) registers one prediction per trading
day -- a `PredictionRecord` (`domain/prediction_record.py`) -- under the same registration the
holdout ran on (`docs/research/p6-registration.json`). This report, run weekly, is a thin layer
over the landed forward-evidence machinery (`V2-P6-011` rounds 9-12, for this issue): it admits the
registration, asks the append-only prediction store which sessions the command actually rebalanced
on (`daily_selection.forward_rebalances`), reads those records through the registration's own
verification (`daily_selection.forward_record_check`), prices them in one continuous book with the
registered portfolio rules on exactly those days (`strategy_view.backtest_strategy` with
`rebalance_days`), and renders what `daily_selection.forward_summary` says about the evidence
behind it. **There is one source of truth for what the evidence means; this module does not keep a
second one.**

Fix round 2 replaces round 1's own `classify_prediction`/exclusion machinery entirely: round 1 was
written before `V2-P6-011`'s forward-evidence rounds landed, and re-derived (less carefully) what
the store, the registration's own check and `forward_summary` now do -- which days the command
rebalanced on, whether a late-filed record is trustworthy, and how a correction discovered after
filing is shown. None of that is re-derived here anymore.

## Reuse map

- **Which days the book trades, and on which record**: `daily_selection.forward_rebalances`
  (`ForwardSchedule`) -- witnessed from the append-only prediction store, the journal only
  cross-checked, from the store's own first bound on-time record (`V2-P6-011` rounds 9-10).
- **Whether a record filed after its signal instant may be read**:
  `daily_selection.forward_record_check` (a `strategy_registration.RecordCheck`), passed as
  `backtest_strategy(verify_late=...)`: bound to the registration, recomputed at its own filing
  time, and -- when an input it read was corrected since -- admitted and flagged as
  `strategy_registration.UNVERIFIABLE` rather than silently trusted or refused.
- **The continuous book**: `strategy_view.strategy_request` (with `rebalance_days` from the
  schedule, replacing the fixed grid) and `strategy_view.backtest_strategy`, the registered
  portfolio's own rules -- holding count, rebalance interval, buffer, industry cap, costs, sell
  side -- with the registered configuration read through `daily_selection.strategy_arguments`.
- **Where the last period ends**: `strategy_registration.book_period_end` on the schedule's newest
  record, ending on the registered grid's next rebalance after it -- the interval after it on a
  scheduled day, sooner after a catch-up -- capped by what the panel has actually published. The
  calendar is read through the next year when stored (`daily_selection._outcome_calendar`'s
  rule), so a period that ends in January can be placed on 31 December.
- **What the evidence means**: `daily_selection.forward_summary` /
  `daily_selection.forward_summary_lines` -- the candidate book as listed, the same book's
  statistics excluding the periods an unverifiable record opened, each tested as the holdout
  tested it (`grid.strategy_result` under the registration's own settings), the unprovable holds
  before the first record, and `daily_selection.INTEGRITY`, the threat model stated on every
  forward report. This module renders exactly that; it computes no statistic of its own.
- **Admission**: `daily_selection.admit_registration` itself, with this file added to what the
  registration binds (`also_bound`) -- the registry's code-binding check (`V2-P6-008`), the
  running environment against the registration's `.python-version` and `uv.lock`, and the
  registration's `config_id` re-derived from its `config` (`run_holdout`'s defence against two
  halves that disagree) all in that one place.
- **The panel and the prediction store**: `panel_view.panel_store` and
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from openalpha_cn.backtest.strategy_backtest import StrategyBacktest
from openalpha_cn.domain.trading_calendar import TRADING_CALENDAR_DATASET, TradingCalendarError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import load_trading_calendar, newest_published_session
from openalpha_cn.panel_view import panel_store
from openalpha_cn.providers.tushare import TRADING_CALENDAR_DEFAULT_EXCHANGE
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.predictions import FilePredictionStore
from openalpha_cn.strategy_registration import (
    StrategyRegistrationError,
    book_period_end,
    schedule_of,
)
from openalpha_cn.strategy_view import SHANGHAI, StrategyViewError, backtest_strategy
from openalpha_cn.strategy_view import strategy_request as _strategy_request

_THIS_SCRIPT: Final[str] = "scripts/forward_report.py"
"""This file's repository-relative path, added to what a registration binds, beside
`daily_selection.THIS_SCRIPT` -- a forward report is as much this file's claim as it is the daily
command's."""

_SCRIPTS: Final[Path] = Path(__file__).resolve().parent
_RESEARCH_ROOT: Final[Path] = _SCRIPTS / "research"
for _root in (_SCRIPTS, _RESEARCH_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import daily_selection  # noqa: E402  (scripts/daily_selection.py: the forward-evidence machinery)
import registry  # noqa: E402  (scripts/research/registry.py)

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
class ForwardReport:
    """What the weekly report answers: the continuous book and the evidence summary behind it.

    `summary` is `daily_selection.forward_summary`'s own dict, unmodified -- the one source of
    truth for what the evidence means; `backtest`, `schedule` and `check` are kept for a caller
    that wants the underlying pieces (the SDK, say), not restated by this dataclass.
    """

    as_of: datetime
    config_id: str
    registration: Any
    """`daily_selection.Registration`, kept opaque here so this module does not restate its
    shape."""
    schedule: Any
    """`daily_selection.ForwardSchedule`."""
    check: Any
    """`strategy_registration.RecordCheck`, already run: `check.verified` and `check.unverifiable`
    are populated by the backtest that read it."""
    backtest: StrategyBacktest
    summary: Mapping[str, Any]


def _admit(registration: Path, repo: Path) -> daily_selection.Registration:
    """`daily_selection.admit_registration`, with this file added to what the registration binds;
    its refusal is this report's."""
    if not registration.is_file():
        raise ForwardReportError(
            f"{registration} does not exist; the forward report reads the registered "
            "configuration's own records and there is no registration to read"
        )
    try:
        return daily_selection.admit_registration(registration, repo, also_bound=(_THIS_SCRIPT,))
    except daily_selection.StepFailedError as error:
        raise ForwardReportError(str(error)) from error


def _forward_request_arguments(
    config: Mapping[str, object], *, prediction_ids: Sequence[str], rebalance_days: Sequence[Any]
) -> dict[str, Any]:
    """The registered configuration's portfolio rules, as a `prediction_ids` source's keywords,
    rebalancing on exactly `rebalance_days` (the schedule the store witnessed) rather than the
    fixed grid.

    Reuses `daily_selection.strategy_arguments` to parse the registration's canonical-JSON
    `config` -- Decimal and date parsing, the unknown-key refusal -- and keeps only the portfolio
    keys (`_PORTFOLIO_ARGUMENT_KEYS`): a static, trailing-IC or walk-forward source's own
    `components`/`trailing_ic`/`walk_forward` is replaced by `prediction_ids`, the daily command's
    own equality test's arrangement
    (`test_a_backtest_reading_the_registered_records_trades_as_the_configuration_would`).
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
        rebalance_days=tuple(rebalance_days),
    )
    return arguments


def forward_report(
    store: PanelStore,
    prediction_store: FilePredictionStore,
    runtime_dir: Path,
    *,
    registration: Path,
    repo: Path,
    as_of: datetime,
) -> ForwardReport:
    """The weekly forward-tracking report: the registered configuration's continuous book over
    every session the daily command actually rebalanced on, and the evidence summary behind it.

    `registration` and `repo` are mandatory -- there is no report without a registration to
    compare against, the way there is no forward result without one. The book's range is the
    schedule's, not the first admitted record's own signal day (`forward_rebalances` decides both
    the start and every rebalance day); its end is the newest rebalance's own `book_period_end`,
    capped by what the panel has published.
    """
    admitted = _admit(registration, repo)
    config = admitted.config
    exchange = str(config.get("exchange", TRADING_CALENDAR_DEFAULT_EXCHANGE))

    through = as_of.astimezone(SHANGHAI).date()
    try:
        schedule = daily_selection.forward_rebalances(
            runtime_dir, admitted, through=through, as_of=as_of
        )
    except daily_selection.StepFailedError as error:
        raise ForwardReportError(str(error)) from error
    if not schedule.rebalances:
        raise ForwardReportError(
            f"the store witnesses no rebalance of this registration through {through.isoformat()}"
        )
    check = daily_selection.forward_record_check(runtime_dir, admitted, as_of=as_of)

    rebalance_days = tuple(session for session, _record in schedule.rebalances)
    prediction_ids = tuple(record_id for _session, record_id in schedule.rebalances)
    arguments = _forward_request_arguments(
        config, prediction_ids=prediction_ids, rebalance_days=rebalance_days
    )
    # V2-P6-024: the registration's own excess benchmark is priced even when it is no longer
    # one of the protocol's defaults, so an older registration is tested against what it named.
    try:
        arguments["benchmarks"] = daily_selection.priced_benchmarks(
            arguments.get("benchmarks"), admitted.settings
        )
    except daily_selection.StepFailedError as error:
        raise ForwardReportError(str(error)) from error
    rebalance_every_sessions = int(arguments["rebalance_every_sessions"])

    # Through the next year when it is stored: the newest period may end in January.
    years = tuple(range(admitted_anchor_year(admitted), as_of.astimezone(SHANGHAI).year + 2))
    held = set(store.registered_years(TRADING_CALENDAR_DATASET))
    calendar_years = tuple(year for year in years if year in held)
    if not calendar_years:
        raise ForwardReportError(f"the panel holds no {exchange} trading calendar for {years}")
    calendar = load_trading_calendar(store, exchange=exchange, years=calendar_years, as_of=as_of)

    newest_day, newest_record_id = schedule.rebalances[-1]
    newest_record = prediction_store.get(newest_record_id)
    if newest_record is None:
        raise ForwardReportError(
            f"the schedule names {newest_record_id}, which the prediction store no longer holds"
        )
    try:
        # The grid's next rebalance after the newest one: the interval after a scheduled day,
        # sooner after a catch-up, which the daily command rebalances on as scheduled.
        position = schedule_of(
            calendar,
            anchor=daily_selection.anchor_of(config),
            session=newest_day,
            every=rebalance_every_sessions,
            previous=None,
        ).position
        next_rebalance = calendar.shift(
            newest_day, rebalance_every_sessions - position % rebalance_every_sessions
        )
        grid_end = book_period_end(
            newest_record, calendar=calendar, next_rebalance=next_rebalance
        ).date()
        published = newest_published_session(calendar, as_of=as_of)
    except (TradingCalendarError, StrategyRegistrationError) as error:
        raise ForwardReportError(
            f"the newest period's end cannot be placed on the stored calendar: {error}"
        ) from error
    end = min(grid_end, published)
    if end <= schedule.first_record:
        raise ForwardReportError(
            f"{end.isoformat()} is not after the first rebalance day "
            f"{schedule.first_record.isoformat()}; there is no session to trade the book on"
        )

    try:
        request = _strategy_request(**arguments, start=schedule.first_record, end=end, as_of=as_of)
        backtest = backtest_strategy(
            store, request, predictions=prediction_store.get, verify_late=check
        )
    except StrategyViewError as error:
        raise ForwardReportError(f"the continuous book could not be run: {error}") from error

    try:
        summary = daily_selection.forward_summary(
            backtest, check, schedule, settings=admitted.settings
        )
    except daily_selection.StepFailedError as error:
        raise ForwardReportError(str(error)) from error
    return ForwardReport(
        as_of=as_of,
        config_id=admitted.config_id,
        registration=admitted,
        schedule=schedule,
        check=check,
        backtest=backtest,
        summary=summary,
    )


def admitted_anchor_year(admitted: daily_selection.Registration) -> int:
    """The registered configuration's own anchor year -- `daily_selection.anchor_of`, named here
    so a mypy-visible `int` reaches the calendar-years range without a second `date.fromisoformat`
    of the same field."""
    return daily_selection.anchor_of(admitted.config).year


# --- rendering and the command line --------------------------------------------------------------


def forward_report_view(report: ForwardReport) -> dict[str, Any]:
    """`report` as a JSON-safe mapping: `daily_selection.forward_summary`'s own dict (the one
    source of truth for what the evidence means), with `as_of` and `config_id` beside it."""
    return {"as_of": report.as_of.isoformat(), "config_id": report.config_id, **report.summary}


def summary_lines(report: ForwardReport) -> list[str]:
    """The report as printed lines: `as_of`/`config_id`, then `daily_selection
    .forward_summary_lines`'s own rendering of `report.summary`, unmodified."""
    return [
        f"as_of              {report.as_of.isoformat()}",
        f"config_id          {report.config_id}",
        *daily_selection.forward_summary_lines(report.summary),
    ]


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
            runtime_dir,
            registration=arguments.registration.resolve(),
            repo=arguments.repo.resolve(),
            as_of=as_of,
        )
    except (
        ForwardReportError,
        registry.HoldoutRefusedError,
        StrategyViewError,
        daily_selection.StepFailedError,
    ) as error:
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
        print("\n".join(summary_lines(report)))
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
