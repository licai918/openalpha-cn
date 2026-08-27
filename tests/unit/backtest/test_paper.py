"""A paper portfolio that could reach a broker is the defect (`V2-P5-004`).

The prohibition is not a comment and not an import-linter contract, and the second of those is a
measurement rather than a preference: **`openalpha_cn.backtest`'s own `__init__` already loads a
live HTTP client into the process.** Importing any module under `backtest/` runs that `__init__`,
which reaches `replay -> runtime -> agents -> models -> models/openai_compatible.py`, and that
module's line 11 is `from urllib.request import Request, urlopen`. Measured on a bare
interpreter: `import openalpha_cn.backtest.execution` leaves `_socket`, `ssl`, `http.client` and
`urllib.request` in `sys.modules`, where `import openalpha_cn` alone leaves none of them. So a
static claim about this module's import closure would be **false**, and no rearrangement of
`paper.py` could make it true.

What is left is the mechanism `tests/offline_guard.py` already proves works: a CPython audit hook
(PEP 578), refusing the events by which a process reaches outward. `paper.py` ships one in `src/`
rather than in `tests/`, scoped to the thread running the session so a paper book inside the
FastAPI process cannot refuse an unrelated request's traffic.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import os
import socket
import struct
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from openalpha_cn.backtest.execution import MarketBar
from openalpha_cn.backtest.multi_day import PortfolioBacktestStep, PortfolioTransition
from openalpha_cn.backtest.paper import (
    KNOWN_PAPER_LIMITATIONS,
    OUTWARD_AUDIT_EVENTS,
    PAPER_EXECUTION_VENUE,
    PaperLimitation,
    PaperPortfolio,
    PaperPortfolioReachedOutward,
    PaperSessionResult,
    refusing_outward_calls,
)
from openalpha_cn.backtest.portfolio import PortfolioOrder, PortfolioState

OPENING = PortfolioState(as_of=date(2026, 7, 23), cash=Decimal("100000.00"))
SESSION_DATE = date(2026, 7, 24)
OBSERVED = date(2026, 7, 24)


def flat(subject: str, trade_date: date, close: str) -> MarketBar:
    price = Decimal(close)
    return MarketBar(
        subject=subject,
        trade_date=trade_date,
        board="main",
        previous_close=Decimal("10.00"),
        open=price,
        high=price,
        low=price,
        close=price,
        suspended=False,
        is_st=False,
    )


def session(
    trade_date: date = SESSION_DATE,
    *,
    order_id: str = "paper-1",
    quantity: int = 1000,
) -> PortfolioBacktestStep:
    return PortfolioBacktestStep(
        trade_date=trade_date,
        bars=(flat("AAA.SZ", trade_date, "10.00"),),
        orders=(
            PortfolioOrder(order_id=order_id, subject="AAA.SZ", side="buy", quantity=quantity),
        ),
        benchmark_close=Decimal("100"),
    )


_AUDIT_TRAIL: list[str] = []
_RECORDING = [False]


def _record(event: str, args: object) -> None:
    if _RECORDING[0]:
        _AUDIT_TRAIL.append(event)


sys.addaudithook(_record)
"""Installed once, at module import, and gated on a flag -- because `sys` offers no removal.

Installing one *inside* a test would leave a hook appending every audit event in the process to
a list that nothing ever clears, for the rest of the session: unbounded memory and a dispatch on
every `open`, `import` and `sqlite3.connect` in the 3,000 tests that follow. That is what the
first draft of `test_a_real_session_over_a_real_sqlite_ledger_raises_none_of_the_refused_events`
did.
"""


@contextmanager
def recording_audit_events() -> Iterator[list[str]]:
    """Collect every audit event raised inside the block, and nothing outside it."""
    _AUDIT_TRAIL.clear()
    _RECORDING[0] = True
    try:
        yield _AUDIT_TRAIL
    finally:
        _RECORDING[0] = False


class RecordingLedger:
    """The smallest thing that satisfies `PortfolioLedger`: one `append`, kept in a list."""

    def __init__(self) -> None:
        self.appended: list[PortfolioTransition] = []

    def append(self, transition: PortfolioTransition) -> None:
        self.appended.append(transition)


class BrokerReachingLedger:
    """A caller-supplied ledger that tries to open a socket while the session is running.

    `PortfolioLedger` is a `Protocol`, so *anything* carrying `append` satisfies it and this
    module can never vet what it is handed. That is the hole the guard exists to close, and this
    class is the probe that proves it closed: the reach happens inside `advance`, from code
    `paper.py` never imported.
    """

    def __init__(self) -> None:
        self.attempts = 0

    def append(self, transition: PortfolioTransition) -> None:
        self.attempts += 1
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


# --- the prohibition ----------------------------------------------------------------------


def test_a_ledger_that_reaches_for_a_broker_is_refused_while_the_session_runs() -> None:
    """The sharp case, and the one no import graph can see.

    `lint-imports` reads static edges; this reach is made by an object handed in at run time,
    from a module `paper.py` has never heard of. The refusal names the event, so a reader learns
    *what* was attempted rather than only that something was.
    """
    ledger = BrokerReachingLedger()
    book = PaperPortfolio(ledger=ledger)

    with pytest.raises(PaperPortfolioReachedOutward, match=r"socket\.__new__"):
        book.advance(state=OPENING, session=session(), observed_on=OBSERVED)

    assert ledger.attempts == 1, "the ledger was reached, and refused inside its own append"


def test_every_refused_event_is_one_this_test_provokes_rather_than_a_name_in_reserve() -> None:
    """`OUTWARD_AUDIT_EVENTS` is held equal to the set this test actually fires.

    `tests/offline_guard.py`'s docstring makes the same argument for its family check -- a guard
    listing an event nothing raises is a guard whose coverage is unmeasured. Every entry below is
    provoked, and the equality at the end is what stops an entry being added without one.
    """
    provoked: set[str] = set()

    def refuse(make: object, event: str) -> None:
        with (
            pytest.raises(PaperPortfolioReachedOutward, match=event.replace(".", r"\.")),
            refusing_outward_calls(),
        ):
            make()  # type: ignore[operator]
        provoked.add(event)

    # A socket may not be created at all -- which covers the raw C class and the detach/re-wrap
    # escape `V2-P4-105` had to reach below the class graph for: measured, `_socket.socket(...)`
    # and `_socket.socket(..., fileno=fd)` both raise `socket.__new__`.
    refuse(lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM), "socket.__new__")

    # ...and one that already existed may not transmit. Built outside the block on purpose:
    # inside it, there would be nothing to send from.
    #
    # All three are `AF_UNIX`, and that is a measurement rather than convenience. Audit hooks
    # fire in installation order, `tests/offline_guard.py`'s is installed first (conftest import
    # beats this module's), and on `AF_INET` it raises `OfflineSuiteViolation` before this guard
    # is consulted at all -- observed, on the first run of this test. `AF_UNIX` is the family
    # that guard deliberately leaves alone, so it is the only family on which *this* guard's
    # answer is the observable one. Which is the difference between the two stated as a fixture:
    # a local socket is not the network, but a broker gateway on a unix socket is a broker.
    connectable = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    datagram = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        refuse(lambda: connectable.connect("/nonexistent.sock"), "socket.connect")
        refuse(lambda: datagram.sendto(b"x", "/nonexistent.sock"), "socket.sendto")
        refuse(lambda: datagram.sendmsg([b"x"], [], 0, "/nonexistent.sock"), "socket.sendmsg")
    finally:
        connectable.close()
        datagram.close()

    # A broker reached through a child process or a shared library is still a broker. Each of
    # these is refused *before* the syscall, which is why provoking them here is safe.
    refuse(lambda: subprocess.Popen(["/bin/echo", "x"]), "subprocess.Popen")
    refuse(lambda: os.posix_spawn("/bin/echo", ["/bin/echo"], os.environ), "os.posix_spawn")
    refuse(lambda: os.execv("/bin/echo", ["/bin/echo"]), "os.exec")
    # `find_library` is resolved *outside* `refuse`, and that is the whole of `V2-P5-058`. On
    # Linux it shells out -- `gcc`/`objdump`/`ld` -- so called inside the guarded block it was
    # `subprocess.Popen` that raised, the assertion matched the wrong event, and `ctypes.dlopen`
    # was never provoked at all. macOS resolves it through dyld without a child process, so this
    # test passed locally while proving nothing on the platform the `Dockerfile` ships.
    libc = ctypes.util.find_library("c")
    assert libc is not None, "this test provokes `ctypes.dlopen` by loading libc, and found none"

    refuse(lambda: ctypes.CDLL(libc), "ctypes.dlopen")

    assert provoked == set(OUTWARD_AUDIT_EVENTS)


@pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"},
    reason="the sockaddr_in layout below is written for BSD and Linux only",
)
def test_a_shared_library_loaded_before_the_session_opens_a_socket_the_guard_never_sees() -> None:
    """`ctypes.dlopen` in `OUTWARD_AUDIT_EVENTS` covers *loading*, and reads as covering ctypes.

    `V2-P5-033`. The two "started beforehand" rows of `KNOWN_PAPER_LIMITATIONS` both need
    something arranged before `advance()`: a socket already connected, or a child already
    spawned. This one needs only a **handle**. Every socket call -- create, connect, send --
    happens inside the session, through `libc` rather than through CPython's socket module, so
    not one of them raises an audit event and the guard is never consulted.

    Driven rather than declared, and asserted in the direction that hurts: **the bytes arrive**.
    A test that asserted a refusal would be describing a guarantee this module does not have,
    and one that merely called `libc.socket` and shrugged would go green on a platform where
    the call failed for an unrelated reason. The listener is in this process on `127.0.0.1`,
    so nothing leaves the machine -- and note that this escape is invisible to
    `tests/offline_guard.py` for the identical reason, which is a limit that guard declares.

    The day an in-process mechanism closes this, the assertion below is what goes red.
    """
    library = ctypes.util.find_library("c")
    assert library is not None, "no libc to load; this test cannot measure what it is about"
    libc = ctypes.CDLL(library, use_errno=True)

    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5.0)
        host, port = listener.getsockname()
        packed = socket.inet_aton(host)
        raw = (
            (
                struct.pack("BB", 16, socket.AF_INET)
                if sys.platform == "darwin"
                else struct.pack("=H", socket.AF_INET)
            )
            + struct.pack("!H", port)
            + packed
            + b"\x00" * 8
        )
        address = (ctypes.c_char * len(raw)).from_buffer_copy(raw)

        with refusing_outward_calls():
            descriptor = libc.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
            assert descriptor >= 0, f"libc.socket failed, errno {ctypes.get_errno()}"
            try:
                assert libc.connect(descriptor, address, len(raw)) == 0, (
                    f"libc.connect failed, errno {ctypes.get_errno()}"
                )
                sent = libc.send(descriptor, b"BROKER-ORDER", 12, 0)
            finally:
                libc.close(descriptor)

        accepted, _ = listener.accept()
        with contextlib.closing(accepted):
            accepted.settimeout(5.0)
            delivered = accepted.recv(64)

    assert sent == 12
    assert delivered == b"BROKER-ORDER", (
        "the guard now sees a raw syscall through a pre-loaded library; if that is deliberate, "
        "delete KNOWN_PAPER_LIMITATIONS"
        ".a_shared_library_loaded_before_the_session_opens_its_own_socket with it"
    )


def test_the_guard_stops_refusing_once_the_session_unwinds() -> None:
    """A guard that never releases is indistinguishable from one that broke the process.

    The socket below is created and closed and connects to nothing, so the suite's own offline
    guarantee is untouched -- `tests/offline_guard.py` refuses `connect`/`sendto`/`sendmsg`, not
    construction.
    """
    with pytest.raises(PaperPortfolioReachedOutward), refusing_outward_calls():
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    released = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    released.close()


def test_the_guard_binds_the_thread_that_runs_the_session_and_not_the_process() -> None:
    """The reason the depth is thread-local rather than global, as a measurement.

    `tests/offline_guard.py` keeps a process-wide `_depth` and is right to: it guards a whole
    test. This guard runs inside a shipped library, and `create_app` serves handlers from a
    thread pool -- so a process-wide flag would mean a paper session in one request refusing a
    provider fetch in another. That is the defect this test exists to keep closed.
    """
    other_thread_succeeded = threading.Event()
    inside_the_session = threading.Event()
    release = threading.Event()

    def elsewhere() -> None:
        inside_the_session.wait(timeout=5)
        spare = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        spare.close()
        other_thread_succeeded.set()
        release.set()

    worker = threading.Thread(target=elsewhere)
    worker.start()
    try:
        with refusing_outward_calls():
            inside_the_session.set()
            assert release.wait(timeout=5), "the other thread never finished"
            with pytest.raises(PaperPortfolioReachedOutward):
                socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    finally:
        worker.join(timeout=5)

    assert other_thread_succeeded.is_set()


def test_a_real_session_over_a_real_sqlite_ledger_raises_none_of_the_refused_events(
    tmp_path: Path,
) -> None:
    """The ban costs the legitimate path nothing, and that is measured rather than hoped.

    A whole session against a real `SQLitePortfolioLedger` raises exactly two audit events,
    `sqlite3.connect` and `sqlite3.connect/handle`, and neither is on the list. If a future
    dependency starts spawning a helper process during a session, this test is what says so.
    """
    from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

    book = PaperPortfolio(ledger=SQLitePortfolioLedger(tmp_path / "paper.sqlite3"))

    with recording_audit_events() as seen:
        result = book.advance(state=OPENING, session=session(), observed_on=OBSERVED)

    assert result.transitions[0].status == "filled"
    assert not set(seen) & set(OUTWARD_AUDIT_EVENTS)
    assert "sqlite3.connect" in seen


def test_the_result_declares_that_no_broker_was_contacted_as_a_validated_field() -> None:
    """`PortfolioConstruction.method`'s idiom from `V2-P5-001`, applied to the prohibition.

    A `Literal` rather than prose, so a build that stopped saying it would not validate, and so
    the sentence rides on the record every face prints rather than living in a docstring.
    """
    result = PaperPortfolio(ledger=RecordingLedger()).advance(
        state=OPENING, session=session(), observed_on=OBSERVED
    )

    payload = result.model_dump(mode="json", exclude_computed_fields=True)

    assert result.execution_venue == PAPER_EXECUTION_VENUE
    assert payload["execution_venue"] == PAPER_EXECUTION_VENUE
    assert PaperSessionResult.model_validate(payload) == result, (
        "the round trip has to succeed, or the refusal below would prove nothing about the "
        "venue and everything about the payload"
    )
    with pytest.raises(ValidationError) as refusal:
        PaperSessionResult.model_validate(payload | {"execution_venue": "live"})
    assert refusal.value.error_count() == 1
    assert refusal.value.errors()[0]["loc"] == ("execution_venue",)


# --- what makes it forward, and paper ------------------------------------------------------


def test_a_paper_book_cannot_be_built_without_a_ledger() -> None:
    """`PortfolioBacktestRunner` takes an optional ledger; a paper book requires one.

    A backtest that records nothing is still a backtest -- the report is the answer. A paper
    trade nobody wrote down is not a paper trade, so the argument has no default and the
    difference is in the signature rather than in a docstring.
    """
    with pytest.raises(TypeError, match="ledger"):
        PaperPortfolio()  # type: ignore[call-arg]


def test_a_session_dated_after_what_has_been_observed_is_refused() -> None:
    """Forward simulation runs on sessions that have happened, which is what separates it from
    a backtest of imagined prices. `observed_on` is a **declaration** the caller makes -- like
    `PortfolioOrder.target_weight`, this module cannot verify it -- and it is required rather
    than defaulted so that nobody declares it by accident."""
    book = PaperPortfolio(ledger=RecordingLedger())

    with pytest.raises(ValueError, match="has not been observed"):
        book.advance(
            state=OPENING,
            session=session(date(2026, 7, 28)),
            observed_on=date(2026, 7, 24),
        )


def test_a_session_that_does_not_move_the_book_forward_is_refused() -> None:
    """Strictly forward, where the backtest runner allows its first step to land *on* the
    opening state's date. Re-advancing a book through a session it has already lived through
    would double every fill on it."""
    book = PaperPortfolio(ledger=RecordingLedger())

    with pytest.raises(ValueError, match="forward"):
        book.advance(
            state=OPENING.model_copy(update={"as_of": SESSION_DATE}),
            session=session(SESSION_DATE),
            observed_on=OBSERVED,
        )


def test_advancing_the_book_records_every_order_and_returns_the_state_it_reached() -> None:
    ledger = RecordingLedger()
    book = PaperPortfolio(ledger=ledger)

    first = book.advance(state=OPENING, session=session(), observed_on=OBSERVED)
    second = book.advance(
        state=first.after,
        session=PortfolioBacktestStep(
            trade_date=date(2026, 7, 27),
            bars=(flat("AAA.SZ", date(2026, 7, 27), "12.00"),),
            orders=(),
            benchmark_close=Decimal("101"),
        ),
        observed_on=date(2026, 7, 27),
    )

    assert first.before == OPENING
    assert first.after.position("AAA.SZ").quantity == 1000
    assert [transition.order.order_id for transition in ledger.appended] == ["paper-1"]
    assert second.transitions == ()
    assert second.after.mark("AAA.SZ") == Decimal("12.00")
    assert second.equity > first.equity
    assert second.trade_date == "2026-07-27"


def test_replaying_a_session_the_ledger_already_holds_hits_its_conflict_guard(
    tmp_path: Path,
) -> None:
    """A paper book cannot quietly re-write a session it has already lived.

    `SQLitePortfolioLedger.append` compares payloads **by bytes**, so re-advancing the same
    session with the same orders is idempotent, and re-advancing it with a *different* fill on
    the same `order_id` raises. That is the durable half of "forward only": the in-memory rule
    above refuses a backwards date, and this refuses a rewritten past.
    """
    from openalpha_cn.storage.portfolio import SQLitePortfolioLedger

    ledger = SQLitePortfolioLedger(tmp_path / "paper.sqlite3")
    book = PaperPortfolio(ledger=ledger)
    book.advance(state=OPENING, session=session(), observed_on=OBSERVED)

    book.advance(state=OPENING, session=session(), observed_on=OBSERVED)
    assert len(ledger.list()) == 1

    with pytest.raises(ValueError, match="order_id conflicts"):
        book.advance(
            state=OPENING,
            session=session(quantity=500),
            observed_on=OBSERVED,
        )


def test_the_contracts_this_module_publishes_are_frozen_and_forbid_extra_keys() -> None:
    """A mutation sweep found both flags unheld: `frozen=True` on `PaperSessionResult` and
    `frozen=True, slots=True` on `PaperLimitation`. A mutable session result is a record a
    caller can edit after the fact -- including its `execution_venue` -- which is precisely the
    claim this row exists to make unfalsifiable."""
    assert PaperSessionResult.model_config.get("frozen") is True
    assert PaperSessionResult.model_config.get("extra") == "forbid"

    entry = KNOWN_PAPER_LIMITATIONS[0]
    with pytest.raises((AttributeError, TypeError)):
        entry.code = "rewritten"  # type: ignore[misc]
    with pytest.raises(TypeError):
        PaperLimitation("positional", "detail")  # type: ignore[call-arg]


def test_the_known_limitations_are_declared_and_uniquely_coded() -> None:
    codes = tuple(entry.code for entry in KNOWN_PAPER_LIMITATIONS)

    assert len(codes) == len(set(codes))
    assert set(codes) == {
        "a_descriptor_connected_before_the_session_is_not_seen",
        "a_child_process_started_before_the_session_is_not_seen",
        "a_shared_library_loaded_before_the_session_opens_its_own_socket",
        "work_handed_to_another_thread_leaves_the_guard_behind",
        "the_audit_hook_can_never_be_uninstalled",
        "the_ledger_is_structurally_typed_so_its_identity_is_the_callers",
        "this_module_fetches_no_prices_and_cannot_check_the_ones_it_is_given",
        "observed_on_is_declared_by_the_caller_and_verified_by_nobody",
    }
    assert all(entry.detail for entry in KNOWN_PAPER_LIMITATIONS)
