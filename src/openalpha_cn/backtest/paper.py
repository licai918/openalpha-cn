"""Paper Portfolio: forward simulation that cannot reach a broker (`V2-P5-004`).

## Why the prohibition is an audit hook and not an import contract

The obvious mechanism was a static one -- forbid this module's import closure to contain
anything that can open a connection -- and it was measured before it was designed, which is how
it stopped being the plan. **`openalpha_cn.backtest`'s own `__init__` already loads a live HTTP
client into the process.** Importing any module under `backtest/` executes that `__init__`, which
reaches `replay -> runtime -> agents -> models -> models/openai_compatible.py`, and line 11 of
that file is `from urllib.request import Request, urlopen`. Measured on a bare interpreter:

    import openalpha_cn                     -> sys.modules holds none of them
    import openalpha_cn.backtest.execution  -> _socket, ssl, http.client, urllib.request

So "this module reaches no networking module" is **false about every module in this package**,
and no arrangement of `paper.py` could make it true. `lint-imports` is not the tool either: it
reads a static graph, and the reach this row has to refuse is made by an object handed in at run
time -- a `PortfolioLedger` is a `Protocol`, so *anything* carrying `append` satisfies it and
nothing here can vet what it is given.

What is left is the mechanism `tests/offline_guard.py` already proves works, moved from `tests/`
into `src/`: a CPython audit hook (PEP 578) refusing the events by which a process reaches
outward. `V2-P4-105` is the reason it is events rather than shadowed method names -- a shadow on
`socket.socket` is walked around by `import _socket` in one line, and `_socket.socket` is an
immutable extension type that cannot be patched. Every event below is raised inside C code, so a
caller reaches it whichever class object it got hold of.

## What is refused, and why creation rather than only transmission

`socket.__new__` is the head of the list and it is the one that does most of the work: a session
that cannot **create** a socket has nothing to connect or send from. Measured, it covers all
three escapes `V2-P4-105` found -- `_socket.socket(...)`, `_socket.socket(..., fileno=fd)` over a
detached descriptor, and `socket.socketpair()`. The three transmit events remain for the socket
that already existed when the session began. `subprocess.Popen`, `os.posix_spawn`, `os.exec` and
`ctypes.dlopen` are here because a broker reached through a child process or a shared library is
still a broker; each was measured firing before it was listed, and
`tests/unit/backtest/test_paper.py::
test_every_refused_event_is_one_this_test_provokes_rather_than_a_name_in_reserve` provokes all
eight on every run so that none of them is a name in reserve.

**`ctypes.dlopen` covers loading and nothing else, and its presence in that list made ctypes
look covered.** A handle obtained *before* `advance()` calls `libc.socket`/`connect`/`send`
inside the session without raising a single audit event -- measured, twelve bytes delivered
over loopback from inside `refusing_outward_calls()`. That is wider than either of the two
"started beforehand" rows: it needs no connected descriptor and no child process, and every
socket call happens during the session. It is
`KNOWN_PAPER_LIMITATIONS.a_shared_library_loaded_before_the_session_opens_its_own_socket`, and
`test_a_shared_library_loaded_before_the_session_opens_a_socket_the_guard_never_sees` drives
it rather than describing it.

**The ban costs the legitimate path nothing, measured**: a whole session against a real
`SQLitePortfolioLedger` raises exactly `sqlite3.connect` and `sqlite3.connect/handle`, and
neither is on the list.

## The depth is thread-local, and that is the difference from `tests/offline_guard.py`

That guard keeps a process-wide `_depth`, correctly: it wraps a whole test, and a test owns its
process. This one runs inside a shipped library. `create_app` serves handlers out of a thread
pool, so a process-wide flag would mean a paper session in one request refusing a provider fetch
in another -- a paper book breaking live research. The depth therefore lives in a
`threading.local`, the hook reads the *calling* thread's depth (audit events are raised on the
thread performing the operation), and `advance` never yields inside its block, so nothing else
runs on that thread while it is held.

The cost of that choice is stated rather than hidden and is the first entry of
`KNOWN_PAPER_LIMITATIONS`' neighbourhood: work the session hands to another thread runs outside
the guard.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from openalpha_cn.backtest.multi_day import (
    CarriedMark,
    PortfolioBacktestRunner,
    PortfolioBacktestStep,
    PortfolioLedger,
)
from openalpha_cn.backtest.portfolio import (
    PortfolioLimits,
    PortfolioState,
    PortfolioTransition,
)

__all__ = [
    "KNOWN_PAPER_LIMITATIONS",
    "OUTWARD_AUDIT_EVENTS",
    "PAPER_EXECUTION_VENUE",
    "PaperLimitation",
    "PaperPortfolio",
    "PaperPortfolioReachedOutward",
    "PaperSessionResult",
    "refusing_outward_calls",
]

_SIX = Decimal("0.000001")

PAPER_EXECUTION_VENUE: Final[str] = "paper -- no broker was contacted"
"""The sentence every result carries, as a value rather than as prose.

`V2-P5-001`'s `PortfolioConstruction.method` is the precedent: a `Literal` field, so a build that
stopped saying it would not validate, and the claim rides on the record every face renders rather
than living in a docstring nobody renders.
"""


OUTWARD_AUDIT_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "socket.__new__",
        "socket.connect",
        "socket.sendto",
        "socket.sendmsg",
        "subprocess.Popen",
        "os.posix_spawn",
        "os.exec",
        "ctypes.dlopen",
    }
)
"""Every CPython audit event a paper session refuses. See this module's docstring for the
measurement behind each, and `test_every_refused_event_is_one_this_test_provokes_rather_than_a
_name_in_reserve` for the test that fires all eight rather than trusting the list.

Deliberately **family-blind**, where `tests/offline_guard.py` exempts `AF_UNIX`. That guard is
answering "did a test touch the network", and a local socket is not the network. This one is
answering "did a paper portfolio reach a broker", and a broker gateway on a unix socket is a
broker. Refusing creation outright is what makes the family question moot.
"""


REFUSAL_MESSAGE: Final[str] = (
    "A paper portfolio simulates forward and contacts nobody: it takes bars a caller already "
    "has, records transitions into the ledger it was given, and reaches nothing else. If this "
    "call is legitimate it belongs outside `PaperPortfolio.advance`, which is the whole of what "
    "this guard covers."
)
"""Named rather than inlined so the refusal states the way *out*. A guard that only says no is a
guard the next person deletes, which is the reasoning `REFUSAL_MESSAGE` in
`tests/offline_guard.py` records, and the same reason for it here."""


class PaperPortfolioReachedOutward(RuntimeError):
    """Something inside a running paper session tried to leave the process."""


_local = threading.local()
"""Per-thread nesting depth. Zero on a thread means the hook is inert there.

A depth rather than a flag so a nested `refusing_outward_calls()` does not switch the guard off
when the inner block closes, and thread-local rather than global for the reason in this module's
docstring.
"""


def _refuse(event: str, args: tuple[Any, ...]) -> None:
    """Refuse `OUTWARD_AUDIT_EVENTS` on a thread inside a paper session, and be invisible
    otherwise.

    Nothing is read out of `args`. `tests/offline_guard.py` reads `args[0].family` to leave
    `AF_UNIX` alone; this guard is family-blind on purpose, and the eight events do not share an
    argument shape anyway -- `ctypes.dlopen` passes a path, `os.exec` passes a path, `socket
    .__new__` passes a socket. Reading none of them is what lets one branch serve all eight.
    """
    if event not in OUTWARD_AUDIT_EVENTS or getattr(_local, "depth", 0) == 0:
        return
    raise PaperPortfolioReachedOutward(f"{event} from inside a paper session. {REFUSAL_MESSAGE}")


sys.addaudithook(_refuse)
"""Installed once, at import, and **it can never be removed** -- `sys` offers no way to.

That is the price of reaching below the class graph, and it is paid deliberately rather than
quietly (`KNOWN_PAPER_LIMITATIONS.the_audit_hook_can_never_be_uninstalled`). The compensation is
that it is inert on every thread that is not inside `advance`, so importing this module changes
what no other code can do.
"""


@contextmanager
def refusing_outward_calls() -> Iterator[None]:
    """Refuse `OUTWARD_AUDIT_EVENTS` on **this thread** for the duration of the block."""
    _local.depth = getattr(_local, "depth", 0) + 1
    try:
        yield
    finally:
        _local.depth -= 1


@dataclass(frozen=True, slots=True, kw_only=True)
class PaperLimitation:
    """One named boundary on what "never connects to a broker" is proved to mean here."""

    code: str
    detail: str


KNOWN_PAPER_LIMITATIONS: Final[tuple[PaperLimitation, ...]] = (
    PaperLimitation(
        code="a_descriptor_connected_before_the_session_is_not_seen",
        detail=(
            "The guard refuses creating a socket and refuses connect/sendto/sendmsg through "
            "the socket API. A raw os.write to a file descriptor that was already connected "
            "before advance() was entered raises no audit event at all -- CPython publishes "
            "none for os.write -- so it is outside this refusal. The reach it would take to "
            "arrange is a socket connected earlier and its fileno carried into the session, "
            "which is a deliberate act rather than an accident, and stating it is the honest "
            "form of a guarantee that otherwise sounds total."
        ),
    ),
    PaperLimitation(
        code="a_child_process_started_before_the_session_is_not_seen",
        detail=(
            "subprocess.Popen, os.posix_spawn and os.exec are refused during a session, so a "
            "broker CLI cannot be started from inside one. A worker started earlier and fed "
            "through an already-open pipe is file I/O to this guard, exactly as the descriptor "
            "case above. Both are the same boundary seen twice: the guard covers what a "
            "session *starts*, not what a process was already holding."
        ),
    ),
    PaperLimitation(
        code="a_shared_library_loaded_before_the_session_opens_its_own_socket",
        detail=(
            "ctypes.dlopen is refused during a session, so a broker library cannot be *loaded* "
            "from inside one -- and listing it alongside the socket events reads as though "
            "ctypes were covered. Only loading is. A handle obtained before advance() was "
            "entered calls libc.socket, libc.connect and libc.send inside the session, none of "
            "which passes through CPython's socket module and none of which raises any audit "
            "event, so a brand-new connection is opened and bytes leave the process unrefused. "
            "Measured, over loopback: 12 bytes delivered from inside refusing_outward_calls(). "
            "This is strictly wider than the descriptor and child-process rows above, which is "
            "why it is stated separately rather than folded into them: those two need something "
            "connected or spawned beforehand, and this one needs only a handle -- every socket "
            "call happens during the session. No in-process mechanism closes it, because an "
            "audit hook sees what CPython chooses to publish and a raw syscall through a "
            "loaded library publishes nothing; a seccomp-style boundary is a property of the "
            "process a caller starts, not of a library it imports."
        ),
    ),
    PaperLimitation(
        code="work_handed_to_another_thread_leaves_the_guard_behind",
        detail=(
            "The depth is thread-local, and that is a choice with a cost. Its benefit is "
            "measured in the test that pins it: a process-wide flag would let a paper session "
            "in one FastAPI request refuse a provider fetch in another, which is a paper book "
            "breaking live research. Its cost is that a session dispatching to a "
            "ThreadPoolExecutor runs that work unguarded. Nothing in advance() does so today "
            "-- it calls PortfolioBacktestRunner.run, which is straight-line arithmetic and "
            "one ledger append -- but a ledger the caller supplies could."
        ),
    ),
    PaperLimitation(
        code="the_audit_hook_can_never_be_uninstalled",
        detail=(
            "sys.addaudithook offers no removal, so importing this module installs a hook for "
            "the life of the process. It is inert on every thread not inside advance(), which "
            "is why the trade is worth making, but a process that imports openalpha_cn.backtest"
            ".paper cannot go back. tests/offline_guard.py pays the identical price for the "
            "identical reason and measured no runtime cost it could distinguish from noise."
        ),
    ),
    PaperLimitation(
        code="the_ledger_is_structurally_typed_so_its_identity_is_the_callers",
        detail=(
            "PortfolioLedger is a Protocol: anything with append(transition) satisfies it, and "
            "this module cannot check what it was handed. So the guarantee is not 'a broker "
            "client cannot be passed in' -- it is 'a broker client passed in cannot reach the "
            "broker while the session runs', which is what "
            "test_a_ledger_that_reaches_for_a_broker_is_refused_while_the_session_runs "
            "measures. What the caller does with that object outside advance() is the caller's."
        ),
    ),
    PaperLimitation(
        code="this_module_fetches_no_prices_and_cannot_check_the_ones_it_is_given",
        detail=(
            "A paper book that fetched its own bars would need a provider, which is the network "
            "this row exists to refuse. So bars arrive from the caller, and a caller feeding "
            "stale, adjusted-differently or invented closes gets a stale, differently-adjusted "
            "or invented book with no complaint from here. backtest/execution.py's "
            "KNOWN_EXECUTION_LIMITATIONS.the_registry_verdict_is_not_an_input is the same "
            "boundary one layer down and states the same defence: a caller filters its universe "
            "before it builds bars, and that is a discipline this contract cannot audit."
        ),
    ),
    PaperLimitation(
        code="observed_on_is_declared_by_the_caller_and_verified_by_nobody",
        detail=(
            "advance() refuses a session dated after observed_on, which is what makes it "
            "forward simulation rather than a backtest of prices that do not exist yet. "
            "observed_on is a declaration, in the sense PortfolioOrder.target_weight is: this "
            "module has no clock -- no module under backtest/ takes one, because a wall clock "
            "makes a study unreproducible -- so a caller passing a date in the future defeats "
            "the check. It is required rather than defaulted so that nobody declares it by "
            "accident, and it is echoed on the result so the declaration is on the record."
        ),
    ),
)


class PaperSessionResult(BaseModel):
    """What one forward session did to a paper book, and the venue it did not use."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_venue: Literal["paper -- no broker was contacted"] = (
        "paper -- no broker was contacted"
    )
    trade_date: str
    observed_on: str
    before: PortfolioState
    after: PortfolioState
    transitions: tuple[PortfolioTransition, ...]
    carried_marks: tuple[CarriedMark, ...]
    equity: Decimal
    gross_exposure: Decimal = Field(ge=0)


class PaperPortfolio:
    """A book advanced one real session at a time, recording into a ledger and reaching nobody.

    Built on `PortfolioBacktestRunner` rather than beside it, which is what the roadmap row means
    by reusing the immutable order/transition accounting: a paper session **is** one session of
    the multi-day runner, so cash, T+1, FIFO, fees and every clamp are the same code that a
    backtest and `POST /api/v1/portfolio/execute` already run. What this class adds is the
    prohibition, and two rules that separate paper from backtest:

    1. **The ledger is required.** `PortfolioBacktestRunner` takes an optional one and is right
       to -- a backtest's answer is its report. A paper trade nobody wrote down is not a paper
       trade, so the argument here has no default.
    2. **A session must move the book strictly forward and must already have been observed.**
       The runner lets its first step land *on* the opening state's date; re-advancing a paper
       book through a session it has lived would double every fill on it.
    """

    def __init__(
        self,
        *,
        ledger: PortfolioLedger,
        limits: PortfolioLimits | None = None,
    ) -> None:
        self.ledger = ledger
        self._runner = PortfolioBacktestRunner(limits=limits, ledger=ledger)

    def advance(
        self,
        *,
        state: PortfolioState,
        session: PortfolioBacktestStep,
        observed_on: date,
    ) -> PaperSessionResult:
        """Live one observed session forward, recording every order, contacting nobody.

        The whole body runs inside `refusing_outward_calls()`, so the refusal covers the ledger
        the caller supplied as well as this module -- which is the only place it *can* be
        covered, because a `PortfolioLedger` is structurally typed and could be anything.
        """
        if session.trade_date <= state.as_of:
            raise ValueError(
                f"paper session {session.trade_date.isoformat()} must move the book forward "
                f"from {state.as_of.isoformat()}"
            )
        if session.trade_date > observed_on:
            raise ValueError(
                f"paper session {session.trade_date.isoformat()} has not been observed as of "
                f"{observed_on.isoformat()}"
            )
        with refusing_outward_calls():
            report = self._runner.run(initial=state, steps=(session,))
        point = report.equity_curve[0]
        return PaperSessionResult(
            trade_date=point.trade_date,
            observed_on=observed_on.isoformat(),
            before=report.initial_state,
            after=report.final_state,
            transitions=report.transitions,
            carried_marks=report.carried_marks,
            equity=point.equity,
            gross_exposure=point.gross_exposure.quantize(_SIX),
        )
