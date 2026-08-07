"""Behavioral guards for the shared fixture layer itself (V2-P0B-013).

`tests/conftest.py` cannot host its own `test_*` functions and have them actually run:
pytest's default collection (`testpaths = ["tests"]`, default `test_*.py` file patterns)
never walks `conftest.py` for test items, it only imports it for fixtures/hooks. So these
guards live here instead, in an ordinary collected test module, one directory up from
nothing -- deliberately at the `tests/` root, since the fixtures they guard
(`frozen_now`, `frozen_clock`, `plain_frozen_now`) are shared across every subtree.

Without a test like this, a future edit to `tests/conftest.py` that silently breaks the
frozen-clock contract -- e.g. swapping `frozen_now`'s literal for `datetime.now(UTC)`, or
letting the two shared clocks collide -- would show up only as mysterious flakiness or
silent semantic drift across the dozens of tests built on top of it, not as a clear
failure here.
"""

from collections.abc import Callable
from datetime import UTC, datetime

# Mirrors `tests/conftest.py`'s `FROZEN_NOW` / `PLAIN_FROZEN_NOW` literals. Duplicated
# deliberately (not imported): this module's job is to verify the fixtures' contract
# from the outside, the same way a caller of `frozen_now` would, not to share
# construction logic with them.
EXPECTED_FROZEN_NOW = datetime(2026, 7, 24, 10, 30, tzinfo=UTC)
EXPECTED_PLAIN_FROZEN_NOW = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def test_frozen_now_fixture_is_a_fixed_timezone_aware_instant(frozen_now: datetime) -> None:
    assert frozen_now == EXPECTED_FROZEN_NOW
    assert frozen_now.tzinfo is UTC


def test_frozen_clock_callable_is_pinned_to_frozen_now_and_does_not_advance(
    frozen_now: datetime,
    frozen_clock: Callable[[], datetime],
) -> None:
    """The core "is it actually frozen" guard: two calls to the clock, one right after
    the other, must return the identical instant -- and that instant must be exactly
    `frozen_now`, not a fresh sample of wall-clock time."""
    first_call = frozen_clock()
    second_call = frozen_clock()

    assert first_call == second_call == frozen_now == EXPECTED_FROZEN_NOW


def test_plain_frozen_now_is_a_fixed_instant_distinct_from_frozen_now(
    plain_frozen_now: datetime,
    frozen_now: datetime,
) -> None:
    """The two shared clocks must never accidentally collapse to the same instant --
    see `tests/conftest.py`'s `PLAIN_FROZEN_NOW` docstring for why that distinction is
    deliberate (it protects the point-in-time visibility boundary `frozen_now` encodes
    from being coincidentally satisfied by an unrelated fixture)."""
    assert plain_frozen_now == EXPECTED_PLAIN_FROZEN_NOW
    assert plain_frozen_now.tzinfo is UTC
    assert plain_frozen_now != frozen_now
