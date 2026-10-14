"""The paper book's record layer of the survivor forward report (`V2-P7-018`, task E3).

The forward report's one evidence layer is the registered prediction record (design section 2(a)).
The paper book beside it is a **record**, never tested: what the ledger did, said once, so that a
reader can see how a ten-name book behaved, and how it compares -- over the same closes, and with
every difference named -- with the benchmarks of the registered window. `paper_record` builds that
record from three things the caller read and passes in:

- **`snapshots`**: the paper book's whole history, the opening snapshot (ordinal 0) first, one per
  session after it, each a `PaperBookSnapshot` the store read back and verified. Every figure of
  the book -- equity, cash, exposure, names held -- is read from **these**, never from a
  `Settlement.runner_record` (which is the runner's trace from before the close re-mark and before
  the write-offs, and overstates a write-off day by the whole of what was written off) and never
  from a number a journal prints;
- **`transitions`**: the paper ledger's rows (`SQLitePortfolioLedger.list()`), in the order
  appended. A row belongs to the session its `after.as_of` names; **the snapshot is the commit**,
  so a row of a session the history holds no snapshot for (a crash between the rows and the
  snapshot) is left out of every figure and named;
- **`documents`**: the daily journals, `RT/daily_survivor/<config_id[:16]>/<session>.json` read
  back (`openalpha-daily-survivor/v1`), one per day the daily command ran, in any order. They are
  the only place three things exist: an order **not attempted** for want of a bar, a buy that
  **expired** and one that was **cancelled** (the ledger stores filled and rejected transitions and
  nothing else), and the **decision** an evening made. A journal with no `result` is a day that
  was refused before it could file one: it is counted and named, and claims nothing.

The module is pure: it reads no store, no file and no clock, imports no numerical library, and
keeps every ratio an exact `Fraction` until it is printed once (half away from zero, six places;
money, two). The same inputs give the same bytes under any `PYTHONHASHSEED`: no set is iterated
unsorted, and every mapping this module builds is built in a fixed or a sorted order.

## What is refused, by name

A record built on inputs that do not describe one book is worse than none, so `PaperRecordError`
refuses: a history that is empty, does not begin with the opening snapshot, repeats an ordinal, has
a gap, or mixes two configurations; an order id held twice by the ledger; a ledger row that is
labelled a write-off and carries an execution, or is filled with no execution and is not a
write-off (the two shapes `paper_settlement` never writes); a document that is not a
`openalpha-daily-survivor/v1` journal, a session described by two journals, and a session two
journals settled in two different ways; windows that overlap or repeat a record id; and a
`holding_count` below one. **`JournalSnapshotMismatchError`**, a `PaperRecordError`, refuses a
journal that names a snapshot the store does not hold, or holds as another one: every snapshot id a
journal gives for a session -- the evening's, a filed or documented catch-up's, the paper stage's,
the opening's -- is compared with the snapshot the history holds for that session, and one
difference refuses the whole record, naming the session and both ids. The caller bounds its reads
to one consistent head, so a journal of a session past the history's end is a refusal too, not a
day to be left out.

## Units

Two kinds of number never meet. **Ledger units**: the paper book's money, in yuan -- cash plus each
holding's ledger share count at the ledger's mark, which is the real price times the adjustment
ratio since the buy filled (a cash dividend is reinvested and untaxed; a name's share count after a
bonus issue is not the account's). Equity, turnover, costs and the write-offs are in them, and the
D16 sensitivity -- "recovered at the last mark", the ledger quantity times the **previous**
snapshot's mark -- is expressly in them: a write-off row carries no price, and the real close of a
name that went ex-rights before it was delisted is a different number. **Real closes**: the
benchmarks the caller supplies, gross buy-and-hold over the same closes as the prediction record's
labels, and nothing else. Every block says which it is in.

## What it does not say

The block statistics are descriptive: no test, no p-value, no verdict, no comparison with any
expectation. Ledger equity is not evidence (`SURVIVOR_WORDING`, carried by every block): a book of
ten names over one year is mostly luck, and IR 0.5 would need years of it. The relative return is
the book's equity ratio over each window beside the benchmarks over the same closes, compounded
along a chain of contiguous windows -- the book is net of the protocol's costs and cash, the
benchmarks are gross, and the paper book fills at the D+1 open while the labels start at the D+1
close (a one-leg timing difference, said in the record's own label).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from fractions import Fraction
from itertools import pairwise
from typing import Any, Final, Literal

from openalpha_cn.backtest.paper_settlement import WRITE_OFF_REASON
from openalpha_cn.backtest.paper_state import PaperBookSnapshot
from openalpha_cn.domain.portfolio import PortfolioTransition
from openalpha_cn.strategy_survivor_summary import SURVIVOR_DOCUMENT_KIND, SURVIVOR_WORDING

__all__ = [
    "JOURNAL_SCHEMA",
    "SESSIONS_PER_YEAR",
    "WINDOW_LABEL",
    "BenchmarkWindow",
    "JournalSnapshotMismatchError",
    "PaperRecordError",
    "paper_record",
]

JOURNAL_SCHEMA: Final[str] = "openalpha-daily-survivor/v1"
"""The survivor day's journal schema (`scripts/daily_selection.py::SURVIVOR_JOURNAL_SCHEMA`); a test
holds the two spellings equal, because the script cannot be imported from here."""

SESSIONS_PER_YEAR: Final[int] = 244
"""The protocol's sessions in a year, the one every annualised figure of the record uses."""

WINDOW_LABEL: Final[str] = (
    "the book is net of the protocol's costs and cash; the benchmarks are gross buy-and-hold over "
    "the same closes; the paper book fills at the D+1 open while the labels start at the D+1 "
    "close (a one-leg timing difference)"
)
"""What the relative return compares, and what is different about the two sides."""

STATUS: Final[str] = (
    "descriptive, not a test: the record says what the paper ledger did and decides nothing"
)

_LEDGER_UNITS: Final[str] = (
    "ledger units: yuan of the paper ledger -- cash plus each holding's ledger share count at the "
    "ledger's mark (the real price times the adjustment ratio since the buy filled); cash "
    "dividends are reinvested and untaxed; fills are at the open"
)
_COUNT_UNITS: Final[str] = "counts of sessions, names, orders and journals; no money"
_REAL_UNITS: Final[str] = (
    "real closes: gross buy-and-hold over the label window's closes, no costs, no cash"
)
_SENSITIVITY: Final[str] = (
    "D16: a write-off books a total loss and carries no price. The sensitivity 'recovered at the "
    "last mark' is the ledger quantity times the previous session's ledger mark, in LEDGER units "
    "(the real price times the adjustment ratio since the buy filled): it is not a real close, "
    "and a name that went ex-rights before it was delisted has a different real close"
)
_ATTEMPTS_BASIS: Final[str] = (
    "filled and rejected: the paper ledger. not_attempted, expired and cancelled: the journals "
    "only -- the ledger stores filled and rejected transitions and nothing else -- so they are "
    "counted over the sessions a journal describes, and the sessions it does not are named"
)
_DECISIONS_BASIS: Final[str] = (
    "evenings only: a catch-up or a settle-only session settles without a decision, so every "
    "count here is over the evenings counted below, never over every settled session"
)
_NO_WINDOW: Final[str] = (
    "no window runs from {start} to {end}: the chain is broken, and the returns on either side "
    "are not compounded across it"
)

_ZERO: Final[Fraction] = Fraction(0)
_PLACES: Final[int] = 6
_BLOCK_STAGES: Final[tuple[str, ...]] = ("panel", "prediction", "catch_ups", "paper")
"""The journal stages whose presence a refused day's entry names, in the order they are filed."""


class PaperRecordError(ValueError):
    """The inputs do not describe one paper book, or a window cannot be read; named, never guessed.

    A `ValueError`: it is a refusal of an input, and the forward report maps it to its paper
    section's refusal without letting it cost the prediction layer.
    """


class JournalSnapshotMismatchError(PaperRecordError):
    """A journal names a snapshot the store does not hold, or holds as another one."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BenchmarkWindow:
    """One prediction record's label window and the benchmarks over it, as the caller read them.

    `entry` and `exit` are the sessions of the entry and the exit close (D+1 and D+1+h of the
    record), `investable_ew` is the record's investable equal weight -- the mean label over the
    labelled scope, gross buy-and-hold -- and `csi500` the CSI 500's close ratio less one over the
    same closes. Both are returns (0.03 is 3%), from the caller; a float is read by its shortest
    text, so 0.1 is one tenth. Windows chain only where one's `exit` is the next one's `entry`.
    """

    record_id: str
    entry: date
    exit: date
    investable_ew: Decimal | float
    csi500: Decimal | float

    def __post_init__(self) -> None:
        if not self.record_id:
            raise PaperRecordError("a benchmark window names its record: record_id is empty")
        if self.exit <= self.entry:
            raise PaperRecordError(
                f"window {self.record_id}: the exit {self.exit.isoformat()} must be after the "
                f"entry {self.entry.isoformat()}"
            )
        for name, value in (("investable_ew", self.investable_ew), ("csi500", self.csi500)):
            exact = _exact(self.record_id, name, value)
            if exact <= -1:
                raise PaperRecordError(
                    f"window {self.record_id}: {name} is a return of -100% or below ({value}); "
                    "no buy-and-hold loses more than everything, and the chain compounds 1 + r"
                )


def paper_record(
    snapshots: Sequence[PaperBookSnapshot],
    transitions: Sequence[PortfolioTransition],
    documents: Sequence[Mapping[str, Any]],
    *,
    windows: Sequence[BenchmarkWindow],
    holding_count: int,
) -> dict[str, Any]:
    """The record layer of the forward report: `{"paper_ledger": ..., "relative": ...}`.

    `holding_count` is the book's N (`SurvivorBookConfig.holding_count`), which "days below N"
    counts against: it is the registration's, never defaulted here. `windows` are the benchmark
    windows of the registered sample, in any order (see `BenchmarkWindow`).

    Everything is JSON: money and ratios are strings, dates ISO strings, and each block of
    `paper_ledger` carries `SURVIVOR_WORDING` and the units its numbers are in. See the module
    docstring for what is refused.
    """
    if holding_count < 1:
        raise PaperRecordError(
            f"holding_count must be at least 1, not {holding_count}: it is the book's N"
        )
    book = _Book.of(snapshots)
    rows = _Rows.of(transitions, book)
    days = _Days.of(documents, book)
    curve = _Curve.of(book)
    return {
        "paper_ledger": {
            "wording": SURVIVOR_WORDING,
            "status": STATUS,
            "coverage": _coverage(book, days),
            "equity": _equity(book, curve),
            "holdings": _holdings(curve, holding_count),
            "turnover": _turnover(rows, curve),
            "costs": _costs(book, rows, curve),
            "write_offs": _write_offs(book, rows, curve),
            "attempts": _attempts(book, rows, days),
            "decisions": _decisions(days),
            "illiquid_exits": _illiquid_exits(days),
            "reconciliation": _reconciliation(rows, days),
        },
        "relative": _relative(book, windows),
    }


# --- the history --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Book:
    """The history, checked: the opening snapshot and the settled ones after it."""

    history: tuple[PaperBookSnapshot, ...]
    by_index: Mapping[int, PaperBookSnapshot]
    by_date: Mapping[date, PaperBookSnapshot]

    @property
    def opening(self) -> PaperBookSnapshot:
        return self.history[0]

    @property
    def settled(self) -> tuple[PaperBookSnapshot, ...]:
        return self.history[1:]

    @property
    def last(self) -> PaperBookSnapshot:
        return self.history[-1]

    @classmethod
    def of(cls, snapshots: Sequence[PaperBookSnapshot]) -> _Book:
        if not snapshots:
            raise PaperRecordError(
                "no snapshot was given: the record needs the paper book's whole history, its "
                "opening snapshot first"
            )
        ordered = tuple(sorted(snapshots, key=lambda item: item.session_index))
        counted = Counter(item.session_index for item in ordered)
        repeated = sorted(index for index, count in counted.items() if count > 1)
        if repeated:
            raise PaperRecordError(
                f"session indices {repeated} are given twice: the history holds one snapshot per "
                "session"
            )
        first = ordered[0]
        if first.session_index != 0 or first.last_settled_session is not None:
            raise PaperRecordError(
                "the history must begin with the book's opening snapshot (session index 0, "
                f"nothing settled); it begins with session index {first.session_index} "
                f"({first.session.isoformat()})"
            )
        for earlier, later in pairwise(ordered):
            if later.session_index != earlier.session_index + 1:
                raise PaperRecordError(
                    f"the history is not contiguous: session index {later.session_index} "
                    f"({later.session.isoformat()}) follows {earlier.session_index} "
                    f"({earlier.session.isoformat()}); a missing session is settled, never jumped "
                    "over"
                )
            if later.session <= earlier.session:
                raise PaperRecordError(
                    f"session index {later.session_index} is dated {later.session.isoformat()}, "
                    f"not after session index {earlier.session_index} "
                    f"({earlier.session.isoformat()})"
                )
            if later.last_settled_session != later.session_index:
                raise PaperRecordError(
                    f"session index {later.session_index} ({later.session.isoformat()}) is not a "
                    "settled snapshot: only the first one of a history is an opening"
                )
        digests = sorted({item.config_digest for item in ordered})
        if len(digests) > 1:
            raise PaperRecordError(
                f"the snapshots are of {len(digests)} configurations ({', '.join(digests)}): one "
                "paper book has one configuration"
            )
        return cls(
            history=ordered,
            by_index={item.session_index: item for item in ordered},
            by_date={item.session: item for item in ordered},
        )


@dataclass(frozen=True, slots=True)
class _Point:
    """One settled session as the snapshot holds it, as exact numbers."""

    snapshot: PaperBookSnapshot
    equity: Fraction
    cash: Fraction
    market_value: Fraction
    held: int

    @property
    def cash_share(self) -> Fraction | None:
        return None if self.equity <= 0 else self.cash / self.equity

    @property
    def gross_exposure(self) -> Fraction | None:
        return None if self.equity <= 0 else self.market_value / self.equity


@dataclass(frozen=True, slots=True)
class _Curve:
    """The settled sessions' points, in order, and the means the annualised figures use."""

    points: tuple[_Point, ...]

    @classmethod
    def of(cls, book: _Book) -> _Curve:
        return cls(
            tuple(
                _Point(
                    snapshot=item,
                    equity=Fraction(item.book.equity),
                    cash=Fraction(item.book.cash),
                    market_value=Fraction(item.book.market_value),
                    held=len(item.book.positions),
                )
                for item in book.settled
            )
        )

    @property
    def sessions(self) -> int:
        return len(self.points)

    @property
    def mean_equity(self) -> Fraction | None:
        if not self.points:
            return None
        return sum((point.equity for point in self.points), _ZERO) / len(self.points)

    def per_year(self, amount: Fraction) -> Fraction | None:
        """`amount`, a total over the record's sessions, as a share of mean equity per year."""
        mean = self.mean_equity
        if mean is None or mean <= 0:
            return None
        return amount / mean * SESSIONS_PER_YEAR / len(self.points)


# --- the ledger ---------------------------------------------------------------------------------

_Kind = Literal["fill", "rejection", "write_off"]


@dataclass(frozen=True, slots=True)
class _Row:
    transition: PortfolioTransition
    kind: _Kind

    @property
    def order_id(self) -> str:
        return self.transition.order.order_id


@dataclass(frozen=True, slots=True)
class _Rows:
    """The ledger's rows of the sessions the history settled, by session index, and the ids of the
    rows of sessions it did not."""

    by_session: Mapping[int, tuple[_Row, ...]]
    unsettled: tuple[str, ...]

    @property
    def settled(self) -> tuple[_Row, ...]:
        return tuple(row for index in sorted(self.by_session) for row in self.by_session[index])

    def kind(self, kind: _Kind) -> tuple[_Row, ...]:
        return tuple(row for row in self.settled if row.kind == kind)

    @classmethod
    def of(cls, transitions: Sequence[PortfolioTransition], book: _Book) -> _Rows:
        counted = Counter(item.order.order_id for item in transitions)
        repeated = sorted(order_id for order_id, count in counted.items() if count > 1)
        if repeated:
            raise PaperRecordError(
                f"the ledger holds order ids more than once: {repeated}; a ledger refuses an id "
                "reused with another payload, so this is not the paper ledger"
            )
        index_of = {item.session: item.session_index for item in book.settled}
        grouped: dict[int, list[_Row]] = defaultdict(list)
        unsettled: list[str] = []
        for transition in transitions:
            index = index_of.get(transition.after.as_of)
            if index is None:
                unsettled.append(transition.order.order_id)
            else:
                grouped[index].append(_Row(transition, _kind_of(transition)))
        return cls(
            by_session={index: tuple(rows) for index, rows in sorted(grouped.items())},
            unsettled=tuple(sorted(unsettled)),
        )


def _kind_of(transition: PortfolioTransition) -> _Kind:
    """What kind of row `transition` is, refusing the two shapes `paper_settlement` never writes."""
    order_id = transition.order.order_id
    if transition.reason == WRITE_OFF_REASON:
        if transition.status != "filled" or transition.execution is not None:
            raise PaperRecordError(
                f"ledger row {order_id} is labelled a write-off and carries an execution (or is "
                "not filled): a write-off is a price-less filled row, so this is not one the "
                "paper book wrote"
            )
        return "write_off"
    if transition.status == "filled":
        if transition.execution is None:
            raise PaperRecordError(
                f"ledger row {order_id} is filled with no execution and is not a write-off "
                f"(reason {transition.reason!r}): a filled row without a price is read as a "
                "trade by no reader, so this one is refused"
            )
        return "fill"
    return "rejection"


# --- blocks -------------------------------------------------------------------------------------


def _block(units: str, **fields: Any) -> dict[str, Any]:
    """A block of the record: the wording first, then the units its numbers are in."""
    return {"wording": SURVIVOR_WORDING, "units": units, **fields}


def _fixed(value: Fraction, places: int) -> str:
    """`value` to `places` decimals, half away from zero, as a plain string -- exact, because it
    is made of integers and text: no float and no decimal context takes part."""
    scaled = abs(value) * 10**places
    whole = scaled.numerator // scaled.denominator
    if scaled - whole >= Fraction(1, 2):
        whole += 1
    integer, fraction = divmod(whole, 10**places)
    sign = "-" if value < 0 and whole else ""
    return f"{sign}{integer}.{fraction:0{places}d}"


def _money(value: Fraction) -> str:
    return _fixed(value, 2)


def _ratio(value: Fraction | None) -> str | None:
    return None if value is None else _fixed(value, _PLACES)


def _mean(values: Iterable[Fraction]) -> Fraction | None:
    items = list(values)
    return sum(items, _ZERO) / len(items) if items else None


def _day(value: date) -> str:
    return value.isoformat()


def _exact(owner: str, name: str, value: Decimal | float) -> Fraction:
    """`value` as an exact number: a float by its shortest text, a non-finite one refused."""
    number = Decimal(repr(value)) if isinstance(value, float) else value
    if not number.is_finite():
        raise PaperRecordError(f"window {owner}: {name} must be finite, not {value}")
    return Fraction(number)


# --- coverage and equity ------------------------------------------------------------------------


def _coverage(book: _Book, days: _Days) -> dict[str, Any]:
    documented = {claim.index for claim in days.claims if claim.settled is not None}
    return _block(
        _COUNT_UNITS,
        opening_session=_day(book.opening.session),
        opening_snapshot_id=book.opening.snapshot_id,
        settled_sessions=len(book.settled),
        first_settled=_day(book.settled[0].session) if book.settled else None,
        last_settled=_day(book.last.session) if book.settled else None,
        documents=days.documents,
        opening_days=days.opening_days,
        evenings=len(days.evenings),
        settle_only_days=len(days.settle_only),
        settle_only=[_day(item) for item in days.settle_only],
        caught_up_sessions=len(days.caught_up),
        caught_up=[_day(item) for item in days.caught_up],
        refused_days=[
            {"session": _day(session), "stages_filed": list(stages)}
            for session, stages in days.refused
        ],
        settled_sessions_without_document=[
            _day(item.session) for item in book.settled if item.session_index not in documented
        ],
        basis=(
            "evenings are the days that made a decision; settle-only days and caught-up sessions "
            "settled without one; a refused day is a journal with no result, and claims nothing"
        ),
    )


def _equity(book: _Book, curve: _Curve) -> dict[str, Any]:
    return _block(
        _LEDGER_UNITS,
        opening={
            "session": _day(book.opening.session),
            "equity": _money(Fraction(book.opening.book.equity)),
            "snapshot_id": book.opening.snapshot_id,
        },
        sessions=curve.sessions,
        first_session=_day(book.settled[0].session) if book.settled else None,
        last_session=_day(book.last.session) if book.settled else None,
        final_equity=_money(curve.points[-1].equity) if curve.points else None,
        mean_equity=_money(curve.mean_equity) if curve.mean_equity is not None else None,
        minimum_equity=(
            _money(min(point.equity for point in curve.points)) if curve.points else None
        ),
        curve=[
            {
                "session": _day(point.snapshot.session),
                "session_index": point.snapshot.session_index,
                "equity": _money(point.equity),
                "cash": _money(point.cash),
                "cash_share": _ratio(point.cash_share),
                "gross_exposure": _ratio(point.gross_exposure),
                "held": point.held,
            }
            for point in curve.points
        ],
    )


def _holdings(curve: _Curve, holding_count: int) -> dict[str, Any]:
    held = [point.held for point in curve.points]
    full = next((position for position, count in enumerate(held) if count >= holding_count), None)
    return _block(
        _COUNT_UNITS,
        holding_count=holding_count,
        mean_held=_ratio(_mean(Fraction(count) for count in held)),
        minimum_held=min(held) if held else None,
        maximum_held=max(held) if held else None,
        sessions_below_n=sum(1 for count in held if count < holding_count),
        first_session_at_n=(None if full is None else _day(curve.points[full].snapshot.session)),
        sessions_below_n_after_first_full=(
            None if full is None else sum(1 for count in held[full + 1 :] if count < holding_count)
        ),
        mean_cash_share=_ratio(
            _mean(share for point in curve.points if (share := point.cash_share) is not None)
        ),
        mean_gross_exposure=_ratio(
            _mean(share for point in curve.points if (share := point.gross_exposure) is not None)
        ),
    )


# --- turnover and costs -------------------------------------------------------------------------


def _turnover(rows: _Rows, curve: _Curve) -> dict[str, Any]:
    bought = _ZERO
    sold = _ZERO
    for row in rows.kind("fill"):
        execution = row.transition.execution
        assert execution is not None  # `_kind_of` made a fill of nothing else
        if row.transition.order.side == "buy":
            bought += Fraction(execution.notional)
        else:
            sold += Fraction(execution.notional)
    mean = curve.mean_equity
    cumulative = None if mean is None or mean <= 0 else (bought + sold) / (2 * mean)
    return _block(
        _LEDGER_UNITS,
        basis=(
            "one-sided: the filled notional of every buy and sell over twice the mean equity of "
            "the settled sessions; a write-off has no execution and is not turnover; annualised "
            f"at {SESSIONS_PER_YEAR} sessions a year"
        ),
        filled_notional={
            "buy": _money(bought),
            "sell": _money(sold),
            "total": _money(bought + sold),
        },
        mean_equity=None if mean is None else _money(mean),
        sessions=curve.sessions,
        sessions_per_year=SESSIONS_PER_YEAR,
        one_sided_turnover=_ratio(cumulative),
        annualised=_ratio(
            None if cumulative is None else cumulative * SESSIONS_PER_YEAR / curve.sessions
        ),
    )


def _costs(book: _Book, rows: _Rows, curve: _Curve) -> dict[str, Any]:
    components = {"commission": _ZERO, "slippage": _ZERO, "stamp_duty": _ZERO}
    total = _ZERO
    inconsistent: list[str] = []
    for row in rows.kind("fill"):
        execution = row.transition.execution
        assert execution is not None
        components["commission"] += Fraction(execution.commission)
        components["slippage"] += Fraction(execution.transfer_fee)
        components["stamp_duty"] += Fraction(execution.stamp_duty)
        total += Fraction(execution.total_cost)
        if execution.commission + execution.transfer_fee + execution.stamp_duty != (
            execution.total_cost
        ):
            inconsistent.append(row.order_id)

    def share(amount: Fraction) -> dict[str, Any]:
        return {
            "total": _money(amount),
            "annualised_share_of_mean_equity": _ratio(curve.per_year(amount)),
        }

    mismatched = []
    for point in curve.points:
        index = point.snapshot.session_index
        ledger_cost = sum(
            (
                Fraction(row.transition.execution.total_cost)
                for row in rows.by_session.get(index, ())
                if row.kind == "fill" and row.transition.execution is not None
            ),
            _ZERO,
        )
        paid = Fraction(point.snapshot.book.fees_paid) - Fraction(
            book.by_index[index - 1].book.fees_paid
        )
        if ledger_cost != paid:
            mismatched.append(
                {
                    "session": _day(point.snapshot.session),
                    "ledger_total_cost": _money(ledger_cost),
                    "fees_paid_change": _money(paid),
                }
            )
    paid_total = Fraction(book.last.book.fees_paid) - Fraction(book.opening.book.fees_paid)
    return _block(
        _LEDGER_UNITS,
        basis=(
            "by component over the filled rows of the settled sessions; slippage is the ledger's "
            "transfer_fee field, in which the protocol's book charges its slippage; each total's "
            "annualised share is over the mean equity of the settled sessions at "
            f"{SESSIONS_PER_YEAR} sessions a year"
        ),
        commission=share(components["commission"]),
        slippage={
            **share(components["slippage"]),
            "ledger_field": "transfer_fee (the protocol's slippage rides in this slot)",
        },
        stamp_duty=share(components["stamp_duty"]),
        total=share(total),
        cross_check={
            "ledger_total_cost": _money(total),
            "fees_paid_change": _money(paid_total),
            "difference": _money(total - paid_total),
            "matches": total == paid_total and not mismatched and not inconsistent,
            "sessions_mismatched": mismatched,
            "rows_whose_components_do_not_sum": sorted(inconsistent),
            "basis": (
                "the filled rows' total cost against the change in the snapshots' fees_paid, "
                "per session and in all; a mismatch is reported, never hidden"
            ),
        },
    )


# --- write-offs ---------------------------------------------------------------------------------


def _write_offs(book: _Book, rows: _Rows, curve: _Curve) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    basis = _ZERO
    removed_total = _ZERO
    recovered_total: Fraction | None = _ZERO
    for index in sorted(rows.by_session):
        session = book.by_index[index]
        previous = book.by_index[index - 1]
        for row in rows.by_session[index]:
            if row.kind != "write_off":
                continue
            transition = row.transition
            code = transition.order.subject
            quantity = transition.order.quantity
            cost_basis = -Fraction(transition.realized_pnl_delta)
            before = Fraction(transition.before.equity)
            removed = before - Fraction(transition.after.equity)
            mark = previous.book.mark(code)
            recovered = None if mark is None else quantity * Fraction(mark)
            basis += cost_basis
            removed_total += removed
            recovered_total = (
                None
                if recovered is None or recovered_total is None
                else recovered_total + recovered
            )
            items.append(
                {
                    "session": _day(session.session),
                    "session_index": index,
                    "ts_code": code,
                    "ledger_quantity": quantity,
                    "cost_basis": _money(cost_basis),
                    "equity_before": _money(before),
                    "equity_removed": _money(removed),
                    "share_of_equity": _ratio(None if before <= 0 else removed / before),
                    "recovered_at_last_mark": {
                        "value": None if recovered is None else _money(recovered),
                        "ledger_mark": None if mark is None else format(mark, "f"),
                        "mark_session": _day(previous.session),
                        "units": "ledger",
                    },
                }
            )
    mean = curve.mean_equity
    return _block(
        _LEDGER_UNITS,
        basis=(
            "a transition with reason delisting_write_off and no execution; the cost basis is the "
            "realised loss with its sign turned over; the equity removed is what the holding was "
            "marked at, and its share is of the book's equity just before it"
        ),
        sensitivity=_SENSITIVITY,
        count=len(items),
        total_cost_basis=_money(basis),
        total_equity_removed=_money(removed_total),
        total_recovered_at_last_mark=None if recovered_total is None else _money(recovered_total),
        total_recovered_share_of_mean_equity=_ratio(
            None if recovered_total is None or mean is None or mean <= 0 else recovered_total / mean
        ),
        items=items,
    )


# --- attempts, decisions, illiquid exits and their reconciliation -------------------------------


def _attempts(book: _Book, rows: _Rows, days: _Days) -> dict[str, Any]:
    filled = Counter(row.transition.order.side for row in rows.kind("fill"))
    rejected = Counter(
        row.transition.reason or "(no reason given)" for row in rows.kind("rejection")
    )
    not_attempted: Counter[str] = Counter()
    expired = cancelled = 0
    documented = 0
    for claim in days.claims:
        if claim.settled is None:
            continue
        documented += 1
        for attempt in claim.settled.attempts:
            if attempt.status == "not_attempted":
                not_attempted[attempt.reason or "(no reason given)"] += 1
        expired += len(claim.settled.expired)
        cancelled += len(claim.settled.cancelled)
    return _block(
        _COUNT_UNITS,
        basis=_ATTEMPTS_BASIS,
        ledger={
            "filled": {
                "buy": filled["buy"],
                "sell": filled["sell"],
                "total": filled["buy"] + filled["sell"],
            },
            "rejected": {
                "total": sum(rejected.values()),
                "by_reason": dict(sorted(rejected.items())),
            },
            "write_offs": len(rows.kind("write_off")),
        },
        journals={
            "sessions_documented": documented,
            "sessions_undocumented": len(book.settled) - documented,
            "not_attempted": {
                "total": sum(not_attempted.values()),
                "by_reason": dict(sorted(not_attempted.items())),
            },
            "expired": expired,
            "cancelled": cancelled,
        },
    )


def _tally(lists: Sequence[Sequence[str]]) -> dict[str, int]:
    sizes = [len(names) for names in lists]
    return {
        "name_sessions": sum(sizes),
        "evenings_with_any": sum(1 for size in sizes if size),
        "most_in_one_evening": max(sizes, default=0),
    }


def _decisions(days: _Days) -> dict[str, Any]:
    evenings = days.evenings
    exits: Counter[str] = Counter()
    for evening in evenings:
        exits.update(evening.exit_reasons)
    return _block(
        _COUNT_UNITS,
        basis=_DECISIONS_BASIS,
        evenings=len(evenings),
        exits={"total": sum(exits.values()), "by_reason": dict(sorted(exits.items()))},
        unfunded=_tally([evening.unfunded for evening in evenings]),
        unaffordable=_tally([evening.unaffordable for evening in evenings]),
        frozen=_tally([evening.frozen for evening in evenings]),
        near_exit=_tally([evening.near_exit for evening in evenings]),
        deferred_exits=_tally([evening.deferred_exits for evening in evenings]),
    )


def _illiquid_exits(days: _Days) -> dict[str, Any]:
    listed = [
        {"session": _day(evening.session), **exit_}
        for evening in days.evenings
        for exit_ in evening.illiquid_exits
    ]
    return _block(
        _COUNT_UNITS,
        rank_basis=(
            "rank 1 = thinnest by traded amount in the population the liquidity cut was taken "
            "over; the cut drops that many of the thinnest"
        ),
        count=len(listed),
        evenings_with_any=sum(1 for evening in days.evenings if evening.illiquid_exits),
        exits=listed,
    )


def _reconciliation(rows: _Rows, days: _Days) -> dict[str, Any]:
    mismatches = []
    compared = 0
    for claim in days.claims:
        if claim.settled is None:
            continue
        compared += 1
        held = {row.order_id for row in rows.by_session.get(claim.index, ())}
        journalled = claim.settled.ledger_ids
        if held != journalled:
            mismatches.append(
                {
                    "session": _day(claim.session),
                    "only_in_journal": sorted(journalled - held),
                    "only_in_ledger": sorted(held - journalled),
                }
            )
    return _block(
        _COUNT_UNITS,
        basis=(
            "per documented session, the attempts a journal says were filled or rejected (and "
            "its write-offs) against the ledger's rows of the session, by order id"
        ),
        reconciled=not mismatches,
        sessions_reconciled=compared,
        mismatches=mismatches,
        rows_of_unsettled_sessions=list(rows.unsettled),
    )


# --- the journals -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Attempt:
    attempt_id: str
    status: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class _Settled:
    """What a journal says one session settled: the `settled` block of a document or an entry."""

    attempts: tuple[_Attempt, ...]
    expired: tuple[str, ...]
    cancelled: tuple[str, ...]
    written_off: tuple[str, ...]

    @property
    def ledger_ids(self) -> frozenset[str]:
        """The order ids the ledger holds a row for if it agrees: every attempt with a verdict, and
        every write-off."""
        return frozenset(
            [item.attempt_id for item in self.attempts if item.status != "not_attempted"]
            + list(self.written_off)
        )


@dataclass(frozen=True, slots=True)
class _Claim:
    """A journal's statement that a session settled as a snapshot, with what it settled."""

    index: int
    session: date
    snapshot_id: str
    kind: str
    source: str
    settled: _Settled | None


@dataclass(frozen=True, slots=True)
class _Evening:
    """What an evening's document says it decided."""

    session: date
    exit_reasons: tuple[str, ...]
    unfunded: tuple[str, ...]
    unaffordable: tuple[str, ...]
    frozen: tuple[str, ...]
    near_exit: tuple[str, ...]
    deferred_exits: tuple[str, ...]
    illiquid_exits: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _Days:
    """The journals, read and checked against the history."""

    documents: int
    opening_days: int
    claims: tuple[_Claim, ...]
    """One per settled session a journal describes, in session order."""
    evenings: tuple[_Evening, ...]
    settle_only: tuple[date, ...]
    caught_up: tuple[date, ...]
    refused: tuple[tuple[date, tuple[str, ...]], ...]

    @classmethod
    def of(cls, documents: Sequence[Mapping[str, Any]], book: _Book) -> _Days:
        journals: dict[date, Mapping[str, Any]] = {}
        for item in documents:
            if not isinstance(item, Mapping) or item.get("schema") != JOURNAL_SCHEMA:
                schema = item.get("schema") if isinstance(item, Mapping) else type(item).__name__
                raise PaperRecordError(
                    f"a document is not a {JOURNAL_SCHEMA} journal: its schema is {schema!r}; the "
                    "P6 day's journals and any other file are never read as the survivor day's"
                )
            session = _date(item.get("session"), "a journal's session")
            if session in journals:
                raise PaperRecordError(
                    f"{_day(session)} is described by two journals: a day has one journal"
                )
            journals[session] = item

        claims: list[_Claim] = []
        evenings: list[_Evening] = []
        settle_only: list[date] = []
        refused: list[tuple[date, tuple[str, ...]]] = []
        opening_days = 0
        for session in sorted(journals):
            body = journals[session]
            where = f"the journal of {_day(session)}"
            result = body.get("result")
            stage = body.get("paper")
            if stage is not None:
                _check_stage(stage, session=session, book=book, where=where)
            filed = _entries(body.get("catch_ups", ()), session=session, where=f"{where} catch_ups")
            if result is None:
                refused.append((session, tuple(name for name in _BLOCK_STAGES if name in body)))
                claims.extend(filed)
                continue
            if not isinstance(result, Mapping) or result.get("kind") != SURVIVOR_DOCUMENT_KIND:
                raise PaperRecordError(
                    f"{where} holds a result that is not a {SURVIVOR_DOCUMENT_KIND} document"
                )
            claims.extend(filed)
            if "opening" in result:
                opening_days += 1
                claims.append(_opening_claim(result, where=where))
                continue
            paper = result.get("paper")
            if isinstance(paper, Mapping):
                claim, evening = _evening(result, paper, session=session, where=where)
                claims.append(claim)
                evenings.append(evening)
                claims.extend(
                    _entries(paper.get("catch_ups", ()), session=session, where=f"{where} paper")
                )
            elif paper is None and isinstance(result.get("paper_skipped"), Mapping):
                settle_only.append(session)
                claims.extend(
                    _entries(
                        result["paper_skipped"].get("catch_ups", ()),
                        session=session,
                        where=f"{where} paper_skipped",
                    )
                )
            else:
                raise PaperRecordError(
                    f"{where} holds a result that is neither an opening, an evening nor a "
                    "settle-only day"
                )

        for claim in claims:
            _check_claim(claim, book)
        kept: dict[int, _Claim] = {}
        for claim in claims:
            held = kept.get(claim.index)
            if held is None:
                kept[claim.index] = claim
            elif held.kind != claim.kind:
                raise PaperRecordError(
                    f"session {_day(claim.session)} is settled twice: as {held.kind} by "
                    f"{held.source} and as {claim.kind} by {claim.source}"
                )
            elif held.settled is None and claim.settled is not None:
                kept[claim.index] = claim
        ordered = tuple(kept[index] for index in sorted(kept))
        return cls(
            documents=len(journals),
            opening_days=opening_days,
            claims=ordered,
            evenings=tuple(evenings),
            settle_only=tuple(settle_only),
            caught_up=tuple(claim.session for claim in ordered if claim.kind == "caught_up"),
            refused=tuple(refused),
        )


def _check_claim(claim: _Claim, book: _Book) -> None:
    snapshot = book.by_index.get(claim.index)
    named = f"{claim.source} says session {_day(claim.session)} (index {claim.index}) settled as"
    if snapshot is None:
        raise JournalSnapshotMismatchError(
            f"{named} {claim.snapshot_id}, and the store holds no snapshot for it (its history "
            f"ends {_day(book.last.session)}, index {book.last.session_index}): the journals and "
            "the store were not read at one head"
        )
    if snapshot.session != claim.session:
        raise JournalSnapshotMismatchError(
            f"{named} {claim.snapshot_id}, and the store's snapshot of that index is dated "
            f"{_day(snapshot.session)}: the two number the sessions differently"
        )
    if snapshot.snapshot_id != claim.snapshot_id:
        raise JournalSnapshotMismatchError(
            f"{named} {claim.snapshot_id}, and the store's snapshot for it is "
            f"{snapshot.snapshot_id}: a journal and the store disagree about which book the "
            "session left, and neither is preferred"
        )


def _check_stage(stage: Any, *, session: date, book: _Book, where: str) -> None:
    """The paper stage's snapshot id, if it names one, against the store's for the journal's day."""
    if not isinstance(stage, Mapping):
        raise PaperRecordError(f"{where} has a paper stage that is not a mapping")
    snapshot_id = stage.get("snapshot_id")
    if snapshot_id is None:
        return
    snapshot = book.by_date.get(session)
    if snapshot is None:
        raise JournalSnapshotMismatchError(
            f"{where} paper stage says {_day(session)} settled as {snapshot_id}, and the store "
            f"holds no snapshot for it (its history ends {_day(book.last.session)}): the "
            "journals and the store were not read at one head"
        )
    if snapshot.snapshot_id != snapshot_id:
        raise JournalSnapshotMismatchError(
            f"{where} paper stage says {_day(session)} settled as {snapshot_id}, and the store's "
            f"snapshot for it is {snapshot.snapshot_id}: a journal and the store disagree about "
            "which book the session left, and neither is preferred"
        )


def _opening_claim(result: Mapping[str, Any], *, where: str) -> _Claim:
    opening = _mapping(result.get("opening"), f"{where} opening")
    return _Claim(
        index=_int(result.get("session_index"), f"{where} session_index"),
        session=_date(result.get("session"), f"{where} session"),
        snapshot_id=_text(opening.get("snapshot_id"), f"{where} opening snapshot_id"),
        kind="opening",
        source=f"{where} opening",
        settled=None,
    )


def _evening(
    result: Mapping[str, Any], paper: Mapping[str, Any], *, session: date, where: str
) -> tuple[_Claim, _Evening]:
    index = _int(result.get("session_index"), f"{where} session_index")
    day = _date(result.get("session"), f"{where} session")
    if day != session:
        raise PaperRecordError(f"{where} holds the result of {_day(day)}")
    decision = _mapping(paper.get("decision"), f"{where} decision")
    plan = _mapping(paper.get("trade_list"), f"{where} trade_list")
    exits = decision.get("exits")
    if not isinstance(exits, list):
        raise PaperRecordError(f"{where} decision has no list of exits")
    reasons = tuple(
        _text(_mapping(item, f"{where} an exit").get("reason"), f"{where} an exit's reason")
        for item in exits
    )
    illiquid = paper.get("illiquid_exits")
    if not isinstance(illiquid, list):
        raise PaperRecordError(f"{where} has no list of illiquid exits")
    claim = _Claim(
        index=index,
        session=day,
        snapshot_id=_text(paper.get("snapshot_id"), f"{where} snapshot_id"),
        kind="evening",
        source=f"{where} result",
        settled=_settled(paper.get("settled"), where=f"{where} settled"),
    )
    evening = _Evening(
        session=day,
        exit_reasons=reasons,
        unfunded=_names(plan.get("unfunded"), f"{where} trade_list unfunded"),
        unaffordable=_names(decision.get("unaffordable"), f"{where} decision unaffordable"),
        frozen=_names(decision.get("frozen"), f"{where} decision frozen"),
        near_exit=_names(decision.get("near_exit"), f"{where} decision near_exit"),
        deferred_exits=_names(decision.get("deferred_exits"), f"{where} decision deferred_exits"),
        illiquid_exits=tuple(
            _illiquid(_mapping(item, f"{where} an illiquid exit"), where) for item in illiquid
        ),
    )
    return claim, evening


def _illiquid(item: Mapping[str, Any], where: str) -> dict[str, Any]:
    return {
        "ts_code": _text(item.get("ts_code"), f"{where} an illiquid exit's ts_code"),
        "rank": _int(item.get("rank"), f"{where} an illiquid exit's rank"),
        "population": _int(item.get("population"), f"{where} an illiquid exit's population"),
        "cut": _int(item.get("cut"), f"{where} an illiquid exit's cut"),
    }


def _entries(entries: Any, *, session: date, where: str) -> list[_Claim]:
    """The catch-ups a journal lists, as claims: a session settled on the journal's own day is that
    day settling only; any other is a missed session a later run caught up."""
    if not isinstance(entries, list | tuple):
        raise PaperRecordError(f"{where} is not a list of catch-up entries")
    claims = []
    for entry in entries:
        body = _mapping(entry, f"{where} entry")
        day = _date(body.get("session"), f"{where} entry session")
        claims.append(
            _Claim(
                index=_int(body.get("session_index"), f"{where} entry session_index"),
                session=day,
                snapshot_id=_text(body.get("snapshot_id"), f"{where} entry snapshot_id"),
                kind="settle_only" if day == session else "caught_up",
                source=where,
                settled=_settled(body.get("settled"), where=f"{where} entry settled"),
            )
        )
    return claims


def _settled(value: Any, *, where: str) -> _Settled:
    body = _mapping(value, where)
    attempts = body.get("attempts")
    if not isinstance(attempts, list):
        raise PaperRecordError(f"{where} has no list of attempts")
    written = body.get("written_off")
    if not isinstance(written, list):
        raise PaperRecordError(f"{where} has no list of write-offs")
    parsed = []
    for item in attempts:
        entry = _mapping(item, f"{where} an attempt")
        status = _text(entry.get("status"), f"{where} an attempt's status")
        if status not in ("filled", "rejected", "not_attempted"):
            raise PaperRecordError(f"{where} has an attempt with status {status!r}")
        reason = entry.get("reason")
        parsed.append(
            _Attempt(
                attempt_id=_text(entry.get("attempt_id"), f"{where} an attempt's attempt_id"),
                status=status,
                reason=reason if isinstance(reason, str) else None,
            )
        )
    return _Settled(
        attempts=tuple(parsed),
        expired=_names(body.get("expired"), f"{where} expired"),
        cancelled=_names(body.get("cancelled"), f"{where} cancelled"),
        written_off=tuple(
            _text(_mapping(item, f"{where} a write-off").get("order_id"), f"{where} a write-off")
            for item in written
        ),
    )


def _mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperRecordError(f"{where} is missing or is not a mapping")
    return value


def _names(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple) or not all(isinstance(name, str) for name in value):
        raise PaperRecordError(f"{where} is missing or is not a list of names")
    return tuple(value)


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise PaperRecordError(f"{where} is missing or is not text")
    return value


def _int(value: Any, where: str) -> int:
    if type(value) is not int:
        raise PaperRecordError(f"{where} is missing or is not an integer")
    return value


def _date(value: Any, where: str) -> date:
    try:
        return date.fromisoformat(_text(value, where))
    except ValueError as error:
        raise PaperRecordError(f"{where} is not an ISO date: {value!r}") from error


# --- the relative return ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Leg:
    """One computed window: its three returns, exact."""

    window: BenchmarkWindow
    book: Fraction
    investable_ew: Fraction
    csi500: Fraction


def _relative(book: _Book, windows: Sequence[BenchmarkWindow]) -> dict[str, Any]:
    ordered = sorted(windows, key=lambda item: (item.entry, item.exit, item.record_id))
    counted = Counter(item.record_id for item in ordered)
    repeated = sorted(name for name, count in counted.items() if count > 1)
    if repeated:
        raise PaperRecordError(f"record ids {repeated} are given more than once as windows")
    for earlier, later in pairwise(ordered):
        if later.entry < earlier.exit:
            raise PaperRecordError(
                f"windows {earlier.record_id} ({_day(earlier.entry)} to {_day(earlier.exit)}) and "
                f"{later.record_id} ({_day(later.entry)} to {_day(later.exit)}) overlap: a chain "
                "compounds windows that do not, and the registered sample's windows do not"
            )

    rows: list[dict[str, Any]] = []
    chains: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    current: list[_Leg] = []

    def close() -> None:
        if current:
            chains.append(_chain(current))
            current.clear()

    previous: BenchmarkWindow | None = None
    for item in ordered:
        ew = _exact(item.record_id, "investable_ew", item.investable_ew)
        csi = _exact(item.record_id, "csi500", item.csi500)
        if previous is not None and item.entry > previous.exit:
            close()
            gaps.append(
                {
                    "kind": "no_window",
                    "after": previous.record_id,
                    "before": item.record_id,
                    "from": _day(previous.exit),
                    "to": _day(item.entry),
                    "reason": _NO_WINDOW.format(start=_day(previous.exit), end=_day(item.entry)),
                }
            )
        missing = [day for day in (item.entry, item.exit) if day not in book.by_date]
        if missing:
            close()
            reason = (
                f"no snapshot for {', '.join(_day(day) for day in missing)}: the paper book's "
                f"history runs from {_day(book.opening.session)} to {_day(book.last.session)}"
            )
            gaps.append(
                {
                    "kind": "no_snapshot",
                    "record_id": item.record_id,
                    "entry": _day(item.entry),
                    "exit": _day(item.exit),
                    "missing": [_day(day) for day in missing],
                    "reason": reason,
                }
            )
            rows.append(_window_row(item, ew, csi, None, reason))
        else:
            entry_equity = Fraction(book.by_date[item.entry].book.equity)
            exit_equity = Fraction(book.by_date[item.exit].book.equity)
            if entry_equity <= 0:
                reason = f"the book's equity on {_day(item.entry)} is not positive"
                close()
                gaps.append(
                    {
                        "kind": "no_snapshot",
                        "record_id": item.record_id,
                        "entry": _day(item.entry),
                        "exit": _day(item.exit),
                        "missing": [],
                        "reason": reason,
                    }
                )
                rows.append(_window_row(item, ew, csi, None, reason))
            else:
                leg = _Leg(item, exit_equity / entry_equity - 1, ew, csi)
                current.append(leg)
                rows.append(_window_row(item, ew, csi, leg.book, None))
        previous = item
    close()
    return {
        "wording": SURVIVOR_WORDING,
        "status": STATUS,
        "label": WINDOW_LABEL,
        "units": {
            "book": _LEDGER_UNITS,
            "benchmarks": _REAL_UNITS,
        },
        "windows_given": len(ordered),
        "windows_computed": sum(1 for row in rows if row["status"] == "computed"),
        "windows": rows,
        "chains": chains,
        "gaps": gaps,
    }


def _window_row(
    item: BenchmarkWindow,
    ew: Fraction,
    csi: Fraction,
    book_return: Fraction | None,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "record_id": item.record_id,
        "entry": _day(item.entry),
        "exit": _day(item.exit),
        "status": "computed" if book_return is not None else "unavailable",
        "reason": reason,
        "book_return": _ratio(book_return),
        "investable_ew": _ratio(ew),
        "csi500": _ratio(csi),
        "book_minus_ew": _ratio(None if book_return is None else book_return - ew),
        "book_minus_csi500": _ratio(None if book_return is None else book_return - csi),
    }


def _chain(legs: Sequence[_Leg]) -> dict[str, Any]:
    """The windows `legs` -- contiguous, each entering where the one before exits -- compounded:
    the product of 1 + r, less one, for the book and for each benchmark, all exact."""
    book = ew = csi = Fraction(1)
    for leg in legs:
        book *= 1 + leg.book
        ew *= 1 + leg.investable_ew
        csi *= 1 + leg.csi500
    book, ew, csi = book - 1, ew - 1, csi - 1
    return {
        "entry": _day(legs[0].window.entry),
        "exit": _day(legs[-1].window.exit),
        "windows": len(legs),
        "record_ids": [leg.window.record_id for leg in legs],
        "book": _ratio(book),
        "investable_ew": _ratio(ew),
        "csi500": _ratio(csi),
        "book_minus_ew": _ratio(book - ew),
        "book_minus_csi500": _ratio(book - csi),
    }
