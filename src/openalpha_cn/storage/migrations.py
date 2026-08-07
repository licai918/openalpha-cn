"""Forward-only SQLite schema-migration engine for the shared `state.sqlite3` (V2-P0B-004).

Why this exists: the nine tables spread across the seven SQLite stores in this package are
each created by their own store's `CREATE TABLE IF NOT EXISTS` -- table creation is a side
effect of constructing a store, not a tracked, versioned event. Rows are opaque JSON payloads
validated by pydantic models with `extra="forbid"` and a literal `.../v1` `schema_version` --
any new field makes old rows unreadable to new code and new rows unreadable to old code, with
no code path able to tell the two apart. A later phase (`V2-P4-001`) must make three breaking
contract changes at once; without an explicit, auditable, reversible migration mechanism, that
would silently destroy every research record a user has accumulated.

Design, in one paragraph: `PRAGMA user_version` is the cheap, atomic source of truth for "what
schema version is this database at"; a `schema_migrations` table is a human-readable audit trail
recorded alongside it (version, name, applied_at) -- both are required by the brief because
`user_version` alone can't answer "when was this applied" or "what was it called" without
decoding a version number back to a name. Migrations are a plain, hand-ordered tuple
(`MIGRATIONS`) -- no directory scanning, no plugin discovery, no migration framework: this
repository's runtime dependency set is deliberately seven packages, and a dozen-line executor
over a hardcoded, version-sorted tuple needs none of alembic's machinery. Each migration is one
function `apply(connection) -> None` that runs arbitrary DDL/DML against a connection already
inside an explicit transaction; the executor sorts by `.version` (not by declaration order, so a
future contributor who appends out of order still gets correct ordering), applies only versions
strictly greater than the current `user_version`, and commits the DDL, the audit row, and the
`PRAGMA user_version` bump together, atomically, per migration.

The one subtlety worth documenting: this engine's mount point is `runtime/composition.py`'s
`build_storage()`, which must run migrations *before constructing any store* (per the brief) --
so on a brand-new install, migrations run against a database that has zero tables, because the
stores that would create them haven't been constructed yet. A migration that alters a
pre-existing table (like this module's demo migration) has nothing to alter yet in that case.
Rather than crash a fresh install, such a migration raises `MigrationNotYetApplicable`: the
executor stops (does not advance past it, does not record it as applied) and leaves it pending
for the next invocation, by which point the owning store has created its table. This is not
speculative -- `test_new_database_lands_on_baseline_and_defers_the_demo_migration` and
`test_build_storage_catches_up_the_demo_migration_on_a_second_call` exercise exactly this path.
"""

import os
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


class MigrationNotYetApplicable(RuntimeError):
    """Raised by a migration's `apply()` when its precondition isn't met yet.

    Not a failure: the executor treats this as "nothing to roll back, try again later" --
    it stops applying further migrations, but does not touch `user_version` or record
    anything, because nothing was attempted. The canonical case is a migration that alters
    a table an existing store owns, running against a brand-new database where that store
    has not yet created the table (migrations run before any store is constructed).
    """


class MigrationFailedError(RuntimeError):
    """Raised after a migration's transaction has already been rolled back.

    Carries the failed migration's identity and the pre-migration backup path so a caller
    (the CLI, in particular) can tell the user exactly where to find their data.
    """

    def __init__(self, message: str, *, version: int, name: str, backup_path: Path) -> None:
        super().__init__(message)
        self.version = version
        self.name = name
        self.backup_path = backup_path


@dataclass(frozen=True)
class Migration:
    """One forward-only, idempotently-applied schema change."""

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class AppliedMigration:
    """One row of the `schema_migrations` audit trail."""

    version: int
    name: str
    applied_at: str


@dataclass(frozen=True)
class MigrationStatus:
    """A read-only snapshot of a database's migration state."""

    path: Path
    current_version: int
    applied: tuple[AppliedMigration, ...]
    pending: tuple[Migration, ...]


@dataclass(frozen=True)
class MigrationRunResult:
    """The outcome of one `run_migrations()` call."""

    path: Path
    from_version: int
    to_version: int
    applied: tuple[AppliedMigration, ...]
    backup_path: Path | None


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def require_table(connection: sqlite3.Connection, name: str) -> None:
    """Raise `MigrationNotYetApplicable` unless table `name` already exists.

    Reusable precondition guard for any migration that alters a table owned by one of
    this package's stores. Migrations run before any store is constructed (see module
    docstring), so on a fresh install the owning table does not exist yet; a migration
    that assumes otherwise and runs a bare `ALTER TABLE`/`UPDATE`/etc. against it crashes
    every fresh install with a bare `sqlite3.OperationalError`, wrapped by the executor
    into `MigrationFailedError` and propagated out of `build_storage()` -- i.e. it takes
    down `OpenAlphaSDK.__init__` and `create_app()` for every new installation, not just
    this one migration. Call this as the first line of `apply()` instead of hand-rolling
    the same `_table_exists` check --
    `test_every_non_baseline_migration_defers_gracefully_on_a_fresh_empty_database`
    (tests/integration/storage/test_migrations.py) enforces that every non-baseline
    migration in `MIGRATIONS` actually does this.
    """
    if not _table_exists(connection, name):
        raise MigrationNotYetApplicable(f"{name} table does not exist yet")


def _baseline_apply(connection: sqlite3.Connection) -> None:
    """Stamp an unversioned database at the baseline version. Touches no data.

    The nine pre-migration tables are (and stay) created by their owning stores'
    `CREATE TABLE IF NOT EXISTS` statements, not by this migration -- see the module
    docstring. Whether `connection` already holds a populated v1-shaped database or is
    completely empty, there is nothing for this migration to do beyond being recorded:
    both land on `BASELINE_VERSION`.
    """


def _demo_add_runs_archived_at(connection: sqlite3.Connection) -> None:
    """Demonstration migration (V2-P0B-004's acceptance proof): add `runs.archived_at`.

    Proves the executor can make a real, additive schema change to a table that already
    holds real records, while leaving those records fully readable through
    `SQLiteRunRepository` afterwards -- its INSERT/SELECT statements name explicit
    columns, so an additive column is invisible to it. This migration is illustrative
    scaffolding for this task, not one of the three breaking changes `V2-P4-001` will make.

    Guarded twice: if `runs` doesn't exist yet, there is nothing to alter (see module
    docstring), so this raises `MigrationNotYetApplicable` via `require_table`. If
    `archived_at` is already present (this migration has already run once, or the table
    was created after this migration first shipped), it is a silent no-op rather than a
    duplicate-column error.
    """
    require_table(connection, "runs")
    columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
    if "archived_at" in columns:
        return
    connection.execute("ALTER TABLE runs ADD COLUMN archived_at TEXT")


BASELINE_VERSION = 1
DEMO_ADD_RUNS_ARCHIVED_AT_VERSION = 2

MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=BASELINE_VERSION, name="baseline", apply=_baseline_apply),
    Migration(
        version=DEMO_ADD_RUNS_ARCHIVED_AT_VERSION,
        name="demo_add_runs_archived_at",
        apply=_demo_add_runs_archived_at,
    ),
)


def _current_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _pending(current_version: int, migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    ordered = sorted(migrations, key=lambda migration: migration.version)
    return tuple(migration for migration in ordered if migration.version > current_version)


def _take_backup(path: Path, *, current_version: int, clock: Callable[[], datetime]) -> Path:
    """Back up `path` (via the SQLite backup API, safe under WAL) before migrating.

    Lands in `path.parent / "backups"` -- inside the runtime directory, which is
    gitignored -- named with both the pre-migration version and a timestamp so an
    operator can identify which backup corresponds to which failed or successful run.
    `.bak` is a SQLite binary dump just like `.sqlite3`/`.db`, so it is one of
    `scripts/verify_publication.py`'s `BLOCKED_SUFFIXES`: escaping the publication scan
    is the risk for a file like this, not a protection, and `.gitignore` alone stops
    covering it the moment someone passes a custom `--runtime-dir` outside the ignored
    tree. The filename is claimed with `os.O_CREAT | os.O_EXCL` (atomic at the OS level,
    unlike an exists-then-create check) and falls back to a numeric suffix on collision,
    so two callers backing up the same version within the same clock tick -- e.g. two
    concurrent processes, or a frozen test clock -- never silently overwrite each other.
    """
    backups_dir = path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    timestamp = clock().strftime("%Y%m%dT%H%M%SZ")
    stem = f"{path.name}.v{current_version}.{timestamp}"
    suffix = 0
    while True:
        candidate = stem if suffix == 0 else f"{stem}-{suffix}"
        backup_path = backups_dir / f"{candidate}.bak"
        try:
            claimed_fd = os.open(backup_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            suffix += 1
            continue
        os.close(claimed_fd)
        break
    source = sqlite3.connect(path)
    try:
        destination = sqlite3.connect(backup_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return backup_path


def read_status(path: Path, *, migrations: Sequence[Migration] = MIGRATIONS) -> MigrationStatus:
    """Return the current schema version, applied migrations, and pending migrations.

    Read-only: never takes a backup, never opens a write transaction, never creates
    `schema_migrations` if it is absent. Used by the `migrate status` CLI command and by
    `migrate run --dry-run`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        current_version = _current_version(connection)
        if _table_exists(connection, "schema_migrations"):
            rows = connection.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            ).fetchall()
        else:
            rows = []
        applied = tuple(
            AppliedMigration(version=row[0], name=row[1], applied_at=row[2]) for row in rows
        )
    finally:
        connection.close()
    pending = _pending(current_version, migrations)
    return MigrationStatus(
        path=path, current_version=current_version, applied=applied, pending=pending
    )


def run_migrations(
    path: Path,
    *,
    clock: Callable[[], datetime],
    migrations: Sequence[Migration] = MIGRATIONS,
) -> MigrationRunResult:
    """Apply every pending migration, in order, each in its own transaction.

    Forward-only and idempotent: already-applied versions (per `PRAGMA user_version`) are
    never reconsidered. If nothing is pending, this is a fast no-op that opens no write
    transaction and takes no backup. Otherwise, backs up `path` once before applying
    anything, then applies each pending migration inside `BEGIN IMMEDIATE` / `COMMIT`: the
    migration's own DDL/DML, the `schema_migrations` audit row, and the `PRAGMA
    user_version` bump are one atomic unit. Any failure rolls the whole unit back --
    `user_version` does not move and no audit row is written for that migration -- and is
    re-raised as `MigrationFailedError` naming the pre-migration backup. A migration that
    raises `MigrationNotYetApplicable` stops the run (without error) and leaves it, and
    everything after it, pending.

    Concurrency: `pending` is computed once, from a snapshot taken before any lock is
    held, so two callers racing the same database (e.g. the API and the CLI hitting the
    same `runtime_dir`) can both decide the same migration is pending. `BEGIN IMMEDIATE`
    correctly serializes the two writers, but without a re-check the loser would replay
    `migration.apply()` and then hit a primary-key collision on its own `INSERT INTO
    schema_migrations` -- a real `sqlite3.IntegrityError` that the generic `except
    Exception` below would report as `MigrationFailedError`, even though the database is
    healthy and the migration is in fact already applied. Each iteration therefore
    re-reads `PRAGMA user_version` *after* acquiring the write lock and skips migrations
    a concurrent winner already committed while this connection was blocked waiting for
    the lock, instead of attempting to reapply them.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(_SCHEMA_MIGRATIONS_DDL)
        from_version = _current_version(connection)
        pending = _pending(from_version, migrations)
        if not pending:
            return MigrationRunResult(
                path=path,
                from_version=from_version,
                to_version=from_version,
                applied=(),
                backup_path=None,
            )
        backup_path = _take_backup(path, current_version=from_version, clock=clock)
        applied: list[AppliedMigration] = []
        for migration in pending:
            connection.execute("BEGIN IMMEDIATE")
            if migration.version <= _current_version(connection):
                # A concurrent writer already applied this exact migration while this
                # connection was blocked waiting for the write lock above -- not a
                # fault, just a lost race. Skip it instead of replaying `apply()` and
                # colliding with the winner's `schema_migrations` row.
                connection.execute("ROLLBACK")
                continue
            try:
                migration.apply(connection)
                applied_at = clock().isoformat()
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, applied_at),
                )
                connection.execute(f"PRAGMA user_version = {migration.version:d}")
            except MigrationNotYetApplicable:
                connection.execute("ROLLBACK")
                break
            except Exception as error:
                connection.execute("ROLLBACK")
                raise MigrationFailedError(
                    f"migration {migration.version} ({migration.name}) failed and was rolled back",
                    version=migration.version,
                    name=migration.name,
                    backup_path=backup_path,
                ) from error
            else:
                connection.execute("COMMIT")
                applied.append(
                    AppliedMigration(
                        version=migration.version, name=migration.name, applied_at=applied_at
                    )
                )
        to_version = _current_version(connection)
        return MigrationRunResult(
            path=path,
            from_version=from_version,
            to_version=to_version,
            applied=tuple(applied),
            backup_path=backup_path,
        )
    finally:
        connection.close()
