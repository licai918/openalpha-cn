"""`V2-P4-002`: `runs.mode` as an indexed projection of the payload, against a real database.

Finding F70's claim was about a query nothing in the repository could yet issue: `mode` lived
only inside `runs.payload`, so "list every paper run" meant fetching every row and JSON-parsing
each one in Python. These tests are about the two halves of the fix and, deliberately, about
the two ways each half fails quietly.

**The column is a projection, not a second copy.** The tempting fix -- a plain `TEXT mode`
written by `append_run` -- states the same fact twice and nothing re-reads both, which is this
repository's most repeated failure. `RUNS_MODE_COLUMN_DDL` is `GENERATED ALWAYS AS`, so SQLite
derives it from `payload` on every read and refuses every attempt to write it independently;
`test_the_mode_column_cannot_be_written_independently_of_the_payload` and
`test_rewriting_a_payload_carries_the_projection_with_it` are what say that is true of the
shipped schema rather than of the docstring.

**The index has to be used, not merely to exist.** "Added an index, so it is fast now" is a
claim about a file, not about a query. `test_listing_one_mode_seeks_the_index_instead_of_
scanning_the_table` reads `EXPLAIN QUERY PLAN`, and
`test_listing_one_mode_hands_python_only_the_matching_payloads` measures the quantity that
actually moved: the number of payloads that cross into Python and get validated. On the
100,000-row benchmark behind `SQLiteRunRepository.list_runs`'s docstring those two numbers are
100,000 and 20,000 (evenly spread) or 100,000 and 1,000 (1-in-100 spread); here they are small
and exact, because what is being pinned is the ratio's *shape*, not the machine it ran on.

**Two callers, one implementation.** `ensure_runs_mode_projection` is the only code that
establishes the column and the index; `SQLiteRunRepository._initialize` calls it on every
construction and `add_runs_mode_projection` (migration 6) calls it as a recorded, backed-up,
audited event. The store has to call it and not defer to the migration, because
`build_storage()` runs migrations *before* constructing any store (so migration 6 defers on a
fresh install) and because a replay `state_path` never satisfies migration 4's precondition (so
migration 6 defers there forever) --
`test_a_legacy_runs_table_gains_the_projection_at_store_construction_with_no_migration_at_all`
is that second case, and it is a crash rather than a slowdown: the index statement would meet
`no such column: mode`. `test_a_migrated_legacy_database_and_a_freshly_created_one_agree_on_the_
column_and_the_index` keeps the two callers landing on one schema even though one function
serves both.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import pytest

from openalpha_cn.domain.run import RunManifest
from openalpha_cn.domain.run_mode import RUN_MODES, RunMode
from openalpha_cn.runtime.composition import build_storage
from openalpha_cn.storage.migrations import (
    ADD_RUNS_MODE_PROJECTION_VERSION,
    REWRITE_CONTRACT_IDENTITIES_VERSION,
    SPLIT_BATCH_TASK_ITEMS_VERSION,
    MigrationFailedError,
    RunsModeProjectionError,
    _add_runs_mode_projection,
    read_status,
    run_migrations,
)
from openalpha_cn.storage.sqlite import (
    RUNS_MODE_COLUMN,
    RUNS_MODE_INDEX_NAME,
    RUNS_MODE_PAYLOAD_PATH,
    SQLiteRunRepository,
)

DIGEST: Final[str] = "a" * 64

MINIMUM_SQLITE_VERSION: Final[tuple[int, int]] = (3, 31)
"""Generated columns landed in SQLite 3.31.0 (2020-01-22). `json_extract` needs JSON1, which is
unconditional from 3.38 and enabled by default in every distribution build older than that."""


def _manifest(run_id: str, mode: str, *, now: datetime) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        mode=RunMode(mode),
        as_of=now,
        code_commit="0123456789abcdef",
        config_digest=DIGEST,
        random_seed=7,
        started_at=now,
        status="running",
    )


def _populate(repository: SQLiteRunRepository, *, now: datetime, per_mode: int) -> None:
    """Write `per_mode` runs for each of the five declared modes, interleaved.

    Interleaved rather than mode-by-mode so that a listing that merely returned a contiguous
    slice of the table -- or the insertion order -- could not pass by accident.
    """
    for index in range(per_mode):
        for mode in RUN_MODES:
            repository.append_run(_manifest(f"run-{mode.value}-{index:04d}", mode.value, now=now))


def _table_xinfo(path: Path) -> dict[str, tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        return {row[1]: row for row in connection.execute("PRAGMA table_xinfo(runs)")}


def _index_sql(path: Path) -> dict[str, str]:
    with closing(sqlite3.connect(path)) as connection:
        return {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'runs'"
            )
        }


def _query_plan(path: Path, sql: str, parameters: tuple[Any, ...]) -> str:
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(f"EXPLAIN QUERY PLAN {sql}", parameters).fetchall()
    return " | ".join(str(row[3]) for row in rows)


def _legacy_runs_database(path: Path, *, now: datetime, payloads: list[tuple[str, str]]) -> None:
    """Write the pre-`V2-P4-002` table layout: `runs(run_id, payload)` and nothing else.

    The four sibling tables the earlier migrations are precondition-bound to are created empty
    alongside it, so this fixture exercises *this* migration rather than stalling on one of
    theirs -- `test_migrations.py` already owns the deferral behaviour of each.
    """
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE runs (run_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE decisions (
                decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE checkpoints (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE TABLE portfolio_transitions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT NOT NULL
            );
            CREATE TABLE research_reports (
                report_id TEXT PRIMARY KEY, subject TEXT NOT NULL, payload TEXT NOT NULL
            );
            """
        )
        connection.executemany("INSERT INTO runs (run_id, payload) VALUES (?, ?)", payloads)


def _v1_payload(run_id: str, mode: str, *, now: datetime) -> str:
    """A genuine `run-manifest/v1` row: the three-mode set, written the way an old install did."""
    return json.dumps(
        {
            "schema_version": "run-manifest/v1",
            "run_id": run_id,
            "mode": mode,
            "as_of": now.isoformat(),
            "code_commit": "0123456789abcdef",
            "config_digest": DIGEST,
            "provider_payload_digests": [],
            "model_versions": [],
            "prompt_versions": [],
            "random_seed": 7,
            "environment": [],
            "started_at": now.isoformat(),
            "finished_at": None,
            "status": "running",
            "checkpoints": [],
        }
    )


# --- the platform floor -----------------------------------------------------------------


def test_the_runtime_sqlite_supports_the_generated_column_this_schema_needs() -> None:
    """State the floor as an assertion rather than letting it surface as a bare
    `OperationalError` from whichever store happened to be constructed first.

    `runs` is created with a `GENERATED ALWAYS AS (json_extract(...))` column, which needs
    SQLite >= 3.31 for generated columns and JSON1 for the expression. Both are satisfied by
    every interpreter this project's `requires-python = ">=3.11"` admits in practice, but
    "in practice" is exactly the kind of claim this repository asks to be measured, and a
    failure here names the reason instead of naming a column.
    """
    assert sqlite3.sqlite_version_info[:2] >= MINIMUM_SQLITE_VERSION, (
        f"SQLite {sqlite3.sqlite_version} predates generated columns "
        f"(>= {'.'.join(str(part) for part in MINIMUM_SQLITE_VERSION)} required)"
    )
    with closing(sqlite3.connect(":memory:")) as connection:
        probe = connection.execute(
            f"SELECT json_extract(?, '{RUNS_MODE_PAYLOAD_PATH}')", (json.dumps({"mode": "paper"}),)
        ).fetchone()
    assert probe[0] == "paper"


# --- the column is a projection, never a second copy --------------------------------------


def test_a_freshly_created_runs_table_is_born_with_the_derived_mode_column(
    tmp_path: Path,
) -> None:
    """`SQLiteRunRepository.__init__` alone -- no migration run at all -- is enough.

    Constructing the store directly is a real, supported path (`test_sqlite_repository.py`
    does exactly this), and it is also what `build_storage()` does on a fresh install *after*
    every table-altering migration has already deferred. Either way the column has to be there
    the first time, because `list_runs(mode=...)` names it.

    The hidden flag is the load-bearing assertion, not the presence of the name: `2` is
    `VIRTUAL GENERATED`, `3` is `STORED GENERATED`, and `0` is an ordinary column that some
    future contributor could write to independently of the payload.
    """
    path = tmp_path / "state.sqlite3"

    SQLiteRunRepository(path)

    columns = _table_xinfo(path)
    assert RUNS_MODE_COLUMN in columns
    assert columns[RUNS_MODE_COLUMN][-1] == 2
    assert RUNS_MODE_INDEX_NAME in _index_sql(path)


def test_a_legacy_runs_table_gains_the_projection_at_store_construction_with_no_migration_at_all(
    tmp_path: Path, migration_now: datetime
) -> None:
    """The case that makes the store's call load-bearing rather than merely early.

    A `runs` table written before `V2-P4-002` and never migrated is not hypothetical: a replay
    `state_path` never constructs `SQLitePortfolioLedger`/`SQLiteReportStore`, so migration 4
    can never satisfy its precondition there and migration 6, ordered behind it, stays pending
    for the life of that database. Were the column left to the migration alone, this
    constructor's `CREATE INDEX ... ON runs(mode, run_id)` would fail against such a file with
    `no such column: mode` -- a crash on open, not a slow query. Asserted with the store built
    directly on a legacy table and `run_migrations` never called.
    """
    path = tmp_path / "state.sqlite3"
    _legacy_runs_database(
        path,
        now=migration_now,
        payloads=[
            ("run-old-live", _v1_payload("run-old-live", "live", now=migration_now)),
            ("run-old-replay", _v1_payload("run-old-replay", "replay", now=migration_now)),
        ],
    )

    repository = SQLiteRunRepository(path)

    assert read_status(path).current_version == 0  # nothing migrated this database
    assert _table_xinfo(path)[RUNS_MODE_COLUMN][-1] == 2
    assert RUNS_MODE_INDEX_NAME in _index_sql(path)
    assert [run.run_id for run in repository.list_runs(mode=RunMode.replay)] == ["run-old-replay"]


def test_the_mode_column_cannot_be_written_independently_of_the_payload(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """The property that makes "one copy" true of the database and not just of the writer.

    A plain column is kept in step by whoever remembers to update it; this one cannot be
    updated at all. SQLite refuses the write itself, so a future `INSERT`/`UPDATE` that tried
    to state a mode differing from the payload's is a hard error at the statement, not a row
    that disagrees with itself and is discovered years later.
    """
    path = tmp_path / "state.sqlite3"
    repository = SQLiteRunRepository(path)
    repository.append_run(_manifest("run-a", "live", now=plain_frozen_now))
    payload = _v1_payload("run-b", "live", now=plain_frozen_now)

    with closing(sqlite3.connect(path)) as connection:
        with pytest.raises(sqlite3.OperationalError, match="cannot INSERT into generated column"):
            connection.execute(
                f"INSERT INTO runs (run_id, payload, {RUNS_MODE_COLUMN}) VALUES (?, ?, ?)",
                ("run-b", payload, "paper"),
            )
        with pytest.raises(sqlite3.OperationalError, match="cannot UPDATE generated column"):
            connection.execute(
                f"UPDATE runs SET {RUNS_MODE_COLUMN} = ? WHERE run_id = ?", ("paper", "run-a")
            )


def test_rewriting_a_payload_carries_the_projection_with_it(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """Drift's other direction, and the one that actually happens here.

    `storage/migrations.py::_rewrite_run_manifests` rewrites every `runs.payload` in place.
    A hand-maintained column would have needed that pass to remember it; this column follows
    the payload because it is computed from it, and the index follows the column. Asserted
    through a raw `UPDATE` rather than through the migration so the property is the schema's,
    not one migration's.
    """
    path = tmp_path / "state.sqlite3"
    repository = SQLiteRunRepository(path)
    repository.append_run(_manifest("run-a", "live", now=plain_frozen_now))
    rewritten = _manifest("run-a", "daily", now=plain_frozen_now).model_dump_json(
        exclude_computed_fields=True
    )

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE runs SET payload = ? WHERE run_id = ?", (rewritten, "run-a"))

    assert [run.run_id for run in repository.list_runs(mode=RunMode.daily)] == ["run-a"]
    assert repository.list_runs(mode=RunMode.live) == ()


# --- the query F70 named -------------------------------------------------------------------


def test_listing_one_mode_returns_only_that_modes_runs_in_run_id_order(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """All five modes, so the listing cannot pass by returning "everything that is not live".

    Five values is the whole selectivity story for this column, and a fixture holding two of
    them could not tell a filter from a partition.
    """
    path = tmp_path / "state.sqlite3"
    repository = SQLiteRunRepository(path)
    _populate(repository, now=plain_frozen_now, per_mode=3)

    for mode in RUN_MODES:
        listed = repository.list_runs(mode=mode)
        assert [run.run_id for run in listed] == [
            f"run-{mode.value}-{index:04d}" for index in range(3)
        ]
        assert {run.mode for run in listed} == {mode}

    assert len(repository.list_runs()) == 3 * len(RUN_MODES)
    assert [run.run_id for run in repository.list_runs()] == sorted(
        run.run_id for run in repository.list_runs()
    )


def test_listing_one_mode_seeks_the_index_instead_of_scanning_the_table(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """ "An index exists" and "the query uses it" are different claims; this is the second.

    The plan must be a `SEARCH ... USING INDEX` on the composite index, and it must contain no
    `SCAN runs` and no `USE TEMP B-TREE FOR ORDER BY`. The temp-b-tree half is why the index is
    `(mode, run_id)` rather than `(mode)`: a single-column index answers the filter and then
    leaves SQLite to sort the matches, which reintroduces per-row work proportional to the
    result set on a query whose whole point was to stop doing per-row work.
    """
    path = tmp_path / "state.sqlite3"
    repository = SQLiteRunRepository(path)
    _populate(repository, now=plain_frozen_now, per_mode=40)

    plan = _query_plan(
        path,
        f"SELECT payload FROM runs WHERE {RUNS_MODE_COLUMN} = ? ORDER BY run_id",
        ("paper",),
    )

    assert f"USING INDEX {RUNS_MODE_INDEX_NAME}" in plan, plan
    assert "SEARCH" in plan, plan
    assert "SCAN runs" not in plan, plan
    assert "TEMP B-TREE" not in plan, plan


def test_listing_one_mode_hands_python_only_the_matching_payloads(
    tmp_path: Path, plain_frozen_now: datetime
) -> None:
    """The quantity that actually moved, stated as a ratio a fixture can hold exactly.

    Before this change the only way to answer "which runs are paper runs" was to fetch all 200
    payloads and validate every one of them in Python, discarding 160. Now SQLite answers the
    predicate and 40 payloads cross the boundary. Timings belong in a report, on a named
    machine; what a test can pin without becoming a flake is the row count, and the row count
    is where the time was going -- `read_versioned` is a pydantic validation per payload.
    """
    path = tmp_path / "state.sqlite3"
    repository = SQLiteRunRepository(path)
    _populate(repository, now=plain_frozen_now, per_mode=40)

    with closing(sqlite3.connect(path)) as connection:
        scanned = connection.execute("SELECT payload FROM runs").fetchall()
        delivered = connection.execute(
            f"SELECT payload FROM runs WHERE {RUNS_MODE_COLUMN} = ? ORDER BY run_id",
            ("paper",),
        ).fetchall()

    assert len(scanned) == 200
    assert len(delivered) == 40
    assert len(repository.list_runs(mode=RunMode.paper)) == 40
    assert len(repository.list_runs()) == 200


def test_an_unknown_mode_is_refused_by_name_rather_than_answered_with_an_empty_listing(
    tmp_path: Path,
) -> None:
    """ "No such runs" and "no such mode" are two different answers and only one of them is true.

    A `WHERE mode = 'papper'` would have returned zero rows, cheerfully, forever. Coercing
    through `RunMode` first turns a typo into a `ValueError` naming the value; the `match=` is
    on the value rather than on "invalid" so it distinguishes this refusal from any other
    `ValueError` the read path can raise.
    """
    repository = SQLiteRunRepository(tmp_path / "state.sqlite3")

    with pytest.raises(ValueError, match="'papper' is not a valid RunMode"):
        repository.list_runs(mode="papper")


# --- the retrofit ---------------------------------------------------------------------------


def test_a_legacy_database_gains_the_column_and_the_index_through_migration_six(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    """A `runs` table written by a pre-`V2-P4-002` install, migrated for real.

    The rows are genuine `run-manifest/v1` JSON, so the projection is being computed from bytes
    this change never wrote -- which is the only version of this test that proves old rows need
    no backfill. A generated column derives itself; there is no `UPDATE` pass here at all, and
    that is the answer to "how do old rows migrate".
    """
    path = tmp_path / "state.sqlite3"
    _legacy_runs_database(
        path,
        now=migration_now,
        payloads=[
            ("run-old-live", _v1_payload("run-old-live", "live", now=migration_now)),
            ("run-old-replay", _v1_payload("run-old-replay", "replay", now=migration_now)),
            ("run-old-backtest", _v1_payload("run-old-backtest", "backtest", now=migration_now)),
        ],
    )

    result = run_migrations(path, clock=migration_clock)

    assert result.to_version == ADD_RUNS_MODE_PROJECTION_VERSION
    assert [migration.name for migration in result.applied][-1] == "add_runs_mode_projection"
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            f"SELECT run_id FROM runs WHERE {RUNS_MODE_COLUMN} = ?", ("replay",)
        ).fetchall() == [("run-old-replay",)]
    assert _table_xinfo(path)[RUNS_MODE_COLUMN][-1] == 2
    assert RUNS_MODE_INDEX_NAME in _index_sql(path)


def test_a_migrated_legacy_database_and_a_freshly_created_one_agree_on_the_column_and_the_index(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    """Two creation paths, one schema -- the drift this design's *other* half could still have.

    The data cannot drift from the payload (the column is generated) and the DDL cannot drift
    between the two callers (`ensure_runs_mode_projection` is one function, called by the store
    and by migration 6). This test is what keeps that second property honest as the code moves:
    it compares the schema two genuinely different histories arrive at, so re-introducing a
    second DDL site -- the obvious edit when someone wants the store's table to differ "just
    slightly" -- fails here rather than being discovered by a query that returns nothing.

    Column *position* is compared as a mapping, not a sequence, and deliberately: a legacy table
    gained `archived_at` (migration 3) before `mode` (migration 6), while a freshly created one
    gains `mode` at construction and `archived_at` afterwards. Ordinal order therefore genuinely
    differs between two correct databases, and nothing in this schema depends on it -- every
    statement in `storage/sqlite.py` names its columns.
    """
    fresh = tmp_path / "fresh.sqlite3"
    legacy = tmp_path / "legacy.sqlite3"
    SQLiteRunRepository(fresh)
    _legacy_runs_database(
        legacy,
        now=migration_now,
        payloads=[("run-old", _v1_payload("run-old", "live", now=migration_now))],
    )
    run_migrations(legacy, clock=migration_clock)

    fresh_column = _table_xinfo(fresh)[RUNS_MODE_COLUMN]
    legacy_column = _table_xinfo(legacy)[RUNS_MODE_COLUMN]

    # (name, declared type, notnull, default, pk, hidden) -- everything but the ordinal.
    assert fresh_column[1:] == legacy_column[1:]
    assert _index_sql(fresh)[RUNS_MODE_INDEX_NAME] == _index_sql(legacy)[RUNS_MODE_INDEX_NAME]


def test_the_first_build_storage_call_can_already_list_by_mode_although_the_migration_deferred(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """Why the column is in the store's `CREATE TABLE` and not left to the migration alone.

    `build_storage()` runs migrations before constructing any store, so on a fresh install
    every table-altering migration -- migration 6 included -- defers to the next call. An index
    arriving one call late is a performance question. A *column* arriving one call late is
    `no such column: mode` raised by a method the store publishes, for the entire lifetime of
    the first process a new installation ever runs.
    """
    runtime_dir = tmp_path / "runtime"

    storage = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    assert storage.migration_result.to_version < ADD_RUNS_MODE_PROJECTION_VERSION
    repository = SQLiteRunRepository(runtime_dir / "state.sqlite3")
    assert repository.list_runs(mode=RunMode.paper) == ()
    assert RUNS_MODE_INDEX_NAME in _index_sql(runtime_dir / "state.sqlite3")


def test_the_second_build_storage_call_applies_migration_six_over_the_stores_own_column(
    tmp_path: Path, migration_clock: Callable[[], datetime]
) -> None:
    """The `PRAGMA table_xinfo` half of `ensure_runs_mode_projection`, which `table_info` gets
    wrong.

    On this path `runs` already carries the column, because the store established it on the
    first call. `PRAGMA table_info` omits generated columns entirely, so an already-present
    guard written against it -- the pragma `_demo_add_runs_archived_at` correctly uses for its
    ordinary column -- would not see it, would re-run the `ALTER TABLE`, and would fail with
    `duplicate column name: mode`. That is every fresh installation's second startup, not an
    exotic database.
    """
    runtime_dir = tmp_path / "runtime"
    build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    second = build_storage(runtime_dir=runtime_dir, clock=migration_clock)

    assert second.migration_result.to_version == SPLIT_BATCH_TASK_ITEMS_VERSION
    assert "add_runs_mode_projection" in [m.name for m in second.migration_result.applied]
    assert read_status(runtime_dir / "state.sqlite3").pending == ()


# --- the run-time audit ---------------------------------------------------------------------


def test_the_audit_refuses_a_projection_that_disagrees_with_the_payload_beside_it(
    tmp_path: Path, migration_now: datetime
) -> None:
    """The failure a generated column actually has, provoked directly.

    `json_extract(payload, '$.run_id')` is a perfectly legal expression that builds a perfectly
    valid index over values that are not modes. Nothing raises; `WHERE mode = 'paper'` simply
    returns nothing, permanently. The audit re-derives the value in Python from
    `RUNS_MODE_PAYLOAD_PATH` and compares, which is the only reading independent enough to
    notice. Provoked by pre-creating the wrong column rather than by editing the constant, so
    the shipped DDL is what the rest of the suite keeps exercising.
    """
    path = tmp_path / "state.sqlite3"
    _legacy_runs_database(
        path,
        now=migration_now,
        payloads=[("run-old", _v1_payload("run-old", "live", now=migration_now))],
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            f"ALTER TABLE runs ADD COLUMN {RUNS_MODE_COLUMN} TEXT GENERATED ALWAYS AS "
            "(json_extract(payload, '$.run_id')) VIRTUAL"
        )

        with pytest.raises(
            RunsModeProjectionError, match="projects mode 'run-old' but its payload states 'live'"
        ):
            _add_runs_mode_projection(connection)


def test_the_audit_refuses_a_plain_column_that_could_drift_from_the_payload(
    tmp_path: Path, migration_now: datetime
) -> None:
    """An ordinary column carrying today's correct value would pass every row comparison.

    That is the whole point of checking the hidden flag as well: correctness on the day of the
    migration is exactly what a second copy has, and losing it later is exactly what a second
    copy does. `match=` names the pragma flag so this refusal cannot be confused with the
    payload-disagreement one above.
    """
    path = tmp_path / "state.sqlite3"
    _legacy_runs_database(
        path,
        now=migration_now,
        payloads=[("run-old", _v1_payload("run-old", "live", now=migration_now))],
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(f"ALTER TABLE runs ADD COLUMN {RUNS_MODE_COLUMN} TEXT")
        connection.execute(f"UPDATE runs SET {RUNS_MODE_COLUMN} = 'live'")

        with pytest.raises(RunsModeProjectionError, match="is not a generated column"):
            _add_runs_mode_projection(connection)


def test_the_audit_refuses_a_stored_run_whose_mode_is_not_a_declared_one(
    tmp_path: Path, migration_now: datetime
) -> None:
    """A payload the current `RunMode` cannot classify would be invisible to every listing.

    Not reachable through today's contracts -- both `RunManifestV1` and `RunManifest` constrain
    the field -- and that is the reason to check it here rather than to assume it: this
    migration reads bytes some older build wrote, and `V2-P4-001` has just finished widening
    that set once. A mode nobody can name is the one row a mode-filtered listing silently drops.
    """
    path = tmp_path / "state.sqlite3"
    _legacy_runs_database(
        path,
        now=migration_now,
        payloads=[("run-old", _v1_payload("run-old", "shadow", now=migration_now))],
    )
    with (
        closing(sqlite3.connect(path)) as connection,
        connection,
        pytest.raises(RunsModeProjectionError, match="which is not a declared RunMode"),
    ):
        _add_runs_mode_projection(connection)


def test_an_audit_refusal_rolls_the_whole_migration_back_and_names_the_backup(
    tmp_path: Path, migration_now: datetime, migration_clock: Callable[[], datetime]
) -> None:
    """The refusal has to be transactional, or it is only an opinion.

    Run through the real executor rather than by calling `apply()` directly, because what is
    under test is that `PRAGMA user_version` does not move, no `schema_migrations` row is
    written for version 6, and versions 1-5 keep the ground they took -- the same per-migration
    atomicity `V2-P4-001`'s identity audit relies on.

    The provocation is a mis-pointed generated column rather than an undeclared mode, and that
    choice is forced: an undeclared mode is not a payload `read_versioned` can parse, so
    migration 5 would fail first and this test would be measuring that migration instead. The
    row here is a legal `run-manifest/v1`; only the column reads the wrong field.
    """
    path = tmp_path / "state.sqlite3"
    _legacy_runs_database(
        path,
        now=migration_now,
        payloads=[("run-old", _v1_payload("run-old", "live", now=migration_now))],
    )
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            f"ALTER TABLE runs ADD COLUMN {RUNS_MODE_COLUMN} TEXT GENERATED ALWAYS AS "
            "(json_extract(payload, '$.run_id')) VIRTUAL"
        )

    with pytest.raises(MigrationFailedError, match=r"migration 6 \(add_runs_mode_projection\)"):
        run_migrations(path, clock=migration_clock)

    status = read_status(path)
    assert status.current_version == REWRITE_CONTRACT_IDENTITIES_VERSION
    assert [migration.version for migration in status.pending] == [
        ADD_RUNS_MODE_PROJECTION_VERSION,
        SPLIT_BATCH_TASK_ITEMS_VERSION,
    ]
    assert [migration.version for migration in status.applied] == [1, 2, 3, 4, 5]
    assert RUNS_MODE_INDEX_NAME not in _index_sql(path)
