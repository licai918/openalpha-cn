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

0. **One writer** (`paper_book_lock`): the caller takes the paper book's writer lock before step
   2 and holds it through step 8; every function here that touches the book takes the lock's
   token (`lock=`) and refuses to run unless it is still held (see *One writer* below).
1. **The head** (`paper_head`), step 5. When the store holds no book this run opens it: an
   epoch more than `1 + max_panel_catch_up_sessions` sessions before the session is refused
   first (the memo's decision 3: the panel step could not have caught those sessions up), then
   the paper directory's write-once marker `registration.json` is written -- or found equal --
   binding the book to the registration's `config_id`, the book's `survivor_config_digest` and
   the epoch, then `open_book` saves the opening. When the store holds a book the marker must be
   there and be equal -- one that is missing or differs is refused by name and nothing is
   written, because the marker is what tells this book's registration from another's (the
   digest alone is the book block's; the `config_id` covers the adapter block too, so a changed
   adapter over an unchanged book, which `open_book` accepts, is refused here) -- and then
   `open_book` is called again with the registration's epoch, capital and digest (C3 review
   rule 2): the same opening writes nothing, and is the nightly check that the book held is the
   one registered. The head is the newest snapshot, `held` its position codes, which the caller
   pins in its journal; a run on the epoch itself only opens the book. The caller reads the
   head it pins with `require_bound` -- the same binding checks, reading only, before its first
   request -- so a day never pins the holdings of another registration's book.
2. **The facts day** (`facts_day`) is the caller's step 6: `build_facts_day` with the
   registration's parameters and the pinned `held`. Its held statuses are **not** applied
   there -- the prediction must not be lost to them -- but by the paper book
   (`delisted_or_refuse`): `row` passes, `delisted` is handed to the settlement (a delisted
   holding with no bar is written off, D13), and every other status refuses the day by name with
   its remedy. **`transform`** is the processed tier the factors were built under: the daily
   command resolves `FACTOR_TRANSFORMS.get(adapter.transform)` once and passes that one object
   to `facts_day`, `catch_up`, `evening` and `strategy_survivor_registration`'s
   `survivor_input_provenance`; `None` resolves the same object here. Another object is a test's
   injection, for a store built under another transform.
3. **The catch-up** (`catch_up`), step 8, before the evening: every session `missing_sessions`
   names through the session before tonight's, oldest first, each settled from the book the one
   before it left. For a book holding something the session's facts are read at the run's
   `as_of` with **`min_coverage=0.0`** -- a catch-up makes no decision, so a session whose facts
   the registered coverage refused (its evening stopped at step 6) still settles; a universe of
   nothing is still refused by the adapter -- and the held statuses applied; for a book holding
   nothing no facts are read at all and nothing is delisted. The market is `settlement_market`
   for the book's held names and the names of its live orders. Each session is its own
   transaction: `on_settled` is called with it as soon as it commits, and a refusal at a later
   session raises `CatchUpStalledError`, which carries the sessions settled before it.
4. **The evening** (`evening`), step 8: `run_evening` with tonight's screen, the market of the book
   the session settles from (its held names and its live orders' names), **every** close of the
   facts day as the entrants' prices -- C3 prices held names from the ledger, in its units,
   whatever they say, and a name sold at tonight's open must keep its real close in case the
   decision buys it again (rule 3) -- and the delisted set of the facts day.

**The facts are read again when the catch-up changed the holdings.** The caller reads tonight's
facts with the `held` it pinned before the catch-up; the eligible and survivor sets do not
depend on `held`, but the rows of long-halted holdings and the held statuses do. So `evening`
compares the pinned `held` with the holdings of the book its session settles from and, when they
differ, reads the facts day again itself, with those holdings and **no coverage floor** -- the
day's coverage was judged at step 6, and the prediction filed at step 7 stands on it -- and
screens it again. The second screen must agree with the caller's on the eligible set and on the
survivors, ranked; one that does not is refused by name, because the design's one assumption
about `held` would be wrong. The evening it returns carries the facts and screen it ran on.

**Reruns.** A session already settled is run as an evening by `evening`, whatever the clock
(rule 1): it always calls `run_evening`, whose answer for a held session is `unchanged`, writing
nothing, with the evening's settlement, decision and trade list derived again -- from the
journal-pinned inputs, which is what makes a rerun reproduce; the book it settles from is then
read with `load_snapshot` (the session before, held), because the head is the session itself.
`evening` never routes a held session to a catch-up. `settle_only` -- the caller's choice for a
session with no snapshot when the run's pinned `as_of` is at or after 09:15 of the next session
(decision 2) -- derives its **own** session again with `run_catch_up` when it is held already: a
settle-only run that committed and stopped before its journal is finished that way, writing
nothing, while a session an evening settled with orders is refused by C3 by name. **Which of the
two a rerun is is the caller's, by the journal-pinned `as_of`, never by the clock**: an evening
iff that `as_of` is before 09:15 of the session after. A session `catch_up` committed whose run
stopped before the caller journalled it is no longer missing, so no catch-up settles it again:
`catch_up_again` derives such sessions again with `run_catch_up` (`unchanged`, nothing written)
and reports them through `on_settled`, so their fills and write-offs reach a document; the caller
names which (D5 joint review, Important 3).

**`observed_on`** is the Shanghai date of the run's journal-pinned `as_of` on every call (rule
6); an `as_of` without a time zone is a programming error (`TypeError`). A rerun on another day
reads at that pinned `as_of` and derives what the first run did -- **until the next session's
panel update**: `adj_factor` and `index_daily` are read a year at a time through the panel's
whole-partition door, which refuses an `as_of` older than the newest row its year holds
(`PartitionNotYetKnowableError`, a `PanelStorageError`, deliberately not mapped here: it is the
panel's to name). A rerun after that reads at the newest instant the panel can be read at:
`settlement_market` gives a later read the evening's bytes (C1 pins it), and C3's equality check
refuses any session whose derivation differs from what it settled.

## One writer

C3 assumes one writer per book (rule 4). `paper_book_lock` is the paper book's own lock -- an
exclusive advisory lock on `<paper directory>/writer.lock`, taken without waiting, released when
its holder lets go or dies (the kernel drops it with the process) -- and the panel's catalog lock
is no substitute: it is held per catalog operation, not across a day. It yields a
`PaperBookLock`, the token every function that writes or pins the book (`paper_head`,
`catch_up`, `settle_only`, `evening`) requires and checks to be still held -- still locked by
this run, on the file the paper directory holds under that name -- so the single writer is a
fact of the calls, not a convention: a probe through a second descriptor could not tell this
run's lock from another run's. A second run is refused at once (`PaperBookBusyError`), having
read and written nothing of the book; the caller takes the lock before its step 2 and names that
refusal under its own step. Advisory locks hold on a **local filesystem** only, as
`panel.store`'s catalog lock does (over NFS or SMB they are as good as the server's lock
manager); and a child started by `fork` while the lock is held inherits the open file
description, so it holds the lock with its parent until both have let go.

## Refusals

Every refusal is a `SurvivorDayError` naming its step and its remedy: `paper head` for the head,
`facts` for the caller's own step-6 read (`facts_day`), and **`paper book` for everything raised
inside `catch_up`, `catch_up_again`, `settle_only` and `evening`** -- they run in step 8, after
step 7 filed the prediction, whatever they were reading when they stopped. The paper book's three
error classes are mapped by class, never by message: `PaperStateError` (`PaperLedgerConflictError`
included), the store's `storage.paper_state.PaperStateStoreError` (which `backtest/` cannot
import, so C3 cannot make it a `PaperStateError`), and `ValueError` -- the contract refusals of
the facts adapter, the session market, the ordinals and the kernel. Anything else reaches the
caller as itself: a `TypeError` or an `AssertionError` is a programming error, and the panel's
own `RuntimeError`s (a busy or damaged catalog, a partition not yet knowable) are the panel's to
name.
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
    "CatchUpStalledError",
    "PaperBookBusyError",
    "PaperBookLock",
    "PaperHead",
    "SurvivorDayError",
    "SurvivorEvening",
    "bind_paper_book",
    "catch_up",
    "catch_up_again",
    "decided_by_an_evening",
    "delisted_or_refuse",
    "evening",
    "facts_day",
    "held_codes",
    "paper_book_lock",
    "paper_head",
    "require_bound",
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

_NO_COVERAGE_FLOOR: Final[float] = 0.0
"""A catch-up decides nothing, and the evening's second read decides nothing the first did not:
both read a facts day with no coverage floor."""

_SYNC_DIRECTORIES: Final[bool] = os.name != "nt"
"""Whether the marker's directory is synced after its link: everywhere but Windows, which cannot
open a directory as a file (`storage/paper_state.py`'s policy)."""

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
        "the book can settle it, and a code change binds a new registration (V2-P7-006)"
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


class PaperBookBusyError(SurvivorDayError):
    """Another run holds the paper book's writer lock. Its `step` is `paper head`; the daily
    command, which takes the lock before its step 2, names it under its own step."""


class CatchUpStalledError(SurvivorDayError):
    """A catch-up stopped at a session, by name; `caught_up` is every session it settled before
    that one -- committed, each its own transaction, and kept."""

    def __init__(self, step: str, message: str, *, caught_up: tuple[CatchUpResult, ...]) -> None:
        super().__init__(step, message)
        self.caught_up = caught_up


class PaperBookLock:
    """The paper book's writer lock as this run holds it: the token `paper_head`, `catch_up`,
    `settle_only` and `evening` require. Made only by `paper_book_lock`.

    `held` is whether this run still holds it: the lock is not let go, and the file under its
    name in the paper directory is still the one this run locked -- a lock file removed and made
    again could be locked by another run while this one's descriptor locks nothing anybody sees.
    """

    __slots__ = ("_descriptor", "directory", "path")

    def __init__(self, directory: Path, path: Path, descriptor: int) -> None:
        self.directory = directory
        self.path = path
        self._descriptor: int | None = descriptor

    @property
    def held(self) -> bool:
        if self._descriptor is None:
            return False
        try:
            on_disk = os.stat(self.path)
        except FileNotFoundError:
            return False
        locked = os.fstat(self._descriptor)
        return (locked.st_dev, locked.st_ino) == (on_disk.st_dev, on_disk.st_ino)

    def _let_go(self) -> None:
        self._descriptor = None


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


def delisted_or_refuse(facts: FactsDay, *, step: str = "paper book") -> frozenset[str]:
    """The held codes `facts` reports `delisted` -- handed to the settlement, which writes off one
    with no bar -- or a refusal naming every held code the book cannot settle and its remedy.

    `row` passes; `no_bar_in_reach`, `not_in_registry`, `not_yet_listed` and
    `board_unrecognised` refuse the day (`SurvivorDayError` under `step`, the paper book's by
    default: the held statuses are the paper book's to apply), as does any status this module
    does not know.
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
            step,
            f"the facts day of {facts.session.isoformat()} reports holdings the paper book cannot "
            f"settle -- {'; '.join(refused)}. The book stops until the data is fixed; nothing of "
            "the session was settled",
        )
    return frozenset(status.ts_code for status in facts.held_status if status.status == "delisted")


def bind_paper_book(
    directory: Path, *, config_id: str, config_digest: str, epoch_session: date
) -> None:
    """Write the paper book's marker `<directory>/registration.json` -- the registration's
    `config_id`, the book's `survivor_config_digest` and the epoch -- or find it equal.

    `paper_head` calls it when it opens a book, after the epoch check and before `open_book`;
    the daily command does not call it. The same binding again writes nothing; another one, or a
    marker that cannot be read, is refused by name (`SurvivorDayError`, step `paper head`) and
    left as it is. The marker reaches its name whole or not at all: written to a file of this
    call's own beside it, synced, then hard-linked to its name, which fails rather than
    overwrites when another run got there first; the directory is synced after the link. A
    filesystem that cannot do that (no hard links: FAT, exFAT, many network shares; or a
    directory this account cannot write) is refused by name.
    """
    if not config_id:
        raise ValueError("config_id names the registration and cannot be empty")
    binding = _binding(config_id=config_id, config_digest=config_digest, epoch=epoch_session)
    marker = directory / PAPER_BOOK_MARKER
    text = json.dumps(binding, indent=2, sort_keys=True) + "\n"
    if not marker.exists() and _link_once(marker, text):
        return
    _refuse_another_binding(marker, binding)


def _binding(*, config_id: str, config_digest: str, epoch: date) -> dict[str, str]:
    return {
        "schema_version": PAPER_BINDING_SCHEMA,
        "config_id": config_id,
        "config_digest": config_digest,
        "epoch_session": epoch.isoformat(),
    }


def _link_once(marker: Path, text: str) -> bool:
    """Write `text` beside `marker` and link it to `marker`'s name: `True` when this call wrote
    it, `False` when another run had already.

    The file beside it is this call's own (`mkstemp`), never a name another writer could open
    too: a shared one, truncated by a second writer after the first had linked it, would empty
    the marker through its other name."""
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=marker.parent, prefix=f"{marker.name}.", suffix=".partial"
        )
    except OSError as error:
        raise _unwritable_marker(marker, error) from error
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
        if _SYNC_DIRECTORIES:
            synced = os.open(marker.parent, os.O_RDONLY)
            try:
                os.fsync(synced)
            finally:
                os.close(synced)
        return True
    except OSError as error:
        raise _unwritable_marker(marker, error) from error
    finally:
        partial.unlink(missing_ok=True)


def _unwritable_marker(marker: Path, error: OSError) -> SurvivorDayError:
    return SurvivorDayError(
        "paper head",
        f"the paper book's marker {marker} cannot be written ({error.strerror or error}; errno "
        f"{error.errno}): the paper directory must be writable by this account, on a local "
        "filesystem that supports hard links -- not FAT or exFAT, and not a network share -- "
        "because the marker is linked into place so that it is whole or absent. Move the runtime "
        "directory to such a filesystem and run again; no book was opened",
    )


def require_bound(
    *,
    state_store: PaperStateDocumentStore,
    paper_directory: Path,
    registration: SurvivorRegistration,
    config_id: str,
    lock: PaperBookLock,
) -> PaperBookSnapshot | None:
    """The paper book's head, read only once its binding to this registration is checked: what
    the daily command reads to pin the day's holdings, before any request (`V2-P7-008`).

    Reads and writes nothing else. A held book needs its marker present and equal to this
    registration's binding (`config_id`, the book's digest, the epoch); a marker that exists and
    differs is refused even when no book is held under it -- it is another registration's
    paper directory either way; no book and no marker is a book `paper_head` has yet to open,
    and `None` is returned. Every refusal is a `SurvivorDayError`, step `paper head`, with
    nothing written: the same checks `paper_head` makes before it opens or writes anything, so
    a day refused here would have been refused there, after its requests."""
    _require_lock(lock, "paper head", directory=paper_directory)
    binding = _binding(
        config_id=config_id,
        config_digest=registration.digest,
        epoch=registration.adapter.epoch_session,
    )
    with _refusals("paper head", "the paper book's head, read to pin the day's holdings"):
        head = held_head(state_store)
    marker = paper_directory / PAPER_BOOK_MARKER
    if head is not None:
        _require_binding(marker, binding)
    elif marker.exists():
        _refuse_another_binding(marker, binding)
    return head


def _require_binding(marker: Path, binding: dict[str, str]) -> None:
    """Refuse a held book whose marker is missing, cannot be read, or is not `binding`."""
    if not marker.exists():
        raise SurvivorDayError(
            "paper head",
            f"a paper book is held in this runtime directory with no binding marker: {marker} is "
            "missing. The marker is what tells this book's registration from another's, so a run "
            "never writes it over a held book: restore it from the backup the book's snapshots "
            "and ledger come from. Nothing was written",
        )
    _refuse_another_binding(marker, binding)


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
def paper_book_lock(directory: Path) -> Iterator[PaperBookLock]:
    """Hold the paper book's writer lock for the body, as the token the book's functions require;
    or refuse at once.

    `<directory>/writer.lock`, exclusive and advisory, taken without waiting: a second holder --
    another run, in this process or another -- is refused (`PaperBookBusyError`) before anything
    of the book is read or written. The kernel drops the lock with its holder however the holder
    ends, so a crashed run never wedges the book. See the module docstring for what it must
    cover, and for the local-filesystem and `fork` caveats.
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
            raise PaperBookBusyError(
                "paper head",
                f"another run holds the paper book's writer lock {path}: one writer per paper "
                "book. Let it finish -- a holder that crashed has released it already -- and run "
                "again. Nothing of the book was read or written",
            )
        token = PaperBookLock(directory, path, descriptor)
        try:
            yield token
        finally:
            token._let_go()
            _unlock(descriptor)
    finally:
        os.close(descriptor)


def paper_head(
    *,
    state_store: PaperStateDocumentStore,
    registration: SurvivorRegistration,
    ordinals: SessionOrdinals,
    session: date,
    paper_directory: Path,
    config_id: str,
    lock: PaperBookLock,
) -> PaperHead:
    """Bind and open the paper book -- on every run -- and return its head as `session`'s run
    finds it.

    `paper_directory` is the runtime directory's paper directory (its marker's and its lock's),
    `config_id` the registration's (`grid.config_id` of the whole registered configuration), and
    `lock` this run's token for that directory's writer lock. `ordinals` must count from the
    registration's epoch. A store with no book: the epoch bound, then the marker written or found
    equal, then `open_book`. A store holding a book: the marker must be there and equal, then
    `open_book` with the epoch, the book's capital and its digest -- the same opening writes
    nothing, another is refused by name. Every refusal is `SurvivorDayError`, step `paper head`,
    with nothing written.
    """
    _require_lock(lock, "paper head", directory=paper_directory)
    adapter = registration.adapter
    epoch = adapter.epoch_session
    _require_registered_epoch(ordinals, registration, step="paper head")
    binding = _binding(config_id=config_id, config_digest=registration.digest, epoch=epoch)
    with _refusals("paper head", f"the paper book's head for {session.isoformat()}"):
        index = ordinals.index_of(session)
        opening = held_head(state_store) is None
    if opening:
        bound = 1 + adapter.max_panel_catch_up_sessions
        if index > bound:
            raise SurvivorDayError(
                "paper head",
                f"the paper book opens on its epoch {epoch.isoformat()}, {index} sessions before "
                f"this run's {session.isoformat()}, and an epoch may be at most {bound} sessions "
                "before the first run (1 + max_panel_catch_up_sessions): the sessions between "
                "were never caught up by the panel step. Register the epoch as the session "
                "before the first scheduled run. Nothing was written",
            )
        bind_paper_book(
            paper_directory,
            config_id=config_id,
            config_digest=registration.digest,
            epoch_session=epoch,
        )
    else:
        _require_binding(paper_directory / PAPER_BOOK_MARKER, binding)
    with _refusals("paper head", f"the paper book's head for {session.isoformat()}"):
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
    given -- the caller's step 6.

    `transform`: see the module docstring (the daily command passes the one object it resolved;
    `None` resolves the same). A refusal of the adapter is a `SurvivorDayError`, step `facts`;
    the held statuses are not applied here (`delisted_or_refuse`).
    """
    return _read_facts(
        panel=panel,
        registration=registration,
        session=session,
        as_of=as_of,
        held=held,
        min_coverage=min_coverage,
        transform=transform,
        step="facts",
    )


def _read_facts(
    *,
    panel: PanelStore,
    registration: SurvivorRegistration,
    session: date,
    as_of: datetime,
    held: Collection[str],
    min_coverage: float | None,
    transform: FactorTransformSpec | None,
    step: str,
) -> FactsDay:
    adapter = registration.adapter
    with _refusals(step, f"the facts day of {session.isoformat()}"):
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
    lock: PaperBookLock,
    on_settled: Callable[[CatchUpResult], None] | None = None,
    transform: FactorTransformSpec | None = None,
) -> tuple[CatchUpResult, ...]:
    """Settle, oldest first and without a decision, every session the book is missing through
    ordinal `through_index` -- `missing_sessions`' answer -- and return what each did.

    Each is settled from the book the one before it left: the facts day at `as_of` with that
    book's holdings and no coverage floor, and its delisted set (`delisted_or_refuse`), when the
    book holds something, and neither when it holds nothing; the session's market for the held
    names and the live orders' names; then `run_catch_up`, observed on `as_of`'s Shanghai date.
    Each session is its own transaction, and `on_settled` is called with it as soon as it has
    committed -- a fault of `on_settled` reaches the caller as itself and undoes nothing. A
    refusal stops the catch-up at a session: `CatchUpStalledError`, step `paper book`, carrying
    the sessions settled before it. A session already held is not missing, so it is never caught
    up. `lock` is this run's writer-lock token; `transform` as `facts_day`'s.
    """
    observed_on = _observed_on(as_of)
    _require_lock(lock, "paper book")
    _require_registered_epoch(ordinals, registration, step="paper book")
    results: list[CatchUpResult] = []
    try:
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
    except SurvivorDayError as error:
        raise _stalled(error, at="its start", caught_up=()) from _root(error)
    for index in missing:
        try:
            with _refusals("paper book", f"the catch-up of session index {index}"):
                day = ordinals.day_of(index)
            result = _settle_without_a_decision(
                panel=panel,
                state_store=state_store,
                ledger=ledger,
                registration=registration,
                calendar=calendar,
                before=head,
                session=day,
                session_index=index,
                as_of=as_of,
                observed_on=observed_on,
                make_portfolio=make_portfolio,
                transform=transform,
            )
        except SurvivorDayError as error:
            raise _stalled(error, at=f"session index {index}", caught_up=tuple(results)) from _root(
                error
            )
        results.append(result)
        head = result.snapshot
        if on_settled is not None:
            on_settled(result)
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
    lock: PaperBookLock,
    on_settled: Callable[[CatchUpResult], None] | None = None,
    transform: FactorTransformSpec | None = None,
) -> tuple[CatchUpResult, ...]:
    """`session` settled without a decision: `catch_up` through `session` itself when it has no
    snapshot yet, and -- when it is held already -- `session` derived again with `run_catch_up`.

    The caller's choice for a session whose run's journal-pinned `as_of` is at or after 09:15 of
    the session after it (decision 2): no decision may be made any more. Run again on a session it
    settled itself -- the run committed and stopped before its journal -- it derives the session
    again from the book before it and its market, which C3 answers `unchanged`, writing nothing,
    and reports through `on_settled` like a session just settled. A session an evening settled
    with orders is refused by C3 by name (it was settled by an evening): it is finished by
    `evening` with its pinned inputs, and the caller chooses between the two by the journal's
    `as_of`, never by the clock. The epoch is refused: no evening or catch-up settles it. Every
    refusal is step `paper book`, and one of the settlement itself is a `CatchUpStalledError`.
    """
    observed_on = _observed_on(as_of)
    _require_lock(lock, "paper book")
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
    if head.session_index < index:
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
            lock=lock,
            on_settled=on_settled,
            transform=transform,
        )
    try:
        with _refusals("paper book", f"settling {session.isoformat()} only, settled already"):
            before = load_snapshot(state_store, ordinals.day_of(index - 1))
        result = _settle_without_a_decision(
            panel=panel,
            state_store=state_store,
            ledger=ledger,
            registration=registration,
            calendar=calendar,
            before=before,
            session=session,
            session_index=index,
            as_of=as_of,
            observed_on=observed_on,
            make_portfolio=make_portfolio,
            transform=transform,
        )
    except SurvivorDayError as error:
        raise _stalled(error, at=f"session index {index}", caught_up=()) from _root(error)
    if on_settled is not None:
        on_settled(result)
    return (result,)


def catch_up_again(
    *,
    panel: PanelStore,
    state_store: PaperStateDocumentStore,
    ledger: ReadableLedger,
    registration: SurvivorRegistration,
    ordinals: SessionOrdinals,
    calendar: TradingCalendar,
    indices: Sequence[int],
    as_of: datetime,
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    lock: PaperBookLock,
    on_settled: Callable[[CatchUpResult], None] | None = None,
    transform: FactorTransformSpec | None = None,
) -> tuple[CatchUpResult, ...]:
    """Derive again, oldest first, the sessions of ordinals `indices` a catch-up settled, and
    report each through `on_settled` like a session just settled -- C3's `run_catch_up` on a held
    session answers `unchanged`, writing nothing, with its settlement derived again from the book
    of the session before and the session's market, at `as_of`, as `catch_up` settled it.

    The caller's remedy for a catch-up committed and never journalled (D5 joint review, Important
    3): its run stopped between a session's commit and its journal entry, and the run after it
    finds the session held -- not missing, so `catch_up` never settles it again -- and its fills
    and write-offs in no document. `settle_only` does the same for its own session.

    A session of `indices` an evening decided -- an order planned on it is pending in its
    snapshot, or its first decision is its own -- is passed over: it is its own day's to document,
    and C3 refuses it as a catch-up wherever the two differ (an evening that planned nothing over
    an earlier first decision is the same snapshot either way, and is derived again). A session
    with no snapshot is refused: it is `catch_up`'s to settle, never a derivation's. Every
    refusal is step `paper book`, a `CatchUpStalledError` carrying the sessions derived before it.
    `lock` and `transform` as `catch_up`'s.
    """
    observed_on = _observed_on(as_of)
    _require_lock(lock, "paper book")
    _require_registered_epoch(ordinals, registration, step="paper book")
    results: list[CatchUpResult] = []
    try:
        with _refusals("paper book", "the paper book's catch-up derived again"):
            head = held_head(state_store)
    except SurvivorDayError as error:
        raise _stalled(error, at="its start", caught_up=()) from _root(error)
    for index in sorted(set(indices)):
        try:
            with _refusals("paper book", f"deriving session index {index} again"):
                day = ordinals.day_of(index)
                if index == 0:
                    raise SurvivorDayError(
                        "paper book",
                        f"{day.isoformat()} is the paper book's epoch, which it is opened on and "
                        "no catch-up settles",
                    )
                # The history grows only at its head: every session up to it is held.
                if head is None or index > head.session_index:
                    raise SurvivorDayError(
                        "paper book",
                        f"{day.isoformat()} (session index {index}) is not settled: a session "
                        "with no snapshot is the catch-up's to settle, never derived again",
                    )
                held = load_snapshot(state_store, day)
                if decided_by_an_evening(held):
                    continue
                before = load_snapshot(state_store, ordinals.day_of(index - 1))
            result = _settle_without_a_decision(
                panel=panel,
                state_store=state_store,
                ledger=ledger,
                registration=registration,
                calendar=calendar,
                before=before,
                session=day,
                session_index=index,
                as_of=as_of,
                observed_on=observed_on,
                make_portfolio=make_portfolio,
                transform=transform,
            )
        except SurvivorDayError as error:
            raise _stalled(error, at=f"session index {index}", caught_up=tuple(results)) from _root(
                error
            )
        results.append(result)
        if on_settled is not None:
            on_settled(result)
    return tuple(results)


def decided_by_an_evening(snapshot: PaperBookSnapshot) -> bool:
    """Whether `snapshot`'s session was decided by an evening: an order planned on it is pending
    (a session's plan executes at the open after it, so all of it is pending at its close), or
    the book's first decision is the session's own. Where it is, C3 refuses to settle the session
    again as a catch-up -- `catch_up_again` passes it over, and the daily command refuses a day
    that would re-pin it settle-only (D5 joint review, Minor 5)."""
    index = snapshot.session_index
    return snapshot.first_decision_session == index or any(
        item.order.signal_session == index for item in snapshot.pending_orders
    )


def _settle_without_a_decision(
    *,
    panel: PanelStore,
    state_store: PaperStateDocumentStore,
    ledger: ReadableLedger,
    registration: SurvivorRegistration,
    calendar: TradingCalendar,
    before: PaperBookSnapshot,
    session: date,
    session_index: int,
    as_of: datetime,
    observed_on: date,
    make_portfolio: Callable[[PortfolioLedger], PaperPortfolio],
    transform: FactorTransformSpec | None,
) -> CatchUpResult:
    """`session` settled from `before`, the book of the session before it, with no decision."""
    held = held_codes(before)
    delisted: frozenset[str] = frozenset()
    if held:
        delisted = delisted_or_refuse(
            _read_facts(
                panel=panel,
                registration=registration,
                session=session,
                as_of=as_of,
                held=held,
                min_coverage=_NO_COVERAGE_FLOOR,
                transform=transform,
                step="paper book",
            )
        )
    named = f"the catch-up of {session.isoformat()} (session index {session_index})"
    with _refusals("paper book", named):
        market = settlement_market(
            panel,
            session=session,
            as_of=as_of,
            names=settlement_names(before),
            calendar=calendar,
            benchmark=registration.adapter.benchmark,
        )
        return run_catch_up(
            store=state_store,
            ledger=ledger,
            make_portfolio=make_portfolio,
            config=registration.book,
            session=session,
            session_index=session_index,
            market=market,
            delisted=delisted,
            observed_on=observed_on,
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
    lock: PaperBookLock,
    transform: FactorTransformSpec | None = None,
) -> SurvivorEvening:
    """Run `session`'s evening on the paper book: `run_evening`, always (see the module
    docstring); every refusal is step `paper book`.

    `facts` and `screen` are the caller's step 6, read with `held` -- the holdings it pinned
    before the catch-up -- at `as_of`. The sessions before `session` must be settled (`catch_up`
    first). When the book the session settles from holds other codes than `held`, the facts day
    is read again with them, with no coverage floor, and screened again, and the second screen
    must agree with `screen` on the eligible set and the ranked survivors. Then: the held statuses
    (`delisted_or_refuse`), the session's market for the names of that book, every close of the
    facts day as the entrants' prices, and `run_evening` observed on `as_of`'s Shanghai date.
    `lock` is this run's writer-lock token; `transform` as `facts_day`'s.
    """
    observed_on = _observed_on(as_of)
    pinned = _pinned(held)
    _require_lock(lock, "paper book")
    if facts.session != session or facts.held != pinned:
        raise SurvivorDayError(
            "paper book",
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
        again = _read_facts(
            panel=panel,
            registration=registration,
            session=session,
            as_of=as_of,
            held=holdings,
            min_coverage=_NO_COVERAGE_FLOOR,
            transform=transform,
            step="paper book",
        )
        with _refusals("paper book", f"the screen of {session.isoformat()} read again"):
            screened = survivor_screen(again.facts, registration.book)
        _refuse_another_screen(screen, screened, session=session)
        facts, screen = again, screened
    delisted = delisted_or_refuse(facts)
    with _refusals("paper book", f"the evening of {session.isoformat()}"):
        market = settlement_market(
            panel,
            session=session,
            as_of=as_of,
            names=settlement_names(before),
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


def _refuse_another_screen(given: ScreenResult, again: ScreenResult, *, session: date) -> None:
    """Refuse a second screen that disagrees with the caller's on the eligible set or on the
    ranked survivors: neither depends on `held`, and the prediction filed at step 7 stands on
    the first."""
    differs = []
    eligible = {verdict.ts_code for verdict in given.verdicts if verdict.eligible}
    eligible_again = {verdict.ts_code for verdict in again.verdicts if verdict.eligible}
    if eligible != eligible_again:
        differs.append(
            f"the eligible set (only in the caller's: {sorted(eligible - eligible_again)}; only "
            f"in the second read's: {sorted(eligible_again - eligible)})"
        )
    survivors = [name.ts_code for name in given.survivors]
    survivors_again = [name.ts_code for name in again.survivors]
    if survivors != survivors_again:
        differs.append(f"the survivors ({survivors} against {survivors_again})")
    if differs:
        raise SurvivorDayError(
            "paper book",
            f"the facts day of {session.isoformat()} read again with the holdings the catch-up "
            f"left screens otherwise than the caller's: {'; '.join(differs)}. Neither depends on "
            "the holdings, so the caller's screen is not the one of its own facts day; the "
            "evening is not run on either. Nothing was written",
        )


def _require_lock(lock: object, step: str, *, directory: Path | None = None) -> None:
    """Refuse a run that does not hold the paper book's writer lock, or holds another one's."""
    if not isinstance(lock, PaperBookLock) or not lock.held:
        raise SurvivorDayError(
            step,
            "this run does not hold the paper book's writer lock (given "
            f"{lock.path if isinstance(lock, PaperBookLock) else lock!r}, not held): take it with "
            "paper_book_lock(<paper directory>) before step 2 and hold it through the paper book. "
            "Nothing was read or written",
        )
    if directory is not None and lock.directory.resolve() != directory.resolve():
        raise SurvivorDayError(
            step,
            f"the writer lock held is {lock.path}, another paper directory's than {directory}: "
            "a run holds the lock of the book it runs. Nothing was read or written",
        )


def _stalled(
    error: SurvivorDayError, *, at: str, caught_up: tuple[CatchUpResult, ...]
) -> CatchUpStalledError:
    settled = [item.snapshot.session.isoformat() for item in caught_up]
    kept = (
        f"the {len(settled)} session(s) settled before it are kept: {settled}"
        if settled
        else "no session was settled by it"
    )
    return CatchUpStalledError(
        "paper book", f"the catch-up stopped at {at}; {kept}. {error}", caught_up=caught_up
    )


def _root(error: SurvivorDayError) -> BaseException:
    """The fault a refusal names, for the stall to be raised from: its cause, or itself."""
    return error.__cause__ if error.__cause__ is not None else error


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
            f"(PaperStateStoreError: {error}). A damaged snapshot is never read as an empty book. "
            "Restore the paper directory's snapshots and its ledger together, from one backup: "
            "snapshots restored alone disagree with the ledger's rows, and every session after "
            "them is refused (PaperLedgerConflictError)",
        ) from error
    except (PaperStateError, ValueError) as error:
        raise SurvivorDayError(
            step, f"{what} was refused ({type(error).__name__}): {error}"
        ) from error
