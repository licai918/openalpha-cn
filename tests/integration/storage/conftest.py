"""Fixtures shared by the storage integration tests in this directory (V2-P0B-013).

Not promoted to `tests/conftest.py`: nothing here is needed outside `tests/integration/
storage/`, and `migration_clock` is deliberately a *different* instant from the
suite-wide `frozen_now` (see its docstring below) rather than an alias for it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

# Shared by `test_migrations.py` and `test_versioned_reads.py`, the only two files that
# used this exact literal. Both test schema-version/migration mechanics that have nothing
# to do with evidence point-in-time visibility, so this deliberately does NOT reuse
# `frozen_now` (2026-07-24T10:30Z, the evidence-visibility-boundary clock from
# `tests/conftest.py`): keeping it a distinct, later date makes it structurally
# impossible for a future test in this file to accidentally rely on it lining up with
# an evidence fixture's `available_time`.
MIGRATION_CLOCK: datetime = datetime(2026, 8, 7, 9, 0, tzinfo=UTC)


@pytest.fixture
def migration_now() -> datetime:
    """The raw frozen instant, for tests that embed it directly in a model
    (`as_of=migration_now`, `created_at=migration_now`, ...)."""
    return MIGRATION_CLOCK


@pytest.fixture
def migration_clock(migration_now: datetime) -> Callable[[], datetime]:
    """A zero-arg `clock=` callable pinned to `migration_now`, for the `run_migrations(...,
    clock=...)` call sites in `test_migrations.py`."""
    return lambda: migration_now
