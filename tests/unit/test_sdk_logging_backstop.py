"""Prove, in a fresh interpreter, that importing only `openalpha_cn.sdk` installs the
permanent `NullHandler` backstop -- and that a real `logger.error(...)` call reachable
purely through the SDK never reaches the host process's real stderr.

`logging_setup.py`'s module docstring promises this `NullHandler` is permanent, from the
moment this package is used at all. Before this fix, that was only true if the caller
happened to import `cli.py` or `api/app.py` -- the two modules that imported
`logging_setup` -- because the `NullHandler`-install line lived at the bottom of
`logging_setup.py` itself, and nothing forced that module to be imported by any other path
into the package. A reviewer proved the gap concretely: a fresh interpreter that imports
only `openalpha_cn.sdk` and constructs `OpenAlphaSDK` never gets a `NullHandler` at all
(`logging.getLogger("openalpha_cn").handlers == []`), and a real migration failure --
`OpenAlphaSDK.__init__ -> build_storage() -> run_migrations()`, `storage/migrations.py`'s
`logger.error("migration_failed", ...)` -- prints a bare, unformatted `migration_failed`
line straight to the real process stderr.

This cannot be checked in-process: by the time any test in this suite runs, pytest has
already imported `openalpha_cn.logging_setup` (transitively, via `tests/conftest.py` and
every other test module), so an in-process check would trivially pass regardless of
whether the guarantee is real. Subprocess isolation, launched with `sys.executable` so it
runs under the same interpreter/venv as the test suite itself, is essential here, exactly
as it was for `tests/unit/runtime/test_contracts_import_isolation.py`.

## Why the second probe corrupts a `runs` table instead of injecting a fake `Migration`

The second probe must trigger the real `logger.error("migration_failed", ...)` call at
`storage/migrations.py:353`, reached through the *real*, unmodified `MIGRATIONS` tuple --
`build_storage()` takes no `migrations=` override, so there is no way to hand it an
injected failing migration the way `tests/integration/storage/test_migrations.py` does
against `run_migrations()` directly. Instead, the probe pre-creates the `runs` table (which
`build_storage()`'s migration run always precedes any store construction for -- see
`storage/migrations.py`'s module docstring) with an `archived_at` column already present
under a different case: `"Archived_At"`. `_demo_add_runs_archived_at`'s own guard
(`"archived_at" in columns`) is a case-sensitive Python set-membership check against
`PRAGMA table_info(runs)`'s case-*preserved* column names, so it misses the existing
column and proceeds to `ALTER TABLE runs ADD COLUMN archived_at TEXT` -- which SQLite
genuinely rejects, case-insensitively, as `duplicate column name: archived_at`. This is a
real, unscripted `sqlite3.OperationalError` reaching the exact call site the reviewer
named, not a stand-in.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_HANDLER_PROBE_SCRIPT = """
import json
import logging

import openalpha_cn.sdk  # noqa: F401 -- only the SDK, nothing else from this package

logger = logging.getLogger("openalpha_cn")
print(json.dumps({
    "has_null_handler": any(isinstance(h, logging.NullHandler) for h in logger.handlers),
    "handler_count": len(logger.handlers),
}))
"""


def test_importing_only_the_sdk_in_a_fresh_process_installs_the_null_handler() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _HANDLER_PROBE_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"probe subprocess failed (exit {result.returncode}):\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    payload = json.loads(result.stdout)
    assert payload["has_null_handler"] is True, (
        "importing only openalpha_cn.sdk in a fresh interpreter did not install a "
        "NullHandler on the openalpha_cn logger -- the safety net promised by "
        "logging_setup.py's module docstring is not guaranteed for library embedders "
        "that never import cli.py or api/app.py."
    )


_MIGRATION_FAILURE_PROBE_SCRIPT = """
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

runtime_dir = Path(sys.argv[1])
runtime_dir.mkdir(parents=True, exist_ok=True)
db_path = runtime_dir / "state.sqlite3"
connection = sqlite3.connect(db_path)
connection.execute('CREATE TABLE runs (id INTEGER PRIMARY KEY, "Archived_At" TEXT)')
connection.commit()
connection.close()

import openalpha_cn.sdk as sdk_module  # only the SDK -- nothing else from this package

try:
    sdk_module.OpenAlphaSDK(
        runtime_dir=runtime_dir,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
except Exception as error:
    print(f"CAUGHT:{type(error).__name__}")
else:
    print("NO_ERROR_RAISED")
"""


def test_a_real_migration_failure_through_only_the_sdk_never_reaches_the_hosts_stderr(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _MIGRATION_FAILURE_PROBE_SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"probe subprocess crashed unexpectedly (exit {result.returncode}):\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    assert "CAUGHT:MigrationFailedError" in result.stdout, (
        "the probe's deliberately-corrupted runs table did not reproduce a real "
        f"MigrationFailedError -- got stdout={result.stdout!r}. This test's premise "
        "(a genuine migration failure reachable purely through OpenAlphaSDK.__init__) "
        "did not hold; see this module's docstring."
    )
    assert result.stderr == "", (
        "a real migration_failed log record, reached purely through "
        "OpenAlphaSDK.__init__ with neither cli.py nor api/app.py ever imported, "
        f"printed directly to the host process's real stderr: {result.stderr!r}. The "
        "permanent NullHandler backstop is not installed for library embedders."
    )
