"""The offline guarantee: what it refuses, through which mechanism, and the limits it declares.

A module of its own rather than two lines inside `tests/conftest.py`, because the test that
proves the guard is live (`tests/unit/test_offline_suite.py`) has to import the exception it
raises, and `import conftest` is ambiguous the moment a second `conftest.py` exists anywhere
on the collection path: pytest imports each one under its own basename, so which module that
name resolves to depends on collection order. `tests/e2e/conftest.py` made that concrete --
the import returned the e2e conftest and the unit test failed to collect. `tests/panel_fixtures
.py` is the precedent for a plain shared module at the `tests/` root, and this follows it.

`V2-P4-039` moved the patching itself here too, and that is a second reason of the same kind.
While it lived inside an autouse fixture there was no moment at which any test could look at
the guard **off**, so "the restoration leaves the process exactly as it was found" was a
`finally` block nothing could observe -- and a mutation that skipped the teardown outright left
the whole suite green. `tests/import_linter_containment.py::raw_lint_imports_disables` is the
same problem answered the same way one directory over.
"""

from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Final

GUARDED_SOCKET_FAMILIES: Final[frozenset[int]] = frozenset(
    {int(socket.AF_INET), int(socket.AF_INET6)}
)
"""The two families that reach the network. Everything else -- `AF_UNIX` above all -- is left
alone: a local socket is not the network, and refusing one would be a guess about what a future
test needs rather than a rule about what this one forbids.

The family check does real work rather than standing in reserve, and that is measurable: the
audit events below fire on `AF_UNIX` sockets too (measured -- `socket.connect` with
`('/nope/nope.sock',)`, `socket.sendto` likewise), so this frozenset is the whole of what keeps
a local socket out of the refusal."""

GUARDED_AUDIT_EVENTS: Final[frozenset[str]] = frozenset(
    {"socket.connect", "socket.sendto", "socket.sendmsg"}
)
"""The CPython audit events (PEP 578) this guard refuses, and the reason it is events at all.

`V2-P4-105` is why this is no longer a list of method names. Until that issue the guard
*shadowed* four names on `socket.socket` -- and `socket.socket` is the Python wrapper class,
which inherits every one of them from the C `_socket.socket` and defines none of its own. The
base class is untouched by any shadow, `import _socket` is one line, and the technical
acceptance walked out of the guard three ways from inside a non-e2e test (loopback only):

  - `_socket.socket(...).connect(...)` then `.sendall(...)` -- delivered `b'ESCAPED-TCP'`,
  - `_socket.socket(...).sendto(...)` -- returned 11, and the listener received `b'ESCAPED-UDP'`,
  - and the sharpest, needing no import of a fresh class at all: take a **guarded** socket's own
    file descriptor with `detach()`, re-wrap it with `_socket.socket(..., fileno=fd)`, and it
    delivered `b'ESCAPED-DETACH'`.

The row's preferred repair was to widen the shadow onto `_socket.socket` itself, and that was
measured and **cannot be done**: `_socket.socket` is an immutable extension type, and
`setattr(_socket.socket, "connect", ...)` raises `TypeError: cannot set 'connect' attribute of
immutable type '_socket.socket'`. No arrangement of names closes this, because the C class is
reachable by too many spellings that are not names the guard could hold -- `import _socket`,
`socket.socket.__bases__[0]`, `socket.socket.__mro__[1]`, `type(sock).__mro__[1]`.

So the guard moved **below** the class graph instead of widening across it. Every one of these
events is raised inside `_socket`'s own C implementation, so a caller reaches them whichever
class object it got hold of, and all three escapes above are refused by the same three lines.
Narrowing the *claim* to "outbound TCP through the `socket` wrapper" remains the other repair
and remains declined, for `V2-P4-039`'s reason: it makes the sentence true by making the
guarantee smaller, which is the direction every Critical this project has booked already went.

**Three events, not four, and that is a measurement rather than an omission.** CPython raises
`socket.connect` for `connect_ex` as well as for `connect` -- there is no `socket.connect_ex`
event -- so the two entry points `V2-P4-039` wrapped separately arrive here as one name, and a
refusal provoked by `connect_ex` reports itself as `connect`.

**These three are the whole outbound surface, and that is an argument rather than a list.**
`send`, `sendall` and `socket.sendfile` raise no audit event, and they do not need to: all three
require a *connected* socket, and the only way to connect one on a guarded family is `connect`
or `connect_ex`, which raise. `sendto` and `sendmsg` are the only two that transmit without
connecting. `tests/unit/test_offline_suite.py::
test_the_unaudited_sends_are_unreachable_because_connecting_is_what_is_refused` drives that
closure over the raw C class rather than asserting it: it connects, is refused, and then watches
`sendall` fail as an unconnected socket fails while the loopback listener receives nothing.
"""

UNGUARDED_RESOLUTION_EVENT: Final[str] = "socket.getaddrinfo"
"""Named here so the boundary `tests/conftest.py` declares is executable rather than prose.

Name resolution is deliberately **not** guarded: `getaddrinfo` alone transfers nothing, and
refusing it would break `socket.getaddrinfo("localhost", ...)`-style calls inside the standard
library that never go on to connect. It is also shaped differently from the three above --
measured, its audit args are `(host, port, family, type, protocol)`, so `args[0]` is a `str`
and not a socket -- which means adding it to `GUARDED_AUDIT_EVENTS` would not merely widen the
guard, it would raise `AttributeError` inside an audit hook. `tests/unit/test_offline_suite.py
::test_name_resolution_is_outside_the_guard_and_stays_outside` pins it.
"""

REFUSAL_MESSAGE: Final[str] = (
    "This suite runs offline: a provider test injects its transport, a panel test generates "
    "its fixture, and an HTTP test drives the ASGI app in process. If this test genuinely "
    "needs the network it belongs in `tests/e2e/`, which is deselected by default; see that "
    "package's conftest."
)
"""Named rather than inlined so the guard's test can assert the way *out* is stated, not only
that something was refused. A guard that only says "no" gets deleted by the next person who
needs the network."""


class OfflineSuiteViolation(RuntimeError):
    """A test that is not marked `e2e` tried to send something out over IP.

    "Open an outbound TCP connection" is what this said until `V2-P4-039`, and it was accurate
    about the guard and wrong about the guarantee: a UDP datagram opens nothing and left the
    process unrefused. The wording is wider now because the guard is, and `V2-P4-105` made it
    true of the C class as well as of the Python wrapper.
    """


_depth = 0
"""How many `refusing_outbound_traffic()` blocks are open. Zero means the hook is inert.

A depth rather than a flag because `tests/conftest.py`'s `pytest_runtest_protocol` wrapper holds
one open around every non-e2e item -- and its `pytest_collection` wrapper holds one around the
whole import phase -- so a test that opens its own nested block must not switch the guard off
when it closes.
"""


def _refuse(event: str, args: tuple[Any, ...]) -> None:
    """The audit hook: refuse `event` on a guarded family, and be invisible otherwise.

    `args[0]` is read as a socket without a `getattr` default, deliberately. All three events
    pass one (measured), so a default would be a branch no input reaches -- and if a future
    CPython ever passed something else, an `AttributeError` out of an audit hook is a loud
    failure, where a default that happened to name a guarded family would be a silent one.

    The destination is read out of the event's own arguments -- `(self, address)` for all three
    -- rather than hunted for among `*args` by type, which is what the shadow had to do because
    `connect(address)` takes it first and `sendmsg(buffers, ancdata, flags, address)` fourth.
    **Nothing is decided by it**, which is the address-blindness `V2-P4-039` relies on: a
    datagram at loopback is refused on the same terms as one at a routable host. `sendmsg` on a
    connected socket passes `None`, and that is reported as `None` rather than guessed at.
    """
    if event not in GUARDED_AUDIT_EVENTS or _depth == 0:
        return
    sock = args[0]
    if sock.family not in GUARDED_SOCKET_FAMILIES:
        return
    destination = args[1] if len(args) > 1 else None
    raise OfflineSuiteViolation(
        f"{event}({destination!r}) from a test that is not marked `e2e`. {REFUSAL_MESSAGE}"
    )


sys.addaudithook(_refuse)
"""Installed once, at import, and **it can never be removed** -- `sys` offers no way to.

That is the price of reaching below the class graph, and it is paid deliberately rather than
quietly: `_depth` is what turns the guard on and off, and an `e2e` test runs with the hook
installed and inert. The compensation is that the thing the old design had to restore no longer
exists -- `socket.socket` is never mutated at all now, so there is no class dict to put back and
no `delattr` whose omission a mutation could hide.

Measured before choosing it: `tests/unit` runs 33.58s with the hook installed against 35.49s
without, on the same machine minutes apart -- no cost this suite can distinguish from noise.
"""


@contextmanager
def refusing_outbound_traffic() -> Iterator[None]:
    """Refuse `GUARDED_AUDIT_EVENTS` on `GUARDED_SOCKET_FAMILIES` for the duration.

    Takes no `target` any more, and the loss is worth stating. `V2-P4-039` gave it one so the
    install/restore round trip could be driven over a throwaway subclass -- the only way to
    observe a class-shadowing guard from inside a suite where every test already runs under it.
    There is no class to hand over now, and the same observability problem is answered instead
    by `tests/unit/test_offline_suite.py::test_the_guard_stops_refusing_once_it_unwinds`, which
    drives the whole cycle in a child interpreter and watches a loopback datagram be refused
    inside the block and **delivered** after it. That is a stronger measurement than the
    subclass round trip was: it observes the guarantee, not the shape of a class dict.
    """
    global _depth
    _depth += 1
    try:
        yield
    finally:
        _depth -= 1
