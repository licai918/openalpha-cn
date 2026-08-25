"""The default test run is offline, and `tests/e2e/` is the only way out of that.

Three switches keep `uv run pytest` from reaching the network, and each is tested here for a
different failure. The marker deselection is *policy* -- one `addopts` edit undoes it. The
`OPENALPHA_E2E` requirement is *policy* too. Only the socket guard is a measurement, so it is
the one this file spends most of its assertions on: an installed fixture that does not
actually refuse anything is exactly the shape of the twelve Criticals this project has booked,
where the stated property and the observed behaviour came apart.

Deliberately under `tests/unit/`, not `tests/e2e/`: these tests must run in the default suite,
and every test in `tests/e2e/` is deselected there.
"""

from __future__ import annotations

import _socket
import ast
import re
import socket
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
from offline_guard import (
    GUARDED_AUDIT_EVENTS,
    UNGUARDED_RESOLUTION_EVENT,
    OfflineSuiteViolation,
)

ROOT = Path(__file__).resolve().parents[2]
E2E_ROOT = ROOT / "tests" / "e2e"


def _pytest_config() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)
    section = config["tool"]["pytest"]["ini_options"]
    assert isinstance(section, dict)
    return section


# --- switch 1: the marker is registered and deselected by default -------------------------


def test_the_e2e_marker_is_registered_so_strict_markers_does_not_reject_it() -> None:
    markers = _pytest_config()["markers"]
    assert isinstance(markers, list)
    assert any(entry.split(":")[0].strip() == "e2e" for entry in markers), (
        "`--strict-markers` is on, so an unregistered `e2e` marker would make every module "
        "under tests/e2e/ a collection error rather than a deselection"
    )


def test_the_default_run_deselects_the_e2e_marker() -> None:
    addopts = _pytest_config()["addopts"]
    assert isinstance(addopts, str)
    assert '-m "not e2e"' in addopts, (
        "without this, `uv run pytest` -- the command CI runs -- would execute every test "
        "under tests/e2e/ and the whole suite would depend on a live endpoint and a token"
    )


def test_every_e2e_module_marks_itself_at_module_level() -> None:
    """A file under `tests/e2e/` that forgot the marker would run in the default suite.

    Checked structurally (a module-level `pytestmark = pytest.mark.e2e`) rather than by
    collecting the package, because collection is exactly what a missing marker would make
    look fine: the module would be collected either way, and the difference only shows up as
    a network call at run time.
    """
    modules = sorted(E2E_ROOT.glob("test_*.py"))
    assert modules, "tests/e2e/ holds no test module; this guard would then prove nothing"
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        marked = any(
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "pytestmark"
                for target in node.targets
            )
            and "e2e" in ast.unparse(node.value)
            for node in tree.body
        )
        assert marked, f"{module.relative_to(ROOT)} has no module-level `pytestmark` naming e2e"


# --- switch 3: the socket guard is live ---------------------------------------------------


def test_an_outbound_tcp_connection_is_refused_from_an_unmarked_test() -> None:
    """The guard itself, exercised the way a stray provider call would exercise it.

    The address is TEST-NET-1 (RFC 5737, reserved for documentation and never routed) so that
    a machine on which this test somehow ran *unguarded* would time out rather than reach
    anything -- the failure would be slow, and it would still be a failure.
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(OfflineSuiteViolation),
    ):
        sock.connect(("192.0.2.1", 80))


def test_connect_ex_is_refused_too_rather_than_returning_an_error_number() -> None:
    """`connect_ex` reports failure as a return value, so an unguarded one is silent.

    A caller that used it and ignored the result would reach the network with nothing raised
    anywhere, which is why the guard covers both entry points rather than the obvious one.
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(OfflineSuiteViolation),
    ):
        sock.connect_ex(("192.0.2.1", 80))


def test_an_ipv6_socket_is_refused_on_the_same_terms() -> None:
    with (
        socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as sock,
        pytest.raises(OfflineSuiteViolation),
    ):
        sock.connect(("2001:db8::1", 80))


def test_a_udp_datagram_is_refused_although_it_opens_no_connection() -> None:
    """`V2-P4-039`: the hole `connect` alone leaves, and the reason it is a hole.

    A datagram needs no connection, so a guard on `connect`/`connect_ex` never sees one.
    Measured at `146698c` before this was closed: `sendto` returned 5 with nothing raised,
    against a wrapped set of `connect: True, connect_ex: True, sendto: False, sendmsg: False,
    send: False, sendall: False`. The guard is address-blind by design, so the datagram that
    left was no more refused when it was aimed at a routable host than at loopback.
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(OfflineSuiteViolation, match="tests/e2e/"),
    ):
        sock.sendto(b"probe", ("192.0.2.1", 9))


def test_the_other_unconnected_send_is_refused_too_rather_than_only_the_obvious_one() -> None:
    """`sendmsg` is `sendto`'s second spelling, and it takes its destination in a fourth slot.

    Wrapping only the obvious entry point is the shape of the defect this whole fixture exists
    to close -- `connect_ex` is here for the same reason one section up.
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(OfflineSuiteViolation),
    ):
        sock.sendmsg([b"probe"], [], 0, ("192.0.2.1", 9))


def test_the_refusal_names_the_destination_wherever_the_call_puts_it() -> None:
    """`connect` takes its address first and `sendmsg` fourth; one wrapper reports both.

    Diagnostic rather than load-bearing -- the guard refuses by family and reads the address
    for nothing -- but that is exactly why it needs an assertion of its own: a message that
    said `None` for every call would send a reader hunting the wrong socket, and every other
    test in this file would still pass. It survived the first mutation round for that reason.
    """
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream,
        pytest.raises(OfflineSuiteViolation, match=re.escape("connect(('192.0.2.1', 80))")),
    ):
        stream.connect(("192.0.2.1", 80))

    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram,
        pytest.raises(OfflineSuiteViolation, match=re.escape("sendto(('192.0.2.2', 9))")),
    ):
        datagram.sendto(b"probe", ("192.0.2.2", 9))

    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as fourth_slot,
        pytest.raises(OfflineSuiteViolation, match=re.escape("sendmsg(('192.0.2.3', 9))")),
    ):
        fourth_slot.sendmsg([b"probe"], [], 0, ("192.0.2.3", 9))


def test_a_datagram_on_a_family_that_is_not_the_network_is_left_alone() -> None:
    """The `AF_UNIX` half of the send guard, so widening it did not widen what it forbids.

    An `AF_UNIX` datagram to a path that does not exist must fail the way the operating system
    fails it, exactly as `test_a_non_internet_socket_is_left_alone` requires of `connect`.
    """
    family = getattr(socket, "AF_UNIX", None)
    if family is None:  # pragma: no cover - Windows has no AF_UNIX in this stdlib build
        pytest.skip("this platform has no AF_UNIX")
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        with pytest.raises(OSError) as caught:
            sock.sendto(b"probe", "/openalpha-cn/this/path/does/not/exist.sock")
        assert not isinstance(caught.value, OfflineSuiteViolation)


def test_the_guard_names_the_way_out_rather_than_only_the_refusal() -> None:
    """A guard that only says "no" gets deleted by the next person who needs the network."""
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(OfflineSuiteViolation, match="tests/e2e/"),
    ):
        sock.connect(("192.0.2.1", 80))


def test_a_non_internet_socket_is_left_alone() -> None:
    """The guard forbids the network, not sockets.

    A `AF_UNIX` connect to a path that does not exist must fail the way the operating system
    fails it -- `OSError`, not `OfflineSuiteViolation` -- or the guard is refusing something
    it never claimed to. Load-bearing rather than decorative since `V2-P4-105`: the audit
    events fire on `AF_UNIX` sockets too, so `GUARDED_SOCKET_FAMILIES` is now the *only* thing
    standing between a local socket and a refusal.
    """
    family = getattr(socket, "AF_UNIX", None)
    if family is None:  # pragma: no cover - Windows has no AF_UNIX in this stdlib build
        pytest.skip("this platform has no AF_UNIX")
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        with pytest.raises(OSError) as caught:
            sock.connect("/openalpha-cn/this/path/does/not/exist.sock")
        assert not isinstance(caught.value, OfflineSuiteViolation)


# --- V2-P4-105: the guard is below the class graph, not spread across it ------------------
#
# Every probe in this section aims at **loopback**, and that is the opposite of the choice the
# section above makes on purpose. Up there the destination is TEST-NET-1, so a test that somehow
# ran unguarded would time out rather than reach anything. Down here the point of the test is
# that a real listener on this machine receives -- or does not receive -- a real datagram, which
# is the only way to tell "refused" apart from "sent and silently dropped". Nothing here can
# leave the machine even if the guard is removed entirely.


def _loopback_datagram_listener() -> socket.socket:
    """A bound `AF_INET` UDP socket on `127.0.0.1`, with a short timeout so a probe that was
    *not* refused shows up as a delivered datagram and one that was shows up as a timeout."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listener.bind(("127.0.0.1", 0))
    listener.settimeout(0.25)
    return listener


def test_the_c_base_class_the_python_wrapper_inherits_from_is_refused_too() -> None:
    """The escape `V2-P4-105` was filed for, closed: `import _socket` is not a way out.

    `socket.socket` defines none of the four guarded methods -- it inherits every one of them
    from `_socket.socket` -- so a guard that shadowed names on the wrapper never saw a caller
    that asked the base class directly. Measured at `46253c4` before this closed, from inside
    this very file's fixture: `_socket.socket(...).sendto(...)` returned 11 and the loopback
    listener received `b'ESCAPED-UDP'`, while `socket.socket.connect` from the same test raised.
    """
    with _loopback_datagram_listener() as listener:
        address = listener.getsockname()
        raw = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            with pytest.raises(OfflineSuiteViolation, match="tests/e2e/"):
                raw.sendto(b"ESCAPED-UDP", address)
        finally:
            raw.close()
        with pytest.raises(TimeoutError):
            listener.recv(64)


def test_a_detached_file_descriptor_rewrapped_in_the_c_class_is_refused() -> None:
    """The sharpest of the three escapes: it imports no new class, it borrows a guarded one's fd.

    `socket.detach()` hands back the file descriptor and leaves the wrapper closed; passing that
    number to `_socket.socket(..., fileno=...)` produces a second object over the *same* kernel
    socket, owned by the class no shadow could reach. Measured at `46253c4`: the listener
    received `b'ESCAPED-DETACH'`. It is worth its own test rather than being folded into the one
    above because it defeats a guard that had somehow managed to hand out only wrapped classes.
    """
    with _loopback_datagram_listener() as listener:
        address = listener.getsockname()
        guarded = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        descriptor = guarded.detach()
        raw = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM, 0, descriptor)
        try:
            with pytest.raises(OfflineSuiteViolation):
                raw.sendto(b"ESCAPED-DETACH", address)
        finally:
            raw.close()
        with pytest.raises(TimeoutError):
            listener.recv(64)


def test_the_c_base_class_cannot_be_shadowed_which_is_why_this_is_an_audit_hook() -> None:
    """The measurement that ruled out the repair `V2-P4-105` would otherwise have preferred.

    "Widen the shadow from `socket.socket` onto `_socket.socket`" is the obvious closure and it
    is not available: the C class is an immutable extension type. This asserts the refusal
    rather than describing it, so that a future CPython which *did* allow the assignment would
    make this file red and force the choice to be re-argued instead of leaving a paragraph in
    `offline_guard.py` quietly claiming something untrue.
    """
    with pytest.raises(TypeError, match="immutable type"):
        _socket.socket.connect = None  # type: ignore[assignment, method-assign]


def test_the_unaudited_sends_are_unreachable_because_connecting_is_what_is_refused() -> None:
    """The closure argument, driven over the C class rather than asserted about a class dict.

    `send`, `sendall` and `socket.sendfile` raise no audit event, and the claim that this leaves
    no hole rests entirely on their needing a **connected** socket. What stood here before
    `V2-P4-105` asserted over `vars(socket.socket)` -- that the four guarded names were shadowed
    and `send`/`sendall` were not -- which is structurally blind to the whole finding: every one
    of those names is inherited, so the class dict says nothing about what the C base will do.

    This drives it instead. The connect is refused, the socket is therefore never connected, and
    `sendall` fails the way an unconnected socket fails while the listener on the other end
    receives nothing at all.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(0.25)
    address = listener.getsockname()
    try:
        raw = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            with pytest.raises(OfflineSuiteViolation):
                raw.connect(address)
            with pytest.raises(OSError) as caught:
                raw.sendall(b"ESCAPED-TCP")
            assert not isinstance(caught.value, OfflineSuiteViolation), (
                "the point of this test is that `sendall` fails for want of a connection, not "
                "that it is guarded; if the guard starts covering it this argument has changed"
            )
        finally:
            raw.close()
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        listener.close()


def test_the_guarded_events_are_the_three_the_c_module_raises_for_transmission() -> None:
    """The declared surface, and the one place `connect_ex` stops being a separate name.

    Three rather than `V2-P4-039`'s four because CPython raises `socket.connect` for
    `connect_ex` as well -- measured, there is no `socket.connect_ex` event -- so the two
    entry points arrive here as one. `test_connect_ex_is_refused_too_rather_than_returning_an
    _error_number` above is what keeps that from being a coverage claim about a name nobody
    checked.
    """
    assert {"socket.connect", "socket.sendto", "socket.sendmsg"} == GUARDED_AUDIT_EVENTS
    assert all(event.startswith("socket.") for event in GUARDED_AUDIT_EVENTS)


def test_name_resolution_is_outside_the_guard_and_stays_outside() -> None:
    """`tests/conftest.py` declares DNS a boundary; this is that sentence made executable.

    `getaddrinfo` transfers nothing on its own, and refusing it would break stdlib calls that
    resolve `localhost` and never go on to connect. It is also shaped differently from the three
    that are guarded -- its audit args are `(host, port, family, type, protocol)`, so `args[0]`
    is a `str` -- which is why widening the set to include it would raise `AttributeError`
    inside an audit hook rather than merely forbidding more.
    """
    assert UNGUARDED_RESOLUTION_EVENT not in GUARDED_AUDIT_EVENTS
    assert socket.getaddrinfo("127.0.0.1", 80, socket.AF_INET, socket.SOCK_STREAM)

    script = textwrap.dedent(
        """
        import socket, sys
        seen = []
        sys.addaudithook(lambda event, args: seen.append(event))
        socket.getaddrinfo("127.0.0.1", 80, socket.AF_INET, socket.SOCK_STREAM)
        print(" ".join(sorted({e for e in seen if e.startswith("socket.")})))
        """
    )
    finished = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=True
    )
    raised = set(finished.stdout.split())
    assert UNGUARDED_RESOLUTION_EVENT in raised, (
        f"resolving a name raises {sorted(raised)}, and the constant this module declares "
        "outside the guard is not among them -- so the boundary is named after nothing and "
        "asserting it is absent from GUARDED_AUDIT_EVENTS proves nothing at all"
    )
    assert raised & GUARDED_AUDIT_EVENTS == set(), (
        "resolving a name raises an event this guard refuses, so `getaddrinfo` is not outside "
        "the guard however the declaration reads"
    )


def test_the_python_wrapper_class_is_not_mutated_by_the_guard_at_all() -> None:
    """The restoration problem, deleted rather than solved.

    While the guard shadowed names, "leave the class exactly as it found it" needed a `delattr`
    in a `finally`, and a mutation replacing that `delattr` with `pass` left all 59 tests of the
    three modules `V2-P4-037`/`038`/`039` touch green -- because no test could observe
    `socket.socket` unguarded. An audit hook mutates nothing, so there is no teardown to skip:
    the class owns none of these names during a guarded test, exactly as it owns none of them
    outside one.
    """
    for name in ("connect", "connect_ex", "sendto", "sendmsg", "send", "sendall"):
        assert name not in vars(socket.socket), (
            f"{name} is shadowed on the wrapper class; the guard is supposed to sit underneath "
            "it, and a shadow here would be a second mechanism whose removal nothing notices"
        )


def test_the_guard_stops_refusing_once_it_unwinds() -> None:
    """The whole cycle -- refused inside, delivered after -- which no in-process test can see.

    Every test in this suite runs inside the autouse guard, so `_depth` never reaches zero here
    and "the guard unwinds" is unobservable from within, exactly as the class-shadow round trip
    was before `V2-P4-039` gave it a `target`. A child interpreter is the observation point now,
    and it is a better one: it watches the *guarantee* rather than the shape of a class dict.

    Loopback only, and the child is a plain `sys.executable` with `tests/` on its path -- the
    same shelling-out `tests/unit/test_repository_assets.py` and
    `tests/integration/storage/test_migrations.py` already do, and the same reason the
    child-process limit is declared in `tests/conftest.py` rather than papered over.
    """
    script = textwrap.dedent(
        f"""
        import socket, sys
        sys.path.insert(0, {str(ROOT / "tests")!r})
        from offline_guard import OfflineSuiteViolation, refusing_outbound_traffic

        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(2.0)
        address = listener.getsockname()

        with refusing_outbound_traffic():
            try:
                socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"inside", address)
                print("INSIDE-DELIVERED")
            except OfflineSuiteViolation:
                print("INSIDE-REFUSED")

        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"outside", address)
        print("AFTER-" + listener.recv(64).decode())
        """
    )
    finished = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=True
    )
    assert "INSIDE-REFUSED" in finished.stdout, finished.stderr
    assert "AFTER-outside" in finished.stdout, (
        "the guard is still refusing after its block closed; the hook cannot be uninstalled, "
        "so `_depth` returning to zero is the whole of the restoration and this is what says so"
    )


def test_a_nested_block_closing_does_not_switch_the_outer_guard_off() -> None:
    """`_depth` is a count, not a flag, and this is the failure a flag would have.

    The autouse fixture holds one block open for the whole of every non-e2e test, so a test that
    opens its own -- as `test_the_guard_stops_refusing_once_it_unwinds`'s child does, and as any
    future test asserting about the guard would -- must not leave the suite unguarded when it
    closes. With a boolean this test's final assertion delivers a datagram to the listener.
    """
    from offline_guard import refusing_outbound_traffic

    with _loopback_datagram_listener() as listener:
        address = listener.getsockname()
        with refusing_outbound_traffic():
            pass
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
            pytest.raises(OfflineSuiteViolation),
        ):
            sock.sendto(b"NESTED", address)
        with pytest.raises(TimeoutError):
            listener.recv(64)
