"""The offline guarantee: what it refuses, on which families, and the restoration it promises.

A module of its own rather than two lines inside `tests/conftest.py`, because the test that
proves the guard is live (`tests/unit/test_offline_suite.py`) has to import the exception it
raises, and `import conftest` is ambiguous the moment a second `conftest.py` exists anywhere
on the collection path: pytest imports each one under its own basename, so which module that
name resolves to depends on collection order. `tests/e2e/conftest.py` made that concrete --
the import returned the e2e conftest and the unit test failed to collect. `tests/panel_fixtures
.py` is the precedent for a plain shared module at the `tests/` root, and this follows it.

`V2-P4-039` moved the patching itself here too, and that is a second reason of the same kind.
While it lived inside an autouse fixture there was no moment at which any test could look at
`socket.socket` **unguarded**, so "deleting the shadow is the only restoration that leaves the
class exactly as it was found" was a `finally` block nothing could observe -- and a mutation
that skipped the `delattr` outright left the whole suite green. `refusing_outbound_traffic`
takes the class it patches, so the round trip can be driven over a throwaway subclass that
inherits the same methods from the same C base. `tests/import_linter_containment.py::
raw_lint_imports_disables` is the same problem answered the same way one directory over.
"""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Final

GUARDED_SOCKET_FAMILIES: Final[frozenset[int]] = frozenset(
    {int(socket.AF_INET), int(socket.AF_INET6)}
)
"""The two families that reach the network. Everything else -- `AF_UNIX` above all -- is left
alone: a local socket is not the network, and refusing one would be a guess about what a future
test needs rather than a rule about what this one forbids."""

GUARDED_SOCKET_METHODS: Final[tuple[str, ...]] = ("connect", "connect_ex", "sendto", "sendmsg")
"""Every `socket.socket` method the offline guard shadows: two that connect, two that do not.

`V2-P4-039` is why the last two are here. The guard covered `connect`/`connect_ex` only, and a
datagram opens no connection, so nothing saw one: measured on `146698c`, `sendto` on an
`AF_INET` socket returned 5 with no refusal while a TCP `connect` from the same test raised.
The guard is address-blind on purpose -- it refuses by family, not by destination -- so the
datagram that left was no more refused when aimed at a routable host than at loopback, and a
green build could have depended on a remote endpoint answering.

**These four are the whole outbound surface, and that is an argument rather than a list.**
`send`, `sendall` and `socket.sendfile` all require a *connected* socket, and the only way to
connect one on a guarded family is `connect` or `connect_ex`, which raise -- so guarding them
would be code no input can reach, and code no input can reach is what hides which line is
actually doing the work. `sendto` and `sendmsg` are the only two that transmit without
connecting. `tests/unit/test_offline_suite.py::
test_the_guarded_methods_are_the_whole_of_what_leaves_this_process` asserts both halves: these
four are shadowed and `send`/`sendall` are deliberately not.

Narrowing the *claim* to "outbound TCP" was the other repair the row offered, and it was
declined: it would have made the sentence true by making the guarantee smaller, which is the
direction every Critical this project has booked already went.
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
    process unrefused. The wording is wider now because the guard is.
    """


def _refuse(name: str, wrapped: Callable[..., Any]) -> Callable[..., Any]:
    """`name`, refused on a guarded family and passed straight through on every other.

    One wrapper over `*args` rather than one per method, because the four differ in where the
    destination sits -- `connect(address)` takes it first, `sendmsg(buffers, ancdata, flags,
    address)` fourth -- and agree that it is the last positional argument that is a tuple or a
    string. The refusal names the destination only to help a reader find the call; **nothing is
    decided by it**, which is the address-blindness `V2-P4-039` relies on: a datagram at
    loopback is refused on the same terms as one at a routable host.
    """

    def _guard(self: socket.socket, *args: Any, **kwargs: Any) -> Any:
        if self.family in GUARDED_SOCKET_FAMILIES:
            destination = next(
                (item for item in reversed(args) if isinstance(item, tuple | str)), None
            )
            raise OfflineSuiteViolation(
                f"socket.{name}({destination!r}) from a test that is not marked `e2e`. "
                f"{REFUSAL_MESSAGE}"
            )
        return wrapped(self, *args, **kwargs)

    return _guard


@contextmanager
def refusing_outbound_traffic(target: type = socket.socket) -> Iterator[None]:
    """Shadow `GUARDED_SOCKET_METHODS` on `target`, and leave the class exactly as found.

    Restored by hand rather than through `monkeypatch` because `socket.socket` inherits every
    guarded method from the C `_socket.socket` and defines none of its own: `monkeypatch.undo`
    would put the inherited function back as an attribute *on the Python subclass*, which is a
    different object graph from the one the test started with. So whether the class owned each
    name on entry is recorded per name, and a name it did not own is **deleted** rather than
    reassigned.

    `target` is a parameter for the reason this module's docstring gives -- it is what makes
    that last sentence a measurement instead of a comment. `tests/unit/test_offline_suite.py::
    test_the_guard_leaves_a_class_exactly_as_it_found_it` drives the round trip over a
    throwaway subclass. Measured before it existed: replacing that `delattr` with `pass` left
    all 59 tests of the three modules `V2-P4-037`/`038`/`039` touch green.
    """
    installed: dict[str, tuple[Callable[..., Any], bool]] = {}
    for name in GUARDED_SOCKET_METHODS:
        original = getattr(target, name)
        installed[name] = (original, name in vars(target))
        setattr(target, name, _refuse(name, original))
    try:
        yield
    finally:
        for name, (original, had_own) in installed.items():
            if had_own:
                setattr(target, name, original)
            else:
                delattr(target, name)
