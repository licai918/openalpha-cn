"""SQLite WAL repository for append-only runs, decisions, and checkpoints."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

from openalpha_cn.domain.decision import DECISION_LEDGER_VERSIONS, DecisionLedger
from openalpha_cn.domain.run import (
    CHECKPOINT_RECORD_VERSIONS,
    RUN_MANIFEST_VERSIONS,
    CheckpointRecord,
    RunManifest,
)
from openalpha_cn.domain.run_mode import RunMode
from openalpha_cn.domain.versioning import read_versioned
from openalpha_cn.storage.connection import open_state_connection

RUNS_TABLE: Final[str] = "runs"

RUNS_MODE_COLUMN: Final[str] = "mode"
"""The name of the queryable projection of `runs.payload`'s `mode` (`V2-P4-002`)."""

RUNS_MODE_PAYLOAD_PATH: Final[str] = "$.mode"
"""The JSON path the column is derived from. Named once so the migration's run-time audit
(`storage/migrations.py::_audit_runs_mode_projection`) can re-derive the same value in Python
and refuse a column that disagrees -- a mistyped path is otherwise a silent all-NULL column."""

RUNS_MODE_COLUMN_DDL: Final[str] = (
    f"{RUNS_MODE_COLUMN} TEXT GENERATED ALWAYS AS "
    f"(json_extract(payload, '{RUNS_MODE_PAYLOAD_PATH}')) VIRTUAL"
)
"""`runs.mode` as a **derived projection of the payload**, never a second copy of it.

Finding F70: `mode` lives inside the opaque `payload` JSON, so "list every paper run" is a full
table scan plus one JSON parse per row. The obvious fix -- a plain `TEXT` column written by
`append_run` -- would put the same fact in two places, and this repository's most expensive
recurring lesson is that a table and the implementation beside it drift the moment nothing
re-reads both. A `GENERATED ALWAYS AS` column has no such second place: SQLite computes it from
`payload` on every read, refuses any attempt to write it (`cannot INSERT into generated column
"mode"`), and re-derives it automatically when `payload` is updated -- which
`storage/migrations.py::_rewrite_run_manifests` does to every row. The payload stays the single
source of truth; the column is a query path onto it.

`VIRTUAL` rather than `STORED` for one reason that decides it: `ALTER TABLE ... ADD COLUMN`
accepts only `VIRTUAL` generated columns, and the retrofit onto every already-populated
database is the entire point. A `VIRTUAL` column costs one `json_extract` per row *scanned*,
which is exactly what `RUNS_MODE_INDEX_DDL` below removes from the query path: the index is a
real b-tree holding the computed values, so a `WHERE mode = ?` seek never evaluates the
expression at all.

Requires SQLite >= 3.31 (generated columns) with JSON1, which is unconditional from 3.38.
`tests/integration/storage/test_runs_mode_column.py::test_the_runtime_sqlite_supports_the_generated_column_this_schema_needs`
states that floor as an assertion rather than leaving it to fail as a bare `OperationalError`
at the first write."""

RUNS_MODE_INDEX_NAME: Final[str] = "runs_mode_run_id_idx"

RUNS_MODE_INDEX_DDL: Final[str] = (
    f"CREATE INDEX IF NOT EXISTS {RUNS_MODE_INDEX_NAME} ON runs({RUNS_MODE_COLUMN}, run_id)"
)
"""`(mode, run_id)` and not `(mode)` alone, measured rather than assumed.

`list_runs` orders by `run_id` because `runs` has no `sequence` column and an unordered listing
is not a reproducible answer. A single-column index answers the `WHERE` and then leaves SQLite
to sort the matches in a transient b-tree (`USE TEMP B-TREE FOR ORDER BY`); the composite index
answers both. That is a measured difference and not a tidiness one: on 100,000 runs returning
20,000 matches, the SQL step costs 22.2 ms with `(mode)` plus the sort and 11.5 ms with
`(mode, run_id)`. `EXPLAIN QUERY PLAN` is what
`tests/integration/storage/test_runs_mode_column.py::test_listing_one_mode_seeks_the_index_instead_of_scanning_the_table`
holds, because "an index exists" and "the query uses it" are different claims and only the
second one is the fix. `run_id` is the table's primary key, so the second column adds one
already-unique value per entry and no ambiguity; the index costs about 28 bytes per row
(53.7 MB of `runs` becomes 56.5 MB)."""


def ensure_runs_mode_projection(connection: sqlite3.Connection) -> None:
    """Add `runs.mode` and its index unless they are already there. Idempotent; moves no data.

    The **only** code path that establishes this projection, called from two places for two
    different reasons: `SQLiteRunRepository._initialize` (so every database this store opens can
    answer `list_runs(mode=...)`) and `storage/migrations.py`'s `add_runs_mode_projection` (so
    the change is a recorded, backed-up, audited schema event on every database the migration
    engine reaches). One function rather than two copies of the DDL, because "the migration and
    the store agree on the schema" is a claim that only stays true while somebody re-reads both.

    Adding a `VIRTUAL` generated column is the one kind of `ALTER TABLE` that is honest inside a
    constructor: it rewrites no row and stores no byte -- SQLite records a new expression in the
    table's schema and every existing row is untouched, because the value is computed at read
    time. That is why this may run where `_demo_add_runs_archived_at`'s ordinary column may not.
    Building the index does write, but only a derived b-tree, and only once.

    It has to run here and not only in the migration, and the reason is a database that really
    exists: a replay `state_path` never constructs `SQLitePortfolioLedger`/`SQLiteReportStore`,
    so `create_query_path_indexes` (migration 4) can never satisfy its precondition there and
    everything ordered after it -- migration 6 included -- stays pending forever
    (`tests/unit/backtest/test_replay.py::
    test_run_catches_up_the_demo_migration_on_a_second_call_but_the_index_migration_never_lands`).
    Left to the migration alone, an *older* replay database would keep a `runs` table with no
    `mode` column for the rest of its life, and the index statement below would fail against it
    with `no such column: mode` at store construction.

    `PRAGMA table_xinfo` and not `PRAGMA table_info`, which is not a stylistic preference:
    `table_info` omits generated columns entirely, so the other pragma never sees a column that
    is already there and re-runs the `ALTER`, failing with `duplicate column name: mode`.

    Disclosed rather than papered over: the read and the `ALTER` are two statements and are not
    serialized against another *process*, so two installations opening the same not-yet-projected
    database within the same instant can have one of them raise `duplicate column name: mode`.
    The window is one statement wide and exists only until the column is added once, ever, per
    database -- and the obvious guard, swallowing that error, would also swallow the
    `table_info` mistake above, which is not a race but a deterministic failure on every call.
    A one-in-a-million crash that names itself is the better trade against a silent one.
    `run_migrations` takes a real write lock and re-checks under it (see its docstring); this
    constructor does not, exactly as the other seven `CREATE TABLE IF NOT EXISTS` constructors
    in this package do not.
    """
    columns = {row[1] for row in connection.execute(f"PRAGMA table_xinfo({RUNS_TABLE})")}
    if RUNS_MODE_COLUMN not in columns:
        connection.execute(f"ALTER TABLE {RUNS_TABLE} ADD COLUMN {RUNS_MODE_COLUMN_DDL}")
    connection.execute(RUNS_MODE_INDEX_DDL)


class DuplicateRecordError(ValueError):
    """Raised when an append would replace an existing immutable record."""


class SQLiteRunRepository:
    """Persist immutable research records in a local SQLite WAL database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        return open_state_connection(self.path)

    def _initialize(self) -> None:
        """Create this store's three tables and their indexes, if they do not exist yet.

        `ensure_runs_mode_projection` runs last, on every construction, so `list_runs(mode=...)`
        works against every database this store opens rather than only against ones the
        migration engine has caught up. That is load-bearing on two real paths and not
        defensive coding: `build_storage()` runs migrations *before* constructing any store, so
        on a fresh install migration 6 defers to the next call; and on a replay `state_path` it
        defers forever. An index arriving one call late is a performance question -- a column a
        published query names arriving late is `no such column: mode`. See that function's
        docstring for why an `ALTER` adding a virtual generated column is honest here while
        `_demo_add_runs_archived_at`'s ordinary column would not be.
        """
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                f"""
                CREATE TABLE IF NOT EXISTS {RUNS_TABLE} (
                    run_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS decisions_run_id_uq
                ON decisions(run_id);

                CREATE TABLE IF NOT EXISTS checkpoints (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs(run_id)
                );
                """
            )
            ensure_runs_mode_projection(connection)

    def journal_mode(self) -> str:
        """Return the active SQLite journal mode."""
        with closing(self._connect()) as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        if row is None:
            raise RuntimeError("SQLite did not return a journal mode")
        return str(row[0]).lower()

    def append_run(self, manifest: RunManifest) -> None:
        """Append a run manifest without replacing an existing run."""
        payload = manifest.model_dump_json(exclude_computed_fields=True)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    "INSERT INTO runs (run_id, payload) VALUES (?, ?)",
                    (manifest.run_id, payload),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicateRecordError(f"run already exists: {manifest.run_id}") from error

    def get_run(self, run_id: str) -> RunManifest | None:
        """Load a run manifest by ID."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else read_versioned(RUN_MANIFEST_VERSIONS, row[0])

    def list_runs(self, *, mode: RunMode | str | None = None) -> tuple[RunManifest, ...]:
        """Return every run manifest, or only those of one mode, ordered by `run_id`.

        This is Finding F70's query, and the reason `runs.mode` exists as a column at all: with
        `mode` reachable only inside `payload`, the *only* way to answer it was to fetch every
        row and JSON-parse each one in Python, then discard the ones that did not match. Now
        SQLite answers it from `RUNS_MODE_INDEX_NAME` and hands back only the matching rows, so
        the number of payloads this method validates is the number of runs in that mode rather
        than the number of runs in the database.

        Measured through this method, against a legacy `runs` table of 100,000 stored runs
        (median of five, Apple M-series, SQLite 3.50.4). With the five modes spread evenly,
        listing the 20,000 `paper` ones costs **461 ms before and 106 ms after**; at a 1-in-100
        spread, **450 ms before and 5.3 ms after**; at 1-in-1,000, **437 ms before and 0.8 ms
        after**. The three ratios are the finding rather than any one of them: the saving is
        rows *not* parsed, so it scales with how rare the mode is, not with how big the table
        is -- 4.4x, 84x, 537x.

        Isolating the two halves on the same data: the column alone, with no index at all,
        already takes the even spread from 496 ms to 149 ms, because 80,000 payloads stop being
        validated; the index is what takes the 1-in-100 case from 50 ms to 5.4 ms. An index on
        a five-valued column is a *low-selectivity* index by construction, and it earns its
        keep here for a reason worth stating rather than assuming: the row it skips does not
        cost a row lookup, it costs a `json_extract` plus a pydantic validation.

        `ORDER BY run_id` because a listing without an order is not a reproducible answer and
        `runs` has no `sequence` column to mean insertion order; `run_id` is the primary key, so
        the order is total. The composite index serves the filter and the sort together.

        An unknown mode is refused by name rather than answered with an empty tuple: `RunMode`
        coerces the argument, so a caller who asks for `"papper"` gets a `ValueError` naming the
        value instead of a confident "there are no such runs" -- the distinction between
        *withheld* and *absent* that this repository draws everywhere else.
        """
        with closing(self._connect()) as connection:
            if mode is None:
                rows = connection.execute("SELECT payload FROM runs ORDER BY run_id").fetchall()
            else:
                rows = connection.execute(
                    f"SELECT payload FROM runs WHERE {RUNS_MODE_COLUMN} = ? ORDER BY run_id",
                    (RunMode(mode).value,),
                ).fetchall()
        return tuple(read_versioned(RUN_MANIFEST_VERSIONS, row[0]) for row in rows)

    def append_decision(self, decision: DecisionLedger) -> None:
        """Append a decision linked to an existing run."""
        if self.get_run(decision.run_id) is None:
            raise ValueError(f"unknown run_id: {decision.run_id}")
        payload = decision.model_dump_json(exclude_computed_fields=True)
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute(
                    """
                    INSERT INTO decisions (decision_id, run_id, payload)
                    VALUES (?, ?, ?)
                    """,
                    (decision.decision_id, decision.run_id, payload),
                )
        except sqlite3.IntegrityError as error:
            raise DuplicateRecordError(
                f"decision already exists: {decision.decision_id}"
            ) from error

    def get_decision(self, decision_id: str) -> DecisionLedger | None:
        """Load a decision by its content-derived ID."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
        return None if row is None else read_versioned(DECISION_LEDGER_VERSIONS, row[0])

    def get_decision_for_run(self, run_id: str) -> DecisionLedger | None:
        """Load the single immutable decision associated with a run."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload FROM decisions WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return None if row is None else read_versioned(DECISION_LEDGER_VERSIONS, row[0])

    def append_checkpoint(self, *, run_id: str, checkpoint: CheckpointRecord) -> None:
        """Append a checkpoint to an existing run."""
        if self.get_run(run_id) is None:
            raise ValueError(f"unknown run_id: {run_id}")
        payload = checkpoint.model_dump_json()
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO checkpoints (run_id, payload) VALUES (?, ?)",
                (run_id, payload),
            )

    def list_checkpoints(self, *, run_id: str) -> tuple[CheckpointRecord, ...]:
        """Return checkpoints in append order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM checkpoints
                WHERE run_id = ?
                ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        return tuple(read_versioned(CHECKPOINT_RECORD_VERSIONS, row[0]) for row in rows)
