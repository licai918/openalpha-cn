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

## A survivor registration (`V2-P7-018`)

A registered configuration that names `kind: "survivor_book/v1"` is read right after the
admission (`_survivor_of`, through `daily_selection.survivor_registration`) and dispatched to
`_survivor_forward_report`; one that names no `kind` is the P6 book and everything above runs
exactly as before. The survivor report is glue -- what it means lives in `src/`: the protocol
read from the registration's settings (`strategy_survivor_forward.read_forward_protocol`), the
records the prediction store witnesses, the late-record check over every one of them
(`strategy_survivor_check.SurvivorRecordCheck`, its provenance index and verdict cache), the
labels of the shared reader (`model_view.OutcomeLabels`) and the CSI 500's levels at each
pre-registered horizon, the paper book's record (`strategy_survivor_paper_report.paper_record`)
read without the paper book's lock and bounded to one head, and the document
(`strategy_survivor_forward.survivor_forward_document`), written to
`RT/reports/forward-survivor-<date>.json` beside P6's `forward-<date>.json` -- whole, by one
replace of a synced file of its own (`_replace_whole`), so a rerun that stops in the middle leaves
the earlier report standing, never a torn one. Every survivor module is imported inside that
branch: they load `backtest/paper.py`, whose audit hook a P6 report never installs. Its refusals
are `SurvivorReportError`, a `ForwardReportError`, so the command's exits are P6's: 0 written, 1
refused; a busy panel catalog is no refusal and stops the command as it is.

### The order it reads in, and the instant it reads at

No lock is taken, so a daily evening may be writing beside the report. An evening writes its
record into the prediction store, then its journal's `prediction` stage, then the paper ledger's
rows and its snapshot, then its journal's `paper` and `result` stages. The report reads in the
reverse order -- the paper book's snapshots are listed first, then the journals are read, then
the prediction store is scanned -- so that a later read never sees less than an earlier one
decided on: every snapshot listed has its journal's prediction stage, and every record a journal
names is in the store when it is scanned. The paper book's head is fixed by that first listing
(`_paper_section`), and every paper read is bounded to it.

`--as-of` pins what the report reads to that instant: a record registered after it is not
witnessed (`strategy_registration.registered_at`), a journal pinned after it is not read, and a
journal whose record was registered after it is read without the stages written after the record
(`_journals_at`) -- so the paper book's head is an evening that had filed by then. The panel is
the limit: its readers judge a partition whole, so a report pinned before the newest stored
session's 16:30 cannot read its labels or the benchmark at that instant and is refused by name.

One window remains whatever the order, and it is the design's head rule ("a snapshot and a
journal that filed its prediction or its result"): between an evening's snapshot and its
journal's `result`, the head is that evening while its journal has filed only its prediction, so
the paper record shows the session as a refused day. It lasts as long as the evening takes from
its snapshot to its summary; a day refused after its snapshot looks the same on disk, so the two
cannot be told apart, and a report run after the evening has finished reads the day whole.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from openalpha_cn.backtest.strategy_backtest import StrategyBacktest
from openalpha_cn.domain.index_prices import INDEX_DAILY_DATASET
from openalpha_cn.domain.trading_calendar import (
    TRADING_CALENDAR_DATASET,
    TradingCalendar,
    TradingCalendarError,
)
from openalpha_cn.panel.catalog import PanelStorageError
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_index_prices,
    load_trading_calendar,
    newest_published_session,
)
from openalpha_cn.panel_view import panel_store
from openalpha_cn.providers.tushare import TRADING_CALENDAR_DEFAULT_EXCHANGE
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.predictions import FilePredictionStore, PredictionStoreError
from openalpha_cn.strategy_registration import (
    StrategyRegistrationError,
    book_period_end,
    registered_at,
    schedule_of,
)
from openalpha_cn.strategy_view import SHANGHAI, StrategyViewError, backtest_strategy
from openalpha_cn.strategy_view import strategy_request as _strategy_request

if TYPE_CHECKING:
    # For their types only: at run time the survivor report imports these, and only for a
    # registration that names a `kind` (they load `backtest/paper.py`).
    from openalpha_cn.domain.index_prices import IndexBar
    from openalpha_cn.domain.prediction_record import PredictionRecord
    from openalpha_cn.domain.survivor_book import SurvivorBookConfig
    from openalpha_cn.runtime.composition import StorageContainer
    from openalpha_cn.strategy_survivor import SurvivorRegistration
    from openalpha_cn.strategy_survivor_forward import (
        PredictionLayer,
        RecordOutcome,
        WitnessedRecord,
    )

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


class SurvivorReportError(ForwardReportError):
    """A survivor registration's forward report cannot be produced as asked (`V2-P7-018`): no
    forward protocol, two records for one session, a calendar, a label or the provenance index
    that cannot be read, a check asked a question it cannot answer, a cache key or a change census
    the stored data refuses. A `ForwardReportError`, so the command's refusals are P6's: exit 1."""


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


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorForwardReport:
    """A survivor registration's weekly report (`V2-P7-018`): its `survivor_forward_report/v1`
    document, which is what is written and what `--json` prints.

    `this_run` is how this run reached it -- the late-record check's verdicts, and how many of
    them it served from the verdict cache rather than scoring the day again. It is printed, never
    written: a rerun at the same `as_of` serves every verdict the first run kept, and writes the
    same bytes."""

    as_of: datetime
    config_id: str
    document: Mapping[str, Any]
    this_run: Mapping[str, Any] = field(default_factory=dict)


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


def _survivor_of(admitted: daily_selection.Registration) -> SurvivorRegistration | None:
    """The admitted registration's survivor book, or `None` for one that names no `kind`: the
    P6 book, whose report runs as it always has. `daily_selection.survivor_registration` imports
    the survivor modules only for a registration that names a `kind`; its refusal (an unknown
    `kind`, a block that does not read, a seed that contradicts the registration's) is this
    report's."""
    try:
        return daily_selection.survivor_registration(admitted)
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
) -> ForwardReport | SurvivorForwardReport:
    """The weekly forward-tracking report: the registered configuration's continuous book over
    every session the daily command actually rebalanced on, and the evidence summary behind it.

    `registration` and `repo` are mandatory -- there is no report without a registration to
    compare against, the way there is no forward result without one. The book's range is the
    schedule's, not the first admitted record's own signal day (`forward_rebalances` decides both
    the start and every rebalance day); its end is the newest rebalance's own `book_period_end`,
    capped by what the panel has published.

    A survivor registration (`kind: "survivor_book/v1"`) is dispatched right after the admission
    to `_survivor_forward_report` (module docstring).
    """
    admitted = _admit(registration, repo)
    survivor = _survivor_of(admitted)
    if survivor is not None:
        return _survivor_forward_report(
            store,
            prediction_store,
            runtime_dir,
            admitted=admitted,
            survivor=survivor,
            as_of=as_of,
        )
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


# --- the survivor report (`V2-P7-018`) -----------------------------------------------------------


def _survivor_forward_report(
    store: PanelStore,
    prediction_store: FilePredictionStore,
    runtime_dir: Path,
    *,
    admitted: daily_selection.Registration,
    survivor: SurvivorRegistration,
    as_of: datetime,
) -> SurvivorForwardReport:
    """A survivor registration's weekly report (module docstring); glue only.

    In order: the protocol from the registration's settings, refused by name before anything is
    read when it is absent or does not read, and the power plan beside it, its absence said in
    the document; the stored calendar from the epoch's year through
    the report's (and the next when stored), counted from the epoch; then, in the reverse of an
    evening's write order and each as it stood at `as_of` (module docstring), the paper book's
    snapshots listed, the daily journals read, and the records the prediction store witnesses
    through the report's date, the journals held against them (a journal naming a record the
    store does not witness for its session refuses the report); the late-record check over every
    witnessed record, mature or not; each pre-registered horizon's outcomes and its layer on the
    non-overlapping sample; the paper book's record (`_paper_section`), bounded to the head the
    listing fixed, whose refusal is that section's and never the report's; and the document. The
    prediction layer's refusals -- the protocol, the witness (two records for one session, a
    journal that contradicts the store or carries no pinned instant, a record the store cannot
    read back), the check's provenance index and arguments, its cache key and change census (a
    registration, factor or panel-storage refusal of the data), the label reader, the benchmark's
    levels -- are `SurvivorReportError`; a `TypeError`, an `AssertionError` or a busy catalog is
    not a refusal, and propagates.
    """
    # Imported here, never for a P6 report: they load `backtest/paper.py` and its audit hook.
    from openalpha_cn import strategy_survivor_check as check
    from openalpha_cn import strategy_survivor_forward as forward
    from openalpha_cn.model_view import ModelPanelUnreadableError, ModelRunBlockedError
    from openalpha_cn.panel_factors import FactorEngineError
    from openalpha_cn.strategy_survivor_market import SessionOrdinalError
    from openalpha_cn.strategy_survivor_registration import (
        SurvivorCheckArgumentError,
        SurvivorRegistrationError,
    )

    try:
        return _survivor_report(
            store,
            prediction_store,
            runtime_dir,
            admitted=admitted,
            survivor=survivor,
            as_of=as_of,
        )
    except (
        forward.SurvivorForwardError,
        check.SurvivorCheckError,
        SurvivorCheckArgumentError,
        ModelRunBlockedError,
        ModelPanelUnreadableError,
        SessionOrdinalError,
        PredictionStoreError,
        # The late-record check's cache key and change census run outside the recompute's
        # refusal set (seams review 6): a 16:30 tier holding two builds, a blocked manifest
        # read, a catalog that refuses the read. A busy catalog (`PanelCatalogBusyError`, no
        # `PanelStorageError`) says nothing about the data and stops the command as it is.
        SurvivorRegistrationError,
        FactorEngineError,
        PanelStorageError,
    ) as error:
        raise SurvivorReportError(f"the survivor forward report is refused: {error}") from error


def _survivor_report(
    store: PanelStore,
    prediction_store: FilePredictionStore,
    runtime_dir: Path,
    *,
    admitted: daily_selection.Registration,
    survivor: SurvivorRegistration,
    as_of: datetime,
) -> SurvivorForwardReport:
    """`_survivor_forward_report`'s steps, its refusals unmapped."""
    from openalpha_cn import strategy_survivor_check as check
    from openalpha_cn import strategy_survivor_forward as forward
    from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
    from openalpha_cn.strategy_survivor_market import SessionOrdinals

    protocol = forward.read_forward_protocol(admitted.settings)
    power = forward.read_forward_power(admitted.settings, protocol=protocol)
    adapter, book = survivor.adapter, survivor.book
    exchange, epoch = adapter.exchange, adapter.epoch_session
    calendar = _survivor_calendar(store, exchange=exchange, epoch=epoch, as_of=as_of)
    ordinals = SessionOrdinals(calendar, epoch)
    registered = admitted.declared
    reported = as_of.astimezone(SHANGHAI).date()
    # The evening's writes, read in reverse (module docstring): the snapshots, the journals, the
    # prediction store -- each as it stood at `as_of`.
    listing = _paper_listing(runtime_dir, as_of=as_of)
    try:
        journals = _journals_at(
            _survivor_journals(runtime_dir, admitted), prediction_store, as_of=as_of
        )
    except daily_selection.StepFailedError as error:
        raise SurvivorReportError(f"a survivor journal cannot be read: {error}") from error
    witnessed = forward.witnessed_survivor_records(
        _FiledBy(prediction_store, as_of=as_of),
        book=book,
        registered=registered,
        calendar=calendar,
        ordinals=ordinals,
        through=reported,
    )
    sessions = tuple(item.session for item in witnessed)
    forward.check_journals_against_the_store(witnessed, journals, through=reported)
    through = forward.report_through(calendar, ordinals, as_of=as_of, witnessed=sessions)

    # Every witnessed record is checked, mature or not: an unchecked record is not evidence, and
    # the UNVERIFIABLE share is over all of them.
    record_check = check.SurvivorRecordCheck(
        store,
        book=book,
        transform=FACTOR_TRANSFORMS.get(adapter.transform),
        exchange=exchange,
        min_coverage=adapter.min_coverage,
        max_carry_sessions=adapter.max_carry_sessions,
        registered=registered,
        provenance=check.survivor_provenance_index(
            runtime_dir, registration_sha256=admitted.sha256
        ),
        verdicts=check.FileSurvivorVerdicts(runtime_dir, clock=_utc_now),
    )
    verdicts = {item.record_id: record_check(item.record) for item in witnessed}

    horizons = (protocol.primary_horizon, *protocol.secondary_horizons)
    outcomes = _survivor_outcomes(
        store, witnessed, horizons=horizons, book=book, exchange=exchange, as_of=as_of
    )
    layers = tuple(
        forward.prediction_layer(
            outcomes[horizon],
            verdicts,
            protocol=protocol,
            sampled=forward.sampled_sessions(
                ordinals, anchor_ordinal=protocol.anchor_ordinal, through=through, horizon=horizon
            ),
        )
        for horizon in horizons
    )
    paper = _paper_section(
        listing,
        journals,
        book=book,
        layer=layers[0],
        outcomes=outcomes[protocol.primary_horizon],
    )
    document = forward.survivor_forward_document(
        as_of=as_of,
        config_id=admitted.config_id,
        registration_sha256=admitted.sha256,
        protocol=protocol,
        ordinals=ordinals,
        through=through,
        witnessed=sessions,
        verdicts=verdicts,
        layers=layers,
        paper=paper,
        power=power,
        integrity=daily_selection.INTEGRITY,
    )
    return SurvivorForwardReport(
        as_of=as_of,
        config_id=admitted.config_id,
        document=document,
        this_run={
            "checked": len(verdicts),
            "served_from_cache": sum(1 for verdict in verdicts.values() if verdict.cached),
        },
    )


def _survivor_calendar(
    store: PanelStore, *, exchange: str, epoch: date, as_of: datetime
) -> TradingCalendar:
    """The stored calendar from the epoch's year through the report's, every year of it, and the
    next year when it is stored (a record's window may cross New Year). A year missing is refused
    by name: the book counts its sessions on it, and a report in a new year needs that year."""
    reported = as_of.astimezone(SHANGHAI).year
    required = tuple(range(epoch.year, reported + 1))
    held = set(store.registered_years(TRADING_CALENDAR_DATASET))
    missing = [year for year in required if year not in held]
    if missing:
        raise SurvivorReportError(
            f"the {exchange} calendar of {', '.join(map(str, missing))} is not built: the "
            f"survivor report counts its sessions from the epoch {epoch.isoformat()} through "
            f"the report's date {as_of.astimezone(SHANGHAI).date().isoformat()}, so a report in "
            "a new year needs that year's calendar -- the daily command's panel update builds it"
        )
    years = (*required, *((reported + 1,) if reported + 1 in held else ()))
    try:
        return load_trading_calendar(store, exchange=exchange, years=years, as_of=as_of)
    except (TradingCalendarError, PanelStorageError) as error:
        raise SurvivorReportError(
            f"the {exchange} calendar of {list(years)} cannot be read at {as_of.isoformat()}: "
            f"{error}"
        ) from error


def _survivor_outcomes(
    store: PanelStore,
    witnessed: Sequence[WitnessedRecord],
    *,
    horizons: Sequence[int],
    book: SurvivorBookConfig,
    exchange: str,
    as_of: datetime,
) -> dict[int, tuple[RecordOutcome, ...]]:
    """Every witnessed record at every horizon, labelled at `as_of` by the shared reader over the
    years from the first record's through the report's, beside the protocol benchmark's levels.
    No record, nothing read."""
    from openalpha_cn import strategy_survivor_forward as forward
    from openalpha_cn.model_view import LabelReach, OutcomeLabels

    if not witnessed:
        return {horizon: () for horizon in horizons}
    years = tuple(range(witnessed[0].session.year, as_of.astimezone(SHANGHAI).year + 1))
    labels = OutcomeLabels(store, LabelReach(as_of=as_of, years=years, exchange=exchange))
    csi500 = _benchmark_levels(store, labels.calendar, years=years, as_of=as_of)
    return {
        horizon: tuple(
            forward.record_outcome(
                labels, item, horizon=horizon, book=book, csi500=csi500, as_of=as_of
            )
            for item in witnessed
        )
        for horizon in horizons
    }


def _benchmark_levels(
    store: PanelStore, calendar: TradingCalendar, *, years: Sequence[int], as_of: datetime
) -> tuple[IndexBar, ...]:
    """The protocol benchmark's levels (`000905.SH`) over the stored years of `years`: the paper
    book persists no benchmark close, so the report reads the index at report time. A year not
    stored contributes nothing, and its closes are absent from every window that needs them. A
    read the panel blocks, a stored level row that does not read (`IndexPriceError`) and a read
    the batch rules refuse (`PanelBatchError`) refuse the report by name."""
    from openalpha_cn.domain.index_prices import IndexPriceError
    from openalpha_cn.domain.panel_batch import PanelBatchError
    from openalpha_cn.strategy_survivor_market import PROTOCOL_BENCHMARK

    stored = set(store.registered_years(INDEX_DAILY_DATASET))
    read = tuple(year for year in years if year in stored)
    if not read:
        return ()
    try:
        levels = load_index_prices(store, calendar, years=read, as_of=as_of, max_staleness=None)
    except (PanelStorageError, IndexPriceError, PanelBatchError) as error:
        raise SurvivorReportError(
            f"the {PROTOCOL_BENCHMARK} levels of {list(read)} cannot be read at "
            f"{as_of.isoformat()}: {error}"
        ) from error
    return tuple(levels.get(PROTOCOL_BENCHMARK, ()))


@dataclass(frozen=True, slots=True, kw_only=True)
class _PaperListing:
    """The paper book as the report found it first, before any journal or record is read: its
    stores, and the sessions its snapshots are held for (`_paper_section` takes its head from
    these, and nothing listed later)."""

    storage: StorageContainer
    sessions: tuple[date, ...]


def _paper_listing(runtime_dir: Path, *, as_of: datetime) -> _PaperListing:
    """The paper book's stores (`build_storage`, the one composition root) and its snapshots'
    sessions, listed now: the first of the report's reads (module docstring)."""
    storage = build_storage(runtime_dir=runtime_dir, clock=lambda: as_of)
    return _PaperListing(storage=storage, sessions=storage.paper_state_store.sessions())


def _paper_section(
    listing: _PaperListing,
    journals: Mapping[date, Mapping[str, Any]],
    *,
    book: SurvivorBookConfig,
    layer: PredictionLayer,
    outcomes: Sequence[RecordOutcome],
) -> dict[str, Any]:
    """The paper book's record (`paper_record`'s two blocks), or its refusal in both
    (`paper_refusal`): every refusal of the record -- the record's own, a snapshot that does not
    read back, a ledger row or a ledger table that does not -- is this section's, so that a fault
    of the paper book never costs the prediction layer. `journals` are this registration's
    survivor journals as they stood at the report's instant (`_journals_at`), read after the
    snapshots were listed.

    No lock is taken. Every read is bounded to one head: the newest session `listing` holds a
    snapshot for whose journal filed its `prediction` or its `result` -- snapshots stored since
    the listing, and the ledger rows and journals of sessions after the head, which an evening
    may be writing beside the report, are left out. Since a journal is read only as it stood at
    the report's instant, the head is an evening that had filed by then. The relative return's
    windows are the primary layer's sampled periods (`sampled_benchmarks`)."""
    import sqlite3

    from pydantic import ValidationError

    from openalpha_cn import strategy_survivor_forward as forward
    from openalpha_cn import strategy_survivor_paper_report as paper
    from openalpha_cn.backtest.paper_state import PaperStateError, load_snapshot
    from openalpha_cn.domain.versioning import UnknownSchemaVersionError
    from openalpha_cn.storage.paper_state import PaperStateStoreError

    state = listing.storage.paper_state_store
    try:
        filed = [
            session
            for session in listing.sessions
            if session in journals and {"prediction", "result"} & set(journals[session])
        ]
        if not filed:
            raise paper.PaperRecordError(
                "the paper book holds no snapshot of a session whose journal filed its "
                "prediction or its result, so it has no record to report yet"
            )
        head = max(filed)
        windows = [
            paper.BenchmarkWindow(
                record_id=item.record_id,
                entry=item.entry,
                exit=item.exit,
                investable_ew=item.investable_ew,
                csi500=item.csi500,
            )
            for item in forward.sampled_benchmarks(layer, outcomes)
        ]
        return paper.paper_record(
            [load_snapshot(state, session) for session in listing.sessions if session <= head],
            [row for row in listing.storage.paper_ledger.list() if row.after.as_of <= head],
            [journals[session] for session in sorted(journals) if session <= head],
            windows=windows,
            holding_count=book.holding_count,
        )
    except (
        paper.PaperRecordError,
        PaperStateError,
        PaperStateStoreError,
        # A ledger row that does not read back: an unknown schema version, a row its model
        # refuses, a payload that is not JSON; and a ledger table that cannot be read.
        UnknownSchemaVersionError,
        ValidationError,
        json.JSONDecodeError,
        sqlite3.DatabaseError,
    ) as error:
        return forward.paper_refusal(str(error))


@dataclass(frozen=True, slots=True)
class _FiledBy:
    """The prediction store as it stood at `as_of` (a `HeldRecordLookup`): a record registered
    after it -- `strategy_registration.registered_at`, the later of its batch's and the store's
    stamps -- is not shown, so a report pinned to an instant witnesses the records that existed
    then."""

    records: FilePredictionStore
    as_of: datetime

    def list_ids(self) -> tuple[str, ...]:
        return self.records.list_ids()

    def get(self, record_id: str) -> PredictionRecord | None:
        record = self.records.get(record_id)
        return None if record is None or registered_at(record) > self.as_of else record


_FILED_WITH_THE_RECORD: Final[tuple[str, ...]] = ("prediction", "paper", "result")
"""A survivor journal's stages an evening writes once its record is filed, in that order."""


def _journals_at(
    journals: Mapping[date, Mapping[str, Any]],
    records: FilePredictionStore,
    *,
    as_of: datetime,
) -> dict[date, Mapping[str, Any]]:
    """`journals` as they stood at `as_of`: one pinned after it is left out -- its evening had not
    begun -- and one whose prediction stage names a record registered after it is read without
    that stage and the two written after it (`_FILED_WITH_THE_RECORD`). A journal's `as_of` is the
    instant its evening pinned before any fetch; one that carries none, or one with no offset,
    refuses the report."""
    kept: dict[date, Mapping[str, Any]] = {}
    for session, body in sorted(journals.items()):
        pinned = body.get("as_of")
        try:
            begun = datetime.fromisoformat(pinned) if isinstance(pinned, str) else None
        except ValueError:
            begun = None
        if begun is None or begun.utcoffset() is None:
            raise SurvivorReportError(
                f"the journal of {session.isoformat()} pins no instant with its offset "
                f"({pinned!r}); the report reads a journal as it stood at its own as_of, and "
                "cannot tell whether this one had begun"
            )
        if begun > as_of:
            continue
        stage = body.get("prediction")
        if isinstance(stage, Mapping) and stage.get("registered") is True:
            record = records.get(str(stage.get("record_id")))
            if record is not None and registered_at(record) > as_of:
                body = {
                    name: value
                    for name, value in body.items()
                    if name not in _FILED_WITH_THE_RECORD
                }
        kept[session] = body
    return kept


def _survivor_journals(
    runtime_dir: Path, admitted: daily_selection.Registration
) -> dict[date, Mapping[str, Any]]:
    """This registration's survivor journals by session (`daily_selection.read_survivor_journal`,
    which refuses one that is not, `StepFailedError`), skipping every file whose name is not a
    session's -- the morning re-check's advice beside them, say. A journal another registration
    wrote is refused: the journals are what the store is held against."""
    directory = daily_selection.survivor_journal_directory(runtime_dir, admitted)
    journals: dict[date, Mapping[str, Any]] = {}
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else ():
        try:
            session = date.fromisoformat(path.stem)
        except ValueError:
            continue
        body = daily_selection.read_survivor_journal(path)
        if body is None:
            continue
        registration = body.get("registration")
        stated = registration.get("sha256") if isinstance(registration, Mapping) else None
        if stated not in (None, admitted.sha256):
            raise SurvivorReportError(
                f"{path.name} was written under another registration of this configuration "
                f"({stated}, not {admitted.sha256})"
            )
        journals[session] = body
    return journals


# --- rendering and the command line --------------------------------------------------------------


def forward_report_view(report: ForwardReport | SurvivorForwardReport) -> dict[str, Any]:
    """`report` as a JSON-safe mapping: `daily_selection.forward_summary`'s own dict (the one
    source of truth for what the evidence means), with `as_of` and `config_id` beside it -- or,
    for a survivor registration, its `survivor_forward_report/v1` document."""
    if isinstance(report, SurvivorForwardReport):
        return dict(report.document)
    return {"as_of": report.as_of.isoformat(), "config_id": report.config_id, **report.summary}


def summary_lines(report: ForwardReport | SurvivorForwardReport) -> list[str]:
    """The report as printed lines: `as_of`/`config_id`, then `daily_selection
    .forward_summary_lines`'s own rendering of `report.summary`, unmodified -- or, for a survivor
    registration, `strategy_survivor_forward.survivor_forward_lines` of its document and the
    run's own line."""
    if isinstance(report, SurvivorForwardReport):
        from openalpha_cn.strategy_survivor_forward import survivor_forward_lines

        run = report.this_run
        return [
            *survivor_forward_lines(report.document),
            f"this run: {run.get('checked', 0)} records checked, "
            f"{run.get('served_from_cache', 0)} verdicts served from the verdict cache",
        ]
    return [
        f"as_of              {report.as_of.isoformat()}",
        f"config_id          {report.config_id}",
        *daily_selection.forward_summary_lines(report.summary),
    ]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _replace_whole(path: Path, text: str) -> None:
    """`text` at `path`, which takes it whole or keeps what it held (seams review 10): written to
    a temporary file of this call's own beside it, flushed and synced, then put in place by one
    `os.replace`; the temporary file is removed when anything fails. A rerun's report replaces
    the earlier one, so a crash in the middle leaves the earlier report standing, never a torn
    one. The survivor report's file only: P6's is written as it always was."""
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".partial")
    partial = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


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
    day = as_of.astimezone(SHANGHAI).date().isoformat()
    survivor = isinstance(report, SurvivorForwardReport)
    # A survivor report's file never takes the P6 report's name (`V2-P7-018`).
    out = reports_dir / (f"forward-survivor-{day}.json" if survivor else f"forward-{day}.json")
    if survivor:
        _replace_whole(out, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        out.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
        )
    if arguments.json:
        printed = (
            {**payload, "this_run": dict(report.this_run)}
            if isinstance(report, SurvivorForwardReport)
            else payload
        )
        print(json.dumps(printed, sort_keys=True, ensure_ascii=False))
    else:
        print("\n".join(summary_lines(report)))
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
