"""What the survivor book's paper settlement reads off the panel for one session (`V2-P7-009`).

The paper book settles a session from three things the panel holds: the bars of the names it
trades or holds, with the exchange's published band (`backtest/paper_step.build_paper_step`
refuses a bar without one); each of those names' cumulative adjustment factor, whose ratio to the
factor on the session a buy filled scales a held name's bar (T5
`docs/research/p7-step0-validation/T5-paper-step-builder.md` section 2); and the benchmark's
close, which the step carries and the book does not keep (T5 section 0). `settlement_market`
reads those, and `SessionOrdinals` turns a session into the integer `TradeOrder` counts and back.

## One session off the panel (`settlement_market`)

Every read is at `as_of`, through the loader that owns the dataset, so the panel's visibility
rules are the only time filter here; `as_of` must be at or after the session's 16:30 publication
(`panel_ingest.session_publication_instant`), and is refused by name before anything is read
otherwise. The reads share one catalog state (`PanelStore.reading`), and the loaders' own
refusals reach the caller unchanged.

- **Bars.** A name is in `bars` when the session has its `daily` row and its published
  `stk_limit` band, and absent from it only when the session has no `daily` row for it -- a
  whole-day halt, or a name the panel does not price -- which the caller reads as exactly that:
  `build_paper_step` records the name's order as not attempted, and the settlement may write
  off a delisted holding (D13: nothing recovered, a realised loss of its whole cost basis). A
  name with a `daily` row and **no** band is refused by name (`UnbandedBarError`) rather than
  left out: left out, a priced name would read as unpriced, and the write-off cannot be taken
  back. On the book's own window it is a data failure, not a shape of the market: `daily` is a
  subset of `stk_limit` on every session sampled from 2023-01-03 on (`domain/price_limits.py`).
  **Before that it is a shape**, and this reader must not be reused for a 2017-2021 replay
  without handling it: `stk_limit` reached the Beijing board late and `001914.SZ` has no band on
  any session sampled from 2013-01-04 to 2022-04-01, so `UnbandedBarError` would stop every
  night on which such a name is held or ordered. The bar is `execution.stored_market_bar`'s --
  the one assembly the P6 faces use too; T5 7.1 refused a fourth copy. Its `suspended` is
  `execution.suspended_at_the_open`, the P6 book's collapse for an order filled at the open: a
  halt through the close, or any timed
  interruption whatever its window (fail-closed, as `strategy_backtest` records). So a name
  halted part of the session has a bar marked `suspended`, `execution.at_the_open` keeps the mark
  and the book refuses its order whole. `is_st` is `False` and is never read: every bar here
  carries the exchange's band, so `_price_band` never derives one -- the P6 book's own choice,
  for the same book shape -- and reading the name corpus to fill it would add a dataset and a
  refusal to the evening for a field nobody reads.
- **Adjustment factors.** The factor in force on the session, read from the session's own year
  (`panel_ingest.load_adjustment_histories`), for each name the year's series covers through the
  session; a name it does not cover is left out, never given 1 or a carried value, and the caller
  refuses a holding without a ratio by name. Tushare serves `adj_factor` on a whole-day halt:
  measured for this issue on the research store's year-edge sessions (2017-01-03 .. 2021-01-04,
  the first and last session of each research year, where a compressed partition shows the
  first and last session a factor was served), **543 of 543** main-board whole-day halts -- a
  `suspend_d` `S` row and no `daily` row -- had a factor row on that very session, which is
  `domain/adjustment.py`'s `suspension_is_invisible` measured at scale. So a halted holding with
  a sale due has its ratio, and the sale is not attempted rather than stopping the night.
  **Only the session's year is read**, where the P6 book's quotes (`strategy_view._PanelDays.
  adjustments`) read the year before it too, "for a name with no factor row yet this year": on
  a year's first sessions, a holding whose series for the year starts after the session is left
  out here -- and refused by the settlement -- where the P6 book would carry the previous year's
  value. That fails closed, and the year-head measurement above found 0 such holdings among the
  halted names (every one had its row on the year's first session).
- **The benchmark's close** on the session itself, from `index_daily` (`000905.SH` by default,
  the protocol's 中证500). A benchmark with no close on the session is refused by name
  (`MissingBenchmarkError`); the previous close is never used.
- **Only the names asked for are built.** Each loader reads what it always reads -- a session of
  bars and bands, a year of halts, factors and index levels -- but a `MarketBar` is assembled and
  a factor looked up only for a name in `names`, so the cost of a settlement scales with the
  book, not the market, and a name outside it can neither enter the answer nor fail it -- a
  band-less bar refuses the read only when its name was asked for. A name the panel has never
  heard of is absent from both maps.

**The look-ahead guard.** `daily`, `stk_limit` and `suspend_d` are read through their
point-in-time doors, so a later session's rows are invisible at `as_of`. `adj_factor` and
`index_daily` are read a year at a time through the whole-partition door, which refuses an
`as_of` earlier than the newest row its year holds (`PartitionNotYetKnowableError`) rather than
answering from it. So a store that already holds a later session cannot be read at this
session's evening at all, and one read after the later session has published answers with the
evening's bytes -- a session that missed its evening is settled from a later read -- with two
exceptions. A factor or a close the upstream restated in between is read as restated. And the
factor horizon is per security (`AdjustmentHistory.covered_through`): a name with a `daily` row
on the session but no factor row on it is left out of `adj_factor` on the evening read, while
a later read, once a later row of its series is stored, answers it with the value in force
before the gap (`bisect`).

**A precondition this reader cannot check: `suspend_d` completeness.** It fails open -- the
reads use `max_staleness=None`, and `suspension_requirement` waives `required_dates` -- so a
session whose `suspend_d` rows were never ingested reads as one on which nobody was halted, and
a halted name's bar is not marked `suspended`. The evening gate (Task C3 / Part D) must require,
from the ingest's own record, that every dataset this reader reads was ingested for the session.

## Session ordinals (`SessionOrdinals`)

`TradeOrder` counts sessions as integers. Ordinal 0 is the registered epoch session -- a closed,
open-day session -- and a session's ordinal is the number of open days of the calendar after the
epoch up to and including it. It depends on the epoch and on the calendar from the epoch on, and
on nothing before the epoch, so backfilling an earlier year into the stored calendar moves no
ordinal and a snapshot written before the backfill still names its sessions. A day that is not an
open session, one before the epoch, and one outside the calendar's range have no ordinal and are
refused by name; so are an ordinal below zero and one past the calendar's last session.
"""

from bisect import bisect_left
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Final

from openalpha_cn.backtest.execution import (
    MarketBar,
    stored_market_bar,
    suspended_at_the_open,
)
from openalpha_cn.domain.adjustment import AdjustmentHorizonError
from openalpha_cn.domain.index_membership import CSI500_INDEX_CODE
from openalpha_cn.domain.labels import halt_corpus_for_years
from openalpha_cn.domain.trading_calendar import CalendarDayStatus, TradingCalendar
from openalpha_cn.panel.store import PanelStore
from openalpha_cn.panel_ingest import (
    load_adjustment_histories,
    load_daily_bars,
    load_index_prices,
    load_price_limits,
    load_suspensions,
    session_publication_instant,
)

# The facts adapter's spelling of a security code, shared rather than restated a second time: a
# name the book holds or orders is a code the facts day spelled, and both refuse the same typos.
from openalpha_cn.strategy_survivor_facts import _SECURITY_CODE

__all__ = [
    "PROTOCOL_BENCHMARK",
    "MalformedNameError",
    "MissingBenchmarkError",
    "SessionMarket",
    "SessionOrdinalError",
    "SessionOrdinals",
    "SettlementMarketError",
    "UnbandedBarError",
    "settlement_market",
]

PROTOCOL_BENCHMARK: Final[str] = CSI500_INDEX_CODE
"""`000905.SH`, 中证500: the benchmark the research protocol pairs the book with (T5 section 0)."""


class SessionOrdinalError(ValueError):
    """A day or an ordinal that has no place in the book's count of sessions."""


class SettlementMarketError(ValueError):
    """A session market that cannot be read as asked."""


class MalformedNameError(SettlementMarketError):
    """A name asked for is not spelled as a security code, so it could match no daily row and
    an order for it would read as not attempted on every session."""


class MissingBenchmarkError(SettlementMarketError):
    """The benchmark has no close on the session; the previous close is not used."""


class UnbandedBarError(SettlementMarketError):
    """A name asked for has a `daily` row on the session and no published band; left out of
    `bars` it would read as a name with no `daily` row."""


@dataclass(frozen=True, slots=True)
class SessionOrdinals:
    """The book's integer count of sessions: `epoch` is 0, each open day after it one more.

    `calendar` must reach from `epoch` through every session asked about; see the module
    docstring for what is refused.
    """

    calendar: TradingCalendar
    epoch: date
    _first: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self._require_open(self.epoch, role="epoch")
        object.__setattr__(self, "_first", bisect_left(self.calendar.trading_days, self.epoch))

    def index_of(self, day: date) -> int:
        """`day`'s ordinal: the open sessions after the epoch through `day`."""
        self._require_open(day, role="day")
        if day < self.epoch:
            raise SessionOrdinalError(
                f"{day.isoformat()} is before the epoch session {self.epoch.isoformat()} "
                "(ordinal 0), so it has no ordinal"
            )
        return bisect_left(self.calendar.trading_days, day) - self._first

    def day_of(self, index: int) -> date:
        """The session with ordinal `index`."""
        if index < 0:
            raise SessionOrdinalError(
                f"ordinal {index} is before the epoch session {self.epoch.isoformat()} "
                "(ordinal 0), so no session has it"
            )
        sessions = self.calendar.trading_days
        position = self._first + index
        if position >= len(sessions):
            raise SessionOrdinalError(
                f"ordinal {index} is {index} sessions after {self.epoch.isoformat()}, after "
                f"{sessions[-1].isoformat()}, the last session the {self.calendar.exchange} "
                "calendar holds; an unpublished session is unknown, not absent"
            )
        return sessions[position]

    def _require_open(self, day: date, *, role: str) -> None:
        status = self.calendar.day_status(day)
        if status is CalendarDayStatus.beyond_horizon:
            horizon = self.calendar.horizon
            raise SessionOrdinalError(
                f"the {role} {day.isoformat()} is outside the {self.calendar.exchange} "
                f"calendar's range {horizon.first_date.isoformat()}.."
                f"{horizon.last_date.isoformat()}; read a calendar that reaches it"
            )
        if status is not CalendarDayStatus.trading:
            raise SessionOrdinalError(
                f"the {role} {day.isoformat()} is not an open {self.calendar.exchange} "
                "session, so it has no ordinal"
            )


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionMarket:
    """One session's settlement inputs for the names asked about; see the module docstring.

    `bars` and `adj_factor` are read-only and ascending by code. A name asked for is in `bars`
    unless the session has no `daily` row for it (a name with a row and no published band is
    refused, never left out), and in `adj_factor` when its factor series covers the session; the
    two are decided apart.
    """

    bars: Mapping[str, MarketBar]
    adj_factor: Mapping[str, Decimal]
    benchmark_close: Decimal


def settlement_market(
    store: PanelStore,
    *,
    session: date,
    as_of: datetime,
    names: Collection[str],
    calendar: TradingCalendar,
    benchmark: str = PROTOCOL_BENCHMARK,
) -> SessionMarket:
    """`session`'s bars, adjustment factors and benchmark close for `names`, read at `as_of`.

    Read-only. `calendar` is the exchange's, reaching the session; `as_of` at or after the
    session's 16:30 publication. Refuses by name a read before the publication, a session that
    is not an open one of `calendar`, `names` given as one string or holding a name not spelled
    as a security code (`MalformedNameError`, the facts adapter's spelling), a name asked for
    with a `daily` row and no published band (`UnbandedBarError`), and a benchmark with no close
    on the session (`MissingBenchmarkError`); the loaders' own refusals are raised unchanged.

    **Precondition, unchecked here:** `suspend_d` completeness is a precondition this reader
    cannot check, and it fails open (the reads use `max_staleness=None` and
    `suspension_requirement` waives `required_dates`): a session whose `suspend_d` rows were
    never ingested reads as nobody halted. The evening gate (Task C3 / Part D) must require every
    dataset this reader reads to have been ingested for the session, from the ingest's own
    record. And before 2023 a name with a `daily` row and no published band was a recurring
    shape (`001914.SZ`, the Beijing board; `domain/price_limits.py`), on which
    `UnbandedBarError` stops every night the name is held or ordered: do not reuse this reader
    for a 2017-2021 replay without handling that.
    """
    if isinstance(names, str):
        raise SettlementMarketError(
            f"names must be a collection of codes, not the one string {names!r}, whose "
            "characters would each be read as a code"
        )
    malformed = sorted(
        {
            repr(code)
            for code in names
            if not isinstance(code, str) or _SECURITY_CODE.fullmatch(code) is None
        }
    )
    if malformed:
        raise MalformedNameError(
            f"name(s) {', '.join(malformed)} are not spelled as a security code -- six digits, a "
            "dot and SH, SZ or BJ -- so each would match no daily row, and an order for one would "
            "read as not attempted on every session (a sale never expires)"
        )
    asked = tuple(sorted(set(names)))
    published = session_publication_instant(session)
    if as_of < published:
        raise SettlementMarketError(
            f"{session.isoformat()} publishes at {published.isoformat()} (16:30 Asia/Shanghai) "
            f"and the read is at {as_of.isoformat()}; its market did not exist yet"
        )
    if calendar.day_status(session) is not CalendarDayStatus.trading:
        horizon = calendar.horizon
        raise SettlementMarketError(
            f"{session.isoformat()} is not an open {calendar.exchange} session of the calendar "
            f"read ({horizon.first_date.isoformat()}..{horizon.last_date.isoformat()}), so it "
            "has no market to settle"
        )
    with store.reading():
        return _read_session_market(
            store,
            session=session,
            as_of=as_of,
            names=asked,
            calendar=calendar,
            benchmark=benchmark,
        )


def _read_session_market(
    store: PanelStore,
    *,
    session: date,
    as_of: datetime,
    names: tuple[str, ...],
    calendar: TradingCalendar,
    benchmark: str,
) -> SessionMarket:
    years = (session.year,)
    bars = load_daily_bars(store, day=session, calendar=calendar, as_of=as_of, max_staleness=None)
    limits = load_price_limits(
        store, day=session, calendar=calendar, as_of=as_of, max_staleness=None
    )
    halts = halt_corpus_for_years(
        load_suspensions(store, years=years, as_of=as_of, max_staleness=None), years=years
    )
    histories = load_adjustment_histories(store, years=years, as_of=as_of, max_staleness=None)
    levels = load_index_prices(store, calendar, years=years, as_of=as_of, max_staleness=None)

    unbanded = [code for code in names if code in bars and code not in limits]
    if unbanded:
        raise UnbandedBarError(
            f"{', '.join(unbanded)} {'has' if len(unbanded) == 1 else 'have'} a daily row on "
            f"{session.isoformat()} and no published stk_limit band in the panel read at "
            f"{as_of.isoformat()}. Without a band no bar can be judged, and a name left out of "
            "the bars would read as one with no daily row -- an order not attempted, a delisted "
            "holding written off -- so the session is not settled from it. Update the panel's "
            "price-limit bands for the session and settle it again"
        )
    priced: dict[str, MarketBar] = {}
    factors: dict[str, Decimal] = {}
    for code in names:
        bar, limit = bars.get(code), limits.get(code)
        if bar is not None and limit is not None:
            priced[code] = stored_market_bar(
                bar,
                limit,
                suspended=suspended_at_the_open(
                    halts.state_on(session, code), halts.timing_on(session, code)
                ),
                is_st=False,
            )
        history = histories.get(code)
        if history is None:
            continue
        try:
            factors[code] = Decimal(str(history.factor_on(session)))
        except AdjustmentHorizonError:
            continue
    close = next(
        (level.close for level in levels.get(benchmark, ()) if level.trade_date == session), None
    )
    if close is None:
        raise MissingBenchmarkError(
            f"the benchmark {benchmark} has no close on {session.isoformat()} in the index levels "
            f"read at {as_of.isoformat()}; the previous session's close is not this one's, so it "
            "is not used. Update the panel's index levels for the session and settle it again"
        )
    return SessionMarket(
        bars=MappingProxyType(priced),
        adj_factor=MappingProxyType(factors),
        benchmark_close=Decimal(str(close)),
    )
