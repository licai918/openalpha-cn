"""The survivor day's paper book, wired for the daily entry (`V2-P7-008`, task D3).

Steps 5 (paper head) and 8 (paper book) of a survivor day, and the held statuses of its facts
day, as functions with plain inputs, so that `scripts/daily_selection.py` (D5) is glue. The paper
book itself is `backtest/paper_evening.py`'s (C3), and this module reaches it only through the
four entry points C3 sanctions -- `open_book`, `missing_sessions`, `run_catch_up`, `run_evening`
-- and its reads `held_head` and `load_snapshot`; it never calls `settle_session`,
`save_snapshot` or `book_state`, so the transaction, the gap rule, the ledger read and the
reproduction check stay C3's. What it adds is what C3 leaves to the daily entry: which sessions
to settle in which order, what to read off the panel for each, and every refusal named.

## A survivor day's paper book, in order

1. **The binding** (`bind_paper_book`), before the head: one paper book per runtime directory,
   bound by a write-once marker `<paper directory>/registration.json` to the registration's
   `config_id`, the book's `survivor_config_digest` and the epoch. The digest alone is the
   book's identity in every snapshot; the marker adds the `config_id`, which covers the adapter
   block too, so a changed adapter over an unchanged book -- which `open_book` accepts -- is
   refused instead of continuing the same book under another registration. Written before the
   book is opened, so a run that stops between the two leaves a marker and no book, which the
   same registration completes and another one is refused by; the marker guards against a
   changed registration, not against its own deletion -- one removed by hand is written again
   by the next run.
2. **The head** (`paper_head`): `open_book` on **every** run, with the registration's epoch,
   capital and digest (C3 review rule 2): the same opening again writes nothing, and is the
   nightly check that the book held is the one registered; any other opening is refused by
   name. When this run opens the book, an epoch more than `1 + max_panel_catch_up_sessions`
   sessions before the session is refused first (the memo's decision 3: the panel step could not
   have caught those sessions up). The head is the newest snapshot, `held` its position codes,
   which the caller pins in its journal; a run on the epoch itself only opens the book.
3. **The facts day** (`facts_day`) is the caller's step 6: `build_facts_day` with the
   registration's parameters and the pinned `held`. Its held statuses are **not** applied
   there -- the prediction must not be lost to them -- but here, by the paper book
   (`delisted_or_refuse`): `row` passes, `delisted` is handed to the settlement (a delisted
   holding with no bar is written off, D13), and every other status refuses the day by name with
   its remedy. Every function that reads a facts day takes `transform`, the processed tier the
   factors were built under: the registration's (`FACTOR_TRANSFORMS.get(adapter.transform)`)
   unless it is given, as it is for a store built under another one.
4. **The catch-up** (`catch_up`), before the evening: every session `missing_sessions` names
   through the session before tonight's, oldest first. For a book holding something the session's
   facts are read at the run's `as_of` with **`min_coverage=0.0`** -- a catch-up makes no
   decision, so a session whose facts the registered coverage refused (its evening stopped at
   step 6) still settles; a universe of nothing is still refused by the adapter -- and the held
   statuses applied; for a book holding nothing no facts are read at all and nothing is
   delisted. The market is `settlement_market` for the head's held names and the names of its
   live orders. `settle_only` is the same catch-up for the session itself: the caller's choice
   for a run after 09:15 of the next session on a session with **no** snapshot (decision 2); a
   session already held is refused there, never routed to a catch-up (C3 review rule 1).
5. **The evening** (`evening`): `run_evening` with tonight's screen, the market of the book before
   the session (its held names and live orders' names, with the pinned `held`), **every** close
   of the facts day as the entrants' prices -- C3 prices held names from the ledger, in its units,
   whatever they say, and a name sold at tonight's open must keep its real close in case the
   decision buys it again (rule 3) -- and the delisted set of the facts day.

**The facts are read again when the catch-up changed the holdings.** The caller reads tonight's
facts with the `held` it pinned before the catch-up; the eligible and survivor sets do not
depend on `held`, but the rows of long-halted holdings and the held statuses do. So `evening`
compares the pinned `held` with the holdings of the book its session settles from and, when they
differ, reads the facts day again itself, with those holdings, at the registered coverage, and
screens it again (the caller's facts and screen are used as given otherwise). The evening it
returns carries the facts and screen it ran on.

**A session already settled is run as an evening, whatever the clock** (rule 1): `evening` always
calls `run_evening`, whose answer for a held session is `unchanged`, writing nothing, with the
evening's settlement, decision and trade list derived again -- from the pinned inputs, which is
what makes a rerun reproduce. The book it settles from is then read with `load_snapshot` (the
session before, held), because the head is the session itself.

**`observed_on`** is the Shanghai date of the run's journal-pinned `as_of` on every call (rule
6), so a rerun on another day derives what the first run did; an `as_of` without a time zone is a
programming error (`TypeError`).

## One writer

C3 assumes one writer per book (rule 4). `paper_book_lock` is the paper book's own lock -- an
exclusive advisory lock on `<paper directory>/writer.lock`, taken without waiting, released when
its holder lets go or dies (the kernel drops it with the process) -- and the panel's catalog lock
is no substitute: it is held per catalog operation, not across a day. The caller takes it before
it reads the head whose `held` it pins and holds it until the paper book's step is done, so that
`held`, the catch-up and the evening all see one book; a second run meanwhile is refused by name
(`SurvivorDayError`, step `paper head`) having read and written nothing of the book. This module
does not take it itself: D5 wraps steps 5 through 8 in it.

## Refusals

Every refusal is a `SurvivorDayError` naming its step -- `paper head`, `facts` or `paper book`,
the daily command's own step names -- and its remedy. The paper book's three error classes are
mapped by class, never by message: `PaperStateError` (`PaperLedgerConflictError` included), the
store's `storage.paper_state.PaperStateStoreError` (which `backtest/` cannot import, so C3 cannot
make it a `PaperStateError`), and `ValueError` -- the contract refusals of the facts adapter, the
session market, the ordinals and the kernel. Anything else reaches the caller as itself: a
`TypeError` or an `AssertionError` is a programming error, and the panel's own `RuntimeError`s
(a busy or damaged catalog) are the panel's to name.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from openalpha_cn.backtest.multi_day import PortfolioLedger
from openalpha_cn.backtest.paper import PaperPortfolio
from openalpha_cn.backtest.paper_evening import (
    CatchUpResult,
    EveningResult,
    ReadableLedger,
    missing_sessions,
    open_book,
    run_catch_up,
    run_evening,
)
from openalpha_cn.backtest.paper_state import (
    PaperBookSnapshot,
    PaperStateDocumentStore,
    PaperStateError,
    held_head,
    load_snapshot,
)
from openalpha_cn.domain.factor_transform import FactorTransformSpec
from openalpha_cn.domain.survivor_book import ScreenResult
from openalpha_cn.domain.survivor_screen import screen as survivor_screen
from openalpha_cn.domain.trading_calendar import TradingCalendar

# `_try_lock` and `_unlock` are the panel catalog's two lock primitives, shared rather than
# restated: an exclusive `flock(2)` (`msvcrt.locking` on Windows) through one open file
# description, which two descriptors of one process meet exactly as two processes do, and which
# the kernel drops with its holder.
from openalpha_cn.panel.store import PanelStore, _try_lock, _unlock
from openalpha_cn.panel_factors import FACTOR_TRANSFORMS
from openalpha_cn.storage.paper_state import PaperStateStoreError
from openalpha_cn.strategy_survivor import SurvivorRegistration
from openalpha_cn.strategy_survivor_facts import FactsDay, build_facts_day
from openalpha_cn.strategy_survivor_market import SessionOrdinals, settlement_market

__all__ = [
    "PAPER_BINDING_SCHEMA",
    "PAPER_BOOK_LOCK",
    "PAPER_BOOK_MARKER",
    "SURVIVOR_BOOK_STEPS",
    "PaperHead",
    "SurvivorDayError",
    "SurvivorEvening",
    "bind_paper_book",
    "catch_up",
    "delisted_or_refuse",
    "evening",
    "facts_day",
    "held_codes",
    "paper_book_lock",
    "paper_head",
    "settle_only",
    "settlement_names",
]

SURVIVOR_BOOK_STEPS: Final[tuple[str, ...]] = ("paper head", "facts", "paper book")
"""The survivor day's steps this module refuses under: `SURVIVOR_STEPS`' own names, so the daily
command numbers a refusal from here as it numbers its own."""

PAPER_BOOK_MARKER: Final[str] = "registration.json"
"""The write-once binding of a runtime directory's paper book to its registration."""

PAPER_BOOK_LOCK: Final[str] = "writer.lock"
"""The paper book's writer lock, beside the marker in the paper directory."""

PAPER_BINDING_SCHEMA: Final[str] = "survivor-paper-binding/v1"

_SHANGHAI: Final[ZoneInfo] = ZoneInfo("Asia/Shanghai")
"""The exchange's zone: a run's `observed_on` is its `as_of`'s date there."""

_CATCH_UP_COVERAGE: Final[float] = 0.0
"""A catch-up makes no decision, so it reads a session's facts with no coverage floor."""

_REFUSED_STATUS_REMEDIES: Final[dict[str, str]] = {
    "no_bar_in_reach": (
        "listed, and the panel holds no daily bar of it since its listing or the panel's first "
        "session, so it has no close to carry and the kernel would sell it as unassessed: "
        "ingest its `daily` bars back to its last traded session, then run again"
    ),
    "not_in_registry": (
        "the security registry has no listing row for it at all -- a typo or a restated code, "
        "never a security to sell: update the registry (`stock_basic`) and check the code the "
        "paper ledger holds"
    ),
    "not_yet_listed": (
        "the registry dates its listing after the day, so it is not a delisting and is never "
        "written off as one: the registry and the book disagree -- update the registry "
        "(`stock_basic`) and run again"
    ),
    "board_unrecognised": (
        "its prefix, with its exchange, is not a main-board pair the adapter recognises "
        "(`MAIN_BOARD_PREFIXES`): the prefix is a decision to take in the adapter's code before "
        "the book can settle it"
    ),
}
"""Every held status that refuses the day, with its remedy. `row` passes, `delisted` is handed on,
and a status not named here (a later addition) is refused too: fail closed."""


class SurvivorDayError(RuntimeError):
    """A survivor day's paper book refused, by name, with its remedy; `step` is the daily
    command's name of the step it stopped (`SURVIVOR_BOOK_STEPS`).

    A `RuntimeError`, as `PaperStateError` is: a caller's `except ValueError` around a contract
    must not swallow a book that stopped.
    """

    def __init__(self, step: str, message: str) -> None:
        if step not in SURVIVOR_BOOK_STEPS:
            raise ValueError(f"{step!r} is not one of the survivor day's steps here")
        super().__init__(message)
        self.step = step


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperHead:
    """The paper book as a run finds it, after its opening is checked.

    `snapshot` is the head -- the newest snapshot held -- and `held` its position codes, which the
    caller pins in its journal and reads the session's facts with. `session_index` is the run's
    session's ordinal. `opened_only`: the session is the epoch, which no evening settles.
    `settled`: the session already has its snapshot (an evening, or a catch-up, committed it);
    `held` is then the head's, not that evening's input -- a rerun takes `held` from its journal.
    """

    snapshot: PaperBookSnapshot
    session_index: int
    held: tuple[str, ...]
    opened_only: bool
    settled: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class SurvivorEvening:
    """One evening of the paper book: C3's `EveningResult`, the holdings it leaves (`held_after`),
    and the facts day and screen it ran on -- the caller's, or the ones read again when the
    catch-up had changed the holdings."""

    result: EveningResult
    held_after: tuple[str, ...]
    facts: FactsDay
    screen: ScreenResult


def held_codes(snapshot: PaperBookSnapshot) -> tuple[str, ...]:
    """The codes `snapshot`'s book holds, ascending: the `held` a facts day is read with."""
    return tuple(sorted(position.subject for position in snapshot.book.positions))


def settlement_names(snapshot: PaperBookSnapshot) -> tuple[str, ...]:
    """The held codes and the names of the live orders, ascending, each once: the names the next
    session's market is read for."""
    return tuple(
        sorted({*held_codes(snapshot), *(item.order.ts_code for item in snapshot.pending_orders)})
    )


def delisted_or_refuse(facts: FactsDay) -> frozenset[str]:
    """The held codes `facts` reports `delisted` -- handed to the settlement, which writes off one
    with no bar -- or a refusal naming every held code the book cannot settle and its remedy.

    `row` passes; `no_bar_in_reach`, `not_in_registry`, `not_yet_listed` and
    `board_unrecognised` refuse the day (`SurvivorDayError`, step `facts`), as does any status
    this module does not know.
    """
    refused = [
        f"{status.ts_code} is {status.status}: "
        + _REFUSED_STATUS_REMEDIES.get(
            status.status, "a held status this build does not know; refused rather than guessed"
        )
        for status in facts.held_status
        if status.status not in ("row", "delisted")
    ]
    if refused:
        raise SurvivorDayError(
            "facts",
            f"the facts day of {facts.session.isoformat()} reports holdings the paper book cannot "
            f"settle -- {'; '.join(refused)}. The book stops until the data is fixed; nothing of "
            "the session was settled",
        )
    return frozenset(status.ts_code for status in facts.held_status if status.status == "delisted")


def bind_paper_book(
    directory: Path, *, config_id: str, config_digest: str, epoch_session: date
) -> None:
    """Bind the runtime directory's paper book to its registration, once.

    Writes `<directory>/registration.json` -- the registration's `config_id`, the book's
    `survivor_config_digest` and the epoch -- when it is not there; the same binding again
    writes nothing; another one, or a marker that cannot be read, is refused by name
    (`SurvivorDayError`, step `paper head`) and the marker is left as it is. One paper book per
    runtime directory: a changed registration is another book, whose block of the adapter alone
    would otherwise continue this one. The marker reaches its name whole or not at all (written
    beside it, then linked to it, which fails rather than overwrites when another run got there
    first).
    """
    if not config_id:
        raise ValueError("config_id names the registration and cannot be empty")
    binding = {
        "schema_version": PAPER_BINDING_SCHEMA,
        "config_id": config_id,
        "config_digest": config_digest,
        "epoch_session": epoch_session.isoformat(),
    }
    marker = directory / PAPER_BOOK_MARKER
    if not marker.exists():
        directory.mkdir(parents=True, exist_ok=True)
        if _link_once(marker, json.dumps(binding, indent=2, sort_keys=True) + "\n"):
            return
    _refuse_another_binding(marker, binding)


def _link_once(marker: Path, text: str) -> bool:
    """Write `text` beside `marker` and link it to `marker`'s name: `True` when this call wrote
    it, `False` when another run had already.

    The file beside it is this call's own (`mkstemp`), never a name another writer could open
    too: a shared one, truncated by a second writer after the first had linked it, would empty
    the marker through its other name."""
    descriptor, name = tempfile.mkstemp(
        dir=marker.parent, prefix=f"{marker.name}.", suffix=".partial"
    )
    partial = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(partial, marker)
        except FileExistsError:
            return False
        return True
    finally:
        partial.unlink(missing_ok=True)


def _refuse_another_binding(marker: Path, binding: dict[str, str]) -> None:
    """Refuse by name a held marker that is not `binding`, or that cannot be read."""
    try:
        held = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SurvivorDayError(
            "paper head",
            f"the paper book's marker {marker} cannot be read ({type(error).__name__}: {error}); "
            "it binds this runtime directory's paper book to its registration, so it is never "
            "replaced by a run: restore it, or -- if no book was ever opened here -- remove it",
        ) from error
    if held == binding:
        return
    differing = sorted(
        field
        for field in set(binding) | (set(held) if isinstance(held, dict) else set())
        if not isinstance(held, dict) or held.get(field) != binding.get(field)
    )
    raise SurvivorDayError(
        "paper head",
        f"this runtime directory's paper book is bound by {marker} to {held!r}, and this run is "
        f"{binding!r}: they differ in {differing}. One paper book per runtime directory -- a "
        "changed registration, even one whose book block is unchanged, is another book, with a "
        "runtime directory of its own. Nothing was written",
    )


@contextmanager
def paper_book_lock(directory: Path) -> Iterator[Path]:
    """Hold the paper book's writer lock for the body, or refuse at once.

    `<directory>/writer.lock`, exclusive and advisory, taken without waiting: a second holder --
    another run, in this process or another -- is refused by name (`SurvivorDayError`, step
    `paper head`) before anything of the book is read or written. The kernel drops the lock with
    its holder however the holder ends, so a crashed run never wedges the book. See the module
    docstring for what it must cover.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / PAPER_BOOK_LOCK
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as error:
        raise SurvivorDayError(
            "paper head",
            f"the paper book's writer lock {path} cannot be opened ({error.strerror or error}): "
            "the paper directory must be writable by this account. Nothing was read or written",
        ) from error
    try:
        if not _try_lock(descriptor, exclusive=True):
            raise SurvivorDayError(
                "paper head",
                f"another run holds the paper book's writer lock {path}: one writer per paper "
                "book. Let it finish -- a holder that crashed has released it already -- and run "
                "again. Nothing of the book was read or written",
            )
        try:
            yield path
        finally:
            _unlock(descriptor)
    finally:
        os.close(descriptor)


def paper_head(
    *,
    state_store: PaperStateDocumentStore,
    registration: SurvivorRegistration,
    ordinals: SessionOrdinals,
    session: date,
) -> PaperHead:
    """Open the paper book -- on every run -- and return its head as `session`'s run finds it.

    `ordinals` must count from the registration's epoch. `open_book` is called with the epoch,
    the book's capital and its digest whatever the store holds: the same opening writes nothing,
    another is refused by name (`SurvivorDayError`, step `paper head`). When the store holds no
    book, an epoch more than `1 + max_panel_catch_up_sessions` sessions before `session` is
    refused before the book is opened.
    """
    adapter = registration.adapter
    epoch = adapter.epoch_session
    _require_registered_epoch(ordinals, registration, step="paper head")
    with _refusals("paper head", f"the paper book's head for {session.isoformat()}"):
        index = ordinals.index_of(session)
        opening = held_head(state_store) is None
        bound = 1 + adapter.max_panel_catch_up_sessions
        if opening and index > bound:
            raise SurvivorDayError(
                "paper head",
                f"the paper book opens on its epoch {epoch.isoformat()}, {index} sessions before "
                f"this run's {session.isoformat()}, and an epoch may be at most {bound} sessions "
                "before the first run (1 + max_panel_catch_up_sessions): the sessions between "
                "were never caught up by the panel step. Register the epoch as the session "
                "before the first scheduled run. Nothing was written",
            )
        open_book(
            store=state_store,
            session=epoch,
            session_index=0,
            cash=registration.book.capital,
            config_digest=registration.digest,
        )
        head = held_head(state_store)
    if head is None:
        raise SurvivorDayError(
            "paper head",
            "the paper book's store holds no snapshot after its opening was saved: the store "
            "does not keep what it is given. Check the runtime directory's paper/snapshots",
        )
    return PaperHead(
        snapshot=head,
        session_index=index,
        held=held_codes(head),
        opened_only=index == 0,
        settled=index > 0 and head.session_index >= index,
    )


def facts_day(
    *,
    panel: PanelStore,
    registration: SurvivorRegistration,
    session: date,
    as_of: datetime,
    held: Collection[str],
    min_coverage: float | None = None,
    transform: FactorTransformSpec | None = None,
) -> FactsDay:
    """`session`'s facts day read off `panel` at `as_of` with `held`, under the registration:
    its composite factors, its exchange, its carry bound, and `min_coverage` unless another is
    given (a catch-up's is 0).

    `transform` is the processed tier the factors were built under; `None` is the registration's
    (`FACTOR_TRANSFORMS.get(adapter.transform)`). A refusal of the adapter is a
    `SurvivorDayError`, step `facts`; the held statuses are not applied (`delisted_or_refuse`).
    """
    adapter = registration.adapter
    with _refusals("facts", f"the facts day of {session.isoformat()}"):
        return build_facts_day(
            store=panel,
            session=session,
            as_of=as_of,
            config=registration.book,
            factor_ids=registration.book.composite_factors,
            transform=FACTOR_TRANSFORMS.get(adapter.transform) if transform is None else transform,
            exchange=adapter.exchange,
            min_coverage=adapter.min_coverage if min_coverage is None else min_coverage,
            max_carry_sessions=adapter.max_carry_sessions,
            held=held,
        )


def catch_up(
    *,
    panel: PanelStore,
    state_store: PaperStateDocumentStore,
    ledger: ReadableLedger,
    registration: SurvivorRegistration,
    ordinals: SessionOrdinals,
    calendar: TradingCalendar,
    through_index: int,
    as_of: datetime,
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    transform: FactorTransformSpec | None = None,
) -> tuple[CatchUpResult, ...]:
    """Settle, oldest first and without a decision, every session the book is missing through
    ordinal `through_index` -- `missing_sessions`' answer -- and return what each did.

    For each: the facts day at `as_of` with the book's holdings and no coverage floor, and its
    delisted set (`delisted_or_refuse`), when the book holds something, and neither when it
    holds nothing; the session's market for the held names and the live orders' names; then
    `run_catch_up`, observed on `as_of`'s Shanghai date. A refusal stops the catch-up at that
    session with the sessions before it settled (each is its own transaction). `transform` as
    `facts_day`'s. A session already held is not missing, so it is never caught up.
    """
    observed_on = _observed_on(as_of)
    _require_registered_epoch(ordinals, registration, step="paper book")
    with _refusals("paper book", "the paper book's catch-up"):
        missing = missing_sessions(
            state_store, config_digest=registration.digest, through_index=through_index
        )
        head = held_head(state_store)
    if head is None:
        raise SurvivorDayError(
            "paper book",
            "no paper book is open in this runtime directory: the paper head opens it first",
        )
    results: list[CatchUpResult] = []
    for index in missing:
        with _refusals("paper book", f"the catch-up of session index {index}"):
            day = ordinals.day_of(index)
        held = held_codes(head)
        delisted: frozenset[str] = frozenset()
        if held:
            delisted = delisted_or_refuse(
                facts_day(
                    panel=panel,
                    registration=registration,
                    session=day,
                    as_of=as_of,
                    held=held,
                    min_coverage=_CATCH_UP_COVERAGE,
                    transform=transform,
                )
            )
        named = f"the catch-up of {day.isoformat()} (session index {index})"
        with _refusals("paper book", named):
            market = settlement_market(
                panel,
                session=day,
                as_of=as_of,
                names=settlement_names(head),
                calendar=calendar,
                benchmark=registration.adapter.benchmark,
            )
            result = run_catch_up(
                store=state_store,
                ledger=ledger,
                make_portfolio=make_portfolio,
                config=registration.book,
                session=day,
                session_index=index,
                market=market,
                delisted=delisted,
                observed_on=observed_on,
            )
        results.append(result)
        head = result.snapshot
    return tuple(results)


def settle_only(
    *,
    panel: PanelStore,
    state_store: PaperStateDocumentStore,
    ledger: ReadableLedger,
    registration: SurvivorRegistration,
    ordinals: SessionOrdinals,
    calendar: TradingCalendar,
    session: date,
    as_of: datetime,
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    transform: FactorTransformSpec | None = None,
) -> tuple[CatchUpResult, ...]:
    """`catch_up` through `session` itself: the sessions missed before it, then `session`,
    settled without a decision.

    The caller's choice for a run at or after 09:15 of the session after `session`, when no
    decision may be made any more (decision 2), and only for a session with **no** snapshot: a
    session already held -- an evening that saved its snapshot and stopped before its journal --
    is refused by name, because it is re-run by `evening` with the journal-pinned inputs (C3
    review rule 1), and the epoch is refused because no evening settles it.
    """
    _require_registered_epoch(ordinals, registration, step="paper book")
    with _refusals("paper book", f"settling {session.isoformat()} only"):
        index = ordinals.index_of(session)
        head = held_head(state_store)
    if head is None:
        raise SurvivorDayError(
            "paper book",
            "no paper book is open in this runtime directory: the paper head opens it first",
        )
    if index == 0:
        raise SurvivorDayError(
            "paper book",
            f"{session.isoformat()} is the paper book's epoch, which it is opened on and no "
            "evening or catch-up settles; the first session settled is the one after it",
        )
    if head.session_index >= index:
        raise SurvivorDayError(
            "paper book",
            f"{session.isoformat()} (session index {index}) already has its snapshot -- the book "
            f"is settled through {head.session.isoformat()} -- so it is never settled again, and "
            "never caught up: a held session is run again by its evening, with the inputs its "
            "journal pinned, whose unchanged path writes nothing whatever the clock says",
        )
    return catch_up(
        panel=panel,
        state_store=state_store,
        ledger=ledger,
        registration=registration,
        ordinals=ordinals,
        calendar=calendar,
        through_index=index,
        as_of=as_of,
        make_portfolio=make_portfolio,
        transform=transform,
    )


def evening(
    *,
    panel: PanelStore,
    state_store: PaperStateDocumentStore,
    ledger: ReadableLedger,
    registration: SurvivorRegistration,
    ordinals: SessionOrdinals,
    calendar: TradingCalendar,
    session: date,
    as_of: datetime,
    facts: FactsDay,
    screen: ScreenResult,
    held: Sequence[str],
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    transform: FactorTransformSpec | None = None,
) -> SurvivorEvening:
    """Run `session`'s evening on the paper book: `run_evening`, always (see the module
    docstring).

    `facts` and `screen` are the caller's step 6, read with `held` -- the holdings it pinned
    before the catch-up -- at `as_of`. The sessions before `session` must be settled (`catch_up`
    first). When the book the session settles from holds other codes than `held`, the facts day
    is read again with them and screened again. Then: the held statuses (`delisted_or_refuse`),
    the session's market for the names of that book and `held`, every close of the facts day as
    the entrants' prices, and `run_evening` observed on `as_of`'s Shanghai date. `transform` as
    `facts_day`'s.
    """
    observed_on = _observed_on(as_of)
    pinned = _pinned(held)
    if facts.session != session or facts.held != pinned:
        raise SurvivorDayError(
            "facts",
            f"the evening of {session.isoformat()} was given the facts day of "
            f"{facts.session.isoformat()} read with held {list(facts.held)}, and its pinned "
            f"held is {list(pinned)}: the facts are read for the session with the held the "
            "journal pinned",
        )
    _require_registered_epoch(ordinals, registration, step="paper book")
    with _refusals("paper book", f"the evening of {session.isoformat()}"):
        index = ordinals.index_of(session)
        head = held_head(state_store)
    if head is None:
        raise SurvivorDayError(
            "paper book",
            "no paper book is open in this runtime directory: the paper head opens it first",
        )
    if index == 0:
        raise SurvivorDayError(
            "paper book",
            f"{session.isoformat()} is the paper book's epoch, which it is opened on and no "
            "evening settles; its first evening is the session after it",
        )
    if head.session_index < index - 1:
        raise SurvivorDayError(
            "paper book",
            f"the paper book is settled through {head.session.isoformat()} (session index "
            f"{head.session_index}) and tonight is session index {index}: the sessions between "
            "are caught up first (catch_up through the session before tonight's)",
        )
    before = head
    if head.session_index >= index:
        with _refusals("paper book", f"the evening of {session.isoformat()}, settled already"):
            before = load_snapshot(state_store, ordinals.day_of(index - 1))
    holdings = held_codes(before)
    if holdings != pinned:
        facts = facts_day(
            panel=panel,
            registration=registration,
            session=session,
            as_of=as_of,
            held=holdings,
            transform=transform,
        )
        with _refusals("facts", f"the screen of {session.isoformat()}"):
            screen = survivor_screen(facts.facts, registration.book)
    delisted = delisted_or_refuse(facts)
    with _refusals("paper book", f"the evening of {session.isoformat()}"):
        market = settlement_market(
            panel,
            session=session,
            as_of=as_of,
            names=sorted({*settlement_names(before), *pinned}),
            calendar=calendar,
            benchmark=registration.adapter.benchmark,
        )
        result = run_evening(
            store=state_store,
            ledger=ledger,
            make_portfolio=make_portfolio,
            config=registration.book,
            session=session,
            session_index=index,
            market=market,
            screen=screen,
            entrant_prices=_closes(facts),
            delisted=delisted,
            observed_on=observed_on,
        )
    return SurvivorEvening(
        result=result, held_after=held_codes(result.snapshot), facts=facts, screen=screen
    )


def _require_registered_epoch(
    ordinals: SessionOrdinals, registration: SurvivorRegistration, *, step: str
) -> None:
    """Refuse ordinals counted from another day than the registration's epoch: every snapshot
    names its session by the registration's count."""
    epoch = registration.adapter.epoch_session
    if ordinals.epoch != epoch:
        raise SurvivorDayError(
            step,
            f"the session ordinals count from {ordinals.epoch.isoformat()} and the registration's "
            f"epoch is {epoch.isoformat()}: the book's ordinals are the registration's; build "
            "them from adapter.epoch_session",
        )


def _closes(facts: FactsDay) -> dict[str, Decimal]:
    """Every close of the facts day: C3 prices the held names from the ledger over these."""
    return {fact.ts_code: fact.close for fact in facts.facts}


def _pinned(held: Sequence[str]) -> tuple[str, ...]:
    if isinstance(held, str):
        raise TypeError(f"held is a collection of codes, not the one string {held!r}")
    return tuple(sorted(set(held)))


def _observed_on(as_of: datetime) -> date:
    """`as_of`'s date in Shanghai: the `observed_on` of every settlement of the run."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise TypeError(
            f"as_of {as_of.isoformat()} has no time zone; a run's instant is pinned with its "
            "time zone, and its observed_on is the date in Shanghai"
        )
    return as_of.astimezone(_SHANGHAI).date()


@contextmanager
def _refusals(step: str, what: str) -> Iterator[None]:
    """Name a refusal of the paper book or of a contract as `step`'s, by its class: the book's
    state (`PaperStateError`), its store (`PaperStateStoreError`) and a contract (`ValueError`).
    A `SurvivorDayError` passes through; nothing else is caught."""
    try:
        yield
    except SurvivorDayError:
        raise
    except PaperStateStoreError as error:
        raise SurvivorDayError(
            step,
            f"{what} was refused: the paper book's snapshot store is damaged "
            f"(PaperStateStoreError: {error}). A damaged snapshot is never read as an empty book; "
            "restore the runtime directory's paper/snapshots from a backup",
        ) from error
    except (PaperStateError, ValueError) as error:
        raise SurvivorDayError(
            step, f"{what} was refused ({type(error).__name__}): {error}"
        ) from error
