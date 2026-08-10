"""The refusal `tests/conftest.py`'s autouse guard raises, and the families it guards.

A module of its own rather than two lines inside `tests/conftest.py`, because the test that
proves the guard is live (`tests/unit/test_offline_suite.py`) has to import the exception it
raises, and `import conftest` is ambiguous the moment a second `conftest.py` exists anywhere
on the collection path: pytest imports each one under its own basename, so which module that
name resolves to depends on collection order. `tests/e2e/conftest.py` made that concrete --
the import returned the e2e conftest and the unit test failed to collect. `tests/panel_fixtures
.py` is the precedent for a plain shared module at the `tests/` root, and this follows it.
"""

from __future__ import annotations

import socket
from typing import Final

GUARDED_SOCKET_FAMILIES: Final[frozenset[int]] = frozenset(
    {int(socket.AF_INET), int(socket.AF_INET6)}
)
"""The two families that reach the network. Everything else -- `AF_UNIX` above all -- is left
alone: a local socket is not the network, and refusing one would be a guess about what a future
test needs rather than a rule about what this one forbids."""

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
    """A test that is not marked `e2e` tried to open an outbound TCP connection."""
