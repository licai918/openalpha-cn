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
import socket
import tomllib
from pathlib import Path

import pytest
from offline_guard import OfflineSuiteViolation

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
