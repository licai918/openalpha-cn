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

import ast
import re
import socket
import tomllib
from pathlib import Path

import pytest
from offline_guard import (
    GUARDED_SOCKET_METHODS,
    OfflineSuiteViolation,
    refusing_outbound_traffic,
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


def test_the_guarded_methods_are_the_whole_of_what_leaves_this_process() -> None:
    """The closure argument, as an assertion rather than as a sentence in a comment.

    `send`, `sendall` and `socket.sendfile` all require a **connected** socket, and the only
    way to connect one on a guarded family is through `connect` or `connect_ex`, both of which
    raise. So the four names below are the whole of the outbound surface: two that open a
    connection and two that transmit without one. A fifth unconnected send arriving in the
    standard library would have to be added here, and this is what says so.
    """
    assert GUARDED_SOCKET_METHODS == ("connect", "connect_ex", "sendto", "sendmsg")
    for name in GUARDED_SOCKET_METHODS:
        installed = vars(socket.socket).get(name)
        assert installed is not None, f"{name} is not guarded during this test"
        assert installed.__name__ == "_guard", name
    for name in ("send", "sendall"):
        assert name not in vars(socket.socket), (
            f"{name} is shadowed; it needs a connected socket, so guarding it would be dead "
            "code hiding that `connect` is what closes it"
        )


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
    it never claimed to.
    """
    family = getattr(socket, "AF_UNIX", None)
    if family is None:  # pragma: no cover - Windows has no AF_UNIX in this stdlib build
        pytest.skip("this platform has no AF_UNIX")
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        with pytest.raises(OSError) as caught:
            sock.connect("/openalpha-cn/this/path/does/not/exist.sock")
        assert not isinstance(caught.value, OfflineSuiteViolation)


def test_the_guard_leaves_a_class_exactly_as_it_found_it() -> None:
    """The restoration, driven end to end -- which no test could do while it lived in a fixture.

    Every test runs inside the autouse guard, so `socket.socket` is never observable unguarded
    and "deleting the shadow is the only restoration that leaves the class exactly as it was
    found" was a `finally` block with nothing under it. Measured: replacing that `delattr` with
    `pass` left all 59 tests of the three modules this issue touches green.

    A throwaway subclass is the observable stand-in and not a weaker one: it inherits every
    guarded method from the same C `_socket.socket` that `socket.socket` inherits them from, so
    "the class did not own this name" is true of it for the same reason.
    """

    class _Probe(socket.socket):
        pass

    before = set(vars(_Probe))
    assert not before & set(GUARDED_SOCKET_METHODS), "precondition: it owns none of them"

    with refusing_outbound_traffic(_Probe):
        assert set(vars(_Probe)) - before == set(GUARDED_SOCKET_METHODS)

    assert set(vars(_Probe)) == before


def test_the_guard_leaves_the_socket_class_exactly_as_it_found_it() -> None:
    """No shadowing attribute is left behind on `socket.socket` after the fixture unwinds.

    `socket.socket` inherits `connect` from the C `_socket.socket`; a restoration that
    assigned the inherited function back onto the Python subclass would leave the class in a
    different shape from the one every earlier test saw. This asserts the shape *during* a
    guarded test, which is the only moment the shadow is supposed to exist, and
    `test_the_guard_is_undone_between_tests` covers the other side.
    """
    assert "connect" in vars(socket.socket), "the guard is not installed during this test"


def test_the_guard_is_undone_between_tests() -> None:
    """Whatever the previous test did, this one starts from a guard freshly installed.

    Two tests both observing the shadow is not enough to prove the teardown ran -- a leaked
    shadow would look identical. What separates them is that the refusal message is built per
    fixture invocation, so a guard left over from an earlier test would still refuse, and the
    only observable difference is the class dict returning to its inherited state in between.
    The direct check is in `tests/e2e/`-free territory: `socket.socket.connect` must be a
    plain function this fixture installed, never a stack of them.
    """
    guard = vars(socket.socket)["connect"]
    assert guard.__name__ == "_guard"
    assert not hasattr(guard, "__wrapped__"), "guards have nested; a teardown was skipped"
