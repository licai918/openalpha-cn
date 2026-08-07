"""Proves `build_storage()` mounts the migration engine before constructing any store."""

from datetime import UTC, datetime
from pathlib import Path

from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.migrations import (
    BASELINE_VERSION,
    DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    read_status,
)


def _clock() -> datetime:
    return datetime(2026, 8, 7, 9, 0, tzinfo=UTC)


def test_build_storage_stamps_a_fresh_runtime_dir_to_baseline_without_crashing(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"

    build_storage(runtime_dir=runtime_dir, clock=_clock)

    status = read_status(runtime_dir / "state.sqlite3")
    # The demo migration defers here: migrations run before SQLiteRunRepository creates
    # `runs`, so only the precondition-free baseline can apply on this first call.
    assert status.current_version == BASELINE_VERSION
    assert [m.version for m in status.pending] == [DEMO_ADD_RUNS_ARCHIVED_AT_VERSION]


def test_build_storage_catches_up_the_demo_migration_on_a_second_call(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"

    build_storage(runtime_dir=runtime_dir, clock=_clock)  # creates `runs` via SQLiteRunRepository
    build_storage(runtime_dir=runtime_dir, clock=_clock)  # `runs` now exists; demo can apply

    status = read_status(runtime_dir / "state.sqlite3")
    assert status.current_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert status.pending == ()
