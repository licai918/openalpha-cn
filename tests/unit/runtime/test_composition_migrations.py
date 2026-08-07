"""Proves `build_storage()` mounts the migration engine before constructing any store."""

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.migrations import (
    BASELINE_VERSION,
    DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
    read_status,
)


def test_build_storage_stamps_a_fresh_runtime_dir_to_baseline_without_crashing(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    runtime_dir = tmp_path / "runtime"

    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    status = read_status(runtime_dir / "state.sqlite3")
    # The demo migration defers here: migrations run before SQLiteRunRepository creates
    # `runs`, so only the precondition-free baseline can apply on this first call.
    assert status.current_version == BASELINE_VERSION
    assert [m.version for m in status.pending] == [DEMO_ADD_RUNS_ARCHIVED_AT_VERSION]
    # `migration_result` (exposed for `cli.py::migrate_run`, which needs the
    # `from_version`/`to_version`/`applied`/`backup_path` this call already computed
    # without re-running migrations a second time) matches `read_status()` exactly.
    assert storage.migration_result.from_version == 0
    assert storage.migration_result.to_version == BASELINE_VERSION
    assert [m.version for m in storage.migration_result.applied] == [BASELINE_VERSION]


def test_build_storage_catches_up_the_demo_migration_on_a_second_call(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    runtime_dir = tmp_path / "runtime"

    # creates `runs` via SQLiteRunRepository
    build_storage(runtime_dir=runtime_dir, clock=migration_clock)
    # `runs` exists; demo can apply
    second = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    status = read_status(runtime_dir / "state.sqlite3")
    assert status.current_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert status.pending == ()
    assert second.migration_result.from_version == BASELINE_VERSION
    assert second.migration_result.to_version == DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    assert [m.version for m in second.migration_result.applied] == [
        DEMO_ADD_RUNS_ARCHIVED_AT_VERSION
    ]


def test_build_storage_logs_runtime_dir_and_schema_version_on_startup(
    tmp_path: Path,
    migration_clock: Callable[[], datetime],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The fourth and last call site V2-P0B-007's brief names explicitly:
    `build_storage()`'s own startup assembly (`runtime_dir`, schema version) -- the
    one line that tells an operator, after the fact, which `runtime_dir` a process
    actually started against and what schema it landed on."""
    runtime_dir = tmp_path / "runtime"
    caplog.set_level(logging.INFO, logger="openalpha_cn.runtime.composition")

    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    events = [r for r in caplog.records if r.message == "storage_initialized"]
    assert len(events) == 1
    assert events[0].runtime_dir == str(runtime_dir)  # type: ignore[attr-defined]
    assert events[0].schema_version == storage.migration_result.to_version  # type: ignore[attr-defined]
